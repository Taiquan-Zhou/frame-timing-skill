import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from frame_timing_agent.auto_timing_agent import TimingAgentResult
from frame_timing_agent.ui.view_model import AnalysisViewData, ExecutionSummary
from frame_timing_agent.ui.worker import RunSettings, default_artifact_dir, run_analysis, run_export


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


if __name__ == "__main__":
    unittest.main()
