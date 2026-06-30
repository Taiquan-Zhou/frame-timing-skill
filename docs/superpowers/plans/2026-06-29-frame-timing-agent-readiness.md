# Frame Timing Agent Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `frame-timing-skill` 从需要人工理解内部参数的 Alpha pipeline，升级为可被任意 Agent 安全调用、可审计、可回滚，并能保护三维重建覆盖率的确定性工具内核。

**Architecture:** 核心包继续负责帧分析、策略规划、约束验证、无损复制执行和结果审计，不引入任何 LLM SDK。新增的 Agent-safe v3 Python API 与 JSON CLI 形成独立入口；现有 v2 Python facade、批处理和一条命令 CLI 在 v0.3.0 中冻结行为，不根据可选参数静默切换到 v3。Agent 只能通过 v3 入口选择受约束的策略预设、查看候选方案并提交执行；所有危险参数、低置信度判断和覆盖率限制由包内验证器控制。

**Tech Stack:** Python 3.10-3.12、NumPy、OpenCV、标准库 `dataclasses/enum/json/pathlib/hashlib`；开发工具使用 pytest、pytest-cov、Ruff、mypy、build、twine、pip-audit 和 GitHub Actions。

---

## 1. 结论和实施边界

### 1.1 当前成熟度

当前版本可以作为“有人工审查的帧策略 pipeline”使用，也可以作为 Agent 的底层命令调用，但不能让 LLM 无约束地修改底层阈值后直接向建模系统交付结果。

已具备的基础：

- 输入帧只读，输出隔离在 `output/`。
- 输出帧保持源文件字节不变。
- 已有策略、执行、审计、健康检查和批处理结构。
- 已有 CLI、Python 调用入口和 99 项测试。
- 支持静止段、快速运动段、抖动候选段和人工 override。

当前阻塞 Agent 自主调用的主要问题：

- `mode` 只有 `reconstruction_balanced`，没有正式的多策略契约。
- `override_config` 没有严格 Schema、未知字段拒绝和参数间约束。
- 抖动检测仅依赖连续相邻位移方向反转，无法可靠区分慢速主动运动与叠加抖动。
- `duplicate_range` 对三维重建不增加新视角，默认策略不应继续生成重复帧。
- 没有最低保留率、最大连续丢帧数、首尾帧保护和低置信度回退等硬性约束。
- Python 公共 API 未从包根导出；文档引用了不存在的 `result.batch_report`。
- PowerShell 未指定 UTF-8 时可能把中文报告显示为乱码；源文件已确认是有效 UTF-8，但缺少跨平台编码回归测试。

### 1.2 明确不做

- 不在本包中接入 OpenAI、Anthropic、Google 或其他 LLM SDK。
- 不实现图像去模糊、生成式补帧、裁剪、透视校正或像素级稳定。
- 不运行 NeRF、Gaussian Splatting、COLMAP 或其他建模程序。
- 不允许 LLM 绕过验证器直接调用文件执行器。
- 不在 v0.3.0 中把现有 v2 facade 或一条命令 CLI 部分迁移到 v3；迁移必须在真实样本验证后作为独立版本变更实施。
- 不把私有测试视频、绝对路径或大体积运行产物提交到仓库。
- 不为了减少帧数而牺牲可观测的建模覆盖率。

## 2. 目标调用流程

```text
Agent / Host Project (v3 only)
        |
        v
capabilities()      查询版本、策略、参数边界和能力限制
        |
        v
parse + resolve     将 Agent 请求解析为 ResolvedStrategyConfig
        |
        v
analyze_frames()    只分析，不生成建模帧
        |
        v
plan_strategy()     生成一个或多个候选策略
        |
        v
validate_strategy() 执行硬性覆盖与安全校验
        |
        v
apply_validated_strategy() 仅接受已验证且摘要匹配的策略
        |
        v
verify_output()     验证数量、来源哈希、间隔、报告和目录边界
```

LLM 负责解释用户目标、选择预设、比较候选方案和处理人工确认。核心包负责计算、阈值自适应、参数合法性、安全约束、执行和审计。

兼容路径与上述流程严格隔离：`run_timing_agent()`、`run_batch_timing_agent()` 和现有 `frame-timing` 命令在 v0.3.0 中继续执行完整的 v2 流程。是否提供 `override_config_path` 只影响 v2 自身，不得成为选择 v2/v3 引擎的路由条件。新代码不得从 v3 `service.py` 反向调用 legacy facade，也不得从 legacy facade 隐式转调 v3 service。

## 3. 技术栈与依赖政策

### 3.1 运行时

| 技术 | 用途 | 决策 |
|---|---|---|
| Python 3.10-3.12 | 包、CLI、Agent API | 保持现状 |
| NumPy | 轨迹、统计、矩阵 | 保持现状 |
| OpenCV | 特征、光流、仿射估计、图像指标 | 保持现状 |
| dataclasses + Enum | 稳定契约和类型 | 新增，无额外依赖 |
| JSON | Agent 与 CLI 数据交换 | Schema 带版本号 |

不引入 SciPy、Pydantic 或 LLM 框架。配置校验使用冻结 dataclass、显式解析器和验证函数，避免给仅做帧处理的包增加大型运行时依赖。

### 3.2 开发与审查工具

| 工具 | 目的 | CI 门禁 |
|---|---|---|
| pytest | 单元、集成、回归测试 | 必须通过 |
| pytest-cov | 核心模块覆盖率 | 总体不低于 90%，核心验证器不低于 95% |
| Ruff | 格式、导入、错误模式、复杂度 | 必须通过 |
| mypy | 公共 API 与核心数据流类型检查 | 必须通过 |
| build | wheel 和 sdist 构建 | 必须通过 |
| twine check | 包元数据和 README 渲染 | 必须通过 |
| pip-audit | 依赖漏洞检查 | 高危漏洞阻断发布 |
| 自定义 wheel audit | 防止测试、文档、缓存进入 wheel | 必须通过 |
| GitHub Actions | Windows + Ubuntu、Python 3.10/3.12 | 全矩阵通过 |

`Ruff`、`mypy`、`pytest-cov`、`build`、`twine`、`pip-audit` 放入 `[project.optional-dependencies].dev`，不进入普通用户运行依赖。

## 4. 目标模块设计

### 4.1 新增模块

