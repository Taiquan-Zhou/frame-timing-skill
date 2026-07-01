from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from frame_timing_agent.contracts import (
    MAXIMUM_CONSECUTIVE_DROPS,
    RETENTION_RATIO_ERROR_MESSAGE,
    SCHEMA_VERSION,
    ConfigurationError,
    PolicyName,
    StrategyRequest,
)

_REQUIRED_FIELDS = frozenset({"schema_version", "policy"})
_POLICY_OPTIONS = ", ".join(policy.value for policy in PolicyName)
_POLICY_ERROR_MESSAGE = f"policy must be one of: {_POLICY_OPTIONS}"
_ALLOWED_FIELDS = frozenset(
    {
        "schema_version",
        "policy",
        "minimum_retention_ratio",
        "maximum_consecutive_drops",
    }
)


@dataclass(frozen=True)
class PolicyPreset:
    """Built-in safety defaults for one policy."""

    policy: PolicyName
    minimum_retention_ratio: float
    maximum_consecutive_drops: int


_POLICY_PRESETS: Mapping[PolicyName, PolicyPreset] = MappingProxyType(
    {
        PolicyName.COVERAGE_FIRST: PolicyPreset(PolicyName.COVERAGE_FIRST, 0.85, 2),
        PolicyName.BALANCED: PolicyPreset(PolicyName.BALANCED, 0.65, 4),
        PolicyName.JITTER_REDUCTION: PolicyPreset(PolicyName.JITTER_REDUCTION, 0.45, 7),
    }
)


@dataclass(frozen=True)
class ResolvedStrategyConfig:
    """Concrete safety constraints after applying and validating overrides."""

    policy: PolicyName
    minimum_retention_ratio: float
    maximum_consecutive_drops: int

    def __post_init__(self) -> None:
        request = StrategyRequest(
            policy=self.policy,
            minimum_retention_ratio=self.minimum_retention_ratio,
            maximum_consecutive_drops=self.maximum_consecutive_drops,
        )
        if request.minimum_retention_ratio is None:
            raise ConfigurationError(
                RETENTION_RATIO_ERROR_MESSAGE,
                code="invalid_value",
                fields=("minimum_retention_ratio",),
            )
        if request.maximum_consecutive_drops is None:
            raise ConfigurationError(
                f"maximum_consecutive_drops must be an integer in [0, {MAXIMUM_CONSECUTIVE_DROPS}]",
                code="invalid_value",
                fields=("maximum_consecutive_drops",),
            )

        preset = _POLICY_PRESETS[request.policy]
        if request.minimum_retention_ratio < preset.minimum_retention_ratio:
            raise ConfigurationError(
                f"minimum_retention_ratio is weaker than the policy safety limit {preset.minimum_retention_ratio}",
                code="unsafe_override",
                fields=("minimum_retention_ratio",),
            )
        if request.maximum_consecutive_drops > preset.maximum_consecutive_drops:
            raise ConfigurationError(
                f"maximum_consecutive_drops is weaker than the policy safety limit {preset.maximum_consecutive_drops}",
                code="unsafe_override",
                fields=("maximum_consecutive_drops",),
            )

        object.__setattr__(self, "policy", request.policy)
        object.__setattr__(self, "minimum_retention_ratio", request.minimum_retention_ratio)
        object.__setattr__(self, "maximum_consecutive_drops", request.maximum_consecutive_drops)


def parse_strategy_request(data: object) -> StrategyRequest:
    if not isinstance(data, Mapping):
        raise ConfigurationError("strategy request must be an object", code="invalid_request_type")
    fields: dict[str, object] = {}
    for key, value in data.items():
        if not isinstance(key, str):
            raise ConfigurationError("strategy request field names must be strings", code="invalid_field_name")
        fields[key] = value

    unknown_fields = set(fields) - _ALLOWED_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ConfigurationError(
            f"unknown strategy request field: {names}",
            code="unknown_field",
            fields=tuple(sorted(unknown_fields)),
        )

    missing_fields = _REQUIRED_FIELDS - set(fields)
    if missing_fields:
        names = ", ".join(sorted(missing_fields))
        raise ConfigurationError(
            f"missing strategy request field: {names}",
            code="missing_field",
            fields=tuple(sorted(missing_fields)),
        )

    schema_version = fields["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != SCHEMA_VERSION:
        raise ConfigurationError(
            f"schema_version must be {SCHEMA_VERSION}",
            code="unsupported_schema_version",
            fields=("schema_version",),
        )

    raw_policy = fields["policy"]
    if not isinstance(raw_policy, str):
        raise ConfigurationError(
            _POLICY_ERROR_MESSAGE,
            code="invalid_policy",
            fields=("policy",),
        )
    try:
        policy = PolicyName(raw_policy)
    except ValueError as exc:
        raise ConfigurationError(
            _POLICY_ERROR_MESSAGE,
            code="invalid_policy",
            fields=("policy",),
        ) from exc

    return StrategyRequest(
        policy=policy,
        minimum_retention_ratio=_parse_optional_retention_ratio(fields.get("minimum_retention_ratio")),
        maximum_consecutive_drops=_parse_optional_consecutive_drops(fields.get("maximum_consecutive_drops")),
    )


def _parse_optional_retention_ratio(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigurationError(
            RETENTION_RATIO_ERROR_MESSAGE,
            code="invalid_value",
            fields=("minimum_retention_ratio",),
        )
    try:
        return float(value)
    except (OverflowError, ValueError) as exc:
        raise ConfigurationError(
            RETENTION_RATIO_ERROR_MESSAGE,
            code="invalid_value",
            fields=("minimum_retention_ratio",),
        ) from exc


def _parse_optional_consecutive_drops(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(
            f"maximum_consecutive_drops must be an integer in [0, {MAXIMUM_CONSECUTIVE_DROPS}]",
            code="invalid_value",
            fields=("maximum_consecutive_drops",),
        )
    return value


def resolve_strategy_request(request: StrategyRequest) -> ResolvedStrategyConfig:
    if not isinstance(request, StrategyRequest):
        raise ConfigurationError("request must be a StrategyRequest", code="invalid_request_type")
    preset = _POLICY_PRESETS[request.policy]

    minimum_retention_ratio = request.minimum_retention_ratio
    if minimum_retention_ratio is None:
        minimum_retention_ratio = preset.minimum_retention_ratio

    maximum_consecutive_drops = request.maximum_consecutive_drops
    if maximum_consecutive_drops is None:
        maximum_consecutive_drops = preset.maximum_consecutive_drops

    return ResolvedStrategyConfig(
        policy=request.policy,
        minimum_retention_ratio=minimum_retention_ratio,
        maximum_consecutive_drops=maximum_consecutive_drops,
    )
