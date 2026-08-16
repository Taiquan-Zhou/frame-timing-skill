# Offline Batch Production Design

Date: 2026-08-16
Status: Approved design, pending implementation plan

## 1. Purpose

Extend Frame Timing Skill from a single-directory analysis tool into a CPU-only,
offline batch production workflow while preserving the current algorithms,
single-directory CLI, desktop UI, and Agent-safe lifecycle.

The first release targets Windows machines without a discrete GPU. A typical
input directory contains 700 to 1,000 cleaned image frames. The system must
handle multiple independent frame directories, isolate failures, persist
progress, require explicit human decisions for quality risks, and make every
export auditable.

The project should demonstrate capabilities relevant to AI application roles:

- deterministic tool contracts;
- resumable task orchestration;
- explainable quality gates;
- human-in-the-loop decisions;
- safe artifact handling;
- Agent-facing structured interfaces and evals.

This is not a distributed big-data platform. It is a local, auditable batch
pipeline for moderate CPU workloads.

## 2. Goals

The first batch production release shall:

1. Accept one frame directory, several explicitly selected frame directories,
   or a root directory containing multiple frame directories.
2. Discover valid inputs deterministically and deduplicate canonical paths.
3. Analyze every accepted directory independently on CPU.
4. Continue processing other items when one item fails.
5. Persist batch and item state so an interrupted batch can be reopened.
6. Require the user to click Continue before an interrupted batch resumes.
7. Separate execution failures from quality review warnings.
8. Allow a reviewer to approve or retain the review status with an audit note.
9. Analyze the full batch before any bulk export.
10. Export only passed or explicitly approved items after user confirmation.
11. Expose the same batch service through desktop UI and structured CLI
    adapters without placing business logic in either adapter.
12. Keep source frame directories read-only and preserve existing
    single-directory behavior.

## 3. Non-Goals

The first release will not include:

- GPU acceleration or a cosmetic CPU/GPU switch;
- raw video upload, decoding, or frame extraction;
- cloud execution, remote queues, or user accounts;
- a database or distributed scheduler;
- automatic export immediately after analysis;
- forced cancellation of a directory while its analysis is running;
- learned quality models or universal image-quality scores;
- editing individual frame decisions in the batch UI;
- automatic retry loops without an explicit user or Agent action;
- unrelated refactors of the existing analysis and strategy algorithms.

Future GPU support is acceptable only after a learned model or measurable
compute bottleneck exists and CPU/CUDA benchmark results justify an optional
runtime such as ONNX Runtime CPU/CUDA.

## 4. Existing System Compatibility

The existing lifecycle remains authoritative:

```text
analyze -> plan -> validate -> apply -> verify
```

The existing `run_batch_timing_agent` behavior, JSON/CSV summaries, artifact
health checks, human review artifacts, and console scripts remain compatible.
The current `batch_timing_agent.py` becomes a compatibility adapter over the new
batch service rather than continuing to accumulate orchestration logic.

The following entry points must continue to work:

- `frame-timing-tool`;
- `frame-timing-batch`;
- `frame-timing-health`;
- `frame-timing-ui`.

Existing analysis contracts and policy calculations are reused. The batch and
quality layers coordinate those modules; they do not duplicate or reinterpret
the core algorithm inside UI code.

## 5. Architecture

The implementation is divided into four boundaries:

```text
Desktop UI / Agent CLI
        |
        v
Batch application service
        |
        +---- Batch state and artifact store
        |
        +---- Quality evaluator
        |
        v
Existing analyze / plan / validate / apply / verify services
```

### 5.1 Batch Domain

Proposed package:

```text
scripts/frame_timing_agent/batch/
  __init__.py
  models.py
  discovery.py
  manifest.py
  state_store.py
  runner.py
  service.py
```

- `models.py` defines typed batch, item, attempt, status, and review contracts.
- `discovery.py` discovers and canonicalizes frame directories.
- `manifest.py` creates and validates immutable batch manifests.
- `state_store.py` owns locking and atomic mutable-state persistence.
- `runner.py` executes one item at a time and reports progress through callbacks.
- `service.py` exposes the application operations used by UI and CLI.