| 文件 | 单一职责 |
|---|---|
| `scripts/frame_timing_agent/contracts.py` | 公开枚举、请求、结果、候选方案和验证问题类型 |
| `scripts/frame_timing_agent/configuration.py` | 策略预设、严格配置解析、边界和交叉字段验证 |
| `scripts/frame_timing_agent/motion_model.py` | 全局相机变换估计、轨迹构建、置信度和降级路径 |
| `scripts/frame_timing_agent/analysis.py` | 组合现有质量指标与运动模型，生成不含策略决策的 `AnalysisResult` |
| `scripts/frame_timing_agent/strategy_planner.py` | 从分析结果生成覆盖优先、平衡、去抖候选策略 |
| `scripts/frame_timing_agent/strategy_validator.py` | 保留率、连续丢帧、端点、范围、重复和摘要校验 |
| `scripts/frame_timing_agent/service.py` | analyze/plan/validate/apply/verify 五阶段编排 |
| `scripts/frame_timing_agent/tool_cli.py` | 面向 Agent 的稳定 JSON CLI，不包含业务算法 |

### 4.2 保留并收敛的模块

| 文件 | 调整方向 |
|---|---|
| `frame_source.py` | 保持加载职责，新增输入摘要和尺寸一致性检查 |
| `timing_metrics.py` | 保留单帧质量指标；由 `analysis.py` 与运动结果组合为 `FrameAnalysis` |
| `apply_frame_strategy.py` | 新增仅接受验证结果的 v3 执行函数；现有 v2 `apply_strategy()` 保持兼容行为 |
| `strategy_execution_audit.py` | 增加覆盖约束和计划摘要一致性审计 |
| `batch_artifact_health.py` | 调用统一验证器，避免重复规则 |
| `strategy_visual_review.py` | 继续生成人工审查材料，不参与决策 |
| `auto_timing_agent.py` | 冻结为 legacy v2 facade，不转调 `service.py` |
| `batch_timing_agent.py` | 冻结为 legacy v2 批处理入口，不转调 `service.py` |
| `simple_cli.py` | 冻结为 legacy v2 一条命令入口；v0.3.0 不改变默认算法和产物版本 |

### 4.3 迁移完成后删除的旧实现

只有在新模块、兼容测试和真实样本影子运行全部通过后，才删除已无引用的旧函数。预计收敛对象：

- `jitter_detector.py` 中仅支持方向反转的旧检测实现。
- `stable_frame_selector.py` 中未受全局覆盖约束的选择实现。
- `segment_detector.py` 中把相对低运动直接解释为静止的路径。
- 默认策略生成的 `duplicate_range`；v2 执行器仍可读取历史策略，但 v3 不生成该操作。

删除前必须运行 `Get-ChildItem -LiteralPath scripts,tests -Recurse -File -Filter '*.py' | Select-String -Pattern 'jitter_detector|stable_frame_selector|segment_detector'` 和 Ruff/F401 检查引用，并由用户确认删除列表。不得在同一提交中一边替换一边大范围清理无关文件。

## 5. 公共数据契约

### 5.1 策略和风险枚举

```python
from enum import Enum

class PolicyName(str, Enum):
    COVERAGE_FIRST = "coverage_first"
    BALANCED = "balanced"
    JITTER_REDUCTION = "jitter_reduction"

class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class ValidationSeverity(str, Enum):
    WARNING = "warning"
    ERROR = "error"
```

### 5.2 Agent 请求

```python
@dataclass(frozen=True)
class StrategyRequest:
    policy: PolicyName
    minimum_retention_ratio: float | None = None
    maximum_consecutive_drops: int | None = None
```

LLM 只能提交这组高层参数。量化阈值、RANSAC 参数、轨迹窗口和质量归一化阈值由预设和算法自适应计算，不能通过 Agent 公共入口任意注入。

### 5.3 分析结果

```python
@dataclass(frozen=True)
class FrameAnalysis:
    source_index: int
    output_index: int
    timestamp_sec: float
    sharpness: float
    brightness: float
    contrast: float
    dx: float
    dy: float
    rotation_deg: float
    scale: float
    motion_confidence: float
    jitter_score: float
    low_quality_candidate: bool

@dataclass(frozen=True)
class QualitySummary:
    sharpness_p10: float
    sharpness_median: float
    brightness_median: float
    contrast_median: float
    low_quality_count: int

@dataclass(frozen=True)
class TrajectorySummary:
    mean_confidence: float
    normalized_residual_p95: float
    rotation_residual_p95: float
    fallback_count: int

@dataclass(frozen=True)
class AnalysisRange:
    start: int
    end: int
    kind: str
    confidence: float
    reason: str

@dataclass(frozen=True)
class AnalysisResult:
    schema_version: int
    run_id: str
    input_digest: str
    frame_count: int
    fps: float
    width: int
    height: int
    motion_confidence: float
    quality_summary: QualitySummary
    trajectory_summary: TrajectorySummary
    frames: tuple[FrameAnalysis, ...]
    ranges: tuple[AnalysisRange, ...]
    warnings: tuple[str, ...]
```

`input_digest` 由排序后的源文件名、大小和 SHA-256 摘要构成。计划和执行必须引用同一个摘要，防止 Agent 分析后输入目录发生变化。

### 5.4 策略结果

```python
@dataclass(frozen=True)
class StrategyCandidate:
    schema_version: int
    strategy_id: str
    input_digest: str
    policy: PolicyName
    request: StrategyRequest
    selected_sources: tuple[int, ...]
    estimated_output_count: int
    retention_ratio: float
    maximum_consecutive_drops: int
    maximum_source_index_gap: int
    maximum_time_gap_seconds: float
    estimated_jitter_reduction: float
    estimated_quality_change: float
    confidence: float
    risk_level: RiskLevel
    reasons: tuple[str, ...]
```

### 5.5 验证结果

```python
@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: ValidationSeverity
    message: str
    source_range: tuple[int, int] | None = None

@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    strategy_id: str
    input_digest: str
    candidate_digest: str
    issues: tuple[ValidationIssue, ...]
```

`candidate_digest` 是候选规范化 JSON 的 SHA-256。`apply_validated_strategy()` 不信任 JSON 中的 `valid=True`，执行时必须重新调用验证器，并校验 `strategy_id/input_digest/candidate_digest` 与当前文件完全一致。验证 JSON 是审计证据，不是可绕过规则的授权令牌。

