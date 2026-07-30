from __future__ import annotations

from pathlib import Path

from frame_timing_agent.contracts import (
    AgentHealthResult,
    AnalysisResult,
    ExecutionResult,
    StrategyCandidate,
)
from frame_timing_agent.review_policy import requires_human_confirmation


def write_agent_report(
    path: Path | str,
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    execution: ExecutionResult,
    health: AgentHealthResult,
) -> Path:
    path = Path(path)
    lines = [
        "# Frame Timing Agent Report",
        "",
        f"Status: {health.status}",
        f"Policy: {candidate.policy.value}",
        f"Input frames: {analysis.frame_count}",
        f"Output frames: {execution.output_frame_count}",
        f"Retention ratio: {candidate.retention_ratio:.6f}",
        f"Maximum consecutive drops: {candidate.maximum_consecutive_drops}",
        f"Maximum source index gap: {candidate.maximum_source_index_gap}",
        f"Maximum time gap: {candidate.maximum_time_gap_seconds:.6f} seconds",
        f"Estimated jitter reduction: {candidate.estimated_jitter_reduction:.6f}",
        f"Estimated residual jitter: {health.estimated_residual_jitter:.6f}",
        f"Estimated quality change: {candidate.estimated_quality_change:.6f}",
        f"Confidence: {candidate.confidence:.6f}",
        f"Risk level: {candidate.risk_level.value}",
        "",
        "## Decision reasons:",
    ]
    lines.extend([f"- {reason}" for reason in health.reasons] if health.reasons else ["- none"])
    lines.extend(["", "## Review ranges:"])
    if health.review_ranges:
        lines.extend(
            f"- {item.kind}: source {item.start}-{item.end}, confidence={item.confidence:.6f}, reason={item.reason}"
            for item in health.review_ranges
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Health issues:"])
    lines.extend(
        [f"- {issue.severity.value}: {issue.code}: {issue.message}" for issue in health.issues]
        if health.issues
        else ["- none"]
    )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def write_agent_human_review(
    path: Path | str,
    candidate: StrategyCandidate,
    health: AgentHealthResult,
) -> Path:
    path = Path(path)
    requires_confirmation = requires_human_confirmation(
        valid=health.valid,
        risk_level=candidate.risk_level,
        has_review_ranges=bool(health.review_ranges),
    )
    lines = [
        "# Frame Timing Human Review",
        "",
        f"Decision: {'human confirmation required' if requires_confirmation else 'ready'}",
        f"Policy: {candidate.policy.value}",
        f"Risk level: {candidate.risk_level.value}",
        f"Validation: {'passed' if health.validation_valid else 'failed'}",
        f"Output verification: {'passed' if health.output_valid else 'failed'}",
        f"Input frames: {health.input_frame_count}",
        f"Output frames: {health.output_frame_count}",
        f"Retention ratio: {health.retention_ratio:.6f}",
        "",
        "## Review ranges:",
    ]
    if health.review_ranges:
        lines.extend(
            f"- {item.kind}: source {item.start}-{item.end}, reason={item.reason}" for item in health.review_ranges
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Required action:"])
    lines.append(
        "- Confirm the candidate before downstream reconstruction."
        if requires_confirmation
        else "- No additional confirmation is required by the current policy."
    )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
