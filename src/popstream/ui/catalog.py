from __future__ import annotations

import json
from collections import defaultdict

from PySide6.QtCore import QMimeData, QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QDrag, QMouseEvent, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from popstream.core.engine import Engine
from popstream.core.icons import icon_pixmap
from popstream.core.store import load_settings, save_settings
from popstream.ui.theme import C, MIME_ACTION

SKIP_PLUGINS = {"com.popstream.example.hello"}


def _split_category(name: str) -> tuple[str, str]:
    text = (name or "").strip()
    if " / " in text:
        parent, child = text.split(" / ", 1)
        parent, child = parent.strip(), child.strip()
        if parent and child:
            return parent, child
    return text, ""


def action_payload(plugin_id: str, action_id: str, settings: dict | None = None) -> bytes:
    data: dict = {"pluginId": plugin_id, "actionId": action_id}
    if settings:
        data["settings"] = settings
    return json.dumps(data).encode()


def _drag_preview(icon_name: str, title: str) -> QPixmap:
    pm = QPixmap(72, 72)
    pm.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    path = QPainterPath()
    path.addRoundedRect(QRectF(1, 1, 70, 70), 12, 12)
    painter.fillPath(path, QColor("#1a1a1a"))
    painter.setPen(QColor(C.accent))
    painter.drawPath(path)
    painter.drawPixmap(18, 10, icon_pixmap(icon_name, 36))
    painter.setPen(QColor(C.text))
    painter.drawText(QRectF(4, 48, 64, 20), int(Qt.AlignmentFlag.AlignCenter), title)
    painter.end()
    return pm


class ElidingLabel(QLabel):
    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        self._full = text
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.setText(self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(0, self.width())))


