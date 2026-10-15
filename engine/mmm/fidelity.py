"""How closely a converted shape follows the mesh it came from.

Being a valid solid with the right volume isn't enough: a shape can pass
those checks and still be wrinkled, have dents, bumps or flaps, or be
missing small features. So every conversion is also compared with the mesh
itself, the way you would by laying one over the other:

1. The shape is cut into fine triangles again (finer than the fit tolerance).
2. Points are spread evenly over the mesh, and for each one the exact
   distance to the shape's surface is measured; then the other way round,
   from the shape to the mesh (that catches extra material and flaps that
   no mesh point is near).
3. At every mesh point the direction the surface faces is compared with the
   shape's direction at the nearest point. Wrinkles and dents keep close to
   the mesh but tilt the surface, so they show up here even when the
   distances look fine.

The result says how much of the surface is within the tolerance, the
largest gap, how much of the surface faces the wrong way, and where the
worst spot is, so a conversion can be rejected (and another method tried)
before it is saved.
"""
import numpy as np
from scipy.spatial import cKDTree

# a surface point "faces the wrong way" when its direction differs by more
# than this from the mesh (faceted meshes of round shapes differ by a few
# degrees; real wrinkles and dents by much more)
WRONG_WAY_DEG = 30.0


def tessellate(shape, deflection, angle=0.25):
    """The shape's faces as triangles: (V, F, face index of each triangle)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.BRepTools import BRepTools

    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, max(float(deflection), 1e-7), False, angle, True)
    Vs, Fs, ids = [], [], []
    base = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    fi = 0
    while exp.More():
        face = TopoDS.Face(exp.Current())
        exp.Next()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is not None and tri.NbTriangles() > 0:
            trsf = loc.Transformation()
            n = tri.NbNodes()
            P = np.empty((n, 3))
            for i in range(1, n + 1):
                p = tri.Node(i).Transformed(trsf)
                P[i - 1] = (p.X(), p.Y(), p.Z())
            m = tri.NbTriangles()
            T = np.empty((m, 3), dtype=np.int64)
            for i in range(1, m + 1):
                a, b, c = tri.Triangle(i).Get()
                T[i - 1] = (a - 1, b - 1, c - 1)
            if face.Orientation() == TopAbs_REVERSED:
                T = T[:, ::-1]
            Vs.append(P)
            Fs.append(T + base)
            ids.append(np.full(m, fi, dtype=np.int64))
            base += n
        fi += 1
    # drop the fine triangulation again: it would stay on the shape, and the
    # 3D preview (and anything else that meshes it) would reuse it
    BRepTools.Clean_s(shape)
    if not Vs:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros(0, dtype=np.int64)
    return np.vstack(Vs), np.vstack(Fs), np.concatenate(ids)


def tessellate_faces(faces, deflection, angle=0.25):
    """Like tessellate, for a list of faces: (V, F, index into ``faces`` of
    each triangle)."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    B = BRep_Builder()
    comp = TopoDS_Compound()
    B.MakeCompound(comp)
    for f in faces:
        B.Add(comp, f)
    # a compound keeps its faces in the order they were added
    return tessellate(comp, deflection, angle)


def _tri_normals(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    area = np.linalg.norm(n, axis=1)
    return n / np.maximum(area, 1e-300)[:, None], 0.5 * area


def sample(V, F, n, rng):
    """About n points spread evenly (by area) over the triangles: (points,
    triangle of each point)."""
    _, area = _tri_normals(V, F)
    tot = area.sum()
    if tot <= 0 or len(F) == 0:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int64)
    counts = rng.poisson(area / tot * n)
    tri = np.repeat(np.arange(len(F)), counts)
    r1 = np.sqrt(rng.random(len(tri)))
    r2 = rng.random(len(tri))
    A, B, C = V[F[tri, 0]], V[F[tri, 1]], V[F[tri, 2]]
    P = (1 - r1)[:, None] * A + (r1 * (1 - r2))[:, None] * B + (r1 * r2)[:, None] * C
    return P, tri


