from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QFormLayout, QPushButton, QWidget

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import fit_combo, plain_spin, tighten_form

VOL_MIN = -30.0
VOL_MAX = 6.0
EQ_PRESETS = ("Flat", "Rock", "Pop", "Jazz", "Classical", "Bass Boost", "Treble Boost")

_status_cache: tuple[float, dict[str, Any] | None] = (0.0, None)


def _cliamp(*args: str) -> tuple[int, str]:
    binary = shutil.which("cliamp")
    if not binary:
        return 1, ""
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=1.5,
        )
        return result.returncode, (result.stdout or result.stderr or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def cliamp_ok(*args: str) -> bool:
    return _cliamp(*args)[0] == 0


def cliamp_status(*, fresh: bool = False) -> dict[str, Any] | None:
    global _status_cache
    now = time.monotonic()
    if not fresh and _status_cache[1] is not None and now - _status_cache[0] < 0.35:
        return _status_cache[1]
    code, raw = _cliamp("status", "--json")
    if code != 0 or not raw:
        _status_cache = (now, None)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        _status_cache = (now, None)
        return None
    if not isinstance(data, dict):
        _status_cache = (now, None)
        return None
    track = data.get("track") if isinstance(data.get("track"), dict) else {}
    title = str(track.get("title") or "").strip()
    state = str(data.get("state") or "").strip().lower()
    shuffle = data.get("shuffle")
    repeat = str(data.get("repeat") or "off").strip().lower()
    volume = data.get("volume")
    if volume is None:
        text_code, text = _cliamp("status")
        if text_code == 0:
            match = re.search(r"Volume:\s*([+-]?\d+(?:\.\d+)?)", text or "")
            if match:
                volume = float(match.group(1))
            if not title:
                tmatch = re.search(r"^Track:\s*(.+)$", text or "", re.M)
                if tmatch:
                    title = tmatch.group(1).strip()
    try:
        volume_db = float(volume) if volume is not None else None
    except (TypeError, ValueError):
        volume_db = None
    data["title"] = title
    data["playing"] = state == "playing"
    data["paused"] = state == "paused"
    data["shuffle_on"] = shuffle is True or str(shuffle).lower() in {"on", "true"}
    data["repeat_mode"] = repeat if repeat in {"off", "all", "one"} else "off"
    data["volume_db"] = volume_db
    _status_cache = (now, data)
    return data


def cliamp_playlists() -> list[str]:
    code, out = _cliamp("playlist", "list")
    if code != 0 or not out:
        return []
    names: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("no playlist"):
            continue
        name = re.sub(r"\s*\(\d+\s+tracks?\)$", "", line, flags=re.I).strip()
        if name:
            names.append(name)
    return names


def _set_volume(db: float) -> bool:
    value = max(VOL_MIN, min(VOL_MAX, db))
    if abs(value - round(value)) < 0.05:
        arg = str(int(round(value)))
    else:
        arg = f"{value:.1f}"
    return cliamp_ok("volume", arg)


def _paint_offline(ctx: ActionContext, status: dict[str, Any] | None) -> bool:
    if status:
        return False
    ctx.set_state("danger")
    ctx.set_title("OFF")
    return True


def _keep_or_set(ctx: ActionContext, title: str) -> None:
    if ctx.slot_title.strip():
        ctx.set_title(None)
    else:
        ctx.set_title(title)


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


class _StepInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.step = plain_spin(1, 12, int(ctx.settings.get("step", 3)))
        self.step.setSuffix(" dB")
        self.step.valueChanged.connect(self._save)
        layout.addRow("Step", self.step)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["step"] = self.step.value()
        self._ctx.set_settings(settings)


class _PlaylistInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        self.combo.currentIndexChanged.connect(self._save)
        refresh = QPushButton("Refresh playlists")
        refresh.clicked.connect(self._fill)
        layout.addRow("Playlist", self.combo)
        layout.addRow("", refresh)
        self._fill()

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("playlist") or "")
        names = cliamp_playlists()
        self.combo.blockSignals(True)
        self.combo.clear()
        if not names:
            self.combo.addItem("No playlists", "")
        else:
            self.combo.addItem("Select a playlist", "")
            for name in names:
                self.combo.addItem(name, name)
        index = self.combo.findData(current)
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.blockSignals(False)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["playlist"] = self.combo.currentData() or ""
        self._ctx.set_settings(settings)


