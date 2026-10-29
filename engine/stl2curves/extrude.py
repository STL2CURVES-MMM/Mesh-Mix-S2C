"""
Extrusions: curved areas that are a 2D profile pushed straight along one of the part's
design directions.

Most parts are drawn as sketches on the three planes of the design and pushed out, so
a rounded edge, a channel or a slot running along one of those directions is a curve
seen end-on. On a coarse mesh such an area is a row of long thin strips the surface
passes can't make sense of (a curvature-continuous fillet's middle fits a cylinder, its
flanks don't; the rest is left to freeform patches and blends). Seen along the
direction, every corner falls on one curve: the profile.

So, for each design direction (the normals of the biggest flat faces, and the file's
axes): the curved facets standing square to it are grouped, their corners projected
onto the plane square to it, ordered along the curve they lie on (as for pipes.py's
spines) and fitted with a cubic B-spline by penalised least squares (Eilers and Marx,
"Flexible smoothing with B-splines and penalties", Statistical Science 1996), more
spans only as needed to come within FIT_DEV of every corner. The surface is that
profile swept along the direction; its face is cut from it like any freeform patch.
"""
import math

import numpy as np
from scipy.interpolate import BSpline
from scipy.spatial import cKDTree

from . import freeform
from . import pipes
from .features import Feature, Revolved, Sphere

ENABLED = False         # off for now: on the GPS case its few small patches mostly failed to build and
                        # took their neighbours with them (8814 -> 9265 faces); to try on extruded parts
FIT_DEV = freeform.FIT_DEV      # mm: the profile passes this close to every corner's projection
SQUARE_DEG = 3.0        # a facet within this of standing square to the direction can be part of an extrusion
AXIS_DEG = 1.0          # directions closer than this are one
DESIGN_SHARE = 0.02     # a flat-face direction with this share of the flat area is a design direction
MIN_FACETS = 6          # an extrusion takes at least this many facets...
MIN_POINTS = 5          # ...whose corners fall on at least this many points of the profile...
MIN_LENGTH = 1.0        # mm: ...and runs at least this far along the direction
MERGE = 1e-3            # mm: corners' projections closer than this are one point of the profile
MARGIN = 0.15           # the profile runs on past its last points by this share of its length
MAX_BULGE = 0.15        # the profile bows off the chord between its points by at most this share of it
MAX_SPANS = 200         # knot intervals in the profile at most
NORMAL_DEG = 20.0       # every facet faces within this of the profile's normal where it lies
SAME_AXIS_COS = math.cos(math.radians(0.5))


class Extrusion:
    """The profile c(s) (a cubic B-spline, knots t, 2D control points C in the plane through
    o spanned by e1, e2) swept along d, from z0 to z1 along it. signed(p): the distance
    from the profile seen along d, positive on the side its left-hand normal points to."""
    kind = "extrusion"
    line = circle = None

    def __init__(self, o, d, e1, e2, t, C, z0, z1):
        self.o, self.d, self.e1, self.e2 = (np.asarray(v, float) for v in (o, d, e1, e2))
        self.t, self.C = np.asarray(t, float), np.asarray(C, float)
        self.z0, self.z1 = float(z0), float(z1)
        self.rim_dev = 0.0      # how far the outline's corners are off the surface (mm)
        self.dev = 0.0
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
            s = np.linspace(self.lo, self.hi, 4001)
            self._cache = {"c": c, "c1": c.derivative(), "c2": c.derivative(2), "s": s, "tree": cKDTree(c(s))}
        return self._cache

    def flat(self, P):
        """The points seen along d: 2D coordinates in the profile's plane."""
        Q = np.atleast_2d(np.asarray(P, float)) - self.o
        return np.c_[Q @ self.e1, Q @ self.e2]

    def _foot(self, q):
        cache = self._spline()
        s = cache["s"][cache["tree"].query(q)[1]]
        return pipes._foot(cache["c"], cache["c1"], cache["c2"], q, s, self.lo, self.hi)

    def _offset(self, P):
        q = self.flat(P)
        s = self._foot(q)
        cache = self._spline()
        T = cache["c1"](s)
        T = T / np.maximum(np.linalg.norm(T, axis=1), 1e-12)[:, None]
        n = np.c_[-T[:, 1], T[:, 0]]
        return np.einsum("ij,ij->i", q - cache["c"](s), n), n

    def signed(self, P):
        return self._offset(P)[0]

    def normal(self, P):
        n = self._offset(P)[1]
        return n[:, :1] * self.e1 + n[:, 1:] * self.e2

    def point(self, s, z):
        s, z = np.asarray(s, float), np.asarray(z, float)
        c = self._spline()["c"](s)
        return self.o + c[:, :1] * self.e1 + c[:, 1:] * self.e2 + z[:, None] * self.d

    def surface(self):
        """The swept profile over its whole domain and a margin along d, as an
        OpenCascade B-spline surface."""
        if self._surface is None:
            fine = np.linspace(self.lo, self.hi, 4001)
            run = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(self._spline()["c"](fine), axis=0), axis=1))]
            n = int(min(400, max(9, math.ceil(run[-1] / 0.05) + 1)))
            S = np.interp(np.linspace(0, run[-1], n), run, fine)
            grow = 0.2 * (self.z1 - self.z0) + 0.5
            Z = np.linspace(self.z0 - grow, self.z1 + grow, 5)
            self._surface = freeform.approximate(self.point, S, Z)
        return self._surface

    def face(self):
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        surface = self.surface()
        if surface is None:
            return None
        u0, u1, v0, v1 = surface.Bounds()
        maker = BRepBuilderAPI_MakeFace(surface, u0, u1, v0, v1, 1e-7)
        return maker.Face() if maker.IsDone() else None