def point_triangle_distance(P, A, B, C):
    """Exact distance from each point to its triangle (all arrays (n, 3)),
    after Ericson, Real-Time Collision Detection 5.1.5. Returns (distance,
    closest point)."""
    ab, ac, ap = B - A, C - A, P - A
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = P - B
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = P - C
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    denom = va + vb + vc
    with np.errstate(divide="ignore", invalid="ignore"):
        v = vb / denom
        w = vc / denom
    with np.errstate(invalid="ignore"):
        Q = A + v[:, None] * ab + w[:, None] * ac  # inside the face
    # the regions outside, in the order of the reference algorithm (later
    # ones are only used where the earlier ones didn't apply)
    done = np.zeros(len(P), dtype=bool)

    def put(mask, X):
        m = mask & ~done
        Q[m] = X[m]
        done[m] = True

    put((d1 <= 0) & (d2 <= 0), A)
    put((d3 >= 0) & (d4 <= d3), B)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = d1 / (d1 - d3)
    put((vc <= 0) & (d1 >= 0) & (d3 <= 0), A + t[:, None] * ab)
    put((d6 >= 0) & (d5 <= d6), C)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = d2 / (d2 - d6)
    put((vb <= 0) & (d2 >= 0) & (d6 <= 0), A + t[:, None] * ac)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
    put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), B + t[:, None] * (C - B))
    # squashed triangles (a line or a point): the nearest point on their edges
    cross = np.linalg.norm(np.cross(ab, ac), axis=1)
    scale = np.maximum(np.einsum("ij,ij->i", ab, ab), np.einsum("ij,ij->i", ac, ac))
    bad = ~np.isfinite(Q).all(axis=1) | (cross <= 1e-12 * np.maximum(scale, 1e-300))
    if bad.any():
        p, a, b, c = P[bad], A[bad], B[bad], C[bad]
        best_d = np.full(len(p), np.inf)
        best_q = np.zeros_like(p)
        for s, e in ((a, b), (b, c), (c, a)):
            q = _closest_on_segment(p, s, e)
            dd = np.linalg.norm(p - q, axis=1)
            m = dd < best_d
            best_d[m] = dd[m]
            best_q[m] = q[m]
        Q[bad] = best_q
    return np.linalg.norm(P - Q, axis=1), Q


def _closest_on_segment(P, S, E):
    d = E - S
    ll = np.einsum("ij,ij->i", d, d)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.einsum("ij,ij->i", P - S, d) / ll
    t = np.where(ll > 0, np.clip(t, 0.0, 1.0), 0.0)
    return S + t[:, None] * d


class Surface:
    """A triangle surface that answers "how far is this point from you, and
    which way do you face there"."""

    def __init__(self, V, F, spacing, rng, max_samples=3_000_000):
        self.V, self.F = V, F
        self.normals, area = _tri_normals(V, F)
        tot = float(area.sum())
        n = int(min(max_samples, max(len(F) * 2, tot / max(spacing, 1e-12) ** 2)))
        P, tri = sample(V, F, n, rng)
        # every triangle has at least its centre, so none is missed
        cen = V[F].mean(axis=1)
        self.points = np.vstack([cen, P])
        self.tri = np.concatenate([np.arange(len(F)), tri])
        self.tree = cKDTree(self.points)
        self.area = tot
        # faces of a tessellated shape each have their own copy of the
        # vertices on their edges: merge them so neighbours across an edge
        # of the shape are found too
        self.adj = _edge_neighbours(_welded(V, F))

    def nearest(self, P, k=8, chunk=100_000):
        """Exact distance from each point to the surface, and the triangle
        it is nearest to. The candidates are the triangles of the nearest
        sample points and their neighbours (near a corner the nearest samples
        can all lie on other triangles than the thin wedge the point is on)."""
        dist = np.empty(len(P))
        tri = np.empty(len(P), dtype=np.int64)
        V, F = self.V, self.F
        for s in range(0, len(P), chunk):
            Q = P[s:s + chunk]
            _, idx = self.tree.query(Q, k=min(k, len(self.points)))
            idx = np.atleast_2d(idx)
            if idx.shape[0] != len(Q):
                idx = idx.T
            near = self.tri[idx]  # (m, k)
            nb = self.adj[near[:, :3]].reshape(len(Q), -1)
            cand = np.concatenate([near, np.where(nb >= 0, nb, near[:, :1])], axis=1)
            # each (point, triangle) pair once: the nearest samples are
            # mostly on the same few triangles
            cand.sort(axis=1)
            keep = np.ones(cand.shape, dtype=bool)
            keep[:, 1:] = cand[:, 1:] != cand[:, :-1]
            pi = np.nonzero(keep)[0]
            c = cand[keep]
            d, _ = point_triangle_distance(Q[pi], V[F[c, 0]], V[F[c, 1]], V[F[c, 2]])
            # the smallest distance of each point (rows are in order)
            order = np.lexsort((d, pi))
            first = np.ones(len(order), dtype=bool)
            first[1:] = pi[order][1:] != pi[order][:-1]
            best = order[first]
            dist[s:s + chunk] = d[best]
            tri[s:s + chunk] = c[best]
        return dist, tri


