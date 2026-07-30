from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import cv2
import numpy as np
import numpy.typing as npt


def read_image(path: Path | str, flags: int = cv2.IMREAD_COLOR) -> npt.NDArray[np.generic] | None:
    """Read an image without relying on OpenCV's Windows path handling."""

    try:
        encoded = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    except OSError:
        return None
    if encoded.size == 0:
        return None
    return cast(npt.NDArray[np.generic] | None, cv2.imdecode(encoded, flags))


def write_image(path: Path | str, image: Any) -> bool:
    """Write an image without relying on OpenCV's Windows path handling."""

    destination = Path(path)
    try:
        ok, encoded = cv2.imencode(destination.suffix, image)
        if not ok:
            return False
        destination.write_bytes(encoded.tobytes())
    except (cv2.error, OSError):
        return False
    return True
