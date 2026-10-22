"""bad_spots.py mesh shape.step tol [k] - every spot where the saved STEP is over k (1.5) x the allowed gap from the mesh, either way, grouped into clusters: centre, size, largest gap, and the surface type there"""
import sys; sys.path.insert(0, "/home/user/Manifold-Mesh-Mix/engine")
import numpy as np
from scipy.spatial import cKDTree
from mmm import fidelity, stepio
from mmm.pipeline import load_and_prepare
from OCP.TopAbs import TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
b, _ = load_and_prepare(sys.argv[1]); shape = stepio.read_step(sys.argv[2]); tol = float(sys.argv[3])
k = float(sys.argv[4]) if len(sys.argv) > 4 else 1.5
r = fidelity.compare(shape, [(s.V, s.F) for body in b for s in body], tol, fine=True)
allow = r["allow"]; p = r["points"]
print("allow %.4f kind %s max mesh->shape %.4f shape->mesh %.4f within2 %.6f" % (allow, r["mesh_kind"], r["to_shape"]["max"], r["to_mesh"]["max"], r["within2"]))
SV, SF, owner = fidelity.tessellate(shape, tol * 0.125)
faces = []
e = TopExp_Explorer(shape, TopAbs_FACE)
while e.More(): faces.append(TopoDS.Face(e.Current())); e.Next()
ctree = cKDTree(SV[SF].mean(1))
for name, P, d in (("mesh->shape", p["mesh"], p["mesh_dist"]), ("shape->mesh", p["shape"], p["shape_dist"])):
    bad = d > k * allow
    X, D = P[bad], d[bad]
    if not len(X):
        print(name, "none"); continue
    # clusters: points within 3 allow of each other
    pairs = cKDTree(X).query_pairs(max(3 * allow, 0.5))
    lab = np.arange(len(X))
    def find(i):
        while lab[i] != i: lab[i] = lab[lab[i]]; i = lab[i]
        return i
    for i, j in pairs: lab[find(i)] = find(j)
    roots = np.array([find(i) for i in range(len(X))])
    out = []
    for r0 in np.unique(roots):
        m = roots == r0; c = X[m]; j = int(np.argmax(D[m]))
        fi = owner[ctree.query(c[j])[1]]
        out.append((D[m].max(), m.sum(), c[j], np.ptp(c, 0).max(), fi, str(BRepAdaptor_Surface(faces[fi]).GetType()).split("_")[-1]))
    out.sort(key=lambda t: -t[0])
    print(name, len(out), "clusters")
    for dm, n, w, size, fi, typ in out[:12]:
        print("  max %.3f (%.1fx)  pts %4d  at %s  size %.2f  face %d %s" % (dm, dm / allow, n, np.round(w, 2), size, fi, typ))
