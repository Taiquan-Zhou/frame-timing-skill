from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from frame_timing_agent.configuration import ResolvedStrategyConfig, strategy_safety_limits
from frame_timing_agent.contracts import (
    POLICY_REVISION,
    SCHEMA_VERSION,
    AnalysisRange,
    AnalysisResult,
    FrameAnalysis,
    PolicyName,
    RiskLevel,
    StrategyCandidate,
    StrategyRequest,
)
from frame_timing_agent.selection_constraints import (
    deletion_respects_limit,
    deletion_respects_non_static_limit,
    frame_kinds,
    maximum_consecutive_drops,
    range_confidence,
    static_range_endpoint_positions,
)

_MINIMUM_DECISION_CONFIDENCE = 0.5


@dataclass(frozen=True)
class _DeletionOption:
    position: int
    category: int
    replacement_position: int | None = None


def plan_strategy(analysis: AnalysisResult, config: ResolvedStrategyConfig) -> StrategyCandidate:
    """Plan a deterministic, coverage-constrained frame selection candidate."""
    frames = analysis.frames
    if not frames:
        raise ValueError("analysis must contain at least one frame")

    safety_limits = strategy_safety_limits(config)
    source_indices = tuple(frame.source_index for frame in frames)
    kinds = frame_kinds(source_indices, analysis.ranges)
    options, low_quality_without_substitute, low_confidence_retained, static_confidence_guard_applied = (
        _deletion_options(
            frames,
            kinds,
            analysis.ranges,
            config.policy,
            safety_limits.minimum_static_range_confidence,
        )
    )
    minimum_count = math.ceil(len(frames) * config.minimum_retention_ratio)
    non_static_positions = {position for position, kind in enumerate(kinds) if kind != "static"}
    minimum_non_static_count = None
    if safety_limits.minimum_non_static_retention_ratio is not None:
        minimum_non_static_count = math.ceil(
            len(non_static_positions) * safety_limits.minimum_non_static_retention_ratio
        )
    selected_positions = set(range(len(frames)))
    selected_non_static_count = len(non_static_positions)
    protected_positions = {0, len(frames) - 1}
    if safety_limits.protect_static_range_endpoints:
        protected_positions.update(
            static_range_endpoint_positions(
                source_indices,
                kinds,
                analysis.ranges,
                safety_limits.minimum_static_range_confidence,
            )
        )
    removal_reasons: set[str] = set()
    coverage_floor_applied = False
    consecutive_drop_limit_applied = False
    non_static_coverage_floor_applied = False
    non_static_consecutive_drop_limit_applied = False

    for option in options:
        if option.position in protected_positions or option.position not in selected_positions:
            continue
        if len(selected_positions) <= minimum_count:
            coverage_floor_applied = True
            break
        if option.position in non_static_positions and minimum_non_static_count is not None:
            if selected_non_static_count <= minimum_non_static_count:
                non_static_coverage_floor_applied = True
                continue
            maximum_non_static_drops = safety_limits.maximum_non_static_consecutive_drops
            if maximum_non_static_drops is not None and not deletion_respects_non_static_limit(
                option.position,
                kinds,
                selected_positions,
                maximum_non_static_drops,
            ):
                non_static_consecutive_drop_limit_applied = True
                continue
        if not deletion_respects_limit(
            option.position,
            len(frames),
            selected_positions,
            config.maximum_consecutive_drops,
        ):
            consecutive_drop_limit_applied = True
            continue
        if option.replacement_position is not None:
            if option.replacement_position not in selected_positions:
                continue
            protected_positions.add(option.replacement_position)
            removal_reasons.add("low_quality_with_substitute_removed")
        if option.category == 1:
            removal_reasons.add("high_confidence_jitter_removed")
        elif option.category == 2:
            removal_reasons.add("redundant_static_removed")
        selected_positions.remove(option.position)
        if option.position in non_static_positions:
            selected_non_static_count -= 1

    selected_frames = tuple(frame for position, frame in enumerate(frames) if position in selected_positions)
    selected_sources = tuple(frame.source_index for frame in selected_frames)
    reasons = _candidate_reasons(
        analysis.ranges,
        removal_reasons,
        low_quality_without_substitute,
        low_confidence_retained,
        static_confidence_guard_applied,
        coverage_floor_applied,
        consecutive_drop_limit_applied,
        non_static_coverage_floor_applied,
        non_static_consecutive_drop_limit_applied,
    )
    risk_level = _risk_level(config.policy, analysis.ranges)
    request = StrategyRequest(
        policy=config.policy,
        minimum_retention_ratio=config.minimum_retention_ratio,
        maximum_consecutive_drops=config.maximum_consecutive_drops,
    )

    return StrategyCandidate(
        schema_version=SCHEMA_VERSION,
        strategy_id=_strategy_id(analysis.input_digest, config, selected_sources),
        policy_revision=POLICY_REVISION,
        input_digest=analysis.input_digest,
        policy=config.policy,
        request=request,
        selected_sources=selected_sources,
        estimated_output_count=len(selected_frames),
        retention_ratio=len(selected_frames) / len(frames),
        maximum_consecutive_drops=maximum_consecutive_drops(len(frames), selected_positions),
        maximum_source_index_gap=_maximum_source_index_gap(selected_sources),
        maximum_time_gap_seconds=_maximum_time_gap_seconds(selected_frames),
        estimated_jitter_reduction=_estimated_jitter_reduction(frames, selected_frames),
        estimated_quality_change=_estimated_quality_change(frames, selected_frames),
        confidence=analysis.motion_confidence,
        risk_level=risk_level,
        reasons=reasons,
    )


