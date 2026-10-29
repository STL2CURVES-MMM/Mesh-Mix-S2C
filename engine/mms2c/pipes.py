"""
Fillets along curved edges, as pipes.

A constant-radius fillet is the surface a ball sweeps as it rolls along an edge touching
both faces: a tube round the path of the ball's centre (its spine). Along a straight edge
that is a cylinder, along a circular one a torus. Along any other edge (a flat face
meeting a tilted cylinder, whose spine is then an ellipse, or a spline-shaped outline) no
simple surface fits, and detection covers the fillet with narrow pieces of tori,
cylinders and spheres of the fillet's radius, each fitting a short stretch and each
centred somewhere else, with freeform pieces and blends between them. Here such a chain
becomes one tube round a fitted spine: a cubic B-spline through the ball centres (read
off the pieces' own surfaces), refined until the tube passes within FIT_DEV of every mesh
corner (minimising the corners' distances to the tube, each corner's parameter corrected
to its foot point on the spine between steps: Hoschek, "Intrinsic parametrization for
approximation", CAGD 1988). The face is a B-spline surface sampled from the tube, its
cross-sections laid out in a rotation-minimising frame (Wang, Juettler, Zheng and Liu,
"Computation of rotation minimizing frames", ACM TOG 2008).
"""
import functools
import math

import numpy as np
import scipy.sparse as sp
from scipy.interpolate import BSpline
from scipy.sparse.csgraph import connected_components, minimum_spanning_tree, shortest_path
from scipy.spatial import cKDTree

from . import freeform
from .accuracy import scale
from .features import Feature, Revolved, Sphere, _right_vectors

FIT_DEV = 0.002 * scale("pipes")  # mm: the tube passes this close to every mesh corner of its area
MAX_RADIUS = 10.0               # mm: fillets up to this radius
CHAIN_PIECES = 5                # a pipe is at most this many cylinders and tori in a row ...
CHAIN_REACH = 1.0               # ... each reaching this many radii along its spine
LONG_STRAIGHT = 5.0             # radii: a cylinder piece this long is a straight fillet, never a pipe's
REAL_SIDE_SHARE = 0.25          # a face beside a piece counts as its side if it shares this share of the main side's edges
SAME_RADIUS = 0.02              # pieces whose radii differ by less than this share are one fillet's
SAME_SPINE = 0.01               # mm: pieces whose spines lie this close are one surface's
MIN_SPAN = 0.5                  # mm: spine knots no closer than this
MARGIN = 0.05                   # share of the spine's length (plus MARGIN_MM) the tube reaches past its corners
MARGIN_MM = 0.5
ANGLE_MARGIN = math.radians(10)     # ...and how much further round it reaches
ENDS = 0.01                     # radii: a point further than this past a spine end isn't over the tube
SMOOTHING = 1e-6                # penalty weight on the spine's bending: only settles it where corners are sparse
EVEN_SPEED = 0.01               # ...and (relative to that) on its speed changing
MIN_FACETS = 100                # a pipe joining no two pieces must take in at least this many facets
ORDER_LINKS = 6                 # ball centres are ordered along the spine through their nearest this many
SEED_HELPERS = 3                # a piece too small to fit a tube alone tries this many neighbours with it
NEAR = 0.3                      # radii: a piece this far off a chain's tube so far isn't tried with it
GROW_COS = math.cos(math.radians(10))   # a facet a pipe takes in faces within 10 deg of it


