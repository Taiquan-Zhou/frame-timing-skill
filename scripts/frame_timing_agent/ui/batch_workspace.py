from __future__ import annotations

import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QSettings, QThreadPool, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from frame_timing_agent.batch_discovery import discover_frame_directories
from frame_timing_agent.batch_session import (
    BatchExportSummary,
    BatchItemState,
    BatchItemStatus,
    BatchState,
    BatchStatus,
    approve_item,
    create_batch,
    export_batch,
    item_has_export_artifacts,
    load_batch,
    recover_batch,
    run_batch,
)
from frame_timing_agent.ui.style import OPERATION_COLORS, OPERATION_LABELS
from frame_timing_agent.ui.widgets import LineChart, SegmentBar, ThumbnailImage
from frame_timing_agent.ui.worker import create_task, load_existing_run


_STATUS_LABELS = {
    BatchItemStatus.PENDING: "等待",
    BatchItemStatus.RUNNING: "分析中",
    BatchItemStatus.COMPLETED: "完成",
    BatchItemStatus.REVIEW_REQUIRED: "待复核",
    BatchItemStatus.FAILED: "失败",
}


class BatchWorkspace(QWidget):
    """Compact persisted batch list/detail workspace."""

    running_changed = Signal(bool)

    def __init__(
        self,
        thread_pool: QThreadPool,
        settings: QSettings | None = None,
        fps_provider: Callable[[], float] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.thread_pool = thread_pool
        self.settings = settings
        self._fps_provider = fps_provider or (lambda: 30.0)
        self.current_state: BatchState | None = None
        self.state_path: Path | None = None
        self._active_task = None
        self._operation = "idle"
        self._pause_event = threading.Event()
        self._explicit_directories: list[Path] = []
        self._discovery_root: Path | None = None
        self._build_ui()
        self._restore_last_batch()

    @property
    def is_running(self) -> bool:
        return self._operation != "idle"

    @property
    def operation(self) -> str:
        return self._operation

    @property
    def pause_requested(self) -> bool:
        return self._pause_event.is_set()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        actions = QFrame()
        actions.setObjectName("batchToolbar")
        action_layout = QHBoxLayout(actions)
        action_layout.setContentsMargins(12, 8, 12, 8)
        action_layout.setSpacing(8)
        self.add_button = QPushButton("添加目录")
        self.discover_button = QPushButton("发现根目录")
        self.start_button = QPushButton("开始")
        self.start_button.setObjectName("primaryButton")
        self.pause_button = QPushButton("当前项完成后暂停")
        self.continue_button = QPushButton("继续")
        self.retry_button = QPushButton("重试选中失败项")
        self.export_button = QPushButton("导出可用结果")
        self.open_batch_button = QPushButton("打开批次产物")
        for button in (
            self.add_button,
            self.discover_button,
            self.start_button,
            self.pause_button,
            self.continue_button,
            self.retry_button,
            self.export_button,
            self.open_batch_button,
        ):
            action_layout.addWidget(button)
        action_layout.addStretch()
        self.summary_label = QLabel("尚未创建批次")
        self.summary_label.setObjectName("muted")
        action_layout.addWidget(self.summary_label)
        root.addWidget(actions)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("batchSplitter")
        splitter.setChildrenCollapsible(False)
        list_panel = QFrame()
        list_panel.setObjectName("panel")
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(12, 12, 12, 12)
        list_title = QLabel("批次项目")
        list_title.setObjectName("sectionTitle")
        list_layout.addWidget(list_title)
        self.item_list = QListWidget()
        self.item_list.setObjectName("batchList")
        self.item_list.setMinimumWidth(280)
        self.item_list.currentItemChanged.connect(self._selection_changed)
        list_layout.addWidget(self.item_list, 1)
        splitter.addWidget(list_panel)

        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail = QFrame()
        detail.setObjectName("panel")
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(16, 14, 16, 14)
        detail_layout.setSpacing(10)
        self.detail_name = QLabel("选择一个批次项目")
        self.detail_name.setObjectName("sectionTitle")
        self.detail_status = QLabel("")
        self.detail_status.setObjectName("muted")
        self.strategy_label = QLabel("策略：--")
        self.strategy_label.setObjectName("muted")
        self.warning_label = QLabel("")
        self.warning_label.setObjectName("batchWarning")
        self.warning_label.setWordWrap(True)
        detail_layout.addWidget(self.detail_name)
        detail_layout.addWidget(self.detail_status)
        detail_layout.addWidget(self.strategy_label)
        detail_layout.addWidget(self.warning_label)

        self.chart = LineChart()
        self.chart.setMinimumHeight(210)
        detail_layout.addWidget(self.chart)
        self.segment_bar = SegmentBar()
        detail_layout.addWidget(self.segment_bar)

        self.thumbnail_container = QWidget()
        self.thumbnail_layout = QHBoxLayout(self.thumbnail_container)
        self.thumbnail_layout.setContentsMargins(0, 0, 0, 0)
        self.thumbnail_layout.setSpacing(8)
        detail_layout.addWidget(self.thumbnail_container)

        note_row = QHBoxLayout()
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("复核备注（可选）")
        self.approve_button = QPushButton("批准选中项")
        note_row.addWidget(self.note_edit, 1)
        note_row.addWidget(self.approve_button)
        detail_layout.addLayout(note_row)
        self.artifact_path = QLineEdit()
        self.artifact_path.setReadOnly(True)
        self.artifact_path.setObjectName("destinationField")
        self.artifact_path.setPlaceholderText("分析产物路径")
        self.output_path = QLineEdit()
        self.output_path.setReadOnly(True)
        self.output_path.setObjectName("destinationField")
        self.output_path.setPlaceholderText("输出帧路径")
        detail_layout.addWidget(self.artifact_path)
        detail_layout.addWidget(self.output_path)
        detail_layout.addStretch()
        detail_scroll.setWidget(detail)
        splitter.addWidget(detail_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([310, 900])
        root.addWidget(splitter, 1)

        self.overall_progress = QProgressBar()
        self.overall_progress.setRange(0, 100)
        self.overall_progress.setTextVisible(False)
        self.status_label = QLabel("等待添加目录")
        self.status_label.setObjectName("muted")
        root.addWidget(self.overall_progress)
        root.addWidget(self.status_label)

        self.add_button.clicked.connect(self.add_directory)
        self.discover_button.clicked.connect(self.discover_root)
        self.start_button.clicked.connect(self.start_new_batch)
        self.pause_button.clicked.connect(self.request_pause)
        self.continue_button.clicked.connect(self.continue_batch)
        self.retry_button.clicked.connect(self.retry_selected)
        self.approve_button.clicked.connect(self.approve_selected)
        self.export_button.clicked.connect(self.export_eligible)
        self.open_batch_button.clicked.connect(self.open_batch_artifacts)
        self._sync_actions()

    def add_directory(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "添加帧目录", str(Path.home()))
        if not directory:
            return
        if self.current_state is not None:
            self._reset_for_new_batch()
        path = Path(directory).expanduser().resolve()
        if path not in self._explicit_directories:
            self._explicit_directories.append(path)
        self.status_label.setText(f"已添加 {len(self._explicit_directories)} 个目录")
        self._sync_actions()

    def discover_root(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择发现根目录", str(Path.home()))
        if not directory:
            return
        if self.current_state is not None:
            self._reset_for_new_batch()
        self._discovery_root = Path(directory).expanduser().resolve()
        discovery = discover_frame_directories(root=self._discovery_root)
        self.status_label.setText(f"发现 {len(discovery.frame_dirs)} 个帧目录，忽略/无效 {len(discovery.issues)} 项")
        self.start_button.setEnabled(bool(discovery.frame_dirs))

    def start_new_batch(self) -> None:
        if self.is_running:
            return
        discovery = discover_frame_directories(
            explicit=self._explicit_directories,
            root=self._discovery_root,
        )
        if not discovery.frame_dirs:
            QMessageBox.warning(self, "没有可处理目录", "请添加有效帧目录或选择发现根目录。")
            return
        first = discovery.frame_dirs[0]
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        artifact_root = first.parent / "output" / "frame_timing_batch" / f"{timestamp}-{uuid.uuid4().hex[:8]}"
        try:
            self.set_state(
                create_batch(
                    discovery,
                    artifact_root=artifact_root,
                    fps=self._fps_provider(),
                )
            )
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "无法创建批次", str(error))
            return
        self._start_run()

    def set_state(self, state: BatchState, *, update_progress: bool = True) -> None:
        self.current_state = state
        self.state_path = state.state_path
        self._persist_state_path()
        selected_name = self.selected_item_name()
        self.item_list.clear()
        for item in sorted(state.items, key=lambda candidate: candidate.safe_name.casefold()):
            row = QListWidgetItem()
            row.setData(Qt.ItemDataRole.UserRole, item.safe_name)
            exported = " · 已导出" if item_has_export_artifacts(item) else ""
            warning = f" · {len(item.warnings)} 警告" if item.warnings else ""
            row.setText(
                f"{item.safe_name}\n{_STATUS_LABELS[item.status]} · {round(item.progress * 100)}%{warning}{exported}"
            )
            self.item_list.addItem(row)
        if self.item_list.count():
            self.select_item(selected_name or self.item_list.item(0).data(Qt.ItemDataRole.UserRole))
        completed = sum(
            item.status in {BatchItemStatus.COMPLETED, BatchItemStatus.REVIEW_REQUIRED, BatchItemStatus.FAILED}
            for item in state.items
        )
        self.summary_label.setText(f"{completed}/{len(state.items)} 已处理 · {state.status.value}")
        if update_progress:
            self.overall_progress.setValue(round(completed * 100 / len(state.items)))
        self._sync_actions()

    def _reset_for_new_batch(self) -> None:
        self.current_state = None
        self.state_path = None
        self._explicit_directories.clear()
        self._discovery_root = None
        self.item_list.clear()
        self.summary_label.setText("尚未创建批次")
        self.overall_progress.setValue(0)
        self.status_label.setText("等待添加目录")
        self._render_selected_item()
        if self.settings is not None:
            self.settings.remove("last_batch_state_path")
            self.settings.sync()

    def select_item(self, safe_name: str) -> None:
        for index in range(self.item_list.count()):
            row = self.item_list.item(index)
            if row.data(Qt.ItemDataRole.UserRole) == safe_name:
                self.item_list.setCurrentRow(index)
                return

    def selected_item_name(self) -> str | None:
        row = self.item_list.currentItem()
        if row is None:
            return None
        value = row.data(Qt.ItemDataRole.UserRole)
        return str(value) if value is not None else None

    def selected_item(self) -> BatchItemState | None:
        if self.current_state is None:
            return None
        name = self.selected_item_name()
        return next((item for item in self.current_state.items if item.safe_name == name), None)

    def _selection_changed(self, _current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        self._render_selected_item()
        self._sync_actions()

    def _render_selected_item(self) -> None:
        item = self.selected_item()
        if item is None or self.current_state is None:
            self.detail_name.setText("选择一个批次项目")
            self.detail_status.clear()
            self.strategy_label.setText("策略：--")
            self.warning_label.clear()
            self.note_edit.clear()
            self.artifact_path.clear()
            self.output_path.clear()
            self.chart.set_series((), (), "运动强度")
            self.segment_bar.set_segments((), 0, 1)
            self._render_thumbnails(())
            return
        self.detail_name.setText(item.safe_name)
        self.detail_status.setText(
            f"{_STATUS_LABELS[item.status]} · {round(item.progress * 100)}%" + (" · 已批准" if item.approved else "")
        )
        warning_names = {
            "quality.bad_candidate_ratio": "低质量候选帧比例达到复核阈值",
            "quality.low_motion_review": "存在低运动待复核区间",
        }
        notices = [warning_names.get(code, code) for code in item.warnings]
        if item.last_error:
            notices.append(f"失败原因：{item.last_error}")
        self.warning_label.setText("\n".join(notices))
        self.note_edit.setText(item.note or "")
        self.strategy_label.setText("策略：reconstruction_balanced" if item.analyzed_count else "策略：--")
        artifact_path = str(self.current_state.artifact_root / item.safe_name)
        output_path = str(item.output_path) if item.output_path is not None else "尚未导出"
        self.artifact_path.setText(artifact_path)
        self.artifact_path.setCursorPosition(0)
        self.artifact_path.setToolTip(artifact_path)
        self.output_path.setText(output_path)
        self.output_path.setCursorPosition(0)
        self.output_path.setToolTip(output_path)
        analysis_dir = self.current_state.artifact_root / item.safe_name / "analysis"
        if item.analyzed_count and (analysis_dir / "strategy.json").is_file():
            try:
                view = load_existing_run(
                    self._item_run_settings(item),
                    analyzed_count=item.analyzed_count,
                    estimated_output_count=item.output_count or 0,
                )
            except (OSError, ValueError):
                self.chart.set_series((), (), "运动强度")
                self.segment_bar.set_segments((), 0, 1)
                self._render_thumbnails(())
            else:
                self.chart.set_series(view.source_indices, view.motion_values, "运动强度")
                if view.source_indices:
                    self.segment_bar.set_segments(view.segments, view.source_indices[0], view.source_indices[-1])
                self._render_thumbnails(view.thumbnails)
        else:
            self.chart.set_series((), (), "运动强度")
            self.segment_bar.set_segments((), 0, 1)
            self._render_thumbnails(())

    def _item_run_settings(self, item: BatchItemState):
        from frame_timing_agent.run_workflow import RunSettings

        if self.current_state is None:
            raise RuntimeError("batch state is unavailable")
        return RunSettings(
            frame_dir=item.frame_dir,
            artifact_dir=self.current_state.artifact_root / item.safe_name,
            fps=self.current_state.fps,
            limit_first_n=self.current_state.limit_first_n,
        )

    def _render_thumbnails(self, thumbnails) -> None:
        while self.thumbnail_layout.count():
            layout_item = self.thumbnail_layout.takeAt(0)
            widget = layout_item.widget()
            if widget is not None:
                widget.deleteLater()
        if not thumbnails:
            empty = QLabel("分析完成后显示代表帧")
            empty.setObjectName("emptyState")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.thumbnail_layout.addWidget(empty, 1)
            return
        for thumbnail in thumbnails:
            card = QFrame()
            card.setObjectName("thumbnail")
            card.setStyleSheet(
                f"QFrame#thumbnail {{ border-top: 3px solid {OPERATION_COLORS.get(thumbnail.operation, '#98a2b3')}; }}"
            )
            layout = QVBoxLayout(card)
            layout.setContentsMargins(4, 4, 4, 5)
            image = ThumbnailImage()
            pixmap = QPixmap(str(thumbnail.path))
            if pixmap.isNull():
                image.setText("无法读取")
            else:
                image.set_source_pixmap(pixmap)
            label = QLabel(
                f"src {thumbnail.source_index}\n{OPERATION_LABELS.get(thumbnail.operation, thumbnail.operation)}"
            )
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(image, 1)
            layout.addWidget(label)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            self.thumbnail_layout.addWidget(card, 1)

    def request_pause(self) -> None:
        if self._operation != "analysis":
            return
        self._pause_event.set()
        self.status_label.setText("将在当前项目完成后暂停")
        self._sync_actions()

    def continue_batch(self) -> None:
        if self.current_state is None or self.current_state.status not in {BatchStatus.READY, BatchStatus.PAUSED}:
            return
        self._start_run()

    def retry_selected(self) -> None:
        item = self.selected_item()
        if item is None or item.status is not BatchItemStatus.FAILED:
            return
        self._start_run((item.safe_name,))

    def _start_run(self, retry_items: tuple[str, ...] = ()) -> None:
        if self.state_path is None or self.is_running:
            return
        self._pause_event.clear()
        self._set_operation("analysis")
        self.status_label.setText("正在处理批次")
        self._sync_actions()
        task = create_task(
            lambda progress: run_batch(
                self.state_path,
                progress_callback=progress,
                should_pause=self._pause_event.is_set,
                retry_items=retry_items,
            ),
            self._run_succeeded,
            self._run_failed,
            self._run_progress,
        )
        self._active_task = task
        self.thread_pool.start(task)

    def _run_progress(self, percent: int, message: str) -> None:
        self.overall_progress.setValue(max(0, min(100, int(percent))))
        self.status_label.setText(message)
        if self.state_path is None:
            return
        try:
            persisted = load_batch(self.state_path)
        except (OSError, ValueError):
            return
        running = next((item for item in persisted.items if item.status is BatchItemStatus.RUNNING), None)
        if running is not None:
            terminal_count = sum(
                item.status not in {BatchItemStatus.PENDING, BatchItemStatus.RUNNING} for item in persisted.items
            )
            item_progress = max(
                0,
                min(100, round((percent * len(persisted.items) / 100 - terminal_count) * 100)),
            )
            running.progress = item_progress / 100
            self.current_state = persisted
            self._update_row(running)
            self.summary_label.setText(f"{terminal_count}/{len(persisted.items)} 已处理 · 当前：{running.safe_name}")
            if self.selected_item_name() == running.safe_name:
                self.detail_status.setText(f"分析中 · {item_progress}%")
        elif message in {item.safe_name for item in persisted.items}:
            self.set_state(persisted, update_progress=False)

    def _run_succeeded(self, state: BatchState) -> None:
        self._active_task = None
        self._set_operation("idle")
        self.set_state(state)
        if state.status is BatchStatus.PAUSED:
            self.status_label.setText("批次已暂停")
            return
        failed_count = sum(item.status is BatchItemStatus.FAILED for item in state.items)
        self.status_label.setText(f"批次分析完成，{failed_count} 项失败" if failed_count else "批次分析完成")

    def _run_failed(self, message: str) -> None:
        self._active_task = None
        self._set_operation("idle")
        self.status_label.setText(f"批次处理失败：{message}")
        if self.state_path is not None:
            try:
                self.set_state(recover_batch(self.state_path))
            except (OSError, ValueError):
                pass
        self._sync_actions()
        QMessageBox.critical(self, "批次处理失败", message)

    def approve_selected(self) -> None:
        item = self.selected_item()
        if self.state_path is None or item is None or self.is_running:
            return
        item_name = item.safe_name
        note = self.note_edit.text()
        state_path = self.state_path
        self._set_operation("approval")
        self.status_label.setText(f"正在校验并批准 {item_name}")
        self._sync_actions()
        task = create_task(
            lambda: approve_item(state_path, item_name, note),
            lambda state: self._approval_succeeded(state, item_name),
            self._run_failed,
        )
        self._active_task = task
        self.thread_pool.start(task)

    def _approval_succeeded(self, state: BatchState, item_name: str) -> None:
        self._active_task = None
        self._set_operation("idle")
        self.set_state(state)
        self.select_item(item_name)
        self.status_label.setText(f"已批准 {item_name}")

    def export_eligible(self) -> None:
        if self.state_path is None or self.is_running or not self.export_button.isEnabled():
            return
        if (
            QMessageBox.question(
                self,
                "确认导出",
                "只导出已完成或已明确批准的项目。未解决复核项和失败项将跳过。",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        self._set_operation("export")
        self.status_label.setText("正在导出可用结果")
        self._sync_actions()
        task = create_task(
            lambda progress: export_batch(self.state_path, progress_callback=progress),
            self._export_succeeded,
            self._run_failed,
            self._run_progress,
        )
        self._active_task = task
        self.thread_pool.start(task)

    def _export_succeeded(self, summary: BatchExportSummary) -> None:
        self._active_task = None
        self._set_operation("idle")
        if self.state_path is not None:
            self.set_state(recover_batch(self.state_path))
        self.status_label.setText(
            f"导出完成：{len(summary.exported)} 成功，{len(summary.skipped)} 跳过，{len(summary.failed)} 失败"
        )

    def open_batch_artifacts(self) -> None:
        if self.current_state is not None and self.current_state.artifact_root.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.current_state.artifact_root)))

    def _restore_last_batch(self) -> None:
        if self.settings is None:
            return
        raw_path = self.settings.value("last_batch_state_path", "", str)
        if not raw_path:
            return
        path = Path(raw_path).expanduser().resolve()
        if not path.is_file():
            return
        try:
            state = recover_batch(path)
        except (OSError, ValueError) as error:
            self.status_label.setText(f"上次批次无法恢复：{error}")
            return
        self.set_state(state)
        if state.status is BatchStatus.FINISHED:
            self.status_label.setText("已恢复上次分析完成批次")
        else:
            self.status_label.setText("已恢复批次，等待用户继续")

    def _persist_state_path(self) -> None:
        if self.settings is None or self.state_path is None:
            return
        self.settings.setValue("last_batch_state_path", str(self.state_path))
        self.settings.sync()

    def _sync_actions(self) -> None:
        state = self.current_state
        item = self.selected_item()
        has_inputs = bool(self._explicit_directories or self._discovery_root)
        can_create = state is None or state.status is BatchStatus.FINISHED
        self.add_button.setText("新建批次" if state is not None else "添加目录")
        self.add_button.setEnabled(not self.is_running and can_create)
        self.discover_button.setEnabled(not self.is_running and can_create)
        self.start_button.setEnabled(not self.is_running and state is None and has_inputs)
        self.pause_button.setEnabled(self._operation == "analysis" and not self._pause_event.is_set())
        self.continue_button.setEnabled(
            not self.is_running
            and state is not None
            and state.status in {BatchStatus.READY, BatchStatus.PAUSED}
            and any(candidate.status is BatchItemStatus.PENDING for candidate in state.items)
        )
        self.retry_button.setEnabled(not self.is_running and item is not None and item.status is BatchItemStatus.FAILED)
        self.approve_button.setEnabled(
            not self.is_running
            and item is not None
            and item.status is BatchItemStatus.REVIEW_REQUIRED
            and not item.approved
        )
        self.export_button.setEnabled(
            not self.is_running
            and state is not None
            and state.status is BatchStatus.FINISHED
            and any(
                (
                    candidate.status is BatchItemStatus.COMPLETED
                    or (candidate.status is BatchItemStatus.REVIEW_REQUIRED and candidate.approved)
                )
                for candidate in state.items
            )
        )
        self.open_batch_button.setEnabled(not self.is_running and state is not None and state.artifact_root.is_dir())
        self.note_edit.setEnabled(
            not self.is_running
            and item is not None
            and item.status is BatchItemStatus.REVIEW_REQUIRED
            and not item.approved
        )

    def _set_operation(self, operation: str) -> None:
        if operation not in {"idle", "analysis", "export", "approval"}:
            raise ValueError(f"unknown batch operation: {operation}")
        was_running = self.is_running
        self._operation = operation
        if was_running != self.is_running:
            self.running_changed.emit(self.is_running)

    def _update_row(self, item: BatchItemState) -> None:
        for index in range(self.item_list.count()):
            row = self.item_list.item(index)
            if row.data(Qt.ItemDataRole.UserRole) != item.safe_name:
                continue
            exported = " · 已导出" if item_has_export_artifacts(item) else ""
            warning = f" · {len(item.warnings)} 警告" if item.warnings else ""
            row.setText(
                f"{item.safe_name}\n{_STATUS_LABELS[item.status]} · {round(item.progress * 100)}%{warning}{exported}"
            )
            return
