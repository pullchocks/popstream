from __future__ import annotations

from popstream.plugins.audio import AudioPlugin
from popstream.plugins.battery import BatteryPlugin
from popstream.plugins.bloop import BloopPlugin
from popstream.plugins.cliamp import CliampPlugin
from popstream.plugins.clock import ClockPlugin
from popstream.plugins.navigation import NavigationPlugin
from popstream.plugins.system import SystemPlugin


def builtin_plugins():
    return [
        SystemPlugin(),
        NavigationPlugin(),
        ClockPlugin(),
        AudioPlugin(),
        BatteryPlugin(),
        BloopPlugin(),
        CliampPlugin(),
    ]
