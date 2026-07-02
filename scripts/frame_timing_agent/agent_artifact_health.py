from __future__ import annotations

from pathlib import Path

from frame_timing_agent.artifact_io import (
    read_analysis_result,
    read_execution_result,
    read_strategy_candidate,
    read_validation_result,
)
from frame_timing_agent.artifact_layout import (
    ANALYSIS_ARTIFACT,
    EXECUTION_ARTIFACT,
    HEALTH_ARTIFACT,
    HUMAN_REVIEW_ARTIFACT,
    OUTPUT_DIRECTORY,
    REPORT_ARTIFACT,
    STRATEGY_ARTIFACT,
    VALIDATION_ARTIFACT,
    validate_artifact_root,
)
from frame_timing_agent.contracts import AgentHealthResult
from frame_timing_agent.agent_lifecycle_audit import audit_agent_strategy_lifecycle
from frame_timing_agent.agent_report import write_agent_human_review, write_agent_report
from frame_timing_agent.output_verifier import verify_output
from frame_timing_agent.serialization import write_canonical_json_atomic


def run_agent_artifact_health_check(
    frame_dir: Path | str,
    artifact_root: Path | str,
) -> AgentHealthResult:
    artifact_root = validate_artifact_root(artifact_root, frame_dir)
    analysis = read_analysis_result(artifact_root / ANALYSIS_ARTIFACT)
    candidate = read_strategy_candidate(artifact_root / STRATEGY_ARTIFACT)
    validation = read_validation_result(artifact_root / VALIDATION_ARTIFACT)
    execution = read_execution_result(artifact_root / EXECUTION_ARTIFACT)
    output_verification = verify_output(
        frame_dir,
        analysis,
        candidate,
        execution,
        artifact_root / OUTPUT_DIRECTORY,
    )
    health = audit_agent_strategy_lifecycle(
        analysis,
        candidate,
        validation,
        execution,
        output_verification,
    )
    write_canonical_json_atomic(artifact_root / HEALTH_ARTIFACT, health)
    write_agent_report(artifact_root / REPORT_ARTIFACT, analysis, candidate, execution, health)
    write_agent_human_review(artifact_root / HUMAN_REVIEW_ARTIFACT, candidate, health)
    return health
