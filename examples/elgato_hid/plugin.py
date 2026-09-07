"""Skeleton Elgato Stream Deck HID driver.

This file is the contract for the next milestone: a plugin that actually
talks to hardware. It is not loaded by default.

Public protocol notes (Elgato VID 0x0FD9):

* Output reports carry JPEG (MK.2 / Mini / XL) or rotated BMP (Original).
* Key images are sliced into HID packets; packet header differs per model.
* Feature report 0x05 / 0x03 typically sets brightness.
* Input reports deliver key bitmasks on press/release.

A real driver should:

1. ``probe()`` using hidapi (VID 0x0FD9, PIDs in ``popstream.core.specs``)
2. Return a ``Device`` subclass that implements ``set_key_image`` / ``set_brightness``
3. Emit ``key_down`` / ``key_up`` from a reader thread via queued Qt signals
"""

from popstream.core.device import DeviceDriver
from popstream.core.specs import ELGATO_VID, PID_TO_SPEC
from popstream.core.usbprobe import probe_elgato_usb


class ElgatoHidDriver(DeviceDriver):
    id = "com.popstream.driver.elgato"
    name = "Elgato Stream Deck HID"

    def probe(self):
        devices = []
        for item in probe_elgato_usb():
            devices.append(
                {
                    "spec_id": item["spec_id"],
                    "serial": item["serial"],
                    "label": f"{item['label']} ({item['serial']})",
                    "vid": ELGATO_VID,
                    "pid": item["pid"],
                }
            )
        return devices

    def open(self, probe: dict):
        # Intentionally unimplemented — hardware I/O is the plugin milestone.
        _ = PID_TO_SPEC
        return None


driver = ElgatoHidDriver()
