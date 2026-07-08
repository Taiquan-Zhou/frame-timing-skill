from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import csv
import hashlib
import json
import re
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frame_timing_agent.frame_source import SUPPORTED_EXTENSIONS, load_frame_records


MARKDOWN_TARGET_PATTERN = re.compile(r"(!?)\[[^\]]*\]\(([^)]+)\)")


@dataclass(frozen=True)
class BatchArtifactHealthResult:
    artifact_root: Path
    status: str
    errors: list[str]
    warnings: list[str]
    checked_items: int
    checked_links: int
    checked_output_frames: int
    report_path: Path
    json_path: Path


def run_batch_artifact_health_check(artifact_root: Path | str) -> BatchArtifactHealthResult:
    artifact_root = Path(artifact_root)
    analysis_dir = artifact_root / "analysis"
    report_path = analysis_dir / "maintenance_report.md"
    json_path = analysis_dir / "maintenance_report.json"
    errors: list[str] = []
    warnings: list[str] = []

    _check_output_root(artifact_root, errors)
    summary = _load_json(analysis_dir / "batch_summary.json", errors)
    _require_file(analysis_dir / "batch_summary.csv", errors)
    _require_file(analysis_dir / "human_review.md", errors)
    dashboard_path = analysis_dir / "review_dashboard.md"
    _require_file(dashboard_path, errors)

    checked_links = _check_markdown_links(dashboard_path, errors, warnings) if dashboard_path.exists() else 0
    item_count = 0
    checked_output_frames = 0
    if summary:
        items = summary.get("items", [])
        if not isinstance(items, list):
            errors.append("batch_summary.json items must be a list")
            items = []
        _check_summary_counts(summary, items, errors)
        _check_unregistered_item_dirs(artifact_root, items, errors)
        for item in items:
            item_count += 1
            checked_output_frames += _check_item(artifact_root, item, errors, warnings)

    status = "failed" if errors else "ok"
    result = BatchArtifactHealthResult(
        artifact_root=artifact_root,
        status=status,
        errors=errors,
        warnings=warnings,
        checked_items=item_count,
        checked_links=checked_links,
        checked_output_frames=checked_output_frames,
        report_path=report_path,
        json_path=json_path,
    )
    _write_health_artifacts(result)
    return result


def _check_output_root(artifact_root: Path, errors: list[str]) -> None:
    if "output" not in {part.lower() for part in artifact_root.parts}:
        errors.append(f"artifact_root is not inside output: {artifact_root}")


def _require_file(path: Path, errors: list[str]) -> None:
    if not path.is_file():
        errors.append(f"missing required file: {path}")


def _load_json(path: Path, errors: list[str]) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        errors.append(f"missing required file: {path}")
    except json.JSONDecodeError as exc:
        errors.append(f"invalid json file {path}: {exc}")
    return {}


def _check_summary_counts(summary: dict, items: list[dict], errors: list[str]) -> None:
    success_count = sum(1 for item in items if item.get("status") == "ok")
    failure_count = sum(1 for item in items if item.get("status") != "ok")
    if int(summary.get("success_count", -1)) != success_count:
        errors.append(
            f"batch_summary success_count mismatch: summary={summary.get('success_count')}, items={success_count}"
        )
    if int(summary.get("failure_count", -1)) != failure_count:
        errors.append(
            f"batch_summary failure_count mismatch: summary={summary.get('failure_count')}, items={failure_count}"
        )


def _check_unregistered_item_dirs(artifact_root: Path, items: list[dict], errors: list[str]) -> None:
    registered = {str(item.get("name")) for item in items}
    for child in artifact_root.iterdir():
        if not child.is_dir() or child.name == "analysis":
            continue
        if child.name not in registered:
            errors.append(f"unregistered item directory: {child.name}")


