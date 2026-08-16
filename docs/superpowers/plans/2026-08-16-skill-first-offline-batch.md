# Skill-First Offline Batch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add CPU-only, recoverable multi-directory batch analysis, minimal quality review, explicit verified export, Agent-safe commands, and a compact batch UI while preserving the existing Skill and single-directory behavior.

**Architecture:** Extend the current batch runner instead of replacing it. Extract the existing Qt-independent analysis/export transaction into one core workflow, then add deterministic discovery, one atomic batch-state file, two existing-signal quality rules, a thin JSON CLI adapter, and one PySide6 batch workspace that reuses the generic task runner and existing result widgets.

**Tech Stack:** Python 3.10+, dataclasses, pathlib, JSON/CSV, OpenCV/NumPy through existing modules, PySide6 optional UI dependency, pytest/unittest.

## Global Constraints

- Develop only in `D:\A all code\company\frame-timing-skill-batch-production` on `codex/offline-batch-production`.
- Before Task 1, inspect the main workspace's uncommitted `ui/run_artifacts.py` and `test_ui_run_artifacts.py` changes and port them into the worktree without modifying or reverting the main workspace.
- CPU-only and sequential: process exactly one frame directory at a time; expose no worker-count or GPU option.
- Inputs are cleaned frame directories; do not add video decoding or upload behavior.
- Preserve `frame-timing`, `frame-timing-batch`, `frame-timing-health`, `frame-timing-tool`, and `frame-timing-ui` compatibility.
- Do not change analysis, segment detection, strategy, or frame-selection algorithms.
- Do not add runtime dependencies, a database, cloud service, account system, or separate eval framework.
- Source frame directories are read-only; export must stage, verify, and atomically replace output.
- No automatic resume, retry, approval, or export.
- Keep UI dependencies optional; core and CLI modules must not import PySide6.
- Use TDD for every behavior change and commit after every task.

## File Map

**Create**

- `scripts/frame_timing_agent/run_workflow.py`: Qt-free single-run analysis snapshot and transactional export.
- `scripts/frame_timing_agent/batch_discovery.py`: explicit/root input discovery and deduplication.
- `scripts/frame_timing_agent/batch_session.py`: typed batch state, atomic persistence, sequential execution, resume, approval, and export coordination.
- `scripts/frame_timing_agent/batch_quality.py`: two existing-signal review rules.
- `scripts/frame_timing_agent/ui/batch_workspace.py`: batch list/detail UI and state-driven actions.
- Focused test files corresponding to each module.

**Modify**

- `scripts/frame_timing_agent/ui/run_artifacts.py`: retain UI thumbnail persistence and compatibility imports.
- `scripts/frame_timing_agent/ui/worker.py`: delegate analysis/export to the core workflow; keep generic `create_task`.
- `scripts/frame_timing_agent/batch_timing_agent.py`: expose existing report publication without changing the public runner or per-item execution cost.
- `scripts/frame_timing_agent/tool_cli.py`: add nested Agent-safe batch actions.
- `scripts/frame_timing_agent/ui/main_window.py`: add mode switch and host `BatchWorkspace` without changing single-run behavior.
- `SKILL.md`, `README.md`, package/documentation contract tests.

---

### Task 1: Preserve Current UI Artifact Fixes and Extract the Core Run Workflow

**Files:**
- Create: `scripts/frame_timing_agent/run_workflow.py`
- Modify: `scripts/frame_timing_agent/ui/run_artifacts.py`
- Modify: `scripts/frame_timing_agent/ui/worker.py`
- Test: `tests/test_run_workflow.py`
- Test: `tests/test_ui_run_artifacts.py`
- Test: `tests/test_ui_worker.py`

**Interfaces:**
- Produces: `RunSettings`, `analyze_run(settings, progress_callback=None) -> TimingAgentResult`, `export_run(settings, progress_callback=None) -> TimingAgentResult`, and snapshot helpers in `frame_timing_agent.run_workflow`.
- Preserves: imports of snapshot helper names from `frame_timing_agent.ui.run_artifacts` and `RunSettings`, `run_analysis`, `run_export` from `frame_timing_agent.ui.worker`.

- [ ] **Step 1: Port the two overlapping main-workspace changes into the worktree**

Inspect, do not mutate, the main workspace diff:

```powershell
git -C "D:\A all code\company\frame-timing-skill" diff -- scripts/frame_timing_agent/ui/run_artifacts.py tests/test_ui_run_artifacts.py
```

