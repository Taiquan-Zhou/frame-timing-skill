from __future__ import annotations

import json
import math
from collections.abc import Mapping
from pathlib import Path
from typing import TypeVar

from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AnalysisRange,
    AnalysisResult,
    ExecutionResult,
    FrameAnalysis,
    PolicyName,
    QualitySummary,
    RiskLevel,
    StrategyCandidate,
    StrategyRequest,
    TrajectorySummary,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)


class ArtifactFormatError(ValueError):
    pass


EnumType = TypeVar("EnumType", PolicyName, RiskLevel, ValidationSeverity)


def read_analysis_result(path: Path | str) -> AnalysisResult:
    data = _load_object(path)
    _expect_fields(
        data,
        {
            "schema_version",
            "run_id",
            "input_digest",
            "frame_count",
            "fps",
            "width",
            "height",
            "motion_confidence",
            "quality_summary",
            "trajectory_summary",
            "frames",
            "ranges",
            "warnings",
        },
        "analysis",
    )
    _schema(data)
    frames = tuple(_frame(item) for item in _list(data["frames"], "analysis.frames"))
    frame_count = _integer(data["frame_count"], "analysis.frame_count", minimum=1)
    if frame_count != len(frames):
        raise ArtifactFormatError("analysis.frame_count does not match frames")
    sources = tuple(frame.source_index for frame in frames)
    if sources != tuple(sorted(set(sources))):
        raise ArtifactFormatError("analysis frames must have unique increasing source indices")
    return AnalysisResult(
        schema_version=SCHEMA_VERSION,
        run_id=_string(data["run_id"], "analysis.run_id"),
        input_digest=_string(data["input_digest"], "analysis.input_digest"),
        frame_count=frame_count,
        fps=_number(data["fps"], "analysis.fps", minimum=0.0, exclusive_minimum=True),
        width=_integer(data["width"], "analysis.width", minimum=1),
        height=_integer(data["height"], "analysis.height", minimum=1),
        motion_confidence=_number(data["motion_confidence"], "analysis.motion_confidence", minimum=0.0, maximum=1.0),
        quality_summary=_quality_summary(data["quality_summary"]),
        trajectory_summary=_trajectory_summary(data["trajectory_summary"]),
        frames=frames,
        ranges=tuple(_analysis_range(item) for item in _list(data["ranges"], "analysis.ranges")),
        warnings=tuple(_string(item, "analysis.warning") for item in _list(data["warnings"], "analysis.warnings")),
    )


def read_strategy_candidate(path: Path | str) -> StrategyCandidate:
    data = _load_object(path)
    _expect_fields(
        data,
        {
            "schema_version",
            "strategy_id",
            "policy_revision",
            "input_digest",
            "policy",
            "request",
            "selected_sources",
            "estimated_output_count",
            "retention_ratio",
            "maximum_consecutive_drops",
            "maximum_source_index_gap",
            "maximum_time_gap_seconds",
            "estimated_jitter_reduction",
            "estimated_quality_change",
            "confidence",
            "risk_level",
            "reasons",
        },
        "strategy",
    )
    _schema(data)
    return StrategyCandidate(
        schema_version=SCHEMA_VERSION,
        strategy_id=_string(data["strategy_id"], "strategy.strategy_id"),
        policy_revision=_string(data["policy_revision"], "strategy.policy_revision"),
        input_digest=_string(data["input_digest"], "strategy.input_digest"),
        policy=_enum(PolicyName, data["policy"], "strategy.policy"),
        request=_strategy_request(data["request"]),
        selected_sources=tuple(
            _integer(item, "strategy.selected_source", minimum=0)
            for item in _list(data["selected_sources"], "strategy.selected_sources")
        ),
        estimated_output_count=_integer(data["estimated_output_count"], "strategy.estimated_output_count", minimum=0),
        retention_ratio=_number(data["retention_ratio"], "strategy.retention_ratio", minimum=0.0, maximum=1.0),
        maximum_consecutive_drops=_integer(
            data["maximum_consecutive_drops"], "strategy.maximum_consecutive_drops", minimum=0
        ),
        maximum_source_index_gap=_integer(
            data["maximum_source_index_gap"], "strategy.maximum_source_index_gap", minimum=0
        ),
        maximum_time_gap_seconds=_number(
            data["maximum_time_gap_seconds"], "strategy.maximum_time_gap_seconds", minimum=0.0
        ),
        estimated_jitter_reduction=_number(
            data["estimated_jitter_reduction"], "strategy.estimated_jitter_reduction", minimum=0.0, maximum=1.0
        ),
        estimated_quality_change=_number(data["estimated_quality_change"], "strategy.estimated_quality_change"),
        confidence=_number(data["confidence"], "strategy.confidence", minimum=0.0, maximum=1.0),
        risk_level=_enum(RiskLevel, data["risk_level"], "strategy.risk_level"),
        reasons=tuple(_string(item, "strategy.reason") for item in _list(data["reasons"], "strategy.reasons")),
    )


