# Frame Timing Skill

[中文](README.md)

Frame Timing Skill is a local frame timing analysis and selection tool for reconstruction, NeRF, Gaussian Splatting, and photogrammetry. It analyzes an already-clean image-frame directory, identifies static, fast-motion, jitter, and review-required ranges, and produces auditable model-ready frame output.

[![Latest Release](https://img.shields.io/github/v/release/Taiquan-Zhou/frame-timing-skill?label=Release)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest)
[![CI](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml)
[![Download Windows desktop app](https://img.shields.io/badge/Windows-Download-2563eb?logo=windows)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)

## For Users

### Windows Desktop App

The desktop app processes local frame directories without uploading source images or requiring a Python installation.

**[Download FrameTimingSkill for Windows x64](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)**

<p align="center">
  <img src="https://raw.githubusercontent.com/Taiquan-Zhou/frame-timing-skill/main/assets/frame-timing-ui.png" alt="Frame Timing Skill Windows desktop interface" width="100%">
</p>

The desktop workflow provides:

- Local frame-directory selection and FPS configuration.
- Motion, sharpness, and contrast timelines.
- Static, fast-motion, very-fast-motion, and review-required ranges.
- Representative-frame previews related to strategy ranges.
- Managed `output_frames/` generation without modifying source frames.
- Persistent settings, run history, and frozen historical thumbnails.
- Input and strategy digest binding plus copied-output verification.

Usage:

1. Download and extract `FrameTimingSkill-Windows-x64.zip`.
2. Run `FrameTimingSkill.exe`.
3. Select an already-clean image-frame directory, set FPS, and start analysis.
4. Review the chart, ranges, and representative frames, then generate `output_frames`.

All analysis and copying stay on the local machine. Source frames are not overwritten. Desktop artifacts are normally written under a sibling `output/frame_timing_ui/` directory.

### Use as an Agent Skill

Ask an AI agent or coding tool to install this repository:

```text
Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill
```

Then process an already-clean frame directory:

```text
Use frame-timing-skill on path/to/clean_frames.
Analyze first, compare candidates if needed, validate before apply, and verify before using output_frames downstream.
```

## For Agents And Developers

### Agent-safe v3 JSON CLI

Agent-safe v3 separates `analyze -> plan -> validate -> apply -> verify` and uses `schema_version 3` with policy revision `coverage-static-thinning-v1`.

<p align="center">
  <img src="https://raw.githubusercontent.com/Taiquan-Zhou/frame-timing-skill/main/assets/frame-timing-workflow.png" alt="Frame Timing workflow: clean_frames to analyze, plan, validate, apply, verify and output_frames" width="100%">
</p>

```bash
frame-timing-tool capabilities
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
frame-timing-tool apply --frames path/to/clean_frames --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
frame-timing-tool verify --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

Candidate policies:

- `coverage_first`: recommended default; prioritizes non-static frame coverage.
- `balanced`: compares coverage and output-count tradeoffs.
- `jitter_reduction`: more aggressive jitter reduction that requires stricter review.

### Install from Source

Install the command-line package:

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-timing-skill.git
```

Install the optional desktop UI:

```bash
git clone https://github.com/Taiquan-Zhou/frame-timing-skill.git
cd frame-timing-skill
python -m pip install ".[ui]"
frame-timing-ui
```

Compatibility one-command workflow:

```bash
frame-timing path/to/clean_frames
```

### Outputs and Audit

Agent-safe v3 writes these artifacts under `output/frame_timing_run/`:

- `analysis.json`
- `strategy.json`
- `validation.json`
- `execution.json`
- `health.json`
- `report.md`
- `human_review.md`
- `output_frames/`

Only `output_frames/` should be passed downstream. Output images are byte-identical source-frame copies. This project does not extract video, edit pixels, deblur or stabilize images, upload data, or run reconstruction.

## License

MIT. See [LICENSE](LICENSE).
