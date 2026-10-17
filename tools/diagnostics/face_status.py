import sys, glob
sys.path.insert(0, "/home/user/Manifold-Mesh-Mix/engine")
from OCP.BRepTools import BRepTools
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.BRepCheck import BRepCheck_Analyzer, BRepCheck_Face, BRepCheck_Wire, BRepCheck_Status
from OCP.TopAbs import TopAbs_WIRE, TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Edge
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.ShapeAnalysis import ShapeAnalysis_Surface
names = {v: k.replace("BRepCheck_", "") for k, v in BRepCheck_Status.__members__.items()}
for p in sorted(glob.glob(sys.argv[1])):
    s = TopoDS_Shape(); BRepTools.Read_s(s, p, BRep_Builder()); f = TopoDS.Face(s)
    bf = BRepCheck_Face(f)
    st = []
    for nm in ("IntersectWires", "ClassifyWires", "OrientationOfWires"):
        try: st.append(f"{nm}={names.get(getattr(bf, nm)(), '?')}")
        except Exception as e: st.append(f"{nm}=exc")
    try:
        lst = bf.StatusOnShape(f) if hasattr(bf, "StatusOnShape") else None
    except Exception: lst = None
    ws = []
    e = TopExp_Explorer(f, TopAbs_WIRE); nw = 0
    while e.More():
        w = TopoDS.Wire(e.Current()); nw += 1; bw = BRepCheck_Wire(w)
        for nm, args in (("Closed", ()), ("Closed2d", (f,)), ("Orientation", (f,))):
            try:
                r = names.get(getattr(bw, nm)(*args), "?")
                if r != "NoError": ws.append(f"w{nw}.{nm}={r}")
            except Exception as ex: ws.append(f"w{nw}.{nm}=exc")
        try:
            e1, e2 = TopoDS_Edge(), TopoDS_Edge()
            r = names.get(bw.SelfIntersect(f, e1, e2), "?")
            if r != "NoError": ws.append(f"w{nw}.SelfIntersect={r}")
        except Exception as ex: ws.append(f"w{nw}.SelfIntersect=exc")
        e.Next()
    ne = 0; e = TopExp_Explorer(f, TopAbs_EDGE)
    while e.More(): ne += 1; e.Next()
    print(p.split("/")[-1], "wires", nw, "edges", ne, "tol %.2g" % BRep_Tool.Tolerance_s(f), st, ws)
