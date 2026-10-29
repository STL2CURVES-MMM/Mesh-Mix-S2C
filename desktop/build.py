"""Builds Mesh Mix-S2C for Windows or Linux (run it on that system).

    python desktop/build.py               app folder + installer (Windows) / AppImage (Linux)
    python desktop/build.py --no-package  only the app folder (dist-desktop/ManifoldMeshMix)
    python desktop/build.py --app-only    reuse the engine build from last time

Needs the packages in requirements.txt and desktop/requirements.txt.
Windows also needs Inno Setup 6 (ISCC.exe); Linux downloads appimagetool.
"""
import argparse
import os
import shutil
import stat
import subprocess
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DIST = os.path.join(ROOT, "dist-desktop")
APP_DIR = os.path.join(DIST, "ManifoldMeshMix")
BUILD = os.path.join(HERE, "build")
ENGINE_DIST = os.path.join(ROOT, "engine", "build", "dist", "mmm-engine")
IS_WINDOWS = sys.platform == "win32"
APPIMAGETOOL = "https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage"


def version():
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
        v = fh.read().strip()
    ns = {}
    with open(os.path.join(HERE, "mmm_app", "version.py"), encoding="utf-8") as fh:
        exec(fh.read(), ns)
    if ns["__version__"] != v:
        sys.exit(f"VERSION ({v}) and desktop/mmm_app/version.py ({ns['__version__']}) don't match")
    return v


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kw)


def make_icons():
    """app.ico (Windows) and PNGs (Linux) from the Mac app's icon."""
    from PIL import Image

    src = Image.open(os.path.join(ROOT, "app", "Resources", "AppIcon-1024.png")).convert("RGBA")
    os.makedirs(BUILD, exist_ok=True)
    ico = os.path.join(BUILD, "app.ico")
    src.save(ico, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    pngs = {}
    for size in (64, 128, 256, 512):
        p = os.path.join(BUILD, f"icon-{size}.png")
        src.resize((size, size), Image.LANCZOS).save(p)
        pngs[size] = p
    return ico, pngs


def build_engine():
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", os.path.join(ROOT, "engine", "build", "dist"),
         "--workpath", os.path.join(ROOT, "engine", "build", "work"),
         os.path.join(ROOT, "engine", "mmm-engine.spec")])


def build_app(ico):
    env = dict(os.environ)
    if IS_WINDOWS:
        env["MMM_APP_ICON"] = ico
    run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
         "--distpath", os.path.join(BUILD, "dist"), "--workpath", os.path.join(BUILD, "work"),
         os.path.join(HERE, "packaging", "mmm-app.spec")], env=env)


def assemble(ver):
    if os.path.exists(APP_DIR):
        shutil.rmtree(APP_DIR)
    shutil.copytree(os.path.join(BUILD, "dist", "ManifoldMeshMix"), APP_DIR, symlinks=True)
    shutil.copytree(ENGINE_DIST, os.path.join(APP_DIR, "engine"), symlinks=True)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md", "VERSION"):
        shutil.copy2(os.path.join(ROOT, name), APP_DIR)


def smoke_test(ver):
    exe = os.path.join(APP_DIR, "engine", "mmm-engine.exe" if IS_WINDOWS else "mmm-engine")
    out = subprocess.run([exe, "version"], capture_output=True, text=True, check=True).stdout.strip()
    print("engine version:", out)
    run([exe, "selfcheck"])
    if not IS_WINDOWS:
        out = subprocess.run([os.path.join(APP_DIR, "ManifoldMeshMix"), "--version"], capture_output=True, text=True,
                             check=True, env={**os.environ, "QT_QPA_PLATFORM": "offscreen"}).stdout.strip()
        if out != ver:
            sys.exit(f"the app says it is version {out!r}, expected {ver}")
        print("app version:", out)


def find_iscc():
    for p in (shutil.which("ISCC"), shutil.which("iscc"),
              os.path.expandvars(r"%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"),
              os.path.expandvars(r"%ProgramFiles%\Inno Setup 6\ISCC.exe"),
              os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe")):
        if p and os.path.exists(p):
            return p
    sys.exit("Inno Setup 6 (ISCC.exe) wasn't found. Install it from https://jrsoftware.org/isinfo.php")


def package_windows(ver, ico):
    iss = os.path.join(HERE, "packaging", "windows", "installer.iss")
    run([find_iscc(), f"/DAppVersion={ver}", f"/DAppNumVersion={ver.split('-')[0]}", f"/DSourceDir={APP_DIR}", f"/DOutputDir={DIST}",
         f"/DIconFile={ico}", f"/DLicenseFile={os.path.join(ROOT, 'LICENSE')}", iss])
    out = os.path.join(DIST, f"MeshMix-S2C-{ver}-Windows-x64-Setup.exe")
    print("built", out)
    return out


def appimagetool():
    tools = os.path.join(ROOT, ".tools")
    os.makedirs(tools, exist_ok=True)
    path = os.path.join(tools, "appimagetool-1.9.1-x86_64.AppImage")
    if not os.path.exists(path):
        print("downloading", APPIMAGETOOL)
        urllib.request.urlretrieve(APPIMAGETOOL, path + ".part")
        os.replace(path + ".part", path)
        os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def package_linux(ver, pngs):
    appdir = os.path.join(BUILD, "AppDir")
    if os.path.exists(appdir):
        shutil.rmtree(appdir)
    lib = os.path.join(appdir, "usr", "lib", "manifold-mesh-mix")
    shutil.copytree(APP_DIR, lib, symlinks=True)
    shutil.copy2(os.path.join(HERE, "packaging", "linux", "AppRun"), os.path.join(appdir, "AppRun"))
    os.chmod(os.path.join(appdir, "AppRun"), 0o755)
    with open(os.path.join(HERE, "packaging", "linux", "manifold-mesh-mix.desktop"), encoding="utf-8") as fh:
        desktop = fh.read().replace("@VERSION@", ver)
    for d in (appdir, os.path.join(appdir, "usr", "share", "applications")):
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "manifold-mesh-mix.desktop"), "w", encoding="utf-8") as fh:
            fh.write(desktop)
    shutil.copy2(pngs[256], os.path.join(appdir, "manifold-mesh-mix.png"))
    shutil.copy2(pngs[256], os.path.join(appdir, ".DirIcon"))
    for size, p in pngs.items():
        d = os.path.join(appdir, "usr", "share", "icons", "hicolor", f"{size}x{size}", "apps")
        os.makedirs(d, exist_ok=True)
        shutil.copy2(p, os.path.join(d, "manifold-mesh-mix.png"))
    out = os.path.join(DIST, f"MeshMix-S2C-{ver}-Linux-x86_64.AppImage")
    env = dict(os.environ, ARCH="x86_64", APPIMAGE_EXTRACT_AND_RUN="1")
    run([appimagetool(), "--no-appstream", appdir, out], env=env)
    print("built", out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-only", action="store_true", help="reuse the last engine build")
    ap.add_argument("--no-package", action="store_true", help="skip the installer / AppImage")
    args = ap.parse_args()
    ver = version()
    ico, pngs = make_icons()
    if not args.app_only or not os.path.isdir(ENGINE_DIST):
        build_engine()
    build_app(ico)
    assemble(ver)
    smoke_test(ver)
    if args.no_package:
        print("built", APP_DIR)
        return
    if IS_WINDOWS:
        package_windows(ver, ico)
    else:
        package_linux(ver, pngs)


if __name__ == "__main__":
    main()
