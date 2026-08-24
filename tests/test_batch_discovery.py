import os
import subprocess
from pathlib import Path

import pytest

from frame_timing_agent import batch_discovery
from frame_timing_agent.batch_discovery import DiscoveryIssue, DiscoveryResult, discover_frame_directories


def make_frames(path: Path, *names: str) -> Path:
    path.mkdir(parents=True)
    for name in names or ("frame_000001.jpg",):
        (path / name).write_bytes(b"not decoded")
    return path


def make_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            pytest.skip("directory junctions are unavailable")
        return
    link.symlink_to(target, target_is_directory=True)


def remove_directory_link(link: Path) -> None:
    if os.name == "nt":
        link.rmdir()
    else:
        link.unlink()


def test_discovery_prefers_clean_frames_and_deduplicates(tmp_path):
    preferred = make_frames(tmp_path / "video_a" / "clean_frames")
    make_frames(tmp_path / "output" / "clean_frames")

    result = discover_frame_directories(explicit=[preferred], root=tmp_path)

    assert result.frame_dirs == (preferred.resolve(),)
    assert any(
        issue.path == (tmp_path / "output").resolve() and issue.code == "ignored_output" for issue in result.issues
    )


def test_discovery_accepts_direct_children_but_not_arbitrary_nested_directories(tmp_path):
    direct = make_frames(tmp_path / "video_a")
    nested = make_frames(tmp_path / "video_b" / "frames")

    result = discover_frame_directories(root=tmp_path)

    assert result.frame_dirs == (direct.resolve(),)
    assert all(issue.path != nested.resolve() for issue in result.issues)


def test_discovery_ignores_hidden_cache_artifact_and_output_candidates(tmp_path):
    hidden = make_frames(tmp_path / ".hidden" / "clean_frames")
    cache = make_frames(tmp_path / "cache" / "clean_frames")
    artifact = make_frames(tmp_path / "artifacts" / "clean_frames")
    output = make_frames(tmp_path / "output_frames" / "clean_frames")

    result = discover_frame_directories(root=tmp_path)

    assert result.frame_dirs == ()
    assert {(issue.path, issue.code) for issue in result.issues} == {
        (hidden.parent.resolve(), "ignored_hidden"),
        (cache.parent.resolve(), "ignored_cache"),
        (artifact.parent.resolve(), "ignored_artifact"),
        (output.parent.resolve(), "ignored_output"),
    }


def test_discovery_suppresses_parent_when_preferred_child_is_selected(tmp_path):
    parent = make_frames(tmp_path / "video_a")
    preferred = make_frames(tmp_path / "video_a" / "clean_frames")

    result = discover_frame_directories(root=tmp_path)

    assert result.frame_dirs == (preferred.resolve(),)
    assert any(issue.path == parent.resolve() and issue.code == "superseded_by_clean_frames" for issue in result.issues)


def test_nested_clean_frames_does_not_suppress_unrelated_ancestor_candidate(tmp_path):
    parent = make_frames(tmp_path / "video_a")
    nested = make_frames(tmp_path / "video_a" / "unrelated" / "clean_frames")

    result = discover_frame_directories(root=tmp_path)

    assert result.frame_dirs == (parent.resolve(), nested.resolve())


def test_recursive_root_is_not_itself_a_batch_item(tmp_path):
    (tmp_path / "frame_000001.jpg").write_bytes(b"not decoded")
    child = make_frames(tmp_path / "video_a")

    result = discover_frame_directories(root=tmp_path)

    assert result.frame_dirs == (child.resolve(),)


def test_discovery_prunes_ignored_directory_trees(tmp_path, monkeypatch):
    direct = make_frames(tmp_path / "video_a")
    ignored_root = tmp_path / "output_frames"
    make_frames(ignored_root / "deep" / "clean_frames")
    original_contains = batch_discovery._contains_supported_frames

    def reject_ignored_descendants(path: Path) -> bool:
        if path != ignored_root and path.is_relative_to(ignored_root):
            raise AssertionError("ignored directory tree was traversed")
        return original_contains(path)

    monkeypatch.setattr(batch_discovery, "_contains_supported_frames", reject_ignored_descendants)

    result = discover_frame_directories(root=tmp_path)

    assert result.frame_dirs == (direct.resolve(),)
    assert DiscoveryIssue(ignored_root.resolve(), "ignored_output") in result.issues


def test_discovery_does_not_follow_directory_links(tmp_path):
    external = make_frames(tmp_path.parent / f"{tmp_path.name}-external" / "clean_frames")
    link = tmp_path / "linked-video"
    make_directory_link(link, external.parent)
    try:
        result = discover_frame_directories(root=tmp_path)
    finally:
        remove_directory_link(link)

    assert result.frame_dirs == ()
    assert DiscoveryIssue(link.absolute(), "ignored_link") in result.issues


def test_discovery_ignores_supported_file_links(tmp_path, monkeypatch):
    candidate = make_frames(tmp_path / "video_a")
    frame = candidate / "frame_000001.jpg"
    monkeypatch.setattr(batch_discovery, "_is_path_link", lambda path: path == frame)

    result = discover_frame_directories(root=tmp_path)

    assert result.frame_dirs == ()


def test_discovery_suppresses_explicit_parent_when_preferred_child_is_selected(tmp_path):
    parent = make_frames(tmp_path / "video_a")
    preferred = make_frames(tmp_path / "video_a" / "clean_frames")

    result = discover_frame_directories(explicit=[parent], root=tmp_path)

    assert result.frame_dirs == (preferred.resolve(),)
    assert any(issue.path == parent.resolve() and issue.code == "superseded_by_clean_frames" for issue in result.issues)


def test_discovery_reports_invalid_explicit_paths(tmp_path):
    missing = tmp_path / "missing"
    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("x", encoding="utf-8")
    empty = tmp_path / "empty"
    empty.mkdir()

    result = discover_frame_directories(explicit=[missing, file_path, empty])

    assert result.frame_dirs == ()
    assert {(issue.path, issue.code) for issue in result.issues} == {
        (missing.resolve(), "invalid_missing"),
        (file_path.resolve(), "invalid_not_directory"),
        (empty.resolve(), "invalid_no_frames"),
    }


def test_discovery_canonicalizes_and_sorts_paths_stably(tmp_path):
    first = make_frames(tmp_path / "z-video")
    second = make_frames(tmp_path / "a-video")
    duplicate = tmp_path / "z-video" / ".." / "z-video"

    result = discover_frame_directories(explicit=[first, duplicate, second])

    assert isinstance(result, DiscoveryResult)
    assert result.frame_dirs == (second.resolve(), first.resolve())
    assert isinstance(result.frame_dirs, tuple)
    assert isinstance(result.issues, tuple)


def test_discovery_reports_invalid_root(tmp_path):
    missing = tmp_path / "missing-root"

    result = discover_frame_directories(root=missing)

    assert result.frame_dirs == ()
    assert result.issues == (DiscoveryIssue(missing.resolve(), "invalid_root_missing"),)


def test_discovery_reports_unreadable_candidate(tmp_path, monkeypatch):
    candidate = tmp_path / "video_a"
    candidate.mkdir()
    original_contains = batch_discovery._contains_supported_frames

    def fail_for_candidate(path: Path) -> bool:
        if path == candidate:
            raise PermissionError("access denied")
        return original_contains(path)

    monkeypatch.setattr(batch_discovery, "_contains_supported_frames", fail_for_candidate)

    result = discover_frame_directories(root=tmp_path)

    assert result.frame_dirs == ()
    assert DiscoveryIssue(candidate.resolve(), "unreadable_directory") in result.issues