### 5.6 执行结果

```python
@dataclass(frozen=True)
class ExecutionResult:
    schema_version: int
    run_id: str
    strategy_id: str
    input_digest: str
    candidate_digest: str
    output_frame_count: int
    selected_sources: tuple[int, ...]
    output_manifest: str
    output_digest: str
```

`output_manifest` 必须是相对于本次 artifact root 的 POSIX 风格路径，不得包含用户绝对路径。`output_digest` 由有序输出文件名、大小和内容 SHA-256 计算，用于 `verify_output()` 检测执行后篡改。

## 6. 算法设计

### 6.1 相机运动估计

主路径使用 OpenCV 已有能力，不增加依赖：

1. 灰度缩放到固定分析宽度，不修改源图。
2. `goodFeaturesToTrack` 提取稳定角点。
3. `calcOpticalFlowPyrLK` 跟踪相邻帧。
4. 前后向光流误差过滤错误匹配。
5. `estimateAffinePartial2D(source_points, target_points, method=cv2.RANSAC)` 估计平移、旋转和尺度。
6. 记录内点率、重投影误差、特征数量和变换置信度。
7. 特征不足时降级到 `phaseCorrelate` 平移估计，并明确标记低置信度。
8. 两种方法均失败时保留帧，不产生删除建议。

每帧运动记录至少包含：

```text
dx, dy, rotation_deg, scale, magnitude_px,
feature_count, inlier_ratio, reprojection_error,
response, confidence, fallback_reason
```

内部运动类型使用以下精确字段：

```python
@dataclass(frozen=True)
class MotionConfig:
    analysis_width: int
    max_features: int
    forward_backward_error: float
    ransac_reprojection_threshold: float
    minimum_inlier_ratio: float
    smoothing_window_seconds: float

@dataclass(frozen=True)
class MotionSample:
    source_index: int
    output_index: int
    dx: float
    dy: float
    rotation_deg: float
    scale: float
    magnitude_px: float
    feature_count: int
    inlier_ratio: float
    reprojection_error: float
    response: float
    confidence: float
    fallback_reason: str | None
```

### 6.2 主动运动与抖动分离

不能再用“低运动等于静止”或“方向反转等于全部抖动”。采用离线轨迹分解：

- 将相邻仿射变换累积为全局轨迹。
- 使用鲁棒局部中位数消除孤立异常，再使用中心加权移动平均得到低频主动轨迹。
- 原轨迹减去低频轨迹得到高频残差。
- 使用图像对角线归一化平移残差，旋转残差单独归一化。
- 抖动分数同时考虑残差速度、残差加速度、方向反转和估计置信度。
- 连续低置信度帧不判定为抖动，而是生成 `review_required` 范围。

静止段必须同时满足：低频轨迹位移小、高频残差小、持续时间足够、特征置信度合格。持续慢速平移会有低残差但非零低频位移，因此不会被判定为静止。

### 6.3 质量模型

质量指标不使用跨视频固定绝对分数直接删除帧。每个视频内部计算：

- Laplacian 清晰度及其稳健分位数。
- 亮度、对比度和过曝/欠曝比例。
- 有效特征数量、RANSAC 内点率和重投影误差。
- 与相邻保留帧的运动基线。

质量分数只用于同一局部时间窗口内排序。低质量帧只有在存在覆盖位置相近、运动估计可靠且质量更高的替代帧时才可删除。

### 6.4 三种策略预设

| 策略 | 默认最低保留率 | 默认最多连续丢弃输入帧 | 行为 |
|---|---:|---:|---|
| `coverage_first` | 0.85 | 2 | 只删除高置信度抖动/模糊冗余帧，v3 Agent-safe CLI 默认使用 |
| `balanced` | 0.65 | 4 | 在覆盖保护下减少冗余与明显抖动 |
| `jitter_reduction` | 0.45 | 7 | 更积极选择稳定关键帧，必须输出中风险或高风险提示 |

这些是安全边界，不是承诺达到某个视觉质量。真实样本回归结果可以提高保留率，不能低于对应边界。用户显式指定的边界必须比预设更保守或经过验证器批准。

### 6.5 选择约束

任何候选策略都必须满足：

- 保留第一帧和最后一帧。
- 不产生重复源帧。
- 不生成不存在的源编号。
- 输出按源时间顺序排列。
- 保留率不低于策略边界。
- 连续丢弃的当前输入帧数量不超过策略边界。源编号可能因上游采样天然不连续，因此只作为报告指标，不直接作为通用硬阈值。
- 低置信度范围默认全保留。
- 主动转向、快速平移和场景切换不作为抖动删除。
- 候选方案无法满足约束时回退到原样保留，并返回错误码 `unsafe_strategy_fallback`。

## 7. Agent-safe API 和 CLI

### 7.1 Python API

包根公开以下稳定入口：

```python
from frame_timing_agent import (
    AnalysisResult,
    PolicyName,
    StrategyCandidate,
    StrategyRequest,
    ValidationResult,
    analyze_frames,
    apply_validated_strategy,
    capabilities,
    plan_strategy,
    validate_strategy,
    verify_output,
)
```

`__init__.py` 只导出以上契约，不导出 OpenCV 或内部算法函数。

这些名称只代表 v3 Agent-safe API。legacy `run_timing_agent()`、`run_batch_timing_agent()` 和 v2 `apply_strategy()` 继续从原模块导入，不提升为新的包根稳定接口，也不与 v3 函数共用名称。

### 7.2 JSON CLI

新增命令：

```text
frame-timing-tool capabilities
frame-timing-tool analyze --frames path/to/clean_frames --artifact-root output/frame_timing_run
frame-timing-tool plan --analysis output/frame_timing_run/analysis.json --policy coverage_first
frame-timing-tool validate --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json
frame-timing-tool apply --analysis output/frame_timing_run/analysis.json --strategy output/frame_timing_run/strategy.json --validation output/frame_timing_run/validation.json --output-dir output/frame_timing_run/output_frames
frame-timing-tool verify --artifact-root output/frame_timing_run
```

规则：

