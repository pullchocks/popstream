from __future__ import annotations

from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPen, QPixmap

from popstream.ui.theme import C


def icon_pixmap(kind: str, size: int = 72, color: str | None = None) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    accent = QColor(color or C.accent)
    muted = QColor(C.text)
    painter.setPen(QPen(accent, max(2, size // 24)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    m = size * 0.22
    box = QRectF(m, m, size - 2 * m, size - 2 * m)
    k = (kind or "grid").lower()

    if k in ("globe", "url"):
        painter.drawEllipse(box)
        painter.drawLine(box.center().x(), box.top(), box.center().x(), box.bottom())
        painter.drawArc(box.adjusted(box.width() * 0.22, 0, -box.width() * 0.22, 0), 0, 360 * 16)
    elif k == "app":
        painter.drawRoundedRect(box, 6, 6)
        gap = box.width() / 3
        painter.drawLine(box.left() + gap, box.top(), box.left() + gap, box.bottom())
        painter.drawLine(box.left() + 2 * gap, box.top(), box.left() + 2 * gap, box.bottom())
        painter.drawLine(box.left(), box.top() + gap, box.right(), box.top() + gap)
    elif k in ("terminal", "command"):
        painter.drawRoundedRect(box, 4, 4)
        painter.drawLine(
            box.left() + box.width() * 0.18,
            box.top() + box.height() * 0.38,
            box.left() + box.width() * 0.38,
            box.center().y(),
        )
        painter.drawLine(
            box.left() + box.width() * 0.18,
            box.top() + box.height() * 0.62,
            box.left() + box.width() * 0.38,
            box.center().y(),
        )
        painter.drawLine(
            box.left() + box.width() * 0.48,
            box.top() + box.height() * 0.7,
            box.right() - box.width() * 0.18,
            box.top() + box.height() * 0.7,
        )
    elif k in ("keyboard", "hotkey"):
        painter.drawRoundedRect(box, 6, 6)
        ks = box.width() / 6
        for r in range(2):
            for c in range(3):
                painter.drawRoundedRect(
                    QRectF(
                        box.left() + box.width() * 0.15 + c * ks * 1.3,
                        box.top() + box.height() * 0.22 + r * ks * 1.4,
                        ks,
                        ks,
                    ),
                    2,
                    2,
                )
    elif k in ("type", "text"):
        font = QFont("Sans Serif", int(size * 0.38), QFont.Weight.Bold)
        painter.setFont(font)
        painter.setPen(accent)
        painter.drawText(QRect(0, 0, size, size), int(Qt.AlignmentFlag.AlignCenter), "T")
    elif k == "folder":
        painter.drawRoundedRect(box.adjusted(0, box.height() * 0.18, 0, 0), 4, 4)
        tab = QRectF(box.left(), box.top() + 2, box.width() * 0.42, box.height() * 0.28)
        painter.drawRoundedRect(tab, 3, 3)
    elif k == "back":
        painter.drawLine(box.center().x() + box.width() * 0.2, box.top() + box.height() * 0.2, box.left() + box.width() * 0.15, box.center().y())
        painter.drawLine(box.center().x() + box.width() * 0.2, box.bottom() - box.height() * 0.2, box.left() + box.width() * 0.15, box.center().y())
        painter.drawLine(box.left() + box.width() * 0.15, box.center().y(), box.right() - box.width() * 0.1, box.center().y())
    elif k == "next":
        painter.drawLine(box.left() + box.width() * 0.15, box.center().y(), box.right() - box.width() * 0.15, box.center().y())
        painter.drawLine(box.right() - box.width() * 0.35, box.top() + box.height() * 0.22, box.right() - box.width() * 0.12, box.center().y())
        painter.drawLine(box.right() - box.width() * 0.35, box.bottom() - box.height() * 0.22, box.right() - box.width() * 0.12, box.center().y())
    elif k == "prev":
        painter.drawLine(box.right() - box.width() * 0.15, box.center().y(), box.left() + box.width() * 0.15, box.center().y())
        painter.drawLine(box.left() + box.width() * 0.35, box.top() + box.height() * 0.22, box.left() + box.width() * 0.12, box.center().y())
        painter.drawLine(box.left() + box.width() * 0.35, box.bottom() - box.height() * 0.22, box.left() + box.width() * 0.12, box.center().y())
    elif k == "clock":
        painter.drawEllipse(box)
        c = box.center()
        painter.drawLine(c.x(), c.y(), c.x(), box.top() + box.height() * 0.22)
        painter.drawLine(c.x(), c.y(), box.right() - box.width() * 0.22, c.y() + box.height() * 0.08)
    elif k in ("date", "calendar"):
        painter.drawRoundedRect(box, 4, 4)
        painter.drawLine(box.left(), box.top() + box.height() * 0.28, box.right(), box.top() + box.height() * 0.28)
        painter.setPen(muted)
        font = QFont("Sans Serif", int(size * 0.22), QFont.Weight.Bold)
        painter.setFont(font)
        painter.drawText(QRectF(box.left(), box.top() + box.height() * 0.32, box.width(), box.height() * 0.6), int(Qt.AlignmentFlag.AlignCenter), "31")
    elif k in ("sun", "brightness"):
        painter.drawEllipse(box.adjusted(box.width() * 0.22, box.height() * 0.22, -box.width() * 0.22, -box.height() * 0.22))
        c = box.center()
        for i in range(8):
            painter.save()
            painter.translate(c)
            painter.rotate(i * 45)
            painter.drawLine(0, -box.height() * 0.18, 0, -box.height() * 0.42)
            painter.restore()
    elif k in ("speaker", "volume"):
        path = QPainterPath()
        path.moveTo(box.left() + box.width() * 0.42, box.top() + box.height() * 0.28)
        path.lineTo(box.left() + box.width() * 0.22, box.top() + box.height() * 0.42)
        path.lineTo(box.left() + box.width() * 0.22, box.top() + box.height() * 0.58)
        path.lineTo(box.left() + box.width() * 0.42, box.top() + box.height() * 0.72)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawArc(QRectF(box.center().x() - box.width() * 0.08, box.top() + box.height() * 0.28, box.width() * 0.42, box.height() * 0.44), -50 * 16, 100 * 16)
        painter.drawArc(QRectF(box.center().x() + box.width() * 0.02, box.top() + box.height() * 0.18, box.width() * 0.48, box.height() * 0.64), -50 * 16, 100 * 16)
    elif k == "volup":
        painter.drawLine(box.center().x(), box.top() + box.height() * 0.22, box.center().x(), box.bottom() - box.height() * 0.22)
        painter.drawLine(box.left() + box.width() * 0.22, box.center().y(), box.right() - box.width() * 0.22, box.center().y())
    elif k == "voldown":
        painter.drawLine(box.left() + box.width() * 0.22, box.center().y(), box.right() - box.width() * 0.22, box.center().y())
    elif k == "mute":
        path = QPainterPath()
        path.moveTo(box.left() + box.width() * 0.38, box.top() + box.height() * 0.28)
        path.lineTo(box.left() + box.width() * 0.18, box.top() + box.height() * 0.42)
        path.lineTo(box.left() + box.width() * 0.18, box.top() + box.height() * 0.58)
        path.lineTo(box.left() + box.width() * 0.38, box.top() + box.height() * 0.72)
        path.closeSubpath()
        painter.drawPath(path)
        painter.drawLine(box.left() + box.width() * 0.15, box.bottom() - box.height() * 0.18, box.right() - box.width() * 0.12, box.top() + box.height() * 0.18)
    elif k == "mic":
        painter.drawRoundedRect(QRectF(box.center().x() - box.width() * 0.14, box.top() + box.height() * 0.08, box.width() * 0.28, box.height() * 0.48), 8, 8)
        painter.drawArc(QRectF(box.left() + box.width() * 0.18, box.top() + box.height() * 0.28, box.width() * 0.64, box.height() * 0.42), 180 * 16, 180 * 16)
        painter.drawLine(box.center().x(), box.top() + box.height() * 0.7, box.center().x(), box.bottom() - box.height() * 0.12)
        painter.drawLine(box.center().x() - box.width() * 0.16, box.bottom() - box.height() * 0.12, box.center().x() + box.width() * 0.16, box.bottom() - box.height() * 0.12)
    elif k == "monitor":
        frame = QRectF(box.left() + box.width() * 0.12, box.top() + box.height() * 0.12, box.width() * 0.76, box.height() * 0.52)
        painter.drawRoundedRect(frame, 4, 4)
        painter.drawLine(box.center().x(), frame.bottom(), box.center().x(), box.bottom() - box.height() * 0.16)
        painter.drawLine(box.center().x() - box.width() * 0.18, box.bottom() - box.height() * 0.16, box.center().x() + box.width() * 0.18, box.bottom() - box.height() * 0.16)
    elif k == "play":
        path = QPainterPath()
        path.moveTo(box.left() + box.width() * 0.28, box.top() + box.height() * 0.18)
        path.lineTo(box.right() - box.width() * 0.18, box.center().y())
        path.lineTo(box.left() + box.width() * 0.28, box.bottom() - box.height() * 0.18)
        path.closeSubpath()
        painter.setBrush(accent)
        painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    elif k == "pause":
        w = box.width() * 0.18
        painter.setBrush(accent)
        painter.drawRoundedRect(QRectF(box.left() + box.width() * 0.22, box.top() + box.height() * 0.18, w, box.height() * 0.64), 3, 3)
        painter.drawRoundedRect(QRectF(box.right() - box.width() * 0.22 - w, box.top() + box.height() * 0.18, w, box.height() * 0.64), 3, 3)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    elif k == "stop":
        painter.setBrush(accent)
        painter.drawRoundedRect(box.adjusted(box.width() * 0.18, box.height() * 0.18, -box.width() * 0.18, -box.height() * 0.18), 4, 4)
        painter.setBrush(Qt.BrushStyle.NoBrush)
    elif k == "check":
        painter.setPen(QPen(QColor(C.ok), max(3, size // 18)))
        painter.drawLine(box.left() + box.width() * 0.15, box.center().y(), box.center().x() - 2, box.bottom() - box.height() * 0.22)
        painter.drawLine(box.center().x() - 2, box.bottom() - box.height() * 0.22, box.right() - box.width() * 0.12, box.top() + box.height() * 0.2)
    elif k == "alert":
        path = QPainterPath()
        path.moveTo(box.center().x(), box.top())
        path.lineTo(box.right(), box.bottom())
        path.lineTo(box.left(), box.bottom())
        path.closeSubpath()
        painter.setPen(QPen(QColor(C.danger), max(2, size // 24)))
        painter.drawPath(path)
        painter.drawLine(box.center().x(), box.top() + box.height() * 0.38, box.center().x(), box.top() + box.height() * 0.62)
        painter.drawPoint(box.center().x(), box.top() + box.height() * 0.78)
    elif k == "battery":
        body = QRectF(
            box.left() + box.width() * 0.28,
            box.top() + box.height() * 0.22,
            box.width() * 0.44,
            box.height() * 0.58,
        )
        painter.drawRoundedRect(body, 3, 3)
        nub_w = body.width() * 0.28
        painter.drawRoundedRect(
            QRectF(body.center().x() - nub_w / 2, body.top() - box.height() * 0.09, nub_w, box.height() * 0.1),
            2,
            2,
        )
        inner = body.adjusted(size * 0.04, size * 0.04, -size * 0.04, -size * 0.04)
        fill_h = inner.height() * 0.55
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(
            QRectF(inner.left(), inner.bottom() - fill_h, inner.width(), fill_h),
            2,
            2,
        )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, max(2, size // 24)))
    elif k in ("eq", "eqfx", "preset"):
        bars = 5
        gap = box.width() * 0.08
        bar_w = (box.width() - gap * (bars - 1)) / bars
        heights = (0.35, 0.7, 1.0, 0.55, 0.8)
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        for i, height in enumerate(heights):
            h = box.height() * height
            painter.drawRoundedRect(
                QRectF(box.left() + i * (bar_w + gap), box.bottom() - h, bar_w, h),
                2,
                2,
            )
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, max(2, size // 24)))
    elif k in ("wave", "sound", "pad"):
        mid = box.center()
        path = QPainterPath()
        path.moveTo(box.left(), mid.y())
        path.cubicTo(
            box.left() + box.width() * 0.2,
            box.top(),
            box.left() + box.width() * 0.35,
            box.bottom(),
            mid.x(),
            mid.y(),
        )
        path.cubicTo(
            box.left() + box.width() * 0.7,
            box.top(),
            box.left() + box.width() * 0.82,
            box.bottom(),
            box.right(),
            mid.y(),
        )
        painter.drawPath(path)
    elif k == "cable":
        painter.drawRoundedRect(
            box.adjusted(box.width() * 0.06, box.height() * 0.28, -box.width() * 0.06, -box.height() * 0.28),
            8,
            8,
        )
        painter.drawEllipse(
            QRectF(
                box.left() + box.width() * 0.16,
                box.center().y() - box.height() * 0.12,
                box.width() * 0.22,
                box.height() * 0.24,
            )
        )
        painter.drawEllipse(
            QRectF(
                box.right() - box.width() * 0.38,
                box.center().y() - box.height() * 0.12,
                box.width() * 0.22,
                box.height() * 0.24,
            )
        )
    elif k == "shuffle":
        painter.drawLine(
            box.left() + box.width() * 0.12,
            box.top() + box.height() * 0.28,
            box.right() - box.width() * 0.28,
            box.top() + box.height() * 0.28,
        )
        painter.drawLine(
            box.right() - box.width() * 0.42,
            box.top() + box.height() * 0.14,
            box.right() - box.width() * 0.18,
            box.top() + box.height() * 0.28,
        )
        painter.drawLine(
            box.right() - box.width() * 0.42,
            box.top() + box.height() * 0.42,
            box.right() - box.width() * 0.18,
            box.top() + box.height() * 0.28,
        )
        painter.drawLine(
            box.left() + box.width() * 0.12,
            box.bottom() - box.height() * 0.28,
            box.right() - box.width() * 0.28,
            box.bottom() - box.height() * 0.28,
        )
        painter.drawLine(
            box.right() - box.width() * 0.42,
            box.bottom() - box.height() * 0.14,
            box.right() - box.width() * 0.18,
            box.bottom() - box.height() * 0.28,
        )
        painter.drawLine(
            box.right() - box.width() * 0.42,
            box.bottom() - box.height() * 0.42,
            box.right() - box.width() * 0.18,
            box.bottom() - box.height() * 0.28,
        )
        painter.drawLine(
            box.left() + box.width() * 0.38,
            box.top() + box.height() * 0.28,
            box.right() - box.width() * 0.38,
            box.bottom() - box.height() * 0.28,
        )
    elif k == "repeat":
        painter.drawArc(
            QRectF(box.left() + box.width() * 0.12, box.top() + box.height() * 0.18, box.width() * 0.76, box.height() * 0.64),
            40 * 16,
            280 * 16,
        )
        painter.drawLine(
            box.right() - box.width() * 0.22,
            box.top() + box.height() * 0.18,
            box.right() - box.width() * 0.08,
            box.top() + box.height() * 0.34,
        )
        painter.drawLine(
            box.right() - box.width() * 0.22,
            box.top() + box.height() * 0.18,
            box.right() - box.width() * 0.38,
            box.top() + box.height() * 0.32,
        )
    elif k in ("dice", "random"):
        painter.drawRoundedRect(box, 8, 8)
        dot = max(2.0, size * 0.05)
        painter.setBrush(accent)
        painter.setPen(Qt.PenStyle.NoPen)
        cx, cy = box.center().x(), box.center().y()
        painter.drawEllipse(QRectF(cx - dot, cy - dot, dot * 2, dot * 2))
        painter.drawEllipse(QRectF(box.left() + box.width() * 0.22 - dot, box.top() + box.height() * 0.22 - dot, dot * 2, dot * 2))
        painter.drawEllipse(QRectF(box.right() - box.width() * 0.22 - dot, box.bottom() - box.height() * 0.22 - dot, dot * 2, dot * 2))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(accent, max(2, size // 24)))
    else:
        # grid
        cell = box.width() / 3.6
        gap = cell * 0.35
        for r in range(2):
            for c in range(3):
                painter.drawRoundedRect(
                    QRectF(
                        box.left() + c * (cell + gap),
                        box.top() + r * (cell + gap) + box.height() * 0.08,
                        cell,
                        cell,
                    ),
                    3,
                    3,
                )

    painter.end()
    return pm


def gradient_well(size: int) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    g = QLinearGradient(0, 0, 0, size)
    g.setColorAt(0, QColor("#242424"))
    g.setColorAt(1, QColor("#101010"))
    p.fillRect(0, 0, size, size, g)
    p.end()
    return pm


def application_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 22, 24, 32, 48, 64, 128):
        icon.addPixmap(icon_pixmap("grid", size))
    return icon


def tray_icon() -> QIcon:
    return application_icon()
