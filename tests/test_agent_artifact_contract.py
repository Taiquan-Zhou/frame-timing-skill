from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest
from frame_timing_agent.agent_lifecycle_audit import audit_agent_strategy_lifecycle
from frame_timing_agent.agent_report import write_agent_human_review
from frame_timing_agent.artifact_io import read_analysis_result, read_execution_result
from frame_timing_agent.configuration import resolve_strategy_request
from frame_timing_agent.contracts import (
    AnalysisRange,
    OutputVerificationResult,
    PolicyName,
    StrategyRequest,
)
from frame_timing_agent.strategy_planner import plan_strategy as plan_strategy_candidate
from frame_timing_agent.strategy_validator import validate_strategy as validate_strategy_candidate
from frame_timing_agent.tool_cli import main


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
        encoded, buffer = cv2.imencode(".png", image)
        assert encoded
        (frame_dir / f"frame_{index:06d}_src_{index:06d}.png").write_bytes(buffer.tobytes())


def _invoke(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, object]]:
    exit_code = main(argv)
    captured = capsys.readouterr()
    return exit_code, json.loads(captured.out)


def _nested_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(text for item in value.values() for text in _nested_strings(item))
    if isinstance(value, list):
        return tuple(text for item in value for text in _nested_strings(item))
    return ()


