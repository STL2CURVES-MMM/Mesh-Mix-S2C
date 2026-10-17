"""edge_check.py face.brep - per edge: valid alone, 3D vs pcurve deviation, pcurve inside the surface's range"""
import sys, glob
import numpy as np
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopAbs import TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.ShapeAnalysis import ShapeAnalysis_Edge
for p in sorted(glob.glob(sys.argv[1])):
    s = TopoDS_Shape(); BRepTools.Read_s(s, p, BRep_Builder()); f = TopoDS.Face(s)
    su = BRepAdaptor_Surface(f); U0, U1, V0, V1 = su.FirstUParameter(), su.LastUParameter(), su.FirstVParameter(), su.LastVParameter()
    surf = BRep_Tool.Surface_s(f)
    print(p.split("/")[-1], "surface u %.3f..%.3f v %.3f..%.3f" % (U0, U1, V0, V1))
    w = TopoDS.Wire(TopExp_Explorer(f, TopAbs_WIRE).Current()); ex = BRepTools_WireExplorer(w, f); i = 0
    sae = ShapeAnalysis_Edge()
    while ex.More():
        e = ex.Current(); a, b = BRep_Tool.Range_s(e, f); c2 = BRep_Tool.CurveOnSurface_s(e, f, 0.0, 0.0)
        c3 = BRep_Tool.Curve_s(e, 0.0, 0.0); a3, b3 = BRep_Tool.Range_s(e)
        ts = np.linspace(a, b, 60)
        uv = np.array([[c2.Value(t).X(), c2.Value(t).Y()] for t in ts])
        P2 = np.array([[*surf.Value(u, v).Coord()] for u, v in uv])
        P3 = np.array([[*c3.Value(a3 + (t - a) / (b - a) * (b3 - a3)).Coord()] for t in ts])
        dev = np.linalg.norm(P2 - P3, axis=1).max()
        out = (uv[:, 0].min() < U0 - 1e-9) or (uv[:, 0].max() > U1 + 1e-9) or (uv[:, 1].min() < V0 - 1e-9) or (uv[:, 1].max() > V1 + 1e-9)
        print(f"  edge {i}: valid {BRepCheck_Analyzer(e).IsValid()} tol {BRep_Tool.Tolerance_s(e):.4f} 3D-vs-pcurve {dev:.4f} outside range {out} same range {abs(a-a3)<1e-9 and abs(b-b3)<1e-9}")
        ex.Next(); i += 1
