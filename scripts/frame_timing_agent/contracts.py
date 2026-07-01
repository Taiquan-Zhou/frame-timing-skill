from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

# Schema versions are enforced at JSON boundaries, not stored on in-memory requests.
SCHEMA_VERSION = 3
MAXIMUM_CONSECUTIVE_DROPS = 7
RETENTION_RATIO_ERROR_MESSAGE = "minimum_retention_ratio must be a finite number in (0, 1]"


class ConfigurationError(ValueError):
    def __init__(self, message: str, *, code: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.fields = fields


class AnalysisError(ValueError):
    def __init__(self, message: str, *, code: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.code = code
        self.fields = fields


class PolicyName(str, Enum):
    COVERAGE_FIRST = "coverage_first"
    BALANCED = "balanced"
    JITTER_REDUCTION = "jitter_reduction"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ValidationSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class StrategyRequest:
    policy: PolicyName
    minimum_retention_ratio: float | None = None
    maximum_consecutive_drops: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.policy, PolicyName):
            raise ConfigurationError("policy must be a PolicyName", code="invalid_value", fields=("policy",))
        if self.minimum_retention_ratio is not None:
            value = self.minimum_retention_ratio
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ConfigurationError(
                    RETENTION_RATIO_ERROR_MESSAGE,
                    code="invalid_value",
                    fields=("minimum_retention_ratio",),
                )
            try:
                normalized_value = float(value)
            except (OverflowError, ValueError) as exc:
                raise ConfigurationError(
                    RETENTION_RATIO_ERROR_MESSAGE,
                    code="invalid_value",
                    fields=("minimum_retention_ratio",),
                ) from exc
            if not math.isfinite(normalized_value) or not 0 < normalized_value <= 1:
                raise ConfigurationError(
                    RETENTION_RATIO_ERROR_MESSAGE,
                    code="invalid_value",
                    fields=("minimum_retention_ratio",),
                )
            object.__setattr__(self, "minimum_retention_ratio", normalized_value)
        if self.maximum_consecutive_drops is not None:
            value = self.maximum_consecutive_drops
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAXIMUM_CONSECUTIVE_DROPS:
                raise ConfigurationError(
                    f"maximum_consecutive_drops must be an integer in [0, {MAXIMUM_CONSECUTIVE_DROPS}]",
                    code="invalid_value",
                    fields=("maximum_consecutive_drops",),
                )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "policy": self.policy.value,
            "minimum_retention_ratio": self.minimum_retention_ratio,
            "maximum_consecutive_drops": self.maximum_consecutive_drops,
        }


@dataclass(frozen=True)
class FrameAnalysis:
    source_index: int
    output_index: int
    timestamp_sec: float
    sharpness: float
    brightness: float
    contrast: float
    dx: float
    dy: float
    rotation_deg: float
    scale: float
    motion_confidence: float
    normalized_residual_spatial_iqr: float
    normalized_residual_spatial_p90: float
    inlier_spatial_coverage: float
    jitter_score: float
    jitter_confidence: float
    low_quality_candidate: bool


@dataclass(frozen=True)
class QualitySummary:
    sharpness_p10: float
    sharpness_median: float
    brightness_median: float
    contrast_median: float
    low_quality_count: int


@dataclass(frozen=True)
class TrajectorySummary:
    mean_confidence: float
    normalized_residual_p95: float
    rotation_residual_p95: float
    fallback_count: int
    spatial_uncertainty_count: int
    multiscale_disagreement_count: int


@dataclass(frozen=True)
class AnalysisRange:
    start: int
    end: int
    kind: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class AnalysisResult:
    schema_version: int
    run_id: str
    input_digest: str
    frame_count: int
    fps: float
    width: int
    height: int
    motion_confidence: float
    quality_summary: QualitySummary
    trajectory_summary: TrajectorySummary
    frames: tuple[FrameAnalysis, ...]
    ranges: tuple[AnalysisRange, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class StrategyCandidate:
    schema_version: int
    strategy_id: str
    input_digest: str
    policy: PolicyName
    request: StrategyRequest
    selected_sources: tuple[int, ...]
    estimated_output_count: int
    retention_ratio: float
    maximum_consecutive_drops: int
    maximum_source_index_gap: int
    maximum_time_gap_seconds: float
    estimated_jitter_reduction: float
    estimated_quality_change: float
    confidence: float
    risk_level: RiskLevel
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        integer_limits = {
            "estimated_output_count": None,
            "maximum_consecutive_drops": MAXIMUM_CONSECUTIVE_DROPS,
            "maximum_source_index_gap": None,
        }
        for field_name, maximum_integer in integer_limits.items():
            value = getattr(self, field_name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
                or (maximum_integer is not None and value > maximum_integer)
            ):
                upper_bound = "" if maximum_integer is None else f" and at most {maximum_integer}"
                raise ConfigurationError(
                    f"{field_name} must be a non-negative integer{upper_bound}",
                    code="invalid_value",
                    fields=(field_name,),
                )

        float_limits = {
            "retention_ratio": (0.0, 1.0),
            "maximum_time_gap_seconds": (0.0, None),
            "estimated_jitter_reduction": (0.0, 1.0),
            "estimated_quality_change": (None, None),
            "confidence": (0.0, 1.0),
        }
        for field_name, (minimum_float, maximum_float) in float_limits.items():
            normalized = _normalize_candidate_float(getattr(self, field_name), field_name)
            if (minimum_float is not None and normalized < minimum_float) or (
                maximum_float is not None and normalized > maximum_float
            ):
                raise ConfigurationError(
                    f"{field_name} is outside its supported range",
                    code="invalid_value",
                    fields=(field_name,),
                )
            object.__setattr__(self, field_name, normalized)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: ValidationSeverity
    message: str
    source_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    strategy_id: str
    input_digest: str
    candidate_digest: str
    issues: tuple[ValidationIssue, ...]


@dataclass(frozen=True)
class ExecutionResult:
    schema_version: int
    run_id: str
    strategy_id: str
    input_digest: str
    candidate_digest: str
    output_frame_count: int
    selected_sources: tuple[int, ...]
    output_manifest: str
    output_digest: str


@dataclass(frozen=True)
class OutputVerificationResult:
    valid: bool
    output_digest: str
    issues: tuple[ValidationIssue, ...]


def _normalize_candidate_float(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(
            f"{field_name} must be a finite number",
            code="invalid_value",
            fields=(field_name,),
        )
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise ConfigurationError(
            f"{field_name} must be a finite number",
            code="invalid_value",
            fields=(field_name,),
        ) from exc
    if not math.isfinite(normalized):
        raise ConfigurationError(
            f"{field_name} must be a finite number",
            code="invalid_value",
            fields=(field_name,),
        )
    return normalized
