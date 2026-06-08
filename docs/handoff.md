# Handoff: Frame Timing Skill

## Goal

Create a standalone downloadable Codex skill for local frame timing optimization.

The skill assumes input frames are already clean and extracted. Watermark removal and OCR are out of scope.

## Migrated Modules

Core behavior was migrated from the previous local preprocessing package into this standalone skill.

Migrated modules:

- `frame_source.py`
- `timing_metrics.py`
- `segment_detector.py`
- `frame_strategy.py`
- `apply_frame_strategy.py`
- `auto_timing_agent.py`
- `batch_timing_agent.py`
- `batch_artifact_health.py`
- `strategy_execution_audit.py`
- `strategy_visual_review.py`
- `human_review.py`

## Current Verified Behavior

The source project passed:

```text
Ran 43 tests
OK
```

The real sample health report showed:

```text
Health status: ok
checked_output_frames: 311
errors: []
warnings: []
```

## Migration Plan

1. Core modules copied into `scripts/frame_timing_agent/`.
2. Legacy package imports rewritten to `frame_timing_agent.*`.
3. Tests copied and adapted into `tests/`.
4. `examples/make_demo_frames.py` added.
5. Tests run from this standalone project.
6. Demo batch run on generated frames.
7. Private path and data leak review performed.
8. Prepare GitHub repository or installable zip.

## Release Readiness Criteria

- No private paths.
- No private video/frame data.
- Fresh checkout can run tests.
- Demo command runs without external project dependencies.
- `SKILL.md` and `agents/openai.yaml` are present.
- Output health report verifies byte-identical frame provenance.
