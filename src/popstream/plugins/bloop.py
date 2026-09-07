from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QPushButton,
    QWidget,
)

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import fit_combo, plain_spin, tighten_form

DEFAULT_URL = "http://127.0.0.1:17380"


def _ipc_candidates() -> list[Path]:
    home = Path.home()
    here = Path(__file__).resolve()
    candidates = [
        home / ".local" / "share" / "Bloop" / "ipc.json",
        home / ".local" / "share" / "bloop" / "ipc.json",
        Path.cwd() / ".bloop-data" / "ipc.json",
    ]
    try:
        candidates.append(here.parents[4] / "bloop" / ".bloop-data" / "ipc.json")
    except IndexError:
        pass
    return candidates


def bloop_url() -> str:
    env = os.environ.get("BLOOP_URL", "").strip()
    if env:
        return env.rstrip("/")
    for path in _ipc_candidates():
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        url = str(data.get("url") or "").rstrip("/")
        if url:
            return url
        port = data.get("port")
        if port:
            return f"http://127.0.0.1:{int(port)}"
    return DEFAULT_URL


def bloop_request(method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 0.6) -> dict[str, Any] | None:
    url = bloop_url().rstrip("/") + path
    body = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode() or "{}")
            return data if isinstance(data, dict) else None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def bloop_status() -> dict[str, Any] | None:
    return bloop_request("GET", "/v1/status")


def bloop_library() -> dict[str, Any] | None:
    return bloop_request("GET", "/v1/library", timeout=1.2)


def bloop_sounds() -> list[dict[str, Any]]:
    data = bloop_library()
    if not data:
        return []
    sounds = data.get("sounds") or []
    return [row for row in sounds if isinstance(row, dict)]


def bloop_categories() -> list[dict[str, Any]]:
    data = bloop_library()
    if not data:
        return []
    cats = data.get("categories") or []
    return [row for row in cats if isinstance(row, dict)]


def _playing_ids(status: dict[str, Any] | None) -> set[str]:
    if not status:
        return set()
    return {str(item.get("id") or "") for item in status.get("playing") or [] if isinstance(item, dict)}


class _LiveAction(Action):
    interval = 900

    def __init__(self) -> None:
        self._timer: QTimer | None = None
        self._ctx: ActionContext | None = None

    def will_appear(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        if self._timer is None:
            self._timer = QTimer()
            self._timer.timeout.connect(self._tick)
        self._timer.start(self.interval)
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
        self.update_visual(self._ctx)

    def update_visual(self, ctx: ActionContext) -> None:
        return


class _SoundInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None, *, toggle: bool = False) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        self.combo.currentIndexChanged.connect(self._save)
        self.preview = QCheckBox("Speakers only (preview)")
        self.preview.setChecked(bool(ctx.settings.get("preview")))
        self.preview.toggled.connect(self._save)
        refresh = QPushButton("Refresh library")
        refresh.clicked.connect(self._fill)
        layout.addRow("Sound", self.combo)
        layout.addRow("", refresh)
        layout.addRow("", self.preview)
        self._fill()

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("sound_id") or "")
        sounds = bloop_sounds()
        self.combo.blockSignals(True)
        self.combo.clear()
        if not sounds:
            self.combo.addItem("Bloop is not running", "")
        else:
            self.combo.addItem("Select a sound", "")
            for sound in sounds:
                ident = str(sound.get("id") or "")
                name = str(sound.get("name") or ident)
                self.combo.addItem(name, ident)
        index = self.combo.findData(current)
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.blockSignals(False)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["sound_id"] = self.combo.currentData() or ""
        settings["sound_name"] = "" if not settings["sound_id"] else self.combo.currentText()
        settings["preview"] = self.preview.isChecked()
        self._ctx.set_settings(settings)


