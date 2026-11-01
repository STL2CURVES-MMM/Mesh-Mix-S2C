# From Manifold Mesh Mix 1.6.0 (desktop/tests/broken_meshes.py, AGPL-3.0), changed for this fork:
# the engine is imported as mmm, from engine/ (Mesh-Mix-S2C).
"""Broken test meshes for the mesh check, one per problem."""
import numpy as np
import trimesh

def tris(m):
    return np.asarray(m.vertices)[np.asarray(m.faces)].astype(float)

def box(size=(10, 10, 10), at=(0, 0, 0)):
    m = trimesh.creation.box(extents=size)
    m.apply_translation(at)
    return m

def cases():
    out = {}
    out["clean_box"] = tris(box())
    # two cubes glued: the shared face is kept on both sides with different
    # diagonals -> a zero-thickness internal wall attached along 4 edges
    a, b = box(at=(0, 0, 0)), box(at=(10, 0, 0))
    ta, tb = tris(a), tris(b)
    def on_x(t, x):
        return np.all(np.isclose(t[:, :, 0], x), axis=1)

    wb = on_x(tb, 5.0)   # b's -x face
    q = tb[wb]           # re-triangulate b's face along the other diagonal
    pts = np.unique(q.reshape(-1, 3), axis=0)  # 4 corners: x=5, y,z = +-5
    p = {(int(np.sign(v[1])), int(np.sign(v[2]))): v for v in pts}
    alt = np.array([[p[(-1, -1)], p[(1, 1)], p[(1, -1)]], [p[(-1, -1)], p[(-1, 1)], p[(1, 1)]]])
    n_old = np.cross(q[0, 1] - q[0, 0], q[0, 2] - q[0, 0])
    n_new = np.cross(alt[0, 1] - alt[0, 0], alt[0, 2] - alt[0, 0])
    if np.dot(n_old, n_new) < 0:
        alt = alt[:, ::-1]
    out["glued_internal_wall"] = np.vstack([ta, tb[~wb], alt])
    t = tris(box()); out["hole"] = t[2:]
    t = tris(box()); t[[0, 3, 5]] = t[[0, 3, 5]][:, ::-1]; out["flipped"] = t
    out["inside_out"] = tris(box())[:, ::-1]
    t = tris(box()); out["duplicates"] = np.vstack([t, t[:3]])
    t = tris(box()); bad = t[:1].copy(); bad[0, 0, 0] = np.nan; out["nan"] = np.vstack([t, bad])
    out["corner_touch"] = np.vstack([tris(box()), tris(box(at=(10, 10, 10)))])
    # two boxes meeting along an edge, with corners along it (where each
    # side of each box is a fan of its own)
    out["edge_touch"] = np.vstack([tris(box().subdivide()), tris(box(at=(10, 10, 0)).subdivide())])
    m = box(); v = np.asarray(m.vertices).copy(); k = int(np.argmax(v.sum(1))); v[k] = [5, 5, -9]
    out["self_crossing"] = v[np.asarray(m.faces)]
    out["overlap"] = np.vstack([tris(box()), tris(box(at=(5, 5, 0)))])
    out["nested"] = np.vstack([tris(box((20, 20, 20))), tris(box((4, 4, 4)))])
    # T-junction: split one triangle's edge on one side only
    m = trimesh.creation.box(extents=(10, 10, 10))
    t = tris(m)
    e0, e1 = t[0, 0], t[0, 1]
    mid = (e0 + e1) / 2
    split = np.array([[t[0, 0], mid, t[0, 2]], [mid, t[0, 1], t[0, 2]]])
    out["t_junction"] = np.vstack([split, t[1:]])
    # the +x face pulled away from the rest by a hair (a crack all round it)
    t = tris(box()); face = np.all(t[:, :, 0] > 4.9, axis=1); t[face] += [1e-4, 0, 0]
    out["tiny_crack"] = t
    out["open_sheet"] = np.array([[[0, 0, 0], [10, 0, 0], [10, 10, 0]], [[0, 0, 0], [10, 10, 0], [0, 10, 0]]], float)
    out["tiny_scale"] = tris(box((0.2, 0.2, 0.2)))
    return out