Apply the same logical thumbnail transaction changes to the worktree with `apply_patch`, then verify only the worktree changed:

```powershell
git -C "D:\A all code\company\frame-timing-skill" status --short
git status --short
```

- [ ] **Step 2: Write failing core workflow tests**

Add tests proving core analysis binds the input snapshot and core export rejects changed input while preserving an existing valid output:

```python
def test_analyze_run_binds_source_and_strategy(tmp_path):
    settings = make_settings_with_frames(tmp_path)
    result = analyze_run(settings)
    snapshot = json.loads((result.artifact_dir / "analysis" / "input_snapshot.json").read_text())
    assert snapshot["strategy_sha256"]


def test_export_run_keeps_previous_output_when_source_changed(tmp_path):
    settings = make_analyzed_settings(tmp_path)
    existing = settings.artifact_dir / "output_frames" / "sentinel.txt"
    existing.parent.mkdir()
    existing.write_text("keep", encoding="utf-8")
    mutate_first_source_frame(settings.frame_dir)
    with pytest.raises(ValueError, match="input frames changed"):
        export_run(settings)
    assert existing.read_text(encoding="utf-8") == "keep"
```

- [ ] **Step 3: Run the tests to verify failure**

Run:

```powershell
python -m pytest tests/test_run_workflow.py -q
```

Expected: collection fails because `frame_timing_agent.run_workflow` does not exist.

- [ ] **Step 4: Implement the minimal Qt-free workflow**

Move snapshot primitives out of the UI module and move the business portion of UI analysis/export behind these signatures:

```python
@dataclass(frozen=True)
class RunSettings:
    frame_dir: Path
    artifact_dir: Path
    fps: float
    limit_first_n: int | None


def analyze_run(
    settings: RunSettings,
    progress_callback: ProgressCallback | None = None,
) -> TimingAgentResult:
    before = capture_input_snapshot(settings.frame_dir, settings.fps, settings.limit_first_n)
    result = run_timing_agent(
        frames=settings.frame_dir,
        artifact_dir=settings.artifact_dir,
        limit_first_n=settings.limit_first_n,
        mode="reconstruction_balanced",
        write=False,
        fps=settings.fps,
        progress_callback=progress_callback,
    )
    after = capture_input_snapshot(settings.frame_dir, settings.fps, settings.limit_first_n)
    if after != before:
        raise ValueError("input frames changed during analysis; run analysis again")
    write_input_snapshot(settings.artifact_dir / "analysis", bind_strategy_snapshot(after, result.strategy_path))
    return result


def export_run(
    settings: RunSettings,
    progress_callback: ProgressCallback | None = None,
) -> TimingAgentResult:
    analysis_dir = settings.artifact_dir / "analysis"
    verify_input_snapshot(analysis_dir, settings.frame_dir, settings.fps, settings.limit_first_n)
    strategy = load_bound_strategy(analysis_dir)
    records = load_frame_records(settings.frame_dir, fps=settings.fps, limit_first_n=settings.limit_first_n)
    output_dir = settings.artifact_dir / "output_frames"
    staging_dir = settings.artifact_dir / f".output_frames.export-{uuid.uuid4().hex}"
    try:
        applied = apply_strategy(records, strategy, staging_dir)
        verify_input_snapshot(analysis_dir, settings.frame_dir, settings.fps, settings.limit_first_n)
        verify_output_snapshot(analysis_dir, staging_dir)
        audit = audit_strategy_execution(records, strategy, staging_dir, fps=settings.fps)
        if audit.get("status") != "ok":
            raise ValueError("output verification failed")
        replace_verified_output(staging_dir, output_dir, analysis_dir, audit)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return TimingAgentResult(len(records), applied.output_count, settings.artifact_dir, analysis_dir / "strategy.json", output_dir)
```

`replace_verified_output` is the renamed Qt-free form of existing
`ui.worker._replace_output_directory` plus `_replace_execution_audit`; move those
two implementations unchanged into `run_workflow.py` rather than writing a new
replacement algorithm.

`analyze_run` must capture before/after snapshots, call existing `run_timing_agent(write=False)`, reject changes, and bind the strategy hash. `export_run` must retain the existing staging, audit, verification, rollback, and cleanup behavior.

- [ ] **Step 5: Keep UI compatibility wrappers thin**

