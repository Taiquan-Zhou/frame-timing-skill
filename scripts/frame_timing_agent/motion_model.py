from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TypeGuard, cast

import cv2
import numpy as np
import numpy.typing as npt

from frame_timing_agent.frame_source import FrameRecord


@dataclass(frozen=True)
class MotionConfig:
    analysis_width: int = 960
    max_features: int = 400
    forward_backward_error: float = 1.0
    ransac_reprojection_threshold: float = 2.0
    minimum_inlier_ratio: float = 0.45
    smoothing_windows_seconds: tuple[float, float, float] = (0.15, 0.35, 0.75)
    decision_deadband_ratio: float = 0.10

    def __post_init__(self) -> None:
        if isinstance(self.analysis_width, bool) or not isinstance(self.analysis_width, int) or self.analysis_width < 32:
            raise ValueError("analysis_width must be at least 32")
        if isinstance(self.max_features, bool) or not isinstance(self.max_features, int) or self.max_features < 8:
            raise ValueError("max_features must be at least 8")
        if not _is_positive_finite_number(self.forward_backward_error) or not _is_positive_finite_number(
            self.ransac_reprojection_threshold
        ):
            raise ValueError("motion error thresholds must be positive")
        if not _is_finite_number(self.minimum_inlier_ratio) or not 0 < self.minimum_inlier_ratio <= 1:
            raise ValueError("minimum_inlier_ratio must be in (0, 1]")
        if not isinstance(self.smoothing_windows_seconds, tuple) or len(self.smoothing_windows_seconds) != 3 or any(
            not _is_positive_finite_number(value) for value in self.smoothing_windows_seconds
        ):
            raise ValueError("smoothing_windows_seconds must contain three positive finite values")
        if tuple(sorted(self.smoothing_windows_seconds)) != self.smoothing_windows_seconds:
            raise ValueError("smoothing_windows_seconds must be strictly increasing")
        if len(set(self.smoothing_windows_seconds)) != 3:
            raise ValueError("smoothing_windows_seconds must be strictly increasing")
        if not _is_finite_number(self.decision_deadband_ratio) or not 0 <= self.decision_deadband_ratio < 1:
            raise ValueError("decision_deadband_ratio must be in [0, 1)")


@dataclass(frozen=True)
class MotionSample:
    source_index: int
    output_index: int
    dx: float
    dy: float
    rotation_deg: float
    scale: float
    magnitude_px: float
    feature_count: int
    inlier_ratio: float
    reprojection_error: float
    normalized_residual_spatial_iqr: float
    normalized_residual_spatial_p90: float
    inlier_spatial_coverage: float
    response: float
    confidence: float
    fallback_reason: str | None


def estimate_camera_motion(
    records: Sequence[FrameRecord],
    config: MotionConfig,
) -> tuple[MotionSample, ...]:
    if not isinstance(config, MotionConfig):
        raise TypeError("config must be a MotionConfig")
    if not records:
        return ()

    first_gray = _load_analysis_gray(records[0], config.analysis_width)
    analysis_diagonal = math.hypot(first_gray.shape[1], first_gray.shape[0])
    samples = [_initial_sample(records[0])]
    previous_gray = first_gray

    for record in records[1:]:
        current_gray = _load_analysis_gray(record, config.analysis_width)
        if current_gray.shape != previous_gray.shape:
            raise ValueError(
                f"frame size mismatch after analysis resize: previous={previous_gray.shape}, current={current_gray.shape}"
            )
        samples.append(_estimate_pair(previous_gray, current_gray, record, config, analysis_diagonal))
        previous_gray = current_gray

    return tuple(samples)


def _initial_sample(record: FrameRecord) -> MotionSample:
    return MotionSample(
        source_index=record.source_index,
        output_index=record.output_index,
        dx=0.0,
        dy=0.0,
        rotation_deg=0.0,
        scale=1.0,
        magnitude_px=0.0,
        feature_count=0,
        inlier_ratio=0.0,
        reprojection_error=math.inf,
        normalized_residual_spatial_iqr=0.0,
        normalized_residual_spatial_p90=0.0,
        inlier_spatial_coverage=0.0,
        response=0.0,
        confidence=0.0,
        fallback_reason="initial_frame",
    )


