from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np
import pytest

from fixtures.generate_motion_sequences import generate_motion_sequence
from frame_timing_agent.motion_model import (
    MotionConfig,
    MotionSample,
    estimate_camera_motion,
)
from frame_timing_agent.trajectory_model import (
    _repair_low_confidence_trajectory,
    _weighted_moving_average,
    decompose_camera_trajectory,
)


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


def test_estimates_known_translation_without_changing_opencv_threads(tmp_path: Path) -> None:
    records = generate_motion_sequence(
        tmp_path / "translation",
        [(index * 1.5, index * -0.5, 0.0) for index in range(12)],
    )
    threads_before = cv2.getNumThreads()

    samples = estimate_camera_motion(records, _config())

    assert cv2.getNumThreads() == threads_before
    assert len(samples) == len(records)
    assert samples[0].fallback_reason == "initial_frame"
    assert samples[0].confidence == 0.0
    for sample in samples[2:]:
        assert sample.dx == pytest.approx(1.5, abs=0.5)
        assert sample.dy == pytest.approx(-0.5, abs=0.5)
        assert sample.confidence >= 0.45


def test_estimates_known_rotation(tmp_path: Path) -> None:
    records = generate_motion_sequence(
        tmp_path / "rotation",
        [(0.0, 0.0, index * 0.6) for index in range(12)],
    )

    samples = estimate_camera_motion(records, _config())

    for sample in samples[2:]:
        assert abs(sample.rotation_deg) == pytest.approx(0.6, abs=0.2)


def test_low_texture_uses_explicit_low_confidence_fallback(tmp_path: Path) -> None:
    records = generate_motion_sequence(
        tmp_path / "low_texture",
        [(float(index), 0.0, 0.0) for index in range(8)],
        low_texture=True,
    )

    samples = estimate_camera_motion(records, _config())

    assert all(sample.confidence < 0.45 for sample in samples[1:])
    assert all(sample.fallback_reason is not None for sample in samples[1:])


@pytest.mark.parametrize(
    "foreground_transform",
    [
        lambda index: (index * 10.0, 0.0, 0.0),
        lambda index: ((-1.0 if index % 2 else 1.0) * 8.0, 0.0, 0.0),
    ],
)
def test_spatially_inconsistent_motion_cannot_claim_high_confidence(
    tmp_path: Path,
    foreground_transform,
) -> None:
    records = generate_motion_sequence(
        tmp_path / f"spatial_{len(list(tmp_path.iterdir()))}",
        [(float(index), 0.0, 0.0) for index in range(12)],
        foreground_transform=foreground_transform,
    )

    samples = estimate_camera_motion(records, _config())

    uncertain = [
        sample
        for sample in samples[1:]
        if sample.normalized_residual_spatial_iqr
        > 2 * _config().ransac_reprojection_threshold / math.hypot(192, 128)
        or sample.normalized_residual_spatial_p90
        > 4 * _config().ransac_reprojection_threshold / math.hypot(192, 128)
        or sample.inlier_spatial_coverage < 0.25
    ]
    assert uncertain
    assert all(sample.confidence < 0.45 for sample in uncertain)


def test_foreground_parallax_has_more_residual_dispersion_than_pure_translation(tmp_path: Path) -> None:
    transforms = [(float(index), 0.0, 0.0) for index in range(12)]
    baseline = generate_motion_sequence(tmp_path / "baseline", transforms)
    parallax = generate_motion_sequence(
        tmp_path / "parallax",
        transforms,
        foreground_transform=lambda index: (index * 10.0, 0.0, 0.0),
    )

    baseline_samples = estimate_camera_motion(baseline, _config())
    parallax_samples = estimate_camera_motion(parallax, _config())

    baseline_p90 = np.median([sample.normalized_residual_spatial_p90 for sample in baseline_samples[1:]])
    parallax_p90 = np.median([sample.normalized_residual_spatial_p90 for sample in parallax_samples[1:]])
    assert parallax_p90 > baseline_p90 * 5


