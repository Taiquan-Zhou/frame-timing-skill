from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path, PureWindowsPath

from frame_timing_agent.apply_frame_strategy import FRAME_OUTPUT_PATTERN, compute_output_digest
from frame_timing_agent.configuration import resolve_strategy_request
from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AnalysisResult,
    ExecutionResult,
    OutputVerificationResult,
    StrategyCandidate,
    ValidationIssue,
    ValidationSeverity,
)
from frame_timing_agent.frame_source import SUPPORTED_EXTENSIONS, load_frame_records
from frame_timing_agent.serialization import sha256_digest
from frame_timing_agent.strategy_validator import validate_strategy


def verify_output(
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    execution: ExecutionResult,
    output_dir: Path | str,
) -> OutputVerificationResult:
    output_dir = Path(output_dir)
    issues: list[ValidationIssue] = []
    if not output_dir.is_dir():
        return OutputVerificationResult(
            False,
            "",
            (_error("missing_output_directory", "output directory does not exist"),),
        )

    _check_execution_identity(analysis, candidate, execution, issues)
    config = resolve_strategy_request(candidate.request)
    if not validate_strategy(analysis, candidate, config).valid:
        issues.append(_error("candidate_validation_failed", "candidate no longer passes strategy validation"))
    rows = _load_selected_rows(output_dir, issues)
    selected_sources = _selected_sources(rows, issues)
    if tuple(selected_sources) != execution.selected_sources or tuple(selected_sources) != candidate.selected_sources:
        issues.append(_error("selected_sources_mismatch", "output sources do not match the validated candidate"))
    if len(selected_sources) != execution.output_frame_count:
        issues.append(_error("output_count_mismatch", "output source count does not match execution result"))

    _check_output_boundary(output_dir, issues)
    _check_manifest(output_dir, execution, issues)
    _check_source_hashes(output_dir, rows, issues)
    if not any(issue.code == "unsafe_output_path" for issue in issues):
        _check_output_record_identity(output_dir, analysis.fps, candidate, issues)
    try:
        output_digest = compute_output_digest(output_dir)
    except (FileNotFoundError, OSError, ValueError) as exc:
        output_digest = ""
        issues.append(_error("output_digest_failed", f"cannot compute output digest: {exc}"))
    if output_digest != execution.output_digest:
        issues.append(_error("output_digest_mismatch", "output content digest does not match execution result"))
    return OutputVerificationResult(not issues, output_digest, tuple(issues))


def _check_execution_identity(
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    execution: ExecutionResult,
    issues: list[ValidationIssue],
) -> None:
    if execution.schema_version != SCHEMA_VERSION:
        issues.append(_error("unsupported_execution_schema", f"execution schema_version must be {SCHEMA_VERSION}"))
    if execution.run_id != analysis.run_id:
        issues.append(_error("run_id_mismatch", "execution run_id does not match analysis"))
    if execution.strategy_id != candidate.strategy_id:
        issues.append(_error("strategy_id_mismatch", "execution strategy_id does not match candidate"))
    if execution.input_digest != analysis.input_digest or candidate.input_digest != analysis.input_digest:
        issues.append(_error("input_digest_mismatch", "execution input digest does not match analysis"))
    if execution.candidate_digest != sha256_digest(candidate):
        issues.append(_error("candidate_digest_mismatch", "execution candidate digest does not match candidate"))


def _load_selected_rows(output_dir: Path, issues: list[ValidationIssue]) -> list[dict[str, str]]:
    selected_path = output_dir / "selected_frames.txt"
    try:
        with selected_path.open("r", encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))
    except (FileNotFoundError, OSError, UnicodeError) as exc:
        issues.append(_error("selected_manifest_invalid", f"cannot read selected_frames.txt: {exc}"))
        return []


