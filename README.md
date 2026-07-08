# Frame Timing Skill

[English](README.md) | [中文](README.zh-CN.md)

Frame Timing Skill prepares already-clean extracted image frames before reconstruction, NeRF, Gaussian Splatting, photogrammetry, or visual review. It analyzes frame motion and quality, plans a safe frame selection strategy, copies selected frames byte-for-byte, and writes local audit artifacts.

It does not extract video, edit pixels, stabilize images, deblur frames, upload data, or run reconstruction. v0.3.0 provides coverage protection for frame selection; it is not a 3D geometry, parallax, or camera-baseline coverage optimizer.

## For Users

Ask your AI agent or AI coding tool to install this repository as a skill:

```text
Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill
```

Then ask it to process an already-clean frame directory. The recommended Agent workflow uses `frame-timing-tool`:

```text
Use frame-timing-skill on path/to/clean_frames.
Analyze first, compare candidates if needed, validate before apply, and verify before using output_frames downstream.
```

If you only need the compatibility one-command flow, use:

```bash
frame-timing path/to/clean_frames
```

`frame-timing` is the legacy v2 compatibility entrypoint. It keeps the older `reconstruction_balanced` behavior and artifact layout for simple local use.
Legacy v2 strategy files may contain operations such as `select_sources`.

## For Agents And Developers

Install from the repository:

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-timing-skill.git
```

### Agent-safe v3 JSON CLI

Use `frame-timing-tool` when an Agent needs explicit, auditable stages. The lifecycle uses `schema_version 3` and policy revision `coverage-static-thinning-v1`.

```bash
frame-timing-tool capabilities
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
frame-timing-tool apply --frames path/to/clean_frames --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
frame-timing-tool verify --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

Available v3 policies:

- `coverage_first`: default for reconstruction-oriented Agent use; protects non-static coverage and thins only confirmed static ranges conservatively.
- `balanced`: middle-risk comparison candidate.
- `jitter_reduction`: aggressive comparison candidate; useful for visual review, but high-risk for reconstruction coverage.

Medium-risk and high-risk candidates should be shown to the user before applying. Failed validation must not be bypassed by editing JSON; apply revalidates the candidate digest and strategy identity.

### Python API

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

### Benchmark protocol

`frame-timing-benchmark` records external smoke-test results without copying private frames:

```bash
frame-timing-benchmark --case-id sample --frames path/to/clean_frames --artifact-root output/benchmark_sample
```

Benchmark results are release evidence, not a statistical accuracy claim.

## Artifacts

Agent-safe v3 writes:

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

Only `output_frames/` should be passed to downstream reconstruction. The copied images are byte-identical source-frame copies.

## More Documentation

- [Usage reference](references/usage.md)
- [Artifact contract](references/artifact_contract.md)
- [Agent integration](references/agent-integration.md)
- [Migration from v2 to v3](references/migration-v2-to-v3.md)
- [Benchmark protocol](benchmarks/README.md)

## License

MIT. See [LICENSE](LICENSE).
