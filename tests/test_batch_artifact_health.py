import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from frame_timing_agent.batch_artifact_health import run_batch_artifact_health_check
from frame_timing_agent.batch_timing_agent import BatchTimingItem, run_batch_timing_agent


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
        _write_image(frame_dir / f"frame_{index:06d}_src_{index:06d}.jpg", 70 + index)


class BatchArtifactHealthTest(unittest.TestCase):
    def test_valid_batch_artifact_writes_ok_maintenance_report(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "agent_files" / "health_ok"
            _make_frames(frames, 12)

            run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=12,
                write=True,
            )

            result = run_batch_artifact_health_check(artifact_root)

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.errors, [])
            self.assertGreaterEqual(result.checked_links, 2)
            self.assertGreater(result.checked_output_frames, 0)
            self.assertTrue(result.report_path.exists())
            self.assertTrue(result.json_path.exists())
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("阶段 9：Agent 产物健康检查", report)
            self.assertIn("状态：ok", report)
            self.assertIn("检查输出帧溯源数", report)
            data = json.loads(result.json_path.read_text(encoding="utf-8"))
            self.assertEqual(data["status"], "ok")
            self.assertGreater(data["checked_output_frames"], 0)

    def test_batch_artifacts_do_not_leak_private_paths_and_health_uses_hash_provenance(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "private_customer_frames"
            artifact_root = root / "agent_files" / "privacy_ok"
            _make_frames(frames, 10)

            run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=10,
                write=True,
            )

            private_root = str(root)
            text_artifacts = [
                artifact_root / "analysis" / "batch_summary.json",
                artifact_root / "analysis" / "batch_summary.csv",
                artifact_root / "analysis" / "human_review.md",
                artifact_root / "analysis" / "review_dashboard.md",
                artifact_root / "analysis" / "maintenance_report.json",
                artifact_root / "analysis" / "maintenance_report.md",
                artifact_root / "sample" / "analysis" / "strategy.json",
                artifact_root / "sample" / "analysis" / "human_review.md",
                artifact_root / "sample" / "analysis" / "execution_audit.json",
            ]
            for path in text_artifacts:
                self.assertNotIn(private_root, path.read_text(encoding="utf-8"), path)

            shutil.rmtree(frames)
            result = run_batch_artifact_health_check(artifact_root)

            self.assertEqual(result.status, "ok")
            self.assertGreater(result.checked_output_frames, 0)

    def test_missing_dashboard_target_marks_artifact_failed(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "agent_files" / "health_bad_link"
            _make_frames(frames, 8)

            run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=8,
                write=False,
            )
            dashboard = artifact_root / "analysis" / "review_dashboard.md"
            dashboard.write_text(
                dashboard.read_text(encoding="utf-8") + "\n[bad link](../missing/file.png)\n",
                encoding="utf-8",
            )

            result = run_batch_artifact_health_check(artifact_root)

            self.assertEqual(result.status, "failed")
            self.assertTrue(any("missing dashboard target" in error for error in result.errors))

    def test_script_entrypoint_checks_existing_artifact_root(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "agent_files" / "health_cli"
            _make_frames(frames, 6)
            run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=6,
                write=False,
            )

            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts") / "frame_timing_agent" / "batch_artifact_health.py"),
                    "--artifact_root",
                    str(artifact_root),
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("Health status: ok", completed.stdout)

    def test_preview_batch_without_output_frames_is_ok_with_warning(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "agent_files" / "health_preview"
            _make_frames(frames, 6)

            run_batch_timing_agent(
                [BatchTimingItem(name="preview", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=6,
                write=False,
            )

            result = run_batch_artifact_health_check(artifact_root)

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.checked_output_frames, 0)
            self.assertTrue(any("preview mode has no output_frames" in warning for warning in result.warnings))

    def test_modified_output_frame_is_failed_as_trick(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "agent_files" / "health_modified_output"
            _make_frames(frames, 8)

            run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=8,
                write=True,
            )
            output_dir = artifact_root / "sample" / "output_frames"
            first_output = next(path for path in output_dir.iterdir() if path.suffix.lower() == ".jpg")
            first_output.unlink()
            _write_image(first_output, 250)

            result = run_batch_artifact_health_check(artifact_root)

            self.assertEqual(result.status, "failed")
            self.assertTrue(any("output frame differs from recorded source hash" in error for error in result.errors))

    def test_unexpected_file_in_output_frames_is_failed(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "agent_files" / "health_output_boundary"
            _make_frames(frames, 8)

            run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=8,
                write=True,
            )
            output_dir = artifact_root / "sample" / "output_frames"
            (output_dir / "debug_report.md").write_text("analysis must not be here", encoding="utf-8")

            result = run_batch_artifact_health_check(artifact_root)

            self.assertEqual(result.status, "failed")
            self.assertTrue(any("unexpected output_frames file" in error for error in result.errors))

    def test_summary_path_outside_artifact_root_is_failed(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "agent_files" / "health_path_escape"
            _make_frames(frames, 6)

            run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=6,
                write=False,
            )
            summary_path = artifact_root / "analysis" / "batch_summary.json"
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            summary["items"][0]["human_review_path"] = str(root / "outside.md")
            summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

            result = run_batch_artifact_health_check(artifact_root)

            self.assertEqual(result.status, "failed")
            self.assertTrue(any("human_review_path is outside artifact_root" in error for error in result.errors))

    def test_unregistered_item_directory_is_failed(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "agent_files" / "health_orphan"
            _make_frames(frames, 6)

            run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=6,
                write=False,
            )
            orphan = artifact_root / "old_item"
            orphan.mkdir()

            result = run_batch_artifact_health_check(artifact_root)

            self.assertEqual(result.status, "failed")
            self.assertTrue(any("unregistered item directory" in error for error in result.errors))


if __name__ == "__main__":
    unittest.main()
