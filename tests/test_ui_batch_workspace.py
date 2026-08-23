from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 UI dependency is not installed")
class BatchWorkspaceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from frame_timing_agent.ui.app import create_application

        cls.app = create_application(["frame-timing-ui-batch-test"])

    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary_directory.name).resolve()

    def tearDown(self):
        self.temporary_directory.cleanup()

    def make_state(self, *, status="paused", item_status="pending"):
        from frame_timing_agent.batch_session import (
            BatchItemState,
            BatchItemStatus,
            BatchState,
            BatchStatus,
        )

        frame_dir = self.root / "frames"
        frame_dir.mkdir(exist_ok=True)
        return BatchState(
            batch_id="batch-test",
            created_at="2026-08-23T00:00:00+00:00",
            updated_at="2026-08-23T00:00:00+00:00",
            fps=30.0,
            limit_first_n=None,
            artifact_root=self.root / "artifacts",
            status=BatchStatus(status),
            items=[
                BatchItemState(
                    frame_dir=frame_dir,
                    safe_name="frames",
                    status=BatchItemStatus(item_status),
                )
            ],
        )

    def test_mode_switch_keeps_single_workspace_and_batch_workspace_at_same_level(self):
        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            self.assertIs(window.workspace_stack.currentWidget(), window.single_workspace)
            window.select_batch_mode()
            self.assertIs(window.workspace_stack.currentWidget(), window.batch_workspace)
            self.assertFalse(window.analyze_button.isEnabled())
            window.select_single_mode()
            self.assertIs(window.workspace_stack.currentWidget(), window.single_workspace)
            self.assertTrue(window.analyze_button.isEnabled())
        finally:
            window.close()

    def test_batch_workspace_is_bounded_and_scrollable_at_minimum_window_size(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QFrame, QScrollArea

        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            window.resize(1100, 700)
            window.select_batch_mode()
            window.show()
            self.app.processEvents()

            workspace = window.batch_workspace
            toolbar = workspace.findChild(QFrame, "batchToolbar")
            self.assertIsNotNone(toolbar)
            for button in (
                workspace.add_button,
                workspace.discover_button,
                workspace.start_button,
                workspace.pause_button,
                workspace.continue_button,
                workspace.retry_button,
                workspace.export_button,
                workspace.open_batch_button,
            ):
                button_rect = button.geometry()
                self.assertGreaterEqual(button_rect.left(), toolbar.contentsRect().left())
                self.assertLessEqual(button_rect.right(), toolbar.contentsRect().right())

            detail_scroll = workspace.findChild(QScrollArea)
            self.assertIsNotNone(detail_scroll)
            self.assertTrue(detail_scroll.widgetResizable())
            self.assertGreater(detail_scroll.verticalScrollBar().maximum(), 0)
            detail_scroll.verticalScrollBar().setValue(detail_scroll.verticalScrollBar().maximum())
            self.app.processEvents()
            self.assertEqual(
                detail_scroll.verticalScrollBar().value(),
                detail_scroll.verticalScrollBar().maximum(),
            )
            output_top = workspace.output_path.mapTo(detail_scroll.viewport(), QPoint(0, 0)).y()
            self.assertGreaterEqual(output_top, 0)
            self.assertLessEqual(
                output_top + workspace.output_path.height(),
                detail_scroll.viewport().height(),
            )
        finally:
            window.close()

    def test_unfinished_batch_is_loaded_but_not_started(self):
        from PySide6.QtCore import QSettings

        from frame_timing_agent.batch_session import save_batch
        from frame_timing_agent.ui.main_window import MainWindow

        state = self.make_state(status="paused")
        save_batch(state)
        settings_path = self.root / "settings.ini"
        settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
        settings.setValue("last_batch_state_path", str(state.state_path))
        settings.sync()

        with patch("frame_timing_agent.ui.batch_workspace.run_batch") as run:
            window = MainWindow(settings=settings)
            try:
                window.select_batch_mode()
                self.assertEqual(window.batch_workspace.current_state.status.value, "paused")
                self.assertTrue(window.batch_workspace.continue_button.isEnabled())
                self.assertFalse(window.batch_workspace.is_running)
                run.assert_not_called()
            finally:
                window.close()

    def test_finished_batch_restore_does_not_prompt_to_continue(self):
        from PySide6.QtCore import QSettings, QThreadPool

        from frame_timing_agent.batch_session import save_batch
        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="completed")
        state.items[0].progress = 1.0
        save_batch(state)
        settings = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("last_batch_state_path", str(state.state_path))
        settings.sync()

        workspace = BatchWorkspace(QThreadPool(), settings=settings)
        try:
            self.assertEqual(workspace.status_label.text(), "已恢复上次分析完成批次")
            self.assertFalse(workspace.continue_button.isEnabled())
            self.assertTrue(workspace.export_button.isEnabled())
            self.assertEqual(workspace.output_path.text(), "尚未导出")
        finally:
            workspace.close()

    def test_failed_item_shows_public_error_and_failed_count(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="failed")
        state.items[0].progress = 1.0
        state.items[0].last_error = "RuntimeError: decoder rejected <input_frame_dir>"
        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace.set_state(state)
            workspace._run_succeeded(state)
            self.assertIn("失败原因", workspace.warning_label.text())
            self.assertIn("decoder rejected <input_frame_dir>", workspace.warning_label.text())
            self.assertEqual(workspace.status_label.text(), "批次分析完成，1 项失败")
        finally:
            workspace.close()

    def test_rows_are_deterministic_and_actions_follow_selected_item_state(self):
        from dataclasses import replace

        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.batch_session import BatchItemState, BatchItemStatus
        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="completed")
        second_dir = self.root / "other"
        second_dir.mkdir()
        state.items = [
            BatchItemState(
                frame_dir=second_dir,
                safe_name="zeta",
                status=BatchItemStatus.REVIEW_REQUIRED,
                warnings=("quality.low_motion_review",),
            ),
            replace(state.items[0], safe_name="alpha", output_path=self.root / "output_frames"),
        ]
        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace.set_state(state)
            self.assertEqual(
                [workspace.item_list.item(index).data(0x0100) for index in range(2)],
                ["alpha", "zeta"],
            )
            workspace.select_item("zeta")
            self.assertTrue(workspace.approve_button.isEnabled())
            self.assertFalse(workspace.retry_button.isEnabled())
            self.assertTrue(workspace.export_button.isEnabled())
        finally:
            workspace.close()

    def test_missing_persisted_output_remains_exportable_and_is_not_labeled_exported(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="completed")
        state.items[0].output_path = state.artifact_root / state.items[0].safe_name / "output_frames"
        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace.set_state(state)

            self.assertTrue(workspace.export_button.isEnabled())
            self.assertNotIn("已导出", workspace.item_list.item(0).text())
        finally:
            workspace.close()

    def test_pause_sets_event_without_interrupting_the_running_item(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace._set_operation("analysis")
            workspace._sync_actions()
            workspace.request_pause()
            self.assertTrue(workspace.pause_requested)
            self.assertTrue(workspace.is_running)
        finally:
            workspace.close()

    def test_finished_batch_allows_starting_a_new_batch(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="completed")
        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace.set_state(state)
            self.assertTrue(workspace.add_button.isEnabled())
            self.assertTrue(workspace.discover_button.isEnabled())
            self.assertEqual(workspace.add_button.text(), "新建批次")
        finally:
            workspace.close()

    def test_export_operation_never_enables_pause(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace._set_operation("export")
            workspace._sync_actions()
            self.assertTrue(workspace.is_running)
            self.assertFalse(workspace.pause_button.isEnabled())
        finally:
            workspace.close()

    def test_export_progress_does_not_jump_to_terminal_analysis_progress(self):
        from dataclasses import replace

        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.batch_session import BatchItemState, BatchItemStatus
        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="completed")
        second_dir = self.root / "other"
        second_dir.mkdir()
        state.items = [
            replace(state.items[0], safe_name="alpha", progress=1.0),
            BatchItemState(
                frame_dir=second_dir,
                safe_name="beta",
                status=BatchItemStatus.COMPLETED,
                progress=1.0,
            ),
        ]
        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace.set_state(state)
            workspace._set_operation("export")
            with patch("frame_timing_agent.ui.batch_workspace.load_batch", return_value=state):
                workspace._run_progress(50, "alpha")
            self.assertEqual(workspace.overall_progress.value(), 50)
            workspace._run_progress(60, "正在导出")
            self.assertEqual(workspace.overall_progress.value(), 60)
        finally:
            workspace.close()

    def test_running_item_progress_updates_selected_detail(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="running", item_status="running")
        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace.set_state(state)
            workspace._set_operation("analysis")
            with patch("frame_timing_agent.ui.batch_workspace.load_batch", return_value=state):
                workspace._run_progress(50, "正在计算")
            self.assertIn("50%", workspace.detail_status.text())
            self.assertIn("50%", workspace.item_list.item(0).text())
        finally:
            workspace.close()

    def test_persisted_output_does_not_block_explicit_reexport(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="completed")
        state.items[0].output_path = state.artifact_root / state.items[0].safe_name / "output_frames"
        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace.set_state(state)
            self.assertTrue(workspace.export_button.isEnabled())
        finally:
            workspace.close()

    def test_selected_completed_item_shows_strategy_and_paths(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="completed")
        state.items[0].analyzed_count = 10
        state.items[0].output_path = state.artifact_root / state.items[0].safe_name / "output_frames"
        workspace = BatchWorkspace(QThreadPool())
        try:
            with patch("frame_timing_agent.ui.batch_workspace.load_existing_run", side_effect=ValueError("missing")):
                workspace.set_state(state)
            self.assertEqual(workspace.strategy_label.text(), "策略：reconstruction_balanced")
            self.assertEqual(workspace.artifact_path.text(), str(state.artifact_root / state.items[0].safe_name))
            self.assertEqual(workspace.artifact_path.cursorPosition(), 0)
            self.assertEqual(workspace.artifact_path.toolTip(), workspace.artifact_path.text())
            self.assertEqual(workspace.output_path.text(), str(state.items[0].output_path))
            self.assertEqual(workspace.output_path.cursorPosition(), 0)
            self.assertEqual(workspace.output_path.toolTip(), workspace.output_path.text())
        finally:
            workspace.close()

    def test_new_batch_uses_the_shared_fps_value(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.batch_discovery import DiscoveryResult
        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="ready")
        workspace = BatchWorkspace(QThreadPool(), fps_provider=lambda: 24.0)
        workspace._explicit_directories.append(state.items[0].frame_dir)
        try:
            with (
                patch(
                    "frame_timing_agent.ui.batch_workspace.discover_frame_directories",
                    return_value=DiscoveryResult((state.items[0].frame_dir,), ()),
                ),
                patch("frame_timing_agent.ui.batch_workspace.create_batch", return_value=state) as create,
                patch.object(workspace, "_start_run"),
            ):
                workspace.start_new_batch()
            self.assertEqual(create.call_args.kwargs["fps"], 24.0)
        finally:
            workspace.close()

    def test_approval_runs_in_the_shared_thread_pool(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="review_required")
        pool = QThreadPool()
        workspace = BatchWorkspace(pool)
        try:
            workspace.set_state(state)
            workspace.note_edit.setText("checked")
            with (
                patch("frame_timing_agent.ui.batch_workspace.approve_item") as approve,
                patch.object(pool, "start") as start,
            ):
                workspace.approve_selected()
            approve.assert_not_called()
            start.assert_called_once()
            self.assertEqual(workspace.operation, "approval")
        finally:
            workspace.close()

    def test_approved_note_is_read_only(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="review_required")
        state.items[0].approved = True
        state.items[0].note = "checked"
        workspace = BatchWorkspace(QThreadPool())
        try:
            workspace.set_state(state)
            self.assertFalse(workspace.note_edit.isEnabled())
            self.assertEqual(workspace.note_edit.text(), "checked")
        finally:
            workspace.close()

    def test_export_confirmation_cancellation_does_not_start_background_task(self):
        from PySide6.QtCore import QThreadPool
        from PySide6.QtWidgets import QMessageBox

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        state = self.make_state(status="finished", item_status="completed")
        pool = QThreadPool()
        workspace = BatchWorkspace(pool)
        try:
            workspace.set_state(state)
            with (
                patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No),
                patch.object(pool, "start") as start,
            ):
                workspace.export_eligible()
            start.assert_not_called()
            self.assertFalse(workspace.is_running)
        finally:
            workspace.close()

    def test_add_directory_resets_finished_batch_and_enables_start(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        frame_dir = self.root / "new-frames"
        frame_dir.mkdir()
        workspace = BatchWorkspace(QThreadPool())
        workspace.set_state(self.make_state(status="finished", item_status="completed"))
        try:
            with patch(
                "frame_timing_agent.ui.batch_workspace.QFileDialog.getExistingDirectory",
                return_value=str(frame_dir),
            ):
                workspace.add_directory()

            self.assertIsNone(workspace.current_state)
            self.assertEqual(workspace._explicit_directories, [frame_dir.resolve()])
            self.assertEqual(workspace.status_label.text(), "已添加 1 个目录")
            self.assertTrue(workspace.start_button.isEnabled())
        finally:
            workspace.close()

    def test_discover_root_reports_result_and_enables_valid_batch(self):
        from PySide6.QtCore import QThreadPool

        from frame_timing_agent.batch_discovery import DiscoveryIssue, DiscoveryResult
        from frame_timing_agent.ui.batch_workspace import BatchWorkspace

        root = self.root / "input-root"
        frames = root / "frames"
        invalid = root / "empty"
        frames.mkdir(parents=True)
        workspace = BatchWorkspace(QThreadPool())
        try:
            with (
                patch(
                    "frame_timing_agent.ui.batch_workspace.QFileDialog.getExistingDirectory",
                    return_value=str(root),
                ),
                patch(
                    "frame_timing_agent.ui.batch_workspace.discover_frame_directories",
                    return_value=DiscoveryResult((frames.resolve(),), (DiscoveryIssue(invalid, "invalid_no_frames"),)),
                ),
            ):
                workspace.discover_root()

            self.assertEqual(workspace._discovery_root, root.resolve())
            self.assertEqual(workspace.status_label.text(), "发现 1 个帧目录，忽略/无效 1 项")
            self.assertTrue(workspace.start_button.isEnabled())
        finally:
            workspace.close()


if __name__ == "__main__":
    unittest.main()
