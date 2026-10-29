"""
Fillets between flat faces, and between a flat face and a curved wall, found the way a
designer sees them.

Two flat faces that would meet at a sharp edge, rounded off with a fillet: the fillet
is a cylinder touching both faces (a ball of the fillet radius rolled along the
edge). Its axis runs parallel to the edge where the faces would meet, so given the two
faces the only unknown is the radius, and every mesh corner on the rounded band
between them pins it down. (Constant-radius "rolling ball" blend recovery between
primary surfaces, as in reverse-engineering work by Várady, Benkő, Kós and Martin.)

Everything happens in a cross-section, where the ball is a circle rolling between two
curves (a configuration: _Lines, _LineCircle), and each band corner gives the radius
of the circle through it touching both, from a quadratic:

- two flat faces: two lines, seen across their edge; the fillet is a cylinder.
- a flat face square to the axis of a cone or cylinder found already (a floor round a
  boss or hole): two lines in the half plane through the axis; the fillet is a torus.
- a flat face beside a cylinder whose axis runs along it (a boss standing out of a
  wall, a groove cut into a face): a line and a circle, seen along the axis; the
  fillet is a cylinder. (|o - c| = R +- r and |o - q| = r, for the ball's centre o
  at height r off the line, differ by an equation linear in o and r.)

Fillets are everywhere in printed parts, and a mesh often keeps too little accuracy to
show them cleanly, so this is forgiving where the general surface passes can't be:

- The whole band between the two faces is collected first (facets turning steadily
  from one face's normal to the other's, lying between the faces), and the radius is
  the one most of its corners agree on, so a few stray corners don't spoil it.
- A band whose corners stay within FILLET_TOL of that fillet is taken: looser than the
  mesh's own tolerance, since the faces either side pin the surface down. The faces
  must run along the whole band (a narrow strip of a curved wall is no face for a
  rounded edge that follows the curve).
- One fillet radius is usually used all over a design. Bands that miss on their own are
  tried again with each radius found with confidence elsewhere in the part held fixed
  (a 4.99 or 4.9 mm look-alike next to clean 5 mm fillets is a 5 mm fillet), within
  PRIOR_TOL.

It runs before the passes that propose surfaces from pairs of neighbouring facets,
which would otherwise cut a coarsely meshed fillet into narrow strips of their own.
Finely cut pins aren't mistaken for fillets: the faces a fillet joins must be clearly
bigger than the facets of its band.
"""
import math

import numpy as np

from . import features as F
from .accuracy import scale
ACCURACY = scale("fillets")
from .features import Revolved

MIN_FACE_AREA = 2       # mm^2: flat facets at least this big can be the faces a fillet joins
MIN_ANGLE_DEG = 30      # the faces must meet at least this far from flat...
MAX_ANGLE_DEG = 170     # ...and not be (nearly) parallel
LINK_RINGS = 48         # a fillet's band is at most this many facets across (a finely cut r 3 fillet: 28)
FACE_TO_STRIP = 3       # each face a fillet joins is at least this many times its biggest band facet
FILLET_TOL = 0.02 * ACCURACY  # mm: how far most band corners may lie off the fillet found from them ...
GROW_TOL = 0.05 * ACCURACY  # mm: ... and its facets' corners at most (meshes lose accuracy at fillets)
REL_TOL = 0.04          # ... but neither more than this share of its radius
SPAN_SLACK_DEG = 8      # deg: a fillet may turn this much more than its faces do
CONFIDENT_TOL = 0.005   # mm: fillets fitting this well (or cylinders and tori found strictly)
                        # vouch for their radius elsewhere in the part
PRIOR_RANGE = 0.05      # a band whose own radius is within this share of a vouched one ...
PRIOR_TOL = 0.05        # mm: ... is that radius if its corners lie this close to it
FACE_LENGTH = 1.0       # a fillet, and the faces it joins alongside each other, run this many radii along it
FACE_WIDTH = 0.3        # each face it joins is this many radii wide across it
ROUGH_AGREE = 0.7       # (faces stopping short of their meeting line by distances agreeing within e^this)
WALL_SQUARE_DEG = 2     # deg: a flat face this close to square to a wall's axis can have a torus fillet to it
BEND_DEG = 5            # deg: a fillet beside a wall turns at least this much across its facets
COPLANAR_DEG = 0.25     # deg: flat facets this close in direction, and in one plane, are one face
GROW_FACTOR = 3         # its facets may lie this many times as far off as its middle does
ON_SHARE = 75           # percent of a band's corners that must lie within the tolerance
CORNER_AGREE = 0.2      # a band facet's corners give radii agreeing within e^this ...
WEDGE_SLACK = 0.01      # mm: how far outside the faces' wedge a band corner may lie
DEPTH_AGREE = 0.3       # ... and agreeing with the rest of the band's within e^this


def _adjacency(mesh):
    """Every facet's neighbours across any shared edge, however sharp the bend."""
    nbrs = [set() for _ in range(len(mesh.farea))]
    for fs in mesh.edge_facets.values():
        for a in fs:
            for b in fs:
                if a != b:
                    nbrs[a].add(b)
    return nbrs


