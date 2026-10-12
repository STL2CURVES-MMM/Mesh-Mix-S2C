"""Things that work differently on Windows and Linux: showing files in the
file manager, opening files, checking whether a process is alive, and the
environment for programs the app starts."""
import os
import subprocess
import sys

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = not IS_WINDOWS and not IS_MAC
OS_NAME = "Windows" if IS_WINDOWS else "macOS" if IS_MAC else "Linux"
FROZEN = bool(getattr(sys, "frozen", False))

# "Show in Finder" on the Mac
REVEAL_LABEL = "Show in Explorer" if IS_WINDOWS else "Show in Folder"
FILE_MANAGER = "Explorer" if IS_WINDOWS else "the file manager"

# Keyboard shortcut text for tooltips
MOD = "Ctrl"


def clean_env(extra=None):
    """Environment for other programs started by the app.

    A packaged app points the library search path at its own bundled
    libraries; other programs (a file manager, the converter, the
    installer) must get the user's original environment back, or they may
    load the wrong libraries."""
    env = dict(os.environ)
    if FROZEN:
        for var in ("LD_LIBRARY_PATH",):
            orig = env.pop(var + "_ORIG", None)
            if orig is not None:
                env[var] = orig
            else:
                env.pop(var, None)
        for var in ("QT_PLUGIN_PATH", "QML2_IMPORT_PATH", "QML_IMPORT_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                    "QTWEBENGINEPROCESS_PATH"):
            env.pop(var, None)
        for var in list(env):
            if var.startswith("_PYI_") or var.startswith("_MEI"):
                env.pop(var, None)
        # another frozen program started from this one must not reuse our
        # bootloader state
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    if extra:
        env.update(extra)
    return env


def popen_detached(args, cwd=None, env=None):
    """Starts a program that keeps running on its own (it isn't tied to the
    app and doesn't show a console window)."""
    kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
              cwd=cwd, env=env if env is not None else clean_env(), close_fds=True)
    if IS_WINDOWS:
        kw["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED | NEW_GROUP | NO_WINDOW
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(args, **kw)


def pid_alive(pid):
    """True while a process with this id is running."""
    if pid <= 0:
        return False
    if IS_WINDOWS:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = wintypes.HANDLE
        k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.CloseHandle.argtypes = [wintypes.HANDLE]
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return ctypes.get_last_error() == 5  # exists, but belongs to someone else
        try:
            code = wintypes.DWORD()
            if not k32.GetExitCodeProcess(h, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            k32.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def is_hidden(path):
    name = os.path.basename(path)
    if name.startswith("."):
        return True
    if IS_WINDOWS:
        try:
            return bool(os.stat(path).st_file_attributes & 0x2)  # FILE_ATTRIBUTE_HIDDEN
        except (OSError, AttributeError):
            return False
    return False


def _windows_reveal(paths):
    import ctypes
    from ctypes import wintypes

    shell32 = ctypes.WinDLL("shell32")
    ole32 = ctypes.WinDLL("ole32")
    shell32.ILCreateFromPathW.restype = ctypes.c_void_p
    shell32.ILCreateFromPathW.argtypes = [wintypes.LPCWSTR]
    shell32.ILFree.argtypes = [ctypes.c_void_p]
    shell32.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, wintypes.UINT,
                                                   ctypes.POINTER(ctypes.c_void_p), wintypes.DWORD]
    shell32.SHOpenFolderAndSelectItems.restype = ctypes.c_long
    ole32.CoInitializeEx(None, 2)
    by_folder = {}
    for p in paths:
        by_folder.setdefault(os.path.dirname(p), []).append(p)
    ok = True
    for folder, files in by_folder.items():
        fpidl = shell32.ILCreateFromPathW(folder)
        items = [shell32.ILCreateFromPathW(f) for f in files]
        items = [i for i in items if i]
        try:
            if not fpidl or not items:
                ok = False
                continue
            arr = (ctypes.c_void_p * len(items))(*items)
            if shell32.SHOpenFolderAndSelectItems(fpidl, len(items), arr, 0) != 0:
                ok = False
        finally:
            for i in items:
                shell32.ILFree(i)
            if fpidl:
                shell32.ILFree(fpidl)
    return ok


def _linux_reveal(paths):
    from PySide6.QtCore import QUrl

    try:
        from PySide6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage

        bus = QDBusConnection.sessionBus()
        if bus.isConnected():
            iface = QDBusInterface("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1",
                                   "org.freedesktop.FileManager1", bus)
            if iface.isValid():
                uris = [QUrl.fromLocalFile(p).toString() for p in paths]
                reply = iface.call("ShowItems", uris, "")
                if reply.type() != QDBusMessage.MessageType.ErrorMessage:
                    return True
    except Exception:
        pass
    folders = []
    for p in paths:
        d = os.path.dirname(p)
        if d not in folders:
            folders.append(d)
    return all(open_path(d) for d in folders)


def reveal(paths):
    """Opens the file manager with these files selected."""
    paths = [os.path.abspath(p) for p in paths if p and os.path.exists(p)]
    if not paths:
        return False
    try:
        if IS_WINDOWS:
            if _windows_reveal(paths):
                return True
            popen_detached(["explorer", "/select,", paths[0]])
            return True
        if IS_MAC:
            subprocess.run(["open", "-R", *paths], check=False)
            return True
        return _linux_reveal(paths)
    except Exception:
        return False


def open_path(path):
    """Opens a file or folder with its default program."""
    try:
        if IS_WINDOWS:
            os.startfile(path)  # noqa: S606 - the user's own file
            return True
        if IS_MAC:
            subprocess.run(["open", path], check=False)
            return True
        popen_detached(["xdg-open", path])
        return True
    except Exception:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        return QDesktopServices.openUrl(QUrl.fromLocalFile(path))


def open_url(url):
    try:
        if IS_WINDOWS:
            os.startfile(url)  # noqa: S606
            return True
        if IS_LINUX:
            popen_detached(["xdg-open", url])
            return True
    except Exception:
        pass
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    return QDesktopServices.openUrl(QUrl(url))


def resource(name):
    """Path of a file in mmm_app/resources (also inside the packaged app)."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources", name)


def bundled_doc(name):
    """LICENSE / THIRD_PARTY_NOTICES.md: next to the program when packaged,
    the repository root when running from source."""
    here = os.path.dirname(os.path.abspath(__file__))
    for d in (os.path.dirname(sys.executable) if FROZEN else None,
              os.path.join(here, "..", ".."), here):
        if d:
            p = os.path.abspath(os.path.join(d, name))
            if os.path.exists(p):
                return p
    return None
