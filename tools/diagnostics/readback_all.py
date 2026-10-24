"""readback_all.py out.jsonl rows.jsonl[,rows2...] steps_dir[,dir2...] - each saved STEP read back and checked against its mesh (same checker for every version)"""
import sys, os, json, glob; sys.path.insert(0, "/home/user/Manifold-Mesh-Mix/engine")
from mmm import fidelity, stepio
from mmm.pipeline import load_and_prepare
out = sys.argv[1]
rows = {}
for f in sys.argv[2].split(","):
    for l in open(f):
        r = json.loads(l); rows[r["name"]] = r
dirs = sys.argv[3].split(",")
done = set()
if os.path.exists(out):
    done = {json.loads(l)["name"] for l in open(out)}
S = "/tmp/claude-0/-home-user-Manifold-Mesh-Mix/b7e72182-d0ef-5b7f-a819-53090ffa7b0e/scratchpad"
src = {}
for p in glob.glob("/home/user/Manifold-Mesh-Mix/tests/models/*") + glob.glob(S + "/gb/models/*") + glob.glob(S + "/sharps/orig/*"):
    src.setdefault(os.path.basename(p), p)
for name, r in rows.items():
    if name in done or not r.get("tol") or name not in src: continue
    stem = os.path.splitext(name)[0]
    steps = [p for d in dirs for p in glob.glob(os.path.join(d, stem + ".*.step")) if not p.endswith(".valid.step")]
    if not steps: continue
    step = max(steps, key=os.path.getmtime)
    try:
        b, _ = load_and_prepare(src[name]); shape = stepio.read_step(step)
        c = fidelity.compare(shape, [(s.V, s.F) for body in b for s in body], r["tol"])
        row = {"name": name, "step": os.path.basename(step), "local_max": c.get("local_max"), "worst": c.get("worst_distance"), "within": c.get("within"), "tol": r["tol"]}
    except Exception as e:
        row = {"name": name, "error": str(e)}
    open(out, "a").write(json.dumps(row) + "\n"); print(row, flush=True)
