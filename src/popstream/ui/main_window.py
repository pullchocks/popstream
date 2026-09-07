from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from popstream.core.engine import Engine
from popstream.core.icons import application_icon, tray_icon
from popstream.core.store import load_settings, save_settings
from popstream.ui.canvas import DeviceCanvas
from popstream.ui.catalog import ActionCatalog
from popstream.ui.inspector import PropertyInspector
from popstream.ui.settings_dialog import SettingsDialog
from popstream.ui.theme import C, apply_app_theme, shade


class MainWindow(QMainWindow):
    def __init__(self, engine: Engine) -> None:
        super().__init__()
        self.engine = engine
        self.setWindowTitle("PopStream")
        self.resize(1280, 820)
        self._syncing = False
        self._settings = None
        self._force_quit = False
        self._hiding_to_tray = False
        self.setWindowIcon(application_icon())
        self._setup_tray()

        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 8)
        layout.setSpacing(10)

        layout.addWidget(self._build_topbar())
        self.banner = QLabel()
        self.banner.setWordWrap(True)
        self._style_banner()
        self.banner.hide()
        layout.addWidget(self.banner)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.catalog = ActionCatalog(engine)
        self.catalog.setMinimumWidth(220)
        center = QWidget()
        center_l = QVBoxLayout(center)
        center_l.setContentsMargins(0, 0, 0, 0)
        self.canvas = DeviceCanvas(engine)
        center_l.addWidget(self.canvas, 1)
        center_l.addWidget(self._build_pagebar())
        self.inspector = PropertyInspector(engine)
        self.inspector.setMinimumWidth(280)
        splitter.addWidget(self.catalog)
        splitter.addWidget(center)
        splitter.addWidget(self.inspector)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([250, 700, 320])
        layout.addWidget(splitter, 1)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        self.setStatusBar(status)
        self._status = QLabel("")
        status.addWidget(self._status, 1)
        status.hide()

        self._connect_engine()
        self._bind_shortcuts()
        self.refresh_devices()
        self.refresh_profiles()
        self.refresh_pages()
        self._on_hardware([])
        if self.engine.lock_active:
            self._on_lock_overlay(True)

    def _build_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(14, 10, 14, 10)
        row.addWidget(self._muted("Device"))
        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(220)
        self.device_combo.currentIndexChanged.connect(self._device_changed)
        row.addWidget(self.device_combo)
        row.addSpacing(12)
        row.addWidget(self._muted("Profile"))
        self.profile_combo = QComboBox()
        self.profile_combo.setMinimumWidth(160)
        self.profile_combo.currentIndexChanged.connect(self._profile_changed)
        row.addWidget(self.profile_combo)
        row.addStretch()
        settings = QPushButton("Settings")
        settings.setToolTip("Appearance, lock screen, and profiles  (Ctrl+,)")
        settings.clicked.connect(self._open_settings)
        row.addWidget(settings)
        return bar

    def _build_pagebar(self) -> QWidget:
        wrap = QWidget()
        row = QHBoxLayout(wrap)
        row.setContentsMargins(8, 0, 8, 4)
        prev_b = QPushButton("‹")
        next_b = QPushButton("›")
        prev_b.setFixedWidth(36)
        next_b.setFixedWidth(36)
        prev_b.clicked.connect(self.engine.prev_page)
        next_b.clicked.connect(self.engine.next_page)
        self.page_name = QLineEdit()
        self.page_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_name.editingFinished.connect(lambda: self.engine.rename_page(self.page_name.text()))
        add = QPushButton("+ Page")
        add.clicked.connect(self.engine.add_page)
        trash = QPushButton("Delete page")
        trash.setObjectName("danger")
        trash.clicked.connect(self.engine.delete_current_page)
        row.addWidget(prev_b)
        row.addWidget(self.page_name, 1)
        row.addWidget(next_b)
        row.addWidget(add)
        row.addWidget(trash)
        return wrap

    def _muted(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("muted")
        return label

    def _connect_engine(self) -> None:
        e = self.engine
        e.profile_changed.connect(self.refresh_profiles)
        e.page_changed.connect(self.refresh_pages)
        e.selection_changed.connect(self._on_selection)
        e.key_visual_changed.connect(self.canvas.refresh)
        e.log_message.connect(self._on_log)
        e.brightness_changed.connect(self._on_brightness)
        e.devices_changed.connect(self.refresh_devices)
        e.hardware_seen.connect(self._on_hardware)
        e.theme_changed.connect(self._on_theme_changed)
        e.lock_overlay_changed.connect(self._on_lock_overlay)
        e.palette_changed.connect(self.inspector.refresh)

    def _bind_shortcuts(self) -> None:
        for seq, slot in (
            (QKeySequence.StandardKey.Delete, lambda: self.engine.clear_key(self.engine.selected_key)),
            (QKeySequence("Backspace"), lambda: self.engine.clear_key(self.engine.selected_key)),
            (QKeySequence("Space"), lambda: self.engine.test_key(self.engine.selected_key)),
            (QKeySequence("Ctrl+Right"), self.engine.next_page),
            (QKeySequence("Ctrl+Left"), self.engine.prev_page),
            (QKeySequence("Ctrl+,"), self._open_settings),
        ):
            action = QAction(self)
            action.setShortcut(seq)
            action.triggered.connect(slot)
            self.addAction(action)

    def refresh_devices(self) -> None:
        self._syncing = True
        self.device_combo.clear()
        current = self.engine.device
        for device in self.engine.all_devices():
            self.device_combo.addItem(device.label(), device)
        if current is not None:
            for i in range(self.device_combo.count()):
                if self.device_combo.itemData(i) is current:
                    self.device_combo.setCurrentIndex(i)
                    break
        self._syncing = False

    def refresh_profiles(self) -> None:
        self._syncing = True
        self.profile_combo.clear()
        model = self.engine.device.spec.id if self.engine.device else "mk2"
        for profile in self.engine.profiles:
            suffix = "" if profile.device_model == model else f"  ({profile.device_model})"
            mark = " ★" if profile.id == self.engine.default_profile_id(profile.device_model) else ""
            self.profile_combo.addItem(profile.name + suffix + mark, profile.id)
        if self.engine.profile is not None:
            idx = self.profile_combo.findData(self.engine.profile.id)
            if idx >= 0:
                self.profile_combo.setCurrentIndex(idx)
        self._syncing = False

    def refresh_pages(self) -> None:
        page = self.engine.current_page()
        mains = self.engine.profile.main_pages() if self.engine.profile else []
        if page is None:
            self.page_name.setText("")
            return
        index = 1
        if not page.folder:
            ids = [p.id for p in mains]
            if page.id in ids:
                index = ids.index(page.id) + 1
        kind = "folder" if page.folder else f"{index} / {max(1, len(mains))}"
        self.page_name.blockSignals(True)
        self.page_name.setText(page.name)
        self.page_name.setPlaceholderText(kind)
        self.page_name.blockSignals(False)
        self.canvas.rebuild()
        self.inspector.refresh()

    def _on_selection(self, _index: int) -> None:
        self.canvas.refresh()
        self.inspector.refresh()

    def _on_brightness(self, _value: int) -> None:
        self.canvas.refresh()

    def _on_log(self, message: str) -> None:
        self._status.setText(message)
        bar = self.statusBar()
        if bar is not None:
            bar.setVisible(bool(message))

    def _style_banner(self) -> None:
        self.banner.setStyleSheet(
            f"background: {shade(C.accent, 0.22)}; color: {C.accent}; "
            "border-radius: 8px; padding: 8px 12px;"
        )

    def _on_theme_changed(self) -> None:
        apply_app_theme()
        icon = application_icon()
        self.setWindowIcon(icon)
        app = QApplication.instance()
        if app is not None:
            app.setWindowIcon(icon)
        if self.tray is not None:
            self.tray.setIcon(tray_icon())
        self.catalog.rebuild()
        self.canvas.refresh()
        self.canvas.update()
        self.inspector.refresh()
        self._style_banner()

    def _setup_tray(self) -> None:
        self.tray: QSystemTrayIcon | None = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self.tray = QSystemTrayIcon(tray_icon(), self)
        self.tray.setToolTip("PopStream")
        menu = QMenu()
        open_act = QAction("Open PopStream", self)
        open_act.triggered.connect(self._show_from_tray)
        settings_act = QAction("Settings", self)
        settings_act.triggered.connect(self._open_settings_from_tray)
        quit_act = QAction("Quit", self)
        quit_act.triggered.connect(self._quit_app)
        menu.addAction(open_act)
        menu.addAction(settings_act)
        menu.addSeparator()
        menu.addAction(quit_act)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()

    def _tray_available(self) -> bool:
        return self.tray is not None and self.tray.isVisible()

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.MiddleClick,
        ):
            self._show_from_tray()

    def _show_from_tray(self) -> None:
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self.show()
        self.raise_()
        self.activateWindow()

    def _open_settings_from_tray(self) -> None:
        self._show_from_tray()
        self._open_settings()

    def _hide_to_tray(self) -> None:
        if not self._tray_available():
            return
        self._hiding_to_tray = True
        self.hide()
        self.setWindowState(self.windowState() & ~Qt.WindowState.WindowMinimized)
        self._hiding_to_tray = False
        settings = load_settings()
        if not settings.get("tray_hint_shown") and self.tray is not None:
            self.tray.showMessage(
                "PopStream",
                "Still running. Click the tray icon to open, or right-click to quit.",
                QSystemTrayIcon.MessageIcon.Information,
                4000,
            )
            settings["tray_hint_shown"] = True
            save_settings(settings)

    def _quit_app(self) -> None:
        self._force_quit = True
        app = QApplication.instance()
        if app is not None:
            app.quit()
        else:
            self.close()

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() != QEvent.Type.WindowStateChange:
            return
        if self._hiding_to_tray or self._force_quit:
            return
        if self.isMinimized() and self._tray_available():
            QTimer.singleShot(0, self._hide_to_tray)

    def closeEvent(self, event) -> None:
        if self._force_quit or not self._tray_available():
            event.accept()
            app = QApplication.instance()
            if app is not None:
                app.quit()
            return
        if load_settings().get("close_to_tray"):
            event.ignore()
            self._hide_to_tray()
            return
        box = QMessageBox(self)
        box.setWindowTitle("PopStream")
        box.setText("Keep PopStream running in the tray so the Stream Deck still works?")
        box.setInformativeText("Quit only if you want to stop the deck.")
        tray_btn = box.addButton("Minimize to tray", QMessageBox.ButtonRole.AcceptRole)
        quit_btn = box.addButton("Quit", QMessageBox.ButtonRole.DestructiveRole)
        always = QCheckBox("Always minimize to tray")
        box.setCheckBox(always)
        box.exec()
        clicked = box.clickedButton()
        if clicked is quit_btn:
            self._force_quit = True
            event.accept()
            app = QApplication.instance()
            if app is not None:
                QTimer.singleShot(0, app.quit)
            return
        event.ignore()
        if clicked is not tray_btn:
            return
        if always.isChecked():
            settings = load_settings()
            settings["close_to_tray"] = True
            save_settings(settings)
        self._hide_to_tray()

    def _open_settings(self) -> None:
        if self._settings is None:
            self._settings = SettingsDialog(self.engine, self)
        self._settings.show()
        self._settings.raise_()
        self._settings.activateWindow()

    def _on_lock_overlay(self, _active: bool) -> None:
        self.canvas.refresh()

    def _on_hardware(self, devices: list) -> None:
        opened = self.engine.hardware_devices
        if opened:
            self.banner.hide()
            return
        if not devices:
            self.banner.hide()
            return
        names = ", ".join(f"{d['label']} ({d['serial']})" for d in devices)
        extra = self.engine.last_hardware_error
        self.banner.setText(
            f"Stream Deck found ({names}) but it could not be opened. "
            f"Quit any other program using the deck. "
            f"{extra} "
            f"If this persists, install udev/99-popstream-streamdeck.rules and replug the deck."
        )
        self.banner.show()

    def _device_changed(self, _index: int) -> None:
        if self._syncing:
            return
        device = self.device_combo.currentData()
        if device is not None:
            self.engine.set_device(device)

    def _profile_changed(self, _index: int) -> None:
        if self._syncing:
            return
        pid = self.profile_combo.currentData()
        if pid:
            self.engine.set_profile(pid)
