from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
import argparse
import json
import sys


if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frame_timing_agent.apply_frame_strategy import apply_strategy
from frame_timing_agent.frame_source import FrameRecord, load_frame_records
from frame_timing_agent.frame_strategy import build_strategy
from frame_timing_agent.human_review import write_human_review
from frame_timing_agent.jitter_strategy import build_jitter_reduction_strategy, merge_jitter_with_base_strategy
from frame_timing_agent.motion_estimator import estimate_frame_motion
from frame_timing_agent.segment_detector import detect_segments
from frame_timing_agent.strategy_execution_audit import audit_strategy_execution, write_execution_audit
from frame_timing_agent.strategy_visual_review import write_strategy_visual_review
from frame_timing_agent.timing_metrics import compute_frame_metrics
from frame_timing_agent.timing_report import write_analysis_artifacts


DEFAULT_CONFIG_PATH = Path(__file__).parent / "config" / "timing_default.json"
ProgressCallback = Callable[[int, str], None]


@dataclass(frozen=True)
class TimingAgentResult:
    analyzed_count: int
    estimated_output_count: int
    artifact_dir: Path
    strategy_path: Path
    output_dir: Path | None


def load_config(config_path: Path | str = DEFAULT_CONFIG_PATH) -> dict:
    return json.loads(Path(config_path).read_text(encoding="utf-8"))


