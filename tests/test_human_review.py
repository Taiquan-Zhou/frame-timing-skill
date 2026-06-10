import tempfile
import unittest
from pathlib import Path

from frame_timing_agent.human_review import write_human_review


class HumanReviewTest(unittest.TestCase):
    def test_write_human_review_summarizes_stage_for_user_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = Path(tmp) / "analysis"
            strategy = {
                "operations": [
                    {
                        "op": "keep_uniform",
                        "range": {"start": 0, "end": 54},
                        "count": 20,
                        "source": "auto_detection",
                        "reason": "long static section",
                    },
                    {
                        "op": "duplicate_range",
                        "range": {"start": 58, "end": 62},
                        "total_instances": 3,
                        "source": "auto_detection",
                        "reason": "fast motion",
                    },
                ]
            }
            audit = {
                "status": "ok",
                "input_count": 55,
                "output_count": 20,
                "manifest_output_count": 20,
                "image_count": 20,
                "errors": [],
                "warnings": [],
                "operation_results": [
                    {
                        "op": "keep_uniform",
                        "range": {"start": 0, "end": 54},
                        "source": "auto_detection",
                        "affected_source_count": 55,
                        "output_record_count": 20,
                        "dropped_count": 35,
                        "kept_sources": [0, 3, 6, 54],
                    }
                ],
            }

            path = write_human_review(
                analysis_dir=analysis_dir,
                stage_name="阶段 4：本地图片处理 Agent",
                input_frame_dir=Path("frames"),
                output_dir=Path("output_frames"),
                analyzed_count=55,
                estimated_output_count=20,
                strategy=strategy,
                audit=audit,
                preview_only=False,
            )

            text = path.read_text(encoding="utf-8")
            self.assertIn("# 阶段 4：本地图片处理 Agent", text)
            self.assertIn("source_index", text)
            self.assertIn("0-54", text)
            self.assertIn("55 -> 20", text)
            self.assertIn("不修改图片内容", text)
            self.assertIn("等待你确认", text)
            self.assertNotIn("闃", text)
            self.assertNotIn("涓", text)


if __name__ == "__main__":
    unittest.main()
