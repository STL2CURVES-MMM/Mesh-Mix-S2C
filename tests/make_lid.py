import math
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeCone
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet, BRepFilletAPI_MakeChamfer
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Line
from OCP.TopoDS import TopoDS
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.BRep import BRep_Tool
cut=lambda a,b: BRepAlgoAPI_Cut(a,b).Shape()
def edges(s, pred):
    out=[]; ex=TopExp_Explorer(s,TopAbs_EDGE)
    while ex.More():
        e=TopoDS.Edge(ex.Current()); c=BRepAdaptor_Curve(e)
        p0=c.Value(c.FirstParameter()); p1=c.Value(c.LastParameter())
        if pred(c,p0,p1): out.append(e)
        ex.Next()
    return out
def fillet(s,r,es):
    f=BRepFilletAPI_MakeFillet(s)
    for e in es: f.Add(r,e)
    return f.Shape()
vert=lambda c,p0,p1: c.GetType()==GeomAbs_Line and abs(p0.Z()-p1.Z())>1 and abs(p0.X()-p1.X())<1e-6 and abs(p0.Y()-p1.Y())<1e-6
s=BRepPrimAPI_MakeBox(80,50,20).Shape()
s=fillet(s,6,edges(s,vert))                                              # rounded vertical corners
pocket=BRepPrimAPI_MakeBox(gp_Pnt(10,10,8),60,30,20).Shape()
pocket=fillet(pocket,4,edges(pocket,vert))
s=cut(s,pocket)
# floor edges of pocket (z=8, inside) fillet r=1.5 (concave)
s=fillet(s,1.5,edges(s,lambda c,p0,p1: abs(p0.Z()-8)<1e-6 and abs(p1.Z()-8)<1e-6))
# top outer edges fillet r=2 (z=20, outer boundary: x or y at extremes or corner arcs with radius 6)
def top_outer(c,p0,p1):
    if abs(p0.Z()-20)>1e-6 or abs(p1.Z()-20)>1e-6: return False
    m=c.Value((c.FirstParameter()+c.LastParameter())/2)
    return m.X()<5 or m.X()>75 or m.Y()<5 or m.Y()>45
s=fillet(s,2,edges(s,top_outer))
# bottom outer edges chamfer 1
ch=BRepFilletAPI_MakeChamfer(s)
for e in edges(s,lambda c,p0,p1: abs(p0.Z())<1e-6 and abs(p1.Z())<1e-6): ch.Add(1.0,e)
s=ch.Shape()
# countersunk hole through floor, from bottom
s=cut(s,BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(40,25,-1),gp_Dir(0,0,1)),1.7,12).Shape())
s=cut(s,BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(40,25,-0.001),gp_Dir(0,0,1)),3.4,1.7,1.701).Shape())
pr=GProp_GProps(); BRepGProp.VolumeProperties_s(s,pr); print("true volume",pr.Mass())
BRepMesh_IncrementalMesh(s,0.01,False,0.25,True); w=StlAPI_Writer(); w.ASCIIMode=False; w.Write(s,"test_lid.stl")
