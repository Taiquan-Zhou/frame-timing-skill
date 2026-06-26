import unittest

from frame_timing_agent.frame_strategy import build_strategy
from frame_timing_agent.segment_detector import Segment


class FrameStrategyTest(unittest.TestCase):
    def test_static_segment_longer_than_keep_count_uses_keep_uniform(self):
        strategy = build_strategy(
            [Segment("static", 0, 55, 56, 0.001, "static")],
            frame_dir="input",
            limit_first_n=300,
        )

        self.assertEqual(strategy["version"], 1)
        self.assertEqual(strategy["input"], {"frame_dir_name": "input", "limit_first_n": 300})
        self.assertEqual(strategy["options"]["interpret_ranges_by"], "source_index")
        operation = strategy["operations"][0]
        self.assertEqual(operation["op"], "keep_uniform")
        self.assertEqual(operation["range"], {"start": 0, "end": 55})
        self.assertEqual(operation["count"], 20)

    def test_static_segment_within_keep_count_is_left_unchanged(self):
        strategy = build_strategy(
            [Segment("static", 10, 19, 10, 0.001, "short static")],
            frame_dir="input",
            limit_first_n=None,
        )

        self.assertEqual(strategy["operations"], [])

    def test_low_motion_review_segment_is_not_compressed(self):
        strategy = build_strategy(
            [Segment("low_motion_review", 0, 136, 137, 0.001, "slow camera motion")],
            frame_dir="input",
            limit_first_n=570,
            static_keep_count=40,
        )

        operation = strategy["operations"][0]
        self.assertEqual(operation["op"], "mark_review")
        self.assertEqual(operation["range"], {"start": 0, "end": 136})
        self.assertNotIn("count", operation)

    def test_fast_and_very_fast_use_aggressive_duplication(self):
        strategy = build_strategy(
            [
                Segment("fast_motion", 60, 80, 21, 0.2, "fast"),
                Segment("very_fast_motion", 100, 110, 11, 0.5, "very fast"),
            ],
            frame_dir="input",
            limit_first_n=300,
        )

        operations = strategy["operations"]
        self.assertEqual(operations[0]["op"], "duplicate_range")
        self.assertEqual(operations[0]["range"], {"start": 60, "end": 80})
        self.assertEqual(operations[0]["total_instances"], 3)
        self.assertEqual(operations[1]["op"], "duplicate_range")
        self.assertEqual(operations[1]["range"], {"start": 100, "end": 110})
        self.assertEqual(operations[1]["total_instances"], 4)

    def test_unknown_segment_type_is_marked_for_review_without_deleting(self):
        strategy = build_strategy(
            [Segment("bad_quality_candidate", 3, 5, 3, 0.4, "quality")],
            frame_dir="input",
            limit_first_n=10,
        )

        operation = strategy["operations"][0]
        self.assertEqual(operation["op"], "mark_review")
        self.assertEqual(operation["range"], {"start": 3, "end": 5})
        self.assertIn("bad_quality_candidate", operation["reason"])

    def test_custom_aggressive_options_are_reflected_in_strategy(self):
        strategy = build_strategy(
            [
                Segment("static", 0, 99, 100, 0.001, "static"),
                Segment("fast_motion", 100, 120, 21, 0.2, "fast"),
                Segment("very_fast_motion", 130, 140, 11, 0.5, "very fast"),
            ],
            frame_dir="input",
            limit_first_n=300,
            static_keep_count=30,
            fast_motion_total_instances=4,
            very_fast_motion_total_instances=5,
        )

        self.assertEqual(strategy["options"]["static_keep_count"], 30)
        self.assertEqual(strategy["options"]["fast_motion_total_instances"], 4)
        self.assertEqual(strategy["options"]["very_fast_motion_total_instances"], 5)
        self.assertEqual(strategy["operations"][0]["count"], 30)
        self.assertEqual(strategy["operations"][1]["total_instances"], 4)
        self.assertEqual(strategy["operations"][2]["total_instances"], 5)

    def test_manual_overrides_replace_overlapping_auto_operations(self):
        strategy = build_strategy(
            [
                Segment("fast_motion", 10, 20, 11, 0.2, "auto fast"),
                Segment("static", 100, 180, 81, 0.001, "auto static"),
            ],
            frame_dir="input",
            limit_first_n=300,
            overrides={
                "force_duplicate": [
                    {"start": 10, "end": 20, "total_instances": 5, "reason": "manual fast"}
                ],
                "force_keep_uniform": [
                    {"start": 100, "end": 180, "count": 30, "reason": "manual static"}
                ],
                "ignore_range": [
                    {"start": 220, "end": 230, "reason": "manual keep"}
                ],
            },
        )

        operations = strategy["operations"]
        self.assertEqual(len(operations), 3)
        self.assertEqual(operations[0]["op"], "duplicate_range")
        self.assertEqual(operations[0]["total_instances"], 5)
        self.assertEqual(operations[0]["source"], "manual_override")
        self.assertEqual(operations[1]["op"], "keep_uniform")
        self.assertEqual(operations[1]["count"], 30)
        self.assertEqual(operations[1]["source"], "manual_override")
        self.assertEqual(operations[2]["op"], "keep")
        self.assertEqual(operations[2]["source"], "manual_override")

    def test_overlapping_manual_overrides_raise_value_error(self):
        with self.assertRaisesRegex(ValueError, "overlap"):
            build_strategy(
                [],
                frame_dir="input",
                limit_first_n=300,
                overrides={
                    "force_duplicate": [
                        {"start": 10, "end": 20, "total_instances": 3},
                    ],
                    "ignore_range": [
                        {"start": 15, "end": 25},
                    ],
                },
            )


if __name__ == "__main__":
    unittest.main()
