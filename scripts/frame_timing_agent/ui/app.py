from __future__ import annotations

from collections.abc import Sequence
import argparse
import sys


def create_application(argv: Sequence[str] | None = None):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    existing = QApplication.instance()
    if existing is not None:
        return existing
    application = QApplication(list(argv) if argv is not None else sys.argv)
    application.setApplicationName("Frame Timing Skill")
    application.setOrganizationName("frame-timing-skill")
    application.setStyle("Fusion")
    application.setAttribute(Qt.ApplicationAttribute.AA_DontShowIconsInMenus, False)
    return application


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Open the local Frame Timing Skill desktop UI.")
    parser.add_argument("--smoke-test", action="store_true", help="Create the main window and exit immediately.")
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QMessageBox
    except ModuleNotFoundError:
        print('PySide6 is required. Install it with: python -m pip install -e ".[ui]"', file=sys.stderr)
        return 2
    except ImportError as exc:
        print(f"PySide6 could not start: {exc}", file=sys.stderr)
        return 1

    from frame_timing_agent.ui.main_window import MainWindow

    application = create_application(["frame-timing-ui"] if args.smoke_test else None)
    window = MainWindow()
    window.show()
    if args.smoke_test:
        QTimer.singleShot(0, application.quit)
    try:
        return application.exec()
    except Exception as exc:
        QMessageBox.critical(None, "Frame Timing Skill", f"程序发生错误：\n{type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