def read_validation_result(path: Path | str) -> ValidationResult:
    data = _load_object(path)
    _expect_fields(data, {"valid", "strategy_id", "input_digest", "candidate_digest", "issues"}, "validation")
    return ValidationResult(
        valid=_boolean(data["valid"], "validation.valid"),
        strategy_id=_string(data["strategy_id"], "validation.strategy_id"),
        input_digest=_string(data["input_digest"], "validation.input_digest"),
        candidate_digest=_string(data["candidate_digest"], "validation.candidate_digest"),
        issues=tuple(_validation_issue(item) for item in _list(data["issues"], "validation.issues")),
    )


def read_execution_result(path: Path | str) -> ExecutionResult:
    data = _load_object(path)
    _expect_fields(
        data,
        {
            "schema_version",
            "run_id",
            "strategy_id",
            "input_digest",
            "candidate_digest",
            "output_frame_count",
            "selected_sources",
            "output_manifest",
            "output_digest",
        },
        "execution",
    )
    _schema(data)
    return ExecutionResult(
        schema_version=SCHEMA_VERSION,
        run_id=_string(data["run_id"], "execution.run_id"),
        strategy_id=_string(data["strategy_id"], "execution.strategy_id"),
        input_digest=_string(data["input_digest"], "execution.input_digest"),
        candidate_digest=_string(data["candidate_digest"], "execution.candidate_digest"),
        output_frame_count=_integer(data["output_frame_count"], "execution.output_frame_count", minimum=0),
        selected_sources=tuple(
            _integer(item, "execution.selected_source", minimum=0)
            for item in _list(data["selected_sources"], "execution.selected_sources")
        ),
        output_manifest=_string(data["output_manifest"], "execution.output_manifest"),
        output_digest=_string(data["output_digest"], "execution.output_digest"),
    )


def _load_object(path: Path | str) -> dict[str, object]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ArtifactFormatError("artifact JSON cannot be read") from exc
    return _object(payload, "artifact")


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ArtifactFormatError(f"{label} must be an object with string keys")
    return {str(key): item for key, item in value.items()}


def _expect_fields(data: Mapping[str, object], expected: set[str], label: str) -> None:
    actual = set(data)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ArtifactFormatError(f"{label} fields do not match contract; missing={missing}, unknown={unknown}")


def _schema(data: Mapping[str, object]) -> None:
    if _integer(data["schema_version"], "schema_version") != SCHEMA_VERSION:
        raise ArtifactFormatError(f"schema_version must be {SCHEMA_VERSION}")


def _quality_summary(value: object) -> QualitySummary:
    data = _object(value, "quality_summary")
    fields = {"sharpness_p10", "sharpness_median", "brightness_median", "contrast_median", "low_quality_count"}
    _expect_fields(data, fields, "quality_summary")
    return QualitySummary(
        _number(data["sharpness_p10"], "quality_summary.sharpness_p10"),
        _number(data["sharpness_median"], "quality_summary.sharpness_median"),
        _number(data["brightness_median"], "quality_summary.brightness_median"),
        _number(data["contrast_median"], "quality_summary.contrast_median"),
        _integer(data["low_quality_count"], "quality_summary.low_quality_count", minimum=0),
    )


def _trajectory_summary(value: object) -> TrajectorySummary:
    data = _object(value, "trajectory_summary")
    fields = {
        "mean_confidence",
        "normalized_residual_p95",
        "rotation_residual_p95",
        "fallback_count",
        "spatial_uncertainty_count",
        "multiscale_disagreement_count",
    }
    _expect_fields(data, fields, "trajectory_summary")
    return TrajectorySummary(
        _number(data["mean_confidence"], "trajectory_summary.mean_confidence", minimum=0.0, maximum=1.0),
        _number(data["normalized_residual_p95"], "trajectory_summary.normalized_residual_p95", minimum=0.0),
        _number(data["rotation_residual_p95"], "trajectory_summary.rotation_residual_p95", minimum=0.0),
        _integer(data["fallback_count"], "trajectory_summary.fallback_count", minimum=0),
        _integer(data["spatial_uncertainty_count"], "trajectory_summary.spatial_uncertainty_count", minimum=0),
        _integer(data["multiscale_disagreement_count"], "trajectory_summary.multiscale_disagreement_count", minimum=0),
    )


