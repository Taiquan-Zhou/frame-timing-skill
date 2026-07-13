import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from frame_timing_agent.image_io import read_image, write_image


class ImageIoTest(unittest.TestCase):
    def test_round_trip_supports_unicode_windows_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "中文帧目录" / "测试帧.jpg"
            path.parent.mkdir()
            image = np.full((12, 16, 3), 127, dtype=np.uint8)

            self.assertTrue(write_image(path, image))
            loaded = read_image(path, cv2.IMREAD_COLOR)

            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.shape, image.shape)

    def test_read_missing_image_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_image(Path(tmp) / "missing.png", cv2.IMREAD_GRAYSCALE))

    def test_write_rejects_unknown_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            image = np.zeros((4, 4, 3), dtype=np.uint8)
            self.assertFalse(write_image(Path(tmp) / "frame.unknown", image))


if __name__ == "__main__":
    unittest.main()
