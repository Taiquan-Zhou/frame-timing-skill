# Frame Timing Skill

[English](README.md) | [中文](README.zh-CN.md)

Frame Timing Skill 用于在三维重建、NeRF、Gaussian Splatting、摄影测量或人工审查之前，处理已经抽取并清理好的图片帧目录。它会分析帧运动和画质，规划安全的选帧策略，以字节级一致的方式复制选中的帧，并写出本地审计产物。

它不负责视频抽帧、修改像素、画面稳定、去模糊、上传数据或执行重建。v0.3.0 提供的是帧选择层面的覆盖保护，不是基于三维几何、视差或相机基线的覆盖优化器。

## 普通用户

让你的 AI agent 或 AI 编程工具安装这个仓库作为 skill：

```text
Install this skill: https://github.com/Taiquan-Zhou/frame-timing-skill
```

然后让它处理已经清理好的帧目录。推荐 Agent 使用 `frame-timing-tool`：

```text
Use frame-timing-skill on path/to/clean_frames.
Analyze first, compare candidates if needed, validate before apply, and verify before using output_frames downstream.
```

如果只需要兼容的一条命令入口，可以使用：

```bash
frame-timing path/to/clean_frames
```

`frame-timing` 是 legacy v2 兼容入口，保留旧的 `reconstruction_balanced` 行为和产物结构，适合简单本地使用。
legacy v2 的策略文件可能包含 `select_sources` 等操作。

## Agent 和开发者

从仓库安装：

```bash
python -m pip install git+https://github.com/Taiquan-Zhou/frame-timing-skill.git
```

### Agent-safe v3 JSON CLI

当 Agent 需要明确、可审计的阶段时，使用 `frame-timing-tool`。该生命周期使用 `schema_version 3` 和策略版本 `coverage-static-thinning-v1`。

```bash
frame-timing-tool capabilities
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
frame-timing-tool apply --frames path/to/clean_frames --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
frame-timing-tool verify --frames path/to/clean_frames --artifact-root output/frame_timing_run
```

v3 策略：

- `coverage_first`：推荐默认策略，用于重建场景；保护非静止段覆盖率，只对高置信静止段做保守稀疏。
- `balanced`：中等风险对比候选。
- `jitter_reduction`：更激进的对比候选；适合视觉审查，但对重建覆盖风险更高。

中风险和高风险候选应先展示给用户确认。验证失败时禁止通过手工编辑 JSON 绕过；apply 阶段会重新验证候选摘要和策略身份。

### Python API

```python
from pathlib import Path
from frame_timing_agent import (
    PolicyName,
    StrategyRequest,
    analyze_frames,
    apply_validated_strategy,
    plan_strategy,
    validate_strategy,
    verify_output,
)

frame_dir = Path("path/to/clean_frames")
artifact_root = Path("output/frame_timing_run")
analysis = analyze_frames(frame_dir, artifact_root)
candidate = plan_strategy(analysis, StrategyRequest(PolicyName.COVERAGE_FIRST), artifact_root)
validation = validate_strategy(analysis, candidate, candidate.request, artifact_root)
execution = apply_validated_strategy(frame_dir, analysis, candidate, validation, artifact_root / "output_frames")
health = verify_output(frame_dir, analysis, candidate, execution, artifact_root / "output_frames")
```

### Benchmark 协议

`frame-timing-benchmark` 用于记录外部冒烟验收结果，不复制私有帧：

```bash
frame-timing-benchmark --case-id sample --frames path/to/clean_frames --artifact-root output/benchmark_sample
```

Benchmark 结果是发布验收证据，不是统计准确率声明。

## 产物

Agent-safe v3 会写出：

```text
output/frame_timing_run/
  analysis.json
  strategy.json
  validation.json
  execution.json
  health.json
  report.md
  human_review.md
  output_frames/
```

只有 `output_frames/` 应传给下游重建工具。输出图片是源帧的字节级一致副本。

## 更多文档

- [使用说明](references/usage.md)
- [产物契约](references/artifact_contract.md)
- [Agent 集成](references/agent-integration.md)
- [从 v2 迁移到 v3](references/migration-v2-to-v3.md)
- [Benchmark 协议](benchmarks/README.md)

## License

MIT. See [LICENSE](LICENSE).
