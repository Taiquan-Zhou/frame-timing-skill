from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest
from frame_timing_agent import tool_cli
from frame_timing_agent import batch_session
from frame_timing_agent.batch_session import BatchItemStatus, BatchStatus, load_batch, save_batch
from frame_timing_agent.run_workflow import StaleSourceError
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


def _invoke(capsys: pytest.CaptureFixture[str], argv: list[str]) -> tuple[int, dict[str, object], str, str]:
    exit_code = main(argv)
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.out.strip().startswith("{")
    assert captured.out.strip().endswith("}")
    assert payload["schema_version"] == 3
    assert "status" in payload
    assert "run_id" in payload
    assert "artifacts" in payload
    return exit_code, payload, captured.out, captured.err


def _prepare_validated(
    capsys: pytest.CaptureFixture[str],
    frame_dir: Path,
    artifact_root: Path,
) -> None:
    for argv in (
        ["analyze", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
        ["plan", "--analysis", str(artifact_root / "analysis.json"), "--policy", "coverage_first"],
        [
            "validate",
            "--analysis",
            str(artifact_root / "analysis.json"),
            "--strategy",
            str(artifact_root / "strategy.json"),
        ],
    ):
        exit_code, payload, _, _ = _invoke(capsys, argv)
        assert exit_code == 0, payload


def test_six_subcommands_complete_agent_safe_lifecycle_with_single_json_stdout(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "测试帧"
    artifact_root = tmp_path / "output" / "cli_run"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)

    exit_code, payload, stdout, _ = _invoke(capsys, ["capabilities"])
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["artifacts"] == {}
    assert payload["result"]["policies"] == ["coverage_first", "balanced", "jitter_reduction"]
    assert stdout.count("{") >= 1

    exit_code, payload, stdout, stderr = _invoke(
        capsys,
        ["analyze", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
    )
    assert exit_code == 0
    assert payload["input_name"] == "测试帧"
    assert "测试帧" in stdout
    assert "analyze" in stderr
    assert payload["artifacts"] == {"analysis": "analysis.json"}
    assert str(tmp_path) not in stdout

    exit_code, payload, _, _ = _invoke(
        capsys,
        ["plan", "--analysis", str(artifact_root / "analysis.json"), "--policy", "coverage_first"],
    )
    assert exit_code == 0
    assert payload["artifacts"]["strategy"] == "strategy.json"

    exit_code, payload, _, _ = _invoke(
        capsys,
        [
            "validate",
            "--analysis",
            str(artifact_root / "analysis.json"),
            "--strategy",
            str(artifact_root / "strategy.json"),
        ],
    )
    assert exit_code == 0
    assert payload["result"]["valid"] is True

    exit_code, payload, _, _ = _invoke(
        capsys,
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
    assert exit_code == 0
    assert payload["artifacts"]["execution"] == "execution.json"
    assert payload["artifacts"]["output_frames"] == "output_frames"

    exit_code, payload, _, _ = _invoke(
        capsys,
        ["verify", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
    )
    assert exit_code == 0
    assert payload["result"]["valid"] is True


def test_batch_create_accepts_explicit_frames_and_reports_safe_ready_status(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "private" / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)

    exit_code, payload, stdout, stderr = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )

    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["artifacts"] == {
        "batch_state": "analysis/batch_state.json",
        "batch_summary": "analysis/batch_summary.json",
        "human_review": "analysis/human_review.md",
    }
    assert payload["result"]["batch"]["status"] == "ready"
    assert payload["result"]["progress"] == {"completed": 0, "total": 1}
    assert payload["result"]["next_actions"] == ["run"]
    assert payload["result"]["items"] == [
        {
            "name": "frames",
            "status": "pending",
            "progress": 0,
            "retry_count": 0,
            "risks": [],
            "approved": False,
            "exported": False,
            "analyzed_count": None,
            "output_count": None,
            "error": None,
        }
    ]
    assert str(tmp_path) not in stdout
    assert stderr == ""


def test_batch_create_accepts_discovery_root(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_dir = tmp_path / "source" / "clean_frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)

    exit_code, payload, stdout, stderr = _invoke(
        capsys,
        ["batch", "create", "--root", str(tmp_path / "source"), "--state", str(state_path)],
    )

    assert exit_code == 0
    assert payload["result"]["batch"]["status"] == "ready"
    assert payload["result"]["items"][0]["name"] == "clean_frames"
    assert str(tmp_path) not in stdout
    assert stderr == ""


def test_batch_status_exposes_explicit_retry_action_for_failed_items(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0
    state = load_batch(state_path)
    state.status = BatchStatus.FINISHED
    state.items[0].status = BatchItemStatus.FAILED
    state.items[0].progress = 1.0
    state.items[0].last_error = "analysis failed"
    save_batch(state)
    (state_path.parent / "maintenance_report.json").write_text('{"status": "ok"}', encoding="utf-8")

    exit_code, payload, stdout, stderr = _invoke(capsys, ["batch", "status", "--state", str(state_path)])

    assert exit_code == 0
    assert payload["result"]["next_actions"] == ["run"]
    assert payload["result"]["retry_items"] == ["frames"]
    assert payload["result"]["items"][0]["retry_count"] == 0
    assert str(tmp_path) not in stdout
    assert stderr == ""


def test_batch_run_retries_only_explicit_failed_item_and_never_exports_implicitly(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0
    state = load_batch(state_path)
    state.status = BatchStatus.FINISHED
    state.items[0].status = BatchItemStatus.FAILED
    state.items[0].progress = 1.0
    state.items[0].last_error = "analysis failed"
    save_batch(state)

    exit_code, payload, stdout, stderr = _invoke(
        capsys,
        ["batch", "run", "--state", str(state_path), "--retry-item", "frames"],
    )

    assert exit_code == 0
    assert payload["result"]["batch"]["status"] == "finished"
    assert payload["result"]["items"][0]["status"] in {"completed", "review_required"}
    assert payload["result"]["items"][0]["retry_count"] == 1
    assert payload["result"]["next_actions"] in (["export"], ["approve"])
    assert not (state_path.parents[1] / "frames" / "output_frames").exists()
    assert str(tmp_path) not in stdout
    assert stderr == ""


def test_batch_approval_and_export_require_explicit_actions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0
    run_code, _, _, _ = _invoke(capsys, ["batch", "run", "--state", str(state_path)])
    assert run_code == 0
    state = load_batch(state_path)
    state.items[0].status = BatchItemStatus.REVIEW_REQUIRED
    state.items[0].approved = False
    save_batch(state)

    status_code, status_payload, _, status_stderr = _invoke(capsys, ["batch", "status", "--state", str(state_path)])
    assert status_code == 0
    assert status_payload["result"]["next_actions"] == ["approve"]
    assert status_payload["result"]["items"][0]["approved"] is False
    assert status_stderr == ""

    approve_code, approve_payload, approve_stdout, approve_stderr = _invoke(
        capsys,
        ["batch", "approve", "--state", str(state_path), "--item", "frames", "--note", "checked"],
    )
    assert approve_code == 0
    assert approve_payload["result"]["items"][0]["approved"] is True
    assert approve_payload["result"]["next_actions"] == ["export"]
    assert str(tmp_path) not in approve_stdout
    assert approve_stderr == ""
    assert not (state_path.parents[1] / "frames" / "output_frames").exists()

    export_code, export_payload, export_stdout, export_stderr = _invoke(
        capsys,
        ["batch", "export", "--state", str(state_path)],
    )
    assert export_code == 0
    assert export_payload["result"]["export"] == {"exported": ["frames"], "skipped": [], "failed": []}
    assert export_payload["result"]["next_actions"] == []
    assert (state_path.parents[1] / "frames" / "output_frames").is_dir()
    assert str(tmp_path) not in export_stdout
    assert export_stderr == ""


def test_batch_export_reports_stale_source_without_suggesting_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0
    run_code, _, _, _ = _invoke(capsys, ["batch", "run", "--state", str(state_path)])
    assert run_code == 0
    state = load_batch(state_path)
    state.items[0].status = BatchItemStatus.COMPLETED
    save_batch(state)
    (frame_dir / "frame_000000_src_000000.png").write_bytes(b"changed after analysis")

    exit_code, payload, stdout, stderr = _invoke(capsys, ["batch", "export", "--state", str(state_path)])

    assert exit_code == 4
    assert payload["error"] == {"code": "stale_source", "message": "batch source changed since analysis"}
    assert "export" not in payload.get("result", {}).get("next_actions", [])
    assert not (state_path.parents[1] / "frames" / "output_frames").exists()
    assert str(tmp_path) not in stdout
    assert str(tmp_path) not in stderr


def test_batch_export_reports_stale_source_raised_during_actual_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0
    run_code, _, _, _ = _invoke(capsys, ["batch", "run", "--state", str(state_path)])
    assert run_code == 0
    state = load_batch(state_path)
    state.items[0].status = BatchItemStatus.COMPLETED
    save_batch(state)
    exported = []

    def fail_after_export_starts(*args, **kwargs):
        exported.append(args[0])
        raise StaleSourceError("input changed after export started")

    monkeypatch.setattr(batch_session, "export_run", fail_after_export_starts)

    exit_code, payload, stdout, stderr = _invoke(capsys, ["batch", "export", "--state", str(state_path)])

    assert exported
    assert exit_code == 4
    assert payload["error"] == {"code": "stale_source", "message": "batch source changed since analysis"}
    assert "export" not in payload.get("result", {}).get("next_actions", [])
    assert str(tmp_path) not in stdout
    assert str(tmp_path) not in stderr


def test_batch_export_reports_ordinary_export_failure_as_unsafe_export(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0
    run_code, _, _, _ = _invoke(capsys, ["batch", "run", "--state", str(state_path)])
    assert run_code == 0
    state = load_batch(state_path)
    state.items[0].status = BatchItemStatus.COMPLETED
    save_batch(state)
    monkeypatch.setattr(
        batch_session,
        "export_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("synthetic export failure")),
    )

    exit_code, payload, stdout, stderr = _invoke(capsys, ["batch", "export", "--state", str(state_path)])

    assert exit_code == 4
    assert payload["error"] == {"code": "unsafe_export", "message": "one or more batch items could not be exported"}
    assert str(tmp_path) not in stdout
    assert stderr == ""


def test_batch_approval_reports_stale_source_without_paths(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0
    run_code, _, _, _ = _invoke(capsys, ["batch", "run", "--state", str(state_path)])
    assert run_code == 0
    state = load_batch(state_path)
    state.items[0].status = BatchItemStatus.REVIEW_REQUIRED
    state.items[0].approved = False
    save_batch(state)
    (frame_dir / "frame_000000_src_000000.png").write_bytes(b"changed after analysis")

    exit_code, payload, stdout, stderr = _invoke(
        capsys,
        ["batch", "approve", "--state", str(state_path), "--item", "frames"],
    )

    assert exit_code == 4
    assert payload["error"] == {"code": "stale_source", "message": "batch source changed since analysis"}
    assert str(tmp_path) not in stdout
    assert str(tmp_path) not in stderr


@pytest.mark.parametrize("health_payload", [None, "{", '{"status": "failed"}'])
def test_finished_batch_requires_healthy_maintenance_report(
    health_payload: str | None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0
    state = load_batch(state_path)
    state.status = BatchStatus.FINISHED
    state.items[0].status = BatchItemStatus.COMPLETED
    state.items[0].progress = 1.0
    save_batch(state)
    maintenance_path = state_path.parent / "maintenance_report.json"
    if health_payload is not None:
        maintenance_path.write_text(health_payload, encoding="utf-8")

    exit_code, payload, stdout, stderr = _invoke(capsys, ["batch", "status", "--state", str(state_path)])

    assert exit_code == 5
    assert payload["error"] == {"code": "artifact_health_failed", "message": "batch artifact health check failed"}
    assert str(tmp_path) not in stdout
    assert str(tmp_path) not in stderr


def test_ready_batch_allows_missing_maintenance_report(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0

    exit_code, payload, _, stderr = _invoke(capsys, ["batch", "status", "--state", str(state_path)])

    assert exit_code == 0
    assert payload["result"]["batch"]["status"] == "ready"
    assert stderr == ""


def test_batch_run_does_not_retry_failed_item_without_retry_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0
    state = load_batch(state_path)
    state.status = BatchStatus.FINISHED
    state.items[0].status = BatchItemStatus.FAILED
    state.items[0].progress = 1.0
    state.items[0].last_error = "analysis failed"
    save_batch(state)

    exit_code, payload, _, stderr = _invoke(capsys, ["batch", "run", "--state", str(state_path)])

    assert exit_code == 4
    assert payload["error"]["code"] == "analysis_failed"
    assert payload["result"]["items"][0]["status"] == "failed"
    assert payload["result"]["items"][0]["retry_count"] == 0
    assert stderr == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["batch"],
        ["batch", "status"],
        ["batch", "create", "--state", "output/batch/analysis/batch_state.json"],
        ["batch", "status", "--state", "not-a-batch-state.json"],
    ],
)
def test_batch_argument_errors_use_invalid_input_code(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, payload, _, stderr = _invoke(capsys, argv)

    assert exit_code == 2
    assert payload["error"] == {"code": "invalid_input", "message": "batch command input is invalid"}
    assert stderr


def test_batch_errors_are_stable_and_do_not_leak_state_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_state = tmp_path / "private" / "analysis" / "batch_state.json"

    exit_code, payload, stdout, stderr = _invoke(capsys, ["batch", "status", "--state", str(missing_state)])

    assert exit_code == 2
    assert payload["status"] == "error"
    assert payload["error"] == {"code": "invalid_input", "message": "batch command input is invalid"}
    assert str(tmp_path) not in stdout
    assert str(tmp_path) not in stderr

    invalid_state = tmp_path / "output" / "invalid" / "analysis" / "batch_state.json"
    exit_code, payload, stdout, stderr = _invoke(
        capsys,
        ["batch", "create", "--root", str(tmp_path / "missing"), "--state", str(invalid_state)],
    )

    assert exit_code == 2
    assert payload["error"] == {"code": "invalid_input", "message": "batch command input is invalid"}
    assert str(tmp_path) not in stdout
    assert str(tmp_path) not in stderr


def test_batch_busy_and_invalid_approval_errors_are_stable(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_dir = tmp_path / "frames"
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    _write_frames(frame_dir)
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    assert create_code == 0

    with batch_session._run_lock(state_path):
        busy_code, busy_payload, busy_stdout, busy_stderr = _invoke(
            capsys, ["batch", "run", "--state", str(state_path)]
        )

    assert busy_code == 4
    assert busy_payload["error"] == {"code": "busy_batch", "message": "batch is busy"}
    assert str(tmp_path) not in busy_stdout
    assert str(tmp_path) not in busy_stderr

    approval_code, approval_payload, approval_stdout, approval_stderr = _invoke(
        capsys,
        ["batch", "approve", "--state", str(state_path), "--item", "frames"],
    )

    assert approval_code == 2
    assert approval_payload["error"] == {"code": "invalid_input", "message": "batch command input is invalid"}
    assert str(tmp_path) not in approval_stdout
    assert str(tmp_path) not in approval_stderr


@pytest.mark.parametrize(
    "argv",
    [
        ["unknown"],
        ["plan", "--analysis", "missing.json", "--policy", "not-a-policy"],
        ["apply", "--analysis", "analysis.json"],
    ],
)
def test_cli_input_errors_return_json_and_exit_two(
    argv: list[str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, payload, _, stderr = _invoke(capsys, argv)

    assert exit_code == 2
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "input_error"
    assert stderr


def test_unsafe_strategy_returns_exit_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "unsafe"
    _write_frames(frame_dir)
    _prepare_validated(capsys, frame_dir, artifact_root)
    strategy_path = artifact_root / "strategy.json"
    strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
    strategy["selected_sources"] = [0, 7]
    strategy_path.write_text(json.dumps(strategy), encoding="utf-8")

    exit_code, payload, _, _ = _invoke(
        capsys,
        ["validate", "--analysis", str(artifact_root / "analysis.json"), "--strategy", str(strategy_path)],
    )

    assert exit_code == 3
    assert payload["status"] == "unsafe"
    assert payload["result"]["valid"] is False


def test_execution_failure_returns_exit_four(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "execution_failure"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    _prepare_validated(capsys, frame_dir, artifact_root)
    changed = np.full((48, 64, 3), 250, dtype=np.uint8)
    assert cv2.imwrite(str(frame_dir / "frame_000003_src_000003.png"), changed)

    exit_code, payload, stdout, _ = _invoke(
        capsys,
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

    assert exit_code == 4
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "execution_failed"
    assert str(tmp_path) not in stdout


def test_apply_rejects_strategy_derived_from_tampered_analysis(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "tampered_analysis"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)

    exit_code, _, _, _ = _invoke(
        capsys,
        ["analyze", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
    )
    assert exit_code == 0

    analysis_path = artifact_root / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["motion_confidence"] = 1.0
    analysis["ranges"] = [
        {
            "start": 0,
            "end": 7,
            "kind": "jitter",
            "confidence": 1.0,
            "reason": "forged jitter range",
        }
    ]
    analysis["warnings"] = []
    for frame in analysis["frames"]:
        frame["motion_confidence"] = 1.0
        frame["jitter_score"] = 1.0
        frame["jitter_confidence"] = 1.0
        frame["low_quality_candidate"] = False
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    for argv in (
        ["plan", "--analysis", str(analysis_path), "--policy", "jitter_reduction"],
        [
            "validate",
            "--analysis",
            str(analysis_path),
            "--strategy",
            str(artifact_root / "strategy.json"),
        ],
    ):
        exit_code, payload, _, _ = _invoke(capsys, argv)
        assert exit_code == 0, payload

    strategy = json.loads((artifact_root / "strategy.json").read_text(encoding="utf-8"))
    assert len(strategy["selected_sources"]) < 8

    exit_code, payload, _, _ = _invoke(
        capsys,
        [
            "apply",
            "--frames",
            str(frame_dir),
            "--analysis",
            str(analysis_path),
            "--strategy",
            str(artifact_root / "strategy.json"),
            "--validation",
            str(artifact_root / "validation.json"),
            "--output-dir",
            str(output_dir),
        ],
    )

    assert exit_code == 4
    assert payload["error"]["code"] == "execution_failed"
    assert not output_dir.exists()


def test_health_failure_returns_exit_five(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "health_failure"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    _prepare_validated(capsys, frame_dir, artifact_root)
    exit_code, _, _, _ = _invoke(
        capsys,
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
    assert exit_code == 0
    next(output_dir.glob("*.png")).write_bytes(b"tampered")

    exit_code, payload, _, _ = _invoke(
        capsys,
        ["verify", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
    )

    assert exit_code == 5
    assert payload["status"] == "failed"
    assert payload["result"]["valid"] is False


def test_cli_rejects_unknown_artifact_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    artifact_root = tmp_path / "output" / "unknown_field"
    artifact_root.mkdir(parents=True)
    analysis_path = artifact_root / "analysis.json"
    analysis_path.write_text(json.dumps({"schema_version": 3, "unexpected": True}), encoding="utf-8")

    exit_code, payload, _, _ = _invoke(
        capsys,
        ["plan", "--analysis", str(analysis_path), "--policy", "coverage_first"],
    )

    assert exit_code == 2
    assert payload["error"]["code"] == "invalid_artifact"


@pytest.mark.parametrize("invalid_fps", [True, float("nan"), 10**1000])
def test_cli_rejects_noncanonical_artifact_numbers(
    invalid_fps: object,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "invalid_number"
    _write_frames(frame_dir)
    exit_code, _, _, _ = _invoke(
        capsys,
        ["analyze", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
    )
    assert exit_code == 0
    analysis_path = artifact_root / "analysis.json"
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    analysis["fps"] = invalid_fps
    analysis_path.write_text(json.dumps(analysis), encoding="utf-8")

    exit_code, payload, _, _ = _invoke(
        capsys,
        ["plan", "--analysis", str(analysis_path), "--policy", "coverage_first"],
    )

    assert exit_code == 2
    assert payload["error"]["code"] == "invalid_artifact"


def test_cli_accepts_utf8_bom_artifacts(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "bom"
    _write_frames(frame_dir)
    exit_code, _, _, _ = _invoke(
        capsys,
        ["analyze", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
    )
    assert exit_code == 0
    analysis_path = artifact_root / "analysis.json"
    analysis_path.write_text("\ufeff" + analysis_path.read_text(encoding="utf-8"), encoding="utf-8")

    exit_code, payload, _, _ = _invoke(
        capsys,
        ["plan", "--analysis", str(analysis_path), "--policy", "coverage_first"],
    )

    assert exit_code == 0
    assert payload["status"] == "ok"


def test_cli_errors_do_not_leak_absolute_input_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing_frames = tmp_path / "private" / "missing_frames"
    artifact_root = tmp_path / "output" / "missing_input"

    exit_code, payload, stdout, stderr = _invoke(
        capsys,
        ["analyze", "--frames", str(missing_frames), "--artifact-root", str(artifact_root)],
    )

    assert exit_code == 2
    assert payload["error"]["code"] == "input_error"
    assert str(tmp_path) not in stdout
    assert str(tmp_path) not in stderr


def test_verify_issues_do_not_leak_absolute_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "private" / "frames"
    artifact_root = tmp_path / "output" / "private_health"
    output_dir = artifact_root / "output_frames"
    _write_frames(frame_dir)
    _prepare_validated(capsys, frame_dir, artifact_root)
    exit_code, _, _, _ = _invoke(
        capsys,
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
    assert exit_code == 0
    selected_path = output_dir / "selected_frames.txt"
    lines = selected_path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split("\t")
    first_row = lines[1].split("\t")
    first_row[header.index("source_index")] = str(tmp_path)
    lines[1] = "\t".join(first_row)
    selected_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    exit_code, payload, stdout, stderr = _invoke(
        capsys,
        ["verify", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
    )

    assert exit_code == 5
    issues = payload["result"]["issues"]
    assert isinstance(issues, list)
    assert any(issue["code"] == "selected_manifest_invalid" for issue in issues)
    assert all(str(tmp_path) not in issue["message"] for issue in issues)
    assert str(tmp_path) not in stdout
    assert str(tmp_path) not in stderr


def test_cli_rejects_excessively_nested_artifact_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact_root = tmp_path / "output" / "deep_json"
    artifact_root.mkdir(parents=True)
    analysis_path = artifact_root / "analysis.json"
    analysis_path.write_text("[" * 10_000 + "0" + "]" * 10_000, encoding="utf-8")

    exit_code, payload, _, _ = _invoke(
        capsys,
        ["plan", "--analysis", str(analysis_path), "--policy", "coverage_first"],
    )

    assert exit_code == 2
    assert payload["error"]["code"] == "invalid_artifact"


def test_cli_rejects_noncanonical_artifact_filename(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "noncanonical"
    _write_frames(frame_dir)
    exit_code, _, _, _ = _invoke(
        capsys,
        ["analyze", "--frames", str(frame_dir), "--artifact-root", str(artifact_root)],
    )
    assert exit_code == 0
    custom_analysis = artifact_root / "custom-analysis.json"
    (artifact_root / "analysis.json").replace(custom_analysis)

    exit_code, payload, _, _ = _invoke(
        capsys,
        ["plan", "--analysis", str(custom_analysis), "--policy", "coverage_first"],
    )

    assert exit_code == 2
    assert payload["error"]["code"] == "input_error"
    assert not (artifact_root / "strategy.json").exists()


def test_cli_rejects_noncanonical_output_directory_as_input_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    artifact_root = tmp_path / "output" / "noncanonical_output"
    _write_frames(frame_dir)
    _prepare_validated(capsys, frame_dir, artifact_root)

    exit_code, payload, _, _ = _invoke(
        capsys,
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
            str(artifact_root / "frames_out"),
        ],
    )

    assert exit_code == 2
    assert payload["error"]["code"] == "input_error"


def test_tool_cli_does_not_import_legacy_facades_and_is_registered() -> None:
    assert not hasattr(tool_cli, "run_timing_agent")
    assert not hasattr(tool_cli, "run_batch_timing_agent")
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'frame-timing-tool = "frame_timing_agent.tool_cli:main"' in pyproject
