from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import argparse
import csv
import json
import os
import re
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frame_timing_agent.auto_timing_agent import TimingAgentResult, run_timing_agent


SAFE_NAME_PATTERN = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(frozen=True)
class BatchTimingItem:
    name: str
    frames: Path
    override_config_path: Path | None = None


@dataclass(frozen=True)
class BatchTimingConfig:
    items: list[BatchTimingItem]
    artifact_root: Path
    limit_first_n: int | None = 300
    mode: str = "reconstruction_balanced"
    write: bool = False
    fps: float | None = None
    override_config_path: Path | None = None


@dataclass(frozen=True)
class BatchTimingItemResult:
    name: str
    frame_dir: Path
    artifact_dir: Path
    analyzed_count: int
    estimated_output_count: int
    strategy_path: Path | None
    output_dir: Path | None
    human_review_path: Path
    status: str
    error: str = ""


@dataclass(frozen=True)
class BatchTimingResult:
    artifact_root: Path
    items: list[BatchTimingItemResult]
    summary_json_path: Path
    summary_csv_path: Path
    human_review_path: Path
    review_dashboard_path: Path

    @property
    def success_count(self) -> int:
        return sum(1 for item in self.items if item.status == "ok")

    @property
    def failure_count(self) -> int:
        return sum(1 for item in self.items if item.status != "ok")


def run_batch_timing_agent(
    items: Sequence[BatchTimingItem],
    artifact_root: Path | str,
    limit_first_n: int | None = 300,
    mode: str = "reconstruction_balanced",
    write: bool = False,
    fps: float | None = None,
    override_config_path: Path | str | None = None,
) -> BatchTimingResult:
    if not items:
        raise ValueError("at least one frame directory is required")

    artifact_root = Path(artifact_root)
    _validate_artifact_root(artifact_root)
    analysis_dir = artifact_root / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    normalized_items = _normalize_items(items, default_override_config_path=override_config_path)
    results: list[BatchTimingItemResult] = []
    for item in normalized_items:
        item_artifact_dir = artifact_root / item.name
        try:
            result = run_timing_agent(
                frames=item.frames,
                artifact_dir=item_artifact_dir,
                limit_first_n=limit_first_n,
                mode=mode,
                write=write,
                fps=fps,
                override_config_path=item.override_config_path,
            )
            results.append(_success_result(item, item_artifact_dir, result))
        except Exception as exc:  # Keep batch processing independent across directories.
            results.append(build_failure_result(item, item_artifact_dir, exc))

    return publish_batch_timing_reports(artifact_root, results, preview_only=not write)


def publish_batch_timing_reports(
    artifact_root: Path | str,
    results: Sequence[BatchTimingItemResult],
    *,
    preview_only: bool | None = None,
) -> BatchTimingResult:
    artifact_root = Path(artifact_root)
    analysis_dir = artifact_root / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    summary_json_path = analysis_dir / "batch_summary.json"
    summary_csv_path = analysis_dir / "batch_summary.csv"
    human_review_path = analysis_dir / "human_review.md"
    review_dashboard_path = analysis_dir / "review_dashboard.md"
    _write_summary_json(summary_json_path, artifact_root, results)
    _write_summary_csv(summary_csv_path, artifact_root, results)
    _write_batch_human_review(
        human_review_path,
        artifact_root,
        results,
        preview_only=all(item.output_dir is None for item in results) if preview_only is None else preview_only,
    )
    _write_review_dashboard(review_dashboard_path, artifact_root, results)
    from frame_timing_agent.batch_artifact_health import run_batch_artifact_health_check

    run_batch_artifact_health_check(artifact_root)
    return BatchTimingResult(
        artifact_root=artifact_root,
        items=list(results),
        summary_json_path=summary_json_path,
        summary_csv_path=summary_csv_path,
        human_review_path=human_review_path,
        review_dashboard_path=review_dashboard_path,
    )


def parse_frame_item(value: str) -> BatchTimingItem:
    if "=" in value:
        name, raw_path = value.split("=", 1)
        if not name.strip():
            raise ValueError(f"empty item name in --frames value: {value}")
        return BatchTimingItem(name=name.strip(), frames=Path(raw_path))

    path = Path(value)
    return BatchTimingItem(name=path.name, frames=path)