`ui.worker.run_analysis` calls `analyze_run`, builds `AnalysisViewData`, and persists thumbnails. `ui.worker.run_export` calls `export_run` and builds the exported view. `ui.run_artifacts` imports/re-exports core snapshot names while keeping only thumbnail functions locally.

- [ ] **Step 6: Run focused and regression tests**

Run:

```powershell
python -m pytest tests/test_run_workflow.py tests/test_ui_run_artifacts.py tests/test_ui_worker.py tests/test_ui_smoke.py -q
```

Expected: all selected tests pass and no test imports PySide6 through `run_workflow.py`.

- [ ] **Step 7: Commit**

```powershell
git add scripts/frame_timing_agent/run_workflow.py scripts/frame_timing_agent/ui/run_artifacts.py scripts/frame_timing_agent/ui/worker.py tests/test_run_workflow.py tests/test_ui_run_artifacts.py tests/test_ui_worker.py
git commit -m "refactor: extract safe local run workflow"
```

### Task 2: Add Deterministic Batch Discovery

**Files:**
- Create: `scripts/frame_timing_agent/batch_discovery.py`
- Test: `tests/test_batch_discovery.py`

**Interfaces:**
- Produces: `DiscoveryIssue`, `DiscoveryResult`, and `discover_frame_directories(explicit=(), root=None) -> DiscoveryResult`.
- Consumed by: Task 3 batch creation and Task 7 UI folder actions.

- [ ] **Step 1: Write failing discovery tests**

Cover explicit directories, preferred recursive `clean_frames`, direct children, ignored output/cache/hidden directories, parent-child precedence, canonical deduplication, invalid reasons, and stable path sorting:

```python
def test_discovery_prefers_clean_frames_and_deduplicates(tmp_path):
    preferred = make_frames(tmp_path / "video_a" / "clean_frames")
    ignored = make_frames(tmp_path / "output" / "clean_frames")
    result = discover_frame_directories(explicit=[preferred], root=tmp_path)
    assert result.frame_dirs == (preferred.resolve(),)
    assert any(issue.path == ignored.resolve() and issue.code == "ignored_output" for issue in result.issues)
```

- [ ] **Step 2: Verify the tests fail**

Run `python -m pytest tests/test_batch_discovery.py -q`.

Expected: import failure for `batch_discovery`.

- [ ] **Step 3: Implement discovery without decoding images**

Use `Path.iterdir()`/`rglob()` and the existing supported image suffix contract. Return immutable tuples:

```python
@dataclass(frozen=True)
class DiscoveryIssue:
    path: Path
    code: str


@dataclass(frozen=True)
class DiscoveryResult:
    frame_dirs: tuple[Path, ...]
    issues: tuple[DiscoveryIssue, ...]
```

Do not hash or open every image. Resolve paths before deduplication and sort by `os.path.normcase(str(path))`.

- [ ] **Step 4: Run tests and lint the module**

```powershell
python -m pytest tests/test_batch_discovery.py -q
python -m ruff check scripts/frame_timing_agent/batch_discovery.py tests/test_batch_discovery.py
```

- [ ] **Step 5: Commit**

```powershell
git add scripts/frame_timing_agent/batch_discovery.py tests/test_batch_discovery.py
git commit -m "feat: discover batch frame directories"
```

### Task 3: Add Minimal Atomic Batch State

**Files:**
- Create: `scripts/frame_timing_agent/batch_session.py`
- Test: `tests/test_batch_session_state.py`

**Interfaces:**
- Consumes: `DiscoveryResult.frame_dirs` from Task 2.
- Produces: `BatchStatus`, `BatchItemStatus`, `BatchItemState`, `BatchState`, `create_batch`, `load_batch`, `recover_batch`, and `save_batch`.

- [ ] **Step 1: Write failing state tests**

Test JSON round-trip, unique safe names for equal leaf directories, atomic replace cleanup, recovery of running items to pending, finished items unchanged, retry count increment, and schema rejection:

```python
def test_recover_resets_only_running_item(tmp_path):
    state_path = create_state_with_statuses(tmp_path, ["running", "completed"])
    recovered = recover_batch(state_path)
    assert [item.status for item in recovered.items] == [
        BatchItemStatus.PENDING,
        BatchItemStatus.COMPLETED,
    ]
    assert recovered.items[0].retry_count == 1
```

- [ ] **Step 2: Verify the tests fail**

Run `python -m pytest tests/test_batch_session_state.py -q`.

