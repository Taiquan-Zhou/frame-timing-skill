from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from frame_timing_agent.agent_artifact_health import HealthPublicationError, run_agent_artifact_health_check
from frame_timing_agent.artifact_io import (
    ArtifactFormatError,
    read_analysis_result,
    read_strategy_candidate,
    read_validation_result,
)
from frame_timing_agent.batch_discovery import discover_frame_directories
from frame_timing_agent.batch_session import (
    BatchBusyError,
    BatchExportStaleSourceError,
    BatchItemState,
    BatchItemStatus,
    BatchState,
    BatchStateError,
    BatchStatus,
    approve_item,
    create_batch,
    export_batch,
    load_batch,
    run_batch,
)
from frame_timing_agent.artifact_layout import (
    ANALYSIS_ARTIFACT,
    EXECUTION_ARTIFACT,
    HEALTH_ARTIFACT,
    HUMAN_REVIEW_ARTIFACT,
    OUTPUT_DIRECTORY,
    REPORT_ARTIFACT,
    STRATEGY_ARTIFACT,
    VALIDATION_ARTIFACT,
)
from frame_timing_agent.configuration import parse_strategy_request
from frame_timing_agent.contracts import SCHEMA_VERSION, ConfigurationError, PolicyName
from frame_timing_agent.serialization import canonical_json_bytes
from frame_timing_agent.service import (
    analyze_frames,
    apply_validated_strategy,
    capabilities,
    plan_strategy,
    validate_strategy,
)

EXIT_SUCCESS = 0
EXIT_INPUT_ERROR = 2
EXIT_UNSAFE_STRATEGY = 3
EXIT_EXECUTION_FAILED = 4
EXIT_HEALTH_FAILED = 5


class CliInputError(ValueError):
    pass


class _BatchStaleSourceError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CliInputError(message)


def main(argv: Sequence[str] | None = None) -> int:
    command: str | None = None
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        args = _parser().parse_args(raw_argv)
        command = str(args.command)
        exit_code, payload = _dispatch(args)
    except CliInputError as exc:
        if command == "batch" or (raw_argv and raw_argv[0] == "batch"):
            return _emit_error(EXIT_INPUT_ERROR, "invalid_input", "batch command input is invalid", exc)
        return _emit_error(EXIT_INPUT_ERROR, "input_error", "command arguments are invalid", exc)
    except ArtifactFormatError as exc:
        return _emit_error(EXIT_INPUT_ERROR, "invalid_artifact", "artifact JSON is invalid", exc)
    except BatchBusyError as exc:
        return _emit_error(EXIT_EXECUTION_FAILED, "busy_batch", "batch is busy", exc)
    except BatchStateError as exc:
        return _emit_error(EXIT_INPUT_ERROR, "invalid_input", "batch command input is invalid", exc)
    except _BatchStaleSourceError as exc:
        return _emit_error(EXIT_EXECUTION_FAILED, "stale_source", "batch source changed since analysis", exc)
    except (ConfigurationError, FileNotFoundError) as exc:
        return _emit_error(EXIT_INPUT_ERROR, "input_error", "command input is invalid", exc)
    except HealthPublicationError as exc:
        return _emit_error(EXIT_HEALTH_FAILED, "health_failed", "health artifact publication failed", exc)
    except (OSError, ValueError) as exc:
        if command == "apply":
            return _emit_error(EXIT_EXECUTION_FAILED, "execution_failed", "validated execution failed", exc)
        return _emit_error(EXIT_INPUT_ERROR, "input_error", "command input is invalid", exc)
    _emit(payload)
    return exit_code


