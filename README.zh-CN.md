# Frame Timing Skill

[English](README.md) | [中文](README.zh-CN.md)

Frame Timing Skill 是一个本地 Python package 和 Codex skill，用于在重建前优化已经清理并抽取好的视频帧。

它可以检测静止区间和快速运动区间，生成字节级一致复制的模型安全输出帧，并在 `agent_files/` 下生成本地审查产物。它不负责视频抽帧、去水印、OCR、修改像素、上传数据或执行重建。

## 功能

- 检测静止帧区间和快速运动帧区间。
- 为下游重建流程生成 timing strategy。
- 使用源帧的字节级一致副本写出模型安全的 `output_frames/`。
- 记录复制帧的 `source_sha256` 来源校验。
- 生成人工审查、可视化审查和健康检查报告。
- 同时提供 CLI 入口和 Python API。

## 安装

从 GitHub 安装：

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill.git
```

这里的 `git+` 是 pip 从 Git 仓库直接安装时需要的标准前缀。

## AI 编程工具调用

把这个仓库网址复制给 AI 编程工具：

```text
https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill
```

然后让 AI 编程工具在目标项目里运行这个安装命令：

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-Extraction-and-Processing-skill.git
```

再让它用这一条命令处理帧目录：

```bash
frame-timing path/to/clean_frames
```

## 使用方法

对已经清理并抽取好的帧目录运行 frame timing：

```bash
frame-timing path/to/clean_frames
```

默认产物会写入 `agent_files/frame_timing_run`。

PowerShell：

```powershell
frame-timing path\to\clean_frames
```

如果要处理多个帧目录，或需要自定义批处理参数，再使用高级 batch 命令：

```bash
frame-timing-batch \
  --frames "sample=path/to/clean_frames" \
  --artifact_root agent_files/frame_timing_run \
  --write
```

检查生成的产物：

```bash
frame-timing-health --artifact_root agent_files/frame_timing_run
```

PowerShell batch 命令示例：

```powershell
frame-timing-batch `
  --frames "sample=path\to\clean_frames" `
  --artifact_root agent_files\frame_timing_run `
  --write
```

## CLI

- `frame-timing`：用默认本地产物结构处理一个 clean frame 目录。
- `frame-timing-demo`：生成用于本地检查的确定性 demo frames。
- `frame-timing-batch`：分析 clean frame 目录，并写出 `output_frames/` 和审查产物。
- `frame-timing-health`：验证产物结构和复制帧来源。

可以重复传入 `--frames "<item_name>=<clean_frame_dir>"` 来处理多个帧目录。

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

## 作为 Codex Skill 使用

把仓库根目录安装为 Codex skill：

```bash
python ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo Taiquan-Zhou/frame-Extraction-and-Processing-skill \
  --path . \
  --name frame-timing-skill
```

安装后重启 Codex。

在支持 slash command 的 AI 编程工具里，选择或调用 `/skill frame-timing-skill`，然后让它处理你的帧目录：

```text
Use frame-timing-skill on path/to/clean_frames
```

该 skill 应优先运行 `frame-timing path/to/clean_frames`，并用 `frame-timing-health` 验证结果。

## 输出

模型安全输出写入：

```text
agent_files/<run_name>/<item_name>/output_frames/
```

审查和健康检查产物写入：

```text
agent_files/<run_name>/analysis/
agent_files/<run_name>/<item_name>/analysis/
```

只有 `output_frames/` 应传给下游重建工具。

## License

MIT. See [LICENSE](LICENSE).
