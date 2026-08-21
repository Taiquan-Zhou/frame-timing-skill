from pathlib import Path

import pytest

from frame_timing_agent import batch_session
from frame_timing_agent.auto_timing_agent import TimingAgentResult
from frame_timing_agent.batch_discovery import DiscoveryResult
from frame_timing_agent.batch_session import (
    BatchItemStatus,
    BatchStateError,
    BatchStatus,
    create_batch,
    load_batch,
    save_batch,
)


def make_finished_batch(tmp_path: Path) -> Path:
    frame_dirs = []
    for index in range(4):
        frame_dir = tmp_path / f"source-{index}" / "frames"
        frame_dir.mkdir(parents=True)
        frame_dirs.append(frame_dir.resolve())
    state = create_batch(
        DiscoveryResult(frame_dirs=tuple(frame_dirs), issues=()),
        artifact_root=tmp_path / "output" / "batch",
        fps=24.0,
        limit_first_n=120,
    )
    state.status = BatchStatus.FINISHED
    for item in state.items:
        item.progress = 1.0
        item.analyzed_count = 10
        item.output_count = 8
        analysis_dir = state.artifact_root / item.safe_name / "analysis"
        analysis_dir.mkdir(parents=True)
        (analysis_dir / "strategy.json").write_text("{}", encoding="utf-8")
        (analysis_dir / "human_review.md").write_text("review", encoding="utf-8")
    state.items[0].status = BatchItemStatus.COMPLETED
    state.items[1].status = BatchItemStatus.REVIEW_REQUIRED
    state.items[1].approved = True
    state.items[2].status = BatchItemStatus.REVIEW_REQUIRED
    state.items[3].status = BatchItemStatus.FAILED
    state.items[3].last_error = "analysis failed"
    save_batch(state)
    return state.state_path


def fake_export(settings, progress_callback=None) -> TimingAgentResult:
    output_dir = settings.artifact_dir / "output_frames"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "exported.txt").write_text("exported", encoding="utf-8")
    return TimingAgentResult(
        10,
        8,
        settings.artifact_dir,
        settings.artifact_dir / "analysis" / "strategy.json",
        output_dir,
    )


def test_export_requires_a_finished_batch_without_mutating_state(tmp_path, monkeypatch):
    state_path = make_finished_batch(tmp_path)
    state = load_batch(state_path)
    state.status = BatchStatus.PAUSED
    save_batch(state)
    before = state_path.read_bytes()
    monkeypatch.setattr(batch_session, "export_run", lambda *args, **kwargs: pytest.fail("must not export"))

    with pytest.raises(BatchStateError, match="finished"):
        batch_session.export_batch(state_path)

    assert state_path.read_bytes() == before


def test_export_exports_completed_and_approved_items_and_skips_unresolved_or_failed(tmp_path, monkeypatch):
    state_path = make_finished_batch(tmp_path)
    exported_settings = []
    report_results = []
    monkeypatch.setattr(
        batch_session,
        "export_run",
        lambda settings, progress_callback=None: (exported_settings.append(settings), fake_export(settings))[1],
    )
    monkeypatch.setattr(
        batch_session,
        "analyze_run",
        lambda *args, **kwargs: pytest.fail("export must not reanalyze"),
    )
    monkeypatch.setattr(
        batch_session,
        "publish_batch_timing_reports",
        lambda artifact_root, results: report_results.append(tuple(results)),
    )

    summary = batch_session.export_batch(state_path)
    persisted = load_batch(state_path)

    assert summary.exported == (persisted.items[0].safe_name, persisted.items[1].safe_name)
    assert summary.skipped == (persisted.items[2].safe_name, persisted.items[3].safe_name)
    assert summary.failed == ()
    assert [settings.frame_dir for settings in exported_settings] == [
        persisted.items[0].frame_dir,
        persisted.items[1].frame_dir,
    ]
    assert [item.output_path for item in persisted.items[:2]] == [
        persisted.artifact_root / persisted.items[0].safe_name / "output_frames",
        persisted.artifact_root / persisted.items[1].safe_name / "output_frames",
    ]
    assert report_results and [item.name for item in report_results[-1]] == [item.safe_name for item in persisted.items]


def test_export_persists_each_eligible_item_after_its_output_is_written(tmp_path, monkeypatch):
    state_path = make_finished_batch(tmp_path)
    state = load_batch(state_path)
    state.items = state.items[:2]
    save_batch(state)
    persisted_output_sets = []
    real_save = batch_session.save_batch

    def record_save(current_state):
        real_save(current_state)
        persisted_output_sets.append(tuple(item.output_path for item in current_state.items))

    monkeypatch.setattr(batch_session, "save_batch", record_save)
    monkeypatch.setattr(batch_session, "export_run", fake_export)
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda *args, **kwargs: None)

    batch_session.export_batch(state_path)

    first_output = state.artifact_root / state.items[0].safe_name / "output_frames"
    second_output = state.artifact_root / state.items[1].safe_name / "output_frames"
    assert (first_output, None) in persisted_output_sets
    assert (first_output, second_output) in persisted_output_sets


def test_export_blocks_changed_input_and_preserves_previous_valid_output(tmp_path, monkeypatch):
    state_path = make_finished_batch(tmp_path)
    state = load_batch(state_path)
    item = state.items[0]
    previous_output = state.artifact_root / item.safe_name / "output_frames"
    previous_output.mkdir(parents=True)
    marker = previous_output / "previous-output.txt"
    marker.write_text("keep", encoding="utf-8")
    item.output_path = previous_output
    save_batch(state)
    monkeypatch.setattr(
        batch_session,
        "export_run",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            ValueError("input frames changed since analysis; run analysis again before exporting")
        ),
    )
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda *args, **kwargs: None)

    summary = batch_session.export_batch(state_path)
    persisted = load_batch(state_path)

    assert item.safe_name in summary.failed
    assert marker.read_text(encoding="utf-8") == "keep"
    assert persisted.items[0].output_path == previous_output


def test_mixed_export_report_counts_only_items_with_output_directories(tmp_path, monkeypatch):
    state_path = make_finished_batch(tmp_path)
    state = load_batch(state_path)
    state.items = state.items[:2]
    state.items[1].approved = False
    save_batch(state)
    monkeypatch.setattr(batch_session, "export_run", fake_export)

    batch_session.export_batch(state_path)

    review = (state.artifact_root / "analysis" / "human_review.md").read_text(encoding="utf-8")
    assert "执行模式，已为 1 个项写出 output_frames" in review
