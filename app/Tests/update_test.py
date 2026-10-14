"""End-to-end test of the Mac app's self-update (run on a Mac).

    python3 app/Tests/update_test.py "dist/Manifold Mesh Mix.app"

Copies the app into a temporary "Applications" folder, publishes a fake
"version 9.9.9" release on a local web server (the same app with a marker
file added, signed again), starts the copy in self-test mode with
MMM_SELFTEST_INSTALL_UPDATE, and checks that it downloaded the update,
replaced itself and opened again. Also tests install-update.sh's rollback.
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

MARKER = "update-test-marker.txt"


def sh(*args, **kw):
    print("+", " ".join(args))
    return subprocess.run(args, check=True, **kw)


def running(binary):
    out = subprocess.run(["pgrep", "-f", binary], capture_output=True, text=True).stdout.split()
    return [int(p) for p in out if p.strip()]


def script_rollback_test(app, work):
    """install-update.sh puts the old app back when the new one can't be copied."""
    script = os.path.join(app, "Contents", "Resources", "install-update.sh")
    dest = os.path.join(work, "rollback", "Test.app")
    os.makedirs(os.path.join(dest, "Contents", "MacOS"))
    with open(os.path.join(dest, "Contents", "MacOS", "old"), "w") as fh:
        fh.write("old")
    marker = os.path.join(work, "rollback", "failed.txt")
    # "new app" without Contents/MacOS: refused before anything is touched
    bogus = os.path.join(work, "rollback", "new", "Test.app")
    os.makedirs(bogus)
    r = subprocess.run(["/bin/sh", script, "999999", bogus, dest, marker], env=dict(os.environ, PATH="/usr/bin:/bin"))
    # `open` of a fake app fails; the script still has to leave the old app in place
    ok = r.returncode == 1 and os.path.exists(os.path.join(dest, "Contents", "MacOS", "old")) and os.path.exists(marker)
    print("install-update.sh refuses an incomplete update:", "OK" if ok else "FAILED")
    return ok


def main():
    src = os.path.abspath(sys.argv[1])
    work = tempfile.mkdtemp(prefix="mmm-update-test-")
    problems = []
    if not script_rollback_test(src, work):
        problems.append("install-update.sh didn't keep the old app")

    apps = os.path.join(work, "Applications")
    os.makedirs(apps)
    installed = os.path.join(apps, "Manifold Mesh Mix.app")
    sh("/usr/bin/ditto", src, installed)
    staging = os.path.join(work, "new")
    os.makedirs(staging)
    new_app = os.path.join(staging, "Manifold Mesh Mix.app")
    sh("/usr/bin/ditto", src, new_app)
    with open(os.path.join(new_app, "Contents", "Resources", MARKER), "w") as fh:
        fh.write("9.9.9")
    sh("codesign", "--force", "--deep", "--sign", "-", new_app)
    site = os.path.join(work, "site")
    os.makedirs(site)
    name = "ManifoldMeshMix-9.9.9-macOS-arm64.zip"
    sh("/usr/bin/ditto", "-c", "-k", "--keepParent", new_app, os.path.join(site, name))
    with open(os.path.join(site, name), "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    with open(os.path.join(site, "SHA256SUMS.txt"), "w") as fh:
        fh.write(f"{digest}  {name}\n")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=site)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    base = f"http://127.0.0.1:{httpd.server_port}"
    with open(os.path.join(site, "latest"), "w") as fh:
        json.dump({"tag_name": "v9.9.9", "body": "Test release", "html_url": base,
                   "assets": [{"name": name, "browser_download_url": f"{base}/{name}",
                               "size": os.path.getsize(os.path.join(site, name))},
                              {"name": "SHA256SUMS.txt", "browser_download_url": f"{base}/SHA256SUMS.txt"}]}, fh)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    report_dir = os.path.join(work, "selftest")
    binary = os.path.join(installed, "Contents", "MacOS", "ManifoldMeshMix")
    env = dict(os.environ, MMM_SELFTEST=report_dir, MMM_SELFTEST_CONVERT_ONLY="1", MMM_SELFTEST_FILES="",
               MMM_UPDATE_URL=f"{base}/latest", MMM_SELFTEST_INSTALL_UPDATE="1")
    print("running", binary)
    p = subprocess.run([binary], env=env, timeout=600)
    print("first run exited with", p.returncode)
    events = {}
    try:
        with open(os.path.join(report_dir, "report.json"), encoding="utf-8") as fh:
            events = {e["event"]: e for e in json.load(fh)}
    except OSError as e:
        problems.append(f"no self-test report: {e}")
    check = events.get("update-check", {})
    print("update check:", check)
    if check.get("state") != "available" or check.get("version") != "9.9.9" or check.get("can_install") != [True, ""]:
        problems.append(f"the update wasn't offered: {check}")
    if "update-install-started" not in events:
        problems.append("the install didn't start")
    new_marker = os.path.join(installed, "Contents", "Resources", MARKER)
    end = time.time() + 120
    while time.time() < end and not os.path.exists(new_marker):
        time.sleep(0.5)
    if os.path.exists(new_marker):
        print("the app was replaced with the new version")
    else:
        failed = os.path.join(tempfile.gettempdir(), "ManifoldMeshMix-update-failed.txt")
        why = open(failed).read() if os.path.exists(failed) else "no reason recorded"
        problems.append(f"the app wasn't replaced ({why})")
    pids = []
    end = time.time() + 60
    while time.time() < end and not pids:
        pids = running(binary)
        time.sleep(0.5)
    if pids:
        print("the updated app opened again (pid", pids[0], ")")
    else:
        problems.append("the updated app didn't open again")
    # the installer removes the old app after copying the new one (the
    # marker shows up during the copy), then opens the app: check after that
    leftovers = [n for n in os.listdir(apps) if n != "Manifold Mesh Mix.app"]
    if leftovers:
        problems.append(f"files left in the Applications folder: {leftovers}")
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
    print("Mac update test OK")


if __name__ == "__main__":
    main()
