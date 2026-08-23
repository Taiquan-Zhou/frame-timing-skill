import importlib.util
import os
import unittest
from dataclasses import replace
from unittest.mock import patch


PYSIDE_AVAILABLE = importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(PYSIDE_AVAILABLE, "PySide6 UI dependency is not installed")
class UiSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from frame_timing_agent.ui.app import create_application

        cls.app = create_application(["frame-timing-ui-test"])

    def test_main_window_starts_with_export_disabled(self):
        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            self.assertEqual(window.windowTitle(), "Frame Timing Skill")
            self.assertFalse(window.export_button.isEnabled())
            self.assertTrue(window.analyze_button.isEnabled())
        finally:
            window.close()

    def test_main_window_starts_in_single_directory_mode(self):
        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            self.assertTrue(window.single_mode_button.isChecked())
            self.assertFalse(window.batch_mode_button.isChecked())
            self.assertIs(window.workspace_stack.currentWidget(), window.single_workspace)
        finally:
            window.close()

    def test_batch_run_disables_mode_switch_and_single_run_controls(self):
        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            window.select_batch_mode()
            window.batch_workspace._set_operation("analysis")
            self.assertFalse(window.single_mode_button.isEnabled())
            self.assertFalse(window.batch_mode_button.isEnabled())
            self.assertFalse(window.analyze_button.isEnabled())
            self.assertFalse(window.fps_spin.isEnabled())

            window.batch_workspace._set_operation("idle")
            self.assertTrue(window.single_mode_button.isEnabled())
            self.assertTrue(window.batch_mode_button.isEnabled())
            self.assertFalse(window.analyze_button.isEnabled())
            self.assertTrue(window.fps_spin.isEnabled())
        finally:
            window.close()

    def test_batch_run_blocks_close_until_workspace_returns_idle(self):
        from PySide6.QtGui import QCloseEvent

        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            window.batch_workspace._set_operation("analysis")
            running_event = QCloseEvent()
            with patch("frame_timing_agent.ui.main_window.QMessageBox.information"):
                window.closeEvent(running_event)
            self.assertFalse(running_event.isAccepted())

            window.batch_workspace._set_operation("idle")
            idle_event = QCloseEvent()
            window.closeEvent(idle_event)
            self.assertTrue(idle_event.isAccepted())
        finally:
            window.batch_workspace._set_operation("idle")
            window.close()

    def test_single_directory_task_keeps_fps_disabled_while_busy(self):
        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            window._begin_task("正在分析")
            self.assertFalse(window.fps_spin.isEnabled())
            self.assertFalse(window.single_mode_button.isEnabled())
            self.assertFalse(window.batch_mode_button.isEnabled())
        finally:
            window._finish_task("完成")
            window.close()

    def test_background_task_delivers_success_and_failure(self):
        from PySide6.QtCore import QEventLoop, QThreadPool, QTimer

        from frame_timing_agent.ui.worker import create_task

        successes = []
        failures = []

        def execute(function):
            loop = QEventLoop()
            task = create_task(
                function,
                lambda value: (successes.append(value), loop.quit()),
                lambda message: (failures.append(message), loop.quit()),
            )
            QThreadPool.globalInstance().start(task)
            QTimer.singleShot(2000, loop.quit)
            loop.exec()

        execute(lambda: 42)
        execute(lambda: 1 / 0)

        self.assertEqual(successes, [42])
        self.assertEqual(len(failures), 1)
        self.assertIn("ZeroDivisionError", failures[0])

    def test_background_task_delivers_progress_on_the_qt_event_loop(self):
        from PySide6.QtCore import QEventLoop, QThread, QThreadPool, QTimer

        from frame_timing_agent.ui.worker import create_task

        updates = []
        callback_threads = []
        loop = QEventLoop()

        def work(progress):
            progress(17, "正在计算帧指标")
            return 42

        task = create_task(
            work,
            lambda _value: loop.quit(),
            lambda _message: loop.quit(),
            lambda percent, message: (
                updates.append((percent, message)),
                callback_threads.append(QThread.currentThread()),
            ),
        )
        QThreadPool.globalInstance().start(task)
        QTimer.singleShot(2000, loop.quit)
        loop.exec()

        self.assertEqual(updates, [(17, "正在计算帧指标")])
        self.assertEqual(callback_threads, [self.app.thread()])

    def test_execution_progress_stays_bound_and_failure_preserves_value(self):
        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            window._begin_task("正在分析帧目录")
            window._task_progress(42, "正在计算帧指标")

            self.assertEqual(window.progress.value(), 42)
            self.assertEqual(window.progress_percent_label.text(), "42%")
            with patch("frame_timing_agent.ui.main_window.QMessageBox.critical"):
                window._task_failed("测试错误")

            self.assertEqual(window.progress.value(), 42)
            self.assertEqual(window.progress_percent_label.text(), "42%")
            self.assertFalse(window.progress.isHidden())
            self.assertIn("失败", window.status_label.text())
            self.assertIn("测试错误", window.status_label.text())
        finally:
            window.close()

    def test_analysis_completion_sets_exact_terminal_progress(self):
        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            window._begin_task("正在分析帧目录")
            window._finish_task("分析帧目录完成")

            self.assertEqual(window.status_label.text(), "分析帧目录完成")
            self.assertEqual(window.progress.value(), 100)
            self.assertEqual(window.progress_percent_label.text(), "100%")
        finally:
            window.close()

    def test_render_failure_keeps_precompletion_progress(self):
        from frame_timing_agent.ui.main_window import MainWindow
        from frame_timing_agent.ui.view_model import AnalysisViewData

        window = MainWindow()
        view = AnalysisViewData(
            analyzed_count=1,
            estimated_output_count=1,
            strategy_name="reconstruction_balanced",
            source_indices=(0,),
            motion_values=(0.0,),
            sharpness_values=(1.0,),
            contrast_values=(1.0,),
            segments=(),
            operation_counts={},
            thumbnails=(),
            artifact_dir=__import__("pathlib").Path("output"),
            output_dir=None,
            execution=None,
        )
        try:
            window._begin_task("正在分析帧目录")
            window._task_progress(98, "正在准备分析结果")
            with (
                patch.object(window, "_render_view", side_effect=ValueError("bad view")),
                patch("frame_timing_agent.ui.main_window.QMessageBox.critical"),
            ):
                window._analysis_finished(view)

            self.assertEqual(window.progress.value(), 98)
            self.assertIn("处理失败", window.status_label.text())
            self.assertIn("bad view", window.status_label.text())
        finally:
            window.close()

    def test_analysis_completion_renders_default_metric_immediately(self):
        from pathlib import Path

        from frame_timing_agent.ui.main_window import MainWindow
        from frame_timing_agent.ui.view_model import AnalysisViewData

        window = MainWindow()
        view = AnalysisViewData(
            analyzed_count=3,
            estimated_output_count=3,
            strategy_name="reconstruction_balanced",
            source_indices=(0, 1, 2),
            motion_values=(0.0, 0.2, 0.1),
            sharpness_values=(1.0, 2.0, 3.0),
            contrast_values=(4.0, 5.0, 6.0),
            segments=(),
            operation_counts={},
            thumbnails=(),
            artifact_dir=Path("output"),
            output_dir=None,
            execution=None,
        )
        try:
            window._begin_task("正在分析帧目录")
            window._analysis_finished(view)

            self.assertEqual(window.chart._sources, view.source_indices)
            self.assertEqual(window.chart._values, view.motion_values)
            self.assertEqual(window.chart._label, "运动强度")
        finally:
            window.close()

    def test_line_chart_hover_snaps_to_nearest_data_point(self):
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest

        from frame_timing_agent.ui.widgets import LineChart

        chart = LineChart()
        chart.resize(640, 300)
        chart.set_series((100, 200, 428, 800), (0.01, 0.02, 0.037, 0.08), "运动强度")
        chart.show()
        try:
            QApplication.processEvents()
            plot = chart._plot_rect()
            target_x = chart._point_x(2, plot)
            QTest.mouseMove(chart, QPoint(round(target_x), round(plot.center().y())))
            QApplication.processEvents()

            self.assertEqual(chart.hovered_data(), (428, 0.037))
            marker = chart._data_point(2, plot)
            self.assertAlmostEqual(marker.x(), target_x)
            self.assertAlmostEqual(
                marker.y(),
                plot.bottom()
                - plot.height() * (0.037 - min(chart._values)) / (max(chart._values) - min(chart._values)),
            )

            chart.resize(820, 340)
            QApplication.processEvents()
            resized_plot = chart._plot_rect()
            resized_x = chart._point_x(2, resized_plot)
            QTest.mouseMove(chart, QPoint(round(resized_x), round(resized_plot.bottom() - 2)))
            QApplication.processEvents()
            self.assertEqual(chart.hovered_data(), (428, 0.037))

            QApplication.sendEvent(chart, QEvent(QEvent.Type.Leave))
            self.assertIsNone(chart.hovered_data())
        finally:
            chart.close()

    def test_line_chart_hover_supports_nonmonotonic_source_indices(self):
        from PySide6.QtCore import QPoint
        from PySide6.QtWidgets import QApplication
        from PySide6.QtTest import QTest

        from frame_timing_agent.ui.widgets import LineChart

        chart = LineChart()
        chart.resize(640, 300)
        chart.set_series((10, 100, 20, 200), (0.01, 0.02, 0.03, 0.04), "运动强度")
        chart.show()
        try:
            QApplication.processEvents()
            plot = chart._plot_rect()
            QTest.mouseMove(chart, QPoint(round(chart._point_x(1, plot)), round(plot.center().y())))
            QApplication.processEvents()
            self.assertEqual(chart.hovered_data(), (100, 0.02))
        finally:
            chart.close()

    def test_line_chart_and_segment_bar_share_sparse_source_coordinates(self):
        from frame_timing_agent.ui.widgets import LineChart, source_center_ratio

        chart = LineChart()
        chart.resize(1000, 300)
        chart.set_series((0, 100, 101), (0.01, 0.02, 0.03), "运动强度")
        try:
            plot = chart._plot_rect()
            actual_ratio = (chart._point_x(1, plot) - plot.left()) / plot.width()
            expected_ratio = source_center_ratio(100, 0, 101)

            self.assertAlmostEqual(actual_ratio, expected_ratio)
            self.assertGreater(actual_ratio, 0.98)
        finally:
            chart.close()

    def test_single_source_uses_full_segment_bar_and_centered_chart_point(self):
        from frame_timing_agent.ui.view_model import SegmentView
        from frame_timing_agent.ui.widgets import LineChart, SegmentBar

        chart = LineChart()
        bar = SegmentBar()
        chart.resize(1000, 300)
        chart.set_series((5,), (0.01,), "运动强度")
        bar.set_segments((SegmentView(5, 5, "static", 1),), 5, 5)
        try:
            plot = chart._plot_rect()
            self.assertAlmostEqual(chart._point_x(0, plot), plot.center().x())
            self.assertEqual(bar._source_min, bar._source_max)
        finally:
            chart.close()
            bar.close()

    def test_line_chart_rejects_mismatched_series_lengths(self):
        from frame_timing_agent.ui.widgets import LineChart

        chart = LineChart()
        try:
            with self.assertRaisesRegex(ValueError, "same length"):
                chart.set_series((1, 2), (0.1,), "运动强度")
        finally:
            chart.close()

    def test_line_chart_base_cache_uses_device_pixel_ratio(self):
        from unittest.mock import patch

        from frame_timing_agent.ui.widgets import LineChart

        chart = LineChart()
        chart.resize(640, 300)
        try:
            with patch.object(chart, "devicePixelRatioF", return_value=1.5):
                pixmap = chart._render_base_pixmap()

            self.assertEqual(pixmap.devicePixelRatio(), 1.5)
            self.assertEqual(pixmap.width(), 960)
            self.assertEqual(pixmap.height(), 450)
        finally:
            chart.close()

    def test_line_chart_x_axis_title_is_below_plot_area(self):
        from frame_timing_agent.ui.widgets import LineChart

        chart = LineChart()
        chart.resize(640, 300)
        try:
            plot = chart._plot_rect()
            title_rect = chart._x_axis_title_rect(plot)

            self.assertGreater(title_rect.top(), plot.bottom())
            self.assertLessEqual(title_rect.bottom(), chart.height())
        finally:
            chart.close()

    def test_run_history_dialog_exposes_reopen_and_output_actions(self):
        import tempfile
        import time
        from pathlib import Path

        from PySide6.QtWidgets import QApplication

        from frame_timing_agent.ui.history import RunRecord
        from frame_timing_agent.ui.history_dialog import RunHistoryDialog

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame_dir = root / "frames"
            analysis_dir = root / "run" / "analysis"
            output_dir = root / "run" / "output_frames"
            frame_dir.mkdir()
            analysis_dir.mkdir(parents=True)
            output_dir.mkdir()
            (analysis_dir / "strategy.json").write_text("{}", encoding="utf-8")
            record = RunRecord(
                run_id="run-1",
                created_at="2026-07-15T10:00:00+08:00",
                updated_at="2026-07-15T10:05:00+08:00",
                frame_dir=frame_dir,
                artifact_dir=analysis_dir.parent,
                fps=30.0,
                analyzed_count=100,
                estimated_output_count=80,
                output_count=78,
                output_dir=output_dir,
                status="exported",
                strategy_name="reconstruction_balanced",
            )

            deleted: list[RunRecord] = []
            dialog = RunHistoryDialog([record], delete_callback=lambda item: not deleted.append(item))
            try:
                self.assertEqual(dialog.table.rowCount(), 1)
                self.assertEqual(dialog.selected_record(), record)
                self.assertTrue(dialog.reopen_button.isEnabled())
                self.assertTrue(dialog.open_artifact_button.isEnabled())
                self.assertTrue(dialog.open_output_button.isEnabled())
                from PySide6.QtGui import QColor, QPalette

                palette = dialog.table.palette()
                self.assertEqual(palette.color(QPalette.ColorRole.Base), QColor("#ffffff"))
                self.assertEqual(palette.color(QPalette.ColorRole.AlternateBase), QColor("#f5f7fa"))
                self.assertEqual(palette.color(QPalette.ColorRole.Text), QColor("#172033"))
                self.assertEqual(palette.color(QPalette.ColorRole.Highlight), QColor("#dbeafe"))
                self.assertEqual(palette.color(QPalette.ColorRole.HighlightedText), QColor("#172033"))
                from PySide6.QtWidgets import QFrame, QMessageBox

                self.assertEqual(dialog.table.frameShape(), QFrame.Shape.Box)
                self.assertEqual(dialog.table.lineWidth(), 1)
                self.assertIn("border: 1px solid #d8e0ea", dialog.table.styleSheet())
                self.assertTrue(dialog.delete_button.isEnabled())
                with patch(
                    "frame_timing_agent.ui.history_dialog.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    dialog.delete_button.click()
                deadline = time.monotonic() + 2
                while dialog._delete_task is not None and time.monotonic() < deadline:
                    QApplication.processEvents()
                    time.sleep(0.01)
                self.assertEqual(deleted, [record])
                self.assertEqual(dialog.table.rowCount(), 0)
            finally:
                dialog.close()

    def test_run_history_dialog_deletes_in_background(self):
        import tempfile
        import threading
        import time
        from pathlib import Path

        from PySide6.QtWidgets import QApplication, QMessageBox

        from frame_timing_agent.ui.history import RunRecord
        from frame_timing_agent.ui.history_dialog import RunHistoryDialog

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame_dir = root / "frames"
            artifact_dir = root / "run"
            frame_dir.mkdir()
            artifact_dir.mkdir()
            record = RunRecord(
                run_id="run-1",
                created_at="2026-07-15T10:00:00+08:00",
                updated_at="2026-07-15T10:00:00+08:00",
                frame_dir=frame_dir,
                artifact_dir=artifact_dir,
                fps=30.0,
                analyzed_count=1,
                estimated_output_count=1,
                output_count=None,
                output_dir=None,
                status="analyzed",
                strategy_name="reconstruction_balanced",
            )
            started = threading.Event()
            release = threading.Event()

            def slow_delete(_record):
                started.set()
                release.wait(timeout=2)

            dialog = RunHistoryDialog([record], delete_callback=slow_delete)
            dialog.show()
            try:
                with patch(
                    "frame_timing_agent.ui.history_dialog.QMessageBox.question",
                    return_value=QMessageBox.StandardButton.Yes,
                ):
                    dialog.delete_button.click()
                deadline = time.monotonic() + 2
                while not started.is_set() and time.monotonic() < deadline:
                    QApplication.processEvents()
                    time.sleep(0.01)

                self.assertTrue(started.is_set())
                self.assertEqual(dialog.table.rowCount(), 1)
                self.assertFalse(dialog.delete_button.isEnabled())
                self.assertTrue(dialog.isVisible())
                dialog.reject()
                QApplication.processEvents()
                self.assertTrue(dialog.isVisible())

                release.set()
                deadline = time.monotonic() + 2
                while dialog._delete_task is not None and time.monotonic() < deadline:
                    QApplication.processEvents()
                    time.sleep(0.01)
                self.assertEqual(dialog.table.rowCount(), 0)
            finally:
                release.set()
                dialog.close()

    def test_main_window_restores_persisted_directory_fps_and_geometry(self):
        import tempfile
        from pathlib import Path

        from PySide6.QtCore import QSettings

        from frame_timing_agent.ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            settings_path = root / "settings.ini"
            settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
            first = MainWindow(settings=settings)
            try:
                first.path_edit.setText(str(frames))
                first.fps_spin.setValue(24)
                first.resize(1100, 720)
                first._save_preferences()
                self.assertIsNotNone(settings.value("ui/geometry"))
            finally:
                first.close()

            restored = MainWindow(settings=QSettings(str(settings_path), QSettings.Format.IniFormat))
            try:
                self.assertEqual(restored.path_edit.text(), str(frames))
                self.assertEqual(restored.fps_spin.value(), 24)
                self.assertGreaterEqual(restored.size().width(), restored.minimumWidth())
                self.assertGreaterEqual(restored.size().height(), restored.minimumHeight())
            finally:
                restored.close()

    def test_analysis_and_export_update_one_history_record(self):
        import tempfile
        from pathlib import Path

        from frame_timing_agent.ui.history import RunHistoryStore
        from frame_timing_agent.ui.main_window import MainWindow
        from frame_timing_agent.ui.view_model import AnalysisViewData, ExecutionSummary
        from frame_timing_agent.ui.worker import RunSettings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_dir = root / "output" / "run-1"
            frames.mkdir()
            artifact_dir.mkdir(parents=True)
            store = RunHistoryStore(root / "app-data" / "run_history.json")
            view = AnalysisViewData(
                analyzed_count=3,
                estimated_output_count=2,
                strategy_name="reconstruction_balanced",
                source_indices=(0, 1, 2),
                motion_values=(0.0, 0.2, 0.1),
                sharpness_values=(1.0, 2.0, 3.0),
                contrast_values=(4.0, 5.0, 6.0),
                segments=(),
                operation_counts={},
                thumbnails=(),
                artifact_dir=artifact_dir,
                output_dir=None,
                execution=None,
            )
            window = MainWindow(history_store=store)
            try:
                window._current_settings = RunSettings(frames, artifact_dir, fps=30.0)
                window._begin_task("正在分析帧目录")
                window._analysis_finished(view)
                self.assertEqual(len(store.list_records()), 1)
                self.assertEqual(store.list_records()[0].status, "analyzed")

                output_dir = artifact_dir / "output_frames"
                output_dir.mkdir()
                exported = replace(
                    view,
                    output_dir=output_dir,
                    execution=ExecutionSummary("ok", 2, 0, 0),
                )
                window._begin_task("正在生成 output_frames")
                window._export_finished(exported)

                records = store.list_records()
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0].status, "exported")
                self.assertEqual(records[0].output_count, 2)
                self.assertEqual(records[0].output_dir, output_dir)
                self.assertFalse(window.export_button.isEnabled())
                self.assertTrue(window.open_output_button.isEnabled())
            finally:
                window._busy = False
                window._set_controls_busy(False)
                window.close()

    def test_reopened_history_is_read_only_and_actions_are_locked_while_busy(self):
        import tempfile
        from pathlib import Path

        from frame_timing_agent.ui.history import RunRecord
        from frame_timing_agent.ui.main_window import MainWindow
        from frame_timing_agent.ui.view_model import AnalysisViewData
        from frame_timing_agent.ui.worker import RunSettings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact_dir = root / "run"
            frames.mkdir()
            artifact_dir.mkdir()
            view = AnalysisViewData(
                analyzed_count=1,
                estimated_output_count=1,
                strategy_name="reconstruction_balanced",
                source_indices=(0,),
                motion_values=(0.0,),
                sharpness_values=(1.0,),
                contrast_values=(1.0,),
                segments=(),
                operation_counts={},
                thumbnails=(),
                artifact_dir=artifact_dir,
                output_dir=None,
                execution=None,
            )
            record = RunRecord(
                run_id="run-1",
                created_at="2026-07-15T10:00:00+08:00",
                updated_at="2026-07-15T10:00:00+08:00",
                frame_dir=frames,
                artifact_dir=artifact_dir,
                fps=30.0,
                analyzed_count=1,
                estimated_output_count=1,
                output_count=None,
                output_dir=None,
                status="analyzed",
                strategy_name="reconstruction_balanced",
            )
            settings = RunSettings(frames, artifact_dir)
            view = replace(view, source_snapshot_matches=False)
            window = MainWindow()
            try:
                window._begin_task("正在打开历史结果")
                window._history_loaded(record, settings, view)

                self.assertFalse(window.export_button.isEnabled())
                self.assertIn("历史结果只读", window.export_button.toolTip())
                self.assertIn("源帧已变化", window.status_label.text())

                window._begin_task("正在处理")
                self.assertFalse(window.browse_button.isEnabled())
                self.assertFalse(window.open_artifact_button.isEnabled())
                self.assertFalse(window.open_output_button.isEnabled())
            finally:
                window._busy = False
                window._set_controls_busy(False)
                window.close()

    def test_long_output_path_does_not_resize_main_columns(self):
        from PySide6.QtWidgets import QApplication

        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        window.resize(1240, 780)
        window.show()
        try:
            QApplication.processEvents()
            chart_width = window.chart.width()

            window.destination_value.setText(
                "D:\\very-long-project-name\\01_preprocess\\output\\frame_timing_ui\\clean_frames\\"
                + ("nested-output-folder\\" * 8)
                + r"20260715-141013-262433-80918577\output_frames"
            )
            window._begin_task("正在计算帧指标")
            window.resize(1241, 780)
            window.resize(1240, 780)
            QApplication.processEvents()

            self.assertLessEqual(abs(window.chart.width() - chart_width), 2)
            self.assertTrue(window.destination_value.isReadOnly())
            self.assertLessEqual(window.destination_value.height(), 32)
            for label in window.operation_labels.values():
                self.assertGreaterEqual(label.height(), label.fontMetrics().height())
        finally:
            window._busy = False
            window.close()

    def test_thumbnail_pixmap_is_scaled_inside_current_label_size(self):
        from PySide6.QtGui import QColor, QPixmap
        from PySide6.QtWidgets import QApplication

        from frame_timing_agent.ui.widgets import ThumbnailImage

        source = QPixmap(400, 200)
        source.fill(QColor("#2878f0"))
        image = ThumbnailImage()
        image.resize(100, 80)
        image.set_source_pixmap(source)
        image.show()
        try:
            QApplication.processEvents()
            displayed = image.pixmap()

            self.assertIsNotNone(displayed)
            self.assertLessEqual(displayed.width(), image.contentsRect().width())
            self.assertLessEqual(displayed.height(), image.contentsRect().height())
            self.assertEqual(displayed.width(), 100)
            self.assertEqual(displayed.height(), 50)
        finally:
            image.close()

    def test_history_background_task_keeps_callbacks_alive_until_completion(self):
        import tempfile
        import time
        from pathlib import Path
        from unittest.mock import patch

        from PySide6.QtWidgets import QApplication

        from frame_timing_agent.ui.history import RunRecord
        from frame_timing_agent.ui.main_window import MainWindow
        from frame_timing_agent.ui.view_model import AnalysisViewData

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact = root / "run"
            frames.mkdir()
            artifact.mkdir()
            view = AnalysisViewData(
                analyzed_count=1,
                estimated_output_count=1,
                strategy_name="reconstruction_balanced",
                source_indices=(0,),
                motion_values=(0.0,),
                sharpness_values=(1.0,),
                contrast_values=(1.0,),
                segments=(),
                operation_counts={},
                thumbnails=(),
                artifact_dir=artifact,
                output_dir=None,
                execution=None,
            )
            record = RunRecord(
                run_id="run-1",
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
            window = MainWindow()
            try:
                with patch("frame_timing_agent.ui.main_window.load_existing_run", return_value=view):
                    window._load_history_record(record)
                    deadline = time.monotonic() + 2
                    while window._busy and time.monotonic() < deadline:
                        QApplication.processEvents()
                        time.sleep(0.01)

                self.assertFalse(window._busy)
                self.assertIs(window._current_view, view)
                self.assertEqual(window.progress.value(), 100)
            finally:
                window._busy = False
                window.close()

    def test_strategy_operation_values_have_text_safety_padding(self):
        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            for row, label in enumerate(window.operation_labels.values()):
                self.assertGreaterEqual(label.minimumWidth(), 84)
                self.assertGreaterEqual(label.contentsMargins().left(), 8)
                self.assertGreaterEqual(window.operation_grid.rowMinimumHeight(row), 22)
            style = window.styleSheet()
            self.assertLess(style.index('"Microsoft YaHei UI"'), style.index('"Segoe UI"'))
        finally:
            window.close()

    def test_returning_from_history_restores_current_exportable_result(self):
        import tempfile
        import time
        from pathlib import Path
        from unittest.mock import patch

        from PySide6.QtWidgets import QApplication

        from frame_timing_agent.ui.history import RunRecord
        from frame_timing_agent.ui.main_window import MainWindow
        from frame_timing_agent.ui.view_model import AnalysisViewData
        from frame_timing_agent.ui.worker import RunSettings

        def make_view(artifact: Path, analyzed_count: int) -> AnalysisViewData:
            return AnalysisViewData(
                analyzed_count=analyzed_count,
                estimated_output_count=analyzed_count,
                strategy_name="reconstruction_balanced",
                source_indices=(0,),
                motion_values=(0.0,),
                sharpness_values=(1.0,),
                contrast_values=(1.0,),
                segments=(),
                operation_counts={},
                thumbnails=(),
                artifact_dir=artifact,
                output_dir=None,
                execution=None,
            )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            current_artifact = root / "current"
            history_artifact = root / "history"
            frames.mkdir()
            current_artifact.mkdir()
            history_artifact.mkdir()
            current_view = make_view(current_artifact, 10)
            history_view = make_view(history_artifact, 5)
            history_record = RunRecord(
                run_id="history",
                created_at="2026-07-15T10:00:00+08:00",
                updated_at="2026-07-15T10:00:00+08:00",
                frame_dir=frames,
                artifact_dir=history_artifact,
                fps=30.0,
                analyzed_count=5,
                estimated_output_count=5,
                output_count=None,
                output_dir=None,
                status="analyzed",
                strategy_name="reconstruction_balanced",
            )
            window = MainWindow()
            try:
                window._current_settings = RunSettings(frames, current_artifact)
                window._begin_task("正在分析")
                window._analysis_finished(current_view)
                self.assertTrue(window.export_button.isEnabled())

                with patch("frame_timing_agent.ui.main_window.load_existing_run", return_value=history_view):
                    window._load_history_record(history_record)
                    deadline = time.monotonic() + 2
                    while window._busy and time.monotonic() < deadline:
                        QApplication.processEvents()
                        time.sleep(0.01)

                self.assertIs(window._current_view, history_view)
                self.assertFalse(window.return_current_button.isHidden())
                self.assertFalse(window.export_button.isEnabled())

                window._return_to_current_result()

                self.assertIs(window._current_view, current_view)
                self.assertTrue(window.return_current_button.isHidden())
                self.assertTrue(window.export_button.isEnabled())
                self.assertEqual(window.input_value.text(), "10")
            finally:
                window._busy = False
                window.close()

    def test_main_window_exposes_consistent_modern_visual_hierarchy(self):
        from PySide6.QtWidgets import QFrame, QLabel

        from frame_timing_agent.ui.main_window import MainWindow

        window = MainWindow()
        try:
            self.assertIsNotNone(window.findChild(QLabel, "brandIcon"))
            summary_cards = window.findChildren(QFrame, "summaryCard")
            self.assertEqual(len(summary_cards), 3)
            self.assertEqual(window.findChild(QLabel, "summaryIconBlue").property("iconKind"), "frames")
            self.assertEqual(window.findChild(QLabel, "summaryIconPurple").property("iconKind"), "strategy")
            self.assertEqual(window.findChild(QLabel, "summaryIconGreen").property("iconKind"), "output")
            for panel in window.findChildren(QFrame, "panel"):
                self.assertIsNone(panel.graphicsEffect())
            self.assertIsNotNone(window.findChild(QFrame, "metricSwitch"))
            self.assertGreaterEqual(window.findChild(QFrame, "header").minimumHeight(), 58)
            self.assertGreaterEqual(window.minimumWidth(), 1100)
            self.assertGreaterEqual(window.path_edit.minimumWidth(), 240)
            style = window.styleSheet()
            self.assertIn("background: #f7f9fc", style)
            self.assertIn("border-radius: 8px", style)
            self.assertIn("QFrame#summaryCard", style)
            self.assertIn("QPushButton#primaryButton", style)
        finally:
            window.close()

    def test_ui_smoke_entrypoint_runs_the_real_window_lifecycle(self):
        from frame_timing_agent.ui.app import main

        self.assertEqual(main(["--smoke-test"]), 0)

    def test_main_window_validates_form_and_schedules_analysis_and_export(self):
        import tempfile
        from pathlib import Path

        from frame_timing_agent.ui.main_window import MainWindow
        from frame_timing_agent.ui.view_model import AnalysisViewData

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            frames.mkdir()
            (frames / "frame_000000.jpg").write_bytes(b"frame")
            artifact = root / "artifact"
            view = AnalysisViewData(
                analyzed_count=1,
                estimated_output_count=1,
                strategy_name="reconstruction_balanced",
                source_indices=(0,),
                motion_values=(0.0,),
                sharpness_values=(1.0,),
                contrast_values=(1.0,),
                segments=(),
                operation_counts={},
                thumbnails=(),
                artifact_dir=artifact,
                output_dir=None,
                execution=None,
            )
            window = MainWindow()
            try:
                window.path_edit.setText(str(root / "missing"))
                with patch("frame_timing_agent.ui.main_window.QMessageBox.warning") as warning:
                    self.assertIsNone(window._settings_from_form())
                warning.assert_called_once()

                window.path_edit.setText(str(frames))
                settings = window._settings_from_form()
                self.assertIsNotNone(settings)
                self.assertEqual(settings.frame_dir, frames.resolve())

                scheduled = []
                with (
                    patch("frame_timing_agent.ui.main_window.create_task", return_value=object()) as create,
                    patch.object(window._thread_pool, "start", side_effect=scheduled.append),
                ):
                    window._start_analysis()
                self.assertEqual(scheduled, [window._active_task])
                self.assertTrue(window._busy)
                self.assertIn("output_frames", window.destination_value.text())
                self.assertTrue(callable(create.call_args.args[0]))

                window._busy = False
                window._current_view = view
                window._current_settings = settings
                window._export_completed = False
                scheduled.clear()
                with (
                    patch("frame_timing_agent.ui.main_window.create_task", return_value=object()) as create,
                    patch.object(window._thread_pool, "start", side_effect=scheduled.append),
                ):
                    window._start_export()
                self.assertEqual(scheduled, [window._active_task])
                self.assertTrue(window._busy)
                self.assertTrue(callable(create.call_args.args[0]))
            finally:
                window._busy = False
                window.close()

    def test_main_window_directory_open_reset_and_close_guards(self):
        import tempfile
        from pathlib import Path
        from types import SimpleNamespace

        from frame_timing_agent.ui.main_window import MainWindow
        from frame_timing_agent.ui.view_model import AnalysisViewData

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact = root / "artifact"
            analysis = artifact / "analysis"
            output = artifact / "output_frames"
            frames.mkdir()
            analysis.mkdir(parents=True)
            output.mkdir()
            view = AnalysisViewData(
                analyzed_count=1,
                estimated_output_count=1,
                strategy_name="reconstruction_balanced",
                source_indices=(0,),
                motion_values=(0.0,),
                sharpness_values=(1.0,),
                contrast_values=(1.0,),
                segments=(),
                operation_counts={},
                thumbnails=(),
                artifact_dir=artifact,
                output_dir=output,
                execution=None,
            )
            window = MainWindow()
            try:
                with patch(
                    "frame_timing_agent.ui.main_window.QFileDialog.getExistingDirectory",
                    return_value=str(frames),
                ):
                    window._choose_directory()
                self.assertEqual(window.path_edit.text(), str(frames))

                window._current_view = view
                window._current_settings = SimpleNamespace(frame_dir=frames, fps=30.0)
                with patch("frame_timing_agent.ui.main_window.QDesktopServices.openUrl") as opened:
                    window._open_artifact()
                    window._open_output()
                self.assertEqual(opened.call_count, 2)

                window._switch_metric("sharpness")
                self.assertEqual(window.chart._values, view.sharpness_values)
                window._switch_metric("contrast")
                self.assertEqual(window.chart._values, view.contrast_values)

                window._clear_result_display("cleared")
                self.assertEqual(window.status_label.text(), "cleared")
                self.assertEqual(window.input_value.text(), "--")
                self.assertFalse(window.export_button.isEnabled())

                ignored = SimpleNamespace(ignore=lambda: None)
                window._busy = True
                with (
                    patch("frame_timing_agent.ui.main_window.QMessageBox.information") as information,
                    patch.object(ignored, "ignore") as ignore,
                ):
                    window.closeEvent(ignored)
                information.assert_called_once()
                ignore.assert_called_once()
            finally:
                window._busy = False
                window.close()

    def test_main_window_handles_history_read_errors_and_record_updates(self):
        import tempfile
        from pathlib import Path

        from frame_timing_agent.ui.history import RunHistoryStore
        from frame_timing_agent.ui.main_window import MainWindow
        from frame_timing_agent.ui.view_model import AnalysisViewData, ExecutionSummary
        from frame_timing_agent.ui.worker import RunSettings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frames = root / "frames"
            artifact = root / "artifact"
            frames.mkdir()
            artifact.mkdir()
            store = RunHistoryStore(root / "history.json")
            window = MainWindow(history_store=store)
            view = AnalysisViewData(
                analyzed_count=2,
                estimated_output_count=1,
                strategy_name="reconstruction_balanced",
                source_indices=(0, 1),
                motion_values=(0.0, 0.1),
                sharpness_values=(1.0, 2.0),
                contrast_values=(1.0, 2.0),
                segments=(),
                operation_counts={},
                thumbnails=(),
                artifact_dir=artifact,
                output_dir=artifact / "output_frames",
                execution=ExecutionSummary("ok", 1, 0, 0),
            )
            try:
                window._current_settings = RunSettings(frames, artifact)
                record = window._record_from_view(view, "analyzed")
                window._current_record = record
                window._persist_current_record()
                self.assertEqual(store.list_records(), [record])

                window._update_current_record(view, "exported")
                updated = store.list_records()[0]
                self.assertEqual(updated.status, "exported")
                self.assertEqual(updated.output_count, 1)

                with (
                    patch.object(store, "list_records", side_effect=ValueError("broken history")),
                    patch("frame_timing_agent.ui.main_window.QMessageBox.critical") as critical,
                ):
                    window._show_history()
                critical.assert_called_once()
            finally:
                window.close()


if __name__ == "__main__":
    unittest.main()
