"""sub_dbg.py mesh tol q - smooth patches, their own motion, and the sub-patches found in them"""
import sys; sys.path.insert(0, "engine")
import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from mmm.pipeline import load_and_prepare
from mmm import cad, sweep
b, info = load_and_prepare(sys.argv[1]); s = b[0][0]; tol = float(sys.argv[2])
cad.FIND_SWEEPS = False
seg = cad.Segmenter(s.V, s.F, s.topo, tol); seg.detail = float(sys.argv[3]); seg.run()
F, V, fn, fa, nb, lab = seg.F, seg.V, seg.fn, seg.fa, seg.nb, seg.label
nf = len(F); rows = np.repeat(np.arange(nf), 3); cols = nb.reshape(-1); m = cols >= 0
sm = m.copy(); sm[m] = (fn[rows[m]] * fn[cols[m]]).sum(1) >= np.cos(np.radians(30))
k, comp = connected_components(sparse.coo_matrix((np.ones(sm.sum()), (rows[sm], cols[sm])), shape=(nf, nf)), directed=False)
cen = V[F].mean(1)
for p in range(k):
    f = np.nonzero(comp == p)[0]
    if len(f) < 12: continue
    regs = np.unique(lab[f])
    print(f"patch {len(f)} tris {len(regs)} regions kinds {sorted(set(seg.regions[r].kind for r in regs))}; own motion {sweep.fit_motion(cen[f], fn[f], fa[f])}")
    if len(regs) < 3: continue
    for faces, mot in seg._sub_patches(f):
        print(f"   sub: {len(faces)} tris, {len(np.unique(lab[faces]))} regions, {mot.describe()} rms {mot.rms_deg:.2f} sure {mot.sureness:.2f}")
    # the raw agreement for extrusion along z
    mz = sweep.Motion("extrusion", [0, 0, 0], [0, 0, 1]); v = mz.velocity(cen[f])
    ok = np.abs((fn[f] * v).sum(1)) <= np.sin(np.radians(3))
    comps = cad._cluster_components(f[ok], nb)
    print("   along z: agreeing", ok.sum(), "pieces", [(len(c), len(np.unique(lab[np.asarray(c)]))) for c in comps][:8])
    for c in comps:
        c = np.asarray(c)
        if len(c) > 100:
            mm = sweep.fit_motion(cen[c], fn[c], fa[c], min_gap=0, max_rms=90, taper=False)
            print("      piece", len(c), "rigid fit:", mm.describe(), "rms %.3f sure %.2f" % (mm.rms_deg, mm.sureness))
print("--- swept fits of the sub-patches")
for p in range(k):
    f = np.nonzero(comp == p)[0]
    if len(np.unique(lab[f])) < 3: continue
    for faces, mot in seg._sub_patches(f):
        surf, dev = sweep.fit_swept(cen[faces], fn[faces], fa[faces], tol, X=V[np.unique(F[faces])], motion=mot)
        if surf is None:
            Q, t = mot.to_profile(V[np.unique(F[faces])]); O = sweep.order_curve(Q, 0.25 * tol)
            print(f"   {mot.kind}: no surface (ordered {None if O is None else len(O)} of {len(Q)})"); continue
        T = V[F[faces]]; M = np.concatenate([T, (T + np.roll(T, -1, axis=1)) / 2], axis=1).reshape(-1, 3)
        print(f"   {mot.kind}: dev {dev/tol:.2f} tol, closed {surf.curve.closed}, turns {mot.turns(surf.t_range[1]-surf.t_range[0]):.2f}, mid-edge {surf.distance(M).max()/tol:.2f} tol (limit {seg.tol_mid/tol:.1f})")
print("--- which corners stray (extrusion piece)")
for p in range(k):
    f = np.nonzero(comp == p)[0]
    if len(np.unique(lab[f])) < 3: continue
    faces, mot = seg._sub_patches(f)[0]
    X = V[np.unique(F[faces])]
    surf, dev = sweep.fit_swept(cen[faces], fn[faces], fa[faces], tol, X=X, motion=mot)
    d = surf.distance(X) / tol
    z = X[:, 2]
    for lo, hi in ((0, 0.01), (0.01, 9.99), (9.99, 10.01), (10.01, 20)):
        sel = (z >= lo) & (z < hi)
        if sel.any(): print(f"   z {lo}..{hi}: {sel.sum()} corners, dist p50 {np.median(d[sel]):.2f} p99 {np.percentile(d[sel], 99):.2f} max {d[sel].max():.2f} tol")
    # angle of the piece's normals to the horizontal
    ang = np.degrees(np.arcsin(np.abs(fn[faces][:, 2])))
    print("   normal tilt p50/p99/max", np.round(np.percentile(ang, [50, 99, 100]), 2))
    print("   side top height in mesh: max z of corners", z.max())
