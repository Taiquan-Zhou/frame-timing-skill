from __future__ import annotations

from pathlib import Path
from typing import Any

from frame_timing_agent.frame_source import FrameRecord
from frame_timing_agent.jitter_detector import detect_jitter_ranges
from frame_timing_agent.motion_estimator import MotionEstimate
from frame_timing_agent.stable_frame_selector import select_stable_sources


def build_jitter_reduction_strategy(
    records: list[FrameRecord],
    estimates: list[MotionEstimate],
    frame_dir: str | Path,
    limit_first_n: int | None,
    max_output_ratio: float = 0.60,
    min_jitter_frames: int = 5,
    min_motion: float = 2.0,
    min_response: float = 0.02,
    min_sharpness: float = 100.0,
) -> dict[str, Any]:
    operations: list[dict[str, Any]] = []
    jitter_ranges = detect_jitter_ranges(
        estimates,
        min_jitter_frames=min_jitter_frames,
        min_motion=min_motion,
        min_response=min_response,
    )
    available_sources = {record.source_index for record in records}

    for jitter_range in jitter_ranges:
        sources = [
            source
            for source in select_stable_sources(
                estimates,
                jitter_range,
                max_output_ratio=max_output_ratio,
            )
            if source in available_sources
        ]
        if not sources:
            continue
        operations.append(
            {
                "op": "select_sources",
                "range": {"start": jitter_range.start, "end": jitter_range.end},
                "sources": sources,
                "reason": (
                    "jitter_reduction_v2 stable keyframe selection; "
                    f"mean_jitter_score={jitter_range.mean_jitter_score:.6f}"
                ),
                "source": "jitter_reduction_v2",
            }
        )

    return {
        "version": 2,
        "input": {
            "frame_dir_name": Path(frame_dir).name,
            "limit_first_n": limit_first_n,
        },
        "options": {
            "jitter_reduction_mode": "v2",
            "max_output_ratio": max_output_ratio,
            "min_jitter_frames": min_jitter_frames,
            "min_motion": min_motion,
            "min_response": min_response,
            "min_sharpness": min_sharpness,
            "interpret_ranges_by": "source_index",
            "pixel_policy": "copy_source_frames_without_warping",
        },
        "operations": operations,
    }


def merge_jitter_with_base_strategy(
    base_strategy: dict[str, Any],
    jitter_strategy: dict[str, Any],
    records: list[FrameRecord],
) -> dict[str, Any]:
    jitter_operations = sorted(
        jitter_strategy.get("operations", []),
        key=lambda operation: (int(operation["range"]["start"]), int(operation["range"]["end"])),
    )
    source_indices = sorted({record.source_index for record in records})
    manual_operations = [
        operation
        for operation in base_strategy.get("operations", [])
        if operation.get("source") == "manual_override"
    ]
    merge_warnings: list[str] = []
    jitter_operations = _clip_jitter_operations_around_manual_overrides(
        jitter_operations,
        manual_operations,
        merge_warnings,
    )
    merged_operations: list[dict[str, Any]] = []

    for base_operation in base_strategy.get("operations", []):
        if base_operation.get("source") == "manual_override":
            merged_operations.append(base_operation)
            continue
        merged_operations.extend(_clip_base_operation(base_operation, jitter_operations, source_indices))
    merged_operations.extend(jitter_operations)
    merged_operations.sort(key=lambda operation: (int(operation["range"]["start"]), int(operation["range"]["end"]), operation["op"]))

    merged_options = dict(base_strategy.get("options", {}))
    merged_options.update(jitter_strategy.get("options", {}))
    merged_options["mode"] = "reconstruction_balanced"
    if merge_warnings:
        merged_options["merge_warnings"] = merge_warnings

    return {
        "version": 2,
        "input": base_strategy.get("input", jitter_strategy.get("input", {})),
        "options": merged_options,
        "operations": merged_operations,
    }


