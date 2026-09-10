from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from popstream.core.engine import PALETTE_PAGE, Engine
from popstream.core.icons import icon_pixmap
from popstream.core.plugin import ActionContext
from popstream.ui.theme import C, ColorSwatch, fit_combo, plain_spin, tighten_form


class PropertyInspector(QWidget):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setObjectName("panel")
        self._plugin_widget: QWidget | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(6)
        self.header = QLabel("Key")
        self.header.setStyleSheet("font-weight: 700; font-size: 14px;")
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon.setFixedHeight(40)
        self.empty = QLabel("Select a key, then drag an action onto it.")
        self.empty.setObjectName("hint")
        self.empty.setWordWrap(True)
        self.color_host = QWidget()
        color_form = QFormLayout(self.color_host)
        tighten_form(color_form)
        color_row = QHBoxLayout()
        color_row.setContentsMargins(0, 0, 0, 0)
        color_row.setSpacing(8)
        self.key_color = ColorSwatch(allow_reset=True, fallback="accent")
        self.key_color.setToolTip("Key color — right-click to use the theme scheme")
        self.key_color.color_changed.connect(self._save_color)
        color_row.addWidget(self.key_color)
        color_row.addStretch()
        color_wrap = QWidget()
        color_wrap.setLayout(color_row)
        text_row = QHBoxLayout()
        text_row.setContentsMargins(0, 0, 0, 0)
        text_row.setSpacing(8)
        self.key_text = ColorSwatch(allow_reset=True, fallback="text")
        self.key_text.setToolTip("Text color — right-click to use the theme text color")
        self.key_text.color_changed.connect(self._save_text_color)
        reset = QPushButton("Default")
        reset.setObjectName("flat")
        reset.setToolTip("Use the app theme colors")
        reset.clicked.connect(self._reset_color)
        text_row.addWidget(self.key_text)
        text_row.addWidget(reset)
        text_row.addStretch()
        text_wrap = QWidget()
        text_wrap.setLayout(text_row)
        color_form.addRow("Scheme", color_wrap)
        color_form.addRow("Text", text_wrap)
        self.form_host = QWidget()
        form = QFormLayout(self.form_host)
        tighten_form(form)
        self.title = QLineEdit()
        self.title.editingFinished.connect(self._save_common)
        self.show_title = QCheckBox("Show title")
        self.show_title.toggled.connect(self._save_common)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(8)
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.show_title)
        title_wrap = QWidget()
        title_wrap.setLayout(title_row)
        self.font_size = plain_spin(8, 24, 12)
        self.font_size.valueChanged.connect(self._save_common)
        self.align = fit_combo(QComboBox())
        self.align.addItem("Bottom", "bottom")
        self.align.addItem("Middle", "middle")
        self.align.addItem("Top", "top")
        self.align.currentIndexChanged.connect(self._save_common)
        icon_row = QHBoxLayout()
        icon_row.setContentsMargins(0, 0, 0, 0)
        icon_row.setSpacing(8)
        self.icon_path = QLineEdit()
        self.icon_path.setPlaceholderText("Custom icon")
        self.icon_path.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.icon_path.editingFinished.connect(self._save_common)
        browse = QPushButton("…")
        browse.setFixedHeight(32)
        browse.setMinimumWidth(40)
        browse.clicked.connect(self._browse_icon)
        icon_row.addWidget(self.icon_path, 1)
        icon_row.addWidget(browse)
        icon_wrap = QWidget()
        icon_wrap.setLayout(icon_row)
        form.addRow("Title", title_wrap)
        form.addRow("Font", self.font_size)
        form.addRow("Align", self.align)
        form.addRow("Icon", icon_wrap)
        self.plugin_host = QWidget()
        self.plugin_layout = QVBoxLayout(self.plugin_host)
        self.plugin_layout.setContentsMargins(0, 4, 0, 0)
        self.plugin_layout.setSpacing(6)
        self.remove = QPushButton("Remove action")
        self.remove.setObjectName("danger")
        self.remove.clicked.connect(self._remove)
        self.test = QPushButton("Test action")
        self.test.setObjectName("accent")
        self.test.clicked.connect(self._test)
        outer.addWidget(self.header)
        outer.addWidget(self.icon)
        outer.addWidget(self.empty)
        outer.addWidget(self.color_host)
        outer.addWidget(self.form_host)
        outer.addWidget(self.plugin_host)
        outer.addSpacing(8)
        outer.addWidget(self.test)
        outer.addWidget(self.remove)
        outer.addStretch(1)
        self._loading = False
        self.refresh()

    def refresh(self) -> None:
        self._loading = True
        if self._plugin_widget is not None:
            self.plugin_layout.removeWidget(self._plugin_widget)
            self._plugin_widget.deleteLater()
            self._plugin_widget = None
        if self.engine.palette_focus and self.engine.palette_plugin_id:
            self._show_palette()
            self._loading = False
            return
        slot = self.engine.slot_at(self.engine.selected_key)
        self.header.setText(f"Key {self.engine.selected_key + 1}")
        if slot is None:
            self.empty.setText("Select a key, then drag an action onto it.")
            self.empty.show()
            self.color_host.hide()
            self.form_host.hide()
            self.plugin_host.hide()
            self.remove.hide()
            self.test.hide()
            self.icon.setPixmap(icon_pixmap("grid", 40, C.muted))
            self._loading = False
            return
        self.color_host.show()
        self.key_color.set_color(slot.background)
        self.key_text.set_color(slot.text_color)
        if slot.empty:
            self.empty.setText("Drag an action onto this key. You can still set a custom color.")
            self.empty.show()
            self.form_host.hide()
            self.plugin_host.hide()
            self.remove.hide()
            self.test.hide()
            self.icon.setPixmap(icon_pixmap("grid", 40, C.muted))
            self.header.setText(f"Key {self.engine.selected_key + 1}")
            self._loading = False
            return
        self.empty.hide()
        self.form_host.show()
        self.plugin_host.show()
        self.remove.show()
        self.test.show()
        info = self.engine.host.find_action(slot.plugin_id, slot.action_id)
        self.header.setText(info.name if info else "Key")
        self.icon.setPixmap(icon_pixmap(info.icon if info else "grid", 40))
        self.title.setText(slot.title)
        self.show_title.setChecked(slot.show_title)
        self.font_size.setValue(slot.font_size)
        idx = self.align.findData(slot.title_align)
        self.align.setCurrentIndex(idx if idx >= 0 else 0)
        self.icon_path.setText(slot.icon_path)
        page = self.engine.current_page()
        if page is not None:
            bound = self.engine._bound.get(self.engine._bkey(page.id, self.engine.selected_key))
            if bound and bound.action is not None:
                widget = bound.action.create_property_inspector(bound.ctx, self.plugin_host)
                if widget is not None:
                    self._plugin_widget = widget
                    self.plugin_layout.addWidget(widget)
        self._loading = False

    def _show_palette(self) -> None:
        info = self.engine.host.find_action(self.engine.palette_plugin_id, self.engine.palette_action_id)
        self.header.setText(info.name if info else "Action")
        self.icon.setPixmap(icon_pixmap(info.icon if info else "grid", 40))
        self.empty.hide()
        self.color_host.hide()
        self.form_host.hide()
        self.remove.hide()
        self.test.hide()
        self.plugin_host.show()
        action = self.engine.host.create_action(self.engine.palette_plugin_id, self.engine.palette_action_id)
        if action is None:
            self.plugin_host.hide()
            return
        ctx = ActionContext(self.engine, PALETTE_PAGE, 0)
        widget = action.create_property_inspector(ctx, self.plugin_host)
        if widget is not None:
            self._plugin_widget = widget
            self.plugin_layout.addWidget(widget)
        else:
            self.plugin_host.hide()

    def _save_common(self) -> None:
        if self._loading:
            return
        self.engine.update_slot(
            self.engine.selected_key,
            title=self.title.text(),
            show_title=self.show_title.isChecked(),
            font_size=self.font_size.value(),
            title_align=self.align.currentData(),
            icon_path=self.icon_path.text().strip(),
        )

    def _save_color(self, hex_color: str) -> None:
        if self._loading:
            return
        self.engine.update_slot(self.engine.selected_key, background=hex_color)

    def _save_text_color(self, hex_color: str) -> None:
        if self._loading:
            return
        self.engine.update_slot(self.engine.selected_key, text_color=hex_color)

    def _reset_color(self) -> None:
        self.key_color.set_color("")
        self.key_text.set_color("")
        self.engine.update_slot(self.engine.selected_key, background="", text_color="")

    def _browse_icon(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose icon", "", "Images (*.png *.jpg *.jpeg *.bmp *.svg)"
        )
        if path:
            self.icon_path.setText(path)
            self._save_common()

    def _remove(self) -> None:
        self.engine.clear_key(self.engine.selected_key)

    def _test(self) -> None:
        self.engine.test_key(self.engine.selected_key)
