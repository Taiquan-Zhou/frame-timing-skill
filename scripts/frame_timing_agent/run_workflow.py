from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterator, TypeAlias, cast
import csv
import errno
import hashlib
import io
import json
import os
import shutil
import stat
import uuid

from frame_timing_agent.apply_frame_strategy import apply_strategy
from frame_timing_agent.auto_timing_agent import TimingAgentResult, run_timing_agent
from frame_timing_agent.frame_source import load_frame_records
from frame_timing_agent.strategy_execution_audit import audit_strategy_execution, write_execution_audit


SNAPSHOT_VERSION = 1
INPUT_SNAPSHOT_NAME = "input_snapshot.json"
EXPORT_TRANSACTION_NAME = ".output_frames.transaction.json"
OUTPUT_TRANSACTION_BACKUP_NAME = ".output_frames.transaction-backup"
AUDIT_TRANSACTION_STAGING_NAME = ".execution_audit.transaction-staging"
AUDIT_TRANSACTION_BACKUP_NAME = ".execution_audit.transaction-backup"
EXECUTION_AUDIT_NAMES = ("execution_audit.json", "execution_audit.md")
EXPORT_LOCK_NAME = ".output_frames.export.lock"
ProgressCallback = Callable[[int, str], None]
JsonDict: TypeAlias = dict[str, Any]


class StaleSourceError(ValueError):
    """Raised when analysis-bound input or strategy data changes before export."""


class ExportBusyError(RuntimeError):
    """Raised when another process is already exporting the same run."""


@dataclass(frozen=True)
class RunSettings:
    frame_dir: Path
    artifact_dir: Path
    fps: float = 30.0
    limit_first_n: int | None = None


def capture_input_snapshot(
    frame_dir: Path | str,
    fps: float,
    limit_first_n: int | None,
) -> JsonDict:
    resolved_frame_dir = Path(frame_dir).expanduser().resolve()
    records = load_frame_records(resolved_frame_dir, fps=fps, limit_first_n=limit_first_n)
    frames: list[JsonDict] = []
    for record in records:
        path = record.path.expanduser().resolve()
        frames.append(
            {
                "source_index": record.source_index,
                "output_index": record.output_index,
                "instance_id": record.instance_id,
                "timestamp_sec": record.timestamp_sec,
                "is_duplicate": record.is_duplicate,
                "path": str(path),
                "size": path.stat().st_size,
                "sha256": _file_sha256(path),
            }
        )
    return {
        "version": SNAPSHOT_VERSION,
        "frame_dir": str(resolved_frame_dir),
        "fps": float(fps),
        "limit_first_n": limit_first_n,
        "frames": frames,
    }


def write_input_snapshot(analysis_dir: Path | str, snapshot: JsonDict) -> Path:
    path = Path(analysis_dir) / INPUT_SNAPSHOT_NAME
    _write_json_atomic(path, snapshot)
    return path


def bind_strategy_snapshot(snapshot: JsonDict, strategy_path: Path | str) -> JsonDict:
    bound = dict(snapshot)
    bound["strategy_sha256"] = _file_sha256(Path(strategy_path))
    return bound


def load_bound_strategy(analysis_dir: Path | str) -> JsonDict:
    analysis_dir = Path(analysis_dir)
    snapshot = _read_snapshot(analysis_dir)
    expected_hash = snapshot.get("strategy_sha256")
    if not isinstance(expected_hash, str):
        raise ValueError("analysis snapshot is not bound to a strategy; run analysis again before exporting")
    strategy_path = analysis_dir / "strategy.json"
    try:
        payload = strategy_path.read_bytes()
    except FileNotFoundError as exc:
        raise ValueError(f"analysis strategy is missing: {strategy_path}") from exc
    if hashlib.sha256(payload).hexdigest() != expected_hash:
        raise ValueError("analysis strategy changed since analysis; run analysis again before exporting")
    try:
        strategy = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"analysis strategy is invalid: {strategy_path}: {exc}") from exc
    if not isinstance(strategy, dict):
        raise ValueError(f"analysis strategy is invalid: {strategy_path}: expected a JSON object")
    return cast(JsonDict, strategy)


