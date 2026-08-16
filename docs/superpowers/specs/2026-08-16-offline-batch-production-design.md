# Skill-First Offline Batch Design

Date: 2026-08-16
Status: Revised after complexity review, pending user approval

## 1. Design Position

Frame Timing Skill remains a local Skill with deterministic CLI tools. The
desktop UI is a human-facing adapter for the same capabilities, not the start of
a separate production platform.

The batch feature adds one practical capability: process several cleaned frame
directories in one recoverable session, review obvious risks, and explicitly
export selected results. It must reuse the existing analysis, report, audit, and
artifact-health code.

Target workload:

- Windows;
- CPU-only;
- local/offline execution;
- usually 700 to 1,000 frames per directory;
- one directory processed at a time;
- tens of directories per manually started batch, not distributed big data.

## 2. Complexity Review

The first design was broader than the project needs. This revision removes the
following unnecessary work:

- no separate `batch/` package with six domain files;
- no separate `quality/` package with four policy/report files;
- no three-axis item state machine for execution, review, and export;
- no immutable-manifest plus mutable-state protocol;
- no derived-batch concept;
- no attempt ledger or request-id idempotency subsystem;
- no persistent `pausing` or `interrupted` status;
- no speculative two-worker scheduler;
- no new standalone Agent Eval framework;
- no UI navigation rail for only two modes;
- no broad rewrite from the legacy batch pipeline to the newer service
  lifecycle in this feature.

These removals do not weaken the user-visible requirements. They avoid building
infrastructure for cloud, multi-user, or distributed scenarios that are outside
the project.

## 3. Existing Capabilities to Reuse

The repository already provides:

- `run_batch_timing_agent`;
- per-directory failure isolation;
- JSON and CSV batch summaries;
- batch human-review and review-dashboard artifacts;
- batch artifact-health verification;
- preview and write modes;
- single-run UI history and atomic history writes;
- source snapshot checks and transactional UI export;
- progress callbacks;
- `frame-timing-batch`, `frame-timing-health`, `frame-timing-tool`, and
  `frame-timing-ui` entry points.

The new implementation extends these capabilities. It must not create a second
report format, a second analysis algorithm, or a second strategy planner.

To persist after each item without duplicating the current batch implementation,
`batch_timing_agent.py` may expose its existing one-item execution and report
publication steps as internal helpers. The public `run_batch_timing_agent`
signature and behavior remain unchanged and call those same helpers.

One existing architectural issue affects the work: source snapshot validation
and the safe export transaction currently live under `ui/`. Batch Skill commands
must not depend on PySide6, so the pure-Python analysis/export transaction will
be extracted into one small core workflow and the existing UI worker will
delegate to it. Its behavior will not be changed.

## 4. Goals

The first release shall:

1. Accept multiple explicitly selected frame directories.
2. Accept one root directory and discover frame directories below it.
3. Deduplicate and deterministically order discovered paths.
4. Analyze each item independently with the existing CPU pipeline.
5. Save progress after every item.
6. Show unfinished batches after restart without automatically resuming them.
7. Resume only after the user or Agent explicitly requests it.
8. Skip items that already completed successfully.
9. Distinguish execution failure from a small set of explainable quality risks.
10. Require explicit approval for quality-risk items before export.
11. Export only after the full analysis pass and an explicit confirmation.
12. Preserve the source directories and all existing single-directory behavior.

## 5. Non-Goals

The first release will not include:

- GPU acceleration or a CPU/GPU selector;
- raw video decoding or frame extraction;
- cloud services, database, accounts, permissions, or remote queues;
- parallel directory processing;
- forced cancellation during one directory analysis;
- learned quality models;
- per-frame manual editing;
- configurable quality-rule editors;
- automatic retry or automatic export;
- general workflow-engine abstractions;
- replacement of `auto_timing_agent` or core algorithms;
- unrelated UI redesign outside the batch workspace.

## 6. Minimal Architecture

Only three small core responsibilities are added:

```text
batch_discovery.py  -> find and normalize input directories
batch_session.py    -> persist progress and coordinate existing batch operations
batch_quality.py    -> summarize two existing risk signals
run_workflow.py      -> existing source snapshot and safe analysis/export transaction
```