def _selected_sources(rows: list[dict[str, str]], issues: list[ValidationIssue]) -> list[int]:
    sources: list[int] = []
    try:
        for row in rows:
            sources.append(int(row["source_index"]))
    except (KeyError, TypeError, ValueError) as exc:
        issues.append(_error("selected_manifest_invalid", f"invalid source index in selected_frames.txt: {exc}"))
    return sources


def _check_output_boundary(output_dir: Path, issues: list[ValidationIssue]) -> None:
    allowed_names = {"selected_frames.txt", "run_manifest.json"}
    image_count = 0
    for path in output_dir.iterdir():
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            image_count += 1
        is_generated_frame = (
            path.is_file()
            and path.suffix.lower() in SUPPORTED_EXTENSIONS
            and FRAME_OUTPUT_PATTERN.fullmatch(path.stem) is not None
        )
        if not is_generated_frame and (not path.is_file() or path.name not in allowed_names):
            issues.append(_error("unexpected_output_entry", f"unexpected output entry: {path.name}"))
    if image_count != _manifest_output_count(output_dir):
        issues.append(_error("output_image_count_mismatch", "output image count does not match run_manifest"))


def _manifest_output_count(output_dir: Path) -> int:
    try:
        payload = json.loads((output_dir / "run_manifest.json").read_text(encoding="utf-8"))
        value = payload.get("output_count", -1)
        return int(value) if not isinstance(value, bool) else -1
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError):
        return -1


def _check_manifest(output_dir: Path, execution: ExecutionResult, issues: list[ValidationIssue]) -> None:
    manifest_relative = Path(execution.output_manifest)
    windows_path = PureWindowsPath(execution.output_manifest)
    expected_manifest = Path(output_dir.name) / "run_manifest.json"
    if (
        manifest_relative != expected_manifest
        or manifest_relative.anchor
        or windows_path.anchor
        or ".." in manifest_relative.parts
        or ".." in windows_path.parts
    ):
        issues.append(_error("unsafe_output_manifest", "execution output_manifest must identify this output directory"))
        return
    manifest_path = output_dir.parent / manifest_relative
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        issues.append(_error("output_manifest_invalid", f"cannot read output manifest: {exc}"))
        return
    if manifest.get("output_count") != execution.output_frame_count:
        issues.append(_error("output_count_mismatch", "run_manifest output_count does not match execution result"))


def _check_source_hashes(
    output_dir: Path,
    rows: list[dict[str, str]],
    issues: list[ValidationIssue],
) -> None:
    for row in rows:
        raw_path = row.get("path", "")
        relative = Path(raw_path)
        windows_path = PureWindowsPath(raw_path)
        if (
            not raw_path
            or relative.anchor
            or windows_path.anchor
            or ".." in relative.parts
            or ".." in windows_path.parts
        ):
            issues.append(_error("unsafe_output_path", "selected frame path must be output-relative"))
            continue
        frame_path = output_dir / relative
        try:
            actual_hash = _file_sha256(frame_path)
        except (FileNotFoundError, OSError) as exc:
            issues.append(_error("output_frame_missing", f"cannot read output frame: {exc}"))
            continue
        if actual_hash != row.get("source_sha256"):
            issues.append(_error("source_hash_mismatch", f"output frame bytes changed: {relative.as_posix()}"))


def _check_output_record_identity(
    output_dir: Path,
    fps: float,
    candidate: StrategyCandidate,
    issues: list[ValidationIssue],
) -> None:
    try:
        records = load_frame_records(output_dir, fps=fps)
    except (FileNotFoundError, OSError, UnicodeError, ValueError) as exc:
        issues.append(_error("output_record_identity_mismatch", f"cannot load output frame identities: {exc}"))
        return
    if tuple(record.source_index for record in records) != candidate.selected_sources:
        issues.append(_error("output_record_identity_mismatch", "output frame identities do not match candidate"))


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _error(code: str, message: str) -> ValidationIssue:
    return ValidationIssue(code, ValidationSeverity.ERROR, message)
