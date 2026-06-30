from __future__ import annotations

import math

import pytest
from frame_timing_agent import PolicyName, StrategyRequest
from frame_timing_agent.configuration import parse_strategy_request, resolve_strategy_request


@pytest.mark.parametrize(
    ("policy", "minimum_retention_ratio", "maximum_consecutive_drops"),
    [
        ("coverage_first", 0.85, 2),
        ("balanced", 0.65, 4),
        ("jitter_reduction", 0.45, 7),
    ],
)
def test_policy_presets_resolve_to_documented_safety_limits(
    policy: str,
    minimum_retention_ratio: float,
    maximum_consecutive_drops: int,
) -> None:
    request = StrategyRequest(policy=PolicyName(policy))

    resolved = resolve_strategy_request(request)

    assert resolved.policy is PolicyName(policy)
    assert resolved.minimum_retention_ratio == minimum_retention_ratio
    assert resolved.maximum_consecutive_drops == maximum_consecutive_drops


def test_parse_strategy_request_round_trips_serialized_contract() -> None:
    original = StrategyRequest(
        policy=PolicyName.BALANCED,
        minimum_retention_ratio=0.8,
        maximum_consecutive_drops=2,
    )

    parsed = parse_strategy_request(original.to_dict())

    assert parsed == original


@pytest.mark.parametrize("payload", [None, [], "coverage_first", 3])
def test_parse_rejects_non_object_payloads(payload: object) -> None:
    with pytest.raises(ValueError, match="object"):
        parse_strategy_request(payload)


def test_parse_rejects_non_string_field_names() -> None:
    with pytest.raises(ValueError, match="field names"):
        parse_strategy_request({"schema_version": 3, "policy": "coverage_first", 1: "invalid"})


@pytest.mark.parametrize("schema_version", [True, 3.0, "3", None])
def test_parse_rejects_non_integer_schema_version(schema_version: object) -> None:
    with pytest.raises(ValueError, match="schema_version"):
        parse_strategy_request({"schema_version": schema_version, "policy": "coverage_first"})


@pytest.mark.parametrize(
    "field",
    ["preserve_endpoints", "allow_low_confidence_removal", "static_motion_quantile"],
)
def test_parse_rejects_unknown_or_unsafe_fields(field: str) -> None:
    payload = {"schema_version": 3, "policy": "coverage_first", field: True}

    with pytest.raises(ValueError, match=field):
        parse_strategy_request(payload)


@pytest.mark.parametrize("missing", ["schema_version", "policy"])
def test_parse_rejects_missing_required_fields(missing: str) -> None:
    payload = {"schema_version": 3, "policy": "coverage_first"}
    del payload[missing]

    with pytest.raises(ValueError, match=missing):
        parse_strategy_request(payload)


def test_parse_rejects_unsupported_schema_version() -> None:
    with pytest.raises(ValueError, match="schema_version"):
        parse_strategy_request({"schema_version": 2, "policy": "coverage_first"})


@pytest.mark.parametrize("policy", ["fast", "", 1, True, None])
def test_parse_rejects_invalid_policy(policy: object) -> None:
    with pytest.raises(ValueError, match="policy"):
        parse_strategy_request({"schema_version": 3, "policy": policy})


@pytest.mark.parametrize("value", [0.0, -0.1, 1.1, math.nan, math.inf, -math.inf, True, "0.9"])
def test_parse_rejects_invalid_retention_ratio(value: object) -> None:
    payload = {
        "schema_version": 3,
        "policy": "coverage_first",
        "minimum_retention_ratio": value,
    }

    with pytest.raises(ValueError, match="minimum_retention_ratio"):
        parse_strategy_request(payload)


@pytest.mark.parametrize("value", [-1, 1.5, math.nan, True, "2", 8, 10**1000])
def test_parse_rejects_invalid_consecutive_drop_limit(value: object) -> None:
    payload = {
        "schema_version": 3,
        "policy": "coverage_first",
        "maximum_consecutive_drops": value,
    }

    with pytest.raises(ValueError, match="maximum_consecutive_drops"):
        parse_strategy_request(payload)


def test_configuration_errors_expose_machine_readable_metadata() -> None:
    from frame_timing_agent import ConfigurationError

    with pytest.raises(ConfigurationError) as captured:
        parse_strategy_request(
            {
                "schema_version": 3,
                "policy": "coverage_first",
                "preserve_endpoints": False,
            }
        )

    assert captured.value.code == "unknown_field"
    assert captured.value.fields == ("preserve_endpoints",)


@pytest.mark.parametrize(
    "payload",
    [
        {"schema_version": 3, "policy": "coverage_first", "minimum_retention_ratio": 0.84},
        {"schema_version": 3, "policy": "balanced", "maximum_consecutive_drops": 5},
    ],
)
def test_resolve_rejects_overrides_weaker_than_policy_limits(payload: dict[str, object]) -> None:
    from frame_timing_agent import ConfigurationError

    request = parse_strategy_request(payload)

    with pytest.raises(ConfigurationError, match="safety limit") as captured:
        resolve_strategy_request(request)
    assert captured.value.code == "unsafe_override"


def test_resolve_accepts_more_conservative_overrides() -> None:
    request = parse_strategy_request(
        {
            "schema_version": 3,
            "policy": "jitter_reduction",
            "minimum_retention_ratio": 0.9,
            "maximum_consecutive_drops": 1,
        }
    )

    resolved = resolve_strategy_request(request)

    assert resolved.minimum_retention_ratio == 0.9
    assert resolved.maximum_consecutive_drops == 1