The existing modules remain authoritative:

```text
batch_timing_agent.py     -> item execution and batch reports
batch_artifact_health.py  -> artifact verification
auto_timing_agent.py      -> existing analysis pipeline
ui/worker.py              -> Qt task adapter only
```

The exact helper names may change during the implementation plan, but the
following boundaries are required:

- no Qt imports in discovery, session, quality, or export core logic;
- no batch business decisions in widgets;
- no quality calculations duplicated in UI code;
- existing public CLI behavior remains compatible.

### 6.1 Targeted Workflow Extraction

The source-snapshot validation, analysis-time input change check,
staging-directory apply, output verification, and atomic replacement currently
implemented under `ui/` will be moved behind pure-Python functions. The current
single-directory UI and the new batch session will call those functions. UI-only
thumbnail persistence remains under `ui/`.

This is the only planned cross-cutting refactor. It is required so the Skill can
export safely without importing PySide6. It must be covered by the current UI
export tests before batch code uses it.

## 7. Input Discovery

Batch mode supports:

1. Repeated folder selection or drag-and-drop of explicit frame directories.
2. Recursive discovery below one selected root.

Rules:

- Explicitly selected directories are accepted when they contain supported
  frame images.
- Recursive discovery prefers directories named `clean_frames`.
- Direct child directories containing supported images are also accepted.
- Hidden, cache, artifact, `output`, and `output_frames` directories are ignored.
- Canonical absolute paths are used for deduplication.
- A parent candidate is not added when its preferred child `clean_frames` is
  already selected.
- Results are sorted by normalized path.
- Invalid and ignored candidates return short reason codes for UI and CLI.

Discovery only reads directory metadata. It does not copy or decode all images.
The existing frame loader remains responsible for strict input validation.

## 8. Batch Session

Each batch stores one atomic state document inside the existing batch artifact
root:

```text
<artifact_root>/
  analysis/
    batch_state.json
    batch_summary.json
    batch_summary.csv
    human_review.md
    review_dashboard.md
    maintenance_report.json
    maintenance_report.md
  <existing-item-name>/
    analysis/
    output_frames/        # only after explicit export
```

This preserves the current artifact layout. There is no new `items/` hierarchy,
duplicate report set, or separate manifest file.

`batch_state.json` contains:

- schema version;
- batch ID and creation/update times;
- FPS and existing analysis options;
- ordered item path, safe item name, and optional source snapshot identity after
  analysis;
- item status, progress, last error, retry count, risk summary, approval note,
  and output path when exported;
- batch status and pause request.

State writes use the same temporary-file and replace approach already used by UI
history. A small standard-library lock file prevents two processes from running
the same batch concurrently. Multi-user locking and distributed leases are out
of scope.

### 8.1 Statuses

Batch status has four values:

- `ready`;
- `running`;
- `paused`;
- `finished`.

Item status has five values:

- `pending`;
- `running`;
- `completed`;
- `review_required`;
- `failed`.

Export is not another state machine. An item is exported when a verified
`output_frames` path and execution audit exist. Aggregate counts such as
"finished with issues" are derived from item statuses rather than persisted as
additional statuses.

### 8.2 Pause, Restart, and Retry

- Pause means finish the current directory and do not start the next one.
- The pause request may be persisted as a boolean; `pausing` is not a status.
- On application restart, a persisted `running` item is reset to `pending` and
  its retry count is incremented.
- The UI lists the unfinished batch and waits for Continue.
- Continue processes only `pending` items.
- A failed item is retried only after an explicit Retry action.
- Successful and review-required analyses are not repeated.
- The first release retains only retry count and last error, not a full attempt
  history.

## 9. Processing Flow

### 9.1 Create

1. Discover or accept explicit directories.
2. Validate that at least one unique candidate exists.
3. Allocate the existing batch artifact root and safe item names.
4. Atomically write `batch_state.json` with all items pending.

Creation does not hash every frame. Source snapshots are captured immediately
before and after each item's actual analysis by the extracted workflow, avoiding
an extra full read of the entire batch.

The item list is fixed after Start. To add directories, the user creates another
batch. There is no derived-batch model.

### 9.2 Analyze

