"""tire_patches.py S - smooth patches of a tire wedge: size, regions, own motion, sub-patches, swept fit"""
import sys; sys.path.insert(0, "engine")
import numpy as np, trimesh
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from mmm import cad, sweep
from mmm.meshio import EdgeTopology
S = sys.argv[1]
m = trimesh.load(S + "/u3/tire/right-side-tire-x2.stl", process=True)
c = np.array([379, -154.0]); C = m.triangles_center
th = np.degrees(np.arctan2(C[:, 1] - c[1], C[:, 0] - c[0])); r = np.hypot(C[:, 0] - c[0], C[:, 1] - c[1])
keep = (th > 0) & (th < 40) & (r > 50)
sub = m.submesh([np.nonzero(keep)[0]], append=True)
V, F = np.asarray(sub.vertices, float), np.asarray(sub.faces, np.int64)
cad.FIND_SWEEPS = False
tol = 0.0879
seg = cad.Segmenter(V, F, EdgeTopology(V, F), tol); seg.detail = 0.8; seg.run()
F, fn, fa, nb, lab = seg.F, seg.fn, seg.fa, seg.nb, seg.label
nf = len(F); rows = np.repeat(np.arange(nf), 3); cols = nb.reshape(-1); mm = cols >= 0
sm = mm.copy(); sm[mm] = (fn[rows[mm]] * fn[cols[mm]]).sum(1) >= np.cos(np.radians(30))
k, comp = connected_components(sparse.coo_matrix((np.ones(sm.sum()), (rows[sm], cols[sm])), shape=(nf, nf)), directed=False)
cen = V[F].mean(1)
sizes = np.bincount(comp)
def gap_old(faces):
    o = 0.0
    for q in np.unique(lab[faces]):
        g = faces[lab[faces] == q]; o = max(o, float(seg._gap(seg.regions[q].prim, g).max()))
    return o
from scipy.sparse.csgraph import connected_components as cc
from mmm.fillet import fit_pipe
regs = seg.regions; nr = len(regs)
rad = np.full(nr, np.nan)
for i, reg in enumerate(regs):
    if not reg.weak and len(reg.faces) and reg.kind in ("torus", "cylinder") and not hasattr(reg.prim, "motion"):
        rad[i] = reg.prim.r
a, b = lab[rows[mm]], lab[cols[mm]]
k = (a != b) & (a >= 0) & (b >= 0); a, b = a[k], b[k]
k = np.isfinite(rad[a]) & np.isfinite(rad[b]); a, b = a[k], b[k]
same = np.abs(rad[a] - rad[b]) <= np.maximum(0.03 * np.minimum(rad[a], rad[b]), 0.5 * tol)
n, comp2 = cc(sparse.coo_matrix((np.ones(same.sum()), (a[same], b[same])), shape=(nr, nr)), directed=False)
print("cylinders/tori with r<3:", [(i, regs[i].kind, round(rad[i], 3), len(regs[i].faces)) for i in range(nr) if rad[i] < 3])
for cpt in range(n):
    members = np.nonzero(comp2 == cpt)[0]
    if len(members) < 2: continue
    print("group", [(i, round(rad[i], 3)) for i in members])
for members in ([76, 94, 97], [34, 60]):
    faces = np.concatenate([np.asarray(regs[i].faces) for i in members])
    Cs = [regs[i].prim.a + np.outer((cen[regs[i].faces] - regs[i].prim.a) @ regs[i].prim.d, regs[i].prim.d) for i in members]
    surf, dev = fit_pipe(np.vstack(Cs), V[np.unique(F[faces])], 1.0, tol)
    print(members, "pipe:", None if surf is None else f"r {surf.r:.3f} dev {dev/tol:.2f} tol closed {surf.curve.closed}")
    if surf is None: continue
    T = V[F[faces]]; M = np.concatenate([T, (T + np.roll(T, -1, axis=1)) / 2], axis=1).reshape(-1, 3)
    print("   mid-edge", surf.distance(M).max() / tol, "no_worse", seg._no_worse(surf, faces))
    # neighbours along it
    near = nb[faces]; near = np.unique(lab[near[near >= 0]])
    for l in near:
        g = np.asarray(regs[l].faces)
        if l in members or not len(g): continue
        gp = seg._gap(surf, g)
        print(f"   neighbour {l} {regs[l].kind} r={getattr(regs[l].prim,'r',None)} {len(g)} tris: gap p50 {np.median(gp)/tol:.2f} max {gp.max()/tol:.2f} tol")
