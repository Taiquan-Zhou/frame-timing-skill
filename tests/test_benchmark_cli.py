from __future__ import annotations

import json
import shutil
from pathlib import Path

import cv2
import numpy as np
import pytest

from frame_timing_agent.benchmark_cli import (
    AutomatedChecks,
    HumanReview,
    evaluate_case_gate,
    main,
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
        encoded, buffer = cv2.imencode(".png", image)
        assert encoded
        (frame_dir / f"frame_{index:06d}_src_{index:06d}.png").write_bytes(buffer.tobytes())


def _nested_strings(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, dict):
        return tuple(text for item in value.values() for text in _nested_strings(item))
    if isinstance(value, list):
        return tuple(text for item in value for text in _nested_strings(item))
    return ()


def _base_args(frame_dir: Path, output_root: Path) -> list[str]:
    return [
        "--frames",
        str(frame_dir),
        "--output-root",
        str(output_root),
        "--case-id",
        "synthetic-motion",
        "--fps",
        "30",
        "--device-category",
        "synthetic",
        "--motion-type",
        "slow_translation",
        "--depth-structure",
        "layered",
        "--lighting",
        "controlled",
        "--expected-active-range",
        "0:7",
        "--human-conclusion",
        "pending",
    ]


def test_benchmark_runs_three_policies_without_private_paths_or_raw_frames(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "private_customer" / "frames"
    output_root = tmp_path / "output" / "benchmark"
    _write_frames(frame_dir)

    exit_code = main(_base_args(frame_dir, output_root))
    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload == {
        "result": "synthetic-motion/benchmark_result.json",
        "status": "ok",
    }
    result_path = output_root / "synthetic-motion" / "benchmark_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert result["schema_version"] == 1
    assert result["case_id"] == "synthetic-motion"
    assert result["input_frame_count"] == 8
    assert result["resolution"] == {"height": 48, "width": 64}
    assert result["fps"] == 30.0
    assert result["acceptance_scope"] == "current_external_smoke_set_only"
    assert result["human_review"]["conclusion"] == "pending"
    assert result["release_gate"]["status"] == "pending"
    assert [item["policy"] for item in result["policies"]] == [
        "coverage_first",
        "balanced",
        "jitter_reduction",
    ]
    assert all(item["validation_valid"] for item in result["policies"])
    assert all(item["removed_frame_count"] >= 0 for item in result["policies"])
    assert all(
        set(item["deletion_reason_codes"]) <= {"high_confidence_jitter_removed", "low_quality_with_substitute_removed"}
        for item in result["policies"]
    )
    assert result["automated_checks"]["active_range_static_misclassified_sources"] == []
    assert result["automated_checks"]["all_removals_use_jitter_or_quality_reason"] is True
    assert str(tmp_path) not in _nested_strings(result)
    assert str(tmp_path) not in captured.out
    assert not list((output_root / "synthetic-motion").rglob("*.png"))
    for policy in ("coverage_first", "balanced", "jitter_reduction"):
        assert (output_root / "synthetic-motion" / "policies" / policy / "strategy.json").is_file()
        assert (output_root / "synthetic-motion" / "policies" / policy / "validation.json").is_file()


def test_benchmark_rejects_invalid_active_range_as_json_input_error(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    output_root = tmp_path / "output" / "benchmark"
    _write_frames(frame_dir)
    args = _base_args(frame_dir, output_root)
    range_index = args.index("0:7")
    args[range_index] = "7:0"

    exit_code = main(args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "invalid_input"
    assert not output_root.exists()


def test_benchmark_command_is_registered() -> None:
    pyproject = (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    assert 'frame-timing-benchmark = "frame_timing_agent.benchmark_cli:main"' in pyproject


def test_failed_rerun_invalidates_previous_benchmark_result(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    frame_dir = tmp_path / "frames"
    output_root = tmp_path / "output" / "benchmark"
    _write_frames(frame_dir)
    args = _base_args(frame_dir, output_root)
    assert main(args) == 0
    capsys.readouterr()
    result_path = output_root / "synthetic-motion" / "benchmark_result.json"
    assert result_path.is_file()
    shutil.rmtree(frame_dir)

    exit_code = main(args)
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["status"] == "error"
    assert not result_path.exists()


def test_benchmark_schema_and_readme_define_honest_external_protocol() -> None:
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "benchmarks" / "case_schema.json").read_text(encoding="utf-8"))
    required = set(schema["required"])
    assert required >= {
        "case_id",
        "input_digest",
        "input_frame_count",
        "resolution",
        "fps",
        "device_category",
        "motion_type",
        "depth_structure",
        "lighting",
        "software_version",
        "policies",
        "human_review",
        "release_gate",
    }
    assert schema["additionalProperties"] is False
    assert schema["properties"]["input_digest"]["pattern"] == "^[0-9a-f]{64}$"
    readme = (root / "benchmarks" / "README.md").read_text(encoding="utf-8")
    for category in (
        "slow translation",
        "handheld jitter",
        "rapid intentional turn",
        "low texture",
        "blur burst",
        "parallax",
        "independent foreground motion",
    ):
        assert category in readme
    assert "not a statistical accuracy claim" in readme
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "benchmarks/private/" in gitignore
    assert "benchmarks/results/" in gitignore


def test_case_gate_rejects_false_positive_or_high_coverage_risk_despite_pass_label() -> None:
    checks = AutomatedChecks((), True, True, True)

    false_positive = evaluate_case_gate(
        checks,
        HumanReview("pass", 3, 1, 0, 1, "low"),
    )
    high_coverage_risk = evaluate_case_gate(
        checks,
        HumanReview("pass", 3, 0, 0, 1, "high"),
    )

    assert false_positive.status == "failed"
    assert "human_false_positives_observed" in false_positive.reasons
    assert high_coverage_risk.status == "failed"
    assert "high_reconstruction_coverage_risk" in high_coverage_risk.reasons


def test_case_gate_passes_reviewed_case_with_zero_errors() -> None:
    gate = evaluate_case_gate(
        AutomatedChecks((), True, True, True),
        HumanReview("pass", 3, 0, 0, 2, "low"),
    )

    assert gate.status == "passed"
    assert gate.reasons == ()