1. Mark the next item running and persist state.
2. Run the extracted analysis workflow in preview mode (`write=False`), including
   the existing before/after source snapshot check.
3. Publish the existing per-item artifacts.
4. Evaluate the minimal risk summary.
5. Mark the item completed, review-required, or failed and persist state.
6. Refresh the existing batch summary and review artifacts.
7. Continue after an item failure.
8. Stop at the next item boundary when pause is requested.

The coordinator runs one directory at a time. No worker-count option is exposed.

### 9.3 Review

After all analysis items stop, the UI highlights failed and review-required
items. A reviewer can:

- approve a review-required item for export;
- leave it unresolved;
- add a short note.

Approval is valid only while the saved source snapshot and strategy identity
still match. Existing snapshot verification runs again before export. No new
general approval-policy engine is introduced.

### 9.4 Export

1. After the batch reaches `finished`, the user clicks Export eligible results.
2. The UI shows eligible, unresolved, and failed counts.
3. The user confirms.
4. The extracted core export helper verifies the source snapshot, uses a staging
   directory, applies the existing strategy, audits the output, and replaces the
   final output only after verification.
5. State and existing batch reports are refreshed after each item.

Eligible means `completed`, or `review_required` with explicit approval. Failed,
unresolved, changed-source, and verification-failed items are skipped and
reported. Export never starts automatically after analysis.

## 10. Minimal Quality Review

The first release does not attempt to score reconstruction quality. It exposes
only risk signals already computed by the current pipeline:

1. `bad_quality_candidate` ratio from `frame_metrics.csv`.
2. Presence and extent of `low_motion_review` segments from `segments.json`.

Default review rules:

- mark review-required when bad-quality candidates are at least 10% of analyzed
  frames;
- mark review-required when one or more `low_motion_review` segments exist.

The 10% default is a transparent product threshold, not a claim of universal
image-quality validity. It will be a named constant with focused tests. Changing
it in the first release requires code/config review, not an end-user rule editor.

Each warning contains:

- stable code;
- measured value;
- threshold when applicable;
- affected frame count or source range;
- short explanation.

Input errors and artifact-health failures remain hard failures. Other metrics
such as exposure percentiles, duplicate detection, retention risk, or learned
quality models are deferred until real batch results show they are needed.

## 11. Skill and CLI Surface

The existing `frame-timing-batch` command remains compatible. The Agent-safe
surface adds only the actions needed by the Skill:

```text
frame-timing-tool batch create
frame-timing-tool batch run
frame-timing-tool batch status
frame-timing-tool batch approve
frame-timing-tool batch export
```

`create` accepts explicit directories or a discovery root. `run` starts or
explicitly continues an existing batch; it accepts selected failed item IDs when
an explicit retry is requested. A separate discovery command, resume alias,
review-policy command, and request-id subsystem are not required in the first
release.

Responses reuse the existing JSON envelope:

```text
schema_version, status, run_id, artifacts, result, error
```

The result includes batch/item statuses, risk summaries, progress counts, and
allowed next actions. Stable error codes distinguish invalid input, busy batch,
analysis failure, stale source, unsafe export, and artifact-health failure.

The Skill instructions remain short:

1. create or inspect the batch;
2. run or explicitly resume it;
3. report failures and review warnings;
4. request human approval where needed;
5. export only after explicit instruction;
6. report artifact paths and final counts.

Agent behavior is tested through normal pytest CLI contract tests. No separate
eval service or framework is added.

## 12. Desktop UI

The current window gains a same-level mode switch:

- Single Directory;
- Batch Processing.

The existing single-directory workspace remains unchanged. Batch mode uses a
compact two-column task workspace:

```text
Batch item list | Selected item detail and actions
```

There is no navigation rail, separate settings page, or multi-page dashboard in
the first release.

### 12.1 Batch List

The list shows:

- directory name;
- pending/running/completed/review/failed status;
- item progress;
- warning count;
- exported indicator.

The header shows overall completed count and the current item. Status filtering,
sorting controls, and bulk row editing are deferred.

### 12.2 Selected Item

The detail area reuses existing result components where practical:

- time-series chart;
- representative frames;
- strategy summary;
- output/artifact paths.

