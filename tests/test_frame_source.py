import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from frame_timing_agent.frame_source import FrameRecord, load_frame_records


def _write_image(path: Path, color: int) -> None:
    image = np.full((16, 16, 3), color, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


class FrameSourceLoaderTest(unittest.TestCase):
    def test_load_frame_records_raises_file_not_found_when_frame_dir_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_dir = Path(tmp) / "missing-frames"

            with self.assertRaisesRegex(FileNotFoundError, "frame directory does not exist"):
                load_frame_records(missing_dir, fps=30.0)

    def test_load_frame_records_raises_value_error_when_fps_is_not_positive(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            _write_image(frame_dir / "frame_000001.jpg", 30)

            with self.assertRaisesRegex(ValueError, "fps must be positive"):
                load_frame_records(frame_dir, fps=0.0)

            with self.assertRaisesRegex(ValueError, "fps must be positive"):
                load_frame_records(frame_dir, fps=-5.0)

    def test_load_frame_records_sorts_by_source_index_from_filenames(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            _write_image(frame_dir / "frame_000003.jpg", 30)
            _write_image(frame_dir / "frame_000001_src_000010.jpg", 60)
            _write_image(frame_dir / "frame_000002_src_000004.png", 90)

            records = load_frame_records(frame_dir, fps=20.0)

            self.assertEqual([record.source_index for record in records], [3, 4, 10])
            self.assertEqual([record.output_index for record in records], [0, 1, 2])
            self.assertEqual([record.timestamp_sec for record in records], [0.15, 0.2, 0.5])
            self.assertEqual(records[0], FrameRecord(3, 0, 0.15, frame_dir / "frame_000003.jpg"))

    def test_load_frame_records_limit_first_n_uses_sorted_source_index(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            _write_image(frame_dir / "frame_000005.jpg", 30)
            _write_image(frame_dir / "frame_000001.jpg", 60)
            _write_image(frame_dir / "frame_000003.jpg", 90)

            records = load_frame_records(frame_dir, fps=10.0, limit_first_n=2)

            self.assertEqual([record.source_index for record in records], [1, 3])
            self.assertEqual([record.output_index for record in records], [0, 1])

    def test_load_frame_records_ignores_linked_image_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame_dir = root / "frames"
            frame_dir.mkdir()
            _write_image(frame_dir / "frame_000001.jpg", 30)
            external = root / "frame_000002.jpg"
            _write_image(external, 60)
            linked = frame_dir / "frame_000002.jpg"
            try:
                linked.symlink_to(external)
            except OSError:
                self.skipTest("file symlinks are unavailable")

            records = load_frame_records(frame_dir, fps=10.0)

            self.assertEqual([record.source_index for record in records], [1])

    def test_load_frame_records_prefers_selected_frames_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            first = frame_dir / "frame_000005_src_000020.jpg"
            second = frame_dir / "frame_000001_src_000007.png"
            _write_image(first, 30)
            _write_image(second, 60)
            (frame_dir / "selected_frames.txt").write_text(
                "\n".join(
                    [
                        "output_index\tsource_index\ttimestamp_sec\tinstance_id\tis_duplicate\tpath",
                        f"0\t20\t1.250000\t2\ttrue\t{first.name}",
                        f"1\t7\t0.500000\t3\tfalse\t{second.name}",
                    ]
                ),
                encoding="utf-8",
            )

            records = load_frame_records(frame_dir, fps=30.0)

            self.assertEqual([record.source_index for record in records], [20, 7])
            self.assertEqual([record.output_index for record in records], [0, 1])
            self.assertEqual([record.timestamp_sec for record in records], [1.25, 0.5])
            self.assertEqual([record.instance_id for record in records], [2, 3])
            self.assertEqual([record.is_duplicate for record in records], [True, False])
            self.assertEqual(records[0].path, first)
            self.assertEqual(records[1].path, second)

    def test_load_frame_records_resolves_project_relative_selected_frame_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            frame_dir = project_root / "exports" / "test1_clean_frames"
            frame_dir.mkdir(parents=True)
            image_path = frame_dir / "frame_000000_src_000000.jpg"
            _write_image(image_path, 30)
            (frame_dir / "selected_frames.txt").write_text(
                "\n".join(
                    [
                        "output_index\tsource_index\ttimestamp_sec\tpath",
                        "0\t0\t0.000000\texports/test1_clean_frames/frame_000000_src_000000.jpg",
                    ]
                ),
                encoding="utf-8",
            )

            records = load_frame_records(frame_dir, fps=30.0)

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].path, image_path)

    def test_load_frame_records_raises_when_selected_frames_source_index_mismatches_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            image_path = frame_dir / "frame_000005_src_000020.jpg"
            _write_image(image_path, 30)
            (frame_dir / "selected_frames.txt").write_text(
                "\n".join(
                    [
                        "output_index\tsource_index\ttimestamp_sec\tinstance_id\tis_duplicate\tpath",
                        f"0\t21\t1.250000\t0\tfalse\t{image_path.name}",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaises(ValueError):
                load_frame_records(frame_dir, fps=30.0)

    def test_load_frame_records_raises_when_selected_frames_path_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame_dir = Path(tmp)
            missing_path = frame_dir / "frame_000005_src_000020.jpg"
            (frame_dir / "selected_frames.txt").write_text(
                "\n".join(
                    [
                        "output_index\tsource_index\ttimestamp_sec\tinstance_id\tis_duplicate\tpath",
                        f"0\t20\t1.250000\t0\tfalse\t{missing_path.name}",
                    ]
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(FileNotFoundError, "selected frame path does not exist"):
                load_frame_records(frame_dir, fps=30.0)


if __name__ == "__main__":
    unittest.main()
