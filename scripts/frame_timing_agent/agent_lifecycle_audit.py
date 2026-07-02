from __future__ import annotations

import re

from frame_timing_agent.configuration import resolve_strategy_request
from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AgentHealthResult,
    AnalysisRange,
    AnalysisResult,
    ExecutionResult,
    OutputVerificationResult,
    StrategyCandidate,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from frame_timing_agent.serialization import sha256_digest
from frame_timing_agent.strategy_validator import validate_strategy

_AUDIT_CODE_PATTERN = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}")
_REDACTED_AUDIT_CODE = "untrusted_text_redacted"


def audit_agent_strategy_lifecycle(
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    validation: ValidationResult,
    execution: ExecutionResult,
    output_verification: OutputVerificationResult,
) -> AgentHealthResult:
    fresh_validation = validate_strategy(
        analysis,
        candidate,
        resolve_strategy_request(candidate.request),
    )
    issues = list(fresh_validation.issues)
    expected_candidate_digest = sha256_digest(candidate)
    validation_identity_valid = True
    identity_checks = (
        (
            validation.strategy_id == candidate.strategy_id,
            "validation_strategy_id_mismatch",
            "saved validation strategy_id does not match the candidate",
        ),
        (
            validation.input_digest == analysis.input_digest,
            "validation_input_digest_mismatch",
            "saved validation input_digest does not match the analysis",
        ),
        (
            validation.candidate_digest == expected_candidate_digest,
            "validation_candidate_digest_mismatch",
            "saved validation candidate_digest does not match the candidate",
        ),
    )
    for matches, code, message in identity_checks:
        if not matches:
            validation_identity_valid = False
            issues.append(ValidationIssue(code, ValidationSeverity.ERROR, message))
    saved_validation_matches = validation == fresh_validation
    if not saved_validation_matches:
        issues.append(
            ValidationIssue(
                "saved_validation_mismatch",
                ValidationSeverity.ERROR,
                "saved validation does not match fresh strategy validation",
            )
        )
    audit_text_safe = _audit_text_is_safe(analysis, candidate)
    if not audit_text_safe:
        issues.append(
            ValidationIssue(
                "unsafe_audit_text",
                ValidationSeverity.ERROR,
                "saved analysis or candidate contains unsafe audit text",
            )
        )
    issues.extend(output_verification.issues)
    validation_valid = (
        fresh_validation.valid and saved_validation_matches and validation_identity_valid and audit_text_safe
    )
    valid = validation_valid and output_verification.valid
    review_ranges = tuple(
        AnalysisRange(
            start=item.start,
            end=item.end,
            kind="review_required",
            confidence=item.confidence,
            reason=_safe_audit_code(item.reason),
        )
        for item in analysis.ranges
        if item.kind == "review_required"
    )
    selected_sources = set(candidate.selected_sources)
    selected_jitter_scores = [frame.jitter_score for frame in analysis.frames if frame.source_index in selected_sources]
    estimated_residual_jitter = (
        sum(selected_jitter_scores) / len(selected_jitter_scores) if selected_jitter_scores else 0.0
    )
    return AgentHealthResult(
        schema_version=SCHEMA_VERSION,
        run_id=analysis.run_id,
        strategy_id=candidate.strategy_id,
        input_digest=analysis.input_digest,
        candidate_digest=expected_candidate_digest,
        output_digest=output_verification.output_digest,
        status="ok" if valid else "failed",
        valid=valid,
        validation_valid=validation_valid,
        output_valid=output_verification.valid,
        input_frame_count=analysis.frame_count,
        output_frame_count=execution.output_frame_count,
        policy=candidate.policy,
        retention_ratio=candidate.retention_ratio,
        maximum_consecutive_drops=candidate.maximum_consecutive_drops,
        maximum_source_index_gap=candidate.maximum_source_index_gap,
        maximum_time_gap_seconds=candidate.maximum_time_gap_seconds,
        estimated_jitter_reduction=candidate.estimated_jitter_reduction,
        estimated_residual_jitter=estimated_residual_jitter,
        estimated_quality_change=candidate.estimated_quality_change,
        confidence=candidate.confidence,
        risk_level=candidate.risk_level,
        reasons=tuple(_safe_audit_code(reason) for reason in candidate.reasons),
        review_ranges=review_ranges,
        issues=tuple(issues),
    )


def _audit_text_is_safe(analysis: AnalysisResult, candidate: StrategyCandidate) -> bool:
    values = [*analysis.warnings, *candidate.reasons]
    values.extend(item.kind for item in analysis.ranges)
    values.extend(item.reason for item in analysis.ranges)
    return all(_AUDIT_CODE_PATTERN.fullmatch(value) is not None for value in values)


def _safe_audit_code(value: str) -> str:
    return value if _AUDIT_CODE_PATTERN.fullmatch(value) is not None else _REDACTED_AUDIT_CODE
