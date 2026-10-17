"""judged_worst.py mesh shape.brep tol - checker on a saved in-memory shape: worst point, its face, the face's tolerances, and whether the fine meshing failed for it"""
import sys; sys.path.insert(0, "/home/user/Manifold-Mesh-Mix/engine")
import numpy as np
from mmm import fidelity
from mmm.pipeline import load_and_prepare
from OCP.BRepTools import BRepTools
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX
from OCP.TopExp import TopExp_Explorer
from OCP.BRepAdaptor import BRepAdaptor_Surface
b, _ = load_and_prepare(sys.argv[1]); tol = float(sys.argv[3])
shape = TopoDS_Shape(); BRepTools.Read_s(shape, sys.argv[2], BRep_Builder())
r = fidelity.compare(shape, [(s.V, s.F) for body in b for s in body], tol)
w = np.asarray(r["worst"]); print({k: v for k, v in r.items() if not hasattr(v, "__len__")}, "worst", np.round(w, 2))
SV, SF, owner = fidelity.tessellate(shape, tol * 0.25)
faces = []
e = TopExp_Explorer(shape, TopAbs_FACE)
while e.More(): faces.append(TopoDS.Face(e.Current())); e.Next()
c = SV[SF].mean(1); i = int(np.argmin(np.linalg.norm(c - w, axis=1))); fi = owner[i]; f = faces[fi]
def tols(kind, cast):
    out = []; x = TopExp_Explorer(f, kind)
    while x.More(): out.append(BRep_Tool.Tolerance_s(cast(x.Current()))); x.Next()
    return max(out)
print("face", fi, str(BRepAdaptor_Surface(f).GetType()).split("_")[-1], "face tol", BRep_Tool.Tolerance_s(f), "edge tol max", tols(TopAbs_EDGE, TopoDS.Edge), "vertex tol max", tols(TopAbs_VERTEX, TopoDS.Vertex))
print("that face's triangles", int(np.sum(owner == fi)), "nearest triangle to the worst point", np.linalg.norm(c[i] - w))
