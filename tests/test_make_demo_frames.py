import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from frame_timing_agent.demo_frames import generate_demo_frames, main as demo_frames_main
from frame_timing_agent.frame_source import load_frame_records


class MakeDemoFramesTest(unittest.TestCase):
    def test_generate_demo_frames_replaces_old_demo_images(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output" / "demo_frames" / "sample"
            output_dir.mkdir(parents=True)
            stale = output_dir / "frame_999999_src_999999.jpg"
            stale.write_bytes(b"stale")

            paths = generate_demo_frames(output_dir=output_dir, count=4)

            self.assertEqual(len(paths), 4)
            self.assertFalse(stale.exists())
            records = load_frame_records(output_dir, limit_first_n=None)
            self.assertEqual([record.source_index for record in records], [0, 1, 2, 3])

    def test_demo_frames_main_reports_generated_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output" / "demo_frames" / "sample"
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = demo_frames_main(["--output_dir", str(output_dir), "--count", "3"])

            self.assertEqual(exit_code, 0)
            self.assertIn("Generated 3 demo frames", stdout.getvalue())
            self.assertEqual(len(list(output_dir.glob("*.jpg"))), 3)

    def test_demo_frames_rejects_output_outside_output_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                generate_demo_frames(output_dir=Path(tmp) / "demo_frames", count=3)

    def test_script_generates_loadable_demo_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "output" / "demo_frames" / "sample"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path("examples") / "make_demo_frames.py"),
                    "--output_dir",
                    str(output_dir),
                    "--count",
                    "12",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Generated 12 demo frames", completed.stdout)
            self.assertEqual(len(list(output_dir.glob("*.jpg"))), 12)
            records = load_frame_records(output_dir, limit_first_n=None)
            self.assertEqual([record.source_index for record in records], list(range(12)))


if __name__ == "__main__":
    unittest.main()
