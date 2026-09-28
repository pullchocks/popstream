from __future__ import annotations

import concurrent.futures
import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QProcess, QRunnable, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QLineEdit, QPushButton, QWidget

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import fit_combo, tighten_form

WISHLIST_TTL_S = 900
TICK_MS = 60_000
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_STEAMID_RE = re.compile(r"7656119\d{10}")
_SKIP_GAME = (
    "proton ",
    "steam linux runtime",
    "steamworks common",
    "source sdk",
    "easy anti-cheat",
    "battleye",
)
WISHLIST_URL = "https://store.steampowered.com/wishlist/"
SPECIALS_URL = "https://store.steampowered.com/specials/"
DESTINATIONS: list[tuple[str, str, str]] = [
    ("main", "Client", "steam://open/main"),
    ("friends", "Friends", "steam://open/friends"),
    ("library", "Library", "steam://open/games"),
    ("store", "Store", "steam://url/StoreFrontPage"),
    ("wishlist", "Wishlist", ""),
    ("downloads", "Downloads", "steam://open/downloads"),
    ("inventory", "Inventory", "steam://url/CommunityInventory"),
    ("profile", "Profile", "steam://url/SteamIDMyProfile"),
    ("screenshots", "Screenshots", "steam://open/screenshots"),
    ("news", "News", "steam://open/news"),
    ("bigpicture", "Big Picture", "steam://open/bigpicture"),
    ("servers", "Servers", "steam://open/servers"),
    ("settings", "Settings", "steam://settings"),
    ("specials", "Specials", f"steam://openurl/{SPECIALS_URL}"),
]
STATUSES: list[tuple[str, str]] = [
    ("online", "Online"),
    ("away", "Away"),
    ("busy", "Busy"),
    ("invisible", "Invisible"),
    ("offline", "Offline"),
]
_DEST_URI = {ident: uri for ident, _label, uri in DESTINATIONS}

_GAMES_CACHE: tuple[float, list[tuple[str, str]]] | None = None
_STEAM_CMD: list[str] | None = None
_STEAM_CMD_AT = 0.0
_RESOLVE_CACHE: dict[str, tuple[float, str]] = {}


def parse_vdf(text: str) -> dict[str, Any]:
    tokens = _vdf_tokens(text)
    pos = 0

    def parse_value() -> Any:
        nonlocal pos
        if pos >= len(tokens):
            return {}
        if tokens[pos] == "{":
            pos += 1
            obj: dict[str, Any] = {}
            while pos < len(tokens) and tokens[pos] != "}":
                key = tokens[pos]
                pos += 1
                obj[key] = parse_value()
            if pos < len(tokens) and tokens[pos] == "}":
                pos += 1
            return obj
        value = tokens[pos]
        pos += 1
        return value

    root: dict[str, Any] = {}
    while pos < len(tokens):
        if tokens[pos] == "{":
            parsed = parse_value()
            return parsed if isinstance(parsed, dict) else root
        key = tokens[pos]
        pos += 1
        root[key] = parse_value()
    return root


def _vdf_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch in "{}":
            tokens.append(ch)
            i += 1
            continue
        if ch == '"':
            i += 1
            buf: list[str] = []
            while i < n:
                if text[i] == "\\" and i + 1 < n:
                    buf.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                buf.append(text[i])
                i += 1
            tokens.append("".join(buf))
            continue
        j = i
        while j < n and text[j] not in " \t\r\n{}":
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens


def steam_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        home / ".steam" / "steam",
        home / ".steam" / "root",
        home / ".local" / "share" / "Steam",
        home / ".var" / "app" / "com.valvesoftware.Steam" / "data" / "Steam",
    ]
    roots: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            if not path.is_dir():
                continue
            resolved = str(path.resolve())
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(Path(resolved))
    return roots


