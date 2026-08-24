# Frame Timing Skill

[中文](README.md)

Frame Timing Skill is a local frame timing analysis and selection tool for reconstruction, NeRF, Gaussian Splatting, and photogrammetry. It analyzes an already-clean image-frame directory, identifies static, fast-motion, jitter, and review-required ranges, and produces auditable model-ready frame output.

[![Latest Release](https://img.shields.io/github/v/release/Taiquan-Zhou/frame-timing-skill?label=Release)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest)
[![CI](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml)
[![Download Windows desktop app](https://img.shields.io/badge/Windows-Download-2563eb?logo=windows)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)

## For Users

### Choose a Workflow

| Workflow | Best for | Entry point | Key behavior |
| --- | --- | --- | --- |
| Single-directory workspace | Inspecting one cleaned frame directory interactively | `FrameTimingSkill.exe` / `frame-timing-ui` | Charts, representative frames, local history, and explicit export |
| CPU-only offline batch | Processing one or more frame directories in sequence | Desktop Batch Processing / `frame-timing-tool batch` | Sequential execution, failure isolation, recovery, and human approval |
| Agent-safe v3 | Agent use or system integration | `frame-timing-tool` | Structured JSON, staged validation, and auditable artifacts |

Input must already be an extracted and cleaned image-frame directory. The project does not extract video, enhance pixels, or run reconstruction.

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

### CPU-only Offline Batches

Batch processing is designed for offline machines without a dedicated GPU and does not require CUDA. The desktop app keeps the existing single-directory workflow and adds a compact Batch Processing workspace for explicit directories or deterministic root discovery.

- Items run sequentially, and one failed item does not stop later items.
- Pause takes effect after the current item. Reopening the app restores the last recorded batch for inspection; an unfinished batch never resumes automatically and requires an explicit Continue action.
- `review_required` is limited to two explainable signals: `bad_quality_candidate >= 10%` and existing `low_motion_review` ranges.
- Review approval and verified export are always explicit actions.

<p align="center">
  <img src="https://raw.githubusercontent.com/Taiquan-Zhou/frame-timing-skill/main/assets/frame-timing-batch-ui.png" alt="Frame Timing Skill recoverable offline batch workspace" width="100%">
</p>

#### Batch Lifecycle

```text
create -> sequential analysis -> inspect status and risks -> explicit retry/approval -> explicit export -> health check
              |                                                   ^
              +------------- wait for user after interruption ----+
```

State is persisted after each directory. Pause takes effect after the current directory, and restarting the app restores the batch for inspection without resuming it automatically.

#### Structured CLI

```bash
frame-timing-ui

frame-timing-tool batch create --frames path/to/a/clean_frames --frames path/to/b/clean_frames --state output/frame_timing_batch/analysis/batch_state.json --fps 30
frame-timing-tool batch create --root path/to/dataset_root --state output/frame_timing_batch/analysis/batch_state.json --fps 30
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch status --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json --retry-item FAILED_ITEM_NAME
frame-timing-tool batch approve --state output/frame_timing_batch/analysis/batch_state.json --item ITEM_NAME --note "reviewed"
frame-timing-tool batch export --state output/frame_timing_batch/analysis/batch_state.json
```

`batch run` is also the explicit resume action. A failed item may be retried with `--retry-item` only after user authorization. Neither the app nor an Agent may automatically resume, retry, approve, or export. The canonical state path is `output/**/analysis/batch_state.json`; item analysis and verified `output_frames/` stay inside the same batch root. Source snapshots are checked before export, copied frames are byte-verified, and source frames are never modified.

#### Batch Artifacts

```text
output/frame_timing_batch/
  analysis/
    batch_state.json          # recoverable state
    batch_summary.json        # machine-readable summary
    batch_summary.csv         # tabular summary
    review_dashboard.md       # human review entry point
    human_review.md           # batch review guidance
    maintenance_report.json   # generated by the health check
  <item-name>/
    analysis/                 # item analysis, strategy, audit, and previews
    output_frames/            # created only after explicit export
```

Do not edit `batch_state.json` manually. Only each item's `output_frames/` should be passed downstream; keep summaries and audit artifacts with the output.

#### v0.5.0 Compatibility

- The existing single-directory desktop workflow and Agent-safe v3 lifecycle remain unchanged.
- Use `frame-timing-tool batch ...` for the new recoverable batch workflow.
- `frame-timing-batch` remains a legacy compatibility entry point and does not provide recovery, approval, or explicit-export state control.

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