def find(mesh, regions, taken=None, known=(), walls=()):
    """Fillet features between pairs of flat faces, and between a flat face and a cone or
    cylinder wall (walls: features found so far) on an axis square to it, or a cylinder
    wall on an axis running along it. Marks their facets as used in the regions so
    later passes leave them alone. known: radii found with confidence so far
    (cylinders, tori), which fillets can take on."""
    nbrs = _adjacency(mesh)
    used = np.zeros(len(mesh.farea), bool) if taken is None else taken.copy()
    faces = [f for f in np.argsort(-mesh.farea) if mesh.farea[f] >= MIN_FACE_AREA and not used[f]]
    plane_pt = {f: mesh.pts[mesh.fverts[f][0]] for f in faces}
    # Pairs of flat faces linked by a band of smaller facets (at most LINK_RINGS of
    # them across), meeting at a real angle; the biggest pairs first (real faces before
    # the strips of a fillet).
    is_face = np.zeros(len(mesh.farea), bool)
    is_face[faces] = True
    lo_c, hi_c = math.cos(math.radians(MAX_ANGLE_DEG)), math.cos(math.radians(MIN_ANGLE_DEG))
    square = math.cos(math.radians(WALL_SQUARE_DEG))
    beside = math.sin(math.radians(WALL_SQUARE_DEG))

    def linked(wall_of=None, walls=()):
        """Pairs of flat faces linked by a band of smaller facets (at most LINK_RINGS of
        them across), meeting at a real angle, or (with walls) of a flat face and a wall
        square to it or (a cylinder) running along it; the biggest first (real faces
        before the strips of a fillet)."""
        pairs = set()
        # (Python values for the walk, which looks at every facet within LINK_RINGS of
        # every face: numpy's per-item look-ups were most of its 10 s on the instrument
        # body. The tests are the same, in the same order)
        face_ok, open_area = is_face.tolist(), np.where(used, np.inf, mesh.farea).tolist()
        wall_at = wall_of.tolist() if wall_of is not None else None
        for A in faces:
            if used[A]:
                continue
            small = mesh.farea[A] / FACE_TO_STRIP
            seen, frontier = {A}, [A]
            for _ in range(LINK_RINGS):
                step = []
                for g in frontier:
                    for h in nbrs[g]:
                        if h in seen:
                            continue
                        seen.add(h)
                        if wall_of is None:
                            if face_ok[h] and lo_c <= mesh.fn[A] @ mesh.fn[h] <= hi_c:
                                pairs.add((min(A, h), max(A, h)))
                        elif wall_at[h] >= 0:
                            X = walls[wall_at[h]]
                            along = abs(mesh.fn[A] @ X.model.d)
                            if along >= square or (along <= beside and X.model.line[1] == 0):
                                pairs.add((A, -1 - int(wall_at[h])))
                        if open_area[h] <= small:
                            step.append(h)      # (a band facet: the far face is further on)
                frontier = step
                if not frontier:
                    break
        if wall_of is None:
            return sorted(((min(mesh.farea[A], mesh.farea[B]), A, B) for A, B in pairs), key=lambda x: -x[0])
        return sorted(((mesh.farea[A], A, B) for A, B in pairs), key=lambda x: -x[0])

    noise = getattr(mesh, "noise", 0.0)
    exact = 2 * F._tol() + 2 * noise
    coplanar_cos = math.cos(math.radians(COPLANAR_DEG))
    groups = {}

    def coplanar(X):
        """Every free facet lying in face X's plane (X first): a floor round a boss is
        often several facets, split by what stands on it, and a fillet round the boss
        can border any of them."""
        if X not in groups:
            near = np.nonzero((mesh.fn @ mesh.fn[X] > coplanar_cos)
                              & (np.abs((mesh.fcent - plane_pt[X]) @ mesh.fn[X]) <= exact) & ~used)[0]
            groups[X] = np.r_[X, near[near != X]].astype(int)
        return groups[X][~used[groups[X]]]

    corners = _Corners(mesh)
    skip = mesh.__dict__.get("mirror_skip")
    cache = np.full(len(mesh.farea), -1.0)       # (verdicts on facets, reset after each try)
    vouched = sorted(set(round(float(r), 6) for r in known if r > 0))
    found = []

    def take(pairs, walls=()):
        """Go through the pairs, taking the fillets found: first only those that fit
        closely (they vouch for their radius), then the rest, with every radius vouched
        for in hand (a 2.52 mm band near clean 2.5 mm fillets is a 2.5 mm fillet)."""
        nonlocal vouched
        waiting = []
        for round_ in (0, 1):
            for item in (pairs if round_ == 0 else waiting):
                _, A, B = item
                got = None
                if B < 0:                        # (a flat face and a wall)
                    if used[A]:
                        continue
                    X = walls[-1 - B]
                    if abs(mesh.fn[A] @ X.model.d) >= square:
                        sec = _Wall(mesh, A, X, plane_pt, coplanar(A))
                    else:
                        sec = _Beside(mesh, A, X, plane_pt, coplanar(A))
                        if not lo_c <= sec.meet <= hi_c:
                            continue
                    got = _fillet(mesh, nbrs, used, sec, plane_pt, noise, vouched, corners, cache)
                else:
                    if used[A] or used[B]:
                        continue
                    if skip is not None and skip[A] and skip[B]:
                        continue        # (both on the far side of a mirror plane: found mirrored)
                    if round_ == 0 and not _could_join(mesh, A, B, plane_pt):
                        continue
                    for X, Y in ((A, B), (B, A)):    # (from either face: one may border others too)
                        # (each face as its own facet: where a fillet ends in a rounded
                        # corner, the plane carries on past it in other facets, and
                        # where both run alongside is where the fillet's corners are)
                        sec = _Flat(mesh, X, Y, plane_pt, np.array([X]), np.array([Y]))
                        got = _fillet(mesh, nbrs, used, sec, plane_pt, noise, vouched, corners, cache)
                        if got is not None:
                            break
                if got is None or (round_ == 0 and got[4] > CONFIDENT_TOL + noise):
                    if round_ == 0:
                        waiting.append(item)     # (taken in the second round, if at all)
                    continue
                facets, sign, model, r, err = got
                feature = _feature(mesh, sec, model, sign, facets)
                if feature is None:
                    continue
                used[facets] = True
                found.append(feature)
                if err <= CONFIDENT_TOL + noise:
                    vouched = sorted(set(vouched) | {round(r, 6)})

    # fillets between flat faces first; then between flat faces and the cones and
    # cylinders found so far, these fillets included (a rounded edge running round a
    # rounded corner is a torus on the corner's axis)
    take(linked())
    walls = _join_walls(mesh, [X for X in list(walls) + found if isinstance(X.model, Revolved) and X.model.line],
                        exact + FILLET_TOL / 2)
    if walls:
        wall_of = np.full(len(mesh.farea), -1)
        for k, X in enumerate(walls):
            wall_of[X.facets] = k
        take(linked(wall_of, walls), walls)
    return found


