"""zigzag.py shape.step [shape.step ...] - saw-tooth edges: for each edge, how often its direction swings back and forth (sign changes of the turn, per mm); lists the worst and counts edges over a limit"""
import sys; sys.path.insert(0, "/home/user/Manifold-Mesh-Mix/engine")
import numpy as np
from mmm import stepio
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_UniformAbscissa
from OCP.GCPnts import GCPnts_AbscissaPoint
for path in sys.argv[1:]:
    shape = stepio.read_step(path)
    rows = []; seen = set()
    e = TopExp_Explorer(shape, TopAbs_EDGE)
    while e.More():
        ed = TopoDS.Edge(e.Current()); e.Next()
        if ed.HashCode(1 << 30) in seen if hasattr(ed, "HashCode") else False: continue
        c = BRepAdaptor_Curve(ed)
        L = GCPnts_AbscissaPoint.Length_s(c)
        if L < 0.5: continue
        n = int(min(400, max(20, L / 0.05)))
        u = np.linspace(c.FirstParameter(), c.LastParameter(), n)
        P = np.array([c.Value(x).Coord() for x in u])
        d = np.diff(P, axis=0); d /= np.maximum(np.linalg.norm(d, axis=1)[:, None], 1e-12)
        # turn direction at each point, and how often it flips
        t = np.cross(d[:-1], d[1:])
        ref = t.sum(0); ref = ref / max(np.linalg.norm(ref), 1e-12)
        s = np.sign(t @ ref) * (np.linalg.norm(t, axis=1) > 0.02)
        s = s[s != 0]
        flips = int((np.diff(s) != 0).sum()) if len(s) > 1 else 0
        rows.append((flips / L, flips, L, P[len(P) // 2]))
    rows.sort(key=lambda r: -r[0])
    bad = [r for r in rows if r[1] >= 6 and r[0] > 1.0]
    print("%s: edges %d, saw-tooth (6+ swings, >1 per mm): %d" % (path.split("/")[-1], len(rows), len(bad)))
    for r in bad[:6]: print("   %.1f swings/mm  %d swings over %.1f mm  at %s" % (r[0], r[1], r[2], np.round(r[3], 1)))
