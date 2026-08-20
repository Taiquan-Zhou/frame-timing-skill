import csv
from pathlib import Path

import pytest

from frame_timing_agent.auto_timing_agent import TimingAgentResult
from frame_timing_agent.batch_discovery import DiscoveryResult
from frame_timing_agent.batch_session import (
    BatchBusyError,
    BatchItemStatus,
    BatchStateError,
    BatchStatus,
    create_batch,
    load_batch,
    run_batch,
    save_batch,
)
from frame_timing_agent import batch_session


def make_batch(tmp_path: Path, count: int = 2) -> Path:
    frame_dirs = []
    for index in range(count):
        frame_dir = tmp_path / f"source-{index}" / "frames"
        frame_dir.mkdir(parents=True)
        frame_dirs.append(frame_dir.resolve())
    state = create_batch(
        DiscoveryResult(frame_dirs=tuple(frame_dirs), issues=()),
        artifact_root=tmp_path / "output" / "batch",
        fps=24.0,
        limit_first_n=120,
    )
    return state.state_path


def fake_result(settings, progress_callback=None) -> TimingAgentResult:
    analysis_dir = settings.artifact_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    strategy_path = analysis_dir / "strategy.json"
    strategy_path.write_text("{}", encoding="utf-8")
    human_review = analysis_dir / "human_review.md"
    human_review.write_text("review", encoding="utf-8")
    with (analysis_dir / "frame_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "source_index",
                "output_index",
                "timestamp_sec",
                "sharpness",
                "brightness",
                "contrast",
                "motion_score",
                "similarity_score",
                "bad_quality_candidate",
            ],
        )
        writer.writeheader()
        for index in range(10):
            writer.writerow(
                {
                    "source_index": index,
                    "output_index": index,
                    "timestamp_sec": index / 24,
                    "sharpness": 100,
                    "brightness": 120,
                    "contrast": 20,
                    "motion_score": 0.01,
                    "similarity_score": 0.99,
                    "bad_quality_candidate": "0",
                }
            )
    (analysis_dir / "segments.json").write_text("[]", encoding="utf-8")
    return TimingAgentResult(10, 8, settings.artifact_dir, strategy_path, None)


def fake_review_result(settings, progress_callback=None) -> TimingAgentResult:
    result = fake_result(settings)
    metrics_path = settings.artifact_dir / "analysis" / "frame_metrics.csv"
    rows = metrics_path.read_text(encoding="utf-8").splitlines()
    columns = rows[1].split(",")
    columns[-1] = "1"
    rows[1] = ",".join(columns)
    metrics_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return result


def make_review_batch(tmp_path: Path) -> tuple[Path, str]:
    state_path = make_batch(tmp_path, count=1)
    state = load_batch(state_path)
    item = state.items[0]
    state.status = BatchStatus.FINISHED
    item.status = BatchItemStatus.REVIEW_REQUIRED
    item.progress = 1.0
    item.warnings = ("quality.bad_candidate_ratio",)
    item.analyzed_count = 10
    item.output_count = 8
    save_batch(state)
    return state_path, item.safe_name


def test_run_saves_running_and_terminal_state_after_each_item(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path)
    persisted = []
    real_save = batch_session.save_batch

    def recording_save(state):
        real_save(state)
        persisted.append((state.status, tuple(item.status for item in state.items)))

    monkeypatch.setattr(batch_session, "save_batch", recording_save)
    monkeypatch.setattr(batch_session, "analyze_run", lambda settings, progress_callback=None: fake_result(settings))
    publish_calls = []
    monkeypatch.setattr(
        batch_session,
        "publish_batch_timing_reports",
        lambda artifact_root, results: publish_calls.append(tuple(results)),
    )

    result = run_batch(state_path)

    assert result.status is BatchStatus.FINISHED
    assert [item.status for item in result.items] == [BatchItemStatus.COMPLETED] * 2
    assert any(statuses[0] is BatchItemStatus.RUNNING for _, statuses in persisted)
    assert any(statuses[0] is BatchItemStatus.COMPLETED for _, statuses in persisted)
    assert any(statuses[1] is BatchItemStatus.RUNNING for _, statuses in persisted)
    assert len(publish_calls) == 2
    assert [len(results) for results in publish_calls] == [1, 2]
    assert load_batch(state_path) == result


