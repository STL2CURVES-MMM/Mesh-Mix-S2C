"""
Freeform surfaces: big smooth areas that no cylinder, cone, sphere or torus explains (a
crown that fades out round a bend, the walls of a slot milled along a curve), each as
one B-spline surface cut to the area's outline like any other trimmed patch.

Smooth blends (blends.py) span small areas, each an N-sided patch through its outline.
A big area cut into such patches meets itself along zigzag outlines (runs of mesh
edges), and most of those patches won't fit. Here the whole area gets one surface: a
height field over a simple base (a plane square to the area's mean direction, or for an
area curling further round than that, a cylinder round the axis its normals turn
about), fitted to the mesh corners by penalised least squares (P-splines: Eilers and
Marx, "Flexible smoothing with B-splines and penalties", Statistical Science 1996). The
knots are spaced evenly and refined only until the surface passes within FIT_DEV of
every corner, so a gently curved area gets a light surface (few control points), which
is easier to edit. A CAD export's corners lie on the true surface, so a smooth area fits
to a few ten-thousandths of a millimetre, and an area with a crease in it doesn't fit.
"""
import math

import numpy as np
import scipy.sparse as sp
from scipy.interpolate import BSpline
from scipy.sparse.linalg import spsolve

from .accuracy import scale
ACCURACY = scale("freeform")

FIT_DEV = 0.002 * ACCURACY  # mm: the surface passes this close to every mesh corner of its area
GRAPH_DEG = 75          # no facet turned further than this from the base's direction there
CYLINDER_DEG = 30       # a cylinder base: every facet within this of square to the axis
MAX_INTERVALS = 128     # knot intervals along each direction at most
MIN_SPAN = 0.25         # mm: knots no closer than this
SMOOTHING = 1e-7        # penalty weight: only settles the surface where corners are sparse
SAMPLE_WEIGHT = 0.01    # weight of points on the facets between their corners (corners: 1)
RIM_WEIGHT = 10.0       # weight of the corners on the area's outline
MARGIN = 0.05           # share of the area's extent (plus MARGIN_MM) the surface reaches past it
MARGIN_MM = 0.5
BULGE_MM = 0.005 * ACCURACY  # between the corners the surface may bow off a facet by L*theta/4 + this


