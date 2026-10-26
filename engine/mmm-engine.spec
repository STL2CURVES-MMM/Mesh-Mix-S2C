# PyInstaller spec for the Manifold Mesh Mix conversion engine (onedir).
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

here = os.path.abspath(SPECPATH)

# The commit this engine is built from, logged at the start of every
# conversion, so a log shows which build made a file (all releases of one
# version look alike otherwise)
try:
    import subprocess

    _sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=here, capture_output=True,
                          text=True, timeout=30).stdout.strip()
except Exception:
    _sha = ""
with open(os.path.join(here, "mmm", "_build.py"), "w", encoding="utf-8") as _f:
    _f.write(f"BUILD = {_sha!r}\n")

hidden = (
    collect_submodules("mmm")
    # the merged engine (its Studio page and VTK renderer aren't part of conversions)
    + collect_submodules("stl2curves", filter=lambda n: n not in ("stl2curves.render", "stl2curves.studio"))
    + collect_submodules("OCP")
    + [
        "scipy.sparse.csgraph._validation",
        "scipy.spatial.transform._rotation_groups",
        "pymeshfix",
        "pynanoinstantmeshes",
        "trimesh",
    ]
)

a = Analysis(
    [os.path.join(here, "mmm_engine.py")],
    pathex=[here],
    binaries=[],
    datas=[],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "PIL",
        "skimage",
        "IPython",
        "PyInstaller",
        "pytest",
        "vtkmodules.all",
        "vtkmodules.qt",
        "vtkmodules.web",
        "vtk",
        "networkx",
        "sympy",
        "pandas",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="mmm-engine",
    debug=False,
    strip=False,
    upx=False,
    console=True,
    # Apple silicon on macOS; Windows and Linux build for the machine's own architecture
    target_arch="arm64" if sys.platform == "darwin" else None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="mmm-engine")