class _Joined:
    """Wall features lying on one surface, as one wall (a cylinder found in pieces, which
    are joined only once the passes are done, or cut through by a slot: a fillet beside
    it runs along them all)."""

    def __init__(self, parts):
        self.model = max(parts, key=lambda X: len(X.facets)).model
        self.facets = np.concatenate([X.facets for X in parts])
        self.convex = parts[0].convex
        self.parts = parts


def _join_walls(mesh, walls, tol):
    """walls, with those on one surface (on one axis, each one's corners within tol of
    the other's surface) joined."""
    verts = [mesh.pts[np.unique(np.concatenate([mesh.fverts[f] for f in X.facets]))] for X in walls]
    parallel = math.cos(math.radians(0.5))
    parent = list(range(len(walls)))

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k
    for a in range(len(walls)):
        X = walls[a]
        for b in range(a + 1, len(walls)):
            Y = walls[b]
            if root(a) == root(b) or X.convex != Y.convex or abs(X.model.d @ Y.model.d) < parallel:
                continue
            if np.linalg.norm(_cross(Y.model.a - X.model.a, X.model.d)) > 10 * tol:
                continue                    # (not on one axis)
            if (np.abs(X.model.signed(verts[b])).max() <= tol
                    and np.abs(Y.model.signed(verts[a])).max() <= tol):
                parent[root(b)] = root(a)
    groups = {}
    for k in range(len(walls)):
        groups.setdefault(root(k), []).append(walls[k])
    return [g[0] if len(g) == 1 else _Joined(g) for g in groups.values()]


def _cross(a, b):
    """np.cross of two 3-vectors: the same products and differences, without numpy's
    overhead on so small a job (the instrument body made 830,000 of them here: 20 s)."""
    a0, a1, a2 = map(float, a)
    b0, b1, b2 = map(float, b)
    return np.array([a1 * b2 - a2 * b1, a2 * b0 - a0 * b2, a0 * b1 - a1 * b0])


def _could_join(mesh, A, B, plane_pt):
    """Could faces A and B be joined by a fillet, judged by the faces alone? A fillet of
    radius r touches each face r tan(theta/2) short of the line where they would meet
    (theta the angle between their normals): both faces stop about equally short of it,
    and they must be wide enough, and run alongside each other far enough, for that r.
    (A quick look, before the band between them is gathered: on a finely cut bore, every
    pair of its strips would otherwise be tried.)"""
    n1, n2 = mesh.fn[A], mesh.fn[B]
    d = _cross(n1, n2)
    d /= np.linalg.norm(d)
    p = np.linalg.solve(np.array([n1, n2, d]), [n1 @ plane_pt[A], n2 @ plane_pt[B], 0.0])
    near, width, along = [], [], []
    for X, n in ((A, n1), (B, n2)):
        P = mesh.pts[mesh.fverts[X]] - p
        across = P @ _cross(n, d)
        near.append(float(np.abs(across).min()) if (across > 0).all() or (across < 0).all() else 0.0)
        width.append(float(np.ptp(across)))
        along.append((float((P @ d).min()), float((P @ d).max())))
    theta = math.acos(max(-1.0, min(1.0, float(n1 @ n2))))
    if min(near) <= 0:
        return True                 # (a face reaching the line: a sharp edge, or nothing to tell)
    if abs(math.log(near[0] / near[1])) > ROUGH_AGREE:
        return False
    r = math.sqrt(near[0] * near[1]) / math.tan(theta / 2)
    run = min(along[0][1], along[1][1]) - max(along[0][0], along[1][0])
    rough = math.exp(-ROUGH_AGREE) * r
    return min(width) >= FACE_WIDTH * rough and run >= FACE_LENGTH * rough


class _Corners:
    """Every facet's corners in one array, to look at many facets at once."""

    def __init__(self, mesh):
        self.count = np.array([len(v) for v in mesh.fverts])
        self.start = np.r_[0, np.cumsum(self.count)[:-1]]
        self.ids = np.concatenate(mesh.fverts)
        self.pts = mesh.pts

    def of(self, gs):
        """(corner points of facets gs, where each facet's run of them starts)."""
        n = self.count[gs]
        first = np.r_[0, np.cumsum(n)[:-1]]
        k = np.repeat(self.start[gs] - first, n) + np.arange(int(n.sum()))
        return self.pts[self.ids[k]], first


class _Lines:
    """The ball rolling between two lines of the section (normals n1, n2 pointing out of
    the material, meeting at p), inside the material (sign +1: a rounded convex edge) or
    outside it (-1: a fillet in a concave corner). Its centre runs along w from p."""

    def __init__(self, n1, n2, p, sign):
        self.n1, self.n2, self.p, self.sign = n1, n2, p, sign
        self.w = -sign * (n1 + n2) / (1 + n1 @ n2)
        # (the arc turns from one side's normal to the other's)
        self.span = math.acos(max(-1.0, min(1.0, float(n1 @ n2))))

    def heights(self, X):
        """How far points X lie out of the material past either side."""
        Q = X - self.p
        return Q @ self.n1, Q @ self.n2

    def radii(self, X):
        """Each point's radius: the circle touching both sides through it on the side
        facing their corner, as a fillet's points lie; and whether there is none."""
        return _corner_radii(X - self.p, self.w)

    def both_radii(self, X):
        """Both circles' radii through each point (where there are any), to vote with."""
        # (a corner q lies on the ball of radius r when |q - r w| = r: a quadratic in r)
        Q = X - self.p
        qa, qb, qc = self.w @ self.w - 1.0, -2.0 * (Q @ self.w), np.einsum("ij,ij->i", Q, Q)
        if abs(qa) < 1e-12:
            return np.zeros(0)
        disc = qb * qb - 4 * qa * qc
        ok = disc >= 0
        if ok.sum() < 3:
            return np.zeros(0)
        root = np.sqrt(disc[ok])
        return np.r_[(-qb[ok] - root) / (2 * qa), (-qb[ok] + root) / (2 * qa)]

    def centre(self, r):
        return self.p + r * self.w

    def turn(self, r):
        """How far a fillet of radius r turns round its axis, from one side to the other."""
        return self.span