class Freeform:
    """A height field H(u, v) over a base: a plane (u, v along e1, e2, height along d) or
    a cylinder round the axis (o, d) of radius R (u = R x angle from e1, v along d,
    height away from the axis). signed(p) grows along the height, normal(p) with it."""
    kind = "freeform"
    line = circle = None

    def __init__(self, base, o, d, e1, R, tu, tv, C):
        self.base, self.o, self.d, self.e1, self.R = base, o, d, e1, R
        self.e2 = np.cross(d, e1)
        self.tu, self.tv, self.C = tu, tv, C
        self.rim_dev = 0.0      # how far the outline's corners are off the surface (mm)
        self.dev = 0.0          # ...and the farthest corner
        self._surface = None

    def __getstate__(self):
        state = dict(self.__dict__)
        state["_surface"] = None        # (OpenCascade objects don't pickle)
        return state

    def uvh(self, P):
        X = np.atleast_2d(np.asarray(P, float)) - self.o
        if self.base == "plane":
            return X @ self.e1, X @ self.e2, X @ self.d
        z = X @ self.d
        Q = X - np.outer(z, self.d)
        rho = np.linalg.norm(Q, axis=1)
        return self.R * np.arctan2(Q @ self.e2, Q @ self.e1), z, rho - self.R

    def height(self, u, v, du=0, dv=0):
        """H (or its du-th / dv-th derivative) at the parameters."""
        C, tu, tv = self.C, self.tu, self.tv
        for _ in range(du):
            C, tu = _derivative(C, tu)
        for _ in range(dv):
            Ct, tv = _derivative(C.T, tv)
            C = Ct.T
        Bu = _design(np.asarray(u, float), tu, len(tu) - C.shape[0] - 1)
        Bv = _design(np.asarray(v, float), tv, len(tv) - C.shape[1] - 1)
        return np.asarray(Bv.multiply(Bu @ C).sum(axis=1)).ravel()

    def _gradient(self, P):
        """(value of the implicit function, its gradient) at the points."""
        u, v, h = self.uvh(P)
        H, Hu, Hv = self.height(u, v), self.height(u, v, 1), self.height(u, v, 0, 1)
        if self.base == "plane":
            g = self.d[None] - Hu[:, None] * self.e1 - Hv[:, None] * self.e2
            return h - H, g
        X = np.atleast_2d(P) - self.o
        Q = X - np.outer(X @ self.d, self.d)
        rho = np.maximum(np.linalg.norm(Q, axis=1), 1e-12)
        out = Q / rho[:, None]
        around = np.cross(self.d, out)
        # (u is arc length at the base radius: at radius rho it moves R / rho as fast)
        g = out - (Hu * self.R / rho)[:, None] * around - Hv[:, None] * self.d
        return h - H, g

    def signed(self, P):
        f, g = self._gradient(P)
        return f / np.linalg.norm(g, axis=1)

    def normal(self, P):
        _, g = self._gradient(P)
        return g / np.linalg.norm(g, axis=1)[:, None]

    def point(self, u, v):
        u, v = np.asarray(u, float), np.asarray(v, float)
        H = self.height(u, v)
        if self.base == "plane":
            return self.o + np.outer(u, self.e1) + np.outer(v, self.e2) + np.outer(H, self.d)
        a = u / self.R
        out = np.outer(np.cos(a), self.e1) + np.outer(np.sin(a), self.e2)
        return self.o + np.outer(v, self.d) + (self.R + H)[:, None] * out

    def surface(self):
        """The surface as an OpenCascade B-spline surface over the whole fitted domain:
        exact over a plane (the control points of a height field, at the knots' Greville
        abscissae), approximated over a cylinder (see approximate)."""
        if self._surface is None:
            self._surface = _plane_surface(self) if self.base == "plane" else _sampled_surface(self)
        return self._surface

    def face(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        surface = self.surface()
        if surface is None:
            return None
        u0, u1, v0, v1 = surface.Bounds()
        maker = BRepBuilderAPI_MakeFace(surface, u0, u1, v0, v1, 1e-7)
        return maker.Face() if maker.IsDone() else None


def _derivative(C, t):
    """Coefficients and knots of a spline's derivative along its first index."""
    k = len(t) - len(C) - 1
    scale = k / np.maximum(t[k + 1:len(C) + k] - t[1:len(C)], 1e-300)
    return (C[1:] - C[:-1]) * scale.reshape((-1,) + (1,) * (C.ndim - 1)), t[1:-1]


def _design(x, t, k):
    """B-spline basis values at x (sparse, one row per point); x clamped into the domain."""
    x = np.clip(x, t[k], t[-k - 1])
    return BSpline.design_matrix(x, t, k).tocsr()


def _knots(lo, hi, n, k=3):
    return np.r_[[lo] * k, np.linspace(lo, hi, n + 1), [hi] * k]


def _tensor(Bu, Bv):
    """Row by row Kronecker product of two design matrices (k+1 entries a row each)."""
    n, mv = Bu.shape[0], Bv.shape[1]
    ku, kv = np.diff(Bu.indptr), np.diff(Bv.indptr)
    if not ((ku == ku[0]).all() and (kv == kv[0]).all()):
        Bu, Bv = Bu.toarray(), Bv.toarray()
        return sp.csr_matrix(np.einsum("ij,ik->ijk", Bu, Bv).reshape(n, -1))
    iu, bu = Bu.indices.reshape(n, -1), Bu.data.reshape(n, -1)
    iv, bv = Bv.indices.reshape(n, -1), Bv.data.reshape(n, -1)
    cols = (iu[:, :, None] * mv + iv[:, None, :]).reshape(n, -1)
    vals = (bu[:, :, None] * bv[:, None, :]).reshape(n, -1)
    rows = np.repeat(np.arange(n), cols.shape[1])
    return sp.csr_matrix((vals.ravel(), (rows, cols.ravel())), shape=(n, Bu.shape[1] * mv))


def _difference(n, order):
    D = sp.eye(n, format="csr")
    for _ in range(order):
        D = D[1:] - D[:-1]
    return D


def _solve(u, v, h, tu, tv, w):
    """Penalised least-squares coefficients (mu, mv) of the height field (w: each
    point's weight)."""
    A = _tensor(_design(u, tu, 3), _design(v, tv, 3))
    mu, mv = len(tu) - 4, len(tv) - 4
    Iu, Iv = sp.eye(mu), sp.eye(mv)
    D2u, D2v, D1u, D1v = _difference(mu, 2), _difference(mv, 2), _difference(mu, 1), _difference(mv, 1)
    # bending energy in coefficient terms: both second differences and the twist
    P = sp.kron(D2u.T @ D2u, Iv) + sp.kron(Iu, D2v.T @ D2v) + 2 * sp.kron(D1u.T @ D1u, D1v.T @ D1v)
    weight = SMOOTHING * max(len(u) / (mu * mv), 1.0)
    W = sp.diags(w)
    M = (A.T @ W @ A + weight * P + 1e-12 * sp.eye(mu * mv)).tocsc()
    c = spsolve(M, A.T @ (w * h))
    return c.reshape(mu, mv), A


def _inner_samples(corners, step):
    """Points on the triangles (corners (n, 3, 3)) no further apart than about half the
    knot spacing (at least each one's middle and edge midpoints): a long sliver of a
    triangle leaves the surface free to bow between its far-apart corners otherwise."""
    size = np.linalg.norm(corners - corners[:, [1, 2, 0]], axis=2).max(axis=1)
    out = []
    for n in np.unique(np.clip(np.ceil(2 * size / step), 2, 16).astype(int)):
        a, b = np.meshgrid(np.arange(n + 1), np.arange(n + 1), indexing="ij")
        keep = (a + b <= n) & (a < n) & (b < n) & (a + b > 0)      # (not the corners)
        bary = np.c_[a[keep], b[keep], n - a[keep] - b[keep]] / n
        group = corners[np.clip(np.ceil(2 * size / step), 2, 16).astype(int) == n]
        out.append(np.einsum("kj,tjd->tkd", bary, group).reshape(-1, 3))
    return np.vstack(out)


def _bases(P, N, area, cent):
    """Candidate bases for a height field over the area: (base, o, d, e1, R)."""
    out = []
    d = N.T @ area
    d = d / np.linalg.norm(d)
    if (N @ d).min() > math.cos(math.radians(GRAPH_DEG)):
        o = P.mean(axis=0)
        X = P - o
        X = X - np.outer(X @ d, d)
        e1 = np.linalg.svd(X, full_matrices=False)[2][0]
        e1 = e1 - (e1 @ d) * d
        out.append(("plane", o, d, e1 / np.linalg.norm(e1), 0.0))
    # a cylinder: the axis the normals all stand square to
    w, V = np.linalg.eigh((N * area[:, None]).T @ N)
    axis = V[:, 0]
    if np.abs(N @ axis).max() <= math.sin(math.radians(CYLINDER_DEG)):
        # the axis line: closest to every facet's normal line (centroid along normal)
        Np = N - np.outer(N @ axis, axis)
        Np /= np.maximum(np.linalg.norm(Np, axis=1), 1e-12)[:, None]
        Q = np.eye(3)[None] - Np[:, :, None] * Np[:, None, :] - np.outer(axis, axis)[None]
        Q = Q * area[:, None, None]
        M = Q.sum(axis=0) + np.outer(axis, axis) * area.sum()
        b = np.einsum("ijk,ik->j", Q, cent)
        o = np.linalg.solve(M, b)
        X = P - o
        X = X - np.outer(X @ axis, axis)
        rho = np.linalg.norm(X, axis=1)
        R = float(np.median(rho))
        mid = (X / np.maximum(rho, 1e-12)[:, None]).mean(axis=0)
        if R > 1e-3 and np.linalg.norm(mid) > 0.1:
            e1 = mid / np.linalg.norm(mid)
            out.append(("cylinder", o, axis, e1, R))
    return out


def fit(mesh, facets):
    """A Freeform surface through the facets' corners, or None if none fits (too far
    round for a height field over any base, a crease inside, or a surface that waves
    between the corners). Also returns how far it strays at most from the corners."""
    facets = np.asarray(facets)
    tids = np.concatenate([mesh.ftris[f] for f in facets])
    T = mesh.tris[tids]
    V, local = np.unique(T, return_inverse=True)
    local = local.reshape(-1, 3)
    # the corners on the area's outline: the edges shared with the faces beside it run
    # through them, and the face is cut along those edges, so they weigh more
    edges = np.sort(np.concatenate([local[:, [0, 1]], local[:, [1, 2]], local[:, [2, 0]]]), axis=1)
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    rim = np.zeros(len(V), bool)
    rim[uniq[counts == 1].ravel()] = True
    P = mesh.pts[V]
    N, area, cent = mesh.fn[facets], mesh.farea[facets], mesh.fcent[facets]
    for base, o, d, e1, R in _bases(P, N, area, cent):
        model = Freeform(base, o, d, e1, R, None, None, None)
        u, v, h = model.uvh(P)
        if base == "cylinder":
            # (no seam inside: the area spans less than a whole turn)
            if np.ptp(u) / R > 2 * math.pi - 0.3:
                continue
            # a height field over the cylinder: every facet facing away from the axis,
            # or every one towards it
            X = cent - o
            X = X - np.outer(X @ d, d)
            X /= np.maximum(np.linalg.norm(X, axis=1), 1e-12)[:, None]
            facing = np.einsum("ij,ij->i", N, X)
            if not ((facing > math.cos(math.radians(GRAPH_DEG))).all()
                    or (facing < -math.cos(math.radians(GRAPH_DEG))).all()):
                continue
        span_u, span_v = np.ptp(u), np.ptp(v)
        mu_, mv_ = MARGIN * span_u + MARGIN_MM, MARGIN * span_v + MARGIN_MM
        lo_u, hi_u, lo_v, hi_v = u.min() - mu_, u.max() + mu_, v.min() - mv_, v.max() + mv_
        step = max(hi_u - lo_u, hi_v - lo_v) / 4
        corners = mesh.pts[T]
        # (no more knots along a direction than the corners are spaced on average, nor
        # closer than MIN_SPAN: a long narrow strip gets one span across, and no area
        # more spans than corners; one that only fits more finely than that has a
        # crease or a tight round inside)
        spacing = max(MIN_SPAN, math.sqrt((hi_u - lo_u) * (hi_v - lo_v) / len(V)))
        cap_u = min(MAX_INTERVALS, max(1, math.ceil((hi_u - lo_u) / spacing)))
        cap_v = min(MAX_INTERVALS, max(1, math.ceil((hi_v - lo_v) / spacing)))
        while True:
            nu = int(min(cap_u, max(1, math.ceil((hi_u - lo_u) / step))))
            nv = int(min(cap_v, max(1, math.ceil((hi_v - lo_v) / step))))
            tu, tv = _knots(lo_u, hi_u, nu), _knots(lo_v, hi_v, nv)
            # the corners, and (weakly: a facet is off the surface by up to its sag)
            # points on the facets between them
            su, sv, sh = model.uvh(_inner_samples(corners, step))
            w = np.r_[np.where(rim, RIM_WEIGHT, 1.0), np.full(len(su), SAMPLE_WEIGHT)]
            C, A = _solve(np.r_[u, su], np.r_[v, sv], np.r_[h, sh], tu, tv, w)
            gap = np.abs(A[:len(u)] @ C.ravel() - h)
            dev = float(gap.max())
            model.tu, model.tv, model.C = tu, tv, C
            model.rim_dev = float(gap[rim].max()) if rim.any() else dev
            if dev <= FIT_DEV and _hugs(model, mesh, tids, T, local, P):
                model.dev = dev
                return model, dev
            if nu >= cap_u and nv >= cap_v:
                break
            step /= 2
    return None, None


def feature(mesh, model, facets, parts, dev):
    """The patch for a fitted area (parts: the pieces it replaces, given back if its
    face can't be built). Its volume change is measured as for any exact surface, from
    how far each facet's edge midpoints sit off it."""
    from .features import Feature
    tids = np.concatenate([mesh.ftris[f] for f in facets])
    corners = mesh.pts[mesh.tris[tids]]
    s = model.signed(((corners + corners[:, [1, 2, 0]]) / 2).reshape(-1, 3)).reshape(-1, 3)
    area = mesh.tarea[tids]
    # (material on the far side of the height direction, or on the near side)
    outward = np.einsum("ij,ij->i", mesh.fn[facets], model.normal(mesh.fcent[facets])) @ mesh.farea[facets]
    convex = bool(outward > 0)
    change = (-1 if convex else 1) * float((area * s.mean(axis=1)).sum())
    gain = float((area * np.abs(s).mean(axis=1)).sum())
    spans = f"{len(model.tu) - 7}x{len(model.tv) - 7} spans"
    return Feature(model, "freeform surface", f"{len(facets)} facets, {spans}, within {dev:.4f} mm of the mesh",
                   convex=convex, kind="trimmed", facets=np.asarray(facets), change=change,
                   tolerance=0.5 * gain + 1e-3, worst=max(float(np.abs(s).max()), dev), parts=tuple(parts))


def _hugs(model, mesh, tids, T, local, P):
    """Does the surface stay as close to each triangle as a smooth surface through its
    corners can (blends._hugs_mesh's rule: L*theta/4, theta how far the surface turns
    across it, read from the corners' normals)? A surface that waves between sparse
    corners bows much further."""
    tn, tarea = mesh.tn[tids], mesh.tarea[tids]
    vn = np.zeros_like(P)
    for j in range(3):
        np.add.at(vn, local[:, j], tn * tarea[:, None])
    vn /= np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]
    theta = np.arccos(np.clip(np.einsum("tkj,tj->tk", vn[local], tn), -1, 1)).max(axis=1)
    corners = mesh.pts[T]
    size = np.linalg.norm(corners - corners[:, [1, 2, 0]], axis=2).max(axis=1)
    allowed = size * theta / 4 + BULGE_MM
    samples = np.concatenate([(corners + corners[:, [1, 2, 0]]) / 2, corners.mean(axis=1)[:, None]], axis=1)
    gap = np.abs(model.signed(samples.reshape(-1, 3))).reshape(len(T), -1).max(axis=1)
    return bool((gap <= allowed + FIT_DEV).all())


