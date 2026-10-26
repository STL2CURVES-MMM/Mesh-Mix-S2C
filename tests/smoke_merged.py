"""Smoke test of the merged engine, for CI: convert two generated test parts with
`python -m stl2curves.merged` (rivals and all, as a user runs it) and have the
benchmark judge (engine/stl2curves/judge.py) grade each STEP file against its mesh.

  python tests/smoke_merged.py [OUT_DIR]

Run it from the repository root with the engine's Python (the packages in requirements.txt).
The two packages the engine is made of, stl2curves and mmm, import from engine/, so that
is where the conversions here start and what PYTHONPATH points to.

Each part must come back good by the judge (read back valid and closed, volume within
1%, 99% of points within 0.1 mm both ways) and within its face limit. The limits are
loose on purpose: this catches a broken engine, not a few faces more; face counts are
judged by the whole-set benchmark.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENGINE = HERE.parent / "engine"
sys.path.insert(0, str(ENGINE))

from stl2curves.judge import verdict  # noqa: E402

# part: (generator, most faces allowed: about twice what the merged engine gives today)
CASES = {
    "test_coarse.stl": ("make_plate.py", 60),   # holes, pins, rounded corners, coarse mesh
    "test_lid.stl": ("make_lid.py", 90),        # fillet network, tori, cones
}


def main():
    # (absolute: the conversions below start in engine/, not where this was run)
    out = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "parts" / "smoke").resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONPATH=str(ENGINE))
    for script in sorted({g for g, _ in CASES.values()}):
        subprocess.run([sys.executable, str(HERE / script)], cwd=out, check=True,
                       stdout=subprocess.DEVNULL, env=env)
    failures = 0
    for name, (_, max_faces) in CASES.items():
        mesh, step, card_path = out / name, out / (Path(name).stem + ".step"), out / (Path(name).stem + ".json")
        t = time.time()
        r = subprocess.run([sys.executable, "-m", "stl2curves.merged", str(mesh), "-o", str(step)],
                           cwd=ENGINE, env=env, capture_output=True, text=True)
        took = time.time() - t
        res = {}
        try:
            res = json.loads(r.stdout[r.stdout.index("{"):])
        except ValueError:
            pass
        subprocess.run([sys.executable, "-m", "stl2curves.judge", str(mesh), str(step), str(card_path)],
                       cwd=ENGINE, env=env, check=True)
        card = json.loads(card_path.read_text())
        good, close, sound, score = verdict(card)
        faces = res.get("faces")
        ok = r.returncode == 0 and good and faces is not None and faces <= max_faces
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:<16} {faces} faces (max {max_faces}), kept {res.get('preset')}, "
              f"good {good} (sound {sound}, close {close}, p99 {-score * 0.02:.3f} mm), {took:.0f}s")
        if not ok:
            print(r.stdout[-3000:], r.stderr[-3000:], json.dumps(card)[:2000], sep="\n")
    print("all passed" if not failures else f"{failures} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
