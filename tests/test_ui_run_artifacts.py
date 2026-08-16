import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from frame_timing_agent.ui.run_artifacts import (
    bind_strategy_snapshot,
    capture_input_snapshot,
    load_persisted_thumbnails,
    load_bound_strategy,
    persist_thumbnails,
    verify_input_snapshot,
    verify_output_snapshot,
    write_input_snapshot,
)
from frame_timing_agent.ui.view_model import ThumbnailView


class UiRunArtifactsTest(unittest.TestCase):
    def test_missing_thumbnail_manifest_is_distinct_from_empty_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            analysis_dir = Path(tmp) / "analysis"

            self.assertIsNone(load_persisted_thumbnails(analysis_dir))

            persist_thumbnails(analysis_dir, ())
            self.assertEqual(load_persisted_thumbnails(analysis_dir), ())

    def test_input_snapshot_detects_content_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            analysis = root / "run" / "analysis"
            frames.mkdir()
            frame = frames / "frame_000001.jpg"
            frame.write_bytes(b"original")
            strategy = analysis / "strategy.json"
            strategy.parent.mkdir(parents=True)
            strategy.write_text("{}", encoding="utf-8")

            snapshot = capture_input_snapshot(frames, fps=30.0, limit_first_n=None)
            write_input_snapshot(analysis, bind_strategy_snapshot(snapshot, strategy))
            self.assertTrue(verify_input_snapshot(analysis, frames, fps=30.0, limit_first_n=None))

            frame.write_bytes(b"changed")

            with self.assertRaisesRegex(ValueError, "changed since analysis"):
                verify_input_snapshot(analysis, frames, fps=30.0, limit_first_n=None)

    def test_input_snapshot_detects_strategy_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            analysis = root / "run" / "analysis"
            frames.mkdir()
            (frames / "frame_000001.jpg").write_bytes(b"original")
            strategy = analysis / "strategy.json"
            strategy.parent.mkdir(parents=True)
            strategy.write_text('{"operations": []}', encoding="utf-8")
            snapshot = capture_input_snapshot(frames, fps=30.0, limit_first_n=None)
            write_input_snapshot(analysis, bind_strategy_snapshot(snapshot, strategy))

            strategy.write_text('{"operations": [{"op": "keep"}]}', encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "strategy changed"):
                verify_input_snapshot(analysis, frames, fps=30.0, limit_first_n=None)

            with self.assertRaisesRegex(ValueError, "strategy changed"):
                load_bound_strategy(analysis)

    def test_output_snapshot_detects_bytes_from_a_different_source_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            analysis = root / "run" / "analysis"
            output = root / "run" / "output_frames"
            frames.mkdir()
            output.mkdir(parents=True)
            source = frames / "frame_000001.jpg"
            source.write_bytes(b"original")
            strategy = analysis / "strategy.json"
            strategy.parent.mkdir(parents=True)
            strategy.write_text('{"operations": []}', encoding="utf-8")
            snapshot = capture_input_snapshot(frames, fps=30.0, limit_first_n=None)
            write_input_snapshot(analysis, bind_strategy_snapshot(snapshot, strategy))
            output_name = "frame_000000_src_000001.jpg"
            (output / output_name).write_bytes(b"temporary replacement")
            (output / "selected_frames.txt").write_text(
                "output_index\tsource_index\ttimestamp_sec\tinstance_id\tis_duplicate\toperation\tsource_sha256\tpath\n"
                f"0\t1\t0.0\t0\t0\tkeep\tignored\t{output_name}\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "does not match analysis snapshot"):
                verify_output_snapshot(analysis, output)

    def test_persisted_thumbnails_survive_source_removal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = root / "run" / "analysis"
            source = root / "frame_000010.jpg"
            source.write_bytes(b"thumbnail")
            thumbnails = (ThumbnailView(10, source, "keep"),)

            frozen = persist_thumbnails(analysis, thumbnails)
            source.unlink()
            loaded = load_persisted_thumbnails(analysis)

            self.assertEqual(loaded, frozen)
            self.assertEqual(loaded[0].path.read_bytes(), b"thumbnail")

    def test_failed_thumbnail_refresh_preserves_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = root / "run" / "analysis"
            original_source = root / "frame_000010.jpg"
            original_source.write_bytes(b"original thumbnail")
            original = persist_thumbnails(
                analysis,
                (ThumbnailView(10, original_source, "keep"),),
            )

            replacement_source = root / "frame_000020.jpg"
            replacement_source.write_bytes(b"replacement thumbnail")
            missing_source = root / "frame_000030.jpg"
            with self.assertRaises(FileNotFoundError):
                persist_thumbnails(
                    analysis,
                    (
                        ThumbnailView(20, replacement_source, "keep"),
                        ThumbnailView(30, missing_source, "drop"),
                    ),
                )

            loaded = load_persisted_thumbnails(analysis)
            self.assertEqual(loaded, original)
            self.assertEqual(loaded[0].path.read_bytes(), b"original thumbnail")
            self.assertEqual(
                [path.name for path in analysis.iterdir() if ".staging-" in path.name],
                [],
            )

    def test_failed_thumbnail_manifest_write_restores_previous_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis = root / "run" / "analysis"
            original_source = root / "frame_000010.jpg"
            original_source.write_bytes(b"original thumbnail")
            original = persist_thumbnails(
                analysis,
                (ThumbnailView(10, original_source, "keep"),),
            )

            replacement_source = root / "frame_000020.jpg"
            replacement_source.write_bytes(b"replacement thumbnail")
            with (
                patch(
                    "frame_timing_agent.ui.run_artifacts._write_json_atomic",
                    side_effect=OSError("disk full"),
                ),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                persist_thumbnails(
                    analysis,
                    (ThumbnailView(20, replacement_source, "keep"),),
                )

            loaded = load_persisted_thumbnails(analysis)
            self.assertEqual(loaded, original)
            self.assertEqual(loaded[0].path.read_bytes(), b"original thumbnail")
            self.assertEqual(
                [path.name for path in analysis.iterdir() if ".staging-" in path.name or ".backup-" in path.name],
                [],
            )


if __name__ == "__main__":
    unittest.main()
