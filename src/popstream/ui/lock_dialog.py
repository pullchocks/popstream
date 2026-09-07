from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from popstream.core.engine import Engine
from popstream.core.lockscreen import LOCK_MODES, normalize_lock_config


class LockScreenPage(QWidget):
    def __init__(self, engine: Engine, parent=None) -> None:
        super().__init__(parent)
        self.engine = engine

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        hint = QLabel(
            "When this computer is locked, PopStream can take over the Stream Deck. "
            "Date & time spreads across the keys (AUG 30 / 11 : 33 / PM). "
            "Preview it here without locking."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        outer.addWidget(hint)

        form_host = QWidget()
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 8, 0, 8)
        self.form = form

        self.enabled = QCheckBox("Show a lock screen on the Stream Deck")
        self.mode = QComboBox()
        self.mode.addItem("Date & time", "clock")
        self.mode.addItem("Image", "image")
        self.mode.addItem("Screensaver", "screensaver")
        self.hour12 = QCheckBox("12-hour clock with AM/PM")
        image_row = QHBoxLayout()
        self.image_path = QLineEdit()
        self.image_path.setPlaceholderText("Choose a photo to split across the keys")
        browse = QPushButton("…")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse)
        image_row.addWidget(self.image_path)
        image_row.addWidget(browse)
        image_wrap = QWidget()
        image_wrap.setLayout(image_row)
        self._image_wrap = image_wrap

        form.addRow("", self.enabled)
        form.addRow("Display", self.mode)
        form.addRow("", self.hour12)
        form.addRow("Image", image_wrap)
        outer.addWidget(form_host)

        buttons = QHBoxLayout()
        self.preview = QPushButton("Preview")
        self.preview.setCheckable(True)
        self.preview.setObjectName("accent")
        self.preview.clicked.connect(self._toggle_preview)
        buttons.addWidget(self.preview)
        buttons.addStretch()
        outer.addLayout(buttons)
        outer.addStretch()

        self._loading = False
        self._load()
        self.enabled.toggled.connect(self._commit)
        self.mode.currentIndexChanged.connect(self._commit)
        self.hour12.toggled.connect(self._commit)
        self.image_path.editingFinished.connect(self._commit)
        self.engine.lock_overlay_changed.connect(self._sync_preview_button)

    def _load(self) -> None:
        self._loading = True
        cfg = normalize_lock_config(self.engine.lock_config)
        self.enabled.setChecked(cfg["enabled"])
        idx = self.mode.findData(cfg["mode"] if cfg["mode"] in LOCK_MODES else "clock")
        self.mode.setCurrentIndex(idx if idx >= 0 else 0)
        self.hour12.setChecked(cfg["hour12"])
        self.image_path.setText(cfg["image"])
        self._loading = False
        self._update_mode_widgets()
        self._sync_preview_button(self.engine.lock_preview)

    def _update_mode_widgets(self) -> None:
        mode = self.mode.currentData()
        self.hour12.setVisible(mode == "clock")
        self._image_wrap.setVisible(mode == "image")
        image_label = self.form.labelForField(self._image_wrap)
        if image_label is not None:
            image_label.setVisible(mode == "image")

    def _commit(self) -> None:
        if self._loading:
            return
        self._update_mode_widgets()
        self.engine.set_lock_config(
            {
                "enabled": self.enabled.isChecked(),
                "mode": self.mode.currentData() or "clock",
                "image": self.image_path.text().strip(),
                "hour12": self.hour12.isChecked(),
            }
        )

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Lock screen image",
            self.image_path.text(),
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if path:
            self.image_path.setText(path)
            self._commit()

    def _toggle_preview(self, checked: bool) -> None:
        self._commit()
        self.engine.set_lock_preview(checked)

    def _sync_preview_button(self, _active: bool) -> None:
        previewing = self.engine.lock_preview
        self.preview.blockSignals(True)
        self.preview.setChecked(previewing)
        self.preview.setText("Stop preview" if previewing else "Preview")
        self.preview.blockSignals(False)

    def stop_preview(self) -> None:
        if self.engine.lock_preview:
            self.engine.set_lock_preview(False)
