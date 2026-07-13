from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from frame_timing_agent.frame_source import FrameRecord
from frame_timing_agent.image_io import read_image


@dataclass(frozen=True)
class FrameMetric:
    source_index: int
    output_index: int
    timestamp_sec: float
    sharpness: float
    brightness: float
    contrast: float
    motion_score: float
    similarity_score: float
    bad_quality_candidate: bool


def _load_gray_image(path) -> np.ndarray:
    image = read_image(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Cannot read frame image: {path}")
    return image


def _compute_motion_score(current_gray_normalized: np.ndarray, previous_gray_normalized: np.ndarray) -> float:
    if current_gray_normalized.shape != previous_gray_normalized.shape:
        raise ValueError(
            "frame size mismatch: "
            f"previous={previous_gray_normalized.shape}, current={current_gray_normalized.shape}"
        )
    return float(np.mean(np.abs(current_gray_normalized - previous_gray_normalized)))


def compute_frame_metrics(records: list[FrameRecord]) -> list[FrameMetric]:
    metrics: list[FrameMetric] = []
    previous_gray_normalized: np.ndarray | None = None

    for record in records:
        gray = _load_gray_image(record.path)
        gray_normalized = gray.astype(np.float32) / 255.0
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(np.mean(gray))
        contrast = float(np.std(gray))

        if previous_gray_normalized is None:
            motion_score = 0.0
        else:
            motion_score = _compute_motion_score(gray_normalized, previous_gray_normalized)

        similarity_score = max(0.0, 1.0 - motion_score)
        bad_quality_candidate = sharpness < 8.0 or brightness < 10.0 or brightness > 245.0

        metrics.append(
            FrameMetric(
                source_index=record.source_index,
                output_index=record.output_index,
                timestamp_sec=record.timestamp_sec,
                sharpness=sharpness,
                brightness=brightness,
                contrast=contrast,
                motion_score=motion_score,
                similarity_score=similarity_score,
                bad_quality_candidate=bad_quality_candidate,
            )
        )
        previous_gray_normalized = gray_normalized

    return metrics