def steam_command() -> list[str] | None:
    global _STEAM_CMD, _STEAM_CMD_AT
    now = time.monotonic()
    if _STEAM_CMD is not None and now - _STEAM_CMD_AT < 30:
        return _STEAM_CMD
    native = shutil.which("steam")
    if native:
        _STEAM_CMD, _STEAM_CMD_AT = [native], now
        return _STEAM_CMD
    flatpak = shutil.which("flatpak")
    if flatpak:
        try:
            result = subprocess.run(
                [flatpak, "info", "com.valvesoftware.Steam"],
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            result = None
        if result is not None and result.returncode == 0:
            _STEAM_CMD, _STEAM_CMD_AT = [flatpak, "run", "com.valvesoftware.Steam"], now
            return _STEAM_CMD
    _STEAM_CMD, _STEAM_CMD_AT = None, now
    return None


def open_steam_uri(uri: str) -> bool:
    if not uri:
        return False
    argv = steam_command()
    if argv:
        return QProcess.startDetached(argv[0], argv[1:] + [uri])
    opener = shutil.which("xdg-open")
    if opener:
        return QProcess.startDetached(opener, [uri])
    return QDesktopServices.openUrl(QUrl(uri))


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def logged_in_steamid() -> str:
    best = ""
    best_ts = -1
    for root in steam_roots():
        blob = _read_text(root / "config" / "loginusers.vdf")
        if not blob:
            continue
        data = parse_vdf(blob)
        users = data.get("users") if isinstance(data.get("users"), dict) else data
        if not isinstance(users, dict):
            continue
        for sid, info in users.items():
            ident = str(sid).strip()
            if not ident.isdigit() or not isinstance(info, dict):
                continue
            if str(info.get("MostRecent") or "") in {"1", "true"}:
                return ident
            try:
                stamp = int(str(info.get("Timestamp") or "0"))
            except ValueError:
                stamp = 0
            if stamp >= best_ts:
                best, best_ts = ident, stamp
    return best


def extract_steamid(raw: str) -> str:
    text = (raw or "").strip()
    match = _STEAMID_RE.search(text)
    if match:
        return match.group(0)
    return ""


def resolve_steamid(raw: str) -> str:
    text = (raw or "").strip().rstrip("/")
    found = extract_steamid(text)
    if found:
        return found
    if not text:
        return logged_in_steamid()
    now = time.time()
    cached = _RESOLVE_CACHE.get(text.lower())
    if cached and now - cached[0] < 3600:
        return cached[1]
    custom = ""
    if "/id/" in text:
        custom = text.split("/id/", 1)[1].split("/", 1)[0]
    elif text.startswith("http"):
        custom = ""
    elif not text.isdigit():
        custom = text
    if not custom:
        ident = logged_in_steamid()
        _RESOLVE_CACHE[text.lower()] = (now, ident)
        return ident
    url = f"https://steamcommunity.com/id/{custom}/?xml=1"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": BROWSER_UA})
        with urllib.request.urlopen(request, timeout=6) as response:
            xml = response.read().decode("utf-8", "ignore")
    except (urllib.error.URLError, TimeoutError, OSError):
        ident = ""
    else:
        match = re.search(r"<steamID64>(\d+)</steamID64>", xml)
        ident = match.group(1) if match else ""
    _RESOLVE_CACHE[text.lower()] = (now, ident)
    return ident


def destination_uri(dest: str, steamid: str = "") -> str:
    if dest == "wishlist":
        if steamid:
            return f"steam://openurl/{WISHLIST_URL}profiles/{steamid}/"
        return f"steam://openurl/{WISHLIST_URL}"
    return _DEST_URI.get(dest) or "steam://open/main"


def store_uri(appid: str) -> str:
    return f"steam://store/{appid}" if appid else f"steam://openurl/{SPECIALS_URL}"


def _library_stamp() -> float:
    stamp = 0.0
    for root in steam_roots():
        for rel in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
            path = root / rel
            try:
                stamp = max(stamp, path.stat().st_mtime)
            except OSError:
                continue
    return stamp


def _skip_game(name: str) -> bool:
    low = name.lower()
    return any(low.startswith(prefix) or prefix in low for prefix in _SKIP_GAME)


def installed_games() -> list[tuple[str, str]]:
    global _GAMES_CACHE
    stamp = _library_stamp()
    if _GAMES_CACHE is not None and _GAMES_CACHE[0] == stamp:
        return _GAMES_CACHE[1]
    games: list[tuple[str, str]] = []
    seen: set[str] = set()
    folders: list[Path] = []
    for root in steam_roots():
        folders.append(root)
        for rel in ("steamapps/libraryfolders.vdf", "config/libraryfolders.vdf"):
            blob = _read_text(root / rel)
            if not blob:
                continue
            data = parse_vdf(blob)
            rows = data.get("libraryfolders") if isinstance(data.get("libraryfolders"), dict) else data
            if not isinstance(rows, dict):
                continue
            for item in rows.values():
                if not isinstance(item, dict):
                    continue
                path = str(item.get("path") or "").strip()
                if path:
                    folders.append(Path(path))
    for folder in folders:
        apps = folder / "steamapps"
        if not apps.is_dir():
            continue
        try:
            manifests = list(apps.glob("appmanifest_*.acf"))
        except OSError:
            continue
        for path in manifests:
            parsed = parse_vdf(_read_text(path))
            state = parsed.get("AppState") if isinstance(parsed.get("AppState"), dict) else parsed
            if not isinstance(state, dict):
                continue
            appid = str(state.get("appid") or "").strip()
            name = str(state.get("name") or "").strip()
            flags = str(state.get("StateFlags") or "0")
            if not appid or not name or appid in seen or _skip_game(name):
                continue
            try:
                installed = int(flags) & 4 == 4
            except ValueError:
                installed = True
            if not installed:
                continue
            seen.add(appid)
            games.append((appid, name))
    games.sort(key=lambda item: item[1].lower())
    _GAMES_CACHE = (stamp, games)
    return games


@dataclass
class WishItem:
    appid: str
    name: str
    discount: int = 0
    coming_soon: bool = False
    release_at: float | None = None


@dataclass
class WishSnap:
    items: list[WishItem] = field(default_factory=list)
    error: str | None = None
    fetched_at: float = 0.0
    steamid: str = ""
    query: str = ""

    def sales(self) -> list[WishItem]:
        return sorted(
            [item for item in self.items if item.discount > 0],
            key=lambda item: (-item.discount, item.name.lower()),
        )

    def releases(self, now: float | None = None) -> tuple[list[WishItem], list[WishItem]]:
        stamp = time.time() if now is None else now
        fresh: list[WishItem] = []
        soon: list[WishItem] = []
        for item in self.items:
            if item.release_at and stamp - 14 * 86400 <= item.release_at <= stamp:
                fresh.append(item)
            elif item.coming_soon or (item.release_at is not None and item.release_at > stamp):
                soon.append(item)
        fresh.sort(key=lambda item: -(item.release_at or 0))
        soon.sort(key=lambda item: (item.release_at is None, item.release_at or 0, item.name.lower()))
        return fresh, soon


def _parse_release(raw: Any) -> float | None:
    if isinstance(raw, (int, float)):
        value = float(raw)
        if value > 1e12:
            value /= 1000.0
        if value > 1e9:
            return value
        return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    for fmt in ("%b %d, %Y", "%d %b, %Y", "%d %b %Y", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return None


def _item_from_details(appid: str, info: dict[str, Any]) -> WishItem:
    price = info.get("price_overview") if isinstance(info.get("price_overview"), dict) else {}
    release = info.get("release_date") if isinstance(info.get("release_date"), dict) else {}
    try:
        discount = int(price.get("discount_percent") or 0)
    except (TypeError, ValueError):
        discount = 0
    release_at = _parse_release(release.get("date"))
    blob = str(release.get("date") or "").lower()
    coming = bool(release.get("coming_soon"))
    if any(token in blob for token in ("coming soon", "to be announced", "tba")):
        coming = True
    if re.search(r"\bq[1-4]\b", blob):
        coming = True
    if release_at is not None and release_at > time.time():
        coming = True
    name = str(info.get("name") or appid).strip() or appid
    return WishItem(
        appid=appid,
        name=name,
        discount=max(0, discount),
        coming_soon=coming,
        release_at=release_at,
    )


def _http_json(url: str, timeout: float = 8) -> Any:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": BROWSER_UA, "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read().decode("utf-8", "ignore")
    if not raw.strip():
        return {}
    if raw.lstrip().startswith("<"):
        raise json.JSONDecodeError("html", raw, 0)
    return json.loads(raw)


def _app_detail(appid: str) -> dict[str, Any]:
    url = (
        "https://store.steampowered.com/api/appdetails"
        f"?appids={appid}&filters=price_overview,release_date,basic"
    )
    try:
        payload = _http_json(url, timeout=10)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict):
        return {}
    row = payload.get(str(appid))
    if not isinstance(row, dict) or not row.get("success"):
        return {}
    data = row.get("data")
    return data if isinstance(data, dict) else {}


def _app_details(appids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    if not appids:
        return out
    workers = min(6, len(appids))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_app_detail, appid): appid for appid in appids}
        for fut in concurrent.futures.as_completed(futs):
            appid = futs[fut]
            try:
                data = fut.result()
            except Exception:
                continue
            if data:
                out[appid] = data
    return out


