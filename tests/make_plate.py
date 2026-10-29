from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakePrism
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet, BRepFilletAPI_MakeChamfer
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.StlAPI import StlAPI_Writer
from OCP.gp import gp_Ax2, gp_Pnt, gp_Dir, gp_Vec
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_Line
from OCP.TopoDS import TopoDS
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
import math
def cyl(x,y,z,r,h,d=(0,0,1)): return BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x,y,z),gp_Dir(*d)),r,h).Shape()
cut=lambda a,b: BRepAlgoAPI_Cut(a,b).Shape(); fuse=lambda a,b: BRepAlgoAPI_Fuse(a,b).Shape()
s=BRepPrimAPI_MakeBox(60,40,15).Shape()
# fillet the 4 vertical edges r=4
f=BRepFilletAPI_MakeFillet(s); ex=TopExp_Explorer(s,TopAbs_EDGE)
while ex.More():
    e=TopoDS.Edge(ex.Current()); c=BRepAdaptor_Curve(e)
    if c.GetType()==GeomAbs_Line:
        d=c.Line().Direction()
        if abs(d.Z())>0.99: f.Add(4.0,e)
    ex.Next()
s=f.Shape()
s=cut(s,cyl(10,10,-1,2.5,17))           # through hole
s=cut(s,cyl(20,10,7,1.6,9))             # blind hole depth 8
s=cut(s,cyl(40,10,-1,1.7,17)); s=cut(s,cyl(40,10,12,3,4))   # counterbore
s=cut(s,cyl(-1,30,7.5,1.5,62,(1,0,0)))  # horizontal through hole
# hex nut trap
poly=BRepBuilderAPI_MakePolygon()
for k in range(6): poly.Add(gp_Pnt(50+3.2*math.cos(k*math.pi/3),28+3.2*math.sin(k*math.pi/3),11))
poly.Close(); hexf=BRepBuilderAPI_MakeFace(poly.Wire()).Face()
s=cut(s,BRepPrimAPI_MakePrism(hexf,gp_Vec(0,0,5)).Shape())
# chamfered hole
s=cut(s,cyl(30,25,-1,2,17))
# pins on top
s=fuse(s,cyl(15,30,15,2,6)); s=fuse(s,cyl(28,33,15,1.2,4))
p=GProp_GProps(); BRepGProp.VolumeProperties_s(s,p); print("true volume",p.Mass())
from OCP.BRepTools import BRepTools
for name,ang in [("test_fine.stl",0.2),("test_coarse.stl",0.5)]:
    BRepTools.Clean_s(s); BRepMesh_IncrementalMesh(s,0.01,False,ang,True); w=StlAPI_Writer(); w.ASCIIMode=False; w.Write(s,name)