class _LineCircle:
    """The ball rolling between a line and a circle: face A through the origin of the
    section, out of the material along its first coordinate, and a cylinder wall seen
    along its axis (centre c, radius R; the material inside it when out2 is +1, outside
    when -1). Inside the material (sign +1) or outside it (-1), on one side of the
    circle's centre or the other along the line (side)."""

    def __init__(self, c, R, out2, sign, side):
        self.c, self.R, self.out2, self.sign, self.side = c, R, out2, sign, side
        # (the ball is inside the circle when it rolls in whatever the circle holds)
        self.sc = -1.0 if out2 == sign else 1.0
        self.cy, self.cx = float(c[0]), float(c[1])     # (height off the line, place along it)

    def heights(self, X):
        return X[:, 0], self.out2 * (np.linalg.norm(X - self.c, axis=1) - self.R)

    def centre(self, r):
        """Where the ball of radius r touching both sits (nan where it can't)."""
        r = np.asarray(r, float)
        h = -self.sign * r
        under = (self.R + self.sc * r) ** 2 - (h - self.cy) ** 2
        with np.errstate(invalid="ignore"):
            x = self.cx + self.side * np.sqrt(np.where((under >= 0) & (self.R + self.sc * r > 0), under, np.nan))
        return np.stack([h, x], axis=-1)

    def _touching(self, O):
        """Unit directions from ball centres O to where they touch the line and the circle."""
        a1 = np.zeros_like(O)
        a1[..., 0] = self.sign
        v = self.c - O
        with np.errstate(invalid="ignore", divide="ignore"):
            return a1, self.sc * v / np.linalg.norm(v, axis=-1, keepdims=True)

    def _candidates(self, X):
        """Both radii of the balls touching both through each point X, on this side, nan
        where there is none: rows of two. (A ball centred at height -sign r touches the
        line; |o - c| = R + sc r and |o - q| = r then differ by a linear equation, which
        leaves a quadratic in r. A corner where the fillet meets a side is a double root:
        a hair off it, from rounding, still counts.)"""
        s = self.sign
        qy, qx = X[:, 0], X[:, 1]
        D = qx - self.cx
        K = self.R ** 2 - self.cx ** 2 - self.cy ** 2 + qx * qx + qy * qy
        m = s * (self.cy - qy) - self.R * self.sc
        G = K / 2 - qx * D
        qa, qb, qc = m * m, 2 * (s * qy * D * D - G * m), G * G + qy * qy * D * D
        root = np.sqrt(np.maximum(qb * qb - 4 * qa * qc, 0))
        with np.errstate(invalid="ignore", divide="ignore"):
            r = np.stack([(-qb - root) / (2 * qa), (-qb + root) / (2 * qa)], axis=1)
        r = np.where(np.isfinite(r) & (r > 0), r, np.nan)
        O = self.centre(r)                                          # (n, 2, 2)
        gap = np.abs(np.linalg.norm(X[:, None] - O, axis=2) - r)
        with np.errstate(invalid="ignore"):
            return np.where(gap <= WEDGE_SLACK, r, np.nan), O

    def radii(self, X):
        """Each point's radius: the ball touching both through it, the point on its arc
        between where it touches them (as a fillet's points lie, not on its far side);
        and whether there is none."""
        r, O = self._candidates(X)
        a1, a2 = self._touching(O)
        b = a1 + a2
        Q = X[:, None] - O
        with np.errstate(invalid="ignore", divide="ignore"):
            nb = np.linalg.norm(b, axis=-1)
            half = np.arccos(np.clip(nb / 2, -1, 1))
            cos_q = np.einsum("...i,...i", Q, b) / (np.linalg.norm(Q, axis=-1) * nb)
            between = np.arccos(np.clip(cos_q, -1, 1)) <= half + WEDGE_SLACK / r
        best = np.fmax(*np.where(between, r, np.nan).T)
        bad = ~np.isfinite(best)
        return np.where(bad, 1.0, best), bad

    def both_radii(self, X):
        r, _ = self._candidates(X)
        r1, r2 = r.T
        return np.r_[r1[np.isfinite(r1)], r2[np.isfinite(r2) & ~(r2 == r1)]]

    def turn(self, r):
        a1, a2 = self._touching(self.centre(r))
        c = float(a1 @ a2)
        return math.acos(max(-1.0, min(1.0, c))) if np.isfinite(c) else 0.0


class _Flat:
    """Two flat faces A and B seen across the edge where they would meet: lines in the
    section square to the edge; a fillet between them is a cylinder along it. group_A
    and group_B: the facets making up each face."""

    def __init__(self, mesh, A, B, plane_pt, group_A, group_B):
        n1, n2 = mesh.fn[A], mesh.fn[B]
        d = _cross(n1, n2)
        self.d = d / np.linalg.norm(d)
        self.origin = np.linalg.solve(np.array([n1, n2, self.d]), [n1 @ plane_pt[A], n2 @ plane_pt[B], 0.0])
        self.e1 = n1
        self.e2 = _cross(self.d, n1)
        self.n1, self.n2 = self.coords_of(n1, vector=True), self.coords_of(n2, vector=True)
        self.p = np.zeros(2)
        self.A, self.B = A, B
        self.normal_A, self.normal_B = n1, n2
        PA = mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in group_A]))]
        PB = mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in group_B]))]
        tA, tB = self.along(PA), self.along(PB)
        self.t0, self.t1 = max(tA.min(), tB.min()), min(tA.max(), tB.max())
        self.width_A = float(np.ptp(PA @ _cross(n1, self.d)))
        self.width_B = float(np.ptp(PB @ _cross(n2, self.d)))
        self.area_A = float(mesh.farea[group_A].sum())
        self.area_B = float(mesh.farea[group_B].sum())
        self.a_mask = np.zeros(len(mesh.farea), bool)
        self.a_mask[group_A] = True
        self.b_mask = np.zeros(len(mesh.farea), bool)
        self.b_mask[group_B] = True
        self.b_flat = True
        self.group_A = group_A
        self.rough = 0.0

    def coords_of(self, P, vector=False):
        Q = np.atleast_2d(P) - (0 if vector else self.origin)
        out = np.c_[Q @ self.e1, Q @ self.e2]
        return out[0] if vector else out

    def coords(self, P):
        return self.coords_of(P)

    def along(self, P):
        return np.atleast_2d(P) @ self.d

    def configs(self):
        return [_Lines(self.n1, self.n2, self.p, sign) for sign in (1.0, -1.0)]

    def model(self, o, r):
        return Revolved(self.origin + o[0] * self.e1 + o[1] * self.e2, self.d, line=(r, 0.0))


