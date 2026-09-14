from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QFormLayout, QPushButton, QWidget

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import fit_combo, tighten_form

NUDGE_DIR = Path.home() / ".local/share/nudge"
ORIENT = [("Normal", 0), ("90°", 1), ("180°", 2), ("270°", 3)]


def _nudge_root() -> Path | None:
    here = Path(__file__).resolve()
    try:
        sibling = here.parents[4] / "nudge"
        if (sibling / "src" / "nudge").is_dir():
            return sibling
    except IndexError:
        pass
    return None


def _ensure_nudge() -> bool:
    root = _nudge_root()
    if root is None:
        return False
    src = str(root / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    return True


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return fallback
    return raw


def list_layouts() -> list[tuple[str, str]]:
    if _ensure_nudge():
        try:
            from nudge.core.store import load_layouts

            return [(item.id, item.name) for item in load_layouts()]
        except Exception:
            pass
    raw = _read_json(NUDGE_DIR / "layouts.json", [])
    rows = raw if isinstance(raw, list) else raw.get("layouts") if isinstance(raw, dict) else []
    layouts: list[tuple[str, str]] = []
    if not isinstance(rows, list):
        return layouts
    for item in rows:
        if not isinstance(item, dict):
            continue
        ident = str(item.get("id") or "").strip()
        name = str(item.get("name") or ident).strip()
        if ident:
            layouts.append((ident, name))
    return layouts


def active_layout() -> str:
    if _ensure_nudge():
        try:
            from nudge.core.store import load_settings

            return str(load_settings().active_layout or "")
        except Exception:
            pass
    raw = _read_json(NUDGE_DIR / "settings.json", {})
    if isinstance(raw, dict):
        return str(raw.get("active_layout") or "")
    return ""


def list_displays() -> list[dict[str, Any]]:
    if _ensure_nudge():
        try:
            from nudge.core.hypr import list_outputs

            rows = []
            for item in list_outputs():
                rows.append(
                    {
                        "name": item.name,
                        "title": item.title,
                        "width": item.width,
                        "height": item.height,
                        "refresh": item.refresh,
                        "transform": item.transform,
                        "primary": item.primary,
                        "disabled": item.disabled,
                        "modes": [
                            {"width": mode.width, "height": mode.height, "refresh": mode.refresh, "label": mode.label}
                            for mode in item.modes
                        ],
                    }
                )
            return rows
        except Exception:
            pass
    return []


def display_label(name: str) -> str:
    for item in list_displays():
        if item["name"] == name:
            title = str(item.get("title") or name)
            return f"{name}  ·  {title}" if title != name else name
    return name or "Display"


def apply_nudge(token: str) -> bool:
    wanted = (token or "").strip()
    if not wanted:
        return False
    if _ensure_nudge():
        try:
            from nudge.core.actions import apply_token
            from nudge.core.store import write_wanted_layout

            if wanted != "identify":
                write_wanted_layout(wanted)
            return bool(apply_token(wanted))
        except Exception:
            pass
    root = _nudge_root()
    launcher = root / "packaging" / "nudge" if root is not None else None
    if launcher is not None and launcher.is_file():
        try:
            proc = subprocess.run(
                [str(launcher), "apply", wanted],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    return False


def open_nudge() -> bool:
    root = _nudge_root()
    launcher = root / "packaging" / "nudge" if root is not None else None
    if launcher is not None and launcher.is_file():
        try:
            subprocess.Popen([str(launcher)], start_new_session=True)
            return True
        except OSError:
            return False
    try:
        subprocess.Popen(["gtk-launch", "nudge"], start_new_session=True)
        return True
    except OSError:
        return False


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
            _LiveAction._shared.start(800)
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
        for action in list(cls._listeners):
            action._tick()

    def _tick(self) -> None:
        if self._ctx is None:
            return
        self.update_visual(self._ctx)

    def update_visual(self, ctx: ActionContext) -> None:
        return


class _LayoutInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        self._fill()
        self.combo.currentIndexChanged.connect(self._save)
        refresh = QPushButton("Refresh layouts")
        refresh.clicked.connect(self._fill)
        layout.addRow("Layout", self.combo)
        layout.addRow("", refresh)

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("layout") or "")
        self.combo.blockSignals(True)
        self.combo.clear()
        layouts = list_layouts()
        if not layouts:
            self.combo.addItem("Save a layout in Nudge", "")
        for ident, name in layouts:
            self.combo.addItem(name, ident)
        index = self.combo.findData(current)
        if index >= 0:
            self.combo.setCurrentIndex(index)
        elif self.combo.count() and not current:
            self.combo.setCurrentIndex(0)
            self._save()
        self.combo.blockSignals(False)

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        ident = self.combo.currentData() or ""
        settings["layout"] = ident
        settings["layout_label"] = self.combo.currentText() if ident else ""
        self._ctx.set_settings(settings)


class _DisplayInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None, *, with_refresh: bool = False, with_rotate: bool = False) -> None:
        super().__init__(parent)
        self._ctx = ctx
        self._with_refresh = with_refresh
        self._with_rotate = with_rotate
        layout = QFormLayout(self)
        tighten_form(layout)
        self.display = fit_combo(QComboBox())
        self.display.currentIndexChanged.connect(self._fill_extra)
        layout.addRow("Display", self.display)
        if with_refresh:
            self.mode = fit_combo(QComboBox())
            self.mode.currentIndexChanged.connect(self._save)
            layout.addRow("Mode", self.mode)
        if with_rotate:
            self.orient = fit_combo(QComboBox())
            for label, value in ORIENT:
                self.orient.addItem(label, value)
            self.orient.currentIndexChanged.connect(self._save)
            layout.addRow("Rotate", self.orient)
        refresh = QPushButton("Refresh displays")
        refresh.clicked.connect(self._fill)
        layout.addRow("", refresh)
        self._fill()

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("display") or "")
        self.display.blockSignals(True)
        self.display.clear()
        rows = list_displays()
        if not rows:
            self.display.addItem("Nudge / hyprctl not available", "")
        for item in rows:
            self.display.addItem(display_label(str(item["name"])), item["name"])
        index = self.display.findData(current)
        self.display.setCurrentIndex(index if index >= 0 else 0)
        self.display.blockSignals(False)
        self._fill_extra()

    def _fill_extra(self) -> None:
        name = str(self.display.currentData() or "")
        current = next((item for item in list_displays() if item["name"] == name), None)
        if self._with_refresh:
            wanted = self._ctx.settings.get("refresh")
            self.mode.blockSignals(True)
            self.mode.clear()
            modes = (current or {}).get("modes") or []
            if not modes and current:
                modes = [
                    {
                        "width": current["width"],
                        "height": current["height"],
                        "refresh": current["refresh"],
                        "label": f"{current['width']}x{current['height']} @ {current['refresh']:g} Hz",
                    }
                ]
            for mode in modes:
                self.mode.addItem(str(mode.get("label") or f"{mode['refresh']:g} Hz"), float(mode["refresh"]))
            if wanted is not None:
                index = self.mode.findData(float(wanted))
                if index >= 0:
                    self.mode.setCurrentIndex(index)
            self.mode.blockSignals(False)
        if self._with_rotate:
            transform = int((current or {}).get("transform") or self._ctx.settings.get("transform") or 0)
            self.orient.blockSignals(True)
            index = self.orient.findData(transform)
            self.orient.setCurrentIndex(index if index >= 0 else 0)
            self.orient.blockSignals(False)
        self._save()

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["display"] = self.display.currentData() or ""
        settings["display_label"] = display_label(str(settings["display"]))
        if self._with_refresh:
            settings["refresh"] = self.mode.currentData()
            settings["refresh_label"] = self.mode.currentText()
        if self._with_rotate:
            settings["transform"] = int(self.orient.currentData() or 0)
            settings["transform_label"] = self.orient.currentText()
        self._ctx.set_settings(settings)


class LayoutAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        layouts = list_layouts()
        chosen = str(ctx.settings.get("layout") or "")
        current = active_layout()
        if not layouts or (chosen and chosen not in {ident for ident, _name in layouts}):
            ctx.set_state("danger")
        elif chosen and chosen == current:
            ctx.set_state("ok")
        else:
            ctx.set_state("")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            ctx.set_title(str(ctx.settings.get("layout_label") or "") or chosen or "Layout")

    def key_down(self, ctx: ActionContext) -> None:
        layouts = list_layouts()
        if not layouts:
            ctx.show_alert()
            self.update_visual(ctx)
            return
        chosen = str(ctx.settings.get("layout") or "")
        if not chosen:
            names = [ident for ident, _name in layouts]
            current = active_layout()
            chosen = names[(names.index(current) + 1) % len(names)] if current in names else names[0]
        if apply_nudge(chosen):
            ctx.show_ok()
        else:
            ctx.show_alert()
        _LiveAction._tick_all()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _LayoutInspector(ctx, parent)


class RefreshAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        name = str(ctx.settings.get("display") or "")
        wanted = ctx.settings.get("refresh")
        current = next((item for item in list_displays() if item["name"] == name), None)
        if not name or current is None:
            ctx.set_state("danger")
        elif wanted is not None and abs(float(current["refresh"]) - float(wanted)) < 0.2:
            ctx.set_state("ok")
        else:
            ctx.set_state("")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            label = str(ctx.settings.get("refresh_label") or "")
            ctx.set_title(label or (f"{wanted:g} Hz" if wanted else "Refresh"))

    def key_down(self, ctx: ActionContext) -> None:
        name = str(ctx.settings.get("display") or "")
        wanted = ctx.settings.get("refresh")
        if not name or wanted is None:
            ctx.show_alert()
            return
        if apply_nudge(f"refresh:{name}:{wanted}"):
            ctx.show_ok()
        else:
            ctx.show_alert()
        _LiveAction._tick_all()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DisplayInspector(ctx, parent, with_refresh=True)


class RotateAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        name = str(ctx.settings.get("display") or "")
        wanted = int(ctx.settings.get("transform") or 0)
        current = next((item for item in list_displays() if item["name"] == name), None)
        if not name or current is None:
            ctx.set_state("danger")
        elif int(current.get("transform") or 0) == wanted:
            ctx.set_state("ok")
        else:
            ctx.set_state("")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            ctx.set_title(str(ctx.settings.get("transform_label") or "") or "Rotate")

    def key_down(self, ctx: ActionContext) -> None:
        name = str(ctx.settings.get("display") or "")
        if not name:
            ctx.show_alert()
            return
        turn = int(ctx.settings.get("transform") or 0)
        if apply_nudge(f"rotate:{name}:{turn}"):
            ctx.show_ok()
        else:
            ctx.show_alert()
        _LiveAction._tick_all()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DisplayInspector(ctx, parent, with_rotate=True)


class IdentifyAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        if apply_nudge("identify"):
            ctx.show_ok()
        else:
            ctx.show_alert()


class PrimaryAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        name = str(ctx.settings.get("display") or "")
        current = next((item for item in list_displays() if item["name"] == name), None)
        if not name or current is None:
            ctx.set_state("danger")
        elif current.get("primary"):
            ctx.set_state("ok")
        else:
            ctx.set_state("")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            ctx.set_title(display_label(name) if name else "Primary")

    def key_down(self, ctx: ActionContext) -> None:
        name = str(ctx.settings.get("display") or "")
        if not name:
            ctx.show_alert()
            return
        if apply_nudge(f"primary:{name}"):
            ctx.show_ok()
        else:
            ctx.show_alert()
        _LiveAction._tick_all()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DisplayInspector(ctx, parent)


class ToggleAction(_LiveAction):
    def update_visual(self, ctx: ActionContext) -> None:
        name = str(ctx.settings.get("display") or "")
        current = next((item for item in list_displays() if item["name"] == name), None)
        if not name or current is None:
            ctx.set_state("danger")
        elif current.get("disabled"):
            ctx.set_state("")
        else:
            ctx.set_state("ok")
        if ctx.slot_title.strip():
            ctx.set_title(None)
        else:
            suffix = "Off" if current and current.get("disabled") else "On"
            ctx.set_title(f"{name or 'Display'}  {suffix}")

    def key_down(self, ctx: ActionContext) -> None:
        name = str(ctx.settings.get("display") or "")
        if not name:
            ctx.show_alert()
            return
        if apply_nudge(f"toggle:{name}"):
            ctx.show_ok()
        else:
            ctx.show_alert()
        _LiveAction._tick_all()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _DisplayInspector(ctx, parent)


class OpenNudgeAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        if open_nudge():
            ctx.show_ok()
        else:
            ctx.show_alert()


class NudgePlugin(Plugin):
    id = "com.popstream.nudge"
    name = "Nudge"
    version = "1.0.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        items = [
            ActionInfo("layout", "Layout", "Nudge", "Apply a saved Nudge monitor layout", "monitor"),
            ActionInfo("refresh", "Refresh rate", "Nudge", "Set a display refresh rate", "monitor"),
            ActionInfo("rotate", "Rotate", "Nudge", "Rotate a display", "monitor"),
            ActionInfo("identify", "Identify", "Nudge", "Flash the connector name on each screen", "monitor"),
            ActionInfo("primary", "Primary", "Nudge", "Make a display the primary (0x0)", "monitor"),
            ActionInfo("toggle", "Toggle display", "Nudge", "Enable or disable a display", "monitor"),
            ActionInfo("open", "Open Nudge", "Nudge", "Open the Nudge window", "app"),
        ]
        for ident, name in list_layouts():
            items.append(
                ActionInfo(
                    f"layout:{ident}",
                    name,
                    "Nudge / Layouts",
                    f"Apply layout {name}",
                    "monitor",
                    defaults={"layout": ident, "layout_label": name},
                )
            )
        return items

    def create_action(self, action_id: str) -> Action | None:
        if action_id.startswith("layout:") or action_id == "layout":
            return LayoutAction()
        mapping = {
            "refresh": RefreshAction,
            "rotate": RotateAction,
            "identify": IdentifyAction,
            "primary": PrimaryAction,
            "toggle": ToggleAction,
            "open": OpenNudgeAction,
        }
        cls = mapping.get(action_id)
        return cls() if cls else None
