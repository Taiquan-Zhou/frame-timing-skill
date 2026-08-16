import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from frame_timing_agent.auto_timing_agent import TimingAgentResult
from frame_timing_agent.ui.run_artifacts import (
    bind_strategy_snapshot,
    capture_input_snapshot,
    persist_thumbnails,
    verify_input_snapshot,
    write_input_snapshot,
)
from frame_timing_agent.ui.view_model import AnalysisViewData, ExecutionSummary, ThumbnailView
from frame_timing_agent.ui.worker import (
    RunSettings,
    default_artifact_dir,
    delete_history_run,
    load_existing_run,
    new_run_artifact_dir,
    run_analysis,
    run_export,
)


def _view(artifact_dir: Path, output_dir: Path | None = None) -> AnalysisViewData:
    return AnalysisViewData(
        analyzed_count=2,
        estimated_output_count=2,
        strategy_name="reconstruction_balanced",
        source_indices=(0, 1),
        motion_values=(0.0, 0.1),
        sharpness_values=(10.0, 11.0),
        contrast_values=(3.0, 4.0),
        segments=(),
        operation_counts={},
        thumbnails=(),
        artifact_dir=artifact_dir,
        output_dir=output_dir,
        execution=None,
    )


class UiWorkerTest(unittest.TestCase):
    def test_default_artifact_dir_is_never_inside_input_directory(self):
        frame_dir = Path("C:/data/output")

        artifact_dir = default_artifact_dir(frame_dir)

        self.assertFalse(artifact_dir.is_relative_to(frame_dir))

    def test_new_run_artifact_dir_is_unique_and_below_default_root(self):
        frame_dir = Path("C:/data/frames")

        first = new_run_artifact_dir(frame_dir)
        second = new_run_artifact_dir(frame_dir)

        self.assertEqual(first.parent, default_artifact_dir(frame_dir))
        self.assertEqual(second.parent, default_artifact_dir(frame_dir))
        self.assertNotEqual(first, second)

    def test_delete_history_run_removes_record_and_its_isolated_artifacts(self):
        from frame_timing_agent.ui.history import RunHistoryStore, RunRecord

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            run_id = "run-1"
            artifact = default_artifact_dir(frames) / run_id
            artifact.mkdir(parents=True)
            (artifact / "analysis.json").write_text("{}", encoding="utf-8")
            record = RunRecord(
                run_id=run_id,
                created_at="2026-07-15T10:00:00+08:00",
                updated_at="2026-07-15T10:00:00+08:00",
                frame_dir=frames,
                artifact_dir=artifact,
                fps=30.0,
                analyzed_count=1,
                estimated_output_count=1,
                output_count=None,
                output_dir=None,
                status="analyzed",
                strategy_name="reconstruction_balanced",
            )
            store = RunHistoryStore(root / "history.json")
            store.upsert(record)

            delete_history_run(record, store)

            self.assertTrue(frames.is_dir())
            self.assertFalse(artifact.exists())
            self.assertEqual(store.list_records(), [])

    def test_delete_history_run_rejects_artifacts_outside_managed_root(self):
        from frame_timing_agent.ui.history import RunHistoryStore, RunRecord

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            unsafe = root / "unrelated"
            frames.mkdir()
            unsafe.mkdir()
            record = RunRecord(
                run_id="run-1",
                created_at="2026-07-15T10:00:00+08:00",
                updated_at="2026-07-15T10:00:00+08:00",
                frame_dir=frames,
                artifact_dir=unsafe,
                fps=30.0,
                analyzed_count=1,
                estimated_output_count=1,
                output_count=None,
                output_dir=None,
                status="analyzed",
                strategy_name="reconstruction_balanced",
            )
            store = RunHistoryStore(root / "history.json")
            store.upsert(record)

            with self.assertRaisesRegex(ValueError, "managed run directory"):
                delete_history_run(record, store)

            self.assertTrue(unsafe.is_dir())
            self.assertEqual(store.list_records(), [record])

    def test_run_analysis_delegates_to_core_workflow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "frames", root / "output" / "run", fps=24.0, limit_first_n=120)
            settings.frame_dir.mkdir()
            (settings.frame_dir / "frame_000000.jpg").write_bytes(b"frame")
            result = TimingAgentResult(
                2, 2, settings.artifact_dir, settings.artifact_dir / "analysis" / "strategy.json", None
            )
            result.strategy_path.parent.mkdir(parents=True)
            result.strategy_path.write_text("{}", encoding="utf-8")

            with (
                patch("frame_timing_agent.ui.worker.analyze_run", return_value=result) as analyze,
                patch("frame_timing_agent.ui.worker.build_analysis_view", return_value=_view(settings.artifact_dir)),
            ):
                view = run_analysis(settings)

            self.assertEqual(view.analyzed_count, 2)
            analyze.assert_called_once_with(settings, progress_callback=None)

    def test_run_analysis_forwards_progress_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "frames", root / "output" / "run")
            settings.frame_dir.mkdir()
            (settings.frame_dir / "frame_000000.jpg").write_bytes(b"frame")
            result = TimingAgentResult(
                2, 2, settings.artifact_dir, settings.artifact_dir / "analysis" / "strategy.json", None
            )
            result.strategy_path.parent.mkdir(parents=True)
            result.strategy_path.write_text("{}", encoding="utf-8")
            updates: list[tuple[int, str]] = []

            def analyze(settings, progress_callback):
                progress_callback(37, "正在计算帧指标")
                return result

            with (
                patch("frame_timing_agent.ui.worker.analyze_run", side_effect=analyze) as mocked,
                patch("frame_timing_agent.ui.worker.build_analysis_view", return_value=_view(settings.artifact_dir)),
            ):
                run_analysis(settings, lambda percent, message: updates.append((percent, message)))

            self.assertEqual(updates, [(37, "正在计算帧指标")])
            self.assertTrue(callable(mocked.call_args.kwargs["progress_callback"]))

    def test_view_build_failure_never_reports_successful_100_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "frames", root / "output" / "run")
            settings.frame_dir.mkdir()
            (settings.frame_dir / "frame_000000.jpg").write_bytes(b"frame")
            result = TimingAgentResult(
                2, 2, settings.artifact_dir, settings.artifact_dir / "analysis" / "strategy.json", None
            )
            updates: list[tuple[int, str]] = []

            def analyze(settings, progress_callback):
                progress_callback(100, "分析帧目录完成")
                return result

            with (
                patch("frame_timing_agent.ui.worker.analyze_run", side_effect=analyze),
                patch("frame_timing_agent.ui.worker.build_analysis_view", side_effect=ValueError("broken artifacts")),
            ):
                with self.assertRaisesRegex(ValueError, "broken artifacts"):
                    run_analysis(settings, lambda percent, message: updates.append((percent, message)))

            self.assertEqual(updates, [(98, "正在准备分析结果")])

    def test_run_analysis_rejects_frames_changed_during_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            frame = frames / "frame_000000.jpg"
            frame.write_bytes(b"original")
            settings = RunSettings(frames, root / "output" / "run")

            with (
                patch(
                    "frame_timing_agent.ui.worker.analyze_run",
                    side_effect=ValueError("input frames changed during analysis; run analysis again"),
                ),
                patch(
                    "frame_timing_agent.ui.worker.build_analysis_view",
                    return_value=_view(settings.artifact_dir),
                ),
                self.assertRaisesRegex(ValueError, "changed during analysis"),
            ):
                run_analysis(settings)


    def test_run_export_uses_saved_strategy_without_rerunning_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            (frames / "frame_000000.jpg").write_bytes(b"frame")
            settings = RunSettings(frames, root / "output" / "run", fps=30.0, limit_first_n=None)
            analysis_dir = settings.artifact_dir / "analysis"
            analysis_dir.mkdir(parents=True)
            (analysis_dir / "strategy.json").write_text(
                '{"version": 1, "operations": []}',
                encoding="utf-8",
            )
            write_input_snapshot(
                analysis_dir,
                bind_strategy_snapshot(
                    capture_input_snapshot(frames, fps=30.0, limit_first_n=None),
                    analysis_dir / "strategy.json",
                ),
            )
            output_dir = settings.artifact_dir / "output_frames"
            execution = ExecutionSummary("ok", 1, 0, 0)
            exported_view = _view(settings.artifact_dir, output_dir=output_dir)

            with (
                patch(
                    "frame_timing_agent.ui.worker.export_run",
                    return_value=TimingAgentResult(
                        1,
                        1,
                        settings.artifact_dir,
                        analysis_dir / "strategy.json",
                        output_dir,
                    ),
                ) as export,
                patch("frame_timing_agent.ui.worker.build_analysis_view", return_value=exported_view),
                patch("frame_timing_agent.ui.worker.load_execution_summary", return_value=execution),
            ):
                view = run_export(settings)

            self.assertEqual(view.execution, execution)
            export.assert_called_once_with(settings, progress_callback=None)

    def test_run_export_rejects_frames_changed_after_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            frame = frames / "frame_000000.jpg"
            frame.write_bytes(b"original")
            settings = RunSettings(frames, root / "output" / "run")
            analysis_dir = settings.artifact_dir / "analysis"
            analysis_dir.mkdir(parents=True)
            (analysis_dir / "strategy.json").write_text(
                '{"version": 1, "operations": []}',
                encoding="utf-8",
            )
            write_input_snapshot(
                analysis_dir,
                bind_strategy_snapshot(
                    capture_input_snapshot(frames, fps=30.0, limit_first_n=None),
                    analysis_dir / "strategy.json",
                ),
            )
            frame.write_bytes(b"changed")

            with self.assertRaisesRegex(ValueError, "changed since analysis"):
                run_export(settings)

            self.assertFalse((settings.artifact_dir / "output_frames").exists())

    def test_run_export_keeps_previous_output_when_frames_change_during_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            frame = frames / "frame_000000.jpg"
            frame.write_bytes(b"original")
            settings = RunSettings(frames, root / "output" / "run")
            analysis_dir = settings.artifact_dir / "analysis"
            analysis_dir.mkdir(parents=True)
            strategy_path = analysis_dir / "strategy.json"
            strategy_path.write_text('{"version": 1, "operations": []}', encoding="utf-8")
            write_input_snapshot(
                analysis_dir,
                bind_strategy_snapshot(
                    capture_input_snapshot(frames, fps=30.0, limit_first_n=None),
                    strategy_path,
                ),
            )
            output_dir = settings.artifact_dir / "output_frames"
            output_dir.mkdir()
            marker = output_dir / "previous-output.txt"
            marker.write_text("keep", encoding="utf-8")
            verify_count = 0

            def mutate_before_second_verify(*args, **kwargs):
                nonlocal verify_count
                verify_count += 1
                if verify_count == 2:
                    frame.write_bytes(b"changed")
                return verify_input_snapshot(*args, **kwargs)

            with (
                patch(
                    "frame_timing_agent.run_workflow.verify_input_snapshot",
                    side_effect=mutate_before_second_verify,
                ),
                self.assertRaisesRegex(ValueError, "changed since analysis"),
            ):
                run_export(settings)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse(any(settings.artifact_dir.glob(".output_frames.export-*")))

    def test_run_export_keeps_previous_output_when_audit_write_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            (frames / "frame_000000.jpg").write_bytes(b"original")
            settings = RunSettings(frames, root / "output" / "run")
            analysis_dir = settings.artifact_dir / "analysis"
            analysis_dir.mkdir(parents=True)
            strategy_path = analysis_dir / "strategy.json"
            strategy_path.write_text('{"version": 1, "operations": []}', encoding="utf-8")
            write_input_snapshot(
                analysis_dir,
                bind_strategy_snapshot(
                    capture_input_snapshot(frames, fps=30.0, limit_first_n=None),
                    strategy_path,
                ),
            )
            output_dir = settings.artifact_dir / "output_frames"
            output_dir.mkdir()
            marker = output_dir / "previous-output.txt"
            marker.write_text("keep", encoding="utf-8")

            with (
                patch(
                    "frame_timing_agent.run_workflow.write_execution_audit",
                    side_effect=OSError("disk full"),
                ),
                self.assertRaisesRegex(OSError, "disk full"),
            ):
                run_export(settings)

            self.assertEqual(marker.read_text(encoding="utf-8"), "keep")
            self.assertFalse(any(settings.artifact_dir.glob(".output_frames.export-*")))
            self.assertFalse(any(settings.artifact_dir.glob(".execution_audit.export-*")))

    def test_output_and_audit_commit_roll_back_together(self):
        from frame_timing_agent.run_workflow import _replace_output_directory

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output_dir = root / "output_frames"
            staging_dir = root / ".output_frames.export-test"
            output_dir.mkdir()
            staging_dir.mkdir()
            (output_dir / "old.txt").write_text("old", encoding="utf-8")
            (staging_dir / "new.txt").write_text("new", encoding="utf-8")

            with self.assertRaisesRegex(OSError, "audit commit failed"):
                _replace_output_directory(
                    staging_dir,
                    output_dir,
                    lambda: (_ for _ in ()).throw(OSError("audit commit failed")),
                )

            self.assertEqual((output_dir / "old.txt").read_text(encoding="utf-8"), "old")
            self.assertFalse((output_dir / "new.txt").exists())
            self.assertFalse(any(root.glob(".output_frames.backup-*")))

    def test_execution_audit_commit_replaces_both_files(self):
        from frame_timing_agent.run_workflow import _replace_execution_audit

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis_dir = root / "analysis"
            staging_dir = root / "staging"
            analysis_dir.mkdir()
            staging_dir.mkdir()
            for name in ("execution_audit.json", "execution_audit.md"):
                (analysis_dir / name).write_text("old", encoding="utf-8")
                (staging_dir / name).write_text("new", encoding="utf-8")

            _replace_execution_audit(staging_dir, analysis_dir)

            self.assertEqual((analysis_dir / "execution_audit.json").read_text(encoding="utf-8"), "new")
            self.assertEqual((analysis_dir / "execution_audit.md").read_text(encoding="utf-8"), "new")
            self.assertFalse(any(analysis_dir.glob(".execution_audit.backup-*")))

    def test_execution_audit_commit_restores_old_files_after_partial_failure(self):
        from frame_timing_agent.run_workflow import _replace_execution_audit

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            analysis_dir = root / "analysis"
            staging_dir = root / "staging"
            analysis_dir.mkdir()
            staging_dir.mkdir()
            for name in ("execution_audit.json", "execution_audit.md"):
                (analysis_dir / name).write_text(f"old-{name}", encoding="utf-8")
            (staging_dir / "execution_audit.json").write_text("new", encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                _replace_execution_audit(staging_dir, analysis_dir)

            for name in ("execution_audit.json", "execution_audit.md"):
                self.assertEqual(
                    (analysis_dir / name).read_text(encoding="utf-8"),
                    f"old-{name}",
                )
            self.assertFalse(any(analysis_dir.glob(".execution_audit.backup-*")))

    def test_load_existing_run_rebuilds_view_without_running_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "frames", root / "output" / "run", fps=24.0)
            output_dir = settings.artifact_dir / "output_frames"
            output_dir.mkdir(parents=True)
            settings.frame_dir.mkdir()
            analysis_dir = settings.artifact_dir / "analysis"
            analysis_dir.mkdir()
            (analysis_dir / "execution_audit.json").write_text("{}", encoding="utf-8")
            existing = _view(settings.artifact_dir, output_dir=output_dir)
            execution = ExecutionSummary("ok", 2, 0, 0)

            with (
                patch("frame_timing_agent.ui.worker.build_analysis_view", return_value=existing) as build,
                patch("frame_timing_agent.ui.worker.load_execution_summary", return_value=execution),
            ):
                view = load_existing_run(settings, analyzed_count=2, estimated_output_count=2)

            self.assertEqual(view.execution, execution)
            self.assertEqual(view.output_dir, output_dir)
            build.assert_called_once()
            self.assertIsNone(build.call_args.kwargs["persisted_thumbnails"])

    def test_load_existing_legacy_run_without_source_uses_empty_thumbnails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "missing-frames", root / "run")
            analysis_dir = settings.artifact_dir / "analysis"
            analysis_dir.mkdir(parents=True)
            (analysis_dir / "strategy.json").write_text("{}", encoding="utf-8")

            with patch(
                "frame_timing_agent.ui.worker.build_analysis_view",
                side_effect=[FileNotFoundError("frames missing"), _view(settings.artifact_dir)],
            ) as build:
                load_existing_run(settings, analyzed_count=1, estimated_output_count=1)

            self.assertEqual(build.call_count, 2)
            self.assertEqual(build.call_args.kwargs["persisted_thumbnails"], ())

    def test_load_existing_legacy_run_with_empty_source_uses_empty_thumbnails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "empty-frames"
            frames.mkdir()
            settings = RunSettings(frames, root / "run")
            analysis_dir = settings.artifact_dir / "analysis"
            analysis_dir.mkdir(parents=True)
            (analysis_dir / "strategy.json").write_text("{}", encoding="utf-8")

            with patch(
                "frame_timing_agent.ui.worker.build_analysis_view",
                side_effect=[ValueError("no image frames"), _view(settings.artifact_dir)],
            ) as build:
                load_existing_run(settings, analyzed_count=1, estimated_output_count=1)

            self.assertEqual(build.call_count, 2)
            self.assertEqual(build.call_args.kwargs["persisted_thumbnails"], ())

    def test_load_existing_run_uses_frozen_thumbnails_and_flags_changed_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            source = frames / "frame_000000.jpg"
            source.write_bytes(b"original")
            settings = RunSettings(frames, root / "run")
            analysis_dir = settings.artifact_dir / "analysis"
            analysis_dir.mkdir(parents=True)
            (analysis_dir / "strategy.json").write_text("{}", encoding="utf-8")
            write_input_snapshot(
                analysis_dir,
                bind_strategy_snapshot(
                    capture_input_snapshot(frames, fps=30.0, limit_first_n=None),
                    analysis_dir / "strategy.json",
                ),
            )
            frozen = persist_thumbnails(
                analysis_dir,
                (ThumbnailView(0, source, "keep"),),
            )
            source.write_bytes(b"changed")

            with patch(
                "frame_timing_agent.ui.worker.build_analysis_view",
                return_value=_view(settings.artifact_dir),
            ) as build:
                view = load_existing_run(settings, analyzed_count=1, estimated_output_count=1)

            self.assertFalse(view.source_snapshot_matches)
            self.assertEqual(build.call_args.kwargs["persisted_thumbnails"], frozen)


if __name__ == "__main__":
    unittest.main()