class _Beside:
    """A flat face A beside a cylinder wall X whose axis runs along it, seen across the
    axis: a line and a circle there, and a fillet between them is a cylinder along the
    axis (a boss standing out of a wall, rounded into it)."""

    def __init__(self, mesh, A, X, plane_pt, group_A):
        self.mesh = mesh
        nA = mesh.fn[A]
        d = X.model.d - (X.model.d @ nA) * nA
        self.d = d / np.linalg.norm(d)
        self.origin = plane_pt[A]
        self.e1, self.e2 = nA, _cross(self.d, nA)
        self.A, self.X = A, X
        self.normal_A = nA
        # the wall's circle in the section, from its own corners
        V = mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in X.facets]))]
        self.c, self.R = _circle(self.coords(V))
        self.out2 = 1.0 if X.convex else -1.0
        # (how they meet where the circle crosses the line: the cosine between their
        # normals there; tangent, or apart, they make no edge to round)
        self.meet = -self.out2 * float(self.c[0]) / self.R
        PA = mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in group_A]))]
        tA, tB = self.along(PA), self.along(V)
        self.t0, self.t1 = max(tA.min(), tB.min()), min(tA.max(), tB.max())
        self.width_A = float(np.ptp(PA @ self.e2))
        Q = self.coords(V) - self.c
        ang = np.sort(np.mod(np.arctan2(Q[:, 1], Q[:, 0]), 2 * math.pi))
        gaps = np.diff(np.r_[ang, ang[0] + 2 * math.pi])
        self.width_B = float((2 * math.pi - gaps.max()) * self.R)
        self.area_A = float(mesh.farea[group_A].sum())
        self.area_B = float(mesh.farea[X.facets].sum())
        self.a_mask = np.zeros(len(mesh.farea), bool)
        self.a_mask[group_A] = True
        self.b_mask = np.zeros(len(mesh.farea), bool)
        self.b_mask[X.facets] = True
        self.b_flat = False
        self.group_A = group_A
        self.rough = float(self.off_B(self.coords(V)).max())

    def coords(self, P):
        Q = np.atleast_2d(P) - self.origin
        return np.c_[Q @ self.e1, Q @ self.e2]

    def off_B(self, X):
        return np.abs(np.linalg.norm(X - self.c, axis=1) - self.R)

    def along(self, P):
        return np.atleast_2d(P) @ self.d

    def configs(self):
        return [_LineCircle(self.c, self.R, self.out2, sign, side)
                for sign in (1.0, -1.0) for side in (1.0, -1.0)]

    def profile_angles(self, gs):
        """Which way facets gs face in the section."""
        n = self.mesh.fn[np.asarray(gs, dtype=int)]
        return np.arctan2(n @ self.e2, n @ self.e1)

    def model(self, o, r):
        return Revolved(self.origin + o[0] * self.e1 + o[1] * self.e2, self.d, line=(r, 0.0))


def _arc(angles):
    """The smallest arc holding all the angles."""
    ang = np.sort(np.mod(angles, 2 * math.pi))
    gaps = np.diff(np.r_[ang, ang[0] + 2 * math.pi])
    return 2 * math.pi - gaps.max()


def _circle(X):
    """(centre, radius) of the circle best through 2D points X (algebraic fit)."""
    A = np.c_[2 * X, np.ones(len(X))]
    b = (X * X).sum(axis=1)
    sol = np.linalg.lstsq(A, b, rcond=None)[0]
    c = sol[:2]
    return c, float(math.sqrt(max(sol[2] + c @ c, 1e-12)))


