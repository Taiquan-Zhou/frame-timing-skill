from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Never

from frame_timing_agent.artifact_layout import validate_artifact_root
from frame_timing_agent.contracts import AnalysisRange, PolicyName, RiskLevel, StrategyRequest
from frame_timing_agent.serialization import canonical_json_bytes, write_canonical_json_atomic
from frame_timing_agent.service import analyze_frames, plan_strategy, validate_strategy

BENCHMARK_SCHEMA_VERSION = 1
RESULT_ARTIFACT = "benchmark_result.json"
_SAFE_CODE = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}")
_CASE_ID = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_DELETION_REASON_CODES = (
    "high_confidence_jitter_removed",
    "low_quality_with_substitute_removed",
    "redundant_static_removed",
)
_ALLOWED_BENCHMARK_DELETION_REASONS = {
    "high_confidence_jitter_removed",
    "low_quality_with_substitute_removed",
}


class BenchmarkInputError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise BenchmarkInputError(message)


@dataclass(frozen=True)
class SourceRange:
    start: int
    end: int


@dataclass(frozen=True)
class Resolution:
    width: int
    height: int


@dataclass(frozen=True)
class PolicyBenchmarkResult:
    policy: str
    output_frame_count: int
    removed_frame_count: int
    retention_ratio: float
    maximum_consecutive_drops: int
    maximum_source_index_gap: int
    maximum_time_gap_seconds: float
    estimated_jitter_reduction: float
    confidence: float
    risk_level: str
    validation_valid: bool
    human_confirmation_required: bool
    deletion_reason_codes: tuple[str, ...]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class AutomatedChecks:
    active_range_static_misclassified_sources: tuple[int, ...]
    all_policy_validations_passed: bool
    all_high_risk_policies_require_confirmation: bool
    all_removals_use_jitter_or_quality_reason: bool


@dataclass(frozen=True)
class HumanReview:
    conclusion: str
    correct_detections: int | None
    false_positives: int | None
    false_negatives: int | None
    review_requests: int | None
    reconstruction_coverage_risk: str


@dataclass(frozen=True)
class ReleaseGate:
    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class BenchmarkResult:
    schema_version: int
    case_id: str
    input_digest: str
    input_frame_count: int
    resolution: Resolution
    fps: float
    device_category: str
    motion_type: str
    depth_structure: str
    lighting: str
    software_version: str
    acceptance_scope: str
    expected_active_ranges: tuple[SourceRange, ...]
    review_request_rate: float
    policies: tuple[PolicyBenchmarkResult, ...]
    automated_checks: AutomatedChecks
    human_review: HumanReview
    release_gate: ReleaseGate


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
        result_path = run_benchmark(
            frame_dir=Path(args.frames),
            output_root=Path(args.output_root),
            case_id=args.case_id,
            fps=args.fps,
            device_category=args.device_category,
            motion_type=args.motion_type,
            depth_structure=args.depth_structure,
            lighting=args.lighting,
            expected_active_ranges=tuple(_parse_range(value) for value in args.expected_active_range),
            human_review=_human_review_from_args(args),
        )
    except (BenchmarkInputError, FileNotFoundError, OSError, ValueError) as exc:
        _emit_error("invalid_input", exc)
        return 2
    _emit({"status": "ok", "result": result_path.as_posix()})
    return 0


