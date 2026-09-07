#!/usr/bin/env python3
"""First-run installer for PopStream on Pop!_OS.

This is not a setuptools script. From the project root:

    python3 setup.py

Installs PySide6 into .venv, writes the app-menu and autostart entries for this
checkout, writes the icon, and (with sudo) installs udev rules for the deck.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
LAUNCHER = ROOT / "packaging" / "popstream"
UDEV_SRC = ROOT / "udev" / "99-popstream-streamdeck.rules"
UDEV_DST = Path("/etc/udev/rules.d/99-popstream-streamdeck.rules")
ICON = Path.home() / ".local/share/icons/hicolor/128x128/apps/popstream.png"
APP_DESKTOP = Path.home() / ".local/share/applications/popstream.desktop"
AUTOSTART_DESKTOP = Path.home() / ".config/autostart/popstream.desktop"


def _run(command: list[str], **kwargs) -> None:
    print("+", " ".join(command))
    subprocess.run(command, check=True, **kwargs)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {path}")


def _fill_desktop(name: str, icon: Path) -> str:
    template = (ROOT / "packaging" / name).read_text(encoding="utf-8")
    return template.replace("@ROOT@", str(ROOT)).replace("@ICON@", str(icon))


def install_deps() -> None:
    VENV.mkdir(parents=True, exist_ok=True)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--target",
            str(VENV),
            "-r",
            str(ROOT / "requirements.txt"),
        ]
    )


def write_icon() -> Path:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(VENV))
    sys.path.insert(0, str(ROOT / "src"))
    from PySide6.QtGui import QGuiApplication

    from popstream.core.icons import icon_pixmap

    _app = QGuiApplication.instance() or QGuiApplication(["popstream-setup"])
    ICON.parent.mkdir(parents=True, exist_ok=True)
    if not icon_pixmap("grid", 128).save(str(ICON), "PNG"):
        raise SystemExit(f"could not write {ICON}")
    print(f"wrote {ICON}")
    return ICON


def write_desktop_files(icon: Path, autostart: bool) -> None:
    LAUNCHER.chmod(LAUNCHER.stat().st_mode | 0o111)
    _write(APP_DESKTOP, _fill_desktop("popstream.desktop", icon))
    if autostart:
        _write(AUTOSTART_DESKTOP, _fill_desktop("popstream-autostart.desktop", icon))
    update = shutil.which("update-desktop-database")
    if update:
        subprocess.run([update, str(APP_DESKTOP.parent)], check=False)


def install_udev() -> None:
    _run(["sudo", "cp", str(UDEV_SRC), str(UDEV_DST)])
    _run(["sudo", "udevadm", "control", "--reload-rules"])
    _run(["sudo", "udevadm", "trigger"])
    print("Unplug and replug the Stream Deck so the new permissions apply.")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    leftover = {
        "build",
        "egg_info",
        "sdist",
        "bdist_wheel",
        "develop",
        "editable_wheel",
    }
    if argv and argv[0] in leftover:
        print(
            "setup.py is the PopStream first-run installer, not setuptools.\n"
            "Run: python3 setup.py\n"
            "The package metadata lives in pyproject.toml.",
            file=sys.stderr,
        )
        return 2

    parser = argparse.ArgumentParser(description="Install PopStream on this Pop!_OS machine.")
    parser.add_argument("--no-udev", action="store_true", help="Skip Stream Deck udev rules (no sudo).")
    parser.add_argument("--no-autostart", action="store_true", help="Skip the login autostart entry.")
    args = parser.parse_args(argv)

    if os.geteuid() == 0:
        print("Do not run setup.py as root. It will sudo only for udev.", file=sys.stderr)
        return 1

    print(f"Installing PopStream from {ROOT}")
    install_deps()
    icon = write_icon()
    write_desktop_files(icon, autostart=not args.no_autostart)
    if args.no_udev:
        print("Skipped udev. The deck will stay permission-denied until the rules are installed.")
    else:
        try:
            install_udev()
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            print(f"udev install failed ({exc}). Manual steps:", file=sys.stderr)
            print(f"  sudo cp {UDEV_SRC} {UDEV_DST}", file=sys.stderr)
            print("  sudo udevadm control --reload-rules && sudo udevadm trigger", file=sys.stderr)
            return 1

    print()
    print("Done. Then:")
    print("  python3 run.py")
    print("Or open PopStream from the app menu. After login it starts in the tray.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
