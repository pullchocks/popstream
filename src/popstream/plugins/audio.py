from __future__ import annotations

import json
import re
import shutil
import struct
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QWidget,
)

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import C, fit_combo, plain_spin, tighten_form


def _run_bin(name: str, *args: str) -> tuple[int, str]:
    binary = shutil.which(name)
    if not binary:
        return 1, ""
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=2,
        )
        return result.returncode, (result.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def _pactl(*args: str) -> tuple[int, str]:
    return _run_bin("pactl", *args)


def _amixer(*args: str) -> tuple[int, str]:
    return _run_bin("amixer", *args)


def _wpctl(*args: str) -> tuple[int, str]:
    return _run_bin("wpctl", *args)


def _playerctl(*args: str) -> tuple[int, str]:
    return _run_bin("playerctl", *args)


def _json_list(kind: str) -> list[dict[str, Any]]:
    code, out = _pactl("--format=json", "list", kind)
    if code != 0 or not out:
        return []
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


_ENDPOINT_TAIL = re.compile(
    r"\.(?:analog-[^.]+|pro-(?:output|input)-\d+|hdmi-stereo(?:-\d+)?|iec958-stereo)$"
)
_SNAP_TTL = 0.8
_SNAP: dict[str, Any] | None = None


def card_key(name: str) -> str:
    return _ENDPOINT_TAIL.sub("", name) if name else ""


def same_card(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if left == right:
        return True
    a, b = card_key(left), card_key(right)
    return bool(a and b and a == b)


def _invalidate_snap() -> None:
    global _SNAP
    _SNAP = None


def _snapshot(*, force: bool = False) -> dict[str, Any]:
    global _SNAP
    now = time.monotonic()
    if not force and _SNAP is not None and now - float(_SNAP["at"]) < _SNAP_TTL:
        return _SNAP
    _SNAP = {
        "at": now,
        "sinks": _json_list("sinks"),
        "sources": _json_list("sources"),
        "sink_inputs": _json_list("sink-inputs"),
        "default_sink": _pactl("get-default-sink")[1],
        "default_source": _pactl("get-default-source")[1],
        "alsa_cap": {},
        "wpctl_mute": {},
    }
    return _SNAP


def _live_rows(kind: str) -> list[dict[str, Any]]:
    key = "sinks" if kind == "sink" else "sources"
    rows = _snapshot().get(key) or []
    return rows if isinstance(rows, list) else []


EQFX_SINK_NAMES = {"eqfx.sink", "minieq.sink"}
EQFX_PLAYBACK_NAMES = {"eqfx.playback", "minieq.playback"}
EQFX_DIR = Path.home() / ".local" / "share" / "eqfx"
WANTED_OUTPUT_PATH = EQFX_DIR / "wanted-output"
DEVICE_VOLUMES_PATH = EQFX_DIR / "device_volumes.json"
WANTED_PRESET_PATH = EQFX_DIR / "wanted-preset"
EQFX_SETTINGS_PATH = EQFX_DIR / "settings.json"


def _is_eqfx_sink(name: str, desc: str = "") -> bool:
    return (
        name in EQFX_SINK_NAMES
        or name.startswith("eqfx.")
        or name.startswith("minieq.")
        or desc in {"eqFX", "MiniEQ"}
    )


def list_sinks() -> list[tuple[str, str]]:
    items = []
    for sink in _live_rows("sink"):
        name = str(sink.get("name") or "")
        desc = str(sink.get("description") or name)
        if name and not _is_eqfx_sink(name, desc):
            items.append((name, desc))
    return items


def list_sources() -> list[tuple[str, str]]:
    items = []
    for source in _live_rows("source"):
        name = str(source.get("name") or "")
        if not name or name.endswith(".monitor"):
            continue
        desc = str(source.get("description") or name)
        items.append((name, desc))
    return items


def default_sink() -> str:
    return str(_snapshot().get("default_sink") or "")


def default_source() -> str:
    return str(_snapshot().get("default_source") or "")


def pick_live_name(kind: str, wanted: str) -> str:
    """Map a stored Pulse name onto the live Analog node for that card."""
    if not wanted:
        return ""
    items = list_sinks() if kind == "sink" else list_sources()
    names = [name for name, _desc in items]
    if wanted in names:
        return wanted
    same = [name for name in names if same_card(name, wanted)]
    if not same:
        return wanted
    analog = [
        name
        for name in same
        if ".analog-" in name or "mono-fallback" in name or ".mono-" in name
    ]
    return analog[0] if analog else same[0]


def _pretty(name: str, items: list[tuple[str, str]]) -> str:
    for ident, desc in items:
        if ident == name:
            return desc
    return name


def _short(label: str) -> str:
    for suffix in (" Analog Stereo", " Digital Stereo (HDMI)", " Pro", " Stereo"):
        if label.endswith(suffix):
            label = label[: -len(suffix)]
    return label.strip() or label


def _eqfx_sink_name() -> str:
    for sink in _live_rows("sink"):
        name = str(sink.get("name") or "")
        desc = str(sink.get("description") or "")
        if _is_eqfx_sink(name, desc):
            return name
    return ""


def _write_wanted_output(name: str) -> None:
    try:
        WANTED_OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
        WANTED_OUTPUT_PATH.write_text(name, encoding="utf-8")
    except OSError:
        pass


def peek_wanted_output() -> str:
    try:
        if WANTED_OUTPUT_PATH.is_file():
            return WANTED_OUTPUT_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ""


def _write_wanted_preset(preset_id: str) -> None:
    try:
        WANTED_PRESET_PATH.parent.mkdir(parents=True, exist_ok=True)
        WANTED_PRESET_PATH.write_text(preset_id, encoding="utf-8")
    except OSError:
        pass


def peek_wanted_preset() -> str:
    try:
        if WANTED_PRESET_PATH.is_file():
            return WANTED_PRESET_PATH.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return ""


def _eqfx_settings() -> dict[str, Any]:
    try:
        if not EQFX_SETTINGS_PATH.is_file():
            return {}
        raw = json.loads(EQFX_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _eqfx_presets_path() -> Path | None:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[4] / "eqfx" / "src" / "eqfx" / "core" / "data" / "presets.json",
        Path("/home/owner/Desktop/Projects/eqfx/src/eqfx/core/data/presets.json"),
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def list_eqfx_presets() -> list[tuple[str, str, str]]:
    """Return (id, name, category) from the eqFX factory list."""
    path = _eqfx_presets_path()
    if path is None:
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows: list[tuple[str, str, str]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "").strip()
        name = str(item.get("name") or ident).strip()
        category = str(item.get("category") or "Other").strip() or "Other"
        if ident:
            rows.append((ident, name, category))
    return rows


def eqfx_preset_categories() -> list[str]:
    cats: list[str] = []
    for _ident, _name, category in list_eqfx_presets():
        if category not in cats:
            cats.append(category)
    return cats


def preset_label(preset_id: str) -> str:
    for ident, name, _category in list_eqfx_presets():
        if ident == preset_id:
            return name
    return preset_id


def active_eqfx_preset() -> str:
    wanted = peek_wanted_preset()
    if wanted:
        return wanted
    settings = _eqfx_settings()
    current = str(settings.get("preset_id") or "").strip()
    if current:
        return current
    if settings.get("remember_per_device"):
        device = active_output_sink()
        curves = settings.get("device_curves") if isinstance(settings.get("device_curves"), dict) else {}
        stored = curves.get(device) if device else None
        if isinstance(stored, dict):
            return str(stored.get("preset_id") or "").strip()
    return ""


def set_eqfx_preset(preset_id: str) -> bool:
    if not preset_id:
        return False
    known = {ident for ident, _name, _category in list_eqfx_presets()}
    if preset_id not in known:
        return False
    _write_wanted_preset(preset_id)
    return True


def eqfx_playback_sink() -> str:
    snap = _snapshot()
    sinks = {
        item.get("index"): str(item.get("name") or "")
        for item in snap.get("sinks") or []
        if isinstance(item, dict) and item.get("index") is not None
    }
    for item in snap.get("sink_inputs") or []:
        if not isinstance(item, dict):
            continue
        props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        node = str(props.get("node.name") or "")
        if node not in EQFX_PLAYBACK_NAMES:
            continue
        dest = sinks.get(item.get("sink"), "")
        if dest and not _is_eqfx_sink(dest):
            return dest
    return ""


def active_output_sink() -> str:
    """Hardware speakers/headphones currently in use, even when eqFX is default."""
    wanted = peek_wanted_output()
    if wanted:
        return wanted
    playing = eqfx_playback_sink()
    if playing:
        return playing
    current = default_sink()
    if current and not _is_eqfx_sink(current):
        return current
    return current


def _move_eqfx_playback(hardware: str) -> bool:
    moved = False
    for item in _snapshot().get("sink_inputs") or []:
        if not isinstance(item, dict):
            continue
        props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        node = str(props.get("node.name") or "")
        if node not in EQFX_PLAYBACK_NAMES:
            continue
        index = item.get("index")
        if index is None:
            continue
        code, _ = _pactl("move-sink-input", str(index), hardware)
        if code == 0:
            moved = True
    return moved


def set_default_sink(name: str) -> bool:
    name = pick_live_name("sink", name) or name
    eq = _eqfx_sink_name()
    if eq and name and not _is_eqfx_sink(name):
        leaving = active_output_sink()
        leaving_vol = None
        if leaving and leaving != name:
            leaving_vol = _volume_of(leaving)
            _remember_hw_volume(leaving)
        if not _restore_hw_volume(name) and leaving_vol is not None:
            _pactl("set-sink-volume", name, f"{leaving_vol}%")
            _remember_hw_volume(name)
        _write_wanted_output(name)
        moved = _move_eqfx_playback(name)
        _pactl("set-default-sink", eq)
        _invalidate_snap()
        return moved or True
    code, _ = _pactl("set-default-sink", name)
    _invalidate_snap()
    if code != 0:
        return False
    _, rows = _pactl("list", "short", "sink-inputs")
    for line in rows.splitlines():
        parts = line.split()
        if parts:
            _pactl("move-sink-input", parts[0], name)
    return True


def set_default_source(name: str) -> bool:
    name = pick_live_name("source", name) or name
    code, _ = _pactl("set-default-source", name)
    _invalidate_snap()
    if code != 0:
        return False
    entry = _find_device("source", name)
    node = _node_id(entry)
    if node:
        _wpctl("set-default", node)
    _move_source_outputs(name)
    return True


def _move_source_outputs(name: str) -> None:
    _, rows = _pactl("list", "short", "source-outputs")
    for line in rows.splitlines():
        parts = line.split()
        if parts:
            _pactl("move-source-output", parts[0], name)


def _source_volume(name: str, entry: dict[str, Any] | None = None) -> int:
    row = entry if entry is not None else _find_device("source", name)
    if row:
        volume = row.get("volume")
        if isinstance(volume, dict):
            for channel in volume.values():
                if not isinstance(channel, dict):
                    continue
                match = re.search(r"(\d+)", str(channel.get("value_percent") or ""))
                if match:
                    return int(match.group(1))
    _, out = _pactl("get-source-volume", name)
    match = re.search(r"(\d+)%", out)
    return int(match.group(1)) if match else 0


def fix_microphone(settings: dict[str, Any]) -> bool:
    """Re-assert mic default, unmute Pulse/WirePlumber/ALSA, reattach capture clients.

    Helps Proton/Wine games that keep a stale or muted capture after Set Input.
    """
    wanted = str(settings.get("device") or "").strip() or default_source()
    name = pick_live_name("source", wanted) if wanted else ""
    if not name:
        return False
    _invalidate_snap()
    entry = _find_device("source", name)
    set_endpoint_mute("source", name, entry, False)
    raise_if_silent = bool(settings.get("raise_if_silent", True))
    if raise_if_silent and _source_volume(name, entry) <= 0:
        _pactl("set-source-volume", name, "100%")
    if not set_default_source(name):
        return False
    if bool(settings.get("kick_source")):
        _pactl("suspend-source", name, "1")
        time.sleep(0.15)
        _pactl("suspend-source", name, "0")
    time.sleep(0.2)
    _invalidate_snap()
    name = pick_live_name("source", name) or name
    _move_source_outputs(name)
    _invalidate_snap()
    current = default_source()
    entry = _find_device("source", name)
    ok = bool(current) and (current == name or same_card(current, name))
    ok = ok and not endpoint_muted("source", name, entry)
    return ok


def _volume_of(target: str) -> int:
    entry = _find_device("sink", target)
    if entry:
        volume = entry.get("volume")
        if isinstance(volume, dict):
            for channel in volume.values():
                if not isinstance(channel, dict):
                    continue
                match = re.search(r"(\d+)", str(channel.get("value_percent") or ""))
                if match:
                    return int(match.group(1))
    _, out = _pactl("get-sink-volume", target)
    match = re.search(r"(\d+)%", out)
    return int(match.group(1)) if match else 0


def _muted(target: str) -> bool:
    _, out = _pactl("get-sink-mute", target)
    return "yes" in out.lower()


def _source_muted(target: str) -> bool:
    _, out = _pactl("get-source-mute", target)
    return "yes" in out.lower()


_MIXER_HEAD = re.compile(r"Simple mixer control '([^']+)',(\d+)")


def _alsa_card(entry: dict[str, Any] | None) -> str | None:
    if not entry:
        return None
    props = entry.get("properties") if isinstance(entry.get("properties"), dict) else {}
    card = props.get("alsa.card")
    if card is None or str(card) == "":
        return None
    return str(card)


def _node_id(entry: dict[str, Any] | None) -> str | None:
    if not entry:
        return None
    props = entry.get("properties") if isinstance(entry.get("properties"), dict) else {}
    oid = props.get("object.id")
    if oid is None or str(oid) == "":
        return None
    return str(oid)


def _capture_switch_ids(amixer_text: str) -> list[str]:
    ids: list[str] = []
    for block in re.split(r"(?=Simple mixer control )", amixer_text):
        if "cswitch" not in block:
            continue
        head = _MIXER_HEAD.search(block)
        if head is None:
            continue
        has_capture = False
        for line in block.splitlines():
            if "Capture" not in line:
                continue
            flags = re.findall(r"\[([^\]]+)\]", line)
            if flags and flags[-1] in {"on", "off"}:
                has_capture = True
                break
        if has_capture:
            ids.append(f"{head.group(1)},{head.group(2)}")
    mic = [item for item in ids if item.lower().startswith("mic,")]
    return mic or ids


def _alsa_capture_muted(card: str) -> bool | None:
    code, out = _amixer("-c", card)
    if code != 0 or not out:
        return None
    ids = _capture_switch_ids(out)
    if not ids:
        return None
    muted = False
    for block in re.split(r"(?=Simple mixer control )", out):
        head = _MIXER_HEAD.search(block)
        if head is None:
            continue
        ident = f"{head.group(1)},{head.group(2)}"
        if ident not in ids:
            continue
        for line in block.splitlines():
            if "Capture" not in line:
                continue
            flags = re.findall(r"\[([^\]]+)\]", line)
            if flags and flags[-1] == "off":
                muted = True
    return muted


def _set_alsa_capture(card: str, muted: bool) -> bool:
    code, out = _amixer("-c", card)
    if code != 0:
        return False
    ids = _capture_switch_ids(out)
    if not ids:
        return False
    verb = "nocap" if muted else "cap"
    ok = False
    for ident in ids:
        set_code, _ = _amixer("-c", card, "sset", ident, verb)
        if set_code == 0:
            ok = True
    return ok


def _wpctl_muted(node_id: str) -> bool | None:
    code, out = _wpctl("get-volume", node_id)
    if code != 0 or not out:
        return None
    return "muted" in out.lower()


def endpoint_muted(kind: str, name: str, entry: dict[str, Any] | None) -> bool:
    if entry is not None and "mute" in entry:
        if bool(entry.get("mute")):
            return True
        if kind == "sink":
            return False
    elif name:
        pulse = _muted(name) if kind == "sink" else _source_muted(name)
        if pulse:
            return True
    if kind != "source":
        return False
    card = _alsa_card(entry)
    alsa = _snapshot().setdefault("alsa_cap", {})
    if card:
        if card not in alsa:
            alsa[card] = _alsa_capture_muted(card)
        if alsa[card] is True:
            return True
    return False


def set_endpoint_mute(kind: str, name: str, entry: dict[str, Any] | None, muted: bool) -> bool:
    flag = "1" if muted else "0"
    if kind == "sink":
        code, _ = _pactl("set-sink-mute", name, flag)
    else:
        code, _ = _pactl("set-source-mute", name, flag)
    node = _node_id(entry)
    if node:
        _wpctl("set-mute", node, flag)
    if kind == "source":
        card = _alsa_card(entry)
        if card:
            _set_alsa_capture(card, muted)
    _invalidate_snap()
    return code == 0 or endpoint_muted(kind, name, entry) is muted


def toggle_endpoint_mute(kind: str, settings: dict[str, Any]) -> bool:
    target = sink_target(settings) if kind == "sink" else source_target(settings)
    name = resolve_sink(target) if kind == "sink" else resolve_source(target)
    if not name:
        return False
    entry = _find_device(kind, name)
    return set_endpoint_mute(kind, name, entry, not endpoint_muted(kind, name, entry))


def sink_target(settings: dict[str, Any]) -> str:
    return settings.get("device") or "@DEFAULT_SINK@"


def source_target(settings: dict[str, Any]) -> str:
    return settings.get("device") or "@DEFAULT_SOURCE@"


def _port_available(entry: dict[str, Any]) -> bool:
    active = entry.get("active_port")
    ports = entry.get("ports") or []
    avail = ""
    if isinstance(active, dict):
        avail = str(active.get("availability") or "")
    elif isinstance(active, str):
        for port in ports:
            if isinstance(port, dict) and port.get("name") == active:
                avail = str(port.get("availability") or "")
                break
    return "unavail" not in avail.lower()


def _find_device(kind: str, name: str) -> dict[str, Any] | None:
    if not name:
        return None
    rows = _live_rows(kind)
    for row in rows:
        if str(row.get("name") or "") == name:
            return row
    for row in rows:
        if same_card(str(row.get("name") or ""), name):
            return row
    return None


def resolve_sink(target: str) -> str:
    if not target or target == "@DEFAULT_SINK@":
        current = default_sink()
        # While eqFX owns the default sink, volume keys should hit the hardware
        # destination apps are actually hearing.
        if current and _is_eqfx_sink(current):
            active = active_output_sink()
            if active:
                return active
        return current
    return target


def _load_device_volumes() -> dict[str, Any]:
    if not DEVICE_VOLUMES_PATH.is_file():
        return {}
    try:
        raw = json.loads(DEVICE_VOLUMES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_device_volumes(payload: dict[str, Any]) -> None:
    try:
        DEVICE_VOLUMES_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEVICE_VOLUMES_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except OSError:
        pass


def _remember_hw_volume(name: str) -> None:
    if not name or _is_eqfx_sink(name):
        return
    volumes = _load_device_volumes()
    volumes[name] = {"volume": _volume_of(name), "mute": _muted(name)}
    _write_device_volumes(volumes)


def _restore_hw_volume(name: str) -> bool:
    if not name or _is_eqfx_sink(name):
        return False
    entry = _load_device_volumes().get(name)
    if not isinstance(entry, dict):
        return False
    restored = False
    if "volume" in entry:
        try:
            percent = max(0, min(150, int(entry["volume"])))
        except (TypeError, ValueError):
            percent = None
        if percent is not None:
            _pactl("set-sink-volume", name, f"{percent}%")
            restored = True
    if "mute" in entry:
        _pactl("set-sink-mute", name, "1" if entry["mute"] else "0")
        restored = True
    return restored


def resolve_source(target: str) -> str:
    if not target or target == "@DEFAULT_SOURCE@":
        return default_source()
    return target


def _device_label(kind: str, name: str, items: list[tuple[str, str]], settings: dict[str, Any]) -> str:
    stored = str(settings.get("device_label") or "").strip()
    pretty = _short(_pretty(name, items)) if name else ""
    if pretty and pretty != name:
        return pretty
    if stored:
        return stored
    if name:
        return _short(name.split(".")[-1] if len(name) > 28 else name)
    return "Output" if kind == "sink" else "Mic"


def paint_endpoint(ctx: ActionContext, kind: str, mode: str = "switch") -> None:
    settings = ctx.settings
    target = sink_target(settings) if kind == "sink" else source_target(settings)
    items = list_sinks() if kind == "sink" else list_sources()
    chosen = str(settings.get("device") or "")
    raw = resolve_sink(target) if kind == "sink" else resolve_source(target)
    name = pick_live_name(kind, raw) if raw else ""
    entry = _find_device(kind, name)
    available = entry is not None and _port_available(entry)
    muted = endpoint_muted(kind, name, entry) if name else True
    current = default_sink() if kind == "sink" else default_source()

    if not available:
        ctx.set_state("danger")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            ctx.set_title(_device_label(kind, chosen or name, items, settings) if (chosen or name) else "OFF")
        return

    if mode in {"mute", "volume"} and muted:
        ctx.set_state("danger")
        ctx.set_title("MUTE" if kind == "sink" else "MIC OFF")
        return

    selected = active_output_sink() if kind == "sink" else current
    if mode == "switch" and chosen and (chosen == selected or same_card(chosen, selected)):
        ctx.set_state("ok")
    else:
        ctx.set_state("")
    if mode == "mute" and kind != "sink":
        ctx.set_title("MIC ON")
    elif mode in {"mute", "volume"}:
        ctx.set_title(f"{_volume_of(name or target)}%")
    elif ctx.slot_title.strip():
        ctx.set_title(None)
    else:
        ctx.set_title(_device_label(kind, name, items, settings))


class _LiveAction(Action):
    _shared: QTimer | None = None
    _listeners: list[_LiveAction] = []

    def __init__(self) -> None:
        self._ctx: ActionContext | None = None

    def will_appear(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        if self not in _LiveAction._listeners:
            _LiveAction._listeners.append(self)
        if _LiveAction._shared is None:
            _LiveAction._shared = QTimer()
            _LiveAction._shared.timeout.connect(_LiveAction._tick_all)
            _LiveAction._shared.start(1000)
        self._tick()

    def will_disappear(self, ctx: ActionContext) -> None:
        if self in _LiveAction._listeners:
            _LiveAction._listeners.remove(self)
        self._ctx = None
        if not _LiveAction._listeners and _LiveAction._shared is not None:
            _LiveAction._shared.stop()

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._tick()

    @classmethod
    def _tick_all(cls) -> None:
        _snapshot(force=True)
        for action in list(cls._listeners):
            action._tick()

    def _tick(self) -> None:
        if self._ctx is None:
            return
        self.update_visual(self._ctx)

    def update_visual(self, ctx: ActionContext) -> None:
        return


def list_players() -> list[str]:
    code, out = _playerctl("-l")
    if code != 0 or not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


class _DeviceInspector(QWidget):
    def __init__(self, ctx: ActionContext, kind: str, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._kind = kind
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        self._fill()
        self.combo.currentIndexChanged.connect(self._save)
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self._fill)
        layout.addRow("Device", self.combo)
        layout.addRow("", refresh)

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("device") or "")
        items = list_sinks() if self._kind == "sink" else list_sources()
        live = {name for name, _desc in items}
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("Default / current", "")
        for name, desc in items:
            self.combo.addItem(_short(desc), name)
        if current and current not in live:
            label = _short(str(self._ctx.settings.get("device_label") or current))
            self.combo.addItem(f"{label}  (off)", current)
        index = self.combo.findData(current)
        if index < 0 and current:
            for i in range(self.combo.count()):
                if same_card(str(self.combo.itemData(i) or ""), current):
                    index = i
                    break
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.blockSignals(False)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["device"] = self.combo.currentData() or ""
        settings["device_label"] = "" if not settings["device"] else _short(self.combo.currentText())
        self._ctx.set_settings(settings)


class _StepInspector(QWidget):
    def __init__(self, ctx: ActionContext, kind: str, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.step = plain_spin(1, 50, int(ctx.settings.get("step", 5)))
        self.step.setSuffix(" %")
        self.step.valueChanged.connect(self._save)
        self.combo = fit_combo(QComboBox())
        self._kind = kind
        items = list_sinks() if kind == "sink" else list_sources()
        self.combo.addItem("Default / current", "")
        for name, desc in items:
            self.combo.addItem(_short(desc), name)
        index = self.combo.findData(ctx.settings.get("device", ""))
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.combo.currentIndexChanged.connect(self._save)
        layout.addRow("Device", self.combo)
        layout.addRow("Step", self.step)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["step"] = self.step.value()
        settings["device"] = self.combo.currentData() or ""
        settings["device_label"] = "" if not settings["device"] else _short(self.combo.currentText())
        self._ctx.set_settings(settings)


class _LevelInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.level = plain_spin(0, 150, int(ctx.settings.get("level", 50)))
        self.level.setSuffix(" %")
        self.level.valueChanged.connect(self._save)
        self.combo = fit_combo(QComboBox())
        self.combo.addItem("Default / current", "")
        for name, desc in list_sinks():
            self.combo.addItem(_short(desc), name)
        index = self.combo.findData(ctx.settings.get("device", ""))
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.combo.currentIndexChanged.connect(self._save)
        layout.addRow("Device", self.combo)
        layout.addRow("Volume", self.level)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["level"] = self.level.value()
        settings["device"] = self.combo.currentData() or ""
        settings["device_label"] = "" if not settings["device"] else _short(self.combo.currentText())
        self._ctx.set_settings(settings)


class _PlayerInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        self.combo.addItem("Any player", "")
        for player in list_players():
            self.combo.addItem(player, player)
        index = self.combo.findData(ctx.settings.get("player", ""))
        if index >= 0:
            self.combo.setCurrentIndex(index)
        self.combo.currentIndexChanged.connect(self._save)
        layout.addRow("Player", self.combo)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["player"] = self.combo.currentData() or ""
        self._ctx.set_settings(settings)


class _PresetInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search presets")
        self.search.textChanged.connect(self._fill)
        self.category = fit_combo(QComboBox())
        self.category.addItem("All")
        for category in eqfx_preset_categories():
            self.category.addItem(category)
        self.category.currentTextChanged.connect(self._fill)
        self.combo = fit_combo(QComboBox())
        self.combo.setMaxVisibleItems(16)
        self._fill()
        self.combo.currentIndexChanged.connect(self._save)
        layout.addRow("Search", self.search)
        layout.addRow("Bank", self.category)
        layout.addRow("Preset", self.combo)

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("preset") or "")
        query = self.search.text().strip().lower()
        bank = self.category.currentText()
        self.combo.blockSignals(True)
        self.combo.clear()
        for ident, name, category in list_eqfx_presets():
            blob = f"{name} {category} {ident}".lower()
            if query and query not in blob:
                continue
            if bank not in {"", "All"} and category != bank:
                continue
            self.combo.addItem(f"{name}  ·  {category}", ident)
        index = self.combo.findData(current)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        elif self.combo.count() and not current:
            self.combo.setCurrentIndex(0)
            self._save()
        elif self.combo.count():
            self.combo.setCurrentIndex(0)
        self.combo.blockSignals(False)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        ident = self.combo.currentData() or ""
        settings["preset"] = ident
        settings["preset_label"] = preset_label(ident) if ident else ""
        self._ctx.set_settings(settings)


class SetEqPresetAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        presets = list_eqfx_presets()
        chosen = str(ctx.settings.get("preset") or "")
        current = active_eqfx_preset()
        if not presets or (chosen and chosen not in {ident for ident, _name, _cat in presets}):
            ctx.set_state("danger")
        elif chosen and chosen == current:
            ctx.set_state("ok")
        else:
            ctx.set_state("")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            label = str(ctx.settings.get("preset_label") or "") or preset_label(chosen)
            ctx.set_title(label or "EQ Preset")

    def key_down(self, ctx: ActionContext) -> None:
        presets = list_eqfx_presets()
        if not presets:
            ctx.show_alert()
            self.update_visual(ctx)
            return
        chosen = str(ctx.settings.get("preset") or "")
        if not chosen:
            names = [ident for ident, _name, _cat in presets]
            current = active_eqfx_preset()
            nxt = names[(names.index(current) + 1) % len(names)] if current in names else names[0]
            chosen = nxt
        if set_eqfx_preset(chosen):
            ctx.show_ok()
        else:
            ctx.show_alert()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _PresetInspector(ctx, parent)


class SetOutputAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        paint_endpoint(ctx, "sink", mode="switch")

    def key_down(self, ctx: ActionContext) -> None:
        sinks = list_sinks()
        if not sinks:
            ctx.show_alert()
            self.update_visual(ctx)
            return
        chosen = ctx.settings.get("device") or ""
        if not chosen:
            names = [name for name, _ in sinks]
            current = active_output_sink()
            nxt = names[(names.index(current) + 1) % len(names)] if current in names else names[0]
            chosen = nxt
        chosen = pick_live_name("sink", chosen)
        if set_default_sink(chosen):
            ctx.show_ok()
        else:
            ctx.show_alert()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DeviceInspector(ctx, "sink", parent)


class SetInputAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        paint_endpoint(ctx, "source", mode="switch")

    def key_down(self, ctx: ActionContext) -> None:
        sources = list_sources()
        if not sources:
            ctx.show_alert()
            self.update_visual(ctx)
            return
        chosen = ctx.settings.get("device") or ""
        if not chosen:
            names = [name for name, _ in sources]
            current = default_source()
            nxt = names[(names.index(current) + 1) % len(names)] if current in names else names[0]
            chosen = nxt
        chosen = pick_live_name("source", chosen)
        if set_default_source(chosen):
            ctx.show_ok()
        else:
            ctx.show_alert()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DeviceInspector(ctx, "source", parent)


class VolumeUpAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        paint_endpoint(ctx, "sink", mode="volume")

    def key_down(self, ctx: ActionContext) -> None:
        step = int(ctx.settings.get("step", 5))
        target = sink_target(ctx.settings)
        code, _ = _pactl("set-sink-volume", target, f"+{step}%")
        _invalidate_snap()
        if code != 0:
            ctx.show_alert()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _StepInspector(ctx, "sink", parent)


class VolumeDownAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        paint_endpoint(ctx, "sink", mode="volume")

    def key_down(self, ctx: ActionContext) -> None:
        step = int(ctx.settings.get("step", 5))
        target = sink_target(ctx.settings)
        code, _ = _pactl("set-sink-volume", target, f"-{step}%")
        _invalidate_snap()
        if code != 0:
            ctx.show_alert()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _StepInspector(ctx, "sink", parent)


class SetVolumeAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        paint_endpoint(ctx, "sink", mode="volume")

    def key_down(self, ctx: ActionContext) -> None:
        level = int(ctx.settings.get("level", 50))
        target = sink_target(ctx.settings)
        code, _ = _pactl("set-sink-volume", target, f"{level}%")
        _invalidate_snap()
        if code != 0:
            ctx.show_alert()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _LevelInspector(ctx, parent)


class MuteAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        paint_endpoint(ctx, "sink", mode="mute")

    def key_down(self, ctx: ActionContext) -> None:
        if not toggle_endpoint_mute("sink", ctx.settings):
            ctx.show_alert()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DeviceInspector(ctx, "sink", parent)


class MuteMicAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        paint_endpoint(ctx, "source", mode="mute")

    def key_down(self, ctx: ActionContext) -> None:
        if not toggle_endpoint_mute("source", ctx.settings):
            ctx.show_alert()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DeviceInspector(ctx, "source", parent)


class _FixMicInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        self._fill()
        self.combo.currentIndexChanged.connect(self._save)
        refresh = QPushButton("Refresh devices")
        refresh.clicked.connect(self._fill)
        self.raise_silent = QCheckBox("Raise volume if at 0%")
        self.raise_silent.setChecked(bool(ctx.settings.get("raise_if_silent", True)))
        self.raise_silent.toggled.connect(self._save)
        self.kick = QCheckBox("Suspend/resume source (harder kick)")
        self.kick.setChecked(bool(ctx.settings.get("kick_source", False)))
        self.kick.toggled.connect(self._save)
        layout.addRow("Device", self.combo)
        layout.addRow("", refresh)
        layout.addRow("", self.raise_silent)
        layout.addRow("", self.kick)

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("device") or "")
        items = list_sources()
        live = {name for name, _desc in items}
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("Default / current", "")
        for name, desc in items:
            self.combo.addItem(_short(desc), name)
        if current and current not in live:
            label = _short(str(self._ctx.settings.get("device_label") or current))
            self.combo.addItem(f"{label}  (off)", current)
        index = self.combo.findData(current)
        if index < 0 and current:
            for i in range(self.combo.count()):
                if same_card(str(self.combo.itemData(i) or ""), current):
                    index = i
                    break
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.blockSignals(False)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["device"] = self.combo.currentData() or ""
        settings["device_label"] = "" if not settings["device"] else _short(self.combo.currentText())
        settings["raise_if_silent"] = self.raise_silent.isChecked()
        settings["kick_source"] = self.kick.isChecked()
        self._ctx.set_settings(settings)


class FixMicAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        paint_endpoint(ctx, "source", mode="switch")
        label = str(ctx.settings.get("device_label") or "").strip()
        if not label:
            name = resolve_source(source_target(ctx.settings))
            label = _device_label("source", name, list_sources(), ctx.settings)
        ctx.set_title(f"Fix\n{_short(label)}" if label else "Fix Mic")

    def key_down(self, ctx: ActionContext) -> None:
        if fix_microphone(ctx.settings):
            ctx.show_ok()
        else:
            ctx.show_alert()
            ctx.log("Fix Mic could not re-assert the capture device")
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _FixMicInspector(ctx, parent)


class VolumeMeterAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        paint_endpoint(ctx, "sink", mode="volume")

    def key_down(self, ctx: ActionContext) -> None:
        toggle_endpoint_mute("sink", ctx.settings)
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DeviceInspector(ctx, "sink", parent)


class MediaAction(Action):
    command = "play-pause"

    def key_down(self, ctx: ActionContext) -> None:
        player = ctx.settings.get("player") or ""
        args = ["-p", player, self.command] if player else [self.command]
        code, _ = _playerctl(*args)
        if code != 0:
            ctx.show_alert()
            return
        ctx.show_ok()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _PlayerInspector(ctx, parent)


class PlayPauseAction(MediaAction):
    command = "play-pause"


class NextTrackAction(MediaAction):
    command = "next"


class PrevTrackAction(MediaAction):
    command = "previous"


class StopAction(MediaAction):
    command = "stop"


class _MicPeakSampler:
    """Shared background capture reader for live mic peak levels."""

    _instance: _MicPeakSampler | None = None

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._peak = 0.0
        self._source = ""
        self._wanted = ""
        self._refs = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._proc: subprocess.Popen[bytes] | None = None

    @classmethod
    def shared(cls) -> _MicPeakSampler:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def acquire(self, source: str) -> None:
        with self._lock:
            self._refs += 1
            self._wanted = source
            if self._thread is None or not self._thread.is_alive():
                self._stop.clear()
                self._thread = threading.Thread(target=self._run, name="popstream-mic-peak", daemon=True)
                self._thread.start()

    def release(self) -> None:
        with self._lock:
            self._refs = max(0, self._refs - 1)
            if self._refs == 0:
                self._stop.set()
                self._wanted = ""
        self._kill_proc()

    def set_source(self, source: str) -> None:
        with self._lock:
            if source != self._wanted:
                self._wanted = source
                self._peak = 0.0

    def peak(self) -> float:
        with self._lock:
            return self._peak

    def _kill_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=0.4)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if self._refs <= 0:
                    break
                source = self._wanted
            if not source or not shutil.which("parec"):
                time.sleep(0.25)
                with self._lock:
                    self._peak *= 0.7
                continue
            args = [
                "parec",
                "--raw",
                "--format=s16le",
                "--rate=16000",
                "--channels=1",
                "--latency-msec=40",
                f"--device={source}",
            ]
            try:
                self._proc = subprocess.Popen(
                    args,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                )
            except OSError:
                time.sleep(0.4)
                continue
            assert self._proc.stdout is not None
            chunk = 640  # 20ms at 16k mono s16
            while not self._stop.is_set():
                with self._lock:
                    if self._refs <= 0 or self._wanted != source:
                        break
                data = self._proc.stdout.read(chunk)
                if not data:
                    break
                count = len(data) // 2
                if count <= 0:
                    continue
                samples = struct.unpack("<" + "h" * count, data[: count * 2])
                instant = max(abs(s) for s in samples) / 32768.0
                with self._lock:
                    self._peak = max(instant, self._peak * 0.82)
                    self._source = source
            self._kill_proc()
            time.sleep(0.05)
        with self._lock:
            self._peak = 0.0
            self._thread = None


def _focused_app_label() -> tuple[str, str]:
    """Return (short_label, match_blob) for the focused window."""
    if shutil.which("hyprctl"):
        code, out = _run_bin("hyprctl", "-j", "activewindow")
        if code == 0 and out:
            try:
                data = json.loads(out)
            except json.JSONDecodeError:
                data = {}
            if isinstance(data, dict) and data.get("address"):
                klass = str(data.get("class") or "")
                title = str(data.get("title") or "")
                label = title or klass or "Desktop"
                if len(label) > 18:
                    label = label[:16] + "…"
                return label, f"{klass} {title}".lower()
    if shutil.which("xdotool"):
        code, wid = _run_bin("xdotool", "getactivewindow")
        if code == 0 and wid:
            _, title = _run_bin("xdotool", "getwindowname", wid.strip())
            _, klass = _run_bin("xdotool", "getwindowclassname", wid.strip())
            label = (title or klass or "Desktop").strip()
            if len(label) > 18:
                label = label[:16] + "…"
            return label, f"{klass} {title}".lower()
    return "Desktop", ""


def _source_output_apps(source_name: str) -> list[str]:
    rows = _json_list("source-outputs")
    apps: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
        src = str(
            props.get("application.process.binary")
            or props.get("application.name")
            or props.get("media.name")
            or ""
        )
        # Pulse JSON may put source index elsewhere; fall back to matching via short list
        apps.append(src.lower())
    # Prefer short list mapping output -> source name when available
    _, short = _pactl("list", "short", "source-outputs")
    named: list[str] = []
    for line in short.splitlines():
        parts = line.split("\t") if "\t" in line else line.split()
        if len(parts) < 3:
            continue
        # index, source, client, ...
        src = parts[1]
        if src == source_name or same_card(src, source_name):
            # try to find matching row by index
            idx = parts[0]
            for row in rows:
                if str(row.get("index")) == idx:
                    props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
                    blob = " ".join(
                        str(props.get(k) or "")
                        for k in (
                            "application.process.binary",
                            "application.name",
                            "media.name",
                            "application.process.host",
                        )
                    ).lower()
                    if blob.strip():
                        named.append(blob)
            if not named and len(parts) >= 4:
                named.append(parts[3].lower())
    return named or apps


def _focused_app_using_mic(source_name: str, match_blob: str) -> bool:
    if not match_blob:
        return False
    tokens = [t for t in re.split(r"[\s._-]+", match_blob) if len(t) >= 3]
    for app in _source_output_apps(source_name):
        if not app:
            continue
        if any(tok in app for tok in tokens):
            return True
        if any(tok in match_blob for tok in re.split(r"[\s._-]+", app) if len(tok) >= 3):
            return True
    return False


def _mic_check_image(peak: float, muted: bool, capturing: bool, size: int = 144) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor("#121212"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = size * 0.12
    bar = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    painter.setPen(QPen(QColor("#2a2a2a"), max(2, size // 48)))
    painter.setBrush(QColor("#1a1a1a"))
    painter.drawRoundedRect(bar, 10, 10)

    level = 0.0 if muted else max(0.0, min(1.0, peak))
    segments = 10
    gap = bar.height() * 0.03
    seg_h = (bar.height() - gap * (segments + 1)) / segments
    filled = int(round(level * segments))
    for i in range(segments):
        y = bar.bottom() - gap - (i + 1) * (seg_h + gap) + gap
        rect = QRectF(bar.left() + bar.width() * 0.22, y, bar.width() * 0.56, seg_h)
        if i < filled:
            if i >= segments - 2:
                color = QColor(C.danger)
            elif i >= segments - 4:
                color = QColor("#e0a100")
            else:
                color = QColor(C.ok if capturing else C.accent)
            painter.fillRect(rect, color)
        else:
            painter.fillRect(rect, QColor("#2b2b2b"))

    painter.setPen(QColor(C.muted))
    font = QFont()
    font.setPixelSize(max(10, size // 9))
    font.setBold(True)
    painter.setFont(font)
    label = "MUTE" if muted else f"{int(level * 100)}%"
    painter.drawText(QRectF(0, size * 0.02, size, size * 0.16), int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter), label)
    painter.end()
    return image


class MicCheckAction(Action):
    """Live mic VU bar plus focused-app label for in-game mic checks."""

    def __init__(self) -> None:
        self._ctx: ActionContext | None = None
        self._timer: QTimer | None = None
        self._sampler = _MicPeakSampler.shared()
        self._source = ""

    def will_appear(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._source = self._resolve_source(ctx)
        self._sampler.acquire(self._source)
        if self._timer is None:
            self._timer = QTimer()
            self._timer.timeout.connect(self._tick)
        self._timer.start(120)
        self._tick()

    def will_disappear(self, ctx: ActionContext) -> None:
        if self._timer is not None:
            self._timer.stop()
        self._sampler.release()
        self._ctx = None

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._source = self._resolve_source(ctx)
        self._sampler.set_source(self._source)
        self._tick()

    def _resolve_source(self, ctx: ActionContext) -> str:
        wanted = str(ctx.settings.get("device") or "").strip() or default_source()
        return pick_live_name("source", wanted) if wanted else ""

    def _tick(self) -> None:
        ctx = self._ctx
        if ctx is None:
            return
        source = self._resolve_source(ctx)
        if source != self._source:
            self._source = source
            self._sampler.set_source(source)
        entry = _find_device("source", source) if source else None
        muted = endpoint_muted("source", source, entry) if source else True
        peak = 0.0 if muted or not source else self._sampler.peak()
        app_label, match_blob = _focused_app_label()
        capturing = bool(source) and _focused_app_using_mic(source, match_blob)
        if muted:
            ctx.set_state("danger")
        elif capturing and peak > 0.02:
            ctx.set_state("ok")
        elif peak > 0.02:
            ctx.set_state("")
        else:
            ctx.set_state("danger" if not source else "")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            suffix = "IN" if capturing else ("MUTE" if muted else "MIC")
            ctx.set_title(f"{app_label}\n{suffix}")
        ctx.set_image(_mic_check_image(peak, muted, capturing))

    def key_down(self, ctx: ActionContext) -> None:
        if fix_microphone(ctx.settings):
            ctx.show_ok()
        else:
            ctx.show_alert()
        self._tick()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DeviceInspector(ctx, "source", parent)


class AudioPlugin(Plugin):
    id = "com.popstream.audio"
    name = "Audio"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo("set-output", "Set Output", "Audio", "Switch the default speakers / headphones", "speaker"),
            ActionInfo("eq-preset", "EQ Preset", "Audio", "Load an eqFX curve on the current output", "eq"),
            ActionInfo("set-input", "Set Input", "Audio", "Switch the default microphone", "mic"),
            ActionInfo(
                "fix-mic",
                "Fix Mic",
                "Audio",
                "Re-apply mic default, unmute, and reattach games / Proton capture",
                "mic",
            ),
            ActionInfo(
                "mic-check",
                "Mic Check",
                "Audio",
                "Live mic level bar for the focused app; press to Fix Mic",
                "mic",
            ),
            ActionInfo("vol-up", "Volume Up", "Audio", "Raise output volume", "volup"),
            ActionInfo("vol-down", "Volume Down", "Audio", "Lower output volume", "voldown"),
            ActionInfo("set-volume", "Set Volume", "Audio", "Jump to a specific volume", "speaker"),
            ActionInfo("mute", "Mute", "Audio", "Toggle speaker mute", "mute"),
            ActionInfo("mute-mic", "Mute Mic", "Audio", "Toggle microphone mute", "mic"),
            ActionInfo("volume", "Volume Display", "Audio", "Live volume; press to mute", "speaker"),
            ActionInfo("play-pause", "Play / Pause", "Audio", "Toggle the media player", "play"),
            ActionInfo("next-track", "Next Track", "Audio", "Skip to the next track", "next"),
            ActionInfo("prev-track", "Previous Track", "Audio", "Go to the previous track", "prev"),
            ActionInfo("stop", "Stop", "Audio", "Stop playback", "stop"),
        ]

    def create_action(self, action_id: str) -> Action | None:
        mapping = {
            "set-output": SetOutputAction,
            "eq-preset": SetEqPresetAction,
            "set-input": SetInputAction,
            "fix-mic": FixMicAction,
            "mic-check": MicCheckAction,
            "vol-up": VolumeUpAction,
            "vol-down": VolumeDownAction,
            "set-volume": SetVolumeAction,
            "mute": MuteAction,
            "mute-mic": MuteMicAction,
            "volume": VolumeMeterAction,
            "play-pause": PlayPauseAction,
            "next-track": NextTrackAction,
            "prev-track": PrevTrackAction,
            "stop": StopAction,
        }
        cls = mapping.get(action_id)
        return cls() if cls else None
