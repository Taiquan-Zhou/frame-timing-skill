from __future__ import annotations

import hashlib
import inspect
import json
import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import frame_timing_agent
import numpy as np
import pytest
from frame_timing_agent.apply_frame_strategy import compute_output_digest
from frame_timing_agent.contracts import POLICY_REVISION, AnalysisError, PolicyName, StrategyRequest
from frame_timing_agent.image_io import read_image
from frame_timing_agent.service import (
    analyze_frames,
    apply_validated_strategy,
    capabilities,
    plan_strategy,
    validate_strategy,
    verify_output,
)


def _write_frames(frame_dir: Path, count: int = 8) -> None:
    frame_dir.mkdir(parents=True)
    base = np.indices((48, 64)).sum(axis=0).astype(np.uint8)
    for index in range(count):
        image = np.dstack(
            (
                np.roll(base, index, axis=1),
                np.roll(base, index * 2, axis=0),
                np.full_like(base, 80 + index),
            )
        )
        assert cv2.imwrite(str(frame_dir / f"frame_{index:06d}_src_{index:06d}.png"), image)


def _run_until_validation(frame_dir: Path, artifact_root: Path):
    analysis = analyze_frames(frame_dir, artifact_root, fps=30.0)
    request = StrategyRequest(PolicyName.COVERAGE_FIRST)
    candidate = plan_strategy(analysis, request, artifact_root)
    validation = validate_strategy(analysis, candidate, request, artifact_root)
    return analysis, candidate, validation


def _output_bytes(output_dir: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(output_dir.iterdir()) if path.is_file()}


@pytest.mark.parametrize(
    "fps",
    [True, "30", float("nan"), float("inf"), 10**1000, 0, -1],
    ids=["bool", "string", "nan", "inf", "huge-int", "zero", "negative"],
)
def test_analyze_frames_rejects_invalid_fps_with_stable_error(tmp_path: Path, fps: object) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "invalid_fps"
    _write_frames(frame_dir)

    with pytest.raises(AnalysisError) as captured:
        analyze_frames(frame_dir, artifact_root, fps=fps)  # type: ignore[arg-type]

    assert captured.value.code == "invalid_fps"
    assert captured.value.fields == ("fps",)
    assert not (artifact_root / "analysis.json").exists()


def test_five_stage_service_writes_isolated_artifacts_and_verifies_output(tmp_path: Path) -> None:
    frame_dir = tmp_path / "clean_frames"
    artifact_root = tmp_path / "output" / "service_run"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)

    analysis = analyze_frames(frame_dir, artifact_root, fps=30.0)

    assert (artifact_root / "analysis.json").is_file()
    assert not output_dir.exists()
    request = StrategyRequest(PolicyName.COVERAGE_FIRST)
    candidate = plan_strategy(analysis, request, artifact_root)
    validation = validate_strategy(analysis, candidate, request, artifact_root)
    assert validation.valid
    execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    health = verify_output(frame_dir, analysis, candidate, execution, output_dir)

    assert (artifact_root / "strategy.json").is_file()
    assert (artifact_root / "validation.json").is_file()
    assert (artifact_root / "execution.json").is_file()
    assert json.loads((artifact_root / "analysis.json").read_text(encoding="utf-8"))["schema_version"] == 3
    assert execution.output_frame_count == len(candidate.selected_sources)
    assert health.valid
    assert health.issues == ()


def test_repeated_analysis_and_execution_are_decision_and_output_reproducible(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "repeatable"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)

    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    first = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    first_bytes = _output_bytes(output_dir)
    second = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    second_bytes = _output_bytes(output_dir)
    repeated_analysis = analyze_frames(frame_dir, artifact_root, fps=30.0)
    repeated_candidate = plan_strategy(
        repeated_analysis,
        StrategyRequest(PolicyName.COVERAGE_FIRST),
        artifact_root,
    )

    assert second.selected_sources == first.selected_sources
    assert second.output_digest == first.output_digest
    assert second_bytes == first_bytes
    assert repeated_candidate.selected_sources == candidate.selected_sources
    assert [(item.start, item.end, item.kind, item.reason) for item in repeated_analysis.ranges] == [
        (item.start, item.end, item.kind, item.reason) for item in analysis.ranges
    ]