def verify_input_snapshot(
    analysis_dir: Path | str,
    frame_dir: Path | str,
    fps: float,
    limit_first_n: int | None,
) -> bool:
    analysis_dir = Path(analysis_dir)
    expected = _read_snapshot(analysis_dir)
    strategy_sha256 = expected.pop("strategy_sha256", None)
    try:
        current = capture_input_snapshot(frame_dir, fps=fps, limit_first_n=limit_first_n)
        _validate_snapshot_source_paths(current, analysis_dir.parent)
    except (OSError, ValueError, KeyError) as exc:
        raise StaleSourceError("input frames changed since analysis; run analysis again before exporting") from exc
    if not _snapshots_match(expected, current):
        raise StaleSourceError("input frames changed since analysis; run analysis again before exporting")
    if strategy_sha256 is None:
        raise StaleSourceError("analysis snapshot is not bound to a strategy; run analysis again before exporting")
    strategy_path = analysis_dir / "strategy.json"
    if not strategy_path.is_file() or _file_sha256(strategy_path) != strategy_sha256:
        raise StaleSourceError("analysis strategy changed since analysis; run analysis again before exporting")
    return True


def _snapshots_match(expected: JsonDict, current: JsonDict) -> bool:
    normalized_current = dict(current)
    expected_frames = expected.get("frames")
    current_frames = current.get("frames")
    if isinstance(expected_frames, list) and isinstance(current_frames, list):
        normalized_frames: list[object] = []
        for index, current_frame in enumerate(current_frames):
            if not isinstance(current_frame, dict):
                normalized_frames.append(current_frame)
                continue
            normalized_frame = dict(current_frame)
            expected_frame = expected_frames[index] if index < len(expected_frames) else None
            if isinstance(expected_frame, dict):
                for additive_key in ("timestamp_sec", "is_duplicate"):
                    if additive_key not in expected_frame:
                        normalized_frame.pop(additive_key, None)
            normalized_frames.append(normalized_frame)
        normalized_current["frames"] = normalized_frames
    return normalized_current == expected


def verify_output_snapshot(analysis_dir: Path | str, output_dir: Path | str) -> None:
    snapshot = _read_snapshot(Path(analysis_dir))
    source_hashes = {int(item["source_index"]): str(item["sha256"]) for item in snapshot.get("frames", [])}
    output_dir = Path(output_dir)
    selected_path = output_dir / "selected_frames.txt"
    try:
        rows = csv.DictReader(io.StringIO(selected_path.read_text(encoding="utf-8")), delimiter="\t")
        for row in rows:
            source_index = int(row["source_index"])
            filename = row["path"]
            if Path(filename).name != filename:
                raise ValueError(f"invalid output frame filename: {filename}")
            expected_hash = source_hashes.get(source_index)
            if expected_hash is None:
                raise ValueError(f"output references source outside analysis snapshot: {source_index}")
            output_path = output_dir / filename
            if not output_path.is_file() or _file_sha256(output_path) != expected_hash:
                raise ValueError(f"output frame does not match analysis snapshot: {filename}")
    except (FileNotFoundError, KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, ValueError) and str(exc).startswith(("invalid output", "output ")):
            raise
        raise ValueError(f"invalid selected output manifest: {selected_path}: {exc}") from exc


def analyze_run(
    settings: RunSettings,
    progress_callback: ProgressCallback | None = None,
) -> TimingAgentResult:
    _validate_run_path_safety(settings)
    before = capture_input_snapshot(settings.frame_dir, settings.fps, settings.limit_first_n)
    _validate_snapshot_source_paths(before, settings.artifact_dir)
    result = run_timing_agent(
        frames=settings.frame_dir,
        artifact_dir=settings.artifact_dir,
        limit_first_n=settings.limit_first_n,
        mode="reconstruction_balanced",
        write=False,
        fps=settings.fps,
        progress_callback=progress_callback,
    )
    after = capture_input_snapshot(settings.frame_dir, settings.fps, settings.limit_first_n)
    _validate_snapshot_source_paths(after, settings.artifact_dir)
    if after != before:
        raise ValueError("input frames changed during analysis; run analysis again")
    write_input_snapshot(settings.artifact_dir / "analysis", bind_strategy_snapshot(after, result.strategy_path))
    return result


