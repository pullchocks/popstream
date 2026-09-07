from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from popstream.core.specs import DeviceSpec, SPECS


def _uid() -> str:
    return str(uuid.uuid4())


@dataclass
class ButtonSlot:
    plugin_id: str = ""
    action_id: str = ""
    title: str = ""
    show_title: bool = True
    font_size: int = 12
    title_align: str = "bottom"
    icon_path: str = ""
    background: str = ""
    text_color: str = ""
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.plugin_id or not self.action_id

    def to_json(self) -> dict[str, Any]:
        if self.empty and not self.background and not self.text_color:
            return {}
        data: dict[str, Any] = {}
        if not self.empty:
            data.update(
                {
                    "pluginId": self.plugin_id,
                    "actionId": self.action_id,
                    "title": self.title,
                    "showTitle": self.show_title,
                    "fontSize": self.font_size,
                    "titleAlign": self.title_align,
                    "iconPath": self.icon_path,
                    "settings": self.settings,
                }
            )
        if self.background:
            data["background"] = self.background
        if self.text_color:
            data["textColor"] = self.text_color
        return data

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> ButtonSlot:
        if not data:
            return cls()
        return cls(
            plugin_id=data.get("pluginId", ""),
            action_id=data.get("actionId", ""),
            title=data.get("title", ""),
            show_title=data.get("showTitle", True),
            font_size=int(data.get("fontSize", 12)),
            title_align=data.get("titleAlign", "bottom"),
            icon_path=data.get("iconPath", ""),
            background=data.get("background", ""),
            text_color=data.get("textColor", ""),
            settings=dict(data.get("settings") or {}),
        )

    def copy(self) -> ButtonSlot:
        return ButtonSlot.from_json(self.to_json())


@dataclass
class Page:
    id: str = field(default_factory=_uid)
    name: str = "Page 1"
    buttons: list[ButtonSlot] = field(default_factory=list)
    folder: bool = False

    def ensure_size(self, n: int) -> None:
        if len(self.buttons) < n:
            self.buttons.extend(ButtonSlot() for _ in range(n - len(self.buttons)))
        elif len(self.buttons) > n:
            self.buttons = self.buttons[:n]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "folder": self.folder,
            "buttons": [b.to_json() for b in self.buttons],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any], key_count: int) -> Page:
        page = cls(
            id=data.get("id") or _uid(),
            name=data.get("name") or "Page",
            folder=bool(data.get("folder", False)),
            buttons=[ButtonSlot.from_json(x) for x in data.get("buttons") or []],
        )
        page.ensure_size(key_count)
        return page


@dataclass
class Profile:
    id: str = field(default_factory=_uid)
    name: str = "Default"
    device_model: str = "mk2"
    pages: list[Page] = field(default_factory=list)
    brightness: int = 75

    @property
    def spec(self) -> DeviceSpec:
        return SPECS.get(self.device_model, SPECS["mk2"])

    def main_pages(self) -> list[Page]:
        return [p for p in self.pages if not p.folder]

    def page_by_id(self, page_id: str) -> Page | None:
        for page in self.pages:
            if page.id == page_id:
                return page
        return None

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "id": self.id,
            "name": self.name,
            "deviceModel": self.device_model,
            "brightness": self.brightness,
            "pages": [p.to_json() for p in self.pages],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Profile:
        model = data.get("deviceModel", "mk2")
        spec = SPECS.get(model, SPECS["mk2"])
        profile = cls(
            id=data.get("id") or _uid(),
            name=data.get("name") or "Profile",
            device_model=model,
            brightness=int(data.get("brightness", 75)),
            pages=[Page.from_json(p, spec.key_count) for p in data.get("pages") or []],
        )
        if not profile.pages:
            profile.pages = [Page(name="Page 1")]
            profile.pages[0].ensure_size(spec.key_count)
        else:
            for page in profile.pages:
                page.ensure_size(spec.key_count)
        return profile

    @classmethod
    def blank(cls, name: str, device_model: str) -> Profile:
        spec = SPECS.get(device_model, SPECS["mk2"])
        page = Page(name="Page 1")
        page.ensure_size(spec.key_count)
        return cls(name=name, device_model=device_model, pages=[page])


def resize_profile(profile: Profile, device_model: str) -> Profile:
    spec = SPECS[device_model]
    profile.device_model = device_model
    for page in profile.pages:
        page.ensure_size(spec.key_count)
    return profile
