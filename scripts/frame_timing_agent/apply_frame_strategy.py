from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import re
import shutil
from typing import Any, Callable

from frame_timing_agent.analysis import compute_input_digest
from frame_timing_agent.configuration import resolve_strategy_request
from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AnalysisResult,
    ExecutionResult,
    StrategyCandidate,
    ValidationResult,
)
from frame_timing_agent.frame_source import FrameRecord
from frame_timing_agent.strategy_validator import validate_strategy


FRAME_OUTPUT_PATTERN = re.compile(r"^frame_\d{6}_src_\d{6}(?:_dup_\d{2})?$", re.IGNORECASE)


@dataclass(frozen=True)
class ApplyResult:
    output_count: int
    output_dir: Path
    selected_frames_path: Path
    manifest_path: Path


def choose_uniform_sources(source_indices: list[int], count: int) -> list[int]:
    ordered = sorted(source_indices)
    if count <= 0:
        raise ValueError(f"count must be positive: {count}")
    if len(ordered) <= count:
        return ordered
    if count == 1:
        return [ordered[0]]

    selected_positions = [round(index * (len(ordered) - 1) / (count - 1)) for index in range(count)]
    selected = [ordered[position] for position in selected_positions]
    deduped: list[int] = []
    for source_index in selected:
        if source_index not in deduped:
            deduped.append(source_index)

    cursor = 0
    while len(deduped) < count and cursor < len(ordered):
        candidate = ordered[cursor]
        if candidate not in deduped:
            deduped.append(candidate)
        cursor += 1
    return sorted(deduped)


def apply_strategy(
    records: list[FrameRecord],
    strategy: dict[str, Any],
    output_dir: Path | str,
    progress_callback: Callable[[int, int], None] | None = None,
) -> ApplyResult:
    output_dir = Path(output_dir)
    sorted_records = sorted(records, key=lambda record: (record.source_index, record.instance_id, record.output_index))
    operation_map = _operation_by_source(strategy.get("operations", []), sorted_records)

    output_rows = [
        "output_index\tsource_index\ttimestamp_sec\tinstance_id\tis_duplicate\toperation\tsource_sha256\tpath"
    ]
    source_hashes: dict[Path, str] = {}
    output_index = 0
    output_dir.mkdir(parents=True, exist_ok=True)
    clear_generated_outputs(output_dir)

    total = len(sorted_records)
    for completed, record in enumerate(sorted_records, start=1):
        operation = operation_map.get(record.source_index, {"op": "keep", "reason": "unchanged"})
        if operation["op"] == "skip":
            if progress_callback is not None:
                progress_callback(completed, total)
            continue

        total_instances = int(operation.get("total_instances", 1))
        if total_instances <= 0:
            raise ValueError(f"total_instances must be positive: {total_instances}")

        for instance_id in range(total_instances):
            suffix = "" if instance_id == 0 else f"_dup_{instance_id:02d}"
            destination_name = (
                f"frame_{output_index:06d}_src_{record.source_index:06d}{suffix}{record.path.suffix.lower()}"
            )
            destination_path = output_dir / destination_name
            source_hash = source_hashes.get(record.path)
            if source_hash is None:
                source_hash = _file_sha256(record.path)
                source_hashes[record.path] = source_hash
            _copy_frame(record.path, destination_path)
            timestamp = "" if record.timestamp_sec is None else f"{record.timestamp_sec:.6f}"
            output_rows.append(
                f"{output_index}\t{record.source_index}\t{timestamp}\t{instance_id}\t"
                f"{1 if instance_id > 0 else 0}\t{operation['op']}\t{source_hash}\t{destination_name}"
            )
            output_index += 1
        if progress_callback is not None:
            progress_callback(completed, total)

    selected_frames_path = output_dir / "selected_frames.txt"
    selected_frames_path.write_text("\n".join(output_rows) + "\n", encoding="utf-8")

    manifest_path = output_dir / "run_manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "output_count": output_index,
                "strategy_version": strategy.get("version"),
                "operation_count": len(strategy.get("operations", [])),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return ApplyResult(
        output_count=output_index,
        output_dir=output_dir,
        selected_frames_path=selected_frames_path,
        manifest_path=manifest_path,
    )