class _Wall:
    """A flat face A square to the axis of a cone or cylinder wall X, seen in the half
    plane through the axis (distance from it, height along it): both are lines there,
    and a fillet between them is a torus round the axis. (The axis is taken square to
    the face, through the wall's: the wall's own, found from its facets alone, can be a
    degree or two off, which a torus round it would carry far off the face.)"""

    def __init__(self, mesh, A, X, plane_pt, group_A):
        self.mesh = mesh
        u = mesh.fn[A]
        a = X.model.a + ((plane_pt[A] - X.model.a) @ X.model.d) * X.model.d
        frame = Revolved(a, u, line=(1.0, 0.0))
        V = mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in X.facets]))]
        rho, z, w = frame.local(V)
        k, c0 = np.polyfit(z, rho, 1) if np.ptp(z) > 1e-9 else (0.0, float(rho.mean()))
        self.m = Revolved(a, u, line=(float(c0), float(k)))
        self.n1 = np.array([0.0, 1.0])                      # (u faces the way face A does)
        g = (1.0 if X.convex else -1.0) / math.hypot(1, k)
        self.n2 = np.array([g, -g * k])
        zA = float((plane_pt[A] - self.m.a) @ u)
        self.p = np.array([c0 + k * zA, zA])
        self.A, self.X = A, X
        self.normal_A = mesh.fn[A]
        # along: round the axis, at the radius where they meet, from the middle of the
        # wall's arc (it may not go all the way round)
        self.rho = max(float(self.p[0]), 1e-6)
        ang = np.sort(np.mod(self.m.angle(w), 2 * math.pi))
        gaps = np.diff(np.r_[ang, ang[0] + 2 * math.pi])
        g = int(gaps.argmax())
        span = 2 * math.pi - float(gaps[g]) if gaps[g] > math.radians(5) else 2 * math.pi
        self.mid = float(ang[(g + 1) % len(ang)]) + span / 2
        self.t0, self.t1 = -span / 2 * self.rho, span / 2 * self.rho
        rho_A = self.m.local(mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in group_A]))])[0]
        self.width_A = float(np.abs(rho_A - self.p[0]).max())
        self.width_B = float(np.ptp(z) * math.hypot(1, k))
        self.area_A = float(mesh.farea[group_A].sum())
        self.area_B = float(mesh.farea[X.facets].sum())
        self.a_mask = np.zeros(len(mesh.farea), bool)
        self.a_mask[group_A] = True
        self.b_mask = np.zeros(len(mesh.farea), bool)
        self.b_mask[X.facets] = True
        self.b_flat = False
        self.group_A = group_A
        self.rough = float(self.off_B(self.coords(V)).max())

    def coords(self, P):
        rho, z, _ = self.m.local(P)
        return np.c_[rho, z]

    def off_B(self, X):
        """How far section points X lie off the wall."""
        return np.abs((X - self.p) @ self.n2)

    def along(self, P):
        _, _, w = self.m.local(P)
        ang = self.m.angle(w)
        return (np.mod(ang - self.mid + math.pi, 2 * math.pi) - math.pi) * self.rho

    def configs(self):
        return [_Lines(self.n1, self.n2, self.p, sign) for sign in (1.0, -1.0)]

    def profile_angles(self, gs):
        """Which way facets gs face in the half plane through the axis."""
        n = self.mesh.fn[np.asarray(gs, dtype=int)]
        v = self.mesh.fcent[gs] - self.m.a
        w = v - np.outer(v @ self.m.d, self.m.d)
        radial = w / np.maximum(np.linalg.norm(w, axis=1, keepdims=True), 1e-12)
        return np.arctan2(n @ self.m.d, np.einsum("ij,ij->i", n, radial))

    def model(self, o, r):
        return Revolved(self.m.a, self.m.d, circle=(float(o[0]), float(o[1]), r))


