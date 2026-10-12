"""Inspect the CAD reconstruction of one model face by face."""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import numpy as np

from OCP.BRepCheck import BRepCheck_Analyzer
from mmm.meshio import load_and_prepare
from mmm.cad import build_cad_shell, summarize
from mmm.report import Reporter

path = sys.argv[1]
tolf = float(sys.argv[2]) if len(sys.argv) > 2 else 5e-4
rep = Reporter()
bodies, info = load_and_prepare(path, report=rep)
tol = tolf * info.diag
for body in bodies:
    for shell in body:
        asm, faces, regions = build_cad_shell(shell, tol, rep)
        print("regions:", summarize(regions), "failed:", asm.failed_regions[:5])
        bad = 0
        for r, (reg, f) in enumerate(zip([x for x in regions if True], faces)):
            pass
        # faces list skips regions without loops; map back
        k = 0
        for r, reg in enumerate(regions):
            loops = asm.rg.region_loops[r]
            if not loops:
                print(f"  region {r} {reg.prim.describe()} has NO loops ({len(reg.faces)} tris)")
                continue
            if any(x[0] == r for x in asm.failed_regions):
                continue
            f = faces[k]
            k += 1
            ok = BRepCheck_Analyzer(f).IsValid()
            if not ok:
                bad += 1
                nl = len(loops)
                print(f"  BAD face {r}: {reg.prim.describe()} sense={reg.sense} loops={nl} tris={len(reg.faces)} chains={[len(l) for l in loops]}")
        print("bad faces:", bad, "of", len(faces))
