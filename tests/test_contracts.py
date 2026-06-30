from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import frame_timing_agent
import pytest
from frame_timing_agent import PolicyName, RiskLevel, StrategyRequest, ValidationSeverity
from frame_timing_agent.contracts import AnalysisRange, QualitySummary, TrajectorySummary


def test_public_enum_values_are_stable() -> None:
    assert [item.value for item in PolicyName] == [
        "coverage_first",
        "balanced",
        "jitter_reduction",
    ]
    assert [item.value for item in RiskLevel] == ["low", "medium", "high"]
    assert [item.value for item in ValidationSeverity] == ["warning", "error"]


def test_strategy_request_is_frozen() -> None:
    request = StrategyRequest(policy=PolicyName.COVERAGE_FIRST)

    with pytest.raises(FrozenInstanceError):
        request.policy = PolicyName.BALANCED


@pytest.mark.parametrize(
    "kwargs",
    [
        {"policy": "balanced"},
        {"policy": PolicyName.BALANCED, "minimum_retention_ratio": 10**1000},
        {"policy": PolicyName.BALANCED, "maximum_consecutive_drops": True},
        {"policy": PolicyName.BALANCED, "maximum_consecutive_drops": 8},
    ],
)
def test_strategy_request_rejects_invalid_direct_construction(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        StrategyRequest(**kwargs)


def test_direct_construction_error_exposes_field_metadata() -> None:
    from frame_timing_agent import ConfigurationError

    with pytest.raises(ConfigurationError) as captured:
        StrategyRequest(policy=PolicyName.BALANCED, maximum_consecutive_drops=8)

    assert captured.value.code == "invalid_value"
    assert captured.value.fields == ("maximum_consecutive_drops",)


def test_strategy_request_serializes_with_stable_schema_and_key_order() -> None:
    request = StrategyRequest(
        policy=PolicyName.BALANCED,
        minimum_retention_ratio=0.8,
        maximum_consecutive_drops=2,
    )

    payload = request.to_dict()

    assert list(payload) == [
        "schema_version",
        "policy",
        "minimum_retention_ratio",
        "maximum_consecutive_drops",
    ]
    assert payload == {
        "schema_version": 3,
        "policy": "balanced",
        "minimum_retention_ratio": 0.8,
        "maximum_consecutive_drops": 2,
    }
    assert json.loads(json.dumps(payload)) == payload


def test_package_root_exports_only_task_two_public_contracts() -> None:
    required_exports = {
        "ConfigurationError",
        "PolicyName",
        "RiskLevel",
        "StrategyRequest",
        "ValidationSeverity",
    }

    assert required_exports <= set(frame_timing_agent.__all__)
    assert not {"PolicyPreset", "ResolvedStrategyConfig"} & set(frame_timing_agent.__all__)
    assert all(hasattr(frame_timing_agent, name) for name in required_exports)


def test_analysis_summary_contracts_are_frozen() -> None:
    quality = QualitySummary(1.0, 2.0, 3.0, 4.0, 0)
    trajectory = TrajectorySummary(0.9, 0.01, 0.1, 0, 0, 0)
    analysis_range = AnalysisRange(0, 3, "active_motion", 0.9, "coherent_active_motion")

    with pytest.raises(FrozenInstanceError):
        quality.low_quality_count = 1
    with pytest.raises(FrozenInstanceError):
        trajectory.fallback_count = 1
    with pytest.raises(FrozenInstanceError):
        analysis_range.kind = "jitter"