def _parser() -> _ArgumentParser:
    parser = _ArgumentParser(prog="frame-timing-tool")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("capabilities")

    analyze = subparsers.add_parser("analyze")
    analyze.add_argument("--frames", required=True)
    analyze.add_argument("--artifact-root", required=True)
    analyze.add_argument("--fps", type=float, default=30.0)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--analysis", required=True)
    plan.add_argument("--policy", choices=[policy.value for policy in PolicyName], required=True)
    plan.add_argument("--minimum-retention-ratio", type=float)
    plan.add_argument("--maximum-consecutive-drops", type=int)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--analysis", required=True)
    validate.add_argument("--strategy", required=True)

    apply = subparsers.add_parser("apply")
    apply.add_argument("--frames", required=True)
    apply.add_argument("--analysis", required=True)
    apply.add_argument("--strategy", required=True)
    apply.add_argument("--validation", required=True)
    apply.add_argument("--output-dir", required=True)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--frames", required=True)
    verify.add_argument("--artifact-root", required=True)

    batch = subparsers.add_parser("batch")
    batch_actions = batch.add_subparsers(dest="batch_action", required=True)

    batch_create = batch_actions.add_parser("create")
    batch_create.add_argument("--frames", action="append", default=[])
    batch_create.add_argument("--root")
    batch_create.add_argument("--state", required=True)
    batch_create.add_argument("--fps", type=float, default=30.0)
    batch_create.add_argument("--limit-first-n", type=int)

    batch_run = batch_actions.add_parser("run")
    batch_run.add_argument("--state", required=True)
    batch_run.add_argument("--retry-item", action="append", default=[])

    batch_status = batch_actions.add_parser("status")
    batch_status.add_argument("--state", required=True)

    batch_approve = batch_actions.add_parser("approve")
    batch_approve.add_argument("--state", required=True)
    batch_approve.add_argument("--item", required=True)
    batch_approve.add_argument("--note", default="")

    batch_export = batch_actions.add_parser("export")
    batch_export.add_argument("--state", required=True)
    return parser


