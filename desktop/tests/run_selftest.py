"""Runs a built app's scripted self-test and checks the result (CI helper).

    python run_selftest.py APP_PROGRAM OUTPUT_DIR [--files a.stl b.3mf ...] [--big big.stl]
                           [--unicode-temp] [--convert-only] [--update-url URL --expect-update STATE]

APP_PROGRAM is the Windows/Linux app (ManifoldMeshMix.exe, the AppImage)
or the Mac app's executable (…/Manifold Mesh Mix.app/Contents/MacOS/ManifoldMeshMix).
--unicode-temp points the temporary folder at a path with accents, so the
engine writes its files through a non-ASCII path (common user names in
Brazil have them).
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("app")
    ap.add_argument("out")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--big", default=None)
    ap.add_argument("--unicode-temp", action="store_true")
    ap.add_argument("--convert-only", action="store_true")
    ap.add_argument("--update-url", default=None)
    ap.add_argument("--expect-update", default=None)
    ap.add_argument("--timeout", type=int, default=3600)
    args = ap.parse_args()
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    env = dict(os.environ, MMM_SELFTEST=out, MMM_SELFTEST_FILES=os.pathsep.join(os.path.abspath(f) for f in args.files),
               APPIMAGE_EXTRACT_AND_RUN="1")
    if args.big:
        env["MMM_SELFTEST_BIG"] = os.path.abspath(args.big)
    if args.convert_only:
        env["MMM_SELFTEST_CONVERT_ONLY"] = "1"
    if args.update_url:
        env["MMM_UPDATE_URL"] = args.update_url
    if args.unicode_temp:
        tmp = os.path.join(os.path.dirname(out), "temporário Pasta Ção")
        os.makedirs(tmp, exist_ok=True)
        for k in ("TMP", "TEMP", "TMPDIR"):
            env[k] = tmp
        print("temporary folder:", tmp)
    t0 = time.time()
    print("running", args.app, flush=True)
    r = subprocess.run([args.app], env=env, timeout=args.timeout)
    print(f"the app exited with {r.returncode} after {time.time() - t0:.0f} s", flush=True)
    cmd = [sys.executable, os.path.join(HERE, "check_selftest.py"), out]
    if args.convert_only:
        cmd.append("--convert-only")
    if args.expect_update:
        cmd += ["--expect-update", args.expect_update]
    c = subprocess.run(cmd)
    if r.returncode != 0:
        print(f"FAIL: the app exited with code {r.returncode}")
        sys.exit(1)
    sys.exit(c.returncode)


if __name__ == "__main__":
    main()
