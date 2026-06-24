from __future__ import annotations

from pathlib import Path
import csv
import json

from frame_timing_agent.segment_detector import Segment
from frame_timing_agent.timing_metrics import FrameMetric


def write_analysis_artifacts(
    analysis_dir: Path | str,
    metrics: list[FrameMetric],
    segments: list[Segment],
    strategy: dict,
    preview_only: bool,
    timestamp_source: str,
    detection_config: dict | None = None,
    review_ranges: list[dict] | None = None,
) -> None:
    analysis_dir = Path(analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    _write_frame_metrics_csv(analysis_dir / "frame_metrics.csv", metrics)
    _write_segments_json(analysis_dir / "segments.json", segments)
    (analysis_dir / "strategy.json").write_text(
        json.dumps(strategy, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _write_report_md(
        analysis_dir / "report.md",
        metrics,
        segments,
        strategy,
        preview_only,
        timestamp_source,
        detection_config or {},
        review_ranges or [],
    )
    _write_engineering_log_md(
        analysis_dir / "engineering_log.md",
        metrics,
        segments,
        strategy,
        preview_only,
        timestamp_source,
    )


def _write_frame_metrics_csv(path: Path, metrics: list[FrameMetric]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "source_index",
                "output_index",
                "timestamp_sec",
                "sharpness",
                "brightness",
                "contrast",
                "motion_score",
                "similarity_score",
                "bad_quality_candidate",
            ]
        )
        for metric in metrics:
            writer.writerow(
                [
                    metric.source_index,
                    metric.output_index,
                    "" if metric.timestamp_sec is None else f"{metric.timestamp_sec:.6f}",
                    f"{metric.sharpness:.6f}",
                    f"{metric.brightness:.6f}",
                    f"{metric.contrast:.6f}",
                    f"{metric.motion_score:.6f}",
                    f"{metric.similarity_score:.6f}",
                    int(metric.bad_quality_candidate),
                ]
            )


def _write_segments_json(path: Path, segments: list[Segment]) -> None:
    path.write_text(
        json.dumps([segment.__dict__ for segment in segments], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _write_report_md(
    path: Path,
    metrics: list[FrameMetric],
    segments: list[Segment],
    strategy: dict,
    preview_only: bool,
    timestamp_source: str,
    detection_config: dict,
    review_ranges: list[dict],
) -> None:
    operation_count = len(strategy.get("operations", []))
    estimated_output = _estimate_output_count(metrics, strategy)
    thresholds = _motion_thresholds(metrics, detection_config)
    lines = [
        "# Video Timing Agent Report",
        "",
        f"Analyzed: {len(metrics)} frames",
        f"Estimated output: {estimated_output} frames",
        f"Detected segments: {len(segments)}",
        f"Generated operations: {operation_count}",
        "All strategy ranges use source_index.",
        f"Timestamp source: {timestamp_source}",
        f"Preview only: {preview_only}",
        "Engineering log: engineering_log.md",
        "",
        "## Segments",
    ]
    if segments:
        for segment in segments:
            lines.append(
                f"- {segment.segment_type}: source {segment.start}-{segment.end}, "
                f"frames={segment.frame_count}, mean_motion={segment.mean_motion:.6f}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Detection Thresholds"])
    if thresholds:
        lines.extend(
            [
                f"- static_motion_quantile={thresholds['static_quantile']}: threshold={thresholds['static_threshold']:.6f}",
                f"- fast_motion_quantile={thresholds['fast_quantile']}: threshold={thresholds['fast_threshold']:.6f}",
                f"- very_fast_motion_quantile={thresholds['very_fast_quantile']}: threshold={thresholds['very_fast_threshold']:.6f}",
            ]
        )
    else:
        lines.append("- unavailable: no frame metrics")

    lines.extend(["", "## Operations"])
    operations = strategy.get("operations", [])
    if operations:
        for operation in operations:
            source_range = operation.get("range", {})
            lines.append(
                f"- {operation.get('op')}: source {source_range.get('start')}-{source_range.get('end')}, "
                f"source={operation.get('source', 'unknown')}, reason={operation.get('reason', '')}"
            )
    else:
        lines.append("- none")

    manual_operations = [operation for operation in operations if operation.get("source") == "manual_override"]
    lines.extend(["", "## Manual Overrides"])
    if manual_operations:
        for operation in manual_operations:
            source_range = operation["range"]
            lines.append(
                f"- {operation['op']}: source {source_range['start']}-{source_range['end']}, "
                f"reason={operation.get('reason', '')}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Review Ranges"])
    if review_ranges:
        for item in review_ranges:
            summary = _review_range_summary(
                metrics,
                item,
                thresholds.get("static_threshold") if thresholds else None,
            )
            lines.append(
                f"- {summary['name']}: source {summary['start']}-{summary['end']}, "
                f"frames={summary['frame_count']}, mean_motion={summary['mean_motion']}, "
                f"min_motion={summary['min_motion']}, max_motion={summary['max_motion']}, "
                f"below_static_threshold={summary['below_static_threshold']}, "
                f"longest_static_run={summary['longest_static_run']}"
            )
    else:
        lines.append("- none")

    bad_count = sum(1 for metric in metrics if metric.bad_quality_candidate)
    lines.extend(
        [
            "",
            "## Warnings",
            f"- {bad_count} frames marked as bad_quality_candidate, not automatically dropped.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_engineering_log_md(
    path: Path,
    metrics: list[FrameMetric],
    segments: list[Segment],
    strategy: dict,
    preview_only: bool,
    timestamp_source: str,
) -> None:
    operation_count = len(strategy.get("operations", []))
    manual_count = sum(1 for operation in strategy.get("operations", []) if operation.get("source") == "manual_override")
    bad_count = sum(1 for metric in metrics if metric.bad_quality_candidate)
    mode_text = "预览模式，未写出输出帧" if preview_only else "执行模式，已写出输出帧"
    lines = [
        "# 视频帧节奏 Agent 工程日志",
        "",
        "## 输入数据",
        f"- 分析帧数：{len(metrics)}",
        f"- 时间戳来源：{timestamp_source}",
        "",
        "## 发现的问题",
        f"- 检测到节奏片段数量：{len(segments)}",
        f"- 疑似低质量帧数量：{bad_count}",
        "- 重点关注长静止段、快速运动段、极快运动段和可能的质量异常。",
        "",
        "## 使用的方法",
        "- 使用拉普拉斯方差估计清晰度。",
        "- 使用灰度均值和标准差估计亮度与对比度。",
        "- 使用相邻帧归一化灰度差估计运动强度。",
        "- 使用分位数阈值适配当前视频的运动尺度。",
        "- 所有范围都使用稳定的 source_index，避免多轮删帧、增帧后帧号漂移。",
        "",
        "## 策略决策",
        f"- 生成策略操作数量：{operation_count}",
        f"- 人工覆盖策略数量：{manual_count}",
        "- 长静止段默认压缩到 20 帧，减少无效重复计算。",
        "- 快速运动段默认把每帧扩展到 3 份，极快运动段扩展到 4 份。",
        "- 质量异常段只标记和报告，第一版不自动删除。",
        "",
        "## 处理结果",
        f"- 当前模式：{mode_text}。",
        "- 已写出 frame_metrics.csv、segments.json、strategy.json 和 report.md。",
        "",
        "## 风险和局限",
        "- 重复图片不会创造新的视角，只能增加建模过程在局部位置的停留时间。",
        "- 运动检测不理解管道病害语义，缺陷区间仍需要人工复核。",
        "- 对反光、模糊、曝光突变的判断仍可能出现误报。",
        "",
        "## 下一步实验建议",
        "- 先用小样本验证策略，再扩大到全帧。",
        "- 对报告中的快速运动段优先检查三维重建空洞是否改善。",
        "- 如果重复帧导致训练耗时过高，再降低 fast_motion 或 very_fast_motion 的重复倍率。",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _estimate_output_count(metrics: list[FrameMetric], strategy: dict) -> int:
    source_indices = {metric.source_index for metric in metrics}
    estimated = len(metrics)
    for operation in strategy.get("operations", []):
        source_range = operation.get("range", {})
        start = int(source_range.get("start", 0))
        end = int(source_range.get("end", -1))
        affected = sum(1 for source_index in source_indices if start <= source_index <= end)
        if operation.get("op") == "keep_uniform":
            estimated -= max(0, affected - int(operation.get("count", affected)))
        elif operation.get("op") == "duplicate_range":
            estimated += affected * (int(operation.get("total_instances", 1)) - 1)
        elif operation.get("op") == "select_sources":
            selected = {int(source) for source in operation.get("sources", [])}
            affected_sources = {source_index for source_index in source_indices if start <= source_index <= end}
            estimated -= max(0, affected - len(selected & affected_sources))
    return estimated


def _motion_thresholds(metrics: list[FrameMetric], detection_config: dict) -> dict:
    if not metrics:
        return {}
    calibration_metrics = [metric for metric in metrics if not metric.bad_quality_candidate] or metrics
    scores = sorted(metric.motion_score for metric in calibration_metrics)
    static_quantile = float(detection_config.get("static_motion_quantile", 0.15))
    fast_quantile = float(detection_config.get("fast_motion_quantile", 0.70))
    very_fast_quantile = float(detection_config.get("very_fast_motion_quantile", 0.90))
    return {
        "static_quantile": static_quantile,
        "fast_quantile": fast_quantile,
        "very_fast_quantile": very_fast_quantile,
        "static_threshold": _quantile(scores, static_quantile),
        "fast_threshold": _quantile(scores, fast_quantile),
        "very_fast_threshold": _quantile(scores, very_fast_quantile),
    }


def _quantile(sorted_values: list[float], quantile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = quantile * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return float(sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction)


def _review_range_summary(
    metrics: list[FrameMetric],
    review_range: dict,
    static_threshold: float | None,
) -> dict:
    start = int(review_range["start"])
    end = int(review_range["end"])
    name = review_range.get("name", f"{start}-{end}")
    selected = [metric for metric in metrics if start <= metric.source_index <= end]
    if not selected:
        return {
            "name": name,
            "start": start,
            "end": end,
            "frame_count": 0,
            "mean_motion": "n/a",
            "min_motion": "n/a",
            "max_motion": "n/a",
            "below_static_threshold": "n/a",
            "longest_static_run": 0,
        }

    motions = [metric.motion_score for metric in selected]
    below_static = (
        sum(1 for motion in motions if motion <= static_threshold)
        if static_threshold is not None
        else "n/a"
    )
    longest_run = _longest_static_run(selected, static_threshold) if static_threshold is not None else 0
    return {
        "name": name,
        "start": start,
        "end": end,
        "frame_count": len(selected),
        "mean_motion": f"{sum(motions) / len(motions):.6f}",
        "min_motion": f"{min(motions):.6f}",
        "max_motion": f"{max(motions):.6f}",
        "below_static_threshold": below_static,
        "longest_static_run": longest_run,
    }


def _longest_static_run(metrics: list[FrameMetric], static_threshold: float) -> int:
    longest = 0
    current = 0
    for metric in sorted(metrics, key=lambda item: item.source_index):
        if metric.motion_score <= static_threshold:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return longest
