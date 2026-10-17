"""checker_worst.py mesh.stl shape.step tol - the engine's own checker on a saved STEP: its worst point and that face's type"""
import sys; sys.path.insert(0, "/home/user/Manifold-Mesh-Mix/engine")
import numpy as np
from mmm import fidelity, stepio
from mmm.pipeline import load_and_prepare
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
b, _ = load_and_prepare(sys.argv[1]); shape = stepio.read_step(sys.argv[2]); tol = float(sys.argv[3])
meshes = [(s.V, s.F) for body in b for s in body]
r = fidelity.compare(shape, meshes, tol)
print({k: r[k] for k in r if k in ("max", "local_max", "allow")}, "worst", r.get("worst"))
w = np.asarray(r["worst"]) if r.get("worst") is not None else None
SV, SF, owner = fidelity.tessellate(shape, tol * 0.25)
faces = []
e = TopExp_Explorer(shape, TopAbs_FACE)
while e.More(): faces.append(TopoDS.Face(e.Current())); e.Next()
if w is not None:
    c = SV[SF].mean(1); k = int(np.argmin(np.linalg.norm(c - w, axis=1)))
    f = faces[owner[k]]
    print("worst point's face", owner[k], str(BRepAdaptor_Surface(f).GetType()).split("_")[-1], "nearest shape triangle", np.round(c[k], 2), "dist", np.linalg.norm(c[k] - w))