def _check_item(artifact_root: Path, item: dict, errors: list[str], warnings: list[str]) -> int:
    name = str(item.get("name", "<unnamed>"))
    frame_dir = _path_from_item(item, "frame_dir", artifact_root)
    artifact_dir = _path_from_item(item, "artifact_dir", artifact_root)
    human_review = _path_from_item(item, "human_review_path", artifact_root)
    strategy_path = _path_from_item(item, "strategy_path", artifact_root)
    output_dir = _path_from_item(item, "output_dir", artifact_root)
    visual_index = artifact_dir / "analysis" / "visual_review" / "index.md" if artifact_dir else None

    for label, path in [
        ("artifact_dir", artifact_dir),
        ("human_review_path", human_review),
        ("strategy_path", strategy_path),
        ("output_dir", output_dir),
        ("visual_review/index.md", visual_index),
    ]:
        if path is not None:
            _check_inside_root(artifact_root, path, label, name, errors)

    if item.get("status") == "ok":
        _require_item_file(name, "human_review_path", human_review, errors)
        _require_item_file(name, "strategy_path", strategy_path, errors)
        if artifact_dir is None:
            errors.append(f"{name}: missing artifact_dir")
        else:
            visual_index = artifact_dir / "analysis" / "visual_review" / "index.md"
            _require_file(visual_index, errors)
        if output_dir is None:
            warnings.append(f"{name}: preview mode has no output_frames")
            return 0
        if artifact_dir is not None:
            _check_output_audit(name, output_dir, artifact_dir / "analysis" / "execution_audit.json", errors)
        return _check_output_provenance(name, frame_dir, output_dir, errors)
    else:
        if human_review is not None and not human_review.is_file():
            errors.append(f"{name}: missing failure human_review.md")
    return 0


def _require_item_file(name: str, label: str, path: Path | None, errors: list[str]) -> None:
    if path is None:
        errors.append(f"{name}: missing {label}")
        return
    _require_file(path, errors)


def _path_from_item(item: dict, key: str, artifact_root: Path) -> Path | None:
    value = item.get(key)
    if value is None or value == "":
        return None
    path = Path(str(value))
    if path.is_absolute():
        return path
    return artifact_root / path


def _check_inside_root(artifact_root: Path, path: Path, label: str, name: str, errors: list[str]) -> None:
    try:
        path.resolve().relative_to(artifact_root.resolve())
    except ValueError:
        errors.append(f"{name}: {label} is outside artifact_root: {path}")


def _check_output_audit(name: str, output_dir: Path, audit_path: Path, errors: list[str]) -> None:
    if not output_dir.is_dir():
        errors.append(f"{name}: missing output_dir: {output_dir}")
        return
    _check_output_frames_boundary(name, output_dir, errors)
    audit = _load_json(audit_path, errors)
    if not audit:
        return
    if audit.get("status") != "ok":
        errors.append(f"{name}: execution_audit status is {audit.get('status')}")
    image_count = _count_images(output_dir)
    for key in ["output_count", "manifest_output_count", "image_count"]:
        if int(audit.get(key, -1)) != image_count:
            errors.append(f"{name}: {key} mismatch with output images: audit={audit.get(key)}, images={image_count}")


def _check_output_frames_boundary(name: str, output_dir: Path, errors: list[str]) -> None:
    allowed_names = {"selected_frames.txt", "run_manifest.json"}
    for path in output_dir.iterdir():
        if not path.is_file():
            errors.append(f"{name}: unexpected output_frames directory entry: {path}")
            continue
        if path.name in allowed_names or path.suffix.lower() in SUPPORTED_EXTENSIONS:
            continue
        errors.append(f"{name}: unexpected output_frames file: {path}")


def _check_output_provenance(name: str, frame_dir: Path | None, output_dir: Path, errors: list[str]) -> int:
    hash_checked = _check_output_hash_provenance(name, output_dir, errors)
    if hash_checked is not None:
        return hash_checked

    if frame_dir is None:
        errors.append(f"{name}: missing frame_dir for output provenance check")
        return 0
    if not frame_dir.is_dir():
        errors.append(f"{name}: missing frame_dir for output provenance check: {frame_dir}")
        return 0
    if not output_dir.is_dir():
        return 0

    try:
        source_records = load_frame_records(frame_dir)
        output_records = load_frame_records(output_dir)
    except (FileNotFoundError, ValueError) as exc:
        errors.append(f"{name}: failed to load frames for output provenance check: {exc}")
        return 0

    source_by_index = {record.source_index: record.path for record in source_records}
    source_hashes: dict[int, str] = {}
    checked = 0
    for output_record in output_records:
        source_path = source_by_index.get(output_record.source_index)
        if source_path is None:
            errors.append(
                f"{name}: output frame source_index has no source frame: "
                f"source_index={output_record.source_index}, output={output_record.path.name}"
            )
            continue

        source_hash = source_hashes.get(output_record.source_index)
        if source_hash is None:
            source_hash = _file_sha256(source_path)
            source_hashes[output_record.source_index] = source_hash
        output_hash = _file_sha256(output_record.path)
        checked += 1
        if output_hash != source_hash:
            errors.append(
                f"{name}: output frame differs from source frame: "
                f"source_index={output_record.source_index}, output={output_record.path}, source={source_path}"
            )
    return checked


