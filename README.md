# PopStream

A Stream Deck editor for **Pop!_OS**. PopStream talks to Elgato hardware over HID, mirrors the keys on screen, and runs a plugin host for actions. Close the official Elgato software first — only one program can own the device.

This is not a Windows or macOS app. It is built for Pop!_OS with COSMIC: hidraw, udev, PipeWire, the session lock screen, tray, and autostart. Other Linux distros are not supported — the window might open, but audio, lock tiles, hotkeys, and the desktop integration will not behave the same.

## Requirements

- [Pop!_OS](https://pop.system76.com/) (COSMIC)
- Python 3.10+
- [PySide6](https://pypi.org/project/PySide6/) 6.5+

Optional companions:

- [`pactl`](https://www.freedesktop.org/wiki/Software/PulseAudio/) — output / input / mute / volume
- [`playerctl`](https://github.com/altdesktop/playerctl) — play / pause / skip
- [`upower`](https://upower.freedesktop.org/) — wireless device battery
- **Bloop** — soundboard keys (must be running)
- **Cliamp** — music player keys (must be running)
- **eqFX** — EQ preset keys

## Install

From the project root:

```bash
python3 -m pip install --target .venv -r requirements.txt
python3 run.py
```

Start hidden in the tray (used by autostart):

```bash
python3 run.py --tray
```

A second launch raises the existing window. Closing the window can minimize to the tray so the deck keeps working.

### Hardware access

Install the udev rules so your user can open the Stream Deck without root:

```bash
sudo cp udev/99-popstream-streamdeck.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules && sudo udevadm trigger
```

Unplug and replug the deck after that. Supported layouts include Mini, Original, Original V2, MK.2, XL, Plus, Neo, and Pedal. Virtual decks stay in the device list so you can design for a model you do not have plugged in.

### Desktop launcher

`packaging/popstream` launches `run.py`. Copy the `.desktop` files from `packaging/` into `~/.local/share/applications/` and `~/.config/autostart/` (edit the `Exec` and `Path` lines to match this checkout). The autostart entry uses `--tray` and a short delay so it comes up after login.

## Use

- **Click a key** to select it and edit it in the inspector.
- **Drag an action** from the left catalog onto a key. Click an action first to set its options, then drag — those settings apply on drop.
- **Double-click a key** (or press Space) to run the action. Presses on the physical deck always run.
- **Drag keys** onto each other to swap them.
- Pages sit under the canvas. Folders open a nested page; Back returns.
- **Settings** (`Ctrl+,`) covers colors, lock-screen tiles, and profiles. Mark a default profile per deck layout; it is the one PopStream opens on launch.

Mute, missing devices, and the active **Set Output** / **Set Input** show on the icon (red or green). The key background stays as you set it.

## Built-in actions

| Group | Actions |
| --- | --- |
| System | Website, Open App, Run Command, Hotkey, Text |
| Navigation | Folder, Back, Next Page, Previous Page |
| Clock | Clock, Date |
| Audio | Set Output, Set Input, EQ Preset, volume / mute, Play / Pause, skip, stop |
| Battery | Device Battery (UPower) |
| Bloop | Play Sound, Play / Stop, Stop All, Play Random, volume, Cable, Now Playing |
| Cliamp | Play / Pause, Now Playing, skip, stop, volume, shuffle, repeat, playlist, EQ |

## Data

Profiles and settings live under `~/.local/share/PopStream/` (`settings.json` and `profiles/*.json`).

## Plugins

Drop a folder with `plugin.py` into `~/.local/share/PopStream/plugins/` or `./plugins/` next to this repo. The module must expose `plugin` (an action `Plugin`). It may also expose `driver` (a `DeviceDriver`) if it opens hardware.

See `src/popstream/core/plugin.py` and `examples/hello_plugin/`.
