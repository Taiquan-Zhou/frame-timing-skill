---
name: frame-timing-skill
description: Use when preparing already-clean extracted image frames for reconstruction, NeRF, Gaussian Splatting, photogrammetry, or visual review by optimizing frame timing, copying model-safe output_frames, and generating local audit artifacts without changing pixels.
---

# Frame Timing Skill

Use this skill only after the user already has clean extracted frame directories. It is a local package-backed skill: prefer the installed CLI entrypoints, or the Python API when integrating from another project.

## Boundaries

- Input must be extracted image frames, not raw video.
- Do not remove watermarks, OCR overlays, edit pixels, run reconstruction, upload data, or import from host projects.
- Keep all generated outputs under `agent_files/`.
- Original input frames must never be modified.
- Repeated frames mean byte-identical copies of the same source frame.

## Core Workflow

1. Confirm each input is an already-clean frame directory.
2. If CLI commands are unavailable, install from the skill/package checkout:

```powershell
python -m pip install .
```

3. Run batch timing:

```powershell
frame-timing-batch `
  --frames "<item_name>=<clean_frame_dir>" `
  --artifact_root "agent_files/<run_name>" `
  --limit_first_n 300 `
  --write
```

4. Verify before reporting completion:

```powershell
frame-timing-health --artifact_root "agent_files/<run_name>"
```

5. Report `output_frames`, review dashboard, human review, maintenance report, and any warnings/errors.

## Output Contract

Model-safe output:

```text
agent_files/<run_name>/<item_name>/output_frames/
  frame_*.jpg
  selected_frames.txt
  run_manifest.json
```

Analysis-only output:

```text
agent_files/<run_name>/<item_name>/analysis/
  human_review.md
  strategy.json
  visual_review/index.md
agent_files/<run_name>/analysis/
  review_dashboard.md
  maintenance_report.md
  maintenance_report.json
```

Never pass `analysis/` files to reconstruction models. `selected_frames.txt` must include `source_sha256`, and health must exit 0 with status `ok` before the run is considered complete.

## Python API

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

For detailed integration notes, read `references/usage.md`. For artifact validation rules, read `references/artifact_contract.md`.
