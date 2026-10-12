"""Run every test model through the engine and summarise the results.

    python run_suite.py [--presets exact,cad,smooth] [--only substr] [--render]

Each conversion runs in its own subprocess (with a timeout) through the real
CLI in JSON mode, exactly like the app does. The STEP output is then read
back with OpenCASCADE and checked independently.
"""
import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PY = os.path.join(ROOT, ".venv", "bin", "python")
ENGINE = os.path.join(ROOT, "engine", "mmm_engine.py")
MODELS = os.path.join(HERE, "models")
OUT = os.path.join(HERE, "out")


def run_one(model, preset, timeout, extra=()):
    name = os.path.splitext(os.path.basename(model))[0]
    out = os.path.join(OUT, f"{name}.{preset}.step")
    prev = os.path.join(OUT, f"{name}.{preset}.mmmp")
    for p in (out, prev):
        if os.path.exists(p):
            os.remove(p)
    cmd = [PY, ENGINE, "convert", model, "-o", out, "--preset", preset, "--json", "--preview", prev, *extra]
    t0 = time.time()
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"name": name, "preset": preset, "status": "TIMEOUT", "seconds": timeout}
    dt = time.time() - t0
    result, error, warnings, bad_lines = None, None, [], 0
    for line in cp.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            bad_lines += 1
            continue
        if ev.get("event") == "result":
            result = ev
        elif ev.get("event") == "error":
            error = ev.get("message")
        elif ev.get("event") == "warning":
            warnings.append(ev.get("message"))
    row = {"name": name, "preset": preset, "seconds": round(dt, 1), "warnings": warnings, "bad_lines": bad_lines}
    if result is None:
        row["status"] = "ERROR"
        row["error"] = error or (cp.stderr.strip().splitlines() or ["?"])[-1]
        row["stderr"] = cp.stderr[-3000:]
        return row
    row.update({k: result.get(k) for k in ("valid", "solids", "shells", "faces", "volume", "mesh_volume", "file_size", "preset")})
    row["status"] = "OK" if result.get("valid") else "INVALID"
    mv = result.get("mesh_volume") or 0
    v = result.get("volume") or 0
    row["vol_ratio"] = (v / mv) if mv else None
    row["output"] = out
    row["preview"] = prev
    return row


def verify_step(path):
    """Read the STEP back and check validity (independent of the engine)."""
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from mmm import stepio\n"
        "s = stepio.read_step(%r)\n"
        "st = stepio.shape_stats(s)\n"
        "v = stepio.volume(s) if st['solids'] else 0.0\n"
        "print(int(stepio.is_valid(s)), st['solids'], st['faces'], v)\n"
    ) % (os.path.join(ROOT, "engine"), path)
    try:
        cp = subprocess.run([PY, "-c", code], capture_output=True, text=True, timeout=600)
        a = cp.stdout.strip().split()
        return {"reread_valid": a[0] == "1", "reread_solids": int(a[1]), "reread_faces": int(a[2]), "reread_volume": float(a[3])}
    except Exception as e:
        return {"reread_valid": False, "reread_error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--presets", default="exact,cad")
    ap.add_argument("--only", default="")
    ap.add_argument("--skip", default="huge")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--reread", action="store_true")
    ap.add_argument("--json-out", default=os.path.join(OUT, "suite.json"))
    args = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    models = sorted(os.path.join(MODELS, f) for f in os.listdir(MODELS) if f.lower().endswith(".stl"))
    if args.only:
        keys = args.only.split(",")
        models = [m for m in models if any(k in os.path.basename(m) for k in keys)]
    if args.skip:
        skips = args.skip.split(",")
        models = [m for m in models if not any(k in os.path.basename(m) for k in skips) or (args.only and any(k in os.path.basename(m) for k in args.only.split(",")))]
    rows = []
    for preset in args.presets.split(","):
        for m in models:
            row = run_one(m, preset, args.timeout)
            if args.reread and row.get("status") in ("OK", "INVALID"):
                row.update(verify_step(row["output"]))
            if args.render and row.get("preview") and os.path.exists(row["preview"]):
                png = row["preview"].replace(".mmmp", ".png")
                subprocess.run([PY, os.path.join(HERE, "render.py"), row["preview"], png, f"{row['name']} [{preset}]"], capture_output=True)
            rows.append(row)
            vr = row.get("vol_ratio")
            print(
                f"{row['status']:8s} {preset:6s} {row['name']:32s} faces={str(row.get('faces')):>7s} "
                f"solids={str(row.get('solids')):>3s} vol={'%.4f' % vr if vr else '   -  '} "
                f"t={row['seconds']:6.1f}s size={(row.get('file_size') or 0) / 1e6:7.2f}MB"
                + (f" reread={'ok' if row.get('reread_valid') else 'BAD'}" if 'reread_valid' in row else "")
                + (f" rvol={row['reread_volume'] / row['volume']:.4f}" if row.get('reread_volume') and row.get('volume') else "")
                + (f"  !! {row.get('error')}" if row.get("error") else "")
                + (f"  [{len(row['warnings'])} warn]" if row.get("warnings") else ""),
                flush=True,
            )
    with open(args.json_out, "w") as fh:
        json.dump(rows, fh, indent=1)


if __name__ == "__main__":
    main()
