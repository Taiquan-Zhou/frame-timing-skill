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
        estimate
        for estimate in estimates
        if jitter_range.start <= estimate.source_index <= jitter_range.end
    ]
    if not selected_estimates:
        return []

    max_count = max(min_keep_count, floor(len(selected_estimates) * max_output_ratio))
    max_count = min(max_count, len(selected_estimates))
    scores = score_jitter(selected_estimates)
    ranked = sorted(
        selected_estimates,
        key=lambda estimate: (
            estimate.bad_quality_candidate,
            scores.get(estimate.source_index, 0.0),
            estimate.magnitude,
            estimate.source_index,
        ),
    )
    chosen = sorted(estimate.source_index for estimate in ranked[:max_count])
    return chosen
