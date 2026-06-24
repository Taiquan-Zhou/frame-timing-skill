---
name: frame-timing-skill
description: Use when preparing already-clean extracted image frames for reconstruction, NeRF, Gaussian Splatting, photogrammetry, or visual review by optimizing frame timing, copying model-safe output_frames, and generating local audit artifacts without changing pixels.
---

# Frame Timing Skill

Use this skill only after the user already has clean extracted frame directories. It is a local package-backed skill: prefer the installed `frame-timing` CLI, use `frame-timing-batch` only for multiple frame directories or custom batch settings, and use the Python API when integrating from another project.

## Boundaries

- Input must be extracted image frames, not raw video.
- Do not remove watermarks, OCR overlays, edit pixels, run reconstruction, upload data, or import from host projects.
- Keep all generated outputs under `output/`.
- Original input frames must never be modified.
- Repeated frames mean byte-identical copies of the same source frame.
- Reconstruction balancing means moderate static compression, fast-motion duplication, and stable source-frame selection for jitter ranges. It does not warp, crop, interpolate, or stabilize pixels.

## Core Workflow

1. Confirm each input is an already-clean frame directory.
2. If CLI commands are unavailable, install the package from the skill directory. When already inside the skill/package checkout, use:

```powershell
python -m pip install .
```

From another working directory, install with an explicit path:

```powershell
python -m pip install <path-to-frame-timing-skill>
```

POSIX shell:

```bash
python -m pip install /path/to/frame-timing-skill
```

3. Run the simple one-directory interface:

```powershell
frame-timing "<clean_frame_dir>"
```

POSIX shell:

```bash
frame-timing path/to/clean_frames
```

This writes artifacts under `output/frame_timing_run` by default.

4. Use the default `reconstruction_balanced` mode; do not ask the user to choose a strategy.

5. For multiple frame directories or custom batch settings, run batch timing:

```powershell
frame-timing-batch `
  --frames "<item_name>=<clean_frame_dir>" `
  --artifact_root "output/<run_name>" `
  --limit_first_n 300 `
  --write
```

POSIX shell:

```bash
frame-timing-batch --frames "<item_name>=<clean_frame_dir>" --artifact_root "output/<run_name>" --limit_first_n 300 --write
```

6. Verify before reporting completion:

```powershell
frame-timing-health --artifact_root "output/<run_name>"
```

POSIX shell:

```bash
frame-timing-health --artifact_root "output/<run_name>"
```

7. Report `output_frames`, review dashboard, human review, maintenance report, and any warnings/errors.

## Output Contract

Model-safe output:

```text
output/<run_name>/<item_name>/output_frames/
  frame_*.jpg
  selected_frames.txt
  run_manifest.json
```

Analysis-only output:

```text
output/<run_name>/<item_name>/analysis/
  human_review.md
  strategy.json
  visual_review/index.md
output/<run_name>/analysis/
  review_dashboard.md
  maintenance_report.md
  maintenance_report.json
```

Never pass `analysis/` files to reconstruction models. `selected_frames.txt` must include `source_sha256`, and health must exit 0 with status `ok` before the run is considered complete.

In `reconstruction_balanced` mode, `strategy.json` uses version `2`. It combines `keep_uniform`, `duplicate_range`, and `select_sources` operations. `output_frames/` remains byte-identical to source frames; no pixels are modified.

## Python API

```python
from pathlib import Path
from frame_timing_agent.batch_timing_agent import BatchTimingItem, run_batch_timing_agent

result = run_batch_timing_agent(
    [BatchTimingItem(name="sample", frames=Path("path/to/clean_frames"))],
    artifact_root=Path("output/frame_timing_run"),
    limit_first_n=300,
    write=True,
)
```

For detailed integration notes, read `references/usage.md`. For artifact validation rules, read `references/artifact_contract.md`.
