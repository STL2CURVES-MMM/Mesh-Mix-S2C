"""Linux AppImage: add the app to the desktop's applications menu and to
"Open With" for meshes (what the installer does on Windows and the app
bundle does on the Mac)."""
import os
import shutil
import subprocess

from . import system

NAME = "manifold-mesh-mix"
MIME_TYPES = ("model/stl;model/x.stl-ascii;model/x.stl-binary;application/sla;model/3mf;"
              "application/vnd.ms-3mfdocument;model/obj;application/x-tgif;application/x-ply;model/x-ply;")


def _data_home():
    return os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")


def entry_path():
    return os.path.join(_data_home(), "applications", f"{NAME}.desktop")


def icon_path():
    return os.path.join(_data_home(), "icons", "hicolor", "256x256", "apps", f"{NAME}.png")


def appimage():
    p = os.environ.get("APPIMAGE")
    return p if system.IS_LINUX and p and os.path.isfile(p) else None


def available():
    """Only for the AppImage (a packaged folder or the source code has no single file to point at)."""
    return appimage() is not None


def _quote(path):
    # Desktop Entry spec: quote arguments with spaces; escape \ " ` $
    for ch in ("\\", '"', "`", "$"):
        path = path.replace(ch, "\\" + ch)
    return f'"{path}"'


def is_installed():
    """The menu entry exists and points at this AppImage."""
    try:
        with open(entry_path(), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    a = appimage()
    return bool(a) and f"Exec={_quote(a)} %F" in text


def install():
    """Writes the menu entry and icon. Returns an error message or None."""
    a = appimage()
    if a is None:
        return "Only the AppImage version can be added to the applications menu."
    try:
        os.makedirs(os.path.dirname(entry_path()), exist_ok=True)
        os.makedirs(os.path.dirname(icon_path()), exist_ok=True)
        shutil.copyfile(system.resource("icon-256.png"), icon_path())
        text = "\n".join([
            "[Desktop Entry]",
            "Type=Application",
            "Name=Mesh Mix-S2C",
            "GenericName=Mesh to STEP Converter",
            "Comment=Convert 3D-printing meshes (STL, 3MF, OBJ, PLY) into editable STEP files",
            f"Exec={_quote(a)} %F",
            f"TryExec={a}",
            f"Icon={icon_path()}",
            "Terminal=false",
            "Categories=Graphics;3DGraphics;Engineering;",
            f"MimeType={MIME_TYPES}",
            "Keywords=STL;STEP;3MF;CAD;mesh;convert;",
            f"StartupWMClass={NAME}",
            "",
        ])
        tmp = entry_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o755)
        os.replace(tmp, entry_path())
    except OSError as e:
        return f"The menu entry couldn't be written ({e.strerror or e})."
    _refresh()
    return None


def uninstall():
    for p in (entry_path(), icon_path()):
        try:
            os.remove(p)
        except OSError:
            pass
    _refresh()


def _refresh():
    """Lets the desktop notice the change (best effort; most desktops watch the folder anyway)."""
    tool = shutil.which("update-desktop-database")
    if tool:
        try:
            subprocess.run([tool, os.path.dirname(entry_path())], env=system.clean_env(), timeout=20,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            pass
