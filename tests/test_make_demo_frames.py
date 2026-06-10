import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from frame_timing_agent.frame_source import load_frame_records


class MakeDemoFramesTest(unittest.TestCase):
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
