"""Public contracts for the frame timing optimization package."""

from frame_timing_agent.agent_artifact_health import run_agent_artifact_health_check
from frame_timing_agent.contracts import (
    AgentHealthResult,
    AnalysisResult,
    ConfigurationError,
    ExecutionResult,
    OutputVerificationResult,
    PolicyName,
    RiskLevel,
    StrategyCandidate,
    StrategyRequest,
    ValidationResult,
    ValidationSeverity,
)
from frame_timing_agent.service import (
    analyze_frames,
    apply_validated_strategy,
    capabilities,
    plan_strategy,
    validate_strategy,
    verify_output,
)

__all__ = [
    "AgentHealthResult",
    "AnalysisResult",
    "ConfigurationError",
    "ExecutionResult",
    "OutputVerificationResult",
    "PolicyName",
    "RiskLevel",
    "StrategyCandidate",
    "StrategyRequest",
    "ValidationResult",
    "ValidationSeverity",
    "analyze_frames",
    "apply_validated_strategy",
    "capabilities",
    "plan_strategy",
    "run_agent_artifact_health_check",
    "validate_strategy",
    "verify_output",
]
