import json

import cv2
import numpy as np
import pytest

from frame_timing_agent.run_workflow import RunSettings, analyze_run, export_run


def _write_frame(path, value: int) -> None:
    image = np.full((16, 16, 3), value, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write test frame: {path}")


def make_settings_with_frames(tmp_path) -> RunSettings:
    frame_dir = tmp_path / "frames"
    frame_dir.mkdir()
    for index in range(6):
        _write_frame(frame_dir / f"frame_{index:06d}_src_{index:06d}.jpg", 80 + index)
    return RunSettings(frame_dir=frame_dir, artifact_dir=tmp_path / "run", fps=24.0, limit_first_n=None)


def make_analyzed_settings(tmp_path) -> RunSettings:
    settings = make_settings_with_frames(tmp_path)
    analyze_run(settings)
    return settings


def mutate_first_source_frame(frame_dir) -> None:
    _write_frame(frame_dir / "frame_000000_src_000000.jpg", 255)


def test_analyze_run_binds_source_and_strategy(tmp_path):
    settings = make_settings_with_frames(tmp_path)

    result = analyze_run(settings)

    snapshot = json.loads((result.artifact_dir / "analysis" / "input_snapshot.json").read_text())
    assert snapshot["strategy_sha256"]


def test_export_run_keeps_previous_output_when_source_changed(tmp_path):
    settings = make_analyzed_settings(tmp_path)
    existing = settings.artifact_dir / "output_frames" / "sentinel.txt"
    existing.parent.mkdir()
    existing.write_text("keep", encoding="utf-8")
    mutate_first_source_frame(settings.frame_dir)

    with pytest.raises(ValueError, match="input frames changed"):
        export_run(settings)

    assert existing.read_text(encoding="utf-8") == "keep"