The modules may be combined during implementation if the resulting files remain
small and cohesive. The boundary between domain logic and adapters is mandatory;
the exact file count is not.

### 5.2 Quality Domain

Proposed package:

```text
scripts/frame_timing_agent/quality/
  __init__.py
  contracts.py
  evaluator.py
  thresholds.py
  report.py
```

- `contracts.py` defines structured gate results and decisions.
- `evaluator.py` evaluates existing analysis and strategy artifacts.
- `thresholds.py` owns versioned CPU quality policy configuration.
- `report.py` writes machine-readable and human-readable summaries.

Quality evaluation consumes existing artifacts. It does not alter selected
sources or claim to independently prove reconstruction quality.

### 5.3 Desktop UI

The desktop UI remains a PySide6 adapter under
`scripts/frame_timing_agent/ui/`. Batch-specific views are separated from the
existing single-directory workspace:

```text
ui/
  shell.py
  navigation.py
  single_workspace.py
  batch_workspace.py
  quality_inspector.py
  theme.py
```

These names are design guidance, not a requirement to rewrite working widgets.
Existing UI modules should be extracted only when needed to support the new
shell without changing current behavior.

### 5.4 Agent Interface

The Agent interface is a thin JSON CLI adapter over the batch application
service. It must return deterministic schemas, stable error codes, explicit
state transitions, and artifact paths. It must never infer approval or export
permission from natural language inside the core service.

## 6. Input Discovery

The UI and CLI support three equivalent input modes:

1. One explicitly selected frame directory.
2. Multiple explicitly added frame directories, including repeat selection and
   drag-and-drop in the desktop UI.
3. One batch root scanned recursively.

Discovery rules:

- A directory named `clean_frames` containing supported images is preferred.
- A direct child directory containing supported images is accepted when it is
  not itself an ignored or generated directory.
- Recursive discovery of named `clean_frames` directories is supported.
- Hidden directories, output directories, artifact roots, caches, and generated
  frame directories are ignored.
- Canonical absolute paths are used for deduplication.
- If a preferred `clean_frames` directory is found beneath a candidate parent,
  the parent is not also added as a duplicate frame source.
- Results are sorted deterministically by normalized path.
- Discovery reports accepted, duplicate, ignored, and invalid candidates with
  machine-readable reasons.

Input discovery does not copy or mutate source images.

## 7. State Model

Analysis, quality, and export are represented separately so the UI never treats
"analysis completed" as "safe and already exported."

### 7.1 Item State

Execution status:

- `pending`: accepted but not started;
- `running`: currently being analyzed;
- `passed`: analysis and hard checks completed;
- `review_required`: analysis completed but quality risks require a decision;
- `failed`: a hard error prevents completion or export;
- `interrupted`: the process ended while this item was running.

Review status:

- `not_required`;
- `pending`;
- `approved`;
- `retained_for_review`.

Export status:

- `not_requested`;
- `eligible`;
- `exporting`;
- `exported`;
- `export_failed`.

An item is export-eligible only when all hard checks pass and either no review is
required or the current quality result has an explicit approval record.

### 7.2 Batch State

- `ready`: created and not started;
- `running`: processing items;
- `pausing`: the current item may finish, but no new item will start;
- `paused`: stopped at an item boundary;
- `completed`: all items reached terminal analysis states with no unresolved
  issues;
- `completed_with_issues`: all items reached terminal analysis states and at
  least one item failed or still requires review.

Pause is cooperative and occurs between directories. The first release does not
kill OpenCV work in progress. Closing the application while an item is running
causes that item to become `interrupted` on the next open.

### 7.3 Resume and Retry

- Reopening the application lists unfinished batches.
- No unfinished batch resumes automatically.
- The user must select a batch and click Continue.
- On Continue, `interrupted` items become new pending attempts.
- Successfully completed items are not analyzed again.
- Retry is explicit and records a new attempt while retaining previous errors.
- Reopening or retrying never erases review decisions or prior audit records.

## 8. Processing Flow

### 8.1 Create Batch

1. Discover and validate candidate frame directories.
2. Build an immutable manifest with batch ID, canonical inputs, FPS, config
   revision, creation time, and input identity data.
