from __future__ import annotations

import json
import math
import os
import re
import stat
import uuid
import errno
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import BinaryIO, Callable, Iterator, Sequence

from frame_timing_agent.batch_discovery import DiscoveryResult
from frame_timing_agent.batch_quality import QualityWarning, evaluate_quality
from frame_timing_agent.batch_timing_agent import (
    BatchTimingItem,
    BatchTimingItemResult,
    _failure_result,
    publish_batch_timing_reports,
)
from frame_timing_agent.run_workflow import (
    RunSettings,
    StaleSourceError,
    analyze_run,
    export_run,
    verify_input_snapshot,
)


SCHEMA_VERSION = 1


class BatchStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


class BatchItemStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"


class BatchStateError(ValueError):
    """Raised when a persisted batch state cannot be used safely."""


class BatchBusyError(BatchStateError):
    """Raised when another live process owns a batch run lock."""


class BatchReportError(RuntimeError):
    """Raised when durable batch summary publication fails."""


class _LockUnavailable(OSError):
    pass


@dataclass
class BatchItemState:
    frame_dir: Path
    safe_name: str
    status: BatchItemStatus = BatchItemStatus.PENDING
    progress: float = 0.0
    last_error: str | None = None
    retry_count: int = 0
    warnings: tuple[str, ...] = ()
    risk_details: tuple[dict[str, object], ...] = ()
    approved: bool = False
    note: str | None = None
    analyzed_count: int | None = None
    output_count: int | None = None
    output_path: Path | None = None


@dataclass(frozen=True)
class BatchExportSummary:
    exported: tuple[str, ...]
    skipped: tuple[str, ...]
    failed: tuple[str, ...]


class BatchExportStaleSourceError(BatchStateError):
    """Raised after export when one or more items became stale during the operation."""

    def __init__(self, summary: BatchExportSummary, stale_items: tuple[str, ...]) -> None:
        super().__init__("one or more batch export sources changed")
        self.summary = summary
        self.stale_items = stale_items


@dataclass
class BatchState:
    batch_id: str
    created_at: str
    updated_at: str
    fps: float
    limit_first_n: int | None
    artifact_root: Path
    revision: int = 0
    items: list[BatchItemState] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION
    status: BatchStatus = BatchStatus.READY
    pause_requested: bool = False

    @property
    def state_path(self) -> Path:
        return self.artifact_root / "analysis" / "batch_state.json"


def create_batch(
    discovery: DiscoveryResult,
    *,
    artifact_root: Path | str,
    fps: float,
    limit_first_n: int | None = None,
) -> BatchState:
    """Create and persist an initial batch state for discovered frame directories."""
    frame_dirs = tuple(Path(path).resolve() for path in discovery.frame_dirs)
    if not frame_dirs:
        raise ValueError("a batch requires at least one discovered frame directory")
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be a positive finite value")
    if limit_first_n is not None and limit_first_n <= 0:
        raise ValueError("limit_first_n must be positive when provided")
    resolved_artifact_root = Path(artifact_root).resolve()
    if any(_paths_overlap(resolved_artifact_root, frame_dir) for frame_dir in frame_dirs):
        raise ValueError("artifact_root must not overlap a source frame directory")
    _validate_artifact_child(resolved_artifact_root, Path("analysis"))
    existing_state_path = resolved_artifact_root / "analysis" / "batch_state.json"
    if existing_state_path.exists():
        raise BatchStateError(f"batch state already exists: {existing_state_path}")
    if _artifact_root_has_non_lock_content(resolved_artifact_root, existing_state_path):
        raise BatchStateError(f"batch artifact root is not empty: {resolved_artifact_root}")

    now = _timestamp()
    state = BatchState(
        batch_id=uuid.uuid4().hex,
        created_at=now,
        updated_at=now,
        fps=fps,
        limit_first_n=limit_first_n,
        artifact_root=resolved_artifact_root,
        items=[
            BatchItemState(frame_dir=frame_dir, safe_name=safe_name)
            for frame_dir, safe_name in zip(frame_dirs, _unique_safe_names(frame_dirs))
        ],
    )
    with _run_lock(state.state_path):
        if state.state_path.exists():
            raise BatchStateError(f"batch state already exists: {state.state_path}")
        _save_batch_unlocked(state)
    return state


