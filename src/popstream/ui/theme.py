from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QColorDialog,
    QComboBox,
    QFormLayout,
    QPushButton,
    QSizePolicy,
    QSpinBox,
)


DEFAULT_ACCENT = "#f5a623"
DEFAULT_TEXT = "#f2f2f2"


class C:
    bg = "#121212"
    panel = "#1a1a1a"
    panel_alt = "#222222"
    bezel = "#2a2a2a"
    bezel_hi = "#3c3c3c"
    bezel_lo = "#1c1c1c"
    key = "#111111"
    accent = DEFAULT_ACCENT
    accent_dim = "#b07814"
    text = DEFAULT_TEXT
    muted = "#8b8b8b"
    danger = "#e15a5a"
    ok = "#3dcc7a"
    border = "#2f2f2f"
    input = "#161616"
    on_accent = "#1a1204"


MIME_ACTION = "application/x-popstream-action"
MIME_KEY = "application/x-popstream-key"


def _clamp_byte(value: int) -> int:
    return max(0, min(255, int(value)))


def shade(hex_color: str, factor: float) -> str:
    color = QColor(hex_color)
    if not color.isValid():
        color = QColor(DEFAULT_ACCENT)
    return QColor(
        _clamp_byte(color.red() * factor),
        _clamp_byte(color.green() * factor),
        _clamp_byte(color.blue() * factor),
    ).name()


def mix(a: str, b: str, amount: float) -> str:
    left = QColor(a)
    right = QColor(b)
    if not left.isValid():
        left = QColor(DEFAULT_TEXT)
    if not right.isValid():
        right = QColor("#000000")
    t = max(0.0, min(1.0, amount))
    return QColor(
        _clamp_byte(left.red() * (1 - t) + right.red() * t),
        _clamp_byte(left.green() * (1 - t) + right.green() * t),
        _clamp_byte(left.blue() * (1 - t) + right.blue() * t),
    ).name()


def contrast_on(hex_color: str) -> str:
    color = QColor(hex_color)
    if not color.isValid():
        return C.text
    luminance = 0.299 * color.red() + 0.587 * color.green() + 0.114 * color.blue()
    return "#1a1204" if luminance > 160 else C.text


def tighten_form(layout: QFormLayout) -> None:
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setHorizontalSpacing(10)
    layout.setVerticalSpacing(8)
    layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
    layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)


def fit_combo(combo: QComboBox) -> QComboBox:
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(18)
    combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return combo


def plain_spin(minimum: int, maximum: int, value: int = 0) -> QSpinBox:
    box = QSpinBox()
    box.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
    box.setRange(minimum, maximum)
    box.setValue(value)
    box.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return box


def apply_accent(hex_color: str) -> str:
    color = QColor(hex_color or DEFAULT_ACCENT)
    if not color.isValid():
        color = QColor(DEFAULT_ACCENT)
    C.accent = color.name()
    C.accent_dim = shade(C.accent, 0.72)
    C.on_accent = contrast_on(C.accent)
    return C.accent


def apply_text(hex_color: str) -> str:
    color = QColor(hex_color or DEFAULT_TEXT)
    if not color.isValid():
        color = QColor(DEFAULT_TEXT)
    C.text = color.name()
    C.muted = mix(C.text, C.bg, 0.45)
    C.on_accent = contrast_on(C.accent)
    return C.text