def _motion_samples(positions: list[float], *, rotation_positions: list[float] | None = None) -> tuple[MotionSample, ...]:
    rotations = rotation_positions or [0.0] * len(positions)
    samples: list[MotionSample] = []
    for index, position in enumerate(positions):
        previous_position = positions[index - 1] if index else position
        previous_rotation = rotations[index - 1] if index else rotations[index]
        dx = position - previous_position
        rotation = rotations[index] - previous_rotation
        samples.append(
            MotionSample(
                source_index=index,
                output_index=index,
                dx=dx,
                dy=0.0,
                rotation_deg=rotation,
                scale=1.0,
                magnitude_px=abs(dx),
                feature_count=120,
                inlier_ratio=0.95,
                reprojection_error=0.2,
                normalized_residual_spatial_iqr=0.0001,
                normalized_residual_spatial_p90=0.0002,
                inlier_spatial_coverage=0.75,
                response=0.95,
                confidence=0.95,
                fallback_reason="initial_frame" if index == 0 else None,
            )
        )
    return tuple(samples)


def test_uniform_translation_is_active_motion_not_jitter() -> None:
    positions = [index * 1.2 for index in range(90)]

    decisions = decompose_camera_trajectory(_motion_samples(positions), 30.0, math.hypot(192, 128), _config())

    assert len(decisions) == len(positions)
    assert not any(decision.kind == "jitter" for decision in decisions)
    assert sum(decision.kind == "active_motion" for decision in decisions) >= 70


def test_multiscale_oscillation_produces_high_confidence_jitter() -> None:
    positions = [index * 0.6 + 3.5 * math.sin(index * math.pi / 2) for index in range(120)]

    decisions = decompose_camera_trajectory(_motion_samples(positions), 30.0, math.hypot(192, 128), _config())

    jitter = [decision for decision in decisions[15:-15] if decision.kind == "jitter"]
    assert len(jitter) >= 30
    assert all(decision.jitter_confidence >= 0.45 for decision in jitter)
    assert all(decision.normalized_translation_residual > 0 for decision in jitter)


def test_fast_monotonic_turn_is_not_jitter() -> None:
    positions = [float(index) for index in range(90)]
    rotation_positions = [0.0] * 25 + [float(index - 24) * 1.8 for index in range(25, 90)]

    decisions = decompose_camera_trajectory(
        _motion_samples(positions, rotation_positions=rotation_positions),
        30.0,
        math.hypot(192, 128),
        _config(),
    )

    assert not any(decision.kind == "jitter" for decision in decisions)
    assert sum(decision.kind == "active_motion" for decision in decisions[25:]) >= 50


def test_multiscale_disagreement_requires_review_instead_of_jitter() -> None:
    positions = [index * 0.4 + 2.0 * math.sin(2 * math.pi * index / 10) for index in range(120)]

    decisions = decompose_camera_trajectory(_motion_samples(positions), 30.0, math.hypot(192, 128), _config())

    assert any(decision.reason == "multiscale_motion_disagreement" for decision in decisions)
    assert not any(decision.kind == "jitter" for decision in decisions)


def test_short_or_spatially_uncertain_sequences_require_review() -> None:
    short = _motion_samples([float(index) for index in range(12)])
    uncertain = list(_motion_samples([index * 0.5 + (-1) ** index * 3.0 for index in range(90)]))
    uncertain[40] = replace(
        uncertain[40],
        normalized_residual_spatial_p90=0.1,
        confidence=0.4,
        fallback_reason="low_spatial_confidence",
    )

    short_decisions = decompose_camera_trajectory(short, 30.0, math.hypot(192, 128), _config())
    uncertain_decisions = decompose_camera_trajectory(
        tuple(uncertain),
        30.0,
        math.hypot(192, 128),
        _config(),
    )

    assert all(decision.kind == "review_required" for decision in short_decisions)
    assert uncertain_decisions[40].kind == "review_required"


def test_spatial_threshold_deadband_requires_review() -> None:
    samples = list(_motion_samples([float(index) for index in range(90)]))
    threshold = _config().ransac_reprojection_threshold / math.hypot(192, 128)
    samples[30] = replace(
        samples[30],
        normalized_residual_spatial_p90=4 * threshold * 0.95,
    )

    decisions = decompose_camera_trajectory(tuple(samples), 30.0, math.hypot(192, 128), _config())

    assert decisions[30].kind == "review_required"
    assert decisions[30].reason == "spatial_motion_uncertain"