def test_invalid_candidate_cannot_execute_or_create_output(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "invalid"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    tampered = replace(candidate, selected_sources=candidate.selected_sources[:-1])

    with pytest.raises(ValueError, match="validation"):
        apply_validated_strategy(frame_dir, analysis, tampered, validation, output_dir)

    assert not output_dir.exists()


def test_input_change_after_analysis_prevents_execution(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "changed_input"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    changed = np.full((48, 64, 3), 250, dtype=np.uint8)
    assert cv2.imwrite(str(frame_dir / "frame_000003_src_000003.png"), changed)

    with pytest.raises(ValueError, match="input digest"):
        apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)

    assert not output_dir.exists()


def test_verify_output_reports_content_tampering(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "tampered_output"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    output_image = next(path for path in output_dir.iterdir() if path.suffix == ".png")
    output_image.write_bytes(b"tampered")

    health = verify_output(frame_dir, analysis, candidate, execution, output_dir)

    assert not health.valid
    assert {issue.code for issue in health.issues} >= {"output_digest_mismatch", "source_hash_mismatch"}


def test_capabilities_and_public_signatures_exclude_legacy_overrides() -> None:
    payload = capabilities()

    assert payload["schema_version"] == 3
    assert payload["api_version"] == 3
    assert payload["policy_revision"] == POLICY_REVISION
    assert payload["policies"] == ["coverage_first", "balanced", "jitter_reduction"]
    coverage_limits = payload["safety_limits"]["coverage_first"]
    assert coverage_limits == {
        "minimum_retention_ratio": 0.75,
        "maximum_consecutive_drops": 4,
        "minimum_non_static_retention_ratio": 0.85,
        "maximum_non_static_consecutive_drops": 2,
        "minimum_static_range_confidence": 0.90,
        "protect_static_range_endpoints": True,
    }
    assert "coverage_protection_not_viewpoint_optimization" in payload["limitations"]
    assert tuple(inspect.signature(verify_output).parameters) == (
        "frame_dir",
        "analysis",
        "candidate",
        "execution",
        "output_dir",
    )
    for function in (analyze_frames, plan_strategy, validate_strategy, apply_validated_strategy, verify_output):
        parameters = inspect.signature(function).parameters
        assert "mode" not in parameters
        assert "override_config_path" not in parameters
        assert "override_config" not in parameters
        assert "motion_config" not in parameters
        assert "config" not in parameters


def test_package_root_exports_agent_safe_service_only() -> None:
    required = {
        "AnalysisResult",
        "ExecutionResult",
        "OutputVerificationResult",
        "StrategyCandidate",
        "ValidationResult",
        "analyze_frames",
        "apply_validated_strategy",
        "capabilities",
        "plan_strategy",
        "validate_strategy",
        "verify_output",
    }

    assert required <= set(frame_timing_agent.__all__)
    assert not {"MotionConfig", "ResolvedStrategyConfig", "FrameRecord"} & set(frame_timing_agent.__all__)


def test_verify_output_rejects_unregistered_image_even_if_execution_digest_is_replaced(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "extra_image"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    extra = np.full((8, 8, 3), 120, dtype=np.uint8)
    assert cv2.imwrite(str(output_dir / "review_reference.png"), extra)
    forged = replace(execution, output_digest=execution.output_digest)

    health = verify_output(frame_dir, analysis, candidate, forged, output_dir)

    assert not health.valid
    assert {issue.code for issue in health.issues} >= {"unexpected_output_entry", "output_image_count_mismatch"}


def test_analyze_frames_preserves_previous_artifact_when_atomic_replace_fails(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "atomic"
    artifact_root.mkdir(parents=True)
    analysis_path = artifact_root / "analysis.json"
    analysis_path.write_bytes(b"previous-analysis")
    _write_frames(frame_dir)

    with patch("pathlib.Path.replace", side_effect=OSError("replace failed")):
        with pytest.raises(OSError, match="replace failed"):
            analyze_frames(frame_dir, artifact_root)

    assert analysis_path.read_bytes() == b"previous-analysis"
    assert not list(artifact_root.glob(".analysis.json.*.tmp"))


def test_analysis_image_decoding_is_bounded_to_three_reads_per_frame(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "read_count"
    _write_frames(frame_dir)
    with (
        patch("frame_timing_agent.analysis.read_image", wraps=read_image) as dimension_reads,
        patch("frame_timing_agent.timing_metrics.read_image", wraps=read_image) as quality_reads,
        patch("frame_timing_agent.motion_model.read_image", wraps=read_image) as motion_reads,
    ):
        analyze_frames(frame_dir, artifact_root)

    total_reads = dimension_reads.call_count + quality_reads.call_count + motion_reads.call_count
    assert 8 <= total_reads <= 24


def test_apply_requires_dedicated_output_frames_directory(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "wrong_output"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)

    with pytest.raises(ValueError, match="output_frames"):
        apply_validated_strategy(frame_dir, analysis, candidate, validation, artifact_root / "frames")


def test_service_rejects_raw_strategy_mapping(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "typed_request"
    _write_frames(frame_dir)
    analysis = analyze_frames(frame_dir, artifact_root)

    with pytest.raises(ValueError, match="StrategyRequest"):
        plan_strategy(analysis, {"policy": "coverage_first"}, artifact_root)  # type: ignore[arg-type]


def test_verify_output_rejects_manifest_path_not_owned_by_output_directory(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "manifest_path"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    (artifact_root / "other_manifest.json").write_text(
        json.dumps({"output_count": execution.output_frame_count}),
        encoding="utf-8",
    )
    forged = replace(execution, output_manifest="other_manifest.json")

    health = verify_output(frame_dir, analysis, candidate, forged, output_dir)

    assert not health.valid
    assert "unsafe_output_manifest" in {issue.code for issue in health.issues}


def test_apply_cleans_generated_frames_when_execution_artifact_replace_fails(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "execution_atomic"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)

    with patch("pathlib.Path.replace", side_effect=OSError("execution replace failed")):
        with pytest.raises(OSError, match="execution replace failed"):
            apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)

    assert output_dir.is_dir()
    assert list(output_dir.iterdir()) == []


def test_verify_output_rejects_filename_source_identity_rewrite_with_forged_digest(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "identity_rewrite"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    selected_path = output_dir / "selected_frames.txt"
    selected_text = selected_path.read_text(encoding="utf-8")
    original_name = next(path.name for path in output_dir.iterdir() if "src_000000" in path.name)
    forged_name = original_name.replace("src_000000", "src_999999")
    (output_dir / original_name).rename(output_dir / forged_name)
    selected_path.write_text(selected_text.replace(original_name, forged_name), encoding="utf-8")
    forged_execution = replace(execution, output_digest=compute_output_digest(output_dir))

    health = verify_output(frame_dir, analysis, candidate, forged_execution, output_dir)

    assert not health.valid
    assert "output_record_identity_mismatch" in {issue.code for issue in health.issues}


def test_verify_output_rejects_content_tampering_when_manifest_and_digest_are_forged(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "forged_content"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    output_image = next(path for path in output_dir.iterdir() if path.suffix == ".png")
    selected_path = output_dir / "selected_frames.txt"
    original_bytes = output_image.read_bytes()
    forged_bytes = b"forged-output-content"
    output_image.write_bytes(forged_bytes)
    selected_path.write_text(
        selected_path.read_text(encoding="utf-8").replace(
            hashlib.sha256(original_bytes).hexdigest(),
            hashlib.sha256(forged_bytes).hexdigest(),
        ),
        encoding="utf-8",
    )
    forged_execution = replace(execution, output_digest=compute_output_digest(output_dir))

    health = verify_output(frame_dir, analysis, candidate, forged_execution, output_dir)

    assert not health.valid
    assert {issue.code for issue in health.issues} >= {"source_hash_mismatch", "source_manifest_hash_mismatch"}


def test_verify_output_rejects_output_frame_hardlinked_to_input(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "hardlink"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    expected_source = next(frame_dir.glob("*_src_000000.png"))
    alias_source = next(frame_dir.glob("*_src_000001.png"))
    alias_source.write_bytes(expected_source.read_bytes())
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    source_index = candidate.selected_sources[0]
    output_image = next(output_dir.glob(f"*_src_{source_index:06d}.png"))
    output_image.unlink()
    os.link(alias_source, output_image)
    forged_execution = replace(execution, output_digest=compute_output_digest(output_dir))

    health = verify_output(frame_dir, analysis, candidate, forged_execution, output_dir)

    assert not health.valid
    assert "output_aliases_input" in {issue.code for issue in health.issues}


def test_verify_output_rejects_input_changed_after_execution(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "input_drift"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    changed = np.full((48, 64, 3), 17, dtype=np.uint8)
    assert cv2.imwrite(str(next(frame_dir.glob("*_src_000007.png"))), changed)

    health = verify_output(frame_dir, analysis, candidate, execution, output_dir)

    assert not health.valid
    assert "input_digest_mismatch" in {issue.code for issue in health.issues}


def test_verify_output_reports_unreadable_output_directory(tmp_path: Path) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "unreadable"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    analysis, candidate, validation = _run_until_validation(frame_dir, artifact_root)
    execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, output_dir)
    original_iterdir = Path.iterdir

    def guarded_iterdir(path: Path):
        if path == output_dir:
            raise PermissionError("output directory is unreadable")
        return original_iterdir(path)

    with patch.object(Path, "iterdir", guarded_iterdir):
        health = verify_output(frame_dir, analysis, candidate, execution, output_dir)

    assert not health.valid
    assert "output_directory_unreadable" in {issue.code for issue in health.issues}