class Pipe:
    """A tube of radius r round the spine c(s), a cubic B-spline (knots t, control points
    C, s roughly arc length). signed(p): the distance from the spine less r (positive away
    from it), normal(p) with it."""
    kind = "pipe"
    line = circle = None

    def __init__(self, t, C, r):
        self.t, self.C, self.r = np.asarray(t, float), np.asarray(C, float), float(r)
        self.rim_dev = 0.0      # how far the outline's corners are off the surface (mm)
        self.dev = 0.0          # ...and the farthest corner
        self.n0 = None          # the frame's first normal: the corners' angles centre on 0
        self.angles = (-math.pi, math.pi)   # how far round the spine the corners reach
        self.reach = (self.lo, self.hi)     # ...and how far along it
        self._cache = self._surface = None

    def __getstate__(self):
        state = dict(self.__dict__)
        state["_cache"] = state["_surface"] = None      # (kd-tree and OpenCascade objects)
        return state

    @property
    def lo(self):
        return float(self.t[3])

    @property
    def hi(self):
        return float(self.t[-4])

    def _spline(self):
        if self._cache is None:
            c = BSpline(self.t, self.C, 3)
            n = int(min(20000, max(200, math.ceil((self.hi - self.lo) / (self.r / 8)))))
            s = np.linspace(self.lo, self.hi, n)
            self._cache = {"c": c, "c1": c.derivative(), "c2": c.derivative(2), "s": s, "tree": cKDTree(c(s))}
        return self._cache

    def spine(self, s, der=0):
        return self._spline()[("c", "c1", "c2")[der]](np.asarray(s, float))

    def foot(self, P, s=None):
        """Each point's foot-point parameter on the spine (Newton steps from the nearest
        of a dense row of spine points, or from s)."""
        cache = self._spline()
        P = np.atleast_2d(np.asarray(P, float))
        if s is None:
            s = cache["s"][cache["tree"].query(P)[1]]
        return _foot(cache["c"], cache["c1"], cache["c2"], P, s, self.lo, self.hi)

    def _offset(self, P):
        P = np.atleast_2d(np.asarray(P, float))
        V = P - self.spine(self.foot(P))
        return V, np.linalg.norm(V, axis=1)

    def signed(self, P):
        return self._offset(P)[1] - self.r

    def inside(self, P):
        """Whether each point lies across the tube from its spine within the spine's domain
        (not past either end, where signed() measures to the end's ball) and within the
        angles surface() covers: the surface a face is built on (merged engine: see
        merged.OurSurface)."""
        P = np.atleast_2d(np.asarray(P, float))
        s = self.foot(P)
        T = self.spine(s, 1)
        T = T / np.maximum(np.linalg.norm(T, axis=1), 1e-12)[:, None]
        along = np.einsum("ij,ij->i", P - self.spine(s), T)
        a = self.angle(P, s)
        return ((np.abs(along) <= ENDS * self.r) & (a >= self.angles[0] - ANGLE_MARGIN)
                & (a <= self.angles[1] + ANGLE_MARGIN))

    def normal(self, P):
        V, d = self._offset(P)
        return V / np.maximum(d, 1e-12)[:, None]

    def frames(self, s):
        """Unit tangent, normal and binormal at the parameters s: a rotation-minimising
        frame along the spine from n0 (double reflection over a dense row of spine
        points, interpolated between them)."""
        cache = self._spline()
        if "N" not in cache:
            S = cache["s"]
            X, T = cache["c"](S), cache["c1"](S)
            T = T / np.linalg.norm(T, axis=1)[:, None]
            N = np.zeros_like(X)
            n = self.n0 if self.n0 is not None else _square(T[0])
            N[0] = _unit(n - (n @ T[0]) * T[0])
            for i in range(len(S) - 1):
                v1 = X[i + 1] - X[i]
                c1 = v1 @ v1
                if c1 < 1e-24:
                    N[i + 1] = N[i]
                    continue
                rl = N[i] - (2 / c1) * (v1 @ N[i]) * v1
                tl = T[i] - (2 / c1) * (v1 @ T[i]) * v1
                v2 = T[i + 1] - tl
                c2 = v2 @ v2
                N[i + 1] = rl - (2 / c2) * (v2 @ rl) * v2 if c2 > 1e-24 else rl
            cache["N"] = N
        S, N = cache["s"], cache["N"]
        s = np.clip(np.asarray(s, float), S[0], S[-1])
        i = np.clip(np.searchsorted(S, s) - 1, 0, len(S) - 2)
        w = ((s - S[i]) / (S[i + 1] - S[i]))[:, None]
        T = cache["c1"](s)
        T = T / np.linalg.norm(T, axis=1)[:, None]
        n = (1 - w) * N[i] + w * N[i + 1]
        n = n - np.einsum("ij,ij->i", n, T)[:, None] * T
        n = n / np.linalg.norm(n, axis=1)[:, None]
        return T, n, np.cross(T, n)

    def angle(self, P, s=None):
        """How far round the spine each point is from the frame's normal (radians)."""
        P = np.atleast_2d(np.asarray(P, float))
        s = self.foot(P) if s is None else s
        V = P - self.spine(s)
        _, N, B = self.frames(s)
        return np.arctan2(np.einsum("ij,ij->i", V, B), np.einsum("ij,ij->i", V, N))

    def point(self, s, a):
        s, a = np.asarray(s, float), np.asarray(a, float)
        _, N, B = self.frames(s)
        return self.spine(s) + self.r * (np.cos(a)[:, None] * N + np.sin(a)[:, None] * B)

    def surface(self):
        """The tube over the spine's whole domain and the corners' angles (plus a margin)
        as an OpenCascade B-spline surface, approximated within 1e-5 mm."""
        if self._surface is None:
            self._surface = _sampled_surface(self)
        return self._surface

    def face(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        surface = self.surface()
        if surface is None:
            return None
        u0, u1, v0, v1 = surface.Bounds()
        maker = BRepBuilderAPI_MakeFace(surface, u0, u1, v0, v1, 1e-7)
        return maker.Face() if maker.IsDone() else None


def _unit(v):
    return v / np.linalg.norm(v)


def _square(t):
    """Some unit vector square to t."""
    e = np.eye(3)[int(np.argmin(np.abs(t)))]
    return _unit(e - (e @ t) * t)


def _foot(c, c1, c2, P, s, lo, hi, steps=5):
    for _ in range(steps):
        V = c(s) - P
        d1, d2 = c1(s), c2(s)
        f = np.einsum("ij,ij->i", V, d1)
        g = np.einsum("ij,ij->i", d1, d1)
        fp = np.maximum(g + np.einsum("ij,ij->i", V, d2), 0.1 * g)
        s = np.clip(s - f / np.maximum(fp, 1e-300), lo, hi)
    return s


def _knots(lo, hi, n, k=3):
    return np.r_[[lo] * k, np.linspace(lo, hi, n + 1), [hi] * k]


@functools.lru_cache(maxsize=256)
def _penalty(m):
    """Third differences of the control points (the bending changing: where corners are
    sparse, and past the last corners, the spine keeps curving as it did), and weakly
    second differences (the control points' spacing changing: past the last corners
    nothing else keeps the spine's speed even). One coordinate's: they don't mix."""
    out = np.zeros((m, m))
    D = np.eye(m)
    for order in range(1, min(3, m - 1) + 1):
        D = D[1:] - D[:-1]
        if order >= 2:
            out += (1.0 if order == 3 else EVEN_SPEED) * (D.T @ D)
    out.flags.writeable = False
    return out


def _through(s, Q, t):
    """Control points of the spline (knots t) passing closest to the points Q at s
    (each coordinate on its own: a small dense system, solved for all three at once)."""
    m = len(t) - 4
    B = BSpline.design_matrix(np.clip(s, t[3], t[-4]), t, 3).tocsr()
    A = (B.T @ B).toarray() + SMOOTHING * max(len(s) / m, 1.0) * _penalty(m) + 1e-12 * np.eye(m)
    return np.linalg.solve(A, B.T @ Q)


def _refine(P, s, t, C, r, steps=40):
    """The control points moved so the tube of radius r passes closest to the points P:
    each step finds every point's foot on the spine and the ball centre a radius in from
    it towards the spine, and fits the spline through those centres at those feet (point
    distance minimisation: steady, each step lowering the summed squared distances; a
    Newton step on the distances alone let the spine slide along itself and run away).
    (C, s, each point's distance off the tube)."""
    lo, hi = t[3], t[-4]
    for _ in range(steps):
        c = BSpline(t, C, 3)
        s = _foot(c, c.derivative(), c.derivative(2), P, s, lo, hi)
        V = P - c(s)
        d = np.maximum(np.linalg.norm(V, axis=1), 1e-12)
        new = _through(s, P - (r / d)[:, None] * V, t)
        moved = float(np.abs(new - C).max())
        C = new
        if moved < 1e-5:
            break
    c = BSpline(t, C, 3)
    s = _foot(c, c.derivative(), c.derivative(2), P, s, lo, hi)
    return C, s, np.linalg.norm(P - c(s), axis=1) - r


def _order(Q, h):
    """A parameter for each of the points Q, which lie along one open curve: arc length
    along the longest path through a spanning tree of them (smoothed); None if they
    don't form one open curve (a branch, a closed loop). The tree links each point to
    its nearest few whatever the distance: on a coarse mesh the corners lie far apart
    along a fillet compared with its radius."""
    keys = np.floor(Q / h).astype(np.int64)
    _, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.ravel()
    n = int(inv.max()) + 1
    R = np.zeros((n, 3))
    np.add.at(R, inv, Q)
    R /= np.bincount(inv, minlength=n)[:, None]
    if n < 2:
        return None
    k = min(ORDER_LINKS, n - 1)
    dist, idx = cKDTree(R).query(R, k + 1)
    rows = np.repeat(np.arange(n), k)
    w = dist[:, 1:].ravel() + 1e-9
    tree = minimum_spanning_tree(sp.csr_matrix((w, (rows, idx[:, 1:].ravel())), shape=(n, n)))
    if connected_components(tree, directed=False)[0] > 1:
        return None
    a = int(np.argmax(shortest_path(tree, directed=False, indices=0)))
    dist, pred = shortest_path(tree, directed=False, indices=a, return_predecessors=True)
    path = [int(np.argmax(dist))]
    while path[-1] != a:
        path.append(int(pred[path[-1]]))
    X = R[path]
    for _ in range(3):
        X[1:-1] = (X[:-2] + 2 * X[1:-1] + X[2:]) / 4
    step = np.linalg.norm(np.diff(X, axis=0), axis=1)
    length = float(step.sum())
    if length < 4 * h or np.linalg.norm(X[-1] - X[0]) < 0.2 * length:
        return None         # (too short, or curling round into a loop)
    cum = np.r_[0.0, np.cumsum(step)]
    # each point's place along the path: its foot on the nearest of the segments
    near = cKDTree(X).query(Q)[1]
    best_d = np.full(len(Q), np.inf)
    best_s = np.zeros(len(Q))
    for j in (near - 1, near):
        j = np.clip(j, 0, len(X) - 2)
        A, AB = X[j], X[j + 1] - X[j]
        f = np.clip(np.einsum("ij,ij->i", Q - A, AB) / np.maximum(np.einsum("ij,ij->i", AB, AB), 1e-24), 0, 1)
        dd = np.linalg.norm(Q - A - f[:, None] * AB, axis=1)
        better = dd < best_d
        best_d[better], best_s[better] = dd[better], (cum[j] + f * step[j])[better]
    if best_d.max() > max(4 * h, float(np.median(step))):
        return None         # (a branch off the path: not one curve)
    return best_s


def fit(mesh, facets, r, guess):
    """A Pipe of radius r through the facets' corners, or (None, None). guess(V): a first
    guess at the ball centre for each corner (mesh vertex ids V). Also returns how far it
    strays at most from the corners."""
    facets = np.asarray(facets)
    tids = np.concatenate([mesh.ftris[f] for f in facets])
    T = mesh.tris[tids]
    V, local = np.unique(T, return_inverse=True)
    local = local.reshape(-1, 3)
    P = mesh.pts[V]
    Q = guess(V)
    s = _order(Q, max(r / 4, 0.05))
    if s is None:
        return None, None
    edges = np.sort(np.concatenate([local[:, [0, 1]], local[:, [1, 2]], local[:, [2, 0]]]), axis=1)
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    rim = np.zeros(len(V), bool)
    rim[uniq[counts == 1].ravel()] = True
    length = float(np.ptp(s))
    margin = MARGIN * length + MARGIN_MM
    lo, hi = float(s.min()) - margin, float(s.max()) + margin
    # (no more knots than one for every four corners, nor closer than MIN_SPAN)
    cap = max(1, min(math.ceil((hi - lo) / MIN_SPAN), len(V) // 4))
    span = (hi - lo) / 4
    while True:
        n = int(min(cap, max(1, math.ceil((hi - lo) / span))))
        for _ in range(3):
            t = _knots(lo, hi, n)
            C = _through(s, Q, t)
            C, s_fit, gap = _refine(P, s, t, C, r)
            # (the spine can slide along itself as it settles, the corners' feet with
            # it: the domain is laid round where they end up, the tube reaching past
            # them at both ends)
            if s_fit.min() - lo >= margin / 2 and hi - s_fit.max() >= margin / 2:
                break
            s = s_fit
            lo, hi = float(s.min()) - margin, float(s.max()) + margin
        dev = float(np.abs(gap).max())
        model = Pipe(t, C, r)
        room = s_fit.min() - t[3] >= margin / 4 and t[-4] - s_fit.max() >= margin / 4
        if dev <= FIT_DEV and room and _settle_frame(model, P, s_fit) \
                and freeform._hugs(model, mesh, tids, T, local, P):
            model.reach = (float(s_fit.min()), float(s_fit.max()))
            model.dev = dev
            model.rim_dev = float(np.abs(gap[rim]).max()) if rim.any() else dev
            return model, dev
        if n >= cap:
            return None, None
        span /= 2


def _settle_frame(model, P, s):
    """Turn the frame so the corners' angles centre on 0, and note how far round they
    reach; False if they go most of the way round (not a fillet)."""
    T0 = _unit(model.spine(np.array([model.lo]), 1)[0])
    model.n0 = _square(T0)
    model._cache = None
    a = model.angle(P, s)
    mid = math.atan2(float(np.sin(a).mean()), float(np.cos(a).mean()))
    _, N, B = model.frames(np.array([model.lo]))
    model.n0 = math.cos(mid) * N[0] + math.sin(mid) * B[0]
    model._cache = None
    a = model.angle(P, s)
    if np.ptp(a) > math.radians(300):
        return False
    model.angles = (float(a.min()), float(a.max()))
    return True


def _sampled_surface(model):
    r = model.r
    a0, a1 = model.angles[0] - ANGLE_MARGIN, model.angles[1] + ANGLE_MARGIN
    # (cross-sections evenly spaced along the spine, not in its parameter)
    fine = np.linspace(model.lo, model.hi, 4001)
    run = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(model.spine(fine), axis=0), axis=1))]
    S = np.interp(np.linspace(0, run[-1], max(9, math.ceil(run[-1] / min(r / 3, 0.5)) + 1)), run, fine)
    A = np.linspace(a0, a1, max(9, math.ceil((a1 - a0) / math.radians(7.5)) + 1))
    return freeform.approximate(model.point, S, A)