def load_batch(state_path: Path | str) -> BatchState:
    """Load a batch state after validating its schema and typed fields."""
    path = Path(state_path)
    _validate_state_path_location(path)
    try:
        raw_state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BatchStateError(f"cannot load batch state: {path}") from error
    state = _state_from_json(raw_state)
    if state.state_path.resolve() != path.resolve():
        raise BatchStateError("artifact_root does not match the canonical state path")
    return state


def save_batch(state: BatchState) -> None:
    """Lock and durably replace a state unless the loaded revision is stale."""
    _validate_state(state)
    with _run_lock(state.state_path):
        _save_batch_unlocked(state)


def _save_batch_unlocked(state: BatchState) -> None:
    """Persist a state while the caller owns the canonical batch lock."""
    _validate_state(state)
    state_path = state.state_path
    if state_path.exists():
        persisted_revision = load_batch(state_path).revision
        if persisted_revision != state.revision:
            raise BatchStateError("batch state revision is stale")
    elif state.revision != 0:
        raise BatchStateError("batch state revision is stale")

    previous_revision = state.revision
    previous_updated_at = state.updated_at
    state.revision += 1
    state.updated_at = _timestamp()
    state_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = state_path.with_name(f".{state_path.name}.{uuid.uuid4().hex}.tmp")
    replaced = False
    try:
        with temporary_path.open("x", encoding="utf-8", newline="\n") as state_file:
            json.dump(_state_to_json(state), state_file, indent=2, sort_keys=True)
            state_file.write("\n")
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary_path, state_path)
        replaced = True
        _sync_directory(state_path.parent)
    except BaseException:
        if not replaced:
            state.revision = previous_revision
            state.updated_at = previous_updated_at
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def recover_batch(state_path: Path | str) -> BatchState:
    """Explicitly recover an unfinished batch after any stale run lock is cleared."""
    path = Path(state_path).resolve()
    initial_state = load_batch(path)
    if initial_state.status is BatchStatus.FINISHED:
        return initial_state

    canonical_path = initial_state.state_path.resolve()
    with _run_lock(canonical_path):
        state = load_batch(path)
        if state.status is BatchStatus.FINISHED:
            return state

        changed = False
        if state.status is BatchStatus.RUNNING:
            state.status = BatchStatus.READY
            state.pause_requested = False
            changed = True
        for item in state.items:
            if item.status is BatchItemStatus.RUNNING:
                item.status = BatchItemStatus.PENDING
                item.progress = 0.0
                item.retry_count += 1
                changed = True
        if changed:
            _save_batch_unlocked(state)
        return state


def approve_item(state_path: Path | str, item_name: str, note: str) -> BatchState:
    """Approve one review-required item after revalidating its analysis snapshot."""
    path = Path(state_path).resolve()
    canonical_path = load_batch(path).state_path.resolve()
    with _run_lock(canonical_path):
        state = load_batch(path)
        item = next((candidate for candidate in state.items if candidate.safe_name == item_name), None)
        if item is None:
            raise BatchStateError(f"unknown batch item: {item_name}")
        if item.status is not BatchItemStatus.REVIEW_REQUIRED:
            raise BatchStateError(f"batch item is not review_required: {item_name}")
        if not isinstance(note, str):
            raise BatchStateError("approval note must be a string")

        verify_input_snapshot(
            state.artifact_root / item.safe_name / "analysis",
            item.frame_dir,
            state.fps,
            state.limit_first_n,
        )
        item.approved = True
        item.note = note.strip() or None
        _save_batch_unlocked(state)
        return state


