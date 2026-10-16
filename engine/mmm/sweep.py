"""Swept surfaces: a profile curve moved along a straight line (extrusion:
straight gear teeth, a sketch pulled up), turned about an axis (revolution)
or screwed along one (helix: helical gear teeth, threads, treads).

Such a surface is unchanged by its motion, so each normal n at a point p
is square to the velocity v(p) = cbar + c x p of that motion. That is one
linear equation per point in the six numbers (c, cbar); the motion that
best satisfies all of them is the eigenvector of the smallest eigenvalue
of a 6x6 matrix (Pottmann & Randrup, 1998). How much smaller that
eigenvalue is than the next tells how sure the fit is.

Points are then moved back along the motion into one plane (the
profile's), where they lie on a curve; a smooth curve is fitted to them,
and swept again to give the surface."""
import numpy as np

from .fitting import perp_basis, unit

# a patch only counts as swept when its normals are this square to the
# motion (rms, degrees) ...
MAX_RMS_DEG = 2.0
# ... and the best motion fits this many times better than the next one
# (a cylinder or a plane fits two or three motions equally well: those are
# left to the analytic surfaces)
MIN_GAP = 4.0


class Motion:
    """kind: "extrusion" (along d), "helix" (about the axis through a along
    d, moving pitch along it per radian) or "revolution" (pitch 0)."""

    def __init__(self, kind, a, d, pitch=0.0, sureness=1.0, rms_deg=0.0):
        self.kind = kind
        self.a = np.asarray(a, float)
        self.d = unit(np.asarray(d, float))
        self.pitch = float(pitch)
        self.u, self.v = perp_basis(self.d)
        self.sureness = float(sureness)
        self.rms_deg = float(rms_deg)
        self.phi0 = 0.0
        self.z0 = 0.0

    def describe(self):
        if self.kind == "extrusion":
            return f"extrusion along {np.round(self.d, 3).tolist()}"
        if self.kind == "revolution":
            return f"revolution about {np.round(self.d, 3).tolist()}"
        return f"helix about {np.round(self.d, 3).tolist()}, lead {abs(self.pitch) * 2 * np.pi:.4g}"

    def velocity(self, P):
        rel = np.asarray(P) - self.a
        if self.kind == "extrusion":
            return np.tile(self.d, (len(rel), 1))
        return np.cross(self.d, rel) + self.pitch * self.d

    def to_profile(self, P):
        """(profile coordinates (n, 2), sweep parameter (n,)) of points.

        An extrusion's profile is its cross-section; a revolution's lies in
        a plane through the axis, (radius, height); a helix's is its
        cross-section across the axis at height z0 (the way a helical gear
        or a twisted extrusion is drawn: a flank with corners only at its
        ends is then a straight line, as drawn, and meets the end faces in
        the straight edge they have), and the sweep parameter is the turn
        that brings a point there."""
        rel = np.asarray(P, float) - self.a
        if self.kind == "extrusion":
            return np.c_[rel @ self.u, rel @ self.v], rel @ self.d
        z = rel @ self.d
        x, y = rel @ self.u, rel @ self.v
        if self.kind == "helix":
            t = (z - self.z0) / self.pitch
            c, s = np.cos(t), np.sin(t)
            return np.c_[c * x + s * y, -s * x + c * y], t
        phi = np.mod(np.arctan2(y, x) - self.phi0 + np.pi, 2 * np.pi) - np.pi
        return np.c_[np.hypot(x, y), z], phi

    def from_profile(self, Q, t):
        """Points of profile coordinates Q moved by sweep parameter t."""
        Q = np.atleast_2d(Q)
        t = np.broadcast_to(np.asarray(t, float), (len(Q),))
        if self.kind == "extrusion":
            return self.a + np.outer(Q[:, 0], self.u) + np.outer(Q[:, 1], self.v) + np.outer(t, self.d)
        if self.kind == "helix":
            c, s = np.cos(t), np.sin(t)
            x, y = c * Q[:, 0] - s * Q[:, 1], s * Q[:, 0] + c * Q[:, 1]
            return (self.a + np.outer(self.z0 + self.pitch * t, self.d)
                    + np.outer(x, self.u) + np.outer(y, self.v))
        phi = t + self.phi0
        r, z = Q[:, 0], Q[:, 1]
        return (self.a + np.outer(z, self.d)
                + np.outer(r * np.cos(phi), self.u) + np.outer(r * np.sin(phi), self.v))