def test_quality_warning_marks_item_for_review(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path, count=1)
    monkeypatch.setattr(batch_session, "analyze_run", fake_review_result)
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    result = run_batch(state_path)

    assert result.items[0].status is BatchItemStatus.REVIEW_REQUIRED
    assert result.items[0].warnings == ("quality.bad_candidate_ratio",)
    assert result.items[0].approved is False
    assert result.items[0].note is None


def test_invalid_quality_artifact_fails_only_current_item(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path, count=2)
    calls = 0

    def analyze(settings, progress_callback=None):
        nonlocal calls
        calls += 1
        result = fake_result(settings)
        if calls == 1:
            (settings.artifact_dir / "analysis" / "segments.json").write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(batch_session, "analyze_run", analyze)
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    result = run_batch(state_path)

    assert calls == 2
    assert result.items[0].status is BatchItemStatus.FAILED
    assert "segments must be a JSON array" in (result.items[0].last_error or "")
    assert result.items[1].status is BatchItemStatus.COMPLETED


def test_reanalysis_resets_previous_approval_and_warnings(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path, count=1)
    state = load_batch(state_path)
    item = state.items[0]
    item.status = BatchItemStatus.PENDING
    item.warnings = ("quality.low_motion_review",)
    item.approved = True
    item.note = "old approval"
    save_batch(state)
    monkeypatch.setattr(batch_session, "analyze_run", fake_result)
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    result = run_batch(state_path)

    assert result.items[0].status is BatchItemStatus.COMPLETED
    assert result.items[0].warnings == ()
    assert result.items[0].approved is False
    assert result.items[0].note is None


def test_run_maps_item_progress_to_overall_progress(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path)
    events = []

    def analyze(settings, progress_callback=None):
        assert progress_callback is not None
        progress_callback(50, "half")
        return fake_result(settings)

    monkeypatch.setattr(batch_session, "analyze_run", analyze)
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    run_batch(state_path, progress_callback=lambda percent, message: events.append((percent, message)))

    assert (25, "half") in events
    assert (75, "half") in events
    assert events[-1][0] == 100


def test_progress_callback_failure_does_not_change_analysis_result(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path, count=1)

    def analyze(settings, progress_callback=None):
        assert progress_callback is not None
        progress_callback(50, "half")
        return fake_result(settings)

    monkeypatch.setattr(batch_session, "analyze_run", analyze)
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    result = run_batch(
        state_path,
        progress_callback=lambda percent, message: (_ for _ in ()).throw(RuntimeError("callback failed")),
    )

    assert result.status is BatchStatus.FINISHED
    assert result.items[0].status is BatchItemStatus.COMPLETED


