# Frame Timing Skill

Independent skill package for preparing already-clean extracted video frames before reconstruction.

Current status: migrated and testable as a standalone local skill.

## Intended Use

Generate deterministic local demo frames:

```powershell
python examples\make_demo_frames.py `
  --output_dir agent_files\demo_frames\sample `
  --count 72
```

Run the batch timing agent from a source checkout:

```powershell
python scripts\frame_timing_agent\batch_timing_agent.py `
  --frames "sample=agent_files\demo_frames\sample" `
  --artifact_root "agent_files\demo_run" `
  --limit_first_n 72 `
  --write
```

Run the tests:

```powershell
python -m pytest
```

The generated `output_frames/selected_frames.txt` includes a `source_sha256` column. Health checks use that hash to verify byte-identical provenance without storing private input paths in analysis artifacts.

## Non-goals

- watermark removal;
- OCR;
- video extraction;
- 3D reconstruction;
- cloud upload/training.

Those should be separate skills or tools.
