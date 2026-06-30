from __future__ import annotations

import hashlib
import math
from collections.abc import Sequence
from pathlib import Path

import cv2
import numpy as np

from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AnalysisError,
    AnalysisRange,
    AnalysisResult,
    FrameAnalysis,
    QualitySummary,
    TrajectorySummary,
)
from frame_timing_agent.frame_source import FrameRecord
from frame_timing_agent.motion_model import (
    MotionConfig,
    MotionSample,
    estimate_camera_motion,
)
from frame_timing_agent.timing_metrics import FrameMetric, compute_frame_metrics
from frame_timing_agent.trajectory_model import MotionDecision, decompose_camera_trajectory


def analyze_records(
    records: Sequence[FrameRecord],
    fps: float,
    motion_config: MotionConfig,
) -> AnalysisResult:
    if not math.isfinite(fps) or fps <= 0:
        raise AnalysisError("fps must be a positive finite number", code="invalid_fps", fields=("fps",))
    normalized_records = _validate_and_sort_records(records)
    width, height = _validate_dimensions(normalized_records)
    input_digest = _input_digest(normalized_records)
    metrics = compute_frame_metrics(normalized_records)
    motion_samples = estimate_camera_motion(normalized_records, motion_config)
    analysis_height = max(1, round(height * motion_config.analysis_width / width))
    analysis_diagonal = math.hypot(motion_config.analysis_width, analysis_height)
    decisions = decompose_camera_trajectory(motion_samples, fps, analysis_diagonal, motion_config)
    if not (len(metrics) == len(motion_samples) == len(decisions) == len(normalized_records)):
        raise AnalysisError(
            "analysis components returned inconsistent lengths",
            code="analysis_length_mismatch",
            fields=("records",),
        )

    frames = tuple(
        _combine_frame(metric, sample, decision)
        for metric, sample, decision in zip(metrics, motion_samples, decisions, strict=True)
    )
    ranges = _build_ranges(decisions)
    return AnalysisResult(
        schema_version=SCHEMA_VERSION,
        run_id=input_digest[:16],
        input_digest=input_digest,
        frame_count=len(frames),
        fps=float(fps),
        width=width,
        height=height,
        motion_confidence=float(np.mean([sample.confidence for sample in motion_samples])),
        quality_summary=_quality_summary(metrics),
        trajectory_summary=_trajectory_summary(motion_samples, decisions),
        frames=frames,
        ranges=ranges,
        warnings=tuple(
            sorted({item.reason for item in ranges if item.kind == "review_required"})
        ),
    )


def _validate_and_sort_records(records: Sequence[FrameRecord]) -> list[FrameRecord]:
    if not records:
        raise AnalysisError("at least one frame record is required", code="empty_input", fields=("records",))
    normalized = sorted(records, key=lambda record: record.source_index)
    source_indices = [record.source_index for record in normalized]
    if len(source_indices) != len(set(source_indices)):
        raise AnalysisError(
            "frame records contain duplicate source indices",
            code="duplicate_source_index",
            fields=("records",),
        )
    for record in normalized:
        if not record.path.is_file():
            raise AnalysisError(
                f"frame file does not exist: {record.path}",
                code="missing_frame_file",
                fields=("records",),
            )
    return normalized


