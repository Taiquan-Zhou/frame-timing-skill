import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from frame_timing_agent.strategy_visual_review import write_strategy_visual_review


def _write_image(path: Path, value: int) -> None:
    image = np.full((24, 32, 3), value, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


class StrategyVisualReviewTest(unittest.TestCase):
    def test_write_strategy_visual_review_creates_contact_sheets_for_operations(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            analysis_dir = root / "analysis"
            frames.mkdir()
            for index in range(12):
                _write_image(frames / f"frame_{index:06d}_src_{index:06d}.jpg", 20 + index)
            strategy = {
                "operations": [
                    {
                        "op": "keep_uniform",
                        "range": {"start": 0, "end": 5},
                        "count": 3,
                        "source": "auto_detection",
                    },
                    {
                        "op": "duplicate_range",
                        "range": {"start": 8, "end": 10},
                        "total_instances": 4,
                        "source": "auto_detection",
                    },
                ]
            }

            result = write_strategy_visual_review(
                frame_dir=frames,
                analysis_dir=analysis_dir,
                strategy=strategy,
                max_samples_per_operation=4,
                tile_width=64,
            )

            self.assertEqual(result.operation_count, 2)
            self.assertEqual(len(result.contact_sheets), 2)
            self.assertTrue(result.index_path.exists())
            for sheet in result.contact_sheets:
                self.assertTrue(sheet.exists())
                image = cv2.imread(str(sheet))
                self.assertIsNotNone(image)
                self.assertGreater(image.shape[0], 0)
                self.assertGreater(image.shape[1], 0)
            index_text = result.index_path.read_text(encoding="utf-8")
            self.assertIn("阶段 7：策略可视化审查", index_text)
            self.assertIn("0-5", index_text)
            self.assertIn("8-10", index_text)
            self.assertIn("contact_000_keep_uniform_0_5.png", index_text)

    def test_script_entrypoint_reads_strategy_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            analysis_dir = root / "analysis"
            frames.mkdir()
            for index in range(4):
                _write_image(frames / f"frame_{index:06d}_src_{index:06d}.jpg", 80 + index)
            strategy_path = analysis_dir / "strategy.json"
            analysis_dir.mkdir()
            strategy_path.write_text(
                json.dumps(
                    {
                        "operations": [
                            {
                                "op": "duplicate_range",
                                "range": {"start": 1, "end": 3},
                                "total_instances": 3,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            import subprocess
            import sys

            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts") / "frame_timing_agent" / "strategy_visual_review.py"),
                    "--frames",
                    str(frames),
                    "--analysis_dir",
                    str(analysis_dir),
                    "--strategy",
                    str(strategy_path),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Visual review:", completed.stdout)
            self.assertTrue((analysis_dir / "visual_review" / "index.md").exists())


if __name__ == "__main__":
    unittest.main()