def tube_radius(model):
    """The radius of the tube a piece's surface is (a cylinder's, a torus's tube's, a
    sphere's), or None."""
    if isinstance(model, Sphere):
        return model.r
    if isinstance(model, Revolved):
        if model.line:
            return model.line[0] if model.line[1] == 0 else None
        return model.circle[2]
    return None


def _same_spine(m1, m2):
    """Are the two pieces' surfaces one (same centre, axis, ring)?"""
    if isinstance(m1, Sphere) or isinstance(m2, Sphere):
        return isinstance(m1, Sphere) and isinstance(m2, Sphere) and np.linalg.norm(m1.c - m2.c) <= SAME_SPINE
    if bool(m1.line) != bool(m2.line) or np.linalg.norm(np.cross(m1.d, m2.d)) > 1e-3:
        return False
    if m1.line:
        gap = m2.a - m1.a
        return np.linalg.norm(gap - (gap @ m1.d) * m1.d) <= SAME_SPINE
    (rc1, zc1, _), (rc2, zc2, _) = m1.circle, m2.circle
    return abs(rc1 - rc2) <= SAME_SPINE and np.linalg.norm(m1.a + zc1 * m1.d - m2.a - zc2 * m2.d) <= SAME_SPINE


def _real(mesh, features, owner, k, mates):
    """Is piece k a real cylinder or torus fillet rather than one standing in for a
    stretch of a fillet along a curve? A real one lies between faces that agree with it:
    a cylinder's (the two it is widest against) run along its axis (planes along it,
    cylinders beside it), a torus's turn round its axis (planes square to it, cylinders,
    cones and tori round it). One standing in sits against a face tilted to it, or
    against facets nothing explains. (mates: the same-radius pieces, not counted.)"""
    m = features[k].model
    if isinstance(m, Sphere):
        return False
    count = {}
    for a in features[k].facets.tolist():
        for b in mesh.nbrs[a]:
            o = int(owner[b])
            if o == k or o in mates:
                continue
            key = o if o >= 0 else (-1 if len(mesh.ftris[b]) == 1 else -2 - b)
            count[key] = count.get(key, 0) + len(mesh.shared.get((min(a, b), max(a, b)), ()))
    sides = sorted(count, key=lambda x: -count[x])[:2]
    if not sides:
        return False
    # (a side is what runs along it, not a speck at its end: a box's 63 mm edge fillet
    # touched a few loose facets of the corner ball at one end, 3 edges against 24, and
    # went into a pipe with the corner's pieces)
    sides = [x for x in sides if count[x] >= REAL_SIDE_SHARE * count[sides[0]]]
    for key in sides:
        if key == -1:
            return False        # (loose curved facets: a surface nothing explained)
        if key <= -2:
            n = mesh.fn[-2 - key]
            if (abs(n @ m.d) < 0.9998) if m.circle else (abs(n @ m.d) > 0.02):
                return False
            continue
        m2 = features[key].model
        if not isinstance(m2, Revolved) or np.linalg.norm(np.cross(m.d, m2.d)) > 0.02:
            return False
        if m.circle:
            gap = m2.a - m.a
            if np.linalg.norm(gap - (gap @ m.d) * m.d) > 0.01 * max(m.circle[0], 1.0):
                return False
    return True


