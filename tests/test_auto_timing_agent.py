import tempfile
import unittest
import inspect
from pathlib import Path
import subprocess
import sys
import json

import cv2
import numpy as np

import frame_timing_agent.auto_timing_agent as legacy_auto_timing
from frame_timing_agent.auto_timing_agent import run_timing_agent


def _write_image(path: Path, value: int) -> None:
    image = np.full((16, 16, 3), value, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


class AutoTimingAgentTest(unittest.TestCase):
    def test_legacy_entry_keeps_override_signature_without_agent_contracts(self):
        self.assertIn("override_config_path", inspect.signature(run_timing_agent).parameters)
        self.assertFalse(hasattr(legacy_auto_timing, "PolicyName"))
        self.assertFalse(hasattr(legacy_auto_timing, "StrategyRequest"))

    def test_preview_writes_analysis_but_not_output_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_dir = root / "output" / "preview"
            frames.mkdir()
            for index in range(30):
                _write_image(frames / f"frame_{index:06d}_src_{index:06d}.jpg", 100)

            result = run_timing_agent(frames, artifact_dir, limit_first_n=20, write=False)

            self.assertEqual(result.analyzed_count, 20)
            self.assertTrue((artifact_dir / "analysis" / "frame_metrics.csv").exists())
            self.assertTrue((artifact_dir / "analysis" / "segments.json").exists())
            self.assertTrue((artifact_dir / "analysis" / "strategy.json").exists())
            self.assertTrue((artifact_dir / "analysis" / "report.md").exists())
            self.assertTrue((artifact_dir / "analysis" / "engineering_log.md").exists())
            self.assertTrue((artifact_dir / "analysis" / "human_review.md").exists())
            self.assertTrue((artifact_dir / "analysis" / "visual_review" / "index.md").exists())
            human_review = (artifact_dir / "analysis" / "human_review.md").read_text(encoding="utf-8")
            self.assertIn("阶段 4：本地图片处理 Agent", human_review)
            self.assertFalse((artifact_dir / "output_frames").exists())

    def test_write_mode_creates_output_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_dir = root / "output" / "write"
            frames.mkdir()
            for index in range(30):
                _write_image(frames / f"frame_{index:06d}_src_{index:06d}.jpg", 100)

            result = run_timing_agent(frames, artifact_dir, limit_first_n=20, write=True)

            self.assertEqual(result.analyzed_count, 20)
            self.assertIsNotNone(result.output_dir)
            self.assertTrue((artifact_dir / "output_frames" / "selected_frames.txt").exists())
            self.assertTrue((artifact_dir / "analysis" / "execution_audit.json").exists())
            self.assertTrue((artifact_dir / "analysis" / "execution_audit.md").exists())
            self.assertTrue((artifact_dir / "analysis" / "human_review.md").exists())
            self.assertGreater(result.estimated_output_count, 0)

    def test_limit_first_n_uses_only_requested_subset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_dir = root / "output" / "subset"
            frames.mkdir()
            for source_index in [20, 10, 30, 40]:
                _write_image(frames / f"frame_{source_index:06d}_src_{source_index:06d}.jpg", 100)

            result = run_timing_agent(frames, artifact_dir, limit_first_n=2, write=False)

            self.assertEqual(result.analyzed_count, 2)
            metrics_csv = (artifact_dir / "analysis" / "frame_metrics.csv").read_text(encoding="utf-8")
            self.assertIn("10,", metrics_csv)
            self.assertIn("20,", metrics_csv)
            self.assertNotIn("30,", metrics_csv)

    def test_override_config_applies_manual_strategy_and_review_ranges(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_dir = root / "output" / "override"
            override_config = root / "override.json"
            frames.mkdir()
            for index in range(30):
                _write_image(frames / f"frame_{index:06d}_src_{index:06d}.jpg", 100 + index)
            override_config.write_text(
                json.dumps(
                    {
                        "review_ranges": [
                            {"name": "manual_check", "start": 5, "end": 10},
                        ],
                        "overrides": {
                            "force_duplicate": [
                                {
                                    "start": 5,
                                    "end": 10,
                                    "total_instances": 4,
                                    "reason": "manual slow down",
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            result = run_timing_agent(
                frames,
                artifact_dir,
                limit_first_n=20,
                write=False,
                override_config_path=override_config,
            )

            self.assertEqual(result.analyzed_count, 20)
            strategy = json.loads((artifact_dir / "analysis" / "strategy.json").read_text(encoding="utf-8"))
            manual_ops = [op for op in strategy["operations"] if op.get("source") == "manual_override"]
            self.assertEqual(len(manual_ops), 1)
            self.assertEqual(manual_ops[0]["range"], {"start": 5, "end": 10})
            self.assertEqual(manual_ops[0]["total_instances"], 4)
            report = (artifact_dir / "analysis" / "report.md").read_text(encoding="utf-8")
            self.assertIn("manual_check", report)
            self.assertIn("Manual Overrides", report)

    def test_script_entrypoint_runs_from_project_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_dir = root / "output" / "script"
            frames.mkdir()
            for index in range(3):
                _write_image(frames / f"frame_{index:06d}_src_{index:06d}.jpg", 100)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts") / "frame_timing_agent" / "auto_timing_agent.py"),
                    "--frames",
                    str(frames),
                    "--artifact_dir",
                    str(artifact_dir),
                    "--limit_first_n",
                    "3",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Analyzed frames: 3", completed.stdout)
            self.assertTrue((artifact_dir / "analysis" / "strategy.json").exists())


if __name__ == "__main__":
    unittest.main()
