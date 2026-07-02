from __future__ import annotations

from pathlib import Path
from typing import cast

import cv2
import numpy as np
import numpy.typing as npt


def read_image(path: Path | str, flags: int) -> npt.NDArray[np.uint8] | None:
    """Read an image without relying on OpenCV's Windows path handling."""

    try:
        encoded = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    except OSError:
        return None
    if encoded.size == 0:
        return None
    return cast(npt.NDArray[np.uint8] | None, cv2.imdecode(encoded, flags))