def _long_straight(mesh, f):
    """Is f a cylinder running LONG_STRAIGHT radii along its axis? A straight fillet: a
    cylinder fits a fillet along a curve only over a short stretch (bowing off it by
    L^2/8R, a micron over 0.6 mm round a 50 mm bend), so a long one is real whatever lies
    at its ends (a box's 62 mm bottom fillet, its sides judged by the many short edges
    to the corner pieces at its end, went into a pipe with them)."""
    m = f.model
    if not (isinstance(m, Revolved) and m.line and m.line[1] == 0):
        return False
    P = mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in f.facets]))]
    return float(np.ptp((P - m.a) @ m.d)) >= LONG_STRAIGHT * m.line[0]


def add_pipes(mesh, features):
    """The features with each chain of fillet pieces that one tube fits replaced by a
    pipe, which remembers the pieces (put back if its face can't be built)."""
    nf = len(mesh.farea)
    owner = np.full(nf, -1)
    for k, f in enumerate(features):
        owner[f.facets] = k
    radius = {}
    for k, f in enumerate(features):
        r = tube_radius(f.model)
        if r is not None and r <= MAX_RADIUS and f.kind in ("revolve", "trimmed"):
            radius[k] = r
    verts = {k: set(np.concatenate([mesh.fverts[g] for g in features[k].facets]).tolist()) for k in radius}
    touch = {k: set() for k in radius}
    by_vertex = {}
    for k in radius:
        for v in verts[k]:
            by_vertex.setdefault(v, []).append(k)
    for ks in by_vertex.values():
        for a in ks:
            for b in ks:
                if a != b and abs(radius[a] - radius[b]) <= SAME_RADIUS * radius[a]:
                    touch[a].add(b)
    # pieces of one surface together; a real cylinder or torus fillet stays
    surface = {k: k for k in radius}
    for a in sorted(radius):
        for b in touch[a]:
            if surface[b] == b and b > a and _same_spine(features[a].model, features[b].model):
                surface[b] = surface[a]
    stays = {k for k in radius if _real(mesh, features, owner, k, touch[k]) or _long_straight(mesh, features[k])}
    # chains grow from the pieces reaching furthest along their spines
    extent = {}
    for k in radius:
        if k not in stays:
            P = mesh.pts[sorted(verts[k])]
            Q = P - radius[k] * features[k].model.normal(P)
            extent[k] = float(np.linalg.norm(np.ptp(Q, axis=0)))
    out, used = [], set(stays)
    for seed in sorted(extent, key=lambda k: -extent[k]):
        if seed in used or extent[seed] < radius[seed]:
            continue
        got = _chain(mesh, features, owner, seed, radius, used)
        if got is None:
            continue
        facets, model, dev, parts = got
        pieces = [k for k in parts if k in radius]
        if len({surface[k] for k in pieces}) < 2 and len(facets) < max(MIN_FACETS, 1.5 * len(features[seed].facets)):
            continue        # (one surface's pieces and little else: nothing to join)
        # (nor if one of its pieces' own surfaces fits it all: a cylinder or torus with
        # stray pieces of itself, which is no fillet along a curve)
        P = mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in facets]))]
        if any(np.abs(features[k].model.signed(P)).max() <= FIT_DEV
               for k in sorted(pieces, key=lambda k: -len(features[k].facets))[:3]):
            continue
        used.update(parts)
        piped = feature(mesh, model, facets, tuple(features[k] for k in parts), dev)
        out += _exact_tube(mesh, piped) or [piped]
    mesh.__dict__.pop("_vn", None)      # (the mesh goes to every worker: no extra baggage)
    return [f for k, f in enumerate(features) if k not in used or k in stays] + out