def _run_until_apply(
    capsys: pytest.CaptureFixture[str],
    frame_dir: Path,
    artifact_root: Path,
) -> Path:
    output_dir = artifact_root / "output_frames"
    commands = (
        ["analyze", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
        ["plan", "--analysis", str(artifact_root / "analysis.json"), "--policy", "coverage_first"],
        [
            "validate",
            "--analysis",
            str(artifact_root / "analysis.json"),
            "--strategy",
            str(artifact_root / "strategy.json"),
        ],
        [
            "apply",
            "--frames",
            str(frame_dir),
            "--analysis",
            str(artifact_root / "analysis.json"),
            "--strategy",
            str(artifact_root / "strategy.json"),
            "--validation",
            str(artifact_root / "validation.json"),
            "--output-dir",
            str(output_dir),
        ],
    )
    for command in commands:
        exit_code, payload = _invoke(capsys, command)
        assert exit_code == 0, payload
    return output_dir


def _verify(
    capsys: pytest.CaptureFixture[str],
    frame_dir: Path,
    artifact_root: Path,
) -> tuple[int, dict[str, object]]:
    return _invoke(
        capsys,
        ["verify", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
    )


def test_v3_lifecycle_writes_five_json_artifacts_and_complete_private_report(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "private_customer" / "frames"
    artifact_root = tmp_path / "output" / "agent_contract"
    _write_frames(frame_dir)
    _run_until_apply(capsys, frame_dir, artifact_root)

    exit_code, payload = _verify(capsys, frame_dir, artifact_root)

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["artifacts"]["health"] == "health.json"
    for name in ("analysis.json", "strategy.json", "validation.json", "execution.json", "health.json"):
        assert (artifact_root / name).is_file()
    health = json.loads((artifact_root / "health.json").read_text(encoding="utf-8"))
    assert set(health) == {
        "candidate_digest",
        "confidence",
        "estimated_jitter_reduction",
        "estimated_residual_jitter",
        "estimated_quality_change",
        "input_digest",
        "input_frame_count",
        "issues",
        "maximum_consecutive_drops",
        "maximum_source_index_gap",
        "maximum_time_gap_seconds",
        "output_digest",
        "output_frame_count",
        "output_valid",
        "policy",
        "reasons",
        "retention_ratio",
        "review_ranges",
        "risk_level",
        "run_id",
        "schema_version",
        "status",
        "strategy_id",
        "valid",
        "validation_valid",
    }
    assert health["schema_version"] == 3
    assert health["valid"] is True
    assert health["validation_valid"] is True
    assert health["output_valid"] is True
    assert health["input_frame_count"] == 8
    assert health["output_frame_count"] > 0
    analysis = json.loads((artifact_root / "analysis.json").read_text(encoding="utf-8"))
    strategy = json.loads((artifact_root / "strategy.json").read_text(encoding="utf-8"))
    selected_sources = set(strategy["selected_sources"])
    expected_residual_jitter = sum(
        frame["jitter_score"] for frame in analysis["frames"] if frame["source_index"] in selected_sources
    ) / len(selected_sources)
    assert health["estimated_residual_jitter"] == pytest.approx(expected_residual_jitter)

    report = (artifact_root / "report.md").read_text(encoding="utf-8")
    human_review = (artifact_root / "human_review.md").read_text(encoding="utf-8")
    for expected_line in (
        f"Input frames: {health['input_frame_count']}",
        f"Output frames: {health['output_frame_count']}",
        f"Retention ratio: {health['retention_ratio']:.6f}",
        f"Maximum consecutive drops: {health['maximum_consecutive_drops']}",
        f"Maximum source index gap: {health['maximum_source_index_gap']}",
        f"Maximum time gap: {health['maximum_time_gap_seconds']:.6f} seconds",
        f"Estimated jitter reduction: {health['estimated_jitter_reduction']:.6f}",
        f"Estimated residual jitter: {health['estimated_residual_jitter']:.6f}",
        f"Confidence: {health['confidence']:.6f}",
        f"Risk level: {health['risk_level']}",
        "## Decision reasons:",
        "## Review ranges:",
    ):
        assert expected_line in report
    assert "Policy:" in human_review
    assert "Risk level:" in human_review
    assert "Review ranges:" in human_review
    assert str(tmp_path) not in _nested_strings(health)
    for path in (artifact_root / "report.md", artifact_root / "human_review.md"):
        assert str(tmp_path) not in path.read_text(encoding="utf-8")


def test_verify_rejects_saved_validation_identity_mismatch(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "validation_mismatch"
    _write_frames(frame_dir)
    _run_until_apply(capsys, frame_dir, artifact_root)
    validation_path = artifact_root / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["candidate_digest"] = "sha256:" + "0" * 64
    validation_path.write_text(json.dumps(validation), encoding="utf-8")

    exit_code, payload = _verify(capsys, frame_dir, artifact_root)

    assert exit_code == 5
    assert payload["status"] == "failed"
    health = json.loads((artifact_root / "health.json").read_text(encoding="utf-8"))
    assert health["valid"] is False
    assert health["validation_valid"] is False
    assert "validation_candidate_digest_mismatch" in {issue["code"] for issue in health["issues"]}


def test_verify_rejects_saved_validation_with_contradictory_error_issue(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "validation_issue"
    _write_frames(frame_dir)
    _run_until_apply(capsys, frame_dir, artifact_root)
    validation_path = artifact_root / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["issues"] = [
        {
            "code": "forged_error",
            "severity": "error",
            "message": "contradictory saved validation",
            "source_range": None,
        }
    ]
    validation_path.write_text(json.dumps(validation), encoding="utf-8")

    exit_code, _ = _verify(capsys, frame_dir, artifact_root)

    assert exit_code == 5
    health = json.loads((artifact_root / "health.json").read_text(encoding="utf-8"))
    assert health["validation_valid"] is False
    assert "saved_validation_mismatch" in {issue["code"] for issue in health["issues"]}
    assert "forged_error" not in {issue["code"] for issue in health["issues"]}


def test_verify_rejects_saved_validation_warning_without_leaking_its_message(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "private_customer" / "frames"
    artifact_root = tmp_path / "output" / "validation_warning"
    _write_frames(frame_dir)
    _run_until_apply(capsys, frame_dir, artifact_root)
    validation_path = artifact_root / "validation.json"
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["issues"] = [
        {
            "code": "forged_warning",
            "severity": "warning",
            "message": str(frame_dir),
            "source_range": None,
        }
    ]
    validation_path.write_text(json.dumps(validation), encoding="utf-8")

    exit_code, payload = _verify(capsys, frame_dir, artifact_root)

    assert exit_code == 5
    assert payload["status"] == "failed"
    health = json.loads((artifact_root / "health.json").read_text(encoding="utf-8"))
    assert "saved_validation_mismatch" in {issue["code"] for issue in health["issues"]}
    assert str(frame_dir) not in _nested_strings(health)
    assert str(frame_dir) not in (artifact_root / "report.md").read_text(encoding="utf-8")
    assert str(frame_dir) not in (artifact_root / "human_review.md").read_text(encoding="utf-8")


def test_verify_rejects_unsafe_analysis_warning_without_leaking_it(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "private_customer" / "frames"
    artifact_root = tmp_path / "output" / "analysis_warning"
    _write_frames(frame_dir)
    _run_until_apply(capsys, frame_dir, artifact_root)
    analysis_path = artifact_root / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["warnings"] = [str(frame_dir)]
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    exit_code, payload = _verify(capsys, frame_dir, artifact_root)

    assert exit_code == 5
    assert payload["status"] == "failed"
    health = json.loads((artifact_root / "health.json").read_text(encoding="utf-8"))
    assert "unsafe_audit_text" in {issue["code"] for issue in health["issues"]}
    assert str(frame_dir) not in _nested_strings(health)
    assert str(frame_dir) not in (artifact_root / "report.md").read_text(encoding="utf-8")
    assert str(frame_dir) not in (artifact_root / "human_review.md").read_text(encoding="utf-8")


def test_verify_writes_failed_health_when_output_content_is_tampered(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "tampered_output"
    _write_frames(frame_dir)
    output_dir = _run_until_apply(capsys, frame_dir, artifact_root)
    next(output_dir.glob("*.png")).write_bytes(b"tampered")

    exit_code, payload = _verify(capsys, frame_dir, artifact_root)

    assert exit_code == 5
    assert payload["status"] == "failed"
    health = json.loads((artifact_root / "health.json").read_text(encoding="utf-8"))
    assert health["valid"] is False
    assert health["output_valid"] is False
    assert {issue["code"] for issue in health["issues"]} & {
        "output_digest_mismatch",
        "source_hash_mismatch",
    }


def test_verify_rejects_artifact_root_outside_output_tree(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    original_root = tmp_path / "output" / "movable"
    artifact_root = tmp_path / "artifacts"
    _write_frames(frame_dir)
    _run_until_apply(capsys, frame_dir, original_root)
    shutil.move(original_root, artifact_root)

    exit_code, payload = _verify(capsys, frame_dir, artifact_root)

    assert exit_code == 2
    assert payload["error"]["code"] == "input_error"
    assert not (artifact_root / "health.json").exists()


def test_verify_invalidates_previous_health_before_reading_required_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "stale_health"
    _write_frames(frame_dir)
    _run_until_apply(capsys, frame_dir, artifact_root)
    exit_code, _ = _verify(capsys, frame_dir, artifact_root)
    assert exit_code == 0
    assert json.loads((artifact_root / "health.json").read_text(encoding="utf-8"))["valid"] is True
    (artifact_root / "validation.json").write_text("{", encoding="utf-8")

    exit_code, payload = _verify(capsys, frame_dir, artifact_root)

    assert exit_code == 2
    assert payload["error"]["code"] == "invalid_artifact"
    for name in ("health.json", "report.md", "human_review.md"):
        assert not (artifact_root / name).exists()


def test_verify_report_publication_failure_returns_health_failure_without_partial_artifacts(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "publication_failure"
    _write_frames(frame_dir)
    _run_until_apply(capsys, frame_dir, artifact_root)

    with patch(
        "frame_timing_agent.agent_artifact_health.write_agent_report",
        side_effect=OSError("simulated report write failure"),
    ):
        exit_code, payload = _verify(capsys, frame_dir, artifact_root)

    assert exit_code == 5
    assert payload["error"]["code"] == "health_failed"
    for name in ("health.json", "report.md", "human_review.md"):
        assert not (artifact_root / name).exists()


def test_low_risk_non_review_ranges_do_not_require_human_confirmation(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "ordinary_ranges"
    _write_frames(frame_dir)
    _run_until_apply(capsys, frame_dir, artifact_root)
    analysis = replace(
        read_analysis_result(artifact_root / "analysis.json"),
        ranges=(AnalysisRange(0, 7, "static", 1.0, "stable_motion"),),
        warnings=(),
    )
    config = resolve_strategy_request(StrategyRequest(PolicyName.COVERAGE_FIRST))
    candidate = plan_strategy_candidate(analysis, config)
    validation = validate_strategy_candidate(analysis, candidate, config)
    execution = read_execution_result(artifact_root / "execution.json")
    health = audit_agent_strategy_lifecycle(
        analysis,
        candidate,
        validation,
        execution,
        OutputVerificationResult(True, execution.output_digest, ()),
    )
    review_path = artifact_root / "low_risk_review.md"
    write_agent_human_review(review_path, candidate, health)

    assert health.valid
    assert health.review_ranges == ()
    assert "Decision: ready" in review_path.read_text(encoding="utf-8")
