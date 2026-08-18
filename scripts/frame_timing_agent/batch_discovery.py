from __future__ import annotations

import os
import stat
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
        else:
            try:
                has_frames = _contains_supported_frames(path)
            except OSError:
                issues.append(DiscoveryIssue(path, "invalid_unreadable"))
            else:
                if has_frames:
                    frame_dirs.add(path)
                else:
                    issues.append(DiscoveryIssue(path, "invalid_no_frames"))

    if root is not None:
        root_path = _canonical_path(root)
        if not root_path.exists():
            issues.append(DiscoveryIssue(root_path, "invalid_root_missing"))
        elif not root_path.is_dir():
            issues.append(DiscoveryIssue(root_path, "invalid_root_not_directory"))
        else:
            _discover_from_root(root_path, frame_dirs, issues)
    _apply_preferred_child_precedence(frame_dirs, issues)

    return DiscoveryResult(
        frame_dirs=tuple(sorted(frame_dirs, key=_sort_key)),
        issues=tuple(sorted(issues, key=lambda issue: (_sort_key(issue.path), issue.code))),
    )


def _discover_from_root(root: Path, frame_dirs: set[Path], issues: list[DiscoveryIssue]) -> None:
    candidates = _root_candidates(root, issues)
    explicit_paths = set(frame_dirs)

    for candidate in candidates:
        if candidate in explicit_paths:
            continue
        ignored_code = _ignored_code(candidate.relative_to(root).parts)
        if ignored_code is not None:
            issues.append(DiscoveryIssue(candidate, ignored_code))
        else:
            frame_dirs.add(candidate)


def _apply_preferred_child_precedence(frame_dirs: set[Path], issues: list[DiscoveryIssue]) -> None:
    preferred = {path for path in frame_dirs if path.name.casefold() == _CLEAN_FRAMES_NAME}
    superseded = {
        candidate
        for candidate in frame_dirs
        if candidate.name.casefold() != _CLEAN_FRAMES_NAME
        and any(preferred_path.parent == candidate for preferred_path in preferred)
    }
    for candidate in superseded:
        frame_dirs.remove(candidate)
        issues.append(DiscoveryIssue(candidate, "superseded_by_clean_frames"))


def _root_candidates(root: Path, issues: list[DiscoveryIssue]) -> list[Path]:
    candidates: set[Path] = set()

    def record_walk_error(error: OSError) -> None:
        if error.filename:
            issues.append(DiscoveryIssue(Path(error.filename).absolute(), "unreadable_directory"))

    for current_raw, directory_names, _file_names in os.walk(root, topdown=True, onerror=record_walk_error):
        current = Path(current_raw)
        retained_names: list[str] = []
        for directory_name in directory_names:
            child = current / directory_name
            ignored_code = _ignored_code(child.relative_to(root).parts)
            if ignored_code is not None:
                issues.append(DiscoveryIssue(child, ignored_code))
            elif _is_directory_link(child):
                issues.append(DiscoveryIssue(child, "ignored_link"))
            else:
                retained_names.append(directory_name)
        directory_names[:] = retained_names

        if current == root:
            continue
        if current.name.casefold() == _CLEAN_FRAMES_NAME or current.parent == root:
            try:
                has_frames = _contains_supported_frames(current)
            except OSError:
                issues.append(DiscoveryIssue(_canonical_path(current), "unreadable_directory"))
            else:
                if has_frames:
                    candidates.add(_canonical_path(current))
    return sorted(candidates, key=_sort_key)


def _is_directory_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except AttributeError:
        return False
    except OSError:
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _contains_supported_frames(path: Path) -> bool:
    return any(
        entry.is_file() and entry.suffix.casefold() in SUPPORTED_EXTENSIONS
        for entry in path.iterdir()
    )


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