def _exact_tube(mesh, piped):
    """The pipe as the cylinders and tori its tube is made of, if each holds every corner
    of its stretch within FIT_DEV, else None: [one feature] if one surface takes it all,
    else the pieces of a chain of straight runs and circular arcs.

    A fillet run along an edge loop is a chain of such pieces wherever the loop is
    lines and arcs (CAD rounds the edges in turn: a latch's r 0.495 fillet round a face
    whose corner an r 0.99 fillet had rounded is a cylinder, a torus whose centre circle
    is hardly bigger than its tube, and a cylinder). Detection cuts such a torus into
    narrow slices that each fit a small sphere, and the chain of them is joined here as
    a pipe; its spine then runs straight, round a circle and straight again. Only a few
    pieces, each a fair stretch of the spine: a spline-shaped edge also fits many short
    arcs, and stays one pipe."""
    m = piped.model
    facets = np.asarray(piped.facets)
    s = m.foot(mesh.fcent[facets])
    lo, hi = float(s.min()), float(s.max())
    n = int(min(400, max(24, math.ceil((hi - lo) / (m.r / 4)))))
    S = np.linspace(lo, hi, n)
    Q = m.spine(S)
    whole = _exact_fit(mesh, facets, m.r, Q, piped.convex)
    if whole is not None:
        whole.parts = (piped,)     # (if its face can't be built: the pipe again, then its pieces)
        return [whole]
    runs, i = [], 0
    while i < n - 1:
        j = i + 2
        while j < n and _spine_shape(Q[i:j + 1], m.r) is not None:
            j += 1
        j = max(i + 1, j - 1)
        runs.append((i, j))
        i = j
    if not 2 <= len(runs) <= CHAIN_PIECES or any(S[j] - S[i] < CHAIN_REACH * m.r for i, j in runs):
        return None
    # (each run its stretch's facets, by where their middles' feet fall; a run no surface
    # fits joins its neighbours that don't either, and that stretch stays a pipe, refitted
    # on its own from this one's spine: a straight run into a corner whose ball path bends
    # off a circle is a cylinder and a shorter pipe)
    out, rest = [], []
    for i, j in runs:
        mine = facets[(s >= S[i] - 1e-9) & (s <= S[j] + 1e-9) & ~np.isin(facets, [x for g in out for x in g.facets])]
        got = _exact_fit(mesh, mine, m.r, Q[i:j + 1], piped.convex) if len(mine) >= 3 else None
        if got is None:
            rest.append(mine)
        else:
            out.append(got)
    if not out:
        return None
    taken = np.concatenate([g.facets for g in out])
    left = facets[~np.isin(facets, taken)]
    if len(left):
        def guess(V):
            return m.spine(m.foot(mesh.pts[V]))
        for group in _connected(mesh, left):
            model, dev = fit(mesh, group, m.r, guess)
            if model is None:
                return None
            inside = set(group.tolist())
            out.append(feature(mesh, model, group, tuple(p for p in piped.parts if set(p.facets.tolist()) <= inside), dev))
    return out


