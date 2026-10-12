"""End-to-end test of the Windows installer and self-update (run on Windows).

    python windows_update_test.py path/to/ManifoldMeshMix-X-Windows-x64-Setup.exe

1. Installs silently for the current user and checks the files and the
   "Open with" registration.
2. Publishes a fake "version 9.9.9" release (the same installer) on a local
   web server, starts the installed app with MMM_SELFTEST_INSTALL_UPDATE and
   checks that it downloaded the installer, ran it silently and started
   again.
3. Uninstalls silently and checks that the app is gone.
"""
import csv
import functools
import hashlib
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import winreg

APP = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "Manifold Mesh Mix")
EXE = os.path.join(APP, "ManifoldMeshMix.exe")


def processes():
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ManifoldMeshMix.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True).stdout
    return [int(row[1]) for row in csv.reader(io.StringIO(out)) if len(row) > 1 and row[0].lower() == "manifoldmeshmix.exe"]


def kill_all():
    subprocess.run(["taskkill", "/F", "/IM", "ManifoldMeshMix.exe"], capture_output=True)


def main():
    setup = os.path.abspath(sys.argv[1])
    problems = []
    work = tempfile.mkdtemp(prefix="mmm-update-test-")

    # 1. install
    log = os.path.join(work, "install.log")
    r = subprocess.run([setup, "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", f"/LOG={log}"], timeout=900)
    print("installer exit code", r.returncode)
    if r.returncode != 0 or not os.path.exists(EXE):
        problems.append(f"install failed (exit {r.returncode}); log:\n" + open(log, errors="replace").read()[-3000:])
        report(problems)
    for p in ("engine\\mmm-engine.exe", "LICENSE", "THIRD_PARTY_NOTICES.md", "unins000.exe"):
        if not os.path.exists(os.path.join(APP, p)):
            problems.append(f"missing after install: {p}")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\.stl\OpenWithProgids") as k:
            winreg.QueryValueEx(k, "ManifoldMeshMix.Mesh")
        print("'Open with' is registered for .stl")
    except OSError:
        problems.append("'Open with' isn't registered for .stl files")
    engine = subprocess.run([os.path.join(APP, "engine", "mmm-engine.exe"), "version"], capture_output=True, text=True)
    print("installed engine:", engine.stdout.strip())

    # 2. self-update
    site = os.path.join(work, "site")
    os.makedirs(site)
    name = "ManifoldMeshMix-9.9.9-Windows-x64-Setup.exe"
    shutil.copy2(setup, os.path.join(site, name))
    with open(os.path.join(setup), "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    with open(os.path.join(site, "SHA256SUMS.txt"), "w") as fh:
        fh.write(f"{digest}  {name}\n")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{httpd.server_port}"
    with open(os.path.join(site, "latest"), "w") as fh:
        json.dump({"tag_name": "v9.9.9", "body": "Test release", "html_url": base,
                   "assets": [{"name": name, "browser_download_url": f"{base}/{name}", "size": os.path.getsize(setup)},
                              {"name": "SHA256SUMS.txt", "browser_download_url": f"{base}/SHA256SUMS.txt"}]}, fh)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    # the update replaces the program's library folder completely, so a file
    # put there now must be gone afterwards (file dates can't tell: Inno
    # Setup keeps each file's build date)
    marker = os.path.join(APP, "_internal", "update-test-marker.txt")
    with open(marker, "w") as fh:
        fh.write("old version")
    report_dir = os.path.join(work, "selftest")
    update_log = os.path.join(work, "update.log")
    env = dict(os.environ, MMM_SELFTEST=report_dir, MMM_SELFTEST_CONVERT_ONLY="1", MMM_SELFTEST_FILES="",
               MMM_UPDATE_URL=f"{base}/latest", MMM_SELFTEST_INSTALL_UPDATE="1", MMM_INSTALLER_LOG=update_log)
    kill_all()
    started = time.time()
    p = subprocess.Popen([EXE], env=env)
    p.wait(timeout=600)
    print("first run exited with", p.returncode, "after", round(time.time() - started), "s")
    events = {}
    try:
        with open(os.path.join(report_dir, "report.json"), encoding="utf-8") as fh:
            events = {e["event"]: e for e in json.load(fh)}
    except OSError as e:
        problems.append(f"no self-test report: {e}")
    check = events.get("update-check", {})
    print("update check:", check)
    if check.get("state") != "available" or check.get("can_install") != [True, ""]:
        problems.append(f"the update wasn't offered: {check}")
    if "update-install-started" not in events:
        problems.append("the install didn't start")
    if events.get("errors", {}).get("count"):
        problems.append(f"errors: {events['errors']}")
    # the installer runs, replaces the files and starts the app again
    relaunched = []
    end = time.time() + 300
    while time.time() < end:
        relaunched = processes()
        if relaunched and os.path.exists(update_log) and "Installation process succeeded" in open(update_log, errors="replace").read():
            break
        time.sleep(1)
    if os.path.exists(update_log):
        text = open(update_log, errors="replace").read()
        if "Installation process succeeded" not in text:
            problems.append("the update installer didn't finish:\n" + text[-3000:])
        else:
            print("the update installer finished")
    else:
        problems.append("the update installer never ran (no log)")
    if os.path.exists(marker):
        problems.append("the program files weren't replaced")
    elif not os.path.exists(os.path.join(APP, "_internal", "base_library.zip")):
        problems.append("the program files are missing after the update")
    else:
        print("the program files were replaced")
    if relaunched:
        print("the app started again after the update (pid", relaunched[0], ")")
    else:
        problems.append("the app didn't start again after the update")
    kill_all()
    httpd.shutdown()

    # 3. uninstall
    time.sleep(2)
    r = subprocess.run([os.path.join(APP, "unins000.exe"), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], timeout=600)
    end = time.time() + 60
    while os.path.exists(EXE) and time.time() < end:
        time.sleep(1)  # the uninstaller finishes in a copy of itself
    if os.path.exists(EXE):
        problems.append("the app is still there after uninstalling")
    else:
        print("uninstalled")
    report(problems)


def report(problems):
    if problems:
        print("\n".join("FAIL: " + x for x in problems))
        sys.exit(1)
    print("Windows install / update / uninstall test OK")
    sys.exit(0)


if __name__ == "__main__":
    main()
