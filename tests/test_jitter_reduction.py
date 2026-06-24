import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from frame_timing_agent.auto_timing_agent import run_timing_agent
from frame_timing_agent.frame_source import load_frame_records
from frame_timing_agent.jitter_detector import JitterRange, detect_jitter_ranges
from frame_timing_agent.jitter_strategy import build_jitter_reduction_strategy, merge_jitter_with_base_strategy
from frame_timing_agent.motion_estimator import MotionEstimate, estimate_frame_motion
from frame_timing_agent.stable_frame_selector import select_stable_sources


def _write_shifted_grid(path: Path, x_offset: int, blur: bool = False) -> None:
    image = np.full((96, 128, 3), 32, dtype=np.uint8)
    for x in range(16 + x_offset, 128, 24):
        cv2.line(image, (x, 0), (x, 95), (220, 220, 220), 1)
    for y in range(12, 96, 20):
        cv2.line(image, (0, y), (127, y), (160, 160, 160), 1)
    cv2.circle(image, (64 + x_offset, 48), 8, (30, 180, 240), -1)
    if blur:
        image = cv2.GaussianBlur(image, (9, 9), 0)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


def _write_shifted_grid_xy(path: Path, x_offset: int, y_offset: int, blur: bool = False) -> None:
    image = np.full((96, 128, 3), 32, dtype=np.uint8)
    for x in range(16 + x_offset, 128, 24):
        cv2.line(image, (x, 0), (x, 95), (220, 220, 220), 1)
    for y in range(12 + y_offset, 96, 20):
        cv2.line(image, (0, y), (127, y), (160, 160, 160), 1)
    cv2.circle(image, (64 + x_offset, 48 + y_offset), 8, (30, 180, 240), -1)
    if blur:
        image = cv2.GaussianBlur(image, (9, 9), 0)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


def _make_shifted_frames(frame_dir: Path, offsets: list[int], blurred_indices: set[int] | None = None) -> None:
    frame_dir.mkdir()
    blurred_indices = blurred_indices or set()
    for index, offset in enumerate(offsets):
        _write_shifted_grid(
            frame_dir / f"frame_{index:06d}_src_{index:06d}.jpg",
            x_offset=offset,
            blur=index in blurred_indices,
        )


def _make_vertical_shifted_frames(frame_dir: Path, offsets: list[int]) -> None:
    frame_dir.mkdir()
    for index, offset in enumerate(offsets):
        _write_shifted_grid_xy(
            frame_dir / f"frame_{index:06d}_src_{index:06d}.jpg",
            x_offset=0,
            y_offset=offset,
        )


