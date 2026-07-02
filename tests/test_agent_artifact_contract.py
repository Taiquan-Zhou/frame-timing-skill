from __future__ import annotations

import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

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

    report = (artifact_root / "report.md").read_text(encoding="utf-8")
    human_review = (artifact_root / "human_review.md").read_text(encoding="utf-8")
    for label in (
        "Input frames:",
        "Output frames:",
        "Retention ratio:",
        "Maximum consecutive drops:",
        "Maximum source index gap:",
        "Maximum time gap:",
        "Estimated jitter reduction:",
        "Confidence:",
        "Risk level:",
        "Fallback reasons:",
        "Review ranges:",
    ):
        assert label in report
    assert "Policy:" in human_review
    assert "Risk level:" in human_review
    assert "Review ranges:" in human_review
    for path in (artifact_root / "health.json", artifact_root / "report.md", artifact_root / "human_review.md"):
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
    assert "forged_error" in {issue["code"] for issue in health["issues"]}


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
