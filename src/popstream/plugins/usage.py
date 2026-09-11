from __future__ import annotations

import base64
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QWidget

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import fit_combo, tighten_form

USAGE_URL = "https://cursor.com/api/usage-summary"
DASHBOARD_URL = "https://cursor.com/dashboard/usage"
CACHE_TTL_S = 3600
TICK_MS = 60_000
LOW_PERCENT = 20
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _state_db() -> Path:
    return Path.home() / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb"


def _agent_auth() -> Path:
    return Path.home() / ".config" / "cursor" / "auth.json"


def _jwt_claims(token: str) -> dict[str, Any]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1] + "=" * (-len(parts[1]) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, json.JSONDecodeError, UnicodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _token_from_row(row: tuple | None) -> str:
    token = row[0] if row else ""
    if isinstance(token, bytes):
        token = token.decode("utf-8", "ignore")
    return str(token or "").strip()


def _read_access_token() -> str:
    db = _state_db()
    if db.is_file():
        try:
            # WAL databases fail with mode=ro from another process (Cursor holds a write lock).
            con = sqlite3.connect(str(db), timeout=8)
            try:
                con.execute("PRAGMA query_only=ON")
                row = con.execute(
                    "SELECT value FROM ItemTable WHERE key = ?",
                    ("cursorAuth/accessToken",),
                ).fetchone()
            finally:
                con.close()
        except sqlite3.Error:
            row = None
        token = _token_from_row(row)
        if token:
            return token
    auth = _agent_auth()
    if auth.is_file():
        try:
            data = json.loads(auth.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        token = str((data or {}).get("accessToken") or "").strip()
        if token:
            return token
    return ""


def _session_cookie(token: str) -> str:
    sub = str(_jwt_claims(token).get("sub") or "")
    user_id = sub.split("|", 1)[1] if "|" in sub else sub
    user_id = user_id.strip()
    if not user_id:
        raise ValueError("Cursor session is missing a user id")
    return f"{quote(user_id, safe='')}%3A%3A{token}"


@dataclass
class UsageSnapshot:
    remaining_pct: int | None = None
    used_pct: int | None = None
    unlimited: bool = False
    plan: str = ""
    error: str | None = None
    fetched_at: float = 0.0

    def title(self, mode: str = "remaining") -> str:
        if self.error == "Loading":
            return "…"
        if self.error:
            return "—"
        if self.unlimited:
            return "∞" if mode != "used" else "0%"
        if mode == "used":
            if self.used_pct is None:
                return "—"
            return f"{max(0, self.used_pct)}%"
        if self.remaining_pct is None:
            return "—"
        return f"{max(0, self.remaining_pct)}%"

    def low(self) -> bool:
        return (not self.unlimited) and self.remaining_pct is not None and self.remaining_pct <= LOW_PERCENT


def _percent_from_message(message: str) -> float | None:
    if "%" not in message:
        return None
    before = message.split("%", 1)[0]
    digits = ""
    for char in reversed(before):
        if char.isdigit() or char == ".":
            digits = char + digits
        elif digits:
            break
    try:
        return float(digits)
    except ValueError:
        return None


def parse_summary(data: dict[str, Any]) -> UsageSnapshot:
    plan_name = str(data.get("membershipType") or "Cursor").strip() or "Cursor"
    plan_name = plan_name[:1].upper() + plan_name[1:]
    if data.get("isUnlimited"):
        return UsageSnapshot(remaining_pct=100, used_pct=0, unlimited=True, plan=plan_name)
    individual = data.get("individualUsage") if isinstance(data.get("individualUsage"), dict) else {}
    plan = individual.get("plan") if isinstance(individual.get("plan"), dict) else {}
    used_pct: float | None = None
    # Spending's "Cursor Models" bar is autoPercentUsed. Do not let remaining/limit
    # overwrite that; those fields are often a different (empty) quota.
    for key in ("autoPercentUsed", "totalPercentUsed", "apiPercentUsed"):
        value = plan.get(key)
        if isinstance(value, (int, float)):
            used_pct = float(value)
            break
    if used_pct is None:
        used_pct = _percent_from_message(str(data.get("autoModelSelectedDisplayMessage") or ""))
    if used_pct is None:
        remaining = plan.get("remaining")
        limit = plan.get("limit")
        used = plan.get("used")
        if (
            remaining is None
            and isinstance(used, (int, float))
            and isinstance(limit, (int, float))
        ):
            remaining = float(limit) - float(used)
        if isinstance(remaining, (int, float)) and isinstance(limit, (int, float)) and limit > 0:
            used_pct = 100.0 - (100.0 * float(remaining) / float(limit))
    if used_pct is None:
        return UsageSnapshot(error="No usage numbers", plan=plan_name)
    remaining_pct = max(0, int(round(100.0 - float(used_pct))))
    return UsageSnapshot(
        remaining_pct=remaining_pct,
        used_pct=max(0, int(round(float(used_pct)))),
        unlimited=False,
        plan=plan_name,
    )


def _get_summary(cookie: str) -> tuple[int, str]:
    request = urllib.request.Request(
        USAGE_URL,
        headers={
            "Cookie": f"WorkosCursorSessionToken={cookie}",
            "Origin": "https://cursor.com",
            "Referer": "https://cursor.com/dashboard",
            "User-Agent": BROWSER_UA,
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            return response.getcode() or 200, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError):
        return 0, ""


def fetch_usage() -> UsageSnapshot:
    token = _read_access_token()
    if not token:
        return UsageSnapshot(error="Sign in to Cursor")
    try:
        encoded = _session_cookie(token)
    except ValueError:
        return UsageSnapshot(error="Sign in to Cursor")
    user_id = unquote(encoded.split("%3A%3A", 1)[0])
    cookies = [encoded, f"{user_id}::{token}"]
    status = 0
    raw = ""
    for cookie in dict.fromkeys(cookies):
        status, raw = _get_summary(cookie)
        if status == 200:
            break
        if status in {401, 403}:
            continue
        return UsageSnapshot(error="Cursor usage unavailable")
    else:
        if status in {401, 403}:
            return UsageSnapshot(error="Sign in to Cursor")
        return UsageSnapshot(error="Cursor usage unavailable")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return UsageSnapshot(error="Cursor usage unavailable")
    if not isinstance(payload, dict):
        return UsageSnapshot(error="Cursor usage unavailable")
    snap = parse_summary(payload)
    snap.fetched_at = time.time()
    return snap


class _FetchTask(QRunnable):
    def __init__(self, monitor: "UsageMonitor") -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._monitor = monitor

    def run(self) -> None:
        try:
            snap = fetch_usage()
        except Exception:
            snap = UsageSnapshot(error="Cursor usage unavailable")
        self._monitor.finished.emit(snap)


class UsageMonitor(QObject):
    finished = Signal(object)
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.snapshot = UsageSnapshot(error="Loading")
        self._lock = threading.Lock()
        self._inflight = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self.finished.connect(self._store, Qt.ConnectionType.QueuedConnection)

    def request(self, *, force: bool = False) -> None:
        now = time.time()
        with self._lock:
            if force:
                if self._inflight:
                    return
            else:
                fresh = (
                    self.snapshot.fetched_at > 0
                    and (now - self.snapshot.fetched_at) < CACHE_TTL_S
                    and not self.snapshot.error
                )
                if fresh or self._inflight:
                    return
            self._inflight = True
        self._pool.start(_FetchTask(self))

    def _store(self, snap: object) -> None:
        if isinstance(snap, UsageSnapshot):
            if not snap.fetched_at:
                snap.fetched_at = time.time()
            self.snapshot = snap
        with self._lock:
            self._inflight = False
        self.changed.emit()


_MONITOR: UsageMonitor | None = None


def monitor() -> UsageMonitor:
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = UsageMonitor()
    return _MONITOR


class CursorUsageAction(Action):
    def __init__(self) -> None:
        self._timer: QTimer | None = None
        self._ctx: ActionContext | None = None
        self._watch = False

    def will_appear(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        host = monitor()
        if not self._watch:
            host.changed.connect(self._on_changed)
            self._watch = True
        if self._timer is None:
            self._timer = QTimer()
            self._timer.timeout.connect(self._tick)
        self._timer.start(TICK_MS)
        self._paint(ctx)
        host.request()

    def will_disappear(self, ctx: ActionContext) -> None:
        if self._timer is not None:
            self._timer.stop()
        if self._watch:
            monitor().changed.disconnect(self._on_changed)
            self._watch = False
        self._ctx = None

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._paint(ctx)

    def key_down(self, ctx: ActionContext) -> None:
        press = str(ctx.settings.get("on_press") or "")
        if not press:
            press = "open" if ctx.settings.get("open_dashboard") else "refresh"
        if press == "open":
            QDesktopServices.openUrl(QUrl(DASHBOARD_URL))
            ctx.show_ok()
            return
        monitor().request(force=True)
        snap = monitor().snapshot
        if snap.error and snap.remaining_pct is None and not snap.unlimited:
            ctx.show_alert()
        else:
            ctx.show_ok()
        self._paint(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _UsageInspector(ctx, parent)

    def _tick(self) -> None:
        monitor().request()
        if self._ctx is not None:
            self._paint(self._ctx)

    def _on_changed(self) -> None:
        if self._ctx is not None:
            self._paint(self._ctx)

    def _paint(self, ctx: ActionContext) -> None:
        snap = monitor().snapshot
        ctx.set_state("danger" if snap.low() else "")
        mode = str(ctx.settings.get("display") or "remaining")
        ctx.set_title(snap.title(mode))


class _UsageInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        hint = QLabel(
            "The key shows included Cursor Models usage as a percent. "
            "It also refreshes on its own about once an hour."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.display = fit_combo(QComboBox())
        self.display.addItem("Remaining", "remaining")
        self.display.addItem("Used", "used")
        display = str(ctx.settings.get("display") or "remaining")
        index = self.display.findData(display)
        self.display.setCurrentIndex(index if index >= 0 else 0)
        self.display.currentIndexChanged.connect(self._save)
        self.press = fit_combo(QComboBox())
        self.press.addItem("Refresh now", "refresh")
        self.press.addItem("Open usage in browser", "open")
        press = str(ctx.settings.get("on_press") or "")
        if not press:
            press = "open" if ctx.settings.get("open_dashboard") else "refresh"
        index = self.press.findData(press)
        self.press.setCurrentIndex(index if index >= 0 else 0)
        self.press.currentIndexChanged.connect(self._save)
        layout.addRow(hint)
        layout.addRow("Show", self.display)
        layout.addRow("When pressed", self.press)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["display"] = self.display.currentData() or "remaining"
        settings["on_press"] = self.press.currentData() or "refresh"
        settings.pop("open_dashboard", None)
        self._ctx.set_settings(settings)


class UsagePlugin(Plugin):
    id = "com.popstream.usage"
    name = "Usage"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo(
                "cursor",
                "Cursor Usage",
                "Usage",
                "Included Cursor usage this billing cycle",
                "usage",
            ),
        ]

    def create_action(self, action_id: str) -> Action | None:
        if action_id == "cursor":
            return CursorUsageAction()
        return None
