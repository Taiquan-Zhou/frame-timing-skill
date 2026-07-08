from __future__ import annotations

import json
import shutil
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest
from frame_timing_agent import benchmark_cli
from frame_timing_agent.benchmark_cli import (
    AutomatedChecks,
    HumanReview,
    PolicyBenchmarkResult,
    build_automated_checks,
    evaluate_case_gate,
    main,
)
from frame_timing_agent.contracts import AnalysisResult, RiskLevel, StrategyCandidate, StrategyRequest
from frame_timing_agent.review_policy import requires_human_confirmation
from jsonschema import Draft202012Validator


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


def _benchmark_schema_validator() -> Draft202012Validator:
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "benchmarks" / "case_schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


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
    _benchmark_schema_validator().validate(result)
    assert result["schema_version"] == 2
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
        set(item["deletion_reason_codes"])
        <= {"high_confidence_jitter_removed", "low_quality_with_substitute_removed", "redundant_static_removed"}
        for item in result["policies"]
    )
    assert result["automated_checks"]["active_range_static_misclassified_sources"] == []
    assert result["automated_checks"]["all_removals_use_allowed_reason"] is True
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


def test_benchmark_fails_closed_when_confirmation_policy_does_not_require_high_risk_review(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame_dir = tmp_path / "frames"
    output_root = tmp_path / "output" / "benchmark"
    _write_frames(frame_dir)
    monkeypatch.setattr(
        benchmark_cli,
        "requires_human_confirmation",
        lambda **_: False,
        raising=False,
    )

    assert main(_base_args(frame_dir, output_root)) == 0
    capsys.readouterr()
    result = json.loads((output_root / "synthetic-motion" / "benchmark_result.json").read_text(encoding="utf-8"))

    assert result["automated_checks"]["all_high_risk_policies_require_confirmation"] is False
    assert result["release_gate"] == {
        "reasons": ["high_risk_confirmation_missing"],
        "status": "failed",
    }


def test_benchmark_records_and_rejects_unknown_deletion_reason(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame_dir = tmp_path / "frames"
    output_root = tmp_path / "output" / "benchmark"
    _write_frames(frame_dir)
    original_plan_strategy = benchmark_cli.plan_strategy

    def plan_with_unknown_removal(
        analysis: AnalysisResult,
        request: StrategyRequest,
        artifact_root: Path | str,
    ) -> StrategyCandidate:
        candidate = original_plan_strategy(analysis, request, artifact_root)
        return replace(
            candidate,
            estimated_output_count=candidate.estimated_output_count - 1,
            retention_ratio=(candidate.estimated_output_count - 1) / 8,
            reasons=(
                *candidate.reasons,
                "high_confidence_jitter_removed",
                "future_unsafe_removed",
            ),
        )

    monkeypatch.setattr(benchmark_cli, "plan_strategy", plan_with_unknown_removal)

    assert main(_base_args(frame_dir, output_root)) == 0
    capsys.readouterr()
    result = json.loads((output_root / "synthetic-motion" / "benchmark_result.json").read_text(encoding="utf-8"))
    coverage_first = result["policies"][0]

    _benchmark_schema_validator().validate(result)
    assert coverage_first["deletion_reason_codes"] == [
        "high_confidence_jitter_removed",
        "future_unsafe_removed",
    ]
    assert result["automated_checks"]["all_removals_use_allowed_reason"] is False
    assert "unsupported_deletion_reason" in result["release_gate"]["reasons"]


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
    assert "redundant_static_removed" in readme
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


@pytest.mark.parametrize(
    ("checks", "expected_reason"),
    [
        (AutomatedChecks((4,), True, True, True), "active_range_static_misclassification"),
        (AutomatedChecks((), False, True, True), "policy_validation_failed"),
        (AutomatedChecks((), True, False, True), "high_risk_confirmation_missing"),
        (AutomatedChecks((), True, True, False), "unsupported_deletion_reason"),
    ],
)
def test_case_gate_rejects_each_automated_failure(
    checks: AutomatedChecks,
    expected_reason: str,
) -> None:
    gate = evaluate_case_gate(checks, HumanReview("pending", None, None, None, None, "pending"))

    assert gate.status == "failed"
    assert gate.reasons == (expected_reason,)


def test_static_thinning_is_an_allowed_benchmark_deletion_reason() -> None:
    policy = PolicyBenchmarkResult(
        policy="balanced",
        output_frame_count=7,
        removed_frame_count=1,
        retention_ratio=0.875,
        maximum_consecutive_drops=1,
        maximum_source_index_gap=2,
        maximum_time_gap_seconds=0.067,
        estimated_jitter_reduction=0.0,
        confidence=0.9,
        risk_level="medium",
        validation_valid=True,
        human_confirmation_required=True,
        deletion_reason_codes=("redundant_static_removed",),
        reasons=("redundant_static_removed",),
    )

    checks = build_automated_checks(active_range_static_misclassified_sources=(), policies=(policy,))

    assert checks.all_removals_use_allowed_reason is True


@pytest.mark.parametrize(
    ("valid", "risk_level", "has_review_ranges", "expected"),
    [
        (True, RiskLevel.LOW, False, False),
        (False, RiskLevel.LOW, False, True),
        (True, RiskLevel.MEDIUM, False, True),
        (True, RiskLevel.HIGH, False, True),
        (True, RiskLevel.LOW, True, True),
    ],
)
def test_human_confirmation_policy_is_explicit(
    valid: bool,
    risk_level: RiskLevel,
    has_review_ranges: bool,
    expected: bool,
) -> None:
    assert (
        requires_human_confirmation(
            valid=valid,
            risk_level=risk_level,
            has_review_ranges=has_review_ranges,
        )
        is expected
    )


def test_case_gate_passes_reviewed_case_with_zero_errors() -> None:
    gate = evaluate_case_gate(
        AutomatedChecks((), True, True, True),
        HumanReview("pass", 3, 0, 0, 2, "low"),
    )

    assert gate.status == "passed"
    assert gate.reasons == ()
