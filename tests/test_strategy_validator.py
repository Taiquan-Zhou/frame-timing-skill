from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest
from frame_timing_agent.configuration import ResolvedStrategyConfig, resolve_strategy_request
from frame_timing_agent.contracts import (
    POLICY_REVISION,
    SCHEMA_VERSION,
    AnalysisRange,
    AnalysisResult,
    FrameAnalysis,
    PolicyName,
    QualitySummary,
    RiskLevel,
    StrategyCandidate,
    StrategyRequest,
    TrajectorySummary,
    ValidationSeverity,
)
from frame_timing_agent.serialization import sha256_digest
from frame_timing_agent.strategy_planner import plan_strategy
from frame_timing_agent.strategy_validator import validate_strategy


def _config(policy: PolicyName = PolicyName.BALANCED) -> ResolvedStrategyConfig:
    return resolve_strategy_request(StrategyRequest(policy))


def _analysis(
    frame_count: int = 20,
    *,
    low_confidence_sources: frozenset[int] = frozenset(),
    review_range: tuple[int, int] | None = None,
    default_kind: str = "jitter",
) -> AnalysisResult:
    ranges: list[AnalysisRange] = []
    if review_range is None:
        ranges.append(AnalysisRange(0, frame_count - 1, default_kind, 0.95, f"high_confidence_{default_kind}"))
    else:
        start, end = review_range
        if start > 0:
            ranges.append(AnalysisRange(0, start - 1, "jitter", 0.95, "multiscale_jitter_consensus"))
        ranges.append(AnalysisRange(start, end, "review_required", 0.2, "spatial_motion_uncertain"))
        if end < frame_count - 1:
            ranges.append(AnalysisRange(end + 1, frame_count - 1, "jitter", 0.95, "multiscale_jitter_consensus"))
    frames = tuple(
        FrameAnalysis(
            source_index=index,
            output_index=index,
            timestamp_sec=index / 30.0,
            sharpness=100.0,
            brightness=128.0,
            contrast=40.0,
            dx=0.0,
            dy=0.0,
            rotation_deg=0.0,
            scale=1.0,
            motion_confidence=0.2 if index in low_confidence_sources else 0.95,
            normalized_residual_spatial_iqr=0.0001,
            normalized_residual_spatial_p90=0.0002,
            inlier_spatial_coverage=0.8,
            jitter_score=0.9,
            jitter_confidence=0.95,
            low_quality_candidate=False,
        )
        for index in range(frame_count)
    )
    return AnalysisResult(
        schema_version=SCHEMA_VERSION,
        run_id="validation-run",
        input_digest="sha256:validation-input",
        frame_count=frame_count,
        fps=30.0,
        width=1920,
        height=1080,
        motion_confidence=0.95,
        quality_summary=QualitySummary(100.0, 100.0, 128.0, 40.0, 0),
        trajectory_summary=TrajectorySummary(0.95, 0.01, 0.1, 0, 0, 0),
        frames=frames,
        ranges=tuple(ranges),
        warnings=(),
    )


def _candidate(analysis: AnalysisResult) -> StrategyCandidate:
    return plan_strategy(analysis, _config())


def _codes(candidate: StrategyCandidate, analysis: AnalysisResult) -> set[str]:
    return {issue.code for issue in validate_strategy(analysis, candidate, _config()).issues}


def test_valid_candidate_has_stable_digest_and_no_issues() -> None:
    analysis = _analysis()
    candidate = _candidate(analysis)

    validation = validate_strategy(analysis, candidate, _config())

    assert validation.valid
    assert validation.strategy_id == candidate.strategy_id
    assert validation.input_digest == analysis.input_digest
    assert validation.candidate_digest == sha256_digest(candidate)
    assert validation.issues == ()


@pytest.mark.parametrize(
    "strategy_request",
    [
        StrategyRequest(PolicyName.COVERAGE_FIRST),
        StrategyRequest(PolicyName.BALANCED),
        StrategyRequest(PolicyName.JITTER_REDUCTION),
        StrategyRequest(PolicyName.COVERAGE_FIRST, 0.9, 1),
    ],
)
def test_planner_output_validates_for_all_policies_and_conservative_override(
    strategy_request: StrategyRequest,
) -> None:
    analysis = _analysis()
    config = resolve_strategy_request(strategy_request)
    candidate = plan_strategy(analysis, config)

    validation = validate_strategy(analysis, candidate, config)

    assert validation.valid
    assert validation.issues == ()


