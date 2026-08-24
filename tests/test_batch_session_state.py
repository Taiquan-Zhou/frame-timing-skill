import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from frame_timing_agent.batch_discovery import DiscoveryResult
from frame_timing_agent.batch_session import (
    approve_item,
    BatchBusyError,
    BatchItemStatus,
    BatchStateError,
    BatchStatus,
    create_batch,
    export_batch,
    load_batch,
    recover_batch,
    run_batch,
    save_batch,
)
from frame_timing_agent import batch_session


def make_discovery(*frame_dirs: Path) -> DiscoveryResult:
    return DiscoveryResult(frame_dirs=tuple(path.resolve() for path in frame_dirs), issues=())


def make_directory_link(link: Path, target: Path) -> None:
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


def remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


def create_state_with_statuses(tmp_path: Path, statuses: list[str]):
    frame_dirs = []
    for index in range(len(statuses)):
        frame_dir = tmp_path / f"source-{index}" / "frames"
        frame_dir.mkdir(parents=True)
        frame_dirs.append(frame_dir)

    state = create_batch(
        make_discovery(*frame_dirs),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
        limit_first_n=120,
    )
    for item, status in zip(state.items, statuses):
        item.status = BatchItemStatus(status)
    state.status = BatchStatus.RUNNING
    save_batch(state)
    return state.state_path


def test_state_json_round_trip_preserves_typed_values(tmp_path):
    first = tmp_path / "first" / "frames"
    second = tmp_path / "second" / "clean_frames"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    created = create_batch(
        make_discovery(first, second),
        artifact_root=tmp_path / "batch-artifacts",
        fps=29.97,
        limit_first_n=None,
    )
    created.items[0].progress = 0.5
    created.items[0].warnings = ("low_motion",)
    created.items[0].approved = True
    created.items[0].note = "reviewed"
    created.items[0].analyzed_count = 42
    created.items[0].output_count = 36
    created.items[0].output_path = created.artifact_root / created.items[0].safe_name / "output_frames"
    save_batch(created)

    loaded = load_batch(created.state_path)

    assert loaded == created
    assert loaded.items[0].status is BatchItemStatus.PENDING
    assert loaded.status is BatchStatus.READY
    assert loaded.items[0].frame_dir == first.resolve()


