from __future__ import annotations

from pathlib import Path


def write_human_review(
    analysis_dir: Path | str,
    stage_name: str,
    input_frame_dir: Path | str,
    output_dir: Path | str | None,
    analyzed_count: int,
    estimated_output_count: int,
    strategy: dict,
    audit: dict | None,
    preview_only: bool,
) -> Path:
    analysis_dir = Path(analysis_dir)
    analysis_dir.mkdir(parents=True, exist_ok=True)
    output_path = analysis_dir / "human_review.md"
    output_path.write_text(
        _human_review_markdown(
            stage_name=stage_name,
            input_frame_dir=Path(input_frame_dir),
            output_dir=Path(output_dir) if output_dir is not None else None,
            analyzed_count=analyzed_count,
            estimated_output_count=estimated_output_count,
            strategy=strategy,
            audit=audit,
            preview_only=preview_only,
        ),
        encoding="utf-8",
    )
    return output_path


def _human_review_markdown(
    stage_name: str,
    input_frame_dir: Path,
    output_dir: Path | None,
    analyzed_count: int,
    estimated_output_count: int,
    strategy: dict,
    audit: dict | None,
    preview_only: bool,
) -> str:
    operations = strategy.get("operations", [])
    auto_count = sum(1 for operation in operations if operation.get("source") == "auto_detection")
    manual_count = sum(1 for operation in operations if operation.get("source") == "manual_override")
    lines = [
        f"# {stage_name}",
        "",
        "## 1. 本阶段结论",
        "- 本阶段只处理帧节奏策略和输出目录，不修改图片内容，不做补洞、锐化、伪造帧或视觉 trick。",
        f"- 当前模式：{'预览模式，未写出输出帧' if preview_only else '执行模式，已写出输出帧'}。",
        f"- 分析输入帧数：{analyzed_count}",
        f"- 预计输出帧数：{estimated_output_count}",
        f"- 自动策略数量：{auto_count}",
        f"- 人工覆盖策略数量：{manual_count}",
        "",
        "## 2. 输入和输出",
        f"- 输入帧目录名：`{input_frame_dir.name}`",
        f"- 输出帧目录名：`{output_dir.name}`" if output_dir is not None else "- 输出帧目录：预览模式未生成",
        f"- 策略文件：`{Path('strategy.json')}`",
        f"- 机器报告：`{Path('report.md')}`",
        f"- 工程日志：`{Path('engineering_log.md')}`",
        "",
        "## 3. 策略执行表",
        "",
        "| source_index 区间 | 策略 | 来源 | 输入帧数 | 输出帧数 | 变化 | 审查说明 |",
        "|---|---|---|---:|---:|---:|---|",
    ]

    audit_by_range = _audit_results_by_range(audit)
    for operation in operations:
        source_range = operation.get("range", {})
        start = int(source_range.get("start", 0))
        end = int(source_range.get("end", -1))
        audit_result = audit_by_range.get((start, end))
        affected = _affected_count(operation, audit_result)
        output_count = _output_count(operation, audit_result, affected)
        delta = output_count - affected if affected is not None and output_count is not None else None
        lines.append(
            "| "
            f"{start}-{end} | "
            f"{_operation_label(operation)} | "
            f"{_source_label(operation.get('source'))} | "
            f"{_cell(affected)} | "
            f"{_cell(output_count)} | "
            f"{_signed_cell(delta)} | "
            f"{_review_note(operation, audit_result)} |"
        )

    if not operations:
        lines.append("| - | 无策略 | - | - | - | - | 无需处理 |")

    lines.extend(["", "## 4. 关键变化摘要"])
    if operations:
        for operation in operations:
            source_range = operation.get("range", {})
            start = int(source_range.get("start", 0))
            end = int(source_range.get("end", -1))
            audit_result = audit_by_range.get((start, end))
            affected = _affected_count(operation, audit_result)
            output_count = _output_count(operation, audit_result, affected)
            lines.append(
                f"- source_index {start}-{end}: {_operation_label(operation)}，"
                f"{_cell(affected)} -> {_cell(output_count)}"
            )
    else:
        lines.append("- 无帧数变化。")

    lines.extend(["", "## 5. 重点保留帧"])
    kept_sections = [
        result for result in (audit or {}).get("operation_results", [])
        if result.get("op") == "keep_uniform" and result.get("kept_sources")
    ]
    if kept_sections:
        for result in kept_sections:
            source_range = result["range"]
            lines.append(f"### {source_range['start']}-{source_range['end']}")
            lines.append("- 实际保留 source_index：" + ", ".join(str(item) for item in result["kept_sources"]))
    else:
        lines.append("- 当前没有已执行的均匀保留区间，或处于预览模式。")

    lines.extend(["", "## 6. 执行一致性"])
    if audit is None:
        lines.append("- 预览模式未生成输出帧，因此没有 execution audit。")
    else:
        lines.extend(
            [
                f"- 审查状态：{audit.get('status')}",
                f"- 输入记录数：{audit.get('input_count')}",
                f"- 输出记录数：{audit.get('output_count')}",
                f"- manifest 输出数：{audit.get('manifest_output_count')}",
                f"- 实际图片数：{audit.get('image_count')}",
                f"- 错误数：{len(audit.get('errors', []))}",
                f"- 警告数：{len(audit.get('warnings', []))}",
            ]
        )

    lines.extend(
        [
            "",
            "## 7. 风险和限制",
            "- 重复帧只会增加模型在局部位置的停留时间，不会创造新的视角。",
            "- 静止段压缩可能删除一些反光或细节变化帧，因此所有压缩区间都需要人工确认。",
            "- 当前 agent 不识别病害语义，只处理帧节奏。",
            "",
            "## 8. 等待你确认",
            "- 请审查上面的 source_index 区间、帧数变化和重点保留帧。",
            "- 你确认后再进入下一阶段；不确认则继续优化本地图片处理策略。",
            "",
        ]
    )
    return "\n".join(lines)


