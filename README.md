# Frame Timing Skill

[English](README.en.md)

Frame Timing Skill 是一个面向三维重建、NeRF、Gaussian Splatting 和摄影测量的本地帧时序分析与选择工具。它分析已经清理好的图片帧目录，帮助识别静止、快速运动、抖动和需要人工复核的区间，并生成可审计的建模帧输出。

[![Latest Release](https://img.shields.io/github/v/release/Taiquan-Zhou/frame-timing-skill?label=Release)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest)
[![CI](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml)
[![下载 Windows 桌面版](https://img.shields.io/badge/Windows-下载桌面版-2563eb?logo=windows)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)

## 普通用户

### Windows 桌面版

桌面版适合直接检查本地帧目录，不需要上传原图，也不需要先配置 Python。

**[下载 FrameTimingSkill Windows x64](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)**

<p align="center">
  <img src="https://raw.githubusercontent.com/Taiquan-Zhou/frame-timing-skill/main/assets/frame-timing-ui.png" alt="Frame Timing Skill Windows desktop interface" width="100%">
</p>

桌面端目前提供：

- 选择已清理的帧目录并设置 FPS。
- 查看运动、清晰度和对比度时序曲线。
- 查看静止、快速运动、极快速运动和待复核区间。
- 预览与策略区间相关的代表帧。
- 生成独立的 `output_frames/`，不修改源帧目录。
- 保存本地设置、运行记录和历史结果缩略图。
- 导出前绑定输入和策略摘要，并校验复制后的输出帧。

使用步骤：

1. 下载并解压 `FrameTimingSkill-Windows-x64.zip`。
2. 运行 `FrameTimingSkill.exe`。
3. 选择已经清理好的图片帧目录，设置 FPS，然后点击“开始分析”。
4. 检查曲线、区间和代表帧，确认后点击“生成 output_frames”。

所有分析和复制操作都在本机完成。源目录不会被覆盖；运行产物默认写入源目录旁的 `output/frame_timing_ui/`。

### CPU-only 离线批次

批量处理面向没有独立显卡的离线生产环境，当前只使用 CPU，不要求 CUDA。桌面端通过“单目录 / 批量处理”切换保留原有单目录流程，并支持：

- 添加单目录或多个目录；也可以选择“发现根目录”，按确定性顺序发现其中的帧目录。
- 顺序分析各项目；单个项目失败不会阻止后续项目。
- 在当前项目完成后暂停。程序重新打开时恢复上次记录的批次；已完成批次可以重新查看，未完成批次必须由用户显式继续。
- 对 `review_required` 项展示可解释风险；首版只有 `bad_quality_candidate >= 10%` 和存在 `low_motion_review` 区间两类信号。
- 只有用户显式批准后，待复核项目才具备导出资格；导出也必须显式触发。

<p align="center">
  <img src="https://raw.githubusercontent.com/Taiquan-Zhou/frame-timing-skill/main/assets/frame-timing-batch-ui.png" alt="Frame Timing Skill recoverable offline batch workspace" width="100%">
</p>

启动桌面端：

```bash
frame-timing-ui
```

结构化 CLI 支持单目录、多个目录和根目录发现。`--frames` 可以重复传入：

```bash
# 单目录或多个目录
frame-timing-tool batch create --frames path/to/a/clean_frames --frames path/to/b/clean_frames --state output/frame_timing_batch/analysis/batch_state.json --fps 30

# 从根目录发现
frame-timing-tool batch create --root path/to/dataset_root --state output/frame_timing_batch/analysis/batch_state.json --fps 30

frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch status --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json --retry-item FAILED_ITEM_NAME
frame-timing-tool batch approve --state output/frame_timing_batch/analysis/batch_state.json --item ITEM_NAME --note "reviewed"
frame-timing-tool batch export --state output/frame_timing_batch/analysis/batch_state.json
```

`batch run` 也是中断后的显式继续入口；失败项只有在用户同意后才能通过 `--retry-item` 显式重试。程序和 Agent 都不会自动继续、重试、批准或导出。批次状态文件必须使用规范路径 `output/**/analysis/batch_state.json`；每个项目的分析和 `output_frames/` 位于同一批次根目录下。分析和导出前后都会校验输入快照，输出帧按字节验证，整个流程不会修改源帧。待复核项需要显式批准，批次完成后仍需显式导出。

### 作为 Agent Skill 使用

让 AI Agent 或 AI 编程工具安装本仓库：

```text
Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill
```

然后让 Agent 处理已清理的帧目录：

```text
Use frame-timing-skill on path/to/clean_frames.
Analyze first, compare candidates if needed, validate before apply, and verify before using output_frames downstream.
```

## AI Agent 和开发者

### Agent-safe v3 JSON CLI

Agent-safe v3 把处理过程拆成 `analyze -> plan -> validate -> apply -> verify`，使用 `schema_version 3` 和策略修订号 `coverage-static-thinning-v1`。

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

三种候选策略：

- `coverage_first`：推荐默认策略，优先保护非静止帧覆盖。
- `balanced`：用于比较覆盖率与帧数之间的折中。
- `jitter_reduction`：更积极地减少抖动候选帧，需要更严格的人工复核。

### 从源码安装

安装命令行工具：

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-timing-skill.git
```

安装可选桌面界面：

```bash
git clone https://github.com/Taiquan-Zhou/frame-timing-skill.git
cd frame-timing-skill
python -m pip install ".[ui]"
frame-timing-ui
```

兼容的一条命令流程：

```bash
frame-timing path/to/clean_frames
```

### 输出与审计

Agent-safe v3 会在 `output/frame_timing_run/` 下写入：

- `analysis.json`
- `strategy.json`
- `validation.json`
- `execution.json`
- `health.json`
- `report.md`
- `human_review.md`
- `output_frames/`

只有 `output_frames/` 应传给下游建模工具。输出图像是源帧的字节级复制；本项目不做视频抽帧、像素修改、去模糊、图像稳定、云端上传或三维重建。

## License

MIT. See [LICENSE](LICENSE).