def test_input_digest_mismatch_is_an_error() -> None:
    analysis = _analysis()
    candidate = replace(_candidate(analysis), input_digest="sha256:other-input")

    validation = validate_strategy(analysis, candidate, _config())

    assert not validation.valid
    assert "input_digest_mismatch" in {issue.code for issue in validation.issues}
    assert all(issue.severity is ValidationSeverity.ERROR for issue in validation.issues)


def test_tampered_strategy_id_is_rejected_when_candidate_is_revalidated() -> None:
    analysis = _analysis()
    candidate = replace(_candidate(analysis), strategy_id=f"sha256:{'0' * 64}")

    validation = validate_strategy(analysis, candidate, _config())

    assert not validation.valid
    assert "strategy_id_mismatch" in {issue.code for issue in validation.issues}


def test_tampered_policy_revision_is_rejected_explicitly() -> None:
    analysis = _analysis()
    candidate = replace(_candidate(analysis), policy_revision=f"{POLICY_REVISION}-old")

    validation = validate_strategy(analysis, candidate, _config())

    assert not validation.valid
    assert "policy_revision_mismatch" in {issue.code for issue in validation.issues}


def test_constraint_compliant_alternate_selection_is_rejected() -> None:
    analysis = _analysis(default_kind="jitter")
    config = _config(PolicyName.COVERAGE_FIRST)
    candidate = plan_strategy(analysis, config)
    alternate_sources = tuple(source for source in range(20) if source not in {5, 10, 15})
    tampered = replace(
        candidate,
        selected_sources=alternate_sources,
        estimated_output_count=len(alternate_sources),
        retention_ratio=len(alternate_sources) / 20,
        maximum_consecutive_drops=1,
        maximum_source_index_gap=2,
        maximum_time_gap_seconds=2 / 30.0,
    )

    validation = validate_strategy(analysis, tampered, config)

    assert alternate_sources != candidate.selected_sources
    assert not validation.valid
    assert "selected_sources_mismatch" in {issue.code for issue in validation.issues}


@pytest.mark.parametrize(
    ("changes", "expected_code"),
    [
        ({"risk_level": RiskLevel.LOW}, "risk_level_mismatch"),
        ({"estimated_jitter_reduction": 1.0}, "candidate_diagnostic_mismatch"),
        ({"estimated_quality_change": 0.75}, "candidate_diagnostic_mismatch"),
        ({"confidence": 0.1}, "candidate_diagnostic_mismatch"),
        ({"reasons": ("no_review_needed",)}, "candidate_diagnostic_mismatch"),
    ],
)
def test_tampered_risk_or_diagnostics_are_rejected(
    changes: dict[str, object],
    expected_code: str,
) -> None:
    analysis = _analysis()
    config = resolve_strategy_request(StrategyRequest(PolicyName.JITTER_REDUCTION))
    candidate = replace(plan_strategy(analysis, config), **changes)

    validation = validate_strategy(analysis, candidate, config)

    assert not validation.valid
    assert expected_code in {issue.code for issue in validation.issues}


@pytest.mark.parametrize(
    ("sources", "expected_code"),
    [
        ((0, 1, 99, 19), "unknown_source"),
        ((0, 1, 1, 19), "duplicate_source"),
        ((0, 2, 1, 19), "unordered_sources"),
        ((1, 2, 3, 19), "missing_first_source"),
        ((0, 1, 2, 18), "missing_last_source"),
    ],
)
def test_invalid_selected_sources_are_rejected(sources: tuple[int, ...], expected_code: str) -> None:
    analysis = _analysis()
    candidate = replace(_candidate(analysis), selected_sources=sources)

    assert expected_code in _codes(candidate, analysis)


@pytest.mark.parametrize("invalid_source", [True, 1.0, "1"])
def test_invalid_selected_source_runtime_type_is_rejected_without_crashing(invalid_source: object) -> None:
    analysis = _analysis()
    candidate = replace(_candidate(analysis), selected_sources=(0, invalid_source, 19))  # type: ignore[arg-type]

    assert "invalid_source_type" in _codes(candidate, analysis)


def test_retention_is_recomputed_instead_of_trusting_candidate_summary() -> None:
    analysis = _analysis()
    candidate = replace(
        _candidate(analysis),
        selected_sources=(0, 19),
        retention_ratio=1.0,
        estimated_output_count=analysis.frame_count,
    )

    codes = _codes(candidate, analysis)

    assert "retention_below_minimum" in codes
    assert "candidate_metric_mismatch" in codes


def test_consecutive_drops_are_recomputed_from_input_positions() -> None:
    analysis = _analysis()
    selected = (0, 6, *range(7, 20))
    candidate = replace(_candidate(analysis), selected_sources=selected, maximum_consecutive_drops=0)

    codes = _codes(candidate, analysis)

    assert "consecutive_drop_limit_exceeded" in codes
    assert "candidate_metric_mismatch" in codes


