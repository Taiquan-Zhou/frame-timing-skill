# Agent Integration

Use Agent-safe v3 when an AI agent, orchestration service, or host project needs to choose, validate, apply, and audit frame timing decisions explicitly. The package does not include an LLM SDK; the agent owns planning and user interaction, while this package owns deterministic analysis, selection, validation, copying, and verification.

## Recommended Control Flow

1. Run `capabilities`.
2. Run `analyze`.
3. Plan `coverage_first` by default.
4. Optionally plan `balanced` and `jitter_reduction` for comparison.
5. Show medium-risk or high-risk candidates to the user before apply.
6. Run `validate`.
7. Run `apply` only if validation is valid.
8. Run `verify`.
9. Send only `output_frames/` downstream.

The lifecycle uses `schema_version 3` and policy revision `coverage-static-thinning-v1`.

## JSON CLI

```bash
frame-timing-tool capabilities
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
frame-timing-tool apply --frames path/to/clean_frames --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
frame-timing-tool verify --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

Every command returns one JSON object on stdout. Stderr may contain short stage logs. Do not parse private paths from stderr.

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
candidate = plan_strategy(
    analysis,
    StrategyRequest(PolicyName.COVERAGE_FIRST),
    artifact_root,
)
validation = validate_strategy(analysis, candidate, candidate.request, artifact_root)
if not validation.valid:
    raise RuntimeError("frame timing strategy is unsafe")

execution = apply_validated_strategy(
    frame_dir,
    analysis,
    candidate,
    validation,
    artifact_root / "output_frames",
)
health = verify_output(frame_dir, analysis, candidate, execution, artifact_root / "output_frames")
if not health.valid:
    raise RuntimeError("frame timing output verification failed")
```

## Policies

- `coverage_first`: default. Protects non-static coverage and conservatively thins confirmed static ranges.
- `balanced`: comparison candidate. Requires human confirmation for Agent use.
- `jitter_reduction`: aggressive comparison candidate. Requires human confirmation for reconstruction use.

Agents may choose a stricter `minimum_retention_ratio` or `maximum_consecutive_drops` through `StrategyRequest`, but they cannot weaken the policy preset. Hidden safety limits are enforced by validation.

## Required Artifact Names

Agent integrations should expect these artifact names:

- `analysis.json`
- `strategy.json`
- `validation.json`
- `execution.json`
- `health.json`
- `report.md`
- `human_review.md`
- `output_frames`

## Safety Rules

- Never call `apply` before `validate`.
- Never continue after invalid validation.
- Never edit `strategy.json` to force success.
- Never pass `analysis.json`, `strategy.json`, reports, or health files to reconstruction.
- Never write artifacts outside an `output/` directory.
- Never describe this package as a pixel stabilizer, deblurring tool, or reconstruction-quality guarantee.
