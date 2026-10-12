"""How much of the mesh's detail a STEP file kept.

    python measure_detail.py MESH.stl RESULT.step

Tessellates the STEP surfaces finely, then measures the distance from every
mesh vertex to them (to the local tangent plane of dense samples) (mesh -> STEP: detail that was smoothed away shows up
here) and from dense samples on the STEP surfaces back to the mesh
(STEP -> mesh: bulges and overshoot). Prints millimetres and % of the
model's size.
"""
import os
import sys

import numpy as np
from scipy.spatial import cKDTree

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "engine"))


def step_triangles(path, deflection):
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    from mmm import stepio

    shape = stepio.read_step(path)
    BRepMesh_IncrementalMesh(shape, deflection, False, 0.2, True)
    tris = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        face = TopoDS.Face(exp.Current())
        exp.Next()
        loc = TopLoc_Location()
        t = BRep_Tool.Triangulation_s(face, loc)
        if t is None:
            continue
        trsf = loc.Transformation()
        P = np.array([[p.X(), p.Y(), p.Z()] for p in (t.Node(i).Transformed(trsf) for i in range(1, t.NbNodes() + 1))])
        T = np.array([t.Triangle(i).Get() for i in range(1, t.NbTriangles() + 1)]) - 1
        if face.Orientation() == TopAbs_REVERSED:
            T = T[:, ::-1]
        tris.append(P[T])
    return np.concatenate(tris) if tris else np.zeros((0, 3, 3))


def sample(tris, n, rng):
    """n random points on the triangles (by area) and the triangles' normals."""
    c = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    a = 0.5 * np.linalg.norm(c, axis=1)
    idx = rng.choice(len(tris), n, p=a / a.sum())
    r1, r2 = rng.random(n), rng.random(n)
    s = np.sqrt(r1)
    t = tris[idx]
    P = t[:, 0] * (1 - s)[:, None] + t[:, 1] * (s * (1 - r2))[:, None] + t[:, 2] * (s * r2)[:, None]
    N = c[idx] / np.maximum(2 * a[idx], 1e-30)[:, None]
    return P, N


def distance(points, samples, normals, k=4):
    """Distance from points to a surface given as dense samples: to the
    tangent plane of the nearest few samples (removes most of the sampling
    error), never more than the plain distance to the nearest sample."""
    d, i = cKDTree(samples).query(points, k=k, workers=-1)
    plane = np.abs(np.einsum("mkd,mkd->mk", points[:, None, :] - samples[i], normals[i]))
    # a tangent plane only counts as long as its sample is close enough
    # for the surface to be flat there
    plane = np.where(d <= 2 * d[:, :1], plane, np.inf)
    return np.minimum(d[:, 0], plane.min(1))


def stats(d, diag):
    q = np.percentile(d, [50, 90, 99, 99.9])
    return (f"median {q[0]:.4f} mm, 90% {q[1]:.4f}, 99% {q[2]:.4f}, 99.9% {q[3]:.4f}, max {d.max():.4f} mm"
            f"  (99% = {100 * q[2] / diag:.3f}% of size)")


def measure(mesh_path, step_path, n_samples=4_000_000):
    import trimesh

    m = trimesh.load(mesh_path, force="mesh", process=False)
    V = np.asarray(m.vertices, dtype=np.float64)
    tris_mesh = np.asarray(m.triangles)
    diag = float(np.linalg.norm(V.max(0) - V.min(0)))
    rng = np.random.default_rng(0)
    tris = step_triangles(step_path, diag * 5e-5)
    S, SN = sample(tris, n_samples, rng)
    # mesh -> STEP
    d1 = distance(V, S, SN)
    del S, SN
    # STEP -> mesh (against dense samples of the mesh)
    M, MN = sample(tris_mesh, n_samples, rng)
    d2 = distance(sample(tris, 1_000_000, rng)[0], M, MN)
    # sampling spacing (the error floor of this estimate)
    spacing = np.sqrt(np.sum(0.5 * np.linalg.norm(np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]), axis=1)) / n_samples)
    return {"diag": diag, "mesh_to_step": d1, "step_to_mesh": d2, "spacing": spacing, "step_tris": len(tris)}


def main():
    r = measure(sys.argv[1], sys.argv[2])
    print(f"model size {r['diag']:.1f} mm, STEP tessellated to {r['step_tris']} triangles, "
          f"sample spacing ~{r['spacing']:.4f} mm")
    print("mesh -> STEP (lost detail):  " + stats(r["mesh_to_step"], r["diag"]))
    print("STEP -> mesh (overshoot):    " + stats(r["step_to_mesh"], r["diag"]))


if __name__ == "__main__":
    main()
