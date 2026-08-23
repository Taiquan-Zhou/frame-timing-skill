from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from pathlib import Path

from PySide6.QtCore import QSettings, QSignalBlocker, Qt, QThreadPool, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
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
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from frame_timing_agent.ui.batch_workspace import BatchWorkspace
from frame_timing_agent.ui.history import RunHistoryStore, RunRecord
from frame_timing_agent.ui.history_dialog import RunHistoryDialog
from frame_timing_agent.ui.style import (
    LINE_COLOR,
    OPERATION_COLORS,
    OPERATION_LABELS,
    main_window_stylesheet,
    make_line_icon as _make_line_icon,
)
from frame_timing_agent.ui.view_model import AnalysisViewData, ThumbnailView
from frame_timing_agent.ui.widgets import LineChart, SegmentBar, ThumbnailImage
from frame_timing_agent.ui.worker import (
    RunSettings,
    create_task,
    default_artifact_dir,
    delete_history_run,
    load_existing_run,
    new_run_artifact_dir,
    run_analysis,
    run_export,
)


@dataclass(frozen=True)
class CurrentResultState:
    settings: RunSettings | None
    view: AnalysisViewData
    record: RunRecord | None
    export_completed: bool
    status_text: str
    progress: int


class MainWindow(QMainWindow):
    def __init__(
        self,
        settings: QSettings | None = None,
        history_store: RunHistoryStore | None = None,
    ):
        super().__init__()
        self.setWindowTitle("Frame Timing Skill")
        self.resize(1320, 840)
        self.setMinimumSize(1100, 700)
        self._thread_pool = QThreadPool(self)
        self._thread_pool.setMaxThreadCount(1)
        self._active_task = None
        self._busy = False
        self._settings = settings
        self._history_store = history_store
        self._current_settings: RunSettings | None = None
        self._current_view: AnalysisViewData | None = None
        self._current_record: RunRecord | None = None
        self._current_result_state: CurrentResultState | None = None
        self._pending_current_result_state: CurrentResultState | None = None
        self._history_read_only = False
        self._export_completed = False
        self._metric_name = "motion"
        self._build_ui()
        self._apply_style()
        self._restore_preferences()

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(14)
        root.addWidget(self._build_header())
        root.addWidget(self._build_mode_switch())

        self.workspace_stack = QStackedWidget()
        self.workspace_stack.setObjectName("workspaceStack")
        self.single_workspace = self._build_single_workspace()
        self.batch_workspace = BatchWorkspace(
            self._thread_pool,
            self._settings,
            fps_provider=lambda: float(self.fps_spin.value()),
        )
        self.batch_workspace.running_changed.connect(self._batch_running_changed)
        self.workspace_stack.addWidget(self.single_workspace)
        self.workspace_stack.addWidget(self.batch_workspace)
        root.addWidget(self.workspace_stack, 1)
        self.setCentralWidget(central)

    def _build_mode_switch(self) -> QWidget:
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        switch = QFrame()
        switch.setObjectName("modeSwitch")
        switch_layout = QHBoxLayout(switch)
        switch_layout.setContentsMargins(0, 0, 0, 0)
        switch_layout.setSpacing(0)
        self.single_mode_button = QPushButton("单目录")
        self.batch_mode_button = QPushButton("批量处理")
        self.single_mode_button.setObjectName("modeButton")
        self.batch_mode_button.setObjectName("modeButton")
        self.single_mode_button.setCheckable(True)
        self.batch_mode_button.setCheckable(True)
        self.single_mode_button.setChecked(True)
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.single_mode_button)
        group.addButton(self.batch_mode_button)
        self._mode_group = group
        self.single_mode_button.clicked.connect(self.select_single_mode)
        self.batch_mode_button.clicked.connect(self.select_batch_mode)
        switch_layout.addWidget(self.single_mode_button)
        switch_layout.addWidget(self.batch_mode_button)
        layout.addWidget(switch)
        layout.addStretch()
        return container

    def _build_single_workspace(self) -> QWidget:
        workspace = QWidget()
        root = QVBoxLayout(workspace)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)
        root.addLayout(self._build_summary())

        body = QHBoxLayout()
        body.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        left.addWidget(self._build_analysis_panel(), 3)
        left.addWidget(self._build_thumbnail_panel(), 2)
        body.addLayout(left, 7)

        right = QVBoxLayout()
        right.setSpacing(14)
        right.addWidget(self._build_strategy_panel(), 3)
        right.addWidget(self._build_execution_panel(), 2)
        body.addLayout(right, 3)
        body.setStretch(0, 7)
        body.setStretch(1, 3)
        root.addLayout(body, 1)
        return workspace

    def select_single_mode(self) -> None:
        if self._busy or self.batch_workspace.is_running:
            return
        self.single_mode_button.setChecked(True)
        self.workspace_stack.setCurrentWidget(self.single_workspace)
        self._sync_mode_controls()

    def select_batch_mode(self) -> None:
        if self._busy:
            return
        self.batch_mode_button.setChecked(True)
        self.workspace_stack.setCurrentWidget(self.batch_workspace)
        self._sync_mode_controls()

    def _batch_running_changed(self, running: bool) -> None:
        self.single_mode_button.setEnabled(not running and not self._busy)
        self.batch_mode_button.setEnabled(not running and not self._busy)
        self._sync_mode_controls()

    def _sync_mode_controls(self) -> None:
        batch_mode = self.workspace_stack.currentWidget() is self.batch_workspace
        batch_running = self.batch_workspace.is_running
        single_enabled = not self._busy and not batch_mode and not batch_running
        self.path_edit.setEnabled(single_enabled)
        self.browse_button.setEnabled(single_enabled)
        self.analyze_button.setEnabled(single_enabled)
        self.fps_spin.setEnabled(not self._busy and not batch_running)

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        header.setMinimumHeight(60)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(14, 8, 14, 8)
        layout.setSpacing(10)

        brand_icon = QLabel()
        brand_icon.setObjectName("brandIcon")
        brand_icon.setFixedSize(34, 34)
        brand_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand_icon.setPixmap(_make_line_icon("brand", LINE_COLOR, 20))
        layout.addWidget(brand_icon)

        title = QLabel("Frame Timing Skill")
        title.setObjectName("title")
        layout.addWidget(title)
        layout.addSpacing(20)

        self.path_edit = QLineEdit()
        self.path_edit.setMinimumWidth(240)
        self.path_edit.setPlaceholderText("选择已清理的帧目录")
        self.path_edit.setClearButtonEnabled(True)
        self.path_edit.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.path_edit.textChanged.connect(self._invalidate_analysis)
        layout.addWidget(self.path_edit, 1)

        self.browse_button = QPushButton("选择目录")
        self.browse_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.browse_button.clicked.connect(self._choose_directory)
        layout.addWidget(self.browse_button)

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
        layout.setSpacing(14)
        self.input_value = self._summary_box(
            layout,
            "输入帧数",
            "--",
            "summaryIconBlue",
            "frames",
            LINE_COLOR,
        )
        self.strategy_value = self._summary_box(
            layout,
            "当前策略",
            "reconstruction_balanced",
            "summaryIconPurple",
            "strategy",
            "#7c3aed",
        )
        self.output_value = self._summary_box(
            layout,
            "预计输出",
            "--",
            "summaryIconGreen",
            "output",
            "#159f6d",
        )
        return layout

    def _summary_box(
        self,
        parent: QHBoxLayout,
        label: str,
        value: str,
        icon_object_name: str,
        icon_kind: str,
        icon_color: str,
    ) -> QLabel:
        frame = QFrame()
        frame.setObjectName("summaryCard")
        frame.setMinimumHeight(92)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(14)
        icon_label = QLabel()
        icon_label.setObjectName(icon_object_name)
        icon_label.setProperty("iconKind", icon_kind)
        icon_label.setFixedSize(46, 46)
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setPixmap(_make_line_icon(icon_kind, icon_color, 24))
        layout.addWidget(icon_label)
        text_layout = QVBoxLayout()
        text_layout.setSpacing(3)
        text_layout.addStretch()
        caption = QLabel(label)
        caption.setObjectName("summaryCaption")
        value_label = QLabel(value)
        value_label.setObjectName("summaryValue")
        value_label.setWordWrap(True)
        value_label.setMinimumWidth(0)
        value_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text_layout.addWidget(caption)
        text_layout.addWidget(value_label)
        text_layout.addStretch()
        layout.addLayout(text_layout, 1)
        parent.addWidget(frame, 1)
        self._apply_card_shadow(frame)
        return value_label

    @staticmethod
    def _apply_card_shadow(widget: QWidget) -> None:
        shadow = QGraphicsDropShadowEffect(widget)
        shadow.setBlurRadius(18)
        shadow.setOffset(0, 2)
        shadow.setColor(QColor(15, 23, 42, 20))
        widget.setGraphicsEffect(shadow)

    def _build_analysis_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 16, 20, 14)
        layout.setSpacing(8)

        header = QHBoxLayout()
        title = QLabel("时序分析")
        title.setObjectName("sectionTitle")
        header.addWidget(title)
        header.addStretch()
        switch = QFrame()
        switch.setObjectName("metricSwitch")
        switch_layout = QHBoxLayout(switch)
        switch_layout.setContentsMargins(0, 0, 0, 0)
        switch_layout.setSpacing(0)
        group = QButtonGroup(self)
        for key, label in (("motion", "运动"), ("sharpness", "清晰度"), ("contrast", "对比度")):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setObjectName("segmentButton")
            button.setProperty("last", key == "contrast")
            button.setFixedHeight(34)
            button.clicked.connect(partial(self._switch_metric, key))
            group.addButton(button)
            switch_layout.addWidget(button)
            if key == "motion":
                button.setChecked(True)
        header.addWidget(switch)
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
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(10)
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
        layout.setContentsMargins(20, 10, 20, 10)
        layout.setSpacing(5)
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
        self.operation_grid.setVerticalSpacing(2)
        self.operation_labels: dict[str, QLabel] = {}
        for row, op in enumerate(("keep_uniform", "duplicate_range", "select_sources", "mark_review")):
            self.operation_grid.setRowMinimumHeight(row, 22)
            caption = QLabel(OPERATION_LABELS[op])
            caption.setObjectName("muted")
            value = QLabel("0 个区间")
            value.setAlignment(Qt.AlignmentFlag.AlignRight)
            value.setMinimumWidth(84)
            value.setContentsMargins(8, 0, 2, 0)
            self.operation_grid.addWidget(caption, row, 0)
            self.operation_grid.addWidget(value, row, 1)
            self.operation_labels[op] = value
        layout.addLayout(self.operation_grid)
        destination_label = QLabel("输出位置")
        destination_label.setObjectName("muted")
        self.destination_value = QLineEdit("选择帧目录后自动生成")
        self.destination_value.setObjectName("destinationField")
        self.destination_value.setReadOnly(True)
        self.destination_value.setFixedHeight(30)
        self.destination_value.setMinimumWidth(0)
        self.destination_value.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.destination_value.setCursorPosition(0)
        self.destination_value.setToolTip(self.destination_value.text())
        layout.addWidget(destination_label)
        layout.addWidget(self.destination_value)
        return panel

    def _build_execution_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(9)
        title = QLabel("执行状态")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        status_row = QHBoxLayout()
        status_row.setSpacing(8)
        self.status_label = QLabel("等待选择帧目录")
        self.status_label.setWordWrap(True)
        status_row.addWidget(self.status_label, 1)
        self.progress_percent_label = QLabel("")
        self.progress_percent_label.setObjectName("progressPercent")
        self.progress_percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        self.progress_percent_label.setFixedWidth(44)
        self.progress_percent_label.hide()
        status_row.addWidget(self.progress_percent_label)
        layout.addLayout(status_row)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
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
        self.return_current_button = QPushButton("返回当前结果")
        self.return_current_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ArrowBack))
        self.return_current_button.clicked.connect(self._return_to_current_result)
        self.return_current_button.hide()
        layout.addWidget(self.return_current_button)
        self.open_artifact_button = QPushButton("打开分析产物")
        self.open_artifact_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.open_artifact_button.setEnabled(False)
        self.open_artifact_button.clicked.connect(self._open_artifact)
        layout.addWidget(self.open_artifact_button)
        self.open_output_button = QPushButton("打开输出目录")
        self.open_output_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        self.open_output_button.setEnabled(False)
        self.open_output_button.clicked.connect(self._open_output)
        layout.addWidget(self.open_output_button)
        self.history_button = QPushButton("运行记录")
        self.history_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        self.history_button.setEnabled(self._history_store is not None)
        self.history_button.clicked.connect(self._show_history)
        layout.addWidget(self.history_button)
        local_note = QLabel("● 本地处理，不上传原图")
        local_note.setObjectName("localNote")
        local_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(local_note)
        return panel

    def _choose_directory(self) -> None:
        if self._busy:
            return
        directory = QFileDialog.getExistingDirectory(self, "选择帧目录", self.path_edit.text() or str(Path.home()))
        if directory:
            self.path_edit.setText(directory)

    def _invalidate_analysis(self) -> None:
        if self._busy:
            return
        self._current_view = None
        self._current_settings = None
        self._current_record = None
        self._current_result_state = None
        self._pending_current_result_state = None
        self.return_current_button.hide()
        self._history_read_only = False
        self._export_completed = False
        self.export_button.setToolTip("")
        self.export_button.setEnabled(False)
        self.open_artifact_button.setEnabled(False)
        self.open_output_button.setEnabled(False)
        raw_path = self.path_edit.text().strip()
        if raw_path:
            preview = default_artifact_dir(Path(raw_path)) / "[新运行]" / "output_frames"
            self._set_destination_path(str(preview))

    def _settings_from_form(self) -> RunSettings | None:
        frame_dir = Path(self.path_edit.text().strip()).expanduser().resolve()
        if not self.path_edit.text().strip() or not frame_dir.is_dir():
            QMessageBox.warning(self, "帧目录无效", "请选择一个存在的帧目录。")
            return None
        return RunSettings(
            frame_dir=frame_dir,
            artifact_dir=new_run_artifact_dir(frame_dir),
            fps=float(self.fps_spin.value()),
            limit_first_n=None,
        )

    def _start_analysis(self) -> None:
        settings = self._settings_from_form()
        if settings is None:
            return
        self._current_settings = settings
        self._current_result_state = None
        self._pending_current_result_state = None
        self.return_current_button.hide()
        path_blocker = QSignalBlocker(self.path_edit)
        self.path_edit.setText(str(settings.frame_dir))
        del path_blocker
        self._history_read_only = False
        self._export_completed = False
        self.export_button.setToolTip("")
        self._set_destination_path(str(settings.artifact_dir / "output_frames"))
        self._save_preferences()
        self._begin_task("正在分析帧目录")
        task = create_task(
            lambda progress: run_analysis(settings, progress),
            self._analysis_finished,
            self._task_failed,
            self._task_progress,
        )
        self._active_task = task
        self._thread_pool.start(task)

    def _start_export(self) -> None:
        if self._current_settings is None or not self._can_export():
            return
        self._begin_task("正在重新检查输入并生成 output_frames")
        task = create_task(
            lambda progress: run_export(self._current_settings, progress),
            self._export_finished,
            self._task_failed,
            self._task_progress,
        )
        self._active_task = task
        self._thread_pool.start(task)

    def _analysis_finished(self, view: AnalysisViewData) -> None:
        self._active_task = None
        try:
            self._render_view(view)
        except Exception as exc:
            self._task_failed(f"{type(exc).__name__}: {exc}")
            return
        self._current_view = view
        if self._history_store is not None:
            self._current_record = self._record_from_view(view, "analyzed")
            self._persist_current_record()
        self._finish_task("分析帧目录完成")
        self._sync_action_buttons()

    def _export_finished(self, view: AnalysisViewData) -> None:
        self._active_task = None
        try:
            self._render_view(view)
        except Exception as exc:
            self._task_failed(f"{type(exc).__name__}: {exc}")
            return
        self._current_view = view
        self._export_completed = True
        execution = view.execution
        if execution is not None and execution.status == "ok":
            self._update_current_record(view, "exported")
            text = f"导出完成：{execution.output_count} 帧，执行审计通过"
            if execution.warning_count:
                text += f"，{execution.warning_count} 条警告"
            self._finish_task(text)
        else:
            self._update_current_record(view, "export_warning")
            error_count = execution.error_count if execution is not None else 1
            self._finish_task(f"导出完成，但执行审计发现 {error_count} 个问题")
        self._sync_action_buttons()

    def _task_failed(self, message: str) -> None:
        self._active_task = None
        self._pending_current_result_state = None
        self._busy = False
        self.status_label.setText(f"处理失败：{message}")
        self.progress.setVisible(True)
        self.progress_percent_label.setVisible(True)
        self._set_controls_busy(False)
        self._sync_action_buttons()
        QMessageBox.critical(self, "处理失败", message)

    def _begin_task(self, text: str) -> None:
        self._busy = True
        self.status_label.setText(text)
        self._set_progress(0)
        self.progress.setVisible(True)
        self.progress_percent_label.setVisible(True)
        self._set_controls_busy(True)

    def _task_progress(self, percent: int, text: str) -> None:
        if not self._busy:
            return
        self.status_label.setText(text)
        self._set_progress(percent)

    def _finish_task(self, text: str) -> None:
        self._busy = False
        self.status_label.setText(text)
        self._set_progress(100)
        self.progress.setVisible(True)
        self.progress_percent_label.setVisible(True)
        self._set_controls_busy(False)

    def _set_progress(self, percent: int) -> None:
        self.progress.setValue(max(0, min(100, int(percent))))
        self.progress_percent_label.setText(f"{self.progress.value()}%")

    def _set_controls_busy(self, busy: bool) -> None:
        self.analyze_button.setEnabled(not busy)
        self.export_button.setEnabled(not busy and self._can_export())
        self.path_edit.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.fps_spin.setEnabled(not busy)
        self.history_button.setEnabled(not busy and self._history_store is not None)
        self.open_artifact_button.setEnabled(not busy and self._artifact_available())
        self.open_output_button.setEnabled(not busy and self._output_available())
        self.single_mode_button.setEnabled(not busy and not self.batch_workspace.is_running)
        self.batch_mode_button.setEnabled(not busy and not self.batch_workspace.is_running)
        self._sync_mode_controls()

    def _can_export(self) -> bool:
        return self._current_view is not None and not self._history_read_only and not self._export_completed

    def _artifact_available(self) -> bool:
        return self._current_view is not None and self._current_view.artifact_dir.is_dir()

    def _output_available(self) -> bool:
        return (
            self._current_view is not None
            and self._current_view.output_dir is not None
            and self._current_view.output_dir.is_dir()
        )

    def _sync_action_buttons(self) -> None:
        self.export_button.setEnabled(not self._busy and self._can_export())
        self.open_artifact_button.setEnabled(not self._busy and self._artifact_available())
        self.open_output_button.setEnabled(not self._busy and self._output_available())

    def _render_view(self, view: AnalysisViewData) -> None:
        self.input_value.setText(f"{view.analyzed_count:,}")
        self.strategy_value.setText(view.strategy_name)
        output_count = view.execution.output_count if view.execution is not None else view.estimated_output_count
        self.output_value.setText(f"{output_count:,}")
        for op, label in self.operation_labels.items():
            label.setText(f"{view.operation_counts.get(op, 0)} 个区间")
        if view.source_indices:
            self.segment_bar.set_segments(view.segments, view.source_indices[0], view.source_indices[-1])
        self._render_metric(view)
        self._render_thumbnails(view.thumbnails)
        self._set_destination_path(str(view.artifact_dir / "output_frames"))
        self.open_artifact_button.setEnabled(view.artifact_dir.is_dir())

    def _set_destination_path(self, path: str) -> None:
        self.destination_value.setText(path)
        self.destination_value.setCursorPosition(0)
        self.destination_value.setToolTip(path)

    def _switch_metric(self, metric_name: str) -> None:
        self._metric_name = metric_name
        self._render_metric()

    def _render_metric(self, view: AnalysisViewData | None = None) -> None:
        view = view or self._current_view
        if view is None:
            return
        mapping = {
            "motion": (view.motion_values, "运动强度"),
            "sharpness": (view.sharpness_values, "清晰度"),
            "contrast": (view.contrast_values, "对比度"),
        }
        values, label = mapping[self._metric_name]
        self.chart.set_series(view.source_indices, values, label)

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
        frame.setStyleSheet(
            f"QFrame#thumbnail {{ border-top: 3px solid {OPERATION_COLORS.get(thumbnail.operation, '#98a2b3')}; }}"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(4, 4, 4, 5)
        layout.setSpacing(3)
        image = ThumbnailImage()
        pixmap = QPixmap(str(thumbnail.path))
        if pixmap.isNull():
            image.setText("无法读取")
        else:
            image.set_source_pixmap(pixmap)
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

    def _open_artifact(self) -> None:
        if self._current_view is None:
            return
        analysis_dir = self._current_view.artifact_dir / "analysis"
        path = analysis_dir if analysis_dir.is_dir() else self._current_view.artifact_dir
        if path.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _show_history(self) -> None:
        if self._history_store is None:
            return
        try:
            records = self._history_store.list_records()
        except ValueError as exc:
            QMessageBox.critical(self, "运行记录无法读取", str(exc))
            return
        protected_run_ids = set()
        if self._current_record is not None and not self._history_read_only:
            protected_run_ids.add(self._current_record.run_id)
        if self._current_result_state is not None and self._current_result_state.record is not None:
            protected_run_ids.add(self._current_result_state.record.run_id)
        dialog = RunHistoryDialog(
            records,
            self,
            delete_callback=partial(delete_history_run, history_store=self._history_store),
            deleted_callback=self._history_record_deleted,
            protected_run_ids=protected_run_ids,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        record = dialog.selected_record()
        if record is not None:
            self._load_history_record(record)

    def _load_history_record(self, record: RunRecord) -> None:
        if self._history_read_only:
            self._pending_current_result_state = self._current_result_state
        elif self._current_view is not None:
            self._pending_current_result_state = CurrentResultState(
                settings=self._current_settings,
                view=self._current_view,
                record=self._current_record,
                export_completed=self._export_completed,
                status_text=self.status_label.text(),
                progress=self.progress.value(),
            )
        else:
            self._pending_current_result_state = None
        settings = RunSettings(
            frame_dir=record.frame_dir,
            artifact_dir=record.artifact_dir,
            fps=record.fps,
            limit_first_n=None,
        )
        self._begin_task("正在打开历史结果")
        self._set_progress(20)
        task = create_task(
            lambda: load_existing_run(
                settings,
                analyzed_count=record.analyzed_count,
                estimated_output_count=record.estimated_output_count,
            ),
            partial(self._history_loaded, record, settings),
            self._task_failed,
        )
        self._active_task = task
        self._thread_pool.start(task)

    def _history_loaded(self, record: RunRecord, settings: RunSettings, view: AnalysisViewData) -> None:
        self._active_task = None
        try:
            self._render_view(view)
        except Exception as exc:
            self._task_failed(f"{type(exc).__name__}: {exc}")
            return
        path_blocker = QSignalBlocker(self.path_edit)
        fps_blocker = QSignalBlocker(self.fps_spin)
        self.path_edit.setText(str(record.frame_dir))
        self.fps_spin.setValue(round(record.fps))
        del path_blocker, fps_blocker
        self._current_settings = settings
        self._current_view = view
        self._current_record = record
        self._current_result_state = self._pending_current_result_state
        self._pending_current_result_state = None
        self._history_read_only = True
        self._export_completed = view.output_dir is not None
        self.export_button.setToolTip("历史结果只读；如需重新处理，请重新开始分析")
        self.return_current_button.setVisible(self._current_result_state is not None)
        history_status = "历史结果已打开"
        if view.source_snapshot_matches is False:
            history_status += "（源帧已变化，当前显示冻结预览）"
        elif view.source_snapshot_matches is None:
            history_status += "（旧记录没有输入快照）"
        self._finish_task(history_status)
        self._sync_action_buttons()
        self._save_preferences()

    def _return_to_current_result(self) -> None:
        state = self._current_result_state
        if state is None:
            return
        self._render_view(state.view)
        path_blocker = QSignalBlocker(self.path_edit)
        fps_blocker = QSignalBlocker(self.fps_spin)
        if state.settings is not None:
            self.path_edit.setText(str(state.settings.frame_dir))
            self.fps_spin.setValue(round(state.settings.fps))
        del path_blocker, fps_blocker
        self._current_settings = state.settings
        self._current_view = state.view
        self._current_record = state.record
        self._history_read_only = False
        self._export_completed = state.export_completed
        self._current_result_state = None
        self.return_current_button.hide()
        self.export_button.setToolTip("")
        self.status_label.setText(state.status_text)
        self._set_progress(state.progress)
        self._sync_action_buttons()
        self._save_preferences()

    def _history_record_deleted(self, record: RunRecord) -> None:
        if (
            self._history_read_only
            and self._current_record is not None
            and self._current_record.run_id == record.run_id
        ):
            if self._current_result_state is not None:
                self._return_to_current_result()
            else:
                self._clear_result_display("历史结果已删除")

    def _clear_result_display(self, status: str) -> None:
        self._current_settings = None
        self._current_view = None
        self._current_record = None
        self._history_read_only = False
        self._export_completed = False
        self._current_result_state = None
        self._pending_current_result_state = None
        self.return_current_button.hide()
        self.input_value.setText("--")
        self.strategy_value.setText("reconstruction_balanced")
        self.output_value.setText("--")
        for label in self.operation_labels.values():
            label.setText("0 个区间")
        self.chart.set_series((), (), "运动强度")
        self.segment_bar.set_segments((), 0, 1)
        self._render_thumbnails(())
        self.status_label.setText(status)
        self.progress.hide()
        self.progress_percent_label.hide()
        self._sync_action_buttons()

    def _record_from_view(self, view: AnalysisViewData, status: str) -> RunRecord:
        if self._current_settings is None:
            raise RuntimeError("run settings are not available")
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        execution_count = view.execution.output_count if view.execution is not None else None
        return RunRecord(
            run_id=self._current_settings.artifact_dir.name,
            created_at=now,
            updated_at=now,
            frame_dir=self._current_settings.frame_dir,
            artifact_dir=self._current_settings.artifact_dir,
            fps=self._current_settings.fps,
            analyzed_count=view.analyzed_count,
            estimated_output_count=view.estimated_output_count,
            output_count=execution_count,
            output_dir=view.output_dir,
            status=status,
            strategy_name=view.strategy_name,
        )

    def _update_current_record(self, view: AnalysisViewData, status: str) -> None:
        if self._history_store is None:
            return
        if self._current_record is None:
            self._current_record = self._record_from_view(view, status)
        else:
            output_count = view.execution.output_count if view.execution is not None else None
            self._current_record = replace(
                self._current_record,
                updated_at=datetime.now().astimezone().isoformat(timespec="seconds"),
                analyzed_count=view.analyzed_count,
                estimated_output_count=view.estimated_output_count,
                output_count=output_count,
                output_dir=view.output_dir,
                status=status,
            )
        self._persist_current_record()

    def _persist_current_record(self) -> None:
        if self._history_store is None or self._current_record is None:
            return
        try:
            self._history_store.upsert(self._current_record)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "运行记录未保存", str(exc))

    def _restore_preferences(self) -> None:
        if self._settings is None:
            return
        frame_dir = self._settings.value("ui/frame_dir", "", str)
        fps = self._settings.value("ui/fps", 30, int)
        geometry = self._settings.value("ui/geometry")
        if frame_dir:
            self.path_edit.setText(frame_dir)
        self.fps_spin.setValue(max(1, min(240, fps)))
        if geometry is not None:
            self.restoreGeometry(geometry)

    def _save_preferences(self) -> None:
        if self._settings is None:
            return
        raw_path = self.path_edit.text().strip()
        persisted_path = str(Path(raw_path).expanduser().resolve()) if raw_path else ""
        self._settings.setValue("ui/frame_dir", persisted_path)
        self._settings.setValue("ui/fps", self.fps_spin.value())
        self._settings.setValue("ui/geometry", self.saveGeometry())
        self._settings.sync()

    def closeEvent(self, event) -> None:
        if self._busy or self.batch_workspace.is_running:
            QMessageBox.information(self, "任务进行中", "请等待当前分析或导出任务完成后再关闭窗口。")
            event.ignore()
            return
        self._save_preferences()
        super().closeEvent(event)

    def _apply_style(self) -> None:
        self.setStyleSheet(main_window_stylesheet())
