"""Known Stream Deck layouts and USB identities.

Hardware I/O is intentionally not implemented here. A device-driver plugin
will consume these specs (VID/PID, key image size, JPEG vs BMP, rotation)
to talk to real Elgato hardware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


ELGATO_VID = 0x0FD9


@dataclass(frozen=True)
class DeviceSpec:
    id: str
    name: str
    columns: int
    rows: int
    key_size: int
    usb_pid: int
    image_format: str = "jpeg"  # "jpeg" or "bmp"
    rotate_180: bool = False
    extra: str = ""  # "dials", "touchstrip", "no_lcd"
    button_gap: int = 16

    @property
    def key_count(self) -> int:
        return self.columns * self.rows

    @property
    def has_lcd(self) -> bool:
        return self.extra != "no_lcd" and self.key_size > 0


# Product IDs collected from public Stream Deck HID documentation.
SPECS: dict[str, DeviceSpec] = {
    "mini": DeviceSpec("mini", "Stream Deck Mini", 3, 2, 80, 0x0063),
    "original": DeviceSpec(
        "original",
        "Stream Deck Original",
        5,
        3,
        72,
        0x0060,
        image_format="bmp",
        rotate_180=True,
    ),
    "original_v2": DeviceSpec("original_v2", "Stream Deck", 5, 3, 72, 0x006D),
    "mk2": DeviceSpec("mk2", "Stream Deck MK.2", 5, 3, 72, 0x0080),
    "xl": DeviceSpec("xl", "Stream Deck XL", 8, 4, 96, 0x006C),
    "plus": DeviceSpec("plus", "Stream Deck +", 4, 2, 120, 0x0084, extra="dials"),
    "neo": DeviceSpec("neo", "Stream Deck Neo", 4, 2, 96, 0x009A, extra="touchstrip"),
    "pedal": DeviceSpec("pedal", "Stream Deck Pedal", 3, 1, 0, 0x0086, extra="no_lcd"),
}

PID_TO_SPEC: dict[int, DeviceSpec] = {s.usb_pid: s for s in SPECS.values()}

# Additional revisions seen in the wild; map onto the closest layout.
PID_TO_SPEC.update(
    {
        0x006D: SPECS["original_v2"],
        0x008F: SPECS["xl"],
        0x0090: SPECS["mk2"],
        0x00A5: SPECS["mk2"],
    }
)

GRID_SPECS = [SPECS[k] for k in ("mini", "original", "mk2", "xl", "plus", "neo")]


def spec_for_pid(pid: int) -> Optional[DeviceSpec]:
    return PID_TO_SPEC.get(pid)