def _estimate_pair(
    previous_gray: npt.NDArray[np.uint8],
    current_gray: npt.NDArray[np.uint8],
    record: FrameRecord,
    config: MotionConfig,
    analysis_diagonal: float,
) -> MotionSample:
    previous_points = cv2.goodFeaturesToTrack(
        previous_gray,
        maxCorners=config.max_features,
        qualityLevel=0.01,
        minDistance=5.0,
        blockSize=5,
    )
    if previous_points is None or len(previous_points) < 4:
        return _phase_fallback(previous_gray, current_gray, record, "insufficient_features")

    current_points_raw, forward_status_raw, _ = cv2.calcOpticalFlowPyrLK(
        previous_gray,
        current_gray,
        previous_points,
        np.empty_like(previous_points),
    )
    current_points = cast(npt.NDArray[np.float32], current_points_raw)
    forward_status = cast(npt.NDArray[np.uint8], forward_status_raw)
    if current_points is None or forward_status is None:
        return _phase_fallback(previous_gray, current_gray, record, "forward_flow_failed")
    backward_points_raw, backward_status_raw, _ = cv2.calcOpticalFlowPyrLK(
        current_gray,
        previous_gray,
        current_points,
        np.empty_like(current_points),
    )
    backward_points = cast(npt.NDArray[np.float32], backward_points_raw)
    backward_status = cast(npt.NDArray[np.uint8], backward_status_raw)
    if backward_points is None or backward_status is None:
        return _phase_fallback(previous_gray, current_gray, record, "backward_flow_failed")

    source = previous_points.reshape(-1, 2)
    target = current_points.reshape(-1, 2)
    backward = backward_points.reshape(-1, 2)
    valid = (
        forward_status.reshape(-1).astype(bool)
        & backward_status.reshape(-1).astype(bool)
        & (np.linalg.norm(source - backward, axis=1) <= config.forward_backward_error)
    )
    source = source[valid]
    target = target[valid]
    if len(source) < 4:
        return _phase_fallback(previous_gray, current_gray, record, "insufficient_consistent_tracks")

    order = np.lexsort((source[:, 1], source[:, 0]))
    source = np.ascontiguousarray(source[order], dtype=np.float32)
    target = np.ascontiguousarray(target[order], dtype=np.float32)
    matrix, inlier_mask = cv2.estimateAffinePartial2D(
        source,
        target,
        method=cv2.RANSAC,
        ransacReprojThreshold=config.ransac_reprojection_threshold,
        maxIters=2000,
        confidence=0.99,
        refineIters=10,
    )
    if matrix is None or inlier_mask is None:
        return _phase_fallback(previous_gray, current_gray, record, "ransac_failed")

    inliers = inlier_mask.reshape(-1).astype(bool)
    predicted = source @ matrix[:, :2].T + matrix[:, 2]
    residuals = np.linalg.norm(predicted - target, axis=1)
    normalized_residuals = residuals / analysis_diagonal
    inlier_ratio = float(np.mean(inliers))
    reprojection_error = float(np.median(residuals[inliers])) if np.any(inliers) else math.inf
    residual_iqr = float(np.quantile(normalized_residuals, 0.75) - np.quantile(normalized_residuals, 0.25))
    residual_p90 = float(np.quantile(normalized_residuals, 0.90))
    spatial_coverage = _inlier_grid_coverage(source[inliers], previous_gray.shape)

    a = float(matrix[0, 0])
    b = float(matrix[1, 0])
    scale = math.hypot(a, b)
    rotation_deg = math.degrees(math.atan2(b, a))
    dx = float(matrix[0, 2])
    dy = float(matrix[1, 2])
    confidence = _estimate_confidence(
        feature_count=len(source),
        inlier_ratio=inlier_ratio,
        reprojection_error=reprojection_error,
        spatial_coverage=spatial_coverage,
        residual_iqr=residual_iqr,
        residual_p90=residual_p90,
        analysis_diagonal=analysis_diagonal,
        config=config,
    )
    fallback_reason = None if confidence >= config.minimum_inlier_ratio else "low_spatial_confidence"
    return MotionSample(
        source_index=record.source_index,
        output_index=record.output_index,
        dx=dx,
        dy=dy,
        rotation_deg=rotation_deg,
        scale=scale,
        magnitude_px=math.hypot(dx, dy),
        feature_count=len(source),
        inlier_ratio=inlier_ratio,
        reprojection_error=reprojection_error,
        normalized_residual_spatial_iqr=residual_iqr,
        normalized_residual_spatial_p90=residual_p90,
        inlier_spatial_coverage=spatial_coverage,
        response=inlier_ratio,
        confidence=confidence,
        fallback_reason=fallback_reason,
    )