def export_batch(
    state_path: Path | str,
    progress_callback: Callable[[int, str], None] | None = None,
) -> BatchExportSummary:
    """Explicitly export eligible finished batch items without rerunning analysis."""
    path = Path(state_path).resolve()
    canonical_path = load_batch(path).state_path.resolve()
    with _run_lock(canonical_path):
        state = load_batch(path)
        if state.status is not BatchStatus.FINISHED:
            raise BatchStateError("batch must be finished before exporting")

        exported: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        stale_items: list[str] = []
        eligible_items = [item for item in state.items if _is_export_eligible(item)]
        total = len(eligible_items)
        completed = 0

        for item in state.items:
            if not _is_export_eligible(item):
                skipped.append(item.safe_name)
                continue

            settings = RunSettings(
                frame_dir=item.frame_dir,
                artifact_dir=state.artifact_root / item.safe_name,
                fps=state.fps,
                limit_first_n=state.limit_first_n,
            )

            def report_item_progress(percent: int, message: str) -> None:
                bounded = max(0, min(100, int(percent)))
                _report_progress(progress_callback, round((completed + bounded / 100) * 100 / total), message)

            try:
                result = export_run(settings, progress_callback=report_item_progress)
            except StaleSourceError:
                failed.append(item.safe_name)
                stale_items.append(item.safe_name)
                item.status = BatchItemStatus.FAILED
                item.progress = 1.0
                item.last_error = "stale_source: source frames changed after analysis"
                item.approved = False
                item.note = None
            except Exception:
                failed.append(item.safe_name)
            else:
                item.output_count = result.estimated_output_count
                item.output_path = result.output_dir
                exported.append(item.safe_name)
            completed += 1
            _save_batch_unlocked(state)
            _report_progress(progress_callback, round(completed * 100 / total), item.safe_name)

        _publish_reports(state)
        summary = BatchExportSummary(tuple(exported), tuple(skipped), tuple(failed))
        if stale_items:
            raise BatchExportStaleSourceError(summary, tuple(stale_items))
        return summary


def _is_export_eligible(item: BatchItemState) -> bool:
    return item.status is BatchItemStatus.COMPLETED or (
        item.status is BatchItemStatus.REVIEW_REQUIRED and item.approved
    )


def run_batch(
    state_path: Path | str,
    progress_callback: Callable[[int, str], None] | None = None,
    should_pause: Callable[[], bool] | None = None,
    retry_items: Sequence[str] = (),
) -> BatchState:
    """Run pending items sequentially, persisting each state transition."""
    path = Path(state_path).resolve()
    canonical_path = load_batch(path).state_path.resolve()
    with _run_lock(canonical_path):
        state = load_batch(path)
        if state.status is BatchStatus.RUNNING:
            _recover_interrupted_items(state)
        retries = _validate_retry_items(state, retry_items)
        for item in state.items:
            if item.safe_name in retries:
                item.status = BatchItemStatus.PENDING
                item.progress = 0.0
        state.status = BatchStatus.RUNNING
        state.pause_requested = False
        _save_batch_unlocked(state)
        total = len(state.items)
        terminal_count = sum(
            item.status not in {BatchItemStatus.PENDING, BatchItemStatus.RUNNING} and item.safe_name not in retries
            for item in state.items
        )
        reports_current = False

        for item in state.items:
            is_retry = item.safe_name in retries
            if item.status is not BatchItemStatus.PENDING:
                continue
            if should_pause is not None and should_pause():
                state.status = BatchStatus.PAUSED
                state.pause_requested = True
                _save_batch_unlocked(state)
                break

            if is_retry or item.last_error is not None:
                item.retry_count += 1
            item.warnings = ()
            item.risk_details = ()
            item.approved = False
            item.note = None
            item.analyzed_count = None
            item.output_count = None
            item.output_path = None
            item.status = BatchItemStatus.RUNNING
            reports_current = False
            item.progress = 0.0
            item.last_error = None
            _save_batch_unlocked(state)
            settings = RunSettings(
                frame_dir=item.frame_dir,
                artifact_dir=state.artifact_root / item.safe_name,
                fps=state.fps,
                limit_first_n=state.limit_first_n,
            )

            def report_item_progress(percent: int, message: str) -> None:
                bounded = max(0, min(100, int(percent)))
                item.progress = bounded / 100.0
                overall = round((terminal_count + item.progress) * 100 / total)
                _report_progress(progress_callback, overall, message)

            try:
                result = analyze_run(settings, progress_callback=report_item_progress)
                warnings = evaluate_quality(result.artifact_dir / "analysis")
                item.warnings = tuple(warning.code for warning in warnings)
                item.risk_details = tuple(_quality_warning_to_dict(warning) for warning in warnings)
                item.status = BatchItemStatus.REVIEW_REQUIRED if warnings else BatchItemStatus.COMPLETED
                item.progress = 1.0
                item.analyzed_count = result.analyzed_count
                item.output_count = result.estimated_output_count
                item.output_path = result.output_dir
            except Exception as error:
                failed = _failure_result(
                    BatchTimingItem(name=item.safe_name, frames=item.frame_dir),
                    settings.artifact_dir,
                    error,
                )
                item.status = BatchItemStatus.FAILED
                item.progress = 1.0
                item.last_error = failed.error
                item.analyzed_count = 0
                item.output_count = 0
                item.output_path = None

            terminal_count += 1
            _save_batch_unlocked(state)
            _publish_reports(state)
            reports_current = True
            _report_progress(progress_callback, round(terminal_count * 100 / total), item.safe_name)
        else:
            if not reports_current:
                _publish_reports(state)
            state.status = BatchStatus.FINISHED
            state.pause_requested = False
            _save_batch_unlocked(state)
        if state.status is BatchStatus.FINISHED:
            _report_progress(progress_callback, 100, "batch finished")
        return state