def _plane_surface(model):
    from OCP.Geom import Geom_BSplineSurface
    from OCP.collections import Array1_double, Array1_int, Array2_gp_Pnt
    from OCP.gp import gp_Pnt
    tu, tv, C = model.tu, model.tv, model.C
    gu = (tu[1:-3] + tu[2:-2] + tu[3:-1]) / 3       # Greville abscissae
    gv = (tv[1:-3] + tv[2:-2] + tv[3:-1]) / 3
    poles = Array2_gp_Pnt(1, len(gu), 1, len(gv))
    for i, a in enumerate(gu):
        for j, b in enumerate(gv):
            p = model.o + a * model.e1 + b * model.e2 + C[i, j] * model.d
            poles.SetValue(i + 1, j + 1, gp_Pnt(*map(float, p)))

    def knots(t):
        values, counts = np.unique(t, return_counts=True)
        k, m = Array1_double(1, len(values)), Array1_int(1, len(values))
        for i, (x, c) in enumerate(zip(values, counts)):
            k.SetValue(i + 1, float(x))
            m.SetValue(i + 1, int(c))
        return k, m
    uk, um = knots(tu)
    vk, vm = knots(tv)
    return Geom_BSplineSurface(poles, uk, vk, um, vm, 3, 3)


def _sampled_surface(model):
    tu, tv = model.tu, model.tv
    U = np.linspace(tu[0], tu[-1], 4 * (len(tu) - 7) + 1)
    V = np.linspace(tv[0], tv[-1], 4 * (len(tv) - 7) + 1)
    return approximate(model.point, U, V)


