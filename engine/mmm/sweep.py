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
    d, moving pitch along it per radian), "revolution" (pitch 0) or "taper"
    (growing about the point a, and turning twist radians about the axis
    through a along d while it grows by a factor e: lines through one
    point when twist is 0, as on a bevel gear or a drafted side)."""

    def __init__(self, kind, a, d, pitch=0.0, sureness=1.0, rms_deg=0.0, twist=0.0):
        self.kind = kind
        self.a = np.asarray(a, float)
        self.d = unit(np.asarray(d, float))
        self.pitch = float(pitch)
        self.twist = float(twist)
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
        if self.kind == "taper":
            return f"taper towards {np.round(self.a, 3).tolist()}, twist {self.twist:.3g}"
        return f"helix about {np.round(self.d, 3).tolist()}, lead {abs(self.pitch) * 2 * np.pi:.4g}"

    def velocity(self, P):
        rel = np.asarray(P) - self.a
        if self.kind == "extrusion":
            return np.tile(self.d, (len(rel), 1))
        if self.kind == "taper":
            return rel + self.twist * np.cross(self.d, rel)
        return np.cross(self.d, rel) + self.pitch * self.d

    def _turn(self, W, ang):
        """Vectors W turned by angles ang about d."""
        c, s = np.cos(ang)[:, None], np.sin(ang)[:, None]
        return W * c + np.cross(self.d, W) * s + np.outer(W @ self.d, self.d) * (1 - c)

    def turns(self, t):
        """How far round the axis a sweep parameter range goes (radians)."""
        return abs(self.twist) * t if self.kind == "taper" else t

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
        if self.kind == "taper":
            # the profile is the section at height z0 along d (from the
            # centre); a point gets there by shrinking by z0 / its height,
            # and turning back (a point at or behind the centre has none)
            h = rel @ self.d
            with np.errstate(divide="ignore", invalid="ignore"):
                t = np.log(h / self.z0)
            back = self._turn(rel, -self.twist * np.nan_to_num(t)) * np.exp(-np.nan_to_num(t))[:, None]
            Q = np.c_[back @ self.u, back @ self.v]
            Q[~np.isfinite(t)] = np.nan
            return Q, t
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
        if self.kind == "taper":
            W = np.outer(Q[:, 0], self.u) + np.outer(Q[:, 1], self.v) + self.z0 * self.d
            return self.a + self._turn(W, self.twist * t) * np.exp(t)[:, None]
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
    if mot.kind in ("helix", "taper"):
        mot.z0 = float(((np.asarray(P) - mot.a) @ mot.d).mean())
    if mot.kind not in ("extrusion", "taper"):
        rel = np.asarray(P) - mot.a
        ang = np.arctan2(rel @ mot.v, rel @ mot.u)
        mot.phi0 = float(np.arctan2(np.sin(ang).mean(), np.cos(ang).mean()))
    return mot


def copy_motion(mot):
    m = Motion(mot.kind, mot.a, mot.d, mot.pitch, mot.sureness, mot.rms_deg, mot.twist)
    m.phi0, m.z0 = mot.phi0, mot.z0
    return m


def fit_motion(P, N, W=None, min_gap=MIN_GAP, max_rms=None, taper=True):
    """The rigid motion that leaves the surface through points P with unit
    normals N unchanged (or, when there is none, a growing one: see
    fit_taper), or None when there is no single clear one (a lower
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
        # no rigid motion: perhaps one that also grows (a bevel gear, a
        # drafted side)
        return fit_taper(P, N, W, min_gap, max_rms) if taper else None
    return set_phi0(mot, P)


def fit_taper(P, N, W=None, min_gap=MIN_GAP, max_rms=None):
    """The motion that grows about a point (and perhaps turns about an
    axis through it) leaving the surface through points P with normals N
    unchanged, or None. Its velocity is cbar + c x p + g p: one more number
    than a rigid motion's, found the same way (7x7)."""
    o = P.mean(0)
    s = max(float(np.linalg.norm(P - o, axis=1).max()), 1e-12)
    X = (P - o) / s
    A = np.hstack([np.cross(X, N), N, (X * N).sum(1)[:, None]])
    w = np.ones(len(P)) if W is None else np.asarray(W, float)
    M = (A * w[:, None]).T @ A / w.sum()
    lam, U = np.linalg.eigh(M)
    lam = np.maximum(lam, 0.0)
    if lam[1] <= 1e-9 * max(lam.sum(), 1e-300):
        # two motions fit exactly (a cylinder slides and turns): the
        # smallest one is any mix of them, and their ratio is noise
        return None
    e = U[:, 0] * np.sign(U[6, 0] or 1.0)  # growing as the parameter rises
    c, cbar, g = e[:3], e[3:6], e[6]
    if g < 0.05 * max(np.linalg.norm(c), np.linalg.norm(cbar)):
        return None  # hardly growing: a rigid motion that didn't fit
    # the point that stays put: cbar + c x z + g z = 0
    Cx = np.array([[0, -c[2], c[1]], [c[2], 0, -c[0]], [-c[1], c[0], 0]])
    try:
        z = np.linalg.solve(g * np.eye(3) + Cx, -cbar)
    except np.linalg.LinAlgError:
        return None
    if np.linalg.norm(z) > 50:
        return None  # centre far off: as good as a rigid motion
    nc = float(np.linalg.norm(c))
    centre = o + s * z
    if nc > 0.01 * g:  # (less is noise: under a degree over a doubling)
        d, twist = c / nc, nc / g
    else:
        d, twist = unit((P - centre).mean(0)), 0.0
    if ((P - centre) @ d).mean() < 0:
        d, twist = -d, -twist
    mot = Motion("taper", centre, d, twist=twist)
    if ((P - centre) @ d).min() <= 0:
        return None  # the profile's section wouldn't cross every line
    V = mot.velocity(P)
    sp = np.linalg.norm(V, axis=1)
    ok = sp > 1e-9 * s
    if not ok.any():
        return None
    sin = np.abs((N[ok] * V[ok]).sum(1)) / sp[ok]
    rms = float(np.degrees(np.arcsin(np.clip(np.sqrt((w[ok] * sin ** 2).sum() / w[ok].sum()), 0, 1))))
    gap = float(np.sqrt(lam[1] / max(lam[0], 1e-30)))
    mot.rms_deg = rms
    mot.sureness = float(np.clip(min(1.0 - rms / MAX_RMS_DEG, (gap - MIN_GAP) / MIN_GAP), 0.0, 1.0))
    if rms > (MAX_RMS_DEG if max_rms is None else max_rms) or gap < min_gap:
        return None
    return set_phi0(mot, P)


def _thickness(Q, k=10):
    """How far 2D points stray from a curve through them: the rms of each
    point's distance from the line through its k nearest neighbours."""
    from scipy.spatial import cKDTree

    if len(Q) <= k or not np.isfinite(Q).all():
        return np.inf
    _, j = cKDTree(Q).query(Q, k)
    G = Q[j] - Q[j].mean(1, keepdims=True)
    C = np.einsum("nki,nkj->nij", G, G) / k
    # smallest eigenvalue of each 2x2 covariance
    tr, det = C[:, 0, 0] + C[:, 1, 1], C[:, 0, 0] * C[:, 1, 1] - C[:, 0, 1] ** 2
    return float(np.sqrt(np.maximum(0.5 * tr - np.sqrt(np.maximum(0.25 * tr ** 2 - det, 0)), 0).mean()))


def refine_taper(mot, X):
    """A taper's centre (and twist) moved so that the points X, which lie
    on the surface, map to the thinnest curve in the profile's plane. The
    normals of a mesh's flat triangles, which cut across the curved
    surface, place the centre only roughly (0.1 mm off over 40 mm on a
    drafted boss: its top and bottom outlines then map to two curves twice
    the tolerance apart); the corners lie on the surface exactly."""
    from scipy.optimize import minimize

    X = np.asarray(X, float)
    span = max(float(np.linalg.norm(X - X.mean(0), axis=1).max()), 1e-9)
    twisted = abs(mot.twist) > 0

    def make(p):
        m = copy_motion(mot)
        m.a = mot.a + span * p[:3]
        if twisted:
            m.twist = mot.twist + p[3]
        return m

    def cost(p):
        m = make(p)
        if ((X - m.a) @ m.d).min() <= 0:
            return np.inf
        m.z0 = float(((X - m.a) @ m.d).mean())
        return _thickness(m.to_profile(X)[0])

    p0 = np.zeros(4 if twisted else 3)
    res = minimize(cost, p0, method="Nelder-Mead",
                   options={"xatol": 1e-6, "fatol": 1e-12, "maxiter": 400,
                            "initial_simplex": np.vstack([p0, p0 + 0.01 * np.eye(len(p0))])})
    if not np.isfinite(res.fun) or res.fun >= cost(p0):
        return mot
    return set_phi0(make(res.x), X)


def _is_loop(O, Q, tol):
    """Whether ordered points O (of all points Q) go all the way round: the
    path through them ends near where it starts, and points fill the gap
    between its ends (a C-shaped outline's ends are near, but nothing lies
    between them)."""
    from scipy.spatial import cKDTree

    if len(O) < 12:
        return False
    step = np.linalg.norm(np.diff(O, axis=0), axis=1)
    gap = float(np.linalg.norm(O[-1] - O[0]))
    if gap > 0.1 * step.sum():
        return False
    S = O[-1] + np.linspace(0.0, 1.0, 9)[:, None] * (O[0] - O[-1])
    d, _ = cKDTree(Q).query(S)
    # (no farther from the points than the path's own longest step)
    return bool(d.max() <= step.max() + tol)


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
    R = np.zeros((len(first), Q.shape[1]))
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
    ncomp, comp = connected_components(T, directed=False)
    if ncomp > 1:
        # dense clumps (a mesh's vertices bunch where its edges are short)
        # have all their near neighbours inside the clump: join the pieces
        # through their nearest points
        if ncomp > 50:
            return None
        while ncomp > 1:
            # (each piece to its nearest other piece, until one is left)
            rows2, cols2, w2 = [], [], []
            for c in range(ncomp):
                a_, b_ = np.nonzero(comp == c)[0], np.nonzero(comp != c)[0]
                dd, jj = cKDTree(R[b_]).query(R[a_])
                i = int(np.argmin(dd))
                rows2.append(a_[i])
                cols2.append(b_[jj[i]])
                w2.append(dd[i] + 1e-12)
            G = (G + coo_matrix((w2, (rows2, cols2)), shape=(n, n))).tocoo()
            T = minimum_spanning_tree(G)
            T = T + T.T
            ncomp, comp = connected_components(T, directed=False)
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


def _on_polyline(tree, S, ts, Q, closed):
    """(distance, parameter) of the nearest point of the dense polyline S
    (parameters ts) to each point Q: on the segments either side of the
    nearest sample, not just the sample (its spacing alone made a cam's
    side look 1.3 tolerances off its outline when it was 0.86)."""
    Q = np.asarray(Q, float)
    _, k = tree.query(Q)
    n = len(S)
    best_d = np.full(len(Q), np.inf)
    best_t = ts[k].astype(float)
    for j0 in (k - 1, k):
        j1 = j0 + 1
        if closed:
            j0, j1 = j0 % n, j1 % n
            ok = np.ones(len(Q), dtype=bool)
        else:
            ok = (j0 >= 0) & (j1 < n)
            j0, j1 = np.clip(j0, 0, n - 1), np.clip(j1, 0, n - 1)
        A, B = S[j0], S[j1]
        AB = B - A
        L2 = np.maximum((AB * AB).sum(1), 1e-300)
        u = np.clip(((Q - A) * AB).sum(1) / L2, 0.0, 1.0)
        d = np.linalg.norm(Q - (A + u[:, None] * AB), axis=1)
        t1 = ts[j1] + (1.0 if closed else 0.0) * (j1 < j0)  # (across the loop's start)
        tt = ts[j0] + u * (t1 - ts[j0])
        better = ok & (d < best_d)
        best_d[better], best_t[better] = d[better], tt[better]
    return best_d, best_t


class ProfileCurve:
    """A smooth curve (cubic B-spline by chord length; 2D for a profile, 3D
    for a fillet's spine) through ordered points, with a dense polyline for
    distances. A closed one (the outline
    of a part pulled up or tapered all the way round) is periodic: its
    parameter goes round once over 0..1 and on past it."""

    def __init__(self, P, tol, closed=False):
        from scipy.interpolate import make_splprep

        P = np.asarray(P, float)
        self.tol = tol
        self.closed = closed
        self.dim = P.shape[1]
        if closed:
            from scipy.interpolate import splprep

            seg = np.linalg.norm(np.diff(np.vstack([P, P[:1]]), axis=0), axis=1)
            t = np.r_[0.0, np.cumsum(seg[:-1])] / seg.sum()
            self.tck, _ = splprep(np.vstack([P, P[:1]]).T, u=np.r_[t, 1.0], k=3, per=1,
                                  s=len(P) * (0.25 * tol) ** 2)
            self._finish(len(P))
            return
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
        self._finish(len(P))

    def _finish(self, n):
        from scipy.spatial import cKDTree

        self.ts = np.linspace(0.0, 1.0, max(200, 8 * n), endpoint=not self.closed)
        self.S = self.at(self.ts)
        self.tree = cKDTree(self.S)

    def at(self, t):
        if self.closed:
            from scipy.interpolate import splev

            return np.array(splev(np.mod(np.asarray(t, float), 1.0), self.tck)).T.reshape(-1, self.dim)
        # (beyond 0..1 the end spans are continued)
        return np.asarray(self.spl(np.asarray(t, float), extrapolate=True)).T.reshape(-1, self.dim)

    def nearest(self, Q):
        """(distance, curve parameter) of the nearest curve point."""
        return _on_polyline(self.tree, self.S, self.ts, Q, self.closed)


def samples_for(curve, s_range, deg=4.0, most=600):
    """How many evenly spaced samples along a curve's parameter range keep
    the turn between neighbours under deg degrees where it bends most (a
    cam's fillet spine bends round a 0.5 mm radius at its tip: sampled by
    its total turn, 34 degrees fell between two samples there and the face
    stood 3.4 tolerances off the mesh). At most ``most``: fitting the
    B-spline takes 40 s at 1500 and 4 s at 600, and 600 still leaves under
    8 degrees at that tip, where the face then follows the mesh to half a
    tolerance."""
    ss = np.linspace(*s_range, 4001)
    d = np.diff(curve.at(ss), axis=0)
    d /= np.maximum(np.linalg.norm(d, axis=1), 1e-300)[:, None]
    turn = np.arccos(np.clip((d[1:] * d[:-1]).sum(1), -1.0, 1.0))
    # (the sharpest bend, over a few steps so one odd step doesn't decide)
    k = 5
    rate = np.convolve(turn, np.ones(k) / k, mode="valid").max() * 4000
    return int(np.clip(np.degrees(rate) / deg + 24, 24, most))


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
        self.twin = None
        # the profile over the surface's whole range (a little past the
        # patch's ends), densely, for nearest points
        self.ss = np.linspace(s_range[0], s_range[1], max(400, 2 * len(curve.ts)))
        self.SS = curve.at(self.ss)
        self.tree = cKDTree(self.SS)

    def _nearest(self, Q):
        return _on_polyline(self.tree, self.SS, self.ss, Q, False)

    def X(self, s, t):
        return self.motion.from_profile(self.curve.at(s), t)

    def foot(self, P, iters=6):
        """(s, t) of the surface point nearest each point: the profile point
        nearest in the profile's plane to start, then Gauss-Newton on the
        3D distance (for a helix the plane cuts a flank facing round the
        axis at a slant, so the start can be well off)."""
        P = np.atleast_2d(np.asarray(P, float))
        Q, t = self.motion.to_profile(P)
        _, s = self._nearest(np.nan_to_num(Q))
        if self.motion.kind not in ("helix", "taper"):
            return s, t
        t = np.nan_to_num(t)
        S_, T_ = self.s_range[1] - self.s_range[0], self.t_range[1] - self.t_range[0]
        hs, ht = 1e-5 * max(S_, 1e-9), 1e-5 * max(T_, 1e-9)
        X = self.X(s, t)
        err = np.linalg.norm(P - X, axis=1)
        for _ in range(iters):
            Js = (self.X(s + hs, t) - X) / hs
            Jt = (self.X(s, t + ht) - X) / ht
            r = P - X
            a, b, c = (Js * Js).sum(1), (Js * Jt).sum(1), (Jt * Jt).sum(1)
            gs, gt = (Js * r).sum(1), (Jt * r).sum(1)
            det = a * c - b * b
            ok = np.abs(det) > 1e-30
            # small steps only, kept only where they bring the point nearer
            # (on a wavy profile, such as a gear's teeth, a full step can
            # jump to another tooth and run away)
            ds = np.clip(np.where(ok, (c * gs - b * gt) / np.where(ok, det, 1), 0.0), -0.05 * S_, 0.05 * S_)
            dt = np.clip(np.where(ok, (a * gt - b * gs) / np.where(ok, det, 1), 0.0), -0.2 * T_, 0.2 * T_)
            s2, t2 = s + ds, t + dt
            X2 = self.X(s2, t2)
            e2 = np.linalg.norm(P - X2, axis=1)
            better = np.isfinite(e2) & (e2 < err)
            if not better.any():
                break
            s, t = np.where(better, s2, s), np.where(better, t2, t)
            X[better], err[better] = X2[better], e2[better]
            if np.abs(ds[better]).max() < 1e-9 and np.abs(dt[better]).max() < 1e-12:
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
        if self.motion.kind not in ("helix", "taper"):
            return self._nearest(self.motion.to_profile(P)[0])[0]  # exact
        s, t = self.foot(P)
        return np.linalg.norm(P - self.X(s, t), axis=1)

    def normal(self, P):
        P = np.atleast_2d(P)
        s, t = self.foot(P)
        return self._normal_at(s, t)

    def _normal_at(self, s, t):
        h = 1e-4
        lo, hi = (-np.inf, np.inf) if self.curve.closed else (0, 1)
        a = self.motion.from_profile(self.curve.at(np.clip(s - h, lo, hi)), t)
        b = self.motion.from_profile(self.curve.at(np.clip(s + h, lo, hi)), t)
        dt = 1e-4 * (1.0 if self.motion.kind != "extrusion" else max(self.tol, 1e-3) * 100)
        c = self.motion.from_profile(self.curve.at(s), t + dt)
        e = self.motion.from_profile(self.curve.at(s), t - dt)
        n = np.cross(b - a, c - e)
        return n / np.maximum(np.linalg.norm(n, axis=1), 1e-30)[:, None]

    def halves(self, margin=0.05):
        """A surface whose profile goes all the way round, as two surfaces
        of half the profile each (the same curve and motion, a little past
        their ends): a face can't run all the way round one of them, and
        cut in two each half fits one. The cuts between them lie on both
        exactly (see twin)."""
        (t0, t1) = self.t_range
        dt = max(t1 - t0, 1e-9)
        t_range = (t0 - margin * dt, t1 + margin * dt)
        o = self.seam()
        a = SweptSurface(self.motion, self.curve, (o - margin, o + 0.5 + margin), t_range, self.tol)
        b = SweptSurface(self.motion, self.curve, (o + 0.5 - margin, o + 1.0 + margin), t_range, self.tol)
        a.dev = b.dev = self.dev
        a.twin, b.twin = b, a
        return a, b

    def seam(self):
        """Where to cut a closed profile in two (at o and o + 1/2): where
        it bends least, so neither cut lands on a sharp bend (at a cam's
        tip, a fillet's spine bends so tightly that the nearest point on it
        is in doubt, and a cut there made both halves invalid)."""
        if getattr(self, "_seam", None) is None:
            ss = np.linspace(0.0, 1.0, 801)
            d = np.diff(self.curve.at(ss), axis=0)
            d /= np.maximum(np.linalg.norm(d, axis=1), 1e-300)[:, None]
            turn = np.arccos(np.clip((np.roll(d, -1, axis=0) * d).sum(1), -1.0, 1.0))
            # (both cuts count: the worse of the two bends)
            both = np.maximum(turn, np.roll(turn, -400))
            self._seam = float(ss[int(np.argmin(both[:400]))])
        return self._seam

    def half_of(self, P):
        """For a closed surface: whether points fall in the first half."""
        return np.mod(self.foot(P)[0] - self.seam(), 1.0) < 0.5

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

            # along the sweep: by the turn round the axis, 4 degrees between
            # samples (24 samples fixed left 12 degrees on a mount's turned
            # face, which sagged 0.1 mm between them); an extrusion is
            # straight, a taper without twist straight along its lines (but
            # growing exponentially along them)
            turn = np.degrees(self.motion.turns(self.t_range[1] - self.t_range[0]))
            nv = int(np.clip(turn / 4.0 + 4, 8, 200))
            if self.motion.kind == "extrusion":
                nv = 4
            elif self.motion.kind == "taper" and abs(self.motion.twist) < 1e-3:
                nv = 8
            # samples along the profile by how sharply it bends (see
            # samples_for), and cubic pieces only: a gear's teeth sampled 80
            # times and fitted with up to degree 8 swung kilometres off its
            # 60 mm
            G = self.grid(nu=samples_for(self.curve, self.s_range), nv=nv)
            arr = TColgp_Array2OfPnt(1, G.shape[0], 1, G.shape[1])
            for i in range(G.shape[0]):
                for j in range(G.shape[1]):
                    arr.SetValue(i + 1, j + 1, gp_Pnt(*map(float, G[i, j])))
            # evenly spaced parameters: u and v are the profile position and
            # the sweep, scaled to 0..1 (see uv), so edges can be placed on
            # the face exactly instead of by projection (which went astray
            # on twisted flanks, and left the faces invalid)
            ap = GeomAPI_PointsToBSplineSurface(arr, Approx_IsoParametric, 3, 3, GeomAbs_C2, max(0.05 * self.tol, 1e-7))
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
    if mot.kind == "taper" and X is not None:
        mot = refine_taper(mot, X)
    Q, t = mot.to_profile(P if X is None else X)
    if not (np.isfinite(Q).all() and np.isfinite(t).all()):
        return None, np.inf
    O = order_curve(Q, 0.25 * tol)
    if O is None:
        return None, np.inf
    closed = _is_loop(O, Q, tol)
    try:
        curve = ProfileCurve(O, tol, closed=closed)
    except Exception:
        return None, np.inf
    d, s = curve.nearest(Q)
    dev = float(np.percentile(d, 99)) if len(d) else np.inf
    if closed:
        # all the way round: see SweptSurface.halves
        surf = SweptSurface(mot, curve, (0.0, 1.0), (t.min(), t.max()), tol)
        surf.dev = dev
        return surf, dev
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
