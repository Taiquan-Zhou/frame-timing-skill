# Usage Reference

Use this package after a host project has already extracted and cleaned image frames. The host project should call the CLI or Python API with frame directories only.

## Installed CLI

Primary one-directory interface:

```powershell
frame-timing path\to\clean_frames
frame-timing-health --artifact_root agent_files\frame_timing_run
```

POSIX shell:

```bash
frame-timing path/to/clean_frames
frame-timing-health --artifact_root agent_files/frame_timing_run
```

Advanced batch interface:

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

POSIX shell:

```bash
frame-timing-demo --output_dir agent_files/demo_frames/sample --count 72
frame-timing path/to/clean_frames
frame-timing-batch --frames "sample=path/to/clean_frames" --artifact_root agent_files/frame_timing_run --limit_first_n 300 --write
frame-timing-health --artifact_root agent_files/frame_timing_run
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

## Host Project Smoke

Run this from the host project root after the host project has produced clean frames:

```powershell
python -m pip install <path-to-frame-timing-skill>

frame-timing-batch `
  --frames "sample=path\to\clean_frames" `
  --artifact_root "agent_files\frame_timing_run" `
  --write

frame-timing-health --artifact_root "agent_files\frame_timing_run"
```

POSIX shell:

```bash
python -m pip install /path/to/frame-timing-skill
frame-timing path/to/clean_frames
frame-timing-health --artifact_root agent_files/frame_timing_run
```

Prefer `frame-timing <clean_frame_dir>` for loose coupling and simple one-directory runs. Use `frame-timing-batch` for multiple directories or custom batch settings. Prefer the Python API only when the host project needs direct access to result objects or wants to compose the timing step inside an existing Python pipeline.

## Maintainer Notes

Before publishing or handing off a revision, run:

```powershell
python -m pytest
python -m compileall -q scripts examples tests
```

Generated demo data should stay under `agent_files/`.
