# Usage Reference

Use this package after a host project has already extracted and cleaned image frames. The host project should call the CLI or Python API with frame directories only.

## Installed CLI

```powershell
frame-timing-batch `
  --frames "sample=path\to\clean_frames" `
  --artifact_root "agent_files\frame_timing_run" `
  --limit_first_n 300 `
  --write

frame-timing-health --artifact_root "agent_files\frame_timing_run"
```

Generate demo frames for smoke testing:

```powershell
frame-timing-demo `
  --output_dir agent_files\demo_frames\sample `
  --count 72
```

## Python Integration

```python
from pathlib import Path
from frame_timing_agent.batch_timing_agent import BatchTimingItem, run_batch_timing_agent

result = run_batch_timing_agent(
    [BatchTimingItem(name="sample", frames=Path("path/to/clean_frames"))],
    artifact_root=Path("agent_files/frame_timing_run"),
    limit_first_n=300,
    write=True,
)
```

Read `result.batch_report` and `result.items[*]` to locate output and review artifacts.

## Host Project Contract

- Install this package instead of copying modules into the host project.
- Pass only clean extracted frame directories.
- Keep host-specific extraction, OCR, watermark removal, and reconstruction outside this package.
- Treat `output_frames/` as the only model-safe downstream input.
- Treat `analysis/` as human/agent review material only.
- Run `frame-timing-health` before using artifacts downstream.

## Maintainer Notes

Before publishing or handing off a revision, run:

```powershell
python -m pytest
python -m compileall -q scripts examples tests
```

Generated demo data should stay under `agent_files/`.
