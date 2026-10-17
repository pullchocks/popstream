from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QDate, Qt, QTime, QTimer
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QWidget

from popstream.core.lockscreen import glyph_tile
from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import fit_combo, tighten_form

TIME_STYLES = (
    ("12", "12-hour digits (4 keys)"),
    ("12colon", "12-hour with colon (5 keys)"),
    ("24", "24-hour digits (4 keys)"),
    ("24colon", "24-hour with colon (5 keys)"),
    ("ampm", "AM / PM (1 key)"),
)
DATE_STYLES = (
    ("monthday", "Month and day (5 keys)"),
    ("weekday", "Weekday (3 keys)"),
    ("month", "Month (3 keys)"),
    ("day", "Day (2 keys)"),
)


def time_face(when: datetime, style: str) -> str:
    minutes = f"{when.minute:02d}"
    if style == "12colon":
        hour = when.hour % 12 or 12
        return f"{hour:2d}:{minutes}"
    if style == "24colon":
        return f"{when.hour:02d}:{minutes}"
    if style == "24":
        return f"{when.hour:02d}{minutes}"
    if style == "ampm":
        return "AM" if when.hour < 12 else "PM"
    hour = when.hour % 12 or 12
    return f"{hour:2d}{minutes}"


def date_face(when: datetime, style: str) -> str:
    month = when.strftime("%b").upper()
    day = f"{when.day:02d}"
    if style == "weekday":
        return when.strftime("%a").upper()
    if style == "month":
        return month
    if style == "day":
        return day
    return month + day


def face_glyph(text: str, position: int) -> str:
    if position < 0 or position >= len(text):
        return ""
    ch = text[position]
    return "" if ch == " " else ch


def resolve_position(ctx: ActionContext, settings: dict) -> int:
    raw = str(settings.get("position") or "auto")
    if raw in {"", "auto"}:
        spec = ctx.current_spec()
        columns = int(getattr(spec, "columns", 0) or 0) if spec is not None else 0
        if columns > 0:
            return ctx.key % columns
        return 0
    try:
        return max(0, int(raw) - 1)
    except (TypeError, ValueError):
        return 0


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


class _FaceAction(Action):
    kind = "time"
    default_style = "12"

    def __init__(self) -> None:
        self._timer: QTimer | None = None
        self._ctx: ActionContext | None = None
        self._last: tuple | None = None

    def will_appear(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._last = None
        if self._timer is None:
            self._timer = QTimer()
            self._timer.timeout.connect(self._tick)
        self._timer.start(1000)
        self._tick()

    def will_disappear(self, ctx: ActionContext) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._ctx = None
        self._last = None

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._last = None
        self._tick()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _FaceInspector(ctx, parent, date=self.kind == "date")

    def _tick(self) -> None:
        if self._ctx is None:
            return
        now = datetime.now()
        settings = self._ctx.settings
        style = str(settings.get("style") or self.default_style)
        text = date_face(now, style) if self.kind == "date" else time_face(now, style)
        if style == "ampm":
            glyph = text
        else:
            glyph = face_glyph(text, resolve_position(self._ctx, settings))
        blink = glyph == ":" and (int(now.second) % 2) == 0
        spec = self._ctx.current_spec()
        size = int(getattr(spec, "key_size", 0) or 0) if spec is not None else 0
        size = max(64, size or 72)
        stamp = (glyph, blink, size)
        if stamp == self._last:
            return
        self._last = stamp
        self._ctx.set_title("")
        self._ctx.set_image(glyph_tile(size, glyph, blink))


class BigTimeAction(_FaceAction):
    kind = "time"
    default_style = "12"


class BigDateAction(_FaceAction):
    kind = "date"
    default_style = "monthday"


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


class _FaceInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None, *, date: bool = False) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._date = date
        layout = QFormLayout(self)
        tighten_form(layout)
        if date:
            hint = QLabel(
                "Drop several of these in a row. Each key is one letter of the date, "
                "same big type as the lock screen. Auto uses this key's column."
            )
            styles = DATE_STYLES
            fallback = "monthday"
        else:
            hint = QLabel(
                "Drop several of these in a row for a lock-screen time, like four keys for 10 15. "
                "Auto uses this key's column so a row of four is the digits."
            )
            styles = TIME_STYLES
            fallback = "12"
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.style = fit_combo(QComboBox())
        for value, label in styles:
            self.style.addItem(label, value)
        current = str(ctx.settings.get("style") or fallback)
        index = self.style.findData(current)
        self.style.setCurrentIndex(index if index >= 0 else 0)
        self.style.currentIndexChanged.connect(self._save)
        self.position = fit_combo(QComboBox())
        self.position.addItem("Auto (this column)", "auto")
        for i in range(1, 6):
            self.position.addItem(str(i), str(i))
        pos = str(ctx.settings.get("position") or "auto")
        index = self.position.findData(pos)
        self.position.setCurrentIndex(index if index >= 0 else 0)
        self.position.currentIndexChanged.connect(self._save)
        layout.addRow(hint)
        layout.addRow("Layout", self.style)
        layout.addRow("This key", self.position)
        self._sync_position()

    def _sync_position(self) -> None:
        self.position.setEnabled(self.style.currentData() != "ampm")

    def _save(self) -> None:
        self._sync_position()
        settings = dict(self._ctx.settings)
        fallback = "monthday" if self._date else "12"
        settings["style"] = self.style.currentData() or fallback
        settings["position"] = self.position.currentData() or "auto"
        self._ctx.set_settings(settings)


class ClockPlugin(Plugin):
    id = "com.popstream.clock"
    name = "Clock"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo("clock", "Clock", "Clock", "Live time on the key", "clock"),
            ActionInfo("date", "Date", "Clock", "Live date on the key", "date"),
            ActionInfo(
                "big-time",
                "Big Time",
                "Clock",
                "One digit of a lock-style time you place across keys",
                "clock",
                defaults={"style": "12", "position": "auto"},
            ),
            ActionInfo(
                "big-date",
                "Big Date",
                "Clock",
                "One letter of a lock-style date you place across keys",
                "date",
                defaults={"style": "monthday", "position": "auto"},
            ),
        ]

    def create_action(self, action_id: str) -> Action | None:
        if action_id == "clock":
            return ClockAction()
        if action_id == "date":
            return DateAction()
        if action_id == "big-time":
            return BigTimeAction()
        if action_id == "big-date":
            return BigDateAction()
        return None