def _check_output_hash_provenance(name: str, output_dir: Path, errors: list[str]) -> int | None:
    expected_hashes = _load_selected_source_hashes(output_dir, errors)
    if expected_hashes is None:
        return None

    checked = 0
    for output_path, expected_hash in expected_hashes.items():
        if not output_path.is_file():
            errors.append(f"{name}: selected output frame is missing: {output_path.name}")
            continue
        checked += 1
        output_hash = _file_sha256(output_path)
        if output_hash != expected_hash:
            errors.append(
                f"{name}: output frame differs from recorded source hash: "
                f"output={output_path.name}, source_sha256={expected_hash}"
            )
    return checked


def _load_selected_source_hashes(output_dir: Path, errors: list[str]) -> dict[Path, str] | None:
    selected_path = output_dir / "selected_frames.txt"
    try:
        with selected_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
    except FileNotFoundError:
        errors.append(f"missing selected_frames.txt: {selected_path}")
        return {}

    if not rows or "source_sha256" not in rows[0]:
        return None

    expected: dict[Path, str] = {}
    for row in rows:
        source_hash = row.get("source_sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", source_hash):
            errors.append(f"invalid source_sha256 in selected_frames.txt for output={row.get('path', '')}")
            continue
        expected[output_dir / row["path"]] = source_hash
    return expected


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _count_images(output_dir: Path) -> int:
    return sum(1 for path in output_dir.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS)


def _check_markdown_links(path: Path, errors: list[str], warnings: list[str]) -> int:
    text = path.read_text(encoding="utf-8")
    checked = 0
    for _, raw_target in MARKDOWN_TARGET_PATTERN.findall(text):
        if raw_target.startswith(("http://", "https://", "mailto:")):
            continue
        if Path(raw_target).is_absolute():
            warnings.append(f"dashboard contains absolute link: {raw_target}")
            target = Path(raw_target)
        else:
            target = (path.parent / raw_target).resolve()
        checked += 1
        if not target.exists():
            errors.append(f"missing dashboard target: {raw_target}")
    return checked


def _write_health_artifacts(result: BatchArtifactHealthResult) -> None:
    result.report_path.parent.mkdir(parents=True, exist_ok=True)
    result.json_path.write_text(
        json.dumps(
            {
                "artifact_root": ".",
                "status": result.status,
                "checked_items": result.checked_items,
                "checked_links": result.checked_links,
                "checked_output_frames": result.checked_output_frames,
                "errors": result.errors,
                "warnings": result.warnings,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    result.report_path.write_text(_health_markdown(result), encoding="utf-8")


def _health_markdown(result: BatchArtifactHealthResult) -> str:
    lines = [
        "# 阶段 9：Agent 产物健康检查",
        "",
        f"- 状态：{result.status}",
        f"- 检查 item 数：{result.checked_items}",
        f"- 检查 dashboard 链接数：{result.checked_links}",
        f"- 检查输出帧溯源数：{result.checked_output_frames}",
        "- 产物根目录：当前 `output` batch 目录。",
        "",
        "## 错误",
    ]
    lines.extend([f"- {error}" for error in result.errors] if result.errors else ["- 无。"])
    lines.extend(["", "## 警告"])
    lines.extend([f"- {warning}" for warning in result.warnings] if result.warnings else ["- 无。"])
    lines.extend(
        [
            "",
            "## 说明",
            "- 本检查只读取并验证 output 内部产物，不修改原始帧目录。",
            "- 输出帧溯源检查要求 `output_frames` 中的每张图片与记录的源帧哈希字节级一致；重复帧只能复制同一源帧。",
            "- 状态为 ok 只代表本地 agent 产物结构和审计一致，不代表建模质量已经合格。",
            "",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check local batch timing agent artifacts.")
    parser.add_argument("--artifact_root", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_batch_artifact_health_check(args.artifact_root)
    print(f"Health status: {result.status}")
    print(f"Maintenance report: {result.report_path}")
    return 1 if result.status != "ok" else 0


if __name__ == "__main__":
    raise SystemExit(main())
