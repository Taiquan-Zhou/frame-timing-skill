from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass

from frame_timing_agent.configuration import ResolvedStrategyConfig
from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AnalysisRange,
    AnalysisResult,
    FrameAnalysis,
    PolicyName,
    RiskLevel,
    StrategyCandidate,
    StrategyRequest,
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

    kinds = tuple(_kind_for_frame(frame.source_index, analysis.ranges) for frame in frames)
    options, low_quality_without_substitute, low_confidence_retained = _deletion_options(
        frames, kinds, analysis.ranges, config.policy
    )
    minimum_count = math.ceil(len(frames) * config.minimum_retention_ratio)
    selected_positions = set(range(len(frames)))
    protected_positions = {0, len(frames) - 1}
    removal_reasons: set[str] = set()
    coverage_floor_applied = False
    consecutive_drop_limit_applied = False

    for option in options:
        if option.position in protected_positions or option.position not in selected_positions:
            continue
        if len(selected_positions) <= minimum_count:
            coverage_floor_applied = True
            break
        if not _drop_respects_limit(option.position, len(frames), selected_positions, config.maximum_consecutive_drops):
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

    selected_frames = tuple(frame for position, frame in enumerate(frames) if position in selected_positions)
    selected_sources = tuple(frame.source_index for frame in selected_frames)
    reasons = _candidate_reasons(
        analysis.ranges,
        removal_reasons,
        low_quality_without_substitute,
        low_confidence_retained,
        coverage_floor_applied,
        consecutive_drop_limit_applied,
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
        input_digest=analysis.input_digest,
        policy=config.policy,
        request=request,
        selected_sources=selected_sources,
        estimated_output_count=len(selected_frames),
        retention_ratio=len(selected_frames) / len(frames),
        maximum_consecutive_drops=_maximum_consecutive_drops(len(frames), selected_positions),
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
) -> tuple[list[_DeletionOption], bool, bool]:
    options: list[_DeletionOption] = []
    low_quality_without_substitute = False
    low_confidence_retained = False
    for position in range(1, len(frames) - 1):
        frame = frames[position]
        kind = kinds[position]
        range_confidence = _range_confidence(frame.source_index, ranges)
        low_confidence = (
            frame.motion_confidence < _MINIMUM_DECISION_CONFIDENCE or range_confidence < _MINIMUM_DECISION_CONFIDENCE
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
            and range_confidence >= _MINIMUM_DECISION_CONFIDENCE
        ):
            options.append(_DeletionOption(position, 1))
        elif (
            kind == "static"
            and policy is not PolicyName.COVERAGE_FIRST
            and range_confidence >= _MINIMUM_DECISION_CONFIDENCE
        ):
            options.append(_DeletionOption(position, 2))

    options.sort(
        key=lambda item: (
            item.category,
            -frames[item.position].jitter_score,
            frames[item.position].sharpness,
            item.position,
        )
    )
    return options, low_quality_without_substitute, low_confidence_retained


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


def _kind_for_frame(source_index: int, ranges: tuple[AnalysisRange, ...]) -> str:
    matching = [item for item in ranges if item.start <= source_index <= item.end]
    if any(item.kind == "review_required" for item in matching):
        return "review_required"
    for kind in ("active_motion", "jitter", "static"):
        if any(item.kind == kind for item in matching):
            return kind
    return "review_required"


def _range_confidence(source_index: int, ranges: tuple[AnalysisRange, ...]) -> float:
    matching = [item.confidence for item in ranges if item.start <= source_index <= item.end]
    return min(matching, default=0.0)


def _candidate_reasons(
    ranges: tuple[AnalysisRange, ...],
    removal_reasons: set[str],
    low_quality_without_substitute: bool,
    low_confidence_retained: bool,
    coverage_floor_applied: bool,
    consecutive_drop_limit_applied: bool,
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
    if coverage_floor_applied:
        reasons.append("coverage_floor_applied")
    if consecutive_drop_limit_applied:
        reasons.append("consecutive_drop_limit_applied")
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


def _maximum_consecutive_drops(frame_count: int, selected_positions: set[int]) -> int:
    maximum = current = 0
    for position in range(frame_count):
        if position in selected_positions:
            current = 0
        else:
            current += 1
            maximum = max(maximum, current)
    return maximum


def _drop_respects_limit(position: int, frame_count: int, selected_positions: set[int], limit: int) -> bool:
    consecutive = 1
    left = position - 1
    while left >= 0 and left not in selected_positions:
        consecutive += 1
        left -= 1
    right = position + 1
    while right < frame_count and right not in selected_positions:
        consecutive += 1
        right += 1
    return consecutive <= limit


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
        input_digest,
        config.policy.value,
        config.minimum_retention_ratio.hex(),
        str(config.maximum_consecutive_drops),
        ",".join(str(source) for source in selected_sources),
    )
    digest = hashlib.sha256("\n".join(components).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