def _fillet(mesh, nbrs, used, sec, plane_pt, noise, vouched, corners, cache):
    """The rolling-ball fillet joining flat face sec.A to sec's other side (face B, or a
    wall), if there is one: (facets, side, surface, radius, worst corner gap), or None.

    The band between them is gathered loosely (facets whose corners each lie on some
    fillet between the two, of about the same radius), its radius read from the corners
    where both run alongside (not where it meets a rounded corner at its end), and the
    fillet is then the band's facets that lie on that surface."""
    A = sec.A
    small = min(mesh.farea[A], mesh.farea[sec.B] if sec.b_flat else sec.area_B) / FACE_TO_STRIP
    exact = 2 * F._tol() + 2 * noise      # (where the mesh's own corners must agree)
    tol = exact + FILLET_TOL
    flat = math.cos(math.radians(1))
    t0, t1 = sec.t0, sec.t1
    if t1 <= t0:
        return None
    a_mask, b_mask = sec.a_mask, sec.b_mask

    def not_B(gs):
        return ~b_mask[gs]

    def reaches_B(facets):
        """Whether facets border side B (a wall: any of its facets, or a free facet
        reaching its surface, which the wall's feature didn't take: on a rough mesh,
        triangles straddle where the fillet meets the wall)."""
        inside = set(int(g) for g in facets)
        around = np.array(sorted({h for g in facets for h in nbrs[g]} - inside), dtype=int)
        if not len(around):
            return False
        if b_mask[around].any():
            return True
        if sec.b_flat:
            return False
        around = around[~used[around] & ~a_mask[around]]
        if not len(around):
            return False
        P, first = corners.of(around)
        return bool((np.minimum.reduceat(sec.off_B(sec.coords(P)), first) <= tol / 2 + exact).any())

    def judge(gs, cfg):
        """For facets gs: the radius each one's place suggests if it could be part of
        the band, else nan."""
        sign = cfg.sign
        gs = np.asarray(gs, dtype=int)
        ok = ~used[gs] & ~a_mask[gs] & not_B(gs) & (mesh.farea[gs] <= small)
        P, first = corners.of(gs)
        X = sec.coords(P)
        hA, hB = cfg.heights(X)
        absA, absB = np.maximum.reduceat(np.abs(hA), first), np.maximum.reduceat(np.abs(hB), first)
        # (not by its normal: on a mesh with rounded coordinates a sliver lying on the
        # fillet can face any way at all; its corners are what can be trusted. But a
        # piece of face A itself, cut off by the triangulation, isn't band, nor anything
        # lying in either side)
        ok &= ~((mesh.fn[gs] @ sec.normal_A > flat) & (absA <= tol))
        if sec.b_flat:
            ok &= ~((mesh.fn[gs] @ sec.normal_B > flat) & (absB <= tol))
        else:
            # (a free piece of the wall itself: lying in it, and facing the way it does;
            # beside where the fillet meets it, the fillet's own facets lie as close)
            facing = np.abs(np.einsum("ij,ij->i", mesh.fn[gs], sec.X.model.normal(mesh.fcent[gs])))
            ok &= ~((facing > flat) & (absB <= tol))
        ok &= (absA > exact) & (absB > exact)
        # it lies between the two, on the side the ball rolls on
        ok &= (np.maximum.reduceat(sign * hA, first) <= tol) & (np.maximum.reduceat(sign * hB, first) <= tol)
        # and its corners lie on one fillet: each gives the radius of the fillet it would
        # lie on (corners on a wall further off, or a rounded edge along face A's top,
        # give radii far off the fillet's)
        r, bad = cfg.radii(X)
        ok &= ~np.maximum.reduceat(bad, first)
        r = np.where(bad, 1.0, r)
        lo, hi = np.minimum.reduceat(r, first), np.maximum.reduceat(r, first)
        ok &= hi <= math.exp(CORNER_AGREE) * np.maximum(lo, 1e-9)
        return np.where(ok, np.add.reduceat(r, first) / corners.count[gs], np.nan)

    def spread(start, accept):
        """The facets reached from start through facets accept() takes (a whole frontier
        at a time)."""
        group = set(int(g) for g in start)
        frontier = list(group)
        while frontier:
            cand = np.array(sorted({h for g in frontier for h in nbrs[g]} - group), dtype=int)
            if not len(cand):
                break
            frontier = cand[accept(cand)].tolist()
            group.update(frontier)
        return np.array(sorted(group), dtype=int)

    touched = []
    guess = cache                   # (-1: not judged yet; nan: no)

    def _fillet_side(cfg):
        sign = cfg.sign

        def guesses(gs):
            todo = gs[guess[gs] == -1]
            if len(todo):
                guess[todo] = judge(todo, cfg)
                touched.extend(todo.tolist())
            return guess[gs]

        around = np.array(sorted({h for g in sec.group_A for h in nbrs[g]} - set(sec.group_A.tolist())), dtype=int)
        seeds = around[np.isfinite(guesses(around))] if len(around) else around
        if not len(seeds):
            return None
        seeds = seeds[np.argsort(guess[seeds], kind="stable")]
        # The facets along face A suggesting about the same radius, grown through those
        # agreeing with them: each such group on its own (beside the fillet, face A
        # can border others, a rounded edge along its top, say), until one reaches B
        # (each group spans no more than DEPTH_AGREE: radii creeping up step by step
        # round a curved edge aren't one fillet)
        groups, first = [], 0
        logs = np.log(guess[seeds])
        for k in range(1, len(seeds) + 1):
            if k == len(seeds) or logs[k] - logs[first] > DEPTH_AGREE:
                groups.append(seeds[first:k])
                first = k
        band = None
        for group in sorted(groups, key=len, reverse=True):
            middle = float(np.median(guess[group]))
            # (both sides must be long and wide enough for a fillet of about this radius:
            # checked now, before growing the band, with the radius only roughly known)
            rough = math.exp(-DEPTH_AGREE) * middle
            if t1 - t0 < FACE_LENGTH * rough or min(sec.width_A, sec.width_B) < FACE_WIDTH * rough:
                continue
            grown = spread(group, lambda gs: np.abs(np.log(guesses(gs) / middle)) <= DEPTH_AGREE)
            if reaches_B(grown):
                band, seeds = grown, group
                break
        if band is None:
            return None
        V = mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in band]))]
        t = sec.along(V)
        # (a straight fillet is usually cut into strips running its whole length: its
        # corners all lie at its two ends, where the faces end too)
        core = sec.coords(V[(t >= t0 - exact) & (t <= t1 + exact)])
        if len(core) < 4:
            # (or past them, into the corner patches at both ends where it meets fillets
            # of other radii: a box's r 4 bottom fillet ran 1 mm on past the faces'
            # shared stretch at each end, with no corner inside it. Then the corners of
            # the strips reaching across that stretch and ending within about a radius
            # of it, as the fillet's facets are taken below)
            reach = math.exp(DEPTH_AGREE) * middle + exact
            P, first = corners.of(band)
            tt = sec.along(P)
            lo, hi = np.minimum.reduceat(tt, first), np.maximum.reduceat(tt, first)
            strips = band[(lo < t1 - exact) & (hi > t0 + exact) & (lo >= t0 - reach) & (hi <= t1 + reach)]
            if len(strips):
                core = sec.coords(mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in strips]))])
        if len(core) < 4:
            return None
        fit = _radius(core, cfg, noise, vouched)
        if fit is None:
            return None
        r, err, allowed = fit
        # (nor held closer than the wall beside it lies to its own surface: a rough mesh
        # is as rough along the fillet)
        allowed = max(allowed, min(sec.rough, GROW_TOL + 2 * noise))
        if t1 - t0 < FACE_LENGTH * r:
            return None                # (too short along the edge to tell a fillet by)
        if min(sec.width_A, sec.width_B) < FACE_WIDTH * r:
            return None                # (a long narrow strip of a finely cut bore is no face)
        o = cfg.centre(r)              # (the fillet's centre in the section)
        if not np.all(np.isfinite(o)):
            return None
        # (it may run on past where one side ends, into whatever rounds that end off,
        # by about its radius: strips running its whole length end there; but only
        # facets also reaching into the stretch both share)
        run_on = r + exact
        in_band = np.zeros(len(mesh.farea), bool)
        in_band[band] = True

        def off_surface(gs):
            P, first = corners.of(gs)
            Q = sec.coords(P)
            return np.maximum.reduceat(np.abs(np.linalg.norm(Q - o, axis=1) - r), first), P, first

        def on(gs, band_only=True, slack=0.0):
            # on the fillet, and not far past where both sides run (a fillet stops where
            # the edge it rounds off does; whatever carries on round a corner from there
            # is another surface)
            gs = np.asarray(gs, dtype=int)
            off, P, first = off_surface(gs)
            t = sec.along(P)
            lo, hi = np.minimum.reduceat(t, first), np.maximum.reduceat(t, first)
            # (reaching into the stretch both run along: the run-on is the end of a strip
            # running the fillet's length, not a facet of a rounded corner beyond)
            inside = (lo < t1 - exact) & (hi > t0 + exact)
            mine = in_band[gs] if band_only else (~used[gs] & ~a_mask[gs] & not_B(gs) & (mesh.farea[gs] <= small))
            return mine & inside & (lo >= t0 - run_on) & (hi <= t1 + run_on) & (off <= allowed + slack)

        start = seeds[on(seeds)]
        facets = spread(start, on) if len(start) else np.zeros(0, int)
        if len(facets):
            # (and the rows beside each face, which lie within a hair of it too, so no
            # radius could be read off them and the band left them out: on a finely
            # meshed r 4 fillet they were fitted on their own as r 4.067. Held a little
            # less closely: the fillet read off the band's ends lay 0.003 mm off the true
            # one along them, and where it touches the face either would do)
            facets = spread(facets, lambda gs: on(gs, band_only=False, slack=FILLET_TOL / 4))
        if not len(facets) or not reaches_B(facets):
            return None
        # it must bend on the way (one flat strip with its edges on both is a chamfer),
        # and run along its edge for at least about its radius (a narrow slice of a
        # rounded corner or of a rounded edge following a curve fits it too)
        if len(np.unique(np.round(mesh.fn[facets], 2), axis=0)) < 2:
            return None
        # (round a wall every facet faces its own way: a chamfer round a hole has all its
        # corners on its two rims, which a torus touching both sides passes through too;
        # but its facets all face one way in the section, and a fillet's turn)
        if not sec.b_flat and _arc(sec.profile_angles(facets)) < math.radians(BEND_DEG):
            return None
        # (and pass between the sides somewhere: a band whose corners all lie on the two
        # lines where it would touch them, one strip across, is as much a chamfer, and
        # its radius only a double root that rounding makes or unmakes)
        hA, hB = cfg.heights(sec.coords(mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in facets]))]))
        if not np.any((-sign * hA > tol) & (-sign * hB > tol)):
            return None
        t = sec.along(mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in facets]))])
        if np.ptp(t) < FACE_LENGTH * r:
            return None
        # and turn round its axis no further than from one side to the other (more is
        # some other rounded surface it ran on into); nor be part of a bigger cylinder,
        # a hole or pin carrying on round past the faces, through facets on it
        turn = cfg.turn(r)

        def span(gs):
            Q = sec.coords(mesh.pts[np.unique(np.concatenate([mesh.fverts[g] for g in gs]))]) - o
            return _arc(np.arctan2(Q[:, 1], Q[:, 0]))

        if span(facets) > turn + math.radians(SPAN_SLACK_DEG):
            return None

        def on_cylinder(gs):
            gs = np.asarray(gs, dtype=int)
            off = off_surface(gs)[0]
            return ~used[gs] & ~a_mask[gs] & not_B(gs) & (off <= allowed)

        whole = spread(facets, on_cylinder)
        if len(whole) > len(facets) and span(whole) > turn + math.radians(SPAN_SLACK_DEG):
            return None
        return facets, sign, sec.model(o, r), r, err

    for cfg in sec.configs():       # convex edge (ball inside the part) or concave (outside)
        try:
            got = _fillet_side(cfg)
        finally:
            cache[touched] = -1.0
            touched.clear()
        if got is not None:
            return got
    return None