def stylesheet() -> str:
    return f"""
QWidget {{
    background: {C.bg};
    color: {C.text};
    font-family: "Inter", "Segoe UI", "Ubuntu", sans-serif;
    font-size: 13px;
}}
QMainWindow, QDialog {{
    background: {C.bg};
}}
QLabel {{
    background: transparent;
}}
QLabel#hint, QLabel#muted {{
    color: {C.muted};
}}
QFrame#panel, QWidget#panel {{
    background: {C.panel};
    border: 1px solid {C.border};
    border-radius: 12px;
}}
QFrame#topBar {{
    background: {C.panel};
    border: 1px solid {C.border};
    border-radius: 12px;
}}
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit, QKeySequenceEdit {{
    background: {C.input};
    border: 1px solid {C.border};
    border-radius: 8px;
    padding: 6px 10px;
    min-height: 32px;
    selection-background-color: {C.accent_dim};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QKeySequenceEdit:focus {{
    border: 1px solid {C.accent};
}}
QComboBox {{
    padding-right: 28px;
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox QAbstractItemView {{
    min-width: 200px;
    padding: 4px;
}}
QSpinBox::up-button, QSpinBox::down-button {{
    width: 0px;
    border: none;
    background: transparent;
}}
QSpinBox::up-arrow, QSpinBox::down-arrow {{
    width: 0px;
    height: 0px;
}}
QListWidget {{
    background: {C.input};
    border: 1px solid {C.border};
    border-radius: 8px;
    padding: 4px;
}}
QListWidget::item {{
    padding: 8px 10px;
    border-radius: 6px;
}}
QListWidget::item:selected {{
    background: {C.accent};
    color: {C.on_accent};
}}
QPushButton {{
    background: {C.panel_alt};
    border: 1px solid {C.border};
    border-radius: 8px;
    padding: 7px 12px;
}}
QPushButton:hover {{
    border-color: {C.accent_dim};
}}
QPushButton:pressed {{
    background: #1a1a1a;
}}
QPushButton:checked {{
    background: {C.accent};
    color: {C.on_accent};
    border-color: {C.accent};
    font-weight: 600;
}}
QPushButton#accent {{
    background: {C.accent};
    color: {C.on_accent};
    font-weight: 600;
    border: none;
}}
QPushButton#danger {{
    color: {C.danger};
    border-color: #5a2a2a;
}}
QPushButton#categoryHeader {{
    background: transparent;
    border: none;
    color: {C.muted};
    text-align: left;
    padding: 8px 2px 2px 0;
    font-size: 10px;
    letter-spacing: 1px;
    font-weight: 600;
}}
QPushButton#categoryHeader:hover {{
    color: {C.text};
    border: none;
    background: transparent;
}}
QPushButton#categoryHeader:pressed {{
    background: transparent;
    border: none;
}}
QPushButton#categoryHeader:checked {{
    background: transparent;
    color: {C.muted};
    border: none;
}}
QPushButton#categoryHeaderNested {{
    background: transparent;
    border: none;
    color: {C.muted};
    text-align: left;
    padding: 4px 2px 2px 0;
    font-size: 10px;
    letter-spacing: 0.6px;
    font-weight: 600;
}}
QPushButton#categoryHeaderNested:hover {{
    color: {C.text};
    border: none;
    background: transparent;
}}
QPushButton#categoryHeaderNested:pressed {{
    background: transparent;
    border: none;
}}
QPushButton#categoryHeaderNested:checked {{
    background: transparent;
    color: {C.muted};
    border: none;
}}
QPushButton#flat {{
    background: transparent;
    border: none;
    padding: 4px 8px;
}}
QPushButton#swatch {{
    border: 1px solid {C.border};
    border-radius: 6px;
    padding: 0;
    min-width: 36px;
    max-width: 36px;
    min-height: 22px;
    max-height: 22px;
}}
QScrollArea {{
    border: none;
    background: transparent;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 4px;
}}
QScrollBar::handle:vertical {{
    background: #3a3a3a;
    border-radius: 4px;
    min-height: 24px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QSplitter::handle {{
    background: {C.bg};
    width: 8px;
}}
QSlider::groove:horizontal {{
    height: 4px;
    background: #333;
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 14px;
    height: 14px;
    margin: -5px 0;
    border-radius: 7px;
    background: {C.accent};
}}
QCheckBox {{
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {C.border};
    background: {C.input};
}}
QCheckBox::indicator:checked {{
    background: {C.accent};
    border-color: {C.accent};
}}
QMenu {{
    background: {C.panel};
    border: 1px solid {C.border};
    padding: 6px;
}}
QMenu::item {{
    padding: 6px 16px;
    border-radius: 6px;
}}
QMenu::item:selected {{
    background: {C.panel_alt};
}}
QStatusBar {{
    background: {C.bg};
    color: {C.muted};
}}
QToolTip {{
    background: {C.panel_alt};
    color: {C.text};
    border: 1px solid {C.border};
    padding: 4px 8px;
}}
QGroupBox {{
    border: 1px solid {C.border};
    border-radius: 10px;
    margin-top: 12px;
    padding: 12px 8px 8px 8px;
    color: {C.muted};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
}}
QTabWidget::pane {{
    border: 1px solid {C.border};
    border-radius: 8px;
    background: {C.panel};
    top: -1px;
    padding: 8px;
}}
QTabBar::tab {{
    background: {C.panel_alt};
    color: {C.muted};
    border: 1px solid {C.border};
    border-bottom: none;
    border-top-left-radius: 8px;
    border-top-right-radius: 8px;
    padding: 8px 16px;
    margin-right: 4px;
}}
QTabBar::tab:selected {{
    background: {C.panel};
    color: {C.text};
    font-weight: 600;
}}
QTabBar::tab:hover {{
    color: {C.text};
}}
"""


def apply_app_theme(app: QApplication | None = None) -> None:
    app = app or QApplication.instance()
    if app is None:
        return
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(C.bg))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(C.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(C.input))
    palette.setColor(QPalette.ColorRole.Text, QColor(C.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(C.panel_alt))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(C.text))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(C.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(C.on_accent))
    app.setPalette(palette)
    app.setStyleSheet(stylesheet())


class ColorSwatch(QPushButton):
    """Compact color chip. Emits a hex string; empty means “use default” when reset."""

    color_changed = Signal(str)

    def __init__(self, parent=None, allow_reset: bool = False, fallback: str = "accent") -> None:
        super().__init__(parent)
        self.setObjectName("swatch")
        self._color = ""
        self._allow_reset = allow_reset
        self._fallback = fallback
        self.setToolTip("Choose color" + (" — right-click to use default" if allow_reset else ""))
        self.clicked.connect(self._pick)
        self._paint_chip()

    def color(self) -> str:
        return self._color

    def set_color(self, hex_color: str) -> None:
        self._color = hex_color or ""
        self._paint_chip()

    def _fallback_color(self) -> str:
        if self._fallback == "text":
            return C.text
        if self._fallback == "accent":
            return C.accent
        color = QColor(self._fallback)
        return color.name() if color.isValid() else C.accent

    def _paint_chip(self) -> None:
        shown = self._color or self._fallback_color()
        self.setStyleSheet(
            f"QPushButton#swatch {{ background: {shown}; border: 1px solid {C.border}; border-radius: 6px; }}"
        )

    def _pick(self) -> None:
        initial = QColor(self._color or self._fallback_color())
        chosen = QColorDialog.getColor(initial, self.window(), "Choose color")
        if not chosen.isValid():
            return
        self._color = chosen.name()
        self._paint_chip()
        self.color_changed.emit(self._color)

    def mousePressEvent(self, event) -> None:
        from PySide6.QtCore import Qt

        if self._allow_reset and event.button() == Qt.MouseButton.RightButton:
            self._color = ""
            self._paint_chip()
            self.color_changed.emit("")
            event.accept()
            return
        super().mousePressEvent(event)
