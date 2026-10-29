"""Starts the app: one window per user (files opened while it runs go to
the open window), the dark theme, and the optional scripted self-test."""
import getpass
import json
import os
import re
import sys
import time

from PySide6.QtCore import QCoreApplication, QIODevice, Qt
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication

from . import APP_NAME, __version__, system, theme, updater


def _server_name():
    try:
        user = getpass.getuser()
    except Exception:  # noqa: BLE001
        user = "user"
    return "ManifoldMeshMix-" + re.sub(r"[^A-Za-z0-9_.-]", "_", user)


def _send_to_running(paths):
    """Hands the files to an app that is already open. True when one was."""
    s = QLocalSocket()
    s.connectToServer(_server_name(), QIODevice.OpenModeFlag.WriteOnly)
    if not s.waitForConnected(500):
        return False
    s.write((json.dumps({"paths": paths}) + "\n").encode("utf-8"))
    s.waitForBytesWritten(2000)
    s.disconnectFromServer()
    return True


def _listen(window):
    server = QLocalServer(window)
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(_server_name()):
        QLocalServer.removeServer(_server_name())  # left over from a crash
        if not server.listen(_server_name()):
            return None

    def accept():
        while server.hasPendingConnections():
            sock = server.nextPendingConnection()
            buf = bytearray()

            def read(sock=sock, buf=buf):
                buf.extend(bytes(sock.readAll()))
                if b"\n" in buf:
                    try:
                        msg = json.loads(bytes(buf).split(b"\n", 1)[0].decode("utf-8"))
                        paths = [p for p in msg.get("paths", []) if isinstance(p, str)]
                    except ValueError:
                        paths = []
                    window.add_from_other_instance(paths)
                    sock.disconnectFromServer()

            sock.readyRead.connect(read)
            sock.disconnected.connect(sock.deleteLater)

    server.newConnection.connect(accept)
    return server


def _wait_for_previous_copy():
    """Started by the updater: let the old version finish quitting first."""
    try:
        pid = int(os.environ.pop("MMM_WAIT_FOR_PID", "0"))
    except ValueError:
        return
    end = time.time() + 20
    while pid > 0 and system.pid_alive(pid) and time.time() < end:
        time.sleep(0.2)


def _file_args(argv):
    return [os.path.abspath(a) for a in argv if not a.startswith("-") and os.path.exists(a)]


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    if "--version" in argv:
        print(__version__)
        return 0
    _wait_for_previous_copy()
    selftest = bool(os.environ.get("MMM_SELFTEST"))
    if os.environ.get("MMM_SOFTWARE_GL"):
        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL)
    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    if system.IS_WINDOWS:
        try:  # own taskbar icon and grouping instead of python.exe's
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ManifoldMeshMix.App")
        except Exception:  # noqa: BLE001
            pass
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationDisplayName(APP_NAME)
    app.setOrganizationName("ManifoldMeshMix")
    app.setApplicationVersion(__version__)
    app.setDesktopFileName("manifold-mesh-mix")
    app.setStyle("Fusion")
    app.setWindowIcon(QIcon(system.resource("icon-256.png")))

    files = _file_args(argv[1:])
    single = not selftest and not os.environ.get("MMM_NO_SINGLE_INSTANCE")
    if single and _send_to_running(files):
        return 0

    from .main_window import MainWindow
    from .settings import Settings

    settings = Settings()
    theme.install(app, settings)
    window = MainWindow(settings)
    # kept alive with the window: the installer waits for the mutex to go,
    # the server receives files opened while the app runs
    window._mutex = updater.hold_windows_mutex()
    window._server = _listen(window) if single else None
    window.show()
    if files:
        window.model.add(files)
    if selftest:
        from .selftest import SelfTest

        window._selftest = SelfTest(window)
        window._selftest.start()
    code = app.exec()
    return code