- 标准输出只打印一个 UTF-8 JSON 对象。
- 人类日志写入标准错误。
- 成功退出码为 0，输入错误为 2，策略不安全为 3，执行失败为 4，健康检查失败为 5。
- JSON 必须包含 `schema_version`、`status`、`run_id` 和 `artifacts`。
- 不在 JSON 中输出用户绝对路径；使用输入目录名和产物相对路径。

### 7.3 Skill 指令

`SKILL.md` 修改为：

- 默认先执行 `analyze`，不得直接 apply。
- 默认选择 `coverage_first`。
- LLM 可以在读取候选指标后选择三种预设。
- 中风险和高风险候选必须请求人工确认。
- 验证失败时禁止通过手工编辑 JSON 绕过；apply 阶段会重新验证规范化候选摘要。
- 输出必须通过 `verify` 后才能交给建模工具。

## 8. 分阶段实施计划

### Task 1: 建立干净基线和质量工具

**Files:**
- Modify: `pyproject.toml`
- Modify: `.github/workflows/ci.yml`
- Create: `tests/test_text_quality.py`
- Modify: `references/usage.md`
- Modify: `scripts/frame_timing_agent/batch_artifact_health.py`
- Modify: `scripts/frame_timing_agent/human_review.py`

- [x] **Step 1: 创建隔离开发 worktree**

```powershell
$repo = (git rev-parse --show-toplevel)
$target = Join-Path (Split-Path $repo -Parent) "frame-timing-agent-readiness"
git status --short --branch
git worktree add $target -b codex/frame-timing-agent-readiness main
```

预期：主工作区内容不变，新 worktree 位于独立分支。

- [x] **Step 2: 写失败测试，扫描乱码和错误公共文档字段**

测试以 UTF-8 严格模式读取 `scripts/frame_timing_agent/*.py`、`README*.md`、`SKILL.md` 和 `references/*.md`，拒绝 Unicode 替换字符和典型错误转码片段，并断言文档不再引用 `result.batch_report`。

- [x] **Step 3: 运行失败测试**

```powershell
python -m pytest tests/test_text_quality.py -v
```

预期：当前 `result.batch_report` 文档引用导致失败；UTF-8 检查作为跨平台回归保护。

- [x] **Step 4: 修复 UTF-8 文本和文档字段**

只修复被测试证明存在的问题，不改变算法；Python API 文档改为实际存在的 `summary_json_path`、`summary_csv_path`、`review_dashboard_path` 和 `items`。PowerShell 文档中的中文读取示例显式使用 `-Encoding utf8`。

- [x] **Step 5: 增加开发依赖和工具配置**

在 `pyproject.toml` 增加 `dev` 可选依赖和 Ruff 基础规则。经基线扫描，完整规则会产生 363 个既有格式/风格问题，mypy 会产生 7 个既有类型问题；为避免本任务形成大面积无关改动，首阶段只对全仓库启用 `E4/E7/E9/F` 正确性规则。Task 2 起，每个新增模块单独通过完整 Ruff 和 mypy；Task 11 在旧模块迁移完成后再对全仓库启用完整规则。

- [x] **Step 6: 修复基础门禁发现的两个真实问题并核对缓存**

删除 `batch_artifact_health.py` 中未使用的 `os` 导入和 `human_review.py` 中未使用的 `json` 导入。运行 `git ls-files | Select-String '__pycache__|\.pytest_cache|\.tmp_tests'`，确认可再生成缓存未被 Git 跟踪；本任务不删除测试刚生成的本地缓存，因为它们已被 `.gitignore` 正确隔离。

- [x] **Step 7: 完整验证并提交**

```powershell
python -m ruff check --select E4,E7,E9,F scripts tests
python -m ruff format --check tests/test_text_quality.py
python -m pytest -q
python -m compileall -q scripts tests
git add pyproject.toml .github/workflows/ci.yml tests/test_text_quality.py references/usage.md scripts/frame_timing_agent/batch_artifact_health.py scripts/frame_timing_agent/human_review.py
git commit -m "chore: establish agent readiness quality gates"
```

### Task 2: 建立公开契约和严格配置

**Files:**
- Create: `scripts/frame_timing_agent/contracts.py`
- Create: `scripts/frame_timing_agent/configuration.py`
- Create: `tests/test_contracts.py`
- Create: `tests/test_configuration.py`
- Modify: `scripts/frame_timing_agent/__init__.py`

- [x] **Step 1: 为枚举、JSON 往返和不可变性写失败测试**

覆盖三种 `PolicyName`、稳定 `schema_version=3`、未知字段拒绝、缺失必填字段、非有限浮点数和序列化顺序。

- [x] **Step 2: 为配置边界和交叉约束写失败测试**

至少覆盖：保留率不在 `(0, 1]`、连续丢帧上限小于 0、尝试提交 `preserve_endpoints` 或 `allow_low_confidence_removal` 等禁止字段、未知策略名称和宽松参数超过预设安全边界。

- [x] **Step 3: 运行测试确认失败**

```powershell
python -m pytest tests/test_contracts.py tests/test_configuration.py -v
```

- [x] **Step 4: 实现最小契约和严格解析器**

解析器必须显式比较输入键集合，错误信息包含字段名和允许范围；不得使用 `dict.update()` 静默接收未知参数。

- [x] **Step 5: 从包根导出稳定 API 类型**

`__all__` 只包含公开契约和后续 service 函数，内部模块保持私有实现状态。

- [x] **Step 6: 验证并提交**

```powershell
python -m pytest tests/test_contracts.py tests/test_configuration.py -v
python -m mypy scripts/frame_timing_agent/contracts.py scripts/frame_timing_agent/configuration.py
git add scripts/frame_timing_agent/contracts.py scripts/frame_timing_agent/configuration.py scripts/frame_timing_agent/__init__.py tests/test_contracts.py tests/test_configuration.py
git commit -m "feat: add strict agent-facing contracts"
```

### Task 3: 实现可靠的相机运动与分析模型

**Files:**
- Create: `scripts/frame_timing_agent/motion_model.py`
- Create: `scripts/frame_timing_agent/analysis.py`
- Create: `tests/test_motion_model.py`
- Create: `tests/test_analysis.py`
- Create: `tests/fixtures/generate_motion_sequences.py`
- Modify: `scripts/frame_timing_agent/contracts.py`
- Modify: `tests/test_contracts.py`

