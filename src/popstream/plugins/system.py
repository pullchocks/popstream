from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QProcess, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence
from PySide6.QtWidgets import (
    QCheckBox,
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
from popstream.ui.theme import tighten_form


def _xdotool() -> str | None:
    return shutil.which("xdotool") or shutil.which("ydotool")


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
        ]

    def create_action(self, action_id: str) -> Action | None:
        return {
            "open-url": OpenUrlAction,
            "open-app": OpenAppAction,
            "command": RunCommandAction,
            "hotkey": HotkeyAction,
            "type-text": TypeTextAction,
        }.get(action_id, lambda: None)()
