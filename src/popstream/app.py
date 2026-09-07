from __future__ import annotations

import ctypes
import sys
from pathlib import Path

from PySide6.QtGui import QGuiApplication
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from popstream.core.engine import Engine
from popstream.core.icons import application_icon
from popstream.core.store import user_plugins_dir
from popstream.plugins import builtin_plugins
from popstream.ui.main_window import MainWindow
from popstream.ui.theme import apply_app_theme

_INSTANCE = "popstream-session"


def _set_process_identity() -> None:
    QGuiApplication.setDesktopFileName("popstream")
    if sys.argv:
        sys.argv[0] = "popstream"
    try:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(15, b"popstream", 0, 0, 0)
    except Exception:
        pass


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def _handoff_to_running() -> bool:
    socket = QLocalSocket()
    socket.connectToServer(_INSTANCE)
    if not socket.waitForConnected(150):
        return False
    socket.write(b"raise")
    socket.waitForBytesWritten(150)
    socket.flush()
    socket.disconnectFromServer()
    return True


def _listen_for_handoff(app: QApplication) -> QLocalServer:
    QLocalServer.removeServer(_INSTANCE)
    server = QLocalServer(app)
    server.listen(_INSTANCE)
    return server


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    start_in_tray = "--tray" in argv
    argv = [item for item in argv if item != "--tray"]

    _set_process_identity()
    if argv:
        argv[0] = "popstream"
    app = QApplication(argv)
    app.setApplicationName("PopStream")
    app.setOrganizationName("PopStream")
    app.setApplicationDisplayName("PopStream")
    app.setDesktopFileName("popstream")
    app.setQuitOnLastWindowClosed(False)
    if _handoff_to_running():
        return 0
    icon = application_icon()
    app.setWindowIcon(icon)
    server = _listen_for_handoff(app)

    engine = Engine()
    engine.bootstrap(builtin_plugins())
    root = _project_root()
    engine.load_external_plugins(
        user_plugins_dir(),
        root / "plugins",
    )
    apply_app_theme(app)

    window = MainWindow(engine)
    server.newConnection.connect(window._show_from_tray)
    if start_in_tray:
        window._hide_to_tray()
    else:
        window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