Expected: import failure for `batch_session`.

- [ ] **Step 3: Implement typed state and atomic JSON**

Define only the approved statuses:

```python
class BatchStatus(str, Enum):
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


class BatchItemStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    REVIEW_REQUIRED = "review_required"
    FAILED = "failed"
```

Import `Enum` from the Python 3.10 standard library; do not use `StrEnum`.

`BatchItemState` stores path, safe name, status, progress, last error, retry count, warnings, approval/note, analyzed/output counts, and output path. `BatchState` stores schema version, IDs/timestamps, FPS, limit, artifact root, pause flag, and ordered items.

- [ ] **Step 4: Implement a standard-library run lock**

Use exclusive file creation for `<state>.run.lock`, record PID/time, reject an existing live lock with `BatchBusyError`, and allow explicit stale recovery only when loading an unfinished batch after no process owns the lock. Do not import Qt.

- [ ] **Step 5: Run tests and verify canonical persistence**

```powershell
python -m pytest tests/test_batch_session_state.py -q
python -m ruff check scripts/frame_timing_agent/batch_session.py tests/test_batch_session_state.py
```

- [ ] **Step 6: Commit**

```powershell
git add scripts/frame_timing_agent/batch_session.py tests/test_batch_session_state.py
git commit -m "feat: persist recoverable batch sessions"
```

### Task 4: Expose Existing Batch Reports, Then Run Sessions Sequentially

**Files:**
- Modify: `scripts/frame_timing_agent/batch_timing_agent.py`
- Modify: `scripts/frame_timing_agent/batch_session.py`
- Modify: `tests/test_batch_timing_agent.py`
- Create: `tests/test_batch_session_runner.py`

**Interfaces:**
- Produces from legacy module: `publish_batch_timing_reports(artifact_root, results) -> BatchTimingResult`.
- Produces from session module: `run_batch(state_path, progress_callback=None, should_pause=None, retry_items=()) -> BatchState`.

- [ ] **Step 1: Add a failing legacy compatibility test**

Patch the new report helper and assert the public runner calls it while preserving its signature, results, summary paths, failure isolation, and existing call count to `run_timing_agent`.

- [ ] **Step 2: Refactor existing code without changing behavior**

Move only the final report writes behind:

```python
def publish_batch_timing_reports(
    artifact_root: Path,
    results: Sequence[BatchTimingItemResult],
) -> BatchTimingResult:
    analysis_dir = artifact_root / "analysis"
    summary_json = analysis_dir / "batch_summary.json"
    summary_csv = analysis_dir / "batch_summary.csv"
    human_review = analysis_dir / "human_review.md"
    dashboard = analysis_dir / "review_dashboard.md"
    _write_summary_json(summary_json, artifact_root, results)
    _write_summary_csv(summary_csv, artifact_root, results)
    _write_batch_human_review(human_review, artifact_root, results, preview_only=all(item.output_dir is None for item in results))
    _write_review_dashboard(dashboard, artifact_root, results)
    run_batch_artifact_health_check(artifact_root)
    return BatchTimingResult(artifact_root, list(results), summary_json, summary_csv, human_review, dashboard)
```

Keep the existing `run_batch_timing_agent` loop unchanged and call the report helper afterward. The recoverable session uses `analyze_run` and converts its result into the existing `BatchTimingItemResult` dataclass before publishing reports; the legacy command must not gain extra snapshot hashing.

- [ ] **Step 3: Write failing session runner tests**

Test save-after-each-item, progress mapping, failure continuation, pause after current item, explicit continuation, no repeat of completed/review items, and explicit selected retry:

```python
def test_pause_finishes_current_item_and_leaves_next_pending(tmp_path):
    state_path = make_two_item_batch(tmp_path)
    pause = iter([False, True])
    result = run_batch(state_path, should_pause=lambda: next(pause, True))
    assert result.status == BatchStatus.PAUSED
    assert result.items[0].status in {BatchItemStatus.COMPLETED, BatchItemStatus.REVIEW_REQUIRED}
    assert result.items[1].status == BatchItemStatus.PENDING
```

- [ ] **Step 4: Implement sequential session execution**

Use Task 1 `analyze_run` for each pending item, persist running before work and terminal status afterward, collect `BatchTimingItemResult` values, and call `publish_batch_timing_reports` after every item. Catch item exceptions, store a safe message, mark failed, and continue.

- [ ] **Step 5: Run focused tests**

