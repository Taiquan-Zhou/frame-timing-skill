# Usage Reference

Use this package after a host project has already extracted and cleaned image frames. Pass frame directories only. Keep video extraction, OCR, watermark removal, reconstruction, and model execution outside this package.

## Agent-safe v3 CLI

`frame-timing-tool` is the preferred interface for Agents. It exposes an auditable lifecycle with `schema_version 3` and policy revision `coverage-static-thinning-v1`.

```bash
frame-timing-tool capabilities
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
frame-timing-tool apply --frames path/to/clean_frames --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
frame-timing-tool verify --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

Subcommands:

- `capabilities`: print supported policies, stages, safety limits, and limitations.
- `analyze`: write `analysis.json`.
- `plan`: write `strategy.json`.
- `validate`: write `validation.json`.
- `apply`: copy selected frames into `output_frames/` and write `execution.json`.
- `verify`: write `health.json`, `report.md`, and `human_review.md`.

Policies:

- `coverage_first`: default Agent policy for reconstruction-oriented use.
- `balanced`: comparison candidate with medium risk.
- `jitter_reduction`: aggressive comparison candidate with high coverage risk.

## Legacy v2 CLI

`frame-timing` is the simple compatibility command:

```bash
frame-timing path/to/clean_frames
frame-timing-health --artifact_root output/frame_timing_run
```

It uses legacy v2 `reconstruction_balanced` behavior. It is useful for direct local operation, but it is not the Agent-safe v3 staged interface. Legacy v2 strategy files may contain operations such as `select_sources`.

For multiple frame directories or old batch settings:

```bash
frame-timing-batch --frames "sample=path/to/clean_frames" --artifact_root output/frame_timing_run --write
frame-timing-health --artifact_root output/frame_timing_run
```

Legacy batch result fields remain available for existing integrations:

```python
from pathlib import Path
from frame_timing_agent.batch_timing_agent import BatchTimingItem, run_batch_timing_agent

result = run_batch_timing_agent(
    [BatchTimingItem(name="sample", frames=Path("path/to/clean_frames"))],
    artifact_root=Path("output/frame_timing_run"),
    limit_first_n=300,
    write=True,
)

print(result.summary_json_path)
print(result.summary_csv_path)
print(result.review_dashboard_path)
print(result.items)
```

## Agent-safe v3 Python API

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

## Benchmark

Use `frame-timing-benchmark` for release smoke evidence. It does not copy private input frames.

```bash
frame-timing-benchmark --case-id sample --frames path/to/clean_frames --artifact-root output/benchmark_sample
```

Benchmark results are not statistical accuracy claims.