def _validate_dimensions(records: Sequence[FrameRecord]) -> tuple[int, int]:
    expected: tuple[int, int] | None = None
    for record in records:
        image = cv2.imread(str(record.path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise AnalysisError(
                f"cannot read frame image: {record.path}",
                code="unreadable_frame",
                fields=("records",),
            )
        dimensions = (int(image.shape[1]), int(image.shape[0]))
        if expected is None:
            expected = dimensions
        elif dimensions != expected:
            raise AnalysisError(
                f"frame dimensions differ: expected={expected}, actual={dimensions}",
                code="inconsistent_frame_dimensions",
                fields=("records",),
            )
    if expected is None:
        raise AnalysisError("at least one readable frame is required", code="empty_input", fields=("records",))
    return expected


def _input_digest(records: Sequence[FrameRecord]) -> str:
    digest = hashlib.sha256()
    for record in records:
        file_digest = _file_sha256(record.path)
        stat = record.path.stat()
        identity = f"{record.source_index}\0{record.path.name}\0{stat.st_size}\0{file_digest}\n"
        digest.update(identity.encode("utf-8"))
    return digest.hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _combine_frame(metric: FrameMetric, sample: MotionSample, decision: MotionDecision) -> FrameAnalysis:
    if not (
        metric.source_index == sample.source_index == decision.source_index
        and metric.output_index == sample.output_index == decision.output_index
    ):
        raise AnalysisError(
            "analysis components are not aligned by frame identity",
            code="analysis_identity_mismatch",
            fields=("records",),
        )
    return FrameAnalysis(
        source_index=metric.source_index,
        output_index=metric.output_index,
        timestamp_sec=metric.timestamp_sec,
        sharpness=metric.sharpness,
        brightness=metric.brightness,
        contrast=metric.contrast,
        dx=sample.dx,
        dy=sample.dy,
        rotation_deg=sample.rotation_deg,
        scale=sample.scale,
        motion_confidence=sample.confidence,
        normalized_residual_spatial_iqr=sample.normalized_residual_spatial_iqr,
        normalized_residual_spatial_p90=sample.normalized_residual_spatial_p90,
        inlier_spatial_coverage=sample.inlier_spatial_coverage,
        jitter_score=decision.jitter_score,
        jitter_confidence=decision.jitter_confidence,
        low_quality_candidate=metric.bad_quality_candidate,
    )


def _quality_summary(metrics: Sequence[FrameMetric]) -> QualitySummary:
    sharpness = np.asarray([metric.sharpness for metric in metrics], dtype=np.float64)
    brightness = np.asarray([metric.brightness for metric in metrics], dtype=np.float64)
    contrast = np.asarray([metric.contrast for metric in metrics], dtype=np.float64)
    return QualitySummary(
        sharpness_p10=float(np.quantile(sharpness, 0.10)),
        sharpness_median=float(np.median(sharpness)),
        brightness_median=float(np.median(brightness)),
        contrast_median=float(np.median(contrast)),
        low_quality_count=sum(metric.bad_quality_candidate for metric in metrics),
    )


def _trajectory_summary(
    samples: Sequence[MotionSample],
    decisions: Sequence[MotionDecision],
) -> TrajectorySummary:
    return TrajectorySummary(
        mean_confidence=float(np.mean([sample.confidence for sample in samples])),
        normalized_residual_p95=float(
            np.quantile([decision.normalized_translation_residual for decision in decisions], 0.95)
        ),
        rotation_residual_p95=float(np.quantile([decision.rotation_residual_deg for decision in decisions], 0.95)),
        fallback_count=sum(
            sample.fallback_reason is not None and sample.fallback_reason != "initial_frame" for sample in samples
        ),
        spatial_uncertainty_count=sum(decision.reason == "spatial_motion_uncertain" for decision in decisions),
        multiscale_disagreement_count=sum(
            decision.reason == "multiscale_motion_disagreement" for decision in decisions
        ),
    )


def _build_ranges(decisions: Sequence[MotionDecision]) -> tuple[AnalysisRange, ...]:
    if not decisions:
        return ()
    ranges: list[AnalysisRange] = []
    start = 0
    for index in range(1, len(decisions) + 1):
        boundary = index == len(decisions) or (
            decisions[index].kind != decisions[start].kind or decisions[index].reason != decisions[start].reason
        )
        if boundary:
            group = decisions[start:index]
            ranges.append(
                AnalysisRange(
                    start=group[0].source_index,
                    end=group[-1].source_index,
                    kind=group[0].kind,
                    confidence=float(np.mean([decision.jitter_confidence for decision in group])),
                    reason=group[0].reason,
                )
            )
            start = index
    return tuple(ranges)
