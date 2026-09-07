"""Best-effort USB inventory of Elgato devices. Does not claim the HID interface."""

from __future__ import annotations

from pathlib import Path

from popstream.core.specs import ELGATO_VID, spec_for_pid


def probe_elgato_usb() -> list[dict]:
    found: list[dict] = []
    root = Path("/sys/bus/usb/devices")
    if not root.is_dir():
        return found
    seen: set[str] = set()
    for node in root.iterdir():
        vid_file = node / "idVendor"
        pid_file = node / "idProduct"
        if not vid_file.is_file() or not pid_file.is_file():
            continue
        try:
            vid = int(vid_file.read_text().strip(), 16)
            pid = int(pid_file.read_text().strip(), 16)
        except ValueError:
            continue
        if vid != ELGATO_VID:
            continue
        spec = spec_for_pid(pid)
        if spec is None:
            continue
        serial = ""
        serial_file = node / "serial"
        if serial_file.is_file():
            serial = serial_file.read_text().strip()
        key = serial or f"{pid:04x}-{node.name}"
        if key in seen:
            continue
        seen.add(key)
        name = spec.name if spec else f"Elgato USB {pid:04x}"
        found.append(
            {
                "spec_id": spec.id if spec else "mk2",
                "pid": pid,
                "serial": serial or key,
                "label": name,
                "sysfs": str(node),
            }
        )
    return found
