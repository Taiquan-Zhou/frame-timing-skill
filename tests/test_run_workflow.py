import json
import os
import shutil
import subprocess
import sys
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


def remove_directory_link(link) -> None:
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


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
        f"source_index\toutput_index\ttimestamp_sec\tpath\n0\t0\t0.0\t{source}\n",
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


def test_v1_snapshot_without_additive_frame_metadata_remains_export_compatible(tmp_path):
    settings = make_analyzed_settings(tmp_path)
    snapshot_path = settings.artifact_dir / "analysis" / "input_snapshot.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for frame in snapshot["frames"]:
        frame.pop("timestamp_sec")
        frame.pop("is_duplicate")
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    assert run_workflow.verify_input_snapshot(
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
        f"source_index\toutput_index\ttimestamp_sec\tpath\n0\t0\t0.0\t{source}\n",
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
        remove_directory_link(analysis_link)


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_verified_output_rolls_back_output_and_audit_on_base_exception(tmp_path, monkeypatch, interrupt_type):
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    output_dir = tmp_path / "output_frames"
    output_dir.mkdir()
    (output_dir / "old.txt").write_text("old-output", encoding="utf-8")
    for name in ("execution_audit.json", "execution_audit.md"):
        (analysis_dir / name).write_text(f"old-{name}", encoding="utf-8")
    staging_dir = tmp_path / ".output_frames.staging"
    staging_dir.mkdir()
    (staging_dir / "new.txt").write_text("new-output", encoding="utf-8")
    original_rename = Path.rename
    interrupted = False

    def interrupt_during_audit_commit(path: Path, target: Path):
        nonlocal interrupted
        if not interrupted and path.name == "execution_audit.md" and path.parent.name.startswith(".execution_audit."):
            interrupted = True
            raise interrupt_type()
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", interrupt_during_audit_commit)

    with pytest.raises(interrupt_type):
        run_workflow.replace_verified_output(
            staging_dir,
            output_dir,
            analysis_dir,
            {"status": "ok", "errors": [], "warnings": [], "operation_results": []},
        )

    assert (output_dir / "old.txt").read_text(encoding="utf-8") == "old-output"
    assert not (output_dir / "new.txt").exists()
    for name in ("execution_audit.json", "execution_audit.md"):
        assert (analysis_dir / name).read_text(encoding="utf-8") == f"old-{name}"
    assert not (tmp_path / run_workflow.EXPORT_TRANSACTION_NAME).exists()
    assert not (tmp_path / run_workflow.OUTPUT_TRANSACTION_BACKUP_NAME).exists()


def test_pending_verified_output_transaction_recovers_after_process_exit(tmp_path):
    artifact_dir = tmp_path / "run"
    analysis_dir = artifact_dir / "analysis"
    analysis_dir.mkdir(parents=True)
    output_dir = artifact_dir / "output_frames"
    output_dir.mkdir()
    (output_dir / "old.txt").write_text("old-output", encoding="utf-8")
    for name in ("execution_audit.json", "execution_audit.md"):
        (analysis_dir / name).write_text(f"old-{name}", encoding="utf-8")
    staging_dir = artifact_dir / ".output_frames.staging"
    staging_dir.mkdir()
    (staging_dir / "new.txt").write_text("new-output", encoding="utf-8")

    script = f"""
import os
from pathlib import Path
from frame_timing_agent import run_workflow

artifact_dir = Path({str(artifact_dir)!r})
analysis_dir = artifact_dir / "analysis"
output_dir = artifact_dir / "output_frames"
staging_dir = artifact_dir / ".output_frames.staging"
original_rename = Path.rename

def exit_after_new_output_is_installed(path, target):
    result = original_rename(path, target)
    if path == staging_dir and target == output_dir:
        os._exit(91)
    return result

Path.rename = exit_after_new_output_is_installed
run_workflow.replace_verified_output(
    staging_dir,
    output_dir,
    analysis_dir,
    {{"status": "ok", "errors": [], "warnings": [], "operation_results": []}},
)
"""
    repository_root = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join([str(repository_root / "scripts"), environment.get("PYTHONPATH", "")])
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repository_root,
        env=environment,
    )

    assert result.returncode == 91
    assert (output_dir / "new.txt").is_file()

    run_workflow.recover_pending_export(artifact_dir)

    assert (output_dir / "old.txt").read_text(encoding="utf-8") == "old-output"
    assert not (output_dir / "new.txt").exists()
    for name in ("execution_audit.json", "execution_audit.md"):
        assert (analysis_dir / name).read_text(encoding="utf-8") == f"old-{name}"
    assert not (artifact_dir / run_workflow.EXPORT_TRANSACTION_NAME).exists()
    assert not (artifact_dir / run_workflow.OUTPUT_TRANSACTION_BACKUP_NAME).exists()
    assert not (analysis_dir / run_workflow.AUDIT_TRANSACTION_BACKUP_NAME).exists()


