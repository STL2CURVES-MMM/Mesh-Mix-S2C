"""
Build a B-rep shell straight from the mesh and the detected curved patches.

Every flat facet of the mesh becomes one planar face; every detected patch becomes
one exact curved face (swept from its profile, so its outline is the patch's
natural boundary lines). Where a flat face borders a curved patch, that stretch of
its outline is replaced by the exact line or arc, so the faces meet cleanly. All
faces are then sewn together.
"""
import itertools
import math
import os
import shutil
import tempfile
import time

import numpy as np
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import (BRepBuilderAPI_Copy, BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire,
                                BRepBuilderAPI_Sewing, BRepBuilderAPI_MakeVertex)
from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol, BRepPrimAPI_MakeSphere, BRepPrimAPI_MakeBox
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Splitter
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.collections import HArray1_gp_Pnt
from OCP.collections import List_TopoDS_Shape
from OCP.GC import GC_MakeArcOfCircle
from OCP.GCPnts import GCPnts_AbscissaPoint
from OCP.GeomAdaptor import GeomAdaptor_Curve
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopLoc import TopLoc_Location
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.GeomLProp import GeomLProp_SLProps
from OCP.BRepFill import BRepFill_Filling
from OCP.GeomAbs import GeomAbs_C0, GeomAbs_Circle, GeomAbs_Line, GeomAbs_Plane
from OCP.ShapeFix import ShapeFix_Face, ShapeFix_Shape
from OCP.TopAbs import TopAbs_COMPOUND, TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD, TopAbs_SHELL, TopAbs_REVERSED, TopAbs_VERTEX
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Iterator, TopoDS_Shape
from OCP.BRepTools import BRepTools, BRepTools_ReShape
from OCP.collections import (IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher,
                             IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher)
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Pln, gp_Pnt
from OCP.Geom import (Geom_ToroidalSurface, Geom_SphericalSurface, Geom_CylindricalSurface, Geom_ConicalSurface,
                      Geom_Plane)
from OCP.GeomAPI import GeomAPI_IntSS, GeomAPI_ProjectPointOnCurve

from . import features as features_mod
from .accuracy import scale
ACCURACY = scale("build")
from . import workers
from .features import TWO_PI, _angle_gap, _frame, Revolved, Sphere
from .blends import TANGENT_DEG, MAX_DEVIATION, MAX_BULGE, MAX_EDGE_GAP


def _pnt(p):
    return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))


def _line(a, b):
    return BRepBuilderAPI_MakeEdge(_pnt(a), _pnt(b)).Edge()


def _arc(a, mid, b):
    return BRepBuilderAPI_MakeEdge(GC_MakeArcOfCircle(_pnt(a), _pnt(mid), _pnt(b)).Value()).Edge()


# ---------------------------------------------------------------- where a vertex sits on a patch

