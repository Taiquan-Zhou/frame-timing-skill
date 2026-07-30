from __future__ import annotations

from math import floor

from frame_timing_agent.jitter_detector import JitterRange, score_jitter
from frame_timing_agent.motion_estimator import MotionEstimate


def select_stable_sources(
    estimates: list[MotionEstimate],
    jitter_range: JitterRange,
    max_output_ratio: float = 0.60,
    min_keep_count: int = 2,
) -> list[int]:
    if not 0 < max_output_ratio <= 1:
        raise ValueError(f"max_output_ratio must be in (0, 1]: {max_output_ratio}")
    if min_keep_count <= 0:
        raise ValueError(f"min_keep_count must be positive: {min_keep_count}")

    selected_estimates = [
        estimate for estimate in estimates if jitter_range.start <= estimate.source_index <= jitter_range.end
    ]
    if not selected_estimates:
        return []

    max_count = max(min_keep_count, floor(len(selected_estimates) * max_output_ratio))
    max_count = min(max_count, len(selected_estimates))
    scores = score_jitter(selected_estimates)
    if max_count == 1:
        chosen_estimates = [_best_quality_estimate(selected_estimates, scores)]
    elif max_count == len(selected_estimates):
        chosen_estimates = selected_estimates
    else:
        chosen_estimates = _select_covered_estimates(selected_estimates, scores, max_count)
    chosen = sorted(estimate.source_index for estimate in chosen_estimates)
    return chosen


def _select_covered_estimates(
    estimates: list[MotionEstimate],
    scores: dict[int, float],
    count: int,
) -> list[MotionEstimate]:
    chosen: list[MotionEstimate] = []
    used_sources: set[int] = set()
    last_position = len(estimates) - 1
    for slot in range(count):
        ideal_position = round(slot * last_position / (count - 1))
        candidates = [
            (position, estimate)
            for position, estimate in enumerate(estimates)
            if estimate.source_index not in used_sources
        ]
        _, selected = min(
            candidates,
            key=lambda item: (
                item[1].bad_quality_candidate,
                abs(item[0] - ideal_position),
                scores.get(item[1].source_index, 0.0),
                item[1].magnitude,
                item[1].source_index,
            ),
        )
        chosen.append(selected)
        used_sources.add(selected.source_index)
    return chosen


def _best_quality_estimate(
    estimates: list[MotionEstimate],
    scores: dict[int, float],
) -> MotionEstimate:
    return min(estimates, key=lambda estimate: _quality_key(estimate, scores))


def _quality_key(estimate: MotionEstimate, scores: dict[int, float]) -> tuple[bool, float, float, int]:
    return (
        estimate.bad_quality_candidate,
        scores.get(estimate.source_index, 0.0),
        estimate.magnitude,
        estimate.source_index,
    )
