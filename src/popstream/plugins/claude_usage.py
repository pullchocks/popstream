"""Claude account usage for the rolling 5 hour window and the weekly window.

Field names come from the Claude Code client schema for GET /api/oauth/usage
(Claude Code 2.1.322): each of ``five_hour`` and ``seven_day`` is an object
with ``utilization`` (fraction of the window used, 0 to 1) and ``resets_at``
(ISO-8601 string, or a unix timestamp the client also accepts). ``seven_day``
is the weekly limit. The client turns utilization into a percent with
``Math.round(utilization * 1000) / 10``, capped at 100.

Claude Desktop writes the same two windows to
``~/.config/Claude/plan-usage-history.json``. There ``fh`` is the 5 hour
window and ``sd`` is the week, and the numbers are already percent points
from 0 to 100, not the OAuth fraction. That file has no reset time. The local
desktop session is sent only to Claude's own usage endpoint, which returns
``resets_at`` for both windows. A fresh history sample still asks for that
when it has no reset. A stale or empty file uses the same call for the
percents as well.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter
from PySide6.QtWidgets import QComboBox, QFileDialog, QFormLayout, QLabel, QLineEdit, QPushButton, QWidget

from popstream.core.plugin import Action, ActionContext
from popstream.ui.theme import C, fit_combo, tighten_form, ui_font

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
USAGE_PAGE = "https://claude.ai/settings/usage"
# A couple of minutes is enough. The official client caches this for an hour;
# the deck should not hit the endpoint on every key tick.
POLL_S = 120
# After a miss, wait longer than the audio client's 10s pause so a dead
# endpoint is not retried on every tick, but still recover within a minute.
FAIL_BACKOFF_S = 60
FORCE_BACKOFF_S = 10
TICK_MS = 60_000
REQUEST_TIMEOUT_S = 8
LOW_PERCENT = 20
TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
WINDOW_LABELS = {"five_hour": "Claude 5h", "seven_day": "Claude Wk"}
# Desktop records a sample about every 15 minutes and keeps older ones as
# history. The newest sample is the current reading for the hour the Claude
# client itself is willing to cache.
DESKTOP_CACHE_FRESH_S = 60 * 60
DESKTOP_HISTORY_NAME = "plan-usage-history.json"
_HISTORY_MAX_BYTES = 2_000_000
_ORG_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
_LIBCRYPTO: ctypes.CDLL | None = None
_LIBCRYPTO_TRIED = False


@dataclass
class UsageWindow:
    used_pct: int | None = None
    remaining_pct: int | None = None
    reset_at: float | None = None


@dataclass
class ClaudeSnapshot:
    five_hour: UsageWindow | None = None
    seven_day: UsageWindow | None = None
    error: str | None = None
    keep_previous: bool = False
    fetched_at: float = 0.0

    def window(self, window_id: str) -> UsageWindow | None:
        if window_id == "seven_day":
            return self.seven_day
        return self.five_hour


def _has_numbers(snap: ClaudeSnapshot) -> bool:
    for window in (snap.five_hour, snap.seven_day):
        if window is not None and window.used_pct is not None:
            return True
    return False


def apply_reading(previous: ClaudeSnapshot, incoming: ClaudeSnapshot, now: float | None = None) -> ClaudeSnapshot:
    """Keep the last good percentages when a request fails."""
    if incoming.keep_previous and _has_numbers(previous):
        return previous
    stamp = incoming.fetched_at or (time.time() if now is None else now)
    return replace(incoming, keep_previous=False, fetched_at=stamp)


def _percent_used(raw: Any) -> int | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    scaled = float(raw) * 100.0
    if scaled <= 0:
        return 0
    if scaled >= 100:
        return 100
    return int(scaled + 0.5)


def _parse_reset(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1e12:
            value /= 1000.0
        if value > 1e9:
            return value
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip().replace("Z", "+00:00")
    try:
        when = datetime.fromisoformat(text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.timestamp()


def _parse_window(raw: Any) -> UsageWindow | None:
    if not isinstance(raw, dict):
        return None
    used = _percent_used(raw.get("utilization"))
    reset_at = _parse_reset(raw.get("resets_at"))
    if used is None:
        if reset_at is None:
            return None
        return UsageWindow(reset_at=reset_at)
    return UsageWindow(used_pct=used, remaining_pct=max(0, 100 - used), reset_at=reset_at)


def parse_usage(data: dict[str, Any]) -> ClaudeSnapshot:
    """Parse a /api/oauth/usage body. Missing utilization stays unknown."""
    return ClaudeSnapshot(
        five_hour=_parse_window(data.get("five_hour")),
        seven_day=_parse_window(data.get("seven_day")),
    )


@dataclass(frozen=True)
class WindowFace:
    """One window on the key: short label, amount, then reset."""

    label: str
    amount: str
    reset: str


def reset_face(reset_at: float | None, now: float | None = None) -> str:
    """Countdown until reset, in the machine's local calendar.

    A later calendar day is whole days until that day (tomorrow is 1d).
    The reset's own calendar day is whole hours. The last hour is whole
    minutes. A missing or passed reset is blank so the key can show a dash.
    """
    if reset_at is None:
        return ""
    current = time.time() if now is None else now
    left = reset_at - current
    if left <= 0:
        return ""
    if left < 3600:
        return f"{int(left // 60)}m"
    when = datetime.fromtimestamp(reset_at)
    today = datetime.fromtimestamp(current)
    days = (when.date() - today.date()).days
    if days >= 1:
        return f"{days}d"
    return f"{int(left // 3600)}h"


def window_face(snap: ClaudeSnapshot, window_id: str, mode: str = "remaining", now: float | None = None) -> WindowFace:
    label = WINDOW_LABELS.get(window_id, "Claude 5h")
    if snap.error == "Loading":
        return WindowFace(label, "…", "…")
    window = snap.window(window_id)
    reset = reset_face(window.reset_at, now) if window is not None else ""
    if window is None or window.used_pct is None:
        return WindowFace(label, "—", reset or "—")
    percent = window.remaining_pct if mode != "used" else window.used_pct
    amount = "—" if percent is None else f"{max(0, percent)}%"
    return WindowFace(label, amount, reset or "—")


def _window_low(window: UsageWindow | None) -> bool:
    return window is not None and window.remaining_pct is not None and window.remaining_pct <= LOW_PERCENT


def render_meter(size: int, face: WindowFace, low: bool) -> QImage:
    """Full-key face for one usage window. The title strip stays empty."""
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    margin = size * 0.06
    rect = QRectF(margin, margin * 0.35, size - 2 * margin, size - margin)
    label_h = rect.height() * 0.24
    amount_h = rect.height() * 0.46
    reset_h = rect.height() - label_h - amount_h
    center = Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter
    painter.setPen(QColor(C.muted))
    painter.setFont(ui_font(max(8, int(size * 0.16))))
    painter.drawText(QRectF(rect.left(), rect.top(), rect.width(), label_h), int(center), face.label)
    painter.setPen(QColor(C.danger if low else C.text))
    painter.setFont(ui_font(max(14, int(size * 0.32))))
    painter.drawText(
        QRectF(rect.left(), rect.top() + label_h, rect.width(), amount_h),
        int(center),
        face.amount,
    )
    painter.setPen(QColor(C.muted))
    painter.setFont(ui_font(max(8, int(size * 0.16))))
    painter.drawText(
        QRectF(rect.left(), rect.top() + label_h + amount_h, rect.width(), reset_h),
        int(center),
        face.reset,
    )
    painter.end()
    return image


def _credential_files() -> list[Path]:
    paths: list[Path] = []
    for env_name in ("CLAUDE_SECURESTORAGE_CONFIG_DIR", "CLAUDE_CONFIG_DIR"):
        raw = os.environ.get(env_name, "").strip()
        if raw:
            paths.append(Path(raw).expanduser() / ".credentials.json")
    paths.append(Path.home() / ".claude" / ".credentials.json")
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _token_from_text(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            return ""
        if not isinstance(data, dict):
            return ""
        oauth = data.get("claudeAiOauth")
        if isinstance(oauth, dict):
            token = str(oauth.get("accessToken") or "").strip()
            if token:
                return token
        return str(data.get("accessToken") or "").strip()
    return stripped.splitlines()[0].strip()


def _token_from_file(path: Path) -> str:
    try:
        if not path.is_file() or path.stat().st_size > 65_536:
            return ""
        return _token_from_text(path.read_text(encoding="utf-8"))
    except OSError:
        return ""


def read_access_token(token_path: str = "") -> str:
    """Claude Code OAuth token. Never log the return value."""
    chosen = token_path.strip()
    if chosen:
        return _token_from_file(Path(chosen).expanduser())
    env = os.environ.get(TOKEN_ENV, "").strip()
    if env:
        return env
    for path in _credential_files():
        token = _token_from_file(path)
        if token:
            return token
    return ""


def _percent_points(raw: Any) -> int | None:
    """Desktop utilization is already a percent from 0 to 100."""
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value <= 0:
        return 0
    if value >= 100:
        return 100
    return int(value + 0.5)


def _window_from_points(points: int | None, reset_at: float | None = None) -> UsageWindow | None:
    if points is None:
        if reset_at is None:
            return None
        return UsageWindow(reset_at=reset_at)
    return UsageWindow(used_pct=points, remaining_pct=max(0, 100 - points), reset_at=reset_at)


def _both_meters(snap: ClaudeSnapshot | None) -> bool:
    if snap is None:
        return False
    return all(
        window is not None and window.used_pct is not None
        for window in (snap.five_hour, snap.seven_day)
    )


def _any_reset(snap: ClaudeSnapshot | None) -> bool:
    if snap is None:
        return False
    return any(window is not None and window.reset_at is not None for window in (snap.five_hour, snap.seven_day))


def _missing_reset(snap: ClaudeSnapshot | None) -> bool:
    if not _both_meters(snap):
        return True
    return snap.five_hour.reset_at is None or snap.seven_day.reset_at is None


def _with_known_resets(base: ClaudeSnapshot, donor: ClaudeSnapshot | None) -> ClaudeSnapshot:
    """Copy a reset onto a window that does not already have one."""
    if donor is None:
        return base

    def pick(window: UsageWindow | None, other: UsageWindow | None) -> UsageWindow | None:
        if window is None or window.reset_at is not None:
            return window
        if other is None or other.reset_at is None:
            return window
        return replace(window, reset_at=other.reset_at)

    return replace(
        base,
        five_hour=pick(base.five_hour, donor.five_hour),
        seven_day=pick(base.seven_day, donor.seven_day),
    )


def _org_id(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if _ORG_RE.fullmatch(text):
        return text
    return ""


def _sample_usage(sample: dict[str, Any]) -> dict[str, Any]:
    usage = sample.get("u")
    if isinstance(usage, dict):
        return usage
    return {key: sample.get(key) for key in ("fh", "sd") if key in sample}


def parse_desktop_usage(data: dict[str, Any]) -> ClaudeSnapshot:
    """Parse the desktop organization usage body.

    ``utilization`` is a percent point (0 to 100), unlike the OAuth fraction.
    """

    def one(raw: Any) -> UsageWindow | None:
        if not isinstance(raw, dict):
            return None
        return _window_from_points(_percent_points(raw.get("utilization")), _parse_reset(raw.get("resets_at")))

    return ClaudeSnapshot(five_hour=one(data.get("five_hour")), seven_day=one(data.get("seven_day")))


def parse_desktop_history(data: Any) -> tuple[ClaudeSnapshot | None, str]:
    """Newest desktop sample, plus its org id when that id is a uuid.

    The snapshot is None when that newest sample lacks both percents.
    Older samples are history and are not returned in its place.
    ``fetched_at`` is when the sample was written. There is no reset time.
    """
    if not isinstance(data, dict):
        return None, ""
    samples = data.get("samples")
    if not isinstance(samples, list):
        return None, ""
    best: dict[str, Any] | None = None
    best_at = -1.0
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        when = _parse_reset(sample.get("t"))
        if when is None or when < best_at:
            continue
        best = sample
        best_at = when
    if best is None:
        return None, ""
    org = _org_id(best.get("org"))
    usage = _sample_usage(best)
    five = _window_from_points(_percent_points(usage.get("fh")))
    week = _window_from_points(_percent_points(usage.get("sd")))
    if five is None or five.used_pct is None or week is None or week.used_pct is None:
        return None, org
    return ClaudeSnapshot(five_hour=five, seven_day=week, fetched_at=best_at), org


def desktop_history_path() -> Path:
    return Path.home() / ".config" / "Claude" / DESKTOP_HISTORY_NAME


def load_desktop_history(path: Path | None = None) -> tuple[ClaudeSnapshot | None, str]:
    chosen = desktop_history_path() if path is None else path
    try:
        if not chosen.is_file():
            return None, ""
        size = chosen.stat().st_size
        if size <= 0 or size > _HISTORY_MAX_BYTES:
            return None, ""
        data = json.loads(chosen.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None, ""
    return parse_desktop_history(data)


def cache_is_fresh(snap: ClaudeSnapshot, now: float | None = None, *, fresh_s: float = DESKTOP_CACHE_FRESH_S) -> bool:
    current = time.time() if now is None else now
    age = current - snap.fetched_at
    return -120 <= age <= fresh_s


def _history_needs_session(oauth: ClaudeSnapshot | None, cache: ClaudeSnapshot | None, now: float) -> bool:
    """Ask the desktop session unless OAuth, or a fresh file, already has resets."""
    if _both_meters(oauth):
        return False
    fresh = cache is not None and cache_is_fresh(cache, now) and _both_meters(cache)
    if not fresh:
        return True
    return _missing_reset(cache)


def prefer_reading(
    oauth: ClaudeSnapshot | None,
    cache: ClaudeSnapshot | None,
    session: ClaudeSnapshot | None,
    now: float | None = None,
) -> ClaudeSnapshot:
    """OAuth first. Then a session that knows a reset the history file lacks.

    A fresh history file still supplies the percents when the session has no
    reset of its own. One known reset is copied onto that file so the other
    window can stay on the file.
    """
    current = time.time() if now is None else now
    if oauth is not None and _both_meters(oauth):
        return oauth
    fresh = cache is not None and cache_is_fresh(cache, current) and _both_meters(cache)
    if fresh and _missing_reset(cache) and session is not None and _both_meters(session) and _any_reset(session):
        return session
    if fresh:
        return _with_known_resets(cache, session)
    if session is not None and _has_numbers(session):
        return session
    if oauth is not None and oauth.keep_previous:
        return oauth
    return ClaudeSnapshot(error="Sign in to Claude", keep_previous=True)


def _libcrypto() -> ctypes.CDLL | None:
    global _LIBCRYPTO, _LIBCRYPTO_TRIED
    if _LIBCRYPTO_TRIED:
        return _LIBCRYPTO
    _LIBCRYPTO_TRIED = True
    lib = None
    for name in ("libcrypto.so.3", "libcrypto.so"):
        try:
            lib = ctypes.CDLL(name)
            break
        except OSError:
            continue
    if lib is None:
        return None
    lib.EVP_CIPHER_CTX_new.restype = ctypes.c_void_p
    lib.EVP_CIPHER_CTX_free.argtypes = [ctypes.c_void_p]
    lib.EVP_aes_128_cbc.restype = ctypes.c_void_p
    lib.EVP_DecryptInit_ex.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    lib.EVP_DecryptInit_ex.restype = ctypes.c_int
    lib.EVP_DecryptUpdate.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    lib.EVP_DecryptUpdate.restype = ctypes.c_int
    lib.EVP_DecryptFinal_ex.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int)]
    lib.EVP_DecryptFinal_ex.restype = ctypes.c_int
    _LIBCRYPTO = lib
    return lib


def _aes_128_cbc_decrypt(key: bytes, iv: bytes, ciphertext: bytes) -> bytes | None:
    lib = _libcrypto()
    if lib is None or len(key) != 16 or len(iv) != 16 or not ciphertext:
        return None
    ctx = lib.EVP_CIPHER_CTX_new()
    if not ctx:
        return None
    try:
        key_buf = ctypes.create_string_buffer(key)
        iv_buf = ctypes.create_string_buffer(iv)
        in_buf = ctypes.create_string_buffer(ciphertext)
        out_buf = ctypes.create_string_buffer(len(ciphertext) + 32)
        out_len = ctypes.c_int(0)
        if lib.EVP_DecryptInit_ex(
            ctx,
            lib.EVP_aes_128_cbc(),
            None,
            ctypes.cast(key_buf, ctypes.c_void_p),
            ctypes.cast(iv_buf, ctypes.c_void_p),
        ) != 1:
            return None
        if lib.EVP_DecryptUpdate(
            ctx,
            ctypes.cast(out_buf, ctypes.c_void_p),
            ctypes.byref(out_len),
            ctypes.cast(in_buf, ctypes.c_void_p),
            len(ciphertext),
        ) != 1:
            return None
        final_buf = ctypes.create_string_buffer(32)
        final_len = ctypes.c_int(0)
        if lib.EVP_DecryptFinal_ex(ctx, ctypes.cast(final_buf, ctypes.c_void_p), ctypes.byref(final_len)) != 1:
            return None
        return out_buf.raw[: out_len.value] + final_buf.raw[: final_len.value]
    finally:
        lib.EVP_CIPHER_CTX_free(ctx)


def decrypt_cookie_value(blob: bytes, password: str, host: str) -> str:
    """Decrypt one Chromium cookie. Never log ``password`` or the return value."""
    if len(blob) < 4 or blob[:3] not in {b"v10", b"v11"} or not password or not host:
        return ""
    key = hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), b"saltysalt", 1, 16)
    plain = _aes_128_cbc_decrypt(key, b" " * 16, bytes(blob[3:]))
    if not plain:
        return ""
    digest = hashlib.sha256(host.encode("utf-8")).digest()
    if len(plain) >= 32 and plain[:32] == digest:
        plain = plain[32:]
    try:
        text = plain.decode("utf-8")
    except UnicodeError:
        return ""
    if not text or any(ord(ch) < 32 or ord(ch) > 126 for ch in text):
        return ""
    return text


def _safe_storage_password() -> str:
    """Claude desktop cookie key. Never log the return value."""
    try:
        proc = subprocess.run(
            [
                "secret-tool",
                "lookup",
                "application",
                "Claude",
                "xdg:schema",
                "chrome_libsecret_os_crypt_password_v2",
            ],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if proc.returncode != 0 or not proc.stdout:
        return ""
    return proc.stdout.decode("utf-8", "replace").strip("\r\n")


def _desktop_cookie_db() -> Path:
    return Path.home() / ".config" / "Claude" / "Cookies"


def _encrypted_cookie(host: str, name: str) -> bytes:
    db = _desktop_cookie_db()
    if not db.is_file():
        return b""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=2)
        try:
            row = con.execute(
                "SELECT encrypted_value FROM cookies WHERE host_key = ? AND name = ? "
                "ORDER BY last_access_utc DESC LIMIT 1",
                (host, name),
            ).fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return b""
    if not row or not row[0]:
        return b""
    blob = row[0]
    return bytes(blob) if not isinstance(blob, bytes) else blob


def _cookie_is_sendable(value: str) -> bool:
    if not value or len(value) > 4096:
        return False
    return not any(ord(ch) < 32 or ord(ch) > 126 or ch in ";," for ch in value)


def _fetch_org_usage(org: str, session_key: str) -> ClaudeSnapshot | None:
    """Desktop usage call. The cookie is sent only to this Claude host."""
    if not _ORG_RE.fullmatch(org) or not _cookie_is_sendable(session_key):
        return None
    request = urllib.request.Request(
        f"https://claude.ai/api/organizations/{org}/usage",
        method="GET",
        headers={
            "Accept": "application/json",
            "Cookie": f"sessionKey={session_key}",
            "User-Agent": "PopStream",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_S) as response:
            status = response.getcode() or 200
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError:
        return None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    if status != 200 or not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    snap = parse_desktop_usage(payload)
    if not _has_numbers(snap):
        return None
    snap.fetched_at = time.time()
    return snap


def _fetch_desktop_session() -> ClaudeSnapshot | None:
    """Local desktop cookie, sent only to Claude's own usage endpoint."""
    try:
        _cached, org = load_desktop_history()
        password = _safe_storage_password()
        if not password:
            return None
        session_key = decrypt_cookie_value(_encrypted_cookie(".claude.ai", "sessionKey"), password, ".claude.ai")
        if not org:
            org = _org_id(decrypt_cookie_value(_encrypted_cookie(".claude.ai", "lastActiveOrg"), password, ".claude.ai"))
    except Exception:
        return None
    if not session_key:
        return None
    if session_key.startswith("sk-ant-oat"):
        snap = _snapshot_from_oauth(session_key)
        if _has_numbers(snap):
            return snap
        return None
    if not org:
        return None
    return _fetch_org_usage(org, session_key)