def _recover_interrupted_items(state: BatchState) -> None:
    state.pause_requested = False
    for item in state.items:
        if item.status is BatchItemStatus.RUNNING:
            item.status = BatchItemStatus.PENDING
            item.progress = 0.0
            item.retry_count += 1


def _publish_reports(state: BatchState) -> None:
    try:
        publish_batch_timing_reports(state.artifact_root, _reported_results(state))
    except OSError as error:
        raise BatchReportError(f"batch report publication failed: {error}") from error


def _validate_retry_items(state: BatchState, retry_items: Sequence[str]) -> set[str]:
    requested = set(retry_items)
    known = {item.safe_name: item for item in state.items}
    unknown = requested.difference(known)
    if unknown:
        raise BatchStateError(f"unknown retry item: {sorted(unknown)[0]}")
    for name in requested:
        if known[name].status is not BatchItemStatus.FAILED:
            raise BatchStateError(f"retry item is not failed: {name}")
    return requested


def _reported_results(state: BatchState) -> list[BatchTimingItemResult]:
    results: list[BatchTimingItemResult] = []
    for item in state.items:
        if item.status in {BatchItemStatus.PENDING, BatchItemStatus.RUNNING}:
            continue
        artifact_dir = state.artifact_root / item.safe_name
        results.append(
            BatchTimingItemResult(
                name=item.safe_name,
                frame_dir=item.frame_dir,
                artifact_dir=artifact_dir,
                analyzed_count=item.analyzed_count or 0,
                estimated_output_count=item.output_count or 0,
                strategy_path=(artifact_dir / "analysis" / "strategy.json")
                if item.status in {BatchItemStatus.COMPLETED, BatchItemStatus.REVIEW_REQUIRED}
                else None,
                output_dir=item.output_path,
                human_review_path=artifact_dir / "analysis" / "human_review.md",
                status="ok"
                if item.status in {BatchItemStatus.COMPLETED, BatchItemStatus.REVIEW_REQUIRED}
                else "failed",
                error=item.last_error or "",
            )
        )
    return results


def _report_progress(callback: Callable[[int, str], None] | None, percent: int, message: str) -> None:
    if callback is not None:
        try:
            callback(max(0, min(100, percent)), message)
        except Exception:
            pass


def _quality_warning_to_dict(warning: QualityWarning) -> dict[str, object]:
    return {
        "code": warning.code,
        "value": warning.value,
        "threshold": warning.threshold,
        "affected_count": warning.affected_count,
        "ranges": [list(frame_range) for frame_range in warning.ranges],
        "message": warning.message,
    }


