import unittest
from unittest.mock import patch

from frame_timing_agent.segment_detector import Segment, _detect_jittered_static_windows, detect_segments
from frame_timing_agent.timing_metrics import FrameMetric


def _metric(source_index: int, motion_score: float, bad_quality_candidate: bool = False) -> FrameMetric:
    return FrameMetric(
        source_index=source_index,
        output_index=source_index,
        timestamp_sec=source_index / 30.0,
        sharpness=10.0,
        brightness=120.0,
        contrast=20.0,
        motion_score=motion_score,
        similarity_score=max(0.0, 1.0 - motion_score),
        bad_quality_candidate=bad_quality_candidate,
    )


class SegmentDetectorTest(unittest.TestCase):
    def test_detect_segments_returns_empty_list_for_empty_input(self):
        self.assertEqual(detect_segments([]), [])

    def test_detect_segments_classifies_uniform_low_motion_as_static_regression(self):
        metrics = [_metric(index, 0.001) for index in range(25)]

        segments = detect_segments(metrics, min_static_frames=21)

        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertEqual(segment.segment_type, "static")
        self.assertEqual(segment.start, 0)
        self.assertEqual(segment.end, 24)
        self.assertEqual(segment.frame_count, 25)

    def test_detect_segments_classifies_uniform_high_motion_as_very_fast_regression(self):
        metrics = [_metric(index, 0.95) for index in range(25)]

        segments = detect_segments(metrics, min_fast_frames=5)

        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertEqual(segment.segment_type, "very_fast_motion")
        self.assertEqual(segment.start, 0)
        self.assertEqual(segment.end, 24)
        self.assertEqual(segment.frame_count, 25)

    def test_detect_segments_finds_long_static_segment(self):
        metrics = [_metric(index, 0.01) for index in range(100, 121)]
        metrics.extend(_metric(index, 0.20) for index in range(121, 125))
        metrics.extend(_metric(index, 0.40) for index in range(125, 129))

        segments = detect_segments(metrics)

        static_segments = [segment for segment in segments if segment.segment_type == "static"]
        self.assertEqual(len(static_segments), 1)
        segment = static_segments[0]
        self.assertEqual(segment.segment_type, "static")
        self.assertEqual(segment.start, 100)
        self.assertEqual(segment.end, 120)
        self.assertEqual(segment.frame_count, 21)
        self.assertAlmostEqual(segment.mean_motion, 0.01)
        self.assertIn("static", segment.reason)

    def test_detect_segments_marks_low_motion_window_for_review_before_fast_motion(self):
        calm_motions = [
            0.000000,
            0.005900,
            0.001488,
            0.001340,
            0.002784,
            0.002752,
            0.001654,
            0.002757,
            0.001728,
            0.003598,
            0.003573,
            0.001958,
            0.006851,
            0.003236,
            0.004152,
            0.002460,
            0.001504,
            0.002209,
            0.005901,
            0.002025,
            0.001551,
            0.001714,
            0.002621,
            0.003076,
            0.006961,
            0.010476,
            0.004425,
            0.003590,
            0.001989,
            0.001772,
            0.003589,
            0.002628,
            0.001985,
            0.006456,
            0.004101,
            0.003460,
            0.004600,
            0.003278,
            0.003921,
            0.002528,
            0.001776,
            0.002330,
            0.002094,
            0.002612,
            0.002140,
            0.001423,
            0.001914,
            0.002753,
            0.005280,
            0.003255,
            0.008636,
            0.003609,
            0.001860,
            0.001870,
            0.007099,
        ]
        fast_motions = [0.055884, 0.035491, 0.026732, 0.031651, 0.035710, 0.036844, 0.031242, 0.032971]
        metrics = [
            _metric(index, motion)
            for index, motion in enumerate(calm_motions + fast_motions)
        ]

        segments = detect_segments(metrics, min_static_frames=21, min_fast_frames=5)

        self.assertIn(("low_motion_review", 0, 54, 55), [
            (segment.segment_type, segment.start, segment.end, segment.frame_count)
            for segment in segments
        ])
        self.assertTrue(any(
            segment.segment_type in {"fast_motion", "very_fast_motion"} and segment.start == 58 and segment.end == 62
            for segment in segments
        ))

    def test_detect_segments_finds_fast_and_very_fast_motion_segments(self):
        metrics = []
        motions = (
            [0.05] * 10
            + [0.55] * 5
            + [0.20] * 3
            + [0.95] * 5
            + [0.30] * 2
        )
        for index, motion_score in enumerate(motions, start=200):
            metrics.append(_metric(index, motion_score))

        segments = detect_segments(metrics, min_fast_frames=5)

        self.assertEqual(
            [(segment.segment_type, segment.start, segment.end, segment.frame_count) for segment in segments],
            [
                ("fast_motion", 210, 214, 5),
                ("very_fast_motion", 218, 222, 5),
            ],
        )
        self.assertGreater(segments[1].mean_motion, segments[0].mean_motion)

    def test_bad_quality_candidate_is_excluded_from_quantile_calibration_only(self):
        metrics = [_metric(index, 0.02) for index in range(300, 321)]
        metrics.extend(_metric(index, 0.15) for index in range(321, 325))
        metrics.extend(_metric(index, 0.95, bad_quality_candidate=True) for index in range(325, 330))
        metrics.extend(_metric(index, 0.30) for index in range(330, 334))
        metrics.extend(_metric(index, 0.45) for index in range(334, 338))

        segments = detect_segments(metrics, min_fast_frames=5)

        self.assertEqual(
            [(segment.segment_type, segment.start, segment.end, segment.frame_count) for segment in segments],
            [
                ("static", 300, 320, 21),
                ("very_fast_motion", 325, 329, 5),
            ],
        )

    def test_detect_segments_drops_short_fast_motion_runs_below_minimum_length(self):
        metrics = []
        motions = [0.10] * 10 + [0.20] * 6 + [0.30] * 4 + [0.80] * 4
        for index, motion_score in enumerate(motions, start=400):
            metrics.append(_metric(index, motion_score))

        segments = detect_segments(metrics, min_fast_frames=5)

        self.assertEqual(segments, [])

    def test_detect_segments_uses_observed_frame_count_for_non_contiguous_source_indices(self):
        source_indices = [10, 20, 30, 40, 50]
        metrics = [_metric(source_index, 0.80) for source_index in source_indices]

        segments = detect_segments(metrics, min_fast_frames=5)

        self.assertEqual(len(segments), 1)
        segment = segments[0]
        self.assertEqual(segment.segment_type, "very_fast_motion")
        self.assertEqual(segment.start, 10)
        self.assertEqual(segment.end, 50)
        self.assertEqual(segment.frame_count, 5)

    def test_jittered_static_window_honors_mean_multiplier_config(self):
        classified_metrics = [(_metric(index, 0.014), "normal") for index in range(21)]

        segments = _detect_jittered_static_windows(
            classified_metrics=classified_metrics,
            existing_segments=[],
            static_threshold=0.010,
            fast_threshold=0.100,
            very_fast_threshold=0.200,
            min_static_frames=21,
            min_low_ratio=0.70,
            mean_multiplier=1.20,
        )

        self.assertEqual(segments, [])

    def test_jittered_static_window_does_not_expand_sparse_segment_ranges(self):
        classified_metrics = [
            (_metric(1_000_000_000, 0.010), "normal"),
            (_metric(1_000_000_100, 0.010), "normal"),
        ]
        existing_segments = [
            Segment(
                segment_type="fast_motion",
                start=0,
                end=2_000_000_000,
                frame_count=2,
                mean_motion=0.5,
                reason="sparse huge range",
            )
        ]

        def guarded_range(start, stop=None, step=1):
            if stop is not None and abs(stop - start) > 1000:
                raise AssertionError("range expansion is not allowed for sparse segment spans")
            return range(start) if stop is None else range(start, stop, step)

        with patch("frame_timing_agent.segment_detector.range", guarded_range, create=True):
            segments = _detect_jittered_static_windows(
                classified_metrics=classified_metrics,
                existing_segments=existing_segments,
                static_threshold=0.010,
                fast_threshold=0.100,
                very_fast_threshold=0.200,
                min_static_frames=2,
                min_low_ratio=0.70,
                mean_multiplier=1.20,
            )

        self.assertEqual(segments, [])


if __name__ == "__main__":
    unittest.main()