```powershell
python -m pytest tests/test_batch_timing_agent.py tests/test_batch_session_state.py tests/test_batch_session_runner.py -q
```

- [ ] **Step 6: Commit**

```powershell
git add scripts/frame_timing_agent/batch_timing_agent.py scripts/frame_timing_agent/batch_session.py tests/test_batch_timing_agent.py tests/test_batch_session_runner.py
git commit -m "feat: run recoverable batches sequentially"
```

### Task 5: Add Two Explainable Quality Rules and Approval

**Files:**
- Create: `scripts/frame_timing_agent/batch_quality.py`
- Modify: `scripts/frame_timing_agent/batch_session.py`
- Create: `tests/test_batch_quality.py`
- Modify: `tests/test_batch_session_runner.py`

**Interfaces:**
- Produces: `QualityWarning` and `evaluate_quality(analysis_dir) -> tuple[QualityWarning, ...]`.
- Produces: `approve_item(state_path, item_name, note) -> BatchState`.

- [ ] **Step 1: Write failing quality tests**

Cover 9.9% versus 10% bad-quality ratio, missing/empty metrics errors, no low-motion segment, and one/multiple `low_motion_review` ranges:

```python
def test_bad_quality_ratio_at_threshold_requires_review(tmp_path):
    analysis_dir = write_metrics(tmp_path, total=20, bad=2)
    warnings = evaluate_quality(analysis_dir)
    assert warnings[0].code == "quality.bad_candidate_ratio"
    assert warnings[0].value == 0.10
```

- [ ] **Step 2: Implement the two rules only**

```python
BAD_QUALITY_REVIEW_RATIO = 0.10

@dataclass(frozen=True)
class QualityWarning:
    code: str
    value: float | int
    threshold: float | None
    affected_count: int
    ranges: tuple[tuple[int, int], ...]
    message: str
```

Read the existing `bad_quality_candidate` column from `frame_metrics.csv` and
the existing `low_motion_review` segment type from `segments.json`; do not
calculate new image metrics.

- [ ] **Step 3: Integrate risk status and approval**

After successful analysis, set `review_required` when warnings exist, otherwise `completed`. `approve_item` accepts only review-required items, verifies the current source/strategy snapshot, and stores `approved=True` plus a stripped note. Re-analysis resets approval.

- [ ] **Step 4: Run tests**

```powershell
python -m pytest tests/test_batch_quality.py tests/test_batch_session_runner.py -q
python -m ruff check scripts/frame_timing_agent/batch_quality.py scripts/frame_timing_agent/batch_session.py
```

- [ ] **Step 5: Commit**

```powershell
git add scripts/frame_timing_agent/batch_quality.py scripts/frame_timing_agent/batch_session.py tests/test_batch_quality.py tests/test_batch_session_runner.py
git commit -m "feat: flag explainable batch quality risks"
```

### Task 6: Add Explicit Batch Export

**Files:**
- Modify: `scripts/frame_timing_agent/batch_session.py`
- Modify: `scripts/frame_timing_agent/batch_timing_agent.py`
- Create: `tests/test_batch_session_export.py`

**Interfaces:**
- Consumes: Task 1 `export_run` and Task 5 approval fields.
- Produces: `BatchExportSummary` and `export_batch(state_path, progress_callback=None) -> BatchExportSummary`.

- [ ] **Step 1: Write failing export eligibility tests**

Test that export requires batch status finished, exports completed and approved review items, skips unresolved/failed items, persists after each item, blocks changed input, and preserves previous valid output on failure.

- [ ] **Step 2: Implement explicit sequential export**

```python
@dataclass(frozen=True)
class BatchExportSummary:
    exported: tuple[str, ...]
    skipped: tuple[str, ...]
    failed: tuple[str, ...]
```

Treat the CLI call or confirmed UI action as the explicit export instruction. Call `export_run` per eligible item; never call `run_timing_agent` again. Refresh existing reports and run `run_batch_artifact_health_check` after the export pass.

Update the existing batch human-review mode sentence to report the actual
`output_dir is not None` count instead of claiming that every successful item was
written when an export is partial. Add a regression assertion for the mixed
export case; do not introduce a second report format.

- [ ] **Step 3: Run tests**

```powershell
python -m pytest tests/test_batch_session_export.py tests/test_batch_artifact_health.py tests/test_ui_worker.py -q
```

- [ ] **Step 4: Commit**

