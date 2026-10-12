# PyInstaller spec for the Manifold Mesh Mix window on Windows and Linux (onedir).
# The conversion engine is built separately (engine/mmm-engine.spec) and
# copied into an "engine" folder next to the app by desktop/build.py.
import os
import sys

here = os.path.abspath(SPECPATH)
desktop = os.path.dirname(here)
icon = os.environ.get("MMM_APP_ICON") or None

extra_binaries = []
if sys.platform == "win32":
    # Qt's software OpenGL, used when a PC has no usable graphics driver
    # (remote desktops, virtual machines)
    import PySide6

    sw = os.path.join(os.path.dirname(PySide6.__file__), "opengl32sw.dll")
    if os.path.exists(sw):
        extra_binaries.append((sw, "PySide6"))

a = Analysis(
    [os.path.join(here, "launcher.py")],
    pathex=[desktop],
    binaries=extra_binaries,
    datas=[(os.path.join(desktop, "mmm_app", "resources"), os.path.join("mmm_app", "resources"))],
    hiddenimports=["mmm_app.selftest", "certifi", "PySide6.QtDBus"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter", "matplotlib", "PIL", "scipy", "OCP", "trimesh", "manifold3d", "pymeshfix",
        "fast_simplification", "pynanoinstantmeshes", "rtree", "skimage", "IPython", "pytest", "PyInstaller",
        "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets", "PySide6.QtQuickControls2",
        "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtUiTools",
        "PySide6.QtXml", "PySide6.QtConcurrent", "PySide6.QtPrintSupport", "PySide6.QtLabsStyleKit",
        "PySide6.QtQmlFeatures", "PySide6.QtQuickTest",
    ],
    noarchive=False,
)
if sys.platform.startswith("linux"):
    # Libraries every Linux desktop has and that must come from the system:
    # bundled copies of these break graphics drivers (libstdc++, libGL),
    # fonts (fontconfig/freetype) or input. Follows the AppImage
    # "excludelist". GTK is left out too: file dialogs then use the
    # desktop's portal (or Qt's own dialog) instead of a bundled GTK.
    system_libs = (
        "libstdc++.so", "libgcc_s.so", "libGL.so", "libEGL.so", "libGLX.so", "libGLdispatch.so", "libOpenGL.so",
        "libdrm.so", "libgbm.so", "libglapi.so", "libxcb.so.", "libX11.so", "libX11-xcb.so", "libxcb-dri",
        "libasound.so", "libfontconfig.so", "libfreetype.so", "libharfbuzz.so", "libthai.so", "libfribidi.so",
        "libcom_err.so", "libexpat.so", "libgpg-error.so", "libICE.so", "libSM.so", "libusb-1.0.so", "libuuid.so",
        "libz.so", "libp11-kit.so", "libgmp.so", "libjack.so", "libpipewire", "libwayland-",
        "libgtk-3.so", "libgdk-3.so", "libatk-1.0.so", "libatk-bridge-2.0.so", "libatspi.so", "libcairo",
        "libpango", "libgdk_pixbuf", "libepoxy.so", "libpixman", "libdatrie.so", "libgraphite2.so", "libqgtk3.so",
    )
    a.binaries = [b for b in a.binaries if not os.path.basename(b[0]).startswith(system_libs)]

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ManifoldMeshMix",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    icon=icon,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="ManifoldMeshMix")
