from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QFormLayout, QPushButton, QWidget

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import fit_combo, tighten_form

LOW_PERCENT = 20


def _upower(*args: str) -> tuple[int, str]:
    binary = shutil.which("upower")
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


def _short_name(model: str) -> str:
    label = model.strip()
    for junk in (
        " Wireless Gaming Mouse",
        " Wireless Mouse",
        " Gaming Mouse",
        " LIGHTSPEED",
        " Bluetooth",
        " Wireless",
    ):
        label = label.replace(junk, "")
    return label.strip() or model.strip()


def parse_upower_dump(text: str) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("Device:"):
            if current:
                devices.append(current)
            path = line.split(":", 1)[1].strip()
            current = {"path": path, "present": True, "percent": None, "power_supply": False}
            continue
        if line.startswith("Daemon:"):
            if current:
                devices.append(current)
                current = None
            break
        if current is None or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip("'")
        if key == "native-path":
            current["id"] = value
        elif key == "model":
            current["name"] = value
        elif key == "serial":
            current["serial"] = value
        elif key == "power supply":
            current["power_supply"] = value.lower() in {"yes", "true", "1"}
        elif key == "present":
            current["present"] = value.lower() in {"yes", "true", "1"}
        elif key == "percentage":
            match = re.search(r"(-?\d+(?:\.\d+)?)", value)
            if match:
                current["percent"] = float(match.group(1))
        elif key == "state":
            current["state"] = value.lower()
    if current:
        devices.append(current)

    result: list[dict[str, Any]] = []
    for device in devices:
        path = str(device.get("path") or "")
        if "DisplayDevice" in path:
            continue
        if device.get("power_supply"):
            continue
        ident = str(device.get("id") or path)
        name = str(device.get("name") or "").strip()
        percent = device.get("percent")
        if not ident or (not name and percent is None):
            continue
        result.append(
            {
                "id": ident,
                "path": path,
                "name": name or ident,
                "label": _short_name(name or ident),
                "serial": str(device.get("serial") or ""),
                "percent": percent,
                "present": bool(device.get("present", True)) and percent is not None,
                "state": str(device.get("state") or ""),
            }
        )
    return result


def list_batteries() -> list[dict[str, Any]]:
    code, out = _upower("-d")
    if code != 0 or not out:
        return []
    return parse_upower_dump(out)


def resolve_battery(settings: dict[str, Any], devices: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    rows = devices if devices is not None else list_batteries()
    if not rows:
        return None
    chosen = str(settings.get("device") or "")
    serial = str(settings.get("serial") or "")
    if chosen:
        for row in rows:
            if row["id"] == chosen or row["path"] == chosen:
                return row
        if serial:
            for row in rows:
                if row["serial"] == serial:
                    return row
        return None
    for row in rows:
        if row["present"]:
            return row
    return rows[0]


class BatteryAction(Action):
    def __init__(self) -> None:
        self._timer: QTimer | None = None
        self._ctx: ActionContext | None = None

    def will_appear(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        if self._timer is None:
            self._timer = QTimer()
            self._timer.timeout.connect(self._tick)
        self._timer.start(4000)
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
        devices = list_batteries()
        device = resolve_battery(ctx.settings, devices)
        if device is None or not device["present"]:
            ctx.set_state("danger")
            label = str(ctx.settings.get("device_label") or "").strip()
            if ctx.slot_title.strip():
                ctx.set_title(None)
            else:
                ctx.set_title(label or (device["label"] if device else "OFF"))
            return
        percent = int(round(float(device["percent"])))
        ctx.set_state("danger" if percent <= LOW_PERCENT else "")
        ctx.set_title(f"{percent}%")

    def key_down(self, ctx: ActionContext) -> None:
        self.update_visual(ctx)
        device = resolve_battery(ctx.settings)
        if device is None or not device["present"]:
            ctx.show_alert()
        else:
            ctx.show_ok()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _BatteryInspector(ctx, parent)


class _BatteryInspector(QWidget):
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
        layout.addRow("Device", self.combo)
        layout.addRow("", refresh)

    def _fill(self) -> None:
        current = self._ctx.settings.get("device", "")
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("First connected", "")
        for device in list_batteries():
            suffix = f"  ({int(device['percent'])}%)" if device["present"] else "  (offline)"
            self.combo.addItem(device["label"] + suffix, device["id"])
        index = self.combo.findData(current)
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.blockSignals(False)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        ident = self.combo.currentData() or ""
        settings["device"] = ident
        settings["device_label"] = ""
        settings["serial"] = ""
        if ident:
            for device in list_batteries():
                if device["id"] == ident:
                    settings["device_label"] = device["label"]
                    settings["serial"] = device["serial"]
                    break
            if not settings["device_label"]:
                settings["device_label"] = self.combo.currentText().split("  (")[0]
        self._ctx.set_settings(settings)


class BatteryPlugin(Plugin):
    id = "com.popstream.battery"
    name = "Battery"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo(
                "battery",
                "Device Battery",
                "Battery",
                "Live battery for a wireless mouse, headset, or similar",
                "battery",
            ),
        ]

    def create_action(self, action_id: str) -> Action | None:
        if action_id == "battery":
            return BatteryAction()
        return None