3. Create initial item state records.
4. Persist the manifest and state before starting work.

Adding input directories after creation produces a new batch or a derived batch;
the original manifest is never edited in place.

### 8.2 Analyze Batch

1. Acquire the batch writer lock.
2. Select the next `pending` item in manifest order.
3. Mark it `running` and persist immediately.
4. Invoke existing analysis and strategy services.
5. Validate produced artifacts and run the quality evaluator.
6. Persist the item result and summary immediately.
7. Continue to the next item even when the current item fails.
8. Stop after the current item when pause was requested.
9. Produce batch JSON, CSV, and Markdown summaries when all work stops.

The first release defaults to one worker. A two-worker option may be considered
only after benchmark and state-store tests show a useful throughput gain without
excessive CPU, memory, or disk pressure.

### 8.3 Review Batch

After analysis, the reviewer sees all failed and `review_required` items in one
queue. For each quality warning, the UI shows the measured value, policy
threshold, affected frames or intervals, and explanation.

The reviewer may:

- approve the current quality result for export;
- retain `review_required` without approval;
- add or update an audit note.

Approval is tied to the input digest, analysis artifact digest, quality policy
revision, and quality result digest. Re-analysis or input changes invalidate the
approval.

### 8.4 Export Batch

1. The user clicks Export all passed.
2. The UI shows the eligible, unresolved, and failed counts.
3. The user confirms the export operation.
4. Each eligible item is revalidated against its source and approved artifacts.
5. Existing apply and verify services write to a staging output directory.
6. Verified output replaces or becomes the final `output_frames` directory.
7. Export status and verification evidence are persisted per item.

The system never exports failed, unresolved, stale, or unverified items. One
export failure does not invalidate exports that already completed successfully.

## 9. Quality Gates

Quality gates are explainable rules over existing artifacts. They are divided
into hard blockers and review warnings.

### 9.1 Hard Blockers

Examples include:

- source directory missing or empty;
- unreadable frame files;
- inconsistent frame dimensions where the existing analyzer cannot proceed;
- invalid or duplicate source indices;
- invalid FPS;
- output path overlapping the source directory;
- source digest changed after analysis;
- required artifact missing, invalid, or digest-mismatched;
- apply or output verification failure.

A hard blocker sets the item to `failed` and prevents export.

### 9.2 Review Warnings

Technical image warnings may include:

- high blur candidate ratio;
- excessive underexposure or overexposure;
- low contrast distribution;
- high near-duplicate ratio;
- insufficient usable-frame count.

Reconstruction coverage warnings may include:

- low motion-confidence coverage;
- long static intervals;
- excessive extreme-motion coverage;
- many `review_required` intervals;
- suspicious retention ratio;
- excessive source-index or time gap;
- insufficient active-motion or protected-endpoint coverage.

Thresholds must combine interpretable measurements rather than a single
universal sharpness cutoff. Depending on the metric, evaluation may use:

- absolute values;
- within-directory percentiles;
- affected-frame ratios;
- longest continuous affected interval.

Each gate returns a structured result:

```json
{
  "code": "quality.blur_ratio_high",
  "severity": "warning",
  "value": 0.31,
  "threshold": 0.25,
  "affected_frames": [12, 13, 14],
  "decision": "review_required",
  "message": "Blur candidates exceed the configured frame ratio."
}
```

Codes and fields are stable machine contracts. Human messages may be localized
without changing decisions.

## 10. Persistence and Artifact Layout

Each batch uses an isolated artifact root:

```text
output/frame_timing_batches/<batch_id>/
  batch_manifest.json
  batch_state.json
  batch_report.json
  batch_report.csv
  batch_report.md
  logs/
  items/<safe-name-hash>/
    analysis/
    quality.json
    review.json
    output_frames/
```

Rules:

- `batch_manifest.json` is immutable after creation.
- `batch_state.json` is mutable and written atomically through temporary file,
  flush, and replace semantics.
- Only one process may hold the writer lock for a batch.
- Every state transition is persisted before the next item starts.
- Item directories use a readable safe name plus a short canonical-path hash to
  avoid collisions.