def fetch_wishlist(query: str) -> WishSnap:
    ident = resolve_steamid(query)
    if not ident:
        return WishSnap(error="No Steam ID", query=query)
    url = f"https://api.steampowered.com/IWishlistService/GetWishlist/v1/?steamid={ident}"
    try:
        payload = _http_json(url)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return WishSnap(error="Wishlist unavailable", steamid=ident, query=query)
    response = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(response, dict):
        return WishSnap(error="Wishlist unavailable", steamid=ident, query=query)
    if "items" not in response:
        return WishSnap(error="Private wishlist", steamid=ident, query=query)
    rows = response.get("items")
    if not isinstance(rows, list):
        rows = []
    appids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("appid") is None:
            continue
        appid = str(row.get("appid")).strip()
        if not appid or appid in seen:
            continue
        seen.add(appid)
        appids.append(appid)
    details = _app_details(appids)
    items = [_item_from_details(appid, details.get(appid) or {}) for appid in appids]
    return WishSnap(items=items, fetched_at=time.time(), steamid=ident, query=query)


class _FetchTask(QRunnable):
    def __init__(self, monitor: "WishlistMonitor", query: str) -> None:
        super().__init__()
        self.setAutoDelete(True)
        self._monitor = monitor
        self._query = query

    def run(self) -> None:
        try:
            snap = fetch_wishlist(self._query)
        except Exception:
            snap = WishSnap(error="Wishlist unavailable", query=self._query)
        self._monitor.finished.emit(snap)


