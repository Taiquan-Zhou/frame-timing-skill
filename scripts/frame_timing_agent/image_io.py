from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def read_image(path: Path | str, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """Read an image without relying on OpenCV's Windows path handling."""
    try:
        encoded = np.fromfile(Path(path), dtype=np.uint8)
    except OSError:
        return None
    if encoded.size == 0:
        return None
    try:
        return cv2.imdecode(encoded, flags)
    except cv2.error:
        return None


def write_image(path: Path | str, image: np.ndarray) -> bool:
    """Write an image while preserving Unicode paths on Windows."""
    path = Path(path)
    try:
        ok, encoded = cv2.imencode(path.suffix, image)
        if not ok:
            return False
        encoded.tofile(path)
    except (cv2.error, OSError):
        return False
    return True
