import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from frame_timing_agent.auto_timing_agent import TimingAgentResult
from frame_timing_agent.ui.view_model import AnalysisViewData, ExecutionSummary
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

    def test_run_analysis_uses_preview_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "frames", root / "output" / "run", fps=24.0, limit_first_n=120)
            result = TimingAgentResult(2, 2, settings.artifact_dir, settings.artifact_dir / "analysis" / "strategy.json", None)

            with patch("frame_timing_agent.ui.worker.run_timing_agent", return_value=result) as run_agent, patch(
                "frame_timing_agent.ui.worker.build_analysis_view", return_value=_view(settings.artifact_dir)
            ):
                view = run_analysis(settings)

            self.assertEqual(view.analyzed_count, 2)
            run_agent.assert_called_once_with(
                frames=settings.frame_dir,
                artifact_dir=settings.artifact_dir,
                limit_first_n=120,
                mode="reconstruction_balanced",
                write=False,
                fps=24.0,
            )

    def test_run_analysis_forwards_progress_updates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "frames", root / "output" / "run")
            result = TimingAgentResult(2, 2, settings.artifact_dir, settings.artifact_dir / "analysis" / "strategy.json", None)
            updates: list[tuple[int, str]] = []

            def run_agent(**kwargs):
                kwargs["progress_callback"](37, "正在计算帧指标")
                return result

            with patch("frame_timing_agent.ui.worker.run_timing_agent", side_effect=run_agent) as mocked, patch(
                "frame_timing_agent.ui.worker.build_analysis_view", return_value=_view(settings.artifact_dir)
            ):
                run_analysis(settings, lambda percent, message: updates.append((percent, message)))

            self.assertEqual(updates, [(37, "正在计算帧指标")])
            self.assertTrue(callable(mocked.call_args.kwargs["progress_callback"]))

    def test_view_build_failure_never_reports_successful_100_percent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "frames", root / "output" / "run")
            result = TimingAgentResult(2, 2, settings.artifact_dir, settings.artifact_dir / "analysis" / "strategy.json", None)
            updates: list[tuple[int, str]] = []

            def run_agent(**kwargs):
                kwargs["progress_callback"](100, "分析帧目录完成")
                return result

            with patch("frame_timing_agent.ui.worker.run_timing_agent", side_effect=run_agent), patch(
                "frame_timing_agent.ui.worker.build_analysis_view", side_effect=ValueError("broken artifacts")
            ):
                with self.assertRaisesRegex(ValueError, "broken artifacts"):
                    run_analysis(settings, lambda percent, message: updates.append((percent, message)))

            self.assertEqual(updates, [(98, "正在准备分析结果")])

    def test_run_export_uses_write_mode_and_reads_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "frames", root / "output" / "run", fps=30.0, limit_first_n=None)
            output_dir = settings.artifact_dir / "output_frames"
            result = TimingAgentResult(
                2,
                2,
                settings.artifact_dir,
                settings.artifact_dir / "analysis" / "strategy.json",
                output_dir,
            )
            execution = ExecutionSummary("ok", 2, 0, 0)
            exported_view = _view(settings.artifact_dir, output_dir=output_dir)

            with patch("frame_timing_agent.ui.worker.run_timing_agent", return_value=result) as run_agent, patch(
                "frame_timing_agent.ui.worker.build_analysis_view", return_value=exported_view
            ), patch("frame_timing_agent.ui.worker.load_execution_summary", return_value=execution):
                view = run_export(settings)

            self.assertEqual(view.execution, execution)
            run_agent.assert_called_once_with(
                frames=settings.frame_dir,
                artifact_dir=settings.artifact_dir,
                limit_first_n=None,
                mode="reconstruction_balanced",
                write=True,
                fps=30.0,
            )

    def test_load_existing_run_rebuilds_view_without_running_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = RunSettings(root / "frames", root / "output" / "run", fps=24.0)
            output_dir = settings.artifact_dir / "output_frames"
            output_dir.mkdir(parents=True)
            analysis_dir = settings.artifact_dir / "analysis"
            analysis_dir.mkdir()
            (analysis_dir / "execution_audit.json").write_text("{}", encoding="utf-8")
            existing = _view(settings.artifact_dir, output_dir=output_dir)
            execution = ExecutionSummary("ok", 2, 0, 0)

            with patch("frame_timing_agent.ui.worker.build_analysis_view", return_value=existing) as build, patch(
                "frame_timing_agent.ui.worker.load_execution_summary", return_value=execution
            ):
                view = load_existing_run(settings, analyzed_count=2, estimated_output_count=2)

            self.assertEqual(view.execution, execution)
            self.assertEqual(view.output_dir, output_dir)
            build.assert_called_once()


if __name__ == "__main__":
    unittest.main()
