"""Lock-screen overlay: date/time across keys, a still image, or a screensaver."""

from __future__ import annotations

import math
import os
import subprocess
import time
from datetime import datetime
from typing import Any

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)

from popstream.core.specs import DeviceSpec
from popstream.core.store import load_settings, save_settings
from popstream.ui.theme import C

LOCK_MODES = ("clock", "image", "screensaver")
_DBUS_TIMEOUT_MS = 400


def _dbus_call(bus, message):
    from PySide6.QtDBus import QDBus

    return bus.call(message, QDBus.CallMode.Block, _DBUS_TIMEOUT_MS)


def _dbus_path(value: Any) -> str:
    if value is None:
        return ""
    path = getattr(value, "path", None)
    if callable(path):
        text = str(path())
        return text if text.startswith("/") else ""
    if isinstance(value, str) and value.startswith("/"):
        return value
    return ""


def _dbus_bool(value: Any) -> bool | None:
    if value is None:
        return None
    inner = getattr(value, "value", None)
    if callable(inner):
        try:
            value = inner()
        except Exception:
            return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def _runtime_dir() -> str:
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


def _session_id() -> str:
    return (os.environ.get("XDG_SESSION_ID") or "").strip()


def cosmic_lock_paths(runtime: str | None = None, session_id: str | None = None) -> list[str]:
    """Lockfiles cosmic-greeter creates while the COSMIC session is locked."""
    root = runtime or _runtime_dir()
    sid = session_id if session_id is not None else _session_id()
    paths: list[str] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        if path and path not in seen:
            seen.add(path)
            paths.append(path)

    if sid:
        add(os.path.join(root, f"cosmic-greeter-{sid}.lock"))
    try:
        for name in os.listdir(root):
            if name.startswith("cosmic-greeter-") and name.endswith(".lock"):
                add(os.path.join(root, name))
    except OSError:
        pass
    return paths


def cosmic_session_locked(runtime: str | None = None, session_id: str | None = None) -> bool:
    return any(os.path.exists(path) for path in cosmic_lock_paths(runtime, session_id))


def default_lock_config() -> dict[str, Any]:
    return {
        "enabled": True,
        "mode": "clock",
        "image": "",
        "hour12": True,
    }


