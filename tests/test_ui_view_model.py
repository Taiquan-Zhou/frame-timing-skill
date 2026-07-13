import csv
import json
import tempfile
import unittest
from pathlib import Path

from frame_timing_agent.auto_timing_agent import TimingAgentResult
from frame_timing_agent.ui.view_model import build_analysis_view, load_execution_summary


class UiViewModelTest(unittest.TestCase):
    def test_build_analysis_view_reads_existing_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            analysis = root / "output" / "run" / "analysis"
            frames.mkdir()
            analysis.mkdir(parents=True)
            (frames / "frame_000000.jpg").write_bytes(b"frame-0")
            (frames / "frame_000001.jpg").write_bytes(b"frame-1")

            with (analysis / "frame_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(
                    [
                        "source_index",
                        "output_index",
                        "timestamp_sec",
                        "sharpness",
                        "brightness",
                        "contrast",
                        "motion_score",
                        "similarity_score",
                        "bad_quality_candidate",
                    ]
                )
                writer.writerow([0, 0, "0.000000", "12.0", "100.0", "4.0", "0.0", "1.0", 0])
                writer.writerow([1, 1, "0.033333", "18.0", "110.0", "5.0", "0.2", "0.8", 0])
            (analysis / "segments.json").write_text(
                json.dumps(
                    [
                        {
                            "segment_type": "fast_motion",
                            "start": 1,
                            "end": 1,
                            "frame_count": 1,
                            "mean_motion": 0.2,
                            "reason": "test",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (analysis / "strategy.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "operations": [
                            {
                                "op": "duplicate_range",
                                "range": {"start": 1, "end": 1},
                                "total_instances": 3,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = TimingAgentResult(
                analyzed_count=2,
                estimated_output_count=4,
                artifact_dir=analysis.parent,
                strategy_path=analysis / "strategy.json",
                output_dir=None,
            )

            view = build_analysis_view(result, frames, fps=30.0, limit_first_n=2)

            self.assertEqual(view.analyzed_count, 2)
            self.assertEqual(view.estimated_output_count, 4)
            self.assertEqual(view.strategy_name, "reconstruction_balanced")
            self.assertEqual(view.source_indices, (0, 1))
            self.assertEqual(view.motion_values, (0.0, 0.2))
            self.assertEqual(view.sharpness_values, (12.0, 18.0))
            self.assertEqual(view.operation_counts, {"duplicate_range": 1})
            self.assertEqual(view.segments[0].segment_type, "fast_motion")
            self.assertEqual(view.thumbnails[0].source_index, 1)
            self.assertEqual(view.thumbnails[0].operation, "duplicate_range")

    def test_load_execution_summary_reports_audit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp) / "run"
            analysis = artifact_dir / "analysis"
            analysis.mkdir(parents=True)
            (analysis / "execution_audit.json").write_text(
                json.dumps({"status": "ok", "output_count": 17, "errors": [], "warnings": ["review"]}),
                encoding="utf-8",
            )

            summary = load_execution_summary(artifact_dir)

            self.assertEqual(summary.status, "ok")
            self.assertEqual(summary.output_count, 17)
            self.assertEqual(summary.warning_count, 1)
            self.assertEqual(summary.error_count, 0)


if __name__ == "__main__":
    unittest.main()
