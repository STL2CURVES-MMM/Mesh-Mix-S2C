"""exact_check.py mesh shape.step tol [n] - independent check of the checker: the n worst points of the fine check, measured again with OpenCascade's exact point-to-shape distance (no tessellation of the shape), and the same for the shape side"""
import sys; sys.path.insert(0, "/home/user/Manifold-Mesh-Mix/engine")
import numpy as np
from mmm import fidelity, stepio
from mmm.pipeline import load_and_prepare
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.gp import gp_Pnt
from OCP.BRep import BRep_Builder
from OCP.TopoDS import TopoDS_Compound, TopoDS
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
b, _ = load_and_prepare(sys.argv[1]); shape = stepio.read_step(sys.argv[2]); tol = float(sys.argv[3])
n = int(sys.argv[4]) if len(sys.argv) > 4 else 15
# (to the faces, not the solid: a point inside a solid is at distance 0)
skin = TopoDS_Compound(); bb = BRep_Builder(); bb.MakeCompound(skin)
e = TopExp_Explorer(shape, TopAbs_FACE)
while e.More(): bb.Add(skin, TopoDS.Face(e.Current())); e.Next()
r = fidelity.compare(shape, [(s.V, s.F) for body in b for s in body], tol, fine=True)
p = r["points"]; d = p["mesh_dist"]
for i in np.argsort(-d)[:n]:
    x = p["mesh"][i]
    ex = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, x))).Vertex(), skin)
    print("mesh point %s  fine check %.3f  exact %.3f" % (np.round(x, 2), d[i], ex.Value()))
# shape -> mesh: the shape's sample point itself may come from a bad
# tessellation; its exact distance to the shape's faces says so
ds = p["shape_dist"]
for i in np.argsort(-ds)[:n]:
    x = p["shape"][i]
    ex = BRepExtrema_DistShapeShape(BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, x))).Vertex(), skin)
    print("shape sample %s  to mesh %.3f  sample's own distance to the faces %.3f" % (np.round(x, 2), ds[i], ex.Value()))
