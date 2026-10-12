"""Locates and runs the conversion engine (a separate program).

The engine speaks JSON lines on stdout: stage / progress / log / warning /
part / result / error events. It runs in its own process, so a crash or an
out-of-memory kill never takes the window down, and Stop just ends it.
"""
import json
import os
import signal
import subprocess
import sys
import threading

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .system import IS_WINDOWS, clean_env

ENGINE_EXE = "mmm-engine.exe" if IS_WINDOWS else "mmm-engine"
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


def _is_exe(p):
    return bool(p) and os.path.isfile(p) and os.access(p, os.X_OK)


def engine_command():
    """The command that starts the engine, or None when it is missing."""
    # 1. Packaged app: the engine sits in the "engine" folder next to the app.
    if getattr(sys, "frozen", False):
        exe = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "engine", ENGINE_EXE)
        if _is_exe(exe):
            return [exe]
    env = os.environ
    # 2. Development: explicit environment variables.
    if _is_exe(env.get("MMM_ENGINE")):
        return [env["MMM_ENGINE"]]
    py, script = env.get("MMM_ENGINE_PYTHON"), env.get("MMM_ENGINE_SCRIPT")
    if py and script:
        return [py, script]
    # 3. Development: the project checkout.
    script = os.path.join(REPO_ROOT, "engine", "mmm_engine.py")
    if not getattr(sys, "frozen", False) and os.path.isfile(script):
        venv = os.path.join(REPO_ROOT, ".venv", "Scripts", "python.exe") if IS_WINDOWS \
            else os.path.join(REPO_ROOT, ".venv", "bin", "python")
        for candidate in (venv, sys.executable):
            if _is_exe(candidate):
                return [candidate, script]
    return None


class EngineMissing(Exception):
    pass


class EngineTask(QObject):
    """One run of the engine. `event` delivers each JSON event and
    `finished(returncode, stderr_tail)` comes last, both on the main thread."""

    event = Signal(dict)
    # object, not int: Windows crash codes (0xC0000005, ...) don't fit a C int
    finished = Signal(object, str)
    _relay = Signal(object)

    def __init__(self, arguments, parent=None):
        super().__init__(parent)
        cmd = engine_command()
        if cmd is None:
            raise EngineMissing()
        self.args = cmd + list(arguments)
        self.proc = None
        self.cancelled = False
        self.returncode = None
        self._stderr = bytearray()
        self._lock = threading.Lock()
        # events come from reader threads; this queued connection hands them
        # to the main thread in order
        self._relay.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        env = clean_env({"PYTHONUNBUFFERED": "1"})
        env.setdefault("OMP_NUM_THREADS", "4")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, close_fds=True)
        if IS_WINDOWS:
            kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: no console window pops up
        self.proc = subprocess.Popen(self.args, **kw)
        out = threading.Thread(target=self._read_stdout, daemon=True)
        err = threading.Thread(target=self._read_stderr, daemon=True)
        out.start()
        err.start()
        threading.Thread(target=self._wait, args=(out, err), daemon=True).start()

    def _read_stdout(self):
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if isinstance(obj, dict):
                self._relay.emit(("event", obj))
        self.proc.stdout.close()

    def _read_stderr(self):
        for chunk in iter(lambda: self.proc.stderr.read(4096), b""):
            with self._lock:
                self._stderr += chunk
                if len(self._stderr) > 64_000:
                    del self._stderr[:-32_000]
        self.proc.stderr.close()

    def _wait(self, out, err):
        out.join()
        err.join()
        code = self.proc.wait()
        with self._lock:
            tail = bytes(self._stderr).decode("utf-8", "replace")
        self._relay.emit(("exit", code, tail))

    def _deliver(self, item):
        if item[0] == "event":
            self.event.emit(item[1])
        else:
            self.returncode = item[1]
            self.finished.emit(item[1], item[2])

    def cancel(self):
        self.cancelled = True
        if not self.running:
            return
        if IS_WINDOWS:
            self.proc.kill()
            return
        try:
            self.proc.send_signal(signal.SIGTERM)
        except OSError:
            return
        QTimer.singleShot(2000, self.kill)

    def kill(self):
        if self.running:
            try:
                self.proc.kill()
            except OSError:
                pass

    def wait(self, timeout):
        if self.proc is None:
            return
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            pass
