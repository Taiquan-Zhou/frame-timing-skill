from __future__ import annotations

from frame_timing_agent.contracts import RiskLevel


def requires_human_confirmation(
    *,
    valid: bool,
    risk_level: RiskLevel,
    has_review_ranges: bool,
) -> bool:
    return not valid or risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH} or has_review_ranges
