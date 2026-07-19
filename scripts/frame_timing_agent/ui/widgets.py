from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QLabel, QSizePolicy, QWidget

from frame_timing_agent.ui.style import LINE_COLOR, SEGMENT_COLORS, SEGMENT_LABELS
from frame_timing_agent.ui.view_model import SegmentView


class LineChart(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._sources: tuple[int, ...] = ()
        self._values: tuple[float, ...] = ()
        self._label = "运动强度"
        self._hover_index: int | None = None
        self._base_pixmap: QPixmap | None = None
        self._minimum = 0.0
        self._maximum = 0.0
        self._span = 1.0
        self.setMouseTracking(True)
        self.setMinimumHeight(210)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_series(self, sources: tuple[int, ...], values: tuple[float, ...], label: str) -> None:
        if len(sources) != len(values):
            raise ValueError("chart sources and values must have the same length")
        self._sources = sources
        self._values = values
        self._label = label
        self._hover_index = None
        self._minimum = min(values) if values else 0.0
        self._maximum = max(values) if values else 0.0
        self._span = self._maximum - self._minimum or 1.0
        self._base_pixmap = None
        self.update()

    def _plot_rect(self) -> QRectF:
        return QRectF(self.rect().adjusted(52, 22, -18, -34))

    @staticmethod
    def _x_axis_title_rect(plot: QRectF) -> QRectF:
        return QRectF(plot.left() + 50, plot.bottom() + 5, max(0.0, plot.width() - 100), 20)

    def _point_x(self, index: int, plot: QRectF) -> float:
        if len(self._sources) <= 1:
            return plot.left()
        return plot.left() + plot.width() * index / (len(self._sources) - 1)

    def _data_point(self, index: int, plot: QRectF) -> QPointF:
        x = self._point_x(index, plot)
        y = plot.bottom() - plot.height() * (self._values[index] - self._minimum) / self._span
        return QPointF(x, y)

    def hovered_data(self) -> tuple[int, float] | None:
        if self._hover_index is None:
            return None
        return self._sources[self._hover_index], self._values[self._hover_index]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        device_ratio = self.devicePixelRatioF()
        if (
            self._base_pixmap is None
            or self._base_pixmap.deviceIndependentSize().toSize() != self.size()
            or self._base_pixmap.devicePixelRatio() != device_ratio
        ):
            self._base_pixmap = self._render_base_pixmap()
        painter.drawPixmap(0, 0, self._base_pixmap)
        if self._hover_index is not None:
            self._draw_hover_indicator(painter, self._plot_rect())

    def _render_base_pixmap(self) -> QPixmap:
        device_ratio = self.devicePixelRatioF()
        pixmap = QPixmap(max(1, round(self.width() * device_ratio)), max(1, round(self.height() * device_ratio)))
        pixmap.setDevicePixelRatio(device_ratio)
        pixmap.fill(QColor("#ffffff"))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot = self._plot_rect()
        painter.setPen(QPen(QColor("#e6ebf2"), 1))
        for index in range(5):
            y = plot.top() + plot.height() * index / 4
            painter.drawLine(plot.left(), int(y), plot.right(), int(y))

        painter.setPen(QColor("#64748b"))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.drawText(8, 18, self._label)
        if not self._values or not self._sources:
            painter.drawText(plot, Qt.AlignmentFlag.AlignCenter, "选择帧目录并开始分析")
            painter.end()
            return pixmap

        path = QPainterPath()
        for index in range(len(self._values)):
            point = self._data_point(index, plot)
            path.moveTo(point) if index == 0 else path.lineTo(point)
        painter.setPen(QPen(QColor(LINE_COLOR), 2))
        painter.drawPath(path)

        painter.setPen(QColor("#64748b"))
        painter.drawText(4, plot.top() + 5, f"{self._maximum:.2f}")
        painter.drawText(4, plot.bottom(), f"{self._minimum:.2f}")
        painter.drawText(plot.left(), self.height() - 8, str(self._sources[0]))
        painter.drawText(plot.right() - 42, self.height() - 8, str(self._sources[-1]))
        painter.drawText(self._x_axis_title_rect(plot), Qt.AlignmentFlag.AlignCenter, "源帧索引")
        painter.end()
        return pixmap

    def _draw_hover_indicator(self, painter: QPainter, plot: QRectF) -> None:
        index = self._hover_index
        if index is None:
            return
        point = self._data_point(index, plot)
        x = point.x()
        painter.setPen(QPen(QColor("#94a3b8"), 1, Qt.PenStyle.DashLine))
        painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(LINE_COLOR), 2))
        painter.drawEllipse(point, 4, 4)

        lines = (f"Frame: {self._sources[index]}", f"{self._label}: {self._values[index]:.3f}")
        metrics = painter.fontMetrics()
        tooltip_width = max(metrics.horizontalAdvance(line) for line in lines) + 18
        tooltip_height = metrics.height() * len(lines) + 12
        tooltip_x = x + 8
        if tooltip_x + tooltip_width > plot.right():
            tooltip_x = x - tooltip_width - 8
        tooltip_x = max(plot.left(), min(tooltip_x, plot.right() - tooltip_width))
        tooltip_y = max(
            plot.top() + 4,
            min(point.y() - tooltip_height - 8, plot.bottom() - tooltip_height - 4),
        )
        tooltip = QRectF(tooltip_x, tooltip_y, tooltip_width, tooltip_height)
        painter.setPen(QPen(QColor("#d8e0ea"), 1))
        painter.setBrush(QColor("#ffffff"))
        painter.drawRoundedRect(tooltip, 4, 4)
        painter.setPen(QColor("#334155"))
        text_y = tooltip.top() + 6 + metrics.ascent()
        for line in lines:
            painter.drawText(QPointF(tooltip.left() + 9, text_y), line)
            text_y += metrics.height()

    def mouseMoveEvent(self, event) -> None:
        plot = self._plot_rect()
        position = event.position()
        hover_index: int | None = None
        if self._sources and self._values and plot.contains(position):
            if len(self._sources) == 1:
                hover_index = 0
            else:
                ratio = (position.x() - plot.left()) / max(1.0, plot.width())
                hover_index = max(0, min(len(self._sources) - 1, round(ratio * (len(self._sources) - 1))))
        if hover_index != self._hover_index:
            self._hover_index = hover_index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:
        if self._hover_index is not None:
            self._hover_index = None
            self.update()
        super().leaveEvent(event)

    def resizeEvent(self, event) -> None:
        self._base_pixmap = None
        super().resizeEvent(event)


