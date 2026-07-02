from __future__ import annotations

from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AgentHealthResult,
    AnalysisResult,
    ExecutionResult,
    OutputVerificationResult,
    StrategyCandidate,
    ValidationIssue,
    ValidationResult,
    ValidationSeverity,
)
from frame_timing_agent.serialization import sha256_digest


def audit_agent_strategy_lifecycle(
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    validation: ValidationResult,
    execution: ExecutionResult,
    output_verification: OutputVerificationResult,
) -> AgentHealthResult:
    issues = list(validation.issues)
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
    if not validation.valid:
        issues.append(
            ValidationIssue(
                "saved_validation_failed",
                ValidationSeverity.ERROR,
                "saved validation did not approve the candidate",
            )
        )
    saved_validation_has_errors = any(issue.severity is ValidationSeverity.ERROR for issue in validation.issues)
    issues.extend(output_verification.issues)
    validation_valid = validation.valid and validation_identity_valid and not saved_validation_has_errors
    valid = validation_valid and output_verification.valid
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
        estimated_quality_change=candidate.estimated_quality_change,
        confidence=candidate.confidence,
        risk_level=candidate.risk_level,
        reasons=candidate.reasons,
        review_ranges=analysis.ranges,
        issues=tuple(issues),
    )
