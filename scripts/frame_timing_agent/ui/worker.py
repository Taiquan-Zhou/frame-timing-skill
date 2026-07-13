from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Callable
import re

from frame_timing_agent.auto_timing_agent import run_timing_agent
from frame_timing_agent.ui.view_model import AnalysisViewData, build_analysis_view, load_execution_summary


@dataclass(frozen=True)
class RunSettings:
    frame_dir: Path
    artifact_dir: Path
    fps: float = 30.0
    limit_first_n: int | None = None


def default_artifact_dir(frame_dir: Path | str) -> Path:
    frame_dir = Path(frame_dir)
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", frame_dir.name).strip("._-") or "frames"
    candidate = frame_dir.parent / "output" / "frame_timing_ui" / safe_name
    if candidate.is_relative_to(frame_dir):
        return frame_dir.parent / f"{safe_name}_frame_timing_output"
    return candidate


def run_analysis(settings: RunSettings) -> AnalysisViewData:
    result = run_timing_agent(
        frames=settings.frame_dir,
        artifact_dir=settings.artifact_dir,
        limit_first_n=settings.limit_first_n,
        mode="reconstruction_balanced",
        write=False,
        fps=settings.fps,
    )
    return build_analysis_view(
        result,
        settings.frame_dir,
        fps=settings.fps,
        limit_first_n=settings.limit_first_n,
    )


def run_export(settings: RunSettings) -> AnalysisViewData:
    result = run_timing_agent(
        frames=settings.frame_dir,
        artifact_dir=settings.artifact_dir,
        limit_first_n=settings.limit_first_n,
        mode="reconstruction_balanced",
        write=True,
        fps=settings.fps,
    )
    view = build_analysis_view(
        result,
        settings.frame_dir,
        fps=settings.fps,
        limit_first_n=settings.limit_first_n,
    )
    return replace(view, execution=load_execution_summary(settings.artifact_dir))


def create_task(function: Callable[[], object], on_success: Callable[[object], None], on_error: Callable[[str], None]):
    from PySide6.QtCore import QObject, QRunnable, Signal, Slot

    class TaskSignals(QObject):
        succeeded = Signal(object)
        failed = Signal(str)

    class FunctionTask(QRunnable):
        def __init__(self):
            super().__init__()
            self.signals = TaskSignals()
            self.signals.succeeded.connect(on_success)
            self.signals.failed.connect(on_error)

        @Slot()
        def run(self):
            try:
                result = function()
            except Exception as exc:
                self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
                return
            self.signals.succeeded.emit(result)

    return FunctionTask()
