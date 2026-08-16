from __future__ import annotations

from pathlib import Path
import json
import shutil
import uuid

from frame_timing_agent.run_workflow import (
    INPUT_SNAPSHOT_NAME,
    SNAPSHOT_VERSION,
    bind_strategy_snapshot,
    capture_input_snapshot,
    load_bound_strategy,
    verify_input_snapshot,
    verify_output_snapshot,
    write_input_snapshot,
)
from frame_timing_agent.ui.view_model import ThumbnailView


THUMBNAIL_MANIFEST_NAME = "ui_thumbnails.json"
THUMBNAIL_DIR_NAME = "ui_thumbnails"

__all__ = [
    "INPUT_SNAPSHOT_NAME",
    "SNAPSHOT_VERSION",
    "THUMBNAIL_DIR_NAME",
    "THUMBNAIL_MANIFEST_NAME",
    "bind_strategy_snapshot",
    "capture_input_snapshot",
    "load_bound_strategy",
    "load_persisted_thumbnails",
    "persist_thumbnails",
    "verify_input_snapshot",
    "verify_output_snapshot",
    "write_input_snapshot",
]


def persist_thumbnails(
    analysis_dir: Path | str,
    thumbnails: tuple[ThumbnailView, ...],
) -> tuple[ThumbnailView, ...]:
    analysis_dir = Path(analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    thumbnail_dir = analysis_dir / THUMBNAIL_DIR_NAME
    staging_dir = analysis_dir / f".{THUMBNAIL_DIR_NAME}.staging-{uuid.uuid4().hex}"
    staging_dir.mkdir()

    frozen: list[ThumbnailView] = []
    items = []
    try:
        for index, thumbnail in enumerate(thumbnails):
            filename = f"thumb_{index:02d}_src_{thumbnail.source_index:06d}{thumbnail.path.suffix.lower()}"
            shutil.copy2(thumbnail.path, staging_dir / filename)
            frozen.append(
                ThumbnailView(
                    thumbnail.source_index,
                    thumbnail_dir / filename,
                    thumbnail.operation,
                )
            )
            items.append(
                {
                    "source_index": thumbnail.source_index,
                    "operation": thumbnail.operation,
                    "file": filename,
                }
            )

        _replace_thumbnail_snapshot(
            staging_dir,
            thumbnail_dir,
            analysis_dir / THUMBNAIL_MANIFEST_NAME,
            {"version": SNAPSHOT_VERSION, "items": items},
        )
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return tuple(frozen)


def _replace_thumbnail_snapshot(
    staging_dir: Path,
    thumbnail_dir: Path,
    manifest_path: Path,
    manifest: dict,
) -> None:
    backup_dir = thumbnail_dir.with_name(f".{thumbnail_dir.name}.backup-{uuid.uuid4().hex}")
    had_previous = thumbnail_dir.is_dir()
    installed_new = False
    try:
        if had_previous:
            thumbnail_dir.replace(backup_dir)
        staging_dir.replace(thumbnail_dir)
        installed_new = True
        _write_json_atomic(manifest_path, manifest)
    except Exception:
        if installed_new:
            shutil.rmtree(thumbnail_dir, ignore_errors=True)
        if had_previous and backup_dir.is_dir():
            backup_dir.replace(thumbnail_dir)
        raise
    finally:
        shutil.rmtree(backup_dir, ignore_errors=True)


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