APPROX_TRIES = 4        # ways of laying a B-spline surface through a grid of points, tried in turn
APPROX_GOOD = 0.0004    # mm: the first passing this close to the surface between the points is taken
APPROX_CHECKS = 80      # points between the grid points it is measured at


def approximate(point, U, V):
    """An OpenCascade B-spline surface through the points point(U x V) (a grid, U and V
    each in step with the surface's own parameters), as close as one of a few ways of
    laying it can get between them. None of them is close everywhere:
    GeomAPI_PointsToBSplineSurface allowed degrees up to 8 reports success while missing
    its own points by millimetres on some grids, and kept cubic it waves between them by
    a tenth of a millimetre on others. Each is measured at points midway between the
    grid's, and the first within APPROX_GOOD (or else the closest) is taken."""
    from OCP.Approx import Approx_ChordLength, Approx_IsoParametric
    from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface, GeomAPI_ProjectPointOnSurf
    from OCP.GeomAbs import GeomAbs_C2
    from OCP.collections import Array2_gp_Pnt
    from OCP.gp import gp_Pnt
    uu, vv = np.meshgrid(U, V, indexing="ij")
    X = point(uu.ravel(), vv.ravel()).reshape(len(U), len(V), 3)
    grid = Array2_gp_Pnt(1, len(U), 1, len(V))
    for i in range(len(U)):
        for j in range(len(V)):
            grid.SetValue(i + 1, j + 1, gp_Pnt(*map(float, X[i, j])))
    rng = np.random.default_rng(0)
    i = rng.integers(0, len(U) - 1, APPROX_CHECKS)
    j = rng.integers(0, len(V) - 1, APPROX_CHECKS)
    checks = point((U[i] + U[i + 1]) / 2, (V[j] + V[j + 1]) / 2)
    best, best_gap = None, math.inf
    for kind, degree in ((Approx_ChordLength, 8), (Approx_IsoParametric, 3),
                         (Approx_ChordLength, 3), (Approx_IsoParametric, 8))[:APPROX_TRIES]:
        approx = GeomAPI_PointsToBSplineSurface(grid, kind, 3, degree, GeomAbs_C2, 1e-5)
        if not approx.IsDone():
            continue
        surface = approx.Surface()
        gap = 0.0
        for p in checks:
            project = GeomAPI_ProjectPointOnSurf(gp_Pnt(*map(float, p)), surface)
            gap = max(gap, project.LowerDistance() if project.NbPoints() else math.inf)
            if gap >= best_gap:
                break
        if gap < best_gap:
            best, best_gap = surface, gap
        if best_gap <= APPROX_GOOD:
            break
    return best