def run_timing_agent(
    frames: Path | str,
    artifact_dir: Path | str,
    limit_first_n: int | None = 300,
    mode: str = "reconstruction_balanced",
    write: bool = False,
    fps: float | None = None,
    override_config_path: Path | str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> TimingAgentResult:
    if mode != "reconstruction_balanced":
        raise ValueError(f"unsupported timing agent mode: {mode}")

    _report_progress(progress_callback, 0, "正在读取帧目录")
    config = load_config()
    override_config = _load_override_config(override_config_path)
    config = _merge_config(config, override_config.get("config", {}))
    if fps is None:
        fps = float(config["default_fps"])

    frames = Path(frames)
    artifact_dir = Path(artifact_dir)
    analysis_dir = artifact_dir / "analysis"

    records = load_frame_records(frames, fps=fps, limit_first_n=limit_first_n)
    _report_progress(progress_callback, 5, "正在计算帧指标")
    metrics = compute_frame_metrics(
        records,
        progress_callback=_map_stage_progress(progress_callback, 5, 45, "正在计算帧指标"),
    )
    _report_progress(progress_callback, 47, "正在检测时序区间")
    segments = detect_segments(
        metrics,
        static_motion_quantile=float(config["static_motion_quantile"]),
        fast_motion_quantile=float(config["fast_motion_quantile"]),
        very_fast_motion_quantile=float(config["very_fast_motion_quantile"]),
        min_static_frames=int(config["min_static_frames"]),
        min_fast_frames=int(config["min_fast_frames"]),
        static_window_min_low_ratio=float(config.get("static_window_min_low_ratio", 0.70)),
        static_window_mean_multiplier=float(config.get("static_window_mean_multiplier", 2.0)),
    )
    base_strategy = build_strategy(
        segments,
        frame_dir=frames,
        limit_first_n=limit_first_n,
        static_keep_count=int(config["static_keep_count"]),
        fast_motion_total_instances=int(config["fast_motion_total_instances"]),
        very_fast_motion_total_instances=int(config["very_fast_motion_total_instances"]),
        overrides=override_config.get("overrides"),
    )
    _report_progress(progress_callback, 50, "正在估计相邻帧运动")
    jitter_min_sharpness = float(config.get("jitter_min_sharpness", 100.0))
    motion_estimates = estimate_frame_motion(
        records,
        min_sharpness=jitter_min_sharpness,
        progress_callback=_map_stage_progress(progress_callback, 50, 78, "正在估计相邻帧运动"),
    )
    jitter_strategy = build_jitter_reduction_strategy(
        records=records,
        estimates=motion_estimates,
        frame_dir=frames,
        limit_first_n=limit_first_n,
        max_output_ratio=float(config.get("jitter_max_output_ratio", 0.60)),
        min_jitter_frames=int(config.get("jitter_min_frames", 5)),
        min_motion=float(config.get("jitter_min_motion", 2.0)),
        min_response=float(config.get("jitter_min_response", 0.02)),
        min_sharpness=jitter_min_sharpness,
    )
    strategy = merge_jitter_with_base_strategy(base_strategy, jitter_strategy, records)
    estimated_output_count = _estimate_output_count(records, strategy)

    _report_progress(progress_callback, 80, "正在写入分析结果")
    write_analysis_artifacts(
        analysis_dir=analysis_dir,
        metrics=metrics,
        segments=segments,
        strategy=strategy,
        preview_only=not write,
        timestamp_source=f"inferred from fps={fps}",
        detection_config=config,
        review_ranges=override_config.get("review_ranges", []),
    )

    output_dir = None
    audit = None
    if write:
        output_dir = artifact_dir / "output_frames"
        apply_strategy(
            records,
            strategy,
            output_dir,
            progress_callback=_map_stage_progress(progress_callback, 82, 94, "正在生成 output_frames"),
        )
        _report_progress(progress_callback, 95, "正在校验输出结果")
        audit = audit_strategy_execution(records, strategy, output_dir, fps=fps)
        write_execution_audit(audit, analysis_dir)

    write_human_review(
        analysis_dir=analysis_dir,
        stage_name="阶段 4：本地图片处理 Agent",
        input_frame_dir=frames,
        output_dir=output_dir,
        analyzed_count=len(records),
        estimated_output_count=estimated_output_count,
        strategy=strategy,
        audit=audit,
        preview_only=not write,
    )
    visual_start = 96 if write else 82
    _report_progress(progress_callback, visual_start, "正在生成代表帧")
    write_strategy_visual_review(
        frame_dir=frames,
        analysis_dir=analysis_dir,
        strategy=strategy,
        fps=fps,
        progress_callback=_map_stage_progress(progress_callback, visual_start, 99, "正在生成代表帧"),
    )

    terminal_message = "output_frames 生成完成" if write else "分析帧目录完成"
    _report_progress(progress_callback, 100, terminal_message)

    return TimingAgentResult(
        analyzed_count=len(records),
        estimated_output_count=estimated_output_count,
        artifact_dir=artifact_dir,
        strategy_path=analysis_dir / "strategy.json",
        output_dir=output_dir,
    )


def _report_progress(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback is not None:
        callback(max(0, min(100, int(percent))), message)


def _map_stage_progress(
    callback: ProgressCallback | None,
    start: int,
    end: int,
    message: str,
) -> Callable[[int, int], None] | None:
    if callback is None:
        return None

    def report(completed: int, total: int) -> None:
        ratio = completed / max(1, total)
        percent = start + round((end - start) * ratio)
        _report_progress(callback, percent, message)

    return report


def _estimate_output_count(records: Sequence[FrameRecord], strategy: dict) -> int:
    source_indices = {record.source_index for record in records}
    count = len(records)
    for operation in strategy.get("operations", []):
        source_range = operation.get("range", {})
        start = int(source_range["start"])
        end = int(source_range["end"])
        affected = [source for source in source_indices if start <= source <= end]
        if operation.get("op") == "keep_uniform":
            count -= max(0, len(affected) - int(operation["count"]))
        elif operation.get("op") == "duplicate_range":
            count += len(affected) * (int(operation["total_instances"]) - 1)
        elif operation.get("op") == "select_sources":
            selected = {int(source) for source in operation.get("sources", [])}
            count -= max(0, len(affected) - len(selected & set(affected)))
    return count


def _load_override_config(config_path: Path | str | None) -> dict:
    if config_path is None:
        return {}
    config_path = Path(config_path)
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"override config must be a JSON object: {config_path}")
    return data


def _merge_config(default_config: dict, override_config: dict) -> dict:
    if not override_config:
        return dict(default_config)
    if not isinstance(override_config, dict):
        raise ValueError("override config field 'config' must be a JSON object")
    merged = dict(default_config)
    merged.update(override_config)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze and adjust extracted frame timing.")
    parser.add_argument("--frames", required=True, type=Path)
    parser.add_argument("--artifact_dir", required=True, type=Path)
    parser.add_argument("--limit_first_n", type=int, default=300)
    parser.add_argument("--mode", default="reconstruction_balanced", choices=["reconstruction_balanced"])
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--override_config", type=Path, default=None)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    result = run_timing_agent(
        frames=args.frames,
        artifact_dir=args.artifact_dir,
        limit_first_n=args.limit_first_n,
        mode=args.mode,
        write=args.write,
        fps=args.fps,
        override_config_path=args.override_config,
    )
    print(f"Analyzed frames: {result.analyzed_count}")
    print(f"Estimated output frames: {result.estimated_output_count}")
    print(f"Strategy: {result.strategy_path}")
    if result.output_dir is not None:
        print(f"Output frames: {result.output_dir}")


if __name__ == "__main__":
    main()
