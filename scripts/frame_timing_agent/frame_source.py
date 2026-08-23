from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

from frame_timing_agent.contracts import AnalysisError

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}
FRAME_WITH_SOURCE_PATTERN = re.compile(r"^frame_(\d+)_src_(\d+)(?:_dup_\d+)?$", re.IGNORECASE)
FRAME_PATTERN = re.compile(r"^frame_(\d+)$", re.IGNORECASE)


def normalize_fps(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AnalysisError("fps must be positive and finite", code="invalid_fps", fields=("fps",))
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise AnalysisError("fps must be positive and finite", code="invalid_fps", fields=("fps",)) from exc
    if not math.isfinite(normalized) or normalized <= 0:
        raise AnalysisError("fps must be positive and finite", code="invalid_fps", fields=("fps",))
    return normalized


@dataclass(frozen=True)
class FrameRecord:
    source_index: int
    output_index: int
    timestamp_sec: float
    path: Path
    instance_id: int = 0
    is_duplicate: bool = False


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y"}


def _parse_source_index_from_path(path: Path) -> int:
    stem = path.stem
    match = FRAME_WITH_SOURCE_PATTERN.match(stem)
    if match:
        return int(match.group(2))

    match = FRAME_PATTERN.match(stem)
    if match:
        return int(match.group(1))

    raise ValueError(f"unsupported frame filename: {path.name}")


def _iter_frame_paths(frame_dir: Path) -> list[Path]:
    return [
        path
        for path in frame_dir.iterdir()
        if not path.is_symlink() and path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    ]


def _build_filename_records(frame_dir: Path, fps: float) -> list[FrameRecord]:
    records: list[FrameRecord] = []
    for path in _iter_frame_paths(frame_dir):
        source_index = _parse_source_index_from_path(path)
        records.append(
            FrameRecord(
                source_index=source_index,
                output_index=0,
                timestamp_sec=source_index / fps,
                path=path,
            )
        )
    return records


def _build_selected_records(frame_dir: Path, fps: float) -> list[FrameRecord]:
    selected_path = frame_dir / "selected_frames.txt"
    records: list[FrameRecord] = []
    with selected_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            raw_path = row["path"]
            path = _resolve_selected_frame_path(frame_dir, raw_path)
            if not path.is_file():
                raise FileNotFoundError(f"selected frame path does not exist: {path}")
            if path.is_symlink():
                raise ValueError(f"selected frame path must not be a symbolic link: {path}")

            source_index = int(row["source_index"])
            parsed_source_index = _parse_source_index_from_path(path)
            if source_index != parsed_source_index:
                raise ValueError(
                    f"source_index mismatch for {path.name}: selected_frames.txt={source_index}, filename={parsed_source_index}"
                )

            timestamp_value = row.get("timestamp_sec", "")
            timestamp_sec = float(timestamp_value) if timestamp_value else source_index / fps
            output_value = row.get("output_index", "")
            instance_value = row.get("instance_id", "")
            duplicate_value = row.get("is_duplicate", "")
            records.append(
                FrameRecord(
                    source_index=source_index,
                    output_index=int(output_value) if output_value else len(records),
                    timestamp_sec=timestamp_sec,
                    path=path,
                    instance_id=int(instance_value) if instance_value else 0,
                    is_duplicate=_parse_bool(duplicate_value) if duplicate_value else False,
                )
            )
    return records


def _resolve_selected_frame_path(frame_dir: Path, raw_path: str) -> Path:
    path = Path(raw_path)
    if path.is_absolute():
        return path

    candidates = [frame_dir / path]
    candidates.extend(parent / path for parent in frame_dir.parents)
    candidates.append(frame_dir / path.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def _renumber_records(records: list[FrameRecord]) -> list[FrameRecord]:
    return [
        FrameRecord(
            source_index=record.source_index,
            output_index=output_index,
            timestamp_sec=record.timestamp_sec,
            path=record.path,
            instance_id=record.instance_id,
            is_duplicate=record.is_duplicate,
        )
        for output_index, record in enumerate(records)
    ]


def load_frame_records(frame_dir: Path | str, fps: float = 30.0, limit_first_n: int | None = None) -> list[FrameRecord]:
    frame_dir = Path(frame_dir)
    if not frame_dir.exists():
        raise FileNotFoundError(f"frame directory does not exist: {frame_dir}")
    fps = normalize_fps(fps)

    selected_path = frame_dir / "selected_frames.txt"

    if selected_path.exists():
        records = _build_selected_records(frame_dir, fps=fps)
        records.sort(key=lambda record: record.output_index)
        if limit_first_n is not None:
            records = records[:limit_first_n]
        return records
    else:
        records = _build_filename_records(frame_dir, fps=fps)

    records.sort(key=lambda record: record.source_index)
    if limit_first_n is not None:
        records = records[:limit_first_n]
    return _renumber_records(records)
