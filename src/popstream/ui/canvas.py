from __future__ import annotations

import json

from PySide6.QtCore import QMimeData, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QDrag,
    QFont,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QVBoxLayout, QWidget

from popstream.core.engine import Engine
from popstream.ui.theme import C, MIME_ACTION, MIME_KEY


def _action_from_mime(mime: QMimeData) -> tuple[str, str] | None:
    raw = ""
    if mime.hasFormat(MIME_ACTION):
        raw = bytes(mime.data(MIME_ACTION)).decode()
    elif mime.hasText():
        raw = mime.text()
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    plugin_id = data.get("pluginId")
    action_id = data.get("actionId")
    if plugin_id and action_id:
        return plugin_id, action_id
    return None


def _is_action_drop(mime: QMimeData) -> bool:
    return _action_from_mime(mime) is not None


class KeyPad(QWidget):
    selected = Signal(int)
    pressed = Signal(int)
    released = Signal(int)
    tested = Signal(int)
    dropped_action = Signal(int, str, str)
    swap_with = Signal(int, int)

    def __init__(self, index: int, parent=None) -> None:
        super().__init__(parent)
        self.index = index
        self.engine = None
        self._selected = False
        self._down = False
        self._empty = True
        self._press = QPoint()
        self._drop_hover = False
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_empty(self, empty: bool) -> None:
        self._empty = empty
        self.update()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(78, 78)

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        if self._down:
            rect.adjust(2, 2, -2, -2)
        path = QPainterPath()
        path.addRoundedRect(rect, 11, 11)
        painter.setClipPath(path)

        engine = self.engine
        image = None
        custom = False
        if engine is not None:
            image = engine.key_image(self.index)
            slot = engine.slot_at(self.index)
            custom = bool(slot and (slot.background or "").strip())

        lock = bool(engine and getattr(engine, "lock_active", False))
        if image is not None and not image.isNull() and (not self._empty or custom or lock):
            painter.drawImage(rect.toRect(), image)
            if self._empty and not lock:
                painter.setClipping(False)
                painter.setPen(QPen(QColor(255, 255, 255, 50), 1, Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRoundedRect(rect.adjusted(10, 10, -10, -10), 6, 6)
        else:
            painter.fillPath(path, QColor(C.key))
            painter.setClipping(False)
            painter.setPen(QPen(QColor("#2a2a2a"), 1, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(10, 10, -10, -10), 6, 6)

        painter.setClipping(False)
        if self._drop_hover:
            wash = QColor(C.accent)
            wash.setAlpha(50)
            painter.fillPath(path, wash)
            painter.setPen(QPen(QColor(C.accent), 2.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 11, 11)
        elif self._selected:
            painter.setPen(QPen(QColor(C.accent), 2.4))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 11, 11)
        else:
            painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect, 11, 11)

        if engine is not None:
            dim = 140 * (1 - engine.device.brightness / 100.0) if engine.device else 0
            if dim > 0:
                painter.setClipPath(path)
                painter.fillRect(rect, QColor(0, 0, 0, int(dim)))

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press = event.position().toPoint()
        self.selected.emit(self.index)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._empty:
            return
        if self._press.isNull():
            return
        if (event.position().toPoint() - self._press).manhattanLength() < 8:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(MIME_KEY, json.dumps({"index": self.index}).encode())
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)
        self._press = QPoint()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._press = QPoint()

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.tested.emit(self.index)

    def dragEnterEvent(self, event) -> None:
        if _is_action_drop(event.mimeData()) or event.mimeData().hasFormat(MIME_KEY):
            self._drop_hover = True
            self.update()
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if _is_action_drop(event.mimeData()) or event.mimeData().hasFormat(MIME_KEY):
            event.acceptProposedAction()

    def dragLeaveEvent(self, _event) -> None:
        self._drop_hover = False
        self.update()

    def dropEvent(self, event) -> None:
        self._drop_hover = False
        self.update()
        payload = _action_from_mime(event.mimeData())
        if payload is not None:
            self.dropped_action.emit(self.index, payload[0], payload[1])
            event.acceptProposedAction()
            return
        if event.mimeData().hasFormat(MIME_KEY):
            data = json.loads(bytes(event.mimeData().data(MIME_KEY)).decode())
            src = int(data["index"])
            if src != self.index:
                self.swap_with.emit(src, self.index)
            event.acceptProposedAction()


class DeviceCanvas(QWidget):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self._keys: list[KeyPad] = []
        self._hint = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 16, 18, 18)
        self._face = QWidget(self)
        self._face.setObjectName("deviceFace")
        self._face.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._face.setStyleSheet("background: transparent;")
        self._grid = QGridLayout(self._face)
        self._grid.setContentsMargins(26, 22, 26, 54)
        self._grid.setSpacing(14)
        self.setAcceptDrops(True)
        outer.addWidget(self._face, 0, Qt.AlignmentFlag.AlignCenter)
        self.setMinimumHeight(280)
        self.rebuild()

    def rebuild(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._keys.clear()
        spec = self.engine.current_spec()
        extra_bottom = 36 if spec.extra in ("dials", "touchstrip") else 0
        self._grid.setContentsMargins(26, 22, 26, 54 + extra_bottom)
        for i in range(spec.key_count):
            key = KeyPad(i, self._face)
            key.engine = self.engine
            key.selected.connect(self.engine.select_key)
            key.tested.connect(self.engine.test_key)
            key.dropped_action.connect(self.engine.assign_action)
            key.swap_with.connect(self.engine.swap_keys)
            row, col = divmod(i, spec.columns)
            self._grid.addWidget(key, row, col)
            self._keys.append(key)
        self._hint = {
            "dials": "Dials + touch strip — hardware plugin",
            "touchstrip": "Info bar — hardware plugin",
            "no_lcd": "Pedal — no LCD keys",
        }.get(spec.extra, "")
        self.refresh()
        self.update()

    def _key_at(self, pos: QPoint) -> KeyPad | None:
        local = self._face.mapFrom(self, pos)
        for key in self._keys:
            if key.geometry().contains(local):
                return key
        return None

    def dragEnterEvent(self, event) -> None:
        if _is_action_drop(event.mimeData()) or event.mimeData().hasFormat(MIME_KEY):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if _is_action_drop(event.mimeData()) or event.mimeData().hasFormat(MIME_KEY):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        key = self._key_at(event.position().toPoint())
        if key is None:
            event.ignore()
            return
        payload = _action_from_mime(event.mimeData())
        if payload is not None:
            self.engine.assign_action(key.index, payload[0], payload[1])
            event.acceptProposedAction()
            return
        if event.mimeData().hasFormat(MIME_KEY):
            data = json.loads(bytes(event.mimeData().data(MIME_KEY)).decode())
            self.engine.swap_keys(int(data["index"]), key.index)
            event.acceptProposedAction()

    def refresh(self, index: int | None = None) -> None:
        page = self.engine.current_page()
        targets = [index] if index is not None else range(len(self._keys))
        for i in targets:
            if i >= len(self._keys):
                continue
            empty = True
            if page is not None and i < len(page.buttons):
                empty = page.buttons[i].empty
            self._keys[i].set_empty(empty)
            self._keys[i].set_selected(i == self.engine.selected_key)
            self._keys[i].update()

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        face = self._face.geometry().adjusted(-8, -8, 8, 8)
        path = QPainterPath()
        path.addRoundedRect(QRectF(face), 22, 22)
        shadow = QColor(0, 0, 0, 90)
        painter.fillPath(path.translated(0, 5), shadow)
        grad = QLinearGradient(face.topLeft(), face.bottomLeft())
        grad.setColorAt(0, QColor(C.bezel_hi))
        grad.setColorAt(0.12, QColor(C.bezel))
        grad.setColorAt(1, QColor(C.bezel_lo))
        painter.fillPath(path, grad)
        painter.setPen(QPen(QColor(255, 255, 255, 25), 1))
        painter.drawRoundedRect(QRectF(face).adjusted(1, 1, -1, -1), 21, 21)

        well = QRectF(self._face.geometry()).adjusted(12, 10, -12, -42)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#151515"))
        painter.drawRoundedRect(well, 14, 14)

        logo_font = QFont("Inter, Sans Serif", 10, QFont.Weight.Bold)
        painter.setFont(logo_font)
        painter.setPen(QColor(C.muted))
        logo_rect = QRectF(face.left(), face.bottom() - 40, face.width(), 22)
        painter.drawText(logo_rect, int(Qt.AlignmentFlag.AlignCenter), "POPSTREAM")
        painter.setPen(QColor(C.accent))
        painter.setBrush(QColor(C.accent))
        painter.drawEllipse(QRectF(face.center().x() - 54, face.bottom() - 33, 7, 7))

        if self._hint:
            painter.setPen(QColor(C.muted))
            painter.setFont(QFont("Sans Serif", 8))
            painter.drawText(
                QRectF(face.left(), face.bottom() - 18, face.width(), 14),
                int(Qt.AlignmentFlag.AlignCenter),
                self._hint,
            )
