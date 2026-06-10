import json
import tempfile
import unittest
from pathlib import Path

from frame_timing_agent.segment_detector import Segment
from frame_timing_agent.timing_metrics import FrameMetric
from frame_timing_agent.timing_report import write_analysis_artifacts


def _metric(index: int, motion: float, bad_quality_candidate: bool = False) -> FrameMetric:
    return FrameMetric(
        source_index=index,
        output_index=index,
        timestamp_sec=index / 30.0,
        sharpness=50.0,
        brightness=120.0,
        contrast=20.0,
        motion_score=motion,
        similarity_score=max(0.0, 1.0 - motion),
        bad_quality_candidate=bad_quality_candidate,
    )


class TimingReportTest(unittest.TestCase):
    def test_writes_analysis_artifacts_and_chinese_engineering_log(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = Path(tmp) / "analysis"
            strategy = {
                "version": 1,
                "input": {"frame_dir": "input", "limit_first_n": 300},
                "options": {"interpret_ranges_by": "source_index"},
                "operations": [
                    {
                        "op": "duplicate_range",
                        "range": {"start": 10, "end": 12},
                        "total_instances": 3,
                        "reason": "fast",
                    }
                ],
            }

            write_analysis_artifacts(
                analysis_dir=analysis_dir,
                metrics=[_metric(0, 0.0), _metric(1, 0.2, bad_quality_candidate=True)],
                segments=[Segment("fast_motion", 10, 12, 3, 0.2, "fast")],
                strategy=strategy,
                preview_only=True,
                timestamp_source="inferred from fps=30.0",
                detection_config={
                    "static_motion_quantile": 0.15,
                    "fast_motion_quantile": 0.70,
                    "very_fast_motion_quantile": 0.90,
                },
                review_ranges=[{"name": "suspected_static", "start": 0, "end": 1}],
            )

            self.assertTrue((analysis_dir / "frame_metrics.csv").exists())
            self.assertTrue((analysis_dir / "segments.json").exists())
            self.assertTrue((analysis_dir / "strategy.json").exists())
            self.assertTrue((analysis_dir / "report.md").exists())
            self.assertTrue((analysis_dir / "engineering_log.md").exists())

            segments = json.loads((analysis_dir / "segments.json").read_text(encoding="utf-8"))
            self.assertEqual(segments[0]["segment_type"], "fast_motion")
            saved_strategy = json.loads((analysis_dir / "strategy.json").read_text(encoding="utf-8"))
            self.assertEqual(saved_strategy["operations"][0]["op"], "duplicate_range")

            report = (analysis_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("Analyzed: 2 frames", report)
            self.assertIn("source_index", report)
            self.assertIn("engineering_log.md", report)
            self.assertIn("## Detection Thresholds", report)
            self.assertIn("static_motion_quantile", report)
            self.assertIn("## Review Ranges", report)
            self.assertIn("suspected_static", report)
            self.assertIn("longest_static_run", report)

            log = (analysis_dir / "engineering_log.md").read_text(encoding="utf-8")
            self.assertIn("# 视频帧节奏 Agent 工程日志", log)
            self.assertIn("## 输入数据", log)
            self.assertIn("## 发现的问题", log)
            self.assertIn("## 使用的方法", log)
            self.assertIn("## 策略决策", log)
            self.assertIn("## 处理结果", log)
            self.assertIn("## 风险和局限", log)
            self.assertIn("## 下一步实验建议", log)
            self.assertIn("重复图片不会创造新的视角", log)

    def test_frame_metrics_csv_records_bad_quality_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = Path(tmp) / "analysis"

            write_analysis_artifacts(
                analysis_dir=analysis_dir,
                metrics=[_metric(1, 0.5, bad_quality_candidate=True)],
                segments=[],
                strategy={"version": 1, "operations": []},
                preview_only=False,
                timestamp_source="selected_frames.txt",
            )

            csv_text = (analysis_dir / "frame_metrics.csv").read_text(encoding="utf-8")
            self.assertIn("bad_quality_candidate", csv_text)
            self.assertIn(",1\n", csv_text)
            log = (analysis_dir / "engineering_log.md").read_text(encoding="utf-8")
            self.assertIn("执行模式", log)


if __name__ == "__main__":
    unittest.main()
