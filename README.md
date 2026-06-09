# Frame Timing Skill

[English](#english) | [中文](#中文)

<a id="english"></a>

Independent package and Codex skill for optimizing already-clean extracted video frames before reconstruction.

The package detects static and fast-motion ranges, writes byte-identical copied output frames, and generates audit artifacts under `agent_files/`. It does not extract video, remove watermarks, OCR overlays, alter pixels, upload data, or run reconstruction.

## Repository Status

This repository is intended to be published as a standalone GitHub project. It contains runtime code, Codex skill instructions, tests, CI, and release metadata.

The Python release artifacts are slimmed by `MANIFEST.in`; tests, CI files, caches, generated frames, and local handoff notes are kept out of wheel/sdist builds.

## Install

From GitHub:

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill.git
```

From a local checkout:

```powershell
python -m pip install .
```

From another project or agent working directory:

```powershell
python -m pip install <path-to-frame-timing-skill>
```

POSIX shell:

```bash
python -m pip install /path/to/frame-timing-skill
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

POSIX shell:

```bash
frame-timing-demo --output_dir agent_files/demo_frames/sample --count 72
frame-timing-batch --frames "sample=agent_files/demo_frames/sample" --artifact_root agent_files/demo_run --limit_first_n 72 --write
frame-timing-health --artifact_root agent_files/demo_run
```

## CLI Reference

- `frame-timing-demo`: creates synthetic local frames for smoke tests.
- `frame-timing-batch`: analyzes one or more clean frame directories and writes `output_frames/` plus review artifacts.
- `frame-timing-health`: verifies artifact structure, allowed output files, and byte-identical provenance.

Pass multiple frame sets by repeating `--frames "<item_name>=<clean_frame_dir>"`.

## Codex Skill Use

Install this repository as a Codex skill when you want an agent to run the workflow for you. The skill entrypoint is [SKILL.md](SKILL.md), with UI metadata in [agents/openai.yaml](agents/openai.yaml).

Using Codex's skill installer:

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo Taiquan-Zhou/frame-Extraction-and-Processing-skill \
  --path . \
  --name frame-timing-skill
```

The skill calls the same package CLI/API; it does not duplicate host-project code.

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

## Host Project Smoke

From a host project that already has clean extracted frames:

```powershell
python -m pip install <path-to-frame-timing-skill>

frame-timing-batch `
  --frames "sample=path\to\clean_frames" `
  --artifact_root "agent_files\frame_timing_run" `
  --write

frame-timing-health --artifact_root "agent_files\frame_timing_run"
```

POSIX shell:

```bash
python -m pip install /path/to/frame-timing-skill
frame-timing-batch --frames "sample=path/to/clean_frames" --artifact_root agent_files/frame_timing_run --write
frame-timing-health --artifact_root agent_files/frame_timing_run
```

Use CLI integration when the host project only needs artifact generation. Use the Python API when the host project needs structured result objects in-process.

## Release Checklist

- Confirm `LICENSE`, `CHANGELOG.md`, and `SECURITY.md` are present.
- Run `python -m pytest`.
- Run `python -m compileall -q scripts examples tests`.
- Run `python -m build` and inspect that wheel/sdist exclude tests, docs, CI, caches, and handoff files.
- Run `python <path-to-quick_validate.py> .` when the Codex skill validator is available.
- Run an installed CLI smoke from outside the source checkout.
- Tag the release only after health status is `ok` on demo artifacts.

## Release Artifact Scope

The development repository keeps tests and CI so the package remains maintainable. Built wheel/sdist artifacts are intentionally slimmed by `MANIFEST.in` and must not include tests, CI files, migration handoff notes, caches, generated frames, or local agent outputs.

## License

MIT. See [LICENSE](LICENSE).

---

<a id="中文"></a>

# Frame Timing Skill

[English](#english) | [中文](#中文)

用于在重建前优化已经清理并抽取好的视频帧的独立 Python package 和 Codex skill。

本项目可以检测静止区间和快速运动区间，生成字节级一致复制的模型安全输出帧，并在 `agent_files/` 下生成审计产物。它不负责视频抽帧、去水印、OCR、修改像素、上传数据或执行重建。

## 项目状态

这是一个可发布的独立 GitHub 项目，包含运行时代码、Codex skill 指令、测试、CI 和发布元数据。

Python 发布产物通过 `MANIFEST.in` 瘦身；wheel/sdist 不包含测试、CI、缓存、生成帧或本地交接文件。

## 安装

从 GitHub 安装：

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill.git
```

从本地 checkout 安装：

```powershell
python -m pip install .
```

从其他项目或 agent 工作目录安装：

```powershell
python -m pip install <path-to-frame-timing-skill>
```

开发安装：

```powershell
python -m pip install -e .
```

## 快速开始

生成确定性的 demo frames：

```powershell
frame-timing-demo `
  --output_dir agent_files\demo_frames\sample `
  --count 72
```

运行 batch timing agent：

```powershell
frame-timing-batch `
  --frames "sample=agent_files\demo_frames\sample" `
  --artifact_root "agent_files\demo_run" `
  --limit_first_n 72 `
  --write
```

验证输出产物：

```powershell
frame-timing-health --artifact_root agent_files\demo_run
```

POSIX shell：

```bash
frame-timing-demo --output_dir agent_files/demo_frames/sample --count 72
frame-timing-batch --frames "sample=agent_files/demo_frames/sample" --artifact_root agent_files/demo_run --limit_first_n 72 --write
frame-timing-health --artifact_root agent_files/demo_run
```

## CLI

- `frame-timing-demo`：生成用于 smoke test 的合成帧。
- `frame-timing-batch`：分析一个或多个 clean frame 目录，并写出 `output_frames/` 与审计产物。
- `frame-timing-health`：验证产物结构、允许的输出文件和字节级来源一致性。

可以重复传入 `--frames "<item_name>=<clean_frame_dir>"` 来处理多个帧目录。

## Codex Skill 使用

如果希望 Codex agent 自动执行该流程，可以把这个仓库安装为 Codex skill。skill 入口是 [SKILL.md](SKILL.md)，UI 元数据在 [agents/openai.yaml](agents/openai.yaml)。

使用 Codex skill installer：

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo Taiquan-Zhou/frame-Extraction-and-Processing-skill \
  --path . \
  --name frame-timing-skill
```

该 skill 调用同一个 package CLI/API，不会复制宿主项目代码。

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

## 验收清单

发布或集成前运行：

```powershell
python -m pytest
python -m compileall -q scripts examples tests
frame-timing-health --artifact_root agent_files\demo_run
```

`frame-timing-health` 必须返回 `ok`。生成的 `output_frames/selected_frames.txt` 包含 `source_sha256`，用于在不记录私有输入路径的情况下验证复制帧来源。

## 宿主项目 Smoke

当宿主项目已经生成 clean frames 后，在宿主项目根目录运行：

```powershell
python -m pip install <path-to-frame-timing-skill>

frame-timing-batch `
  --frames "sample=path\to\clean_frames" `
  --artifact_root "agent_files\frame_timing_run" `
  --write

frame-timing-health --artifact_root "agent_files\frame_timing_run"
```

POSIX shell：

```bash
python -m pip install /path/to/frame-timing-skill
frame-timing-batch --frames "sample=path/to/clean_frames" --artifact_root agent_files/frame_timing_run --write
frame-timing-health --artifact_root agent_files/frame_timing_run
```

如果宿主项目只需要生成产物，优先使用 CLI；如果需要在 Python 进程内组合结果对象，再使用 Python API。

## 发布产物范围

开发仓库保留测试和 CI，以保证可维护性。构建出的 wheel/sdist 会由 `MANIFEST.in` 瘦身，不应包含测试、CI 文件、迁移交接记录、缓存、生成帧或本地 agent 输出。

## License

MIT. See [LICENSE](LICENSE).
