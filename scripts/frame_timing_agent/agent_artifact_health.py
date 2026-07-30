from __future__ import annotations

import tempfile
from pathlib import Path

from frame_timing_agent.agent_lifecycle_audit import audit_agent_strategy_lifecycle
from frame_timing_agent.agent_report import write_agent_human_review, write_agent_report
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
from frame_timing_agent.contracts import AgentHealthResult, AnalysisResult, ExecutionResult, StrategyCandidate
from frame_timing_agent.output_verifier import verify_output
from frame_timing_agent.serialization import write_canonical_json_atomic

_DERIVED_ARTIFACTS = (HEALTH_ARTIFACT, REPORT_ARTIFACT, HUMAN_REVIEW_ARTIFACT)


class HealthPublicationError(RuntimeError):
    pass


def run_agent_artifact_health_check(
    frame_dir: Path | str,
    artifact_root: Path | str,
) -> AgentHealthResult:
    artifact_root = validate_artifact_root(artifact_root, frame_dir)
    _invalidate_derived_artifacts(artifact_root)
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
    _publish_health_artifacts(artifact_root, analysis, candidate, execution, health)
    return health


def _publish_health_artifacts(
    artifact_root: Path,
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    execution: ExecutionResult,
    health: AgentHealthResult,
) -> None:
    try:
        with tempfile.TemporaryDirectory(dir=artifact_root, prefix=".health-publish-") as raw_temporary_root:
            temporary_root = Path(raw_temporary_root)
            write_canonical_json_atomic(temporary_root / HEALTH_ARTIFACT, health)
            write_agent_report(temporary_root / REPORT_ARTIFACT, analysis, candidate, execution, health)
            write_agent_human_review(temporary_root / HUMAN_REVIEW_ARTIFACT, candidate, health)
            (temporary_root / REPORT_ARTIFACT).replace(artifact_root / REPORT_ARTIFACT)
            (temporary_root / HUMAN_REVIEW_ARTIFACT).replace(artifact_root / HUMAN_REVIEW_ARTIFACT)
            (temporary_root / HEALTH_ARTIFACT).replace(artifact_root / HEALTH_ARTIFACT)
    except OSError as exc:
        _invalidate_derived_artifacts(artifact_root)
        raise HealthPublicationError("health artifacts could not be published") from exc


def _invalidate_derived_artifacts(artifact_root: Path) -> None:
    try:
        for name in _DERIVED_ARTIFACTS:
            (artifact_root / name).unlink(missing_ok=True)
    except OSError as exc:
        raise HealthPublicationError("previous health artifacts could not be invalidated") from exc
