from __future__ import annotations

from dataclasses import dataclass

from frame_timing_agent.motion_estimator import MotionEstimate


@dataclass(frozen=True)
class JitterRange:
    start: int
    end: int
    frame_count: int
    mean_jitter_score: float
    reason: str


def score_jitter(
    estimates: list[MotionEstimate],
    min_motion: float = 2.0,
    min_response: float = 0.02,
) -> dict[int, float]:
    scores = {estimate.source_index: 0.0 for estimate in estimates}
    ordered = sorted(estimates, key=lambda item: item.output_index)
    for previous, current in zip(ordered, ordered[1:]):
        if previous.magnitude < min_motion or current.magnitude < min_motion:
            continue
        if previous.response < min_response or current.response < min_response:
            continue
        dot = previous.dx * current.dx + previous.dy * current.dy
        if dot < 0:
            score = previous.magnitude + current.magnitude
            scores[previous.source_index] = max(scores[previous.source_index], score)
            scores[current.source_index] = max(scores[current.source_index], score)
    return scores


def detect_jitter_ranges(
    estimates: list[MotionEstimate],
    min_jitter_frames: int = 5,
    min_motion: float = 2.0,
    min_response: float = 0.02,
) -> list[JitterRange]:
    if min_jitter_frames <= 0:
        raise ValueError(f"min_jitter_frames must be positive: {min_jitter_frames}")
    ordered = sorted(estimates, key=lambda item: item.output_index)
    if not ordered:
        return []

    scores = score_jitter(ordered, min_motion=min_motion, min_response=min_response)
    ranges: list[JitterRange] = []
    current: list[MotionEstimate] = []
    for estimate in ordered:
        if scores.get(estimate.source_index, 0.0) > 0.0:
            current.append(estimate)
            continue
        _append_range_if_needed(ranges, current, scores, min_jitter_frames)
        current = []
    _append_range_if_needed(ranges, current, scores, min_jitter_frames)
    return ranges


def _append_range_if_needed(
    ranges: list[JitterRange],
    estimates: list[MotionEstimate],
    scores: dict[int, float],
    min_jitter_frames: int,
) -> None:
    if len(estimates) < min_jitter_frames:
        return
    jitter_scores = [scores[estimate.source_index] for estimate in estimates]
    ranges.append(
        JitterRange(
            start=estimates[0].source_index,
            end=estimates[-1].source_index,
            frame_count=len(estimates),
            mean_jitter_score=sum(jitter_scores) / len(jitter_scores),
            reason="alternating high-frequency camera motion",
        )
    )
