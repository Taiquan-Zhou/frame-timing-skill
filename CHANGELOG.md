# Changelog

## 0.3.0 - 2026-07-09

Agent-safe v3 readiness release for audited frame selection before reconstruction.

### Added

- Add `frame-timing-tool` as the staged Agent-safe v3 JSON CLI: `capabilities`, `analyze`, `plan`, `validate`, `apply`, and `verify`.
- Add typed Agent strategy contracts using `schema_version 3` and policy revision `coverage-static-thinning-v1`.
- Add three v3 policies: `coverage_first`, `balanced`, and `jitter_reduction`.
- Add v3 audit artifacts: `analysis.json`, `strategy.json`, `validation.json`, `execution.json`, `health.json`, `report.md`, `human_review.md`, and `output_frames/`.
- Add an external benchmark protocol for smoke-test release evidence without committing private frames or absolute paths.

### Changed

- Make `coverage_first` the recommended Agent policy for reconstruction-oriented use.
- Preserve non-static frame coverage and apply only conservative thinning to confirmed static ranges.
- Strengthen motion analysis, strategy validation, output verification, package build checks, CI matrix, and security/dependency gates.
- Keep copied output frames byte-identical to source frames.

### Compatibility

- Keep `frame-timing`, `frame-timing-batch`, and `frame-timing-health` as legacy v2 compatibility entrypoints.
- Do not automatically route legacy v2 calls into Agent-safe v3.
- Do not map old v2 override files into v3 strategy requests.

### Known Limits

- This release performs frame selection, not pixel stabilization, deblurring, interpolation, 3D coverage optimization, or reconstruction.
- Benchmark results are smoke-test release evidence only; they are not statistical accuracy claims and do not guarantee zero false positives on unknown videos.
- Reconstruction quality still requires downstream validation and human review for high-risk footage.

### Verification

- `python -m ruff check scripts tests`
- `python -m ruff format --check scripts tests`
- `python -m mypy scripts/frame_timing_agent`
- `python -m pytest --cov=frame_timing_agent --cov-report=term-missing --cov-fail-under=90`
- `python -m compileall -q scripts examples tests`
- `python -m build`
- `python -m twine check dist/*`
- `python -m pip_audit`

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