def export_run(
    settings: RunSettings,
    progress_callback: ProgressCallback | None = None,
) -> TimingAgentResult:
    _validate_run_path_safety(settings)
    with _export_lock(settings.artifact_dir):
        return _export_run_locked(settings, progress_callback)


def _export_run_locked(
    settings: RunSettings,
    progress_callback: ProgressCallback | None,
) -> TimingAgentResult:
    analysis_dir = settings.artifact_dir / "analysis"
    recover_pending_export(settings.artifact_dir)
    _report(progress_callback, 2, "正在校验分析快照")
    verify_input_snapshot(analysis_dir, settings.frame_dir, settings.fps, settings.limit_first_n)
    strategy = load_bound_strategy(analysis_dir)
    records = load_frame_records(settings.frame_dir, fps=settings.fps, limit_first_n=settings.limit_first_n)
    output_dir = settings.artifact_dir / "output_frames"
    staging_dir = settings.artifact_dir / f".output_frames.export-{uuid.uuid4().hex}"
    _report(progress_callback, 20, "正在生成 output_frames")
    try:
        applied = apply_strategy(
            records,
            strategy,
            staging_dir,
            progress_callback=_map_progress(progress_callback, 20, 88, "正在生成 output_frames"),
        )
        _report(progress_callback, 90, "正在校验输出结果")
        verify_input_snapshot(analysis_dir, settings.frame_dir, settings.fps, settings.limit_first_n)
        verify_output_snapshot(analysis_dir, staging_dir)
        audit = audit_strategy_execution(records, strategy, staging_dir, fps=settings.fps)
        if audit.get("status") != "ok":
            raise ValueError(f"output verification failed: {'; '.join(audit.get('errors', []))}")
        _validate_run_path_safety(settings)
        replace_verified_output(staging_dir, output_dir, analysis_dir, audit)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
    return TimingAgentResult(
        len(records),
        applied.output_count,
        settings.artifact_dir,
        analysis_dir / "strategy.json",
        output_dir,
    )


def _validate_run_path_safety(settings: RunSettings) -> None:
    frame_dir = settings.frame_dir.expanduser().resolve()
    artifact_dir = settings.artifact_dir.expanduser().resolve()
    if frame_dir == artifact_dir or frame_dir.is_relative_to(artifact_dir) or artifact_dir.is_relative_to(frame_dir):
        raise ValueError("artifact directory must not overlap the input frame directory")
    for child_name in ("analysis", "output_frames"):
        write_path = settings.artifact_dir / child_name
        resolved_write_path = write_path.expanduser().resolve()
        if not resolved_write_path.is_relative_to(artifact_dir):
            raise ValueError("artifact write path must stay inside the artifact directory")
        if (
            resolved_write_path == frame_dir
            or resolved_write_path.is_relative_to(frame_dir)
            or frame_dir.is_relative_to(resolved_write_path)
        ):
            raise ValueError("artifact write path must not overlap the input frame directory")