def run_benchmark(
    *,
    frame_dir: Path,
    output_root: Path,
    case_id: str,
    fps: float,
    device_category: str,
    motion_type: str,
    depth_structure: str,
    lighting: str,
    expected_active_ranges: tuple[SourceRange, ...],
    human_review: HumanReview,
) -> Path:
    _validate_case_id(case_id)
    metadata_codes = {
        "device_category": device_category,
        "motion_type": motion_type,
        "depth_structure": depth_structure,
        "lighting": lighting,
    }
    for field_name, value in metadata_codes.items():
        _validate_code(value, field_name)
    if not expected_active_ranges:
        raise BenchmarkInputError("at least one expected active range is required")
    validate_artifact_root(output_root, frame_dir)
    case_root = output_root / case_id
    (case_root / RESULT_ARTIFACT).unlink(missing_ok=True)
    analysis = analyze_frames(frame_dir, case_root / "analysis", fps=fps)
    available_sources = {frame.source_index for frame in analysis.frames}
    for item in expected_active_ranges:
        if not any(item.start <= source <= item.end for source in available_sources):
            raise BenchmarkInputError("expected active range does not intersect analyzed sources")

    review_sources = _sources_for_kind(analysis.ranges, available_sources, "review_required")
    policies: list[PolicyBenchmarkResult] = []
    for policy in PolicyName:
        policy_root = case_root / "policies" / policy.value
        request = StrategyRequest(policy)
        candidate = plan_strategy(analysis, request, policy_root)
        validation = validate_strategy(analysis, candidate, request, policy_root)
        policies.append(
            PolicyBenchmarkResult(
                policy=policy.value,
                output_frame_count=candidate.estimated_output_count,
                removed_frame_count=analysis.frame_count - candidate.estimated_output_count,
                retention_ratio=candidate.retention_ratio,
                maximum_consecutive_drops=candidate.maximum_consecutive_drops,
                maximum_source_index_gap=candidate.maximum_source_index_gap,
                maximum_time_gap_seconds=candidate.maximum_time_gap_seconds,
                estimated_jitter_reduction=candidate.estimated_jitter_reduction,
                confidence=candidate.confidence,
                risk_level=candidate.risk_level.value,
                validation_valid=validation.valid,
                human_confirmation_required=(
                    candidate.risk_level in {RiskLevel.MEDIUM, RiskLevel.HIGH} or bool(review_sources)
                ),
                deletion_reason_codes=tuple(code for code in _DELETION_REASON_CODES if code in candidate.reasons),
                reasons=candidate.reasons,
            )
        )

    static_sources = _sources_for_kind(analysis.ranges, available_sources, "static")
    expected_active_sources = {
        source for item in expected_active_ranges for source in available_sources if item.start <= source <= item.end
    }
    checks = AutomatedChecks(
        active_range_static_misclassified_sources=tuple(sorted(static_sources & expected_active_sources)),
        all_policy_validations_passed=all(item.validation_valid for item in policies),
        all_high_risk_policies_require_confirmation=all(
            item.risk_level != RiskLevel.HIGH.value or item.human_confirmation_required for item in policies
        ),
        all_removals_use_jitter_or_quality_reason=all(
            item.removed_frame_count == 0
            or (
                bool(item.deletion_reason_codes)
                and set(item.deletion_reason_codes) <= _ALLOWED_BENCHMARK_DELETION_REASONS
            )
            for item in policies
        ),
    )
    release_gate = evaluate_case_gate(checks, human_review)
    result = BenchmarkResult(
        schema_version=BENCHMARK_SCHEMA_VERSION,
        case_id=case_id,
        input_digest=analysis.input_digest,
        input_frame_count=analysis.frame_count,
        resolution=Resolution(width=analysis.width, height=analysis.height),
        fps=analysis.fps,
        device_category=device_category,
        motion_type=motion_type,
        depth_structure=depth_structure,
        lighting=lighting,
        software_version=_software_version(),
        acceptance_scope="current_external_smoke_set_only",
        expected_active_ranges=expected_active_ranges,
        review_request_rate=len(review_sources) / analysis.frame_count,
        policies=tuple(policies),
        automated_checks=checks,
        human_review=human_review,
        release_gate=release_gate,
    )
    write_canonical_json_atomic(case_root / RESULT_ARTIFACT, result)
    return Path(case_id) / RESULT_ARTIFACT


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(prog="frame-timing-benchmark")
    parser.add_argument("--frames", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--case-id", required=True)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--device-category", required=True)
    parser.add_argument("--motion-type", required=True)
    parser.add_argument("--depth-structure", required=True)
    parser.add_argument("--lighting", required=True)
    parser.add_argument("--expected-active-range", action="append", required=True)
    parser.add_argument("--human-conclusion", choices=("pending", "pass", "fail"), default="pending")
    parser.add_argument("--human-correct-detections", type=int)
    parser.add_argument("--human-false-positives", type=int)
    parser.add_argument("--human-false-negatives", type=int)
    parser.add_argument("--human-review-requests", type=int)
    parser.add_argument(
        "--reconstruction-coverage-risk",
        choices=("pending", "low", "medium", "high"),
        default="pending",
    )
    return parser