def _state_to_json(state: BatchState) -> dict[str, object]:
    return {
        "schema_version": state.schema_version,
        "batch_id": state.batch_id,
        "created_at": state.created_at,
        "updated_at": state.updated_at,
        "revision": state.revision,
        "fps": state.fps,
        "limit_first_n": state.limit_first_n,
        "artifact_root": str(state.artifact_root),
        "status": state.status.value,
        "pause_requested": state.pause_requested,
        "items": [
            {
                "frame_dir": str(item.frame_dir),
                "safe_name": item.safe_name,
                "status": item.status.value,
                "progress": item.progress,
                "last_error": item.last_error,
                "retry_count": item.retry_count,
                "warnings": list(item.warnings),
                "risk_details": list(item.risk_details),
                "approved": item.approved,
                "note": item.note,
                "analyzed_count": item.analyzed_count,
                "output_count": item.output_count,
                "output_path": str(item.output_path) if item.output_path is not None else None,
            }
            for item in state.items
        ],
    }


def _state_from_json(raw_state: object) -> BatchState:
    if not isinstance(raw_state, dict):
        raise BatchStateError("batch state must be a JSON object")
    if type(raw_state.get("schema_version")) is not int or raw_state["schema_version"] != SCHEMA_VERSION:
        raise BatchStateError(f"unsupported schema version: {raw_state.get('schema_version')!r}")
    try:
        raw_items = raw_state["items"]
        if not isinstance(raw_items, list):
            raise TypeError("items must be a list")
        state = BatchState(
            schema_version=raw_state["schema_version"],
            batch_id=_required_string(raw_state, "batch_id"),
            created_at=_required_string(raw_state, "created_at"),
            updated_at=_required_string(raw_state, "updated_at"),
            revision=_optional_revision(raw_state),
            fps=_required_float(raw_state, "fps"),
            limit_first_n=_optional_positive_int(raw_state, "limit_first_n"),
            artifact_root=Path(_required_string(raw_state, "artifact_root")),
            status=BatchStatus(_required_string(raw_state, "status")),
            pause_requested=_required_bool(raw_state, "pause_requested"),
            items=[_item_from_json(raw_item) for raw_item in raw_items],
        )
    except (KeyError, TypeError, ValueError) as error:
        raise BatchStateError("invalid batch state") from error
    _validate_state(state)
    return state


def _item_from_json(raw_item: object) -> BatchItemState:
    if not isinstance(raw_item, dict):
        raise TypeError("item must be a JSON object")
    output_path = raw_item.get("output_path")
    if output_path is not None and not isinstance(output_path, str):
        raise TypeError("output_path must be a string or null")
    warnings = raw_item.get("warnings")
    if not isinstance(warnings, list) or not all(isinstance(warning, str) for warning in warnings):
        raise TypeError("warnings must be a list of strings")
    risk_details = raw_item.get("risk_details", [])
    if not isinstance(risk_details, list) or not all(isinstance(detail, dict) for detail in risk_details):
        raise TypeError("risk_details must be a list of objects")
    return BatchItemState(
        frame_dir=Path(_required_string(raw_item, "frame_dir")),
        safe_name=_required_string(raw_item, "safe_name"),
        status=BatchItemStatus(_required_string(raw_item, "status")),
        progress=_required_float(raw_item, "progress"),
        last_error=_optional_string(raw_item, "last_error"),
        retry_count=_required_nonnegative_int(raw_item, "retry_count"),
        warnings=tuple(warnings),
        risk_details=tuple(risk_details),
        approved=_required_bool(raw_item, "approved"),
        note=_optional_string(raw_item, "note"),
        analyzed_count=_optional_nonnegative_int(raw_item, "analyzed_count"),
        output_count=_optional_nonnegative_int(raw_item, "output_count"),
        output_path=Path(output_path) if output_path is not None else None,
    )


