from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from popstream.core.profile import Profile
from popstream.core.specs import SPECS


def _cwd_or_none() -> Path | None:
    """Return cwd when it still exists; deleted cwd raises FileNotFoundError on Linux."""
    try:
        return Path.cwd()
    except FileNotFoundError:
        return None


def data_dir() -> Path:
    candidates = []
    qt_path = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if qt_path:
        candidates.append(Path(qt_path))
    candidates.append(Path.home() / ".local" / "share" / "PopStream")
    cwd = _cwd_or_none()
    if cwd is not None:
        candidates.append(cwd / ".popstream-data")
    for path in candidates:
        # Keep the product name even when Qt has no QApplication yet.
        if path.name.lower() not in {"popstream", ".popstream-data"}:
            path = path / "PopStream"
        try:
            path.mkdir(parents=True, exist_ok=True)
            return path
        except OSError:
            continue
    raise RuntimeError("Unable to create PopStream data directory")


def profiles_dir() -> Path:
    path = data_dir() / "profiles"
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        cwd = _cwd_or_none()
        if cwd is None:
            raise
        path = cwd / ".popstream-data" / "profiles"
        path.mkdir(parents=True, exist_ok=True)
        return path


def user_plugins_dir() -> Path:
    path = data_dir() / "plugins"
    try:
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        cwd = _cwd_or_none()
        if cwd is None:
            raise
        path = cwd / ".popstream-data" / "plugins"
        path.mkdir(parents=True, exist_ok=True)
        return path


def settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    path = settings_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_settings(data: dict) -> None:
    settings_path().write_text(json.dumps(data, indent=2), encoding="utf-8")


class ProfileStore:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or profiles_dir()
        self.root.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[Profile]:
        profiles: list[Profile] = []
        for file in sorted(self.root.glob("*.json")):
            try:
                data = json.loads(file.read_text(encoding="utf-8"))
                profiles.append(Profile.from_json(data))
            except Exception:
                continue
        return profiles

    def save(self, profile: Profile) -> None:
        path = self.root / f"{profile.id}.json"
        path.write_text(json.dumps(profile.to_json(), indent=2), encoding="utf-8")

    def delete(self, profile_id: str) -> None:
        path = self.root / f"{profile_id}.json"
        if path.exists():
            path.unlink()

    def ensure_defaults(self) -> list[Profile]:
        existing = self.list()
        if existing:
            return existing
        profile = _demo_profile()
        self.save(profile)
        return [profile]


def _demo_profile() -> Profile:
    profile = Profile.blank("Studio", "mk2")
    n = SPECS["mk2"].key_count
    page = profile.pages[0]
    page.ensure_size(n)
    page.buttons[0] = page.buttons[0].__class__(
        plugin_id="com.popstream.clock",
        action_id="clock",
        title="",
        show_title=True,
        settings={"format": "HH:mm"},
    )
    page.buttons[1] = page.buttons[1].__class__(
        plugin_id="com.popstream.system",
        action_id="open-url",
        title="GitHub",
        settings={"url": "https://github.com/pullchocks"},
    )
    page.buttons[2] = page.buttons[2].__class__(
        plugin_id="com.popstream.system",
        action_id="open-url",
        title="Docs",
        settings={"url": "https://github.com/pullchocks/popstream/blob/main/README.md"},
    )
    page.buttons[4] = page.buttons[4].__class__(
        plugin_id="com.popstream.navigation",
        action_id="next-page",
        title="Next",
    )
    page.buttons[5] = page.buttons[5].__class__(
        plugin_id="com.popstream.system",
        action_id="open-app",
        title="Terminal",
        settings={"command": "x-terminal-emulator"},
    )
    extra = page.__class__(name="Page 2")
    extra.ensure_size(n)
    extra.buttons[0] = extra.buttons[0].__class__(
        plugin_id="com.popstream.navigation",
        action_id="prev-page",
        title="Back",
    )
    extra.buttons[1] = extra.buttons[1].__class__(
        plugin_id="com.popstream.clock",
        action_id="date",
        title="",
        settings={"format": "ddd d MMM"},
    )
    extra.buttons[2] = extra.buttons[2].__class__(
        plugin_id="com.popstream.system",
        action_id="hotkey",
        title="Copy",
        settings={"sequence": "Ctrl+C"},
    )
    extra.buttons[3] = extra.buttons[3].__class__(
        plugin_id="com.popstream.system",
        action_id="hotkey",
        title="Paste",
        settings={"sequence": "Ctrl+V"},
    )
    profile.pages.append(extra)
    return profile