def _human_review_from_args(args: argparse.Namespace) -> HumanReview:
    values = (
        args.human_correct_detections,
        args.human_false_positives,
        args.human_false_negatives,
        args.human_review_requests,
    )
    if any(value is not None and value < 0 for value in values):
        raise BenchmarkInputError("human review counts must be non-negative")
    if args.human_conclusion != "pending" and (
        any(value is None for value in values) or args.reconstruction_coverage_risk == "pending"
    ):
        raise BenchmarkInputError("completed human review requires counts and reconstruction coverage risk")
    return HumanReview(
        conclusion=args.human_conclusion,
        correct_detections=args.human_correct_detections,
        false_positives=args.human_false_positives,
        false_negatives=args.human_false_negatives,
        review_requests=args.human_review_requests,
        reconstruction_coverage_risk=args.reconstruction_coverage_risk,
    )


def _parse_range(raw: str) -> SourceRange:
    try:
        start_text, end_text = raw.split(":", maxsplit=1)
        start = int(start_text)
        end = int(end_text)
    except (TypeError, ValueError) as exc:
        raise BenchmarkInputError("expected active range must use START:END") from exc
    if start < 0 or end < start:
        raise BenchmarkInputError("expected active range must satisfy 0 <= START <= END")
    return SourceRange(start=start, end=end)


def _sources_for_kind(
    ranges: tuple[AnalysisRange, ...],
    available_sources: set[int],
    kind: str,
) -> set[int]:
    return {
        source
        for item in ranges
        if item.kind == kind
        for source in available_sources
        if item.start <= source <= item.end
    }


def evaluate_case_gate(checks: AutomatedChecks, human_review: HumanReview) -> ReleaseGate:
    reasons: list[str] = []
    if checks.active_range_static_misclassified_sources:
        reasons.append("active_range_static_misclassification")
    if not checks.all_policy_validations_passed:
        reasons.append("policy_validation_failed")
    if not checks.all_high_risk_policies_require_confirmation:
        reasons.append("high_risk_confirmation_missing")
    if not checks.all_removals_use_jitter_or_quality_reason:
        reasons.append("unsupported_deletion_reason")
    if human_review.conclusion == "fail":
        reasons.append("human_review_failed")
    if human_review.conclusion == "pass":
        counts = (
            human_review.correct_detections,
            human_review.false_positives,
            human_review.false_negatives,
            human_review.review_requests,
        )
        if any(value is None for value in counts) or human_review.reconstruction_coverage_risk == "pending":
            reasons.append("human_review_incomplete")
        else:
            if human_review.false_positives is not None and human_review.false_positives > 0:
                reasons.append("human_false_positives_observed")
            if human_review.false_negatives is not None and human_review.false_negatives > 0:
                reasons.append("human_false_negatives_observed")
            if human_review.reconstruction_coverage_risk == "high":
                reasons.append("high_reconstruction_coverage_risk")
    if reasons:
        return ReleaseGate(status="failed", reasons=tuple(reasons))
    if human_review.conclusion == "pending":
        return ReleaseGate(status="pending", reasons=("human_review_pending",))
    return ReleaseGate(status="passed", reasons=())


def _validate_case_id(value: str) -> None:
    if _CASE_ID.fullmatch(value) is None:
        raise BenchmarkInputError("case_id must be a safe lowercase identifier")


def _validate_code(value: str, field_name: str) -> None:
    if _SAFE_CODE.fullmatch(value) is None:
        raise BenchmarkInputError(f"{field_name} must be a safe metadata code")


def _software_version() -> str:
    try:
        return version("frame-timing-skill")
    except PackageNotFoundError:
        return "0+unknown"


def _emit(payload: object) -> None:
    sys.stdout.write(canonical_json_bytes(payload).decode("utf-8") + "\n")


def _emit_error(code: str, exc: Exception) -> None:
    print(f"[benchmark] {code}: {type(exc).__name__}", file=sys.stderr)
    _emit({"status": "error", "error": {"code": code, "message": "benchmark input is invalid"}})


if __name__ == "__main__":
    raise SystemExit(main())
