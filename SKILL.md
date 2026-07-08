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
