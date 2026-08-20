from __future__ import annotations

import csv
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path


BAD_QUALITY_REVIEW_RATIO = 0.10
FRAME_METRIC_COLUMNS = (
    "source_index",
    "output_index",
    "timestamp_sec",
    "sharpness",
    "brightness",
    "contrast",
    "motion_score",
    "similarity_score",
    "bad_quality_candidate",
)
SEGMENT_FIELDS = {"segment_type", "start", "end", "frame_count", "mean_motion", "reason"}


@dataclass(frozen=True)
class QualityWarning:
    code: str
    value: float | int
    threshold: float | None
    affected_count: int
    ranges: tuple[tuple[int, int], ...]
    message: str


def evaluate_quality(analysis_dir: Path | str) -> tuple[QualityWarning, ...]:
    analysis_dir = Path(analysis_dir)
    total_count, bad_count, source_indices = _read_bad_quality_counts(analysis_dir / "frame_metrics.csv")
    low_motion_ranges, low_motion_count = _read_low_motion_ranges(
        analysis_dir / "segments.json",
        source_indices=source_indices,
        total_count=total_count,
    )

    warnings: list[QualityWarning] = []
    bad_ratio = bad_count / total_count
    if bad_ratio >= BAD_QUALITY_REVIEW_RATIO:
        warnings.append(
            QualityWarning(
                code="quality.bad_candidate_ratio",
                value=bad_ratio,
                threshold=BAD_QUALITY_REVIEW_RATIO,
                affected_count=bad_count,
                ranges=(),
                message="低质量候选帧比例达到人工复核阈值。",
            )
        )
    if low_motion_ranges:
        warnings.append(
            QualityWarning(
                code="quality.low_motion_review",
                value=len(low_motion_ranges),
                threshold=None,
                affected_count=low_motion_count,
                ranges=low_motion_ranges,
                message="检测到需要人工确认的低运动区间。",
            )
        )
    return tuple(warnings)


def _read_bad_quality_counts(path: Path) -> tuple[int, int, frozenset[int]]:
    try:
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != FRAME_METRIC_COLUMNS:
                raise ValueError(f"frame metrics columns do not match the artifact contract: {path}")
            total_count = 0
            bad_count = 0
            source_indices: set[int] = set()
            output_indices: set[int] = set()
            for row_number, row in enumerate(reader, start=2):
                if None in row or any(value is None for value in row.values()):
                    raise ValueError(f"frame metrics row {row_number} has an invalid column count: {path}")
                raw_source_index = row.get("source_index")
                if not isinstance(raw_source_index, str) or re.fullmatch(r"0|[1-9][0-9]*", raw_source_index) is None:
                    raise ValueError(f"invalid source_index at frame metrics row {row_number}: {raw_source_index!r}")
                source_index = int(raw_source_index)
                source_indices.add(source_index)
                raw_output_index = row.get("output_index")
                if (
                    not isinstance(raw_output_index, str)
                    or re.fullmatch(r"0|[1-9][0-9]*", raw_output_index) is None
                ):
                    raise ValueError(
                        f"invalid output_index at frame metrics row {row_number}: {raw_output_index!r}"
                    )
                output_index = int(raw_output_index)
                if output_index in output_indices:
                    raise ValueError(f"duplicate output_index at frame metrics row {row_number}: {output_index}")
                output_indices.add(output_index)
                value = row.get("bad_quality_candidate")
                if value not in {"0", "1"}:
                    raise ValueError(
                        f"invalid bad_quality_candidate at frame metrics row {row_number}: {value!r}"
                    )
                total_count += 1
                bad_count += value == "1"
    except FileNotFoundError as error:
        raise ValueError(f"quality artifact is missing: {path}") from error
    except (OSError, UnicodeError, csv.Error) as error:
        raise ValueError(f"cannot read quality artifact: {path}") from error
    if total_count == 0:
        raise ValueError(f"frame metrics contain no frame rows: {path}")
    return total_count, bad_count, frozenset(source_indices)


def _read_low_motion_ranges(
    path: Path,
    *,
    source_indices: frozenset[int],
    total_count: int,
) -> tuple[tuple[tuple[int, int], ...], int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as error:
        raise ValueError(f"quality artifact is missing: {path}") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read quality artifact: {path}") from error
    if not isinstance(payload, list):
        raise ValueError(f"segments must be a JSON array: {path}")

    ranges: list[tuple[int, int]] = []
    affected_count = 0
    previous_low_motion_end: int | None = None
    for index, segment in enumerate(payload):
        if not isinstance(segment, dict) or set(segment) != SEGMENT_FIELDS:
            raise ValueError(f"invalid segment at index {index}: {path}")
        segment_type = segment["segment_type"]
        start = segment.get("start")
        end = segment.get("end")
        frame_count = segment.get("frame_count")
        mean_motion = segment.get("mean_motion")
        reason = segment.get("reason")
        if (
            not isinstance(segment_type, str)
            or isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or isinstance(frame_count, bool)
            or not isinstance(frame_count, int)
            or start < 0
            or end < start
            or frame_count <= 0
            or frame_count > total_count
            or start not in source_indices
            or end not in source_indices
            or isinstance(mean_motion, bool)
            or not isinstance(mean_motion, (int, float))
            or not math.isfinite(mean_motion)
            or not isinstance(reason, str)
        ):
            if isinstance(frame_count, int) and not isinstance(frame_count, bool) and frame_count > total_count:
                raise ValueError(f"segment frame_count exceeds analyzed frames at index {index}: {path}")
            if isinstance(start, int) and isinstance(end, int) and (
                start not in source_indices or end not in source_indices
            ):
                raise ValueError(f"segment endpoint is outside frame metrics at index {index}: {path}")
            raise ValueError(f"invalid segment at index {index}: {path}")
        if segment_type != "low_motion_review":
            continue
        if previous_low_motion_end is not None and start <= previous_low_motion_end:
            raise ValueError(f"low_motion_review ranges overlap at segment index {index}: {path}")
        previous_low_motion_end = end
        ranges.append((start, end))
        affected_count += frame_count
        if affected_count > total_count:
            raise ValueError(f"low_motion_review affected count exceeds analyzed frames: {path}")
    return tuple(ranges), affected_count