Task 3 只负责图像运动估计、轨迹分解和无策略的分析结果组装。它不得读取 `PolicyName`、`ResolvedStrategyConfig`、保留率、连续丢帧限制或 legacy override，也不得选择或删除输出帧。

- [ ] **Step 1: 生成确定性合成序列**

固定随机种子，程序化生成：静止、匀速平移、平移叠加振荡、旋转振荡、主动快速转向、低纹理和模糊突发序列。测试运行时生成到 pytest 临时目录，不提交图片产物。

- [ ] **Step 2: 为仿射估计写失败测试**

断言已知平移误差不超过 0.5 像素、已知旋转误差不超过 0.2 度；低纹理序列必须进入明确的 fallback，不能伪造高置信度。

- [ ] **Step 3: 实现特征、LK 光流和 RANSAC 估计**

实现纯函数接口：

精确公开签名为 `estimate_camera_motion(records: Sequence[FrameRecord], config: MotionConfig) -> tuple[MotionSample, ...]`。

函数不写文件，不依赖全局配置，不修改输入图像。

- [ ] **Step 4: 为轨迹分解写失败测试**

匀速平移的高频残差必须接近零；平移叠加振荡必须检测到振荡区间；主动快速转向不能被判为抖动。

- [ ] **Step 5: 实现轨迹累积、鲁棒平滑和置信度**

所有窗口长度根据 fps 和帧数计算并限制为奇数；短序列走明确分支，不允许空切片或除零。

- [ ] **Step 6: 为无策略分析组合写失败测试**

在 `contracts.py` 增加 5.3 节定义的冻结分析契约。测试 `analyze_records(records: Sequence[FrameRecord], fps: float, motion_config: MotionConfig) -> AnalysisResult` 将现有清晰度、亮度、对比度指标和 `MotionSample` 一一按 `source_index` 合并；输入顺序不稳定时输出必须按源编号排序，重复源编号、尺寸不一致、非法 fps 和运动结果缺失必须返回稳定错误，且不得创建策略或输出帧。

- [ ] **Step 7: 实现纯分析组合层**

`analysis.py` 只调用 `timing_metrics.py` 和 `motion_model.py` 并构造 `AnalysisResult`，不读取 `PolicyName`、`ResolvedStrategyConfig` 或 legacy override，不写文件。路径加载、JSON 落盘和 artifact root 校验留给 Task 6 的 service 边界。

- [ ] **Step 8: 验证并提交**

```powershell
python -m pytest tests/test_motion_model.py tests/test_analysis.py tests/test_contracts.py -v
python -m ruff check scripts/frame_timing_agent/motion_model.py scripts/frame_timing_agent/analysis.py tests/test_motion_model.py tests/test_analysis.py tests/fixtures/generate_motion_sequences.py
python -m mypy scripts/frame_timing_agent/motion_model.py scripts/frame_timing_agent/analysis.py scripts/frame_timing_agent/contracts.py
git add scripts/frame_timing_agent/motion_model.py scripts/frame_timing_agent/analysis.py scripts/frame_timing_agent/contracts.py tests/test_motion_model.py tests/test_analysis.py tests/test_contracts.py tests/fixtures/generate_motion_sequences.py
git commit -m "feat: estimate confidence-aware camera trajectories"
```

### Task 4: 实现候选策略规划器

**Files:**
- Create: `scripts/frame_timing_agent/strategy_planner.py`
- Create: `tests/test_strategy_planner.py`
- Modify: `scripts/frame_timing_agent/contracts.py`
- Modify: `tests/test_contracts.py`

- [ ] **Step 1: 为三种预设写失败测试**

对同一分析结果分别传入三种已解析的 `ResolvedStrategyConfig`；`coverage_first` 保留数不少于 `balanced`，`balanced` 不少于 `jitter_reduction`；候选必须携带指标、原因、风险和置信度。

- [ ] **Step 2: 为慢速平移回归写失败测试**

构造 570 帧持续慢速运动，断言不会被压缩成 40 帧，也不会生成 `static` 操作。

- [ ] **Step 3: 为建模覆盖写失败测试**

断言首尾帧保留、无重复、顺序递增、保留率不低于 `minimum_retention_ratio`、连续丢帧不超过 `maximum_consecutive_drops`；局部低质量帧没有相近替代帧时必须保留。另用天然稀疏源编号输入证明规划器不会把上游采样间隔误判为本阶段连续删帧。

- [ ] **Step 4: 实现局部候选选择和评分**

精确公开签名为 `plan_strategy(analysis: AnalysisResult, config: ResolvedStrategyConfig) -> StrategyCandidate`。规划器直接消费类型化安全约束，不把它们翻译成 legacy engine config dict。它只返回显式 `selected_sources`，v3 不生成 `duplicate_range`。评分由覆盖、残余抖动、质量和置信度组成，并在输出中分别报告，不使用一个无法解释的总分替代各指标。

- [ ] **Step 5: 验证并提交**

```powershell
python -m pytest tests/test_strategy_planner.py tests/test_contracts.py -v
python -m ruff check scripts/frame_timing_agent/strategy_planner.py scripts/frame_timing_agent/contracts.py tests/test_strategy_planner.py
git add scripts/frame_timing_agent/strategy_planner.py scripts/frame_timing_agent/contracts.py tests/test_strategy_planner.py tests/test_contracts.py
git commit -m "feat: plan reconstruction-safe frame candidates"
```

### Task 5: 实现不可绕过的策略验证器

**Files:**
- Create: `scripts/frame_timing_agent/strategy_validator.py`
- Create: `tests/test_strategy_validator.py`
- Modify: `scripts/frame_timing_agent/contracts.py`
- Modify: `scripts/frame_timing_agent/apply_frame_strategy.py`
- Modify: `tests/test_contracts.py`

- [ ] **Step 1: 为每条硬约束写失败测试**

分别测试摘要不匹配、来源不存在、重复来源、乱序、保留率不足、连续丢帧超限、端点缺失、低置信度删除和输出目录越界。

- [ ] **Step 2: 实现统一验证器**

精确公开签名为 `validate_strategy(analysis: AnalysisResult, candidate: StrategyCandidate, config: ResolvedStrategyConfig) -> ValidationResult`。验证器必须独立重算保留率和最大连续丢帧数，不能只信任规划器写入候选的汇总字段。

