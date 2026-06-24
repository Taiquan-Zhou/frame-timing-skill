# Frame Timing Skill

[English](README.md) | [中文](README.zh-CN.md)

Frame Timing Skill 是一个本地 Python package 和通用 Agent Skill，用于在重建、NeRF、Gaussian Splatting、摄影测量或人工审查前，处理已经清理并抽取好的视频帧。

它可以检测静止区间、快速运动区间，以及高频相机抖动区间。它会把输出帧以字节级一致的方式复制到 `output/` 下，并生成本地审查产物。它不负责视频抽帧、去水印、OCR、修改像素、上传数据或执行重建。

## 功能

- 检测静止帧区间和快速运动帧区间。
- 使用稳定关键帧选择来减少严重相机抖动。
- 为下游重建流程生成 timing strategy。
- 使用源帧的字节级一致副本写出模型安全的 `output_frames/`。
- 记录复制帧的 `source_sha256` 来源校验。
- 生成人工审查、可视化审查、执行审计和健康检查报告。
- 同时提供 CLI 入口和 Python API。

## For Users

### 作为 Agent Skill 使用

普通使用者只需要让你的 AI agent 或 AI 编程工具安装这个 skill：

```text
Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill
```

然后让它处理你的帧目录：

```text
/skill frame-timing-skill
Use frame-timing-skill on path/to/clean_frames
```

agent 应优先运行默认的 `reconstruction_balanced` 模式：`frame-timing path/to/clean_frames`，并用 `frame-timing-health` 验证结果。

## For Developers

### 安装 Python Package

如果要直接使用 CLI 或 Python API：

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-timing-skill.git
```

### CLI 使用方法

对已经清理并抽取好的帧目录运行 frame timing：

```bash
frame-timing path/to/clean_frames
```

默认产物会写入 `output/frame_timing_run`。
默认策略是 `reconstruction_balanced`。

如果要处理多个帧目录，或需要自定义批处理参数，再使用高级 batch 命令：

```bash
frame-timing-batch \
  --frames "sample=path/to/clean_frames" \
  --artifact_root output/frame_timing_run \
  --write
```

检查生成的产物：

```bash
frame-timing-health --artifact_root output/frame_timing_run
```

### CLI Reference

- `frame-timing`：用默认本地产物结构处理一个 clean frame 目录。
- `frame-timing-demo`：生成用于本地检查的确定性 demo frames。
- `frame-timing-batch`：分析 clean frame 目录，并写出 `output_frames/` 和审查产物。
- `frame-timing-health`：验证产物结构和复制帧来源。

默认模式：

- `reconstruction_balanced`：适度压缩长静止区间，重复快速运动区间给建模降速，并在抖动区间选择稳定关键帧。

可以重复传入 `--frames "<item_name>=<clean_frame_dir>"` 来处理多个帧目录。

### Python API

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

## 输出

模型安全输出写入：

```text
output/<run_name>/<item_name>/output_frames/
```

审查、审计和健康检查产物写入：

```text
output/<run_name>/analysis/
output/<run_name>/<item_name>/analysis/
```

只有 `output_frames/` 应传给下游重建工具。

在 `reconstruction_balanced` 模式下，`strategy.json` 使用 strategy version `2`。它可能包含 `keep_uniform`、`duplicate_range` 和 `select_sources` 操作。输出图片仍然是源帧的字节级一致副本；本 package 不会 warp、裁剪、插值或稳定化像素。

## License

MIT. See [LICENSE](LICENSE).
