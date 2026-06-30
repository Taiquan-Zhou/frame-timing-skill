from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import cv2
import numpy as np

from frame_timing_agent.frame_source import FrameRecord


Transform = tuple[float, float, float]


def generate_motion_sequence(
    root: Path,
    transforms: Sequence[Transform],
    *,
    fps: float = 30.0,
    width: int = 192,
    height: int = 128,
    foreground_transform: Callable[[int], Transform] | None = None,
    low_texture: bool = False,
    blur_indices: frozenset[int] = frozenset(),
) -> list[FrameRecord]:
    root.mkdir(parents=True, exist_ok=True)
    base = _make_base_image(width, height, low_texture=low_texture)
    foreground = _make_foreground(width, height)
    records: list[FrameRecord] = []

    for index, transform in enumerate(transforms):
        frame = _warp(base, transform)
        if foreground_transform is not None:
            moved_foreground = _warp(foreground, foreground_transform(index))
            mask = np.any(moved_foreground != 0, axis=2)
            frame[mask] = moved_foreground[mask]
        if index in blur_indices:
            frame = cv2.GaussianBlur(frame, (9, 9), 2.5)

        path = root / f"frame_{index:06d}_src_{index:06d}.png"
        if not cv2.imwrite(str(path), frame):
            raise AssertionError(f"failed to write fixture frame: {path}")
        records.append(FrameRecord(index, index, index / fps, path))

    return records


def _make_base_image(width: int, height: int, *, low_texture: bool) -> np.ndarray:
    if low_texture:
        return np.full((height, width, 3), 128, dtype=np.uint8)

    rng = np.random.default_rng(20260630)
    image = np.zeros((height, width, 3), dtype=np.uint8)
    for _ in range(240):
        x = int(rng.integers(4, width - 4))
        y = int(rng.integers(4, height - 4))
        color = tuple(int(value) for value in rng.integers(80, 256, size=3))
        cv2.circle(image, (x, y), 2, color, -1)
    cv2.rectangle(image, (8, 8), (width - 9, height - 9), (180, 180, 180), 1)
    return image


def _make_foreground(width: int, height: int) -> np.ndarray:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    left = width // 3
    top = height // 3
    cv2.rectangle(image, (left, top), (left + width // 4, top + height // 3), (40, 220, 240), -1)
    for offset in range(0, width // 4, 8):
        cv2.line(image, (left + offset, top), (left + offset, top + height // 3), (255, 40, 120), 1)
    for offset in range(0, height // 3, 8):
        cv2.line(image, (left, top + offset), (left + width // 4, top + offset), (40, 30, 255), 1)
    return image


def _warp(image: np.ndarray, transform: Transform) -> np.ndarray:
    dx, dy, rotation_deg = transform
    height, width = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2.0, height / 2.0), rotation_deg, 1.0)
    matrix[:, 2] += (dx, dy)
    return cv2.warpAffine(image, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