def _corner_radii(Q, w):
    """For each point Q (in the section, from where the two sides meet), the radius of
    the circle touching both that passes through it on the side facing their corner, as
    a fillet's points do (the ball's centre at r w; the other circle through it,
    smaller, has it on the far side), and whether there is none (the point lies outside
    their wedge)."""
    qa, qb, qc = w @ w - 1.0, -2.0 * (Q @ w), np.einsum("ij,ij->i", Q, Q)
    disc = qb * qb - 4 * qa * qc
    # (a corner where the fillet meets a face is on both circles at once, a double root:
    # a hair outside the wedge, from rounding, still counts. Moving a point by e
    # changes the discriminant by about 8 r e |qa|.)
    double = np.abs(qb / (2 * qa))
    root = np.sqrt(np.maximum(disc, 0))
    r = np.maximum((-qb - root) / (2 * qa), (-qb + root) / (2 * qa))
    bad = (disc < -8 * double * abs(qa) * WEDGE_SLACK) | ~(r > 0) | ~np.isfinite(r)
    return r, bad


def _radius(X, cfg, noise, vouched):
    """(radius, worst corner gap, gap allowed) of the rolling-ball fillet (configuration
    cfg) through section points X, or None if they don't lie on one (with vouched radii:
    on one of those)."""
    radii = cfg.both_radii(X)
    radii = radii[radii > 1e-3]
    if not len(radii):
        return None

    def gap(r):
        # (most of the corners, not every one: where the band meets a rounded corner or
        # another surface at its ends, a few stray; the fillet is then cut back to the
        # facets lying on it)
        o = cfg.centre(r)
        if not np.all(np.isfinite(o)):
            return np.inf
        return float(np.percentile(np.abs(np.linalg.norm(X - o, axis=1) - r), ON_SHARE))

    # (each corner gives two radii, the right one shared by most corners: the median of
    # the radii in the densest cluster)
    radii = np.sort(radii)
    cluster = np.split(radii, np.nonzero(np.diff(radii) > max(1e-3, 0.01 * np.median(radii)))[0] + 1)
    r = float(np.median(max(cluster, key=len)))
    # (the band's other facets may stray as far as its middle does, give or take: on a
    # clean mesh that keeps the rounded corners at its ends out, on a rough one it
    # still lets the whole fillet in)
    floor = 2 * F._tol() + 2 * noise

    def cap(limit, radius):
        # (a small fillet is held tighter: a hundredth of a millimetre is a lot of a
        # half-millimetre fillet)
        return min(limit, REL_TOL * radius) + 2 * noise

    err = gap(r)
    # One radius is usually used all over a design: a radius found with confidence
    # elsewhere in the part, close to this band's own, is taken if its corners lie
    # nearly as close to it (or close enough, where they fit no fillet on their own)
    for rv in sorted(vouched, key=lambda x: abs(x - r)):
        if abs(rv - r) > PRIOR_RANGE * rv:
            continue
        e = gap(rv)
        if e <= cap(PRIOR_TOL, rv) and (err > cap(FILLET_TOL, r) or e <= max(2 * err, err + FILLET_TOL / 2)):
            return rv, e, min(cap(max(GROW_TOL, PRIOR_TOL), rv), max(GROW_FACTOR * e, floor))
    if err <= cap(FILLET_TOL, r):
        return r, err, min(cap(GROW_TOL, r), max(GROW_FACTOR * err, floor))
    return None


def _feature(mesh, sec, model, sign, facets):
    """The fillet's feature (cut to the band's outline), or None if it can't be made."""
    # the faces it joins must be real faces, clearly bigger than its strips (a few
    # facets of a finely cut pin can fit some other cylinder too)
    if min(sec.area_A, sec.area_B) < FACE_TO_STRIP * mesh.farea[facets].max():
        return None
    F._loose, F._anchored = True, True
    try:
        return F._feature(mesh, model, facets, sign > 0)
    finally:
        F._loose, F._anchored = False, False
