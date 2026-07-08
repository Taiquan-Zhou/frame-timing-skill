# Changelog

## 0.3.0 - Unreleased

Agent-safe v3 readiness release.

- Document `frame-timing-tool` as the staged Agent-safe v3 JSON CLI.
- Document `coverage_first`, `balanced`, and `jitter_reduction` policy selection.
- Document `schema_version 3` and policy revision `coverage-static-thinning-v1`.
- Clarify that `frame-timing` remains the legacy v2 compatibility entrypoint.
- Add Agent integration and v2-to-v3 migration references.
- Clarify that the package performs frame selection and byte-identical copying, not pixel stabilization, deblurring, 3D coverage optimization, or reconstruction-quality guarantees.

## 0.2.0rc1

Strategy upgrade from v1 `aggressive_motion` to v2 `reconstruction_balanced`.

| Area | v1 / 0.1.x | v2 / 0.2.0rc1 |
| --- | --- | --- |
| Default strategy | `aggressive_motion` | `reconstruction_balanced` |
| Static ranges | Compressed long static ranges. | Uses more moderate static compression for reconstruction-oriented output. |
| Fast motion | Duplicated fast-motion ranges. | Keeps fast-motion duplication where useful, but combines it with jitter-aware source selection. |
| Camera jitter | Limited static-window handling. | Detects high-frequency alternating motion and uses stable keyframe selection. |
| Strategy operations | `keep_uniform`, `duplicate_range`, `keep`, `mark_review` | Adds `select_sources` and strategy version `2`. |
| Auditability | Basic reports and health checks. | Adds execution audit for selected sources and manual-override protection. |

- Add `reconstruction_balanced` as the default reconstruction-oriented mode: moderate static compression, fast-motion duplication, and stable keyframe selection in jitter ranges.
- Add strategy version `2` with `select_sources` operations.
- Keep jitter-reduction outputs as byte-identical source-frame copies; no pixel warping or interpolation is performed.
- Add execution-audit checks for `select_sources`.
- Add bounded defaults for `jitter_max_output_ratio` and `jitter_min_frames`.
- Add configurable jitter thresholds for motion, phase-correlation response, and sharpness.
- Preserve manual overrides when they overlap automatic jitter-reduction ranges.
- Improve stable keyframe selection to preserve temporal coverage across jitter ranges.
- Fix sparse range validation to avoid expanding huge source-index spans.

## 0.1.0

- Initial standalone release.
- Provides package CLI entrypoints: `frame-timing`, `frame-timing-demo`, `frame-timing-batch`, and `frame-timing-health`.
- Provides portable agent skill instructions, references, and optional agent metadata.
- Generates model-safe byte-identical copied output frames and local audit artifacts.
- Verifies output provenance with `source_sha256`.