def _validate_state(state: BatchState) -> None:
    if type(state.schema_version) is not int or state.schema_version != SCHEMA_VERSION:
        raise BatchStateError(f"unsupported schema version: {state.schema_version!r}")
    if isinstance(state.revision, bool) or not isinstance(state.revision, int) or state.revision < 0:
        raise BatchStateError("revision must be a non-negative integer")
    if not isinstance(state.batch_id, str) or not state.batch_id:
        raise BatchStateError("batch_id is required")
    _validate_timestamp(state.created_at, "created_at")
    _validate_timestamp(state.updated_at, "updated_at")
    if isinstance(state.fps, bool) or not isinstance(state.fps, (int, float)):
        raise BatchStateError("fps must be a positive finite value")
    if not math.isfinite(state.fps) or state.fps <= 0:
        raise BatchStateError("fps must be a positive finite value")
    if state.limit_first_n is not None and (
        isinstance(state.limit_first_n, bool) or not isinstance(state.limit_first_n, int) or state.limit_first_n <= 0
    ):
        raise BatchStateError("limit_first_n must be positive when provided")
    if not isinstance(state.status, BatchStatus):
        raise BatchStateError("status must be a BatchStatus")
    if not isinstance(state.pause_requested, bool):
        raise BatchStateError("pause_requested must be a boolean")
    if not isinstance(state.artifact_root, Path):
        raise BatchStateError("artifact_root must be a Path")
    _validate_canonical_path(state.artifact_root, "artifact_root")
    _validate_artifact_child(state.artifact_root, Path("analysis"))
    if not isinstance(state.items, list) or not state.items:
        raise BatchStateError("batch state must contain at least one item")
    if len({item.safe_name.casefold() for item in state.items}) != len(state.items):
        raise BatchStateError("item safe names must be unique")
    normalized_sources = {os.path.normcase(str(item.frame_dir)) for item in state.items}
    if len(normalized_sources) != len(state.items):
        raise BatchStateError("source frame directories must be unique")
    for item in state.items:
        _validate_item(state, item)
        _validate_artifact_child(state.artifact_root, Path(item.safe_name))
        if _paths_overlap(state.artifact_root, item.frame_dir):
            raise BatchStateError("artifact_root must not overlap a source frame directory")
    if state.status is BatchStatus.FINISHED and any(
        item.status in {BatchItemStatus.PENDING, BatchItemStatus.RUNNING} for item in state.items
    ):
        raise BatchStateError("finished batch cannot contain pending or running items")
    if state.status is not BatchStatus.RUNNING and any(
        item.status is BatchItemStatus.RUNNING for item in state.items
    ):
        raise BatchStateError("running item requires a running batch")


def _validate_item(state: BatchState, item: BatchItemState) -> None:
    if not isinstance(item, BatchItemState):
        raise BatchStateError("batch items must be BatchItemState values")
    if not isinstance(item.frame_dir, Path):
        raise BatchStateError("item frame_dir must be a Path")
    if not isinstance(item.safe_name, str) or not item.safe_name or not _is_safe_name(item.safe_name):
        raise BatchStateError("item safe_name is invalid")
    if not isinstance(item.status, BatchItemStatus):
        raise BatchStateError("item status must be a BatchItemStatus")
    if isinstance(item.progress, bool) or not isinstance(item.progress, (int, float)):
        raise BatchStateError("item progress must be between zero and one")
    if not math.isfinite(item.progress) or not 0.0 <= item.progress <= 1.0:
        raise BatchStateError("item progress must be between zero and one")
    if isinstance(item.retry_count, bool) or not isinstance(item.retry_count, int) or item.retry_count < 0:
        raise BatchStateError("item retry_count must be a non-negative integer")
    if item.analyzed_count is not None and (
        isinstance(item.analyzed_count, bool) or not isinstance(item.analyzed_count, int) or item.analyzed_count < 0
    ):
        raise BatchStateError("item analyzed_count cannot be negative")
    if item.output_count is not None and (
        isinstance(item.output_count, bool) or not isinstance(item.output_count, int) or item.output_count < 0
    ):
        raise BatchStateError("item output_count cannot be negative")
    if not isinstance(item.approved, bool):
        raise BatchStateError("item approved must be a boolean")
    if item.last_error is not None and not isinstance(item.last_error, str):
        raise BatchStateError("item last_error must be a string or None")
    if item.note is not None and not isinstance(item.note, str):
        raise BatchStateError("item note must be a string or None")
    if not isinstance(item.warnings, tuple) or not all(isinstance(warning, str) for warning in item.warnings):
        raise BatchStateError("item warnings must be a tuple of strings")
    if not isinstance(item.risk_details, tuple):
        raise BatchStateError("item risk_details must be a tuple")
    for detail in item.risk_details:
        _validate_risk_detail(detail)
    if item.risk_details and tuple(str(detail["code"]) for detail in item.risk_details) != item.warnings:
        raise BatchStateError("item risk_details must match item warnings")
    _validate_canonical_path(item.frame_dir, "item frame_dir")
    if item.output_path is not None:
        if not isinstance(item.output_path, Path):
            raise BatchStateError("item output_path must be a Path or None")
        _validate_canonical_path(item.output_path, "item output_path")
        expected_output_path = state.artifact_root / item.safe_name / "output_frames"
        if item.output_path != expected_output_path:
            raise BatchStateError("item output_path does not match its artifact directory")


