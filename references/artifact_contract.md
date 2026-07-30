# Artifact Contract

This package separates model-safe outputs from analysis and audit outputs. Agent-safe v3 artifacts use `schema_version 3` and policy revision `coverage-static-thinning-v1`.

## Agent-safe v3 Layout

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

## JSON Artifacts

- `analysis.json`: deterministic analysis of input frame quality, motion, ranges, and input digest.
- `strategy.json`: selected sources, policy, request, `policy_revision`, risk, reasons, and estimated metrics.
- `validation.json`: validation result, candidate digest, and blocking issues.
- `execution.json`: copied output frame count, selected sources, output manifest, and output digest.
- `health.json`: lifecycle health summary created by `verify`.

All JSON is canonicalized before writing. Paths inside JSON must be artifact-relative or sanitized names; private absolute input paths must not be written.

## Reports

- `report.md`: machine-readable summary for the run.
- `human_review.md`: concise human review checklist.

Reports are audit material. Do not pass them to downstream reconstruction tools.

## Model-safe Output

Only `output_frames/` should be passed downstream.

Allowed files:

- copied image frames with supported image extensions;
- `selected_frames.txt`;
- `run_manifest.json`.

Each output image must be a byte-identical copy of its recorded source frame. The package does not warp, crop, interpolate, deblur, or stabilize pixels.

## Policy Behavior

Agent-safe v3 supports `coverage_first`, `balanced`, and `jitter_reduction`.

- `coverage_first` protects non-static coverage and only thins high-confidence static ranges conservatively.
- `balanced` is a medium-risk comparison candidate.
- `jitter_reduction` is an aggressive comparison candidate.

`validate` must pass before `apply`. `apply` revalidates strategy identity and candidate digest. Validation failure is not an invitation to edit JSON.

## Legacy v2 Artifacts

The legacy v2 `frame-timing` and `frame-timing-batch` paths use `reconstruction_balanced` and may write older batch analysis folders and strategy version `2` with operations such as `keep_uniform`, `duplicate_range`, and `select_sources`. These artifacts remain supported for compatibility, but they are not the Agent-safe v3 staged contract.

## Health Pass Conditions

`frame-timing-tool verify` or `frame-timing-health` must exit 0 before downstream use. A passing v3 run means:

- required artifacts exist: `analysis.json`, `strategy.json`, `validation.json`, `execution.json`, `health.json`, `report.md`, `human_review.md`, and `output_frames/`;
- selected output frames match source provenance;
- output frame bytes match recorded source hashes;
- validation and execution identity match the current candidate;
- no private absolute source paths are published.
