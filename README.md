# PopStream

A Stream Deck editor for **Linux**. PopStream talks to Elgato hardware over HID, mirrors the keys on screen, and runs a plugin host for actions. There is no official Elgato app on Linux. Only one program can own the deck, so quit any other Stream Deck client before opening PopStream.

Needs a typical Linux desktop with:

- Python 3.10+ and PySide6
- `hidraw` access via udev (installer writes the rules)
- PipeWire or PulseAudio (`pactl`) for the Audio actions
- A freedesktop tray / StatusNotifier host (GNOME, COSMIC, KDE, Hyprland, and similar)

Lock-screen tiles and some desktop integrations work best where logind or a ScreenSaver D-Bus API is available; elsewhere those features degrade gracefully.

## Requirements

Install these on the host first. `setup.py` installs PySide6 into `.venv`; it does **not** install Python for you.

- Linux (see above)
- **Python 3.10+** (`python3 --version`), from your distro packages
- [PySide6](https://pypi.org/project/PySide6/) 6.5+ (pulled into `.venv` by setup)

Optional companions:

- [`pactl`](https://www.freedesktop.org/wiki/Software/PulseAudio/): output / input / mute / volume
- `parec` (PipeWire-pulse / PulseAudio): live Mic Check level meter
- [`playerctl`](https://github.com/altdesktop/playerctl): play / pause / skip
- [`upower`](https://upower.freedesktop.org/): wireless device battery
- **Bloop**: soundboard keys (must be running)
- **Cliamp**: music player keys (must be running)
- **eqFX**: EQ preset keys
- **Cursor**: Usage key (uses the account signed into the Cursor app on this machine)
- `hyprctl` / `swaymsg` / `wmctrl`: Move to Monitor on Hyprland, Sway, or X11

## Install

From the project root:

```bash
python3 setup.py
```

That is the first-run installer (not setuptools; package metadata is in `pyproject.toml`). It:

- installs PySide6 into `.venv`
- writes the app-menu launcher and login autostart entry for **this checkout**
- writes the grid icon
- installs udev rules (sudo) so the Stream Deck works without root

Unplug and replug the deck after udev.

Then:

```bash
python3 run.py
```

Or open **PopStream** from the app menu. After login it starts hidden in the tray. Look for the tray icon (Hyprland/Omarchy: top bar), or launch again to raise the window. Closing the window hides to the tray by default so the deck keeps working; that is Settings → General.

Skip hardware rules if you only want the editor:

```bash
python3 setup.py --no-udev
```

`--no-autostart` skips the login entry. Manual udev, if you prefer:

```bash
sudo cp udev/99-popstream-streamdeck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Supported layouts include Mini, Original, Original V2, MK.2, XL, Plus, Neo, and Pedal. Virtual decks stay in the device list so you can design for a model you do not have plugged in.

## Use

- **Click a key** to select it and edit it in the inspector.
- **Drag an action** from the left catalog onto a key. Click an action first to set its options, then drag. Those settings apply on drop.
- **Double-click a key** (or press Space) to run the action. Presses on the physical deck always run.
- **Drag keys** onto each other to swap them.
- Pages sit under the canvas. Folders open a nested page; Back returns.
- **Settings** (`Ctrl+,`) covers general (keep in tray), colors, lock-screen tiles, plugins, and profiles. Mark a default profile per deck layout; it is the one PopStream opens on launch.

Mute, missing devices, and the active **Set Output** / **Set Input** show on the icon (red or green). The key background stays as you set it.

**Set Output** with eqFX remembers each hardware sink’s volume (and mute) in `~/.local/share/eqfx/device_volumes.json` and restores it on switch. Volume keys also target the active hardware sink when eqFX is the default.

## Built-in actions

| Group | Actions |
| --- | --- |
| System | Website, Open App, Run Command, Hotkey, Text, Move to Monitor |
| Navigation | Folder, Back, Next Page, Previous Page |
| Clock | Clock, Date |
| Audio | Set Output, Set Input, Fix Mic, Mic Check, EQ Preset, volume / mute, Play / Pause, skip, stop |
| Battery | Device Battery (UPower) |
| Usage | Cursor Usage (included usage this billing cycle; remaining or used) |
| Bloop | Play Sound, Play / Stop, Stop All, Play Random, volume, Cable, Now Playing. Sound folders sit under Bloop after Now Playing so you can expand a category and drop a clip onto a key. |
| Cliamp | Play / Pause, Now Playing, skip, stop, volume, shuffle, repeat, playlist, EQ |

## Data

Profiles and settings live under `~/.local/share/PopStream/` (`settings.json` and `profiles/*.json`).

## Plugins

Drop a folder with `plugin.py` into `~/.local/share/PopStream/plugins/` or `./plugins/` next to this repo. The module must expose `plugin` (an action `Plugin`). It may also expose `driver` (a `DeviceDriver`) if it opens hardware. Settings → Plugins turns each loaded plugin on or off.

See `src/popstream/core/plugin.py` and `examples/hello_plugin/`.
