import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from frame_timing_agent.apply_frame_strategy import apply_strategy
from frame_timing_agent.frame_source import load_frame_records
from frame_timing_agent.strategy_execution_audit import audit_strategy_execution, write_execution_audit


def _write_image(path: Path, value: int = 120) -> None:
    image = np.full((8, 8, 3), value, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


class StrategyExecutionAuditTest(unittest.TestCase):
    def test_audit_reports_duplicate_and_keep_uniform_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            for index in range(10):
                _write_image(input_dir / f"frame_{index:06d}_src_{index:06d}.jpg", value=50 + index)
            records = load_frame_records(input_dir, fps=30.0)
            strategy = {
                "version": 1,
                "operations": [
                    {
                        "op": "duplicate_range",
                        "range": {"start": 1, "end": 2},
                        "total_instances": 3,
                        "reason": "slow down",
                        "source": "auto_detection",
                    },
                    {
                        "op": "keep_uniform",
                        "range": {"start": 5, "end": 8},
                        "count": 2,
                        "reason": "compress static",
                        "source": "manual_override",
                    },
                ],
            }

            apply_strategy(records, strategy, output_dir)
            audit = audit_strategy_execution(records, strategy, output_dir)

            self.assertEqual(audit["status"], "ok")
            self.assertEqual(audit["input_count"], 10)
            self.assertEqual(audit["output_count"], 12)
            self.assertEqual(audit["manifest_output_count"], 12)
            self.assertEqual(audit["image_count"], 12)
            self.assertEqual(audit["errors"], [])
            duplicate_result = audit["operation_results"][0]
            self.assertEqual(duplicate_result["op"], "duplicate_range")
            self.assertEqual(duplicate_result["affected_source_count"], 2)
            self.assertEqual(duplicate_result["output_record_count"], 6)
            self.assertEqual(duplicate_result["added_count"], 4)
            keep_result = audit["operation_results"][1]
            self.assertEqual(keep_result["op"], "keep_uniform")
            self.assertEqual(keep_result["kept_sources"], [5, 8])
            self.assertEqual(keep_result["dropped_count"], 2)

    def test_audit_reports_broken_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            for index in range(3):
                _write_image(input_dir / f"frame_{index:06d}_src_{index:06d}.jpg", value=80 + index)
            records = load_frame_records(input_dir, fps=30.0)
            strategy = {"version": 1, "operations": []}

            apply_strategy(records, strategy, output_dir)
            next(output_dir.glob("*.jpg")).unlink()
            audit = audit_strategy_execution(records, strategy, output_dir)

            self.assertEqual(audit["status"], "failed")
            self.assertTrue(any("image_count" in error for error in audit["errors"]))

    def test_audit_reports_corrupt_selected_output_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            for index in range(3):
                _write_image(input_dir / f"frame_{index:06d}_src_{index:06d}.jpg", value=80 + index)
            records = load_frame_records(input_dir, fps=30.0)
            strategy = {"version": 1, "operations": []}

            apply_strategy(records, strategy, output_dir)
            selected_path = output_dir / "selected_frames.txt"
            selected_path.write_text(
                selected_path.read_text(encoding="utf-8").replace(
                    "1\t1\t0.033333\t0\t0\tkeep\t",
                    "99\t1\t0.033333\t0\t0\tkeep\t",
                ),
                encoding="utf-8",
            )

            audit = audit_strategy_execution(records, strategy, output_dir)

            self.assertEqual(audit["status"], "failed")
            self.assertTrue(any("output_index is not contiguous" in error for error in audit["errors"]))

    def test_write_execution_audit_outputs_json_and_markdown(self):
        audit = {
            "status": "ok",
            "input_count": 3,
            "output_count": 3,
            "manifest_output_count": 3,
            "image_count": 3,
            "errors": [],
            "warnings": [],
            "operation_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = Path(tmp) / "analysis"

            write_execution_audit(audit, analysis_dir)

            saved = json.loads((analysis_dir / "execution_audit.json").read_text(encoding="utf-8"))
            self.assertEqual(saved["status"], "ok")
            markdown = (analysis_dir / "execution_audit.md").read_text(encoding="utf-8")
            self.assertIn("# Strategy Execution Audit", markdown)
            self.assertIn("Status: ok", markdown)


if __name__ == "__main__":
    unittest.main()