def _deletion_options(
    frames: tuple[FrameAnalysis, ...],
    kinds: tuple[str, ...],
    ranges: tuple[AnalysisRange, ...],
    policy: PolicyName,
    minimum_static_range_confidence: float,
) -> tuple[list[_DeletionOption], bool, bool, bool]:
    options: list[_DeletionOption] = []
    low_quality_without_substitute = False
    low_confidence_retained = False
    static_confidence_guard_applied = False
    for position in range(1, len(frames) - 1):
        frame = frames[position]
        kind = kinds[position]
        frame_range_confidence = range_confidence(frame.source_index, ranges)
        low_confidence = (
            frame.motion_confidence < _MINIMUM_DECISION_CONFIDENCE
            or frame_range_confidence < _MINIMUM_DECISION_CONFIDENCE
        )
        if kind in {"review_required", "active_motion"}:
            if frame.low_quality_candidate:
                low_quality_without_substitute = True
            if low_confidence:
                low_confidence_retained = True
            continue
        if low_confidence:
            low_confidence_retained = True
            continue
        if kind == "static" and frame_range_confidence < minimum_static_range_confidence:
            static_confidence_guard_applied = True
            continue

        if frame.low_quality_candidate:
            replacement = _safe_replacement_position(position, frames, kinds)
            if replacement is None:
                low_quality_without_substitute = True
                continue
            options.append(_DeletionOption(position, 0, replacement))
            continue

        if (
            kind == "jitter"
            and frame.jitter_confidence >= _MINIMUM_DECISION_CONFIDENCE
            and frame_range_confidence >= _MINIMUM_DECISION_CONFIDENCE
        ):
            options.append(_DeletionOption(position, 1))
        elif kind == "static" and frame_range_confidence >= minimum_static_range_confidence:
            options.append(_DeletionOption(position, 2))

    category_priority = {0: 0, 2: 1, 1: 2} if policy is PolicyName.COVERAGE_FIRST else {0: 0, 1: 1, 2: 2}
    options.sort(
        key=lambda item: (
            category_priority[item.category],
            -frames[item.position].jitter_score,
            frames[item.position].sharpness,
            item.position,
        )
    )
    return options, low_quality_without_substitute, low_confidence_retained, static_confidence_guard_applied


def _safe_replacement_position(
    position: int,
    frames: tuple[FrameAnalysis, ...],
    kinds: tuple[str, ...],
) -> int | None:
    candidate = frames[position]
    if kinds[position] not in {"jitter", "static"}:
        return None
    for neighbor in (position - 1, position + 1):
        replacement = frames[neighbor]
        if (
            kinds[neighbor] == kinds[position]
            and not replacement.low_quality_candidate
            and replacement.motion_confidence >= _MINIMUM_DECISION_CONFIDENCE
            and replacement.sharpness > candidate.sharpness
        ):
            return neighbor
    return None