def _http_json(token: str) -> tuple[int, str]:
    request = urllib.request.Request(
        USAGE_URL,
        method="GET",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "User-Agent": "PopStream",
        },
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_S) as response:
            return response.getcode() or 200, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return 0, ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _snapshot_from_oauth(token: str) -> ClaudeSnapshot:
    status, raw = _http_json(token)
    if status in {401, 403}:
        return ClaudeSnapshot(error="Sign in to Claude", keep_previous=True)
    if status != 200 or not raw:
        return ClaudeSnapshot(error="Claude usage unavailable", keep_previous=True)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return ClaudeSnapshot(error="Claude usage unavailable", keep_previous=True)
    if not isinstance(payload, dict):
        return ClaudeSnapshot(error="Claude usage unavailable", keep_previous=True)
    snap = parse_usage(payload)
    snap.fetched_at = time.time()
    return snap


def fetch_claude_usage(token_path: str = "") -> ClaudeSnapshot:
    """OAuth when it is present. Otherwise the desktop session fills reset times.

    The history file has percents and no reset, so a fresh file still asks the
    session. File and cookie reads run on the monitor thread, not the deck
    thread. A failed read keeps the last good snapshot.
    """
    now = time.time()
    token = read_access_token(token_path)
    oauth = _snapshot_from_oauth(token) if token else None
    cached, _org = load_desktop_history()
    session = _fetch_desktop_session() if _history_needs_session(oauth, cached, now) else None
    return prefer_reading(oauth, cached, session, now)


