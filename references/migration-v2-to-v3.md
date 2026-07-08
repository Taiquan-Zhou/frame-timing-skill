# Migration From v2 To v3

This document explains how to move from legacy v2 compatibility entrypoints to the Agent-safe v3 lifecycle.

## Summary

- legacy v2 remains available through `frame-timing`, `frame-timing-batch`, and `frame-timing-health`.
- Agent-safe v3 is available through `frame-timing-tool` and the package-root Python API.
- v3 does not automatically route legacy calls into the new lifecycle.
- v2 `override` configuration is not mapped into v3.
- v0.3.0 does not migrate the old facade automatically.

## What Stays Compatible

Existing local flows can continue using:

```bash
frame-timing path/to/clean_frames
frame-timing-batch --frames "sample=path/to/clean_frames" --artifact_root output/frame_timing_run --write
frame-timing-health --artifact_root output/frame_timing_run
```

These commands keep the legacy v2 `reconstruction_balanced` behavior and older artifact layout. They are useful for direct package users who already depend on that interface.

## What Changes In Agent-safe v3

Agent-safe v3 uses explicit stages:

```bash
frame-timing-tool capabilities
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
frame-timing-tool apply --frames path/to/clean_frames --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
frame-timing-tool verify --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

It writes `analysis.json`, `strategy.json`, `validation.json`, `execution.json`, `health.json`, `report.md`, `human_review.md`, and `output_frames`.

The current v3 strategy identity uses `schema_version 3` and policy revision `coverage-static-thinning-v1`.

## Override Policy

legacy v2 accepted looser configuration and override files in old entrypoints. Agent-safe v3 does not automatically route those overrides into the new lifecycle.

Why:

- v3 protects reconstruction coverage through typed strategy requests and package-owned safety limits.
- v3 validation recomputes selection safety instead of trusting caller-supplied values.
- v3 must remain deterministic and auditable for Agent use.

If a host project needs v3 behavior, migrate explicitly to `StrategyRequest` with one of:

- `coverage_first`
- `balanced`
- `jitter_reduction`

Only stricter public constraints should be passed. Do not weaken hidden policy guards.

## Rollback

Pin the package version or commit used by the host project. To roll back a v3 integration:

1. Stop calling `frame-timing-tool`.
2. Restore the previous package version or commit.
3. Return to legacy v2 commands.
4. Re-run `frame-timing-health` before downstream reconstruction.

Do not mix v2 artifacts and v3 artifacts in the same artifact root.

## Future Migration Requirements

A future migration of the legacy facade must be explicit. It should include:

- a new entrypoint or opt-in flag;
- a deprecation period for legacy v2 behavior;
- real-sample benchmark comparison;
- updated README, Skill, API docs, migration notes, and changelog;
- no hidden routing based on whether an override file exists.
