from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap

from popstream.core.device import Device, DeviceDriver, VirtualDevice
from popstream.core.icons import icon_pixmap
from popstream.core.plugin import Action, ActionContext, ActionInfo, PluginHost
from popstream.core.profile import ButtonSlot, Page, Profile
from popstream.core.renderer import RuntimeVisual, render_key
from popstream.core.specs import GRID_SPECS, SPECS, DeviceSpec
from popstream.core.store import ProfileStore
from popstream.core.usbprobe import probe_elgato_usb

PALETTE_PAGE = "__palette__"


@dataclass
class BoundKey:
    action: Action | None
    ctx: ActionContext
    runtime: RuntimeVisual = field(default_factory=RuntimeVisual)
    info: ActionInfo | None = None
    default_icon: QPixmap | None = None


class Engine(QObject):
    profile_changed = Signal()
    page_changed = Signal()
    selection_changed = Signal(int)
    key_visual_changed = Signal(int)
    log_message = Signal(str)
    brightness_changed = Signal(int)
    devices_changed = Signal()
    hardware_seen = Signal(list)
    theme_changed = Signal()
    lock_overlay_changed = Signal(bool)
    palette_changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.store = ProfileStore()
        self.host = PluginHost(log=self.log)
        self.profiles: list[Profile] = []
        self.profile: Profile | None = None
        self.device: Device | None = None
        self.virtual_devices = [VirtualDevice(spec, self) for spec in GRID_SPECS]
        self.hardware_devices: list[Device] = []
        self.drivers: list[DeviceDriver] = []
        self.page_stack: list[str] = []
        self.selected_key = 0
        self._bound: dict[str, BoundKey] = {}
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._flush_save)
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(400)
        self._flash_timer.timeout.connect(self._clear_flashes)
        self._pending_save = False
        self._builtin_drivers: list[DeviceDriver] = []
        self.last_hardware_error = ""
        self.lock_config: dict[str, Any] = {}
        self.lock_active = False
        self._lock_preview = False
        self._session_locked = False
        self._lock_tiles: list[QImage] | None = None
        self._lock_monitor = None
        self._lock_timer = QTimer(self)
        self._lock_timer.timeout.connect(self._refresh_lock_overlay)
        self.palette_plugin_id = ""
        self.palette_action_id = ""
        self.palette_settings: dict[str, Any] = {}
        self.palette_focus = False

    # --- setup ---------------------------------------------------------

    def bootstrap(self, builtin_plugins: list) -> None:
        from popstream.core.lockscreen import LockMonitor, load_lock_config
        from popstream.core.store import load_settings
        from popstream.drivers.elgato import ElgatoHidDriver
        from popstream.ui.theme import apply_accent, apply_text

        settings = load_settings()
        apply_accent(settings.get("accent", ""))
        apply_text(settings.get("text", ""))
        self.lock_config = load_lock_config()

        for plugin in builtin_plugins:
            self.host.register(plugin, source="builtin")
        self._builtin_drivers = [ElgatoHidDriver()]
        self.drivers = list(self._builtin_drivers)
        self.profiles = self.store.ensure_defaults()
        self.refresh_hardware()
        if self.device is None:
            mk2 = next((d for d in self.virtual_devices if d.spec.id == "mk2"), self.virtual_devices[0])
            self.set_device(mk2)
        if self.profile is None:
            model = self.device.spec.id if self.device else "mk2"
            name = self.device.spec.name if self.device else "Profile"
            self._apply_profile_for_model(model, name)
        self._lock_monitor = LockMonitor(self)
        self._lock_monitor.locked_changed.connect(self._on_session_locked)

    def load_external_plugins(self, *directories) -> None:
        for directory in directories:
            self.host.load_directory(directory)
        self.drivers = list(self._builtin_drivers) + self.host.drivers()
        self.refresh_hardware()

    def refresh_hardware(self) -> None:
        seen = probe_elgato_usb()
        self.hardware_seen.emit(seen)
        existing = {d.serial(): d for d in self.hardware_devices}
        opened: list[Device] = []
        self.last_hardware_error = ""
        for driver in self.drivers:
            try:
                for probe in driver.probe():
                    serial = probe.get("serial") or probe.get("path") or ""
                    if serial in existing:
                        opened.append(existing.pop(serial))
                        continue
                    device = driver.open(probe)
                    if device is not None:
                        opened.append(device)
                    elif getattr(driver, "last_error", ""):
                        self.last_hardware_error = driver.last_error
            except Exception as exc:
                self.last_hardware_error = str(exc)
                self.log(f"Driver {getattr(driver, 'id', '?')} failed: {exc}")
        for leftover in existing.values():
            if leftover is not self.device:
                leftover.close()
        self.hardware_devices = opened
        if opened and (self.device is None or not self.device.is_hardware()):
            self.set_device(opened[0])
        self.devices_changed.emit()

    def all_devices(self) -> list[Device]:
        return list(self.hardware_devices) + list(self.virtual_devices)

    # --- logging / persist --------------------------------------------

    def log(self, message: str) -> None:
        self.log_message.emit(message)

    def schedule_save(self) -> None:
        self._pending_save = True
        self._save_timer.start(250)

    def _flush_save(self) -> None:
        if self.profile is None:
            return
        self.store.save(self.profile)
        self._pending_save = False

    # --- profile / device ---------------------------------------------

    def set_profile(self, profile_id: str) -> None:
        self._release_visible()
        for profile in self.profiles:
            if profile.id == profile_id:
                self.profile = profile
                break
        else:
            return
        mains = self.profile.main_pages()
        self.page_stack = [mains[0].id if mains else self.profile.pages[0].id]
        self.selected_key = 0
        if self.device is not None:
            self.device.set_brightness(self.profile.brightness)
        self._appear_visible()
        self.profile_changed.emit()
        self.page_changed.emit()
        self.selection_changed.emit(self.selected_key)
        self.brightness_changed.emit(self.profile.brightness)
        self._push_all_images()

    def set_device(self, device: Device) -> None:
        if device is self.device:
            return
        previous = self.device
        if previous is not None:
            try:
                previous.key_down.disconnect(self.handle_key_down)
                previous.key_up.disconnect(self.handle_key_up)
            except Exception:
                pass
            previous.close()
        self.device = device
        if not device.open():
            err = device.last_error() if hasattr(device, "last_error") else "Failed to open device"
            self.last_hardware_error = err
            self.log(err)
            if previous is not None:
                self.device = previous
                previous.open()
                previous.key_down.connect(self.handle_key_down)
                previous.key_up.connect(self.handle_key_up)
            return
        device.key_down.connect(self.handle_key_down)
        device.key_up.connect(self.handle_key_up)
        if self.profile is None or self.profile.spec.key_count != device.spec.key_count:
            self._apply_profile_for_model(device.spec.id, device.spec.name)
            self.devices_changed.emit()
            return
        if self.profile is not None:
            device.set_brightness(self.profile.brightness)
        self._appear_visible()
        self.page_changed.emit()
        self._push_all_images()
        self.devices_changed.emit()

    def set_accent(self, color: str) -> None:
        self.set_theme_colors(accent=color)

    def set_text_color(self, color: str) -> None:
        self.set_theme_colors(text=color)

    def set_theme_colors(self, accent: str | None = None, text: str | None = None) -> None:
        from popstream.core.store import load_settings, save_settings
        from popstream.ui.theme import apply_accent, apply_text

        settings = load_settings()
        if accent is not None:
            settings["accent"] = apply_accent(accent)
        if text is not None:
            settings["text"] = apply_text(text)
        save_settings(settings)
        self._push_all_images()
        self.theme_changed.emit()

    @property
    def lock_preview(self) -> bool:
        return self._lock_preview

    def set_lock_config(self, config: dict[str, Any]) -> None:
        from popstream.core.lockscreen import save_lock_config

        self.lock_config = save_lock_config(config)
        self._sync_lock_overlay()

    def set_lock_preview(self, on: bool) -> None:
        self._lock_preview = bool(on)
        self._sync_lock_overlay()

    def _on_session_locked(self, locked: bool) -> None:
        self._session_locked = bool(locked)
        self._sync_lock_overlay()

    def _lock_should_show(self) -> bool:
        if self.device is None or not self.device.spec.has_lcd:
            return False
        if self._lock_preview:
            return True
        return bool(self.lock_config.get("enabled", True) and self._session_locked)

    def _sync_lock_overlay(self) -> None:
        want = self._lock_should_show()
        if want and not self.lock_active:
            self._enter_lock_overlay()
        elif not want and self.lock_active:
            self._exit_lock_overlay()
        elif want:
            self._configure_lock_timer()
            self._refresh_lock_overlay()

    def _enter_lock_overlay(self) -> None:
        self.lock_active = True
        self._configure_lock_timer()
        self._refresh_lock_overlay()
        self.lock_overlay_changed.emit(True)

    def _exit_lock_overlay(self) -> None:
        self.lock_active = False
        self._lock_tiles = None
        self._lock_timer.stop()
        self._push_all_images()
        self.lock_overlay_changed.emit(False)

    def _configure_lock_timer(self) -> None:
        mode = self.lock_config.get("mode", "clock")
        if mode == "screensaver":
            self._lock_timer.start(120)
        elif mode == "clock":
            self._lock_timer.start(500)
        else:
            self._lock_timer.stop()

    def _refresh_lock_overlay(self) -> None:
        if not self.lock_active or self.device is None:
            return
        from popstream.core.lockscreen import render_lock_tiles

        self._lock_tiles = render_lock_tiles(self.device.spec, self.lock_config)
        for i, tile in enumerate(self._lock_tiles):
            self.device.set_key_image(i, tile)
            self.key_visual_changed.emit(i)

    def profiles_for_model(self, model_id: str) -> list[Profile]:
        want = SPECS.get(model_id, SPECS["mk2"]).key_count
        result = []
        for profile in self.profiles:
            have = SPECS.get(profile.device_model, SPECS["mk2"]).key_count
            if have == want:
                result.append(profile)
        return result

    def default_profile_id(self, model_id: str | None = None) -> str:
        from popstream.core.store import load_settings

        model = model_id or (self.device.spec.id if self.device else "")
        settings = load_settings()
        matching = self.profiles_for_model(model) if model else list(self.profiles)
        matching_ids = {p.id for p in matching}
        defaults = settings.get("default_profiles")
        if isinstance(defaults, dict):
            if model:
                value = defaults.get(model)
                if isinstance(value, str) and value in matching_ids:
                    return value
            for pid in defaults.values():
                if isinstance(pid, str) and pid in matching_ids:
                    return pid
        legacy = settings.get("default_profile")
        if isinstance(legacy, str) and legacy in matching_ids:
            return legacy
        return ""

    def set_default_profile(self, profile_id: str) -> None:
        from popstream.core.store import load_settings, save_settings

        profile = next((p for p in self.profiles if p.id == profile_id), None)
        if profile is None:
            return
        settings = load_settings()
        defaults = settings.get("default_profiles")
        if not isinstance(defaults, dict):
            defaults = {}
        defaults[profile.device_model] = profile_id
        settings["default_profiles"] = defaults
        settings.pop("default_profile", None)
        save_settings(settings)
        self.profile_changed.emit()

    def _apply_profile_for_model(self, model_id: str, name: str | None = None) -> None:
        matching = self.profiles_for_model(model_id)
        preferred = self.default_profile_id(model_id)
        chosen = next((p for p in matching if p.id == preferred), None)
        if chosen is None and matching:
            chosen = matching[0]
        if chosen is not None:
            self.set_profile(chosen.id)
            return
        self.create_profile(name or "Profile", model_id)

    def _clear_default_profile(self, profile_id: str) -> None:
        from popstream.core.store import load_settings, save_settings

        settings = load_settings()
        defaults = settings.get("default_profiles")
        changed = False
        if isinstance(defaults, dict):
            for model, pid in list(defaults.items()):
                if pid != profile_id:
                    continue
                fallback = next((p.id for p in self.profiles_for_model(model)), "")
                if fallback:
                    defaults[model] = fallback
                else:
                    del defaults[model]
                changed = True
            if changed:
                settings["default_profiles"] = defaults
        if settings.get("default_profile") == profile_id:
            settings.pop("default_profile", None)
            changed = True
        if changed:
            save_settings(settings)

    def create_profile(self, name: str, model_id: str | None = None) -> Profile:
        model = model_id or (self.device.spec.id if self.device else "mk2")
        profile = Profile.blank(name, model)
        self.profiles.append(profile)
        self.store.save(profile)
        if not self.default_profile_id(profile.device_model):
            self.set_default_profile(profile.id)
        self.set_profile(profile.id)
        return profile

    def delete_profile(self, profile_id: str) -> bool:
        if len(self.profiles) <= 1:
            self.log("Keep at least one profile")
            return False
        was_current = self.profile is not None and self.profile.id == profile_id
        model = self.device.spec.id if self.device else ""
        self.store.delete(profile_id)
        self.profiles = [p for p in self.profiles if p.id != profile_id]
        self._clear_default_profile(profile_id)
        if was_current:
            name = self.device.spec.name if self.device else "Profile"
            self._apply_profile_for_model(model or self.profiles[0].device_model, name)
        else:
            self.profile_changed.emit()
        return True

    def rename_profile(self, name: str, profile_id: str | None = None) -> None:
        pid = profile_id or (self.profile.id if self.profile else "")
        profile = next((p for p in self.profiles if p.id == pid), None)
        if profile is None:
            return
        profile.name = name
        self.store.save(profile)
        self.profile_changed.emit()

    # --- pages --------------------------------------------------------

    def current_page(self) -> Page | None:
        if self.profile is None or not self.page_stack:
            return None
        return self.profile.page_by_id(self.page_stack[-1])

    def current_spec(self) -> DeviceSpec:
        if self.device is not None:
            return self.device.spec
        if self.profile is not None:
            return self.profile.spec
        return SPECS["mk2"]

    def select_key(self, index: int) -> None:
        n = self.current_spec().key_count
        if 0 <= index < n:
            self.selected_key = index
            if self.palette_focus:
                self.palette_focus = False
                self.palette_changed.emit()
            self.selection_changed.emit(index)

    def select_palette_action(self, plugin_id: str, action_id: str) -> None:
        if plugin_id != self.palette_plugin_id or action_id != self.palette_action_id:
            self.palette_plugin_id = plugin_id
            self.palette_action_id = action_id
            self.palette_settings = {}
        self.palette_focus = True
        self.palette_changed.emit()

    def add_page(self) -> None:
        if self.profile is None:
            return
        page = Page(name=f"Page {len(self.profile.main_pages()) + 1}")
        page.ensure_size(self.current_spec().key_count)
        # Insert after last main page so folders stay grouped at the end.
        idx = len(self.profile.pages)
        for i, existing in enumerate(self.profile.pages):
            if existing.folder:
                idx = i
                break
        self.profile.pages.insert(idx, page)
        self._release_visible()
        self.page_stack = [page.id]
        self._appear_visible()
        self.schedule_save()
        self.page_changed.emit()
        self._push_all_images()

    def delete_current_page(self) -> None:
        if self.profile is None:
            return
        page = self.current_page()
        if page is None:
            return
        mains = self.profile.main_pages()
        if not page.folder and len(mains) <= 1:
            self.log("Keep at least one page")
            return
        self._release_visible()
        self.profile.pages = [p for p in self.profile.pages if p.id != page.id]
        mains = self.profile.main_pages()
        self.page_stack = [mains[0].id]
        self._appear_visible()
        self.schedule_save()
        self.page_changed.emit()
        self._push_all_images()

    def rename_page(self, name: str) -> None:
        page = self.current_page()
        if page is None:
            return
        page.name = name
        self.schedule_save()
        self.page_changed.emit()

    def next_page(self) -> None:
        if self.profile is None:
            return
        mains = self.profile.main_pages()
        if len(mains) < 2:
            return
        current = self.page_stack[-1] if self.page_stack else mains[0].id
        ids = [p.id for p in mains]
        if current not in ids:
            current = ids[0]
        nxt = ids[(ids.index(current) + 1) % len(ids)]
        self._goto_main(nxt)

    def prev_page(self) -> None:
        if self.profile is None:
            return
        mains = self.profile.main_pages()
        if len(mains) < 2:
            return
        current = self.page_stack[-1] if self.page_stack else mains[0].id
        ids = [p.id for p in mains]
        if current not in ids:
            current = ids[0]
        nxt = ids[(ids.index(current) - 1) % len(ids)]
        self._goto_main(nxt)

    def push_page(self, page_id: str) -> None:
        if self.profile is None or self.profile.page_by_id(page_id) is None:
            return
        if self.page_stack and self.page_stack[-1] == page_id:
            return
        self._release_visible()
        self.page_stack.append(page_id)
        self._appear_visible()
        self.page_changed.emit()
        self._push_all_images()

    def pop_page(self) -> None:
        if len(self.page_stack) <= 1:
            return
        self._release_visible()
        self.page_stack.pop()
        self._appear_visible()
        self.page_changed.emit()
        self._push_all_images()

    def create_folder_page(self, name: str = "Folder") -> str:
        if self.profile is None:
            return ""
        spec = self.current_spec()
        page = Page(name=name, folder=True)
        page.ensure_size(spec.key_count)
        page.buttons[0] = ButtonSlot(
            plugin_id="com.popstream.navigation",
            action_id="back",
            title="Back",
        )
        self.profile.pages.append(page)
        self.schedule_save()
        return page.id

    def _goto_main(self, page_id: str) -> None:
        self._release_visible()
        self.page_stack = [page_id]
        self._appear_visible()
        self.page_changed.emit()
        self._push_all_images()

    # --- keys ---------------------------------------------------------

    def slot_at(self, index: int) -> ButtonSlot | None:
        page = self.current_page()
        if page is None or not (0 <= index < len(page.buttons)):
            return None
        return page.buttons[index]

    def assign_action(self, index: int, plugin_id: str, action_id: str, settings: dict | None = None) -> None:
        page = self.current_page()
        if page is None or not (0 <= index < len(page.buttons)):
            return
        self._unbind(page.id, index)
        info = self.host.find_action(plugin_id, action_id)
        title = info.name if info else ""
        previous_bg = page.buttons[index].background
        previous_fg = page.buttons[index].text_color
        if not settings and plugin_id == self.palette_plugin_id and action_id == self.palette_action_id:
            settings = dict(self.palette_settings)
        page.buttons[index] = ButtonSlot(
            plugin_id=plugin_id,
            action_id=action_id,
            title=title,
            background=previous_bg,
            text_color=previous_fg,
            settings=dict(settings or {}),
        )
        self._bind(page.id, index)
        self.schedule_save()
        self._render_key(index)
        self.select_key(index)
        self.key_visual_changed.emit(index)

    def clear_key(self, index: int) -> None:
        page = self.current_page()
        if page is None or not (0 <= index < len(page.buttons)):
            return
        self._unbind(page.id, index)
        previous_bg = page.buttons[index].background
        previous_fg = page.buttons[index].text_color
        page.buttons[index] = ButtonSlot(background=previous_bg, text_color=previous_fg)
        self.schedule_save()
        self._render_key(index)
        self.key_visual_changed.emit(index)
        self.selection_changed.emit(self.selected_key)

    def swap_keys(self, a: int, b: int) -> None:
        page = self.current_page()
        if page is None:
            return
        if not (0 <= a < len(page.buttons) and 0 <= b < len(page.buttons)):
            return
        self._unbind(page.id, a)
        self._unbind(page.id, b)
        page.buttons[a], page.buttons[b] = page.buttons[b], page.buttons[a]
        self._bind(page.id, a)
        self._bind(page.id, b)
        self.schedule_save()
        self._render_key(a)
        self._render_key(b)
        self.key_visual_changed.emit(a)
        self.key_visual_changed.emit(b)

    def update_slot(self, index: int, **fields: Any) -> None:
        slot = self.slot_at(index)
        if slot is None:
            return
        for key, value in fields.items():
            if hasattr(slot, key):
                setattr(slot, key, value)
        page = self.current_page()
        if page is not None:
            bound = self._bound.get(self._bkey(page.id, index))
            if bound and bound.action is not None:
                bound.action.settings_did_change(bound.ctx)
        self.schedule_save()
        self._render_key(index)
        self.key_visual_changed.emit(index)

    def handle_key_down(self, index: int) -> None:
        if self.lock_active:
            return
        self.select_key(index)
        page = self.current_page()
        if page is None:
            return
        bound = self._bound.get(self._bkey(page.id, index))
        if bound is None or bound.action is None:
            return
        try:
            bound.action.key_down(bound.ctx)
        except Exception as exc:
            self.log(f"keyDown error: {exc}")
            bound.ctx.show_alert()

    def handle_key_up(self, index: int) -> None:
        if self.lock_active:
            return
        page = self.current_page()
        if page is None:
            return
        bound = self._bound.get(self._bkey(page.id, index))
        if bound is None or bound.action is None:
            return
        try:
            bound.action.key_up(bound.ctx)
        except Exception as exc:
            self.log(f"keyUp error: {exc}")

    def test_key(self, index: int) -> None:
        self.handle_key_down(index)
        self.handle_key_up(index)

    def set_brightness(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        if self.profile is not None:
            self.profile.brightness = percent
            self.schedule_save()
        if self.device is not None:
            self.device.set_brightness(percent)
        self.brightness_changed.emit(percent)
        self._push_all_images()

    def key_image(self, index: int) -> QImage | None:
        if self.lock_active and self._lock_tiles is not None and 0 <= index < len(self._lock_tiles):
            return self._lock_tiles[index]
        if isinstance(self.device, VirtualDevice):
            return self.device.image(index)
        page = self.current_page()
        if page is None:
            return None
        bound = self._bound.get(self._bkey(page.id, index))
        slot = page.buttons[index] if 0 <= index < len(page.buttons) else ButtonSlot()
        runtime = bound.runtime if bound else RuntimeVisual()
        icon = bound.default_icon if bound else None
        kind = bound.info.icon if bound and bound.info else "grid"
        return render_key(self.current_spec(), slot, runtime, icon, kind)

    # --- ActionContext host -------------------------------------------

    def get_settings(self, page_id: str, key: int) -> dict[str, Any]:
        if page_id == PALETTE_PAGE:
            return dict(self.palette_settings)
        page = self.profile.page_by_id(page_id) if self.profile else None
        if page is None or not (0 <= key < len(page.buttons)):
            return {}
        return dict(page.buttons[key].settings)

    def get_slot_title(self, page_id: str, key: int) -> str:
        if page_id == PALETTE_PAGE:
            return ""
        page = self.profile.page_by_id(page_id) if self.profile else None
        if page is None or not (0 <= key < len(page.buttons)):
            return ""
        return page.buttons[key].title or ""

    def set_settings(self, page_id: str, key: int, settings: dict[str, Any]) -> None:
        if page_id == PALETTE_PAGE:
            self.palette_settings = dict(settings)
            return
        page = self.profile.page_by_id(page_id) if self.profile else None
        if page is None or not (0 <= key < len(page.buttons)):
            return
        page.buttons[key].settings = dict(settings)
        self.schedule_save()
        bound = self._bound.get(self._bkey(page_id, key))
        if bound and bound.action is not None:
            bound.action.settings_did_change(bound.ctx)
        if self.current_page() and self.current_page().id == page_id:
            self._render_key(key)
            self.key_visual_changed.emit(key)
            if key == self.selected_key:
                self.selection_changed.emit(key)

    def set_runtime_title(self, page_id: str, key: int, title: str | None) -> None:
        bound = self._bound.get(self._bkey(page_id, key))
        if bound is None:
            return
        if bound.runtime.title == title:
            return
        bound.runtime.title = title
        if self.current_page() and self.current_page().id == page_id:
            self._render_key(key)
            self.key_visual_changed.emit(key)

    def set_runtime_image(self, page_id: str, key: int, image: QImage) -> None:
        bound = self._bound.get(self._bkey(page_id, key))
        if bound is None:
            return
        bound.runtime.image = image
        if self.current_page() and self.current_page().id == page_id:
            self._render_key(key)
            self.key_visual_changed.emit(key)

    def set_runtime_state(self, page_id: str, key: int, state: str) -> None:
        bound = self._bound.get(self._bkey(page_id, key))
        if bound is None:
            return
        tint = state if state in {"danger", "ok"} else ""
        if bound.runtime.tint == tint:
            return
        bound.runtime.tint = tint
        if self.current_page() and self.current_page().id == page_id:
            self._render_key(key)
            self.key_visual_changed.emit(key)

    def show_alert(self, page_id: str, key: int) -> None:
        self._flash(page_id, key, "alert")

    def show_ok(self, page_id: str, key: int) -> None:
        self._flash(page_id, key, "ok")

    # --- binding / render ---------------------------------------------

    def _bkey(self, page_id: str, index: int) -> str:
        return f"{page_id}:{index}"

    def _bind(self, page_id: str, index: int) -> None:
        self._unbind(page_id, index)
        page = self.profile.page_by_id(page_id) if self.profile else None
        if page is None:
            return
        slot = page.buttons[index]
        ctx = ActionContext(self, page_id, index)
        if slot.empty:
            self._bound[self._bkey(page_id, index)] = BoundKey(None, ctx)
            return
        info = self.host.find_action(slot.plugin_id, slot.action_id)
        action = self.host.create_action(slot.plugin_id, slot.action_id)
        icon = icon_pixmap(info.icon if info else "grid", 96)
        bound = BoundKey(action, ctx, info=info, default_icon=icon)
        self._bound[self._bkey(page_id, index)] = bound
        if action is not None:
            try:
                action.will_appear(ctx)
            except Exception as exc:
                self.log(f"willAppear error: {exc}")

    def _unbind(self, page_id: str, index: int) -> None:
        key = self._bkey(page_id, index)
        bound = self._bound.pop(key, None)
        if bound and bound.action is not None:
            try:
                bound.action.will_disappear(bound.ctx)
            except Exception:
                pass

    def _appear_visible(self) -> None:
        page = self.current_page()
        if page is None:
            return
        for i in range(len(page.buttons)):
            self._bind(page.id, i)

    def _release_visible(self) -> None:
        page = self.current_page()
        if page is None:
            return
        for i in range(len(page.buttons)):
            self._unbind(page.id, i)

    def _render_key(self, index: int) -> None:
        if self.lock_active:
            if self.device is not None and self._lock_tiles and 0 <= index < len(self._lock_tiles):
                self.device.set_key_image(index, self._lock_tiles[index])
            return
        page = self.current_page()
        if page is None or self.device is None:
            return
        if not (0 <= index < len(page.buttons)):
            return
        bound = self._bound.get(self._bkey(page.id, index))
        slot = page.buttons[index]
        runtime = bound.runtime if bound else RuntimeVisual()
        icon = bound.default_icon if bound else None
        kind = bound.info.icon if bound and bound.info else "grid"
        image = render_key(self.device.spec, slot, runtime, icon, kind)
        self.device.set_key_image(index, image)

    def _push_all_images(self) -> None:
        if self.lock_active:
            self._refresh_lock_overlay()
            return
        if self.device is None:
            return
        for i in range(self.device.spec.key_count):
            self._render_key(i)
            self.key_visual_changed.emit(i)

    def _flash(self, page_id: str, key: int, kind: str) -> None:
        bound = self._bound.get(self._bkey(page_id, key))
        if bound is None:
            return
        bound.runtime.flash = kind
        if self.current_page() and self.current_page().id == page_id:
            self._render_key(key)
            self.key_visual_changed.emit(key)
        self._flash_timer.start()

    def _clear_flashes(self) -> None:
        self._flash_timer.stop()
        page = self.current_page()
        if page is None:
            return
        for i in range(len(page.buttons)):
            bound = self._bound.get(self._bkey(page.id, i))
            if bound and bound.runtime.flash:
                bound.runtime.flash = ""
                self._render_key(i)
                self.key_visual_changed.emit(i)
