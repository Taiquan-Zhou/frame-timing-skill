---
name: frame-timing-skill
description: Use when preparing already-clean extracted video frames for reconstruction by detecting static or fast-motion ranges, compressing or duplicating frames, writing model-safe output_frames, and generating audit/review artifacts without changing image pixels.
---

# Frame Timing Skill

Use this skill when the user has already handled watermark removal/OCR and provides extracted frame directories that should be optimized before 3D reconstruction or similar downstream processing.

## Boundaries

- Input is an extracted image-frame directory.
- The skill does not remove watermarks.
- The skill does not run OCR.
- The skill does not train or reconstruct 3D models.
- The skill must not modify original input frames.
- Output frames must be byte-identical copies of source frames; repetition means copying the same source frame again.

## Core Workflow

1. Load frame records from filenames or `selected_frames.txt`.
2. Detect long-static and fast-motion ranges.
3. Create a strategy that compresses static ranges and duplicates fast ranges.
4. Write `agent_files/<run_name>/output_frames` only when requested.
5. Generate human review reports, contact sheets, execution audits, and a health report.
6. Verify the health report before telling the user the result is ready.

## Output Contract

The model-safe output is only:

```text
agent_files/<run_name>/<item_name>/output_frames/
  frame_*.jpg
  selected_frames.txt
  run_manifest.json
```

Analysis-only artifacts must stay under `analysis/`.

## Review First

For non-trivial changes, inspect the current project, make a short plan, use tests first, and verify before completion.
