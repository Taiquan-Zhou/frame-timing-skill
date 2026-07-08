from __future__ import annotations

import math

from frame_timing_agent.configuration import ResolvedStrategyConfig, StrategySafetyLimits, strategy_safety_limits
from frame_timing_agent.contracts import (
    POLICY_REVISION,
    SCHEMA_VERSION,
    AnalysisRange,
    AnalysisResult,
    FrameAnalysis,
    StrategyCandidate,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from frame_timing_agent.selection_constraints import (
    frame_kinds,
    maximum_consecutive_drops,
    maximum_non_static_consecutive_drops,
    non_static_retention_ratio,
    range_confidence,
    static_range_endpoint_positions,
)
from frame_timing_agent.serialization import sha256_digest
from frame_timing_agent.strategy_planner import plan_strategy

_MINIMUM_DECISION_CONFIDENCE = 0.5


def validate_strategy(
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    config: ResolvedStrategyConfig,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    candidate_digest = _candidate_digest(candidate, issues)
    expected_candidate = plan_strategy(analysis, config)
    frames = analysis.frames
    available_sources = tuple(frame.source_index for frame in frames)
    kinds = frame_kinds(available_sources, analysis.ranges)
    safety_limits = strategy_safety_limits(config)
    available_set = set(available_sources)
    raw_selected_sources: tuple[object, ...] = candidate.selected_sources
    invalid_source_type = any(
        isinstance(source, bool) or not isinstance(source, int) for source in raw_selected_sources
    )
    selected_sources = tuple(
        source for source in raw_selected_sources if isinstance(source, int) and not isinstance(source, bool)
    )
    selected_set = set(selected_sources)

    if analysis.schema_version != SCHEMA_VERSION:
        issues.append(_error("unsupported_analysis_schema", f"analysis schema_version must be {SCHEMA_VERSION}"))
    if candidate.schema_version != SCHEMA_VERSION:
        issues.append(_error("unsupported_candidate_schema", f"candidate schema_version must be {SCHEMA_VERSION}"))
    if candidate.input_digest != analysis.input_digest:
        issues.append(_error("input_digest_mismatch", "candidate input digest does not match analysis"))
    if candidate.policy_revision != POLICY_REVISION:
        issues.append(_error("policy_revision_mismatch", "candidate policy revision is not current"))
    if candidate.policy != config.policy or candidate.request.policy != config.policy:
        issues.append(_error("policy_mismatch", "candidate policy does not match resolved strategy config"))
    if (
        candidate.request.minimum_retention_ratio != config.minimum_retention_ratio
        or candidate.request.maximum_consecutive_drops != config.maximum_consecutive_drops
    ):
        issues.append(_error("request_mismatch", "candidate request does not match resolved strategy constraints"))
    if not candidate.strategy_id:
        issues.append(_error("invalid_strategy_id", "candidate strategy_id must not be empty"))
    elif candidate.strategy_id != expected_candidate.strategy_id:
        issues.append(_error("strategy_id_mismatch", "candidate strategy_id does not match the planned strategy"))
    if candidate.risk_level is not expected_candidate.risk_level:
        issues.append(_error("risk_level_mismatch", "candidate risk_level does not match the planned strategy"))
    if _candidate_diagnostics_do_not_match(candidate, expected_candidate):
        issues.append(
            _error("candidate_diagnostic_mismatch", "candidate diagnostics do not match the planned strategy")
        )
    if selected_sources != expected_candidate.selected_sources:
        issues.append(_error("selected_sources_mismatch", "candidate sources do not match the planned strategy"))

    if invalid_source_type:
        issues.append(_error("invalid_source_type", "selected_sources must contain only integers"))
    if len(selected_sources) != len(selected_set):
        issues.append(_error("duplicate_source", "selected_sources contains duplicate source indices"))
    if any(right <= left for left, right in zip(selected_sources, selected_sources[1:], strict=False)):
        issues.append(_error("unordered_sources", "selected_sources must be strictly increasing"))
    unknown_sources = sorted(selected_set - available_set)
    if unknown_sources:
        issues.append(
            _error(
                "unknown_source",
                f"selected_sources contains unknown source index {unknown_sources[0]}",
                (unknown_sources[0], unknown_sources[-1]),
            )
        )

    if frames:
        if frames[0].source_index not in selected_set:
            issues.append(
                _error(
                    "missing_first_source",
                    "the first analyzed source must be retained",
                    (frames[0].source_index, frames[0].source_index),
                )
            )
        if frames[-1].source_index not in selected_set:
            issues.append(
                _error(
                    "missing_last_source",
                    "the last analyzed source must be retained",
                    (frames[-1].source_index, frames[-1].source_index),
                )
            )

    retained_known = selected_set & available_set
    retained_positions = {position for position, frame in enumerate(frames) if frame.source_index in retained_known}
    actual_retention = len(retained_known) / len(frames) if frames else 0.0
    actual_maximum_drops = maximum_consecutive_drops(len(frames), retained_positions)
    if actual_retention < config.minimum_retention_ratio:
        issues.append(
            _error(
                "retention_below_minimum",
                f"actual retention ratio {actual_retention:.6f} is below {config.minimum_retention_ratio:.6f}",
            )
        )
    if actual_maximum_drops > config.maximum_consecutive_drops:
        issues.append(
            _error(
                "consecutive_drop_limit_exceeded",
                f"actual maximum consecutive drops {actual_maximum_drops} exceeds {config.maximum_consecutive_drops}",
            )
        )

    if safety_limits.minimum_non_static_retention_ratio is not None:
        actual_non_static_retention = non_static_retention_ratio(kinds, retained_positions)
        if actual_non_static_retention < safety_limits.minimum_non_static_retention_ratio:
            issues.append(
                _error(
                    "non_static_retention_below_minimum",
                    "non-static retention is below the coverage-first safety limit",
                )
            )
    if safety_limits.maximum_non_static_consecutive_drops is not None:
        actual_non_static_maximum_drops = maximum_non_static_consecutive_drops(kinds, retained_positions)
        if actual_non_static_maximum_drops > safety_limits.maximum_non_static_consecutive_drops:
            issues.append(
                _error(
                    "non_static_consecutive_drop_limit_exceeded",
                    "non-static consecutive drops exceed the coverage-first safety limit",
                )
            )

    _append_protected_source_issues(analysis, retained_known, kinds, safety_limits, issues)
    if _candidate_metrics_do_not_match(
        analysis,
        candidate,
        retained_known,
        actual_retention,
        actual_maximum_drops,
    ):
        issues.append(_error("candidate_metric_mismatch", "candidate summary metrics do not match selected_sources"))

    valid = not any(issue.severity is ValidationSeverity.ERROR for issue in issues)
    return ValidationResult(
        valid=valid,
        strategy_id=candidate.strategy_id,
        input_digest=analysis.input_digest,
        candidate_digest=candidate_digest,
        issues=tuple(issues),
    )


def _candidate_digest(candidate: StrategyCandidate, issues: list[ValidationIssue]) -> str:
    try:
        return sha256_digest(candidate)
    except (TypeError, ValueError):
        issues.append(_error("candidate_serialization_invalid", "candidate contains a non-canonical value"))
        return ""


def _append_protected_source_issues(
    analysis: AnalysisResult,
    retained_sources: set[int],
    kinds: tuple[str, ...],
    safety_limits: StrategySafetyLimits,
    issues: list[ValidationIssue],
) -> None:
    removed = {frame.source_index for frame in analysis.frames} - retained_sources
    low_confidence_removed = [
        frame.source_index
        for frame in analysis.frames
        if frame.source_index in removed and _is_low_confidence(frame, analysis.ranges)
    ]
    if low_confidence_removed:
        issues.append(
            _error(
                "low_confidence_source_removed",
                "low-confidence analyzed sources must be retained",
                (min(low_confidence_removed), max(low_confidence_removed)),
            )
        )

    guarded_static_removed = [
        frame.source_index
        for frame, kind in zip(analysis.frames, kinds, strict=True)
        if (
            frame.source_index in removed
            and kind == "static"
            and range_confidence(frame.source_index, analysis.ranges) < safety_limits.minimum_static_range_confidence
        )
    ]
    if guarded_static_removed:
        issues.append(
            _error(
                "insufficient_static_confidence_source_removed",
                "static sources below the policy confidence threshold must be retained",
                (min(guarded_static_removed), max(guarded_static_removed)),
            )
        )

    source_indices = tuple(frame.source_index for frame in analysis.frames)
    endpoint_positions = (
        static_range_endpoint_positions(
            source_indices,
            kinds,
            analysis.ranges,
            safety_limits.minimum_static_range_confidence,
        )
        if safety_limits.protect_static_range_endpoints
        else set()
    )
    removed_endpoints = [
        source_indices[position] for position in endpoint_positions if source_indices[position] in removed
    ]
    if removed_endpoints:
        issues.append(
            _error(
                "static_range_endpoint_removed",
                "confirmed static range endpoints must be retained",
                (min(removed_endpoints), max(removed_endpoints)),
            )
        )

    for item in analysis.ranges:
        protected_code = {
            "review_required": "review_required_source_removed",
            "active_motion": "active_motion_source_removed",
        }.get(item.kind)
        if protected_code is None:
            continue
        removed_in_range = [source for source in removed if item.start <= source <= item.end]
        if removed_in_range:
            issues.append(
                _error(
                    protected_code,
                    f"protected motion range was modified: {item.reason}",
                    (min(removed_in_range), max(removed_in_range)),
                )
            )


def _is_low_confidence(frame: FrameAnalysis, ranges: tuple[AnalysisRange, ...]) -> bool:
    frame_range_confidence = range_confidence(frame.source_index, ranges)
    return (
        frame.motion_confidence < _MINIMUM_DECISION_CONFIDENCE or frame_range_confidence < _MINIMUM_DECISION_CONFIDENCE
    )


def _candidate_metrics_do_not_match(
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    retained_sources: set[int],
    actual_retention: float,
    actual_maximum_drops: int,
) -> bool:
    retained_frames = tuple(frame for frame in analysis.frames if frame.source_index in retained_sources)
    ordered_sources = tuple(frame.source_index for frame in retained_frames)
    maximum_source_gap = max(
        (right - left for left, right in zip(ordered_sources, ordered_sources[1:], strict=False)),
        default=0,
    )
    maximum_time_gap = max(
        (
            right.timestamp_sec - left.timestamp_sec
            for left, right in zip(retained_frames, retained_frames[1:], strict=False)
        ),
        default=0.0,
    )
    return (
        candidate.estimated_output_count != len(retained_sources)
        or not math.isclose(candidate.retention_ratio, actual_retention, rel_tol=0.0, abs_tol=1e-12)
        or candidate.maximum_consecutive_drops != actual_maximum_drops
        or candidate.maximum_source_index_gap != maximum_source_gap
        or not math.isclose(candidate.maximum_time_gap_seconds, maximum_time_gap, rel_tol=0.0, abs_tol=1e-12)
    )


def _candidate_diagnostics_do_not_match(
    candidate: StrategyCandidate,
    expected: StrategyCandidate,
) -> bool:
    return (
        not math.isclose(
            candidate.estimated_jitter_reduction,
            expected.estimated_jitter_reduction,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        or not math.isclose(
            candidate.estimated_quality_change,
            expected.estimated_quality_change,
            rel_tol=0.0,
            abs_tol=1e-12,
        )
        or not math.isclose(candidate.confidence, expected.confidence, rel_tol=0.0, abs_tol=1e-12)
        or candidate.reasons != expected.reasons
    )


def _error(code: str, message: str, source_range: tuple[int, int] | None = None) -> ValidationIssue:
    return ValidationIssue(code, ValidationSeverity.ERROR, message, source_range)
