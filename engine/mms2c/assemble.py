"""Swap assembly (prototype): build the part without sewing it, patch by patch.

The part starts as the solid its bare facets make, every edge shared between the two
faces beside it (build.Edges with shared corners). Each patch's face is then made on its
surface from the very edges round its facets, swapped in for them, and checked on the
spot (valid, its area and the volume it adds as the mesh says, outward, no edge needing
more than MAX_EDGE_TOL): kept, or rolled back and its facets left as they were. There is
no global sewing and no rounds of blame; a patch that fails costs only itself. (Method
after refit.py, boromyr/STL2STEP, MIT licence; written afresh on our detection.)

Version 0: a patch's outline keeps the mesh edges it had (straight chords of its true
outline, within the chord sag of its surface). Later versions replace those chains with
one exact or spline edge on both sides (see build.Edges, local.apply_local).

    shape, report = assemble(mesh, features, mesh_tol)
"""
import dataclasses
import math
import time

import numpy as np
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import (BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeSolid,
                                BRepBuilderAPI_MakeWire)
from OCP.BRepTools import BRepTools_ReShape, BRepTools_WireExplorer
from OCP.GeomAbs import GeomAbs_Plane
from OCP.GeomAPI import GeomAPI_Interpolate
from OCP.ShapeBuild import ShapeBuild_ReShape
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.BRepLProp import BRepLProp_SLProps
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GProp import GProp_GProps
from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds, ShapeAnalysis_Surface
from OCP.ShapeFix import ShapeFix_Edge, ShapeFix_Face, ShapeFix_ShapeTolerance
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHAPE, TopAbs_VERTEX
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Shell
from OCP.collections import HArray1_gp_Pnt, HSequence_TopoDS_Shape, IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher
from OCP.Geom import (Geom_Circle, Geom_ConicalSurface, Geom_CylindricalSurface, Geom_RectangularTrimmedSurface,
                      Geom_SurfaceOfRevolution, Geom_ToroidalSurface)
from OCP.gp import gp_Ax1, gp_Ax2, gp_Ax3, gp_Dir, gp_Pnt

from . import build as B
from . import local
from .blends import _blend as as_blend, fallback as blend_fallback, pipe_fallback, split as split_blend
from .features import TWO_PI, Revolved, Sphere, half_rings

MAX_EDGE_TOL = 0.05     # mm: a patch whose outline needs wider edge tolerances stays faceted
AREA_SLACK = 0.05       # share of the facets' area a patch's face may differ by
SWAP_KINDS = ("trimmed", "revolve", "blend")
FALLBACK_DEPTH = 3      # a failed patch's fallbacks, theirs, ...: this many levels
SMOOTH_RUNS = True      # version 1: an outline run along a flat face is one smooth edge, not chords