def load_batch_manifest(path: Path | str) -> BatchTimingConfig:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict):
        raise ValueError(f"batch manifest must be a JSON object: {path}")

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("batch manifest must contain a non-empty items list")

    items: list[BatchTimingItem] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, dict):
            raise ValueError(f"batch manifest item #{index} must be an object")
        if "name" not in raw_item or "frames" not in raw_item:
            raise ValueError(f"batch manifest item #{index} must contain name and frames")
        item_override = raw_item.get("override_config_path")
        items.append(
            BatchTimingItem(
                name=str(raw_item["name"]),
                frames=Path(raw_item["frames"]),
                override_config_path=Path(item_override) if item_override else None,
            )
        )

    raw_artifact_root = data.get("artifact_root")
    if not raw_artifact_root:
        raise ValueError("batch manifest must contain artifact_root")

    raw_override = data.get("override_config_path")
    return BatchTimingConfig(
        items=items,
        artifact_root=Path(raw_artifact_root),
        limit_first_n=data.get("limit_first_n", 300),
        mode=str(data.get("mode", "reconstruction_balanced")),
        write=bool(data.get("write", False)),
        fps=float(data["fps"]) if data.get("fps") is not None else None,
        override_config_path=Path(raw_override) if raw_override else None,
    )


def _normalize_items(
    items: Sequence[BatchTimingItem],
    default_override_config_path: Path | str | None,
) -> list[BatchTimingItem]:
    normalized: list[BatchTimingItem] = []
    used_names: set[str] = set()
    default_override = Path(default_override_config_path) if default_override_config_path is not None else None

    for item in items:
        safe_name = _safe_artifact_name(item.name)
        if safe_name in used_names:
            raise ValueError(f"duplicate batch item name after normalization: {safe_name}")
        used_names.add(safe_name)
        normalized.append(
            BatchTimingItem(
                name=safe_name,
                frames=Path(item.frames),
                override_config_path=Path(item.override_config_path)
                if item.override_config_path is not None
                else default_override,
            )
        )
    return normalized


def _safe_artifact_name(name: str) -> str:
    safe_name = SAFE_NAME_PATTERN.sub("_", name.strip()).strip("._-")
    if not safe_name:
        raise ValueError(f"invalid batch item name: {name}")
    return safe_name


def _validate_artifact_root(artifact_root: Path) -> None:
    if "output" not in {part.lower() for part in artifact_root.parts}:
        raise ValueError(f"artifact_root must be inside an output directory: {artifact_root}")


def _success_result(
    item: BatchTimingItem,
    item_artifact_dir: Path,
    result: TimingAgentResult,
) -> BatchTimingItemResult:
    return BatchTimingItemResult(
        name=item.name,
        frame_dir=item.frames,
        artifact_dir=item_artifact_dir,
        analyzed_count=result.analyzed_count,
        estimated_output_count=result.estimated_output_count,
        strategy_path=result.strategy_path,
        output_dir=result.output_dir,
        human_review_path=item_artifact_dir / "analysis" / "human_review.md",
        status="ok",
    )