class _CategoryInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        self.combo.currentIndexChanged.connect(self._save)
        refresh = QPushButton("Refresh library")
        refresh.clicked.connect(self._fill)
        layout.addRow("Category", self.combo)
        layout.addRow("", refresh)
        self._fill()

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("category_id") or "all")
        cats = bloop_categories()
        self.combo.blockSignals(True)
        self.combo.clear()
        if not cats:
            self.combo.addItem("Bloop is not running", "")
        else:
            for cat in cats:
                self.combo.addItem(str(cat.get("name") or "Category"), str(cat.get("id") or "all"))
        index = self.combo.findData(current)
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.blockSignals(False)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["category_id"] = self.combo.currentData() or "all"
        settings["category_name"] = self.combo.currentText()
        self._ctx.set_settings(settings)


class _StepInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.step = plain_spin(1, 25, int(ctx.settings.get("step", 5)))
        self.step.setSuffix(" %")
        self.step.valueChanged.connect(self._save)
        layout.addRow("Step", self.step)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["step"] = self.step.value()
        self._ctx.set_settings(settings)


def _paint_offline_or_title(ctx: ActionContext, status: dict[str, Any] | None, fallback: str) -> bool:
    if not status:
        ctx.set_state("danger")
        ctx.set_title("OFF")
        return False
    if ctx.slot_title.strip():
        ctx.set_title(None)
    else:
        ctx.set_title(fallback)
    return True


class PlaySoundAction(_LiveAction):
    toggle = False

    def update_visual(self, ctx: ActionContext) -> None:
        status = bloop_status()
        name = str(ctx.settings.get("sound_name") or "").strip()
        sound_id = str(ctx.settings.get("sound_id") or "")
        if not status:
            ctx.set_state("danger")
            ctx.set_title("OFF")
            return
        if sound_id and sound_id in _playing_ids(status):
            ctx.set_state("ok")
        else:
            ctx.set_state("")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            ctx.set_title(name or ("Play/Stop" if self.toggle else "Play"))

    def key_down(self, ctx: ActionContext) -> None:
        sound_id = str(ctx.settings.get("sound_id") or "")
        if not sound_id:
            ctx.show_alert()
            return
        if self.toggle and sound_id in _playing_ids(bloop_status()):
            result = bloop_request("POST", "/v1/stop", {"id": sound_id}, timeout=2)
        else:
            result = bloop_request(
                "POST",
                "/v1/play",
                {"id": sound_id, "preview": bool(ctx.settings.get("preview"))},
                timeout=2,
            )
        if not result or not result.get("ok", True):
            ctx.show_alert()
        else:
            ctx.show_ok()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _SoundInspector(ctx, parent, toggle=self.toggle)


class PlayStopAction(PlaySoundAction):
    toggle = True


class StopAllAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        result = bloop_request("POST", "/v1/stop", {}, timeout=2)
        if not result:
            ctx.show_alert()
            return
        ctx.show_ok()


class PlayRandomAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        status = bloop_status()
        name = str(ctx.settings.get("category_name") or "All").strip()
        if not _paint_offline_or_title(ctx, status, name if name != "All" else "Random"):
            return
        ctx.set_state("ok" if status and status.get("playing") else "")

    def key_down(self, ctx: ActionContext) -> None:
        category = str(ctx.settings.get("category_id") or "all")
        result = bloop_request("POST", "/v1/play", {"random": True, "category_id": category}, timeout=2)
        if not result or not result.get("ok"):
            ctx.show_alert()
        else:
            ctx.show_ok()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _CategoryInspector(ctx, parent)


class CableAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        status = bloop_status()
        if not status:
            ctx.set_state("danger")
            ctx.set_title("OFF")
            return
        cable = status.get("cable") if isinstance(status.get("cable"), dict) else {}
        enabled = bool(cable.get("enabled"))
        healthy = bool(cable.get("healthy"))
        if enabled and healthy:
            ctx.set_state("ok")
        elif enabled:
            ctx.set_state("danger")
        else:
            ctx.set_state("")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            ctx.set_title("ON" if enabled else "OFF")

    def key_down(self, ctx: ActionContext) -> None:
        result = bloop_request("POST", "/v1/cable", {}, timeout=2)
        if not result:
            ctx.show_alert()
        self.update_visual(ctx)