def _dispatch(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    command = str(args.command)
    if command == "capabilities":
        return EXIT_SUCCESS, _response("ok", None, {}, capabilities())
    if command == "analyze":
        frame_dir = Path(args.frames)
        analysis = analyze_frames(frame_dir, Path(args.artifact_root), fps=float(args.fps))
        _log("analyze", f"analyzed {analysis.frame_count} frames")
        payload = _response("ok", analysis.run_id, {"analysis": ANALYSIS_ARTIFACT}, analysis)
        payload["input_name"] = frame_dir.name
        return EXIT_SUCCESS, payload
    if command == "plan":
        analysis_path = _canonical_artifact_path(args.analysis, ANALYSIS_ARTIFACT)
        analysis = read_analysis_result(analysis_path)
        request = parse_strategy_request(
            {
                "schema_version": SCHEMA_VERSION,
                "policy": args.policy,
                "minimum_retention_ratio": args.minimum_retention_ratio,
                "maximum_consecutive_drops": args.maximum_consecutive_drops,
            }
        )
        candidate = plan_strategy(analysis, request, analysis_path.parent)
        _log("plan", f"planned {candidate.estimated_output_count} output frames")
        return EXIT_SUCCESS, _response(
            "ok",
            analysis.run_id,
            {"analysis": ANALYSIS_ARTIFACT, "strategy": STRATEGY_ARTIFACT},
            candidate,
        )
    if command == "validate":
        analysis_path = _canonical_artifact_path(args.analysis, ANALYSIS_ARTIFACT)
        strategy_path = _canonical_artifact_path(args.strategy, STRATEGY_ARTIFACT)
        artifact_root = _shared_artifact_root(analysis_path, strategy_path)
        analysis = read_analysis_result(analysis_path)
        candidate = read_strategy_candidate(strategy_path)
        validation = validate_strategy(analysis, candidate, candidate.request, artifact_root)
        status = "ok" if validation.valid else "unsafe"
        _log("validate", status)
        return (EXIT_SUCCESS if validation.valid else EXIT_UNSAFE_STRATEGY), _response(
            status,
            analysis.run_id,
            {
                "analysis": ANALYSIS_ARTIFACT,
                "strategy": STRATEGY_ARTIFACT,
                "validation": VALIDATION_ARTIFACT,
            },
            validation,
        )
    if command == "apply":
        analysis_path = _canonical_artifact_path(args.analysis, ANALYSIS_ARTIFACT)
        strategy_path = _canonical_artifact_path(args.strategy, STRATEGY_ARTIFACT)
        validation_path = _canonical_artifact_path(args.validation, VALIDATION_ARTIFACT)
        output_dir = _canonical_artifact_path(args.output_dir, OUTPUT_DIRECTORY)
        artifact_root = _shared_artifact_root(analysis_path, strategy_path, validation_path, output_dir)
        analysis = read_analysis_result(analysis_path)
        candidate = read_strategy_candidate(strategy_path)
        validation = read_validation_result(validation_path)
        execution = apply_validated_strategy(Path(args.frames), analysis, candidate, validation, output_dir)
        _log("apply", f"wrote {execution.output_frame_count} output frames")
        return EXIT_SUCCESS, _response(
            "ok",
            analysis.run_id,
            {
                "analysis": ANALYSIS_ARTIFACT,
                "strategy": STRATEGY_ARTIFACT,
                "validation": VALIDATION_ARTIFACT,
                "execution": EXECUTION_ARTIFACT,
                "output_frames": output_dir.relative_to(artifact_root).as_posix(),
            },
            execution,
        )
    if command == "verify":
        artifact_root = Path(args.artifact_root)
        health = run_agent_artifact_health_check(Path(args.frames), artifact_root)
        status = "ok" if health.valid else "failed"
        _log("verify", status)
        return (EXIT_SUCCESS if health.valid else EXIT_HEALTH_FAILED), _response(
            status,
            health.run_id,
            {
                "analysis": ANALYSIS_ARTIFACT,
                "strategy": STRATEGY_ARTIFACT,
                "validation": VALIDATION_ARTIFACT,
                "execution": EXECUTION_ARTIFACT,
                "health": HEALTH_ARTIFACT,
                "report": REPORT_ARTIFACT,
                "human_review": HUMAN_REVIEW_ARTIFACT,
                "output_frames": OUTPUT_DIRECTORY,
            },
            health,
        )
    if command == "batch":
        return _dispatch_batch(args)
    raise CliInputError("unsupported command")


def _dispatch_batch(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    action = str(args.batch_action)
    if action == "create":
        if not args.frames and args.root is None:
            raise CliInputError("batch create requires frames or a discovery root")
        state_path = _batch_state_path(args.state)
        discovery = discover_frame_directories(args.frames, args.root)
        try:
            state = create_batch(
                discovery,
                artifact_root=state_path.parent.parent,
                fps=float(args.fps),
                limit_first_n=args.limit_first_n,
            )
        except ValueError as exc:
            raise BatchStateError("batch creation input is invalid") from exc
        if state.state_path.resolve() != state_path:
            raise CliInputError("batch state path is not canonical")
        return EXIT_SUCCESS, _batch_response(state, discovery_issues=[issue.code for issue in discovery.issues])

    state_path = _batch_state_path(args.state)
    if action == "status":
        return _batch_terminal_response(load_batch(state_path), action="status")
    if action == "run":
        state = run_batch(state_path, retry_items=tuple(args.retry_item))
        return _batch_terminal_response(state, action="run")
    if action == "approve":
        try:
            state = approve_item(state_path, str(args.item), str(args.note))
        except BatchStateError:
            raise
        except ValueError as exc:
            raise _BatchStaleSourceError() from exc
        return EXIT_SUCCESS, _batch_response(state)
    if action == "export":
        try:
            summary = export_batch(state_path)
        except BatchExportStaleSourceError as exc:
            state = load_batch(state_path)
            export = {
                "exported": list(exc.summary.exported),
                "skipped": list(exc.summary.skipped),
                "failed": list(exc.summary.failed),
            }
            payload = _batch_response(state, export=export)
            payload["status"] = "failed"
            payload["result"]["stale_items"] = list(exc.stale_items)
            payload["error"] = {"code": "stale_source", "message": "batch source changed since analysis"}
            return EXIT_EXECUTION_FAILED, payload
        state = load_batch(state_path)
        export = {
            "exported": list(summary.exported),
            "skipped": list(summary.skipped),
            "failed": list(summary.failed),
        }
        payload = _batch_response(state, export=export)
        if summary.failed:
            payload["status"] = "failed"
            payload["error"] = {"code": "unsafe_export", "message": "one or more batch items could not be exported"}
            return EXIT_EXECUTION_FAILED, payload
        return _batch_terminal_response(state, action="export", export=export)
    raise CliInputError("unsupported batch action")


def _batch_state_path(raw_path: str) -> Path:
    state_path = Path(raw_path).resolve()
    if state_path.name != "batch_state.json" or state_path.parent.name != "analysis":
        raise CliInputError("batch state path is not canonical")
    if "output" not in {part.casefold() for part in state_path.parents[1].parts}:
        raise CliInputError("batch artifact root must be inside an output directory")
    return state_path


def _batch_terminal_response(
    state: BatchState,
    *,
    action: str,
    export: dict[str, list[str]] | None = None,
) -> tuple[int, dict[str, object]]:
    payload = _batch_response(state, export=export)
    if action == "run" and any(item.status is BatchItemStatus.FAILED for item in state.items):
        payload["status"] = "failed"
        payload["error"] = {"code": "analysis_failed", "message": "one or more batch items failed analysis"}
        return EXIT_EXECUTION_FAILED, payload
    if _batch_health_failed(state):
        payload["status"] = "failed"
        payload["error"] = {"code": "artifact_health_failed", "message": "batch artifact health check failed"}
        return EXIT_HEALTH_FAILED, payload
    return EXIT_SUCCESS, payload


def _batch_health_failed(state: BatchState) -> bool:
    health_path = state.artifact_root / "analysis" / "maintenance_report.json"
    try:
        health = json.loads(health_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return state.status is BatchStatus.FINISHED
    except (OSError, json.JSONDecodeError):
        return True
    return not isinstance(health, dict) or health.get("status") != "ok"


def _batch_response(
    state: BatchState,
    *,
    discovery_issues: list[str] | None = None,
    export: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    result: dict[str, object] = {
        "batch": {"id": state.batch_id, "status": state.status.value},
        "items": [_batch_item_response(item) for item in state.items],
        "risks": {
            "review_required_items": [
                item.safe_name for item in state.items if item.status is BatchItemStatus.REVIEW_REQUIRED
            ],
            "warning_count": sum(len(item.warnings) for item in state.items),
            "discovery_issues": discovery_issues or [],
        },
        "progress": {
            "completed": sum(
                item.status not in {BatchItemStatus.PENDING, BatchItemStatus.RUNNING} for item in state.items
            ),
            "total": len(state.items),
        },
        "retry_items": [item.safe_name for item in state.items if item.status is BatchItemStatus.FAILED],
        "next_actions": _batch_next_actions(state),
    }
    if export is not None:
        result["export"] = export
    artifacts = {"batch_state": "analysis/batch_state.json"}
    if state.status is BatchStatus.FINISHED and (state.artifact_root / "analysis" / "batch_summary.json").is_file():
        artifacts["batch_summary"] = "analysis/batch_summary.json"
    if state.status is BatchStatus.FINISHED and (state.artifact_root / "analysis" / "human_review.md").is_file():
        artifacts["human_review"] = "analysis/human_review.md"
    return _response(
        "ok",
        state.batch_id,
        artifacts,
        result,
    )


def _batch_item_response(item: BatchItemState) -> dict[str, object]:
    exported = (
        item.output_path is not None
        and item.output_path.is_dir()
        and (item.output_path.parent / "analysis" / "execution_audit.json").is_file()
    )
    return {
        "name": item.safe_name,
        "status": item.status.value,
        "progress": round(item.progress * 100),
        "retry_count": item.retry_count,
        "risks": list(item.warnings),
        "approved": item.approved,
        "exported": exported,
        "analyzed_count": item.analyzed_count,
        "output_count": item.output_count,
        "error": item.last_error,
    }


def _batch_next_actions(state: BatchState) -> list[str]:
    actions: list[str] = []
    if (
        state.status in {BatchStatus.READY, BatchStatus.RUNNING, BatchStatus.PAUSED}
        or any(item.status is BatchItemStatus.PENDING for item in state.items)
        or any(item.status is BatchItemStatus.FAILED for item in state.items)
    ):
        actions.append("run")
    if any(item.status is BatchItemStatus.REVIEW_REQUIRED and not item.approved for item in state.items):
        actions.append("approve")
    if (
        state.status is BatchStatus.FINISHED
        and any(
            item.status is BatchItemStatus.COMPLETED
            or (item.status is BatchItemStatus.REVIEW_REQUIRED and item.approved)
            for item in state.items
        )
        and any(
            item.output_path is None
            for item in state.items
            if item.status is BatchItemStatus.COMPLETED
            or (item.status is BatchItemStatus.REVIEW_REQUIRED and item.approved)
        )
    ):
        actions.append("export")
    return actions


def _shared_artifact_root(*paths: Path) -> Path:
    parents = {path.resolve().parent for path in paths}
    if len(parents) != 1:
        raise ArtifactFormatError("artifact paths must share one artifact root")
    return parents.pop()


def _canonical_artifact_path(raw_path: str, expected_name: str) -> Path:
    path = Path(raw_path)
    if path.name != expected_name:
        raise CliInputError(f"artifact path must end with {expected_name}")
    return path


def _response(
    status: str,
    run_id: str | None,
    artifacts: dict[str, str],
    result: object,
) -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "run_id": run_id,
        "artifacts": artifacts,
        "result": result,
    }


def _emit_error(exit_code: int, code: str, safe_message: str, exc: Exception) -> int:
    _log("error", f"{code}: {type(exc).__name__}")
    _emit(
        {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "run_id": None,
            "artifacts": {},
            "error": {"code": code, "message": safe_message},
        }
    )
    return exit_code


def _emit(payload: object) -> None:
    sys.stdout.write(canonical_json_bytes(payload).decode("utf-8") + "\n")


def _log(stage: str, message: str) -> None:
    print(f"[{stage}] {message}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
