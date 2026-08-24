# Frame Timing Skill

[English](README.en.md)

面向三维重建数据准备的 **Agent 可调用帧分析与筛选引擎**。同一套核心能力既可以作为 Skill / CLI 接入 Agent，也提供 Windows **本地人工工作台**，用于单目录复核、CPU-only 离线批处理和可验证输出。

[![Latest Release](https://img.shields.io/github/v/release/Taiquan-Zhou/frame-timing-skill?label=Release)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest)
[![CI](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml/badge.svg)](https://github.com/Taiquan-Zhou/frame-timing-skill/actions/workflows/ci.yml)
[![下载 Windows 桌面版](https://img.shields.io/badge/Windows-下载桌面版-2563eb?logo=windows)](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)

```text
Agent Skill / CLI ─┐
                   ├─ analyze → plan → validate → apply → verify
Windows 工作台 ───┘
```

当前版本以帧目录为输入。核心分析与策略执行在固定输入和配置下可复现；人工复核、批准和导出始终由用户显式触发。处理不依赖云端服务，也不会修改源帧。

## 桌面工作台

### 选择工作流

| 工作流 | 适用场景 | 入口 |
| --- | --- | --- |
| 单目录工作台 | 分析、查看并导出一个帧目录 | `FrameTimingSkill.exe` / `frame-timing-ui` |
| CPU-only 离线批次 | 顺序处理一个或多个帧目录 | 桌面端“批量处理” / `frame-timing-tool batch` |
| Agent-safe v3 | Agent Skill 或系统集成 | `frame-timing-tool` |

#### 单目录工作台

选择帧目录并设置 FPS，查看时序曲线、区间和代表帧，确认后生成 `output_frames/`。

<p align="center">
  <img src="assets/frame-timing-ui.png" alt="Frame Timing Skill 单目录工作台" width="100%">
</p>

#### CPU-only 离线批次

添加多个目录或发现根目录，顺序分析并隔离失败项；中断后由用户继续，待复核项经显式批准后才能导出。

<p align="center">
  <img src="assets/frame-timing-batch-ui.png" alt="Frame Timing Skill 离线批次工作台" width="100%">
</p>

### Windows 桌面版

**[下载 FrameTimingSkill Windows x64](https://github.com/Taiquan-Zhou/frame-timing-skill/releases/latest/download/FrameTimingSkill-Windows-x64.zip)**

1. 下载并解压 `FrameTimingSkill-Windows-x64.zip`。
2. 运行 `FrameTimingSkill.exe`。
3. 选择帧目录，设置 FPS，然后开始分析。

所有处理均在本机完成。源帧保持不变，运行产物写入源目录旁的 `output/`。

### 主要能力

- 运动、清晰度和对比度时序曲线。
- 静止、快速运动、极快速运动和 `review_required` 区间。
- 策略相关代表帧、运行历史与可恢复批次。
- 单项失败隔离、显式重试、人工批准和显式导出。
- 输入与策略摘要绑定、输出帧字节验证和审计产物。

### 离线批次 CLI

`--frames` 可重复传入；也可以用 `--root` 发现目录。批次状态保存在 `output/**/analysis/batch_state.json`。

```bash
frame-timing-tool batch create --frames path/to/a/frames --frames path/to/b/frames --state output/frame_timing_batch/analysis/batch_state.json --fps 30
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch status --state output/frame_timing_batch/analysis/batch_state.json
frame-timing-tool batch run --state output/frame_timing_batch/analysis/batch_state.json --retry-item FAILED_ITEM_NAME
frame-timing-tool batch approve --state output/frame_timing_batch/analysis/batch_state.json --item ITEM_NAME --note "reviewed"
frame-timing-tool batch export --state output/frame_timing_batch/analysis/batch_state.json
```

程序和 Agent 不会自动继续、重试、批准或导出。只有各项目验证后的 `output_frames/` 应传给下游工具。

## Agent 与系统集成

### 作为 Agent Skill 使用

```text
Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill
```

```text
Use frame-timing-skill on path/to/clean_frames.
Analyze first, validate before apply, and verify before using output_frames downstream.
```

### Agent-safe v3 JSON CLI

Agent-safe v3 使用 `schema_version 3` 和策略修订号 `coverage-static-thinning-v1`：

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

策略包括 `coverage_first`、`balanced` 和 `jitter_reduction`；默认推荐 `coverage_first`。

### 安装

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-timing-skill.git

# 可选桌面界面
python -m pip install ".[ui]"
frame-timing-ui
```

兼容的一条命令流程：

```bash
frame-timing path/to/clean_frames
```

### 输出与审计

Agent-safe v3 在 `output/frame_timing_run/` 中保存分析、策略、验证、执行、健康检查、人工审查和 `output_frames/`。输出图片是源帧的字节级复制，只有验证后的 `output_frames/` 用于下游处理。

### v0.5.0 兼容说明

- 原有单目录工作台和 Agent-safe v3 五阶段命令保持不变。
- 新的可恢复批次使用 `frame-timing-tool batch ...`。
- `frame-timing-batch` 保留为旧版兼容入口。

## License

MIT. See [LICENSE](LICENSE).
