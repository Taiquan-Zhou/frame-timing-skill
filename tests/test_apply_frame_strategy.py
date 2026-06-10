import tempfile
import unittest
from pathlib import Path
import csv
import re

import cv2
import numpy as np

from frame_timing_agent.apply_frame_strategy import apply_strategy, choose_uniform_sources
from frame_timing_agent.frame_source import FrameRecord, load_frame_records


def _write_image(path: Path, value: int = 120) -> None:
    image = np.full((8, 8, 3), value, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


class ApplyFrameStrategyTest(unittest.TestCase):
    def test_choose_uniform_sources_keeps_first_last_and_count(self):
        chosen = choose_uniform_sources(list(range(10)), count=4)

        self.assertEqual(len(chosen), 4)
        self.assertEqual(chosen[0], 0)
        self.assertEqual(chosen[-1], 9)

    def test_choose_uniform_sources_keeps_all_when_count_is_large_enough(self):
        self.assertEqual(choose_uniform_sources([3, 1, 2], count=5), [1, 2, 3])

    def test_apply_strategy_duplicates_without_mutating_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            for index in range(3):
                _write_image(input_dir / f"frame_{index:06d}_src_{index:06d}.jpg", value=50 + index)
            protected_source = input_dir / "frame_000001_src_000001.jpg"
            protected_source_bytes = protected_source.read_bytes()
            records = load_frame_records(input_dir, fps=30.0)
            strategy = {
                "version": 1,
                "operations": [
                    {
                        "op": "duplicate_range",
                        "range": {"start": 1, "end": 1},
                        "total_instances": 3,
                        "reason": "test",
                    }
                ],
            }

            result = apply_strategy(records, strategy, output_dir)

            self.assertEqual(len(list(input_dir.glob("*.jpg"))), 3)
            self.assertEqual(result.output_count, 5)
            self.assertTrue((output_dir / "selected_frames.txt").exists())
            self.assertTrue((output_dir / "run_manifest.json").exists())
            output_records = load_frame_records(output_dir, fps=30.0)
            self.assertEqual([record.source_index for record in output_records], [0, 1, 1, 1, 2])
            self.assertEqual([record.instance_id for record in output_records], [0, 0, 1, 2, 0])
            _write_image(output_dir / "frame_000001_src_000001.jpg", value=250)
            self.assertEqual(protected_source.read_bytes(), protected_source_bytes)

    def test_apply_strategy_records_source_hashes_without_source_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "private_customer_frames"
            output_dir = root / "output"
            input_dir.mkdir()
            for index in range(2):
                _write_image(input_dir / f"frame_{index:06d}_src_{index:06d}.jpg", value=50 + index)
            records = load_frame_records(input_dir, fps=30.0)

            apply_strategy(records, {"version": 1, "operations": []}, output_dir)

            with (output_dir / "selected_frames.txt").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle, delimiter="\t"))
            self.assertEqual(len(rows), 2)
            self.assertIn("source_sha256", rows[0])
            self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", row["source_sha256"]) for row in rows))
            selected_text = (output_dir / "selected_frames.txt").read_text(encoding="utf-8")
            self.assertNotIn(str(input_dir), selected_text)

    def test_apply_strategy_keep_uniform_compresses_static_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            for index in range(10):
                _write_image(input_dir / f"frame_{index:06d}_src_{index:06d}.jpg", value=70 + index)
            records = load_frame_records(input_dir, fps=30.0)
            strategy = {
                "version": 1,
                "operations": [
                    {
                        "op": "keep_uniform",
                        "range": {"start": 2, "end": 8},
                        "count": 3,
                        "reason": "static",
                    }
                ],
            }

            result = apply_strategy(records, strategy, output_dir)

            self.assertEqual(result.output_count, 6)
            output_records = load_frame_records(output_dir, fps=30.0)
            self.assertEqual([record.source_index for record in output_records], [0, 1, 2, 5, 8, 9])

    def test_apply_strategy_removes_stale_output_files_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            for index in range(2):
                _write_image(input_dir / f"frame_{index:06d}_src_{index:06d}.jpg", value=90 + index)
            stale = output_dir / "frame_999999_src_999999.jpg"
            _write_image(stale, value=200)
            records = load_frame_records(input_dir, fps=30.0)

            result = apply_strategy(records, {"version": 1, "operations": []}, output_dir)

            self.assertEqual(result.output_count, 2)
            self.assertFalse(stale.exists())
            self.assertEqual(
                sorted(path.name for path in output_dir.glob("*.jpg")),
                ["frame_000000_src_000000.jpg", "frame_000001_src_000001.jpg"],
            )

    def test_apply_strategy_preserves_unrelated_images_in_output_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            output_dir.mkdir()
            _write_image(input_dir / "frame_000000_src_000000.jpg", value=90)
            unrelated = output_dir / "review_reference.jpg"
            _write_image(unrelated, value=200)
            unrelated_bytes = unrelated.read_bytes()
            records = load_frame_records(input_dir, fps=30.0)

            apply_strategy(records, {"version": 1, "operations": []}, output_dir)

            self.assertEqual(unrelated.read_bytes(), unrelated_bytes)

    def test_overlapping_operations_raise_error_before_writing_output(self):
        records = [FrameRecord(i, i, i / 30.0, Path(f"frame_{i}.jpg")) for i in range(10)]
        strategy = {
            "version": 1,
            "operations": [
                {"op": "keep_uniform", "range": {"start": 1, "end": 5}, "count": 3, "reason": "a"},
                {"op": "duplicate_range", "range": {"start": 4, "end": 8}, "total_instances": 3, "reason": "b"},
            ],
        }

        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output"
            with self.assertRaisesRegex(ValueError, "Overlapping strategy operations"):
                apply_strategy(records, strategy, output_dir)
            self.assertFalse(output_dir.exists())


if __name__ == "__main__":
    unittest.main()