class WishlistMonitor(QObject):
    finished = Signal(object)
    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.snapshot = WishSnap(error="Loading")
        self._lock = threading.Lock()
        self._inflight = False
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self.finished.connect(self._store, Qt.ConnectionType.QueuedConnection)

    def request(self, query: str, *, force: bool = False) -> None:
        ident = query.strip()
        now = time.time()
        with self._lock:
            if force:
                if self._inflight:
                    return
            else:
                fresh = (
                    self.snapshot.query == ident
                    and self.snapshot.fetched_at > 0
                    and (now - self.snapshot.fetched_at) < WISHLIST_TTL_S
                    and not self.snapshot.error
                )
                if fresh or self._inflight:
                    return
            self._inflight = True
        self._pool.start(_FetchTask(self, ident))

    def _store(self, snap: object) -> None:
        if isinstance(snap, WishSnap):
            if not snap.fetched_at:
                snap.fetched_at = time.time()
            self.snapshot = snap
        with self._lock:
            self._inflight = False
        self.changed.emit()


_MONITOR: WishlistMonitor | None = None


def monitor() -> WishlistMonitor:
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = WishlistMonitor()
    return _MONITOR


def _steamid_for(settings: dict[str, Any]) -> str:
    raw = str(settings.get("steamid") or "").strip()
    return extract_steamid(raw) or logged_in_steamid()


