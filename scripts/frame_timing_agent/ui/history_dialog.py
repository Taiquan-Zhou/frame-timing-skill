from __future__ import annotations

from collections.abc import Callable
from functools import partial
from pathlib import Path

from PySide6.QtCore import QThreadPool, Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QStyle,
)

from frame_timing_agent.ui.history import RunRecord
from frame_timing_agent.ui.worker import create_task


STATUS_LABELS = {
    "analyzed": "分析完成",
    "exported": "输出完成",
    "export_warning": "输出有警告",
}


class RunHistoryDialog(QDialog):
    def __init__(
        self,
        records: list[RunRecord],
        parent=None,
        delete_callback: Callable[[RunRecord], object] | None = None,
        deleted_callback: Callable[[RunRecord], None] | None = None,
        protected_run_ids: set[str] | None = None,
    ):
        super().__init__(parent)
        self._records = list(records)
        self._delete_callback = delete_callback
        self._deleted_callback = deleted_callback
        self._protected_run_ids = protected_run_ids or set()
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._delete_task = None
        self._delete_busy = False
        self.setWindowTitle("运行记录")
        self.resize(920, 430)
        self.setMinimumSize(760, 360)

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 18)
        root.setSpacing(14)
        title = QLabel("运行记录")
        title.setObjectName("sectionTitle")
        root.addWidget(title)

        self.table = QTableWidget(len(records), 6)
        self.table.setHorizontalHeaderLabels(("时间", "帧目录", "分析帧数", "预计/实际输出", "状态", "FPS"))
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setFrameShape(QFrame.Shape.Box)
        self.table.setLineWidth(1)
        self.table.setStyleSheet(
            """
            QTableWidget {
                background: #ffffff;
                border: 1px solid #d8e0ea;
                border-radius: 7px;
                gridline-color: #e2e8f0;
                outline: none;
            }
            QTableWidget::item { padding: 7px 8px; }
            QHeaderView::section {
                background: #f8fafc;
                color: #475569;
                border: none;
                border-right: 1px solid #e2e8f0;
                border-bottom: 1px solid #d8e0ea;
                padding: 8px;
                font-weight: 600;
            }
            """
        )
        table_palette = self.table.palette()
        table_palette.setColor(QPalette.ColorRole.Base, QColor("#ffffff"))
        table_palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#f5f7fa"))
        table_palette.setColor(QPalette.ColorRole.Text, QColor("#172033"))
        table_palette.setColor(QPalette.ColorRole.Highlight, QColor("#dbeafe"))
        table_palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#172033"))
        self.table.setPalette(table_palette)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(38)
        header = self.table.horizontalHeader()
        header.setMinimumHeight(38)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for column in (2, 3, 4, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self._populate()
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.itemDoubleClicked.connect(lambda _item: self._accept_selected())
        root.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        self.delete_button = QPushButton("删除记录与产物")
        self.delete_button.setObjectName("destructiveButton")
        self.delete_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.delete_button.clicked.connect(self._delete_selected)
        actions.addWidget(self.delete_button)
        self.open_artifact_button = QPushButton("打开分析产物")
        self.open_artifact_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.open_artifact_button.clicked.connect(self._open_artifact)
        actions.addWidget(self.open_artifact_button)
        self.open_output_button = QPushButton("打开输出目录")
        self.open_output_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.open_output_button.clicked.connect(self._open_output)
        actions.addWidget(self.open_output_button)
        actions.addStretch()
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.reject)
        actions.addWidget(self.close_button)
        self.reopen_button = QPushButton("重新打开结果")
        self.reopen_button.setObjectName("primaryButton")
        self.reopen_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton))
        self.reopen_button.clicked.connect(self._accept_selected)
        actions.addWidget(self.reopen_button)
        root.addLayout(actions)

        if records:
            self.table.selectRow(0)
        self._update_actions()

    def selected_record(self) -> RunRecord | None:
        row = self.table.currentRow()
        if 0 <= row < len(self._records):
            return self._records[row]
        return None

    def _populate(self) -> None:
        for row, record in enumerate(self._records):
            output_count = record.output_count if record.output_count is not None else record.estimated_output_count
            values = (
                record.created_at.replace("T", " ")[:19],
                str(record.frame_dir),
                f"{record.analyzed_count:,}",
                f"{output_count:,}",
                STATUS_LABELS.get(record.status, record.status),
                f"{record.fps:g}",
            )
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column in (2, 3, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)

    def _update_actions(self) -> None:
        if self._delete_busy:
            for widget in (
                self.reopen_button,
                self.open_artifact_button,
                self.open_output_button,
                self.delete_button,
                self.close_button,
                self.table,
            ):
                widget.setEnabled(False)
            return
        self.table.setEnabled(True)
        self.close_button.setEnabled(True)
        record = self.selected_record()
        self.reopen_button.setEnabled(record is not None and self._can_reopen(record))
        self.open_artifact_button.setEnabled(record is not None and record.artifact_dir.is_dir())
        self.open_output_button.setEnabled(
            record is not None and record.output_dir is not None and record.output_dir.is_dir()
        )
        self.delete_button.setEnabled(
            record is not None and self._delete_callback is not None and record.run_id not in self._protected_run_ids
        )

    def _can_reopen(self, record: RunRecord) -> bool:
        return (record.artifact_dir / "analysis" / "strategy.json").is_file()

    def _accept_selected(self) -> None:
        record = self.selected_record()
        if record is not None and self._can_reopen(record):
            self.accept()

    def _open_artifact(self) -> None:
        record = self.selected_record()
        if record is not None:
            analysis_dir = record.artifact_dir / "analysis"
            self._open_path(analysis_dir if analysis_dir.is_dir() else record.artifact_dir)

    def _open_output(self) -> None:
        record = self.selected_record()
        if record is not None and record.output_dir is not None:
            self._open_path(record.output_dir)

    def _delete_selected(self) -> None:
        record = self.selected_record()
        if record is None or self._delete_callback is None or record.run_id in self._protected_run_ids:
            return
        answer = QMessageBox.question(
            self,
            "删除历史产物",
            "将删除这条运行记录及其分析产物和 output_frames。\n源帧目录不会被删除。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._delete_busy = True
        self._update_actions()
        task = create_task(
            lambda: self._delete_callback(record),
            partial(self._delete_succeeded, record),
            self._delete_failed,
        )
        self._delete_task = task
        self._thread_pool.start(task)

    def _delete_succeeded(self, record: RunRecord, _result: object) -> None:
        self._delete_task = None
        self._delete_busy = False
        try:
            row = self._records.index(record)
        except ValueError:
            self._update_actions()
            return
        self._records.pop(row)
        self.table.setRowCount(len(self._records))
        self._populate()
        if self._records:
            self.table.selectRow(min(row, len(self._records) - 1))
        self._update_actions()
        if self._deleted_callback is not None:
            self._deleted_callback(record)

    def _delete_failed(self, message: str) -> None:
        self._delete_task = None
        self._delete_busy = False
        self._update_actions()
        QMessageBox.critical(self, "删除失败", message)

    def closeEvent(self, event) -> None:
        if self._delete_busy:
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if not self._delete_busy:
            super().reject()

    def _open_path(self, path: Path) -> None:
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