def _validate_risk_detail(detail: object) -> None:
    if not isinstance(detail, dict):
        raise BatchStateError("item risk detail must be an object")
    required = {"code", "value", "threshold", "affected_count", "ranges", "message"}
    if set(detail) != required:
        raise BatchStateError("item risk detail fields are invalid")
    if not isinstance(detail["code"], str) or not detail["code"]:
        raise BatchStateError("item risk detail code is invalid")
    if not isinstance(detail["message"], str) or not detail["message"]:
        raise BatchStateError("item risk detail message is invalid")
    for field_name in ("value", "threshold"):
        value = detail[field_name]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value)
        ):
            raise BatchStateError(f"item risk detail {field_name} is invalid")
    affected_count = detail["affected_count"]
    if isinstance(affected_count, bool) or not isinstance(affected_count, int) or affected_count < 0:
        raise BatchStateError("item risk detail affected_count is invalid")
    ranges = detail["ranges"]
    if not isinstance(ranges, list):
        raise BatchStateError("item risk detail ranges must be a list")
    for frame_range in ranges:
        if (
            not isinstance(frame_range, list)
            or len(frame_range) != 2
            or any(isinstance(value, bool) or not isinstance(value, int) for value in frame_range)
            or frame_range[0] < 0
            or frame_range[1] < frame_range[0]
        ):
            raise BatchStateError("item risk detail range is invalid")


def _validate_state_path_location(state_path: Path) -> None:
    if state_path.name != "batch_state.json" or state_path.parent.name != "analysis":
        return
    artifact_root = state_path.absolute().parent.parent.resolve()
    resolved_state_path = state_path.resolve()
    if not resolved_state_path.is_relative_to(artifact_root):
        raise BatchStateError("batch state path escapes artifact_root")


def _artifact_root_has_non_lock_content(artifact_root: Path, state_path: Path) -> bool:
    if not artifact_root.exists():
        return False
    children = list(artifact_root.iterdir())
    analysis_dir = state_path.parent
    if children != [analysis_dir] or not analysis_dir.is_dir():
        return bool(children)
    analysis_children = list(analysis_dir.iterdir())
    lock_path = Path(f"{state_path}.run.lock")
    if analysis_children != [lock_path]:
        return bool(analysis_children)
    try:
        lock_stat = lock_path.lstat()
    except OSError:
        return True
    return not stat.S_ISREG(lock_stat.st_mode) or lock_stat.st_nlink != 1


def _validate_artifact_child(artifact_root: Path, relative_path: Path) -> None:
    resolved_root = artifact_root.resolve()
    resolved_child = (resolved_root / relative_path).resolve()
    if not resolved_child.is_relative_to(resolved_root):
        raise BatchStateError(f"batch artifact path escapes artifact_root: {relative_path}")


