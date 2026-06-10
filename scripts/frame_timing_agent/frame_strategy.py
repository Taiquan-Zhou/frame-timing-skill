from __future__ import annotations

from pathlib import Path
from typing import Any

from frame_timing_agent.segment_detector import Segment


def build_strategy(
    segments: list[Segment],
    frame_dir: str | Path,
    limit_first_n: int | None,
    static_keep_count: int = 20,
    fast_motion_total_instances: int = 3,
    very_fast_motion_total_instances: int = 4,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    for segment in segments:
        operation = _operation_for_segment(
            segment,
            static_keep_count=static_keep_count,
            fast_motion_total_instances=fast_motion_total_instances,
            very_fast_motion_total_instances=very_fast_motion_total_instances,
        )
        if operation is not None:
            operation["source"] = "auto_detection"
            operations.append(operation)

    if overrides:
        operations = _apply_manual_overrides(operations, overrides)

    return {
        "version": 1,
        "input": {
            "frame_dir_name": Path(frame_dir).name,
            "limit_first_n": limit_first_n,
        },
        "options": {
            "static_keep_count": static_keep_count,
            "fast_motion_total_instances": fast_motion_total_instances,
            "very_fast_motion_total_instances": very_fast_motion_total_instances,
            "interpret_ranges_by": "source_index",
        },
        "operations": operations,
    }


def _apply_manual_overrides(auto_operations: list[dict[str, Any]], overrides: dict[str, Any]) -> list[dict[str, Any]]:
    manual_operations = _manual_override_operations(overrides)
    _validate_manual_ranges_do_not_overlap(manual_operations)

    retained_auto_operations = [
        operation
        for operation in auto_operations
        if not any(_ranges_overlap(operation["range"], manual["range"]) for manual in manual_operations)
    ]
    return retained_auto_operations + manual_operations


def _manual_override_operations(overrides: dict[str, Any]) -> list[dict[str, Any]]:
    operations: list[dict[str, Any]] = []

    for item in overrides.get("force_duplicate", []):
        start, end = _read_range(item)
        total_instances = int(item["total_instances"])
        if total_instances <= 0:
            raise ValueError(f"total_instances must be positive: {total_instances}")
        operations.append(
            {
                "op": "duplicate_range",
                "range": {"start": start, "end": end},
                "total_instances": total_instances,
                "reason": item.get("reason", "manual duplicate override"),
                "source": "manual_override",
            }
        )

    for item in overrides.get("force_keep_uniform", []):
        start, end = _read_range(item)
        count = int(item["count"])
        if count <= 0:
            raise ValueError(f"count must be positive: {count}")
        operations.append(
            {
                "op": "keep_uniform",
                "range": {"start": start, "end": end},
                "count": count,
                "reason": item.get("reason", "manual keep-uniform override"),
                "source": "manual_override",
            }
        )

    for item in overrides.get("ignore_range", []):
        start, end = _read_range(item)
        operations.append(
            {
                "op": "keep",
                "range": {"start": start, "end": end},
                "reason": item.get("reason", "manual ignore override"),
                "source": "manual_override",
            }
        )

    return operations


def _read_range(item: dict[str, Any]) -> tuple[int, int]:
    start = int(item["start"])
    end = int(item["end"])
    if start > end:
        raise ValueError(f"override range start must be <= end: {start}-{end}")
    return start, end


def _validate_manual_ranges_do_not_overlap(operations: list[dict[str, Any]]) -> None:
    for index, operation in enumerate(operations):
        for other in operations[index + 1 :]:
            if _ranges_overlap(operation["range"], other["range"]):
                raise ValueError(
                    "manual override ranges overlap: "
                    f"{operation['range']['start']}-{operation['range']['end']} and "
                    f"{other['range']['start']}-{other['range']['end']}"
                )


def _ranges_overlap(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return int(left["start"]) <= int(right["end"]) and int(right["start"]) <= int(left["end"])


def _operation_for_segment(
    segment: Segment,
    static_keep_count: int,
    fast_motion_total_instances: int,
    very_fast_motion_total_instances: int,
) -> dict[str, Any] | None:
    source_range = {"start": segment.start, "end": segment.end}

    if segment.segment_type == "static":
        if segment.frame_count <= static_keep_count:
            return None
        return {
            "op": "keep_uniform",
            "range": source_range,
            "count": static_keep_count,
            "reason": "long static section",
        }

    if segment.segment_type == "fast_motion":
        return {
            "op": "duplicate_range",
            "range": source_range,
            "total_instances": fast_motion_total_instances,
            "reason": "aggressive fast-motion compensation",
        }

    if segment.segment_type == "very_fast_motion":
        return {
            "op": "duplicate_range",
            "range": source_range,
            "total_instances": very_fast_motion_total_instances,
            "reason": "very aggressive fast-motion compensation",
        }

    return {
        "op": "mark_review",
        "range": source_range,
        "reason": f"segment retained for review: {segment.segment_type}",
    }
