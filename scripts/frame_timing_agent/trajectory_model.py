from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

import numpy as np
import numpy.typing as npt

from frame_timing_agent.motion_model import MotionConfig, MotionSample


@dataclass(frozen=True)
class MotionDecision:
    source_index: int
    output_index: int
    kind: str
    jitter_score: float
    jitter_confidence: float
    normalized_translation_residual: float
    rotation_residual_deg: float
    reason: str


def decompose_camera_trajectory(
    samples: Sequence[MotionSample],
    fps: float,
    analysis_diagonal: float,
    config: MotionConfig,
) -> tuple[MotionDecision, ...]:
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be a positive finite number")
    if not math.isfinite(analysis_diagonal) or analysis_diagonal <= 0:
        raise ValueError("analysis_diagonal must be a positive finite number")
    if not samples:
        return ()

    window_sizes = _window_sizes(config.smoothing_windows_seconds, fps)
    if len(samples) < window_sizes[-1] + 2:
        return tuple(_review_decision(sample, "insufficient_multiscale_context") for sample in samples)

    cumulative_x = np.cumsum(np.asarray([sample.dx for sample in samples], dtype=np.float64))
    cumulative_y = np.cumsum(np.asarray([sample.dy for sample in samples], dtype=np.float64))
    cumulative_rotation = np.cumsum(np.asarray([sample.rotation_deg for sample in samples], dtype=np.float64))
    robust_x = _repair_low_confidence_trajectory(cumulative_x, samples, config.minimum_inlier_ratio)
    robust_y = _repair_low_confidence_trajectory(cumulative_y, samples, config.minimum_inlier_ratio)
    robust_rotation = _repair_low_confidence_trajectory(cumulative_rotation, samples, config.minimum_inlier_ratio)

    votes: list[npt.NDArray[np.bool_]] = []
    ambiguities: list[npt.NDArray[np.bool_]] = []
    scale_scores: list[npt.NDArray[np.float64]] = []
    translation_residuals: list[npt.NDArray[np.float64]] = []
    rotation_residuals: list[npt.NDArray[np.float64]] = []
    for window_size in window_sizes:
        vote, ambiguous, score, translation_residual, rotation_residual = _scale_jitter_evidence(
            robust_x,
            robust_y,
            robust_rotation,
            window_size,
            analysis_diagonal,
            config.decision_deadband_ratio,
        )
        votes.append(vote)
        ambiguities.append(ambiguous)
        scale_scores.append(score)
        translation_residuals.append(translation_residual)
        rotation_residuals.append(rotation_residual)

    vote_count = np.sum(np.stack(votes), axis=0)
    ambiguous_any = np.any(np.stack(ambiguities), axis=0)
    jitter_scores = np.mean(np.stack(scale_scores), axis=0)
    mean_translation_residual = np.mean(np.stack(translation_residuals), axis=0)
    mean_rotation_residual = np.mean(np.stack(rotation_residuals), axis=0)
    decisions: list[MotionDecision] = []
    for index, sample in enumerate(samples):
        decisions.append(
            _decision_for_sample(
                sample,
                int(vote_count[index]),
                bool(ambiguous_any[index]),
                float(jitter_scores[index]),
                float(mean_translation_residual[index]),
                float(mean_rotation_residual[index]),
                analysis_diagonal,
                config,
            )
        )
    return tuple(decisions)


def _decision_for_sample(
    sample: MotionSample,
    vote_count: int,
    ambiguous: bool,
    jitter_score: float,
    translation_residual: float,
    rotation_residual: float,
    analysis_diagonal: float,
    config: MotionConfig,
) -> MotionDecision:
    if _is_spatially_uncertain(sample, analysis_diagonal, config):
        return _review_decision(sample, "spatial_motion_uncertain", translation_residual, rotation_residual)
    if vote_count == 3:
        return MotionDecision(
            sample.source_index,
            sample.output_index,
            "jitter",
            jitter_score,
            sample.confidence,
            translation_residual,
            rotation_residual,
            "multiscale_jitter_consensus",
        )
    if vote_count == 2 or ambiguous:
        return _review_decision(sample, "multiscale_motion_disagreement", translation_residual, rotation_residual)
    if sample.magnitude_px / analysis_diagonal > 0.0005 or abs(sample.rotation_deg) > 0.03:
        return MotionDecision(
            sample.source_index,
            sample.output_index,
            "active_motion",
            jitter_score,
            sample.confidence,
            translation_residual,
            rotation_residual,
            "coherent_active_motion",
        )
    return MotionDecision(
        sample.source_index,
        sample.output_index,
        "static",
        jitter_score,
        sample.confidence,
        translation_residual,
        rotation_residual,
        "low_motion_high_confidence",
    )


def _window_sizes(windows_seconds: tuple[float, float, float], fps: float) -> tuple[int, int, int]:
    sizes: list[int] = []
    for seconds in windows_seconds:
        size = max(3, round(seconds * fps))
        if size % 2 == 0:
            size += 1
        if sizes and size <= sizes[-1]:
            size = sizes[-1] + 2
        sizes.append(size)
    return cast(tuple[int, int, int], tuple(sizes))


def _repair_low_confidence_trajectory(
    values: npt.NDArray[np.float64],
    samples: Sequence[MotionSample],
    minimum_confidence: float,
) -> npt.NDArray[np.float64]:
    repaired = values.copy()
    for index in range(1, len(values) - 1):
        if samples[index].confidence < minimum_confidence:
            repaired[index] = float(np.median(values[index - 1 : index + 2]))
    return repaired