class _EqInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        current = str(ctx.settings.get("preset") or "Flat")
        for name in EQ_PRESETS:
            self.combo.addItem(name, name)
        index = self.combo.findData(current)
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.currentIndexChanged.connect(self._save)
        layout.addRow("Preset", self.combo)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["preset"] = self.combo.currentData() or "Flat"
        self._ctx.set_settings(settings)


class PlayPauseAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        status = cliamp_status()
        if _paint_offline(ctx, status):
            return
        playing = bool(status and status.get("playing"))
        ctx.set_state("ok" if playing else "")
        _keep_or_set(ctx, "PAUSE" if playing else "PLAY")

    def key_down(self, ctx: ActionContext) -> None:
        if not cliamp_ok("toggle"):
            ctx.show_alert()
        else:
            ctx.show_ok()
        cliamp_status(fresh=True)
        self.update_visual(ctx)


class NextAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        if not cliamp_ok("next"):
            ctx.show_alert()
        else:
            ctx.show_ok()


class PrevAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        if not cliamp_ok("prev"):
            ctx.show_alert()
        else:
            ctx.show_ok()


class StopAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        if not cliamp_ok("stop"):
            ctx.show_alert()
        else:
            ctx.show_ok()


class NowPlayingAction(_LiveAction):
    interval = 500

    def update_visual(self, ctx: ActionContext) -> None:
        status = cliamp_status()
        if _paint_offline(ctx, status):
            return
        title = str(status.get("title") or "").strip() if status else ""
        playing = bool(status and status.get("playing"))
        ctx.set_state("ok" if playing and title else "")
        ctx.set_title((title[:18] if title else "—"))

    def key_down(self, ctx: ActionContext) -> None:
        if not cliamp_ok("toggle"):
            ctx.show_alert()
        cliamp_status(fresh=True)
        self.update_visual(ctx)


class VolumeAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        status = cliamp_status()
        if _paint_offline(ctx, status):
            return
        db = status.get("volume_db") if status else None
        ctx.set_state("")
        ctx.set_title("— dB" if db is None else f"{int(round(float(db)))} dB")

    def key_down(self, ctx: ActionContext) -> None:
        status = cliamp_status(fresh=True)
        if not status or status.get("volume_db") is None:
            ctx.show_alert()
            return
        current = float(status["volume_db"])
        target = VOL_MIN if current > VOL_MIN + 0.5 else 0.0
        if not _set_volume(target):
            ctx.show_alert()
        cliamp_status(fresh=True)
        self.update_visual(ctx)


class VolumeStepAction(_LiveAction):
    delta = 3

    def update_visual(self, ctx: ActionContext) -> None:
        status = cliamp_status()
        if _paint_offline(ctx, status):
            return
        db = status.get("volume_db") if status else None
        ctx.set_state("")
        ctx.set_title("— dB" if db is None else f"{int(round(float(db)))} dB")

    def key_down(self, ctx: ActionContext) -> None:
        status = cliamp_status(fresh=True)
        if not status or status.get("volume_db") is None:
            ctx.show_alert()
            return
        step = int(ctx.settings.get("step", abs(self.delta)))
        if self.delta < 0:
            step = -step
        if not _set_volume(float(status["volume_db"]) + step):
            ctx.show_alert()
        cliamp_status(fresh=True)
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _StepInspector(ctx, parent)


class VolumeUpAction(VolumeStepAction):
    delta = 3


class VolumeDownAction(VolumeStepAction):
    delta = -3


class ShuffleAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        status = cliamp_status()
        if _paint_offline(ctx, status):
            return
        on = bool(status and status.get("shuffle_on"))
        ctx.set_state("ok" if on else "")
        _keep_or_set(ctx, "ON" if on else "OFF")

    def key_down(self, ctx: ActionContext) -> None:
        if not cliamp_ok("shuffle", "toggle"):
            ctx.show_alert()
        else:
            ctx.show_ok()
        cliamp_status(fresh=True)
        self.update_visual(ctx)


class RepeatAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        status = cliamp_status()
        if _paint_offline(ctx, status):
            return
        mode = str(status.get("repeat_mode") or "off") if status else "off"
        ctx.set_state("ok" if mode != "off" else "")
        _keep_or_set(ctx, mode.upper())

    def key_down(self, ctx: ActionContext) -> None:
        if not cliamp_ok("repeat", "cycle"):
            ctx.show_alert()
        else:
            ctx.show_ok()
        cliamp_status(fresh=True)
        self.update_visual(ctx)


class LoadPlaylistAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        status = cliamp_status()
        if _paint_offline(ctx, status):
            return
        ctx.set_state("")
        name = str(ctx.settings.get("playlist") or "").strip()
        _keep_or_set(ctx, name or "Playlist")

    def key_down(self, ctx: ActionContext) -> None:
        name = str(ctx.settings.get("playlist") or "").strip()
        if not name:
            ctx.show_alert()
            return
        if not cliamp_ok("load", name):
            ctx.show_alert()
        else:
            ctx.show_ok()
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _PlaylistInspector(ctx, parent)


class EqPresetAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        status = cliamp_status()
        if _paint_offline(ctx, status):
            return
        chosen = str(ctx.settings.get("preset") or "").strip()
        current = str(status.get("eq_preset") or "").strip() if status else ""
        ctx.set_state("ok" if chosen and chosen.lower() == current.lower() else "")
        _keep_or_set(ctx, chosen or current or "EQ")

    def key_down(self, ctx: ActionContext) -> None:
        preset = str(ctx.settings.get("preset") or "Flat").strip() or "Flat"
        if not cliamp_ok("eq", preset):
            ctx.show_alert()
        else:
            ctx.show_ok()
        cliamp_status(fresh=True)
        self.update_visual(ctx)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _EqInspector(ctx, parent)


class CliampPlugin(Plugin):
    id = "com.popstream.cliamp"
    name = "Cliamp"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo("play-pause", "Play / Pause", "Cliamp", "Toggle cliamp playback", "play"),
            ActionInfo("now-playing", "Now Playing", "Cliamp", "Live track title; press to play / pause", "wave"),
            ActionInfo("next", "Next Track", "Cliamp", "Skip to the next cliamp track", "next"),
            ActionInfo("prev", "Previous Track", "Cliamp", "Go to the previous cliamp track", "prev"),
            ActionInfo("stop", "Stop", "Cliamp", "Stop cliamp playback", "stop"),
            ActionInfo("volume", "Volume", "Cliamp", "Live cliamp volume; press to mute / unmute", "speaker"),
            ActionInfo("vol-up", "Volume Up", "Cliamp", "Raise cliamp volume", "volup"),
            ActionInfo("vol-down", "Volume Down", "Cliamp", "Lower cliamp volume", "voldown"),
            ActionInfo("shuffle", "Shuffle", "Cliamp", "Toggle cliamp shuffle", "shuffle"),
            ActionInfo("repeat", "Repeat", "Cliamp", "Cycle cliamp repeat: off, all, one", "repeat"),
            ActionInfo("playlist", "Load Playlist", "Cliamp", "Load a local cliamp playlist", "folder"),
            ActionInfo("eq", "EQ Preset", "Cliamp", "Apply a cliamp EQ preset", "eq"),
        ]

    def create_action(self, action_id: str) -> Action | None:
        mapping = {
            "play-pause": PlayPauseAction,
            "now-playing": NowPlayingAction,
            "next": NextAction,
            "prev": PrevAction,
            "stop": StopAction,
            "volume": VolumeAction,
            "vol-up": VolumeUpAction,
            "vol-down": VolumeDownAction,
            "shuffle": ShuffleAction,
            "repeat": RepeatAction,
            "playlist": LoadPlaylistAction,
            "eq": EqPresetAction,
        }
        cls = mapping.get(action_id)
        return cls() if cls else None