def _wishlist_query(settings: dict[str, Any]) -> str:
    return str(settings.get("steamid") or "").strip()


class OpenSteamAction(Action):
    dest = "main"
    inspect = False

    def key_down(self, ctx: ActionContext) -> None:
        dest = str(ctx.settings.get("dest") or self.dest or "main")
        steamid = _steamid_for(ctx.settings) if dest == "wishlist" else ""
        if not open_steam_uri(destination_uri(dest, steamid)):
            ctx.show_alert()
            return
        ctx.show_ok()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget | None:
        if not self.inspect:
            return None
        return _DestInspector(ctx, parent)


class OpenSteamPickerAction(OpenSteamAction):
    inspect = True


class FriendsAction(OpenSteamAction):
    dest = "friends"


class LibraryAction(OpenSteamAction):
    dest = "library"


class StoreAction(OpenSteamAction):
    dest = "store"


class DownloadsAction(OpenSteamAction):
    dest = "downloads"


class BigPictureAction(OpenSteamAction):
    dest = "bigpicture"


class WishlistAction(OpenSteamAction):
    dest = "wishlist"
    inspect = True

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _SteamIdInspector(ctx, parent, extra="Press opens your wishlist in Steam.")


class LaunchGameAction(Action):
    def will_appear(self, ctx: ActionContext) -> None:
        self._paint(ctx)

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._paint(ctx)

    def _paint(self, ctx: ActionContext) -> None:
        if ctx.slot_title.strip():
            ctx.set_title(None)
            return
        name = str(ctx.settings.get("name") or "").strip()
        ctx.set_title(name[:18] if name else "Game")

    def key_down(self, ctx: ActionContext) -> None:
        appid = str(ctx.settings.get("appid") or "").strip()
        if not appid:
            ctx.show_alert()
            return
        if not open_steam_uri(f"steam://rungameid/{appid}"):
            ctx.show_alert()
            return
        ctx.show_ok()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _GameInspector(ctx, parent)


class StatusAction(Action):
    def will_appear(self, ctx: ActionContext) -> None:
        self._paint(ctx)

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._paint(ctx)

    def _paint(self, ctx: ActionContext) -> None:
        if ctx.slot_title.strip():
            ctx.set_title(None)
            return
        wanted = str(ctx.settings.get("status") or "online")
        label = next((name for ident, name in STATUSES if ident == wanted), "Online")
        ctx.set_title(label)

    def key_down(self, ctx: ActionContext) -> None:
        status = str(ctx.settings.get("status") or "online")
        if status not in {ident for ident, _name in STATUSES}:
            status = "online"
        if not open_steam_uri(f"steam://friends/status/{status}"):
            ctx.show_alert()
            return
        ctx.show_ok()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _StatusInspector(ctx, parent)


