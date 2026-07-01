from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace

import frame_timing_agent
import pytest
from frame_timing_agent import PolicyName, RiskLevel, StrategyRequest, ValidationSeverity
from frame_timing_agent.contracts import (
    AnalysisRange,
    ExecutionResult,
    QualitySummary,
    StrategyCandidate,
    TrajectorySummary,
    ValidationIssue,
    ValidationResult,
)


def _strategy_candidate() -> StrategyCandidate:
    return StrategyCandidate(
        schema_version=3,
        strategy_id="strategy-id",
        input_digest="sha256:input",
        policy=PolicyName.BALANCED,
        request=StrategyRequest(
            policy=PolicyName.BALANCED,
            minimum_retention_ratio=0.65,
            maximum_consecutive_drops=4,
        ),
        selected_sources=(0, 2, 4),
        estimated_output_count=3,
        retention_ratio=0.6,
        maximum_consecutive_drops=1,
        maximum_source_index_gap=2,
        maximum_time_gap_seconds=0.1,
        estimated_jitter_reduction=0.5,
        estimated_quality_change=0.1,
        confidence=0.9,
        risk_level=RiskLevel.MEDIUM,
        reasons=("high_confidence_jitter_removed",),
    )


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


def test_strategy_candidate_contract_is_frozen() -> None:
    candidate = _strategy_candidate()

    with pytest.raises(FrozenInstanceError):
        candidate.risk_level = RiskLevel.HIGH


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("retention_ratio", 10**1000),
        ("maximum_time_gap_seconds", float("inf")),
        ("estimated_jitter_reduction", float("nan")),
        ("estimated_quality_change", "invalid"),
        ("confidence", True),
        ("estimated_output_count", True),
        ("maximum_consecutive_drops", -1),
        ("maximum_source_index_gap", -1),
    ],
)
def test_strategy_candidate_rejects_invalid_numeric_fields(field: str, value: object) -> None:
    from frame_timing_agent import ConfigurationError

    with pytest.raises(ConfigurationError) as captured:
        replace(_strategy_candidate(), **{field: value})

    assert captured.value.code == "invalid_value"
    assert captured.value.fields == (field,)


def test_validation_contracts_are_frozen() -> None:
    issue = ValidationIssue("unsafe", ValidationSeverity.ERROR, "unsafe candidate", (1, 2))
    result = ValidationResult(False, "strategy", "input", "candidate", (issue,))

    with pytest.raises(FrozenInstanceError):
        issue.code = "changed"
    with pytest.raises(FrozenInstanceError):
        result.valid = True


def test_execution_result_contract_is_frozen() -> None:
    result = ExecutionResult(3, "run", "strategy", "input", "candidate", 2, (0, 1), "run_manifest.json", "output")

    with pytest.raises(FrozenInstanceError):
        result.output_frame_count = 0
