"""tire_wedge.py - segment a 40-degree wedge of the tire tread (no sweeps), save labels for study"""
import sys, time, pickle; sys.path.insert(0, "engine")
import numpy as np, trimesh
from mmm import cad
from mmm.meshio import EdgeTopology
S = sys.argv[1]
m = trimesh.load(S + "/u3/tire/right-side-tire-x2.stl", process=True)
c = np.array([379, -154.0]); C = m.triangles_center
th = np.degrees(np.arctan2(C[:, 1] - c[1], C[:, 0] - c[0])); r = np.hypot(C[:, 0] - c[0], C[:, 1] - c[1])
keep = (th > 0) & (th < 40) & (r > 50)
sub = m.submesh([np.nonzero(keep)[0]], append=True)
V, F = np.asarray(sub.vertices, float), np.asarray(sub.faces, np.int64)
print("wedge tris", len(F))
cad.FIND_SWEEPS = sys.argv[2] == "1"
t = time.time()
seg = cad.Segmenter(V, F, EdgeTopology(V, F), 0.0879); seg.detail = 0.8; seg.run()
print("seg s", time.time() - t)
from collections import Counter
print(Counter(r.kind for r in seg.regions if len(r.faces)))
pickle.dump((V, F, seg.label, [(r.kind, r.weak, len(r.faces), getattr(r.prim, "r", None), getattr(r.prim, "d", None) if hasattr(r.prim, "d") else None) for r in seg.regions]), open(S + "/u3/tw/seg%s.pkl" % sys.argv[2], "wb"))
