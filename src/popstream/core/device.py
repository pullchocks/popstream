from __future__ import annotations

from abc import ABC, abstractmethod

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from popstream.core.specs import DeviceSpec


class Device(QObject):
    """Something that can show key images and emit key events.

    VirtualDevice is built in. Hardware plugins should subclass this (or
    wrap a DeviceDriver that returns one) and push the same signals.
    """

    key_down = Signal(int)
    key_up = Signal(int)
    disconnected = Signal()

    def __init__(self, spec: DeviceSpec, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._spec = spec
        self._brightness = 75

    @property
    def spec(self) -> DeviceSpec:
        return self._spec

    @property
    def brightness(self) -> int:
        return self._brightness

    @abstractmethod
    def id(self) -> str: ...

    @abstractmethod
    def label(self) -> str: ...

    @abstractmethod
    def serial(self) -> str: ...

    @abstractmethod
    def is_hardware(self) -> bool: ...

    @abstractmethod
    def is_open(self) -> bool: ...

    @abstractmethod
    def open(self) -> bool: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def set_brightness(self, percent: int) -> None: ...

    @abstractmethod
    def set_key_image(self, index: int, image: QImage) -> None: ...

    @abstractmethod
    def reset_to_logo(self) -> None: ...


class VirtualDevice(Device):
    """On-screen stand-in used until a hardware driver plugin is loaded."""

    def __init__(self, spec: DeviceSpec, parent: QObject | None = None) -> None:
        super().__init__(spec, parent)
        self._open = False
        self._images: list[QImage | None] = [None] * spec.key_count

    def id(self) -> str:
        return f"virtual:{self.spec.id}"

    def label(self) -> str:
        return f"{self.spec.name} (Virtual)"

    def serial(self) -> str:
        return f"VIRT-{self.spec.id.upper()}"

    def is_hardware(self) -> bool:
        return False

    def is_open(self) -> bool:
        return self._open

    def open(self) -> bool:
        self._open = True
        return True

    def close(self) -> None:
        self._open = False

    def set_brightness(self, percent: int) -> None:
        self._brightness = max(0, min(100, int(percent)))

    def set_key_image(self, index: int, image: QImage) -> None:
        if 0 <= index < len(self._images):
            self._images[index] = image.copy()

    def reset_to_logo(self) -> None:
        self._images = [None] * self.spec.key_count

    def image(self, index: int) -> QImage | None:
        if 0 <= index < len(self._images):
            return self._images[index]
        return None

    def simulate_down(self, index: int) -> None:
        self.key_down.emit(index)

    def simulate_up(self, index: int) -> None:
        self.key_up.emit(index)


class DeviceDriver(ABC):
    """Plugin contract for enumerating and opening real Stream Decks."""

    id: str = ""
    name: str = ""

    def probe(self) -> list[dict]:
        """Return dicts with keys: spec_id, serial, label."""
        return []

    def open(self, probe: dict) -> Device | None:
        return None
