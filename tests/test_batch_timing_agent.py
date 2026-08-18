import csv
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

import frame_timing_agent.batch_timing_agent as legacy_batch_timing
from frame_timing_agent.batch_timing_agent import BatchTimingItem, load_batch_manifest, run_batch_timing_agent


TEST_TMP_ROOT = Path.cwd() / ".tmp_tests"


def _tempdir():
    TEST_TMP_ROOT.mkdir(exist_ok=True)
    return tempfile.TemporaryDirectory(dir=TEST_TMP_ROOT)


def _write_image(path: Path, value: int) -> None:
    image = np.full((16, 16, 3), value, dtype=np.uint8)
    ok = cv2.imwrite(str(path), image)
    if not ok:
        raise AssertionError(f"failed to write image: {path}")


def _make_frames(frame_dir: Path, count: int, offset: int = 0) -> None:
    frame_dir.mkdir(parents=True)
    for index in range(count):
        source_index = offset + index
        _write_image(frame_dir / f"frame_{source_index:06d}_src_{source_index:06d}.jpg", 80 + index)


class BatchTimingAgentTest(unittest.TestCase):
    def test_legacy_runner_delegates_only_report_publication(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames_a = root / "frames_a"
            frames_b = root / "frames_b"
            artifact_root = root / "output" / "compatibility"
            _make_frames(frames_a, 2)
            _make_frames(frames_b, 2, offset=10)
            timing_result = legacy_batch_timing.TimingAgentResult(
                analyzed_count=2,
                estimated_output_count=1,
                artifact_dir=artifact_root / "unused",
                strategy_path=artifact_root / "unused" / "analysis" / "strategy.json",
                output_dir=None,
            )
            sentinel = object()

            with (
                patch.object(legacy_batch_timing, "run_timing_agent", return_value=timing_result) as run,
                patch.object(
                    legacy_batch_timing,
                    "publish_batch_timing_reports",
                    return_value=sentinel,
                ) as publish,
            ):
                result = run_batch_timing_agent(
                    [
                        BatchTimingItem(name="first", frames=frames_a),
                        BatchTimingItem(name="second", frames=frames_b),
                    ],
                    artifact_root=artifact_root,
                    limit_first_n=2,
                    write=False,
                )

            self.assertIs(result, sentinel)
            self.assertEqual(run.call_count, 2)
            published_root, published_results = publish.call_args.args
            self.assertEqual(published_root, artifact_root)
            self.assertEqual([item.name for item in published_results], ["first", "second"])
            self.assertTrue(all(item.status == "ok" for item in published_results))

    def test_legacy_write_mode_is_preserved_when_every_item_fails(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "write_failure"
            _make_frames(frames, 2)

            with (
                patch.object(legacy_batch_timing, "run_timing_agent", side_effect=RuntimeError("failed")),
                patch.object(legacy_batch_timing, "_write_batch_human_review") as write_review,
                patch.object(legacy_batch_timing, "run_batch_artifact_health_check", create=True),
            ):
                run_batch_timing_agent(
                    [BatchTimingItem(name="failed", frames=frames)],
                    artifact_root=artifact_root,
                    limit_first_n=2,
                    write=True,
                )

            self.assertFalse(write_review.call_args.kwargs["preview_only"])

    def test_public_error_redacts_input_and_artifact_paths_case_insensitively(self):
        frame_dir = Path(r"D:\Customer\SecretProject\clean_frames")
        artifact_dir = Path(r"D:\Customer\SecretProject\output\batch\item")
        error = RuntimeError(
            r"source d:\customer\secretproject\clean_frames\frame.jpg; "
            r"target D:\CUSTOMER\SECRETPROJECT\OUTPUT\BATCH\ITEM\analysis"
        )

        message = legacy_batch_timing._public_error(error, frame_dir, artifact_dir)

        self.assertNotIn("secretproject", message.lower())
        self.assertIn("<input_frame_dir>", message)
        self.assertIn("<artifact_dir>", message)

    def test_missing_failure_review_is_not_published_as_a_dead_link(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "missing_failure_review"
            artifact_dir = artifact_root / "failed"
            frames.mkdir()
            real_write_text = Path.write_text

            def guarded_write_text(path, data, *args, **kwargs):
                if path == artifact_dir / "analysis" / "human_review.md":
                    raise PermissionError("review is read-only")
                return real_write_text(path, data, *args, **kwargs)

            with patch.object(Path, "write_text", guarded_write_text):
                failed = legacy_batch_timing._failure_result(
                    BatchTimingItem(name="failed", frames=frames),
                    artifact_dir,
                    RuntimeError("analysis failed"),
                )
                result = legacy_batch_timing.publish_batch_timing_reports(artifact_root, [failed])

            summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["items"][0]["human_review_path"], "")
            dashboard = result.review_dashboard_path.read_text(encoding="utf-8")
            self.assertNotIn("failed/analysis/human_review.md", dashboard)
            self.assertIn("无", dashboard)

    def test_failed_retry_does_not_publish_stale_review_when_rewrite_fails(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "stale_failure_review"
            artifact_dir = artifact_root / "failed"
            review_path = artifact_dir / "analysis" / "human_review.md"
            frames.mkdir()
            review_path.parent.mkdir(parents=True)
            review_path.write_text("OLD FAILURE REPORT", encoding="utf-8")
            real_write_text = Path.write_text

            def guarded_write_text(path, data, *args, **kwargs):
                if path == review_path:
                    raise PermissionError("review is read-only")
                return real_write_text(path, data, *args, **kwargs)

            with patch.object(Path, "write_text", guarded_write_text):
                failed = legacy_batch_timing._failure_result(
                    BatchTimingItem(name="failed", frames=frames),
                    artifact_dir,
                    RuntimeError("new failure"),
                )
                result = legacy_batch_timing.publish_batch_timing_reports(artifact_root, [failed])

            summary = json.loads(result.summary_json_path.read_text(encoding="utf-8"))
            self.assertEqual(summary["items"][0]["human_review_path"], "")
            self.assertNotIn(
                "failed/analysis/human_review.md",
                result.review_dashboard_path.read_text(encoding="utf-8"),
            )

    def test_legacy_entry_keeps_override_signature_without_agent_contracts(self):
        self.assertIn("override_config_path", inspect.signature(run_batch_timing_agent).parameters)
        self.assertFalse(hasattr(legacy_batch_timing, "PolicyName"))
        self.assertFalse(hasattr(legacy_batch_timing, "StrategyRequest"))

    def test_write_mode_processes_each_frame_directory_independently(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames_a = root / "frames_a"
            frames_b = root / "frames_b"
            artifact_root = root / "output" / "batch"
            _make_frames(frames_a, 24)
            _make_frames(frames_b, 18, offset=100)

            result = run_batch_timing_agent(
                [
                    BatchTimingItem(name="test_a", frames=frames_a),
                    BatchTimingItem(name="test_b", frames=frames_b),
                ],
                artifact_root=artifact_root,
                limit_first_n=12,
                write=True,
            )

            self.assertEqual(len(result.items), 2)
            self.assertEqual(result.success_count, 2)
            self.assertEqual(result.failure_count, 0)
            for item_name in ["test_a", "test_b"]:
                item_root = artifact_root / item_name
                self.assertTrue((item_root / "analysis" / "human_review.md").exists())
                self.assertTrue((item_root / "analysis" / "execution_audit.json").exists())
                self.assertTrue((item_root / "output_frames" / "selected_frames.txt").exists())

            self.assertTrue((artifact_root / "analysis" / "batch_summary.json").exists())
            self.assertTrue((artifact_root / "analysis" / "batch_summary.csv").exists())
            self.assertTrue((artifact_root / "analysis" / "human_review.md").exists())
            self.assertTrue((artifact_root / "analysis" / "maintenance_report.md").exists())
            self.assertTrue((artifact_root / "analysis" / "maintenance_report.json").exists())

            with (artifact_root / "analysis" / "batch_summary.csv").open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual([row["name"] for row in rows], ["test_a", "test_b"])
            self.assertEqual(rows[0]["status"], "ok")
            self.assertEqual(rows[0]["analyzed_count"], "12")

    def test_cli_accepts_named_frame_directories_from_project_root(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "batch_cli"
            _make_frames(frames, 5)

            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path("scripts") / "frame_timing_agent" / "batch_timing_agent.py"),
                    "--frames",
                    f"sample={frames}",
                    "--artifact_root",
                    str(artifact_root),
                    "--limit_first_n",
                    "5",
                ],
                cwd=Path(__file__).resolve().parents[1],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("OK: sample analyzed=5", completed.stdout)
            summary = json.loads((artifact_root / "analysis" / "batch_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["success_count"], 1)
            self.assertEqual(summary["items"][0]["name"], "sample")
            self.assertFalse((artifact_root / "sample" / "output_frames").exists())

    def test_failed_directory_is_reported_without_stopping_successful_items(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            missing = root / "missing"
            artifact_root = root / "output" / "batch_failures"
            _make_frames(frames, 8)

            result = run_batch_timing_agent(
                [
                    BatchTimingItem(name="ok_item", frames=frames),
                    BatchTimingItem(name="missing_item", frames=missing),
                ],
                artifact_root=artifact_root,
                limit_first_n=8,
                write=False,
            )

            self.assertEqual(result.success_count, 1)
            self.assertEqual(result.failure_count, 1)
            self.assertEqual(result.items[1].status, "failed")
            self.assertIn("frame directory does not exist", result.items[1].error)
            failure_review = artifact_root / "missing_item" / "analysis" / "human_review.md"
            self.assertTrue(failure_review.exists())
            failure_text = failure_review.read_text(encoding="utf-8")
            self.assertIn("阶段 8 子任务失败", failure_text)
            self.assertIn("失败原因", failure_text)

    def test_normalized_duplicate_names_are_rejected(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "batch_duplicate"
            _make_frames(frames, 3)

            with self.assertRaisesRegex(ValueError, "duplicate batch item name"):
                run_batch_timing_agent(
                    [
                        BatchTimingItem(name="test 1", frames=frames),
                        BatchTimingItem(name="test@1", frames=frames),
                    ],
                    artifact_root=artifact_root,
                    limit_first_n=3,
                    write=False,
                )

    def test_artifact_root_must_be_inside_output(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            unsafe_artifact_root = root / "batch"
            _make_frames(frames, 3)

            with self.assertRaisesRegex(ValueError, "artifact_root must be inside an output directory"):
                run_batch_timing_agent(
                    [BatchTimingItem(name="sample", frames=frames)],
                    artifact_root=unsafe_artifact_root,
                    limit_first_n=3,
                    write=False,
                )

            result = run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=root / "output" / "batch",
                limit_first_n=3,
                write=False,
            )

            self.assertEqual(result.success_count, 1)

    def test_manifest_config_can_drive_batch_run(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "manifest_batch"
            manifest_path = root / "batch_manifest.json"
            _make_frames(frames, 6)
            manifest_path.write_text(
                json.dumps(
                    {
                        "artifact_root": str(artifact_root),
                        "limit_first_n": 4,
                        "write": False,
                        "items": [
                            {"name": "from_manifest", "frames": str(frames)},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            config = load_batch_manifest(manifest_path)
            result = run_batch_timing_agent(
                config.items,
                artifact_root=config.artifact_root,
                limit_first_n=config.limit_first_n,
                write=config.write,
            )

            self.assertEqual(result.success_count, 1)
            self.assertEqual(result.items[0].name, "from_manifest")
            self.assertEqual(result.items[0].analyzed_count, 4)
            report = result.human_review_path.read_text(encoding="utf-8")
            self.assertIn("阶段 6：批处理配置化与路径安全", report)
            self.assertIn("本阶段只做本地帧节奏策略批处理", report)
            self.assertTrue(result.review_dashboard_path.exists())
            dashboard = result.review_dashboard_path.read_text(encoding="utf-8")
            self.assertIn("# 阶段 8：批处理审查总览", dashboard)
            self.assertIn("from_manifest", dashboard)
            self.assertIn("visual_review/index.md", dashboard)
            self.assertNotIn(str(artifact_root), dashboard)

    def test_batch_review_artifacts_are_readable_text(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "readable_reports"
            _make_frames(frames, 4)

            result = run_batch_timing_agent(
                [BatchTimingItem(name="sample", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=4,
                write=False,
            )

            texts = [
                result.human_review_path.read_text(encoding="utf-8"),
                result.review_dashboard_path.read_text(encoding="utf-8"),
                (artifact_root / "analysis" / "maintenance_report.md").read_text(encoding="utf-8"),
            ]
            for text in texts:
                self.assertNotIn("闃", text)
                self.assertNotIn("鍏", text)
                self.assertNotIn("鏃", text)

    def test_manifest_loader_accepts_utf8_bom_files_from_powershell(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "bom_manifest_batch"
            manifest_path = root / "batch_manifest.json"
            _make_frames(frames, 2)
            content = json.dumps(
                {
                    "artifact_root": str(artifact_root),
                    "items": [
                        {"name": "bom_item", "frames": str(frames)},
                    ],
                }
            )
            manifest_path.write_text(content, encoding="utf-8-sig")

            config = load_batch_manifest(manifest_path)

            self.assertEqual(config.artifact_root, artifact_root)
            self.assertEqual(config.items[0].name, "bom_item")

    def test_dashboard_embeds_contact_sheets_when_strategy_operations_exist(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "dashboard_contacts"
            override_path = root / "override.json"
            _make_frames(frames, 8)
            override_path.write_text(
                json.dumps(
                    {
                        "overrides": {
                            "force_duplicate": [
                                {
                                    "start": 2,
                                    "end": 5,
                                    "total_instances": 3,
                                    "reason": "force visual review operation",
                                }
                            ]
                        }
                    }
                ),
                encoding="utf-8",
            )

            result = run_batch_timing_agent(
                [BatchTimingItem(name="with_contact", frames=frames)],
                artifact_root=artifact_root,
                limit_first_n=8,
                write=False,
                override_config_path=override_path,
            )

            dashboard = result.review_dashboard_path.read_text(encoding="utf-8")
            self.assertIn("![with_contact contact_000_duplicate_range_2_5]", dashboard)
            self.assertIn("../with_contact/analysis/visual_review/contact_000_duplicate_range_2_5.png", dashboard)

    def test_manifest_config_can_enable_reconstruction_balanced_mode(self):
        with _tempdir() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_root = root / "output" / "manifest_jitter_batch"
            manifest_path = root / "batch_manifest.json"
            _make_frames(frames, 6)
            manifest_path.write_text(
                json.dumps(
                    {
                        "artifact_root": str(artifact_root),
                        "mode": "reconstruction_balanced",
                        "write": True,
                        "items": [
                            {"name": "jitter_item", "frames": str(frames)},
                        ],
                    }
                ),
                encoding="utf-8",
            )

            config = load_batch_manifest(manifest_path)
            result = run_batch_timing_agent(
                config.items,
                artifact_root=config.artifact_root,
                limit_first_n=config.limit_first_n,
                mode=config.mode,
                write=config.write,
            )

            self.assertEqual(result.success_count, 1)
            strategy = json.loads(
                (artifact_root / "jitter_item" / "analysis" / "strategy.json").read_text(encoding="utf-8")
            )
            self.assertEqual(strategy["version"], 2)
            self.assertEqual(strategy["options"]["mode"], "reconstruction_balanced")
            self.assertEqual(strategy["options"]["jitter_reduction_mode"], "v2")


if __name__ == "__main__":
    unittest.main()
