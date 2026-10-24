"""wire_cross.py face.brep - sample each edge's pcurve, report where the 2D outline crosses itself"""
import sys
import numpy as np
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.TopAbs import TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.BRepAdaptor import BRepAdaptor_Surface
s = TopoDS_Shape(); BRepTools.Read_s(s, sys.argv[1], BRep_Builder()); f = TopoDS.Face(s)
su = BRepAdaptor_Surface(f)
print("surface u %.3f..%.3f v %.3f..%.3f" % (su.FirstUParameter(), su.LastUParameter(), su.FirstVParameter(), su.LastVParameter()))
w = TopoDS.Wire(TopExp_Explorer(f, TopAbs_WIRE).Current())
ex = BRepTools_WireExplorer(w, f); polys = []
while ex.More():
    e = ex.Current(); a, b = BRep_Tool.Range_s(e, f); c = BRep_Tool.CurveOnSurface_s(e, f, 0.0, 0.0)
    t = np.linspace(a, b, 200)
    P = np.array([[c.Value(x).X(), c.Value(x).Y()] for x in t])
    if e.Orientation().name.endswith("REVERSED"): P = P[::-1]
    a3, b3 = BRep_Tool.Range_s(e); c3 = BRep_Tool.Curve_s(e, 0.0, 0.0)
    print(f"edge {len(polys)}: {e.Orientation().name} uv {np.round(P[0],3)} -> {np.round(P[-1],3)}  len3d {np.linalg.norm(np.diff(np.array([[*c3.Value(x).Coord()] for x in np.linspace(a3,b3,50)]),axis=0),axis=1).sum():.3f}")
    polys.append(P); ex.Next()
def segx(p, q, r, s_):
    d = (q[0]-p[0])*(s_[1]-r[1]) - (q[1]-p[1])*(s_[0]-r[0])
    if abs(d) < 1e-15: return None
    t = ((r[0]-p[0])*(s_[1]-r[1]) - (r[1]-p[1])*(s_[0]-r[0])) / d
    u = ((r[0]-p[0])*(q[1]-p[1]) - (r[1]-p[1])*(q[0]-p[0])) / d
    return (t, u) if 0 < t < 1 and 0 < u < 1 else None
n = len(polys)
for i in range(n):
    for j in range(i, n):
        A, B = polys[i], polys[j]
        for k in range(len(A)-1):
            for l in range(len(B)-1):
                if i == j and abs(k-l) < 2: continue
                if (j == i+1 and k == len(A)-2 and l == 0) or (i == 0 and j == n-1 and k == 0 and l == len(B)-2): continue
                r = segx(A[k], A[k+1], B[l], B[l+1])
                if r: print(f"  cross edge{i}[{k}] x edge{j}[{l}] at {np.round(A[k],4)}"); break
            else: continue
            break