```powershell
git add scripts/frame_timing_agent/batch_session.py scripts/frame_timing_agent/batch_timing_agent.py tests/test_batch_session_export.py
git commit -m "feat: export eligible batch results safely"
```

### Task 7: Add Agent-Safe Batch Commands

**Files:**
- Modify: `scripts/frame_timing_agent/tool_cli.py`
- Modify: `tests/test_tool_cli.py`
- Modify: `tests/test_package_metadata.py`

**Interfaces:**
- Consumes: `create_batch`, `run_batch`, `load_batch`, `approve_item`, and `export_batch`.
- Produces CLI actions: `batch create`, `batch run`, `batch status`, `batch approve`, `batch export` using the existing response envelope.

- [ ] **Step 1: Write failing subprocess contract tests**

Assert parseable JSON, empty stderr on success except existing stage logs, stable errors, next actions, explicit retry IDs, no implicit approval, and no implicit export:

```python
def test_batch_status_reports_next_actions(tmp_path, capsys):
    frame_dir = tmp_path / "frames"
    _write_frames(frame_dir)
    state_path = tmp_path / "output" / "batch" / "analysis" / "batch_state.json"
    create_code, _, _, _ = _invoke(
        capsys,
        ["batch", "create", "--frames", str(frame_dir), "--state", str(state_path)],
    )
    exit_code, payload, _, _ = _invoke(capsys, ["batch", "status", "--state", str(state_path)])
    assert create_code == 0
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["result"]["next_actions"] == ["run"]
```

Reuse the existing `_write_frames` and `_invoke` helpers already defined in
`tests/test_tool_cli.py`; do not create a second subprocess wrapper.

- [ ] **Step 2: Add nested parsers and dispatch**

Keep the current top-level lifecycle parsers unchanged. Add one `batch` parser with required action subparsers. `batch run --retry-item NAME` resets only named failed items before execution. Reuse `_response` and `_emit_error`.

- [ ] **Step 3: Run CLI and metadata tests**

```powershell
python -m pytest tests/test_tool_cli.py tests/test_package_metadata.py -q
```

- [ ] **Step 4: Commit**

```powershell
git add scripts/frame_timing_agent/tool_cli.py tests/test_tool_cli.py tests/test_package_metadata.py
git commit -m "feat: expose Agent-safe batch commands"
```

### Task 8: Add the Compact Batch Workspace and Explicit Resume UI

**Files:**
- Create: `scripts/frame_timing_agent/ui/batch_workspace.py`
- Modify: `scripts/frame_timing_agent/ui/main_window.py`
- Modify: `scripts/frame_timing_agent/ui/style.py`
- Create: `tests/test_ui_batch_workspace.py`
- Modify: `tests/test_ui_smoke.py`

**Interfaces:**
- Consumes: Tasks 2-6 batch APIs, existing `create_task`, `LineChart`, `SegmentBar`, `ThumbnailImage`, and `build_analysis_view`/`load_existing_run` data.
- Produces: `BatchWorkspace(QWidget)` hosted by `MainWindow`.

- [ ] **Step 1: Write failing Qt offscreen tests**

Test mode switching, deterministic list rows, selection detail, state-driven button enablement, pause event, explicit Continue after recovered state, review approval, export confirmation cancellation, and no automatic resume:

```python
def test_unfinished_batch_is_loaded_but_not_started(self):
    window = self.make_window_with_recovered_state()
    try:
        window.select_batch_mode()
        self.assertEqual(window.batch_workspace.current_state.status, BatchStatus.PAUSED)
        self.assertTrue(window.batch_workspace.continue_button.isEnabled())
        self.assertFalse(window.batch_workspace.is_running)
    finally:
        window.close()
```

Follow the existing `unittest.TestCase` and shared offscreen `QApplication` pattern from `tests/test_ui_smoke.py`; do not add `pytest-qt`.

- [ ] **Step 2: Build one list/detail workspace**

Use a `QSplitter` with a fixed-minimum-width item list and one detail pane. The list shows name, status, progress, warning count, and exported indicator. The detail pane reuses existing chart/segment/thumbnail widgets and adds only warnings, note, approve, retry, and artifact actions.

- [ ] **Step 3: Reuse the generic background task adapter**

Call the existing adapter on the existing single-thread pool:

```python
task = create_task(
    lambda progress: run_batch(
        self.state_path,
        progress_callback=progress,
        should_pause=self._pause_event.is_set,
    ),
    self._run_succeeded,
    self._run_failed,
    self._run_progress,
)
self.thread_pool.start(task)
```

