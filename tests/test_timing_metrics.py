import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from frame_timing_agent.frame_source import FrameRecord
from frame_timing_agent.timing_metrics import compute_frame_metrics


def _write_image(path: Path, image: np.ndarray) -> None:
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


def _checkerboard(size: int = 32, block: int = 4) -> np.ndarray:
    y, x = np.indices((size, size))
    pattern = ((x // block + y // block) % 2) * 255
    return pattern.astype(np.uint8)


class TimingMetricsTest(unittest.TestCase):
    def test_compute_frame_metrics_marks_repeated_frames_as_low_motion(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            image = _checkerboard()
            first_path = frame_dir / "frame_000001.png"
            second_path = frame_dir / "frame_000002.png"
            _write_image(first_path, image)
            _write_image(second_path, image)
            records = [
                FrameRecord(source_index=10, output_index=0, timestamp_sec=0.4, path=first_path),
                FrameRecord(source_index=11, output_index=1, timestamp_sec=0.44, path=second_path),
            ]

            metrics = compute_frame_metrics(records)

            self.assertEqual(len(metrics), 2)
            self.assertEqual(metrics[0].motion_score, 0.0)
            self.assertEqual(metrics[0].similarity_score, 1.0)
            self.assertEqual(metrics[1].motion_score, 0.0)
            self.assertEqual(metrics[1].similarity_score, 1.0)

    def test_compute_frame_metrics_detects_high_motion_for_large_frame_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            first_path = frame_dir / "frame_000001.png"
            second_path = frame_dir / "frame_000002.png"
            _write_image(first_path, np.zeros((32, 32), dtype=np.uint8))
            _write_image(second_path, np.full((32, 32), 255, dtype=np.uint8))
            records = [
                FrameRecord(source_index=3, output_index=0, timestamp_sec=0.1, path=first_path),
                FrameRecord(source_index=4, output_index=1, timestamp_sec=0.133333, path=second_path),
            ]

            metrics = compute_frame_metrics(records)

            self.assertEqual(metrics[1].motion_score, 1.0)
            self.assertEqual(metrics[1].similarity_score, 0.0)
            self.assertTrue(metrics[0].bad_quality_candidate)
            self.assertTrue(metrics[1].bad_quality_candidate)

    def test_compute_frame_metrics_raises_for_regular_frame_size_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            first_path = frame_dir / "frame_000001.png"
            second_path = frame_dir / "frame_000002.png"
            _write_image(first_path, _checkerboard(32))
            _write_image(second_path, _checkerboard()[:16, :32])
            records = [
                FrameRecord(source_index=1, output_index=0, timestamp_sec=0.0, path=first_path),
                FrameRecord(source_index=2, output_index=1, timestamp_sec=0.1, path=second_path),
            ]

            with self.assertRaisesRegex(ValueError, "frame size mismatch"):
                compute_frame_metrics(records)

    def test_compute_frame_metrics_raises_for_broadcastable_but_invalid_frame_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            first_path = frame_dir / "frame_000001.png"
            second_path = frame_dir / "frame_000002.png"
            _write_image(first_path, _checkerboard(32))
            _write_image(second_path, np.zeros((1, 32), dtype=np.uint8))
            records = [
                FrameRecord(source_index=1, output_index=0, timestamp_sec=0.0, path=first_path),
                FrameRecord(source_index=2, output_index=1, timestamp_sec=0.1, path=second_path),
            ]

            with self.assertRaisesRegex(ValueError, "frame size mismatch"):
                compute_frame_metrics(records)

    def test_compute_frame_metrics_raises_value_error_when_image_cannot_be_read(self):
        record = FrameRecord(
            source_index=7,
            output_index=2,
            timestamp_sec=0.7,
            path=Path("C:/definitely/missing/frame.png"),
        )

        with self.assertRaisesRegex(ValueError, "Cannot read frame image"):
            compute_frame_metrics([record])

    def test_compute_frame_metrics_preserves_frame_record_indices_and_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            frame_path = frame_dir / "frame_000123.png"
            _write_image(frame_path, _checkerboard())
            record = FrameRecord(
                source_index=123,
                output_index=9,
                timestamp_sec=4.1,
                path=frame_path,
            )

            metric = compute_frame_metrics([record])[0]

            self.assertEqual(metric.source_index, 123)
            self.assertEqual(metric.output_index, 9)
            self.assertEqual(metric.timestamp_sec, 4.1)


if __name__ == "__main__":
    unittest.main()
