"""bench.py engine_dir out.jsonl model...  - convert each model like the app (Auto, quality by size), one line per model"""
import json, os, subprocess, sys, time
eng, out = sys.argv[1], sys.argv[2]
PY = sys.executable
STEPS = [(50_000, 0.9), (300_000, 0.8), (1_000_000, 0.7), (3_000_000, 0.6)]
done = set()
if os.path.exists(out):
    done = {json.loads(l)["name"] for l in open(out)}
for m in sys.argv[3:]:
    name = os.path.basename(m)
    if name in done: continue
    info = subprocess.run([PY, os.path.join(eng, "mmm_engine.py"), "inspect", m, "--json"], capture_output=True, text=True)
    tris = next((json.loads(l)["triangles"] for l in info.stdout.splitlines() if '"result"' in l), 0)
    q = next((q for lim, q in STEPS if tris < lim), 0.5)
    d = os.path.join(os.path.dirname(out), "steps"); os.makedirs(d, exist_ok=True)
    stem = os.path.join(d, os.path.splitext(name)[0] + "." + os.path.basename(os.path.dirname(os.path.abspath(eng))))
    t = time.time()
    try:
        cp = subprocess.run([PY, os.path.join(eng, "mmm_engine.py"), "convert", m, "-o", stem + ".step", "--preset", "auto",
                             "--quality", str(q), "--preview", stem + ".mmmp", "--json"], capture_output=True, text=True, timeout=int(os.environ.get("BENCH_TIMEOUT", 3600)))
        evs = [json.loads(l) for l in cp.stdout.splitlines() if l.startswith("{")]; warns = [e.get("message", "") for e in evs if e.get("event") == "warning"]
        logs = [e.get("message", "") for e in evs if e.get("event") == "log"]
        res = next((e for e in evs if e.get("event") == "result"), None)
    except subprocess.TimeoutExpired:
        res, warns, logs = None, ["TIMEOUT"], []
    row = {"name": name, "q": q, "seconds": round(time.time() - t, 1)}
    if res:
        f = res.get("fidelity") or {}
        row.update(valid=res.get("valid"), faces=res.get("faces"), preset=res.get("preset"), checks=[c["code"] for c in res.get("checks", [])],
                   ok=f.get("ok"), within2=f.get("within2"), p99=f.get("p99"), max=f.get("max"), wrong=f.get("wrong_way"),
                   tol=f.get("tolerance"), alt=bool(res.get("alternative")), vol=(res.get("volume") or 0) / max(res.get("mesh_volume") or 1, 1e-9))
    else:
        row["error"] = True
    row["warnings"] = warns
    row["replaced"] = next((l for l in logs if "rebuilt as freeform" in l), "")
    with open(out, "a") as fh:
        fh.write(json.dumps(row) + "\n")
    print(name, row.get("faces"), row.get("valid"), row.get("ok"), row["seconds"], flush=True)