def apply_validated_strategy(
    records: Sequence[FrameRecord],
    analysis: AnalysisResult,
    candidate: StrategyCandidate,
    validation: ValidationResult,
    output_dir: Path,
) -> ExecutionResult:
    normalized_records = sorted(
        records, key=lambda record: (record.source_index, record.instance_id, record.output_index)
    )
    if not normalized_records:
        raise ValueError("validated execution requires at least one frame record")
    output_dir = Path(output_dir)
    _validate_output_boundary(normalized_records, output_dir)

    current_input_digest = compute_input_digest(normalized_records)
    if current_input_digest != analysis.input_digest:
        raise ValueError("input digest mismatch: source frames changed after analysis")

    config = resolve_strategy_request(candidate.request)
    current_validation = validate_strategy(analysis, candidate, config)
    if not current_validation.valid:
        raise ValueError("fresh validation failed; candidate cannot be executed")
    if not validation.valid:
        raise ValueError("validation is not valid")
    if validation.strategy_id != candidate.strategy_id:
        raise ValueError("validation strategy_id does not match candidate")
    if validation.input_digest != analysis.input_digest:
        raise ValueError("validation input digest does not match analysis")
    if validation.candidate_digest != current_validation.candidate_digest:
        raise ValueError("validation candidate digest does not match candidate")
    if validation.issues != current_validation.issues:
        raise ValueError("validation issues do not match fresh validation")

    strategy = {
        "version": SCHEMA_VERSION,
        "operations": [
            {
                "op": "select_sources",
                "range": {
                    "start": normalized_records[0].source_index,
                    "end": normalized_records[-1].source_index,
                },
                "sources": list(candidate.selected_sources),
                "reason": "validated_v3_candidate",
            }
        ],
    }
    try:
        applied = apply_strategy(normalized_records, strategy, output_dir)
    except Exception:
        if output_dir.is_dir():
            clear_generated_outputs(output_dir)
        raise
    if compute_input_digest(normalized_records) != analysis.input_digest:
        clear_generated_outputs(output_dir)
        raise ValueError("input frames changed during execution")
    return ExecutionResult(
        schema_version=SCHEMA_VERSION,
        run_id=analysis.run_id,
        strategy_id=candidate.strategy_id,
        input_digest=analysis.input_digest,
        candidate_digest=current_validation.candidate_digest,
        output_frame_count=applied.output_count,
        selected_sources=candidate.selected_sources,
        output_manifest=applied.manifest_path.relative_to(output_dir).as_posix(),
        output_digest=compute_output_digest(output_dir),
    )


def _operation_by_source(operations: list[dict[str, Any]], records: list[FrameRecord]) -> dict[int, dict[str, Any]]:
    _validate_non_overlapping(operations)
    available_sources = {record.source_index for record in records}
    operation_map: dict[int, dict[str, Any]] = {}

    for operation in operations:
        op = operation["op"]
        start = int(operation["range"]["start"])
        end = int(operation["range"]["end"])
        range_sources = [source for source in sorted(available_sources) if start <= source <= end]

        if op == "keep_uniform":
            kept_sources = set(choose_uniform_sources(range_sources, int(operation["count"])))
            for source in range_sources:
                operation_map[source] = (
                    operation
                    if source in kept_sources
                    else {
                        "op": "skip",
                        "reason": operation.get("reason", ""),
                    }
                )
        elif op == "duplicate_range":
            for source in range_sources:
                operation_map[source] = operation
        elif op == "keep":
            for source in range_sources:
                operation_map[source] = operation
        elif op == "mark_review":
            for source in range_sources:
                operation_map[source] = {"op": "keep", "reason": operation.get("reason", "")}
        elif op == "select_sources":
            selected_sources = {int(source) for source in operation.get("sources", [])}
            invalid_sources = [source for source in selected_sources if source < start or source > end]
            if invalid_sources:
                raise ValueError(f"select_sources contains source outside operation range: {min(invalid_sources)}")
            for source in range_sources:
                operation_map[source] = (
                    operation
                    if source in selected_sources
                    else {
                        "op": "skip",
                        "reason": operation.get("reason", ""),
                    }
                )
        else:
            raise ValueError(f"Unsupported strategy operation: {op}")

    return operation_map


def _validate_non_overlapping(operations: list[dict[str, Any]]) -> None:
    claimed_ranges: list[tuple[int, int]] = []
    for operation in operations:
        start = int(operation["range"]["start"])
        end = int(operation["range"]["end"])
        for claimed_start, claimed_end in claimed_ranges:
            if start <= claimed_end and claimed_start <= end:
                raise ValueError(f"Overlapping strategy operations at source_index {max(start, claimed_start)}")
        claimed_ranges.append((start, end))


def _copy_frame(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _validate_output_boundary(records: Sequence[FrameRecord], output_dir: Path) -> None:
    resolved_output = output_dir.resolve()
    for record in records:
        source_parent = record.path.resolve(strict=True).parent
        if (
            resolved_output == source_parent
            or resolved_output.is_relative_to(source_parent)
            or source_parent.is_relative_to(resolved_output)
        ):
            raise ValueError("output directory must not overlap an input directory")


def compute_output_digest(output_dir: Path) -> str:
    generated_paths = [output_dir / "selected_frames.txt", output_dir / "run_manifest.json"]
    generated_paths.extend(path for path in output_dir.iterdir() if path.is_file() and _is_generated_output_frame(path))
    digest = hashlib.sha256()
    for path in sorted(generated_paths, key=lambda item: item.name):
        identity = f"{path.relative_to(output_dir).as_posix()}\0{path.stat().st_size}\0{_file_sha256(path)}\n"
        digest.update(identity.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clear_generated_outputs(output_dir: Path) -> None:
    generated_names = {"selected_frames.txt", "run_manifest.json"}
    for path in output_dir.iterdir():
        if path.is_file() and (path.name in generated_names or _is_generated_output_frame(path)):
            path.unlink()


def _is_generated_output_frame(path: Path) -> bool:
    return (
        path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"} and FRAME_OUTPUT_PATTERN.match(path.stem) is not None
    )