def _surface(feature, k, mesh, owner, bounds, edge_tri, edges, tol):
    """The patch's surface (an OCC Geom_Surface), or None. A cylinder, cone or torus is
    made with its seam (where its angle starts) turned away from the patch: an outline
    crossing the seam makes a face OCC can't lay out (a whole ring has to cross it, and
    ShapeFix is left to add the seam edge)."""
    m = feature.model
    if isinstance(m, Revolved) and feature.kind in ("trimmed", "revolve"):
        a, d = np.asarray(m.a, float), np.asarray(m.d, float)
        c = mesh.fcent[feature.facets].mean(axis=0) - a
        away = -(c - (c @ d) * d)
        if np.linalg.norm(away) < 1e-9:
            away = np.cross(d, [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
        frame = gp_Ax3(gp_Pnt(*a), gp_Dir(*d), gp_Dir(*(away / np.linalg.norm(away))))
        surface = _revolved_surface(m, frame)
        if surface is not None:
            return surface if feature.span >= TWO_PI - 1e-9 else _trimmed_to(surface, mesh, feature)
    if feature.kind == "blend":
        face = B._blend_face(feature, k, mesh, owner, bounds, edge_tri, edges)
        return None if face is None else BRep_Tool.Surface_s(face)
    base = B._generous_surface(feature, bounds[k], mesh)
    if base is None:
        return None
    ex = TopExp_Explorer(base, TopAbs_FACE)
    return BRep_Tool.Surface_s(TopoDS.Face(ex.Current())) if ex.More() else None


def _trimmed_to(surface, mesh, feature):
    """The surface cut down to a little more than the patch's own parameter range, as a
    surface that isn't periodic. On a torus (periodic both ways) one loop parts the
    surface into two pieces, both finite, and OCC can't tell which is the face (bad
    orientation whichever way the loop runs)."""
    corners = np.unique(np.concatenate([mesh.fverts[f] for f in feature.facets]))
    corners = corners[:: max(1, len(corners) // 400)]
    sas = ShapeAnalysis_Surface(surface)
    uv = np.array([(lambda q: (q.X(), q.Y()))(sas.ValueOfUV(gp_Pnt(*map(float, mesh.pts[i])), 1e-6))
                   for i in corners])
    ranges = []
    for k, periodic in ((0, surface.IsUPeriodic()), (1, surface.IsVPeriodic())):
        x = uv[:, k]
        if periodic and x.max() - x.min() > math.pi:
            x = np.where(x < math.pi, x + TWO_PI, x)    # (the patch straddles the seam)
        span = x.max() - x.min()
        margin = max(0.05 * span, 0.02 if periodic else 0.5)
        ranges += [x.min() - margin, x.max() + margin]
    if surface.IsUPeriodic() and ranges[1] - ranges[0] >= TWO_PI:
        return surface
    if surface.IsVPeriodic() and ranges[3] - ranges[2] >= TWO_PI:
        return surface
    return Geom_RectangularTrimmedSurface(surface, *map(float, ranges))


def _revolved_surface(m, frame):
    """A Revolved model's cylinder, cone or torus in this frame (origin on the axis at
    the model's own origin, z the axis, x where the seam goes), or None."""
    if m.line:
        c0, k = m.line
        if abs(k) < 1e-9:
            return Geom_CylindricalSurface(frame, float(c0)) if c0 > 0 else None
        if c0 <= 0 or abs(math.atan(k)) >= math.pi / 2 - 1e-6:
            return None
        return Geom_ConicalSurface(frame, float(math.atan(k)), float(c0))
    rc, zc, r = m.circle
    if r <= 0:
        return None
    o = frame.Location()
    d = frame.Direction()
    x = frame.XDirection()
    centre = gp_Pnt(o.X() + zc * d.X(), o.Y() + zc * d.Y(), o.Z() + zc * d.Z())
    if rc > r:
        return Geom_ToroidalSurface(gp_Ax3(centre, d, x), float(rc), float(r))
    # a tube wider than its ring (a spindle torus crosses itself, and OCC can't tell a
    # face's inside on it): the profile circle swept round the axis instead
    tube = gp_Pnt(centre.X() + rc * x.X(), centre.Y() + rc * x.Y(), centre.Z() + rc * x.Z())
    profile = Geom_Circle(gp_Ax2(tube, d.Crossed(x), x), float(r))
    return Geom_SurfaceOfRevolution(profile, gp_Ax1(o, d))


class Registry:
    """The faces of the part as it is being assembled, and which edges each uses."""

    def __init__(self):
        self.faces = []                 # face id -> TopoDS_Face (None once swapped out)
        self.edges = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
        self.on = []                    # edge index - 1 -> set of face ids on it
        self.of_facet = {}              # mesh facet -> face ids covering it
        self.facets_of = []             # face id -> the mesh facets it covers
        self.planar = {}                # face id -> is it flat?
        self.patches = set()            # face ids of patches swapped in
        self.gone = {}                  # face id -> the face, while swapped out
        self.swapped = {}               # id(feature) -> (its face id, the face ids it replaced, its runs)

    def add(self, face, facets=()):
        i = len(self.faces)
        self.faces.append(face)
        self.facets_of.append([int(f) for f in facets])
        ex = TopExp_Explorer(face, TopAbs_EDGE)
        while ex.More():
            e = self.edges.Add(ex.Current())
            while len(self.on) < e:
                self.on.append(set())
            self.on[e - 1].add(i)
            ex.Next()
        for f in facets:
            self.of_facet.setdefault(int(f), []).append(i)
        return i

    def remove(self, i):
        ex = TopExp_Explorer(self.faces[i], TopAbs_EDGE)
        while ex.More():
            self.on[self.edges.FindIndex(ex.Current()) - 1].discard(i)
            ex.Next()
        self.gone[i] = self.faces[i]
        self.faces[i] = None

    def restore(self, i):
        """Put a face swapped out back (its edges are where they were)."""
        face = self.gone.pop(i)
        self.faces[i] = face
        ex = TopExp_Explorer(face, TopAbs_EDGE)
        while ex.More():
            self.on[self.edges.FindIndex(ex.Current()) - 1].add(i)
            ex.Next()

    def flat(self, i):
        if i not in self.planar:
            self.planar[i] = BRepAdaptor_Surface(self.faces[i]).GetType() == GeomAbs_Plane
        return self.planar[i]

    def users(self, edge):
        """The faces on this edge now."""
        k = self.edges.FindIndex(edge)
        return set(self.on[k - 1]) if k else set()

    def replace(self, i, face):
        """Face i replaced by a new version of itself (an outline run swapped): its id."""
        facets = self.facets_of[i]
        flat = self.planar.get(i)
        patch = i in self.patches
        self.remove(i)
        self.gone.pop(i, None)
        self.patches.discard(i)
        j = self.add(face, facets)
        if flat is not None:
            self.planar[j] = flat
        if patch:
            self.patches.add(j)
            for key, (new, old, runs) in self.swapped.items():
                if new == i:
                    self.swapped[key] = (j, old, runs)
        return j

    def revert(self, feature):
        """Take a patch's face out again and its facets' faces back in, its outline runs
        in the faces beside it turned back into the mesh's chords."""
        new, old, runs = self.swapped.pop(id(feature))
        self.patches.discard(new)
        for edge, chords in runs:
            wire = BRepBuilderAPI_MakeWire()
            for c in chords:
                wire.Add(TopoDS.Edge(c))
            for j in self.users(edge) - {new}:
                reshape = ShapeBuild_ReShape()
                reshape.Replace(edge, wire.Wire())
                self.replace(j, TopoDS.Face(reshape.Apply(self.faces[j])))
        self.remove(new)
        self.gone.pop(new, None)
        for i in old:
            self.restore(i)

    def open_edges(self):
        return sum(1 for s in self.on if len(s) == 1)

    def shell(self):
        shell = TopoDS_Shell()
        builder = BRep_Builder()
        builder.MakeShell(shell)
        for f in self.faces:
            if f is not None:
                builder.Add(shell, f)
        return shell


def _base(mesh, edges, tol):
    """The registry of the bare facets' faces, on shared corners and edges."""
    reg = Registry()
    owner = np.full(len(mesh.fn), -1)
    edge_tri = B._edge_tri(mesh)
    for fid in range(len(mesh.fn)):
        face = B._planar_face(mesh, fid, owner, [], edge_tri, edges)
        if face is None:
            reg.fallback = getattr(reg, "fallback", 0) + 1
            for t in mesh.ftris[fid]:
                a, b, c = (int(x) for x in mesh.tris[t])
                w = BRepBuilderAPI_MakeWire(edges.line(a, b), edges.line(b, c), edges.line(c, a))
                reg.add(BRepBuilderAPI_MakeFace(w.Wire(), True).Face(), [fid])
            continue
        for f in local.faces_of(face):
            reg.add(f, [fid])
    return reg


def _signed_volume(mesh, facets):
    """The facets' share of the mesh's volume (about the origin)."""
    T = mesh.tris[np.concatenate([mesh.ftris[f] for f in facets])]
    P = mesh.pts
    return float(np.einsum("ij,ij->i", P[T[:, 0]], np.cross(P[T[:, 1]], P[T[:, 2]])).sum() / 6)


def _face_volume(face):
    """The face's share of a solid's volume (about the origin), from its tessellation."""
    BRepMesh_IncrementalMesh(face, 0.001, False, 0.1, False)
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    if tri is None:
        return None
    move = loc.Transformation()
    P = np.array([(n.X(), n.Y(), n.Z()) for n in (tri.Node(i).Transformed(move) for i in range(1, tri.NbNodes() + 1))])
    T = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]) - 1
    v = float(np.einsum("ij,ij->i", P[T[:, 0]], np.cross(P[T[:, 1]], P[T[:, 2]])).sum() / 6)
    return -v if face.Orientation() == TopAbs_REVERSED else v


def _tolerances(shape):
    out = []
    for kind in (TopAbs_EDGE, TopAbs_VERTEX):
        ex = TopExp_Explorer(shape, kind)
        while ex.More():
            x = ex.Current()
            out.append((x, BRep_Tool.Tolerance_s(TopoDS.Edge(x)) if kind == TopAbs_EDGE
                        else BRep_Tool.Tolerance_s(TopoDS.Vertex(x))))
            ex.Next()
    return out


def _restore(saved):
    fix = ShapeFix_ShapeTolerance()
    for x, t in saved:
        fix.SetTolerance(x, t, TopAbs_SHAPE)


def _ordered(wire):
    """The wire's edges in order along it, each oriented as the wire runs."""
    out, ex = [], BRepTools_WireExplorer(wire)
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out


def _point(v):
    p = BRep_Tool.Pnt_s(v)
    return np.array([p.X(), p.Y(), p.Z()])


def _runs(reg, loop, region):
    """The loop's edges (mesh chords, in order) grouped into runs: [(outside face id or
    None, edges, closed)]. A run follows one flat face outside the region and turns no
    corner sharper than build.SHARP_DEG (a spline forced round a hairpin overshoots);
    None marks chords kept as they are (beside them a facet of a patch not yet swapped
    in: a smooth edge lies on none of its flat faces)."""
    def outside(e):
        o = reg.users(e) - region
        if len(o) != 1:
            return None
        o = next(iter(o))
        return o if o in reg.patches or reg.flat(o) else None

    n = len(loop)
    tags = [outside(e) for e in loop]
    P = [_point(TopExp.FirstVertex_s(TopoDS.Edge(e), True)) for e in loop]
    cos_sharp = math.cos(math.radians(B.SHARP_DEG))

    def breaks_at(i):           # between edge i-1 and edge i
        if tags[i - 1] != tags[i]:
            return True
        u, v = P[i] - P[i - 1], P[(i + 1) % n] - P[i]
        nu, nv = np.linalg.norm(u), np.linalg.norm(v)
        return bool(nu > 0 and nv > 0 and u @ v < cos_sharp * nu * nv)

    cuts = [i for i in range(n) if breaks_at(i)]
    if not cuts:
        return [(tags[0], loop, True)]
    out = []
    for a, b in zip(cuts, cuts[1:] + [cuts[0] + n]):
        out.append((tags[a], [loop[i % n] for i in range(a, b)], False))
    return out


def _run_edge(run, closed):
    """One smooth edge through a run's mesh corners, on the run's end corners (a closed
    run: one corner), or None. Its points lie in the flat face beside it, and so does a
    spline through them."""
    if len(run) < 2:
        return None
    verts = [TopoDS.Vertex(TopExp.FirstVertex_s(TopoDS.Edge(e), True)) for e in run]
    if not closed:
        verts.append(TopoDS.Vertex(TopExp.LastVertex_s(TopoDS.Edge(run[-1]), True)))
    P = B._even(np.array([_point(v) for v in verts]), closed)
    arr = HArray1_gp_Pnt(1, len(P))
    for i, p in enumerate(P):
        arr.SetValue(i + 1, gp_Pnt(*map(float, p)))
    interp = GeomAPI_Interpolate(arr, closed, 1e-7)
    interp.Perform()
    if not interp.IsDone():
        return None
    curve = interp.Curve()
    last = verts[0] if closed else verts[-1]
    maker = BRepBuilderAPI_MakeEdge(curve, verts[0], last, curve.FirstParameter(), curve.LastParameter())
    return maker.Edge() if maker.IsDone() else None


def _swap(reg, feature, surface, mesh):
    """Try the patch's face on the edges round its facets: (face id or None, why). With
    SMOOTH_RUNS each run of its outline along a flat face becomes one smooth edge, put
    into the flat face as well once the patch's face passes."""
    ids = sorted({i for f in feature.facets for i in reg.of_facet.get(int(f), []) if reg.faces[i] is not None})
    if not ids:
        return None, "facets gone"
    region = set(ids)
    # the region's outline: its edges with a face outside it
    rim = HSequence_TopoDS_Shape()
    rim_map = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    for i in ids:
        ex = TopExp_Explorer(reg.faces[i], TopAbs_EDGE)
        while ex.More():
            e = ex.Current()
            if not rim_map.Contains(e) and reg.users(e) - region:
                rim_map.Add(e)
                rim.Append(e)
            ex.Next()
    if rim.Length() == 0:
        return None, "no outline"
    wires = ShapeAnalysis_FreeBounds.ConnectEdgesToWires_s(rim, 1e-7, True)
    loops = [TopoDS.Wire(wires.Value(i)) for i in range(1, wires.Length() + 1)]
    normal = mesh.fn[feature.facets].T @ mesh.farea[feature.facets]

    def area(w):
        P = []
        ex = TopExp_Explorer(w, TopAbs_VERTEX)
        while ex.More():
            p = BRep_Tool.Pnt_s(TopoDS.Vertex(ex.Current()))
            P.append((p.X(), p.Y(), p.Z()))
            ex.Next()
        P = np.array(P)
        return abs(np.cross(P, np.roll(P, -1, axis=0)).sum(axis=0) @ normal) if len(P) > 2 else 0.0

    loops.sort(key=area, reverse=True)
    # the outline as the patch's face will have it: runs along flat faces made smooth
    runs = []           # (smooth edge, its chords in order, the flat face beside it)
    outline = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    made = []
    for w in loops:
        maker = BRepBuilderAPI_MakeWire()
        parts = _runs(reg, _ordered(w), region) if SMOOTH_RUNS else [(None, _ordered(w), False)]
        for tag, edges, closed in parts:
            smooth = _run_edge(edges, closed) if tag is not None else None
            if smooth is None:
                for e in edges:
                    maker.Add(TopoDS.Edge(e))
                    outline.Add(e)
            else:
                maker.Add(smooth)
                outline.Add(smooth)
                runs.append((smooth, edges, tag))
        if not maker.IsDone():
            return None, "outline"
        made.append(maker.Wire())
    loops = made
    target = float(mesh.farea[feature.facets].sum())
    saved = []
    for w in loops:
        saved += _tolerances(w)
    expected = _signed_volume(mesh, feature.facets) + feature.change
    allowed = feature.tolerance + 1e-3 + 0.01 * abs(feature.change)
    why = "no face"
    for flip, mend in ((False, False), (True, False), (False, True), (True, True)):
        ws = [TopoDS.Wire(w.Reversed()) for w in loops] if flip else loops
        maker = BRepBuilderAPI_MakeFace(surface, ws[0], False)
        for w in ws[1:]:
            maker.Add(w)
        if not maker.IsDone():
            continue
        if mend:
            fix = ShapeFix_Face(maker.Face())
            fix.Perform()
            face = fix.Face()
        else:
            # (just curves on the surface for its edges: ShapeFix's own orientation fix
            # picks the wrong side of a loop on a torus, whichever way the loop runs)
            face = maker.Face()
            ex = TopExp_Explorer(face, TopAbs_EDGE)
            while ex.More():
                fix_edge = ShapeFix_Edge()
                fix_edge.FixAddPCurve(TopoDS.Edge(ex.Current()), face, False)
                fix_edge.FixSameParameter(TopoDS.Edge(ex.Current()), face)
                ex.Next()
        # (a smooth run lies on the surface only within its bow between the corners:
        # its tolerance there is widened to cover that)
        for smooth, _, _ in runs:
            ShapeFix_Edge().FixSameParameter(smooth, face)
        why = _judge(face, outline, target, mesh, feature, expected, allowed)
        if why == "inside out":
            face = TopoDS.Face(face.Reversed())
            why = _judge(face, outline, target, mesh, feature, expected, allowed)
        if why is None:
            for i in ids:
                reg.remove(i)
            new = reg.add(face, feature.facets)
            # the flat faces beside it take the smooth runs in place of their chords
            by_face = {}
            for smooth, edges, tag in runs:
                by_face.setdefault(tag, []).append((smooth, edges))
            for tag, items in by_face.items():
                reshape = BRepTools_ReShape()
                for smooth, edges in items:
                    reshape.Replace(edges[0], smooth)       # (both run the chain's way)
                    for e in edges[1:]:
                        reshape.Remove(e)
                beside = TopoDS.Face(reshape.Apply(reg.faces[tag]))
                if tag in reg.patches:
                    # (a curved face beside it: the smooth runs need curves on its surface)
                    for smooth, _ in items:
                        fix_edge = ShapeFix_Edge()
                        fix_edge.FixAddPCurve(smooth, beside, False)
                        fix_edge.FixSameParameter(smooth, beside)
                reg.replace(tag, beside)
            reg.patches.add(new)
            reg.swapped[id(feature)] = (new, ids, [(smooth, edges) for smooth, edges, _ in runs])
            return new, "ok"
        _restore(saved)
    return None, why


def _judge(face, rim_map, target, mesh, feature, expected, allowed):
    """None if the face may go in, else why not."""
    if not BRepCheck_Analyzer(face).IsValid():
        return "invalid face"
    used = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    ex = TopExp_Explorer(face, TopAbs_EDGE)
    while ex.More():
        e = ex.Current()
        if not rim_map.Contains(e) and not BRep_Tool.IsClosed_s(TopoDS.Edge(e), face):
            return "new edges"          # (a seam is its own; anything else must be shared)
        used.Add(e)
        ex.Next()
    if any(not used.Contains(rim_map.FindKey(i)) for i in range(1, rim_map.Extent() + 1)):
        return "outline not used whole"
    if max((t for _, t in _tolerances(face)), default=0.0) > MAX_EDGE_TOL:
        return "edge tolerance"
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    if abs(props.Mass() - target) > AREA_SLACK * target + 1e-3:
        return "area"
    vol = _face_volume(face)
    if vol is None:
        return "no tessellation"
    # (a gross check, the whole part's volume being checked at the end: a face bounded
    # by the mesh's chords adds less than the change predicted for the true outline)
    if abs(-vol - expected) < abs(vol - expected):
        return "inside out"
    if abs(vol - expected) > allowed + abs(feature.change):
        return "volume"
    return None


def _fallback(mesh, f):
    """What takes a failed patch's place (as convert._build does), or None."""
    try:
        if f.model.kind == "freeform":
            return blend_fallback(mesh, f)
        if f.model.kind in ("pipe", "extrusion"):
            return pipe_fallback(mesh, f)
        if f.kind == "blend":
            return split_blend(mesh, f)
        return [as_blend(mesh, f.facets, ())] if len(f.facets) >= 3 else list(f.parts)
    except Exception:
        return None


def _half_rings(mesh, f):
    """A patch all the way round its axis as two half rings, each with the surface's
    seam turned away from it (an outline of mesh chords round a whole ring makes no face
    on a closed surface), or None. Exact rings (kind revolve) too: here they have the
    mesh's chords as outline like any other patch."""
    if not isinstance(f.model, Revolved) or getattr(f, "span", 0) < TWO_PI - 1e-9 or f.kind not in ("trimmed", "revolve"):
        return None
    return half_rings(mesh, f if f.kind == "trimmed" else dataclasses.replace(f, kind="trimmed"))


def solid_of(reg):
    """The assembled part's solid, as the registry has it now."""
    return BRepBuilderAPI_MakeSolid(reg.shell()).Solid()


def revert(reg, kept, blamed):
    """The patches blamed (indices into kept) taken out again, their facets left flat:
    (solid, patches still in)."""
    for k in blamed:
        reg.revert(kept[k])
    out = [f for k, f in enumerate(kept) if k not in set(blamed)]
    return solid_of(reg), out


def assemble(mesh, features, mesh_tol, tol=0.01):
    """(shape, report): the part built by swapping in each patch's face on the bare
    facets' shared edges. report: counts and timings."""
    t0 = time.time()
    edges = B.Edges(mesh.pts, {}, mesh, share=True, crowd=False)
    reg = _base(mesh, edges, tol)
    report = {"base_faces": len(reg.faces), "base_open": reg.open_edges(), "facets": len(mesh.fn),
              "fallback": getattr(reg, "fallback", 0), "base_s": time.time() - t0, "why": {}}
    owner = np.full(len(mesh.fn), -1)
    for k, f in enumerate(features):
        owner[f.facets] = k
    bounds = [B.Boundary(f, 10 * mesh_tol) for f in features]
    edge_tri = B._edge_tri(mesh)
    t1 = time.time()
    kept = []
    work = list(features)
    depth = [0] * len(work)
    k = 0
    while k < len(work):
        f = work[k]
        if f.kind not in SWAP_KINDS or f.model.kind == "thread":
            report["why"]["kind " + f.kind] = report["why"].get("kind " + f.kind, 0) + 1
            k += 1
            continue
        try:
            surface = _surface(f, k, mesh, owner, bounds, edge_tri, B.Edges(mesh.pts, None, mesh), mesh_tol)
            got, why = _swap(reg, f, surface, mesh) if surface is not None else (None, "no surface")
        except Exception as e:
            got, why = None, f"error {type(e).__name__}"
        report["why"][why] = report["why"].get(why, 0) + 1
        label = f"{why}: {f.kind}/{f.model.kind}"
        report.setdefault("detail", {})[label] = report.setdefault("detail", {}).get(label, 0) + 1
        if got is not None:
            kept.append(f)
        elif depth[k] < FALLBACK_DEPTH:
            # as the sewing build does: a patch that won't go in gives way to what it
            # replaced, or to smooth blends over its facets
            rings = _half_rings(mesh, f) if depth[k] == 0 else None
            for g in rings or _fallback(mesh, f) or ():
                if rings:
                    report.setdefault("ring_of", {})[id(g)] = f   # (the ring is built, in halves)
                work.append(g)
                depth.append(depth[k] + 1)
                bounds.append(B.Boundary(g, 10 * mesh_tol))
                owner[g.facets] = len(work) - 1
        k += 1
    report["swap_s"] = time.time() - t1
    report["kept"] = len(kept)
    report["open"] = reg.open_edges()
    shell = reg.shell()
    solid = BRepBuilderAPI_MakeSolid(shell).Solid()
    report["faces"] = sum(1 for f in reg.faces if f is not None)
    report["registry"] = reg
    return solid, kept, report
