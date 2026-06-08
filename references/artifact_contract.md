# Artifact Contract

This skill separates model-safe outputs from analysis outputs.

## Model-safe output

Only `output_frames/` should be passed to downstream reconstruction.

Allowed files:

- image frames with supported extensions;
- `selected_frames.txt`;
- `run_manifest.json`.

Each output image must be a byte-identical copy of its recorded source frame.
`selected_frames.txt` records `source_sha256` for each output image so health checks can verify provenance without storing private input paths.

## Analysis output

Reports, strategy files, contact sheets, and health reports belong under `analysis/`.

Analysis artifacts must never be copied into `output_frames/`.
Analysis artifacts should use item names and artifact-relative paths instead of private absolute input paths.
