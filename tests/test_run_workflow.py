import json
import os
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from frame_timing_agent import run_workflow
from frame_timing_agent.run_workflow import StaleSourceError, RunSettings, analyze_run, export_run


def _write_frame(path, value: int) -> None:
    image = np.full((16, 16, 3), value, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write test frame: {path}")


def make_settings_with_frames(tmp_path) -> RunSettings:
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    for index in range(6):
        _write_frame(frame_dir / f"frame_{index:06d}_src_{index:06d}.jpg", 80 + index)
    return RunSettings(frame_dir=frame_dir, artifact_dir=tmp_path / "run", fps=24.0, limit_first_n=None)


def make_analyzed_settings(tmp_path) -> RunSettings:
    settings = make_settings_with_frames(tmp_path)
    analyze_run(settings)
    return settings


def mutate_first_source_frame(frame_dir) -> None:
    _write_frame(frame_dir / "frame_000000_src_000000.jpg", 255)


def make_directory_link(link, target) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip("directory junctions are unavailable")
        return
    link.symlink_to(target, target_is_directory=True)


def test_analyze_run_binds_source_and_strategy(tmp_path):
    settings = make_settings_with_frames(tmp_path)

    result = analyze_run(settings)

    snapshot = json.loads((result.artifact_dir / "analysis" / "input_snapshot.json").read_text())
    assert snapshot["strategy_sha256"]
    assert {"timestamp_sec", "is_duplicate"}.issubset(snapshot["frames"][0])


def test_analyze_rejects_manifest_frames_inside_artifact_write_area(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    artifact_dir = tmp_path / "run"
    source = artifact_dir / "output_frames" / "frame_000000_src_000000.jpg"
    source.parent.mkdir(parents=True)
    _write_frame(source, 80)
    (frame_dir / "selected_frames.txt").write_text(
        "source_index\toutput_index\ttimestamp_sec\tpath\n"
        f"0\t0\t0.0\t{source}\n",
        encoding="utf-8",
    )
    settings = RunSettings(frame_dir=frame_dir, artifact_dir=artifact_dir, fps=24.0)

    with pytest.raises(ValueError, match="source frame path must not overlap"):
        analyze_run(settings)

    assert source.is_file()


def test_missing_source_is_reported_as_stale_after_analysis(tmp_path):
    settings = make_analyzed_settings(tmp_path)
    next(settings.frame_dir.glob("*.jpg")).unlink()

    with pytest.raises(StaleSourceError, match="input frames changed"):
        run_workflow.verify_input_snapshot(
            settings.artifact_dir / "analysis",
            settings.frame_dir,
            settings.fps,
            settings.limit_first_n,
        )


def test_manifest_redirect_into_artifact_is_reported_as_stale_after_analysis(tmp_path):
    settings = make_analyzed_settings(tmp_path)
    source = settings.artifact_dir / "output_frames" / "frame_000000_src_000000.jpg"
    source.parent.mkdir()
    _write_frame(source, 80)
    (settings.frame_dir / "selected_frames.txt").write_text(
        "source_index\toutput_index\ttimestamp_sec\tpath\n"
        f"0\t0\t0.0\t{source}\n",
        encoding="utf-8",
    )

    with pytest.raises(StaleSourceError, match="input frames changed"):
        run_workflow.verify_input_snapshot(
            settings.artifact_dir / "analysis",
            settings.frame_dir,
            settings.fps,
            settings.limit_first_n,
        )


def test_export_run_keeps_previous_output_when_source_changed(tmp_path):
    settings = make_analyzed_settings(tmp_path)
    existing = settings.artifact_dir / "output_frames" / "sentinel.txt"
    existing.parent.mkdir()
    existing.write_text("keep", encoding="utf-8")
    mutate_first_source_frame(settings.frame_dir)

    with pytest.raises(StaleSourceError, match="input frames changed"):
        export_run(settings)

    assert existing.read_text(encoding="utf-8") == "keep"


def test_export_run_rejects_final_output_that_overlaps_input_directory(tmp_path):
    frame_dir = tmp_path / "unsafe-run" / "output_frames"
    frame_dir.mkdir(parents=True)
    for index in range(6):
        _write_frame(frame_dir / f"frame_{index:06d}_src_{index:06d}.jpg", 80 + index)

    safe_settings = RunSettings(frame_dir=frame_dir, artifact_dir=tmp_path / "safe-run", fps=24.0)
    analyze_run(safe_settings)
    unsafe_settings = RunSettings(frame_dir=frame_dir, artifact_dir=frame_dir.parent, fps=24.0)
    shutil.copytree(safe_settings.artifact_dir / "analysis", unsafe_settings.artifact_dir / "analysis")
    original_names = sorted(path.name for path in frame_dir.iterdir())

    with pytest.raises(ValueError, match="artifact directory must not overlap"):
        export_run(unsafe_settings)

    assert sorted(path.name for path in frame_dir.iterdir()) == original_names


def test_analyze_run_rejects_analysis_link_into_input_directory(tmp_path):
    settings = make_settings_with_frames(tmp_path)
    settings.artifact_dir.mkdir()
    analysis_link = settings.artifact_dir / "analysis"
    make_directory_link(analysis_link, settings.frame_dir)
    try:
        with pytest.raises(ValueError, match="artifact write path must stay"):
            analyze_run(settings)
    finally:
        analysis_link.rmdir()


def test_output_rollback_restores_previous_output_when_cleanup_fails(tmp_path, monkeypatch):
    output_dir = tmp_path / "output_frames"
    output_dir.mkdir()
    (output_dir / "old.txt").write_text("old", encoding="utf-8")
    staging_dir = tmp_path / ".output_frames.staging"
    staging_dir.mkdir()
    (staging_dir / "new.txt").write_text("new", encoding="utf-8")
    original_rmtree = run_workflow.shutil.rmtree

    def fail_formal_output_cleanup(path, *args, **kwargs):
        if Path(path) == output_dir:
            raise PermissionError("output file is held open")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(run_workflow.shutil, "rmtree", fail_formal_output_cleanup)

    with pytest.raises(RuntimeError, match="metadata failed"):
        run_workflow._replace_output_directory(
            staging_dir,
            output_dir,
            lambda: (_ for _ in ()).throw(RuntimeError("metadata failed")),
        )

    assert (output_dir / "old.txt").read_text(encoding="utf-8") == "old"
    assert not (output_dir / "new.txt").exists()


def test_output_rollback_preserves_both_copies_when_restore_renames_fail(tmp_path, monkeypatch):
    output_dir = tmp_path / "output_frames"
    output_dir.mkdir()
    (output_dir / "old.txt").write_text("old", encoding="utf-8")
    staging_dir = tmp_path / ".output_frames.staging"
    staging_dir.mkdir()
    (staging_dir / "new.txt").write_text("new", encoding="utf-8")
    original_rename = Path.rename

    def fail_restore_renames(path: Path, target: Path):
        if path.name.startswith((".output_frames.backup-", ".output_frames.failed-")):
            raise PermissionError("restore rename failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_restore_renames)

    with pytest.raises(PermissionError, match="restore rename failed"):
        run_workflow._replace_output_directory(
            staging_dir,
            output_dir,
            lambda: (_ for _ in ()).throw(RuntimeError("metadata failed")),
        )

    backups = list(tmp_path.glob(".output_frames.backup-*"))
    failed_outputs = list(tmp_path.glob(".output_frames.failed-*"))
    assert len(backups) == 1
    assert len(failed_outputs) == 1
    assert (backups[0] / "old.txt").read_text(encoding="utf-8") == "old"
    assert (failed_outputs[0] / "new.txt").read_text(encoding="utf-8") == "new"


def test_audit_rollback_preserves_backups_when_restore_fails(tmp_path, monkeypatch):
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    staging_dir = tmp_path / "audit-staging"
    staging_dir.mkdir()
    for name in ("execution_audit.json", "execution_audit.md"):
        (analysis_dir / name).write_text(f"old-{name}", encoding="utf-8")
        (staging_dir / name).write_text(f"new-{name}", encoding="utf-8")
    original_rename = Path.rename

    def fail_commit_and_restore(path: Path, target: Path):
        if path == staging_dir / "execution_audit.md":
            raise PermissionError("audit commit failed")
        if path.parent.name.startswith(".execution_audit.backup-") and path.name == "execution_audit.json":
            raise PermissionError("audit restore failed")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_commit_and_restore)

    with pytest.raises(PermissionError, match="audit restore failed"):
        run_workflow._replace_execution_audit(staging_dir, analysis_dir)

    backups = list(analysis_dir.glob(".execution_audit.backup-*"))
    failed = list(analysis_dir.glob(".execution_audit.failed-*"))
    assert len(backups) == 1
    assert len(failed) == 1
    assert (backups[0] / "execution_audit.json").read_text(encoding="utf-8").startswith("old-")
    assert (failed[0] / "execution_audit.json").read_text(encoding="utf-8").startswith("new-")