错误必须有稳定机器码；警告不能替代错误。验证器不得自动篡改候选，规划器需要根据问题重新生成方案。

- [ ] **Step 3: 增加独立的已验证执行入口**

新增精确公开签名 `apply_validated_strategy(analysis: AnalysisResult, candidate: StrategyCandidate, validation: ValidationResult, output_dir: Path) -> ExecutionResult`。执行前重新计算输入摘要，验证 `strategy_id`，并拒绝不属于该候选或含错误项的验证结果。v3 service 只能调用该函数。

现有接收 v2 strategy dict 的 `apply_strategy()` 保持名称、签名和行为，供 legacy facade 与历史策略使用；不得用 `if validation is None` 之类的可选参数把 v2/v3 合并成一个函数。弃用和移除 legacy 执行入口留给完成真实迁移后的主版本。

- [ ] **Step 4: 验证并提交**

```powershell
python -m pytest tests/test_strategy_validator.py tests/test_apply_frame_strategy.py tests/test_contracts.py -v
python -m mypy scripts/frame_timing_agent/contracts.py scripts/frame_timing_agent/strategy_validator.py scripts/frame_timing_agent/apply_frame_strategy.py
git add scripts/frame_timing_agent/contracts.py scripts/frame_timing_agent/strategy_validator.py scripts/frame_timing_agent/apply_frame_strategy.py tests/test_strategy_validator.py tests/test_apply_frame_strategy.py tests/test_contracts.py
git commit -m "feat: enforce strategy safety before execution"
```

### Task 6: 建立五阶段服务 API

**Files:**
- Create: `scripts/frame_timing_agent/service.py`
- Create: `tests/test_service.py`
- Modify: `scripts/frame_timing_agent/__init__.py`
- Modify: `tests/test_auto_timing_agent.py`
- Modify: `tests/test_batch_timing_agent.py`
- Modify: `tests/test_simple_cli.py`

- [ ] **Step 1: 为 analyze/plan/validate/apply/verify 生命周期写失败测试**

测试新服务入口执行 `StrategyRequest → resolve_strategy_request → analyze → plan → validate → apply_validated_strategy → verify`；分析阶段不创建 `output_frames`，验证失败不能执行，输入变化后执行失败，成功执行后健康检查通过，重复执行结果可复现。Python service 只接收已经构造并自校验的 `StrategyRequest`，不得接受 `override_config_path` 或任意底层参数 dict；JSON 到 `StrategyRequest` 的严格解析属于 Task 7 CLI 适配层。

- [ ] **Step 2: 实现无状态 service 函数**

每个阶段只依赖显式输入和文件产物；不使用模块级可变状态。Agent-safe 服务通过 `resolve_strategy_request()` 将已校验的 `StrategyRequest` 解析为 `ResolvedStrategyConfig`，随后在规划器和验证器中传递该类型。分析、计划和验证 JSON 使用原子写入：先写同目录临时文件，再 `replace()`。`service.py` 不导入 `auto_timing_agent.py`、`batch_timing_agent.py` 或 `simple_cli.py`。

- [ ] **Step 3: 锁定 v2/v3 隔离边界**

不修改 `auto_timing_agent.py`、`batch_timing_agent.py` 或 `simple_cli.py` 的生产代码。兼容测试锁定以下事实：旧函数签名继续接受 `override_config_path`；无 override 和有 override 的调用都生成 v2 strategy；现有一条命令入口继续生成 v2 产物；legacy 入口不暴露 `PolicyName` 或 `StrategyRequest`。新 service 的公开签名不接受 legacy `mode`、`override_config_path` 或 raw dict。

不得根据 `override_config_path is None`、环境变量、产物目录是否存在或调用来源来选择引擎。未来迁移旧 facade 时必须新增显式版本计划、行为对比和弃用周期，不能在本任务内顺带完成。

- [ ] **Step 4: 验证并提交**

```powershell
python -m pytest tests/test_service.py tests/test_auto_timing_agent.py tests/test_batch_timing_agent.py tests/test_simple_cli.py -v
python -m mypy scripts/frame_timing_agent/service.py
git add scripts/frame_timing_agent/service.py scripts/frame_timing_agent/__init__.py tests/test_service.py tests/test_auto_timing_agent.py tests/test_batch_timing_agent.py tests/test_simple_cli.py
git commit -m "feat: expose staged frame timing service"
```

### Task 7: 增加 Agent JSON CLI