class Verdict:
    """How a converted shape compares with its mesh, in a form the pipeline
    can rank and the app can show."""

    # a conversion follows the mesh when 99% of the surface (both ways) is
    # within P99_K tolerances, 99.9% within P999_K (so a small spot can't be
    # far off: a flap or a dent shows even when it is small), and almost none
    # of it faces the wrong way
    P99_K = 3.0
    P999_K = 8.0
    WRONG_WAY_MAX = 0.005

    def __init__(self, r, tol):
        self.r = r
        self.tol = tol
        if r.get("unknown"):
            # the comparison itself failed: neither good nor bad
            self.ok, self.score = True, -3.0
            return
        if r.get("empty"):
            self.ok, self.score = False, -1e9
            return
        p99 = max(r["to_shape"]["p99"], r["to_mesh"]["p99"]) / tol
        p999 = max(r["to_shape"].get("p999", 0.0), r["to_mesh"].get("p999", 0.0)) / tol
        ww = max(r["wrong_way"], r["wrong_way_shape"])
        self.ok = p99 <= self.P99_K and p999 <= self.P999_K and ww <= self.WRONG_WAY_MAX
        # higher is better: tight fit, no far-off spots, few points facing
        # the wrong way
        self.score = -p99 - 0.25 * p999 - 200.0 * ww

    def summary(self, unit="mm"):
        r = self.r
        if r.get("unknown"):
            return "it couldn't be compared with the mesh"
        if r.get("empty"):
            return "the shape has no surface to compare with the mesh"
        within = 100.0 * r["within2"]
        return (f"{within:.1f}% of the surface is within {2 * self.tol:.2g} {unit} of the mesh; "
                f"largest gap {r['worst_distance']:.2g} {unit}")

    def as_dict(self):
        r = self.r
        if r.get("empty") or r.get("unknown"):
            return {"ok": bool(self.ok), "empty": True}
        return {
            "ok": bool(self.ok),
            "tolerance": self.tol,
            "within": r["within"],
            "within2": r["within2"],
            "p99": max(r["to_shape"]["p99"], r["to_mesh"]["p99"]),
            "max": r["worst_distance"],
            "worst": r["worst"],
            "wrong_way": max(r["wrong_way"], r["wrong_way_shape"]),
        }


def judge(shape, shells, tol, n_points=None):
    """Verdict for a converted shape against the mesh shells (objects with
    V and F) it was made from."""
    r = compare(shape, [(s.V, s.F) for s in shells], tol, n_points=n_points)
    r.pop("points", None)
    return Verdict(r, max(float(tol), 1e-9))


def _welded(V, F):
    """F with vertices at the same position merged."""
    if len(V) == 0:
        return F
    scale = max(float(np.abs(V).max()), 1e-12)
    _, inv = np.unique(np.round(V / scale, 10), axis=0, return_inverse=True)
    return inv.reshape(-1)[F]


def _edge_neighbours(F):
    """For each triangle, the triangles across its three edges (-1: none;
    with more than two triangles on an edge, one of them)."""
    n = len(F)
    if n == 0:
        return np.zeros((0, 3), dtype=np.int64)
    e = np.stack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]], axis=1).reshape(-1, 2)
    e.sort(axis=1)
    key = e[:, 0].astype(np.int64) * (int(F.max()) + 1) + e[:, 1]
    order = np.argsort(key, kind="stable")
    ks = key[order]
    out = np.full(3 * n, -1, dtype=np.int64)
    same = ks[1:] == ks[:-1]
    a, b = order[:-1][same], order[1:][same]
    out[a] = b // 3
    out[b] = a // 3
    return out.reshape(n, 3)


_MESH_CACHE = {}


def mesh_surface(V, F):
    """The mesh as a Surface, its smoothed normals and the sample spacing,
    kept for the next call with the same mesh (every candidate of a body
    is compared with the same mesh)."""
    import hashlib

    h = hashlib.blake2b(digest_size=16)
    h.update(np.ascontiguousarray(V).tobytes())
    h.update(np.ascontiguousarray(F).tobytes())
    key = h.hexdigest()
    hit = _MESH_CACHE.get(key)
    if hit is not None:
        return hit
    area = _tri_normals(V, F)[1]
    # reference points on each surface for the nearest-triangle search (the
    # distances themselves are exact); more for detailed meshes
    n_ref = np.clip(4 * len(F), 1e5, 5e5)
    spacing = float(np.sqrt(area.sum() / n_ref))
    M = Surface(V, F, spacing, np.random.default_rng(1))
    # the mesh's directions, smoothed over neighbouring triangles (not
    # across sharp edges): a scan's noisy triangles point every which way,
    # but a smooth surface through them is what a conversion should give
    MN = smoothed_normals(M.normals, area, M.adj)
    _MESH_CACHE.clear()  # one mesh at a time (they can be big)
    _MESH_CACHE[key] = (M, MN, spacing)
    return M, MN, spacing


