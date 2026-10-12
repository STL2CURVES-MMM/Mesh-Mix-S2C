"""End-to-end test of the Linux self-update.

    python appimage_update_test.py path/to/ManifoldMeshMix-X-Linux-x86_64.AppImage

Publishes a fake "version 9.9.9" release on a local web server (the same
AppImage plus a few extra bytes, so the file is different), starts the
AppImage in self-test mode with MMM_SELFTEST_INSTALL_UPDATE, and checks
that it downloaded the update, replaced itself and started again.
Needs a display (run it under xvfb-run on a headless machine).
"""
import functools
import hashlib
import http.server
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def running_updated_copies(appimage):
    """PIDs of restarted apps (they get MMM_JUST_UPDATED=1)."""
    pids = []
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/environ", "rb") as fh:
                env = fh.read().split(b"\0")
        except OSError:
            continue
        if b"MMM_JUST_UPDATED=1" in env and f"APPIMAGE={appimage}".encode() in env:
            pids.append(int(pid))
    return pids


def main():
    src = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="mmm-update-test-")
    installed = os.path.join(work, "installed", "ManifoldMeshMix.AppImage")
    os.makedirs(os.path.dirname(installed))
    shutil.copy2(src, installed)
    os.chmod(installed, 0o755)
    site = os.path.join(work, "site")
    os.makedirs(site)
    name = "ManifoldMeshMix-9.9.9-Linux-x86_64.AppImage"
    new = os.path.join(site, name)
    shutil.copy2(src, new)
    with open(new, "ab") as fh:
        fh.write(b"\0update-test\0")  # a different file that still runs
    want = sha256(new)
    with open(os.path.join(site, "SHA256SUMS.txt"), "w") as fh:
        fh.write(f"{want}  {name}\n")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{httpd.server_port}"
    with open(os.path.join(site, "latest"), "w") as fh:
        json.dump({"tag_name": "v9.9.9", "body": "Test release", "html_url": base,
                   "assets": [{"name": name, "browser_download_url": f"{base}/{name}", "size": os.path.getsize(new)},
                              {"name": "SHA256SUMS.txt", "browser_download_url": f"{base}/SHA256SUMS.txt"}]}, fh)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    report_dir = os.path.join(work, "selftest")
    env = dict(os.environ, MMM_SELFTEST=report_dir, MMM_SELFTEST_CONVERT_ONLY="1", MMM_SELFTEST_FILES="",
               MMM_UPDATE_URL=f"{base}/latest", MMM_SELFTEST_INSTALL_UPDATE="1", APPIMAGE_EXTRACT_AND_RUN="1")
    print("running", installed)
    p = subprocess.run([installed], env=env, timeout=600)
    print("first run exited with", p.returncode)
    with open(os.path.join(report_dir, "report.json"), encoding="utf-8") as fh:
        events = {e["event"]: e for e in json.load(fh)}
    problems = []
    check = events.get("update-check", {})
    print("update check:", check)
    if check.get("state") != "available" or check.get("version") != "9.9.9" or check.get("can_install") != [True, ""]:
        problems.append(f"the update wasn't offered: {check}")
    if "update-install-started" not in events:
        problems.append("the install didn't start")
    if events.get("errors", {}).get("count"):
        problems.append(f"errors: {events['errors']}")
    got = sha256(installed)
    if got != want:
        problems.append("the AppImage wasn't replaced with the new version")
    else:
        print("the AppImage was replaced with the new version")
    if not os.access(installed, os.X_OK):
        problems.append("the new AppImage isn't executable")
    leftovers = sorted(os.listdir(os.path.dirname(installed)))
    if leftovers != ["ManifoldMeshMix.AppImage"]:
        problems.append(f"temporary files left next to the AppImage: {leftovers}")
    pids = []
    end = time.time() + 60
    while time.time() < end and not pids:
        pids = running_updated_copies(installed)
        time.sleep(0.5)
    if pids:
        print("the updated app started again (pid", pids[0], ")")
    else:
        problems.append("the updated app didn't start again")
    for pid in pids:
        try:
            os.kill(pid, 15)
        except OSError:
            pass
    httpd.shutdown()
    if problems:
        print("\n".join("FAIL: " + x for x in problems))
        sys.exit(1)
    shutil.rmtree(work, ignore_errors=True)
    print("AppImage update test OK")


if __name__ == "__main__":
    main()