def motion_rms(mot, P, N, W=None):
    """How square normals N at points P are to a motion (degrees, rms)."""
    V = mot.velocity(np.asarray(P, float))
    sp = np.linalg.norm(V, axis=1)
    ok = sp > 1e-12
    if not ok.any():
        return 90.0
    w = np.ones(len(P)) if W is None else np.asarray(W, float)
    sin = np.abs((np.asarray(N)[ok] * V[ok]).sum(1)) / sp[ok]
    return float(np.degrees(np.arcsin(np.clip(np.sqrt((w[ok] * sin ** 2).sum() / max(w[ok].sum(), 1e-300)), 0, 1))))


def set_phi0(mot, P):
    """Angles measured from the middle of these points (no wrap inside); a
    helix's profile taken at their middle height."""
    if mot.kind == "helix":
        mot.z0 = float(((np.asarray(P) - mot.a) @ mot.d).mean())
    if mot.kind != "extrusion":
        rel = np.asarray(P) - mot.a
        ang = np.arctan2(rel @ mot.v, rel @ mot.u)
        mot.phi0 = float(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean()))
    return mot


def copy_motion(mot):
    m = Motion(mot.kind, mot.a, mot.d, mot.pitch, mot.sureness, mot.rms_deg)
    m.phi0, m.z0 = mot.phi0, mot.z0
    return m


def fit_motion(P, N, W=None, min_gap=MIN_GAP, max_rms=None):
    """The rigid motion that leaves the surface through points P with unit
    normals N unchanged, or None when there is no single clear one (a lower
    ``min_gap`` lets a thin patch propose one for a group to confirm)."""
    P = np.asarray(P, float)
    N = np.asarray(N, float)
    if len(P) < 12:
        return None
    o = P.mean(0)
    s = max(float(np.linalg.norm(P - o, axis=1).max()), 1e-12)
    X = (P - o) / s
    A = np.hstack([np.cross(X, N), N])
    w = np.ones(len(P)) if W is None else np.asarray(W, float)
    M = (A * w[:, None]).T @ A / w.sum()
    lam, U = np.linalg.eigh(M)
    lam = np.maximum(lam, 0.0)
    c, cbar = U[:3, 0], U[3:, 0]
    nc, nb = float(np.linalg.norm(c)), float(np.linalg.norm(cbar))
    if nc < 0.02 * max(nb, 1e-12):
        mot = Motion("extrusion", o, cbar)
    else:
        d = c / nc
        # in the scaled frame: axis point, and translation per radian
        p0 = np.cross(c, cbar) / nc ** 2
        pitch = float(d @ cbar) / nc
        kind = "revolution" if abs(pitch) < 1e-3 else "helix"
        mot = Motion(kind, o + s * p0, d, pitch * s if kind == "helix" else 0.0)
    # how square the normals really are to the motion (degrees, rms)
    V = mot.velocity(P)
    sp = np.linalg.norm(V, axis=1)
    ok = sp > 1e-9 * s
    if not ok.any():
        return None
    sin = np.abs((N[ok] * V[ok]).sum(1)) / sp[ok]
    rms = float(np.degrees(np.arcsin(np.clip(np.sqrt((w[ok] * sin ** 2).sum() / w[ok].sum()), 0, 1))))
    gap = float(np.sqrt(lam[1] / max(lam[0], 1e-30)))
    mot.rms_deg = rms
    # sure when the normals are square to it and nothing else fits nearly as well
    mot.sureness = float(np.clip(min(1.0 - rms / MAX_RMS_DEG, (gap - MIN_GAP) / MIN_GAP), 0.0, 1.0))
    if rms > (MAX_RMS_DEG if max_rms is None else max_rms) or gap < min_gap:
        return None
    return set_phi0(mot, P)


