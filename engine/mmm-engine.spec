# PyInstaller spec for the Manifold Mesh Mix conversion engine (onedir).
import os

from PyInstaller.utils.hooks import collect_submodules

here = os.path.abspath(SPECPATH)

hidden = (
    collect_submodules("mmm")
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
    target_arch="arm64",
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="mmm-engine")