class _WishlistLiveAction(Action):
    kind = "sales"

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
        host.request(_wishlist_query(ctx.settings))

    def will_disappear(self, ctx: ActionContext) -> None:
        if self._timer is not None:
            self._timer.stop()
        if self._watch:
            monitor().changed.disconnect(self._on_changed)
            self._watch = False
        self._ctx = None

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        monitor().request(_wishlist_query(ctx.settings), force=True)
        self._paint(ctx)

    def key_down(self, ctx: ActionContext) -> None:
        steamid = _steamid_for(ctx.settings)
        snap = monitor().snapshot
        press = str(ctx.settings.get("on_press") or "open")
        if press == "refresh":
            monitor().request(_wishlist_query(ctx.settings), force=True)
            ctx.show_ok()
            self._paint(ctx)
            return
        target = self._press_uri(ctx, snap, steamid)
        if not open_steam_uri(target):
            ctx.show_alert()
            return
        ctx.show_ok()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _WishlistInspector(ctx, parent, kind=self.kind)

    def _tick(self) -> None:
        if self._ctx is None:
            return
        monitor().request(_wishlist_query(self._ctx.settings))
        self._paint(self._ctx)

    def _on_changed(self) -> None:
        if self._ctx is not None:
            self._paint(self._ctx)

    def _press_uri(self, ctx: ActionContext, snap: WishSnap, steamid: str) -> str:
        press = str(ctx.settings.get("on_press") or "open")
        if press == "top":
            if self.kind == "sales":
                sales = snap.sales()
                if sales:
                    return store_uri(sales[0].appid)
            else:
                fresh, soon = snap.releases()
                pick = (fresh or soon)
                if pick:
                    return store_uri(pick[0].appid)
        return destination_uri("wishlist", steamid)

    def _paint(self, ctx: ActionContext) -> None:
        snap = monitor().snapshot
        if snap.error == "Loading":
            ctx.set_state("")
            ctx.set_title("…")
            return
        if snap.error:
            ctx.set_state("danger")
            if snap.error == "No Steam ID":
                ctx.set_title("NO ID")
            elif snap.error == "Private wishlist":
                ctx.set_title("PRIV")
            else:
                ctx.set_title("ERR")
            return
        if self.kind == "sales":
            sales = snap.sales()
            ctx.set_state("ok" if sales else "")
            if not sales:
                ctx.set_title("none")
            elif len(sales) == 1:
                ctx.set_title(f"-{sales[0].discount}%")
            else:
                ctx.set_title(f"{len(sales)} SALE")
            return
        fresh, soon = snap.releases()
        if fresh:
            ctx.set_state("ok")
            ctx.set_title("1 NEW" if len(fresh) == 1 else f"{len(fresh)} NEW")
            return
        if soon:
            ctx.set_state("")
            nxt = soon[0]
            if nxt.release_at:
                when = datetime.fromtimestamp(nxt.release_at)
                ctx.set_title(f"{when.strftime('%b')} {when.day}")
            else:
                ctx.set_title("1 SOON" if len(soon) == 1 else f"{len(soon)} SOON")
            return
        ctx.set_state("")
        ctx.set_title("none")


class WishlistSalesAction(_WishlistLiveAction):
    kind = "sales"


class WishlistReleasesAction(_WishlistLiveAction):
    kind = "releases"


class _DestInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        current = str(ctx.settings.get("dest") or "main")
        for ident, label, _uri in DESTINATIONS:
            self.combo.addItem(label, ident)
        index = self.combo.findData(current)
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.currentIndexChanged.connect(self._save)
        layout.addRow("Open", self.combo)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["dest"] = self.combo.currentData() or "main"
        self._ctx.set_settings(settings)


class _SteamIdInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None, extra: str = "") -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        hint = QLabel(
            "Leave Steam ID blank to use the account signed into Steam on this PC. "
            + extra
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        self.steamid = QLineEdit(str(ctx.settings.get("steamid") or ""))
        self.steamid.setPlaceholderText("Steam ID or profile URL")
        self.steamid.editingFinished.connect(self._save)
        layout.addRow(hint)
        layout.addRow("Steam ID", self.steamid)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["steamid"] = self.steamid.text().strip()
        self._ctx.set_settings(settings)


class _WishlistInspector(_SteamIdInspector):
    def __init__(self, ctx: ActionContext, parent=None, *, kind: str = "sales") -> None:
        extra = (
            "Sale and release counts need a public wishlist. "
            "Press still opens Steam even if the list is private."
        )
        super().__init__(ctx, parent, extra=extra)
        layout = self.layout()
        assert isinstance(layout, QFormLayout)
        self.press = fit_combo(QComboBox())
        self.press.addItem("Open wishlist", "open")
        self.press.addItem("Open top item", "top")
        self.press.addItem("Refresh now", "refresh")
        press = str(ctx.settings.get("on_press") or "open")
        index = self.press.findData(press)
        self.press.setCurrentIndex(index if index >= 0 else 0)
        self.press.currentIndexChanged.connect(self._save)
        layout.addRow("When pressed", self.press)
        self._kind = kind

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["steamid"] = self.steamid.text().strip()
        settings["on_press"] = self.press.currentData() or "open"
        self._ctx.set_settings(settings)


class _GameInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        refresh = QPushButton("Refresh games")
        refresh.clicked.connect(self._fill)
        layout.addRow("Game", self.combo)
        layout.addRow("", refresh)
        self.combo.currentIndexChanged.connect(self._save)
        self._fill()

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("appid") or "")
        games = installed_games()
        live = {appid for appid, _name in games}
        self.combo.blockSignals(True)
        self.combo.clear()
        if not games:
            self.combo.addItem("No installed Steam games", "")
        for appid, name in games:
            self.combo.addItem(name, appid)
        if current and current not in live:
            label = str(self._ctx.settings.get("name") or current)
            self.combo.addItem(f"{label}  (missing)", current)
        index = self.combo.findData(current)
        if index < 0 and games:
            index = 0
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.combo.blockSignals(False)
        if not current and games:
            self._save()

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["appid"] = str(self.combo.currentData() or "")
        settings["name"] = self.combo.currentText()
        if settings["name"].endswith("  (missing)"):
            settings["name"] = settings["name"][: -len("  (missing)")]
        self._ctx.set_settings(settings)