def test_run_continues_after_item_failure_and_redacts_source_path(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path)
    calls = 0
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    def analyze(settings, progress_callback=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError(f"cannot analyze {settings.frame_dir}")
        return fake_result(settings)

    monkeypatch.setattr(batch_session, "analyze_run", analyze)

    result = run_batch(state_path)

    assert calls == 2
    assert result.status is BatchStatus.FINISHED
    assert result.items[0].status is BatchItemStatus.FAILED
    assert result.items[1].status is BatchItemStatus.COMPLETED
    assert "<input_frame_dir>" in (result.items[0].last_error or "")
    assert str(result.items[0].frame_dir) not in (result.items[0].last_error or "")


def test_failure_review_write_error_does_not_stop_later_items(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path)
    first_safe_name = load_batch(state_path).items[0].safe_name
    calls = 0
    real_write_text = Path.write_text
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    def analyze(settings, progress_callback=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("analysis failed")
        return fake_result(settings)

    def guarded_write_text(path, data, *args, **kwargs):
        if path.name == "human_review.md" and path.parent.parent.name == first_safe_name:
            raise PermissionError("review is read-only")
        return real_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(batch_session, "analyze_run", analyze)
    monkeypatch.setattr(Path, "write_text", guarded_write_text)

    result = run_batch(state_path)

    assert calls == 2
    assert result.items[0].status is BatchItemStatus.FAILED
    assert result.items[1].status is BatchItemStatus.COMPLETED
    assert result.status is BatchStatus.FINISHED


def test_pause_finishes_current_item_and_leaves_next_pending(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path)
    pause = iter([False, True])
    monkeypatch.setattr(batch_session, "analyze_run", lambda settings, progress_callback=None: fake_result(settings))
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    result = run_batch(state_path, should_pause=lambda: next(pause, True))

    assert result.status is BatchStatus.PAUSED
    assert result.items[0].status in {BatchItemStatus.COMPLETED, BatchItemStatus.REVIEW_REQUIRED}
    assert result.items[1].status is BatchItemStatus.PENDING


def test_explicit_continuation_runs_only_pending_items(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path)
    state = load_batch(state_path)
    state.status = BatchStatus.PAUSED
    state.items[0].status = BatchItemStatus.COMPLETED
    state.items[0].progress = 1.0
    state.items[0].analyzed_count = 10
    state.items[0].output_count = 8
    save_batch(state)
    analyzed = []
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    def analyze(settings, progress_callback=None):
        analyzed.append(settings.frame_dir)
        return fake_result(settings)

    monkeypatch.setattr(batch_session, "analyze_run", analyze)

    result = run_batch(state_path)

    assert analyzed == [state.items[1].frame_dir]
    assert result.status is BatchStatus.FINISHED


def test_completed_and_review_items_are_not_repeated(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path)
    state = load_batch(state_path)
    state.status = BatchStatus.PAUSED
    state.items[0].status = BatchItemStatus.COMPLETED
    state.items[1].status = BatchItemStatus.REVIEW_REQUIRED
    for item in state.items:
        item.progress = 1.0
        item.analyzed_count = 10
        item.output_count = 8
    save_batch(state)
    monkeypatch.setattr(batch_session, "analyze_run", lambda *args, **kwargs: pytest.fail("must not repeat"))
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    result = run_batch(state_path)

    assert result.status is BatchStatus.FINISHED


def test_retry_runs_only_explicitly_selected_failed_items(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path, count=3)
    state = load_batch(state_path)
    state.status = BatchStatus.FINISHED
    for item in state.items:
        item.status = BatchItemStatus.FAILED
        item.progress = 1.0
        item.last_error = "old failure"
    save_batch(state)
    selected = state.items[1]
    analyzed = []
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    def analyze(settings, progress_callback=None):
        analyzed.append(settings.frame_dir)
        return fake_result(settings)

    monkeypatch.setattr(batch_session, "analyze_run", analyze)

    result = run_batch(state_path, retry_items=(selected.safe_name,))

    assert analyzed == [selected.frame_dir]
    assert result.items[1].status is BatchItemStatus.COMPLETED
    assert result.items[1].retry_count == 1
    assert result.items[0].status is BatchItemStatus.FAILED
    assert result.items[2].status is BatchItemStatus.FAILED


def test_paused_retry_preserves_unstarted_failure_diagnostics(tmp_path, monkeypatch):
    state_path = make_batch(tmp_path, count=2)
    state = load_batch(state_path)
    state.status = BatchStatus.FINISHED
    for item in state.items:
        item.status = BatchItemStatus.FAILED
        item.progress = 1.0
        item.last_error = f"old failure {item.safe_name}"
        item.retry_count = 2
    save_batch(state)
    pause = iter([False, True])
    analyzed = []

    def analyze(settings, progress_callback=None):
        analyzed.append(settings.frame_dir)
        return fake_result(settings)

    monkeypatch.setattr(batch_session, "analyze_run", analyze)
    monkeypatch.setattr(batch_session, "publish_batch_timing_reports", lambda artifact_root, results: None)

    result = run_batch(
        state_path,
        should_pause=lambda: next(pause, True),
        retry_items=tuple(item.safe_name for item in state.items),
    )

    assert result.status is BatchStatus.PAUSED
    assert result.items[0].status is BatchItemStatus.COMPLETED
    assert result.items[0].retry_count == 3
    assert result.items[1].status is BatchItemStatus.PENDING
    assert result.items[1].retry_count == 2
    assert result.items[1].last_error == f"old failure {result.items[1].safe_name}"

    continued = run_batch(state_path)

    assert analyzed == [state.items[0].frame_dir, state.items[1].frame_dir]
    assert continued.status is BatchStatus.FINISHED
    assert continued.items[1].status is BatchItemStatus.COMPLETED
    assert continued.items[1].retry_count == 3
    assert continued.items[1].last_error is None


def test_retry_rejects_unknown_or_nonfailed_items_without_mutation(tmp_path):
    state_path = make_batch(tmp_path)
    before = state_path.read_bytes()

    with pytest.raises(BatchStateError, match="retry item is not failed"):
        run_batch(state_path, retry_items=(load_batch(state_path).items[0].safe_name,))
    assert state_path.read_bytes() == before

    with pytest.raises(BatchStateError, match="unknown retry item"):
        run_batch(state_path, retry_items=("missing",))
    assert state_path.read_bytes() == before


def test_approve_item_verifies_snapshot_and_strips_note(tmp_path, monkeypatch):
    state_path, item_name = make_review_batch(tmp_path)
    calls = []

    def verify(analysis_dir, frame_dir, fps, limit_first_n):
        calls.append((analysis_dir, frame_dir, fps, limit_first_n))
        return True

    monkeypatch.setattr(batch_session, "verify_input_snapshot", verify)

    result = batch_session.approve_item(state_path, item_name, "  visually checked  ")

    item = result.items[0]
    assert item.approved is True
    assert item.note == "visually checked"
    assert calls == [
        (
            result.artifact_root / item_name / "analysis",
            item.frame_dir,
            result.fps,
            result.limit_first_n,
        )
    ]
    assert load_batch(state_path) == result


def test_approve_item_allows_an_empty_optional_note(tmp_path, monkeypatch):
    state_path, item_name = make_review_batch(tmp_path)
    monkeypatch.setattr(batch_session, "verify_input_snapshot", lambda *args: True)

    result = batch_session.approve_item(state_path, item_name, "   ")

    assert result.items[0].approved is True
    assert result.items[0].note is None


@pytest.mark.parametrize(
    "status",
    [
        BatchItemStatus.PENDING,
        BatchItemStatus.COMPLETED,
        BatchItemStatus.FAILED,
    ],
)
def test_approve_item_rejects_non_review_status_without_mutation(tmp_path, monkeypatch, status):
    state_path, item_name = make_review_batch(tmp_path)
    state = load_batch(state_path)
    state.items[0].status = status
    if status is BatchItemStatus.PENDING:
        state.status = BatchStatus.PAUSED
    save_batch(state)
    before = state_path.read_bytes()
    monkeypatch.setattr(batch_session, "verify_input_snapshot", lambda *args: pytest.fail("must not verify"))

    with pytest.raises(BatchStateError, match="review_required"):
        batch_session.approve_item(state_path, item_name, "checked")

    assert state_path.read_bytes() == before


def test_approve_item_rejects_unknown_item_without_mutation(tmp_path, monkeypatch):
    state_path, _ = make_review_batch(tmp_path)
    before = state_path.read_bytes()
    monkeypatch.setattr(batch_session, "verify_input_snapshot", lambda *args: pytest.fail("must not verify"))

    with pytest.raises(BatchStateError, match="unknown batch item"):
        batch_session.approve_item(state_path, "missing", "checked")

    assert state_path.read_bytes() == before


def test_approve_item_keeps_state_unchanged_when_snapshot_is_stale(tmp_path, monkeypatch):
    state_path, item_name = make_review_batch(tmp_path)
    before = state_path.read_bytes()

    def reject(*args):
        raise ValueError("input frames changed since analysis")

    monkeypatch.setattr(batch_session, "verify_input_snapshot", reject)

    with pytest.raises(ValueError, match="changed since analysis"):
        batch_session.approve_item(state_path, item_name, "checked")

    assert state_path.read_bytes() == before


def test_approve_item_respects_batch_run_lock(tmp_path, monkeypatch):
    state_path, item_name = make_review_batch(tmp_path)
    monkeypatch.setattr(batch_session, "verify_input_snapshot", lambda *args: True)

    with batch_session._run_lock(state_path):
        with pytest.raises(BatchBusyError, match="already running"):
            batch_session.approve_item(state_path, item_name, "checked")
