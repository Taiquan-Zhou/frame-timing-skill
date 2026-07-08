from __future__ import annotations

import math
from dataclasses import replace

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
)
from frame_timing_agent.strategy_planner import plan_strategy


def _analysis(
    frame_count: int,
    *,
    ranges: tuple[AnalysisRange, ...],
    source_indices: tuple[int, ...] | None = None,
    low_quality_sources: frozenset[int] = frozenset(),
    low_confidence_sources: frozenset[int] = frozenset(),
) -> AnalysisResult:
    sources = source_indices or tuple(range(frame_count))
    frames: list[FrameAnalysis] = []
    for output_index, source_index in enumerate(sources):
        kind = _kind_for_source(source_index, ranges)
        frames.append(
            FrameAnalysis(
                source_index=source_index,
                output_index=output_index,
                timestamp_sec=output_index / 30.0,
                sharpness=20.0 if source_index in low_quality_sources else 100.0,
                brightness=128.0,
                contrast=40.0,
                dx=0.3 if kind == "active_motion" else 0.0,
                dy=0.0,
                rotation_deg=0.0,
                scale=1.0,
                motion_confidence=0.2 if source_index in low_confidence_sources else 0.95,
                normalized_residual_spatial_iqr=0.0001,
                normalized_residual_spatial_p90=0.0002,
                inlier_spatial_coverage=0.8,
                jitter_score=0.9 if kind in {"jitter", "review_required"} else 0.05,
                jitter_confidence=0.95 if kind == "jitter" else 0.0,
                low_quality_candidate=source_index in low_quality_sources,
            )
        )
    return AnalysisResult(
        schema_version=SCHEMA_VERSION,
        run_id="synthetic-run",
        input_digest="sha256:synthetic",
        frame_count=len(frames),
        fps=30.0,
        width=1920,
        height=1080,
        motion_confidence=0.95,
        quality_summary=QualitySummary(20.0, 100.0, 128.0, 40.0, len(low_quality_sources)),
        trajectory_summary=TrajectorySummary(0.95, 0.01, 0.1, 0, 0, 0),
        frames=tuple(frames),
        ranges=ranges,
        warnings=(),
    )


def _kind_for_source(source_index: int, ranges: tuple[AnalysisRange, ...]) -> str:
    for item in ranges:
        if item.start <= source_index <= item.end:
            return item.kind
    return "active_motion"


def _config(policy: PolicyName) -> ResolvedStrategyConfig:
    return resolve_strategy_request(StrategyRequest(policy=policy))


def _maximum_consecutive_drops(all_sources: tuple[int, ...], selected_sources: tuple[int, ...]) -> int:
    selected = set(selected_sources)
    maximum = current = 0
    for source in all_sources:
        if source in selected:
            current = 0
        else:
            current += 1
            maximum = max(maximum, current)
    return maximum


def _selected_in_range(candidate: StrategyCandidate, start: int, end: int) -> tuple[int, ...]:
    return tuple(source for source in candidate.selected_sources if start <= source <= end)


def test_policy_presets_produce_ordered_candidates_with_explainable_metrics() -> None:
    analysis = _analysis(
        120,
        ranges=(
            AnalysisRange(10, 49, "static", 0.95, "low_motion_high_confidence"),
            AnalysisRange(60, 99, "jitter", 0.95, "multiscale_jitter_consensus"),
        ),
    )

    candidates = [plan_strategy(analysis, _config(policy)) for policy in PolicyName]

    counts = [candidate.estimated_output_count for candidate in candidates]
    assert counts[0] > counts[1] > counts[2]
    assert all(candidate.strategy_id for candidate in candidates)
    assert all(0.0 <= candidate.estimated_jitter_reduction <= 1.0 for candidate in candidates)
    assert all(math.isfinite(candidate.estimated_quality_change) for candidate in candidates)
    assert all(0.0 <= candidate.confidence <= 1.0 for candidate in candidates)
    assert [candidate.risk_level for candidate in candidates] == [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH]
    assert all(candidate.policy_revision == POLICY_REVISION for candidate in candidates)
    assert "redundant_static_removed" in candidates[0].reasons
    assert "redundant_static_removed" in candidates[1].reasons


def test_strategy_id_records_current_policy_revision() -> None:
    analysis = _analysis(
        40,
        ranges=(AnalysisRange(0, 39, "jitter", 0.95, "multiscale_jitter_consensus"),),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.COVERAGE_FIRST))

    assert candidate.policy_revision == POLICY_REVISION
    assert POLICY_REVISION in candidate.strategy_id