def _connected(mesh, facets):
    """The facets in groups joined by shared edges."""
    left, groups = set(int(a) for a in facets), []
    while left:
        stack = [left.pop()]
        group = list(stack)
        while stack:
            a = stack.pop()
            for b in mesh.nbrs[a]:
                if b in left:
                    left.remove(b)
                    stack.append(b)
                    group.append(b)
        groups.append(np.array(sorted(group)))
    return groups


def _spine_shape(Q, r):
    """Whether spine points Q lie on a line ("line") or a circle ("circle") within
    FIT_DEV, else None."""
    c = Q.mean(axis=0)
    Vt = _right_vectors(Q - c)
    X = Q - c
    if np.linalg.norm(X - np.outer(X @ Vt[0], Vt[0]), axis=1).max() <= FIT_DEV:
        return "line"
    if np.abs(X @ Vt[2]).max() > FIT_DEV:
        return None
    x, y = X @ Vt[0], X @ Vt[1]
    (p, q, k), *_ = np.linalg.lstsq(np.c_[x, y, np.ones_like(x)], x * x + y * y, rcond=None)
    R = math.sqrt(max(k + p * p / 4 + q * q / 4, 0.0))
    if not 0 < R < 100 * r:
        return None
    return "circle" if np.abs(np.hypot(x - p / 2, y - q / 2) - R).max() <= FIT_DEV else None