def _candidate_reasons(
    ranges: tuple[AnalysisRange, ...],
    removal_reasons: set[str],
    low_quality_without_substitute: bool,
    low_confidence_retained: bool,
    static_confidence_guard_applied: bool,
    coverage_floor_applied: bool,
    consecutive_drop_limit_applied: bool,
    non_static_coverage_floor_applied: bool,
    non_static_consecutive_drop_limit_applied: bool,
) -> tuple[str, ...]:
    reasons: list[str] = []
    review_reasons = sorted({item.reason for item in ranges if item.kind == "review_required"})
    if review_reasons:
        reasons.append("review_required_frames_retained")
        reasons.extend(f"review:{reason}" for reason in review_reasons)
    for code in (
        "low_quality_with_substitute_removed",
        "high_confidence_jitter_removed",
        "redundant_static_removed",
    ):
        if code in removal_reasons:
            reasons.append(code)
    if low_quality_without_substitute:
        reasons.append("low_quality_without_substitute_retained")
    if low_confidence_retained:
        reasons.append("low_confidence_frames_retained")
    if static_confidence_guard_applied:
        reasons.append("static_confidence_guard_applied")
    if coverage_floor_applied:
        reasons.append("coverage_floor_applied")
    if consecutive_drop_limit_applied:
        reasons.append("consecutive_drop_limit_applied")
    if non_static_coverage_floor_applied:
        reasons.append("non_static_coverage_floor_applied")
    if non_static_consecutive_drop_limit_applied:
        reasons.append("non_static_consecutive_drop_limit_applied")
    if not removal_reasons:
        reasons.append("no_safe_removals")
    return tuple(reasons)


def _risk_level(policy: PolicyName, ranges: tuple[AnalysisRange, ...]) -> RiskLevel:
    base = {
        PolicyName.COVERAGE_FIRST: RiskLevel.LOW,
        PolicyName.BALANCED: RiskLevel.MEDIUM,
        PolicyName.JITTER_REDUCTION: RiskLevel.HIGH,
    }[policy]
    if not any(item.kind == "review_required" for item in ranges):
        return base
    if base is RiskLevel.LOW:
        return RiskLevel.MEDIUM
    return RiskLevel.HIGH


def _maximum_source_index_gap(selected_sources: tuple[int, ...]) -> int:
    return max((right - left for left, right in zip(selected_sources, selected_sources[1:], strict=False)), default=0)


def _maximum_time_gap_seconds(selected_frames: tuple[FrameAnalysis, ...]) -> float:
    return max(
        (
            right.timestamp_sec - left.timestamp_sec
            for left, right in zip(selected_frames, selected_frames[1:], strict=False)
        ),
        default=0.0,
    )


def _estimated_jitter_reduction(frames: tuple[FrameAnalysis, ...], selected_frames: tuple[FrameAnalysis, ...]) -> float:
    original_mean = sum(frame.jitter_score for frame in frames) / len(frames)
    if original_mean <= 0:
        return 0.0
    selected_mean = sum(frame.jitter_score for frame in selected_frames) / len(selected_frames)
    return min(1.0, max(0.0, (original_mean - selected_mean) / original_mean))


def _estimated_quality_change(frames: tuple[FrameAnalysis, ...], selected_frames: tuple[FrameAnalysis, ...]) -> float:
    original_mean = sum(frame.sharpness for frame in frames) / len(frames)
    selected_mean = sum(frame.sharpness for frame in selected_frames) / len(selected_frames)
    if original_mean == 0:
        return 0.0
    return (selected_mean - original_mean) / abs(original_mean)


def _strategy_id(
    input_digest: str,
    config: ResolvedStrategyConfig,
    selected_sources: tuple[int, ...],
) -> str:
    components = (
        POLICY_REVISION,
        input_digest,
        config.policy.value,
        config.minimum_retention_ratio.hex(),
        str(config.maximum_consecutive_drops),
        ",".join(str(source) for source in selected_sources),
    )
    digest = hashlib.sha256("\n".join(components).encode("utf-8")).hexdigest()
    return f"{POLICY_REVISION}:sha256:{digest}"
