"""edge_status.py face.brep... - ShapeAnalysis checks of each edge in its face"""
import sys
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopAbs import TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.ShapeAnalysis import ShapeAnalysis_Edge, ShapeAnalysis_Wire
for p in sys.argv[1:]:
    s = TopoDS_Shape(); BRepTools.Read_s(s, p, BRep_Builder()); f = TopoDS.Face(s)
    print(p.split("/")[-1], "valid", BRepCheck_Analyzer(f).IsValid())
    sae = ShapeAnalysis_Edge()
    wx = TopExp_Explorer(f, TopAbs_WIRE); wi = 0
    while wx.More():
        w = TopoDS.Wire(wx.Current())
        saw = ShapeAnalysis_Wire(w, f, 1e-7)
        print("  wire", wi, "closed", not saw.CheckClosed(1e-7), "gaps3d", saw.CheckGaps3d(), "gaps2d", saw.CheckGaps2d(), "selfint", saw.CheckSelfIntersection(), "small", saw.CheckSmall(1e-7), "degen", saw.CheckDegenerated(), "order", saw.CheckOrder())
        ex = BRepTools_WireExplorer(w, f); i = 0
        while ex.More():
            e = ex.Current()
            tol = BRep_Tool.Tolerance_s(e)
            dev = 0.0
            sp = sae.CheckSameParameter(e, f, dev, 23) if hasattr(sae, "CheckSameParameter") else None
            vt = sae.CheckVertexTolerance(e, f, 0.0, 0.0)
            c3p = sae.CheckCurve3dWithPCurve(e, f)
            same_flag = BRep_Tool.SameParameter_s(e), BRep_Tool.SameRange_s(e)
            if sp or vt or c3p or not all(same_flag):
                print("    edge", i, "tol %.4f" % tol, "sameparam-bad", sp, "vertex-tol-bad", vt, "curve3d-vs-pcurve-bad", c3p, "flags", same_flag)
            i += 1; ex.Next()
        wi += 1; wx.Next()