def order_curve(Q, tol):
    """Points scattered along one open curve, in order along it (the
    longest path through a minimum spanning tree of near neighbours), with
    points closer than tol merged. None when they don't form one curve."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components, dijkstra, minimum_spanning_tree
    from scipy.spatial import cKDTree

    Q = np.asarray(Q, float)
    # merge near-duplicates (a profile point is the image of many vertices)
    key = np.round(Q / max(tol, 1e-12)).astype(np.int64)
    _, first, inv = np.unique(key, axis=0, return_index=True, return_inverse=True)
    inv = inv.reshape(-1)
    cnt = np.bincount(inv)
    R = np.zeros((len(first), 2))
    np.add.at(R, inv, Q)
    R /= cnt[:, None]
    n = len(R)
    if n < 4:
        return None
    k = min(8, n - 1)
    d, j = cKDTree(R).query(R, k + 1)
    rows = np.repeat(np.arange(n), k)
    G = coo_matrix((d[:, 1:].reshape(-1) + 1e-12, (rows, j[:, 1:].reshape(-1))), shape=(n, n))
    T = minimum_spanning_tree(G)
    T = T + T.T
    if connected_components(T, directed=False)[0] != 1:
        return None
    # the tree's longest path: farthest from any point, then farthest from that
    d0 = dijkstra(T, indices=0)
    a = int(np.argmax(d0))
    da, pred = dijkstra(T, indices=a, return_predecessors=True)
    b = int(np.argmax(da))
    path = [b]
    while path[-1] != a:
        path.append(int(pred[path[-1]]))
    # a curve, not a blob: most points lie near the path
    on = np.zeros(n, bool)
    on[path] = True
    P = R[path]
    if len(P) < 4:
        return None
    dd, _ = cKDTree(P).query(R[~on]) if (~on).any() else (np.zeros(0), None)
    if len(dd) and np.percentile(dd, 95) > 4 * tol:
        return None
    return P


class ProfileCurve:
    """A smooth 2D curve (cubic B-spline by chord length) through ordered
    points, with a dense polyline for distances."""

    def __init__(self, P, tol):
        from scipy.interpolate import make_splprep

        P = np.asarray(P, float)
        self.tol = tol
        seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
        # A profile known only at a few spots (a flank whose mesh has
        # corners only at its ends: two tight clusters of points) says
        # nothing about a curve between them, and a cubic through the
        # clusters swings far off: straight lines between their centres.
        gap = seg > 0.2 * seg.sum()
        if gap.any() and gap.sum() <= 2:
            parts = np.split(P, np.nonzero(gap)[0] + 1)
            P = np.array([q.mean(0) for q in parts])
            seg = np.linalg.norm(np.diff(P, axis=0), axis=1)
        t = np.r_[0.0, np.cumsum(seg)]
        t /= t[-1]
        # smooth within the tolerance, never wiggling between points
        k = min(3, len(P) - 1)
        self.spl, _ = make_splprep(P.T, u=t, k=k, s=0.0 if k < 3 else len(P) * (0.25 * tol) ** 2)
        self.ts = np.linspace(0.0, 1.0, max(200, 8 * len(P)))
        self.S = self.at(self.ts)
        from scipy.spatial import cKDTree

        self.tree = cKDTree(self.S)

    def at(self, t):
        # (beyond 0..1 the end spans are continued)
        return np.asarray(self.spl(np.asarray(t, float), extrapolate=True)).T.reshape(-1, 2)

    def nearest(self, Q):
        """(distance, curve parameter) of the nearest curve point."""
        d, k = self.tree.query(np.asarray(Q, float))
        return d, self.ts[k]


class SweptSurface:
    """A profile curve swept by a motion over a parameter range. Looks like
    a freeform face to the rest of the converter (geom, distance, normal)."""

    kind = "freeform"
    rank = 4

    def __init__(self, motion, curve, s_range, t_range, tol):
        from scipy.spatial import cKDTree

        self.motion = motion
        self.curve = curve
        self.s_range = s_range
        self.t_range = t_range
        self.tol = tol
        self.dev = 0.0
        self._geom = None
        # the profile over the surface's whole range (a little past the
        # patch's ends), densely, for nearest points
        self.ss = np.linspace(s_range[0], s_range[1], max(400, 2 * len(curve.ts)))
        self.SS = curve.at(self.ss)
        self.tree = cKDTree(self.SS)

    def _nearest(self, Q):
        d, k = self.tree.query(np.asarray(Q, float))
        return d, self.ss[k]

    def X(self, s, t):
        return self.motion.from_profile(self.curve.at(s), t)

    def foot(self, P, iters=6):
        """(s, t) of the surface point nearest each point: the profile point
        nearest in the profile's plane to start, then Gauss-Newton on the
        3D distance (for a helix the plane cuts a flank facing round the
        axis at a slant, so the start can be well off)."""
        P = np.atleast_2d(np.asarray(P, float))
        Q, t = self.motion.to_profile(P)
        _, s = self._nearest(Q)
        if self.motion.kind != "helix":
            return s, t
        hs = 1e-5 * max(self.s_range[1] - self.s_range[0], 1e-9)
        ht = 1e-5 * max(self.t_range[1] - self.t_range[0], 1e-9)
        for _ in range(iters):
            X = self.X(s, t)
            Js = (self.X(s + hs, t) - X) / hs
            Jt = (self.X(s, t + ht) - X) / ht
            r = P - X
            a, b, c = (Js * Js).sum(1), (Js * Jt).sum(1), (Jt * Jt).sum(1)
            gs, gt = (Js * r).sum(1), (Jt * r).sum(1)
            det = a * c - b * b
            ok = np.abs(det) > 1e-30
            ds = np.where(ok, (c * gs - b * gt) / np.where(ok, det, 1), 0.0)
            dt = np.where(ok, (a * gt - b * gs) / np.where(ok, det, 1), 0.0)
            s, t = s + ds, t + dt
            if np.abs(ds).max() < 1e-9 and np.abs(dt).max() < 1e-12:
                break
        return s, t

    def uv(self, P):
        """Surface parameters (0..1 along the profile and the sweep, see
        geom) of points on or near the surface."""
        s, t = self.foot(P)
        (s0, s1), (t0, t1) = self.s_range, self.t_range
        return (s - s0) / (s1 - s0), (t - t0) / (t1 - t0)

    def describe(self):
        return f"swept ({self.motion.describe()})"

    def distance(self, P):
        P = np.atleast_2d(np.asarray(P, float))
        if self.motion.kind != "helix":
            return self._nearest(self.motion.to_profile(P)[0])[0]  # exact
        s, t = self.foot(P)
        return np.linalg.norm(P - self.X(s, t), axis=1)

    def normal(self, P):
        P = np.atleast_2d(P)
        s, t = self.foot(P)
        return self._normal_at(s, t)

    def _normal_at(self, s, t):
        h = 1e-4
        a = self.motion.from_profile(self.curve.at(np.clip(s - h, 0, 1)), t)
        b = self.motion.from_profile(self.curve.at(np.clip(s + h, 0, 1)), t)
        dt = 1e-4 * (1.0 if self.motion.kind != "extrusion" else max(self.tol, 1e-3) * 100)
        c = self.motion.from_profile(self.curve.at(s), t + dt)
        e = self.motion.from_profile(self.curve.at(s), t - dt)
        n = np.cross(b - a, c - e)
        return n / np.maximum(np.linalg.norm(n, axis=1), 1e-30)[:, None]

    def degenerate(self, ext):
        return False

    def same_as(self, o, tol, ext):
        return False

    def refit(self, P, N=None, W=None):
        return None  # kept as fitted

    def pcurve(self, edge, n=40):
        """The edge's curve in this surface's parameters (Geom2d), sharing
        the edge's own parameters, or None."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.Geom2dAPI import Geom2dAPI_Interpolate
        from OCP.gp import gp_Pnt2d
        from OCP.collections import HArray1_double as TColStd_HArray1OfReal
        from OCP.collections import HArray1_gp_Pnt2d as TColgp_HArray1OfPnt2d

        c = BRepAdaptor_Curve(edge)
        a, b = c.FirstParameter(), c.LastParameter()
        ts = np.linspace(a, b, n)
        P = np.array([[*c.Value(float(t)).Coord()] for t in ts])
        u, v = self.uv(P)
        if not (np.isfinite(u).all() and np.isfinite(v).all()):
            return None
        pts = TColgp_HArray1OfPnt2d(1, n)
        prm = TColStd_HArray1OfReal(1, n)
        for i in range(n):
            pts.SetValue(i + 1, gp_Pnt2d(float(u[i]), float(v[i])))
            prm.SetValue(i + 1, float(ts[i]))
        it = Geom2dAPI_Interpolate(pts, prm, False, 1e-9)
        it.Perform()
        return it.Curve() if it.IsDone() else None

    def grid(self, nu=40, nv=24):
        s = np.linspace(*self.s_range, nu)
        t = np.linspace(*self.t_range, nv)
        C = self.curve.at(s)
        return np.stack([self.motion.from_profile(C, tj) for tj in t], axis=1)  # (nu, nv, 3)

    def geom(self):
        if self._geom is None:
            from OCP.Approx import Approx_IsoParametric
            from OCP.GeomAbs import GeomAbs_C2
            from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface
            from OCP.collections import Array2_gp_Pnt as TColgp_Array2OfPnt
            from OCP.gp import gp_Pnt

            nv = 24 if self.motion.kind != "extrusion" else 4
            G = self.grid(nu=max(24, min(80, len(self.curve.S) // 8)), nv=nv)
            arr = TColgp_Array2OfPnt(1, G.shape[0], 1, G.shape[1])
            for i in range(G.shape[0]):
                for j in range(G.shape[1]):
                    arr.SetValue(i + 1, j + 1, gp_Pnt(*map(float, G[i, j])))
            # evenly spaced parameters: u and v are the profile position and
            # the sweep, scaled to 0..1 (see uv), so edges can be placed on
            # the face exactly instead of by projection (which went astray
            # on twisted flanks, and left the faces invalid)
            ap = GeomAPI_PointsToBSplineSurface(arr, Approx_IsoParametric, 3, 8, GeomAbs_C2, max(0.05 * self.tol, 1e-7))
            self._geom = ap.Surface()
        return self._geom


def fit_swept(P, N, W, tol, X=None, margin=0.05, motion=None):
    """A swept surface through a patch (triangle centres P with unit normals
    N and areas W, for the motion; its corners X, which lie on the true
    surface, for the profile), or None when the patch isn't one. Returns
    (surface, deviation of the corners from it)."""
    mot = fit_motion(P, N, W) if motion is None else set_phi0(copy_motion(motion), P)
    if mot is None:
        return None, np.inf
    Q, t = mot.to_profile(P if X is None else X)
    O = order_curve(Q, 0.25 * tol)
    if O is None:
        return None, np.inf
    try:
        curve = ProfileCurve(O, tol)
    except Exception:
        return None, np.inf
    d, s = curve.nearest(Q)
    dev = float(np.percentile(d, 99)) if len(d) else np.inf
    ds = max(s.max() - s.min(), 1e-6)
    dt = max(t.max() - t.min(), 1e-9)
    # a little beyond the patch on every side (the curve is extended past
    # its ends): the face's edges must lie inside the surface, not on its
    # border, or they fall a hair outside it and the face is invalid
    s_range = (s.min() - margin * ds, s.max() + margin * ds)
    t_range = (t.min() - margin * dt, t.max() + margin * dt)
    surf = SweptSurface(mot, curve, s_range, t_range, tol)
    surf.dev = dev
    return surf, dev
