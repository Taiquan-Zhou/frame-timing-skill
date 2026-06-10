# Frame Timing Skill

[English](README.md) | [中文](README.zh-CN.md)

Frame Timing Skill is a local Python package and Codex skill for optimizing already-clean extracted video frames before reconstruction.

It detects static and fast-motion ranges, writes byte-identical copied output frames, and creates local review artifacts under `agent_files/`. It does not extract video, remove watermarks, run OCR, edit pixels, upload data, or run reconstruction.

## Features

- Detect static and fast-motion frame ranges.
- Generate a timing strategy for downstream reconstruction workflows.
- Write model-safe `output_frames/` using byte-identical source-frame copies.
- Record `source_sha256` provenance for copied frames.
- Generate human review, visual review, and health reports.
- Provide both CLI entrypoints and a Python API.

## Install

Install from GitHub:

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill.git
```

The `git+` prefix is required by pip when installing directly from a Git repository.

## AI Coding Tool Use

Copy this repository URL into your AI coding tool:

```text
https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill
```

Ask the AI coding tool to run this install command in the target project:

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill.git
```

## Usage

Run frame timing on a directory of already-clean extracted frames:

```bash
frame-timing-batch \
  --frames "sample=path/to/clean_frames" \
  --artifact_root agent_files/frame_timing_run \
  --write
```

Check the generated artifacts:

```bash
frame-timing-health --artifact_root agent_files/frame_timing_run
```

On PowerShell, use backticks for multiline commands:

```powershell
frame-timing-batch `
  --frames "sample=path\to\clean_frames" `
  --artifact_root agent_files\frame_timing_run `
  --write
```

## CLI

- `frame-timing-demo`: generate deterministic demo frames for local checks.
- `frame-timing-batch`: analyze clean frame directories and write `output_frames/` plus review artifacts.
- `frame-timing-health`: verify artifact structure and copied-frame provenance.

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

## Use as a Codex Skill

Install the repository root as a Codex skill:

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo Taiquan-Zhou/frame-Extraction-and-Processing-skill \
  --path . \
  --name frame-timing-skill
```

Restart Codex after installing the skill.

## Output

Model-safe output is written to:

```text
agent_files/<run_name>/<item_name>/output_frames/
```

Review and health artifacts are written under:

```text
agent_files/<run_name>/analysis/
agent_files/<run_name>/<item_name>/analysis/
```

Only `output_frames/` should be passed to downstream reconstruction tools.

## License

MIT. See [LICENSE](LICENSE).
