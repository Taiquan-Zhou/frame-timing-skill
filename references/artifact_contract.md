# Artifact Contract

This skill separates model-safe outputs from analysis outputs.

## Model-Safe Output

Only `output_frames/` should be passed to downstream reconstruction.

Allowed files:

- image frames with supported extensions;
- `selected_frames.txt`;
- `run_manifest.json`.

Each output image must be a byte-identical copy of its recorded source frame. `selected_frames.txt` records `source_sha256` for each output image so health checks can verify provenance without storing private input paths.

In `reconstruction_balanced` mode, strategy version `2` may combine `keep_uniform`, `duplicate_range`, and `select_sources` operations. `select_sources` drops unstable frames from the selected range and keeps only explicit source indices. It must not create duplicate frames or modify pixels.

## Analysis Output

Reports, strategy files, contact sheets, dashboards, and health reports belong under `analysis/`.

Expected per-item files:

- `analysis/human_review.md`;
- `analysis/strategy.json`;
- `analysis/visual_review/index.md`.

Expected batch files:

- `analysis/review_dashboard.md`;
- `analysis/maintenance_report.md`;
- `analysis/maintenance_report.json`.

Analysis artifacts must never be copied into `output_frames/`. They should use item names and artifact-relative paths instead of private absolute input paths.

## Health Pass Conditions

`frame-timing-health` must exit 0 and report status `ok`. A passing run means:

- every item has allowed `output_frames/` files only;
- every selected output frame has a recorded `source_sha256`;
- output frame bytes match recorded provenance;
- required review and maintenance artifacts are present;
- analysis artifacts do not depend on private absolute source paths.