class _StatusInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        current = str(ctx.settings.get("status") or "online")
        for ident, label in STATUSES:
            self.combo.addItem(label, ident)
        index = self.combo.findData(current)
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.currentIndexChanged.connect(self._save)
        layout.addRow("Set to", self.combo)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["status"] = self.combo.currentData() or "online"
        self._ctx.set_settings(settings)


class SteamPlugin(Plugin):
    id = "com.popstream.steam"
    name = "Steam"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        items = [
            ActionInfo(
                "open",
                "Open Steam",
                "Steam",
                "Open the Steam client or a Steam page",
                "steam",
                defaults={"dest": "main"},
            ),
            ActionInfo("friends", "Friends", "Steam", "Open the Steam friends list", "friends"),
            ActionInfo("library", "Library", "Steam", "Open the Steam library", "game"),
            ActionInfo("store", "Store", "Steam", "Open the Steam store", "store"),
            ActionInfo("downloads", "Downloads", "Steam", "Open Steam downloads", "download"),
            ActionInfo(
                "bigpicture",
                "Big Picture",
                "Steam",
                "Open Steam Big Picture",
                "steam",
            ),
            ActionInfo("launch", "Launch Game", "Steam", "Launch an installed Steam game", "game"),
            ActionInfo("wishlist", "Wishlist", "Steam", "Open your Steam wishlist", "wish"),
            ActionInfo(
                "sales",
                "Wishlist Sales",
                "Steam",
                "How many wishlist games are on sale",
                "sale",
            ),
            ActionInfo(
                "releases",
                "Wishlist Releases",
                "Steam",
                "Upcoming or newly released wishlist games",
                "wish",
            ),
            ActionInfo(
                "status",
                "Status",
                "Steam",
                "Set Steam friends status",
                "status",
                defaults={"status": "online"},
            ),
        ]
        for appid, name in installed_games():
            items.append(
                ActionInfo(
                    f"launch:{appid}",
                    name,
                    "Steam / Games",
                    f"Launch {name}",
                    "game",
                    defaults={"appid": appid, "name": name},
                )
            )
        return items

    def create_action(self, action_id: str) -> Action | None:
        if action_id.startswith("launch:") or action_id == "launch":
            return LaunchGameAction()
        mapping: dict[str, type[Action]] = {
            "open": OpenSteamPickerAction,
            "friends": FriendsAction,
            "library": LibraryAction,
            "store": StoreAction,
            "downloads": DownloadsAction,
            "bigpicture": BigPictureAction,
            "wishlist": WishlistAction,
            "sales": WishlistSalesAction,
            "releases": WishlistReleasesAction,
            "status": StatusAction,
        }
        cls = mapping.get(action_id)
        return cls() if cls is not None else None