def _validate_snapshot_source_paths(snapshot: JsonDict, artifact_dir: Path | str) -> None:
    resolved_artifact_dir = Path(artifact_dir).expanduser().resolve()
    frames = snapshot.get("frames")
    if not isinstance(frames, list):
        raise ValueError("input snapshot frames must be a list")
    for frame in frames:
        if not isinstance(frame, dict) or not isinstance(frame.get("path"), str):
            raise ValueError("input snapshot contains an invalid frame path")
        source_path = Path(frame["path"]).expanduser().resolve()
        if _paths_overlap(source_path, resolved_artifact_dir):
            raise ValueError("source frame path must not overlap the artifact directory")


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def replace_verified_output(staging_dir: Path, output_dir: Path, analysis_dir: Path, audit: JsonDict) -> None:
    artifact_dir = analysis_dir.parent
    recover_pending_export(artifact_dir)
    marker_path = artifact_dir / EXPORT_TRANSACTION_NAME
    output_backup_dir = artifact_dir / OUTPUT_TRANSACTION_BACKUP_NAME
    audit_staging_dir = artifact_dir / AUDIT_TRANSACTION_STAGING_NAME
    audit_backup_dir = analysis_dir / AUDIT_TRANSACTION_BACKUP_NAME
    had_output = output_dir.exists()
    had_audits = {name: (analysis_dir / name).exists() for name in EXECUTION_AUDIT_NAMES}

    transaction = {
        "version": 1,
        "phase": "prepared",
        "had_output": had_output,
        "had_audits": had_audits,
    }
    try:
        _remove_path(audit_staging_dir)
        write_execution_audit(audit, audit_staging_dir)
        _write_json_atomic(marker_path, transaction)
        if had_output:
            output_dir.rename(output_backup_dir)
        audit_backup_dir.mkdir()
        for name, existed in had_audits.items():
            if existed:
                (analysis_dir / name).rename(audit_backup_dir / name)
        staging_dir.rename(output_dir)
        for name in EXECUTION_AUDIT_NAMES:
            (audit_staging_dir / name).rename(analysis_dir / name)
        transaction["phase"] = "committed"
        _write_json_atomic(marker_path, transaction)
    except BaseException:
        if marker_path.exists():
            _recover_export_transaction(artifact_dir, transaction)
        else:
            _remove_path(audit_staging_dir)
        raise
    else:
        _recover_export_transaction(artifact_dir, transaction)


def recover_pending_export(artifact_dir: Path | str) -> None:
    artifact_dir = Path(artifact_dir)
    marker_path = artifact_dir / EXPORT_TRANSACTION_NAME
    if not marker_path.exists():
        return
    try:
        transaction = json.loads(marker_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid pending export transaction: {marker_path}") from exc
    if not isinstance(transaction, dict):
        raise ValueError(f"invalid pending export transaction: {marker_path}")
    _recover_export_transaction(artifact_dir, cast(JsonDict, transaction))


def _recover_export_transaction(artifact_dir: Path, transaction: JsonDict) -> None:
    marker_path = artifact_dir / EXPORT_TRANSACTION_NAME
    output_dir = artifact_dir / "output_frames"
    output_backup_dir = artifact_dir / OUTPUT_TRANSACTION_BACKUP_NAME
    analysis_dir = artifact_dir / "analysis"
    audit_staging_dir = artifact_dir / AUDIT_TRANSACTION_STAGING_NAME
    audit_backup_dir = analysis_dir / AUDIT_TRANSACTION_BACKUP_NAME
    version = transaction.get("version")
    phase = transaction.get("phase")
    had_output = transaction.get("had_output")
    had_audits = transaction.get("had_audits")
    if (
        version != 1
        or phase not in {"prepared", "committed"}
        or not isinstance(had_output, bool)
        or not isinstance(had_audits, dict)
    ):
        raise ValueError(f"invalid pending export transaction: {marker_path}")
    if set(had_audits) != set(EXECUTION_AUDIT_NAMES) or not all(
        isinstance(value, bool) for value in had_audits.values()
    ):
        raise ValueError(f"invalid pending export transaction: {marker_path}")

    if phase == "prepared":
        for name in EXECUTION_AUDIT_NAMES:
            target = analysis_dir / name
            backup = audit_backup_dir / name
            if had_audits[name]:
                if backup.exists():
                    _remove_path(target)
                    backup.rename(target)
            else:
                _remove_path(target)
        if had_output:
            if output_backup_dir.exists():
                _remove_path(output_dir)
                output_backup_dir.rename(output_dir)
        else:
            _remove_path(output_dir)

    _remove_path(output_backup_dir)
    _remove_path(audit_backup_dir)
    _remove_path(audit_staging_dir)
    marker_path.unlink(missing_ok=True)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


@contextmanager
def _export_lock(artifact_dir: Path) -> Iterator[None]:
    lock_path = artifact_dir / EXPORT_LOCK_NAME
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = _open_export_lock_file(lock_path)
    try:
        _acquire_export_lock(lock_file)
    except BlockingIOError as error:
        lock_file.close()
        raise ExportBusyError("export is already running") from error
    except BaseException:
        lock_file.close()
        raise
    try:
        yield
    finally:
        try:
            _release_export_lock(lock_file)
        finally:
            lock_file.close()


def _open_export_lock_file(lock_path: Path) -> BinaryIO:
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise ValueError("export lock file is unsafe") from error
    try:
        path_stat = lock_path.lstat()
        file_stat = os.fstat(descriptor)
        reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        unsafe = (
            not stat.S_ISREG(path_stat.st_mode)
            or path_stat.st_nlink != 1
            or (reparse_flag and getattr(path_stat, "st_file_attributes", 0) & reparse_flag)
            or (path_stat.st_dev, path_stat.st_ino) != (file_stat.st_dev, file_stat.st_ino)
        )
        if unsafe:
            raise ValueError("export lock file is unsafe")
        return os.fdopen(descriptor, "r+b")
    except BaseException:
        os.close(descriptor)
        raise


def _acquire_export_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"L")
        lock_file.flush()
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                raise BlockingIOError from error
            raise
        return
    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]


