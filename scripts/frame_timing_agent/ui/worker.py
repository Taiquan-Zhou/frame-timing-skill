from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Callable
import re
import shutil
import uuid

from frame_timing_agent.apply_frame_strategy import apply_strategy
from frame_timing_agent.auto_timing_agent import TimingAgentResult, run_timing_agent
from frame_timing_agent.frame_source import load_frame_records
from frame_timing_agent.strategy_execution_audit import audit_strategy_execution, write_execution_audit
from frame_timing_agent.ui.history import RunHistoryStore, RunRecord
from frame_timing_agent.ui.run_artifacts import (
    INPUT_SNAPSHOT_NAME,
    bind_strategy_snapshot,
    capture_input_snapshot,
    load_bound_strategy,
    load_persisted_thumbnails,
    persist_thumbnails,
    verify_input_snapshot,
    verify_output_snapshot,
    write_input_snapshot,
)
from frame_timing_agent.ui.view_model import AnalysisViewData, build_analysis_view, load_execution_summary


@dataclass(frozen=True)
class RunSettings:
    frame_dir: Path
    artifact_dir: Path
    fps: float = 30.0
    limit_first_n: int | None = None


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


ProgressCallback = Callable[[int, str], None]


def run_analysis(settings: RunSettings, progress_callback: ProgressCallback | None = None) -> AnalysisViewData:
    initial_snapshot = capture_input_snapshot(
        settings.frame_dir,
        fps=settings.fps,
        limit_first_n=settings.limit_first_n,
    )
    agent_kwargs = dict(
        frames=settings.frame_dir,
        artifact_dir=settings.artifact_dir,
        limit_first_n=settings.limit_first_n,
        mode="reconstruction_balanced",
        write=False,
        fps=settings.fps,
    )
    if progress_callback is not None:
        agent_kwargs["progress_callback"] = _core_progress(progress_callback, "正在准备分析结果")
    result = run_timing_agent(**agent_kwargs)
    view = build_analysis_view(
        result,
        settings.frame_dir,
        fps=settings.fps,
        limit_first_n=settings.limit_first_n,
    )
    final_snapshot = capture_input_snapshot(
        settings.frame_dir,
        fps=settings.fps,
        limit_first_n=settings.limit_first_n,
    )
    if final_snapshot != initial_snapshot:
        raise ValueError("input frames changed during analysis; run analysis again")
    analysis_dir = settings.artifact_dir / "analysis"
    write_input_snapshot(
        analysis_dir,
        bind_strategy_snapshot(final_snapshot, result.strategy_path),
    )
    frozen_thumbnails = persist_thumbnails(analysis_dir, view.thumbnails)
    return replace(view, thumbnails=frozen_thumbnails, source_snapshot_matches=True)


def run_export(settings: RunSettings, progress_callback: ProgressCallback | None = None) -> AnalysisViewData:
    analysis_dir = settings.artifact_dir / "analysis"
    _report(progress_callback, 2, "正在校验分析快照")
    verify_input_snapshot(
        analysis_dir,
        settings.frame_dir,
        fps=settings.fps,
        limit_first_n=settings.limit_first_n,
    )
    strategy_path = analysis_dir / "strategy.json"
    strategy = load_bound_strategy(analysis_dir)

    records = load_frame_records(
        settings.frame_dir,
        fps=settings.fps,
        limit_first_n=settings.limit_first_n,
    )
    output_dir = settings.artifact_dir / "output_frames"
    staging_dir = settings.artifact_dir / f".output_frames.export-{uuid.uuid4().hex}"
    audit_staging_dir = settings.artifact_dir / f".execution_audit.export-{uuid.uuid4().hex}"
    _report(progress_callback, 20, "正在生成 output_frames")
    try:
        apply_result = apply_strategy(
            records,
            strategy,
            staging_dir,
            progress_callback=_map_progress(progress_callback, 20, 88, "正在生成 output_frames"),
        )
        _report(progress_callback, 90, "正在校验输出结果")
        verify_input_snapshot(
            analysis_dir,
            settings.frame_dir,
            fps=settings.fps,
            limit_first_n=settings.limit_first_n,
        )
        verify_output_snapshot(analysis_dir, staging_dir)
        audit = audit_strategy_execution(records, strategy, staging_dir, fps=settings.fps)
        if audit.get("status") != "ok":
            raise ValueError(f"output verification failed: {'; '.join(audit.get('errors', []))}")
        write_execution_audit(audit, audit_staging_dir)
        _replace_output_directory(
            staging_dir,
            output_dir,
            lambda: _replace_execution_audit(audit_staging_dir, analysis_dir),
        )
    finally:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        if audit_staging_dir.exists():
            shutil.rmtree(audit_staging_dir)
    result = TimingAgentResult(
        analyzed_count=len(records),
        estimated_output_count=apply_result.output_count,
        artifact_dir=settings.artifact_dir,
        strategy_path=strategy_path,
        output_dir=output_dir,
    )
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


def _report(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback is not None:
        callback(max(0, min(98, percent)), message)


def _map_progress(
    callback: ProgressCallback | None,
    start: int,
    end: int,
    message: str,
) -> Callable[[int, int], None] | None:
    if callback is None:
        return None

    def report(completed: int, total: int) -> None:
        ratio = completed / max(1, total)
        _report(callback, start + round((end - start) * ratio), message)

    return report


def _replace_output_directory(
    staging_dir: Path,
    output_dir: Path,
    commit_metadata: Callable[[], None] | None = None,
) -> None:
    backup_dir = output_dir.with_name(f".{output_dir.name}.backup-{uuid.uuid4().hex}")
    if output_dir.exists():
        output_dir.rename(backup_dir)
    try:
        staging_dir.rename(output_dir)
        if commit_metadata is not None:
            commit_metadata()
    except Exception:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        if backup_dir.exists() and not output_dir.exists():
            backup_dir.rename(output_dir)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)


def _replace_execution_audit(staging_dir: Path, analysis_dir: Path) -> None:
    names = ("execution_audit.json", "execution_audit.md")
    backup_dir = analysis_dir / f".execution_audit.backup-{uuid.uuid4().hex}"
    backup_dir.mkdir()
    try:
        for name in names:
            target = analysis_dir / name
            if target.exists():
                target.rename(backup_dir / name)
        for name in names:
            (staging_dir / name).rename(analysis_dir / name)
    except Exception:
        for name in names:
            target = analysis_dir / name
            if target.exists():
                target.unlink()
            backup = backup_dir / name
            if backup.exists():
                backup.rename(target)
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


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
