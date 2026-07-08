from __future__ import annotations

from collections.abc import Sequence

from frame_timing_agent.contracts import AnalysisRange


def frame_kind(source_index: int, ranges: tuple[AnalysisRange, ...]) -> str:
    matching = [item for item in ranges if item.start <= source_index <= item.end]
    if any(item.kind == "review_required" for item in matching):
        return "review_required"
    for kind in ("active_motion", "jitter", "static"):
        if any(item.kind == kind for item in matching):
            return kind
    return "review_required"


def frame_kinds(source_indices: Sequence[int], ranges: tuple[AnalysisRange, ...]) -> tuple[str, ...]:
    return tuple(frame_kind(source_index, ranges) for source_index in source_indices)


def range_confidence(source_index: int, ranges: tuple[AnalysisRange, ...]) -> float:
    matching = [item.confidence for item in ranges if item.start <= source_index <= item.end]
    return min(matching, default=0.0)


def static_range_endpoint_positions(
    source_indices: Sequence[int],
    kinds: Sequence[str],
    ranges: tuple[AnalysisRange, ...],
    minimum_confidence: float,
) -> set[int]:
    endpoints: set[int] = set()
    for item in ranges:
        if item.kind != "static" or item.confidence < minimum_confidence:
            continue
        positions = [
            position
            for position, (source_index, kind) in enumerate(zip(source_indices, kinds, strict=True))
            if kind == "static" and item.start <= source_index <= item.end
        ]
        if positions:
            endpoints.update((positions[0], positions[-1]))
    return endpoints


def maximum_consecutive_drops(frame_count: int, selected_positions: set[int]) -> int:
    maximum = current = 0
    for position in range(frame_count):
        if position in selected_positions:
            current = 0
        else:
            current += 1
            maximum = max(maximum, current)
    return maximum


def maximum_non_static_consecutive_drops(kinds: Sequence[str], selected_positions: set[int]) -> int:
    maximum = current = 0
    for position, kind in enumerate(kinds):
        if kind == "static" or position in selected_positions:
            current = 0
        else:
            current += 1
            maximum = max(maximum, current)
    return maximum


def deletion_respects_limit(position: int, frame_count: int, selected_positions: set[int], limit: int) -> bool:
    consecutive = 1
    left = position - 1
    while left >= 0 and left not in selected_positions:
        consecutive += 1
        left -= 1
    right = position + 1
    while right < frame_count and right not in selected_positions:
        consecutive += 1
        right += 1
    return consecutive <= limit


def deletion_respects_non_static_limit(
    position: int,
    kinds: Sequence[str],
    selected_positions: set[int],
    limit: int,
) -> bool:
    if kinds[position] == "static":
        return True
    consecutive = 1
    left = position - 1
    while left >= 0 and kinds[left] != "static" and left not in selected_positions:
        consecutive += 1
        left -= 1
    right = position + 1
    while right < len(kinds) and kinds[right] != "static" and right not in selected_positions:
        consecutive += 1
        right += 1
    return consecutive <= limit


def non_static_retention_ratio(kinds: Sequence[str], selected_positions: set[int]) -> float:
    non_static_positions = {position for position, kind in enumerate(kinds) if kind != "static"}
    if not non_static_positions:
        return 1.0
    return len(non_static_positions & selected_positions) / len(non_static_positions)
