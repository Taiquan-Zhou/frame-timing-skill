from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from frame_timing_agent.timing_metrics import FrameMetric


DEGENERATE_STATIC_MOTION_MAX = 0.02
DEGENERATE_VERY_FAST_MOTION_MIN = 0.30


@dataclass(frozen=True)
class Segment:
    segment_type: str
    start: int
    end: int
    frame_count: int
    mean_motion: float
    reason: str


def detect_segments(
    metrics: list[FrameMetric],
    static_motion_quantile: float = 0.15,
    fast_motion_quantile: float = 0.70,
    very_fast_motion_quantile: float = 0.90,
    min_static_frames: int = 21,
    min_fast_frames: int = 5,
    static_window_min_low_ratio: float = 0.70,
    static_window_mean_multiplier: float = 2.00,
) -> list[Segment]:
    if not metrics:
        return []

    usable_metrics = [metric for metric in metrics if not metric.bad_quality_candidate]
    calibration_metrics = usable_metrics if usable_metrics else metrics
    calibration_motion_scores = [metric.motion_score for metric in calibration_metrics]

    static_threshold = float(np.quantile(calibration_motion_scores, static_motion_quantile))
    fast_threshold = float(np.quantile(calibration_motion_scores, fast_motion_quantile))
    very_fast_threshold = float(np.quantile(calibration_motion_scores, very_fast_motion_quantile))

    segments: list[Segment] = []
    run_type: str | None = None
    run_metrics: list[FrameMetric] = []

    classified_metrics: list[tuple[FrameMetric, str]] = []
    for metric in metrics:
        frame_type = _classify_motion(
            motion_score=metric.motion_score,
            static_threshold=static_threshold,
            fast_threshold=fast_threshold,
            very_fast_threshold=very_fast_threshold,
        )
        classified_metrics.append((metric, frame_type))
        if frame_type == run_type:
            run_metrics.append(metric)
            continue

        _append_segment_if_needed(
            segments=segments,
            run_type=run_type,
            run_metrics=run_metrics,
            static_threshold=static_threshold,
            fast_threshold=fast_threshold,
            very_fast_threshold=very_fast_threshold,
            min_static_frames=min_static_frames,
            min_fast_frames=min_fast_frames,
        )
        run_type = frame_type
        run_metrics = [metric]

    _append_segment_if_needed(
        segments=segments,
        run_type=run_type,
        run_metrics=run_metrics,
        static_threshold=static_threshold,
        fast_threshold=fast_threshold,
        very_fast_threshold=very_fast_threshold,
        min_static_frames=min_static_frames,
        min_fast_frames=min_fast_frames,
    )
    segments.extend(
        _detect_jittered_static_windows(
            classified_metrics=classified_metrics,
            existing_segments=segments,
            static_threshold=static_threshold,
            fast_threshold=fast_threshold,
            very_fast_threshold=very_fast_threshold,
            min_static_frames=min_static_frames,
            min_low_ratio=static_window_min_low_ratio,
            mean_multiplier=static_window_mean_multiplier,
        )
    )
    return sorted(segments, key=lambda segment: (segment.start, segment.end, segment.segment_type))


def _classify_motion(
    motion_score: float,
    static_threshold: float,
    fast_threshold: float,
    very_fast_threshold: float,
) -> str:
    if _thresholds_are_degenerate(static_threshold, fast_threshold, very_fast_threshold):
        if motion_score <= DEGENERATE_STATIC_MOTION_MAX:
            return "static"
        if motion_score >= DEGENERATE_VERY_FAST_MOTION_MIN:
            return "very_fast_motion"
        return "normal"

    if motion_score <= static_threshold:
        return "static"
    if motion_score >= very_fast_threshold:
        return "very_fast_motion"
    if motion_score >= fast_threshold and motion_score > static_threshold:
        return "fast_motion"
    return "normal"


