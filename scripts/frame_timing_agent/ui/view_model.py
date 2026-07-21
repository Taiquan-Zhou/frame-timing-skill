from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import json

from frame_timing_agent.auto_timing_agent import TimingAgentResult
from frame_timing_agent.frame_source import load_frame_records


@dataclass(frozen=True)
class SegmentView:
    segment_type: str
    start: int
    end: int
    frame_count: int


@dataclass(frozen=True)
class ThumbnailView:
    source_index: int
    path: Path
    operation: str


@dataclass(frozen=True)
class ExecutionSummary:
    status: str
    output_count: int
    warning_count: int
    error_count: int


@dataclass(frozen=True)
class AnalysisViewData:
    analyzed_count: int
    estimated_output_count: int
    strategy_name: str
    source_indices: tuple[int, ...]
    motion_values: tuple[float, ...]
    sharpness_values: tuple[float, ...]
    contrast_values: tuple[float, ...]
    segments: tuple[SegmentView, ...]
    operation_counts: dict[str, int]
    thumbnails: tuple[ThumbnailView, ...]
    artifact_dir: Path
    output_dir: Path | None
    execution: ExecutionSummary | None
    source_snapshot_matches: bool | None = None


def build_analysis_view(
    result: TimingAgentResult,
    frame_dir: Path | str,
    fps: float,
    limit_first_n: int | None,
    persisted_thumbnails: tuple[ThumbnailView, ...] | None = None,
) -> AnalysisViewData:
    analysis_dir = result.artifact_dir / "analysis"
    metrics = _read_metrics(analysis_dir / "frame_metrics.csv")
    segments = _read_segments(analysis_dir / "segments.json")
    strategy = _read_json(analysis_dir / "strategy.json")
    operations = strategy.get("operations", [])
    operation_counts: dict[str, int] = {}
    for operation in operations:
        op = str(operation.get("op", "unknown"))
        operation_counts[op] = operation_counts.get(op, 0) + 1

    if persisted_thumbnails is None:
        records = load_frame_records(frame_dir, fps=fps, limit_first_n=limit_first_n)
        thumbnails = _choose_thumbnails(records, operations)
    else:
        thumbnails = persisted_thumbnails
    return AnalysisViewData(
        analyzed_count=result.analyzed_count,
        estimated_output_count=result.estimated_output_count,
        strategy_name="reconstruction_balanced",
        source_indices=tuple(int(row["source_index"]) for row in metrics),
        motion_values=tuple(float(row["motion_score"]) for row in metrics),
        sharpness_values=tuple(float(row["sharpness"]) for row in metrics),
        contrast_values=tuple(float(row["contrast"]) for row in metrics),
        segments=segments,
        operation_counts=operation_counts,
        thumbnails=thumbnails,
        artifact_dir=result.artifact_dir,
        output_dir=result.output_dir,
        execution=None,
    )


def load_execution_summary(artifact_dir: Path | str) -> ExecutionSummary:
    audit = _read_json(Path(artifact_dir) / "analysis" / "execution_audit.json")
    return ExecutionSummary(
        status=str(audit.get("status", "unknown")),
        output_count=int(audit.get("output_count", 0)),
        warning_count=len(audit.get("warnings", [])),
        error_count=len(audit.get("errors", [])),
    )


def _read_metrics(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"analysis contains no frame metrics: {path}")
    return rows


def _read_segments(path: Path) -> tuple[SegmentView, ...]:
    data = _read_json(path)
    if not isinstance(data, list):
        raise ValueError(f"segments artifact must be a list: {path}")
    return tuple(
        SegmentView(
            segment_type=str(item["segment_type"]),
            start=int(item["start"]),
            end=int(item["end"]),
            frame_count=int(item["frame_count"]),
        )
        for item in data
    )


def _read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _choose_thumbnails(records, operations: list[dict], limit: int = 6) -> tuple[ThumbnailView, ...]:
    records_by_source = {record.source_index: record for record in records}
    selected: list[ThumbnailView] = []
    selected_sources: set[int] = set()

    for operation in operations:
        if len(selected) >= limit:
            break
        source_range = operation.get("range", {})
        start = int(source_range.get("start", 0))
        end = int(source_range.get("end", -1))
        candidates = [source for source in sorted(records_by_source) if start <= source <= end]
        if operation.get("op") == "select_sources":
            chosen = [int(source) for source in operation.get("sources", []) if int(source) in records_by_source]
        else:
            chosen = [candidates[len(candidates) // 2]] if candidates else []
        for source in chosen:
            if source in selected_sources or len(selected) >= limit:
                continue
            selected.append(
                ThumbnailView(
                    source_index=source,
                    path=records_by_source[source].path,
                    operation=str(operation.get("op", "keep")),
                )
            )
            selected_sources.add(source)

    remaining = [source for source in sorted(records_by_source) if source not in selected_sources]
    for source in _sample_sources(remaining, limit - len(selected)):
        selected.append(ThumbnailView(source_index=source, path=records_by_source[source].path, operation="keep"))
    return tuple(selected)


def _sample_sources(sources: list[int], count: int) -> list[int]:
    if count <= 0 or not sources:
        return []
    if len(sources) <= count:
        return sources
    if count == 1:
        return [sources[len(sources) // 2]]
    positions = [round(index * (len(sources) - 1) / (count - 1)) for index in range(count)]
    return [sources[position] for position in positions]
