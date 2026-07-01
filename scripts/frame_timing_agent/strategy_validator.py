from __future__ import annotations

import math

from frame_timing_agent.configuration import ResolvedStrategyConfig
from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AnalysisRange,
    AnalysisResult,
    FrameAnalysis,
    StrategyCandidate,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from frame_timing_agent.serialization import sha256_digest

_MINIMUM_DECISION_CONFIDENCE = 0.5


def validate_strategy(
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    config: ResolvedStrategyConfig,
) -> ValidationResult:
    issues: list[ValidationIssue] = []
    candidate_digest = _candidate_digest(candidate, issues)
    frames = analysis.frames
    available_sources = tuple(frame.source_index for frame in frames)
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
    if candidate.policy != config.policy or candidate.request.policy != config.policy:
        issues.append(_error("policy_mismatch", "candidate policy does not match resolved strategy config"))
    if (
        candidate.request.minimum_retention_ratio != config.minimum_retention_ratio
        or candidate.request.maximum_consecutive_drops != config.maximum_consecutive_drops
    ):
        issues.append(_error("request_mismatch", "candidate request does not match resolved strategy constraints"))
    if not candidate.strategy_id:
        issues.append(_error("invalid_strategy_id", "candidate strategy_id must not be empty"))

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
    actual_retention = len(retained_known) / len(frames) if frames else 0.0
    actual_maximum_drops = _maximum_consecutive_drops(available_sources, retained_known)
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

    _append_protected_source_issues(analysis, retained_known, issues)
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
    range_confidences = [item.confidence for item in ranges if item.start <= frame.source_index <= item.end]
    range_confidence = min(range_confidences, default=0.0)
    return frame.motion_confidence < _MINIMUM_DECISION_CONFIDENCE or range_confidence < _MINIMUM_DECISION_CONFIDENCE


def _maximum_consecutive_drops(available_sources: tuple[int, ...], retained_sources: set[int]) -> int:
    maximum = current = 0
    for source in available_sources:
        if source in retained_sources:
            current = 0
        else:
            current += 1
            maximum = max(maximum, current)
    return maximum


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


def _error(code: str, message: str, source_range: tuple[int, int] | None = None) -> ValidationIssue:
    return ValidationIssue(code, ValidationSeverity.ERROR, message, source_range)
