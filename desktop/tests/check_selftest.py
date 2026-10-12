"""Checks the report of a self-test run (MMM_SELFTEST) and exits non-zero
when something went wrong.

    python check_selftest.py OUTPUT_DIR [--convert-only] [--expect-update available|current]

Works for the Windows/Linux app and the Mac app (same report format).
"""
import argparse
import glob
import json
import os
import sys


def fail(problems, msg):
    problems.append(msg)
    print("FAIL:", msg)


def image_has_content(path):
    """False for an all-black / single-color screenshot (a 3D view that didn't draw)."""
    try:
        from PySide6.QtGui import QImage
    except ImportError:
        return True
    img = QImage(path)
    if img.isNull():
        return False
    seen = set()
    w, h = img.width(), img.height()
    for y in range(0, h, max(1, h // 40)):
        for x in range(0, w, max(1, w // 40)):
            seen.add(img.pixel(x, y))
            if len(seen) > 8:
                return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--convert-only", action="store_true")
    ap.add_argument("--expect-update", default=None)
    args = ap.parse_args()
    with open(os.path.join(args.dir, "report.json"), encoding="utf-8") as fh:
        report = json.load(fh)
    ev = {}
    for e in report:
        ev.setdefault(e["event"], e)
    problems = []

    errors = ev.get("errors", {}).get("errors", [])
    if errors:
        fail(problems, f"{len(errors)} Python error(s) in the app:\n" + "\n".join(errors))
    for name in ("added", "inspected", "converted", "exported", "finished"):
        if name not in ev:
            fail(problems, f"the walkthrough never reached '{name}'")
    if ev.get("inspected", {}).get("ok") is not True:
        fail(problems, "reading the files timed out")
    for j in ev.get("inspected", {}).get("jobs", []):
        if not j.get("readable", True):
            fail(problems, f"{j['file']} couldn't be read: {j.get('error')}")
    if ev.get("converted", {}).get("ok") is not True:
        fail(problems, "converting timed out")
    for j in ev.get("converted", {}).get("jobs", []):
        if j["status"] != "Done":
            fail(problems, f"{j['file']} wasn't converted: {j['status']} {j.get('error', '')}")
        elif j.get("valid") is not True or not j.get("checked"):
            fail(problems, f"{j['file']} isn't a checked valid solid (valid={j.get('valid')}, checks={j.get('checks')})")
        print(f"converted {j['file']}: {j.get('preset')} {j.get('faces')} faces in {j.get('seconds')} s")
    if ev.get("exported", {}).get("ok") is not True:
        fail(problems, "the export window didn't close")
    for j in ev.get("exported", {}).get("jobs", []):
        files = j.get("exported") or []
        if not files:
            fail(problems, f"{j['file']} wasn't exported")
        for f in files:
            if not os.path.isfile(f) or os.path.getsize(f) < 100:
                fail(problems, f"exported file missing or empty: {f}")
            else:
                with open(f, "rb") as fh:
                    if not fh.read(16).startswith(b"ISO-10303-21"):
                        fail(problems, f"exported file isn't a STEP file: {f}")
    if not args.convert_only:
        r = ev.get("export-renamed")
        if r is None:
            fail(problems, "the second export (name clash) didn't run")
        else:
            if r.get("clash_shown") is False:  # the Mac report doesn't say
                fail(problems, "the name clash question wasn't shown")
            if not r.get("files") or r["files"] == r.get("before") or not all(" 2.step" in f for f in r["files"]):
                fail(problems, f"the renamed export is wrong: {r.get('files')}")
            for f in r.get("files") or []:
                if not os.path.isfile(f):
                    fail(problems, f"renamed file missing: {f}")
        broken = [j for j in ev.get("broken", {}).get("jobs", []) if j["file"] == "broken-selftest.stl"]
        if not broken or broken[0].get("readable", False) is not False or broken[0]["status"] != "Failed" \
                or not broken[0].get("error"):
            fail(problems, f"the broken file wasn't reported as unreadable: {broken}")
        if "cancel" in ev and ev["cancel"].get("status") != "Cancelled":
            fail(problems, f"cancel didn't work: {ev['cancel']}")
        if any(j["status"] == "Done" for j in ev.get("finished", {}).get("jobs", [])):
            fail(problems, "Clear finished left converted files in the list")
    if args.expect_update:
        u = ev.get("update-check")
        if u is None or u.get("state") != args.expect_update:
            fail(problems, f"update check: expected {args.expect_update}, got {u}")
        else:
            print("update check:", u)
    shots = sorted(glob.glob(os.path.join(args.dir, "*.png")))
    if len(shots) < 5:
        fail(problems, f"only {len(shots)} screenshots")
    views = [s for s in shots if "-result-" in s and s.endswith("-3d0.png")]
    if not views:
        fail(problems, "no 3D view screenshots of results")
    for s in views:
        if not image_has_content(s):
            fail(problems, f"the 3D view is blank: {os.path.basename(s)}")
    print(f"{len(shots)} screenshots, {len(views)} result views checked")
    if problems:
        print(f"\n{len(problems)} problem(s)")
        sys.exit(1)
    print("self-test OK")


if __name__ == "__main__":
    main()
