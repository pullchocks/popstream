from __future__ import annotations

from PySide6.QtCore import QDate, Qt, QTime, QTimer
from PySide6.QtWidgets import QComboBox, QFormLayout, QWidget

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import fit_combo, tighten_form


class _LiveTitleAction(Action):
    format_key = "format"
    default_format = ""

    def __init__(self) -> None:
        self._timer: QTimer | None = None
        self._ctx: ActionContext | None = None

    def will_appear(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        if self._timer is None:
            self._timer = QTimer()
            self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def will_disappear(self, ctx: ActionContext) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._ctx = None

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._tick()

    def _tick(self) -> None:
        if self._ctx is None:
            return
        fmt = self._ctx.settings.get(self.format_key, self.default_format)
        self._ctx.set_title(self._format(fmt))

    def _format(self, fmt: str) -> str:
        raise NotImplementedError

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _FormatInspector(ctx, self._formats(), parent)


class ClockAction(_LiveTitleAction):
    default_format = "HH:mm"

    def _format(self, fmt: str) -> str:
        return QTime.currentTime().toString(fmt)

    def _formats(self) -> list[tuple[str, str]]:
        return [
            ("HH:mm", "24-hour"),
            ("HH:mm:ss", "24-hour with seconds"),
            ("h:mm AP", "12-hour"),
        ]


class DateAction(_LiveTitleAction):
    default_format = "ddd d MMM"

    def _format(self, fmt: str) -> str:
        return QDate.currentDate().toString(fmt)

    def _formats(self) -> list[tuple[str, str]]:
        return [
            ("ddd d MMM", "Wed 30 Aug"),
            ("yyyy-MM-dd", "ISO"),
            ("MMMM d", "August 30"),
        ]


class _FormatInspector(QWidget):
    def __init__(self, ctx: ActionContext, options: list[tuple[str, str]], parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        current = ctx.settings.get("format", options[0][0])
        for value, label in options:
            self.combo.addItem(label, value)
            self.combo.setItemData(self.combo.count() - 1, value, Qt.ItemDataRole.ToolTipRole)
        index = self.combo.findData(current)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.combo.currentIndexChanged.connect(self._save)
        layout.addRow("Format", self.combo)

    def _save(self) -> None:
        s = dict(self._ctx.settings)
        s["format"] = self.combo.currentData()
        self._ctx.set_settings(s)


class ClockPlugin(Plugin):
    id = "com.popstream.clock"
    name = "Clock"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo("clock", "Clock", "Clock", "Live time on the key", "clock"),
            ActionInfo("date", "Date", "Clock", "Live date on the key", "date"),
        ]

    def create_action(self, action_id: str) -> Action | None:
        if action_id == "clock":
            return ClockAction()
        if action_id == "date":
            return DateAction()
        return None