def test_inlier_ratio_deadband_uses_same_spatial_uncertainty_rule() -> None:
    samples = list(_motion_samples([float(index) for index in range(90)]))
    samples[30] = replace(
        samples[30],
        inlier_ratio=_config().minimum_inlier_ratio * 1.05,
        confidence=0.95,
    )

    decisions = decompose_camera_trajectory(tuple(samples), 30.0, math.hypot(192, 128), _config())

    assert decisions[30].kind == "review_required"
    assert decisions[30].reason == "spatial_motion_uncertain"


def test_only_isolated_low_confidence_samples_are_median_repaired() -> None:
    values = np.asarray([0.0, 1.0, 50.0, 3.0, 4.0], dtype=np.float64)
    isolated = list(_motion_samples(values.tolist()))
    isolated[2] = replace(isolated[2], confidence=0.4, fallback_reason="low_spatial_confidence")
    continuous = [replace(sample, confidence=0.4, fallback_reason="insufficient_features") for sample in isolated]

    repaired_isolated = _repair_low_confidence_trajectory(values, tuple(isolated), 0.45)
    repaired_continuous = _repair_low_confidence_trajectory(values, tuple(continuous), 0.45)

    assert repaired_isolated.tolist() == [0.0, 1.0, 3.0, 3.0, 4.0]
    assert repaired_continuous.tolist() == values.tolist()


def test_continuous_low_confidence_translation_requires_review_not_jitter() -> None:
    samples = tuple(
        replace(sample, confidence=0.4, fallback_reason="insufficient_features")
        for sample in _motion_samples([float(index) for index in range(90)])
    )

    decisions = decompose_camera_trajectory(samples, 30.0, math.hypot(192, 128), _config())

    assert all(decision.kind == "review_required" for decision in decisions)
    assert not any(decision.kind == "jitter" for decision in decisions)


def test_weighted_moving_average_rejects_invalid_window() -> None:
    with pytest.raises(ValueError, match="odd integer"):
        _weighted_moving_average(np.asarray([1.0, 2.0, 3.0]), 1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"analysis_width": 31},
        {"analysis_width": 64.5},
        {"max_features": 7},
        {"max_features": True},
        {"forward_backward_error": float("nan")},
        {"forward_backward_error": True},
        {"ransac_reprojection_threshold": float("inf")},
        {"minimum_inlier_ratio": 0.0},
        {"minimum_inlier_ratio": float("nan")},
        {"smoothing_windows_seconds": (0.15, 0.15, 0.75)},
        {"smoothing_windows_seconds": (0.15, float("nan"), 0.75)},
        {"smoothing_windows_seconds": (0.15, True, 0.75)},
        {"decision_deadband_ratio": 1.0},
        {"decision_deadband_ratio": float("nan")},
    ],
)
def test_motion_config_rejects_invalid_boundaries(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MotionConfig(**kwargs)


def test_discrete_motion_decisions_are_repeatable() -> None:
    positions = [index * 0.5 + 2.5 * math.sin(index * math.pi / 2) for index in range(90)]
    samples = _motion_samples(positions)

    runs = [
        decompose_camera_trajectory(samples, 30.0, math.hypot(192, 128), _config())
        for _ in range(20)
    ]

    expected = [(decision.kind, decision.reason) for decision in runs[0]]
    assert all([(decision.kind, decision.reason) for decision in run] == expected for run in runs[1:])
    assert np.isfinite([decision.jitter_score for decision in runs[0]]).all()


def test_ransac_backed_decisions_are_repeatable_without_global_thread_changes(tmp_path: Path) -> None:
    positions = [index * 0.5 + 2.5 * math.sin(index * math.pi / 2) for index in range(48)]
    records = generate_motion_sequence(
        tmp_path / "repeatable_ransac",
        [(position, 0.0, 0.0) for position in positions],
    )
    threads_before = cv2.getNumThreads()

    runs = []
    for _ in range(20):
        samples = estimate_camera_motion(records, _config())
        decisions = decompose_camera_trajectory(samples, 30.0, math.hypot(192, 128), _config())
        runs.append([(decision.kind, decision.reason) for decision in decisions])

    assert all(run == runs[0] for run in runs[1:])
    assert sum(kind == "jitter" for kind, _ in runs[0]) == 45
    assert sum(kind == "review_required" for kind, _ in runs[0]) == 3
    assert cv2.getNumThreads() == threads_before
