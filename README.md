# Frame Timing Skill

Independent package and Codex skill for optimizing already-clean extracted video frames before reconstruction.

The package detects static and fast-motion ranges, writes byte-identical copied output frames, and generates audit artifacts under `agent_files/`. It does not extract video, remove watermarks, OCR overlays, alter pixels, upload data, or run reconstruction.

## Install

From a local checkout:

```powershell
python -m pip install .
```

For development:

```powershell
python -m pip install -e .
```

## Quickstart

Generate deterministic demo frames:

```powershell
frame-timing-demo `
  --output_dir agent_files\demo_frames\sample `
  --count 72
```

Run the batch timing agent:

```powershell
frame-timing-batch `
  --frames "sample=agent_files\demo_frames\sample" `
  --artifact_root "agent_files\demo_run" `
  --limit_first_n 72 `
  --write
```

Verify generated artifacts:

```powershell
frame-timing-health --artifact_root agent_files\demo_run
```

## CLI Reference

- `frame-timing-demo`: creates synthetic local frames for smoke tests.
- `frame-timing-batch`: analyzes one or more clean frame directories and writes `output_frames/` plus review artifacts.
- `frame-timing-health`: verifies artifact structure, allowed output files, and byte-identical provenance.

Pass multiple frame sets by repeating `--frames "<item_name>=<clean_frame_dir>"`.

## Python API

```python
from pathlib import Path
from frame_timing_agent.batch_timing_agent import BatchTimingItem, run_batch_timing_agent

result = run_batch_timing_agent(
    [BatchTimingItem(name="sample", frames=Path("agent_files/demo_frames/sample"))],
    artifact_root=Path("agent_files/demo_run"),
    limit_first_n=72,
    write=True,
)
```

## Acceptance Checklist

Run before publishing or integrating:

```powershell
python -m pytest
python -m compileall -q scripts examples tests
frame-timing-health --artifact_root agent_files\demo_run
```

Health must report status `ok`. The generated `output_frames/selected_frames.txt` includes `source_sha256`; health checks use it to verify copied-frame provenance without storing private input paths in analysis artifacts.