def _release_export_lock(lock_file: BinaryIO) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _replace_output_directory(
    staging_dir: Path,
    output_dir: Path,
    commit_metadata: Callable[[], None] | None = None,
) -> None:
    backup_dir = output_dir.with_name(f".{output_dir.name}.backup-{uuid.uuid4().hex}")
    if output_dir.exists():
        output_dir.rename(backup_dir)
    try:
        staging_dir.rename(output_dir)
        if commit_metadata is not None:
            commit_metadata()
    except BaseException:
        _restore_output_backup(output_dir, backup_dir)
        raise
    if backup_dir.exists():
        shutil.rmtree(backup_dir, ignore_errors=True)


def _restore_output_backup(output_dir: Path, backup_dir: Path) -> None:
    failed_dir = output_dir.with_name(f".{output_dir.name}.failed-{uuid.uuid4().hex}")
    had_previous_output = backup_dir.exists()
    moved_failed_output = False
    if output_dir.exists():
        output_dir.rename(failed_dir)
        moved_failed_output = True
    try:
        if backup_dir.exists():
            backup_dir.rename(output_dir)
    except BaseException:
        if moved_failed_output and failed_dir.exists() and not output_dir.exists():
            failed_dir.rename(output_dir)
        raise
    finally:
        if moved_failed_output and (not had_previous_output or output_dir.exists()):
            shutil.rmtree(failed_dir, ignore_errors=True)


def _replace_execution_audit(staging_dir: Path, analysis_dir: Path) -> None:
    names = EXECUTION_AUDIT_NAMES
    backup_dir = analysis_dir / f".execution_audit.backup-{uuid.uuid4().hex}"
    backup_dir.mkdir()
    try:
        for name in names:
            target = analysis_dir / name
            if target.exists():
                target.rename(backup_dir / name)
        for name in names:
            (staging_dir / name).rename(analysis_dir / name)
    except BaseException:
        failed_dir = analysis_dir / f".execution_audit.failed-{uuid.uuid4().hex}"
        failed_dir.mkdir()
        recovery_error: Exception | None = None
        for name in names:
            target = analysis_dir / name
            backup = backup_dir / name
            if target.exists():
                try:
                    target.rename(failed_dir / name)
                except Exception as error:
                    recovery_error = recovery_error or error
            if backup.exists():
                try:
                    backup.rename(target)
                except Exception as error:
                    recovery_error = recovery_error or error
        if recovery_error is not None:
            raise recovery_error
        shutil.rmtree(failed_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise
    else:
        shutil.rmtree(backup_dir, ignore_errors=True)


def _report(callback: ProgressCallback | None, percent: int, message: str) -> None:
    if callback is not None:
        callback(max(0, min(98, percent)), message)


def _map_progress(
    callback: ProgressCallback | None,
    start: int,
    end: int,
    message: str,
) -> Callable[[int, int], None] | None:
    if callback is None:
        return None

    def report(completed: int, total: int) -> None:
        ratio = completed / max(1, total)
        _report(callback, start + round((end - start) * ratio), message)

    return report


def _write_json_atomic(path: Path, payload: JsonDict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_snapshot(analysis_dir: Path) -> JsonDict:
    path = analysis_dir / INPUT_SNAPSHOT_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"analysis input snapshot is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"analysis input snapshot is invalid: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"analysis input snapshot is invalid: {path}")
    return cast(JsonDict, payload)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