def test_coverage_first_thins_confirmed_static_before_touching_motion() -> None:
    analysis = _analysis(
        120,
        ranges=(
            AnalysisRange(0, 39, "static", 0.90, "low_motion_high_confidence"),
            AnalysisRange(40, 79, "jitter", 0.95, "multiscale_jitter_consensus"),
            AnalysisRange(80, 119, "active_motion", 0.95, "coherent_active_motion"),
        ),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.COVERAGE_FIRST))
    selected_static = _selected_in_range(candidate, 0, 39)
    selected_non_static = _selected_in_range(candidate, 40, 119)

    assert len(selected_static) <= 10
    assert selected_static[0] == 0
    assert selected_static[-1] == 39
    assert selected_non_static == tuple(range(40, 120))
    assert candidate.retention_ratio >= 0.75
    assert candidate.maximum_consecutive_drops <= 4
    assert "redundant_static_removed" in candidate.reasons


def test_coverage_first_does_not_thin_static_below_very_high_confidence() -> None:
    analysis = _analysis(
        40,
        ranges=(AnalysisRange(0, 39, "static", 0.899999, "low_motion_high_confidence"),),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.COVERAGE_FIRST))

    assert candidate.selected_sources == tuple(range(40))
    assert "static_confidence_guard_applied" in candidate.reasons


def test_coverage_first_keeps_original_non_static_safety_density() -> None:
    analysis = _analysis(
        120,
        ranges=(AnalysisRange(0, 119, "jitter", 0.95, "multiscale_jitter_consensus"),),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.COVERAGE_FIRST))

    assert candidate.retention_ratio >= 0.85
    assert candidate.maximum_consecutive_drops <= 2
    assert "non_static_coverage_floor_applied" in candidate.reasons


def test_continuous_slow_translation_is_not_treated_as_static_or_compressed() -> None:
    analysis = _analysis(
        570,
        ranges=(AnalysisRange(0, 569, "active_motion", 0.95, "coherent_active_motion"),),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.JITTER_REDUCTION))

    assert candidate.selected_sources == tuple(range(570))
    assert candidate.estimated_output_count == 570
    assert "redundant_static_removed" not in candidate.reasons
    assert "no_safe_removals" in candidate.reasons


def test_candidate_enforces_reconstruction_coverage_using_input_positions() -> None:
    sources = tuple(index * 10 for index in range(30))
    analysis = _analysis(
        len(sources),
        source_indices=sources,
        ranges=(AnalysisRange(sources[1], sources[-2], "jitter", 0.95, "multiscale_jitter_consensus"),),
    )
    config = ResolvedStrategyConfig(PolicyName.BALANCED, 0.65, 2)

    candidate = plan_strategy(analysis, config)

    assert candidate.selected_sources[0] == sources[0]
    assert candidate.selected_sources[-1] == sources[-1]
    assert candidate.selected_sources == tuple(sorted(set(candidate.selected_sources)))
    assert candidate.retention_ratio >= config.minimum_retention_ratio
    assert _maximum_consecutive_drops(sources, candidate.selected_sources) <= config.maximum_consecutive_drops
    assert candidate.maximum_consecutive_drops <= config.maximum_consecutive_drops
    assert candidate.maximum_source_index_gap >= 10


def test_more_conservative_retention_override_keeps_more_frames() -> None:
    analysis = _analysis(
        120,
        ranges=(AnalysisRange(1, 118, "jitter", 0.95, "multiscale_jitter_consensus"),),
    )
    loose = ResolvedStrategyConfig(PolicyName.JITTER_REDUCTION, 0.45, 7)
    strict = ResolvedStrategyConfig(PolicyName.JITTER_REDUCTION, 0.90, 7)

    loose_candidate = plan_strategy(analysis, loose)
    strict_candidate = plan_strategy(analysis, strict)

    assert strict_candidate.estimated_output_count > loose_candidate.estimated_output_count
    assert strict_candidate.retention_ratio >= strict.minimum_retention_ratio
    assert loose_candidate.retention_ratio >= loose.minimum_retention_ratio


def test_consecutive_drop_override_is_enforced_and_reported() -> None:
    analysis = _analysis(
        120,
        ranges=(AnalysisRange(1, 118, "jitter", 0.95, "multiscale_jitter_consensus"),),
    )
    config = ResolvedStrategyConfig(PolicyName.JITTER_REDUCTION, 0.45, 1)

    candidate = plan_strategy(analysis, config)

    assert candidate.maximum_consecutive_drops <= config.maximum_consecutive_drops
    assert "consecutive_drop_limit_applied" in candidate.reasons


@pytest.mark.parametrize("kind", ["active_motion", "jitter"])
def test_low_quality_frame_without_safe_substitute_is_retained(kind: str) -> None:
    analysis = _analysis(
        20,
        ranges=(AnalysisRange(10, 10, kind, 0.95, "isolated_low_quality_frame"),),
        low_quality_sources=frozenset({10}),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.JITTER_REDUCTION))

    assert 10 in candidate.selected_sources
    assert "low_quality_without_substitute_retained" in candidate.reasons


