# Frame Timing Skill

[中文](README.md)

A local frame timing analysis and selection tool for reconstruction, NeRF, Gaussian Splatting, and photogrammetry. It supports an interactive single-directory workspace, CPU-only offline batches, and Agent-safe automation with traceable, verified frame output.

[![Latest Release](https://img.shields.io/github/v/release/Taiquan-Zhou/frame-timing-skill?label=Release)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest)
[![CI](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml)
[![Download Windows desktop app](https://img.shields.io/badge/Windows-Download-2563eb?logo=windows)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)

## For Users

### Choose a Workflow

| Workflow | Best for | Entry point |
| --- | --- | --- |
| Single-directory workspace | Analyze, inspect, and export one frame directory | `FrameTimingSkill.exe` / `frame-timing-ui` |
| CPU-only offline batch | Process one or more frame directories sequentially | Desktop Batch Processing / `frame-timing-tool batch` |
| Agent-safe v3 | Agent use or system integration | `frame-timing-tool` |

#### Single-directory Workspace

Select a frame directory and FPS, inspect timelines, ranges, and representative frames, then generate `output_frames/`.

<p align="center">
  <img src="https://raw.githubusercontent.com/Taiquan-Zhou/frame-timing-skill/main/assets/frame-timing-ui.png" alt="Frame Timing Skill single-directory workspace" width="100%">
</p>

#### CPU-only Offline Batch

Add explicit directories or discover a root, process items sequentially, isolate failures, resume explicitly, and approve review items before export.

<p align="center">
  <img src="https://raw.githubusercontent.com/Taiquan-Zhou/frame-timing-skill/main/assets/frame-timing-batch-ui.png" alt="Frame Timing Skill offline batch workspace" width="100%">
</p>

### Windows Desktop App

**[Download FrameTimingSkill for Windows x64](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)**

1. Download and extract `FrameTimingSkill-Windows-x64.zip`.
2. Run `FrameTimingSkill.exe`.
3. Select a frame directory, set FPS, and start analysis.

All processing stays local. Source frames remain unchanged, and run artifacts are written under a sibling `output/` directory.

### Highlights

- Motion, sharpness, and contrast timelines.
- Static, fast-motion, very-fast-motion, and `review_required` ranges.
- Strategy-related representative frames, local history, and recoverable batches.
- Failure isolation, explicit retry, human approval, and explicit export.
- Input and strategy binding, byte-level output verification, and audit artifacts.

### Offline Batch CLI

Repeat `--frames` for explicit directories, or use `--root` for discovery. State is stored at `output/**/analysis/batch_state.json`.

```bash
frame-timing-tool batch create --frames path/to/a/frames --frames path/to/b/frames --state output/frame_timing_batch/analysis/batch_state.json --fps 30
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch status --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json --retry-item FAILED_ITEM_NAME
frame-timing-tool batch approve --state output/frame_timing_batch/analysis/batch_state.json --item ITEM_NAME --note "reviewed"
frame-timing-tool batch export --state output/frame_timing_batch/analysis/batch_state.json
```

Neither the app nor an Agent automatically resumes, retries, approves, or exports. Only verified item `output_frames/` directories should be passed downstream.

### Use as an Agent Skill

```text
Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill
```

```text
Use frame-timing-skill on path/to/clean_frames.
Analyze first, validate before apply, and verify before using output_frames downstream.
```

## For Agents And Developers

### Agent-safe v3 JSON CLI

Agent-safe v3 uses `schema_version 3` and policy revision `coverage-static-thinning-v1`:

```text
analyze -> plan -> validate -> apply -> verify
```

```bash
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
frame-timing-tool apply --frames path/to/clean_frames --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
frame-timing-tool verify --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

Policies are `coverage_first`, `balanced`, and `jitter_reduction`; `coverage_first` is the recommended default.

### Installation

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-timing-skill.git

# Optional desktop UI
python -m pip install ".[ui]"
frame-timing-ui
```

Compatibility one-command workflow:

```bash
frame-timing path/to/clean_frames
```

### Outputs and Audit

Agent-safe v3 stores analysis, strategy, validation, execution, health, human-review, and `output_frames/` artifacts under `output/frame_timing_run/`. Output images are byte-identical source-frame copies, and only verified `output_frames/` should be passed downstream.

### v0.5.0 Compatibility

- The existing single-directory workspace and Agent-safe v3 lifecycle remain unchanged.
- Use `frame-timing-tool batch ...` for the new recoverable batch workflow.
- `frame-timing-batch` remains available as a legacy compatibility entry point.

## License

MIT. See [LICENSE](LICENSE).
