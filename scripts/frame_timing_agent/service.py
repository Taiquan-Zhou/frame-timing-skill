from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path

from frame_timing_agent.analysis import analyze_records
from frame_timing_agent.apply_frame_strategy import (
    apply_validated_strategy as _apply_validated_strategy,
)
from frame_timing_agent.apply_frame_strategy import (
    clear_generated_outputs,
)
from frame_timing_agent.configuration import resolve_strategy_request
from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AnalysisResult,
    ExecutionResult,
    PolicyName,
    StrategyCandidate,
    StrategyRequest,
    ValidationResult,
)
from frame_timing_agent.frame_source import load_frame_records
from frame_timing_agent.motion_model import MotionConfig
from frame_timing_agent.output_verifier import verify_output as verify_output
from frame_timing_agent.serialization import canonical_json_bytes
from frame_timing_agent.strategy_planner import plan_strategy as _plan_strategy
from frame_timing_agent.strategy_validator import validate_strategy as _validate_strategy

ANALYSIS_ARTIFACT = "analysis.json"
STRATEGY_ARTIFACT = "strategy.json"
VALIDATION_ARTIFACT = "validation.json"
EXECUTION_ARTIFACT = "execution.json"
OUTPUT_DIRECTORY = "output_frames"


def capabilities() -> dict[str, object]:
    policies = list(PolicyName)
    safety_limits: dict[str, object] = {}
    for policy in policies:
        config = resolve_strategy_request(StrategyRequest(policy))
        safety_limits[policy.value] = {
            "minimum_retention_ratio": config.minimum_retention_ratio,
            "maximum_consecutive_drops": config.maximum_consecutive_drops,
        }
    return {
        "api_version": SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "policies": [policy.value for policy in policies],
        "safety_limits": safety_limits,
        "stages": ["analyze", "plan", "validate", "apply", "verify"],
        "limitations": [
            "coverage_protection_not_viewpoint_optimization",
            "frame_selection_not_pixel_stabilization",
            "human_review_required_for_uncertain_motion",
        ],
    }


def analyze_frames(
    frame_dir: Path | str,
    artifact_root: Path | str,
    *,
    fps: float = 30.0,
) -> AnalysisResult:
    frame_dir = Path(frame_dir)
    artifact_root = Path(artifact_root)
    _validate_artifact_root(artifact_root, frame_dir)
    records = load_frame_records(frame_dir, fps=fps)
    analysis = analyze_records(records, fps=fps, motion_config=MotionConfig())
    _atomic_write(artifact_root / ANALYSIS_ARTIFACT, analysis)
    return analysis


def plan_strategy(
    analysis: AnalysisResult,
    request: StrategyRequest,
    artifact_root: Path | str,
) -> StrategyCandidate:
    artifact_root = Path(artifact_root)
    _validate_artifact_root(artifact_root)
    candidate = _plan_strategy(analysis, resolve_strategy_request(request))
    _atomic_write(artifact_root / STRATEGY_ARTIFACT, candidate)
    return candidate


def validate_strategy(
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    request: StrategyRequest,
    artifact_root: Path | str,
) -> ValidationResult:
    artifact_root = Path(artifact_root)
    _validate_artifact_root(artifact_root)
    validation = _validate_strategy(analysis, candidate, resolve_strategy_request(request))
    _atomic_write(artifact_root / VALIDATION_ARTIFACT, validation)
    return validation


def apply_validated_strategy(
    frame_dir: Path | str,
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    validation: ValidationResult,
    output_dir: Path | str,
) -> ExecutionResult:
    frame_dir = Path(frame_dir)
    output_dir = Path(output_dir)
    if output_dir.name != OUTPUT_DIRECTORY:
        raise ValueError(f"validated execution output directory must be named {OUTPUT_DIRECTORY}")
    _validate_artifact_root(output_dir.parent, frame_dir)
    records = load_frame_records(frame_dir, fps=analysis.fps)
    execution_analysis = analyze_records(records, fps=analysis.fps, motion_config=MotionConfig())
    if execution_analysis.input_digest != analysis.input_digest:
        raise ValueError("input digest mismatch: source frames changed after analysis")
    core_result = _apply_validated_strategy(records, execution_analysis, candidate, validation, output_dir)
    execution = replace(
        core_result,
        output_manifest=(Path(output_dir.name) / core_result.output_manifest).as_posix(),
    )
    try:
        _atomic_write(output_dir.parent / EXECUTION_ARTIFACT, execution)
    except Exception:
        clear_generated_outputs(output_dir)
        raise
    return execution


def _validate_artifact_root(artifact_root: Path, frame_dir: Path | None = None) -> None:
    resolved_root = artifact_root.resolve()
    if "output" not in {part.lower() for part in resolved_root.parts}:
        raise ValueError("artifact root must be inside an output directory")
    if frame_dir is None:
        return
    resolved_frames = frame_dir.resolve()
    if (
        resolved_root == resolved_frames
        or resolved_root.is_relative_to(resolved_frames)
        or resolved_frames.is_relative_to(resolved_root)
    ):
        raise ValueError("artifact root must not overlap the input frame directory")


def _atomic_write(path: Path, payload: object) -> None:
    encoded = canonical_json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()
