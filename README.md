<div align="center">

# Frame Timing Skill

### 让 3D 重建从更好的帧开始

**Agent-ready video-to-reconstruction pipeline**

[Windows 下载](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip) · [快速开始](#快速开始) · [Agent Skill](SKILL.md) · [English](README.en.md)

[![Latest Release](https://img.shields.io/github/v/release/Taiquan-Zhou/frame-timing-skill?label=Release)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest) [![CI](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml) [![下载 Windows 桌面版](https://img.shields.io/badge/Windows-下载桌面版-2563eb?logo=windows)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)

</div>

<p align="center">
  <img src="assets/frame-timing-workflow.gif" alt="从原始视频到可验证建模帧的 Frame Timing Skill 工作流" width="100%">
</p>

<p align="center">
  <strong>Local-first</strong> · <strong>CPU-ready</strong> · <strong>Recoverable</strong> · <strong>Auditable</strong>
</p>

同一套核心同时服务 **Agent 可调用接口**与 Windows **本地人工工作台**。处理在本机完成，不修改源素材；人工复核、批准和导出始终由用户显式触发。

## 一套核心，两种工作方式

桌面端覆盖单项目复核和可恢复离线批次；同一核心通过 `frame-timing-tool` 为 Agent 与系统集成提供稳定 JSON 接口。

### 单目录工作台

查看运动、清晰度和对比度时序，结合区间与代表帧完成复核，然后生成 `output_frames/`。

<p align="center">
  <img src="assets/frame-timing-ui.png" alt="Frame Timing Skill 单目录工作台" width="100%">
</p>

### 可恢复离线批次

批量分析多个项目，隔离单项失败并持久化进度。中断后由用户继续，`review_required` 项经显式批准后才能导出。

<p align="center">
  <img src="assets/frame-timing-batch-ui.png" alt="Frame Timing Skill 离线批次工作台" width="100%">
</p>

## 快速开始

### Windows 桌面版

**[下载 FrameTimingSkill Windows x64](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)**

1. 下载并解压 `FrameTimingSkill-Windows-x64.zip`。
2. 运行 `FrameTimingSkill.exe`。
3. 选择输入，确认参数，然后开始处理。

运行产物写入独立的 `output/`，不会覆盖源素材。

### Python 与 Agent Skill

```bash
python -m pip install "frame-timing-skill @ git+https://github.com/Taiquan-Zhou/frame-timing-skill.git"

# 可选桌面界面
python -m pip install "frame-timing-skill[ui] @ git+https://github.com/Taiquan-Zhou/frame-timing-skill.git"
frame-timing-ui
```

```text
Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill

Use frame-timing-skill to prepare <video-or-frame-directory> for reconstruction.
Pause when review is required, and verify outputs before downstream use.
```

## 核心能力

- 原始视频导入、自适应抽帧与质量评估。
- 运动、清晰度、对比度和时序区间分析。
- 面向重建覆盖率的帧筛选与策略代表帧。
- 单目录工作台和 CPU-only 可恢复离线批次。
- 失败隔离、显式重试、人工批准与显式导出。
- 输入和策略摘要绑定、字节级输出验证与审计产物。

## Agent 与系统集成

### Agent-safe v3 JSON CLI

`frame-timing-tool` 为 Agent 和系统集成提供稳定的 JSON 接口。兼容的一条命令流程仍可使用：

```bash
frame-timing path/to/clean_frames
```

<details>
<summary><strong>查看五阶段 CLI</strong></summary>

程序化工作流保持清晰的安全边界：

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

策略包括 `coverage_first`、`balanced` 和 `jitter_reduction`，默认推荐 `coverage_first`。

</details>

<details>
<summary><strong>离线批次 CLI</strong></summary>

批次状态保存在 `output/**/analysis/batch_state.json`：

```bash
frame-timing-tool batch create --frames path/to/a/frames --frames path/to/b/frames --state output/frame_timing_batch/analysis/batch_state.json --fps 30
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch status --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json --retry-item FAILED_ITEM_NAME
frame-timing-tool batch approve --state output/frame_timing_batch/analysis/batch_state.json --item ITEM_NAME --note "reviewed"
frame-timing-tool batch export --state output/frame_timing_batch/analysis/batch_state.json
```

</details>

## 可信边界

- 源素材保持不变，输出写入独立目录。
- 固定输入和配置下，分析与策略结果可复现。
- 程序和 Agent 不会自动继续、重试、批准或导出。
- Agent-safe v3 使用 `schema_version 3` 和策略修订号 `coverage-static-thinning-v1`。
- 分析、策略、验证、执行、健康检查和人工复核产物保存在 `output/frame_timing_run/`。
- 输出帧是源帧的字节级复制，只有验证后的 `output_frames/` 应进入下游流程。

本项目负责重建前的数据准备，不执行三维重建或模型训练。

## License

MIT. See [LICENSE](LICENSE).
