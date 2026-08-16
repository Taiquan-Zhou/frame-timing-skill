from pathlib import Path

from frame_timing_agent.batch_discovery import DiscoveryIssue, DiscoveryResult, discover_frame_directories


def make_frames(path: Path, *names: str) -> Path:
    path.mkdir(parents=True)
    for name in names or ("frame_000001.jpg",):
        (path / name).write_bytes(b"not decoded")
    return path


def test_discovery_prefers_clean_frames_and_deduplicates(tmp_path):
    preferred = make_frames(tmp_path / "video_a" / "clean_frames")
    ignored = make_frames(tmp_path / "output" / "clean_frames")

    result = discover_frame_directories(explicit=[preferred], root=tmp_path)

    assert result.frame_dirs == (preferred.resolve(),)
    assert any(issue.path == ignored.resolve() and issue.code == "ignored_output" for issue in result.issues)


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
        (hidden.resolve(), "ignored_hidden"),
        (cache.resolve(), "ignored_cache"),
        (artifact.resolve(), "ignored_artifact"),
        (output.resolve(), "ignored_output"),
    }


def test_discovery_suppresses_parent_when_preferred_child_is_selected(tmp_path):
    parent = make_frames(tmp_path / "video_a")
    preferred = make_frames(tmp_path / "video_a" / "clean_frames")

    result = discover_frame_directories(root=tmp_path)

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