def test_low_quality_frame_with_local_safe_substitute_can_be_removed() -> None:
    analysis = _analysis(
        20,
        ranges=(AnalysisRange(0, 19, "static", 0.95, "low_motion_high_confidence"),),
        low_quality_sources=frozenset({10}),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.COVERAGE_FIRST))

    assert 10 not in candidate.selected_sources
    assert {9, 11} & set(candidate.selected_sources)
    assert "low_quality_with_substitute_removed" in candidate.reasons
    assert "redundant_static_removed" in candidate.reasons


@pytest.mark.parametrize("kind", ["static", "jitter"])
def test_low_confidence_range_vetoes_low_quality_removal(kind: str) -> None:
    frame_count = 31
    center = frame_count // 2
    high_confidence_sources = {center - 1, center, center + 1}
    confidences = tuple(0.95 if source in high_confidence_sources else 0.45 for source in range(frame_count))
    range_confidence = sum(confidences) / frame_count
    analysis = _analysis(
        frame_count,
        ranges=(AnalysisRange(0, frame_count - 1, kind, range_confidence, "low_confidence_range"),),
        low_quality_sources=frozenset({center}),
    )
    analysis = replace(
        analysis,
        frames=tuple(
            replace(frame, motion_confidence=confidences[position]) for position, frame in enumerate(analysis.frames)
        ),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.COVERAGE_FIRST))

    assert range_confidence < 0.5
    assert center in candidate.selected_sources
    assert "low_confidence_frames_retained" in candidate.reasons
    assert "low_quality_with_substitute_removed" not in candidate.reasons


@pytest.mark.parametrize(
    "reason",
    [
        "spatial_motion_uncertain",
        "low_inlier_spatial_coverage",
        "multiscale_motion_disagreement",
        "motion_decision_deadband",
    ],
)
def test_review_required_frame_is_never_deleted_for_high_jitter_score(reason: str) -> None:
    analysis = _analysis(
        24,
        ranges=(AnalysisRange(8, 12, "review_required", 0.2, reason),),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.JITTER_REDUCTION))

    assert {8, 9, 10, 11, 12} <= set(candidate.selected_sources)
    assert "review_required_frames_retained" in candidate.reasons
    assert f"review:{reason}" in candidate.reasons


def test_low_confidence_jitter_frame_is_retained_and_reported() -> None:
    analysis = _analysis(
        24,
        ranges=(AnalysisRange(8, 12, "jitter", 0.95, "multiscale_jitter_consensus"),),
        low_confidence_sources=frozenset({10}),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.JITTER_REDUCTION))

    assert 10 in candidate.selected_sources
    assert "low_confidence_frames_retained" in candidate.reasons


def test_static_thinning_does_not_claim_jitter_reduction_when_mean_jitter_is_unchanged() -> None:
    analysis = _analysis(
        40,
        ranges=(AnalysisRange(0, 39, "static", 0.95, "low_motion_high_confidence"),),
    )

    candidate = plan_strategy(analysis, _config(PolicyName.BALANCED))

    assert candidate.estimated_output_count < analysis.frame_count
    assert candidate.estimated_jitter_reduction == pytest.approx(0.0)


def test_initial_placeholder_does_not_reduce_candidate_confidence() -> None:
    analysis = _analysis(
        3,
        ranges=(AnalysisRange(0, 2, "active_motion", 0.95, "coherent_active_motion"),),
    )
    analysis = replace(
        analysis,
        frames=(replace(analysis.frames[0], motion_confidence=0.0), *analysis.frames[1:]),
        motion_confidence=0.95,
    )

    candidate = plan_strategy(analysis, _config(PolicyName.COVERAGE_FIRST))

    assert candidate.confidence == pytest.approx(analysis.motion_confidence)


def test_planning_is_decision_deterministic_across_repeated_runs() -> None:
    analysis = _analysis(
        90,
        ranges=(
            AnalysisRange(5, 35, "static", 0.95, "low_motion_high_confidence"),
            AnalysisRange(45, 80, "jitter", 0.95, "multiscale_jitter_consensus"),
        ),
    )
    config = _config(PolicyName.BALANCED)

    candidates = [plan_strategy(analysis, config) for _ in range(20)]

    expected = candidates[0]
    assert all(candidate.selected_sources == expected.selected_sources for candidate in candidates[1:])
    assert all(candidate.risk_level == expected.risk_level for candidate in candidates[1:])
    assert all(candidate.reasons == expected.reasons for candidate in candidates[1:])
    assert all(candidate.strategy_id == expected.strategy_id for candidate in candidates[1:])
    assert isinstance(expected, StrategyCandidate)
