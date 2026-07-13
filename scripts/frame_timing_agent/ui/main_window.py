from __future__ import annotations

from functools import partial
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt, QThreadPool, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from frame_timing_agent.ui.view_model import AnalysisViewData, SegmentView, ThumbnailView
from frame_timing_agent.ui.worker import RunSettings, create_task, default_artifact_dir, run_analysis, run_export


SEGMENT_COLORS = {
    "static": QColor("#36a269"),
    "fast_motion": QColor("#f39c3d"),
    "very_fast_motion": QColor("#e5484d"),
    "low_motion_review": QColor("#8b5cf6"),
}

OPERATION_COLORS = {
    "keep": "#36a269",
    "keep_uniform": "#36a269",
    "duplicate_range": "#2878f0",
    "select_sources": "#f39c3d",
    "mark_review": "#e5484d",
}

SEGMENT_LABELS = {
    "static": "静止",
    "fast_motion": "快速运动",
    "very_fast_motion": "极快运动",
    "low_motion_review": "低运动待复核",
}

OPERATION_LABELS = {
    "keep": "原样保留",
    "keep_uniform": "静止段压缩",
    "duplicate_range": "运动段补帧",
    "select_sources": "稳定帧选择",
    "mark_review": "待人工复核",
}


class LineChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._sources: tuple[int, ...] = ()
        self._values: tuple[float, ...] = ()
        self._label = "运动强度"
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_series(self, sources: tuple[int, ...], values: tuple[float, ...], label: str) -> None:
        self._sources = sources
        self._values = values
        self._label = label
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        plot = self.rect().adjusted(52, 22, -18, -34)
        axis_pen = QPen(QColor("#d9dee8"), 1)
        painter.setPen(axis_pen)
        for index in range(5):
            y = plot.top() + plot.height() * index / 4
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))

        painter.setPen(QColor("#667085"))
        painter.setFont(QFont("Segoe UI", 9))
        painter.drawText(8, 18, self._label)
        if not self._values or not self._sources:
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "选择帧目录并开始分析")
            return

        minimum = min(self._values)
        maximum = max(self._values)
        span = maximum - minimum or 1.0
        path = QPainterPath()
        for index, value in enumerate(self._values):
            x = plot.left() + plot.width() * index / max(1, len(self._values) - 1)
            y = plot.bottom() - plot.height() * (value - minimum) / span
            point = QPointF(x, y)
            if index == 0:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(QColor("#2878f0"), 2))
        painter.drawPath(path)

        painter.setPen(QColor("#667085"))
        painter.drawText(4, plot.top() + 5, f"{maximum:.2f}")
        painter.drawText(4, plot.bottom(), f"{minimum:.2f}")
        painter.drawText(plot.left(), self.height() - 8, str(self._sources[0]))
        end_text = str(self._sources[-1])
        painter.drawText(plot.right() - 42, self.height() - 8, end_text)
        painter.drawText(plot, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter, "源帧索引")


class SegmentBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._segments: tuple[SegmentView, ...] = ()
        self._source_min = 0
        self._source_max = 1
        self.setFixedHeight(58)

    def set_segments(self, segments: tuple[SegmentView, ...], source_min: int, source_max: int) -> None:
        self._segments = segments
        self._source_min = source_min
        self._source_max = max(source_min + 1, source_max)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(0, 4, self.width(), 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#edf0f5"))
        painter.drawRoundedRect(bar, 4, 4)
        span = self._source_max - self._source_min + 1
        for segment in self._segments:
            start_ratio = (segment.start - self._source_min) / span
            end_ratio = (segment.end - self._source_min + 1) / span
            segment_rect = QRectF(
                max(0.0, start_ratio) * self.width(),
                4,
                max(3.0, (end_ratio - start_ratio) * self.width()),
                12,
            )
            painter.setBrush(SEGMENT_COLORS.get(segment.segment_type, QColor("#98a2b3")))
            painter.drawRoundedRect(segment_rect, 3, 3)

        painter.setFont(QFont("Segoe UI", 8))
        x = 0
        for segment_type in ("static", "fast_motion", "very_fast_motion", "low_motion_review"):
            painter.setBrush(SEGMENT_COLORS[segment_type])
            painter.drawRoundedRect(QRectF(x, 31, 10, 10), 2, 2)
            painter.setPen(QColor("#667085"))
            label = SEGMENT_LABELS[segment_type]
            painter.drawText(x + 15, 41, label)
            x += painter.fontMetrics().horizontalAdvance(label) + 38
            painter.setPen(Qt.PenStyle.NoPen)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Frame Timing Skill")
        self.resize(1240, 780)
        self.setMinimumSize(980, 680)
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._busy = False
        self._current_settings: RunSettings | None = None
        self._current_view: AnalysisViewData | None = None
        self._metric_name = "motion"
        self._build_ui()
        self._apply_style()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 16)
        root.setSpacing(12)
        root.addWidget(self._build_header())
        root.addLayout(self._build_summary())

        body = QHBoxLayout()
        body.setSpacing(12)
        left = QVBoxLayout()
        left.setSpacing(12)
        left.addWidget(self._build_analysis_panel(), 3)
        left.addWidget(self._build_thumbnail_panel(), 2)
        body.addLayout(left, 7)

        right = QVBoxLayout()
        right.setSpacing(12)
        right.addWidget(self._build_strategy_panel(), 3)
        right.addWidget(self._build_execution_panel(), 2)
        body.addLayout(right, 3)
        root.addLayout(body, 1)
        self.setCentralWidget(central)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(9)

        title = QLabel("Frame Timing Skill")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addSpacing(12)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("选择已清理的帧目录")
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.textChanged.connect(self._invalidate_analysis)
        layout.addWidget(self.path_edit, 1)

        browse = QPushButton("选择目录")
        browse.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        browse.clicked.connect(self._choose_directory)
        layout.addWidget(browse)

        fps_label = QLabel("FPS")
        fps_label.setObjectName("muted")
        layout.addWidget(fps_label)
        self.fps_spin = QSpinBox()
        self.fps_spin.setRange(1, 240)
        self.fps_spin.setValue(30)
        self.fps_spin.setSuffix(" fps")
        self.fps_spin.valueChanged.connect(self._invalidate_analysis)
        layout.addWidget(self.fps_spin)

        self.analyze_button = QPushButton("开始分析")
        self.analyze_button.setObjectName("primaryButton")
        self.analyze_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.analyze_button.clicked.connect(self._start_analysis)
        layout.addWidget(self.analyze_button)
        return header

    def _build_summary(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setSpacing(12)
        self.input_value = self._summary_box(layout, "输入帧数", "--")
        self.strategy_value = self._summary_box(layout, "当前策略", "reconstruction_balanced")
        self.output_value = self._summary_box(layout, "预计输出", "--")
        return layout

    def _summary_box(self, parent: QHBoxLayout, label: str, value: str) -> QLabel:
        frame = QFrame()
        frame.setObjectName("panel")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(2)
        caption = QLabel(label)
        caption.setObjectName("muted")
        value_label = QLabel(value)
        value_label.setObjectName("summaryValue")
        value_label.setWordWrap(True)
        layout.addWidget(caption)
        layout.addWidget(value_label)
        parent.addWidget(frame, 1)
        return value_label

    def _build_analysis_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 10)
        layout.setSpacing(5)

        header = QHBoxLayout()
        title = QLabel("时序分析")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        group = QButtonGroup(self)
        for key, label in (("motion", "运动"), ("sharpness", "清晰度"), ("contrast", "对比度")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("segmentButton")
            button.setFixedHeight(28)
            button.clicked.connect(partial(self._switch_metric, key))
            group.addButton(button)
            header.addWidget(button)
            if key == "motion":
                button.setChecked(True)
        layout.addLayout(header)
        self.chart = LineChart()
        layout.addWidget(self.chart, 1)
        self.segment_bar = SegmentBar()
        layout.addWidget(self.segment_bar)
        return panel

    def _build_thumbnail_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 14)
        title = QLabel("代表帧")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.thumbnail_layout = QHBoxLayout()
        self.thumbnail_layout.setSpacing(8)
        self.thumbnail_placeholder = QLabel("分析后显示与策略区间相关的代表帧")
        self.thumbnail_placeholder.setObjectName("emptyState")
        self.thumbnail_placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_layout.addWidget(self.thumbnail_placeholder, 1)
        layout.addLayout(self.thumbnail_layout, 1)
        return panel

    def _build_strategy_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)
        title = QLabel("策略摘要")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        name = QLabel("reconstruction_balanced")
        name.setObjectName("strategyName")
        name.setWordWrap(True)
        layout.addWidget(name)
        note = QLabel("压缩长静止区间，补偿快速运动，并在抖动区间选择稳定帧。")
        note.setObjectName("muted")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.operation_grid = QGridLayout()
        self.operation_grid.setHorizontalSpacing(8)
        self.operation_grid.setVerticalSpacing(8)
        self.operation_labels: dict[str, QLabel] = {}
        for row, op in enumerate(("keep_uniform", "duplicate_range", "select_sources", "mark_review")):
            caption = QLabel(OPERATION_LABELS[op])
            caption.setObjectName("muted")
            value = QLabel("0 个区间")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            self.operation_grid.addWidget(caption, row, 0)
            self.operation_grid.addWidget(value, row, 1)
            self.operation_labels[op] = value
        layout.addLayout(self.operation_grid)
        layout.addStretch()
        destination_label = QLabel("输出位置")
        destination_label.setObjectName("muted")
        self.destination_value = QLabel("选择帧目录后自动生成")
        self.destination_value.setWordWrap(True)
        self.destination_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(destination_label)
        layout.addWidget(self.destination_value)
        return panel

    def _build_execution_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        title = QLabel("执行状态")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.status_label = QLabel("等待选择帧目录")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        layout.addWidget(self.progress)
        layout.addStretch()
        self.export_button = QPushButton("生成 output_frames")
        self.export_button.setObjectName("primaryButton")
        self.export_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self._start_export)
        layout.addWidget(self.export_button)
        self.open_output_button = QPushButton("打开输出目录")
        self.open_output_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.open_output_button.setEnabled(False)
        self.open_output_button.clicked.connect(self._open_output)
        layout.addWidget(self.open_output_button)
        local_note = QLabel("● 本地处理，不上传原图")
        local_note.setObjectName("localNote")
        local_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(local_note)
        return panel

    def _choose_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择帧目录", self.path_edit.text() or str(Path.home()))
        if directory:
            self.path_edit.setText(directory)

    def _invalidate_analysis(self) -> None:
        if self._busy:
            return
        self._current_view = None
        self._current_settings = None
        self.export_button.setEnabled(False)
        self.open_output_button.setEnabled(False)
        raw_path = self.path_edit.text().strip()
        if raw_path:
            self.destination_value.setText(str(default_artifact_dir(Path(raw_path)) / "output_frames"))

    def _settings_from_form(self) -> RunSettings | None:
        frame_dir = Path(self.path_edit.text().strip())
        if not self.path_edit.text().strip() or not frame_dir.is_dir():
            QMessageBox.warning(self, "帧目录无效", "请选择一个存在的帧目录。")
            return None
        return RunSettings(
            frame_dir=frame_dir,
            artifact_dir=default_artifact_dir(frame_dir),
            fps=float(self.fps_spin.value()),
            limit_first_n=None,
        )

    def _start_analysis(self) -> None:
        settings = self._settings_from_form()
        if settings is None:
            return
        self._current_settings = settings
        self._set_busy(True, "正在分析帧目录…")
        task = create_task(lambda: run_analysis(settings), self._analysis_finished, self._task_failed)
        self._thread_pool.start(task)

    def _start_export(self) -> None:
        if self._current_settings is None:
            return
        self._set_busy(True, "正在重新检查输入并生成 output_frames…")
        task = create_task(lambda: run_export(self._current_settings), self._export_finished, self._task_failed)
        self._thread_pool.start(task)

    def _analysis_finished(self, view: AnalysisViewData) -> None:
        self._current_view = view
        self._render_view(view)
        self._set_busy(False, f"分析完成：发现 {len(view.segments)} 个重点区间")
        self.export_button.setEnabled(True)

    def _export_finished(self, view: AnalysisViewData) -> None:
        self._current_view = view
        self._render_view(view)
        execution = view.execution
        if execution is not None and execution.status == "ok":
            text = f"导出完成：{execution.output_count} 帧，执行审计通过"
            if execution.warning_count:
                text += f"，{execution.warning_count} 条警告"
            self._set_busy(False, text)
            self.open_output_button.setEnabled(view.output_dir is not None)
        else:
            error_count = execution.error_count if execution is not None else 1
            self._set_busy(False, f"导出完成，但执行审计发现 {error_count} 个问题")
        self.export_button.setEnabled(True)

    def _task_failed(self, message: str) -> None:
        self._set_busy(False, f"处理失败：{message}")
        self.export_button.setEnabled(self._current_view is not None)
        QMessageBox.critical(self, "处理失败", message)

    def _set_busy(self, busy: bool, text: str) -> None:
        self._busy = busy
        self.status_label.setText(text)
        self.progress.setVisible(busy)
        self.analyze_button.setEnabled(not busy)
        self.export_button.setEnabled(not busy and self._current_view is not None)
        self.path_edit.setEnabled(not busy)
        self.fps_spin.setEnabled(not busy)

    def _render_view(self, view: AnalysisViewData) -> None:
        self.input_value.setText(f"{view.analyzed_count:,}")
        self.strategy_value.setText(view.strategy_name)
        output_count = view.execution.output_count if view.execution is not None else view.estimated_output_count
        self.output_value.setText(f"{output_count:,}")
        for op, label in self.operation_labels.items():
            label.setText(f"{view.operation_counts.get(op, 0)} 个区间")
        if view.source_indices:
            self.segment_bar.set_segments(view.segments, view.source_indices[0], view.source_indices[-1])
        self._render_metric()
        self._render_thumbnails(view.thumbnails)
        self.destination_value.setText(str(view.artifact_dir / "output_frames"))

    def _switch_metric(self, metric_name: str) -> None:
        self._metric_name = metric_name
        self._render_metric()

    def _render_metric(self) -> None:
        if self._current_view is None:
            return
        mapping = {
            "motion": (self._current_view.motion_values, "运动强度"),
            "sharpness": (self._current_view.sharpness_values, "清晰度"),
            "contrast": (self._current_view.contrast_values, "对比度"),
        }
        values, label = mapping[self._metric_name]
        self.chart.set_series(self._current_view.source_indices, values, label)

    def _render_thumbnails(self, thumbnails: tuple[ThumbnailView, ...]) -> None:
        while self.thumbnail_layout.count():
            item = self.thumbnail_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        if not thumbnails:
            placeholder = QLabel("当前策略没有可展示的代表帧")
            placeholder.setObjectName("emptyState")
            placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.thumbnail_layout.addWidget(placeholder, 1)
            return
        for thumbnail in thumbnails:
            self.thumbnail_layout.addWidget(self._thumbnail_widget(thumbnail), 1)

    def _thumbnail_widget(self, thumbnail: ThumbnailView) -> QWidget:
        frame = QFrame()
        frame.setObjectName("thumbnail")
        frame.setStyleSheet(f"QFrame#thumbnail {{ border-top: 3px solid {OPERATION_COLORS.get(thumbnail.operation, '#98a2b3')}; }}")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 4, 4, 5)
        layout.setSpacing(3)
        image = QLabel()
        image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        image.setMinimumHeight(78)
        pixmap = QPixmap(str(thumbnail.path))
        if pixmap.isNull():
            image.setText("无法读取")
        else:
            image.setPixmap(pixmap.scaled(150, 88, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        source = QLabel(f"src {thumbnail.source_index}")
        source.setAlignment(Qt.AlignmentFlag.AlignCenter)
        source.setObjectName("thumbnailSource")
        operation = QLabel(OPERATION_LABELS.get(thumbnail.operation, thumbnail.operation))
        operation.setAlignment(Qt.AlignmentFlag.AlignCenter)
        operation.setObjectName("thumbnailOp")
        operation.setWordWrap(True)
        layout.addWidget(image, 1)
        layout.addWidget(source)
        layout.addWidget(operation)
        return frame

    def _open_output(self) -> None:
        if self._current_view is not None and self._current_view.output_dir is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._current_view.output_dir)))

    def closeEvent(self, event) -> None:
        if self._busy:
            QMessageBox.information(self, "任务进行中", "请等待当前分析或导出任务完成后再关闭窗口。")
            event.ignore()
            return
        super().closeEvent(event)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f4f6f9; color: #172033; font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 13px; }
            QLabel { background: transparent; }
            QFrame#header { background: transparent; }
            QFrame#panel { background: #ffffff; border: 1px solid #dfe4ec; border-radius: 7px; }
            QFrame#thumbnail { background: #ffffff; border: 1px solid #e3e7ee; border-radius: 5px; }
            QLabel#title { font-size: 20px; font-weight: 700; color: #101828; }
            QLabel#sectionTitle { font-size: 15px; font-weight: 700; color: #101828; }
            QLabel#summaryValue { font-size: 22px; font-weight: 700; color: #101828; }
            QLabel#strategyName { font-size: 17px; font-weight: 650; color: #101828; }
            QLabel#muted, QLabel#thumbnailOp { color: #667085; }
            QLabel#emptyState { color: #98a2b3; padding: 18px; }
            QLabel#thumbnailSource { font-weight: 600; }
            QLabel#localNote { color: #248a52; padding-top: 4px; }
            QLineEdit, QSpinBox { background: #ffffff; border: 1px solid #cfd6e2; border-radius: 5px; padding: 7px 9px; min-height: 20px; }
            QLineEdit:focus, QSpinBox:focus { border: 1px solid #2878f0; }
            QPushButton { background: #ffffff; border: 1px solid #cfd6e2; border-radius: 5px; padding: 7px 13px; min-height: 20px; }
            QPushButton:hover { border-color: #2878f0; color: #155ec7; }
            QPushButton:disabled { background: #eef1f5; color: #98a2b3; border-color: #e1e5eb; }
            QPushButton#primaryButton { background: #2878f0; color: #ffffff; border-color: #2878f0; font-weight: 600; }
            QPushButton#primaryButton:hover { background: #1f68d8; }
            QPushButton#primaryButton:disabled { background: #aebed4; border-color: #aebed4; color: #eef2f7; }
            QPushButton#segmentButton { padding: 3px 12px; min-height: 18px; border-radius: 4px; }
            QPushButton#segmentButton:checked { background: #e9f1ff; color: #155ec7; border-color: #75a8f8; }
            QProgressBar { background: #e9edf3; border: none; border-radius: 3px; max-height: 6px; }
            QProgressBar::chunk { background: #2878f0; border-radius: 3px; }
            """
        )
