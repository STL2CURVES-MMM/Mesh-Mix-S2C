"""border_band.py mesh tol q - for each border between two exact surfaces after segmentation: how jagged it is (border triangle edges per mm of straight border) and how many triangles along it fit both surfaces (the band the border could run through)"""
import sys; sys.path.insert(0, "/home/user/Manifold-Mesh-Mix/engine")
import numpy as np
from mmm.pipeline import load_and_prepare
from mmm import cad
b, info = load_and_prepare(sys.argv[1]); s = b[0][0]; tol = float(sys.argv[2])
seg = cad.Segmenter(s.V, s.F, s.topo, tol); seg.detail = float(sys.argv[3]); seg.run()
lab, nb, regs, V, F = seg.label, seg.nb, seg.regions, seg.V, seg.F
analytic = np.array([not r.weak and (r.kind in ("plane", "cylinder", "cone", "sphere", "torus") or hasattr(r.prim, "motion")) for r in regs])
# border triangle edges: (face, k) with neighbour in another region
pairs = {}
for k in range(3):
    o = nb[:, k]; m = (o >= 0) & (lab[np.maximum(o, 0)] != lab)
    for f in np.nonzero(m)[0]:
        a, c = lab[f], lab[o[f]]
        if a < c: pairs.setdefault((a, c), []).append((f, o[f], k))
rows = []
for (a, c), es in pairs.items():
    if not (analytic[a] and analytic[c]) or len(es) < 8: continue
    fa_ = np.array([e[0] for e in es]); fb = np.array([e[1] for e in es])
    # length of the border's triangle edges vs the spread of its points
    ks = np.array([e[2] for e in es])
    p0 = V[F[fa_, ks]]; p1 = V[F[fa_, (ks + 1) % 3]]
    L = np.linalg.norm(p1 - p0, axis=1).sum()
    P = np.vstack([p0, p1]); span = np.linalg.svd(P - P.mean(0), compute_uv=False)[0] / np.sqrt(len(P)) * np.sqrt(12)
    # triangles on both sides of the border that fit both surfaces
    t = np.unique(np.r_[fa_, fb])
    both = seg.accept(regs[a].prim, regs[a].sense, t) & seg.accept(regs[c].prim, regs[c].sense, t)
    rows.append((L / max(span, 1e-9), both.mean(), len(es), regs[a].kind, regs[c].kind, P.mean(0)))
rows.sort(key=lambda r: -r[0])
j = np.array([r[0] for r in rows]); bo = np.array([r[1] for r in rows])
print("borders between exact surfaces:", len(rows))
for lo, hi in ((0, 1.3), (1.3, 2), (2, 99)):
    m = (j >= lo) & (j < hi)
    if m.any(): print("  jaggedness %.1f-%.1f: %3d borders, triangles fitting both surfaces: median %.0f%%" % (lo, hi, m.sum(), 100 * np.median(bo[m])))
for r in rows[:8]: print("   jag %.2f  both-fit %.0f%%  %d edges  %s|%s at %s" % (r[0], 100 * r[1], r[2], r[3], r[4], np.round(r[5], 1)))
