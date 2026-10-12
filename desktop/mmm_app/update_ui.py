"""Check for Updates: the window and the background work behind it."""
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtWidgets import QDialog, QProgressBar, QTextBrowser, QVBoxLayout

from . import __version__, system, theme, updater
from .theme import BOLD
from .widgets import AccentButton, GhostButton, IconLabel, Label, hbox, vbox


class UpdateController(QObject):
    """States: idle, checking, current, available, downloading, ready,
    installing, error."""

    changed = Signal()
    install_requested = Signal()  # the window confirms and quits
    _relay = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "idle"
        self.info = None
        self.error = ""
        self.progress = 0.0
        self.path = None
        self.quiet = False
        self._cancel = False
        self._relay.connect(self._apply, Qt.ConnectionType.QueuedConnection)

    @property
    def available_version(self):
        return self.info.version if self.info is not None and self.state in ("available", "downloading", "ready") else None

    def can_install(self):
        return updater.install_target()

    def _apply(self, fn):
        fn()
        self.changed.emit()

    def _set(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        self.changed.emit()

    def check(self, quiet=False):
        if self.state in ("checking", "downloading", "installing"):
            return
        if self.state in ("ready",) and self.info is not None:
            return
        self.quiet = quiet
        self._set(state="checking", error="")

        def work():
            try:
                info = updater.check()
            except updater.UpdateError as e:
                msg = str(e)
                self._relay.emit(lambda: self._done_check(None, msg))
                return
            except Exception as e:  # noqa: BLE001 - shown to the user
                msg = f"The update check failed ({e})."
                self._relay.emit(lambda: self._done_check(None, msg))
                return
            self._relay.emit(lambda: self._done_check(info, ""))

        threading.Thread(target=work, daemon=True).start()

    def _done_check(self, info, error):
        self.info = info
        if error:
            self.state, self.error = "error", error
        else:
            self.state = "available" if info is not None else "current"

    def download(self):
        if self.info is None or self.state not in ("available", "error"):
            return
        self._cancel = False
        self._set(state="downloading", progress=0.0, error="")
        info = self.info
        last = [0.0]

        def progress(p):
            if p - last[0] >= 0.01 or p >= 1.0:
                last[0] = p
                self._relay.emit(lambda: setattr(self, "progress", p))

        def work():
            try:
                path = updater.download(info, progress, lambda: self._cancel)
            except updater.UpdateError as e:
                msg = str(e)
                self._relay.emit(lambda: self._failed(msg))
                return
            except Exception as e:  # noqa: BLE001
                msg = f"The download failed ({e})."
                self._relay.emit(lambda: self._failed(msg))
                return
            self._relay.emit(lambda: self._downloaded(path))

        threading.Thread(target=work, daemon=True).start()

    def cancel_download(self):
        self._cancel = True

    def _failed(self, msg):
        self.state, self.error = "error", msg

    def _downloaded(self, path):
        self.path = path
        self.state = "ready"
        self.install_requested.emit()

    def install(self):
        """Starts the installer; the caller quits the app right after."""
        if self.path is None:
            return False
        try:
            updater.install(self.path)
        except updater.UpdateError as e:
            self._set(state="error", error=str(e))
            return False
        except OSError as e:
            self._set(state="error", error=f"The update couldn't be started ({e.strerror or e}).")
            return False
        self._set(state="installing")
        return True


class UpdateDialog(QDialog):
    def __init__(self, controller, parent=None):
        super().__init__(parent)
        self.c = controller
        self.setWindowTitle("Software Update")
        self.setFixedWidth(480)
        self.icon = IconLabel("update", theme.accent(), 28, weight=1.6)
        self.title = Label("", 15, BOLD, wrap=True)
        self.text = Label("", 12, color=theme.TEXT_DIM, wrap=True)
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setFixedHeight(160)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setFixedHeight(6)
        self.close_btn = GhostButton("Close")
        self.close_btn.clicked.connect(self.reject)
        self.page_btn = GhostButton("Open Download Page")
        self.page_btn.clicked.connect(self._open_page)
        self.check_btn = GhostButton("Check Again")
        self.check_btn.clicked.connect(lambda: self.c.check())
        self.cancel_btn = GhostButton("Stop Download")
        self.cancel_btn.clicked.connect(self.c.cancel_download)
        self.install_btn = AccentButton("Install and Restart", "update", expand=False)
        self.install_btn.clicked.connect(self._install_clicked)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.setSpacing(14)
        head = hbox(self.icon, vbox(self.title, self.text, spacing=4), spacing=14)
        head.setAlignment(self.icon, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(head)
        lay.addWidget(self.notes)
        lay.addWidget(self.bar)
        lay.addLayout(hbox(self.page_btn, None, self.close_btn, self.check_btn, self.cancel_btn, self.install_btn))
        self.c.changed.connect(self.refresh)
        self.refresh()

    def done(self, r):
        try:
            self.c.changed.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        if self.c.state == "downloading":
            self.c.cancel_download()
        super().done(r)

    def _install_clicked(self):
        if self.c.state == "ready":
            self.c.install_requested.emit()
        else:
            self.c.download()

    def _open_page(self):
        url = self.c.info.page_url if self.c.info is not None else updater.RELEASES_PAGE
        system.open_url(url)

    def refresh(self):
        c = self.c
        st = c.state
        can, why = c.can_install()
        info = c.info
        self.notes.setVisible(info is not None and st in ("available", "downloading", "ready", "installing", "error")
                              and bool(info.notes.strip()))
        if info is not None:
            self.notes.setMarkdown(info.notes or "")
        self.bar.setVisible(st == "downloading")
        self.bar.setValue(int(c.progress * 1000))
        self.cancel_btn.setVisible(st == "downloading")
        self.check_btn.setVisible(st in ("current", "error", "idle") and info is None)
        self.install_btn.setVisible(info is not None and st in ("available", "error", "ready") and can)
        self.install_btn.setText("Try Again" if st == "error" else "Install and Restart")
        self.page_btn.setVisible(info is not None or st == "error")
        self.icon.set("update", theme.WARN if st == "error" else theme.accent())
        if st == "checking" or st == "idle":
            self.title.setText("Checking for updates…")
            self.text.setText(f"You have version {__version__}.")
        elif st == "current":
            self.title.setText("You're up to date")
            self.text.setText(f"Version {__version__} is the newest version of Manifold Mesh Mix for {system.OS_NAME}.")
        elif st == "available":
            self.title.setText(f"Version {info.version} is available")
            if can:
                self.text.setText(f"You have version {__version__}. Install and Restart downloads it, checks it, "
                                  "installs it and opens the app again.")
            else:
                self.text.setText(f"You have version {__version__}. {why} Open the download page to get the new version.")
        elif st == "downloading":
            self.title.setText(f"Downloading version {info.version}…")
            size = f" ({info.size / 1e6:.0f} MB)" if info.size else ""
            self.text.setText(f"{int(c.progress * 100)}%{size}. The app restarts by itself when it's installed.")
        elif st == "ready":
            self.title.setText(f"Version {info.version} is ready to install")
            self.text.setText("The app will close, install the update and open again.")
        elif st == "installing":
            self.title.setText(f"Installing version {info.version}…")
            self.text.setText("The app closes now and opens again when the update is installed.")
        elif st == "error":
            self.title.setText("The update didn't work" if info is not None else "Couldn't check for updates")
            self.text.setText(c.error)
        self.adjustSize()


def schedule_launch_check(controller, settings, delay_ms=4000):
    """Looks for a newer version a few seconds after the app opened
    (installing always needs a click)."""
    if not settings.checkUpdates:
        return
    QTimer.singleShot(delay_ms, lambda: settings.checkUpdates and controller.check(quiet=True))