def design_directions(mesh, features):
    """The part's design directions: the file's axes, and the normals of the flat faces
    with a fair share of the flat area (a part exported turned), one per direction."""
    owned = np.zeros(len(mesh.farea), bool)
    for f in features:
        owned[f.facets] = True
    flat = np.nonzero(~owned & (mesh.farea > 0))[0]
    found = [np.eye(3)[i] for i in range(3)]
    if len(flat):
        order = flat[np.argsort(-mesh.farea[flat])]
        total = float(mesh.farea[flat].sum())
        groups = []         # [direction, area]
        for g in order:
            n = mesh.fn[g]
            for grp in groups:
                if abs(grp[0] @ n) > math.cos(math.radians(AXIS_DEG)):
                    grp[1] += mesh.farea[g]
                    break
            else:
                groups.append([n, float(mesh.farea[g])])
        found += [d for d, a in groups if a >= DESIGN_SHARE * total]
    out = []
    for d in found:
        d = d / np.linalg.norm(d)
        if all(abs(d @ e) < math.cos(math.radians(AXIS_DEG)) for e in out):
            out.append(d)
    return out


def _plane(d):
    e1 = pipes._square(d)
    return e1, np.cross(d, e1)


def fit(mesh, facets, d):
    """(model, deviation) for the facets as one extrusion along d, or (None, None)."""
    V = np.unique(np.concatenate([mesh.fverts[a] for a in facets]))
    P = mesh.pts[V]
    z = P @ d
    if np.ptp(z) < MIN_LENGTH:
        return None, None
    e1, e2 = _plane(d)
    o = np.zeros(3)
    q = np.c_[P @ e1, P @ e2]
    # (both ends of a strip fall on one point of the profile)
    keys = np.round(q / MERGE).astype(np.int64)
    _, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.ravel()
    Q = np.zeros((inv.max() + 1, 2))
    np.add.at(Q, inv, q)
    Q /= np.bincount(inv)[:, None]
    if len(Q) < MIN_POINTS:
        return None, None
    s = pipes._order(np.c_[Q, np.zeros(len(Q))], max(MERGE, 0.25 * float(np.median(
        cKDTree(Q).query(Q, 2)[0][:, 1]))))
    if s is None:
        return None, None
    order = np.argsort(s)
    Q, s = Q[order], s[order]
    length = float(s[-1] - s[0])
    if length <= 0:
        return None, None
    lo, hi = s[0] - MARGIN * length, s[-1] + MARGIN * length
    spans = max(2, len(Q) // 4)
    while True:
        spans = min(spans, MAX_SPANS, max(2, len(Q) - 3))
        t = pipes._knots(lo, hi, spans)
        C = pipes._through(s, Q, t)
        for _ in range(3):      # (each point's parameter: its foot on the curve so far)
            c = BSpline(t, C, 3)
            s = pipes._foot(c, c.derivative(), c.derivative(2), Q, s, lo, hi)
            C = pipes._through(s, Q, t)
        c = BSpline(t, C, 3)
        dev = float(np.linalg.norm(c(s) - Q, axis=1).max())
        if dev <= FIT_DEV or spans >= min(MAX_SPANS, len(Q) - 3):
            break
        spans = int(math.ceil(spans * 1.5))
    if dev > FIT_DEV:
        return None, None
    # it must bow between its points no more than a smooth curve through them can
    for i in range(len(Q) - 1):
        a, b = Q[i], Q[i + 1]
        chord = float(np.linalg.norm(b - a))
        if chord < 1e-9:
            continue
        X = c(np.linspace(s[i], s[i + 1], 9)[1:-1])
        u = (b - a) / chord
        off = np.abs((X - a) @ np.array([-u[1], u[0]]))
        if off.max() > MAX_BULGE * chord + FIT_DEV:
            return None, None
    model = Extrusion(o, d, e1, e2, t, C, float(z.min()), float(z.max()))
    # every facet must face the way the profile does where it lies (a curve zigzagging
    # between the corners of two rows, two curves side by side, passes every corner but
    # faces every which way)
    agree = np.abs(np.einsum("ij,ij->i", mesh.fn[facets], model.normal(mesh.fcent[facets])))
    if agree.min() < math.cos(math.radians(NORMAL_DEG)):
        return None, None
    model.dev = model.rim_dev = dev
    return model, dev


def _square(mesh, a, d):
    return abs(mesh.fn[a] @ d) <= math.sin(math.radians(SQUARE_DEG))


def add_extrusions(mesh, features, soft, owner, unit):
    """Features with extrusions over the soft facets (and the pieces in them: stand-ins,
    and cylinders along the direction that a profile carries on from). Returns (new
    features, pieces they replaced, facets they took)."""
    out, replaced, taken = [], set(), set()
    if not ENABLED:
        return out, replaced, taken
    pool = set(soft)
    for k, f in enumerate(features):
        m = f.model
        if isinstance(m, Revolved) and m.line and m.line[1] == 0 and f.kind != "ball":
            pool.update(int(a) for a in f.facets)        # (a cylinder may be the middle of a profile)
    for d in design_directions(mesh, features):
        ok = {a for a in pool - taken if _square(mesh, a, d)}
        seen = set()
        for seed in sorted(ok):
            if seed in seen:
                continue
            group, stack = [], [seed]
            seen.add(seed)
            while stack:
                a = stack.pop()
                group.append(a)
                for b in mesh.nbrs[a]:
                    if b in ok and b not in seen:
                        seen.add(b)
                        stack.append(b)
            # whole pieces only (so one can be given back whole)
            group = set(group)
            pieces = {int(owner[a]) for a in group if owner[a] >= 0}
            pieces = {k for k in pieces if set(int(x) for x in features[k].facets) <= group}
            group = {a for a in group if owner[a] < 0 or int(owner[a]) in pieces}
            if len(group) < MIN_FACETS:
                continue
            # (cylinders along d alone are already what they are)
            loose = [a for a in group if owner[a] < 0 or not _along(features[int(owner[a])].model, d)]
            if not loose:
                continue
            facets = np.array(sorted(group))
            model, dev = fit(mesh, facets, d)
            if model is None:
                continue
            out.append(feature(mesh, model, facets, tuple(features[k] for k in sorted(pieces)), dev))
            replaced.update(pieces)
            taken.update(group)
    return out, replaced, taken


def _along(m, d):
    return isinstance(m, Revolved) and bool(m.line) and m.line[1] == 0 and abs(m.d @ d) > SAME_AXIS_COS


def feature(mesh, model, facets, parts, dev):
    """The patch for an extrusion (parts: the pieces it replaces, given back if its face
    can't be built); its volume change measured as for any exact surface."""
    tids = np.concatenate([mesh.ftris[f] for f in facets])
    corners = mesh.pts[mesh.tris[tids]]
    s = model.signed(((corners + corners[:, [1, 2, 0]]) / 2).reshape(-1, 3)).reshape(-1, 3)
    area = mesh.tarea[tids]
    outward = np.einsum("ij,ij->i", mesh.fn[facets], model.normal(mesh.fcent[facets])) @ mesh.farea[facets]
    convex = bool(outward > 0)
    change = (-1 if convex else 1) * float((area * s.mean(axis=1)).sum())
    gain = float((area * np.abs(s).mean(axis=1)).sum())
    return Feature(model, "extruded profile",
                   f"{len(facets)} facets, {len(model.t) - 7} spans, within {dev:.4f} mm of the mesh",
                   convex=convex, kind="trimmed", facets=np.asarray(facets), change=change,
                   tolerance=0.5 * gain + 1e-3, worst=max(float(np.abs(s).max()), dev), parts=tuple(parts))
