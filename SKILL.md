---
name: frame-timing-skill
description: Use when preparing already-clean extracted image frames for reconstruction, NeRF, Gaussian Splatting, photogrammetry, or visual review with local frame selection, provenance checks, and audit artifacts.
---

# Frame Timing Skill

Use this skill only after the user already has an extracted, cleaned image-frame directory. Prefer the Agent-safe v3 lifecycle for Agent work. Use the legacy v2 `frame-timing` command only when the user explicitly wants the simple compatibility command.

## Boundaries

- Input is a directory of image frames, not raw video.
- Do not edit pixels, stabilize images, deblur frames, interpolate frames, upload data, or run reconstruction.
- Do not hand-edit JSON to bypass validation.
- Keep generated artifacts under an `output/` directory.
- Only `output_frames/` may be passed to downstream reconstruction.
- Agent-safe v3 provides coverage protection for frame selection, not 3D coverage optimization.

## Install Fallback

If the CLI commands are unavailable, install the package first:

```bash
python -m pip install <path-to-frame-timing-skill>
python -m pip install /path/to/frame-timing-skill
```

## Agent-safe v3 Workflow

Use `frame-timing-tool`. It emits single JSON responses with `schema_version 3`, writes local artifacts, and uses policy revision `coverage-static-thinning-v1`.

1. Inspect capabilities:

```bash
frame-timing-tool capabilities
```

2. Analyze before planning:

```bash
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

3. Plan with the default reconstruction policy:

```bash
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
```

Allowed policies are `coverage_first`, `balanced`, and `jitter_reduction`. Default to `coverage_first`. Use `balanced` and `jitter_reduction` only for comparison or when the user explicitly wants a more aggressive candidate.

4. Validate before apply:

```bash
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
```

If validation fails, stop. Do not patch `strategy.json` to force success.

5. Request human confirmation before applying `balanced`, `jitter_reduction`, or any candidate whose risk is not low.

6. Apply only a validated candidate:

```bash
frame-timing-tool apply --frames path/to/clean_frames --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
```

7. Verify before downstream use:

```bash
frame-timing-tool verify --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

Only report completion when verify exits 0 and `health.json` is valid.

## CPU-only Offline Batch Workflow

Use the recoverable batch workflow for one or more already-clean frame directories, or for deterministic discovery below one root. It is CPU-only and must remain fully local. Keep the state path under an `output/` directory.

Create from one or multiple explicit directories by repeating `--frames`, or use `--root` for discovery:

```bash
frame-timing-tool batch create --frames path/to/a/clean_frames --frames path/to/b/clean_frames --state output/frame_timing_batch/analysis/batch_state.json --fps 30
frame-timing-tool batch create --root path/to/dataset_root --state output/frame_timing_batch/analysis/batch_state.json --fps 30
```

Follow this exact control sequence:

```text
batch create -> batch run/status -> report review_required ->
request explicit approval -> batch approve -> request explicit export -> batch export
```

Commands:

```bash
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch status --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json --retry-item FAILED_ITEM_NAME
frame-timing-tool batch approve --state output/frame_timing_batch/analysis/batch_state.json --item ITEM_NAME --note "reviewed"
frame-timing-tool batch export --state output/frame_timing_batch/analysis/batch_state.json
```

Rules:

- The Agent must not auto-resume. Reopening or inspecting an unfinished batch never authorizes `batch run`.
- The Agent must not auto-approve any `review_required` item. Report the warning codes and request explicit approval first.
- The Agent must not auto-retry failed items. Report failures, request authorization, then pass only approved names with `batch run --retry-item ITEM_NAME`.
- The Agent must not auto-export. Request explicit export after the batch is finished and approvals are resolved.
- Never modify source frames. Export only verified byte-identical copies under each item's `output_frames/`.
- Do not invent quality scores. Batch review uses only the bad-quality-candidate ratio and existing `low_motion_review` ranges.
- The state file must use the canonical `output/**/analysis/batch_state.json` layout.

### Batch Artifact Contract

```text
output/frame_timing_batch/
  analysis/
    batch_state.json
    batch_summary.json
    batch_summary.csv
    review_dashboard.md
    human_review.md
    maintenance_report.json
  <item-name>/
    analysis/
    output_frames/
```

Treat `batch_state.json` as tool-owned state and never edit it to bypass status, approval, retry, or export rules. `maintenance_report.json` exists after a health check. Only verified item `output_frames/` directories may be handed downstream; preserve batch summaries and audit artifacts for traceability.

The older `frame-timing-batch` entry point is compatibility-only. It does not provide the recoverable session, explicit review approval, and explicit verified-export state machine above, so it must not be used for the recoverable production workflow.

## Output Contract

Agent-safe v3 artifacts:

```text
output/frame_timing_run/
  analysis.json
  strategy.json
  validation.json
  execution.json
  health.json
  report.md
  human_review.md
  output_frames/
```

The output images are byte-identical source-frame copies. `strategy.json` records the selected sources and current `policy_revision`. `validation.json`, `execution.json`, `health.json`, `report.md`, and `human_review.md` are audit material.

## Legacy v2 Compatibility

For a simple one-directory local run:

```bash
frame-timing path/to/clean_frames
frame-timing-health --artifact_root output/frame_timing_run
```

This legacy v2 path uses `reconstruction_balanced` and older batch artifacts. Do not describe it as Agent-safe v3. Do not route v2 overrides into v3.
Legacy v2 strategy files may include operations such as `select_sources`.

## Python API

```python
from pathlib import Path
from frame_timing_agent import (
    PolicyName,
    StrategyRequest,
    analyze_frames,
    apply_validated_strategy,
    plan_strategy,
    validate_strategy,
    verify_output,
)

frame_dir = Path("path/to/clean_frames")
artifact_root = Path("output/frame_timing_run")
analysis = analyze_frames(frame_dir, artifact_root)
candidate = plan_strategy(analysis, StrategyRequest(PolicyName.COVERAGE_FIRST), artifact_root)
validation = validate_strategy(analysis, candidate, candidate.request, artifact_root)
execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, artifact_root / "output_frames")
health = verify_output(frame_dir, analysis, candidate, execution, artifact_root / "output_frames")
```

Read `references/usage.md`, `references/artifact_contract.md`, `references/agent-integration.md`, and `references/migration-v2-to-v3.md` before integrating this package into another project.
