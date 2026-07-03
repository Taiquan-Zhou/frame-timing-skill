# Task 9 Human Review Service Design

## Goal

Complete Task 9 with observable, case-by-case evidence and preserve a reusable local review interface that a larger pipeline can launch after exporting frames or a video.

## Scope

- Do not change motion, static, jitter, blur, planning, or validation algorithms.
- Run `coverage_first` through `analyze -> plan -> validate -> apply -> verify -> health`.
- Run `balanced` and `jitter_reduction` only through analysis, planning, validation, and visual sequence comparison.
- Review the six current real cases and one deterministic synthetic static baseline.
- Keep real detected static ranges as primary evidence. The synthetic case only proves the zero-motion baseline.
- Record per-policy human conclusions without claiming statistical accuracy on unknown videos.

## Architecture

### Review session builder

A focused module builds a local review session from existing typed artifacts. It must not reimplement analysis, planning, validation, application, or verification rules.

Each case contains:

- a case identifier and display name;
- source frames;
- optional exported video;
- the complete `coverage_first` artifact root;
- `balanced` and `jitter_reduction` strategy and validation artifacts;
- machine-detected ranges and frame metrics;
- a dedicated review output directory.

Absolute local paths may exist only in process memory or an ignored local session configuration. They must not appear in browser payloads, benchmark results, reports, or saved human conclusions.

### Local review server

Expose a reusable Python API and CLI:

```python
handle = start_review_server(
    session=session,
    host="127.0.0.1",
    port=0,
    open_browser=True,
)
```

```powershell
frame-timing-review `
  --session output/review/session.json `
  --host 127.0.0.1 `
  --port 0 `
  --open-browser
```

The server prints one machine-readable JSON object containing the selected loopback URL. The returned Python handle exposes the URL and a deterministic `close()` operation so a parent Agent or pipeline can control its lifecycle.

The first version binds only to loopback addresses. Remote access, authentication, upload, and arbitrary filesystem browsing are out of scope.

### Safe media access

The server exposes only files registered in the current review session. Requests use opaque case and media identifiers rather than filesystem paths.

- Reject path traversal and unknown media identifiers.
- Serve source and output frames without modifying them.
- Support byte ranges for browser-playable exported video.
- Fall back to frame-sequence playback when the browser cannot decode the exported video.
- Never add overlays, labels, or watermarks to source or model-input files.

### Human result storage

Media and pipeline artifacts are read-only. The only write endpoint atomically replaces a policy-specific human review draft inside the configured review output directory.

Each policy conclusion records:

- `pending`, `passed`, or `failed`;
- correct detections, false positives, and false negatives;
- reviewed source intervals;
- continuity or jump findings;
- reconstruction coverage risk;
- reviewer notes with no local absolute paths.

Task 9 release status remains pending while any required conclusion is pending. A failed high-risk policy does not invalidate a passed `coverage_first` result, but it remains unavailable for automatic execution.

## Review Interface

Use one integrated review page:

1. Case list for six real cases and the synthetic static baseline.
2. Stage tabs for analysis, planning, validation, execution, output verification, and health.
3. Side-by-side source and actual `coverage_first` output playback.
4. A source-time motion timeline showing active motion, static, jitter, and review-required ranges.
5. Three-policy selection comparison, including selected sources, retention, maximum gaps, reasons, and risk.
6. A human review form for static accuracy, jitter/blur accuracy, continuity, and reconstruction coverage.

Playback must preserve source timing. A selected sequence must not be played at one output frame per source frame interval, because that falsely accelerates deleted sequences.

## Data Flow

1. Existing services generate and validate strategy artifacts.
2. `coverage_first` is applied and verified, producing the complete seven-artifact lifecycle.
3. Comparison policies stop after validation.
4. The session builder validates artifact identities and creates a sanitized browser payload.
5. The server exposes registered media and the sanitized payload.
6. The reviewer records per-policy conclusions.
7. Benchmark aggregation imports only sanitized conclusions and updates the release gate without rewriting machine evidence.

## Failure Handling

- A failed stage is displayed as failed with its safe error code; later dependent stages are not presented as successful.
- Missing, stale, or identity-mismatched artifacts prevent session publication for that case.
- An invalid `coverage_first` strategy is never applied.
- Failed output verification is visible and blocks a passing case conclusion.
- Browser or media decode failure falls back to source-frame playback and does not change the machine result.
- Human review writes are atomic and confined to the review output directory.

## Verification

### Automated

- Test session schema and sanitized serialization.
- Test loopback-only binding and dynamic port reporting.
- Test path traversal, unknown media identifiers, and write confinement.
- Test video byte-range responses and frame-sequence fallback metadata.
- Test policy-specific human conclusions and release-gate aggregation.
- Test that only `coverage_first` receives execution and verification artifacts.
- Run Ruff, strict mypy for new modules, pytest, compileall, and package build.

### Real evidence

- Run the complete default lifecycle for all six current real cases.
- Display the four currently detected real static ranges for human confirmation.
- Display the known high-definition slow-motion B interval `43-61` as a jitter/blur recall review interval.
- Display previously observed forward-parallax and lower-quality C jump risks for comparison policies.
- Generate and run one synthetic identical-frame static baseline with a declared expected static range.
- Keep all conclusions pending until the user completes the review form.

## Non-Goals

- Tuning algorithm thresholds to the current samples.
- Generating interpolation frames for reconstruction input.
- Re-encoding exported videos.
- Replacing reconstruction validation with visual review.
- Claiming zero error rates outside the current smoke set.