- Source identity and artifact digests bind analysis, review, and export.
- Output is staged and verified before becoming final.
- Logs may contain paths, counters, durations, codes, and exceptions, but never
  image bytes or secrets.
- Source images remain read-only and byte-identical.

If atomic replacement is unavailable because of filesystem or permission
constraints, the operation fails with an explicit persistence error instead of
falling back to a partial write.

## 11. Application Service Contract

The non-Qt, non-LLM service exposes operations equivalent to:

```text
discover(request) -> DiscoveryResult
create_batch(request) -> BatchSnapshot
run_batch(batch_id, progress_callback) -> BatchSnapshot
inspect_batch(batch_id) -> BatchSnapshot
resume_batch(batch_id, progress_callback) -> BatchSnapshot
retry_items(batch_id, item_ids) -> BatchSnapshot
record_review(batch_id, item_id, decision, note) -> ItemSnapshot
export_items(batch_id, item_ids, progress_callback) -> ExportSummary
```

Callbacks are optional typed events and contain no Qt objects. Cancellation is a
cooperative request to pause at the next item boundary.

All mutating calls validate allowed state transitions. Invalid transitions
return stable error codes and leave persisted state unchanged.

## 12. Agent CLI and Skill Contract

The structured CLI will provide operations equivalent to:

```text
frame-timing-tool batch discover
frame-timing-tool batch create
frame-timing-tool batch run
frame-timing-tool batch status
frame-timing-tool batch resume
frame-timing-tool batch retry
frame-timing-tool batch review
frame-timing-tool batch export
```

Requirements:

- JSON input and output schemas are versioned.
- Successful responses contain IDs, current states, artifact paths, and next
  allowed actions.
- Failures contain stable error codes, a human message, and retryability.
- Destructive or irreversible actions require explicit arguments.
- `review approve` and `export` are distinct operations.
- The Agent cannot silently bypass a quality warning or verification failure.
- Repeating a read operation is idempotent; repeating a mutating request with
  the same request ID must not create duplicate attempts or exports.

The Skill documentation teaches an Agent to inspect state, present unresolved
quality findings, request human approval when required, and export only after an
explicit instruction.

## 13. Desktop UI Design

The main window has two equal-level modes:

- Single Directory;
- Batch Processing.

The batch mode uses a compact task-workspace layout inspired by modern developer
tools, without copying product branding:

```text
Narrow navigation | Batch/task list | Main analysis workspace | Quality inspector
```

Visual rules:

- light neutral background;
- one-pixel separators instead of stacked decorative cards;
- 4 to 6 pixel radius for framed controls;
- low-saturation surfaces;
- blue reserved for selection and primary actions;
- green, amber, and red used only for state;
- compact typography and stable row heights;
- icons from the existing UI icon system where available;
- no marketing hero, oversized headings, decorative gradients, or nested cards.

### 13.1 Batch List

The task list shows batch progress, item name, state, warning count, export state,
duration, and retry availability. It supports status filters and selects one item
without opening a separate modal workflow.

### 13.2 Main Workspace

Selecting an item reuses the existing single-directory analysis presentation:
time-series chart, representative frames, strategy summary, and artifacts. A
Back to batch action returns to the aggregate view without losing current batch
state.

### 13.3 Quality Inspector

The right inspector lists hard errors and quality warnings with measured values,
thresholds, affected ranges, and audit history. It contains explicit Approve and
Keep for review actions plus a note field. Approval is disabled for hard errors
or stale results.

### 13.4 Batch Actions

The persistent command area includes:

- Add directories;
- Discover from root;
- Start analysis;
- Pause after current item;
- Continue;
- Retry selected failures;
- Export all passed;
- Open batch artifacts.

Buttons are enabled from state, not appearance. Export all passed always opens a
confirmation summary and never starts automatically when analysis finishes.

## 14. Error Handling

- An item error is recorded and processing continues with the next item.
- Disk-full, permission, lock, invalid-input, source-changed, and artifact-health
  failures use distinct error codes.
- A process restart converts persisted `running` items to `interrupted` during
  recovery inspection.
- An unfinished batch is displayed on startup but remains idle until Continue.
- Retry records attempt number, start/end time, error, and outcome.
- Quality warnings are not represented as exceptions or execution failures.
- Verification failures prevent final export and retain staging diagnostics.
- UI errors show a concise message and a link to the relevant batch artifact or
  log; they do not expose raw tracebacks by default.