class JitterReductionTest(unittest.TestCase):
    def test_motion_estimator_distinguishes_smooth_pan_from_alternating_shake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            smooth = root / "smooth"
            shake = root / "shake"
            _make_shifted_frames(smooth, [0, 1, 2, 3, 4, 5, 6, 7])
            _make_shifted_frames(shake, [0, 6, -6, 6, -6, 6, -6, 0])

            smooth_estimates = estimate_frame_motion(load_frame_records(smooth, limit_first_n=None))
            shake_estimates = estimate_frame_motion(load_frame_records(shake, limit_first_n=None))
            smooth_ranges = detect_jitter_ranges(smooth_estimates, min_jitter_frames=3)
            shake_ranges = detect_jitter_ranges(shake_estimates, min_jitter_frames=3)

        self.assertEqual(smooth_ranges, [])
        self.assertEqual(len(shake_ranges), 1)
        self.assertGreaterEqual(shake_ranges[0].frame_count, 5)

    def test_motion_estimator_detects_vertical_alternating_shake(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shake = root / "vertical_shake"
            _make_vertical_shifted_frames(shake, [0, 6, -6, 6, -6, 6, -6, 0])

            estimates = estimate_frame_motion(load_frame_records(shake, limit_first_n=None))
            ranges = detect_jitter_ranges(estimates, min_jitter_frames=3)

        self.assertEqual(len(ranges), 1)
        self.assertGreaterEqual(ranges[0].frame_count, 5)

    def test_jitter_strategy_selects_stable_sources_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "shake"
            _make_shifted_frames(frames, [0, 6, -6, 6, -6, 6, -6, 0], blurred_indices={3})
            records = load_frame_records(frames, limit_first_n=None)
            estimates = estimate_frame_motion(records)

            strategy = build_jitter_reduction_strategy(
                records=records,
                estimates=estimates,
                frame_dir=frames,
                limit_first_n=None,
                max_output_ratio=0.60,
                min_jitter_frames=3,
            )

        self.assertEqual(strategy["options"]["jitter_reduction_mode"], "v2")
        self.assertEqual(strategy["version"], 2)
        self.assertEqual(len(strategy["operations"]), 1)
        operation = strategy["operations"][0]
        self.assertEqual(operation["op"], "select_sources")
        self.assertLess(len(operation["sources"]), len(records))
        self.assertEqual(operation["sources"], sorted(set(operation["sources"])))
        self.assertNotIn(3, operation["sources"])

    def test_merge_jitter_with_base_strategy_clips_overlapping_base_operations(self):
        records = [
            type("Record", (), {"source_index": source_index})()
            for source_index in range(10)
        ]
        base_strategy = {
            "version": 1,
            "input": {"frame_dir_name": "frames", "limit_first_n": None},
            "options": {"static_keep_count": 5, "interpret_ranges_by": "source_index"},
            "operations": [
                {
                    "op": "keep_uniform",
                    "range": {"start": 0, "end": 9},
                    "count": 5,
                    "reason": "long static section",
                    "source": "auto_detection",
                }
            ],
        }
        jitter_strategy = {
            "version": 2,
            "input": {"frame_dir_name": "frames", "limit_first_n": None},
            "options": {"jitter_reduction_mode": "v2", "interpret_ranges_by": "source_index"},
            "operations": [
                {
                    "op": "select_sources",
                    "range": {"start": 4, "end": 6},
                    "sources": [5],
                    "reason": "stable jitter keyframe",
                    "source": "jitter_reduction_v2",
                }
            ],
        }

        merged = merge_jitter_with_base_strategy(base_strategy, jitter_strategy, records)

        self.assertEqual(merged["version"], 2)
        self.assertEqual(
            [(op["op"], op["range"]["start"], op["range"]["end"]) for op in merged["operations"]],
            [
                ("keep_uniform", 0, 3),
                ("select_sources", 4, 6),
                ("keep_uniform", 7, 9),
            ],
        )
        self.assertEqual(merged["operations"][1]["sources"], [5])
        self.assertLessEqual(
            sum(op.get("count", 0) for op in merged["operations"] if op["op"] == "keep_uniform"),
            5,
        )

    def test_merge_jitter_with_base_strategy_preserves_manual_overrides(self):
        records = [
            type("Record", (), {"source_index": source_index})()
            for source_index in range(10)
        ]
        base_strategy = {
            "version": 1,
            "input": {"frame_dir_name": "frames", "limit_first_n": None},
            "options": {"interpret_ranges_by": "source_index"},
            "operations": [
                {
                    "op": "duplicate_range",
                    "range": {"start": 3, "end": 7},
                    "total_instances": 4,
                    "reason": "manual slow down",
                    "source": "manual_override",
                }
            ],
        }
        jitter_strategy = {
            "version": 2,
            "input": {"frame_dir_name": "frames", "limit_first_n": None},
            "options": {"jitter_reduction_mode": "v2", "interpret_ranges_by": "source_index"},
            "operations": [
                {
                    "op": "select_sources",
                    "range": {"start": 4, "end": 6},
                    "sources": [5],
                    "reason": "stable jitter keyframe",
                    "source": "jitter_reduction_v2",
                }
            ],
        }

        merged = merge_jitter_with_base_strategy(base_strategy, jitter_strategy, records)

        self.assertEqual(merged["operations"], base_strategy["operations"])
        self.assertIn(
            "jitter_reduction_v2 overlapped manual_override and was skipped",
            merged["options"]["merge_warnings"],
        )

    def test_stable_frame_selector_keeps_temporal_coverage(self):
        estimates = [
            MotionEstimate(
                source_index=index,
                output_index=index,
                dx=6.0 if index % 2 else -6.0,
                dy=0.0,
                magnitude=6.0,
                response=1.0,
                sharpness=1000.0 - index,
                bad_quality_candidate=False,
            )
            for index in range(10)
        ]
        jitter_range = JitterRange(
            start=0,
            end=9,
            frame_count=10,
            mean_jitter_score=12.0,
            reason="test jitter",
        )

        selected = select_stable_sources(estimates, jitter_range, max_output_ratio=0.30)

        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[0], 0)
        self.assertEqual(selected[-1], 9)
        self.assertLessEqual(max(right - left for left, right in zip(selected, selected[1:])), 5)

    def test_jitter_strategy_records_configured_detection_thresholds(self):
        records = [
            type("Record", (), {"source_index": source_index})()
            for source_index in range(8)
        ]
        estimates = [
            MotionEstimate(
                source_index=index,
                output_index=index,
                dx=8.0 if index % 2 else -8.0,
                dy=0.0,
                magnitude=8.0,
                response=0.50,
                sharpness=900.0,
                bad_quality_candidate=False,
            )
            for index in range(8)
        ]

        strategy = build_jitter_reduction_strategy(
            records=records,
            estimates=estimates,
            frame_dir="frames",
            limit_first_n=None,
            max_output_ratio=0.50,
            min_jitter_frames=3,
            min_motion=7.0,
            min_response=0.25,
            min_sharpness=120.0,
        )

        self.assertEqual(strategy["options"]["min_motion"], 7.0)
        self.assertEqual(strategy["options"]["min_response"], 0.25)
        self.assertEqual(strategy["options"]["min_sharpness"], 120.0)

    def test_timing_agent_reconstruction_balanced_mode_writes_auditable_select_sources_strategy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "shake"
            artifact_dir = root / "output" / "jitter"
            _make_shifted_frames(frames, [0, 6, -6, 6, -6, 6, -6, 0], blurred_indices={4})

            result = run_timing_agent(
                frames,
                artifact_dir,
                limit_first_n=None,
                mode="reconstruction_balanced",
                write=True,
            )

            strategy = json.loads((artifact_dir / "analysis" / "strategy.json").read_text(encoding="utf-8"))
            selected_rows = (artifact_dir / "output_frames" / "selected_frames.txt").read_text(encoding="utf-8")

        self.assertEqual(result.analyzed_count, 8)
        self.assertEqual(strategy["version"], 2)
        self.assertEqual(strategy["options"]["mode"], "reconstruction_balanced")
        self.assertEqual(strategy["options"]["jitter_reduction_mode"], "v2")
        self.assertEqual(strategy["operations"][0]["op"], "select_sources")
        self.assertLess(result.estimated_output_count, result.analyzed_count)
        self.assertNotIn("_dup_", selected_rows)

    def test_timing_agent_override_config_can_tune_jitter_thresholds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "shake"
            artifact_dir = root / "output" / "jitter_override"
            override_config = root / "override.json"
            _make_shifted_frames(frames, [0, 6, -6, 6, -6, 6, -6, 0])
            override_config.write_text(
                json.dumps(
                    {
                        "config": {
                            "jitter_min_motion": 1000.0,
                            "jitter_min_response": 0.50,
                            "jitter_min_sharpness": 200.0,
                        }
                    }
                ),
                encoding="utf-8",
            )

            run_timing_agent(
                frames,
                artifact_dir,
                limit_first_n=None,
                mode="reconstruction_balanced",
                write=False,
                override_config_path=override_config,
            )

            strategy = json.loads((artifact_dir / "analysis" / "strategy.json").read_text(encoding="utf-8"))

        self.assertEqual(strategy["options"]["min_motion"], 1000.0)
        self.assertEqual(strategy["options"]["min_response"], 0.50)
        self.assertEqual(strategy["options"]["min_sharpness"], 200.0)
        self.assertFalse(any(operation["op"] == "select_sources" for operation in strategy["operations"]))