def normalize_lock_config(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    mode = data.get("mode", "clock")
    if mode not in LOCK_MODES:
        mode = "clock"
    return {
        "enabled": bool(data.get("enabled", True)),
        "mode": mode,
        "image": str(data.get("image") or ""),
        "hour12": bool(data.get("hour12", True)),
    }


def load_lock_config() -> dict[str, Any]:
    return normalize_lock_config(load_settings().get("lock"))


def save_lock_config(config: dict[str, Any]) -> dict[str, Any]:
    normalized = normalize_lock_config(config)
    settings = load_settings()
    settings["lock"] = normalized
    save_settings(settings)
    return normalized


def clock_cells(
    columns: int,
    rows: int,
    when: datetime,
    hour12: bool = True,
) -> list[str]:
    """One string per key, row-major. Empty string = blank key."""
    cells = [""] * max(1, columns * rows)
    if columns < 1 or rows < 1:
        return cells

    month = when.strftime("%b").upper()
    day = f"{when.day:02d}"
    date = month + day
    minutes = f"{when.minute:02d}"
    if hour12:
        hour = when.hour % 12 or 12
        hour_s = f"{hour:2d}"
        ampm = "AM" if when.hour < 12 else "PM"
    else:
        hour_s = f"{when.hour:02d}"
        ampm = ""
    time_s = f"{hour_s}:{minutes}"

    if rows >= 3 and columns >= 5:
        date_row = 0 if rows == 3 else 1
        time_row = 1 if rows == 3 else 2
        am_row = rows - 1
        if rows >= 4:
            _place(cells, columns, 0, when.strftime("%a").upper(), "center")
        _place(cells, columns, date_row, date, "center")
        _place(cells, columns, time_row, time_s, "center")
        if hour12:
            _place(cells, columns, am_row, ampm, "right")
        return cells

    if rows >= 2 and columns >= 4:
        compact = f"{hour_s.replace(' ', '')}{minutes}"
        if len(compact) > columns:
            compact = compact[-columns:]
        _place(cells, columns, 0, compact, "center")
        bottom = month[: max(0, columns - (2 if hour12 else 0))]
        _place(cells, columns, 1, bottom, "left")
        if hour12:
            _place(cells, columns, 1, ampm, "right")
        return cells

    if rows >= 2:
        _place(cells, columns, 0, time_s.replace(" ", "")[:columns], "center")
        _place(cells, columns, 1, (ampm or date)[:columns], "right" if hour12 else "center")
        return cells

    _place(cells, columns, 0, time_s.replace(" ", "")[:columns], "center")
    return cells


def _place(cells: list[str], columns: int, row: int, text: str, align: str) -> None:
    chars = list(text)
    if not chars:
        return
    if len(chars) > columns:
        chars = chars[:columns]
    if align == "right":
        start = columns - len(chars)
    elif align == "left":
        start = 0
    else:
        start = (columns - len(chars)) // 2
    for i, ch in enumerate(chars):
        idx = row * columns + start + i
        if 0 <= idx < len(cells):
            cells[idx] = "" if ch == " " else ch


def render_lock_tiles(spec: DeviceSpec, config: dict[str, Any], now: datetime | None = None) -> list[QImage]:
    if not spec.has_lcd:
        return []
    cfg = normalize_lock_config(config)
    mode = cfg["mode"]
    if mode == "image":
        tiles = _render_image(spec, cfg.get("image", ""))
        if tiles:
            return tiles
        mode = "clock"
    if mode == "screensaver":
        return _render_screensaver(spec, time.monotonic())
    return _render_clock(spec, now or datetime.now(), cfg.get("hour12", True))


def _render_clock(spec: DeviceSpec, when: datetime, hour12: bool) -> list[QImage]:
    cells = clock_cells(spec.columns, spec.rows, when, hour12)
    blink = (int(when.second) % 2) == 0
    return [_glyph_tile(spec.key_size, cell, blink) for cell in cells]


def _glyph_tile(size: int, text: str, blink: bool) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#101010"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    clip = QPainterPath()
    clip.addRoundedRect(QRectF(0, 0, size, size), size * 0.12, size * 0.12)
    painter.setClipPath(clip)
    if not text:
        painter.end()
        return image
    if text == ":":
        color = QColor(C.text if blink else C.muted)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        r = max(3.0, size * 0.08)
        cx = size / 2
        painter.drawEllipse(QRectF(cx - r, size * 0.32 - r, r * 2, r * 2))
        painter.drawEllipse(QRectF(cx - r, size * 0.68 - r, r * 2, r * 2))
        painter.end()
        return image
    font = QFont("Inter, Ubuntu, Segoe UI, Sans Serif")
    font.setPixelSize(int(size * (0.38 if len(text) > 1 else 0.72)))
    font.setWeight(QFont.Weight.Bold)
    painter.setFont(font)
    painter.setPen(QColor(C.text))
    painter.drawText(QRectF(0, 0, size, size), int(Qt.AlignmentFlag.AlignCenter), text)
    painter.end()
    return image


def _render_image(spec: DeviceSpec, path: str) -> list[QImage] | None:
    if not path:
        return None
    source = QImage(path)
    if source.isNull():
        return None
    size = spec.key_size
    width = spec.columns * size
    height = spec.rows * size
    scaled = source.scaled(
        width,
        height,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )
    x = max(0, (scaled.width() - width) // 2)
    y = max(0, (scaled.height() - height) // 2)
    cropped = scaled.copy(x, y, width, height)
    return _slice(cropped, spec)


def _render_screensaver(spec: DeviceSpec, t: float) -> list[QImage]:
    size = spec.key_size
    width = spec.columns * size
    height = spec.rows * size
    canvas = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(QColor("#070709"))
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    accent = QColor(C.accent)
    for i in range(3):
        cx = (0.5 + 0.38 * math.sin(t * (0.35 + i * 0.13) + i * 1.7)) * width
        cy = (0.5 + 0.36 * math.cos(t * (0.28 + i * 0.11) + i * 2.2)) * height
        radius = min(width, height) * (0.42 + 0.08 * math.sin(t * 0.6 + i))
        gradient = QRadialGradient(cx, cy, radius)
        glow = QColor(accent)
        glow.setAlpha(90 - i * 18)
        gradient.setColorAt(0.0, glow)
        gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(canvas.rect(), QBrush(gradient))

    cx, cy = width / 2, height / 2
    radius = min(width, height) * 0.36
    painter.setPen(QPen(QColor(C.accent), max(2.0, size * 0.06)))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(cx - radius, cy - radius, radius * 2, radius * 2))

    now = datetime.now()
    second = now.second + now.microsecond / 1_000_000
    minute = now.minute + second / 60
    hour = (now.hour % 12) + minute / 60
    _hand(painter, cx, cy, radius * 0.52, hour / 12 * 360, max(2.5, size * 0.08), C.accent)
    _hand(painter, cx, cy, radius * 0.72, minute / 60 * 360, max(2.0, size * 0.06), C.text)
    _hand(painter, cx, cy, radius * 0.82, second / 60 * 360, max(1.4, size * 0.035), C.danger)

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(C.accent))
    hub = max(3.0, size * 0.07)
    painter.drawEllipse(QRectF(cx - hub, cy - hub, hub * 2, hub * 2))
    painter.end()
    return _slice(canvas, spec)


def _hand(painter: QPainter, cx: float, cy: float, length: float, degrees: float, width: float, color: str) -> None:
    angle = math.radians(degrees - 90)
    painter.setPen(QPen(QColor(color), width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(
        int(cx),
        int(cy),
        int(cx + math.cos(angle) * length),
        int(cy + math.sin(angle) * length),
    )


def _slice(canvas: QImage, spec: DeviceSpec) -> list[QImage]:
    size = spec.key_size
    tiles: list[QImage] = []
    for row in range(spec.rows):
        for col in range(spec.columns):
            tiles.append(canvas.copy(col * size, row * size, size, size))
    return tiles


class LockMonitor(QObject):
    """Watch screen lock on GNOME, KDE, and COSMIC.

    COSMIC never sets logind LockedHint and does not implement
    org.freedesktop.ScreenSaver.GetActive. It does emit logind Session.Lock
    (not Unlock) and writes $XDG_RUNTIME_DIR/cosmic-greeter-$XDG_SESSION_ID.lock
    while the greeter is up. Apps launched from Cursor or systemd --user also
    fail GetSessionByPID, so we resolve the seat session by XDG_SESSION_ID.
    """

    locked_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._locked = False
        self._session_path = ""
        self._awaiting_lockfile = False
        self._lock_armed_until = 0.0
        self._screensaver_get_active = True
        self._connect_signals()
        self._poll = QTimer(self)
        self._poll.setInterval(400)
        self._poll.timeout.connect(self._poll_now)
        self._poll.start()
        QTimer.singleShot(200, self._poll_now)

    @property
    def locked(self) -> bool:
        return self._locked

    def _set_locked(self, locked: bool) -> None:
        if locked is self._locked:
            return
        self._locked = locked
        self.locked_changed.emit(locked)

    def _connect_signals(self) -> None:
        try:
            from PySide6.QtCore import SLOT
            from PySide6.QtDBus import QDBusConnection
        except Exception:
            return
        try:
            session = QDBusConnection.sessionBus()
            if session.isConnected():
                for service, path, interface in (
                    ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver"),
                    ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver"),
                    ("org.gnome.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.gnome.ScreenSaver"),
                ):
                    session.connect(
                        service,
                        path,
                        interface,
                        "ActiveChanged",
                        self,
                        SLOT("_on_screensaver_active(bool)"),
                    )
            self._bind_logind()
        except Exception:
            return

    def _bind_logind(self) -> None:
        try:
            from PySide6.QtCore import SLOT
            from PySide6.QtDBus import QDBusConnection
        except Exception:
            return
        try:
            system = QDBusConnection.systemBus()
            if not system.isConnected():
                return
            path = self._resolve_session_path(system)
            if not path:
                return
            self._session_path = path
            system.connect(
                "org.freedesktop.login1",
                path,
                "org.freedesktop.login1.Session",
                "Lock",
                self,
                SLOT("_on_session_lock()"),
            )
            system.connect(
                "org.freedesktop.login1",
                path,
                "org.freedesktop.login1.Session",
                "Unlock",
                self,
                SLOT("_on_session_unlock()"),
            )
        except Exception:
            return

    def _resolve_session_path(self, system) -> str:
        sid = _session_id()
        if sid:
            path = self._login1_call(system, "GetSession", [sid])
            if path:
                return path
        path = self._login1_call(system, "GetSessionByPID", [os.getpid()])
        if path:
            return path
        return self._login1_list_path(system)

    def _login1_call(self, system, method: str, arguments: list[Any]) -> str:
        try:
            from PySide6.QtDBus import QDBusMessage
        except Exception:
            return ""
        msg = QDBusMessage.createMethodCall(
            "org.freedesktop.login1",
            "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager",
            method,
        )
        msg.setArguments(arguments)
        reply = _dbus_call(system, msg)
        if reply.errorName():
            return ""
        args = reply.arguments()
        return _dbus_path(args[0]) if args else ""

    def _login1_list_path(self, system) -> str:
        try:
            from PySide6.QtDBus import QDBusMessage
        except Exception:
            return ""
        msg = QDBusMessage.createMethodCall(
            "org.freedesktop.login1",
            "/org/freedesktop/login1",
            "org.freedesktop.login1.Manager",
            "ListSessions",
        )
        reply = _dbus_call(system, msg)
        if reply.errorName():
            return ""
        args = reply.arguments()
        rows = args[0] if args else []
        uid = os.getuid()
        sid = _session_id()
        fallback = ""
        for row in rows or []:
            try:
                session_id, session_uid, _user, _seat, path = row
            except (TypeError, ValueError):
                continue
            resolved = _dbus_path(path)
            if not resolved:
                continue
            if sid and str(session_id) == sid:
                return resolved
            if int(session_uid) == uid and not fallback:
                fallback = resolved
        return fallback

    @Slot(bool)
    def _on_screensaver_active(self, active: bool) -> None:
        if active:
            self._set_locked(True)
        else:
            self._poll_now()

    @Slot()
    def _on_session_lock(self) -> None:
        self._awaiting_lockfile = True
        self._lock_armed_until = time.monotonic() + 8
        self._set_locked(True)

    @Slot()
    def _on_session_unlock(self) -> None:
        self._awaiting_lockfile = False
        self._lock_armed_until = 0.0
        self._poll_now()

    def _poll_now(self) -> None:
        file_locked = cosmic_session_locked()
        if file_locked:
            self._awaiting_lockfile = False
            self._lock_armed_until = 0.0
        locked = file_locked
        if not locked:
            for reader in (self._gnome_active, self._logind_hint, self._loginctl_hint):
                try:
                    value = reader()
                except Exception:
                    value = None
                if value is True:
                    locked = True
                    break
        if locked:
            self._set_locked(True)
            return
        if self._awaiting_lockfile and time.monotonic() < self._lock_armed_until:
            return
        self._awaiting_lockfile = False
        self._set_locked(False)

    def _gnome_active(self) -> bool | None:
        if not self._screensaver_get_active:
            return None
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusMessage
        except Exception:
            self._screensaver_get_active = False
            return None
        bus = QDBusConnection.sessionBus()
        if not bus.isConnected():
            return None
        for service, path, interface in (
            ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver"),
            ("org.freedesktop.ScreenSaver", "/org/freedesktop/ScreenSaver", "org.freedesktop.ScreenSaver"),
        ):
            msg = QDBusMessage.createMethodCall(service, path, interface, "GetActive")
            reply = _dbus_call(bus, msg)
            if reply.errorName():
                continue
            args = reply.arguments()
            if args:
                parsed = _dbus_bool(args[0])
                if parsed is not None:
                    return parsed
        self._screensaver_get_active = False
        return None

    def _logind_hint(self) -> bool | None:
        if not self._session_path:
            return None
        try:
            from PySide6.QtDBus import QDBusConnection, QDBusMessage
        except Exception:
            return None
        system = QDBusConnection.systemBus()
        if not system.isConnected():
            return None
        msg = QDBusMessage.createMethodCall(
            "org.freedesktop.login1",
            self._session_path,
            "org.freedesktop.DBus.Properties",
            "Get",
        )
        msg.setArguments(["org.freedesktop.login1.Session", "LockedHint"])
        reply = _dbus_call(system, msg)
        if reply.errorName():
            return None
        args = reply.arguments()
        return _dbus_bool(args[0]) if args else None

    def _loginctl_hint(self) -> bool | None:
        session = _session_id()
        if not session:
            return None
        try:
            result = subprocess.run(
                ["loginctl", "show-session", session, "-p", "LockedHint"],
                capture_output=True,
                text=True,
                timeout=0.8,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return None
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            if line.startswith("LockedHint="):
                return line.split("=", 1)[1].strip().lower() in {"yes", "true", "1"}
        return None
