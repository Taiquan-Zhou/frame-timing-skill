# Changelog

## 0.2.0rc1

- Add `reconstruction_balanced` as the default reconstruction-oriented mode: moderate static compression, fast-motion duplication, and stable keyframe selection in jitter ranges.
- Add strategy version `2` with `select_sources` operations.
- Keep jitter-reduction outputs as byte-identical source-frame copies; no pixel warping or interpolation is performed.
- Add execution-audit checks for `select_sources`.
- Add bounded defaults for `jitter_max_output_ratio` and `jitter_min_frames`.
- Fix sparse range validation to avoid expanding huge source-index spans.

## 0.1.0

- Initial standalone release.
- Provides package CLI entrypoints: `frame-timing`, `frame-timing-demo`, `frame-timing-batch`, and `frame-timing-health`.
- Provides portable agent skill instructions, references, and optional agent metadata.
- Generates model-safe byte-identical copied output frames and local audit artifacts.
- Verifies output provenance with `source_sha256`.