def _clip_base_operation(
    operation: dict[str, Any],
    jitter_operations: list[dict[str, Any]],
    source_indices: list[int],
) -> list[dict[str, Any]]:
    source_range = operation["range"]
    start = int(source_range["start"])
    end = int(source_range["end"])
    covered_ranges = [
        (int(jitter_operation["range"]["start"]), int(jitter_operation["range"]["end"]))
        for jitter_operation in jitter_operations
        if start <= int(jitter_operation["range"]["end"]) and int(jitter_operation["range"]["start"]) <= end
    ]
    if not covered_ranges:
        return [operation]

    remaining_ranges = _subtract_ranges(start, end, covered_ranges)
    original_sources = _sources_in_range(source_indices, start, end)
    split_operations = []
    for split_start, split_end in remaining_ranges:
        split_sources = _sources_in_range(source_indices, split_start, split_end)
        if not split_sources:
            continue
        split_operation = dict(operation)
        split_operation["range"] = {"start": split_start, "end": split_end}
        split_operation["reason"] = f"{operation.get('reason', '')}; clipped around jitter_reduction_v2".strip("; ")
        if operation.get("op") == "keep_uniform":
            split_operation["count"] = _scaled_keep_count(
                requested_count=int(operation["count"]),
                original_count=len(original_sources),
                split_count=len(split_sources),
            )
        split_operations.append(split_operation)
    return split_operations


def _clip_jitter_operations_around_manual_overrides(
    jitter_operations: list[dict[str, Any]],
    manual_operations: list[dict[str, Any]],
    merge_warnings: list[str],
) -> list[dict[str, Any]]:
    if not manual_operations:
        return jitter_operations

    manual_ranges = [
        (int(operation["range"]["start"]), int(operation["range"]["end"]))
        for operation in manual_operations
    ]
    retained_operations: list[dict[str, Any]] = []
    for operation in jitter_operations:
        start = int(operation["range"]["start"])
        end = int(operation["range"]["end"])
        overlapping_manual_ranges = [
            (manual_start, manual_end)
            for manual_start, manual_end in manual_ranges
            if start <= manual_end and manual_start <= end
        ]
        if not overlapping_manual_ranges:
            retained_operations.append(operation)
            continue

        remaining_ranges = _subtract_ranges(start, end, overlapping_manual_ranges)
        split_operations = _split_select_sources_operation(operation, remaining_ranges)
        if not split_operations:
            merge_warnings.append("jitter_reduction_v2 overlapped manual_override and was skipped")
            continue
        merge_warnings.append("jitter_reduction_v2 overlapped manual_override and was clipped")
        retained_operations.extend(split_operations)
    return retained_operations


def _split_select_sources_operation(
    operation: dict[str, Any],
    ranges: list[tuple[int, int]],
) -> list[dict[str, Any]]:
    if operation.get("op") != "select_sources":
        return []

    selected_sources = [int(source) for source in operation.get("sources", [])]
    split_operations = []
    for start, end in ranges:
        sources = [source for source in selected_sources if start <= source <= end]
        if not sources:
            continue
        split_operation = dict(operation)
        split_operation["range"] = {"start": start, "end": end}
        split_operation["sources"] = sources
        split_operation["reason"] = f"{operation.get('reason', '')}; clipped around manual_override".strip("; ")
        split_operations.append(split_operation)
    return split_operations


def _subtract_ranges(start: int, end: int, covered_ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    remaining = [(start, end)]
    for covered_start, covered_end in sorted(covered_ranges):
        next_remaining = []
        for current_start, current_end in remaining:
            if covered_end < current_start or current_end < covered_start:
                next_remaining.append((current_start, current_end))
                continue
            if current_start < covered_start:
                next_remaining.append((current_start, covered_start - 1))
            if covered_end < current_end:
                next_remaining.append((covered_end + 1, current_end))
        remaining = next_remaining
    return remaining


def _sources_in_range(source_indices: list[int], start: int, end: int) -> list[int]:
    return [source_index for source_index in source_indices if start <= source_index <= end]


def _scaled_keep_count(requested_count: int, original_count: int, split_count: int) -> int:
    if requested_count <= 0:
        raise ValueError(f"requested keep count must be positive: {requested_count}")
    if original_count <= 0:
        return min(requested_count, split_count)
    scaled = round(requested_count * split_count / original_count)
    return max(1, min(split_count, scaled))