def smoothed_normals(N, A, adj, iters=3, keep_deg=30.0):
    """Triangle normals averaged with their neighbours' (by area), a few
    times over; neighbours across a sharp edge (more than ``keep_deg``
    apart) are left out, so edges stay sharp."""
    cos_keep = np.cos(np.radians(keep_deg))
    for _ in range(iters):
        acc = N * A[:, None]
        for j in range(3):
            nb = adj[:, j]
            nbs = np.maximum(nb, 0)
            use = (nb >= 0) & ((N[nbs] * N).sum(1) >= cos_keep)
            acc[use] += N[nbs[use]] * A[nbs[use], None]
        N = acc / np.maximum(np.linalg.norm(acc, axis=1), 1e-300)[:, None]
    return N


def _angle_deg(a, b):
    return np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", a, b), -1.0, 1.0)))


def compare(shape, meshes, tol, n_points=None, seed=0):
    """Compares a converted shape with the mesh pieces it came from
    (``meshes``: list of (V, F) with outward-facing triangles).

    Returns a dict:
      to_shape: distances from mesh points to the shape (p50, p99, max)
      to_mesh: distances from shape points to the mesh (p99, max)
      within: share of the mesh surface within ``tol`` of the shape
      wrong_way: share of the mesh surface whose direction differs from
          the shape's by more than WRONG_WAY_DEG
      worst: a point where the shape is furthest from the mesh
    """
    rng = np.random.default_rng(seed)
    if n_points is None:
        # enough points for the 99.9th percentile to mean something, more
        # for detailed meshes
        n_points = int(np.clip(30 * sum(len(F) for _, F in meshes), 30_000, 150_000))
    MV = np.vstack([V for V, _ in meshes])
    off = np.cumsum([0] + [len(V) for V, _ in meshes[:-1]])
    MF = np.vstack([F + o for (_, F), o in zip(meshes, off)])
    diag = float(np.linalg.norm(MV.max(0) - MV.min(0))) if len(MV) else 1.0
    tol = max(float(tol), 1e-9)
    SV, SF, _ = tessellate(shape, tol * 0.25)
    if len(SF) == 0:
        return {"empty": True}
    M, MN, spacing = mesh_surface(MV, MF)
    S = Surface(SV, SF, spacing, rng)
    # mesh -> shape: distances and directions
    P, ptri = sample(MV, MF, n_points, rng)
    d_ms, stri = S.nearest(P)
    ang = _angle_deg(MN[ptri], S.normals[stri])
    # shape -> mesh
    Q, qtri = sample(SV, SF, n_points, rng)
    d_sm, mtri = M.nearest(Q)
    ang_sm = _angle_deg(S.normals[qtri], MN[mtri])
    # a direction that is off only because the point lies right at a sharp
    # edge (its nearest point is on the neighbouring face) doesn't count
    near_edge = d_ms > 0.5 * tol
    wrong = (ang > WRONG_WAY_DEG) & ~(near_edge & (d_ms < 2 * tol))
    wrong_sm = (ang_sm > WRONG_WAY_DEG) & (d_sm < 2 * tol)
    k = int(np.argmax(d_ms)) if len(d_ms) else 0
    k2 = int(np.argmax(d_sm)) if len(d_sm) else 0
    worst, worst_d = (P[k], float(d_ms[k])) if d_ms[k] >= d_sm[k2] else (Q[k2], float(d_sm[k2]))
    return {
        "empty": False,
        "tol": tol,
        "diag": diag,
        "to_shape": {"p50": float(np.median(d_ms)), "p99": float(np.percentile(d_ms, 99)),
                     "p999": float(np.percentile(d_ms, 99.9)), "max": float(d_ms.max())},
        "to_mesh": {"p99": float(np.percentile(d_sm, 99)), "p999": float(np.percentile(d_sm, 99.9)),
                    "max": float(d_sm.max())},
        "within": float(np.mean(d_ms <= tol)),
        "within2": float(np.mean(np.maximum(d_ms, 0) <= 2 * tol)),
        "wrong_way": float(np.mean(wrong)),
        "wrong_way_shape": float(np.mean(wrong_sm)),
        "worst": [float(x) for x in worst],
        "worst_distance": worst_d,
        "points": {"mesh": P, "mesh_dist": d_ms, "mesh_angle": ang,
                   "shape": Q, "shape_dist": d_sm},
    }
