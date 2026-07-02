from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np
import pytest

import frame_timing_agent.tool_cli as tool_cli
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


def test_tool_cli_does_not_import_legacy_facades_and_is_registered() -> None:
    assert not hasattr(tool_cli, "run_timing_agent")
    assert not hasattr(tool_cli, "run_batch_timing_agent")
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'frame-timing-tool = "frame_timing_agent.tool_cli:main"' in pyproject