def _estimate_confidence(
    *,
    feature_count: int,
    inlier_ratio: float,
    reprojection_error: float,
    spatial_coverage: float,
    residual_iqr: float,
    residual_p90: float,
    analysis_diagonal: float,
    config: MotionConfig,
) -> float:
    feature_score = min(1.0, feature_count / max(8.0, config.max_features * 0.25))
    reprojection_score = math.exp(-reprojection_error / config.ransac_reprojection_threshold)
    coverage_score = min(1.0, spatial_coverage / 0.5)
    confidence = 0.35 * inlier_ratio + 0.25 * feature_score + 0.20 * reprojection_score + 0.20 * coverage_score

    if has_spatial_uncertainty(
        inlier_ratio=inlier_ratio,
        residual_iqr=residual_iqr,
        residual_p90=residual_p90,
        spatial_coverage=spatial_coverage,
        analysis_diagonal=analysis_diagonal,
        config=config,
    ):
        confidence = min(confidence, config.minimum_inlier_ratio - 0.05)
    return max(0.0, min(1.0, confidence))


def has_spatial_uncertainty(
    *,
    inlier_ratio: float,
    residual_iqr: float,
    residual_p90: float,
    spatial_coverage: float,
    analysis_diagonal: float,
    config: MotionConfig,
) -> bool:
    normalized_threshold = config.ransac_reprojection_threshold / analysis_diagonal
    lower_limit = 1.0 - config.decision_deadband_ratio
    upper_limit = 1.0 + config.decision_deadband_ratio
    return (
        residual_iqr >= 2.0 * normalized_threshold * lower_limit
        or residual_p90 >= 4.0 * normalized_threshold * lower_limit
        or spatial_coverage <= 0.25 * upper_limit
        or inlier_ratio <= config.minimum_inlier_ratio * upper_limit
    )


def _is_finite_number(value: object) -> TypeGuard[int | float]:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _is_positive_finite_number(value: object) -> bool:
    return _is_finite_number(value) and float(value) > 0


def _inlier_grid_coverage(points: npt.NDArray[np.float32], image_shape: tuple[int, ...]) -> float:
    if len(points) == 0:
        return 0.0
    height, width = image_shape[:2]
    x_cells = np.clip((points[:, 0] * 4 / max(width, 1)).astype(int), 0, 3)
    y_cells = np.clip((points[:, 1] * 4 / max(height, 1)).astype(int), 0, 3)
    occupied = {(int(x), int(y)) for x, y in zip(x_cells, y_cells, strict=True)}
    return len(occupied) / 16.0


def _phase_fallback(
    previous_gray: npt.NDArray[np.uint8],
    current_gray: npt.NDArray[np.uint8],
    record: FrameRecord,
    reason: str,
) -> MotionSample:
    shift, response = cv2.phaseCorrelate(
        previous_gray.astype(np.float32),
        current_gray.astype(np.float32),
    )
    dx, dy = (float(shift[0]), float(shift[1])) if all(math.isfinite(value) for value in shift) else (0.0, 0.0)
    response = float(response) if math.isfinite(response) else 0.0
    return MotionSample(
        source_index=record.source_index,
        output_index=record.output_index,
        dx=dx,
        dy=dy,
        rotation_deg=0.0,
        scale=1.0,
        magnitude_px=math.hypot(dx, dy),
        feature_count=0,
        inlier_ratio=0.0,
        reprojection_error=math.inf,
        normalized_residual_spatial_iqr=math.inf,
        normalized_residual_spatial_p90=math.inf,
        inlier_spatial_coverage=0.0,
        response=max(0.0, min(1.0, response)),
        confidence=min(0.4, max(0.0, response) * 0.4),
        fallback_reason=reason,
    )


def _load_analysis_gray(record: FrameRecord, analysis_width: int) -> npt.NDArray[np.uint8]:
    image = cv2.imread(str(record.path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"cannot read frame image: {record.path}")
    height, width = image.shape
    if width == analysis_width:
        return cast(npt.NDArray[np.uint8], image)
    analysis_height = max(1, round(height * analysis_width / width))
    interpolation = cv2.INTER_AREA if width > analysis_width else cv2.INTER_LINEAR
    return cast(
        npt.NDArray[np.uint8],
        cv2.resize(image, (analysis_width, analysis_height), interpolation=interpolation),
    )
