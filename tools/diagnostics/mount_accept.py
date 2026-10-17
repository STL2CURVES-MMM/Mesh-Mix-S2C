"""mount_accept.py mesh tol q - each sub-patch of the big smooth patch through the sweep acceptance chain"""
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
def gap_old(faces):
    o = 0.0
    for q in np.unique(lab[faces]):
        g = faces[lab[faces] == q]; o = max(o, float(seg._gap(seg.regions[q].prim, g).max()))
    return o
f = np.nonzero(comp == np.argmax(np.bincount(comp)))[0]
for faces, mt in seg._sub_patches(f):
    print(f"sub {len(faces)} tris {len(np.unique(lab[faces]))} regions, {mt.describe()}, centre {np.round(cen[faces].mean(0),1)}")
    pa = seg._revolved_analytic(faces); print("   analytic:", None if pa is None else pa.kind)
    surf, dev = sweep.fit_swept(cen[faces], fn[faces], fa[faces], tol, X=V[np.unique(F[faces])], motion=mt)
    if surf is None: print("   no surface"); continue
    T = V[F[faces]]; M = np.concatenate([T, (T + np.roll(T, -1, axis=1)) / 2], axis=1).reshape(-1, 3)
    print(f"   dev {dev/tol:.2f} turns {surf.motion.turns(surf.t_range[1]-surf.t_range[0]):.2f} mid-edge {surf.distance(M).max()/tol:.2f} (limit {seg.tol_mid/tol:.2f}) gap new {seg._gap(surf, faces).max()/tol:.2f} old {gap_old(faces)/tol:.2f} no_worse {seg._no_worse(surf, faces)}")
