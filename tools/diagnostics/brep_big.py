"""brep_big.py shape.(brep|step) - faces: index, kind, area, wire count, edge count, own bbox from the face's mesh/vertices"""
import sys
import numpy as np
from OCP.BRepTools import BRepTools
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.TopoDS import TopoDS_Shape, TopoDS
from OCP.TopAbs import TopAbs_FACE, TopAbs_VERTEX, TopAbs_EDGE, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.BRepCheck import BRepCheck_Analyzer
p = sys.argv[1]
if p.endswith(".step"):
    from OCP.STEPControl import STEPControl_Reader
    r = STEPControl_Reader(); r.ReadFile(p); r.TransferRoots(); s = r.OneShape()
else:
    s = TopoDS_Shape(); BRepTools.Read_s(s, p, BRep_Builder())
def cnt(x, k):
    e = TopExp_Explorer(x, k); n = 0
    while e.More(): n += 1; e.Next()
    return n
ex = TopExp_Explorer(s, TopAbs_FACE); i = 0
while ex.More():
    f = TopoDS.Face(ex.Current())
    V = []; e = TopExp_Explorer(f, TopAbs_VERTEX)
    while e.More():
        V.append(BRep_Tool.Pnt_s(TopoDS.Vertex(e.Current())).Coord()); e.Next()
    V = np.array(V); g = GProp_GProps(); BRepGProp.SurfaceProperties_s(f, g)
    big = np.ptp(V, 0).max() if len(V) else 0
    ok = BRepCheck_Analyzer(f).IsValid()
    if not ok or g.Mass() < 1e-3 or len(sys.argv) > 2:
        print(i, str(BRepAdaptor_Surface(f).GetType()).split("_")[-1], "area %.3f" % g.Mass(), "valid", ok, "wires", cnt(f, TopAbs_WIRE), "edges", cnt(f, TopAbs_EDGE), "verts", np.round(V.min(0), 1).tolist(), np.round(V.max(0), 1).tolist())
    i += 1; ex.Next()
print("faces", i)