For a review-required item it also shows the two supported risk explanations,
an approval action, and an optional note. It does not provide per-frame strategy
editing.

### 12.3 Actions

Batch mode exposes only:

- Add directories;
- Discover root;
- Start;
- Pause after current item;
- Continue;
- Retry selected failure;
- Export eligible results;
- Open batch artifacts.

Actions are enabled from persisted state. Closing and reopening the application
must display an unfinished batch but must not start it.

The visual style follows the already selected compact developer-tool direction:
light neutral surfaces, one-pixel separators, small radii, restrained blue
emphasis, and status colors only for state. This feature does not trigger another
full redesign of the existing single-directory UI.

## 13. Error Handling

- One item failure does not stop later items.
- Errors are stored as safe code plus concise message.
- A running item found after restart becomes pending for explicit resume.
- A busy lock returns a clear error instead of starting a second runner.
- Source changes invalidate approval and block export.
- Export verification failure leaves the previous valid output untouched.
- UI shows the batch artifact location for diagnosis and does not display raw
  tracebacks by default.

## 14. File Scope

Expected new files are limited to approximately:

```text
scripts/frame_timing_agent/batch_discovery.py
scripts/frame_timing_agent/batch_session.py
scripts/frame_timing_agent/batch_quality.py
scripts/frame_timing_agent/run_workflow.py
scripts/frame_timing_agent/ui/batch_workspace.py
```

Batch UI background work reuses the existing generic `ui.worker.create_task`
adapter and its single-thread pool. A batch-specific Qt worker is unnecessary.

Expected modified files include:

```text
scripts/frame_timing_agent/batch_timing_agent.py
scripts/frame_timing_agent/tool_cli.py
scripts/frame_timing_agent/ui/main_window.py
scripts/frame_timing_agent/ui/run_artifacts.py
scripts/frame_timing_agent/ui/worker.py
README.md
SKILL.md
```

The implementation plan must justify any additional production module. Tests do
not count against this limit. Large rewrites of existing modules are rejected.

## 15. Testing

Focused tests cover:

- explicit and recursive discovery, ignores, ordering, and deduplication;
- atomic state persistence and busy-lock rejection;
- one failure not stopping later items;
- pause at an item boundary;
- restart recovery and explicit continue;
- successful items not being repeated;
- the two quality-warning rules and threshold boundaries;
- approval invalidation after source change;
- explicit export and staging verification;
- existing batch CLI compatibility;
- Agent-safe JSON command contracts;
- Qt offscreen batch-mode smoke and state-driven actions;
- source frame bytes remaining unchanged.

The full existing suite must remain green. Agent scenarios are ordinary tests,
not a new testing framework.

## 16. Delivery Order

Implementation is divided into three increments:

1. **Batch session core:** extract the pure-Python analysis workflow, then add
   discovery, state, sequential run, pause/resume, and compatibility tests.
2. **Review and export:** two quality signals, approval, extracted safe export,
   and Agent-safe commands.
3. **Batch UI and documentation:** mode switch, list/detail workspace, unfinished
   batch reopening, README screenshot, and concise Skill instructions.

Each increment is independently reviewable and keeps existing behavior working.

## 17. Acceptance Criteria

The release is accepted when:

1. Explicit multiple directories and recursive root discovery produce the same
   deterministic item model.
2. One invalid item does not stop the rest of a local batch.
3. Progress is saved after every item.
4. Closing and reopening shows unfinished work without automatically resuming.
5. Explicit Continue skips successful items.
6. Only the two documented existing-signal rules cause quality review.
7. Export requires explicit confirmation and skips unresolved or failed items.
8. Source changes or verification failures block export without replacing a
   previous valid output.
9. The Skill receives structured status, risks, next actions, and artifact paths.
10. Existing single-directory UI and all current CLI entry points remain usable.

## 18. Deferred Work

The following work requires evidence from real batch use before design begins:

- more quality rules;
- learned quality models;
- GPU acceleration;
- parallel item workers;
- database-backed history;
- per-frame manual strategy editing;
- batch comparison dashboards;
- cloud or remote execution.

The public description remains accurate and modest: a CPU-only offline Frame
Timing Skill with a desktop UI, recoverable batch sessions, explainable risk
flags, and verified export.
