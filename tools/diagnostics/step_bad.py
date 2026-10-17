"""step_bad.py file.step - list invalid faces, free edges, face kinds and areas"""
import sys
from collections import Counter
from OCP.STEPControl import STEPControl_Reader
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRep import BRep_Tool
r = STEPControl_Reader(); r.ReadFile(sys.argv[1]); r.TransferRoots(); s = r.OneShape()
kinds = Counter(); bad = []
ex = TopExp_Explorer(s, TopAbs_FACE); i = 0
def box(x):
    b = Bnd_Box(); BRepBndLib.Add_s(x, b); return [round(b.CornerMin().X(),1),round(b.CornerMin().Y(),1),round(b.CornerMin().Z(),1),round(b.CornerMax().X(),1),round(b.CornerMax().Y(),1),round(b.CornerMax().Z(),1)]
while ex.More():
    f = TopoDS.Face(ex.Current()); t = str(BRepAdaptor_Surface(f).GetType()).split("_")[-1]; kinds[t] += 1
    g = GProp_GProps(); BRepGProp.SurfaceProperties_s(f, g)
    if not BRepCheck_Analyzer(f).IsValid() or g.Mass() < 1e-3:
        bad.append((i, t, round(g.Mass(), 4), box(f)))
    i += 1; ex.Next()
print(kinds); print("bad faces:"); [print(" ", b) for b in bad]
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds
fb = ShapeAnalysis_FreeBounds(s)
for w in (fb.GetClosedWires(), fb.GetOpenWires()):
    e = TopExp_Explorer(w, TopAbs_EDGE)
    while e.More():
        ed = TopoDS.Edge(e.Current()); print("  free edge tol %.3f" % BRep_Tool.Tolerance_s(ed), box(ed)); e.Next()