def _frame(value: object) -> FrameAnalysis:
    data = _object(value, "frame")
    fields = {
        "source_index",
        "output_index",
        "timestamp_sec",
        "sharpness",
        "brightness",
        "contrast",
        "dx",
        "dy",
        "rotation_deg",
        "scale",
        "motion_confidence",
        "normalized_residual_spatial_iqr",
        "normalized_residual_spatial_p90",
        "inlier_spatial_coverage",
        "jitter_score",
        "jitter_confidence",
        "low_quality_candidate",
    }
    _expect_fields(data, fields, "frame")
    return FrameAnalysis(
        source_index=_integer(data["source_index"], "frame.source_index", minimum=0),
        output_index=_integer(data["output_index"], "frame.output_index", minimum=0),
        timestamp_sec=_number(data["timestamp_sec"], "frame.timestamp_sec", minimum=0.0),
        sharpness=_number(data["sharpness"], "frame.sharpness"),
        brightness=_number(data["brightness"], "frame.brightness"),
        contrast=_number(data["contrast"], "frame.contrast"),
        dx=_number(data["dx"], "frame.dx"),
        dy=_number(data["dy"], "frame.dy"),
        rotation_deg=_number(data["rotation_deg"], "frame.rotation_deg"),
        scale=_number(data["scale"], "frame.scale", minimum=0.0, exclusive_minimum=True),
        motion_confidence=_number(data["motion_confidence"], "frame.motion_confidence", minimum=0.0, maximum=1.0),
        normalized_residual_spatial_iqr=_number(
            data["normalized_residual_spatial_iqr"], "frame.normalized_residual_spatial_iqr", minimum=0.0
        ),
        normalized_residual_spatial_p90=_number(
            data["normalized_residual_spatial_p90"], "frame.normalized_residual_spatial_p90", minimum=0.0
        ),
        inlier_spatial_coverage=_number(
            data["inlier_spatial_coverage"], "frame.inlier_spatial_coverage", minimum=0.0, maximum=1.0
        ),
        jitter_score=_number(data["jitter_score"], "frame.jitter_score", minimum=0.0, maximum=1.0),
        jitter_confidence=_number(data["jitter_confidence"], "frame.jitter_confidence", minimum=0.0, maximum=1.0),
        low_quality_candidate=_boolean(data["low_quality_candidate"], "frame.low_quality_candidate"),
    )


def _analysis_range(value: object) -> AnalysisRange:
    data = _object(value, "analysis_range")
    _expect_fields(data, {"start", "end", "kind", "confidence", "reason"}, "analysis_range")
    start = _integer(data["start"], "analysis_range.start", minimum=0)
    end = _integer(data["end"], "analysis_range.end", minimum=0)
    if end < start:
        raise ArtifactFormatError("analysis_range.end must not precede start")
    return AnalysisRange(
        start,
        end,
        _string(data["kind"], "analysis_range.kind"),
        _number(data["confidence"], "analysis_range.confidence", minimum=0.0, maximum=1.0),
        _string(data["reason"], "analysis_range.reason"),
    )


def _strategy_request(value: object) -> StrategyRequest:
    data = _object(value, "strategy.request")
    _expect_fields(data, {"policy", "minimum_retention_ratio", "maximum_consecutive_drops"}, "strategy.request")
    retention = data["minimum_retention_ratio"]
    drops = data["maximum_consecutive_drops"]
    return StrategyRequest(
        policy=_enum(PolicyName, data["policy"], "strategy.request.policy"),
        minimum_retention_ratio=None
        if retention is None
        else _number(retention, "strategy.request.minimum_retention_ratio"),
        maximum_consecutive_drops=None
        if drops is None
        else _integer(drops, "strategy.request.maximum_consecutive_drops", minimum=0),
    )


def _validation_issue(value: object) -> ValidationIssue:
    data = _object(value, "validation.issue")
    _expect_fields(data, {"code", "severity", "message", "source_range"}, "validation.issue")
    raw_range = data["source_range"]
    source_range: tuple[int, int] | None = None
    if raw_range is not None:
        values = _list(raw_range, "validation.issue.source_range")
        if len(values) != 2:
            raise ArtifactFormatError("validation.issue.source_range must contain two integers")
        source_range = (
            _integer(values[0], "validation.issue.source_range.start", minimum=0),
            _integer(values[1], "validation.issue.source_range.end", minimum=0),
        )
    return ValidationIssue(
        _string(data["code"], "validation.issue.code"),
        _enum(ValidationSeverity, data["severity"], "validation.issue.severity"),
        _string(data["message"], "validation.issue.message"),
        source_range,
    )


def _integer(value: object, label: str, *, minimum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ArtifactFormatError(f"{label} must be an integer")
    if minimum is not None and value < minimum:
        raise ArtifactFormatError(f"{label} is below its minimum")
    return value


def _number(
    value: object,
    label: str,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    exclusive_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ArtifactFormatError(f"{label} must be a finite number")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise ArtifactFormatError(f"{label} must be a finite number") from exc
    if not math.isfinite(normalized):
        raise ArtifactFormatError(f"{label} must be a finite number")
    if minimum is not None and (normalized <= minimum if exclusive_minimum else normalized < minimum):
        raise ArtifactFormatError(f"{label} is below its minimum")
    if maximum is not None and normalized > maximum:
        raise ArtifactFormatError(f"{label} exceeds its maximum")
    return normalized


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ArtifactFormatError(f"{label} must be a boolean")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ArtifactFormatError(f"{label} must be a non-empty string")
    return value


def _list(value: object, label: str) -> list[object]:
    if not isinstance(value, list):
        raise ArtifactFormatError(f"{label} must be an array")
    return list(value)


def _enum(enum_type: type[EnumType], value: object, label: str) -> EnumType:
    raw = _string(value, label)
    try:
        return enum_type(raw)
    except ValueError as exc:
        raise ArtifactFormatError(f"{label} has an unsupported value") from exc