class Boundary:
    """Answers "which of the patch's boundary lines is this point on" and builds those lines exactly."""

    def __init__(self, feature, tol):
        self.f, self.m, self.tol = feature, feature.model, tol

    def labels(self, p):
        f, m, tol = self.f, self.m, self.tol
        if f.kind == "wedge":
            return {i for i, n in enumerate(f.planes) if abs((p - m.c) @ n) <= tol}
        if not isinstance(m, Revolved):
            return set()
        rho, z, w = m.local(p)
        rho, z = rho[0], z[0]
        out = set()
        t, scale = self._param(rho, z)
        if abs(t - f.lo) * scale <= tol:
            out.add("lo")
        if abs(t - f.hi) * scale <= tol:
            out.add("hi")
        if f.span < TWO_PI and rho > tol:
            u = m.angle(w)[0]
            if _angle_gap(u, f.u0) * rho <= tol:
                out.add("u0")
            if _angle_gap(u, f.u0 + f.span) * rho <= tol:
                out.add("u1")
        return out

    def _param(self, rho, z):
        m = self.m
        if m.line:
            return z, 1.0
        rc, zc, r = m.circle
        t = math.atan2(z - zc, rho - rc)
        # keep the angle on the same turn as the patch's range
        while t < self.f.lo - math.pi:
            t += TWO_PI
        while t > self.f.hi + math.pi:
            t -= TWO_PI
        return t, r

    def profile_point(self, t, u):
        m = self.m
        if m.line:
            c0, k = m.line
            rho = c0 + k * t
            return m.point(0.0 if abs(rho) < 1e-4 else rho, t, u)
        rc, zc, r = m.circle
        rho = rc + r * math.cos(t)
        return m.point(0.0 if abs(rho) < 1e-4 else rho, zc + r * math.sin(t), u)   # snap onto the axis

    def edge(self, label, pts):
        """Exact edge along boundary line `label` through the run of mesh points `pts`."""
        a, b = pts[0], pts[-1]
        m, f = self.m, self.f
        if f.kind == "wedge":
            mid = pts[len(pts) // 2] if len(pts) > 2 else self._sphere_mid(a, b)
            return _arc(a, mid, b)
        if label in ("u0", "u1"):
            if m.line:
                return _line(a, b)
            mid = pts[len(pts) // 2] if len(pts) > 2 else None
            if mid is None:
                u = f.u0 if label == "u0" else f.u0 + f.span
                ta = self._param(*[x[0] for x in m.local(a)[:2]])[0]
                tb = self._param(*[x[0] for x in m.local(b)[:2]])[0]
                mid = self.profile_point((ta + tb) / 2, u)
            return _arc(a, mid, b)
        # a circle round the axis
        if len(pts) > 2:
            return _arc(a, pts[len(pts) // 2], b)
        ra, za, wa = m.local(a)
        rb, zb, wb = m.local(b)
        ua, ub = m.angle(wa)[0], m.angle(wb)[0]
        du = math.remainder(ub - ua, TWO_PI)
        rho, z = (ra[0] + rb[0]) / 2, (za[0] + zb[0]) / 2
        return _arc(a, m.point(rho, z, ua + du / 2), b)

    def full_circle(self, label):
        m, f = self.m, self.f
        t = f.lo if label == "lo" else f.hi
        p = self.profile_point(t, 0.0)
        rho, z, _ = m.local(p)
        center = m.a + z[0] * m.d
        return BRepBuilderAPI_MakeEdge(gp_Circ(gp_Ax2(_pnt(center), gp_Dir(*m.d)), float(rho[0]))).Edge()

    def full_profile(self, label):
        """The whole profile circle of a torus at one end of its sweep (the round end of a
        bent tube's bore, where it meets the straight bore)."""
        f = self.f
        return BRepBuilderAPI_MakeEdge(_profile_circle(self, f.u0 if label == "u0" else f.u0 + f.span)).Edge()

    def _sphere_mid(self, a, b):
        c, r = self.m.c, self.m.r
        v = (a + b) / 2 - c
        return c + v / np.linalg.norm(v) * r


# ---------------------------------------------------------------- mesh boundary loops

def _loops(tris):
    """Directed boundary loops (lists of vertex ids) of a set of consistently wound triangles."""
    directed = {}
    for a, b, c in tris:
        for e in ((a, b), (b, c), (c, a)):
            directed[e] = True
    nxt = {}
    for (a, b) in directed:
        if (b, a) not in directed:
            nxt.setdefault(a, []).append(b)
    loops = []
    while nxt:
        start = next(iter(nxt))
        loop, v = [start], start
        while True:
            outs = nxt.get(v)
            if not outs:
                return None
            w = outs.pop()
            if not outs:
                del nxt[v]
            if w == start:
                break
            loop.append(w)
            v = w
            if len(loop) > len(directed) + 1:
                return None
        loops.append(loop)
    return loops


# ---------------------------------------------------------------- shared edges

def _crowded(pts, reach):
    """The mesh points with another point within reach. Sewing merges such points (and
    closes the edges between them) wherever faces arrive unjoined; faces joined on shared
    corners there kept the tiny edges while an unshared face beside them (a facet left
    as its triangles, a blend) had them closed up, and the two no longer met. So these
    corners aren't shared: sewing joins the faces round them as it always did."""
    from scipy.spatial import cKDTree
    pairs = cKDTree(pts).query_pairs(reach, output_type="ndarray")
    return set(int(i) for i in np.unique(pairs)) if len(pairs) else set()


class Edges:
    """Builds each boundary run once, so the two faces on either side share one edge.

    A run is a chain of mesh vertices where a face meets one neighbour. It becomes an
    exact line or arc if it follows one of a patch's boundary lines, otherwise a
    smooth spline through the mesh points (which lie on both surfaces).
    """

    def __init__(self, pts, pool=None, mesh=None, share=None, crowd=True):
        self.pts, self.cache = pts, {}
        self.mesh = mesh            # (for exact edges: the flat facets' planes)
        self.exacts = {}            # run key -> exact intersection edge, or None
        # Corners are shared only with a pool (build_faces': kept across a part's attempts,
        # so a flat face reused from an earlier attempt shares its corners and edges with
        # one built now). Blend fills (settle_blends) keep edges of their own: one fill on
        # edges rebuilt on shared corners ground on for 20 minutes.
        self.shared = (True if share is None else share) and pool is not None
        pool = {} if pool is None else pool
        self.corners = pool.setdefault("corners", {})   # mesh vertex id -> its TopoDS_Vertex
        self.lines = pool.setdefault("lines", {})       # (id, id) -> straight edge between them
        # (crowd=False: every corner shared, for a build that is never sewn)
        if self.shared and crowd and "crowded" not in pool:
            pool["crowded"] = _crowded(pts, SHARED_CROWD)
        self.crowded = pool.get("crowded", set()) if crowd else set()

    def corner(self, i):
        """The vertex at mesh point i, one for every edge ending there: faces whose edges
        share their ends (and edges) are joined already, and sewing is left only the
        seams it has to close."""
        if i in self.crowded:
            return BRepBuilderAPI_MakeVertex(_pnt(self.pts[i])).Vertex()
        if i not in self.corners:
            self.corners[i] = BRepBuilderAPI_MakeVertex(_pnt(self.pts[i])).Vertex()
        return self.corners[i]

    def line(self, i, j):
        """The straight edge from mesh vertex i to j (one per pair, shared by the faces
        either side when the build shares its corners)."""
        if not self.shared or i in self.crowded or j in self.crowded:
            return _line(self.pts[i], self.pts[j])
        key = (min(i, j), max(i, j))
        if key not in self.lines:
            maker = BRepBuilderAPI_MakeEdge(self.corner(key[0]), self.corner(key[1]))
            # (an edge shorter than its corners' tolerances won't make: on its own corners)
            self.lines[key] = maker.Edge() if maker.IsDone() else _line(self.pts[key[0]], self.pts[key[1]])
        edge = self.lines[key]
        return edge if i == key[0] else TopoDS.Edge(edge.Reversed())

    def _on_corners(self, edge, first, last):
        """The edge rebuilt on the shared vertices at mesh points first and last (it starts
        and ends at those points already); the edge as it was if OCC won't, or if either
        point is crowded (left to sewing)."""
        if first in self.crowded or last in self.crowded:
            return edge
        if (TopExp.FirstVertex_s(edge, True).IsSame(self.corner(first))
                and TopExp.LastVertex_s(edge, True).IsSame(self.corner(last))):
            return edge
        if edge.Orientation() == TopAbs_REVERSED:
            return TopoDS.Edge(self._on_corners(TopoDS.Edge(edge.Reversed()), last, first).Reversed())
        try:
            c = BRepAdaptor_Curve(edge)
            curve = BRep_Tool.Curve_s(edge, 0.0, 0.0)
            maker = BRepBuilderAPI_MakeEdge(curve, self.corner(first), self.corner(last),
                                            c.FirstParameter(), c.LastParameter())
            return maker.Edge() if maker.IsDone() else edge
        except Exception:
            return edge

    def exact(self, ids, make):
        """The run's exact edge (make: canonical ids -> edge or None), cached like get but
        None kept as an answer: the caller then builds the run as before."""
        ids = [int(i) for i in ids]
        if len(ids) > 2 and ids[0] == ids[-1]:
            return None
        flip = ids[0] > ids[-1]
        canon = ids[::-1] if flip else ids
        key = tuple(canon)
        if key not in self.exacts:
            try:
                self.exacts[key] = make(canon)
            except Exception:
                self.exacts[key] = None
        edge = self.exacts[key]
        if edge is None:
            return None
        return TopoDS.Edge(edge.Reversed()) if flip else edge

    def get(self, ids, make):
        ids = [int(i) for i in ids]
        if len(ids) > 2 and ids[0] == ids[-1]:
            # a closed loop: canonical start (lowest vertex id) and direction
            ring = ids[:-1]
            i = ring.index(min(ring))
            ring = ring[i:] + ring[:i]
            flip = ring[1] > ring[-1]
            if flip:
                ring = [ring[0]] + ring[1:][::-1]
            canon = ring + [ring[0]]
            key = tuple(canon) + ("closed",)
        else:
            flip = ids[0] > ids[-1]
            canon = ids[::-1] if flip else ids
            key = tuple(canon)
        if key not in self.cache:
            try:
                self.cache[key] = make(canon)
            except Exception:           # e.g. an arc through three nearly aligned points
                self.cache[key] = None
            if self.cache[key] is None and make != self.spline:
                self.cache[key] = self.spline(canon)
            if self.shared and self.cache[key] is not None:
                self.cache[key] = self._on_corners(self.cache[key], canon[0], canon[-1])
        edge = self.cache[key]
        if edge is None:
            return None
        return TopoDS.Edge(edge.Reversed()) if flip else edge

    def spline(self, ids):
        pts = self.pts[ids]
        closed = len(ids) > 3 and ids[0] == ids[-1]
        if closed:
            pts = pts[:-1]
        keep = np.r_[True, np.linalg.norm(np.diff(pts, axis=0), axis=1) > 1e-6]
        pts = pts[keep]
        if len(pts) == 2 and not closed:
            return self.line(ids[0], ids[-1])
        pts = _even(pts, closed)
        arr = HArray1_gp_Pnt(1, len(pts))
        for i, p in enumerate(pts):
            arr.SetValue(i + 1, _pnt(p))
        interp = GeomAPI_Interpolate(arr, closed, 1e-7)
        interp.Perform()
        if not interp.IsDone():
            return None
        return BRepBuilderAPI_MakeEdge(interp.Curve()).Edge()


LONG_STEP = 3.0     # a step this many times the run's typical one gets points along it
PARALLEL_CUTS = 8   # patches: this many faces to cut at once go to the worker processes
CUT_SECONDS = 60    # a cut in a worker still running after this long, with nothing else left, counts as failed
LONG_OUTLINE = 1000  # edges: a patch with an outline this long is cut in a worker even alone (so CUT_SECONDS holds)
OUTLINE_FIRST = 300     # facets: a freeform patch this big gets its face from its outline first
SHARED_TOLERANCE = 0.005    # mm: a flat face mended within this keeps its shared edges and corners
SHARED_CROWD = 0.02     # mm: a mesh point this near another keeps corners of its own, for sewing to merge
REJOIN_SNAP = 1e-7      # mm: corners this near one mesh point are that point (copies read back from files)
MAX_FACE_TOL = 1.0      # mm: a patch's face whose corners or edges needed wider tolerances than this doesn't fit
WIDE_FUZZ = 1.25        # a patch's last try splits its surface with a fuzzy value this many times
                        # the furthest its outline corners are off it
# Experiments, off unless named in the environment variable STL2CURVES_TRY (comma-separated),
# so each can be tried on its own in full regressions: exact (and swap, see convert)
TRYING = {x.strip() for x in os.environ.get("STL2CURVES_TRY", "").split(",") if x.strip()}
EXACT_EDGES = "exact" in TRYING         # a run between two analytic surfaces is their exact intersection
EXACT_DEV = 0.02        # mm: most a run's mesh points may lie off that curve (else a spline)


def _even(pts, closed):
    """The run's points with points added along its long steps. A tessellator puts few
    points on a straight stretch and many round a bend; a spline through such a run
    swings far out between the sparse ones (half a millimetre off a bend's straight
    run-out), and no surface it should lie on contains it. On a run of a few steps the
    shortest is the measure (two steps, 48 mm and 1 mm: the median, their mean, made
    neither long, and the spline swung 0.9 mm off the straight)."""
    ring = np.vstack([pts, pts[:1]]) if closed else pts
    step = np.linalg.norm(np.diff(ring, axis=0), axis=1)
    typical = float(np.median(step)) if len(step) > 3 else float(step.min())
    if step.max() <= LONG_STEP * typical:
        return pts
    out = []
    for a, b, d in zip(ring[:-1], ring[1:], step):
        n = int(min(16, math.ceil(d / typical))) if d > LONG_STEP * typical else 1
        out += [a + (b - a) * t for t in np.arange(n) / n]
    if not closed:
        out.append(ring[-1])
    return np.array(out)


def _analytic_surface(model):
    """The model's surface as an OCC surface (cylinder, cone, torus, sphere), or None."""
    if isinstance(model, Sphere):
        return Geom_SphericalSurface(gp_Ax3(_pnt(model.c), gp_Dir(0, 0, 1)), float(model.r))
    if not isinstance(model, Revolved):
        return None
    a, d = np.asarray(model.a, float), np.asarray(model.d, float)
    if model.line:
        c0, k = model.line
        if abs(k) < 1e-9:
            return Geom_CylindricalSurface(gp_Ax3(_pnt(a), gp_Dir(*d)), float(c0)) if c0 > 0 else None
        if abs(c0) < 1e-9 or abs(math.atan(k)) >= math.pi / 2 - 1e-6:
            return None
        return Geom_ConicalSurface(gp_Ax3(_pnt(a), gp_Dir(*d)), float(math.atan(k)), float(c0)) if c0 > 0 else None
    rc, zc, r = model.circle
    if rc <= 0 or r <= 0:
        return None
    return Geom_ToroidalSurface(gp_Ax3(_pnt(a + zc * d), gp_Dir(*d)), float(rc), float(r))


def _side_surface(mesh, bounds, side):
    """The surface on one side of a run: ("patch", k) or a flat facet's id."""
    if isinstance(side, tuple):
        return _analytic_surface(bounds[side[1]].m)
    centre = mesh.pts[mesh.fverts[side]].mean(axis=0)
    return Geom_Plane(gp_Pln(_pnt(centre), gp_Dir(*mesh.fn[side])))


def _exact_edge(mesh, edges, bounds, k, other, ids):
    """The exact intersection of patch k's surface and the other side's (a flat facet or
    patch), between the run's end corners, if every mesh point of the run lies within
    EXACT_DEV of it; else None (the run becomes a spline)."""
    s1 = _analytic_surface(bounds[k].m)
    s2 = _side_surface(mesh, bounds, other)
    if s1 is None or s2 is None:
        return None
    pts = mesh.pts[ids]
    closed = len(ids) > 3 and ids[0] == ids[-1]
    if closed:
        return None                         # (whole loops: left as they are, for now)
    inter = GeomAPI_IntSS(s1, s2, 1e-7)
    if not inter.IsDone() or inter.NbLines() == 0:
        return None
    best = None
    for i in range(1, inter.NbLines() + 1):
        curve = inter.Line(i)
        params, worst = [], 0.0
        for p in pts:
            proj = GeomAPI_ProjectPointOnCurve(_pnt(p), curve)
            if proj.NbPoints() == 0:
                worst = math.inf
                break
            params.append(proj.LowerDistanceParameter())
            worst = max(worst, proj.LowerDistance())
        if worst <= EXACT_DEV and (best is None or worst < best[0]):
            best = (worst, curve, params)
    if best is None:
        return None
    worst, curve, params = best
    t0, t1 = params[0], params[-1]
    if curve.IsPeriodic():
        # the way round that passes the middle points (a single mesh edge has none: the
        # shorter way; taking the other gave a 0.2 mm chord a 1,055 mm ellipse)
        period = curve.Period()
        t1 = t0 + (t1 - t0) % period
        if len(params) > 2:
            mid = params[len(params) // 2]
            if not t0 <= t0 + (mid - t0) % period <= t1:
                t1 -= period
        elif t1 - t0 > period / 2:
            t1 -= period
    reverse = t1 < t0
    lo, hi = (t1, t0) if reverse else (t0, t1)
    if hi - lo < 1e-9:
        return None
    # (and no longer than the run itself, give or take a chord's sag: else a wrong branch)
    chord = float(np.linalg.norm(np.diff(pts, axis=0), axis=1).sum())
    length = GCPnts_AbscissaPoint.Length_s(GeomAdaptor_Curve(curve, lo, hi))
    if abs(length - chord) > 0.02 * chord + EXACT_DEV / 4:
        return None
    # the corners sit off the exact curve by up to the mesh's error: their tolerance covers it
    builder = BRep_Builder()
    for end, t in ((ids[0], t0), (ids[-1], t1)):
        v = edges.corner(end)
        gap = curve.Value(t).Distance(_pnt(mesh.pts[end]))
        if gap * 1.2 + 1e-7 > BRep_Tool.Tolerance_s(v):
            builder.UpdateVertex(v, gap * 1.2 + 1e-7)
    first, last = (edges.corner(ids[-1]), edges.corner(ids[0])) if reverse else (edges.corner(ids[0]), edges.corner(ids[-1]))
    maker = BRepBuilderAPI_MakeEdge(curve, first, last, lo, hi)
    if not maker.IsDone():
        return None
    edge = maker.Edge()
    _exact_edge.count += 1
    return TopoDS.Edge(edge.Reversed()) if reverse else edge


_exact_edge.count = 0


def _run_edge(edges, bounds, tag, ids, patch_side=False):
    """Edge for a run of mesh vertices; tag = (patch, boundary line or None, ...).

    A single mesh edge along a trimmed outline is a chord of the true curve. The flat
    facet next to it keeps the chord; the patch gets an arc bowed onto its own surface
    (sewing closes the tiny gap, which is no bigger than the facet's own error).
    """
    k, label = tag[0], tag[1]
    if EXACT_EDGES and label is None and len(tag) > 2 and tag[2] is not None and edges.mesh is not None:
        edge = edges.exact(ids, lambda canon: _exact_edge(edges.mesh, edges, bounds, k, tag[2], canon))
        if edge is not None:
            return edge
    if label is None and len(ids) == 2:
        # (a smooth blend's or freeform surface's face is fitted to its outline, so the
        # chord serves it; a bow would leave the flat face beside it an edge off its plane,
        # which then won't merge with the flat faces round it)
        if not patch_side or bounds[k].f.kind == "blend" or bounds[k].m.kind in ("freeform", "pipe", "extrusion"):
            return edges.line(int(ids[0]), int(ids[1]))
        m = bounds[k].m
        a, b = edges.pts[ids[0]], edges.pts[ids[1]]
        mid = ((a + b) / 2)[None]
        bow = m.signed(mid)[0]
        # (no more than a chord this long can sag on the surface's tightest curve: a mesh
        # edge running across the surface rather than along it can project its middle
        # far off, and the arc through it left a torus face's corners 19 mm "tolerant")
        if abs(bow) > 2 * float(np.sum((a - b) ** 2)) / (8 * _tightest_radius(m)) + 1e-4:
            return edges.line(int(ids[0]), int(ids[1]))
        return _safe_arc(a, mid[0] - bow * m.normal(mid)[0], b)
    if label is None:
        return edges.get(ids, edges.spline)
    bound = bounds[k]
    # (only for a patch all the way round: on a D-shaped hole, a cylinder 298 deg round
    # with a flat across the rest, every corner of the end face's hole is on the circle,
    # the flat's two ends too, and the hole came out round, the flat's edge gone)
    if (label in ("lo", "hi") and len(ids) > 3 and ids[0] == ids[-1] and bound.f.kind != "wedge"
            and bound.f.span >= TWO_PI - 1e-9):
        # a whole natural boundary circle: the exact circle (as the face beside it uses)
        return _following(bound.full_circle(label), edges.pts[ids])
    if (label in ("u0", "u1") and len(ids) > 3 and ids[0] == ids[-1] and bound.f.kind != "wedge"
            and not bound.m.line and bound.f.hi - bound.f.lo >= TWO_PI - 1e-9):
        # likewise the whole profile circle of a torus
        return _following(bound.full_profile(label), edges.pts[ids])
    return edges.get(ids, lambda canon: bound.edge(label, edges.pts[canon]))


def _following(edge, pts):
    """A whole-circle edge turned to run the way the loop of points round it does (a
    circle is made running anticlockwise about its axis; a hole wire running the same
    way as its face's outer wire made the face invalid, and its solid's volume
    thousands of cubic millimetres off)."""
    circle = BRepAdaptor_Curve(edge).Circle()
    axis = circle.Axis().Direction()
    centre = circle.Location()
    rel = pts - np.array([centre.X(), centre.Y(), centre.Z()])
    turn = np.cross(rel[:-1], rel[1:]).sum(axis=0) @ np.array([axis.X(), axis.Y(), axis.Z()])
    return TopoDS.Edge(edge.Reversed()) if turn < 0 else edge


def _tightest_radius(m):
    """The smallest radius of curvature anywhere on the model's surface (mm)."""
    if isinstance(m, Sphere):
        return m.r
    if isinstance(m, Revolved):
        if m.circle:
            return m.circle[2]
        return max(abs(m.line[0]), 1e-3) if m.line[1] == 0 else 1e-3
    return 1e-3


def _safe_arc(a, mid, b):
    try:
        return _arc(a, mid, b)
    except Exception:
        return _line(a, b)


def _runs(loop, tags):
    """Split a closed loop of vertices into runs of equal tag: [(tag, [vertex ids...]), ...]."""
    n = len(loop)
    start = next((i for i in range(n) if tags[i] != tags[i - 1]), 0)
    out, i = [], 0
    while i < n:
        j = (start + i) % n
        tag = tags[j]
        run = [loop[j]]
        while i < n and tags[(start + i) % n] == tag:
            run.append(loop[(start + i + 1) % n])
            i += 1
            if tag is None:
                break
        out.append((tag, run))
    return out


SHARP_DEG = 30   # a run of mesh vertices turning this sharply at a vertex is split there


def _split_sharp(runs, pts):
    """Split free-form runs (splines through mesh points) at sharp corners: a smooth curve
    forced through a hairpin overshoots wildly. Depends only on the vertex chain, so both
    faces beside a run split it the same way."""
    out = []
    for tag, run in runs:
        if tag is None or tag[1] is not None or len(run) < 3:
            out.append((tag, run))
            continue
        closed = run[0] == run[-1]
        ring = run[:-1] if closed else run
        n = len(ring)

        def sharp(i):
            a, b, c = pts[ring[i - 1]], pts[ring[i]], pts[ring[(i + 1) % n]]
            u, v = b - a, c - b
            nu, nv = np.linalg.norm(u), np.linalg.norm(v)
            return nu > 0 and nv > 0 and u @ v < math.cos(math.radians(SHARP_DEG)) * nu * nv

        corners = [i for i in (range(n) if closed else range(1, n - 1)) if sharp(i)]
        if not corners:
            out.append((tag, run))
            continue
        if closed:
            # start at the corner with the lowest vertex id, so both sides agree
            start = min(corners, key=lambda i: ring[i])
            ring = ring[start:] + ring[:start]
            corners = sorted((i - start) % n for i in corners)
            ring = ring + [ring[0]]
            cuts = corners + [n]
        else:
            ring = list(ring)
            cuts = [0] + corners + [n - 1]
        for i, j in zip(cuts, cuts[1:]):
            out.append((tag, ring[i:j + 1]))
    return out


def _loop_tags(mesh, loop, owner, bounds, edge_tri, patch=None, own=None):
    """Tag each edge of a boundary loop.

    None: a plain mesh edge between two flat facets. Otherwise (patch, boundary line):
    a curved patch is involved (the loop's own patch if `patch` is given, else the
    neighbouring one); the boundary line is the patch's line the edge runs along, or
    None if it runs along a trimmed outline. On a patch's own outline a third item
    records the neighbour, so runs split exactly where the neighbouring face's do; on a
    flat facet's outline it is the facet itself (own), so both sides of a run know both
    surfaces (EXACT_EDGES).
    """
    pts, n, tags = mesh.pts, len(loop), []
    for i in range(n):
        a, b = loop[i], loop[(i + 1) % n]
        other = edge_tri.get((b, a))
        k = owner[mesh.facet_of[other]] if other is not None else -1
        neighbour = ("patch", int(k)) if k >= 0 else (int(mesh.facet_of[other]) if other is not None else None)
        ref = patch if patch is not None else k
        if ref < 0:
            tags.append(None)
            continue
        both = bounds[ref].labels(pts[a]) & bounds[ref].labels(pts[b])
        tags.append((ref, min(both) if both else None, neighbour if patch is not None else own))
    return tags


def _wire(mesh, loop, tags, edges, bounds):
    wire = BRepBuilderAPI_MakeWire()
    pts = mesh.pts
    for tag, run in _split_sharp(_runs(loop, tags), pts):
        if tag is None:
            wire.Add(edges.line(int(run[0]), int(run[1])))
            continue
        edge = _run_edge(edges, bounds, tag, run)
        if edge is None:
            return None
        wire.Add(edge)
    return wire.Wire() if wire.IsDone() else None


# ---------------------------------------------------------------- faces

def _count(shape, kind):
    """How many sub-shapes of this kind the shape has (each counted once)."""
    m = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, kind, m)
    return m.Extent()


def _max_tolerance(shape):
    """The widest tolerance of the shape's edges and vertices."""
    worst = 0.0
    for kind in (TopAbs_EDGE, TopAbs_VERTEX):
        ex = TopExp_Explorer(shape, kind)
        while ex.More():
            x = ex.Current()
            worst = max(worst, BRep_Tool.Tolerance_s(TopoDS.Edge(x)) if kind == TopAbs_EDGE
                        else BRep_Tool.Tolerance_s(TopoDS.Vertex(x)))
            ex.Next()
    return worst


def _planar_face(mesh, fid, owner, bounds, edge_tri, edges):
    """The flat facet's face; a facet whose outline touches itself (two holes meeting
    its edge at one corner, say) as one face per group of its triangles whose outline
    doesn't (a compound), rather than hundreds of triangles; None if that fails too."""
    tris = mesh.tris[mesh.ftris[fid]]
    face = _plane_face(mesh, fid, tris, owner, bounds, edge_tri, edges)
    if face is not _PINCHED:
        return face
    groups = _unpinched(tris)
    if len(groups) < 2 or len(groups) > PINCH_GROUPS:
        return None
    faces = [_plane_face(mesh, fid, tris[g], owner, bounds, edge_tri, edges) for g in groups]
    if any(f is None or f is _PINCHED for f in faces):
        return None
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for f in faces:
        builder.Add(comp, f)
    return comp


_PINCHED = object()
PINCH_GROUPS = 8        # a pinched flat facet is built in at most this many pieces


def _unpinched(tris):
    """The triangles in groups (index arrays), each grown across shared edges but never
    onto a triangle that would touch the group at a corner alone (a pinch)."""
    by_edge = {}
    for t, (a, b, c) in enumerate(tris):
        for e in ((a, b), (b, c), (c, a)):
            by_edge.setdefault(frozenset(e), []).append(t)
    group = np.full(len(tris), -1)
    out = []
    for start in range(len(tris)):
        if group[start] >= 0:
            continue
        g = len(out)
        group[start] = g
        members, verts, stack = [start], set(tris[start].tolist()), [start]
        while stack:
            t = stack.pop()
            a, b, c = tris[t]
            for e in ((a, b), (b, c), (c, a)):
                for u in by_edge[frozenset(e)]:
                    if group[u] >= 0:
                        continue
                    third = [v for v in tris[u].tolist() if v not in e][0]
                    if third in verts and not any(
                            group[w] == g for x in e for w in by_edge.get(frozenset((x, third)), ()) if w != u):
                        continue
                    group[u] = g
                    members.append(u)
                    verts.add(third)
                    stack.append(u)
        out.append(np.array(members))
    return out


def _plane_face(mesh, fid, tris, owner, bounds, edge_tri, edges):
    """A planar face over these triangles of the facet; _PINCHED if their outline
    touches itself (that makes an invalid face)."""
    loops = _loops(tris)
    if loops is None:
        return None
    ring = [v for loop in loops for v in loop]
    if len(ring) != len(set(ring)):
        return _PINCHED
    pts = mesh.pts
    # outer boundary first: the loop enclosing the most area
    normal = mesh.fn[fid]
    # (each loop's area worked out once: three times over, for 300,000 facets, it was a
    # minute of a big mesh's first build)
    areas = [abs(np.cross(pts[loop], pts[np.roll(loop, -1)]).sum(axis=0) @ normal) for loop in loops]
    order = sorted(range(len(loops)), key=areas.__getitem__, reverse=True)
    loops = [loops[k] for k in order]
    if areas[order[-1]] < 1e-9 * areas[order[0]] + 1e-9:
        return None     # a slit (an outline enclosing nothing): left as triangles
    wires = []
    for loop in loops:
        n = len(loop)
        tags = _loop_tags(mesh, loop, owner, bounds, edge_tri, own=int(fid))
        whole = all(t is not None and t == tags[0] for t in tags)
        if whole and tags[0][1] in ("lo", "hi") and bounds[tags[0][0]].f.kind == "revolve":
            bound = bounds[tags[0][0]]
            edge = bound.full_circle(tags[0][1])
            # the circle runs anticlockwise about the axis; match the loop's direction
            turn = sum(np.cross(pts[loop[i]], pts[loop[(i + 1) % n]]) for i in range(n)) @ bound.m.d
            w = BRepBuilderAPI_MakeWire()
            w.Add(TopoDS.Edge(edge.Reversed()) if turn < 0 else edge)
            wires.append(w.Wire())
            continue
        if whole:
            w = BRepBuilderAPI_MakeWire()
            for tag, run in _split_sharp([(tags[0], loop + [loop[0]])], pts):
                edge = _run_edge(edges, bounds, tag, run)
                if edge is None:
                    return None
                w.Add(edge)
            if not w.IsDone():
                return None
            wires.append(w.Wire())
            continue
        wire = _wire(mesh, loop, tags, edges, bounds)
        if wire is None:
            return None
        wires.append(wire)
    # the plane through the middle of the corners: a facet joined across rounding noise
    # has corners a little either side of it
    plane = gp_Pln(_pnt(pts[mesh.fverts[fid]].mean(axis=0)), gp_Dir(*normal))
    maker = BRepBuilderAPI_MakeFace(plane, wires[0], True)
    for w in wires[1:]:
        maker.Add(w)
    if not maker.IsDone():
        return None
    face = maker.Face()
    own = False         # (shared: is the face on copies of its edges now?)
    if edges.shared:
        # ShapeFix widens the tolerances of the edges and corners it is given, in place,
        # as far as a gap in the outline needs (a millimetre, once), and drops or merges
        # edges it finds too small: shared, that would spread to every face round them
        # (and later builds reusing them). So it tries a copy first, and only a face it
        # mends within SHARED_TOLERANCE, keeping every edge, is mended on its shared
        # edges; the rest keep their own copies, as every face did before corners were
        # shared, for sewing to join.
        probe = ShapeFix_Face(TopoDS.Face(BRepBuilderAPI_Copy(face).Shape()))
        probe.Perform()
        if (_max_tolerance(probe.Face()) > SHARED_TOLERANCE
                or _count(probe.Face(), TopAbs_EDGE) != _count(face, TopAbs_EDGE)):
            face, own = probe.Face(), True
        else:
            fix = ShapeFix_Face(face)
            fix.Perform()
            face = fix.Face()
    else:
        fix = ShapeFix_Face(face)
        fix.Perform()
        face = fix.Face()
    if mesh.noise and not BRepCheck_Analyzer(face).IsValid():
        if edges.shared and not own:
            face = TopoDS.Face(BRepBuilderAPI_Copy(face).Shape())
        # (edges a rounding step off the plane: widen their tolerances to match)
        whole = ShapeFix_Shape(face)
        whole.SetPrecision(mesh.noise)
        whole.SetMaxTolerance(10 * mesh.noise)
        whole.Perform()
        ex = TopExp_Explorer(whole.Shape(), TopAbs_FACE)
        if ex.More():
            face = TopoDS.Face(ex.Current())
    return face


def _patch_faces(feature, bound, mesh):
    m = feature.model
    if feature.kind == "ball":
        ex = TopExp_Explorer(BRepPrimAPI_MakeSphere(_pnt(m.c), m.r).Shape(), TopAbs_SHELL)
        shell = TopoDS.Shell(ex.Current())
        return [shell if feature.convex else TopoDS.Shell(shell.Reversed())]
    if feature.kind == "wedge":
        return [_wedge_face(feature, bound, mesh)]
    return [_revolved_face(bound, feature.lo, feature.hi, feature.u0, feature.span)]


def _plain_torus(m, lo, hi):
    """Would sweeping this profile give a general surface of revolution rather than a
    torus (a tube all round, or a bend tighter than its tube)?"""
    return bool(m.circle) and m.circle[0] > 0 and (hi - lo >= TWO_PI - 1e-9 or m.circle[0] < m.circle[2])


def _revolved_face(bound, lo, hi, u0, span, exact=True):
    m = bound.m
    if exact and _plain_torus(m, lo, hi):
        # there the torus is made directly (the model's frame d, e1, e2 is right-handed
        # and its profile angle is the torus's own v); exact=False sweeps it all the same
        rc, zc, r = m.circle
        torus = Geom_ToroidalSurface(gp_Ax3(_pnt(m.a + zc * m.d), gp_Dir(*m.d), gp_Dir(*m.e1)), rc, r)
        return BRepBuilderAPI_MakeFace(torus, u0, u0 + span, lo, hi, 1e-7).Face()
    if m.line:
        edge = _line(bound.profile_point(lo, u0), bound.profile_point(hi, u0))
    elif hi - lo >= TWO_PI - 1e-9:
        edge = BRepBuilderAPI_MakeEdge(_profile_circle(bound, u0)).Edge()   # a tube all round
    else:
        edge = _arc(bound.profile_point(lo, u0), bound.profile_point((lo + hi) / 2, u0),
                    bound.profile_point(hi, u0))
    revol = BRepPrimAPI_MakeRevol(edge, gp_Ax1(_pnt(m.a), gp_Dir(*m.d)), span)
    return revol.Shape() if revol.IsDone() else None


def _profile_circle(bound, u):
    """A torus's whole profile circle, in the half-plane at angle u round the axis."""
    m = bound.m
    rc, zc, r = m.circle
    centre = m.point(rc, zc, u)
    out = (bound.profile_point(0.0, u) - centre) / r      # away from the axis
    return gp_Circ(gp_Ax2(_pnt(centre), gp_Dir(*np.cross(out, m.d)), gp_Dir(*out)), r)


def _sphere_face(center, r, avoid):
    """Whole sphere face with its seam and poles turned away from the directions `avoid`."""
    avoid = [np.asarray(n, float) for n in avoid]
    mean = np.mean(avoid, axis=0)
    d, e1, e2 = _frame(-mean if np.linalg.norm(mean) > 1e-6 else avoid[0])
    poles = [math.cos(a) * e1 + math.sin(a) * e2 for a in np.radians(np.arange(0, 180, 7.5))]
    pole = max(poles, key=lambda z: min(abs(z @ n) for n in avoid))
    ball = BRepPrimAPI_MakeSphere(gp_Ax2(_pnt(center), gp_Dir(*pole), gp_Dir(*d)), r).Shape()
    return TopoDS.Face(TopExp_Explorer(ball, TopAbs_FACE).Current())


def _generous_surface(feature, bound, mesh, exact=True):
    """A piece of the patch's surface comfortably bigger than the patch (exact: see
    _revolved_face)."""
    m = feature.model
    if isinstance(m, Sphere):
        return _sphere_face(m.c, m.r, [mesh.fn[f] for f in feature.facets])
    if m.kind in ("freeform", "pipe", "extrusion"):
        return m.face()     # (fitted with a margin all round already)
    lo, hi, u0, span = feature.lo, feature.hi, feature.u0, feature.span
    if m.line:
        margin = 0.2 * (hi - lo) + 0.5
        new_lo, new_hi = lo - margin, hi + margin
        c0, k = m.line
        if k != 0:  # don't run a cone past its tip
            apex = -c0 / k
            if new_lo < apex <= lo:
                new_lo = apex
            if hi <= apex < new_hi:
                new_hi = apex
        lo, hi = new_lo, new_hi
    else:
        rc, zc, r = m.circle
        margin = math.radians(10)
        if hi - lo >= TWO_PI - 1e-9:
            margin = 0.0                # a tube all round: nothing to add
        # (less on a side where the margin would run the profile across the axis: a
        # bent tube whose bend is tighter than the tube is wide passes close to it)
        crosses = lambda a, b: (rc + r * np.cos(np.linspace(a, b, 60)) < 0).any()
        grow_lo = next((g for g in margin * np.array([1, 0.5, 0.25, 0.1]) if not crosses(lo - g, lo)), 0.0)
        grow_hi = next((g for g in margin * np.array([1, 0.5, 0.25, 0.1]) if not crosses(hi, hi + g)), 0.0)
        lo, hi = lo - grow_lo, hi + grow_hi
        if margin and hi - lo >= TWO_PI - 1e-6:
            return None
        if crosses(lo, hi):
            return None             # would cross the axis
    if span < TWO_PI:
        grow = math.radians(10)
        if span + 2 * grow >= TWO_PI:
            u0, span = 0.0, TWO_PI
        else:
            u0, span = u0 - grow, span + 2 * grow
    return _revolved_face(bound, lo, hi, u0, span, exact)


THREAD_SAMPLES = 48     # points a turn on a thread's helices (a B-spline through them is
                        # well under a micron off)


def _thread_surface(feature, m):
    """A piece of a thread's profile (threads.Flank) swept along its helix, reaching a
    little past the patch each way: the ruled surface between the helices at the
    piece's two ends (exact for a straight piece), as a B-spline surface whose
    parameters are the model's own (U, s): helix angle, and distance along the piece."""
    from OCP.Geom import Geom_BSplineSurface
    from OCP.collections import Array1_double, Array1_int, Array2_gp_Pnt, HArray1_double
    grow = 0.2 * (feature.hi - feature.lo) + 0.02
    lo, hi = feature.lo - grow, feature.hi + grow
    if min(m.p[1] + lo * m.t[1], m.p[1] + hi * m.t[1]) <= 0:
        return None                     # would cross the axis
    margin = math.radians(10)
    U = np.linspace(feature.u0 - margin, feature.u0 + feature.span + margin,
                    max(8, int(math.ceil((feature.span + 2 * margin) / TWO_PI * THREAD_SAMPLES)) + 1))
    params = HArray1_double(1, len(U))
    for i, u in enumerate(U):
        params.SetValue(i + 1, float(u))
    curves = []
    for s in (lo, hi):
        arr = HArray1_gp_Pnt(1, len(U))
        for i, p in enumerate(m.point(np.full(len(U), s), U)):
            arr.SetValue(i + 1, _pnt(p))
        interp = GeomAPI_Interpolate(arr, params, False, 1e-9)
        interp.Perform()
        if not interp.IsDone():
            return None
        curves.append(interp.Curve())
    a, b = curves                       # (the same parameters: the same knots)
    poles = Array2_gp_Pnt(1, a.NbPoles(), 1, 2)
    for i in range(1, a.NbPoles() + 1):
        poles.SetValue(i, 1, a.Pole(i))
        poles.SetValue(i, 2, b.Pole(i))
    uk, um = Array1_double(1, a.NbKnots()), Array1_int(1, a.NbKnots())
    for i in range(1, a.NbKnots() + 1):
        uk.SetValue(i, a.Knot(i))
        um.SetValue(i, a.Multiplicity(i))
    vk, vm = Array1_double(1, 2), Array1_int(1, 2)
    vk.SetValue(1, lo)
    vk.SetValue(2, hi)
    vm.SetValue(1, 2)
    vm.SetValue(2, 2)
    return Geom_BSplineSurface(poles, uk, vk, um, vm, a.Degree(), 1)


def _thread_face(feature, k, mesh, owner, bounds, edge_tri, straight=False):
    """A thread piece's face, built in its surface's own parameters: each outline corner
    is placed on the piece as (U, s) and the outline drawn there, so its edges lie on
    the surface exactly. (Splines through the corners themselves, a few microns off the
    true thread, won't cut a long helical surface, and neither will their projections.)
    Where the outline follows the piece's own ends (a crest or root edge) it runs along
    s = its end exactly: a helix, the same one the next piece has. straight: the outline
    runs straight between its corners in (U, s) instead of in splines through them
    (tried when the splines fail: the two sides of a thin spike, a corner reaching
    0.1 mm further across the flank than its neighbours, leave its tip nearly
    parallel and their bend crosses them; the gutter mount's threaded hole lost a
    1,116-facet flank piece so)."""
    from OCP.BRepLib import BRepLib
    from OCP.Geom2dAPI import Geom2dAPI_Interpolate
    from OCP.collections import HArray1_gp_Pnt2d
    from OCP.gp import gp_Pnt2d
    from .threads import piece_tol
    m = feature.model
    surface = _thread_surface(feature, m)
    if surface is None:
        return None
    loops = _loops(np.concatenate([mesh.tris[mesh.ftris[f]] for f in feature.facets]))
    if not loops:
        return None
    snap = piece_tol(m.thread) + mesh.noise
    wires, spans = [], []
    for loop in loops:
        tags = _loop_tags(mesh, loop, owner, bounds, edge_tri, patch=k)
        runs = [(tags[0], loop + [loop[0]])] if all(t == tags[0] for t in tags) else _runs(loop, tags)
        wire = BRepBuilderAPI_MakeWire()
        uv = []
        loose = _notches(m, mesh.pts[loop], snap)
        loose = {p for p, n in zip(loop, loose) if n}
        for _, run in _split_sharp(runs, mesh.pts):
            s, _, U = m.place(mesh.pts[run])
            s = np.where([p in loose for p in run], s, _snapped(s, m.length, snap))
            pieces = [slice(0, len(run) // 2 + 1), slice(len(run) // 2, len(run))] \
                if run[0] == run[-1] else [slice(0, len(run))]
            for part in pieces:
                pts = HArray1_gp_Pnt2d(1, len(run[part]))
                for i, (u, v) in enumerate(zip(U[part], s[part])):
                    pts.SetValue(i + 1, gp_Pnt2d(float(u), float(v)))
                if straight:
                    curve = _polyline2d(np.c_[U[part], s[part]])
                    if curve is None:
                        return None
                else:
                    interp = Geom2dAPI_Interpolate(pts, False, 1e-9)
                    interp.Perform()
                    if not interp.IsDone():
                        return None
                    curve = interp.Curve()
                edge = BRepBuilderAPI_MakeEdge(curve, surface).Edge()
                BRepLib.BuildCurve3d_s(edge)
                wire.Add(edge)
            uv += list(zip(U, s))
        if not wire.IsDone():
            return None
        wires.append(wire.Wire())
        P = np.array(uv)
        spans.append(abs(np.sum(P[:, 0] * np.roll(P[:, 1], -1) - np.roll(P[:, 0], -1) * P[:, 1])))
    order = np.argsort(spans)[::-1]         # the outer loop first
    maker = BRepBuilderAPI_MakeFace(surface, wires[order[0]], True)
    for i in order[1:]:
        maker.Add(wires[i])
    if not maker.IsDone():
        return None
    fix = ShapeFix_Face(maker.Face())
    fix.Perform()
    face = fix.Face()
    if not BRepCheck_Analyzer(face).IsValid():
        return None
    # facing out of the material, as the facets do (sewing doesn't always turn a small
    # face round, and one facing in spoils the volume)
    big = feature.facets[np.argsort(mesh.farea[feature.facets])[::-1][:9]]
    s, _, U = m.place(mesh.fcent[big])
    votes = []
    for u, v, f in zip(U, s, big):
        props = GeomLProp_SLProps(surface, float(u), float(np.clip(v, 0, m.length)), 1, 1e-9)
        if props.IsNormalDefined():
            n = props.Normal()
            votes.append(np.array([n.X(), n.Y(), n.Z()]) @ mesh.fn[f])
    if votes and (np.median(votes) < 0) != (face.Orientation() == TopAbs_REVERSED):
        face = TopoDS.Face(face.Reversed())
    # and all of it must face the way the mesh does there (an outline that touches
    # itself, a slit at a run-out, can leave a face spanning a gap, part of it facing
    # the wrong way: sewing then turns the whole face round)
    if _facing_against(face, mesh) > 0.05:
        return None
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    target = float(mesh.farea[feature.facets].sum())
    # (moving the outline onto the piece's ends shifts it by up to `snap` all along: on
    # a narrow crest or root band that is a good share of its area)
    rim = sum(float(np.linalg.norm(np.diff(mesh.pts[loop + loop[:1]], axis=0), axis=1).sum()) for loop in loops)
    return face if abs(props.Mass() - target) <= 0.05 * target + snap * rim / 2 else None


def _polyline2d(P):
    """A degree-1 B-spline through the 2D points P (repeats dropped), or None."""
    from OCP.Geom2d import Geom2d_BSplineCurve
    from OCP.collections import Array1_double, Array1_gp_Pnt2d, Array1_int
    from OCP.gp import gp_Pnt2d
    P = P[np.r_[True, np.linalg.norm(np.diff(P, axis=0), axis=1) > 1e-12]]
    if len(P) < 2:
        return None
    poles, knots, mults = Array1_gp_Pnt2d(1, len(P)), Array1_double(1, len(P)), Array1_int(1, len(P))
    for i, (u, v) in enumerate(P):
        poles.SetValue(i + 1, gp_Pnt2d(float(u), float(v)))
        knots.SetValue(i + 1, float(i))
        mults.SetValue(i + 1, 2 if i in (0, len(P) - 1) else 1)
    return Geom2d_BSplineCurve(poles, knots, mults, 1)


def _snapped(s, length, snap):
    """Positions s across a thread piece, those within snap of its ends put on them."""
    return np.where(np.abs(s) <= snap, 0.0, np.where(np.abs(s - length) <= snap, length, s))


def _notches(m, pts, snap):
    """Which corners of a closed outline on a thread piece to leave off its end lines: a
    corner that snapping would put behind its neighbours on the line, so that the outline
    ran back over itself there (a mesh's crest edge zigzags by a hundredth of a mm, and
    flattened onto the helix the zigzag is a face crossing itself)."""
    s, _, U = m.place(pts)
    on = _snapped(s, m.length, snap)
    loose = np.zeros(len(pts), bool)
    for _ in range(len(pts)):
        S = np.where(loose, np.nan, on)
        a, b = np.roll(S, 1), np.roll(S, -1)
        line = (S == a) & (S == b) & ((S == 0.0) | (S == m.length))
        back = (U - np.roll(U, 1)) * (np.roll(U, -1) - U) < 0
        new = line & back & ~loose
        if not new.any():
            break
        loose |= new
    return loose


def _facing_against(face, mesh):
    """Share of the face's area whose normal points against the nearest mesh triangle's."""
    from scipy.spatial import cKDTree
    BRepMesh_IncrementalMesh(face, 0.005, False, 0.2, True)
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    if tri is None:
        return 1.0
    nodes = np.array([[tri.Node(i).X(), tri.Node(i).Y(), tri.Node(i).Z()] for i in range(1, tri.NbNodes() + 1)])
    ft = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]) - 1
    if face.Orientation() == TopAbs_REVERSED:
        ft = ft[:, [0, 2, 1]]
    a, b, c = nodes[ft[:, 0]], nodes[ft[:, 1]], nodes[ft[:, 2]]
    n = np.cross(b - a, c - a)
    area = np.linalg.norm(n, axis=1)
    if "tri_tree" not in mesh.__dict__:
        mesh.tri_tree = cKDTree(mesh.pts[mesh.tris].mean(axis=1))
    _, k = mesh.tri_tree.query((a + b + c) / 3)
    against = np.einsum("ij,ij->i", n, mesh.tn[k]) < 0
    return float(area[against].sum() / max(area.sum(), 1e-300))


def _trimmed_face(feature, k, bound, mesh, owner, bounds, edge_tri, edges, tol):
    """A patch cut to an arbitrary outline: split a generous piece of its surface along
    the outline edges and keep the piece the patch's facets lie on."""
    outline = _trimmed_outline(feature, k, mesh, owner, bounds, edge_tri, edges)
    if outline is None:
        return None
    return _trimmed_cut(feature, bound, mesh, *outline, tol)


def _trimmed_outline(feature, k, mesh, owner, bounds, edge_tri, edges):
    """(tools, loops, per_loop): the patch's outline loops of mesh vertices and their edges
    (shared with the faces beside it through edges), or None."""
    tris = np.concatenate([mesh.tris[mesh.ftris[f]] for f in feature.facets])
    loops = _loops(tris)
    if not loops:
        return None
    tools, per_loop = [], []
    for loop in loops:
        tags = _loop_tags(mesh, loop, owner, bounds, edge_tri, patch=k)
        runs = [(tags[0], loop + [loop[0]])] if all(t == tags[0] for t in tags) else _runs(loop, tags)
        per_loop.append([])
        for tag, run in _split_sharp(runs, mesh.pts):
            edge = _run_edge(edges, bounds, tag, run, patch_side=True)
            if edge is None:
                return None
            tools.append(edge)
            per_loop[-1].append(edge)
    return tools, loops, per_loop


def _trimmed_cut(feature, bound, mesh, tools, loops, per_loop, tol):
    """The patch's face cut from a generous piece of its surface by its outline edges (the
    slow part: run in worker processes when there are many, see _cut_all)."""
    m = feature.model
    # (a torus made directly doesn't always cut where its swept twin does: then the
    # same surface as a swept profile)
    swept = isinstance(m, Revolved) and _plain_torus(m, feature.lo, feature.hi)
    for exact in (True, False) if swept else (True,):
        base = _generous_surface(feature, bound, mesh, exact)
        if base is None:
            return None
        # (a big freeform surface's outline is laid on it first: splitting a big B-spline
        # surface along hundreds of outline edges took minutes a face on a fine mesh, and
        # often failed; a small one is split, which suits the faces beside it better. A
        # pipe's always: its degree-8 tube took the splitter 78 s to fail on, and on the
        # GPS case 165 of 177 pipes built from their outline, 117 by splitting, ten times
        # slower)
        if m.kind == "pipe" or (m.kind in ("freeform", "extrusion") and len(feature.facets) >= OUTLINE_FIRST):
            ways = (lambda: _outline_face(feature, mesh, base, loops, per_loop),
                    lambda: _split_face(feature, mesh, base, tools, loops, tol))
        else:
            ways = (lambda: _split_face(feature, mesh, base, tools, loops, tol),
                    lambda: _outline_face(feature, mesh, base, loops, per_loop))
        # (last, the split again with its fuzzy value as wide as the outline strays off the
        # surface, within MAX_DEVIATION: a fitted fillet can pass a few hundredths off a
        # corner where it meets the next, and a flat neighbour's chord runs straight past a
        # curved strip, and the splitter then left the outline open: Rak N-USB's r 1.511
        # fillets, corners 0.025 mm off, and a 58 mm freeform strip whose chords stray
        # 0.05 mm mid-edge, came out as one or two pieces of the surface, back to triangles)
        wide = (lambda: _split_face(feature, mesh, base, tools, loops, tol, WIDE_FUZZ * _off_surface(m, tools)),)
        for way in ways + (lambda: _polygon_face(feature, mesh, base, loops),) + wide:
            face = way()
            # (a face whose edges needed tolerances of millimetres doesn't fit its outline:
            # on a ring-shaped surface an edge can be laid on the far side of the ring, a
            # torus face round a bore coming out 19 mm "tolerant" where it ran on tangent
            # into a fillet. The next way may lay it right)
            if face is not None and _worst_tolerance(face) <= MAX_FACE_TOL:
                return face
    return None


def _off_surface(m, tools, samples=9):
    """How far the outline edges stray from the patch's surface at most (sampled), or 0
    past MAX_DEVIATION (an outline that far off isn't the patch's)."""
    off = 0.0
    for edge in tools:
        c = BRepAdaptor_Curve(edge)
        a, b = c.FirstParameter(), c.LastParameter()
        P = np.array([[p.X(), p.Y(), p.Z()] for p in (c.Value(a + (b - a) * t) for t in np.linspace(0, 1, samples))])
        off = max(off, float(np.abs(m.signed(P)).max()))
    return off if off <= MAX_DEVIATION else 0.0


def _started(path):
    """In a worker process: mark a job begun (see _has_started)."""
    try:
        open(path + ".started", "w").close()
    except OSError:
        pass


def _has_started(path):
    """Has a worker begun the job writing to path? A job's time limit counts from then: a
    future reports running as soon as it enters the pool's call queue, where about one job
    per worker waits for one to come free, so behind busy workers a job could overrun its
    limit without having run (a 5 V board's blends came out otherwise by whether the
    stages before had been worked out in the same run)."""
    return os.path.exists(path + ".started")


def _cut_job(job):
    """In a worker process: _trimmed_cut for one patch, its outline edges read from and
    its face written to BRep files."""
    mesh_key, feature, tools_path, counts, loops, tol, face_path, surface_path, has_surface = job
    _started(face_path)
    from . import workers
    mesh = workers.load(mesh_key)
    if has_surface:
        _surface_from(feature.model, surface_path)
    comp = TopoDS_Shape()
    BRepTools.Read_s(comp, tools_path, BRep_Builder())
    tools, it = [], TopoDS_Iterator(comp)
    while it.More():
        tools.append(TopoDS.Edge(it.Value()))
        it.Next()
    per_loop, i = [], 0
    for n in counts:
        per_loop.append(tools[i:i + n])
        i += n
    try:
        face = _trimmed_cut(feature, Boundary(feature, 10 * tol), mesh, tools, loops, per_loop, tol)
    except Exception:
        face = None
    if not has_surface and getattr(feature.model, "_surface", None) is not None:
        _surface_to(feature.model, surface_path)        # (fitted here: handed back, see _cut_all)
    return face is not None and BRepTools.Write_s(face, face_path)


def _surface_to(model, path):
    """Write a fitted model's surface (freeform, pipe, extrusion: see _cut_all) to a BRep file."""
    try:
        face = model.face()
        if face is not None:
            BRepTools.Write_s(face, path)
    except Exception:
        pass


def _surface_from(model, path):
    """Give a fitted model the surface in a BRep file (_surface_to), so it isn't fitted again."""
    try:
        if os.path.exists(path):
            shape = TopoDS_Shape()
            BRepTools.Read_s(shape, path, BRep_Builder())
            ex = TopExp_Explorer(shape, TopAbs_FACE)
            if ex.More():
                model._surface = BRep_Tool.Surface_s(TopoDS.Face(ex.Current()))
    except Exception:
        pass


def _cut_all(mesh, jobs, tol):
    """Run _trimmed_cut for many patches [(key, feature, tools, loops, per_loop)] in the
    worker processes: {key: face or None}. Keys missing from the answer (no workers, or
    they broke down) are left for the caller to cut here."""
    out = {}
    pool = workers.get()
    if pool is None or (len(jobs) < PARALLEL_CUTS and all(len(tools) < LONG_OUTLINE for _, _, tools, _, _ in jobs)):
        return out
    tmp = tempfile.mkdtemp(prefix="stl2curves_")
    try:
        if "shared_as" not in mesh.__dict__:
            mesh.shared_as = workers.share(mesh)
        futures, back = {}, []
        for i, (key, feature, tools, loops, per_loop) in enumerate(jobs):
            comp = TopoDS_Compound()
            builder = BRep_Builder()
            builder.MakeCompound(comp)
            for edge in tools:
                builder.Add(comp, edge)
            tools_path, face_path = os.path.join(tmp, f"{i}_in.brep"), os.path.join(tmp, f"{i}_out.brep")
            BRepTools.Write_s(comp, tools_path)
            # (a freeform patch's or pipe's surface goes along once fitted: pickled without
            # it, each worker fitted it again for every cut, and a big patch is cut afresh
            # whenever a neighbour changes: 15 s a time for one of the toolbox's)
            surface_path = os.path.join(tmp, f"{i}_surface.brep")
            has_surface = getattr(feature.model, "_surface", None) is not None
            if has_surface:
                _surface_to(feature.model, surface_path)
            job = (mesh.shared_as, feature, tools_path, [len(x) for x in per_loop], loops, tol, face_path,
                   surface_path, has_surface)
            future = pool.submit(_cut_job, job)
            futures[future] = (key, face_path)
            if not has_surface and hasattr(feature.model, "_surface"):
                back.append((feature.model, surface_path, future))
        # (cuts take seconds, but one with an outline of thousands of edges can grind on
        # for minutes and fail anyway: a 920k-triangle mesh's first build was one torus
        # patch's 360 s cut, its 4,526 edges every way tried. Once only overrunning cuts
        # are left, they count as failed; their workers are left to finish them, and are
        # restarted once the part is done, as for blend fills)
        # (looked at twice a second, not at every answer: waiting on thousands of cuts
        # at once costs a pass over all of them each time)
        from concurrent.futures import wait
        started, pending = {}, set(futures)
        while pending:
            _, pending = wait(pending, timeout=0.5)
            now = time.time()
            for f in pending:
                if f not in started and _has_started(futures[f][1]):
                    started[f] = now
            if pending and all(f in started and now - started[f] > CUT_SECONDS for f in pending):
                workers.abandoned()
                break
        for f, (key, face_path) in futures.items():
            if not f.done():
                out[key] = None         # (overran: failed, not left to be cut here)
                continue
            if f.exception() is not None:
                continue
            face = None
            if f.result():
                shape = TopoDS_Shape()
                BRepTools.Read_s(shape, face_path, BRep_Builder())
                if shape.ShapeType() == TopAbs_FACE:
                    face = TopoDS.Face(shape)
                elif TopExp_Explorer(shape, TopAbs_FACE).More():
                    face = shape        # (a patch in several pieces: their compound)
            out[key] = face
        for model, path, future in back:
            if model._surface is None and future.done() and os.path.exists(path):
                _surface_from(model, path)
    except Exception:
        out = {}                        # (the worker processes broke down: cut them here)
        workers.broken()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


def _outline_face(feature, mesh, base, loops, per_loop):
    """Fallback: the patch's own outline edges (shared with its neighbours) laid on its
    surface as wires, the face fitted to them by ShapeFix."""
    surface = BRep_Tool.Surface_s(TopoDS.Face(TopExp_Explorer(base, TopAbs_FACE).Current()))
    target = float(mesh.farea[feature.facets].sum())
    normal = mesh.fn[feature.facets].T @ mesh.farea[feature.facets]
    pts = mesh.pts
    area = lambda loop: abs(sum(np.cross(pts[loop[i]], pts[loop[(i + 1) % len(loop)]]) @ normal
                                for i in range(len(loop))))
    order = sorted(range(len(loops)), key=lambda i: -area(loops[i]))
    wires = []
    for i in order:
        w = BRepBuilderAPI_MakeWire()
        for e in per_loop[i]:
            w.Add(e)
        if not w.IsDone():
            return None
        # (a copy: ShapeFix widens the tolerances of the edges it is given, in place, even
        # when the face then fails; these are shared with the faces beside it, so one
        # failed fallback changed its neighbours, and how the part sewed)
        wires.append(TopoDS.Wire(BRepBuilderAPI_Copy(w.Wire()).Shape()))
    for flip in (False, True):
        ws = [TopoDS.Wire(w.Reversed()) for w in wires] if flip else wires
        maker = BRepBuilderAPI_MakeFace(surface, ws[0], False)
        for w in ws[1:]:
            maker.Add(w)
        if not maker.IsDone():
            continue
        fix = ShapeFix_Face(maker.Face())
        fix.Perform()
        face = fix.Face()
        if not BRepCheck_Analyzer(face).IsValid():
            continue
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        if abs(props.Mass() - target) <= 0.05 * target + 1e-3:
            return face
    return None


def _polygon_face(feature, mesh, base, loops):
    """Fallback (the stlToSolid project's method): lay the patch's own mesh outline,
    as straight edges, on its surface and let ShapeFix fit the face to it."""
    surface = BRep_Tool.Surface_s(TopoDS.Face(TopExp_Explorer(base, TopAbs_FACE).Current()))
    target = float(mesh.farea[feature.facets].sum())
    loops = sorted(loops, key=len, reverse=True)
    for flip in (False, True):
        wires = []
        for loop in loops:
            ring = loop[::-1] if flip else loop
            w = BRepBuilderAPI_MakeWire()
            for a, b in zip(ring, ring[1:] + ring[:1]):
                w.Add(_line(mesh.pts[a], mesh.pts[b]))
            if not w.IsDone():
                return None
            wires.append(w.Wire())
        maker = BRepBuilderAPI_MakeFace(surface, wires[0], False)
        for w in wires[1:]:
            maker.Add(w)
        if not maker.IsDone():
            continue
        fix = ShapeFix_Face(maker.Face())
        fix.Perform()
        face = fix.Face()
        if not BRepCheck_Analyzer(face).IsValid():
            continue
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        if abs(props.Mass() - target) <= 0.05 * target + 1e-3:
            return face
    return None


def _split_face(feature, mesh, base, tools, loops, tol, fuzz=0.0):
    m = feature.model
    fuzz_was = max(10 * tol, 1e-4, 2 * getattr(m, "rim_dev", 0.0))
    if fuzz and fuzz <= fuzz_was:
        return None             # (a retry no wider than the first try)
    splitter = BRepAlgoAPI_Splitter()
    splitter.SetArguments(_shapes([base]))
    splitter.SetTools(_shapes(tools))
    # (a fitted freeform surface passes a little off the outline's corners, which the
    # outline edges run through)
    splitter.SetFuzzyValue(max(fuzz_was, fuzz))
    # (otherwise it widens the tolerances of the outline edges it is given, in place:
    # those are shared with the faces beside the patch, whose corners came out 8 mm
    # "wide" and then joined to anything)
    splitter.SetNonDestructive(True)
    splitter.Build()
    if not splitter.IsDone():
        return None

    # Which pieces make up the patch: drop points from the patch's facets onto the true
    # surface and keep every piece they land on (a patch can come out as several pieces).
    facets = feature.facets
    # sample away from the outline: a needle-thin facet on the edge touches both pieces
    rim = set(np.concatenate(loops).tolist())
    inner = np.array([f for f in facets if not rim & set(mesh.fverts[f].tolist())])
    target = float(mesh.farea[facets].sum())
    total = lambda combo: sum(a for _, a in combo)

    def pieces(source):
        pick = source[np.linspace(0, len(source) - 1, min(len(source), 60)).astype(int)]
        c = mesh.fcent[pick]
        probes = c - m.signed(c)[:, None] * m.normal(c)
        vertices = [BRepBuilderAPI_MakeVertex(_pnt(p)).Vertex() for p in probes]
        hit = []
        ex = TopExp_Explorer(splitter.Shape(), TopAbs_FACE)
        while ex.More():
            face = TopoDS.Face(ex.Current())
            ex.Next()
            if any(BRepExtrema_DistShapeShape(v, face).Value() < 1e-3 for v in vertices):
                props = GProp_GProps()
                BRepGProp.SurfaceProperties_s(face, props)
                hit.append((face, props.Mass()))
        return hit

    hit = pieces(inner if len(inner) else facets)
    if len(inner) and abs(total(hit) - target) > 0.1 * target:
        # (a patch in several pieces, each a narrow strip: one may have no facet clear
        # of the outline at all; then from every facet, and the best-matching set below)
        hit = pieces(facets)
    if not hit or len(hit) > 10:
        return None
    # Normally every piece hit belongs to the patch; if they don't add up (a probe sat
    # right on the outline, touching the next piece too), take the best-matching set.
    if abs(total(hit) - target) <= 0.1 * target:
        kept = [f for f, _ in hit]
    else:
        combos = [c for n in range(1, len(hit) + 1) for c in itertools.combinations(hit, n)
                  if abs(total(c) - target) <= 0.1 * target]
        best = min(combos, key=lambda c: abs(total(c) - target), default=())
        kept = [f for f, _ in best]
    if not kept:
        return None
    if len(kept) == 1:
        return kept[0]
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for face in kept:
        builder.Add(comp, face)
    return comp


def _repaired(face):
    """The face if valid, else the first standard repair that makes it valid (edge
    tolerances widened to the real gap, at most twice the allowed deviation)."""
    if BRepCheck_Analyzer(face).IsValid():
        return face
    fix = ShapeFix_Face(face)
    fix.Perform()
    if BRepCheck_Analyzer(fix.Face()).IsValid():
        return fix.Face()
    fix = ShapeFix_Shape(face)
    fix.SetMaxTolerance(2 * MAX_DEVIATION)
    fix.Perform()
    ex = TopExp_Explorer(fix.Shape(), TopAbs_FACE)
    if not ex.More():
        return None
    out = TopoDS.Face(ex.Current())
    ex.Next()
    return out if not ex.More() and BRepCheck_Analyzer(out).IsValid() else None


# degree, points per boundary curve, iterations, anisotropy, 2d/3d/angular/curvature
# tolerances, max degree, max segments
USE_GUIDES = False   # guide points past the outline pull against the real points where the neighbour is curved
BULGE_FACTOR = 1.0   # how far past a circular arc's sag a blend may bow over a facet
HUG_SLACK = 0.005 * ACCURACY   # mm: ... plus this
FILL_SETTINGS = (3, 15, 2, False, 1e-5, 1e-4, 1e-2, 0.1, 8, 9)
FINE_FILL_SETTINGS = (3, 30, 3, False, 1e-5, 1e-5, 1e-2, 0.1, 8, 20)
BLEND_SECONDS = 300     # time allowed for fitting blends; the rest keep their exact pieces or facets
FINE_BLENDS = 150       # a part with more blends than this is mostly freeform: skip the finer (slower) refits
PARALLEL_FILLS = 40     # without worker processes, more fills than this start them
FILL_SECONDS = 5        # a fill in a worker process still running after this long counts as failed
ASSESS_SECONDS = 60     # longest wait for the fit checks of a batch of fills (behind overrunning fills)
ASSESS_RUN_SECONDS = 1  # a fit check still running after this long fails its blend (passing ones
                        # took at most 0.24 s on four parts, 191 of them; failing ones up to 46 s)
BUILD_SHARE = 0.35      # of the conversion's time limit, what is kept back for building and checking
                        # the part (blends not fitted by then keep their exact pieces or facets)


class _Deferred(Exception):
    """A blend's fill was queued to be done with the others (settle_blends)."""


def _fill(outline, inner, settings):
    """An N-sided patch through the outline edges and the points inside, or None."""
    fill = BRepFill_Filling(*settings)
    for edge in outline:
        fill.Add(edge, GeomAbs_C0, True)
    for q in inner:
        fill.Add(_pnt(q))
    try:
        fill.Build()
        return _repaired(fill.Face()) if fill.IsDone() else None
    except Exception:
        return None


def _fill_job(job):
    """In a worker process: one fill, its outline read from and the face written to
    BRep files (OpenCascade shapes don't cross processes otherwise)."""
    edges_path, inner, settings, face_path = job
    _started(face_path)
    comp = TopoDS_Shape()
    BRepTools.Read_s(comp, edges_path, BRep_Builder())
    outline, it = [], TopoDS_Iterator(comp)
    while it.More():
        outline.append(TopoDS.Edge(it.Value()))
        it.Next()
    face = _fill(outline, inner, settings)
    return face is not None and BRepTools.Write_s(face, face_path)


def _assess_job(job):
    """In a worker process: _assess_blend on a face written to a BRep file."""
    face_path, piece = job
    _started(face_path + ".check")
    shape = TopoDS_Shape()
    BRepTools.Read_s(shape, face_path, BRep_Builder())
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    return _assess_blend(TopoDS.Face(ex.Current()), piece) if ex.More() else False


def _fill_all(jobs):
    """Run fills [(key, outline, inner, settings, piece)] -> {key: (face or None, verdict)}.
    OpenCascade holds Python's lock while it works, so they run in worker processes, all
    at once; the workers then also judge each face against its blend's triangles (piece,
    see _assess_blend: the verdict), or the verdict is None and that is left to later."""
    out = {}
    # (workers still starting are waited for: done here a fill can't be cut short, and
    # one of them can take a minute; they are only started for parts big enough)
    pool = workers.get()
    if pool is None and len(jobs) >= PARALLEL_FILLS:
        workers.start()
        pool = workers.get()
    if pool is not None and jobs:
        tmp = tempfile.mkdtemp(prefix="stl2curves_")
        try:
            args = []
            for i, (_, outline, inner, settings, _) in enumerate(jobs):
                comp = TopoDS_Compound()
                builder = BRep_Builder()
                builder.MakeCompound(comp)
                for edge in outline:
                    builder.Add(comp, edge)
                BRepTools.Write_s(comp, os.path.join(tmp, f"{i}_in.brep"))
                args.append((os.path.join(tmp, f"{i}_in.brep"), [tuple(map(float, q)) for q in inner],
                             settings, os.path.join(tmp, f"{i}_out.brep")))
            # Most fills take a few hundredths of a second, but a few that end up failing
            # anyway grind on for tens of seconds: each gets FILL_SECONDS once started
            from concurrent.futures import wait, FIRST_COMPLETED
            futures = {pool.submit(_fill_job, a): (key, a) for (key, *_), a in zip(jobs, args)}
            started, pending = {}, set(futures)
            while pending:
                _, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
                now = time.time()
                for f in pending:
                    if f not in started and _has_started(futures[f][1][3]):
                        started[f] = now
                if pending and all(f in started and now - started[f] > FILL_SECONDS for f in pending):
                    workers.abandoned()
                    break           # only overrunning fills left
            pieces = {key: piece for key, *_, piece in jobs}
            judged, checking = {}, {}
            for f, (key, a) in futures.items():
                face = None
                if f.done() and not f.cancelled() and f.exception() is None and f.result():
                    shape = TopoDS_Shape()
                    BRepTools.Read_s(shape, a[3], BRep_Builder())
                    ex = TopExp_Explorer(shape, TopAbs_FACE)
                    face = TopoDS.Face(ex.Current()) if ex.More() else None
                    if face is not None and pieces[key] is not None:
                        judged[key] = pool.submit(_assess_job, (a[3], pieces[key]))
                        checking[judged[key]] = a[3] + ".check"
                out[key] = (face, None)
            # (an overrunning fill can't be stopped inside OpenCascade; its worker is left
            # to finish it, and the workers are restarted once the part is done. Checks
            # queued behind such fills are waited for only so long: unjudged, a face is
            # judged later, here)
            # A check takes milliseconds; one still running after ASSESS_RUN_SECONDS is on a
            # surface the projections can't settle on (Rak N-USB: a 6-facet blend's check
            # ran 115 s, and the part waited 60 s for it, then 25 s for another): it fails.
            began, pending, start = {}, set(judged.values()), time.time()
            while pending:
                _, pending = wait(pending, timeout=0.1, return_when=FIRST_COMPLETED)
                now = time.time()
                for f in pending:
                    if f not in began and _has_started(checking[f]):
                        began[f] = now
                overran = {f for f in pending if f in began and now - began[f] > ASSESS_RUN_SECONDS}
                if now - start > ASSESS_SECONDS or (overran and all(
                        f in overran or (f not in began and now - start > ASSESS_RUN_SECONDS) for f in pending)):
                    break
            now = time.time()
            for key, f in judged.items():
                if f.done() and not f.cancelled() and f.exception() is None:
                    out[key] = (out[key][0], f.result())
                elif f in began and now - began[f] > ASSESS_RUN_SECONDS:
                    out[key] = (out[key][0], False)
                    workers.abandoned()
        except Exception:
            out = {}                    # (the worker processes broke down: do them here)
            workers.broken()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    for key, outline, inner, settings, _ in jobs:
        if key not in out:
            out[key] = (_fill(outline, inner, settings), None)
    return out


def _blend_face(feature, k, mesh, owner, bounds, edge_tri, edges):
    """A smooth freeform face for a blend: an N-sided patch spanning the blend's outline
    (the same edges its neighbours use) through the mesh corners inside it, plus a few
    guide points just past the outline so it rolls tangentially into the faces beside
    it. Sets the feature's volume change and fit on the way."""
    facets = feature.facets
    inside = set(facets.tolist())
    tids = np.concatenate([mesh.ftris[f] for f in facets])
    tris = mesh.tris[tids]
    loops = _loops(tris)
    if not loops or len(loops) != 1:
        return None                 # one outline only (a ring-shaped area is left as it was)
    loop = loops[0]
    pts = mesh.pts
    V = np.unique(tris)
    rim = set(loop)
    # typical facet width, to place the guide points
    ab = np.linalg.norm(pts[tris[:, 1]] - pts[tris[:, 0]], axis=1)
    width = float(np.median(2 * mesh.tarea[tids] / np.maximum(ab, 1e-9)))
    guides = []
    for a, b in zip(loop, loop[1:] + loop[:1]):
        t_out, t_in = edge_tri.get((b, a)), edge_tri.get((a, b))
        if t_out is None or t_in is None:
            continue
        f_in, f_out = mesh.facet_of[t_in], mesh.facet_of[t_out]
        if mesh.fn[f_in] @ mesh.fn[f_out] < math.cos(math.radians(TANGENT_DEG)):
            continue                # a crease: nothing to roll into
        e = pts[b] - pts[a]
        e = e / np.linalg.norm(e)
        mid = (pts[a] + pts[b]) / 2
        away = np.cross(mesh.fn[f_out], e)
        if away @ (pts[mesh.tris[t_in]].mean(axis=0) - mid) > 0:
            away = -away
        guides.append(mid + 0.35 * max(width, 0.05) * away)

    tags = _loop_tags(mesh, loop, owner, bounds, edge_tri, patch=k)
    runs = [(tags[0], loop + [loop[0]])] if all(t == tags[0] for t in tags) else _runs(loop, tags)
    runs = _split_sharp(runs, pts)
    # The same outline gives the same face: reuse it from an earlier attempt at the part
    cache = mesh.__dict__.setdefault("blend_cache", {})
    key = (facets.tobytes(), tuple(tuple(int(v) for v in run) for _, run in runs))
    if key in cache:
        if cache[key] is None:
            return None
        face, feature.change, feature.tolerance, feature.worst, feature.detail = cache[key]
        return face
    if mesh.__dict__.get("blends_only_cached"):
        return None                 # the time for fitting blends is used up
    jobs = mesh.__dict__.get("fill_jobs")      # a list while settle_blends gathers the fills
    if jobs is None:
        cache[key] = None
    outline = []
    for tag, run in runs:
        edge = _run_edge(edges, bounds, tag, run, patch_side=True)
        if edge is None:
            return None
        outline.append(edge)
    inner = [pts[v] for v in V if v not in rim] + (guides if USE_GUIDES else [])

    done = mesh.__dict__.setdefault("fills", {})
    vids, local = np.unique(mesh.tris[tids], return_inverse=True)
    piece = (pts[mesh.tris[tids]], mesh.tn[tids], mesh.tarea[tids], local.reshape(-1, 3), pts[vids],
             float(mesh.farea[facets].sum()))

    def filled(settings):
        if (key, settings) not in done:
            if jobs is not None:
                jobs.append(((key, settings), outline, inner, settings, piece))
                raise _Deferred
            # (through the workers too when they are running: held to FILL_SECONDS)
            done.update(_fill_all([((key, settings), outline, inner, settings, piece)]))
        return done[key, settings][0]

    # a quick patch first; if it follows its outline loosely, a finer (slower) one
    try:
        face, chosen = filled(FILL_SETTINGS), FILL_SETTINGS
        if (face is None or _edge_gap(face) > MAX_EDGE_GAP / 4) and mesh.__dict__.get("blend_count", 0) <= FINE_BLENDS:
            finer = filled(FINE_FILL_SETTINGS)
            if finer is not None and (face is None or _edge_gap(finer) < _edge_gap(face)):
                face, chosen = finer, FINE_FILL_SETTINGS
    except _Deferred:
        return None
    if face is None or _edge_gap(face) > MAX_EDGE_GAP:
        return None     # a loose outline would force a loose sewing tolerance on the whole part
    verdict = done[key, chosen][1]
    if verdict is None:
        verdict = _assess_blend(face, piece)
    if not verdict:
        return None
    worst, change, spread = verdict
    target = piece[5]
    surface = BRep_Tool.Surface_s(face)
    proj = GeomAPI_ProjectPointOnSurf()
    proj.Init(surface, *surface.Bounds())
    # face the same way as the mesh
    proj.Perform(_pnt(mesh.fcent[facets[0]]))
    u, v = proj.LowerDistanceParameters()
    props = GeomLProp_SLProps(surface, u, v, 1, 1e-9)
    if props.IsNormalDefined():
        n = props.Normal()
        n = np.array([n.X(), n.Y(), n.Z()]) * (-1 if face.Orientation() == TopAbs_REVERSED else 1)
        if n @ mesh.fn[facets[0]] < 0:
            face = TopoDS.Face(face.Reversed())
    feature.change = change
    feature.tolerance = 0.5 * spread + 1e-4 * target
    # its outline is shared with its neighbours, so the gap sewing must close is just
    # how far the face's own edges stray (not how far it lies from the mesh corners)
    feature.worst = _edge_gap(face)
    feature.detail = feature.detail.split(", within")[0] + f", within {worst:.3f} mm of the mesh"
    cache[key] = (face, feature.change, feature.tolerance, feature.worst, feature.detail)
    return face


def _point_triangle_distance(P, T):
    """Distance from each point in P (n, 3) to the nearest of triangles T (m, 3, 3), and
    which triangle that is."""
    best = np.full(len(P), np.inf)
    which = np.zeros(len(P), int)
    for start in range(0, len(T), 256):
        tri = T[start:start + 256]
        a, b, c = tri[:, 0][None], tri[:, 1][None], tri[:, 2][None]
        p = P[:, None]
        ab, ac, ap = b - a, c - a, p - a
        d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
        bp = p - b
        d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
        cp = p - c
        d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
        va = d3 * d6 - d5 * d4
        vb = d5 * d2 - d1 * d6
        vc = d1 * d4 - d3 * d2
        denom = np.where(np.abs(va + vb + vc) > 1e-30, va + vb + vc, 1e-30)
        v, w = vb / denom, vc / denom
        inside = (v >= 0) & (w >= 0) & (v + w <= 1)
        # inside: straight down onto the triangle; outside: the nearest point of its edges
        dist = np.where(inside, np.linalg.norm(p - (a + ab * v[..., None] + ac * w[..., None]), axis=-1), np.inf)
        for e0, e1 in ((a, b), (b, c), (c, a)):
            e = e1 - e0
            t = np.clip(((p - e0) * e).sum(-1) / np.maximum((e * e).sum(-1), 1e-30), 0, 1)
            edge = np.linalg.norm(p - (e0 + e * t[..., None]), axis=-1)
            dist = np.where(inside, dist, np.minimum(dist, edge))
        k = dist.argmin(axis=1)
        dmin = dist[np.arange(len(P)), k]
        better = dmin < best
        best[better], which[better] = dmin[better], k[better] + start
    return best, which


def _assess_blend(face, piece):
    """How a blend's face fits its triangles: (worst gap at a mesh corner, volume it adds,
    spread of that), or False if it strays too far. piece: the triangles' corners (n, 3, 3),
    normals, areas, corner numbers into P and corner points P, and the facets' area.
    Needs nothing else, so the workers that fill the blends can judge them too."""
    T, tn, tarea, corner_ids, P, target = piece
    surface = BRep_Tool.Surface_s(face)
    proj = GeomAPI_ProjectPointOnSurf()
    proj.Init(surface, *surface.Bounds())

    def along(p, n):
        """Signed distance from p to the surface, measured along direction n."""
        proj.Perform(_pnt(p))
        if not proj.IsDone() or not proj.NbPoints():
            return None
        q = proj.NearestPoint()
        return float((np.array([q.X(), q.Y(), q.Z()]) - p) @ n)

    # how far the surface strays from the mesh corners, and the volume it adds
    worst, change, spread = 0.0, 0.0, 0.0
    for corners, n_t, area in zip(T, tn, tarea):
        for p in corners:
            d = along(p, n_t)
            if d is None:
                return False
            worst = max(worst, abs(d))
        s_mid = [along(p, n_t) for p in (corners + corners[[1, 2, 0]]) / 2]
        # between the corners a smooth surface bows away from a flat facet only a little
        # (about a tenth of the facet's size even for a fillet cut into two strips);
        # more than that is the fit overshooting
        centre = corners.mean(axis=0)
        s_in = [along(p, n_t) for p in [centre] + list((corners + centre) / 2)]
        if any(d is None for d in s_mid + s_in):
            return False
        size = float(np.linalg.norm(corners - corners[[1, 2, 0]], axis=1).max())
        if max(abs(d) for d in s_mid + s_in) > max(MAX_DEVIATION, MAX_BULGE * size):
            return False
        change += area * float(np.mean(s_mid))
        spread += area * float(np.mean(np.abs(s_mid)))
    if worst > MAX_DEVIATION:
        return False
    if not _hugs_mesh(face, piece):
        return False
    gp = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, gp)
    if abs(gp.Mass() - target) > 0.15 * target + 1e-3:
        return False
    return worst, change, spread


def _hugs_mesh(face, piece):
    """Does the finished face stay as close to the mesh as a smooth surface through its
    corners can? A smooth surface bows away from a flat facet by only about L*theta/4
    (L its size, theta how far the surface turns across it, read from the mesh's own
    corner normals); a fitted patch that waves between the corners bows much further."""
    T, tn, tarea, corners, P, _ = piece
    BRepMesh_IncrementalMesh(face, 0.005, False, 0.2, True)
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    if tri is None:
        return False
    nodes = np.array([[tri.Node(i).X(), tri.Node(i).Y(), tri.Node(i).Z()] for i in range(1, tri.NbNodes() + 1)])
    ft = np.array([[tri.Triangle(i).Value(j) for j in (1, 2, 3)] for i in range(1, tri.NbTriangles() + 1)]) - 1
    samples = np.vstack([nodes, nodes[ft].mean(axis=1)])
    samples = samples[::max(1, len(samples) // 4000)]
    d, k = _point_triangle_distance(samples, T)
    # corner normals from the blend's own triangles only (averaging in a face across a
    # sharp edge would make the surface look far more curved than it is)
    vn = np.zeros_like(P)
    for j in range(3):
        np.add.at(vn, corners[:, j], tn * tarea[:, None])
    vn /= np.maximum(np.linalg.norm(vn, axis=1), 1e-12)[:, None]
    theta = np.arccos(np.clip(np.einsum("tkj,tj->tk", vn[corners], tn), -1, 1)).max(axis=1)
    size = np.linalg.norm(T - T[:, [1, 2, 0]], axis=2).max(axis=1)
    allowed = BULGE_FACTOR * size * theta / 4 + HUG_SLACK
    if (d > allowed[k]).any():
        return False
    # and every mesh corner lies on the face itself (not just on its untrimmed surface)
    dc, _ = _point_triangle_distance(P, nodes[ft])
    return bool((dc <= MAX_DEVIATION).all())


def _edge_gap(face):
    gap, ex = 0.0, TopExp_Explorer(face, TopAbs_EDGE)
    while ex.More():
        gap = max(gap, BRep_Tool.Tolerance_s(TopoDS.Edge(ex.Current())))
        ex.Next()
    return gap


def _shapes(items):
    out = List_TopoDS_Shape()
    for x in items:
        out.Append(x)
    return out


def _worst_tolerance(shape):
    """The widest tolerance of any corner or edge of the shape (mm)."""
    worst = 0.0
    for kind, tolerance in ((TopAbs_VERTEX, lambda x: BRep_Tool.Tolerance_s(TopoDS.Vertex(x))),
                            (TopAbs_EDGE, lambda x: BRep_Tool.Tolerance_s(TopoDS.Edge(x)))):
        ex = TopExp_Explorer(shape, kind)
        while ex.More():
            worst = max(worst, tolerance(ex.Current()))
            ex.Next()
    return worst


def _wedge_face(feature, bound, mesh):
    """A sphere face trimmed by the planes through its centre that bound the corner."""
    m = feature.model
    mean = np.mean([np.asarray(n) for n in feature.planes], axis=0)
    d, e1, e2 = _frame(-mean if np.linalg.norm(mean) > 1e-6 else feature.planes[0])
    # Keep the sphere's seam (on the far side, along d) and its poles well away from
    # the corner patch and from every trimming plane: a plane through a pole fails.
    poles = [math.cos(a) * e1 + math.sin(a) * e2 for a in np.radians(np.arange(0, 180, 7.5))]
    poles.sort(key=lambda z: -min(abs(z @ n) for n in feature.planes))
    # (and a cut can still fail silently, the face coming back uncut by one plane: a
    # rounded cube's corner, its planes and centre a few 1e-7 off square, came out a
    # quarter sphere and left its three edges unsewn: so check, and turn the poles)
    for pole in poles[:WEDGE_TRIES]:
        shape = _wedge_cut(m, feature.planes, pole, d)
        if shape is not None and _inside_planes(shape, m, feature.planes):
            return shape
    return None


WEDGE_TRIES = 6     # sphere orientations tried for a corner face before giving up


def _wedge_cut(m, planes, pole, d):
    ball = BRepPrimAPI_MakeSphere(gp_Ax2(_pnt(m.c), gp_Dir(*pole), gp_Dir(*d)), m.r).Shape()
    ex = TopExp_Explorer(ball, TopAbs_FACE)
    shape = TopoDS.Face(ex.Current())
    size = 2 * m.r + 1
    for n in planes:
        nd, n1, n2 = _frame(n)
        corner = m.c - size * n1 - size * n2
        box = BRepPrimAPI_MakeBox(gp_Ax2(_pnt(corner), gp_Dir(*nd), gp_Dir(*n1)), 2 * size, 2 * size, size).Shape()
        common = BRepAlgoAPI_Common(shape, box)
        if not common.IsDone():
            return None
        shape = common.Shape()
    return shape


def _inside_planes(shape, m, planes):
    """Does every edge of the cut sphere lie on the kept side of every plane?"""
    tol = 1e-4 * m.r + 1e-6
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    seen = False
    while ex.More():
        c = BRepAdaptor_Curve(TopoDS.Edge(ex.Current()))
        ex.Next()
        for t in np.linspace(c.FirstParameter(), c.LastParameter(), 7):
            q = c.Value(t)
            w = np.array([q.X(), q.Y(), q.Z()]) - m.c
            if any(w @ np.asarray(n) < -tol for n in planes):
                return False
        seen = True
    return seen


def settle_blends(mesh, features, tol, split, skipped):
    """Build every blend's face on its own first (each depends only on its own outline),
    cutting in two any that won't fit and giving back the pieces of any that can't be
    cut further, so the whole part is then built and sewn just once or twice."""
    features = list(features)
    mesh.blend_count = sum(f.kind == "blend" for f in features)
    start = time.time()
    for _ in range(4):
        owner = np.full(len(mesh.fn), -1)
        for k, f in enumerate(features):
            owner[f.facets] = k
        bounds = [Boundary(f, 10 * tol) for f in features]
        edge_tri = _edge_tri(mesh)
        edges = Edges(mesh.pts)
        # every blend's fill first, all at once in worker processes (each depends only on
        # its own outline): the quick ones, then the finer refits they call for
        try:
            for _ in range(2):
                mesh.fill_jobs = []
                for k, f in enumerate(features):
                    if f.kind == "blend":
                        _blend_face(f, k, mesh, owner, bounds, edge_tri, edges)
                jobs, mesh.fill_jobs = mesh.fill_jobs, None
                if not jobs:
                    break
                mesh.fills.update(_fill_all(jobs))
        finally:
            mesh.fill_jobs = None
        out, changed = [], False
        for k, f in enumerate(features):
            if f.kind != "blend":
                out.append(f)
                continue
            if time.time() - start > BLEND_SECONDS or features_mod.time_left() < BUILD_SHARE * (features_mod._budget or 0):
                # out of time: keep only blends already known to fit (their faces are cached)
                mesh.blends_only_cached = True
            if _blend_face(f, k, mesh, owner, bounds, edge_tri, edges) is not None:
                out.append(f)
                continue
            changed = True
            halves = None if mesh.__dict__.get("blends_only_cached") else split(mesh, f)
            if halves:
                out += halves
            else:
                skipped.append(f)
                out += list(f.parts)
        features = out
        if not changed:
            break
    return features


def _across(mesh):
    """For each facet, the facets it shares an edge with (however sharp the bend)."""
    if "across" not in mesh.__dict__:
        near = [set() for _ in range(len(mesh.fn))]
        for fs in mesh.edge_facets.values():
            for a in fs:
                near[a].update(fs)
        mesh.across = [np.array(sorted(s - {a}), int) for a, s in enumerate(near)]
    return mesh.across


def _edge_tri(mesh):
    """Directed mesh edge (a, b) -> the triangle it belongs to."""
    if "edge_tri" not in mesh.__dict__:
        edge_tri = {}
        for t, (a, b, c) in enumerate(mesh.tris):
            edge_tri[(a, b)] = t
            edge_tri[(b, c)] = t
            edge_tri[(c, a)] = t
        mesh.edge_tri = edge_tri
    return mesh.edge_tri


def build_faces(mesh, features, tol):
    """All faces of the rebuilt part, as a compound, plus the features that were used.

    A face depends only on its own patch (or flat facet) and on who owns each facet
    across its outline, so it is reused from an earlier attempt at the part whenever
    those are unchanged (the search for troublemakers builds the part dozens of times,
    changing a few patches at a time)."""
    owner = np.full(len(mesh.fn), -1)
    for k, f in enumerate(features):
        owner[f.facets] = k
    bounds = [Boundary(f, 10 * tol) for f in features]
    edge_tri = _edge_tri(mesh)
    across = _across(mesh)
    cache = mesh.__dict__.setdefault("face_cache", {})
    alive = mesh.__dict__.setdefault("face_cache_features", {})
    for f in features:
        alive[id(f)] = f        # (kept, so no later feature can take over its id)
    ident = np.array([id(f) for f in features] + [-1], dtype=np.int64)

    def who(facets):
        return ident[owner[facets]].tobytes()   # (owner -1 picks the trailing -1)

    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    failed, shells = [], []
    edges = Edges(mesh.pts, mesh.__dict__.setdefault("corner_pool", {}), mesh)
    keys = []
    for f in features:
        if "_across" not in f.__dict__:
            f._across = np.setdiff1d(np.unique(np.concatenate([across[x] for x in f.facets])), f.facets)
        keys.append(("patch", id(f), who(f._across)))
    # Before anything, the new blends' fills, all at once in the worker processes, as
    # settle_blends does (the quick ones, then the finer refits they call for): one by one,
    # a try that brought many new blends waited for each fill in turn (Rak N-USB: 16 in a
    # try, 4 s). The same fills, so the same faces: the same tries on four parts.
    fresh = [k for k, f in enumerate(features) if f.kind == "blend" and keys[k] not in cache]
    if len(fresh) > 1:
        try:
            for _ in range(2):
                mesh.fill_jobs = []
                for k in fresh:
                    try:
                        _blend_face(features[k], k, mesh, owner, bounds, edge_tri, edges)
                    except Exception:
                        pass
                jobs, mesh.fill_jobs = mesh.fill_jobs, None
                if not jobs:
                    break
                mesh.__dict__.setdefault("fills", {}).update(_fill_all(jobs))
        finally:
            mesh.fill_jobs = None
    # The new trimmed patches' outlines first, and the new blends' faces, in the patches'
    # order (an outline run two patches share is built once, by whichever comes first):
    # then the trimmed faces are cut from their surfaces all at once in the worker
    # processes (cutting is slow, and one patch at a time took minutes on a big part).
    outlines, early = {}, {}
    for k, (f, bound) in enumerate(zip(features, bounds)):
        if keys[k] in cache or f.model.kind == "thread":
            continue
        try:
            if f.kind == "trimmed":
                outlines[k] = _trimmed_outline(f, k, mesh, owner, bounds, edge_tri, edges)
            elif f.kind == "blend":
                early[k] = [_blend_face(f, k, mesh, owner, bounds, edge_tri, edges)]
        except Exception:
            early[k] = [None]
    cut = _cut_all(mesh, [(k, features[k], *o) for k, o in outlines.items() if o is not None], tol)
    for k, (f, bound) in enumerate(zip(features, bounds)):
        key = keys[k]
        if key in cache:
            faces = cache[key]
        elif k in early:
            faces = early[k]
        else:
            try:
                if f.model.kind == "thread":
                    faces = [_thread_face(f, k, mesh, owner, bounds, edge_tri)
                             or _thread_face(f, k, mesh, owner, bounds, edge_tri, straight=True)]
                elif f.kind == "trimmed":
                    if outlines.get(k) is None:
                        faces = [None]
                    elif k in cut:
                        faces = [cut[k]]
                    else:
                        faces = [_trimmed_cut(f, bound, mesh, *outlines[k], tol)]
                else:
                    faces = _patch_faces(f, bound, mesh)
            except Exception:
                # (OpenCascade gave up on one patch, a sweep of a degenerate profile say:
                # that patch fails like any other, not the whole conversion)
                faces = [None]
        if any(x is not None and _worst_tolerance(x) > MAX_FACE_TOL for x in faces):
            # (its outline doesn't lie on its surface: a torus piece cut to shape with
            # corners 20 mm "tolerant" swallowed six slots inside a part, which then
            # passed for bodies of their own. Failed here, its facets get a blend instead)
            faces = [None]
        cache[key] = faces
        if any(x is None for x in faces):
            failed.append(k)
            continue
        for x in faces:
            if f.kind == "ball":
                shells.append(x)
            else:
                builder.Add(comp, x)
    for fid in range(len(mesh.fn)):
        if owner[fid] >= 0 and owner[fid] not in failed:
            continue
        face = None
        if owner[fid] < 0:
            key = ("flat", fid, who(across[fid]))
            if key not in cache:
                cache[key] = _planar_face(mesh, fid, owner, bounds, edge_tri, edges)
            face = cache[key]
        if face is None:
            # fall back to the facet's own triangles
            for t in mesh.ftris[fid]:
                a, b, c = mesh.pts[mesh.tris[t]]
                w = BRepBuilderAPI_MakeWire(_line(a, b), _line(b, c), _line(c, a)).Wire()
                builder.Add(comp, BRepBuilderAPI_MakeFace(w, True).Face())
        else:
            builder.Add(comp, face)
    return comp, shells, failed


def sew(shape, tol, shells=(), pts=None):
    """Sew the faces into shells; closed shells made elsewhere (whole spheres) are added as
    they are. Returns (shape, midpoints of the edges left unmatched). pts: the mesh's
    points, for joining corners and edges before sewing (_rejoin)."""
    if pts is not None:
        shape = _rejoin(shape, pts, tol)
    else:
        # (sewing hands faces whose edges were shared already on unchanged, and the checks
        # after it widen tolerances in place: on the faces and edges build_faces keeps
        # for the next attempt, which then sewed apart. A copy keeps them as built,
        # the sharing within it kept; 0.7 s on 38,000 faces)
        shape = BRepBuilderAPI_Copy(shape, True, False).Shape()
    s = BRepBuilderAPI_Sewing(tol)
    s.Add(shape)
    s.Perform()
    free = []
    for i in range(1, s.NbFreeEdges() + 1):
        c = BRepAdaptor_Curve(s.FreeEdge(i))
        p = c.Value((c.FirstParameter() + c.LastParameter()) / 2)
        free.append((p.X(), p.Y(), p.Z()))
    sewn = _without_collapsed(s.SewedShape(), tol)
    if not shells:
        return sewn, free
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    builder.Add(comp, sewn)
    for shell in shells:
        builder.Add(comp, shell)
    return comp, free


def _rejoin(shape, pts, tol):
    """A copy of the faces joined where they are the same already, before sewing: corners
    at one mesh point (within REJOIN_SNAP) become one vertex, and edges between the same
    two corners on the same curve one edge. Faces cut in the worker processes come back
    as copies (read from files), on corners and edges of their own, so sewing had to
    match nearly every edge of a part (19,650 of 21,740 on the GPS case); it costs about
    as much per edge left to match as per face. Only exact matches are joined: an arc
    beside a chord is a gap for sewing to close, and an edge shorter than twice the sewing
    tolerance is sewing's to close up, with every edge meeting it (it merges the short
    edge's ends; with edges round it joined already, the edge came out closed up in one
    face and gone from the other: no closed solid). Their corners stay apart too: merged,
    they changed how sewing matched the edges round them (a free edge left at the end of
    a rounded edge, which the blame then dropped)."""
    from scipy.spatial import cKDTree
    builder = BRep_Builder()
    shape = BRepBuilderAPI_Copy(shape, True, False).Shape()
    # corners
    vmap = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, vmap)
    verts = [TopoDS.Vertex(vmap.FindKey(i)) for i in range(1, vmap.Extent() + 1)]
    if not verts:
        return shape
    P = np.array([(lambda p: (p.X(), p.Y(), p.Z()))(BRep_Tool.Pnt_s(v)) for v in verts])
    dist, near = cKDTree(pts).query(P)
    short = set()
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        e = TopoDS.Edge(ex.Current())
        ex.Next()
        if BRep_Tool.Degenerated_s(e):
            continue
        a, b = TopExp.FirstVertex_s(e), TopExp.LastVertex_s(e)
        if not a.IsNull() and not b.IsNull() and BRep_Tool.Pnt_s(a).Distance(BRep_Tool.Pnt_s(b)) <= 2 * tol:
            short |= {vmap.FindIndex(a) - 1, vmap.FindIndex(b) - 1}
    groups = {}
    for k in np.flatnonzero(dist <= REJOIN_SNAP):
        groups.setdefault(int(near[k]), []).append(int(k))
    reshape = BRepTools_ReShape()
    for ks in groups.values():
        if len(ks) < 2 or short.intersection(ks):
            continue
        keep = verts[ks[0]]
        reach = max(BRep_Tool.Tolerance_s(verts[k]) + float(np.linalg.norm(P[k] - P[ks[0]])) for k in ks)
        if reach > BRep_Tool.Tolerance_s(keep):
            builder.UpdateVertex(keep, reach)       # (sew's own copy)
        for k in ks[1:]:
            reshape.Replace(verts[k], keep.Oriented(verts[k].Orientation()))
    shape = reshape.Apply(shape)
    # edges: the free ones, by their two corners
    emap = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, emap)
    vmap = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, vmap)
    ends, short = {}, set()
    for i in range(1, emap.Extent() + 1):
        e = TopoDS.Edge(emap.FindKey(i))
        if BRep_Tool.Degenerated_s(e):
            continue
        a, b = TopExp.FirstVertex_s(e), TopExp.LastVertex_s(e)
        if a.IsNull() or b.IsNull():
            continue
        ia, ib = vmap.FindIndex(a), vmap.FindIndex(b)
        ends[i] = (ia, ib)
        if BRep_Tool.Pnt_s(a).Distance(BRep_Tool.Pnt_s(b)) <= 2 * tol:
            short |= {ia, ib}
    by_ends = {}
    for i, (ia, ib) in ends.items():
        if emap.FindFromIndex(i).Size() != 1 or ia in short or ib in short:
            continue
        by_ends.setdefault((min(ia, ib), max(ia, ib)), []).append(i)

    def face_of(i):
        return TopoDS.Face(emap.FindFromIndex(i).First())

    def flat(i):
        return BRepAdaptor_Surface(face_of(i), False).GetType() == GeomAbs_Plane

    def middle(c):
        return c.Value(0.5 * (c.FirstParameter() + c.LastParameter()))

    reshape = BRepTools_ReShape()
    for ii in by_ends.values():
        if len(ii) < 2:
            continue
        used = set()
        for x in ii:
            for y in ii:
                if x in used:
                    break
                if y == x or y in used:
                    continue
                if face_of(x).IsSame(face_of(y)):
                    continue    # (one face's two sides of a slit: joined, it uses the edge twice)
                # keep the edge of a curved face (k), and give it to the other face (r)
                k, r = (y, x) if flat(x) and not flat(y) else (x, y)
                ek, er = TopoDS.Edge(emap.FindKey(k)), TopoDS.Edge(emap.FindKey(r))
                ck, cr = BRepAdaptor_Curve(ek), BRepAdaptor_Curve(er)
                if middle(ck).Distance(middle(cr)) > 10 * REJOIN_SNAP:
                    continue
                same_way = TopExp.FirstVertex_s(ek).IsSame(TopExp.FirstVertex_s(er))
                same_params = (abs(ck.FirstParameter() - cr.FirstParameter()) <= 1e-9
                               and abs(ck.LastParameter() - cr.LastParameter()) <= 1e-9)
                r_flat = flat(r)
                if not (same_way and same_params):
                    # (r's curve on its surface is then in other parameters: only a flat
                    # face does without one, and only lines and arcs are surely one curve)
                    if not r_flat or ck.GetType() != cr.GetType() or ck.GetType() not in (GeomAbs_Line, GeomAbs_Circle):
                        continue
                tol = max(BRep_Tool.Tolerance_s(ek), BRep_Tool.Tolerance_s(er))
                if not r_flat:
                    face = face_of(r)
                    c2d = BRep_Tool.CurveOnSurface_s(er, face, cr.FirstParameter(), cr.LastParameter())
                    if c2d is None:
                        continue
                    loc = TopLoc_Location()
                    builder.UpdateEdge(ek, c2d, BRep_Tool.Surface_s(face, loc), loc, tol)
                elif tol > BRep_Tool.Tolerance_s(ek):
                    builder.UpdateEdge(ek, tol)
                reshape.Replace(er.Oriented(TopAbs_FORWARD), ek.Oriented(TopAbs_FORWARD if same_way else TopAbs_REVERSED))
                used |= {x, y}
    return reshape.Apply(shape)


def _without_collapsed(shape, tol):
    """The sewn shape less the edges sewing closed up: a sliver triangle's side, shorter
    than the sewing tolerance, whose two ends were merged into one vertex. Left in, the
    face beside it pinches into a loop that ShapeFix splits off as a face of its own, and
    a STEP reader takes such an edge for the whole circle (or curve) it lies on. A face
    whose every edge went (a sliver narrower than the sewing tolerance) goes too: with no
    outline it is the whole unbounded plane, which spoils the solid's volume and validity
    with nothing near to blame."""
    edges = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    reshape = None
    for i in range(1, edges.Extent() + 1):
        e = TopoDS.Edge(edges.FindKey(i))
        if BRep_Tool.Degenerated_s(e) or not TopExp.FirstVertex_s(e).IsSame(TopExp.LastVertex_s(e)):
            continue
        c = BRepAdaptor_Curve(e)
        a, b = c.Value(c.FirstParameter()), c.Value(c.LastParameter())
        # (a whole circle or closed outline is closed in itself: its ends coincide)
        if a.Distance(b) > 1e-6 and GCPnts_AbscissaPoint.Length_s(c) <= 2 * tol:
            if reshape is None:
                reshape = BRepTools_ReShape()
            reshape.Remove(e)
    if reshape is None:
        return shape
    shape = reshape.Apply(shape)
    bare = None
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        if not TopExp_Explorer(ex.Current(), TopAbs_EDGE).More():
            if bare is None:
                bare = BRepTools_ReShape()
            bare.Remove(ex.Current())
        ex.Next()
    return shape if bare is None else bare.Apply(shape)


PAIRS_AT_ONCE = 1_000_000  # point-triangle pairs measured in one go by point_facet_distance(within=...)


def point_facet_distance(mesh, facets, points, within=None):
    """Distance from each point to the nearest of the facets' triangles, (len(points),).
    (Not to their corners: a gap along a long straight edge has its middle far from
    every corner, though it lies right on the facets.) With `within` (mm): the distances
    up to that, the same; a point farther than that from every triangle gets inf or some
    distance over it. Then each point is measured only against the triangles that could
    be that near: measured against all of them, a big patch's thousands of triangles
    against a few thousand trouble points, a 900k-triangle mesh spent minutes of each
    blame here."""
    points = np.asarray(points, float)
    if within is not None:
        return _distance_within(mesh, facets, points, within)
    if len(points) > 32:        # (a few at a time: points x triangles arrays)
        return np.concatenate([point_facet_distance(mesh, facets, points[k:k + 32])
                               for k in range(0, len(points), 32)])
    T = mesh.tris[np.concatenate([mesh.ftris[x] for x in facets])]
    A, B, C = (mesh.pts[T[:, i]][None] for i in range(3))
    return _triangle_distance(points[:, None], A, B, C).min(axis=1)


def _triangle_distance(P, A, B, C):
    """Distance from points P to triangles ABC, element by element (broadcast: points
    against triangles, or matching rows of pairs; each pair's distance comes out the
    same bit for bit either way)."""
    # nearest point of each triangle's plane, kept only if it falls inside the triangle
    ab, ac = B - A, C - A
    n = np.cross(ab, ac)
    nn = np.maximum((n * n).sum(-1), 1e-300)
    h = ((P - A) * n).sum(-1) / nn
    Q = P - h[..., None] * n
    inside = np.ones(Q.shape[:-1], bool)
    for X, Y in ((A, B), (B, C), (C, A)):
        inside &= (np.cross(Y - X, Q - X) * n).sum(-1) >= 0
    d = np.where(inside, np.abs(h) * np.sqrt(nn), np.inf)
    # else the nearest point on an edge
    for X, Y in ((A, B), (B, C), (C, A)):
        e = Y - X
        t = np.clip(((P - X) * e).sum(-1) / np.maximum((e * e).sum(-1), 1e-300), 0, 1)
        d = np.minimum(d, np.linalg.norm(P - (X + t[..., None] * e), axis=-1))
    return d


def _distance_within(mesh, facets, points, within):
    """point_facet_distance up to `within`: each triangle measured only against the
    points within `within` of the ball round it (centred on its middle, through its
    farthest corner), which holds every point that near it."""
    from scipy.spatial import cKDTree
    d = np.full(len(points), np.inf)
    if not len(points):
        return d
    T = mesh.tris[np.concatenate([mesh.ftris[x] for x in facets])]
    A, B, C = (mesh.pts[T[:, i]] for i in range(3))
    mid = (A + B + C) / 3
    ball = np.sqrt(np.max([((X - mid) ** 2).sum(axis=1) for X in (A, B, C)], axis=0))
    # (a hair wider, for rounding: one point too many costs nothing)
    reach = (within + ball) * (1 + 1e-6) + 1e-9
    tree = cKDTree(points)
    counts = tree.query_ball_point(mid, reach, return_length=True)
    some = np.flatnonzero(counts)
    start = 0
    while start < len(some):
        stop = start + max(1, int(np.searchsorted(np.cumsum(counts[some[start:]]), PAIRS_AT_ONCE)))
        tris = some[start:stop]
        lists = tree.query_ball_point(mid[tris], reach[tris])
        which = np.concatenate([np.asarray(x, dtype=np.intp) for x in lists])
        tri = np.repeat(tris, [len(x) for x in lists])
        np.minimum.at(d, which, _triangle_distance(points[which], A[tri], B[tri], C[tri]))
        start = stop
    return d


def features_near(mesh, features, points, reach=0.5):
    """Indices of the features whose facets come within `reach` mm of any of the points."""
    if not points or not features:
        return []
    points = np.asarray(points)
    # (each patch's box kept on it, and all boxes tested at once: worked out afresh for
    # every patch on every call, this took 79 s of a 2,000-patch part's blame)
    boxes = np.array([_corner_box(mesh, f) for f in features])         # (patches, 2, 3)
    lo, hi = boxes[:, 0] - reach, boxes[:, 1] + reach
    hit = np.zeros(len(features), bool)
    for i in range(0, len(points), 500):
        p = points[i:i + 500][None]
        hit |= np.any(np.all((p >= lo[:, None]) & (p <= hi[:, None]), axis=2), axis=1)
    out = []
    for k in np.flatnonzero(hit):
        near = points[np.all((points >= lo[k]) & (points <= hi[k]), axis=1)]
        if point_facet_distance(mesh, features[k].facets, near, within=reach).min() <= reach:
            out.append(int(k))
    return out


def _corner_box(mesh, f):
    """The lowest and highest corner coordinates of a patch's facets (kept on the patch,
    for as long as its facets are the same)."""
    got = f.__dict__.get("_corner_box")
    if got is None or got[0] is not f.facets:
        P = mesh.pts[np.unique(np.concatenate([mesh.fverts[x] for x in f.facets]))]
        got = f._corner_box = (f.facets, (P.min(axis=0), P.max(axis=0)))
    return got[1]