def _exact_fit(mesh, facets, r, Q, convex):
    """The cylinder or torus (round the line or circle of spine points Q) holding every
    corner of the facets within FIT_DEV, refitted by least squares: a feature, or None."""
    from .features import _described
    from scipy.optimize import least_squares
    P = mesh.pts[np.unique(np.concatenate([mesh.fverts[a] for a in facets]))]
    c = Q.mean(axis=0)
    Vt = _right_vectors(Q - c)

    def direction(x):
        return np.array([math.sin(x[0]) * math.cos(x[1]), math.sin(x[0]) * math.sin(x[1]), math.cos(x[0])])

    def angles(d):
        return [math.acos(float(np.clip(d[2], -1, 1))), math.atan2(d[1], d[0])]

    tries = []
    # (a straight spine: a cylinder round it)
    def cylinder(x):
        return Revolved(x[:3], direction(x[3:5]), line=(float(x[5]), 0.0))
    tries.append((cylinder, np.r_[c, angles(Vt[0]), r]))
    # (a spine in a plane, on a circle: a torus round the plane's normal)
    x, y = (Q - c) @ Vt[0], (Q - c) @ Vt[1]
    (p, q, k), *_ = np.linalg.lstsq(np.c_[x, y, np.ones_like(x)], x * x + y * y, rcond=None)
    R = math.sqrt(max(k + p * p / 4 + q * q / 4, 0.0))
    # (a nearly straight spine is the cylinder's; and a fillet's ball can't roll round an
    # arc tighter than itself: a spindle torus, its centre circle smaller than its tube,
    # fitted a box corner's spline patch within 0.01 mm at the mesh corners and built 0.17
    # off the part between them)
    if r < R < 100 * r:
        centre = c + p / 2 * Vt[0] + q / 2 * Vt[1]

        def torus(x):
            d = direction(x[3:5])
            return Revolved(x[:3], d, circle=(float(x[5]), float(x[:3] @ d), float(x[6])))
        tries.append((torus, np.r_[centre, angles(Vt[2]), R, r]))
    for make, x0 in tries:
        try:
            x = least_squares(lambda x: make(x).signed(P), x0, x_scale="jac", max_nfev=200).x
        except (ValueError, np.linalg.LinAlgError):
            continue
        model = make(x)
        if np.abs(model.signed(P)).max() > FIT_DEV or (model.circle and model.circle[0] <= model.circle[2]):
            continue
        got = _described(mesh, model, np.asarray(facets), convex)
        if got is not None:
            return got
    return None


def _chain(mesh, features, owner, seed, radius, used):
    """(facets, pipe, deviation, pieces) grown from a seed piece: the loose facets and
    pieces beside it whose corners lie on its tube taken in, and the same-radius pieces
    near it each kept if one tube still fits them all (refitted, so the tube reaches on
    along the fillet), until nothing more joins; None if no tube fits the seed."""
    r = radius[seed]
    parts = [seed]
    members = set(features[seed].facets.tolist())
    model, dev = _fit_members(mesh, features, members, parts, r)
    if model is None:
        # (a piece too small to fit a tube alone, a few facets of a coarsely meshed
        # fillet: with its biggest same-radius neighbours, one by one)
        helpers = {int(owner[b]) for a in members for b in mesh.nbrs[a]}
        helpers = sorted((k for k in helpers if k >= 0 and k != seed and k in radius and k not in used
                          and abs(radius[k] - r) <= SAME_RADIUS * r), key=lambda k: -len(features[k].facets))
        for k in helpers[:SEED_HELPERS]:
            parts.append(k)
            members.update(features[k].facets.tolist())
            model, dev = _fit_members(mesh, features, members, parts, r)
            if model is not None:
                break
        if model is None:
            return None
    rejected = set()
    while True:
        near = {}
        for a in members:
            for b in mesh.nbrs[a]:
                if b in members:
                    continue
                k = int(owner[b])
                if k < 0:
                    if ("facet", b) not in rejected:
                        near[("facet", b)] = [b]
                elif k not in used and k not in rejected and k not in parts:
                    near[k] = features[k].facets.tolist()
        on, tries = [], []
        for key, unit in near.items():
            P = mesh.pts[np.unique(np.concatenate([mesh.fverts[x] for x in unit]))]
            off = np.abs(model.signed(P))
            facing = (np.einsum("ij,ij->i", mesh.fn[unit], model.normal(mesh.fcent[unit])) ** 2).min() >= GROW_COS ** 2
            if off.max() <= FIT_DEV and facing:
                on.append(key)
            elif not isinstance(key, tuple) and key in radius and abs(radius[key] - r) <= SAME_RADIUS * r \
                    and np.median(off) <= NEAR * r:
                # (its median corner: one reaching on past the tube's end strays from
                # where the tube would go, more the further it reaches)
                tries.append(key)
            # (anything else may lie on the tube once it reaches further: looked at again)
        if on:
            # (those reaching past the corners the tube was fitted to join only if it
            # refits with them, reaching on: the face is cut from the tube, which must
            # run on past its outline; each end on its own)
            ends = {0: [], 1: [], None: []}
            for key in on:
                s = model.foot(mesh.pts[np.unique(np.concatenate([mesh.fverts[x] for x in near[key]]))])
                ends[0 if s.min() < model.reach[0] else 1 if s.max() > model.reach[1] else None].append(key)
            for end in (None, 0, 1):
                keys = ends[end]
                if not keys:
                    continue
                pieces = [key for key in keys if not isinstance(key, tuple)]
                facets = {x for key in keys for x in near[key]}
                if end is not None:
                    refit, refit_dev = _fit_members(mesh, features, members | facets, parts + pieces, r)
                    if refit is None:
                        rejected.update(keys)
                        continue
                    model, dev = refit, refit_dev
                members.update(facets)
                parts += pieces
            continue
        if not tries:
            break
        k = max(tries, key=lambda x: len(near[x]))
        refit, refit_dev = _fit_members(mesh, features, members | set(near[k]), parts + [k], r)
        if refit is None:
            rejected.add(k)
            continue
        members.update(near[k])
        parts.append(k)
        model, dev = refit, refit_dev
    return np.array(sorted(members)), model, dev, sorted(parts)


