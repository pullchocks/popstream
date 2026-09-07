"""PopStream plugin API.

Action plugins
--------------
A plugin module must expose ``plugin`` — a :class:`Plugin` instance (or a
subclass). Drop a folder here to load it at startup:

* ``<app>/plugins/<name>/plugin.py``  (bundled / next to the binary)
* ``~/.local/share/PopStream/plugins/<name>/plugin.py``

Device drivers
--------------
The same file may also expose ``driver`` — a :class:`~popstream.core.device.DeviceDriver`
instance. Drivers enumerate USB Stream Decks and return :class:`~popstream.core.device.Device`
objects. The core talks to devices only through that interface; HID, JPEG
tiles, brightness reports, etc. belong in a driver plugin.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import QWidget

from popstream.core.device import DeviceDriver


@dataclass(frozen=True)
class ActionInfo:
    id: str
    name: str
    category: str
    tooltip: str = ""
    icon: str = "grid"
    supports_title: bool = True


class ActionContext:
    """Per-key host bridge handed to actions (Elgato-style)."""

    def __init__(self, host: Any, page_id: str, key: int) -> None:
        self._host = host
        self.page_id = page_id
        self.key = key

    @property
    def settings(self) -> dict[str, Any]:
        return self._host.get_settings(self.page_id, self.key)

    def set_settings(self, settings: dict[str, Any]) -> None:
        self._host.set_settings(self.page_id, self.key, settings)

    def set_title(self, title: str | None) -> None:
        self._host.set_runtime_title(self.page_id, self.key, title)

    def set_image(self, image: Any) -> None:
        self._host.set_runtime_image(self.page_id, self.key, image)

    def set_state(self, state: str) -> None:
        self._host.set_runtime_state(self.page_id, self.key, state)

    @property
    def slot_title(self) -> str:
        return str(self._host.get_slot_title(self.page_id, self.key) or "")

    def show_alert(self) -> None:
        self._host.show_alert(self.page_id, self.key)

    def show_ok(self) -> None:
        self._host.show_ok(self.page_id, self.key)

    def log(self, message: str) -> None:
        self._host.log(message)

    def create_folder_page(self, name: str = "Folder") -> str:
        return self._host.create_folder_page(name)

    def push_page(self, page_id: str) -> None:
        self._host.push_page(page_id)

    def pop_page(self) -> None:
        self._host.pop_page()

    def next_page(self) -> None:
        self._host.next_page()

    def prev_page(self) -> None:
        self._host.prev_page()

    def set_brightness(self, percent: int) -> None:
        self._host.set_brightness(percent)


class Action(ABC):
    def will_appear(self, ctx: ActionContext) -> None:
        return None

    def will_disappear(self, ctx: ActionContext) -> None:
        return None

    def key_down(self, ctx: ActionContext) -> None:
        return None

    def key_up(self, ctx: ActionContext) -> None:
        return None

    def settings_did_change(self, ctx: ActionContext) -> None:
        return None

    def create_property_inspector(self, ctx: ActionContext, parent: QWidget) -> QWidget | None:
        return None


class Plugin(ABC):
    id: str
    name: str
    version: str = "1.0.0"
    author: str = ""

    @abstractmethod
    def actions(self) -> list[ActionInfo]: ...

    @abstractmethod
    def create_action(self, action_id: str) -> Action | None: ...


@dataclass
class LoadedPlugin:
    plugin: Plugin
    source: str
    driver: DeviceDriver | None = None
    actions: list[ActionInfo] = field(default_factory=list)


class PluginHost:
    def __init__(self, log: Callable[[str], None] | None = None) -> None:
        self._log = log or (lambda _m: None)
        self.loaded: list[LoadedPlugin] = []

    def plugins(self) -> list[Plugin]:
        return [item.plugin for item in self.loaded]

    def drivers(self) -> list[DeviceDriver]:
        return [item.driver for item in self.loaded if item.driver is not None]

    def find_action(self, plugin_id: str, action_id: str) -> ActionInfo | None:
        for item in self.loaded:
            if item.plugin.id != plugin_id:
                continue
            for info in item.actions:
                if info.id == action_id:
                    return info
        return None

    def create_action(self, plugin_id: str, action_id: str) -> Action | None:
        for item in self.loaded:
            if item.plugin.id == plugin_id:
                try:
                    return item.plugin.create_action(action_id)
                except Exception:
                    self._log(traceback.format_exc())
                    return None
        return None

    def register(self, plugin: Plugin, source: str = "builtin", driver: DeviceDriver | None = None) -> None:
        self.loaded.append(
            LoadedPlugin(
                plugin=plugin,
                source=source,
                driver=driver,
                actions=list(plugin.actions()),
            )
        )
        self._log(f"Loaded plugin {plugin.id} ({source})")

    def load_directory(self, root: Path) -> None:
        if not root.is_dir():
            return
        for child in sorted(root.iterdir()):
            candidate = child / "plugin.py" if child.is_dir() else child
            if child.is_dir() and candidate.is_file():
                self._load_file(candidate, source=str(child))
            elif child.suffix == ".py" and child.name != "__init__.py":
                self._load_file(child, source=str(child))

    def _load_file(self, path: Path, source: str) -> None:
        mod_name = f"popstream_ext_{path.parent.name}_{path.stem}".replace("-", "_")
        try:
            spec = importlib.util.spec_from_file_location(mod_name, path)
            if spec is None or spec.loader is None:
                self._log(f"Cannot load plugin {path}")
                return
            module = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = module
            spec.loader.exec_module(module)
        except Exception:
            self._log(f"Failed loading {path}:\n{traceback.format_exc()}")
            return

        plugin_obj = getattr(module, "plugin", None)
        if isinstance(plugin_obj, type) and issubclass(plugin_obj, Plugin):
            plugin_obj = plugin_obj()
        if not isinstance(plugin_obj, Plugin):
            self._log(f"{path} has no Plugin instance named 'plugin'")
            return
        driver = getattr(module, "driver", None)
        if driver is not None and not isinstance(driver, DeviceDriver):
            self._log(f"{path}: 'driver' is not a DeviceDriver, ignoring")
            driver = None
        self.register(plugin_obj, source=source, driver=driver)