**Files:**
- Create: `scripts/frame_timing_agent/tool_cli.py`
- Create: `tests/test_tool_cli.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: 为六个子命令和退出码写失败测试**

通过 `main(argv)` 测试 JSON Schema、UTF-8、标准输出单对象、日志进入标准错误以及退出码 0/2/3/4/5。

- [ ] **Step 2: 实现薄 CLI 适配层**

CLI 只解析参数和 JSON（使用 `parse_strategy_request()`）、调用 service、序列化结果和映射退出码。不得复制配置约束、分析、规划或验证逻辑。

- [ ] **Step 3: 注册命令**

```toml
[project.scripts]
frame-timing-tool = "frame_timing_agent.tool_cli:main"
```

- [ ] **Step 4: 安装到隔离目录进行黑盒测试**

```powershell
python -m build
$wheel = (Get-ChildItem -LiteralPath dist -Filter '*.whl' | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
python -m pip install --force-reinstall $wheel
frame-timing-tool capabilities
```

预期：输出合法 JSON，包含三个策略和安全边界。

- [ ] **Step 5: 验证并提交**

```powershell
python -m pytest tests/test_tool_cli.py tests/test_simple_cli.py -v
git add scripts/frame_timing_agent/tool_cli.py tests/test_tool_cli.py pyproject.toml
git commit -m "feat: add agent-safe json tool interface"
```

### Task 8: 统一审计、健康检查和报告

**Files:**
- Modify: `scripts/frame_timing_agent/strategy_execution_audit.py`
- Modify: `scripts/frame_timing_agent/batch_artifact_health.py`
- Modify: `scripts/frame_timing_agent/timing_report.py`
- Modify: `scripts/frame_timing_agent/human_review.py`
- Create: `tests/test_agent_artifact_contract.py`

- [ ] **Step 1: 写失败测试覆盖 v3 产物契约**

要求 analysis、candidate、validation、execution 和 health 五类 JSON 均存在；报告不得包含绝对输入路径；输出文件哈希必须与来源摘要一致。

- [ ] **Step 2: 调用统一验证规则**

健康检查不得另写一套保留率和间隔逻辑。它必须读取执行时保存的 `ValidationResult` 并重新检查实际输出。

- [ ] **Step 3: 报告候选对比和风险**

人类报告明确显示输入数、输出数、保留率、最大连续丢帧数、最大源编号间隔、最大时间间隔、残余抖动估计、置信度、风险等级、回退原因和待人工审查区间。

- [ ] **Step 4: 验证并提交**

```powershell
python -m pytest tests/test_agent_artifact_contract.py tests/test_strategy_execution_audit.py tests/test_batch_artifact_health.py tests/test_timing_report.py tests/test_human_review.py -v
git add scripts/frame_timing_agent/strategy_execution_audit.py scripts/frame_timing_agent/batch_artifact_health.py scripts/frame_timing_agent/timing_report.py scripts/frame_timing_agent/human_review.py tests/test_agent_artifact_contract.py
git commit -m "feat: audit agent strategy lifecycle"
```

### Task 9: 真实样本影子验证

**Files:**
- Create: `benchmarks/case_schema.json`
- Create: `benchmarks/README.md`
- Create: `scripts/frame_timing_agent/benchmark_cli.py`
- Create: `tests/test_benchmark_cli.py`
- Modify: `.gitignore`

- [ ] **Step 1: 定义不包含私有路径的 benchmark 结果格式**

记录案例 ID、输入摘要、帧数、策略、输出数、保留率、最大连续丢帧数、最大时间间隔、抖动估计、人工结论和版本；不提交原始图片。

- [ ] **Step 2: 对已知 580/570 帧案例运行三种策略**

使用本地外部数据运行，不覆盖旧目录：

```powershell
if (-not $env:FRAME_TIMING_BENCHMARK_FRAMES) { throw "FRAME_TIMING_BENCHMARK_FRAMES is required" }
frame-timing-tool analyze --frames $env:FRAME_TIMING_BENCHMARK_FRAMES --artifact-root "output/benchmark/test3_cut2"
frame-timing-tool plan --analysis "output/benchmark/test3_cut2/analysis.json" --policy coverage_first
frame-timing-tool validate --analysis "output/benchmark/test3_cut2/analysis.json" --strategy "output/benchmark/test3_cut2/strategy.json"
```

验收：0-136 和 424-579 不得判为静止；任何删帧都必须来自高置信度抖动/质量替代判断，并满足覆盖约束。

- [ ] **Step 3: 人工复核至少五类真实片段**

案例必须覆盖慢速平移、明显手持抖动、快速主动转向、低纹理场景和模糊突发。每类记录“正确检测、误检、漏检、建模覆盖风险”。

- [ ] **Step 4: 设置发布门槛**

已确认的慢速运动误判静止数必须为 0；验证器违规漏过数必须为 0；所有高风险策略必须要求人工确认。算法效果不满足门槛时不发布 v0.3.0，也不得修改期望结果来迁就实现。

通过这些门槛只允许发布独立的 v3 Agent-safe API/CLI，不自动授权迁移 legacy facade 或 `frame-timing` 默认入口。旧入口迁移需基于本次 benchmark 结果另立计划和版本。

- [ ] **Step 5: 提交 benchmark 工具和格式，不提交私有数据**

```powershell
git add benchmarks scripts/frame_timing_agent/benchmark_cli.py tests/test_benchmark_cli.py .gitignore
git commit -m "test: add external frame timing benchmark protocol"
```

### Task 10: 文档、Skill 和迁移说明

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `SKILL.md`
- Modify: `references/usage.md`
- Modify: `references/artifact_contract.md`
- Create: `references/agent-integration.md`
- Create: `references/migration-v2-to-v3.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 写文档一致性测试**

命令、策略名、版本号、公开导入和产物文件名必须来自真实代码；测试 CLI `--help` 中出现文档主命令。

- [ ] **Step 2: 更新用户文档**

README 保持两类用户：普通用户的一条默认命令明确标记为 legacy v2 兼容入口；Agent 和开发者使用独立的 v3 Python API 与 JSON CLI。明确本工具做帧选择而非像素修复，也不得把 v2 一条命令描述成 Agent-safe v3。

- [ ] **Step 3: 更新 Skill 工作流**

写明 analyze-first、候选比较、风险确认、validate-before-apply 和 verify-before-downstream，不限制特定 Agent 产品。

- [ ] **Step 4: 编写迁移文档**

说明 v2 兼容范围、v3 不再生成重复帧、旧 override 不映射到 v3 且只能由 legacy 入口读取、Agent 应如何固定包版本和如何回滚。文档必须明确：v0.3.0 不迁移旧 facade；未来迁移需要显式选择新入口、弃用周期和真实样本对比，不允许按 override 是否存在隐式路由。

- [ ] **Step 5: 验证并提交**

```powershell
python -m pytest tests/test_text_quality.py tests/test_package_metadata.py tests/test_tool_cli.py -v
git add README.md README.zh-CN.md SKILL.md references CHANGELOG.md
git commit -m "docs: document agent-safe frame timing workflow"
```

### Task 11: CI、代码审查和发布门禁

**Files:**
- Modify: `.github/workflows/ci.yml`
- Create: `.github/workflows/security.yml`
- Create: `.github/dependabot.yml`
- Modify: `pyproject.toml`

- [ ] **Step 1: 扩展 CI 矩阵**

Windows 和 Ubuntu 分别运行 Python 3.10、3.12；lint/type/test 只运行一次完整矩阵中的指定组合，包安装和 CLI smoke 两个平台都运行。

- [ ] **Step 2: 增加发布质量命令**

```powershell
python -m ruff check scripts tests
python -m ruff format --check scripts tests
python -m mypy scripts/frame_timing_agent
python -m pytest --cov=frame_timing_agent --cov-report=term-missing --cov-fail-under=90
python -m build
python -m twine check dist\*
python -m pip_audit
```

- [ ] **Step 3: 独立代码审查**

每完成 Tasks 3、5、7、9 后进行一次独立审查，审查输入包含基线 SHA、当前 SHA、本计划对应任务和测试输出。Critical 和 Important 问题必须修复后再进入下一阶段。

- [ ] **Step 4: 最终 diff 审查**

```powershell
git diff --check main...HEAD
git diff --stat main...HEAD
git log --oneline main..HEAD
```

逐文件确认没有私有路径、运行产物、缓存、大图、无关重构、被弱化的断言或未解释依赖。

- [ ] **Step 5: 最终验证并提交**

```powershell
python -m ruff check scripts tests
python -m ruff format --check scripts tests
python -m mypy scripts/frame_timing_agent
python -m pytest --cov=frame_timing_agent --cov-report=term-missing --cov-fail-under=90
python -m compileall -q scripts tests
python -m build
python -m twine check dist\*
python -m pip_audit
git add .github pyproject.toml
git commit -m "ci: enforce frame timing release gates"
```

### Task 12: 发布 v0.3.0

**Files:**
- Modify: `pyproject.toml`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: 确认主分支和远端状态**

```powershell
git fetch origin
git status --short --branch
git log --oneline --decorate -5
```

预期：工作区干净，开发分支只包含本计划范围内提交。

- [ ] **Step 2: 合并前执行完整发布验证**

重复 Task 11 的全部命令，并保存真实样本 benchmark 摘要。任何命令失败都停止发布。

- [ ] **Step 3: 合并并推送主分支**

通过 PR 或经用户批准的本地快进/挑选提交方式合并。不得使用 `git reset --hard` 或强推覆盖远端历史。

- [ ] **Step 4: 创建带注释标签**

```powershell
git tag -a v0.3.0 -m "Frame Timing Skill v0.3.0"
git push origin main
git push origin v0.3.0
```

- [ ] **Step 5: GitHub Release 内容**

Release 必须说明 Agent-safe 五阶段接口、三种策略、运动模型升级、覆盖保护、v2 兼容范围、两条入口不会自动互相迁移、已知限制和验证命令。不得宣称实现像素去抖、去模糊或保证建模质量。

## 9. 测试矩阵

| 层级 | 必测内容 |
|---|---|
| 单元 | 配置边界、契约序列化、轨迹估计、规划评分、每条验证规则 |
| 合成算法 | 静止、慢速平移、振荡、旋转、主动转向、低纹理、模糊 |
| 集成 | analyze-plan-validate-apply-verify 完整生命周期 |
| CLI | JSON、退出码、stderr、安装后命令、跨平台路径 |
| 兼容 | v2 策略读取、旧 Python facade、原一条命令入口均保持 v2；v3 入口不接受 override |
| 安全 | 路径越界、输入变化、恶意 JSON、未知字段、非有限数值 |
| 产物 | 哈希、数量、间隔、相对路径、无分析文件混入输出 |
| 真实回归 | 五类人工标注片段和已知 570 帧案例 |

所有随机测试固定种子。测试不得依赖机器当前时间、文件枚举自然顺序、用户目录或网络。

## 10. 代码审查清单

### 正确性

- 慢速主动运动是否可能再次落入静止规则。
- 抖动残差是否经过图像尺寸和 fps 归一化。
- 低特征、短序列、缺帧和不同尺寸是否有明确行为。
- 策略估计数量是否与实际执行数量一致。
- 输入摘要变化是否阻止执行。

### 建模风险

- 是否保留首尾帧和视角连续性。
- 是否存在大于策略限制的源帧间隔。
- 是否把快速主动运动误当高频抖动。
- 是否生成重复帧或修改像素。
- 低置信度范围是否默认保留。

### 工程质量

- 公共 API 是否有完整类型和稳定错误码。
- 文件是否职责单一，是否出现新的循环依赖。
- CLI 是否只是适配层。
- 是否引入无必要依赖、全局状态或隐藏 I/O。
- 报告、README、Skill 和代码是否一致。

### 安全与隐私

- 日志和 JSON 是否泄漏绝对路径。
- 输出目录是否可逃逸允许的 `output/` 根目录。
- 临时文件是否原子替换并可清理。
- 报错是否包含凭据、环境变量或私有内容。

## 11. 禁止的捷径

- 不通过降低断言、删除失败测试或扩大误差容忍来制造通过。
- 不针对 `test3_cut2` 文件名、路径、帧数或具体区间硬编码。
- 不把“输出帧更少”当作算法更好的证明。
- 不使用 LLM 判断替代可重复的数值验证器。
- 不在执行阶段静默修正危险策略；必须返回结构化错误并重新规划。
- 不吞掉异常后返回 `status=ok`。
- 不在没有替代帧和覆盖证据时删除模糊帧。
- 不把人工标注结果直接复制成算法期望输出。
- 不在同一提交混入目录重构、算法改写和文档清理。
- 不发布未通过真实样本影子验证的版本。

## 12. 回滚策略

- 每个阶段独立提交，主分支合并前可逐任务审查。
- v0.3.0 同时提供相互隔离的两条路径：新增 Agent-safe API/CLI 只生成 v3；旧 Python facade、批处理和一条命令入口继续生成 v2。
- 不存在跨项目统一的“默认版本”：调用哪个显式入口就使用哪个契约，禁止根据可选参数隐式选择引擎。
- Agent 项目固定依赖精确版本；出现回归时回退到上一已验证版本。
- 每次运行使用独立 `output/<run_id>`，不得覆盖输入或旧结果。
- 策略、输入摘要、验证结果和输出审计一起保存，支持复现。
- 若新运动模型置信度不足，回退行为是完整保留并请求复核，不回退到旧的低运动静止压缩。

## 13. 完成定义

只有同时满足以下条件，才可以认为 Agent-ready 工作完成：

- 五阶段 v3 Python API 和 JSON CLI 均可从安装后的 wheel 调用，且与 legacy v2 入口命名和依赖隔离。
- LLM 无法通过公共接口提交越界参数或绕过验证。
- 三种策略都有稳定契约、解释、风险和覆盖约束。
- 慢速平移回归案例不再被静止压缩。
- 抖动序列能检测，主动转向序列不被误删。
- 所有输出帧保持源文件字节一致。
- 测试、覆盖率、Ruff、mypy、构建、twine 和依赖审计通过。
- Windows/Ubuntu 和 Python 3.10/3.12 CI 通过。
- 五类真实样本完成人工影子复核。
- README、中文 README、Skill、API 文档、迁移说明和 Changelog 一致。
- 独立代码审查无未解决的 Critical 或 Important 问题。