## 15. Testing Strategy

### 15.1 Unit Tests

- discovery acceptance, ignore rules, precedence, sorting, and deduplication;
- legal and illegal state transitions;
- export eligibility rules;
- quality gate boundary values and affected intervals;
- manifest immutability and digest binding;
- atomic state writes and lock behavior;
- review invalidation after source or artifact changes;
- JSON schema serialization and stable error codes.

### 15.2 Integration Tests

- temporary image directories covering single, explicit-multiple, and recursive
  discovery modes;
- a mixed batch where one item fails and later items still complete;
- pause after current item and explicit resume;
- simulated crash recovery from `running` to `interrupted`;
- retry without repeating successful items;
- staged apply and verify with source-byte comparison;
- explicit approval followed by export;
- stale approval rejected after input change;
- existing `frame-timing-batch` compatibility behavior.

### 15.3 UI Tests

- Qt offscreen startup for both modes;
- unfinished-batch prompt without automatic resume;
- list selection and inspector binding;
- state-driven button enablement;
- review and export confirmation flows;
- no business decision reconstructed in UI tests.

### 15.4 Agent Evals

Eval scenarios verify that an Agent:

- discovers and creates a batch from valid requests;
- reports partial failures without claiming total success;
- does not resume, approve, retry, or export without the required instruction;
- surfaces quality warnings and asks for human decisions;
- resumes interrupted work without reprocessing passed items;
- rejects stale artifacts and explains the next allowed action;
- produces parseable schema-compliant outputs.

## 16. Delivery Order

Implementation is split into five reviewable increments:

1. Batch domain core: discovery, manifest, persisted state, runner, resume, retry.
2. Quality gates: structured rules, reports, review decisions, export eligibility.
3. Agent-safe interface: JSON CLI operations, compatibility adapter, schemas.
4. Desktop UI shell: mode switch, task list, main workspace, quality inspector,
   explicit batch actions.
5. Skill and evals: Agent workflow documentation and deterministic scenarios.

Each increment must preserve the baseline test suite and add focused tests before
the next increment begins.

## 17. Acceptance Criteria

The release is accepted when all of the following are demonstrated on a CPU-only
Windows machine:

1. Single, explicit-multiple, and recursive-root inputs produce deterministic,
   deduplicated item lists.
2. In a 16-item batch, one invalid item fails without stopping the other 15.
3. Closing during analysis leaves a recoverable unfinished batch.
4. Reopening shows that batch and waits for an explicit Continue action.
5. Continue does not repeat successfully completed items.
6. Quality risks become `review_required` with measurements and explanations.
7. Export never starts automatically and requires user confirmation.
8. Only passed or explicitly approved, freshly validated items are exported.
9. Every export can be traced to input identity, analysis artifacts, quality
   results, review decision, and output verification.
10. Existing single-directory CLI, Skill flow, and desktop UI remain functional.

## 18. Risks and Controls

- **Overclaiming reconstruction quality:** label rules as risk indicators, retain
  human review, and avoid a universal quality score.
- **UI and service divergence:** make the typed batch service the only source of
  state and allowed actions.
- **Corrupt resume state:** use a single writer lock, atomic replacement, and
  recovery tests with fault injection.
- **CPU resource saturation:** default to one worker and benchmark before adding
  concurrency.
- **Artifact collisions:** isolate each item by safe name plus path hash.
- **Stale approval:** bind approval to source, analysis, policy, and quality
  digests.
- **Scope growth:** defer GPU, video decoding, database, cloud execution, and
  per-frame editing.

## 19. Documentation Outcome

When implementation is complete, README updates should describe:

- the CPU-only offline batch use case;
- supported input modes;
- the review and explicit export workflow;
- resume behavior;
- CLI and desktop UI launch commands;
- artifact layout and safety guarantees;
- an actual batch UI screenshot;
- a concise Agent Skill example and eval summary.

The public positioning should be accurate: "CPU-only offline batch and
Agent-safe frame quality analysis pipeline," not a generic large-scale video
platform.
