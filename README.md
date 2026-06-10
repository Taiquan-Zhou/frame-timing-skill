# Frame Timing Skill

[English](README.md) | [中文](README.zh-CN.md)

Frame Timing Skill is a local Python package and portable Agent Skill for optimizing already-clean extracted video frames before reconstruction.

It detects static and fast-motion ranges, writes byte-identical copied output frames, and creates local review artifacts under `output/`. It does not extract video, remove watermarks, run OCR, edit pixels, upload data, or run reconstruction.

## Features

- Detect static and fast-motion frame ranges.
- Generate a timing strategy for downstream reconstruction workflows.
- Write model-safe `output_frames/` using byte-identical source-frame copies.
- Record `source_sha256` provenance for copied frames.
- Generate human review, visual review, and health reports.
- Provide both CLI entrypoints and a Python API.

## Install as an Agent Skill

This repository is designed to be used by any AI agent or coding tool that supports local skills/instructions.

The skill entrypoint is:

```text
SKILL.md
```

Install it by cloning or downloading this repository into your agent's skills directory:

```bash
git clone https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill.git <your-agent-skills-dir>/frame-timing-skill
```

If your agent provides a GitHub skill installer, use these values:

```text
repo: Taiquan-Zhou/frame-Extraction-and-Processing-skill
path: .
name: frame-timing-skill
```

After installation, invoke the skill through your agent's skill picker or slash-command interface, if available:

```text
/skill frame-timing-skill
Use frame-timing-skill on path/to/clean_frames
```

The agent should run `frame-timing path/to/clean_frames` and verify the result with `frame-timing-health`.

## Install Python Package

Install from GitHub:

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill.git
```

## Usage

Run frame timing on a directory of already-clean extracted frames:

```bash
frame-timing your_frames_path
```

By default, artifacts are written to `output/frame_timing_run`.

For multiple frame directories or custom batch settings, use the advanced batch command:

```bash
frame-timing-batch \
  --frames "sample=your_frames_path" \
  --artifact_root output/frame_timing_run \
  --write
```

Check the generated artifacts:

```bash
frame-timing-health --artifact_root output/frame_timing_run
```

## CLI

- `frame-timing`: process one clean frame directory with the default local artifact layout.
- `frame-timing-demo`: generate deterministic demo frames for local checks.
- `frame-timing-batch`: analyze clean frame directories and write `output_frames/` plus review artifacts.
- `frame-timing-health`: verify artifact structure and copied-frame provenance.

Pass multiple frame sets by repeating `--frames "<item_name>=<clean_frame_dir>"`.

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

## Output

Model-safe output is written to:

```text
output/<run_name>/<item_name>/output_frames/
```

Review and health artifacts are written under:

```text
output/<run_name>/analysis/
output/<run_name>/<item_name>/analysis/
```

Only `output_frames/` should be passed to downstream reconstruction tools.

## License

MIT. See [LICENSE](LICENSE).
