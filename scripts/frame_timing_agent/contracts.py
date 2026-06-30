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
