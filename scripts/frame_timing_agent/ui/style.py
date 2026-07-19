from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap


LINE_COLOR = "#2563eb"

SEGMENT_COLORS = {
    "static": QColor("#36a269"),
    "fast_motion": QColor("#f39c3d"),
    "very_fast_motion": QColor("#e5484d"),
    "low_motion_review": QColor("#8b5cf6"),
}

SEGMENT_LABELS = {
    "static": "静止",
    "fast_motion": "快速运动",
    "very_fast_motion": "极快运动",
    "low_motion_review": "低运动待复核",
}

OPERATION_COLORS = {
    "keep": "#36a269",
    "keep_uniform": "#36a269",
    "duplicate_range": "#2878f0",
    "select_sources": "#f39c3d",
    "mark_review": "#e5484d",
}

OPERATION_LABELS = {
    "keep": "原样保留",
    "keep_uniform": "静止段压缩",
    "duplicate_range": "运动段补帧",
    "select_sources": "稳定帧选择",
    "mark_review": "待人工复核",
}


def make_line_icon(kind: str, color: str, size: int = 24) -> QPixmap:
    """Render small, DPI-independent icons without platform theme assets."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 24.0, size / 24.0)

    pen = QPen(QColor(color), 1.8)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if kind == "brand":
        painter.drawRoundedRect(QRectF(4, 12, 3.5, 8), 1, 1)
        painter.drawRoundedRect(QRectF(10.25, 7, 3.5, 13), 1, 1)
        painter.drawRoundedRect(QRectF(16.5, 4, 3.5, 16), 1, 1)
    elif kind == "frames":
        painter.drawRoundedRect(QRectF(3.5, 7.5, 13, 13), 2, 2)
        painter.drawRoundedRect(QRectF(7.5, 3.5, 13, 13), 2, 2)
    elif kind == "strategy":
        painter.drawLine(QPointF(7, 5.5), QPointF(17, 12))
        painter.drawLine(QPointF(17, 12), QPointF(7, 18.5))
        for point in (QPointF(6, 5), QPointF(18, 12), QPointF(6, 19)):
            painter.drawEllipse(point, 2.4, 2.4)
    elif kind == "output":
        painter.drawLine(QPointF(12, 4), QPointF(12, 15))
        painter.drawLine(QPointF(7.5, 8.5), QPointF(12, 4))
        painter.drawLine(QPointF(16.5, 8.5), QPointF(12, 4))
        path = QPainterPath(QPointF(5, 15))
        path.lineTo(5, 19.5)
        path.quadTo(5, 21, 6.5, 21)
        path.lineTo(17.5, 21)
        path.quadTo(19, 21, 19, 19.5)
        path.lineTo(19, 15)
        painter.drawPath(path)

    painter.end()
    return pixmap


def main_window_stylesheet() -> str:
    return """
        QMainWindow, QWidget { background: #f7f9fc; color: #172033; font-family: "Microsoft YaHei UI", "Segoe UI"; font-size: 13px; }
        QLabel { background: transparent; }
        QFrame#header { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; }
        QFrame#summaryCard, QFrame#panel { background: #ffffff; border: 1px solid #e2e8f0; border-radius: 8px; }
        QFrame#thumbnail { background: #ffffff; border: 1px solid #e5eaf1; border-radius: 8px; }
        QFrame#metricSwitch { background: #ffffff; border: 1px solid #d8e1ec; border-radius: 7px; }
        QLabel#brandIcon { background: #e9f2ff; border-radius: 8px; }
        QLabel#summaryIconBlue { background: #eaf2ff; border-radius: 23px; }
        QLabel#summaryIconPurple { background: #f1edff; border-radius: 23px; }
        QLabel#summaryIconGreen { background: #e8f7f1; border-radius: 23px; }
        QLabel#title { font-size: 19px; font-weight: 700; color: #0f172a; }
        QLabel#sectionTitle { font-size: 16px; font-weight: 700; color: #0f172a; }
        QLabel#summaryCaption { color: #64748b; font-size: 12px; }
        QLabel#summaryValue { font-size: 21px; font-weight: 700; color: #0f172a; }
        QLabel#strategyName { font-size: 17px; font-weight: 650; color: #0f172a; }
        QLabel#muted, QLabel#thumbnailOp { color: #64748b; }
        QLabel#emptyState { color: #94a3b8; background: #fbfcfe; border: 1px dashed #d7e0eb; border-radius: 8px; padding: 18px; }
        QLabel#thumbnailSource { color: #1e293b; font-weight: 600; }
        QLabel#localNote { color: #1f9d68; padding-top: 5px; }
        QLabel#progressPercent { color: #2563eb; font-weight: 600; }
        QLineEdit#destinationField { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 3px 8px; min-height: 0; }
        QLineEdit, QSpinBox { background: #ffffff; border: 1px solid #d5deea; border-radius: 7px; padding: 7px 10px; min-height: 24px; selection-background-color: #bfdbfe; }
        QLineEdit:hover, QSpinBox:hover { border-color: #b8c5d6; }
        QLineEdit:focus, QSpinBox:focus { border: 1px solid #3b82f6; }
        QPushButton { background: #ffffff; color: #334155; border: 1px solid #d5deea; border-radius: 7px; padding: 7px 14px; min-height: 22px; }
        QPushButton:hover { background: #f8fafc; border-color: #93b4e8; color: #1d4ed8; }
        QPushButton:pressed { background: #eef4ff; }
        QPushButton:disabled { background: #f1f4f8; color: #9aa7b8; border-color: #e3e8ef; }
        QPushButton#primaryButton { background: #2563eb; color: #ffffff; border-color: #2563eb; font-weight: 600; }
        QPushButton#primaryButton:hover { background: #1d4ed8; border-color: #1d4ed8; color: #ffffff; }
        QPushButton#primaryButton:pressed { background: #1e40af; }
        QPushButton#primaryButton:disabled { background: #b5c5dc; border-color: #b5c5dc; color: #f5f7fb; }
        QPushButton#destructiveButton { color: #b42318; border-color: #efc9c5; }
        QPushButton#destructiveButton:hover { color: #991b1b; background: #fff7f6; border-color: #e5a9a3; }
        QPushButton#segmentButton { background: transparent; color: #475569; border: none; border-right: 1px solid #d8e1ec; border-radius: 0; padding: 4px 18px; min-height: 24px; }
        QPushButton#segmentButton[last="true"] { border-right: none; }
        QPushButton#segmentButton:hover { background: #f8fafc; color: #1d4ed8; }
        QPushButton#segmentButton:checked { background: #eaf2ff; color: #1d4ed8; }
        QProgressBar { background: #e8edf4; border: none; border-radius: 3px; max-height: 6px; }
        QProgressBar::chunk { background: #2563eb; border-radius: 3px; }
        QToolTip { background: #ffffff; color: #334155; border: 1px solid #d8e0ea; padding: 5px; }
    """
