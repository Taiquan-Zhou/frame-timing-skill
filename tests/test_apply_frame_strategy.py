import tempfile
import unittest
from dataclasses import replace
from unittest.mock import patch
from pathlib import Path
import csv
import re

import cv2
import numpy as np

from frame_timing_agent.analysis import compute_input_digest
from frame_timing_agent.apply_frame_strategy import (
    _copy_frame,
    apply_strategy,
    apply_validated_strategy,
    choose_uniform_sources,
)
from frame_timing_agent.configuration import resolve_strategy_request
from frame_timing_agent.contracts import (
    SCHEMA_VERSION,
    AnalysisRange,
    AnalysisResult,
    FrameAnalysis,
    PolicyName,
    QualitySummary,
    StrategyRequest,
    TrajectorySummary,
)
from frame_timing_agent.frame_source import FrameRecord, load_frame_records
from frame_timing_agent.strategy_planner import plan_strategy
from frame_timing_agent.strategy_validator import validate_strategy


def _write_image(path: Path, value: int = 120) -> None:
    image = np.full((8, 8, 3), value, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


def _validated_context(root: Path, *, range_kind: str = "active_motion"):
    input_dir = root / "input"
    input_dir.mkdir()
    for index in range(8):
        _write_image(input_dir / f"frame_{index:06d}_src_{index:06d}.png", value=50 + index)
    records = load_frame_records(input_dir, fps=30.0)
    frames = tuple(
        FrameAnalysis(
            source_index=index,
            output_index=index,
            timestamp_sec=index / 30.0,
            sharpness=100.0,
            brightness=128.0,
            contrast=40.0,
            dx=0.5 if range_kind == "active_motion" else 0.0,
            dy=0.0,
            rotation_deg=0.0,
            scale=1.0,
            motion_confidence=0.95,
            normalized_residual_spatial_iqr=0.0001,
            normalized_residual_spatial_p90=0.0002,
            inlier_spatial_coverage=0.8,
            jitter_score=0.9 if range_kind == "jitter" else 0.05,
            jitter_confidence=0.95 if range_kind == "jitter" else 0.0,
            low_quality_candidate=False,
        )
        for index in range(8)
    )
    analysis = AnalysisResult(
        schema_version=SCHEMA_VERSION,
        run_id="execution-run",
        input_digest=compute_input_digest(records),
        frame_count=len(frames),
        fps=30.0,
        width=8,
        height=8,
        motion_confidence=0.95,
        quality_summary=QualitySummary(100.0, 100.0, 128.0, 40.0, 0),
        trajectory_summary=TrajectorySummary(0.95, 0.01, 0.1, 0, 0, 0),
        frames=frames,
        ranges=(AnalysisRange(0, 7, range_kind, 0.95, f"high_confidence_{range_kind}"),),
        warnings=(),
    )
    config = resolve_strategy_request(StrategyRequest(PolicyName.BALANCED))
    candidate = plan_strategy(analysis, config)
    validation = validate_strategy(analysis, candidate, config)
    return input_dir, records, analysis, candidate, validation


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

    def test_apply_strategy_select_sources_keeps_exact_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            output_dir = root / "output"
            input_dir.mkdir()
            for index in range(6):
                _write_image(input_dir / f"frame_{index:06d}_src_{index:06d}.jpg", value=80 + index)
            records = load_frame_records(input_dir, fps=30.0)
            strategy = {
                "version": 2,
                "operations": [
                    {
                        "op": "select_sources",
                        "range": {"start": 1, "end": 4},
                        "sources": [1, 4],
                        "reason": "stable jitter keyframes",
                    }
                ],
            }

            result = apply_strategy(records, strategy, output_dir)

            self.assertEqual(result.output_count, 4)
            output_records = load_frame_records(output_dir, fps=30.0)
            self.assertEqual([record.source_index for record in output_records], [0, 1, 4, 5])
            self.assertEqual([record.is_duplicate for record in output_records], [False, False, False, False])

    def test_overlapping_validation_does_not_expand_sparse_ranges(self):
        records = [FrameRecord(1_000_000_000, 0, 0.0, Path("frame_000000_src_1000000000.jpg"))]
        strategy = {
            "version": 1,
            "operations": [
                {
                    "op": "keep",
                    "range": {"start": 0, "end": 2_000_000_000},
                    "reason": "huge sparse range",
                }
            ],
        }

        def guarded_range(start, stop=None, step=1):
            if stop is not None and abs(stop - start) > 1000:
                raise AssertionError("range expansion is not allowed for sparse operation spans")
            return range(start) if stop is None else range(start, stop, step)

        with tempfile.TemporaryDirectory() as tmp:
            with patch("frame_timing_agent.apply_frame_strategy.range", guarded_range, create=True):
                with self.assertRaises(FileNotFoundError):
                    apply_strategy(records, strategy, Path(tmp) / "output")

    def test_apply_validated_strategy_copies_selected_sources_without_changing_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, records, analysis, candidate, validation = _validated_context(root, range_kind="jitter")
            output_dir = root / "artifacts" / "output_frames"

            result = apply_validated_strategy(records, analysis, candidate, validation, output_dir)

            self.assertLess(result.output_frame_count, 8)
            self.assertEqual(result.output_frame_count, len(candidate.selected_sources))
            self.assertEqual(result.selected_sources, candidate.selected_sources)
            self.assertEqual(result.output_manifest, "run_manifest.json")
            self.assertRegex(result.output_digest, r"^sha256:[0-9a-f]{64}$")
            output_records = load_frame_records(output_dir, fps=30.0)
            self.assertEqual([record.source_index for record in output_records], list(candidate.selected_sources))
            records_by_source = {record.source_index: record for record in records}
            for output_record in output_records:
                self.assertEqual(
                    output_record.path.read_bytes(), records_by_source[output_record.source_index].path.read_bytes()
                )

    def test_apply_validated_strategy_rejects_input_changed_after_analysis_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, records, analysis, candidate, validation = _validated_context(root)
            output_dir = root / "output"
            _write_image(records[3].path, value=240)

            with self.assertRaisesRegex(ValueError, "input digest"):
                apply_validated_strategy(records, analysis, candidate, validation, output_dir)

            self.assertFalse(output_dir.exists())

    def test_apply_validated_strategy_revalidates_candidate_instead_of_trusting_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, records, analysis, candidate, validation = _validated_context(root)
            output_dir = root / "output"
            tampered_candidate = replace(
                candidate,
                selected_sources=tuple(source for source in candidate.selected_sources if source != 4),
            )

            with self.assertRaisesRegex(ValueError, "validation"):
                apply_validated_strategy(records, analysis, tampered_candidate, validation, output_dir)

            self.assertFalse(output_dir.exists())

    def test_apply_validated_strategy_rejects_mismatched_validation_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, records, analysis, candidate, validation = _validated_context(root)
            output_dir = root / "output"
            forged = replace(validation, candidate_digest="sha256:forged")

            with self.assertRaisesRegex(ValueError, "candidate digest"):
                apply_validated_strategy(records, analysis, candidate, forged, output_dir)

            self.assertFalse(output_dir.exists())

    def test_apply_validated_strategy_rejects_output_overlapping_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir, records, analysis, candidate, validation = _validated_context(root)
            before = {record.path.name: record.path.read_bytes() for record in records}

            with self.assertRaisesRegex(ValueError, "overlap"):
                apply_validated_strategy(records, analysis, candidate, validation, input_dir)

            self.assertEqual(before, {record.path.name: record.path.read_bytes() for record in records})

    def test_apply_validated_strategy_rejects_input_changed_during_copy_and_cleans_generated_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, records, analysis, candidate, validation = _validated_context(root)
            output_dir = root / "output"
            copy_count = 0

            def mutating_copy(source: Path, destination: Path) -> None:
                nonlocal copy_count
                _copy_frame(source, destination)
                copy_count += 1
                if copy_count == 1:
                    _write_image(records[-1].path, value=250)

            with patch("frame_timing_agent.apply_frame_strategy._copy_frame", side_effect=mutating_copy):
                with self.assertRaisesRegex(ValueError, "changed during execution"):
                    apply_validated_strategy(records, analysis, candidate, validation, output_dir)

            self.assertTrue(output_dir.exists())
            self.assertEqual(list(output_dir.iterdir()), [])

    def test_apply_validated_strategy_cleans_partial_outputs_when_copy_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, records, analysis, candidate, validation = _validated_context(root)
            output_dir = root / "output"
            copy_count = 0

            def failing_copy(source: Path, destination: Path) -> None:
                nonlocal copy_count
                copy_count += 1
                if copy_count == 2:
                    raise OSError("simulated copy failure")
                _copy_frame(source, destination)

            with patch("frame_timing_agent.apply_frame_strategy._copy_frame", side_effect=failing_copy):
                with self.assertRaisesRegex(OSError, "simulated copy failure"):
                    apply_validated_strategy(records, analysis, candidate, validation, output_dir)

            self.assertTrue(output_dir.exists())
            self.assertEqual(list(output_dir.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