class ActionTile(QFrame):
    def __init__(self, plugin_id: str, action, catalog: "ActionCatalog", parent=None) -> None:
        super().__init__(parent)
        self.plugin_id = plugin_id
        self.action = action
        self.catalog = catalog
        self._press = QPoint()
        self._dragging = False
        self.setObjectName("tile")
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(8)
        icon = QLabel()
        icon.setPixmap(icon_pixmap(action.icon, 22))
        icon.setFixedSize(24, 24)
        name = ElidingLabel(action.name)
        name.setStyleSheet(f"color: {C.text}; font-size: 12px;")
        for child in (icon, name):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(icon)
        layout.addWidget(name, 1)
        if action.tooltip:
            self.setToolTip(action.tooltip)
        self.set_chosen(False)

    def set_chosen(self, chosen: bool) -> None:
        border = C.accent if chosen else C.border
        self.setStyleSheet(
            f"""
            QFrame#tile {{
                background: {C.panel_alt};
                border: 1px solid {border};
                border-radius: 8px;
            }}
            QFrame#tile:hover {{
                border-color: {C.accent};
            }}
            """
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self._press = event.position().toPoint()
        self._dragging = False
        self.catalog.choose(self.plugin_id, self.action.id)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._press.isNull() or self._dragging:
            return
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return
        if (event.position().toPoint() - self._press).manhattanLength() < QApplication.startDragDistance():
            return
        self._dragging = True
        drag = QDrag(self)
        mime = QMimeData()
        settings = dict(self.action.defaults or {})
        engine = self.catalog.engine
        if engine.palette_plugin_id == self.plugin_id and engine.palette_action_id == self.action.id:
            settings.update(engine.palette_settings)
        raw = action_payload(self.plugin_id, self.action.id, settings or None)
        mime.setData(MIME_ACTION, raw)
        mime.setText(raw.decode())
        drag.setMimeData(mime)
        preview = _drag_preview(self.action.icon, self.action.name)
        drag.setPixmap(preview)
        drag.setHotSpot(preview.rect().center())
        drag.exec(Qt.DropAction.CopyAction)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._press = QPoint()
            self._dragging = False


class CategorySection(QWidget):
    def __init__(
        self,
        name: str,
        collapsed: bool,
        on_toggle,
        parent=None,
        *,
        collapse_key: str | None = None,
        nested: bool = False,
    ) -> None:
        super().__init__(parent)
        self.name = name
        self._key = collapse_key or name
        self._collapsed = collapsed
        self._on_toggle = on_toggle
        self._nested = nested
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10 if nested else 0, 0, 0, 0)
        outer.setSpacing(4)
        self._header = QPushButton()
        self._header.setObjectName("categoryHeaderNested" if nested else "categoryHeader")
        self._header.setCursor(Qt.CursorShape.PointingHandCursor)
        self._header.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._header.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._header.setMinimumHeight(24 if nested else 28)
        self._header.clicked.connect(self._toggle)
        self._body = QWidget()
        self.body_layout = QVBoxLayout(self._body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(6)
        outer.addWidget(self._header)
        outer.addWidget(self._body)
        self._apply()

    def add_tile(self, tile: QWidget) -> None:
        self.body_layout.addWidget(tile)

    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._apply()
        self._on_toggle(self._key, self._collapsed)

    def _apply(self) -> None:
        chevron = "▸" if self._collapsed else "▾"
        label = self.name if self._nested else self.name.upper()
        self._header.setText(f"{chevron}  {label}")
        self._body.setVisible(not self._collapsed)


class ActionCatalog(QWidget):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setObjectName("panel")
        self._tiles: list[ActionTile] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(6)
        title = QLabel("Actions")
        title.setStyleSheet("font-weight: 700; font-size: 14px;")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search actions")
        self.search.textChanged.connect(self.rebuild)
        outer.addWidget(title)
        outer.addWidget(self.search)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._inner = QWidget()
        self._layout = QVBoxLayout(self._inner)
        self._layout.setContentsMargins(0, 4, 0, 0)
        self._layout.setSpacing(6)
        scroll.setWidget(self._inner)
        outer.addWidget(scroll, 1)
        self.engine.palette_changed.connect(self._sync_chosen)
        self.engine.plugins_changed.connect(self.rebuild)
        self._refresh = QTimer(self)
        self._refresh.setInterval(2500)
        self._refresh.timeout.connect(self._refresh_library)
        self._refresh.start()
        self._library_sig = ""
        self.rebuild()

    def choose(self, plugin_id: str, action_id: str) -> None:
        self.engine.select_palette_action(plugin_id, action_id)

    def _expanded(self) -> set[str]:
        raw = load_settings().get("catalog_expanded")
        return set(raw) if isinstance(raw, list) else set()

    def _set_collapsed(self, category: str, collapsed: bool) -> None:
        if self.search.text().strip():
            return
        settings = load_settings()
        items = set(settings.get("catalog_expanded") or [])
        if collapsed:
            items.discard(category)
        else:
            items.add(category)
        settings["catalog_expanded"] = sorted(items)
        settings.pop("catalog_collapsed", None)
        save_settings(settings)

    def _refresh_library(self) -> None:
        self.engine.host.refresh_actions()
        sig = tuple((info.category, info.id, info.name) for loaded in self.engine.host.loaded for info in loaded.actions)
        if sig == self._library_sig:
            return
        self.rebuild()

    def rebuild(self) -> None:
        self.engine.host.refresh_actions()
        self._library_sig = tuple(
            (info.category, info.id, info.name) for loaded in self.engine.host.loaded for info in loaded.actions
        )
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._tiles.clear()
        query = self.search.text().strip().lower()
        searching = bool(query)
        expanded = self._expanded()
        groups: dict[str, dict[str, list[tuple[str, object]]]] = defaultdict(lambda: defaultdict(list))
        for loaded in self.engine.host.loaded:
            if loaded.plugin.id in SKIP_PLUGINS:
                continue
            if not self.engine.plugin_enabled(loaded.plugin.id):
                continue
            for action in loaded.actions:
                hay = f"{action.name} {action.category} {loaded.plugin.name}".lower()
                if query and query not in hay:
                    continue
                parent, child = _split_category(action.category)
                groups[parent][child].append((loaded.plugin.id, action))
        for parent in sorted(groups):
            collapsed = not searching and parent not in expanded
            section = CategorySection(parent, collapsed, self._set_collapsed)
            for plugin_id, action in groups[parent].get("", []):
                tile = ActionTile(plugin_id, action, self)
                section.add_tile(tile)
                self._tiles.append(tile)
            for child in sorted(key for key in groups[parent] if key):
                nest_key = f"{parent} / {child}"
                nested = CategorySection(
                    child,
                    not searching and nest_key not in expanded,
                    self._set_collapsed,
                    collapse_key=nest_key,
                    nested=True,
                )
                for plugin_id, action in groups[parent][child]:
                    tile = ActionTile(plugin_id, action, self)
                    nested.add_tile(tile)
                    self._tiles.append(tile)
                section.add_tile(nested)
            self._layout.addWidget(section)
        self._layout.addStretch()
        self._sync_chosen()

    def _sync_chosen(self) -> None:
        engine = self.engine
        for tile in self._tiles:
            chosen = (
                engine.palette_focus
                and tile.plugin_id == engine.palette_plugin_id
                and tile.action.id == engine.palette_action_id
            )
            tile.set_chosen(chosen)