Pause sets a `threading.Event`; `should_pause=event.is_set` is checked between items. Do not create a batch-specific QRunnable class.

- [ ] **Step 4: Add the same-level mode switch with minimal extraction**

Wrap the existing single-directory layout in `_build_single_workspace()` and place it beside `BatchWorkspace` in a `QStackedWidget`. Add a compact two-button segmented mode control. Do not rewrite existing single-run widgets or interaction methods.

- [ ] **Step 5: Persist only the last batch state path**

Use existing `QSettings` to store `last_batch_state_path`. On startup, load/recover it if present, display it, and wait. Never invoke `run_batch` during restoration.

- [ ] **Step 6: Run UI tests**

```powershell
$env:QT_QPA_PLATFORM='offscreen'
python -m pytest tests/test_ui_batch_workspace.py tests/test_ui_smoke.py tests/test_ui_history.py tests/test_ui_worker.py -q
```

- [ ] **Step 7: Commit**

```powershell
git add scripts/frame_timing_agent/ui/batch_workspace.py scripts/frame_timing_agent/ui/main_window.py scripts/frame_timing_agent/ui/style.py tests/test_ui_batch_workspace.py tests/test_ui_smoke.py
git commit -m "feat: add recoverable batch workspace"
```

### Task 9: Update Skill, README, and Run Full Acceptance Verification

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `tests/test_documentation_contract.py`
- Modify: `tests/test_package_metadata.py`
- Add after visual verification: real batch UI screenshot under the repository's existing documentation image directory.

**Interfaces:**
- Documents the CLI/UI behavior implemented in Tasks 1-8; introduces no production API.

- [ ] **Step 1: Write failing documentation assertions**

Require README and Skill text to mention CPU-only offline batches, explicit resume, explicit review/export, supported input modes, launch commands, and source safety. Require the screenshot path to exist.

- [ ] **Step 2: Update Skill instructions concisely**

Document this exact Agent sequence:

```text
batch create -> batch run/status -> report review_required ->
request explicit approval -> batch approve -> request explicit export -> batch export
```

State that the Agent must not auto-resume, approve, retry, or export.

- [ ] **Step 3: Update README without marketing overclaim**

Describe the project as a CPU-only offline Frame Timing Skill with a desktop UI, recoverable batch sessions, two explainable risk flags, and verified export. Include single/multiple/root input examples, artifact location, and both `frame-timing-ui` and structured CLI examples.

- [ ] **Step 4: Launch and visually verify the real UI**

Run:

```powershell
$env:QT_QPA_PLATFORM='windows'
frame-timing-ui
```

Use a small temporary batch to verify desktop and minimum supported window sizes. Check that text is not clipped, the item list remains stable during progress, charts populate immediately, representative frames use complete aspect-fit images, and review/export controls match state. Capture the real application window, not a mockup.

- [ ] **Step 5: Run focused acceptance scenarios**

Run tests proving: one bad item does not stop a 3-item sample; pause/reopen waits; Continue skips completed; review requires approval; export is explicit; source byte hashes are unchanged.

- [ ] **Step 6: Run full verification**

```powershell
python -m ruff check scripts tests
python -m pytest -q
python -m build
python -m twine check dist/*
```

Expected: zero lint errors, zero test failures, successful sdist/wheel build, and valid package metadata.

- [ ] **Step 7: Perform adversarial review before claiming completion**

Review the full diff for duplicated batch logic, Qt imports in core modules, hidden auto-actions, unsafe filesystem replacement, stale source approval, UI clipping, accidental algorithm changes, and untracked generated artifacts. Remove only files created by this branch and keep all user files untouched.

- [ ] **Step 8: Commit documentation and verified screenshot**

```powershell
git add SKILL.md README.md tests/test_documentation_contract.py tests/test_package_metadata.py docs
git commit -m "docs: document offline batch workflow"
```

## Execution Checkpoints

- After Task 1: review the extracted workflow for behavior parity before any batch feature depends on it.
- After Task 4: run and inspect a small two-directory batch before adding quality or UI.
- After Task 7: verify the Skill can complete the workflow through JSON CLI only.
- After Task 8: stop for user UI review before final screenshots and documentation.
- After Task 9: use `superpowers:requesting-code-review`, then `superpowers:finishing-a-development-branch` only after fresh verification passes.