def _fit_members(mesh, features, members, parts, r):
    return fit(mesh, np.array(sorted(members)), r, _guesser(mesh, features, parts, r))


def _guesser(mesh, features, chain, r):
    """Ball centres for mesh vertices: on a piece of the chain of the fillet's radius,
    from its own surface; on any other facet, a radius in from the vertex along its
    normal (in or out as the chain's pieces have it)."""
    centre = {}
    side = 0.0
    for k in chain:
        f = features[k]
        if abs((tube_radius(f.model) or 0.0) - r) > SAME_RADIUS * r:
            continue
        V = np.unique(np.concatenate([mesh.fverts[g] for g in f.facets]))
        P = mesh.pts[V]
        n = f.model.normal(P)
        for v, q in zip(V.tolist(), P - tube_radius(f.model) * n):
            centre.setdefault(v, q)
        side += float(np.einsum("ij,ij->i", mesh.fn[f.facets], f.model.normal(mesh.fcent[f.facets])) @ mesh.farea[f.facets])
    sign = 1.0 if side >= 0 else -1.0

    def guess(V):
        out = np.empty((len(V), 3))
        missing = []
        for i, v in enumerate(V.tolist()):
            q = centre.get(v)
            if q is None:
                missing.append(i)
            else:
                out[i] = q
        if missing:
            idx = np.array(missing)
            out[idx] = mesh.pts[V[idx]] - sign * r * _vertex_normals(mesh, V[idx])
        return out
    return guess


def _vertex_normals(mesh, V):
    """Area-weighted mean normal of the triangles round each vertex."""
    if not hasattr(mesh, "_vn"):
        vn = np.zeros_like(mesh.pts)
        for j in range(3):
            np.add.at(vn, mesh.tris[:, j], mesh.tn * mesh.tarea[:, None])
        mesh._vn = vn / np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]
    return mesh._vn[V]


def feature(mesh, model, facets, parts, dev):
    """The patch for a pipe (parts: the pieces it replaces, given back if its face can't
    be built). Its volume change is measured as for any exact surface, from how far each
    facet's edge midpoints sit off it."""
    tids = np.concatenate([mesh.ftris[f] for f in facets])
    corners = mesh.pts[mesh.tris[tids]]
    s = model.signed(((corners + corners[:, [1, 2, 0]]) / 2).reshape(-1, 3)).reshape(-1, 3)
    area = mesh.tarea[tids]
    outward = np.einsum("ij,ij->i", mesh.fn[facets], model.normal(mesh.fcent[facets])) @ mesh.farea[facets]
    convex = bool(outward > 0)
    change = (-1 if convex else 1) * float((area * s.mean(axis=1)).sum())
    gain = float((area * np.abs(s).mean(axis=1)).sum())
    return Feature(model, "rounded edge (swept)",
                   f"radius {model.r:.3f}, {len(facets)} facets, {len(model.t) - 7} spans, within {dev:.4f} mm of the mesh",
                   convex=convex, kind="trimmed", facets=np.asarray(facets), change=change,
                   tolerance=0.5 * gain + 1e-3, worst=max(float(np.abs(s).max()), dev), parts=tuple(parts))
