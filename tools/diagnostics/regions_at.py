"""regions_at.py mesh tol q x,y,z ... - regions (full segmenter) with triangles within 0.6 mm of each point"""
import sys; sys.path.insert(0, "engine")
import numpy as np
from mmm.pipeline import load_and_prepare
from mmm import cad
b, info = load_and_prepare(sys.argv[1]); s = b[0][0]; tol = float(sys.argv[2])
seg = cad.Segmenter(s.V, s.F, s.topo, tol); seg.detail = float(sys.argv[3]); seg.run()
cen = seg.V[seg.F].mean(1)
for arg in sys.argv[4:]:
    p = np.array([float(v) for v in arg.split(",")])
    near = np.nonzero(np.linalg.norm(cen - p, axis=1) < 0.6)[0]
    print("at", p)
    for r in np.unique(seg.label[near]):
        reg = seg.regions[r]; f = np.asarray(reg.faces)
        print(f"   region {r} {reg.kind}{' (swept)' if hasattr(reg.prim, 'motion') else ''} weak {reg.weak} {len(f)} tris area {seg.fa[f].sum():.4f}, here {np.sum(seg.label[near]==r)}")
