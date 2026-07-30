import json
import inspect
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

import cv2
import numpy as np

import frame_timing_agent.simple_cli as legacy_simple_cli

TEST_TMP_ROOT = Path.cwd() / ".tmp_tests"


def _tempdir():
    TEST_TMP_ROOT.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT)


def _write_image(path: Path, value: int) -> None:
    image = np.full((16, 16, 3), value, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


def _make_frames(frame_dir: Path, count: int) -> None:
    frame_dir.mkdir(parents=True)
    for index in range(count):
        _write_image(frame_dir / f"frame_{index:06d}.jpg", 80 + index)


class SimpleCliTest(unittest.TestCase):
    def test_legacy_cli_signature_and_module_do_not_expose_agent_contracts(self):
        self.assertIn("argv", inspect.signature(legacy_simple_cli.main).parameters)
        self.assertFalse(hasattr(legacy_simple_cli, "PolicyName"))
        self.assertFalse(hasattr(legacy_simple_cli, "StrategyRequest"))

    def test_cli_processes_one_frame_directory_with_one_argument(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "clean_frames"
            artifact_root = root / "output" / "frame_timing_run"
            _make_frames(frames, 5)
            script = Path(__file__).resolve().parents[1] / "scripts" / "frame_timing_agent" / "simple_cli.py"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(frames),
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Output frames:", completed.stdout)
            self.assertTrue((artifact_root / "clean_frames" / "output_frames" / "selected_frames.txt").exists())
            summary = json.loads((artifact_root / "analysis" / "batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["success_count"], 1)
            self.assertEqual(summary["items"][0]["name"], "clean_frames")

    def test_main_direct_call_processes_one_frame_directory(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "direct_frames"
            artifact_root = root / "output" / "direct_frame_timing_run"
            _make_frames(frames, 5)
            stdout = StringIO()

            with redirect_stdout(stdout):
                exit_code = legacy_simple_cli.main(
                    [
                        str(frames),
                        "--artifact_root",
                        str(artifact_root),
                        "--name",
                        "direct",
                        "--limit_first_n",
                        "5",
                    ]
                )

            self.assertEqual(exit_code, 0)
            self.assertIn("OK: direct analyzed=5", stdout.getvalue())
            self.assertTrue((artifact_root / "direct" / "output_frames" / "selected_frames.txt").exists())

    def test_cli_accepts_reconstruction_balanced_mode(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "shaky_frames"
            artifact_root = root / "output" / "frame_timing_run"
            _make_frames(frames, 5)
            script = Path(__file__).resolve().parents[1] / "scripts" / "frame_timing_agent" / "simple_cli.py"

            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    str(frames),
                    "--mode",
                    "reconstruction_balanced",
                ],
                cwd=root,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            strategy = json.loads(
                (artifact_root / "shaky_frames" / "analysis" / "strategy.json").read_text(encoding="utf-8")
            )
            self.assertEqual(strategy["version"], 2)
            self.assertEqual(strategy["options"]["mode"], "reconstruction_balanced")
            self.assertEqual(strategy["options"]["jitter_reduction_mode"], "v2")


if __name__ == "__main__":
    unittest.main()