class ThumbnailImage(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._source_pixmap = QPixmap()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(78)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)

    def set_source_pixmap(self, pixmap: QPixmap) -> None:
        self._source_pixmap = pixmap
        self._update_scaled_pixmap()

    def sizeHint(self) -> QSize:
        return QSize(120, 88)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 78)

    def resizeEvent(self, event) -> None:
        self._update_scaled_pixmap()
        super().resizeEvent(event)

    def _update_scaled_pixmap(self) -> None:
        if self._source_pixmap.isNull() or self.contentsRect().isEmpty():
            return
        super().setPixmap(
            self._source_pixmap.scaled(
                self.contentsRect().size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )


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

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bar = QRectF(0, 4, self.width(), 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#eef2f7"))
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

        painter.setFont(QFont("Microsoft YaHei UI", 8))
        x = 0
        for segment_type in ("static", "fast_motion", "very_fast_motion", "low_motion_review"):
            painter.setBrush(SEGMENT_COLORS[segment_type])
            painter.drawRoundedRect(QRectF(x, 31, 10, 10), 2, 2)
            painter.setPen(QColor("#64748b"))
            label = SEGMENT_LABELS[segment_type]
            painter.drawText(x + 15, 41, label)
            x += painter.fontMetrics().horizontalAdvance(label) + 38
            painter.setPen(Qt.PenStyle.NoPen)
