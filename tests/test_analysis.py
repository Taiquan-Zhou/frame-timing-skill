from __future__ import annotations

import json
import math
from dataclasses import FrozenInstanceError, asdict
from pathlib import Path

import cv2
import numpy as np
import pytest
from fixtures.generate_motion_sequences import generate_motion_sequence
from frame_timing_agent.analysis import analyze_records
from frame_timing_agent.contracts import AnalysisError, AnalysisResult
from frame_timing_agent.frame_source import FrameRecord
from frame_timing_agent.motion_model import MotionConfig, estimate_camera_motion


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


def test_initial_frame_does_not_inflate_motion_confidence(tmp_path: Path) -> None:
    records = generate_motion_sequence(
        tmp_path / "confidence",
        [(float(index), 0.0, 0.0) for index in range(6)],
        low_texture=True,
    )
    samples = estimate_camera_motion(records, _config())

    result = analyze_records(records, fps=30.0, motion_config=_config())

    expected = float(np.mean([sample.confidence for sample in samples if sample.fallback_reason != "initial_frame"]))
    assert result.motion_confidence == pytest.approx(expected)
    assert result.trajectory_summary.mean_confidence == pytest.approx(expected)


def test_fallback_analysis_contains_only_strict_json_numbers(tmp_path: Path) -> None:
    records = generate_motion_sequence(
        tmp_path / "strict_json_fallback",
        [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
        low_texture=True,
    )

    result = analyze_records(records, fps=30.0, motion_config=_config())

    assert result.trajectory_summary.fallback_count == 1
    assert all(
        math.isfinite(value)
        for frame in result.frames
        for value in (frame.normalized_residual_spatial_iqr, frame.normalized_residual_spatial_p90)
    )
    json.dumps(asdict(result), allow_nan=False)


def test_analyze_records_decisions_are_repeatable_end_to_end(tmp_path: Path) -> None:
    positions = [index * 0.5 + 2.5 * np.sin(index * np.pi / 2) for index in range(48)]
    records = generate_motion_sequence(
        tmp_path / "repeatable_analysis",
        [(float(position), 0.0, 0.0) for position in positions],
    )

    runs = [analyze_records(records, fps=30.0, motion_config=_config()) for _ in range(20)]

    expected = [(item.start, item.end, item.kind, item.reason) for item in runs[0].ranges]
    assert all([(item.start, item.end, item.kind, item.reason) for item in run.ranges] == expected for run in runs[1:])
    assert all(run.input_digest == runs[0].input_digest for run in runs[1:])
    assert any(item.kind == "jitter" for item in runs[0].ranges)