def _audit_results_by_range(audit: dict | None) -> dict[tuple[int, int], dict]:
    if audit is None:
        return {}
    results = {}
    for result in audit.get("operation_results", []):
        source_range = result.get("range", {})
        results[(int(source_range["start"]), int(source_range["end"]))] = result
    return results


def _operation_label(operation: dict) -> str:
    op = operation.get("op")
    if op == "keep_uniform":
        return "均匀保留/压缩静止段"
    if op == "duplicate_range":
        instances = operation.get("total_instances")
        return f"重复帧/放慢运动 x{instances}"
    if op == "keep":
        return "保持原样"
    if op == "mark_review":
        return "仅标记审查"
    return str(op)


def _source_label(source: str | None) -> str:
    if source == "auto_detection":
        return "自动检测"
    if source == "manual_override":
        return "人工指定"
    return source or "未知"


def _affected_count(operation: dict, audit_result: dict | None) -> int | None:
    if audit_result is not None:
        return int(audit_result["affected_source_count"])
    source_range = operation.get("range", {})
    if "start" not in source_range or "end" not in source_range:
        return None
    return max(0, int(source_range["end"]) - int(source_range["start"]) + 1)


def _output_count(operation: dict, audit_result: dict | None, affected: int | None) -> int | None:
    if audit_result is not None:
        return int(audit_result["output_record_count"])
    if affected is None:
        return None
    if operation.get("op") == "keep_uniform":
        return min(affected, int(operation.get("count", affected)))
    if operation.get("op") == "duplicate_range":
        return affected * int(operation.get("total_instances", 1))
    return affected


def _review_note(operation: dict, audit_result: dict | None) -> str:
    if operation.get("op") == "keep_uniform":
        return "请确认保留帧是否覆盖首尾和关键变化。"
    if operation.get("op") == "duplicate_range":
        return "请确认放慢倍率是否符合建模需要。"
    if operation.get("op") == "keep":
        return "该区间不参与自动处理。"
    if audit_result and audit_result.get("warnings"):
        return "存在警告，需要检查。"
    return "请人工复核。"


def _cell(value: int | None) -> str:
    return "-" if value is None else str(value)


def _signed_cell(value: int | None) -> str:
    if value is None:
        return "-"
    return f"{value:+d}"
