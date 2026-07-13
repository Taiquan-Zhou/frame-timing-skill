from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
import argparse
import json
import sys

import cv2
import numpy as np


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frame_timing_agent.frame_source import FrameRecord, load_frame_records
from frame_timing_agent.image_io import read_image, write_image


@dataclass(frozen=True)
class VisualOperationResult:
    operation_index: int
    op: str
    start: int
    end: int
    sample_sources: list[int]
    contact_sheet: Path | None


@dataclass(frozen=True)
class StrategyVisualReviewResult:
    index_path: Path
    contact_sheets: list[Path]
    operation_count: int
    operations: list[VisualOperationResult]


def write_strategy_visual_review(
    frame_dir: Path | str,
    analysis_dir: Path | str,
    strategy: dict,
    max_samples_per_operation: int = 6,
    tile_width: int = 180,
    fps: float = 30.0,
) -> StrategyVisualReviewResult:
    if max_samples_per_operation <= 0:
        raise ValueError(f"max_samples_per_operation must be positive: {max_samples_per_operation}")
    if tile_width <= 0:
        raise ValueError(f"tile_width must be positive: {tile_width}")

    frame_dir = Path(frame_dir)
    analysis_dir = Path(analysis_dir)
    visual_dir = analysis_dir / "visual_review"
    visual_dir.mkdir(parents=True, exist_ok=True)

    records = load_frame_records(frame_dir, fps=fps)
    records_by_source = {record.source_index: record for record in records}
    operation_results: list[VisualOperationResult] = []
    contact_sheets: list[Path] = []

    for operation_index, operation in enumerate(strategy.get("operations", [])):
        source_range = operation.get("range", {})
        if "start" not in source_range or "end" not in source_range:
            continue
        start = int(source_range["start"])
        end = int(source_range["end"])
        range_records = [
            record
            for record in sorted(records_by_source.values(), key=lambda item: item.source_index)
            if start <= record.source_index <= end
        ]
        sample_records = _sample_records(range_records, max_samples_per_operation)
        op = str(operation.get("op", "unknown"))
        if sample_records:
            sheet_path = visual_dir / f"contact_{operation_index:03d}_{_safe_name(op)}_{start}_{end}.png"
            _write_contact_sheet(sheet_path, sample_records, tile_width=tile_width)
            contact_sheets.append(sheet_path)
        else:
            sheet_path = None
        operation_results.append(
            VisualOperationResult(
                operation_index=operation_index,
                op=op,
                start=start,
                end=end,
                sample_sources=[record.source_index for record in sample_records],
                contact_sheet=sheet_path,
            )
        )

    index_path = visual_dir / "index.md"
    _write_index(index_path, frame_dir, operation_results)
    return StrategyVisualReviewResult(
        index_path=index_path,
        contact_sheets=contact_sheets,
        operation_count=len(operation_results),
        operations=operation_results,
    )


def _sample_records(records: Sequence[FrameRecord], count: int) -> list[FrameRecord]:
    if len(records) <= count:
        return list(records)
    if count == 1:
        return [records[0]]
    positions = [round(index * (len(records) - 1) / (count - 1)) for index in range(count)]
    sampled: list[FrameRecord] = []
    seen_positions: set[int] = set()
    for position in positions:
        if position not in seen_positions:
            sampled.append(records[position])
            seen_positions.add(position)
    return sampled


def _write_contact_sheet(path: Path, records: Sequence[FrameRecord], tile_width: int) -> None:
    tiles = []
    for record in records:
        image = read_image(record.path, cv2.IMREAD_COLOR)
        if image is None:
            continue
        tile = _resize_to_width(image, tile_width)
        tile = _add_label_band(tile, f"src {record.source_index}")
        tiles.append(tile)
    if not tiles:
        raise ValueError(f"no readable frames for contact sheet: {path}")

    height = max(tile.shape[0] for tile in tiles)
    normalized_tiles = [_pad_to_height(tile, height) for tile in tiles]
    sheet = np.concatenate(normalized_tiles, axis=1)
    ok = write_image(path, sheet)
    if not ok:
        raise ValueError(f"failed to write contact sheet: {path}")


def _resize_to_width(image: np.ndarray, tile_width: int) -> np.ndarray:
    height, width = image.shape[:2]
    scale = tile_width / width
    target_height = max(1, round(height * scale))
    return cv2.resize(image, (tile_width, target_height), interpolation=cv2.INTER_AREA)


def _add_label_band(image: np.ndarray, label: str) -> np.ndarray:
    band_height = 24
    band = np.full((band_height, image.shape[1], 3), 255, dtype=np.uint8)
    cv2.putText(band, label, (6, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.48, (30, 30, 30), 1, cv2.LINE_AA)
    return np.concatenate([band, image], axis=0)


def _pad_to_height(image: np.ndarray, height: int) -> np.ndarray:
    if image.shape[0] == height:
        return image
    pad_height = height - image.shape[0]
    padding = np.full((pad_height, image.shape[1], 3), 255, dtype=np.uint8)
    return np.concatenate([image, padding], axis=0)


def _write_index(path: Path, frame_dir: Path, operations: Sequence[VisualOperationResult]) -> None:
    lines = [
        "# 阶段 7：策略可视化审查",
        "",
        "## 结论",
        "- 这些图片只用于人工审查策略区间，不作为建模输入。",
        "- 每张 contact sheet 从对应 source_index 区间均匀抽取代表帧。",
        f"- 输入帧目录：`{frame_dir}`",
        "",
        "## 策略区间",
        "",
        "| 序号 | 区间 | 策略 | 代表 source_index | 图片 |",
        "|---:|---|---|---|---|",
    ]
    for result in operations:
        image_name = result.contact_sheet.name if result.contact_sheet is not None else ""
        image_link = f"[{image_name}]({image_name})" if image_name else "无可读帧"
        lines.append(
            "| "
            f"{result.operation_index} | "
            f"{result.start}-{result.end} | "
            f"{result.op} | "
            f"{', '.join(str(source) for source in result.sample_sources)} | "
            f"{image_link} |"
        )
    if not operations:
        lines.append("| - | - | 无策略区间 | - | - |")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in value).strip("_") or "operation"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate visual contact sheets for timing strategy review.")
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--analysis_dir", required=True, type=Path)
    parser.add_argument("--strategy", required=True, type=Path)
    parser.add_argument("--max_samples_per_operation", type=int, default=6)
    parser.add_argument("--tile_width", type=int, default=180)
    parser.add_argument("--fps", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    strategy = json.loads(args.strategy.read_text(encoding="utf-8"))
    result = write_strategy_visual_review(
        frame_dir=args.frames,
        analysis_dir=args.analysis_dir,
        strategy=strategy,
        max_samples_per_operation=args.max_samples_per_operation,
        tile_width=args.tile_width,
        fps=args.fps,
    )
    print(f"Visual review: {result.index_path}")
    print(f"Contact sheets: {len(result.contact_sheets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