def test_coverage_first_rejects_non_static_retention_below_its_stricter_floor() -> None:
    analysis = _analysis(default_kind="jitter")
    config = _config(PolicyName.COVERAGE_FIRST)
    candidate = plan_strategy(analysis, config)
    selected = tuple(source for source in range(20) if source not in {1, 3, 5, 7})
    tampered = replace(
        candidate,
        selected_sources=selected,
        estimated_output_count=len(selected),
        retention_ratio=len(selected) / 20,
        maximum_consecutive_drops=1,
        maximum_source_index_gap=2,
        maximum_time_gap_seconds=2 / 30.0,
    )

    codes = {issue.code for issue in validate_strategy(analysis, tampered, config).issues}

    assert len(selected) / 20 >= config.minimum_retention_ratio
    assert "non_static_retention_below_minimum" in codes


def test_coverage_first_rejects_three_consecutive_non_static_drops() -> None:
    analysis = _analysis(default_kind="jitter")
    config = _config(PolicyName.COVERAGE_FIRST)
    candidate = plan_strategy(analysis, config)
    selected = (0, *range(4, 20))
    tampered = replace(
        candidate,
        selected_sources=selected,
        estimated_output_count=len(selected),
        retention_ratio=len(selected) / 20,
        maximum_consecutive_drops=3,
        maximum_source_index_gap=4,
        maximum_time_gap_seconds=4 / 30.0,
    )

    codes = {issue.code for issue in validate_strategy(analysis, tampered, config).issues}

    assert len(selected) / 20 >= config.minimum_retention_ratio
    assert tampered.maximum_consecutive_drops <= config.maximum_consecutive_drops
    assert "non_static_consecutive_drop_limit_exceeded" in codes


def test_coverage_first_rejects_removal_of_confirmed_static_range_endpoint() -> None:
    analysis = replace(
        _analysis(default_kind="active_motion"),
        ranges=(
            AnalysisRange(0, 4, "active_motion", 0.95, "coherent_active_motion"),
            AnalysisRange(5, 14, "static", 0.95, "low_motion_high_confidence"),
            AnalysisRange(15, 19, "active_motion", 0.95, "coherent_active_motion"),
        ),
    )
    config = _config(PolicyName.COVERAGE_FIRST)
    candidate = plan_strategy(analysis, config)
    selected = tuple(source for source in candidate.selected_sources if source != 5)
    tampered = replace(candidate, selected_sources=selected)

    codes = {issue.code for issue in validate_strategy(analysis, tampered, config).issues}

    assert "static_range_endpoint_removed" in codes


def test_balanced_internal_static_endpoint_removal_remains_valid() -> None:
    analysis = replace(
        _analysis(default_kind="active_motion"),
        ranges=(
            AnalysisRange(0, 4, "active_motion", 0.95, "coherent_active_motion"),
            AnalysisRange(5, 14, "static", 0.95, "low_motion_high_confidence"),
            AnalysisRange(15, 19, "active_motion", 0.95, "coherent_active_motion"),
        ),
    )
    config = _config(PolicyName.BALANCED)

    validation = validate_strategy(analysis, plan_strategy(analysis, config), config)

    assert validation.valid
    assert "static_range_endpoint_removed" not in {issue.code for issue in validation.issues}


def test_deleted_low_confidence_source_is_rejected() -> None:
    analysis = _analysis(low_confidence_sources=frozenset({10}))
    candidate = replace(_candidate(analysis), selected_sources=tuple(index for index in range(20) if index != 10))

    assert "low_confidence_source_removed" in _codes(candidate, analysis)


def test_deleted_review_required_source_is_rejected() -> None:
    analysis = _analysis(review_range=(8, 12))
    candidate = replace(_candidate(analysis), selected_sources=tuple(index for index in range(20) if index != 10))

    assert "review_required_source_removed" in _codes(candidate, analysis)


def test_deleted_active_motion_source_is_rejected() -> None:
    analysis = _analysis(default_kind="active_motion")
    candidate = replace(_candidate(analysis), selected_sources=tuple(index for index in range(20) if index != 10))

    assert "active_motion_source_removed" in _codes(candidate, analysis)


def test_candidate_policy_and_effective_request_must_match_config() -> None:
    analysis = _analysis()
    candidate = replace(_candidate(analysis), policy=PolicyName.COVERAGE_FIRST)

    assert "policy_mismatch" in _codes(candidate, analysis)


def test_validation_is_frozen() -> None:
    analysis = _analysis()
    validation = validate_strategy(analysis, _candidate(analysis), _config())

    with pytest.raises(FrozenInstanceError):
        validation.valid = False
