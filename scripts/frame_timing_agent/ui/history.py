from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import json
import os
import uuid


HISTORY_VERSION = 1


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    created_at: str
    updated_at: str
    frame_dir: Path
    artifact_dir: Path
    fps: float
    analyzed_count: int
    estimated_output_count: int
    output_count: int | None
    output_dir: Path | None
    status: str
    strategy_name: str

    def to_dict(self) -> dict:
        data = asdict(self)
        data["frame_dir"] = str(self.frame_dir.expanduser().resolve())
        data["artifact_dir"] = str(self.artifact_dir.expanduser().resolve())
        data["output_dir"] = str(self.output_dir.expanduser().resolve()) if self.output_dir is not None else None
        return data

    @classmethod
    def from_dict(cls, data: dict) -> RunRecord:
        return cls(
            run_id=str(data["run_id"]),
            created_at=str(data["created_at"]),
            updated_at=str(data["updated_at"]),
            frame_dir=Path(data["frame_dir"]).expanduser().resolve(),
            artifact_dir=Path(data["artifact_dir"]).expanduser().resolve(),
            fps=float(data["fps"]),
            analyzed_count=int(data["analyzed_count"]),
            estimated_output_count=int(data["estimated_output_count"]),
            output_count=int(data["output_count"]) if data.get("output_count") is not None else None,
            output_dir=Path(data["output_dir"]).expanduser().resolve() if data.get("output_dir") else None,
            status=str(data["status"]),
            strategy_name=str(data["strategy_name"]),
        )


class RunHistoryStore:
    def __init__(self, path: Path | str, max_records: int = 100):
        self.path = Path(path)
        self.max_records = max_records

    def list_records(self) -> list[RunRecord]:
        records = []
        for item in self._read_runs():
            try:
                records.append(RunRecord.from_dict(item))
            except (KeyError, TypeError, ValueError):
                # Keep malformed entries on disk, but do not let one entry hide valid history.
                continue
        return sorted(records, key=lambda record: record.updated_at, reverse=True)

    def upsert(self, record: RunRecord) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from PySide6.QtCore import QLockFile

        lock = QLockFile(f"{self.path}.lock")
        lock.setStaleLockTime(30_000)
        if not lock.tryLock(5_000):
            raise OSError(f"cannot lock run history: {self.path}")
        try:
            valid_records: list[RunRecord] = []
            malformed_items: list[object] = []
            for item in self._read_runs():
                try:
                    parsed = RunRecord.from_dict(item)
                except (KeyError, TypeError, ValueError):
                    malformed_items.append(item)
                    continue
                if parsed.run_id != record.run_id:
                    valid_records.append(parsed)
            valid_records.append(record)
            valid_records.sort(key=lambda item: item.updated_at, reverse=True)
            valid_records = valid_records[: self.max_records]
            payload = {
                "version": HISTORY_VERSION,
                "runs": [item.to_dict() for item in valid_records] + malformed_items,
            }
            self._write_payload(payload)
        finally:
            lock.unlock()

    def delete(self, run_id: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        from PySide6.QtCore import QLockFile

        lock = QLockFile(f"{self.path}.lock")
        lock.setStaleLockTime(30_000)
        if not lock.tryLock(5_000):
            raise OSError(f"cannot lock run history: {self.path}")
        try:
            remaining = [
                item
                for item in self._read_runs()
                if not isinstance(item, dict) or str(item.get("run_id", "")) != run_id
            ]
            self._write_payload({"version": HISTORY_VERSION, "runs": remaining})
        finally:
            lock.unlock()

    def _write_payload(self, payload: dict) -> None:
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def _read_runs(self) -> list[object]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("version") != HISTORY_VERSION:
                raise ValueError("unsupported history format")
            runs = payload.get("runs")
            if not isinstance(runs, list):
                raise ValueError("history runs must be a list")
            return runs
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read run history: {self.path}: {exc}") from exc


def default_history_path() -> Path:
    from PySide6.QtCore import QStandardPaths

    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    if not base:
        base = str(Path.home() / ".frame-timing-skill")
    return Path(base) / "run_history.json"
