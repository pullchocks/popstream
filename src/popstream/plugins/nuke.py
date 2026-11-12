"""Wipe one chosen folder so its files are overwritten, then removed."""

from __future__ import annotations

import os
import secrets
import stat
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QFileDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QWidget

from popstream.core.plugin import Action, ActionContext, ActionInfo, Plugin
from popstream.ui.theme import tighten_form

_CHUNK = 1024 * 1024
_ARM_MS = 2500
_EXACT_BLOCK = {
    "/",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/home",
    "/lib",
    "/lib64",
    "/media",
    "/mnt",
    "/opt",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/srv",
    "/sys",
    "/tmp",
    "/usr",
    "/usr/bin",
    "/usr/lib",
    "/usr/local",
    "/usr/share",
    "/var",
    "/var/lib",
    "/var/log",
    "/var/tmp",
}


class NukeError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _blocked_roots() -> set[str]:
    blocked = set(_EXACT_BLOCK)
    try:
        blocked.add(str(Path.home().resolve()))
    except OSError:
        pass
    return blocked


def check_folder(folder: str) -> tuple[Path | None, str, str]:
    """Return (folder, code, detail). code is empty when the folder is safe to wipe."""
    text = str(folder or "").strip()
    if not text:
        return None, "NO DIR", "Choose a folder first."
    raw = Path(text)
    if not raw.is_absolute():
        return None, "NO DIR", "The folder path has to be absolute."
    try:
        resolved = raw.resolve(strict=True)
    except OSError:
        return None, "MISSING", "That folder is not there."
    if not resolved.is_dir():
        return None, "NO DIR", "That path is not a folder."
    if os.path.ismount(resolved):
        return None, "TOO BROAD", "That path is a whole drive, not one folder."
    if str(resolved) in _blocked_roots() or len(resolved.parts) < 3:
        return None, "TOO BROAD", "Pick a folder inside a directory, not a drive or your home itself."
    home = Path.home()
    try:
        home.resolve().relative_to(resolved)
        return None, "TOO BROAD", "That folder contains your home directory."
    except (OSError, ValueError):
        pass
    return resolved, "", ""


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _random_name(parent: Path) -> str:
    for _ in range(8):
        name = secrets.token_hex(8)
        if not (parent / name).exists():
            return name
    return secrets.token_hex(12)


def _overwrite(fd: int, size: int, *, zero: bool) -> None:
    os.lseek(fd, 0, os.SEEK_SET)
    remaining = size
    zeros = b"\x00" * min(_CHUNK, size) if zero else b""
    while remaining > 0:
        chunk = min(_CHUNK, remaining)
        if zero:
            data = zeros if len(zeros) == chunk else b"\x00" * chunk
        else:
            data = os.urandom(chunk)
        written = 0
        while written < chunk:
            count = os.write(fd, data[written:])
            if count <= 0:
                raise OSError("Could not overwrite the file")
            written += count
        remaining -= chunk
    os.fsync(fd)


def _shred_file(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        # A link shares its bytes with a name outside this folder. Remove only the name here.
        os.unlink(path)
        return
    flags = os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags)
    try:
        current = os.fstat(fd).st_size
        if current > 0:
            _overwrite(fd, current, zero=False)
            _overwrite(fd, os.fstat(fd).st_size, zero=True)
        os.ftruncate(fd, 0)
        os.fsync(fd)
    finally:
        os.close(fd)
    renamed = path.with_name(_random_name(path.parent))
    os.rename(path, renamed)
    os.unlink(renamed)


def _remove_dir(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode):
        os.unlink(path)
        return
    renamed = path.with_name(_random_name(path.parent))
    os.rename(path, renamed)
    os.rmdir(renamed)


def _collect(root: Path) -> tuple[list[Path], list[Path], set[Path]]:
    files: list[Path] = []
    dirs: list[Path] = []
    skipped: set[Path] = set()
    root_dev = root.stat().st_dev
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        base = Path(dirpath)
        stay: list[str] = []
        for name in list(dirnames):
            child = base / name
            try:
                info = child.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                files.append(child)
                continue
            if not stat.S_ISDIR(info.st_mode):
                continue
            if info.st_dev != root_dev:
                skipped.add(child)
                continue
            stay.append(name)
            dirs.append(child)
        dirnames[:] = stay
        for name in filenames:
            files.append(base / name)
    dirs.reverse()
    return files, dirs, skipped


def wipe_folder(folder: str) -> None:
    root, code, detail = check_folder(folder)
    if root is None:
        raise NukeError(code, detail)
    failed: list[Path] = []
    skipped: set[Path] = set()
    for _ in range(6):
        files, dirs, skipped = _collect(root)
        if not files and not dirs:
            break
        for path in files:
            try:
                _shred_file(path)
                parent = path.parent
                _fsync_dir(parent if parent.exists() else root)
            except OSError:
                failed.append(path)
        for path in dirs:
            try:
                _remove_dir(path)
                parent = path.parent
                if parent.exists():
                    _fsync_dir(parent)
            except OSError:
                failed.append(path)
    try:
        _fsync_dir(root)
    except OSError:
        pass
    leftover = [path for path in failed if path.exists()]
    still_there = [path for path in root.iterdir() if path not in skipped]
    if leftover or still_there:
        name = (leftover or still_there)[0].name
        raise NukeError("FAIL", f"Could not clear {name}.")


