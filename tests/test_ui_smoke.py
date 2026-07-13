import importlib.util
import os
import unittest


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


if __name__ == "__main__":
    unittest.main()
