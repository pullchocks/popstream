from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QKeySequenceEdit,
    QLineEdit,
    QPushButton,
    QTextEdit,
    QWidget,
)

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import fit_combo, tighten_form


def _run_bin(name: str, *args: str) -> tuple[int, str]:
    binary = shutil.which(name)
    if not binary:
        return 1, ""
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            timeout=3,
        )
        return result.returncode, (result.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def _xdotool() -> str | None:
    return shutil.which("xdotool") or shutil.which("ydotool")


def detect_wm() -> str | None:
    if shutil.which("hyprctl"):
        code, _ = _run_bin("hyprctl", "-j", "monitors")
        if code == 0:
            return "hyprland"
    if shutil.which("swaymsg"):
        code, _ = _run_bin("swaymsg", "-t", "get_outputs")
        if code == 0:
            return "sway"
    if shutil.which("xrandr") and (shutil.which("wmctrl") or shutil.which("xdotool")):
        return "x11"
    return None


def list_monitors() -> list[tuple[str, str]]:
    wm = detect_wm()
    if wm == "hyprland":
        code, out = _run_bin("hyprctl", "-j", "monitors")
        if code != 0 or not out:
            return []
        try:
            rows = json.loads(out)
        except json.JSONDecodeError:
            return []
        items: list[tuple[str, str]] = []
        for row in rows if isinstance(rows, list) else []:
            name = str(row.get("name") or "")
            if not name:
                continue
            desc = str(row.get("description") or row.get("model") or name)
            items.append((name, desc))
        return items
    if wm == "sway":
        code, out = _run_bin("swaymsg", "-t", "get_outputs", "-r")
        if code != 0 or not out:
            return []
        try:
            rows = json.loads(out)
        except json.JSONDecodeError:
            return []
        items = []
        for row in rows if isinstance(rows, list) else []:
            name = str(row.get("name") or "")
            if not name or not row.get("active", True):
                continue
            desc = str((row.get("make") or "")) + " " + str(row.get("model") or name)
            items.append((name, desc.strip() or name))
        return items
    if wm == "x11":
        code, out = _run_bin("xrandr", "--query")
        if code != 0 or not out:
            return []
        items = []
        for line in out.splitlines():
            if " connected" not in line:
                continue
            name = line.split()[0]
            items.append((name, name))
        return items
    return []


def _hypr_clients() -> list[dict]:
    code, out = _run_bin("hyprctl", "-j", "clients")
    if code != 0 or not out:
        return []
    try:
        rows = json.loads(out)
    except json.JSONDecodeError:
        return []
    return rows if isinstance(rows, list) else []


def _hypr_pick_client(target: str, match: str) -> dict | None:
    clients = _hypr_clients()
    if not clients:
        return None
    needle = match.strip().lower()
    if target == "match" and needle:
        for client in clients:
            blob = f"{client.get('class', '')} {client.get('title', '')}".lower()
            if needle in blob:
                return client
        return None
    if target == "fullscreen":
        fullscreen = [c for c in clients if int(c.get("fullscreen") or 0) != 0]
        if fullscreen:
            return max(fullscreen, key=lambda c: int(c.get("size", [0, 0])[0]) * int(c.get("size", [0, 1])[1]) if isinstance(c.get("size"), list) and len(c.get("size")) >= 2 else 0)
    code, out = _run_bin("hyprctl", "-j", "activewindow")
    if code == 0 and out:
        try:
            active = json.loads(out)
            if isinstance(active, dict) and active.get("address"):
                return active
        except json.JSONDecodeError:
            pass
    return None


def _hypr_move(monitor: str, *, target: str, match: str) -> bool:
    monitors = [name for name, _ in list_monitors()]
    if not monitors:
        return False
    chosen = monitor
    if not chosen:
        code, out = _run_bin("hyprctl", "-j", "monitors")
        focused = ""
        try:
            rows = json.loads(out) if code == 0 and out else []
        except json.JSONDecodeError:
            rows = []
        for row in rows if isinstance(rows, list) else []:
            if row.get("focused"):
                focused = str(row.get("name") or "")
                break
        others = [name for name in monitors if name != focused]
        chosen = others[0] if others else (monitors[0] if monitors else "")
    if not chosen:
        return False
    client = _hypr_pick_client(target, match)
    if client and client.get("address"):
        addr = str(client["address"])
        _run_bin("hyprctl", "dispatch", "focuswindow", f"address:{addr}")
    code, _ = _run_bin("hyprctl", "dispatch", "movewindow", f"mon:{chosen}")
    return code == 0


def _sway_move(monitor: str, *, target: str, match: str) -> bool:
    chosen = monitor
    if not chosen:
        items = list_monitors()
        if len(items) < 2:
            chosen = items[0][0] if items else ""
        else:
            code, out = _run_bin("swaymsg", "-t", "get_outputs", "-r")
            focused = ""
            try:
                rows = json.loads(out) if code == 0 and out else []
            except json.JSONDecodeError:
                rows = []
            for row in rows if isinstance(rows, list) else []:
                if row.get("focused"):
                    focused = str(row.get("name") or "")
            others = [name for name, _ in items if name != focused]
            chosen = others[0] if others else items[0][0]
    if not chosen:
        return False
    if target == "match" and match.strip():
        code, _ = _run_bin(
            "swaymsg",
            f"[app_id=\"{match}\"|class=\"{match}\"|title=\"{match}\"]",
            "move",
            "container",
            "to",
            "output",
            chosen,
        )
        return code == 0
    code, _ = _run_bin("swaymsg", "move", "container", "to", "output", chosen)
    return code == 0


def _x11_monitor_geometry() -> dict[str, tuple[int, int, int, int]]:
    code, out = _run_bin("xrandr", "--query")
    geos: dict[str, tuple[int, int, int, int]] = {}
    if code != 0 or not out:
        return geos
    for line in out.splitlines():
        if " connected" not in line or "+" not in line:
            continue
        parts = line.split()
        name = parts[0]
        for token in parts:
            if "x" in token and "+" in token:
                try:
                    wh, rest = token.split("+", 1)
                    w, h = wh.split("x")
                    x, y = rest.split("+")
                    geos[name] = (int(x), int(y), int(w), int(h))
                except ValueError:
                    pass
                break
    return geos


def _x11_move(monitor: str, *, target: str, match: str) -> bool:
    geos = _x11_monitor_geometry()
    if not geos:
        return False
    chosen = monitor if monitor in geos else ""
    if not chosen:
        names = list(geos)
        chosen = names[1] if len(names) > 1 else names[0]
    x, y, w, h = geos[chosen]
    wmctrl = shutil.which("wmctrl")
    xdotool = shutil.which("xdotool")
    win_id = ""
    if target == "match" and match.strip() and wmctrl:
        code, out = _run_bin("wmctrl", "-lx")
        needle = match.strip().lower()
        for line in out.splitlines():
            if needle in line.lower():
                win_id = line.split()[0]
                break
    if not win_id and xdotool:
        if target == "fullscreen":
            code, out = _run_bin("xdotool", "search", "--onlyvisible", "--name", ".")
            # fall through to active
        code, out = _run_bin("xdotool", "getactivewindow")
        if code == 0 and out:
            win_id = out.splitlines()[0]
    if not win_id:
        return False
    if wmctrl:
        code, _ = _run_bin("wmctrl", "-ir", win_id, "-b", "remove,fullscreen,maximized_vert,maximized_horz")
        code, _ = _run_bin("wmctrl", "-ir", win_id, "-e", f"0,{x},{y},{max(800, w - 40)},{max(600, h - 40)}")
        _run_bin("wmctrl", "-ir", win_id, "-b", "add,fullscreen")
        return code == 0
    if xdotool:
        _run_bin("xdotool", "windowmove", win_id, str(x + 20), str(y + 20))
        return True
    return False


def move_window_to_monitor(settings: dict) -> bool:
    monitor = str(settings.get("monitor") or "").strip()
    target = str(settings.get("target") or "active").strip() or "active"
    match = str(settings.get("match") or "").strip()
    backend = str(settings.get("backend") or "auto").strip() or "auto"
    wm = backend if backend != "auto" else (detect_wm() or "")
    if wm == "hyprland":
        return _hypr_move(monitor, target=target, match=match)
    if wm == "sway":
        return _sway_move(monitor, target=target, match=match)
    if wm == "x11":
        return _x11_move(monitor, target=target, match=match)
    return False


def _seq_to_tool(sequence: str) -> str:
    parts = [p.strip() for p in sequence.replace(" ", "").split("+") if p.strip()]
    names = {
        "ctrl": "ctrl",
        "control": "ctrl",
        "alt": "alt",
        "shift": "shift",
        "meta": "super",
        "super": "super",
        "return": "Return",
        "enter": "Return",
        "esc": "Escape",
        "escape": "Escape",
        "space": "space",
        "tab": "Tab",
    }
    out = []
    for part in parts:
        key = part.lower()
        out.append(names.get(key, part if len(part) > 1 else part.lower()))
    return "+".join(out)


class _UrlInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.url = QLineEdit(ctx.settings.get("url", ""))
        self.url.setPlaceholderText("https://")
        self.url.editingFinished.connect(self._save)
        layout.addRow("URL", self.url)

    def _save(self) -> None:
        s = dict(self._ctx.settings)
        s["url"] = self.url.text().strip()
        self._ctx.set_settings(s)


class OpenUrlAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        url = ctx.settings.get("url", "").strip()
        if not url:
            ctx.show_alert()
            return
        if "://" not in url:
            url = "https://" + url
        QDesktopServices.openUrl(QUrl(url))
        ctx.show_ok()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _UrlInspector(ctx, parent)


class _AppInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.cmd = QLineEdit(ctx.settings.get("command", ""))
        self.cmd.setPlaceholderText("Application or command")
        self.cmd.editingFinished.connect(self._save)
        browse = QPushButton("Browse")
        browse.clicked.connect(self._browse)
        row.addWidget(self.cmd, 1)
        row.addWidget(browse)
        wrap = QWidget()
        wrap.setLayout(row)
        layout.addRow("Open", wrap)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose application")
        if path:
            self.cmd.setText(path)
            self._save()

    def _save(self) -> None:
        s = dict(self._ctx.settings)
        s["command"] = self.cmd.text().strip()
        self._ctx.set_settings(s)


class OpenAppAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        command = ctx.settings.get("command", "").strip()
        if not command:
            ctx.show_alert()
            return
        if not QProcess.startDetached(command):
            # Allow arguments via a shell when the binary path isn't a single token.
            if not QProcess.startDetached("/bin/bash", ["-lc", command]):
                ctx.show_alert()
                return
        ctx.show_ok()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _AppInspector(ctx, parent)


class _CommandInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.cmd = QTextEdit()
        self.cmd.setPlainText(ctx.settings.get("command", ""))
        self.cmd.setFixedHeight(80)
        self.cmd.textChanged.connect(self._save)
        self.term = QCheckBox("Keep terminal open")
        self.term.setChecked(bool(ctx.settings.get("terminal", False)))
        self.term.toggled.connect(self._save)
        layout.addRow("Command", self.cmd)
        layout.addRow("", self.term)

    def _save(self) -> None:
        s = dict(self._ctx.settings)
        s["command"] = self.cmd.toPlainText()
        s["terminal"] = self.term.isChecked()
        self._ctx.set_settings(s)


class RunCommandAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        command = ctx.settings.get("command", "").strip()
        if not command:
            ctx.show_alert()
            return
        if ctx.settings.get("terminal"):
            term = shutil.which("x-terminal-emulator") or shutil.which("gnome-terminal") or shutil.which("konsole")
            if term:
                QProcess.startDetached(term, ["-e", "bash", "-lc", command + "; echo; read -n1"])
                ctx.show_ok()
                return
        if QProcess.startDetached("/bin/bash", ["-lc", command]):
            ctx.show_ok()
        else:
            ctx.show_alert()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _CommandInspector(ctx, parent)


class _HotkeyInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.edit = QKeySequenceEdit(QKeySequence(ctx.settings.get("sequence", "")))
        self.edit.editingFinished.connect(self._save)
        layout.addRow("Shortcut", self.edit)

    def _save(self) -> None:
        s = dict(self._ctx.settings)
        s["sequence"] = self.edit.keySequence().toString()
        self._ctx.set_settings(s)


class HotkeyAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        seq = ctx.settings.get("sequence", "").strip()
        tool = _xdotool()
        if not seq or not tool:
            ctx.log("Hotkey needs xdotool (or ydotool) on PATH")
            ctx.show_alert()
            return
        combo = _seq_to_tool(seq)
        if Path(tool).name == "ydotool":
            ok = QProcess.startDetached(tool, ["key", combo.replace("+", "+")])
        else:
            ok = QProcess.startDetached(tool, ["key", "--clearmodifiers", combo])
        if ok:
            ctx.show_ok()
        else:
            ctx.show_alert()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _HotkeyInspector(ctx, parent)


class _TextInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.text = QTextEdit()
        self.text.setPlainText(ctx.settings.get("text", ""))
        self.text.setFixedHeight(80)
        self.text.textChanged.connect(self._save)
        layout.addRow("Text", self.text)

    def _save(self) -> None:
        s = dict(self._ctx.settings)
        s["text"] = self.text.toPlainText()
        self._ctx.set_settings(s)


class TypeTextAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        text = ctx.settings.get("text", "")
        tool = _xdotool()
        if not text or not tool:
            ctx.show_alert()
            return
        if Path(tool).name == "ydotool":
            ok = QProcess.startDetached(tool, ["type", text])
        else:
            ok = QProcess.startDetached(tool, ["type", "--clearmodifiers", "--", text])
        if ok:
            ctx.show_ok()
        else:
            ctx.show_alert()

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _TextInspector(ctx, parent)


class _MonitorInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent=None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.combo = fit_combo(QComboBox())
        self._fill()
        self.combo.currentIndexChanged.connect(self._save)
        refresh = QPushButton("Refresh monitors")
        refresh.clicked.connect(self._fill)
        self.target = fit_combo(QComboBox())
        self.target.addItem("Focused window", "active")
        self.target.addItem("Fullscreen window", "fullscreen")
        self.target.addItem("Match class / title", "match")
        index = self.target.findData(str(ctx.settings.get("target") or "active"))
        self.target.setCurrentIndex(index if index >= 0 else 0)
        self.target.currentIndexChanged.connect(self._save)
        self.match = QLineEdit(str(ctx.settings.get("match") or ""))
        self.match.setPlaceholderText("e.g. steam_app_ or game title")
        self.match.editingFinished.connect(self._save)
        layout.addRow("Monitor", self.combo)
        layout.addRow("", refresh)
        layout.addRow("Window", self.target)
        layout.addRow("Match", self.match)
        self._sync_match()

    def _fill(self) -> None:
        current = str(self._ctx.settings.get("monitor") or "")
        items = list_monitors()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem("Other / next monitor", "")
        for name, desc in items:
            label = desc if desc == name else f"{desc} ({name})"
            self.combo.addItem(label, name)
        index = self.combo.findData(current)
        self.combo.setCurrentIndex(index if index >= 0 else 0)
        self.combo.blockSignals(False)

    def _sync_match(self) -> None:
        self.match.setEnabled(self.target.currentData() == "match")

    def _save(self) -> None:
        self._sync_match()
        settings = dict(self._ctx.settings)
        settings["monitor"] = self.combo.currentData() or ""
        settings["monitor_label"] = "" if not settings["monitor"] else self.combo.currentText()
        settings["target"] = self.target.currentData() or "active"
        settings["match"] = self.match.text().strip()
        self._ctx.set_settings(settings)


class MoveToMonitorAction(Action):
    def key_down(self, ctx: ActionContext) -> None:
        if move_window_to_monitor(ctx.settings):
            ctx.show_ok()
        else:
            ctx.show_alert()
            ctx.log("Move to Monitor needs hyprctl, swaymsg, or wmctrl/xdotool")

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _MonitorInspector(ctx, parent)


class SystemPlugin(Plugin):
    id = "com.popstream.system"
    name = "System"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo("open-url", "Website", "System", "Open a URL", "globe"),
            ActionInfo("open-app", "Open App", "System", "Launch an application", "app"),
            ActionInfo("command", "Run Command", "System", "Run a shell command", "terminal"),
            ActionInfo("hotkey", "Hotkey", "System", "Send a keyboard shortcut", "keyboard"),
            ActionInfo("type-text", "Text", "System", "Type a snippet", "type"),
            ActionInfo(
                "move-monitor",
                "Move to Monitor",
                "System",
                "Move the focused or matched window to another monitor",
                "monitor",
            ),
        ]

    def create_action(self, action_id: str) -> Action | None:
        return {
            "open-url": OpenUrlAction,
            "open-app": OpenAppAction,
            "command": RunCommandAction,
            "hotkey": HotkeyAction,
            "type-text": TypeTextAction,
            "move-monitor": MoveToMonitorAction,
        }.get(action_id, lambda: None)()