class _FetchTask(QRunnable):
    def __init__(self, monitor: "ClaudeMonitor") -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._monitor = monitor

    def run(self) -> None:
        try:
            snap = fetch_claude_usage(self._monitor.token_path)
        except Exception:
            snap = ClaudeSnapshot(error="Claude usage unavailable", keep_previous=True)
        self._monitor.finished.emit(snap)


class ClaudeMonitor(QObject):
    finished = Signal(object)
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.snapshot = ClaudeSnapshot(error="Loading")
        self.token_path = ""
        self._lock = threading.Lock()
        self._inflight = False
        self._failed = False
        self._last_attempt = 0.0
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self.finished.connect(self._store, Qt.ConnectionType.QueuedConnection)

    def request(self, *, token_path: str = "", force: bool = False) -> None:
        now = time.time()
        with self._lock:
            if self._inflight:
                return
            elapsed = now - self._last_attempt if self._last_attempt else None
            if force:
                if self._failed and elapsed is not None and elapsed < FORCE_BACKOFF_S:
                    return
            else:
                interval = FAIL_BACKOFF_S if self._failed else POLL_S
                if elapsed is not None and elapsed < interval:
                    return
            self._inflight = True
            self._last_attempt = now
            self.token_path = token_path
        self._pool.start(_FetchTask(self))

    def _store(self, snap: object) -> None:
        failed = isinstance(snap, ClaudeSnapshot) and snap.keep_previous
        if isinstance(snap, ClaudeSnapshot):
            self.snapshot = apply_reading(self.snapshot, snap)
        with self._lock:
            self._inflight = False
            self._failed = failed
        self.changed.emit()


