from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Never

from frame_timing_agent.artifact_io import (
    ArtifactFormatError,
    read_analysis_result,
    read_execution_result,
    read_strategy_candidate,
    read_validation_result,
)
from frame_timing_agent.configuration import parse_strategy_request
from frame_timing_agent.contracts import SCHEMA_VERSION, ConfigurationError, PolicyName
from frame_timing_agent.serialization import canonical_json_bytes
from frame_timing_agent.service import (
    ANALYSIS_ARTIFACT,
    EXECUTION_ARTIFACT,
    OUTPUT_DIRECTORY,
    STRATEGY_ARTIFACT,
    VALIDATION_ARTIFACT,
    analyze_frames,
    apply_validated_strategy,
    capabilities,
    plan_strategy,
    validate_strategy,
    verify_output,
)

EXIT_SUCCESS = 0
EXIT_INPUT_ERROR = 2
EXIT_UNSAFE_STRATEGY = 3
EXIT_EXECUTION_FAILED = 4
EXIT_HEALTH_FAILED = 5


class CliInputError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise CliInputError(message)


def main(argv: Sequence[str] | None = None) -> int:
    command: str | None = None
    try:
        args = _parser().parse_args(list(argv) if argv is not None else None)
        command = str(args.command)
        exit_code, payload = _dispatch(args)
    except CliInputError as exc:
        return _emit_error(EXIT_INPUT_ERROR, "input_error", "command arguments are invalid", exc)
    except ArtifactFormatError as exc:
        return _emit_error(EXIT_INPUT_ERROR, "invalid_artifact", "artifact JSON is invalid", exc)
    except (ConfigurationError, FileNotFoundError) as exc:
        return _emit_error(EXIT_INPUT_ERROR, "input_error", "command input is invalid", exc)
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
        analysis = read_analysis_result(artifact_root / ANALYSIS_ARTIFACT)
        candidate = read_strategy_candidate(artifact_root / STRATEGY_ARTIFACT)
        execution = read_execution_result(artifact_root / EXECUTION_ARTIFACT)
        health = verify_output(Path(args.frames), analysis, candidate, execution, artifact_root / OUTPUT_DIRECTORY)
        status = "ok" if health.valid else "failed"
        _log("verify", status)
        return (EXIT_SUCCESS if health.valid else EXIT_HEALTH_FAILED), _response(
            status,
            analysis.run_id,
            {
                "analysis": ANALYSIS_ARTIFACT,
                "strategy": STRATEGY_ARTIFACT,
                "execution": EXECUTION_ARTIFACT,
                "output_frames": OUTPUT_DIRECTORY,
            },
            health,
        )
    raise CliInputError("unsupported command")


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