class NowPlayingAction(_LiveAction):
    interval = 500

    def update_visual(self, ctx: ActionContext) -> None:
        status = bloop_status()
        if not status:
            ctx.set_state("danger")
            ctx.set_title("OFF")
            return
        name = str(status.get("now_playing") or "").strip()
        if name:
            ctx.set_state("ok")
            ctx.set_title(name[:18])
        else:
            ctx.set_state("")
            ctx.set_title("—")

    def key_down(self, ctx: ActionContext) -> None:
        result = bloop_request("POST", "/v1/stop", {}, timeout=2)
        if not result:
            ctx.show_alert()
        self.update_visual(ctx)


class VolumeAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        status = bloop_status()
        if not status:
            ctx.set_state("danger")
            ctx.set_title("OFF")
            return
        ctx.set_state("")
        ctx.set_title(f"{int(status.get('volume') or 0)}%")

    def key_down(self, ctx: ActionContext) -> None:
        status = bloop_status()
        if not status:
            ctx.show_alert()
            return
        current = int(status.get("volume") or 0)
        result = bloop_request("POST", "/v1/volume", {"level": 0 if current else 100}, timeout=2)
        if not result:
            ctx.show_alert()
        self.update_visual(ctx)


class VolumeStepAction(_LiveAction):
    delta = 5

    def update_visual(self, ctx: ActionContext) -> None:
        status = bloop_status()
        if not status:
            ctx.set_state("danger")
            ctx.set_title("OFF")
            return
        ctx.set_state("")
        ctx.set_title(f"{int(status.get('volume') or 0)}%")

    def key_down(self, ctx: ActionContext) -> None:
        step = int(ctx.settings.get("step", abs(self.delta)))
        if self.delta < 0:
            step = -step
        result = bloop_request("POST", "/v1/volume", {"step": step}, timeout=2)
        if not result:
            ctx.show_alert()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _StepInspector(ctx, parent)


class VolumeUpAction(VolumeStepAction):
    delta = 5


class VolumeDownAction(VolumeStepAction):
    delta = -5


class BloopPlugin(Plugin):
    id = "com.popstream.bloop"
    name = "Bloop"
    version = "0.2.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo("play", "Play Sound", "Bloop", "Play a Bloop clip on speakers and the virtual cable", "wave"),
            ActionInfo("play-stop", "Play / Stop", "Bloop", "Start a clip; press again to stop it", "pause"),
            ActionInfo("stop", "Stop All", "Bloop", "Stop every playing Bloop sound", "stop"),
            ActionInfo("random", "Play Random", "Bloop", "Play a random clip from a category", "dice"),
            ActionInfo("volume", "Volume", "Bloop", "Live Bloop volume; press to mute / unmute", "speaker"),
            ActionInfo("vol-up", "Volume Up", "Bloop", "Raise Bloop master volume", "volup"),
            ActionInfo("vol-down", "Volume Down", "Bloop", "Lower Bloop master volume", "voldown"),
            ActionInfo("cable", "Cable", "Bloop", "Toggle the Bloop virtual cable / mic mix", "cable"),
            ActionInfo("now-playing", "Now Playing", "Bloop", "Live clip name; press to stop", "play"),
        ]

    def create_action(self, action_id: str) -> Action | None:
        mapping = {
            "play": PlaySoundAction,
            "play-stop": PlayStopAction,
            "stop": StopAllAction,
            "random": PlayRandomAction,
            "volume": VolumeAction,
            "vol-up": VolumeUpAction,
            "vol-down": VolumeDownAction,
            "cable": CableAction,
            "now-playing": NowPlayingAction,
        }
        cls = mapping.get(action_id)
        return cls() if cls else None