class _WipeBridge(QObject):
    finished = Signal(str, str)


class NukeAction(Action):
    def __init__(self) -> None:
        self._bridge = _WipeBridge()
        self._bridge.finished.connect(self._finished)
        self._ctx: ActionContext | None = None
        self._armed = False
        self._busy = False
        self._token = 0

    def will_appear(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._paint_idle(ctx)

    def will_disappear(self, ctx: ActionContext) -> None:
        self._armed = False
        self._token += 1
        self._ctx = None

    def settings_did_change(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        self._armed = False
        if not self._busy:
            self._paint_idle(ctx)

    def _paint_idle(self, ctx: ActionContext) -> None:
        folder = str(ctx.settings.get("folder") or "").strip()
        ctx.set_state("")
        if ctx.slot_title.strip().lower() not in {"", "nuke"}:
            ctx.set_title(None)
            return
        ctx.set_title(Path(folder).name if folder else "NO DIR")

    def _expire(self, token: int) -> None:
        if token != self._token or not self._armed:
            return
        self._armed = False
        if self._ctx is not None and not self._busy:
            self._paint_idle(self._ctx)

    def key_down(self, ctx: ActionContext) -> None:
        self._ctx = ctx
        if self._busy:
            return
        _root, code, detail = check_folder(str(ctx.settings.get("folder") or ""))
        if code:
            ctx.set_title(code)
            ctx.set_state("danger")
            ctx.log(detail)
            ctx.show_alert()
            return
        confirm = bool(ctx.settings.get("confirm", True))
        if confirm and not self._armed:
            self._armed = True
            self._token += 1
            token = self._token
            ctx.set_title("AGAIN")
            ctx.set_state("danger")
            QTimer.singleShot(_ARM_MS, lambda: self._expire(token))
            return
        self._armed = False
        self._token += 1
        self._busy = True
        ctx.set_title("WIPING")
        ctx.set_state("danger")
        folder = str(ctx.settings.get("folder") or "")

        def run() -> None:
            try:
                wipe_folder(folder)
            except NukeError as exc:
                self._bridge.finished.emit(exc.code, exc.detail)
            except OSError as exc:
                self._bridge.finished.emit("FAIL", str(exc) or "Could not clear the folder.")
            else:
                self._bridge.finished.emit("", "")

        threading.Thread(target=run, name="popstream-nuke", daemon=True).start()

    def _finished(self, code: str, detail: str) -> None:
        self._busy = False
        ctx = self._ctx
        if ctx is None:
            return
        if code:
            ctx.set_title(code)
            ctx.set_state("danger")
            ctx.log(detail)
            ctx.show_alert()
            QTimer.singleShot(1600, lambda: self._paint_idle(ctx) if self._ctx is ctx and not self._busy else None)
            return
        ctx.set_title("CLEARED")
        ctx.set_state("ok")
        ctx.show_ok()
        QTimer.singleShot(1600, lambda: self._paint_idle(ctx) if self._ctx is ctx and not self._busy else None)

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget:
        return _FolderInspector(ctx, parent)


class _FolderInspector(QWidget):
    def __init__(self, ctx: ActionContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._ctx = ctx
        layout = QFormLayout(self)
        tighten_form(layout)
        self.path = QLineEdit(str(ctx.settings.get("folder") or ""))
        self.path.setPlaceholderText("/path/to/folder")
        self.path.editingFinished.connect(self._save)
        browse = QPushButton("Choose")
        browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        note = QLabel(
            "Press once, then again within a few seconds. "
            "Every file inside this folder is overwritten and removed. "
            "Nothing outside the folder is touched."
        )
        note.setWordWrap(True)
        layout.addRow("Folder", row)
        layout.addRow("", note)

    def _browse(self) -> None:
        current = self.path.text().strip() or str(Path.home())
        chosen = QFileDialog.getExistingDirectory(self, "Folder to wipe", current)
        if not chosen:
            return
        self.path.setText(chosen)
        self._save()

    def _save(self) -> None:
        settings = dict(self._ctx.settings)
        settings["folder"] = self.path.text().strip()
        settings["confirm"] = True
        self._ctx.set_settings(settings)


class NukePlugin(Plugin):
    id = "com.popstream.nuke"
    name = "Nuke"
    version = "0.1.0"
    author = "PopStream"

    def actions(self) -> list[ActionInfo]:
        return [
            ActionInfo(
                "nuke",
                "Nuke",
                "Nuke",
                "Overwrite and delete everything in one folder. Press twice.",
                "nuke",
                defaults={"folder": "", "confirm": True},
            )
        ]

    def create_action(self, action_id: str) -> Action | None:
        if action_id == "nuke":
            return NukeAction()
        return None
