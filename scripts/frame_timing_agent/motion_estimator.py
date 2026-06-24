from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from frame_timing_agent.frame_source import FrameRecord


@dataclass(frozen=True)
class MotionEstimate:
    source_index: int
    output_index: int
    dx: float
    dy: float
    magnitude: float
    response: float
    sharpness: float
    bad_quality_candidate: bool


def estimate_frame_motion(records: list[FrameRecord], min_sharpness: float = 100.0) -> list[MotionEstimate]:
    estimates: list[MotionEstimate] = []
    previous_gray: np.ndarray | None = None

    for record in sorted(records, key=lambda item: item.output_index):
        gray = _load_gray_image(record)
        sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        bad_quality_candidate = sharpness < min_sharpness
        if previous_gray is None:
            dx = 0.0
            dy = 0.0
            response = 1.0
        else:
            (dx, dy), response = cv2.phaseCorrelate(
                previous_gray.astype(np.float32),
                gray.astype(np.float32),
            )
            dx = float(dx)
            dy = float(dy)
            response = float(response)

        estimates.append(
            MotionEstimate(
                source_index=record.source_index,
                output_index=record.output_index,
                dx=dx,
                dy=dy,
                magnitude=float(np.hypot(dx, dy)),
                response=response,
                sharpness=sharpness,
                bad_quality_candidate=bad_quality_candidate,
            )
        )
        previous_gray = gray

    return estimates


def _load_gray_image(record: FrameRecord) -> np.ndarray:
    gray = cv2.imread(str(record.path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"Cannot read frame image: {record.path}")
    return gray
