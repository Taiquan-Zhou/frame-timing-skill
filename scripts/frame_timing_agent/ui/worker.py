from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Callable
import re
import shutil
import uuid

from frame_timing_agent.auto_timing_agent import TimingAgentResult
from frame_timing_agent.run_workflow import ProgressCallback, RunSettings, analyze_run, export_run
from frame_timing_agent.ui.history import RunHistoryStore, RunRecord
from frame_timing_agent.ui.run_artifacts import (
    INPUT_SNAPSHOT_NAME,
    load_persisted_thumbnails,
    persist_thumbnails,
    verify_input_snapshot,
)
from frame_timing_agent.ui.view_model import AnalysisViewData, build_analysis_view, load_execution_summary


def default_artifact_dir(frame_dir: Path | str) -> Path:
    frame_dir = Path(frame_dir).expanduser().resolve()
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", frame_dir.name).strip("._-") or "frames"
    candidate = (frame_dir.parent / "output" / "frame_timing_ui" / safe_name).resolve()
    if candidate.is_relative_to(frame_dir):
        return (frame_dir.parent / f"{safe_name}_frame_timing_output").resolve()
    return candidate


def new_run_artifact_dir(frame_dir: Path | str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    return default_artifact_dir(frame_dir) / f"{timestamp}-{uuid.uuid4().hex[:8]}"


def delete_history_run(record: RunRecord, history_store: RunHistoryStore) -> None:
    artifact_dir = record.artifact_dir.expanduser().resolve()
    managed_root = default_artifact_dir(record.frame_dir)
    if artifact_dir.parent != managed_root or artifact_dir.name != record.run_id:
        raise ValueError(f"history artifact is not a managed run directory: {artifact_dir}")

    if not artifact_dir.exists():
        history_store.delete(record.run_id)
        return

    quarantine = artifact_dir.with_name(f".{artifact_dir.name}.deleting-{uuid.uuid4().hex}")
    artifact_dir.rename(quarantine)
    try:
        history_store.delete(record.run_id)
    except Exception:
        quarantine.rename(artifact_dir)
        raise

    try:
        shutil.rmtree(quarantine)
    except Exception:
        history_store.upsert(record)
        if quarantine.exists() and not artifact_dir.exists():
            quarantine.rename(artifact_dir)
        raise


def run_analysis(settings: RunSettings, progress_callback: ProgressCallback | None = None) -> AnalysisViewData:
    core_callback = None
    if progress_callback is not None:
        core_callback = _core_progress(progress_callback, "正在准备分析结果")
    result = analyze_run(settings, progress_callback=core_callback)
    view = build_analysis_view(
        result,
        settings.frame_dir,
        fps=settings.fps,
        limit_first_n=settings.limit_first_n,
    )
    analysis_dir = settings.artifact_dir / "analysis"
    frozen_thumbnails = persist_thumbnails(analysis_dir, view.thumbnails)
    return replace(view, thumbnails=frozen_thumbnails, source_snapshot_matches=True)


def run_export(settings: RunSettings, progress_callback: ProgressCallback | None = None) -> AnalysisViewData:
    analysis_dir = settings.artifact_dir / "analysis"
    result = export_run(settings, progress_callback=progress_callback)
    view = build_analysis_view(
        result,
        settings.frame_dir,
        fps=settings.fps,
        limit_first_n=settings.limit_first_n,
        persisted_thumbnails=load_persisted_thumbnails(analysis_dir),
    )
    exported_view = replace(view, execution=load_execution_summary(settings.artifact_dir))
    return replace(exported_view, source_snapshot_matches=True)


def load_existing_run(
    settings: RunSettings,
    analyzed_count: int,
    estimated_output_count: int,
) -> AnalysisViewData:
    analysis_dir = settings.artifact_dir / "analysis"
    output_dir = settings.artifact_dir / "output_frames"
    result = TimingAgentResult(
        analyzed_count=analyzed_count,
        estimated_output_count=estimated_output_count,
        artifact_dir=settings.artifact_dir,
        strategy_path=analysis_dir / "strategy.json",
        output_dir=output_dir if output_dir.is_dir() else None,
    )
    persisted_thumbnails = load_persisted_thumbnails(analysis_dir)
    try:
        view = build_analysis_view(
            result,
            settings.frame_dir,
            fps=settings.fps,
            limit_first_n=settings.limit_first_n,
            persisted_thumbnails=persisted_thumbnails,
        )
    except (FileNotFoundError, ValueError):
        if persisted_thumbnails is not None:
            raise
        view = build_analysis_view(
            result,
            settings.frame_dir,
            fps=settings.fps,
            limit_first_n=settings.limit_first_n,
            persisted_thumbnails=(),
        )
    snapshot_path = analysis_dir / INPUT_SNAPSHOT_NAME
    snapshot_matches: bool | None = None
    if snapshot_path.is_file():
        try:
            verify_input_snapshot(
                analysis_dir,
                settings.frame_dir,
                fps=settings.fps,
                limit_first_n=settings.limit_first_n,
            )
            snapshot_matches = True
        except (OSError, ValueError):
            snapshot_matches = False
    view = replace(view, source_snapshot_matches=snapshot_matches)
    audit_path = analysis_dir / "execution_audit.json"
    if audit_path.is_file():
        view = replace(view, execution=load_execution_summary(settings.artifact_dir))
    return view


def _core_progress(callback: ProgressCallback, terminal_message: str) -> ProgressCallback:
    def report(percent: int, message: str) -> None:
        if percent >= 100:
            callback(98, terminal_message)
        else:
            callback(min(98, percent), message)

    return report


def create_task(
    function: Callable[..., object],
    on_success: Callable[[object], None],
    on_error: Callable[[str], None],
    on_progress: ProgressCallback | None = None,
):
    from PySide6.QtCore import QObject, QRunnable, Signal, Slot

    class TaskSignals(QObject):
        succeeded = Signal(object)
        failed = Signal(str)
        progressed = Signal(int, str)

    class FunctionTask(QRunnable):
        def __init__(self):
            super().__init__()
            self.signals = TaskSignals()
            self.signals.succeeded.connect(on_success)
            self.signals.failed.connect(on_error)
            if on_progress is not None:
                self.signals.progressed.connect(on_progress)

        @Slot()
        def run(self):
            try:
                result = function(self.signals.progressed.emit) if on_progress is not None else function()
            except Exception as exc:
                self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
                return
            self.signals.succeeded.emit(result)

    return FunctionTask()
