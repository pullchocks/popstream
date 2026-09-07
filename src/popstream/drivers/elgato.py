"""Linux hidraw driver for Elgato Stream Deck devices.

Talks the public Stream Deck HID protocol (VID 0x0FD9). Your deck is opened
as a Device so the rest of PopStream can push key images and receive presses.
"""

from __future__ import annotations

import array
import fcntl
import os
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QSocketNotifier, Qt
from PySide6.QtGui import QImage

from popstream.core.device import Device, DeviceDriver
from popstream.core.specs import ELGATO_VID, DeviceSpec, spec_for_pid

# JPEG-family decks (Original V2, MK.2, Mini, XL, Plus, Neo) share this report layout.
_JPEG_PIDS = {0x0063, 0x006C, 0x006D, 0x0080, 0x0084, 0x008F, 0x0090, 0x009A, 0x00A5}


def _ioc(direction: int, type_: str, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord(type_) << 8) | nr


def _hid_set_feature(length: int) -> int:
    return _ioc(3, "H", 0x06, length)


def iter_hidraw_decks() -> list[dict]:
    found: list[dict] = []
    root = Path("/sys/class/hidraw")
    if not root.is_dir():
        return found
    for node in sorted(root.glob("hidraw*")):
        uevent = node / "device" / "uevent"
        if not uevent.is_file():
            continue
        fields: dict[str, str] = {}
        for line in uevent.read_text(errors="ignore").splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                fields[key] = value
        hid_id = fields.get("HID_ID", "")
        parts = hid_id.split(":")
        if len(parts) != 3:
            continue
        try:
            vid = int(parts[1], 16)
            pid = int(parts[2], 16)
        except ValueError:
            continue
        if vid != ELGATO_VID:
            continue
        spec = spec_for_pid(pid)
        if spec is None:
            continue
        serial = fields.get("HID_UNIQ", "").strip()
        path = Path("/dev") / node.name
        found.append(
            {
                "path": str(path),
                "vid": vid,
                "pid": pid,
                "serial": serial,
                "spec": spec,
                "name": fields.get("HID_NAME", spec.name),
            }
        )
    return found


def _to_jpeg(image: QImage, spec: DeviceSpec) -> bytes:
    size = spec.key_size
    img = image.convertToFormat(QImage.Format.Format_RGB888)
    if img.width() != size or img.height() != size:
        img = img.scaled(
            size,
            size,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    # Original V2 / MK.2 / XL expect the bitmap rotated 180° (flip both axes).
    if spec.image_format == "jpeg":
        img = img.mirrored(True, True)
    elif spec.rotate_180:
        img = img.mirrored(True, True)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buffer, "JPEG", 90)
    return bytes(buffer.data())


class ElgatoHidDevice(Device):
    def __init__(self, spec: DeviceSpec, path: str, serial: str, parent=None) -> None:
        super().__init__(spec, parent)
        self._path = path
        self._serial = serial or path
        self._fd = -1
        self._notifier: QSocketNotifier | None = None
        self._open = False
        self._pressed: list[bool] = [False] * spec.key_count
        self._last_error = ""

    def id(self) -> str:
        return f"hid:{self._serial}"

    def label(self) -> str:
        return f"{self.spec.name} ({self._serial})"

    def serial(self) -> str:
        return self._serial

    def is_hardware(self) -> bool:
        return True

    def is_open(self) -> bool:
        return self._open

    def last_error(self) -> str:
        return self._last_error

    def open(self) -> bool:
        if self._open:
            return True
        try:
            self._fd = os.open(self._path, os.O_RDWR)
        except OSError as exc:
            self._last_error = f"Cannot open {self._path}: {exc}"
            return False
        self._open = True
        self._reset_stream()
        self._send_feature(bytes([0x03, 0x02]))  # reset to blank
        self.set_brightness(self._brightness)
        self._notifier = QSocketNotifier(self._fd, QSocketNotifier.Type.Read, self)
        self._notifier.activated.connect(self._on_readable)
        return True

    def close(self) -> None:
        if self._notifier is not None:
            self._notifier.setEnabled(False)
            self._notifier.deleteLater()
            self._notifier = None
        if self._fd >= 0:
            try:
                self._send_feature(bytes([0x03, 0x02]))
            except OSError:
                pass
            os.close(self._fd)
            self._fd = -1
        self._open = False

    def set_brightness(self, percent: int) -> None:
        self._brightness = max(0, min(100, int(percent)))
        if self._open:
            self._send_feature(bytes([0x03, 0x08, self._brightness]))

    def set_key_image(self, index: int, image: QImage) -> None:
        if not self._open or not (0 <= index < self.spec.key_count):
            return
        if self.spec.usb_pid not in _JPEG_PIDS:
            return
        payload = _to_jpeg(image, self.spec)
        report_len = 1024
        header_len = 8
        chunk = report_len - header_len
        remaining = len(payload)
        page = 0
        while remaining > 0:
            this_len = min(remaining, chunk)
            sent = page * chunk
            last = 1 if this_len == remaining else 0
            header = bytes(
                [
                    0x02,
                    0x07,
                    index,
                    last,
                    this_len & 0xFF,
                    (this_len >> 8) & 0xFF,
                    page & 0xFF,
                    (page >> 8) & 0xFF,
                ]
            )
            packet = header + payload[sent : sent + this_len]
            packet += bytes(report_len - len(packet))
            self._write(packet)
            remaining -= this_len
            page += 1

    def reset_to_logo(self) -> None:
        self._send_feature(bytes([0x03, 0x02]))

    def _reset_stream(self) -> None:
        self._write(bytes([0x02]) + bytes(1023))

    def _write(self, data: bytes) -> None:
        if self._fd < 0:
            return
        os.write(self._fd, data)

    def _send_feature(self, prefix: bytes) -> None:
        if self._fd < 0:
            return
        buf = array.array("B", prefix + bytes(32 - len(prefix)))
        fcntl.ioctl(self._fd, _hid_set_feature(len(buf)), buf)

    def _on_readable(self, *_args) -> None:
        if self._fd < 0:
            return
        try:
            data = os.read(self._fd, 64)
        except OSError:
            self.disconnected.emit()
            return
        if not data:
            return
        offset = 4
        states = data[offset : offset + self.spec.key_count]
        for index, raw in enumerate(states):
            pressed = bool(raw)
            if pressed and not self._pressed[index]:
                self.key_down.emit(index)
            elif not pressed and self._pressed[index]:
                self.key_up.emit(index)
            if index < len(self._pressed):
                self._pressed[index] = pressed


class ElgatoHidDriver(DeviceDriver):
    id = "com.popstream.driver.elgato"
    name = "Elgato Stream Deck HID"
    last_error = ""

    def probe(self) -> list[dict]:
        devices = []
        for item in iter_hidraw_decks():
            devices.append(
                {
                    "spec_id": item["spec"].id,
                    "serial": item["serial"],
                    "label": f"{item['spec'].name} ({item['serial']})",
                    "path": item["path"],
                    "vid": item["vid"],
                    "pid": item["pid"],
                    "spec": item["spec"],
                }
            )
        return devices

    def open(self, probe: dict) -> Device | None:
        spec = probe.get("spec") or spec_for_pid(int(probe.get("pid", 0)))
        if spec is None:
            return None
        return ElgatoHidDevice(spec, probe["path"], probe.get("serial") or "", None)