def test_export_lock_rejects_concurrent_export_for_same_artifact(tmp_path):
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()

    with run_workflow._export_lock(artifact_dir):
        with pytest.raises(run_workflow.ExportBusyError, match="already running"):
            with run_workflow._export_lock(artifact_dir):
                pytest.fail("concurrent export lock must not be acquired")


def test_verified_output_cleans_partial_audit_staging_when_preparation_fails(tmp_path, monkeypatch):
    analysis_dir = tmp_path / "analysis"
    analysis_dir.mkdir()
    output_dir = tmp_path / "output_frames"
    output_dir.mkdir()
    (output_dir / "old.txt").write_text("old-output", encoding="utf-8")
    staging_dir = tmp_path / ".output_frames.staging"
    staging_dir.mkdir()
    (staging_dir / "new.txt").write_text("new-output", encoding="utf-8")

    def fail_after_partial_audit_write(audit, target_dir):
        target_dir.mkdir()
        (target_dir / "execution_audit.json").write_text("partial", encoding="utf-8")
        raise OSError("audit preparation failed")

    monkeypatch.setattr(run_workflow, "write_execution_audit", fail_after_partial_audit_write)

    with pytest.raises(OSError, match="audit preparation failed"):
        run_workflow.replace_verified_output(staging_dir, output_dir, analysis_dir, {})

    assert (output_dir / "old.txt").read_text(encoding="utf-8") == "old-output"
    assert not (tmp_path / run_workflow.AUDIT_TRANSACTION_STAGING_NAME).exists()
    assert not (tmp_path / run_workflow.EXPORT_TRANSACTION_NAME).exists()


def test_prepared_transaction_recovers_partial_audit_commit(tmp_path):
    artifact_dir = tmp_path / "run"
    analysis_dir = artifact_dir / "analysis"
    analysis_dir.mkdir(parents=True)
    output_dir = artifact_dir / "output_frames"
    output_dir.mkdir()
    (output_dir / "new.txt").write_text("new-output", encoding="utf-8")
    output_backup = artifact_dir / run_workflow.OUTPUT_TRANSACTION_BACKUP_NAME
    output_backup.mkdir()
    (output_backup / "old.txt").write_text("old-output", encoding="utf-8")
    audit_backup = analysis_dir / run_workflow.AUDIT_TRANSACTION_BACKUP_NAME
    audit_backup.mkdir()
    (audit_backup / "execution_audit.json").write_text("old-json", encoding="utf-8")
    (audit_backup / "execution_audit.md").write_text("old-md", encoding="utf-8")
    (analysis_dir / "execution_audit.json").write_text("new-json", encoding="utf-8")
    transaction = {
        "version": 1,
        "phase": "prepared",
        "had_output": True,
        "had_audits": {"execution_audit.json": True, "execution_audit.md": True},
    }
    (artifact_dir / run_workflow.EXPORT_TRANSACTION_NAME).write_text(json.dumps(transaction), encoding="utf-8")

    run_workflow.recover_pending_export(artifact_dir)

    assert (output_dir / "old.txt").read_text(encoding="utf-8") == "old-output"
    assert (analysis_dir / "execution_audit.json").read_text(encoding="utf-8") == "old-json"
    assert (analysis_dir / "execution_audit.md").read_text(encoding="utf-8") == "old-md"
    assert not (artifact_dir / run_workflow.EXPORT_TRANSACTION_NAME).exists()


def test_committed_transaction_keeps_new_files_and_cleans_backups(tmp_path):
    artifact_dir = tmp_path / "run"
    analysis_dir = artifact_dir / "analysis"
    analysis_dir.mkdir(parents=True)
    output_dir = artifact_dir / "output_frames"
    output_dir.mkdir()
    (output_dir / "new.txt").write_text("new-output", encoding="utf-8")
    output_backup = artifact_dir / run_workflow.OUTPUT_TRANSACTION_BACKUP_NAME
    output_backup.mkdir()
    (output_backup / "old.txt").write_text("old-output", encoding="utf-8")
    audit_backup = analysis_dir / run_workflow.AUDIT_TRANSACTION_BACKUP_NAME
    audit_backup.mkdir()
    (audit_backup / "execution_audit.json").write_text("old-json", encoding="utf-8")
    (analysis_dir / "execution_audit.json").write_text("new-json", encoding="utf-8")
    (analysis_dir / "execution_audit.md").write_text("new-md", encoding="utf-8")
    transaction = {
        "version": 1,
        "phase": "committed",
        "had_output": True,
        "had_audits": {"execution_audit.json": True, "execution_audit.md": True},
    }
    (artifact_dir / run_workflow.EXPORT_TRANSACTION_NAME).write_text(json.dumps(transaction), encoding="utf-8")

    run_workflow.recover_pending_export(artifact_dir)

    assert (output_dir / "new.txt").read_text(encoding="utf-8") == "new-output"
    assert (analysis_dir / "execution_audit.json").read_text(encoding="utf-8") == "new-json"
    assert not output_backup.exists()
    assert not audit_backup.exists()
    assert not (artifact_dir / run_workflow.EXPORT_TRANSACTION_NAME).exists()
