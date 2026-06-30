from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import cv2
import numpy as np
import pytest

from fixtures.generate_motion_sequences import generate_motion_sequence
from frame_timing_agent.analysis import analyze_records
from frame_timing_agent.contracts import AnalysisError, AnalysisResult
from frame_timing_agent.frame_source import FrameRecord
from frame_timing_agent.motion_model import MotionConfig


def _config() -> MotionConfig:
    return MotionConfig(
        analysis_width=192,
        max_features=240,
        forward_backward_error=1.0,
        ransac_reprojection_threshold=2.0,
        minimum_inlier_ratio=0.45,
        smoothing_windows_seconds=(0.15, 0.35, 0.75),
        decision_deadband_ratio=0.10,
    )


def test_analyze_records_returns_sorted_frozen_result_without_writing_files(tmp_path: Path) -> None:
    records = generate_motion_sequence(
        tmp_path / "frames",
        [(index * 0.8, 0.0, 0.0) for index in range(40)],
    )
    before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    result = analyze_records(list(reversed(records)), fps=30.0, motion_config=_config())

    after = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
    assert isinstance(result, AnalysisResult)
    assert result.schema_version == 3
    assert result.frame_count == 40
    assert [frame.source_index for frame in result.frames] == list(range(40))
    assert result.input_digest
    assert result.run_id == result.input_digest[:16]
    assert before == after
    with pytest.raises(FrozenInstanceError):
        result.frame_count = 1


def test_analyze_records_rejects_duplicate_sources_with_stable_code(tmp_path: Path) -> None:
    records = generate_motion_sequence(tmp_path / "duplicate", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    duplicate = FrameRecord(
        source_index=records[0].source_index,
        output_index=2,
        timestamp_sec=records[0].timestamp_sec,
        path=records[1].path,
    )

    with pytest.raises(AnalysisError) as captured:
        analyze_records([records[0], duplicate], fps=30.0, motion_config=_config())

    assert captured.value.code == "duplicate_source_index"
    assert captured.value.fields == ("records",)


@pytest.mark.parametrize("fps", [0.0, -1.0, float("nan"), float("inf")])
def test_analyze_records_rejects_invalid_fps(tmp_path: Path, fps: float) -> None:
    records = generate_motion_sequence(tmp_path / "invalid_fps", [(0.0, 0.0, 0.0)])

    with pytest.raises(AnalysisError) as captured:
        analyze_records(records, fps=fps, motion_config=_config())

    assert captured.value.code == "invalid_fps"


def test_analyze_records_rejects_inconsistent_dimensions(tmp_path: Path) -> None:
    records = generate_motion_sequence(tmp_path / "dimensions", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)])
    replacement = np.full((64, 64, 3), 120, dtype=np.uint8)
    assert cv2.imwrite(str(records[1].path), replacement)

    with pytest.raises(AnalysisError) as captured:
        analyze_records(records, fps=30.0, motion_config=_config())

    assert captured.value.code == "inconsistent_frame_dimensions"


def test_spatial_uncertainty_is_exposed_as_review_range(tmp_path: Path) -> None:
    records = generate_motion_sequence(
        tmp_path / "uncertain",
        [(float(index), 0.0, 0.0) for index in range(40)],
        foreground_transform=lambda index: (index * 10.0, 0.0, 0.0),
    )

    result = analyze_records(records, fps=30.0, motion_config=_config())

    assert any(item.kind == "review_required" for item in result.ranges)
    assert result.trajectory_summary.spatial_uncertainty_count > 0