def _thresholds_are_degenerate(
    static_threshold: float,
    fast_threshold: float,
    very_fast_threshold: float,
) -> bool:
    return bool(
        np.isclose(static_threshold, fast_threshold)
        and np.isclose(fast_threshold, very_fast_threshold)
    )


def _append_segment_if_needed(
    segments: list[Segment],
    run_type: str | None,
    run_metrics: list[FrameMetric],
    static_threshold: float,
    fast_threshold: float,
    very_fast_threshold: float,
    min_static_frames: int,
    min_fast_frames: int,
) -> None:
    if not run_type or run_type == "normal" or not run_metrics:
        return

    frame_count = len(run_metrics)
    min_frames = min_static_frames if run_type == "static" else min_fast_frames
    if frame_count < min_frames:
        return

    mean_motion = float(sum(metric.motion_score for metric in run_metrics) / frame_count)
    threshold = {
        "static": static_threshold,
        "fast_motion": fast_threshold,
        "very_fast_motion": very_fast_threshold,
    }[run_type]
    comparator = "<=" if run_type == "static" else ">="

    segments.append(
        Segment(
            segment_type=run_type,
            start=run_metrics[0].source_index,
            end=run_metrics[-1].source_index,
            frame_count=frame_count,
            mean_motion=mean_motion,
            reason=(
                f"{run_type} run length {frame_count} with mean_motion={mean_motion:.6f}, "
                f"threshold {comparator} {threshold:.6f}"
            ),
        )
    )


def _detect_jittered_static_windows(
    classified_metrics: list[tuple[FrameMetric, str]],
    existing_segments: list[Segment],
    static_threshold: float,
    fast_threshold: float,
    very_fast_threshold: float,
    min_static_frames: int,
    min_low_ratio: float,
    mean_multiplier: float,
) -> list[Segment]:
    calm_runs: list[list[FrameMetric]] = []
    current: list[FrameMetric] = []
    hard_break_segments = [
        segment
        for segment in existing_segments
        if segment.segment_type in {"fast_motion", "very_fast_motion"}
    ]
    for metric, frame_type in classified_metrics:
        if _source_in_any_segment(metric.source_index, hard_break_segments) or frame_type == "very_fast_motion":
            if current:
                calm_runs.append(current)
                current = []
            continue
        current.append(metric)
    if current:
        calm_runs.append(current)

    jitter_segments: list[Segment] = []
    for run in calm_runs:
        if len(run) < min_static_frames:
            continue
        if _overlaps_existing_segment(run[0].source_index, run[-1].source_index, existing_segments):
            continue

        motions = [metric.motion_score for metric in run]
        relaxed_static_threshold = static_threshold * 2.2
        low_count = sum(1 for motion in motions if motion <= relaxed_static_threshold)
        low_ratio = low_count / len(run)
        mean_motion = float(sum(motions) / len(motions))
        max_motion = max(motions)
        if low_ratio < min_low_ratio:
            continue
        if mean_motion > static_threshold * mean_multiplier:
            continue
        if max_motion >= very_fast_threshold:
            continue

        jitter_segments.append(
            Segment(
                segment_type="static",
                start=run[0].source_index,
                end=run[-1].source_index,
                frame_count=len(run),
                mean_motion=mean_motion,
                reason=(
                    "jittered static window with "
                    f"low_motion_ratio={low_ratio:.3f}, mean_motion={mean_motion:.6f}, "
                    f"relaxed_static_threshold={relaxed_static_threshold:.6f}, "
                    f"fast_threshold={fast_threshold:.6f}, very_fast_threshold={very_fast_threshold:.6f}"
                ),
            )
        )
    return jitter_segments


def _overlaps_existing_segment(start: int, end: int, segments: list[Segment]) -> bool:
    return any(start <= segment.end and segment.start <= end for segment in segments)


def _source_in_any_segment(source_index: int, segments: list[Segment]) -> bool:
    return any(segment.start <= source_index <= segment.end for segment in segments)