_MONITOR: ClaudeMonitor | None = None


def monitor() -> ClaudeMonitor:
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = ClaudeMonitor()
    return _MONITOR


class ClaudeWindowAction(Action):
    """One Claude window, drawn with the same face as the Grok usage key."""

    window_id = "five_hour"
    label = "Claude 5h"

    def __init__(self) -> None:
        self._timer: QTimer | None = None
        self._ctx: ActionContext | None = None
        self._watch = False
        self._stamp: tuple | None = None

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
        host.request(token_path=self._token_path(ctx))

    def will_disappear(self, ctx: ActionContext) -> None:
        if self._timer is not None:
            self._timer.stop()
        if self._watch:
            monitor().changed.disconnect(self._on_changed)
            self._watch = False
        self._ctx = None
        self._stamp = None

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._stamp = None
        self._paint(ctx)

    def key_down(self, ctx: ActionContext) -> None:
        press = str(ctx.settings.get("on_press") or "refresh")
        if press == "open":
            QDesktopServices.openUrl(QUrl(USAGE_PAGE))
            ctx.show_ok()
            return
        host = monitor()
        host.request(token_path=self._token_path(ctx), force=True)
        window = host.snapshot.window(self.window_id)
        known = window is not None and window.used_pct is not None
        if known:
            ctx.show_ok()
        else:
            ctx.show_alert()
        self._paint(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _ClaudeInspector(ctx, parent, weekly=self.window_id == "seven_day")

    def _token_path(self, ctx: ActionContext) -> str:
        return str(ctx.settings.get("token_path") or "")

    def _tick(self) -> None:
        if self._ctx is None:
            return
        monitor().request(token_path=self._token_path(self._ctx))
        self._paint(self._ctx)

    def _on_changed(self) -> None:
        if self._ctx is not None:
            self._paint(self._ctx)

    def _paint(self, ctx: ActionContext) -> None:
        snap = monitor().snapshot
        mode = str(ctx.settings.get("display") or "remaining")
        if mode not in {"remaining", "used"}:
            mode = "remaining"
        face = window_face(snap, self.window_id, mode)
        low = _window_low(snap.window(self.window_id))
        spec = ctx.current_spec()
        if spec is not None and not getattr(spec, "has_lcd", True):
            size = 72
        else:
            size = max(64, int(getattr(spec, "key_size", 0) or 72)) if spec is not None else 72
        stamp = (face, low, size)
        if stamp == self._stamp:
            return
        self._stamp = stamp
        ctx.set_state("danger" if low else "")
        # The stock title strip is one bar. An empty title leaves this image as the whole key.
        ctx.set_title("")
        ctx.set_image(render_meter(size, face, low))


class ClaudeUsageAction(ClaudeWindowAction):
    """Existing catalog id. The rolling 5 hour window on its own key."""

    window_id = "five_hour"
    label = "Claude 5h"


class ClaudeWeekAction(ClaudeWindowAction):
    """Weekly window on its own key."""

    window_id = "seven_day"
    label = "Claude Wk"


class _ClaudeInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent: QWidget | None = None, *, weekly: bool = False) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        if weekly:
            hint = QLabel(
                "One key shows Claude usage for this week. "
                "Claude Wk is the short name, then the percent, then when that week resets. "
                "Show sets remaining or used. "
                "It refreshes on its own about every 2 minutes. "
                "Claude Desktop's usage on this machine is used when Claude Code is signed out. "
                "That desktop session also supplies when the week resets. "
                "Token file can point at the Claude Code credentials JSON. "
                "Until a percent is known, the key shows a dash instead of 0%."
            )
        else:
            hint = QLabel(
                "One key shows Claude usage for the rolling 5 hour window. "
                "Claude 5h is the short name, then the percent, then when that window resets. "
                "Show sets remaining or used. "
                "It refreshes on its own about every 2 minutes. "
                "Claude Desktop's usage on this machine is used when Claude Code is signed out. "
                "That desktop session also supplies when the window resets. "
                "Token file can point at the Claude Code credentials JSON. "
                "Until a percent is known, the key shows a dash instead of 0%."
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
        press = str(ctx.settings.get("on_press") or "refresh")
        index = self.press.findData(press)
        self.press.setCurrentIndex(index if index >= 0 else 0)
        self.press.currentIndexChanged.connect(self._save)
        self.path = QLineEdit(str(ctx.settings.get("token_path") or ""))
        self.path.setPlaceholderText("~/.claude/.credentials.json")
        self.path.editingFinished.connect(self._save)
        browse = QPushButton("Choose")
        browse.clicked.connect(self._browse)
        layout.addRow(hint)
        layout.addRow("Show", self.display)
        layout.addRow("When pressed", self.press)
        layout.addRow("Token file", self.path)
        layout.addRow("", browse)

    def _browse(self) -> None:
        current = self.path.text().strip()
        start = str(Path(current).expanduser().parent) if current else str(Path.home() / ".claude")
        chosen, _ = QFileDialog.getOpenFileName(self, "Claude credentials", start, "JSON (*.json);;All files (*)")
        if not chosen:
            return
        self.path.setText(chosen)
        self._save()

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings.pop("window", None)
        settings["display"] = self.display.currentData() or "remaining"
        settings["on_press"] = self.press.currentData() or "refresh"
        settings["token_path"] = self.path.text().strip()
        self._ctx.set_settings(settings)
