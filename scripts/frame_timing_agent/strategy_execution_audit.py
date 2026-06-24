from __future__ import annotations

from pathlib import Path
import json

from frame_timing_agent.frame_source import FrameRecord, SUPPORTED_EXTENSIONS, load_frame_records


def audit_strategy_execution(
    input_records: list[FrameRecord],
    strategy: dict,
    output_dir: Path | str,
    fps: float = 30.0,
) -> dict:
    output_dir = Path(output_dir)
    errors: list[str] = []
    warnings: list[str] = []
    output_records = _load_output_records(output_dir, fps=fps, errors=errors)
    manifest = _load_manifest(output_dir, errors=errors)

    image_count = _count_output_images(output_dir)
    manifest_output_count = manifest.get("output_count") if manifest else None
    output_count = len(output_records)

    if manifest_output_count is not None and manifest_output_count != output_count:
        errors.append(f"manifest output_count mismatch: manifest={manifest_output_count}, selected_frames={output_count}")
    if image_count != output_count:
        errors.append(f"image_count mismatch: images={image_count}, selected_frames={output_count}")
    if output_records and [record.output_index for record in output_records] != list(range(output_count)):
        errors.append("output_index is not contiguous from 0")

    operation_results = [
        _audit_operation(operation, input_records, output_records)
        for operation in strategy.get("operations", [])
    ]
    for result in operation_results:
        errors.extend(result.pop("errors", []))
        warnings.extend(result.pop("warnings", []))

    return {
        "status": "failed" if errors else "ok",
        "input_count": len(input_records),
        "output_count": output_count,
        "manifest_output_count": manifest_output_count,
        "image_count": image_count,
        "errors": errors,
        "warnings": warnings,
        "operation_results": operation_results,
    }


def write_execution_audit(audit: dict, analysis_dir: Path | str) -> None:
    analysis_dir = Path(analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    (analysis_dir / "execution_audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (analysis_dir / "execution_audit.md").write_text(_audit_markdown(audit), encoding="utf-8")


def _load_output_records(output_dir: Path, fps: float, errors: list[str]) -> list[FrameRecord]:
    try:
        return load_frame_records(output_dir, fps=fps)
    except (FileNotFoundError, ValueError) as exc:
        errors.append(f"cannot load selected output records: {exc}")
        return []


def _load_manifest(output_dir: Path, errors: list[str]) -> dict:
    manifest_path = output_dir / "run_manifest.json"
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"missing run_manifest.json: {manifest_path}")
    except json.JSONDecodeError as exc:
        errors.append(f"invalid run_manifest.json: {exc}")
    return {}


def _count_output_images(output_dir: Path) -> int:
    if not output_dir.exists():
        return 0
    return sum(1 for path in output_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)


def _audit_operation(
    operation: dict,
    input_records: list[FrameRecord],
    output_records: list[FrameRecord],
) -> dict:
    op = operation.get("op")
    source_range = operation.get("range", {})
    start = int(source_range.get("start", 0))
    end = int(source_range.get("end", -1))
    affected_sources = sorted({record.source_index for record in input_records if start <= record.source_index <= end})
    output_in_range = [record for record in output_records if start <= record.source_index <= end]
    result = {
        "op": op,
        "range": {"start": start, "end": end},
        "source": operation.get("source", "unknown"),
        "affected_source_count": len(affected_sources),
        "output_record_count": len(output_in_range),
        "errors": [],
        "warnings": [],
    }

    if op == "duplicate_range":
        total_instances = int(operation.get("total_instances", 1))
        expected_output = len(affected_sources) * total_instances
        result["total_instances"] = total_instances
        result["expected_output_record_count"] = expected_output
        result["added_count"] = max(0, expected_output - len(affected_sources))
        if len(output_in_range) != expected_output:
            result["errors"].append(
                f"duplicate_range {start}-{end} expected {expected_output} records, got {len(output_in_range)}"
            )
        for source in affected_sources:
            instances = sorted(record.instance_id for record in output_in_range if record.source_index == source)
            expected_instances = list(range(total_instances))
            if instances != expected_instances:
                result["errors"].append(
                    f"source {source} expected duplicate instances {expected_instances}, got {instances}"
                )
        return result

    if op == "keep_uniform":
        requested_count = int(operation.get("count", len(affected_sources)))
        expected_kept_count = min(len(affected_sources), requested_count)
        kept_sources = sorted({record.source_index for record in output_in_range})
        result["requested_count"] = requested_count
        result["kept_sources"] = kept_sources
        result["dropped_count"] = max(0, len(affected_sources) - len(kept_sources))
        if len(kept_sources) != expected_kept_count:
            result["errors"].append(
                f"keep_uniform {start}-{end} expected {expected_kept_count} kept sources, got {len(kept_sources)}"
            )
        if affected_sources and expected_kept_count > 1:
            expected_edges = {affected_sources[0], affected_sources[-1]}
            if not expected_edges.issubset(set(kept_sources)):
                result["errors"].append(f"keep_uniform {start}-{end} did not keep first and last source")
        return result

    if op in {"keep", "mark_review"}:
        result["kept_sources"] = sorted({record.source_index for record in output_in_range})
        return result

    if op == "select_sources":
        requested_sources = sorted({int(source) for source in operation.get("sources", [])})
        expected_sources = [source for source in requested_sources if source in affected_sources]
        kept_sources = sorted({record.source_index for record in output_in_range})
        result["expected_sources"] = expected_sources
        result["kept_sources"] = kept_sources
        result["dropped_count"] = max(0, len(affected_sources) - len(kept_sources))
        result["expected_output_record_count"] = len(expected_sources)
        if kept_sources != expected_sources:
            result["errors"].append(
                f"select_sources {start}-{end} expected sources {expected_sources}, got {kept_sources}"
            )
        if len(output_in_range) != len(expected_sources):
            result["errors"].append(
                f"select_sources {start}-{end} expected {len(expected_sources)} records, got {len(output_in_range)}"
            )
        for source in kept_sources:
            instances = [record.instance_id for record in output_in_range if record.source_index == source]
            if instances != [0]:
                result["errors"].append(
                    f"source {source} expected one non-duplicate instance [0], got {instances}"
                )
        return result

    result["warnings"].append(f"unsupported audit details for operation: {op}")
    return result


def _audit_markdown(audit: dict) -> str:
    lines = [
        "# Strategy Execution Audit",
        "",
        f"Status: {audit.get('status')}",
        f"Input records: {audit.get('input_count')}",
        f"Output records: {audit.get('output_count')}",
        f"Manifest output_count: {audit.get('manifest_output_count')}",
        f"Image count: {audit.get('image_count')}",
        "",
        "## Errors",
    ]
    errors = audit.get("errors", [])
    lines.extend([f"- {error}" for error in errors] if errors else ["- none"])

    lines.extend(["", "## Warnings"])
    warnings = audit.get("warnings", [])
    lines.extend([f"- {warning}" for warning in warnings] if warnings else ["- none"])

    lines.extend(["", "## Operation Results"])
    operation_results = audit.get("operation_results", [])
    if operation_results:
        for result in operation_results:
            source_range = result["range"]
            lines.append(
                f"- {result['op']}: source {source_range['start']}-{source_range['end']}, "
                f"affected={result['affected_source_count']}, output={result['output_record_count']}, "
                f"source={result.get('source', 'unknown')}"
            )
    else:
        lines.append("- none")

    lines.append("")
    return "\n".join(lines)
