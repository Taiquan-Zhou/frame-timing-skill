from __future__ import annotations

from pathlib import Path
import csv
import hashlib
import io
import json
import shutil
import uuid

from frame_timing_agent.frame_source import load_frame_records
from frame_timing_agent.ui.view_model import ThumbnailView


SNAPSHOT_VERSION = 1
INPUT_SNAPSHOT_NAME = "input_snapshot.json"
THUMBNAIL_MANIFEST_NAME = "ui_thumbnails.json"
THUMBNAIL_DIR_NAME = "ui_thumbnails"


def capture_input_snapshot(
    frame_dir: Path | str,
    fps: float,
    limit_first_n: int | None,
) -> dict:
    resolved_frame_dir = Path(frame_dir).expanduser().resolve()
    records = load_frame_records(resolved_frame_dir, fps=fps, limit_first_n=limit_first_n)
    frames = []
    for record in records:
        path = record.path.expanduser().resolve()
        frames.append(
            {
                "source_index": record.source_index,
                "output_index": record.output_index,
                "instance_id": record.instance_id,
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        )
    return {
        "version": SNAPSHOT_VERSION,
        "frame_dir": str(resolved_frame_dir),
        "fps": float(fps),
        "limit_first_n": limit_first_n,
        "frames": frames,
    }


def write_input_snapshot(analysis_dir: Path | str, snapshot: dict) -> Path:
    path = Path(analysis_dir) / INPUT_SNAPSHOT_NAME
    _write_json_atomic(path, snapshot)
    return path


def bind_strategy_snapshot(snapshot: dict, strategy_path: Path | str) -> dict:
    bound = dict(snapshot)
    bound["strategy_sha256"] = _file_sha256(Path(strategy_path))
    return bound


def load_bound_strategy(analysis_dir: Path | str) -> dict:
    analysis_dir = Path(analysis_dir)
    snapshot = _read_snapshot(analysis_dir)
    expected_hash = snapshot.get("strategy_sha256")
    if not isinstance(expected_hash, str):
        raise ValueError("analysis snapshot is not bound to a strategy; run analysis again before exporting")
    strategy_path = analysis_dir / "strategy.json"
    try:
        payload = strategy_path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"analysis strategy is missing: {strategy_path}") from exc
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ValueError("analysis strategy changed since analysis; run analysis again before exporting")
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"analysis strategy is invalid: {strategy_path}: {exc}") from exc


def verify_input_snapshot(
    analysis_dir: Path | str,
    frame_dir: Path | str,
    fps: float,
    limit_first_n: int | None,
) -> bool:
    analysis_dir = Path(analysis_dir)
    expected = _read_snapshot(analysis_dir)
    strategy_sha256 = expected.pop("strategy_sha256", None)
    current = capture_input_snapshot(frame_dir, fps=fps, limit_first_n=limit_first_n)
    if current != expected:
        raise ValueError("input frames changed since analysis; run analysis again before exporting")
    if strategy_sha256 is None:
        raise ValueError("analysis snapshot is not bound to a strategy; run analysis again before exporting")
    strategy_path = analysis_dir / "strategy.json"
    if not strategy_path.is_file() or _file_sha256(strategy_path) != strategy_sha256:
        raise ValueError("analysis strategy changed since analysis; run analysis again before exporting")
    return True


def verify_output_snapshot(analysis_dir: Path | str, output_dir: Path | str) -> None:
    snapshot = _read_snapshot(Path(analysis_dir))
    source_hashes = {int(item["source_index"]): str(item["sha256"]) for item in snapshot.get("frames", [])}
    output_dir = Path(output_dir)
    selected_path = output_dir / "selected_frames.txt"
    try:
        rows = csv.DictReader(io.StringIO(selected_path.read_text(encoding="utf-8")), delimiter="\t")
        for row in rows:
            source_index = int(row["source_index"])
            filename = row["path"]
            if Path(filename).name != filename:
                raise ValueError(f"invalid output frame filename: {filename}")
            expected_hash = source_hashes.get(source_index)
            if expected_hash is None:
                raise ValueError(f"output references source outside analysis snapshot: {source_index}")
            output_path = output_dir / filename
            if not output_path.is_file() or _file_sha256(output_path) != expected_hash:
                raise ValueError(f"output frame does not match analysis snapshot: {filename}")
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("invalid output", "output ")):
            raise
        raise ValueError(f"invalid selected output manifest: {selected_path}: {exc}") from exc


def persist_thumbnails(
    analysis_dir: Path | str,
    thumbnails: tuple[ThumbnailView, ...],
) -> tuple[ThumbnailView, ...]:
    analysis_dir = Path(analysis_dir)
    thumbnail_dir = analysis_dir / THUMBNAIL_DIR_NAME
    thumbnail_dir.mkdir(parents=True, exist_ok=True)
    for path in thumbnail_dir.iterdir():
        if path.is_file():
            path.unlink()

    frozen: list[ThumbnailView] = []
    items = []
    for index, thumbnail in enumerate(thumbnails):
        filename = f"thumb_{index:02d}_src_{thumbnail.source_index:06d}{thumbnail.path.suffix.lower()}"
        destination = thumbnail_dir / filename
        shutil.copy2(thumbnail.path, destination)
        frozen_view = ThumbnailView(thumbnail.source_index, destination, thumbnail.operation)
        frozen.append(frozen_view)
        items.append(
            {
                "source_index": thumbnail.source_index,
                "operation": thumbnail.operation,
                "file": filename,
            }
        )

    _write_json_atomic(
        analysis_dir / THUMBNAIL_MANIFEST_NAME,
        {"version": SNAPSHOT_VERSION, "items": items},
    )
    return tuple(frozen)


def load_persisted_thumbnails(analysis_dir: Path | str) -> tuple[ThumbnailView, ...] | None:
    analysis_dir = Path(analysis_dir)
    manifest_path = analysis_dir / THUMBNAIL_MANIFEST_NAME
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if payload.get("version") != SNAPSHOT_VERSION or not isinstance(payload.get("items"), list):
            raise ValueError("unsupported thumbnail manifest")
        thumbnails = []
        for item in payload["items"]:
            filename = str(item["file"])
            if Path(filename).name != filename:
                raise ValueError(f"invalid thumbnail filename: {filename}")
            path = analysis_dir / THUMBNAIL_DIR_NAME / filename
            if not path.is_file():
                raise ValueError(f"persisted thumbnail is missing: {path}")
            thumbnails.append(ThumbnailView(int(item["source_index"]), path, str(item["operation"])))
        return tuple(thumbnails)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid thumbnail manifest: {manifest_path}: {exc}") from exc


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_snapshot(analysis_dir: Path) -> dict:
    path = analysis_dir / INPUT_SNAPSHOT_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"analysis input snapshot is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"analysis input snapshot is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"analysis input snapshot is invalid: {path}")
    return payload


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
