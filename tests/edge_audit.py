"""Find edges with excessive tolerance after a CAD build and explain them."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import numpy as np
from mmm.meshio import load_and_prepare
from mmm.cad import build_cad_shell
from mmm.report import Reporter
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.TopLoc import TopLoc_Location

path = sys.argv[1]
b, info = load_and_prepare(path)
tol = 5e-4 * info.diag
for shell in [s for bb in b for s in bb]:
    asm, faces, regions = build_cad_shell(shell, tol, Reporter(quiet=True))
    rg = asm.rg
    for ch in rg.chains:
        for e in (ch.edges or []):
            t = BRep_Tool.Tolerance_s(e)
            if t > 10 * tol:
                L = regions[ch.left]; R = regions[ch.right] if ch.right >= 0 else None
                c = BRepAdaptor_Curve(e)
                S = np.array([[c.Value(x).X(), c.Value(x).Y(), c.Value(x).Z()] for x in np.linspace(c.FirstParameter(), c.LastParameter(), 25)])
                msg = []
                for reg in (L, R):
                    if reg is None: continue
                    if reg.kind == "freeform":
                        u, v = reg.prim.uv(S)
                        tu, tv = reg.prim.tu, reg.prim.tv
                        inside = (u >= tu[0]) & (u <= tu[-1]) & (v >= tv[0]) & (v <= tv[-1])
                        msg.append(f"{reg.prim.describe()} dist={reg.prim.distance(S).max():.3g} inside_domain={inside.mean():.2f}")
                    else:
                        msg.append(f"{reg.prim.describe()} dist={reg.prim.distance(S).max():.3g}")
                P = asm.V[ch.verts]
                print(f"chain {ch.id} n={len(ch.verts)} closed={ch.closed} tol={t:.3g} curve-poly={np.abs(0):.1f} | " + " | ".join(msg))
