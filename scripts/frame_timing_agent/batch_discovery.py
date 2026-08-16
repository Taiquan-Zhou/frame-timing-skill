from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from frame_timing_agent.frame_source import SUPPORTED_EXTENSIONS


@dataclass(frozen=True)
class DiscoveryIssue:
    path: Path
    code: str


@dataclass(frozen=True)
class DiscoveryResult:
    frame_dirs: tuple[Path, ...]
    issues: tuple[DiscoveryIssue, ...]


_CLEAN_FRAMES_NAME = "clean_frames"
_OUTPUT_NAMES = {".output", ".output_frames", "output", "output_frames"}
_CACHE_NAMES = {".cache", "__pycache__", "cache", "caches"}
_ARTIFACT_NAMES = {".artifact", ".artifacts", "artifact", "artifacts"}


def discover_frame_directories(
    explicit: Iterable[Path | str] = (),
    root: Path | str | None = None,
) -> DiscoveryResult:
    """Find cleaned frame directories using directory metadata only."""
    frame_dirs: set[Path] = set()
    issues: list[DiscoveryIssue] = []

    for raw_path in explicit:
        path = _canonical_path(raw_path)
        if not path.exists():
            issues.append(DiscoveryIssue(path, "invalid_missing"))
        elif not path.is_dir():
            issues.append(DiscoveryIssue(path, "invalid_not_directory"))
        elif not _contains_supported_frames(path):
            issues.append(DiscoveryIssue(path, "invalid_no_frames"))
        else:
            frame_dirs.add(path)

    if root is not None:
        root_path = _canonical_path(root)
        if not root_path.exists():
            issues.append(DiscoveryIssue(root_path, "invalid_root_missing"))
        elif not root_path.is_dir():
            issues.append(DiscoveryIssue(root_path, "invalid_root_not_directory"))
        else:
            _discover_from_root(root_path, frame_dirs, issues)

    return DiscoveryResult(
        frame_dirs=tuple(sorted(frame_dirs, key=_sort_key)),
        issues=tuple(sorted(issues, key=lambda issue: (_sort_key(issue.path), issue.code))),
    )


def _discover_from_root(root: Path, frame_dirs: set[Path], issues: list[DiscoveryIssue]) -> None:
    candidates = _root_candidates(root)
    explicit_paths = set(frame_dirs)
    accepted_candidates: list[Path] = []

    for candidate in candidates:
        if candidate in explicit_paths:
            continue
        ignored_code = _ignored_code(candidate.relative_to(root).parts)
        if ignored_code is not None:
            issues.append(DiscoveryIssue(candidate, ignored_code))
        else:
            accepted_candidates.append(candidate)

    preferred = {path for path in accepted_candidates if path.name.casefold() == _CLEAN_FRAMES_NAME}
    for candidate in accepted_candidates:
        if candidate.name.casefold() != _CLEAN_FRAMES_NAME and any(
            preferred_path != candidate and preferred_path.is_relative_to(candidate)
            for preferred_path in preferred
        ):
            issues.append(DiscoveryIssue(candidate, "superseded_by_clean_frames"))
            continue
        frame_dirs.add(candidate)


def _root_candidates(root: Path) -> list[Path]:
    candidates: set[Path] = set()
    if _contains_supported_frames(root):
        candidates.add(root)

    for path in root.rglob("*"):
        if not path.is_dir() or not _contains_supported_frames(path):
            continue
        if path.name.casefold() == _CLEAN_FRAMES_NAME or path.parent == root:
            candidates.add(_canonical_path(path))
    return sorted(candidates, key=_sort_key)


def _contains_supported_frames(path: Path) -> bool:
    try:
        return any(
            entry.is_file() and entry.suffix.casefold() in SUPPORTED_EXTENSIONS
            for entry in path.iterdir()
        )
    except OSError:
        return False


def _ignored_code(parts: tuple[str, ...]) -> str | None:
    for part in parts:
        normalized = part.casefold()
        if normalized in _OUTPUT_NAMES:
            return "ignored_output"
        if normalized in _ARTIFACT_NAMES:
            return "ignored_artifact"
        if normalized in _CACHE_NAMES:
            return "ignored_cache"
        if part.startswith("."):
            return "ignored_hidden"
    return None


def _canonical_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve()


def _sort_key(path: Path) -> str:
    return os.path.normcase(str(path))