def build_failure_result(item: BatchTimingItem, item_artifact_dir: Path, exc: Exception) -> BatchTimingItemResult:
    error = _public_error(exc, item.frames, item_artifact_dir)
    item_analysis_dir = item_artifact_dir / "analysis"
    human_review_path = item_analysis_dir / "human_review.md"
    try:
        item_analysis_dir.mkdir(parents=True, exist_ok=True)
        human_review_path.write_text(
            "\n".join(
                [
                    f"# 阶段 8 子任务失败：{item.name}",
                    "",
                    "## 结论",
                    "- 该帧目录没有成功生成策略产物。",
                    f"- 输入目录名：`{item.frames.name}`",
                    f"- 失败原因：`{error}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )
    except OSError:
        pass
    return BatchTimingItemResult(
        name=item.name,
        frame_dir=item.frames,
        artifact_dir=item_artifact_dir,
        analyzed_count=0,
        estimated_output_count=0,
        strategy_path=None,
        output_dir=None,
        human_review_path=human_review_path,
        status="failed",
        error=error,
    )


def _public_error(exc: Exception, frame_dir: Path, artifact_dir: Path | None = None) -> str:
    del exc, frame_dir, artifact_dir
    return "analysis_failed: frame analysis did not complete"


def _item_to_dict(item: BatchTimingItemResult, artifact_root: Path) -> dict:
    return {
        "name": item.name,
        "status": item.status,
        "input_name": item.frame_dir.name,
        "frame_dir": "",
        "artifact_dir": _artifact_relative_path(artifact_root, item.artifact_dir),
        "analyzed_count": item.analyzed_count,
        "estimated_output_count": item.estimated_output_count,
        "strategy_path": _artifact_relative_path(artifact_root, item.strategy_path),
        "output_dir": _artifact_relative_path(artifact_root, item.output_dir),
        "human_review_path": _artifact_relative_path(artifact_root, item.human_review_path)
        if _has_current_human_review(item)
        else "",
        "error": item.error,
    }


def _write_summary_json(path: Path, artifact_root: Path, results: Sequence[BatchTimingItemResult]) -> None:
    path.write_text(
        json.dumps(
            {
                "artifact_root": ".",
                "success_count": sum(1 for item in results if item.status == "ok"),
                "failure_count": sum(1 for item in results if item.status != "ok"),
                "items": [_item_to_dict(item, artifact_root) for item in results],
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _write_summary_csv(path: Path, artifact_root: Path, results: Sequence[BatchTimingItemResult]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "name",
                "status",
                "input_name",
                "analyzed_count",
                "estimated_output_count",
                "output_dir",
                "human_review_path",
                "error",
            ],
        )
        writer.writeheader()
        for item in results:
            row = _item_to_dict(item, artifact_root)
            writer.writerow({key: row[key] for key in writer.fieldnames})


def _artifact_relative_path(artifact_root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.resolve().relative_to(artifact_root.resolve()).as_posix()
    except ValueError:
        return path.name


def _has_current_human_review(item: BatchTimingItemResult) -> bool:
    if not item.human_review_path.is_file():
        return False
    if item.status == "ok":
        return True
    try:
        content = item.human_review_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return f"- 失败原因：`{item.error}`" in content


def _write_batch_human_review(
    path: Path,
    artifact_root: Path,
    results: Sequence[BatchTimingItemResult],
    preview_only: bool,
) -> None:
    success_count = sum(1 for item in results if item.status == "ok")
    failure_count = len(results) - success_count
    written_count = sum(1 for item in results if item.output_dir is not None)
    lines = [
        "# 阶段 6：批处理配置化与路径安全",
        "",
        "## 1. 本阶段结论",
        "- 本阶段只做本地帧节奏策略批处理，不上传云端、不训练、不修改图片内容。",
        "- 阶段 8 审查入口：`review_dashboard.md`。",
        f"- 当前模式：{'预览模式，未写出 output_frames' if preview_only else f'执行模式，已为 {written_count} 个项写出 output_frames'}。",
        f"- 批处理项数量：{len(results)}",
        f"- 成功：{success_count}",
        f"- 失败：{failure_count}",
        "- 产物根目录：当前 `output` batch 目录。",
        "",
        "## 2. 批处理结果表",
        "",
        "| 名称 | 状态 | 输入帧数 | 预计输出帧数 | 输入目录名 | 子审查报告 |",
        "|---|---|---:|---:|---|---|",
    ]
    for item in results:
        review_path = (
            _artifact_relative_path(artifact_root, item.human_review_path) if _has_current_human_review(item) else ""
        )
        review_label = f"`{review_path}`" if review_path else "无"
        lines.append(
            "| "
            f"{item.name} | "
            f"{item.status} | "
            f"{item.analyzed_count} | "
            f"{item.estimated_output_count} | "
            f"`{item.frame_dir.name}` | "
            f"{review_label} |"
        )

    failed = [item for item in results if item.status != "ok"]
    if failed:
        lines.extend(["", "## 3. 失败项"])
        for item in failed:
            lines.append(f"- {item.name}: `{item.error}`")
    else:
        lines.extend(["", "## 3. 失败项", "- 无。"])

    lines.extend(
        [
            "",
            "## 4. 你需要审查什么",
            "- 先打开每个子目录的 `analysis/human_review.md`，核对 source_index 区间、重复倍数、静止段保留帧。",
            "- 再检查 `output_frames` 是否只在对应子目录内生成，原始帧目录不应新增或改动图片。",
            "- 确认后再进入下一阶段；不确认则继续优化本地策略。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_review_dashboard(path: Path, artifact_root: Path, results: Sequence[BatchTimingItemResult]) -> None:
    lines = [
        "# 阶段 8：批处理审查总览",
        "",
        "## 结论",
        "- 这是批处理的人工审查入口，聚合每个帧目录的策略报告和可视化拼图。",
        "- 本报告不修改图片、不生成建模输入，只链接已经生成的审查产物。",
        "- 产物根目录：本文件所在 batch 目录的上一级。",
        "",
        "## 总览",
        "",
        "| 名称 | 状态 | 输入帧数 | 输出帧数 | 子报告 | 可视化索引 |",
        "|---|---|---:|---:|---|---|",
    ]
    for item in results:
        visual_index = item.artifact_dir / "analysis" / "visual_review" / "index.md"
        human_review_link = (
            _markdown_link(path, item.human_review_path, "human_review.md") if _has_current_human_review(item) else "无"
        )
        lines.append(
            "| "
            f"{item.name} | "
            f"{item.status} | "
            f"{item.analyzed_count} | "
            f"{item.estimated_output_count} | "
            f"{human_review_link} | "
            f"{_markdown_link(path, visual_index, 'visual_review/index.md') if visual_index.exists() else '无'} |"
        )

    lines.extend(["", "## 可视化拼图"])
    for item in results:
        lines.extend(["", f"### {item.name}", ""])
        visual_dir = item.artifact_dir / "analysis" / "visual_review"
        visual_index = visual_dir / "index.md"
        if item.status != "ok":
            lines.append(f"- 失败原因：`{item.error}`")
            continue
        if not visual_index.exists():
            lines.append("- 未生成可视化索引。")
            continue
        contact_sheets = sorted(visual_dir.glob("contact_*.png"))
        if not contact_sheets:
            lines.append("- 本样本没有检测到需要处理的策略区间。")
            continue
        for sheet in contact_sheets:
            relative = _relative_markdown_path(path, sheet)
            lines.append(f"![{item.name} {sheet.stem}]({relative})")
            lines.append("")

    lines.extend(
        [
            "## 审查顺序",
            "",
            "1. 先看上方拼图，判断策略区间是否明显合理。",
            "2. 再打开对应 `visual_review/index.md` 看 source_index 代表帧。",
            "3. 最后打开 `human_review.md` 核对输入帧数、输出帧数和策略来源。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _markdown_link(current_file: Path, target: Path, label: str) -> str:
    return f"[{label}]({_relative_markdown_path(current_file, target)})"


def _relative_markdown_path(current_file: Path, target: Path) -> str:
    return Path(os.path.relpath(target, start=current_file.parent)).as_posix()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Batch run the local extracted-frame timing agent.")
    parser.add_argument("--manifest", type=Path, default=None, help="JSON batch manifest.")
    parser.add_argument(
        "--frames", action="append", default=None, help="Frame directory, optionally named as name=path."
    )
    parser.add_argument("--artifact_root", type=Path)
    parser.add_argument("--limit_first_n", type=int, default=300)
    parser.add_argument("--mode", default="reconstruction_balanced", choices=["reconstruction_balanced"])
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--override_config", type=Path, default=None)
    parser.add_argument("--write", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.manifest is not None:
        config = load_batch_manifest(args.manifest)
        items = config.items
        artifact_root = config.artifact_root
        limit_first_n = config.limit_first_n
        mode = config.mode
        write = config.write
        fps = config.fps
        override_config_path = config.override_config_path
    else:
        if not args.frames:
            raise SystemExit("--frames is required unless --manifest is provided")
        if args.artifact_root is None:
            raise SystemExit("--artifact_root is required unless --manifest is provided")
        items = [parse_frame_item(value) for value in args.frames]
        artifact_root = args.artifact_root
        limit_first_n = args.limit_first_n
        mode = args.mode
        write = args.write
        fps = args.fps
        override_config_path = args.override_config

    result = run_batch_timing_agent(
        items,
        artifact_root=artifact_root,
        limit_first_n=limit_first_n,
        mode=mode,
        write=write,
        fps=fps,
        override_config_path=override_config_path,
    )
    for item in result.items:
        prefix = "OK" if item.status == "ok" else "FAIL"
        print(f"{prefix}: {item.name} analyzed={item.analyzed_count} estimated_output={item.estimated_output_count}")
        if item.error:
            print(f"  error: {item.error}")
    print(f"Batch human review: {result.human_review_path}")
    return 1 if result.failure_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