def test_save_batch_rejects_stale_loaded_revision(tmp_path):
    frame_dir = tmp_path / "source" / "frames"
    frame_dir.mkdir(parents=True)
    created = create_batch(
        make_discovery(frame_dir),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    first_writer = load_batch(created.state_path)
    stale_writer = load_batch(created.state_path)

    first_writer.items[0].progress = 0.25
    save_batch(first_writer)
    stale_writer.items[0].progress = 0.75

    with pytest.raises(BatchStateError, match="revision is stale"):
        save_batch(stale_writer)

    assert load_batch(created.state_path).items[0].progress == 0.25


def test_save_batch_respects_active_batch_lock(tmp_path):
    frame_dir = tmp_path / "source" / "frames"
    frame_dir.mkdir(parents=True)
    state = create_batch(
        make_discovery(frame_dir),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )

    with batch_session._run_lock(state.state_path):
        with pytest.raises(BatchBusyError):
            save_batch(state)


def test_save_batch_rejects_running_item_outside_running_batch(tmp_path):
    frame_dir = tmp_path / "source" / "frames"
    frame_dir.mkdir(parents=True)
    state = create_batch(
        make_discovery(frame_dir),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    state.items[0].status = BatchItemStatus.RUNNING

    with pytest.raises(BatchStateError, match="running item requires a running batch"):
        save_batch(state)


def test_save_batch_keeps_committed_revision_when_directory_sync_fails(tmp_path, monkeypatch):
    frame_dir = tmp_path / "source" / "frames"
    frame_dir.mkdir(parents=True)
    state = create_batch(
        make_discovery(frame_dir),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    previous_revision = state.revision
    monkeypatch.setattr(batch_session, "_sync_directory", lambda path: (_ for _ in ()).throw(OSError("sync failed")))

    with pytest.raises(OSError, match="sync failed"):
        save_batch(state)

    assert state.revision == previous_revision + 1
    assert load_batch(state.state_path).revision == state.revision


def test_create_batch_uses_unique_safe_names_for_same_leaf_directory(tmp_path):
    first = tmp_path / "source-a" / "frames"
    second = tmp_path / "source-b" / "frames"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    state = create_batch(
        make_discovery(first, second),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )

    assert [item.safe_name for item in state.items] == ["frames", "frames-2"]


def test_create_batch_avoids_secondary_safe_name_collisions(tmp_path):
    first = tmp_path / "source-a" / "frames"
    second = tmp_path / "source-b" / "frames"
    third = tmp_path / "source-c" / "frames-2"
    for frame_dir in (first, second, third):
        frame_dir.mkdir(parents=True)

    state = create_batch(
        make_discovery(first, second, third),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )

    assert [item.safe_name for item in state.items] == ["frames", "frames-2", "frames-2-2"]


def test_create_batch_sanitizes_windows_reserved_directory_names(tmp_path):
    frame_dir = tmp_path / "source" / "COM1"
    frame_dir.mkdir(parents=True)

    state = create_batch(
        make_discovery(frame_dir),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )

    assert state.items[0].safe_name == "item"


def test_create_batch_rejects_artifact_root_inside_source_directory(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    artifact_root = frame_dir / "batch-artifacts"

    with pytest.raises(ValueError, match="artifact_root must not overlap"):
        create_batch(make_discovery(frame_dir), artifact_root=artifact_root, fps=24.0)

    assert not artifact_root.exists()


def test_create_batch_refuses_to_replace_an_existing_batch(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    artifact_root = tmp_path / "batch-artifacts"
    created = create_batch(make_discovery(frame_dir), artifact_root=artifact_root, fps=24.0)
    before = created.state_path.read_bytes()

    with pytest.raises(BatchStateError, match="already exists"):
        create_batch(make_discovery(frame_dir), artifact_root=artifact_root, fps=24.0)

    assert created.state_path.read_bytes() == before


def test_create_batch_rejects_nonempty_artifact_root(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    artifact_root = tmp_path / "batch-artifacts"
    artifact_root.mkdir()
    (artifact_root / "old-report.json").write_text("{}", encoding="utf-8")

    with pytest.raises(BatchStateError, match="not empty"):
        create_batch(make_discovery(frame_dir), artifact_root=artifact_root, fps=24.0)


def test_create_batch_allows_only_a_stale_canonical_lock_file(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    artifact_root = tmp_path / "batch-artifacts"
    analysis_dir = artifact_root / "analysis"
    analysis_dir.mkdir(parents=True)
    (analysis_dir / "batch_state.json.run.lock").write_bytes(b"L")

    state = create_batch(make_discovery(frame_dir), artifact_root=artifact_root, fps=24.0)

    assert state.state_path.is_file()


def test_create_batch_rejects_a_hard_linked_canonical_lock_file(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    artifact_root = tmp_path / "batch-artifacts"
    analysis_dir = artifact_root / "analysis"
    analysis_dir.mkdir(parents=True)
    external_target = tmp_path / "outside.lock"
    external_target.write_bytes(b"outside")
    os.link(external_target, analysis_dir / "batch_state.json.run.lock")

    with pytest.raises(BatchStateError, match="artifact root is not empty"):
        create_batch(make_discovery(frame_dir), artifact_root=artifact_root, fps=24.0)

    assert external_target.read_bytes() == b"outside"


def test_batch_operation_rejects_lock_replaced_by_hard_link(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    state = create_batch(make_discovery(frame_dir), artifact_root=tmp_path / "batch-artifacts", fps=24.0)
    lock_path = Path(f"{state.state_path}.run.lock")
    lock_path.unlink()
    external_target = tmp_path / "outside.lock"
    external_target.write_bytes(b"outside")
    os.link(external_target, lock_path)

    with pytest.raises(BatchStateError, match="lock file is unsafe"):
        run_batch(state.state_path)

    assert external_target.read_bytes() == b"outside"


@pytest.mark.parametrize(
    "operation",
    [
        lambda path: run_batch(path),
        lambda path: recover_batch(path),
        lambda path: approve_item(path, "missing", "reviewed"),
        lambda path: export_batch(path),
    ],
)
def test_missing_state_operations_do_not_create_lock_artifacts(tmp_path, operation):
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"

    with pytest.raises(BatchStateError, match="cannot load batch state"):
        operation(state_path)

    assert not state_path.parent.parent.exists()


def test_create_batch_rejects_analysis_link_outside_artifact_root(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    artifact_root = tmp_path / "batch-artifacts"
    artifact_root.mkdir()
    external = tmp_path / "external"
    external.mkdir()
    analysis_link = artifact_root / "analysis"
    make_directory_link(analysis_link, external)
    try:
        with pytest.raises(BatchStateError, match="not empty|escapes artifact_root"):
            create_batch(make_discovery(frame_dir), artifact_root=artifact_root, fps=24.0)
        assert not (external / "batch_state.json").exists()
    finally:
        remove_directory_link(analysis_link)


def test_load_rejects_analysis_link_outside_artifact_root(tmp_path):
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    artifact_root = tmp_path / "batch-artifacts"
    state = create_batch(make_discovery(frame_dir), artifact_root=artifact_root, fps=24.0)
    payload = state.state_path.read_text(encoding="utf-8")
    shutil.rmtree(state.state_path.parent)
    external = tmp_path / "external"
    external.mkdir()
    (external / "batch_state.json").write_text(payload, encoding="utf-8")
    analysis_link = artifact_root / "analysis"
    make_directory_link(analysis_link, external)
    try:
        with pytest.raises(BatchStateError, match="escapes artifact_root"):
            load_batch(analysis_link / "batch_state.json")
    finally:
        remove_directory_link(analysis_link)


@pytest.mark.parametrize("tamper", ["duplicate_source", "artifact_overlap"])
def test_load_rejects_tampered_source_path_invariants(tmp_path, tamper):
    first = tmp_path / "first" / "frames"
    second = tmp_path / "second" / "frames"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    state = create_batch(make_discovery(first, second), artifact_root=tmp_path / "batch-artifacts", fps=24.0)
    payload = json.loads(state.state_path.read_text(encoding="utf-8"))
    if tamper == "duplicate_source":
        payload["items"][1]["frame_dir"] = payload["items"][0]["frame_dir"]
    else:
        payload["items"][1]["frame_dir"] = str(state.artifact_root / state.items[1].safe_name / "output_frames")
    state.state_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(BatchStateError, match="source frame directories|must not overlap"):
        load_batch(state.state_path)


def test_save_preserves_existing_file_and_cleans_temp_file_when_replace_fails(tmp_path, monkeypatch):
    state_path = create_state_with_statuses(tmp_path, ["pending"])
    original_contents = state_path.read_text(encoding="utf-8")
    state = load_batch(state_path)
    state.status = BatchStatus.RUNNING

    def fail_replace(source: str | Path, destination: str | Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("frame_timing_agent.batch_session.os.replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        save_batch(state)

    assert state_path.read_text(encoding="utf-8") == original_contents
    assert list(state_path.parent.glob(f".{state_path.name}.*.tmp")) == []


def test_recover_resets_only_running_item(tmp_path):
    state_path = create_state_with_statuses(tmp_path, ["running", "completed"])

    recovered = recover_batch(state_path)

    assert [item.status for item in recovered.items] == [
        BatchItemStatus.PENDING,
        BatchItemStatus.COMPLETED,
    ]
    assert recovered.items[0].retry_count == 1
    assert recovered.items[1].retry_count == 0
    assert recovered.status is BatchStatus.READY


def test_recover_reuses_unlocked_stale_lock_file_for_unfinished_batch(tmp_path):
    state_path = create_state_with_statuses(tmp_path, ["running"])
    lock_path = Path(f"{state_path}.run.lock")
    lock_path.write_text(json.dumps({"pid": -1, "created_at": "2026-08-16T00:00:00+00:00"}), encoding="utf-8")

    recovered = recover_batch(state_path)

    assert recovered.items[0].status is BatchItemStatus.PENDING
    assert lock_path.exists()


def test_recover_rejects_live_run_lock(tmp_path):
    state_path = create_state_with_statuses(tmp_path, ["running"])

    with batch_session._run_lock(state_path):
        with pytest.raises(BatchBusyError, match="already running"):
            recover_batch(state_path)


def test_recover_uses_same_lock_through_directory_alias(tmp_path):
    state_path = create_state_with_statuses(tmp_path, ["running"])
    alias_root = tmp_path / "artifact-alias"
    make_directory_link(alias_root, state_path.parents[1])
    alias_state_path = alias_root / "analysis" / "batch_state.json"
    try:
        with batch_session._run_lock(state_path):
            with pytest.raises(BatchBusyError, match="already running"):
                recover_batch(alias_state_path)
    finally:
        remove_directory_link(alias_root)


def test_run_lock_is_released_when_owner_process_is_terminated(tmp_path):
    state_path = create_state_with_statuses(tmp_path, ["running"])
    scripts_dir = Path(__file__).parents[1] / "scripts"
    child_code = """
import sys
import time
from pathlib import Path
from frame_timing_agent.batch_session import _run_lock

with _run_lock(Path(sys.argv[1])):
    print("locked", flush=True)
    time.sleep(30)
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(scripts_dir)
    process = subprocess.Popen(
        [sys.executable, "-c", child_code, str(state_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(BatchBusyError, match="already running"):
            recover_batch(state_path)
    finally:
        process.kill()
        process.wait(timeout=5)

    recovered = recover_batch(state_path)
    assert recovered.items[0].status is BatchItemStatus.PENDING


def test_load_rejects_unsupported_schema_version(tmp_path):
    state_path = tmp_path / "batch-artifacts" / "analysis" / "batch_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")

    with pytest.raises(BatchStateError, match="unsupported schema version"):
        load_batch(state_path)


def test_load_rejects_artifact_root_that_redirects_canonical_state_path(tmp_path):
    state = create_batch(
        make_discovery(tmp_path / "frames"),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    raw_state = json.loads(state.state_path.read_text(encoding="utf-8"))
    raw_state["artifact_root"] = str(tmp_path / "other-artifacts")
    state.state_path.write_text(json.dumps(raw_state), encoding="utf-8")

    with pytest.raises(BatchStateError, match="artifact_root does not match"):
        load_batch(state.state_path)


@pytest.mark.parametrize("schema_version", [True, False])
def test_load_rejects_boolean_schema_version(tmp_path, schema_version):
    state_path = tmp_path / "batch-artifacts" / "analysis" / "batch_state.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"schema_version": schema_version}), encoding="utf-8")

    with pytest.raises(BatchStateError, match="unsupported schema version"):
        load_batch(state_path)


def test_load_rejects_finished_batch_with_running_item(tmp_path):
    state_path = create_state_with_statuses(tmp_path, ["running"])
    raw_state = json.loads(state_path.read_text(encoding="utf-8"))
    raw_state["status"] = "finished"
    state_path.write_text(json.dumps(raw_state), encoding="utf-8")

    with pytest.raises(BatchStateError, match="finished batch"):
        load_batch(state_path)


def test_load_rejects_relative_item_paths(tmp_path):
    state = create_batch(
        make_discovery(tmp_path / "frames"),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    raw_state = json.loads(state.state_path.read_text(encoding="utf-8"))
    raw_state["items"][0]["frame_dir"] = "relative/frames"
    state.state_path.write_text(json.dumps(raw_state), encoding="utf-8")

    with pytest.raises(BatchStateError, match="frame_dir must be absolute"):
        load_batch(state.state_path)


def test_load_rejects_output_path_outside_item_artifact(tmp_path):
    state = create_batch(
        make_discovery(tmp_path / "frames"),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    raw_state = json.loads(state.state_path.read_text(encoding="utf-8"))
    raw_state["items"][0]["output_path"] = str(tmp_path / "outside" / "output_frames")
    state.state_path.write_text(json.dumps(raw_state), encoding="utf-8")

    with pytest.raises(BatchStateError, match="output_path does not match"):
        load_batch(state.state_path)


def test_load_rejects_relative_output_path_even_when_cwd_makes_it_match(tmp_path, monkeypatch):
    frame_dir = tmp_path / "frames"
    state = create_batch(
        make_discovery(frame_dir),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    raw_state = json.loads(state.state_path.read_text(encoding="utf-8"))
    raw_state["items"][0]["output_path"] = "batch-artifacts/frames/output_frames"
    state.state_path.write_text(json.dumps(raw_state), encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(BatchStateError, match="output_path must be absolute"):
        load_batch(state.state_path)


def test_load_rejects_empty_batch_items(tmp_path):
    state = create_batch(
        make_discovery(tmp_path / "frames"),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    raw_state = json.loads(state.state_path.read_text(encoding="utf-8"))
    raw_state["items"] = []
    state.state_path.write_text(json.dumps(raw_state), encoding="utf-8")

    with pytest.raises(BatchStateError, match="at least one item"):
        load_batch(state.state_path)


def test_load_rejects_invalid_or_naive_timestamps(tmp_path):
    state = create_batch(
        make_discovery(tmp_path / "frames"),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    raw_state = json.loads(state.state_path.read_text(encoding="utf-8"))
    raw_state["updated_at"] = "2026-08-18T12:00:00"
    state.state_path.write_text(json.dumps(raw_state), encoding="utf-8")

    with pytest.raises(BatchStateError, match="updated_at must be a timezone-aware ISO timestamp"):
        load_batch(state.state_path)


def test_run_lock_closes_file_even_when_unlock_fails(tmp_path, monkeypatch):
    state_path = create_state_with_statuses(tmp_path, ["running"])
    original_release = batch_session._release_file_lock

    def fail_release(lock_file) -> None:
        raise OSError("synthetic unlock failure")

    monkeypatch.setattr(batch_session, "_release_file_lock", fail_release)
    with pytest.raises(OSError, match="synthetic unlock failure"):
        with batch_session._run_lock(state_path):
            pass

    monkeypatch.setattr(batch_session, "_release_file_lock", original_release)
    with batch_session._run_lock(state_path):
        pass


def test_run_lock_does_not_report_generic_io_error_as_busy(tmp_path, monkeypatch):
    state_path = create_state_with_statuses(tmp_path, ["running"])

    def fail_acquire(lock_file) -> None:
        raise OSError("synthetic I/O failure")

    monkeypatch.setattr(batch_session, "_acquire_file_lock", fail_acquire)
    with pytest.raises(OSError, match="synthetic I/O failure"):
        with batch_session._run_lock(state_path):
            pass


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("fps", True, "fps must be a positive finite value"),
        ("pause_requested", 1, "pause_requested must be a boolean"),
    ],
)
def test_save_rejects_state_values_that_cannot_be_loaded(tmp_path, field, value, message):
    state = create_batch(
        make_discovery(tmp_path / "frames"),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    setattr(state, field, value)

    with pytest.raises(BatchStateError, match=message):
        save_batch(state)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("progress", True, "progress must be between zero and one"),
        ("retry_count", True, "retry_count must be a non-negative integer"),
        ("approved", 1, "approved must be a boolean"),
    ],
)
def test_save_rejects_item_values_that_cannot_be_loaded(tmp_path, field, value, message):
    state = create_batch(
        make_discovery(tmp_path / "frames"),
        artifact_root=tmp_path / "batch-artifacts",
        fps=24.0,
    )
    setattr(state.items[0], field, value)

    with pytest.raises(BatchStateError, match=message):
        save_batch(state)