def _validate_timestamp(value: str, name: str) -> None:
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError) as error:
        raise BatchStateError(f"{name} must be a timezone-aware ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BatchStateError(f"{name} must be a timezone-aware ISO timestamp")


def _validate_canonical_path(path: Path, name: str) -> None:
    if not path.is_absolute():
        raise BatchStateError(f"{name} must be absolute")
    if path != path.resolve():
        raise BatchStateError(f"{name} must be canonical")


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _unique_safe_names(frame_dirs: tuple[Path, ...]) -> list[str]:
    used: set[str] = set()
    names: list[str] = []
    for frame_dir in frame_dirs:
        base_name = _safe_name(frame_dir.name)
        candidate = base_name
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{base_name}-{suffix}"
            suffix += 1
        used.add(candidate.casefold())
        names.append(candidate)
    return names


def _safe_name(name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip(".-_")
    device_name = sanitized.split(".", maxsplit=1)[0]
    if not sanitized or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])", device_name):
        return "item"
    return sanitized


def _is_safe_name(name: str) -> bool:
    return _safe_name(name) == name


def _required_string(data: dict[str, object], key: str) -> str:
    value = data[key]
    if not isinstance(value, str) or not value:
        raise TypeError(f"{key} must be a non-empty string")
    return value


def _optional_revision(data: dict[str, object]) -> int:
    value = data.get("revision", 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError("revision must be a non-negative integer")
    return value


def _optional_string(data: dict[str, object], key: str) -> str | None:
    value = data[key]
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null")
    return value


def _required_float(data: dict[str, object], key: str) -> float:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"{key} must be a number")
    return float(value)


def _required_nonnegative_int(data: dict[str, object], key: str) -> int:
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{key} must be a non-negative integer")
    return value


def _optional_nonnegative_int(data: dict[str, object], key: str) -> int | None:
    value = data[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"{key} must be a non-negative integer or null")
    return value


def _optional_positive_int(data: dict[str, object], key: str) -> int | None:
    value = data[key]
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise TypeError(f"{key} must be a positive integer or null")
    return value


def _required_bool(data: dict[str, object], key: str) -> bool:
    value = data[key]
    if not isinstance(value, bool):
        raise TypeError(f"{key} must be a boolean")
    return value


@contextmanager
def _run_lock(state_path: Path) -> Iterator[None]:
    lock_path = Path(f"{state_path.resolve()}.run.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = _open_safe_lock_file(lock_path)
    try:
        _acquire_file_lock(lock_file)
    except _LockUnavailable as error:
        lock_file.close()
        raise BatchBusyError("batch is already running") from error
    except BaseException:
        lock_file.close()
        raise
    try:
        _write_lock_owner(lock_file)
        yield
    finally:
        try:
            _release_file_lock(lock_file)
        finally:
            lock_file.close()


def _open_safe_lock_file(lock_path: Path) -> BinaryIO:
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise BatchStateError("batch lock file is unsafe") from error
    try:
        path_stat = lock_path.lstat()
        file_stat = os.fstat(descriptor)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        path_attributes = getattr(path_stat, "st_file_attributes", 0)
        unsafe = (
            not stat.S_ISREG(path_stat.st_mode)
            or path_stat.st_nlink != 1
            or (reparse_flag and path_attributes & reparse_flag)
            or (path_stat.st_dev, path_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino)
        )
        if unsafe:
            raise BatchStateError("batch lock file is unsafe")
        return os.fdopen(descriptor, "r+b")
    except BaseException:
        os.close(descriptor)
        raise


def _acquire_file_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"L")
        lock_file.flush()
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise _LockUnavailable from error
            raise
        return
    import fcntl

    try:
        fcntl.flock(  # type: ignore[attr-defined]
            lock_file.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB,  # type: ignore[attr-defined]
        )
    except BlockingIOError as error:
        raise _LockUnavailable from error


def _release_file_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _write_lock_owner(lock_file: BinaryIO) -> None:
    payload = json.dumps({"pid": os.getpid(), "created_at": _timestamp()}, sort_keys=True).encode("utf-8")
    lock_file.seek(1)
    lock_file.truncate(1)
    lock_file.write(payload)
    lock_file.write(b"\n")
    lock_file.flush()
    os.fsync(lock_file.fileno())


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")
