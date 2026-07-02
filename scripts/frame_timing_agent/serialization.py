from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path, PureWindowsPath


def canonical_json_bytes(payload: object) -> bytes:
    normalized = _normalize(payload)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_digest(payload: object) -> str:
    digest = hashlib.sha256(canonical_json_bytes(payload)).hexdigest()
    return f"sha256:{digest}"


def write_canonical_json_atomic(path: Path | str, payload: object) -> None:
    destination = Path(path)
    encoded = canonical_json_bytes(payload)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(destination)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _normalize(value: object) -> object:
    if isinstance(value, Enum):
        return _normalize(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite floats are not supported")
        return value
    if isinstance(value, Path):
        return _normalize_path(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _normalize(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON mapping keys must be strings")
            normalized[key] = _normalize(item)
        return normalized
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def _normalize_path(path: Path) -> str:
    raw = str(path)
    windows_path = PureWindowsPath(raw)
    if path.anchor or windows_path.anchor:
        raise ValueError("canonical JSON paths must be relative")
    if ".." in path.parts or ".." in windows_path.parts:
        raise ValueError("canonical JSON paths must not escape their artifact root")
    return path.as_posix()