def _weighted_moving_average(values: npt.NDArray[np.float64], window_size: int) -> npt.NDArray[np.float64]:
    radius = window_size // 2
    weights = np.concatenate(
        (np.arange(1, radius + 2, dtype=np.float64), np.arange(radius, 0, -1, dtype=np.float64))
    )
    weights /= np.sum(weights)
    padded = np.pad(values, (radius, radius), mode="edge")
    return np.convolve(padded, weights, mode="valid")


def _scale_jitter_evidence(
    cumulative_x: npt.NDArray[np.float64],
    cumulative_y: npt.NDArray[np.float64],
    cumulative_rotation: npt.NDArray[np.float64],
    window_size: int,
    analysis_diagonal: float,
    deadband_ratio: float,
) -> tuple[
    npt.NDArray[np.bool_],
    npt.NDArray[np.bool_],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
]:
    residual_x = cumulative_x - _weighted_moving_average(cumulative_x, window_size)
    residual_y = cumulative_y - _weighted_moving_average(cumulative_y, window_size)
    residual_rotation = cumulative_rotation - _weighted_moving_average(cumulative_rotation, window_size)
    normalized_residual = np.hypot(residual_x, residual_y) / analysis_diagonal
    normalized_velocity = np.hypot(np.gradient(residual_x), np.gradient(residual_y)) / analysis_diagonal
    normalized_acceleration = np.hypot(
        np.gradient(np.gradient(residual_x)), np.gradient(np.gradient(residual_y))
    ) / analysis_diagonal
    rotation_residual = np.abs(residual_rotation)
    evidence_window = min(9, window_size)
    if evidence_window % 2 == 0:
        evidence_window -= 1
    residual_energy = _rolling_mean(normalized_residual, evidence_window)
    velocity_energy = _rolling_mean(normalized_velocity, evidence_window)
    acceleration_energy = _rolling_mean(normalized_acceleration, evidence_window)
    rotation_energy = _rolling_mean(rotation_residual, evidence_window)
    reversal_rate = _reversal_rate(residual_x, residual_y, evidence_window)

    translation_threshold = 0.0015
    velocity_threshold = 0.0010
    acceleration_threshold = 0.0008
    rotation_threshold = 0.10
    high = 1.0 + deadband_ratio
    low = 1.0 - deadband_ratio
    translation_high = (
        (residual_energy > translation_threshold * high)
        & (velocity_energy > velocity_threshold * high)
        & (acceleration_energy > acceleration_threshold * high)
    )
    rotation_high = rotation_energy > rotation_threshold * high
    reversal_high = reversal_rate >= 0.22
    vote = (translation_high | rotation_high) & reversal_high
    translation_near = (
        (residual_energy >= translation_threshold * low)
        & (velocity_energy >= velocity_threshold * low)
        & (acceleration_energy >= acceleration_threshold * low)
    )
    rotation_near = rotation_energy >= rotation_threshold * low
    ambiguous = ((translation_near | rotation_near) & (reversal_rate >= 0.12)) & ~vote
    score = np.clip(
        0.45 * residual_energy / translation_threshold
        + 0.25 * velocity_energy / velocity_threshold
        + 0.20 * acceleration_energy / acceleration_threshold
        + 0.10 * rotation_energy / rotation_threshold,
        0.0,
        1.0,
    )
    return vote, ambiguous, score, residual_energy, rotation_energy


def _rolling_mean(values: npt.NDArray[np.float64], window_size: int) -> npt.NDArray[np.float64]:
    radius = window_size // 2
    padded = np.pad(values, (radius, radius), mode="edge")
    kernel = np.full(window_size, 1.0 / window_size, dtype=np.float64)
    return np.convolve(padded, kernel, mode="valid")


def _reversal_rate(
    residual_x: npt.NDArray[np.float64],
    residual_y: npt.NDArray[np.float64],
    window_size: int,
) -> npt.NDArray[np.float64]:
    velocity_x = np.gradient(residual_x)
    velocity_y = np.gradient(residual_y)
    dominant_velocity = np.where(np.abs(velocity_x) >= np.abs(velocity_y), velocity_x, velocity_y)
    signs = np.sign(dominant_velocity)
    reversals = np.zeros(len(signs), dtype=np.float64)
    reversals[1:] = (signs[1:] * signs[:-1] < 0).astype(np.float64)
    return _rolling_mean(reversals, window_size)


def _is_spatially_uncertain(sample: MotionSample, analysis_diagonal: float, config: MotionConfig) -> bool:
    if sample.fallback_reason == "initial_frame":
        return False
    normalized_threshold = config.ransac_reprojection_threshold / analysis_diagonal
    lower_limit = 1.0 - config.decision_deadband_ratio
    upper_limit = 1.0 + config.decision_deadband_ratio
    return (
        sample.confidence < config.minimum_inlier_ratio
        or sample.normalized_residual_spatial_iqr >= 2.0 * normalized_threshold * lower_limit
        or sample.normalized_residual_spatial_p90 >= 4.0 * normalized_threshold * lower_limit
        or sample.inlier_spatial_coverage <= 0.25 * upper_limit
    )


def _review_decision(
    sample: MotionSample,
    reason: str,
    normalized_translation_residual: float = 0.0,
    rotation_residual_deg: float = 0.0,
) -> MotionDecision:
    return MotionDecision(
        sample.source_index,
        sample.output_index,
        "review_required",
        0.0,
        0.0,
        normalized_translation_residual,
        rotation_residual_deg,
        reason,
    )
