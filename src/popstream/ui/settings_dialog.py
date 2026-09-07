from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSlider,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from popstream.core.engine import Engine
from popstream.ui.lock_dialog import LockScreenPage
from popstream.ui.theme import C, ColorSwatch


class SettingsDialog(QDialog):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine
        self.setWindowTitle("Settings")
        self.setModal(False)
        self.setMinimumWidth(460)
        self.setMinimumHeight(420)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        tabs = QTabWidget()
        tabs.addTab(self._appearance_page(), "Appearance")
        self.lock_page = LockScreenPage(engine, self)
        tabs.addTab(self.lock_page, "Lock screen")
        tabs.addTab(self._profiles_page(), "Profiles")
        outer.addWidget(tabs, 1)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        buttons.addWidget(close)
        outer.addLayout(buttons)

        engine.theme_changed.connect(self._sync_theme)
        engine.brightness_changed.connect(self._sync_brightness)
        engine.profile_changed.connect(self._refresh_profile_list)
        self._sync_theme()
        self._sync_brightness(engine.profile.brightness if engine.profile else 70)
        self._refresh_profile_list()

    def _appearance_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 12, 8, 8)

        hint = QLabel(
            "These colors are the app defaults. Individual keys can override them "
            "in the inspector on the right."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        form.setContentsMargins(0, 8, 0, 8)
        scheme_row = QHBoxLayout()
        self.theme_color = ColorSwatch(allow_reset=True, fallback="accent")
        self.theme_color.setToolTip("App scheme color. Right-click to restore the default.")
        self.theme_color.color_changed.connect(self.engine.set_accent)
        scheme_row.addWidget(self.theme_color)
        scheme_row.addStretch()
        scheme_wrap = QWidget()
        scheme_wrap.setLayout(scheme_row)

        text_row = QHBoxLayout()
        self.theme_text = ColorSwatch(allow_reset=True, fallback="text")
        self.theme_text.setToolTip("App text color. Right-click to restore the default.")
        self.theme_text.color_changed.connect(self.engine.set_text_color)
        reset = QPushButton("Default")
        reset.setObjectName("flat")
        reset.setToolTip("Restore the default scheme and text colors")
        reset.clicked.connect(self._reset_theme)
        text_row.addWidget(self.theme_text)
        text_row.addWidget(reset)
        text_row.addStretch()
        text_wrap = QWidget()
        text_wrap.setLayout(text_row)

        form.addRow("Scheme", scheme_wrap)
        form.addRow("Text", text_wrap)
        layout.addLayout(form)

        bright_label = QLabel("Deck")
        bright_label.setStyleSheet("font-weight: 600; padding-top: 12px;")
        layout.addWidget(bright_label)
        bright_hint = QLabel("Brightness of the Stream Deck keys.")
        bright_hint.setObjectName("hint")
        layout.addWidget(bright_hint)

        bright_row = QHBoxLayout()
        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.valueChanged.connect(self.engine.set_brightness)
        self.brightness_value = QLabel("70%")
        self.brightness_value.setMinimumWidth(40)
        bright_row.addWidget(self.brightness, 1)
        bright_row.addWidget(self.brightness_value)
        layout.addLayout(bright_row)
        layout.addStretch()
        return page

    def _profiles_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 12, 8, 8)
        hint = QLabel(
            "The default profile for each deck model is opened when PopStream starts. "
            "Double-click a profile to open it now."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.profile_list = QListWidget()
        self.profile_list.setMinimumHeight(160)
        self.profile_list.itemSelectionChanged.connect(self._sync_profile_buttons)
        self.profile_list.itemDoubleClicked.connect(self._open_selected_profile)
        layout.addWidget(self.profile_list, 1)
        row = QHBoxLayout()
        new_p = QPushButton("New")
        new_p.clicked.connect(self._new_profile)
        self.rename_btn = QPushButton("Rename")
        self.rename_btn.clicked.connect(self._rename_profile)
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("danger")
        self.delete_btn.clicked.connect(self._delete_profile)
        self.default_btn = QPushButton("Set as default")
        self.default_btn.clicked.connect(self._set_default_profile)
        row.addWidget(new_p)
        row.addWidget(self.rename_btn)
        row.addWidget(self.delete_btn)
        row.addWidget(self.default_btn)
        row.addStretch()
        layout.addLayout(row)
        return page

    def _sync_theme(self) -> None:
        self.theme_color.set_color(C.accent)
        self.theme_text.set_color(C.text)

    def _sync_brightness(self, value: int) -> None:
        self.brightness.blockSignals(True)
        self.brightness.setValue(value)
        self.brightness.blockSignals(False)
        self.brightness_value.setText(f"{value}%")

    def _profile_by_id(self, profile_id: str):
        return next((p for p in self.engine.profiles if p.id == profile_id), None)

    def _selected_profile_id(self) -> str:
        item = self.profile_list.currentItem()
        if item is None:
            return ""
        return str(item.data(Qt.ItemDataRole.UserRole) or "")

    def _profile_row_text(self, profile) -> str:
        bits = [profile.name, f"({profile.device_model})"]
        extras = []
        if profile.id == self.engine.default_profile_id(profile.device_model):
            extras.append("default")
        if self.engine.profile is not None and profile.id == self.engine.profile.id:
            extras.append("open")
        if extras:
            bits.append("— " + ", ".join(extras))
        return "  ".join(bits)

    def _refresh_profile_list(self) -> None:
        selected = self._selected_profile_id()
        self.profile_list.blockSignals(True)
        self.profile_list.clear()
        for profile in self.engine.profiles:
            item = QListWidgetItem(self._profile_row_text(profile))
            item.setData(Qt.ItemDataRole.UserRole, profile.id)
            self.profile_list.addItem(item)
        target = selected or (self.engine.profile.id if self.engine.profile else "")
        for i in range(self.profile_list.count()):
            if self.profile_list.item(i).data(Qt.ItemDataRole.UserRole) == target:
                self.profile_list.setCurrentRow(i)
                break
        self.profile_list.blockSignals(False)
        self._sync_profile_buttons()
        if self.engine.profile is not None:
            self._sync_brightness(self.engine.profile.brightness)

    def _sync_profile_buttons(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id())
        enabled = profile is not None
        self.rename_btn.setEnabled(enabled)
        self.delete_btn.setEnabled(enabled and len(self.engine.profiles) > 1)
        is_default = bool(
            profile is not None
            and profile.id == self.engine.default_profile_id(profile.device_model)
        )
        self.default_btn.setEnabled(enabled and not is_default)

    def _reset_theme(self) -> None:
        self.theme_color.set_color("")
        self.theme_text.set_color("")
        self.engine.set_theme_colors(accent="", text="")

    def _new_profile(self) -> None:
        name, ok = QInputDialog.getText(self, "New profile", "Name:", text="New Profile")
        if ok and name.strip():
            self.engine.create_profile(name.strip())

    def _open_selected_profile(self, _item=None) -> None:
        profile_id = self._selected_profile_id()
        if profile_id:
            self.engine.set_profile(profile_id)

    def _rename_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id())
        if profile is None:
            return
        name, ok = QInputDialog.getText(self, "Rename profile", "Name:", text=profile.name)
        if ok and name.strip():
            self.engine.rename_profile(name.strip(), profile.id)

    def _delete_profile(self) -> None:
        profile = self._profile_by_id(self._selected_profile_id())
        if profile is None:
            return
        if len(self.engine.profiles) <= 1:
            QMessageBox.information(self, "Delete profile", "Keep at least one profile.")
            return
        if QMessageBox.question(self, "Delete profile", f"Delete “{profile.name}”?") != QMessageBox.StandardButton.Yes:
            return
        if not self.engine.delete_profile(profile.id):
            QMessageBox.information(self, "Delete profile", "Keep at least one profile.")

    def _set_default_profile(self) -> None:
        profile_id = self._selected_profile_id()
        if profile_id:
            self.engine.set_default_profile(profile_id)

    def closeEvent(self, event) -> None:
        self.lock_page.stop_preview()
        super().closeEvent(event)
