"""The benchmark's judge: one scorecard for a STEP made from a mesh, the same for every
program. Reads the file back as a CAD program would and reports:

- solids, shells (and how many are open), faces, BRepCheck validity
- volume (OCC's integration and a tessellation's) against the mesh's
- faces by surface type, and flat triangles (planar faces with three edges: facets
  left as they were)
- distance mesh -> solid from the mesh's corners and solid -> mesh from points sampled by area on the
  faces (mesh -> solid by area too, as mesh_to_solid_area, reported only until the owners decide), all measured exactly where the tessellation reads high: OCC tessellates
  big B-spline faces coarsely whatever deflection is asked (0.07 mm off at 0.002), so
  the tessellation alone would mark down spline-heavy results.
- solid -> mesh again with each mesh triangle's chordal sag allowed for (chordal_sag): a
  true curve bulges past a coarse mesh's flat facets, by 0.134 mm on MMM's
  cad_plate_holes_coarse (16-gon holes), so the right answer, true cylinders, failed the
  0.1 mm test where a copy of the facets passed (found by MMM's author's Claude, 10-25).
  The same allowance is made for mesh -> solid's points inside triangles (mesh corners lie
  on the true surface and need none).
- design detail lost, which closeness can't see (lost_detail.py): the mesh's exact planes,
  cylinders and cones that the file no longer has, and smooth regions shredded into many
  small faces. Reported only (lost_detail): verdict() doesn't read it.
- measured against what the mesh encloses (Enclosure, by ray counting): a mesh of bodies pressed into
  and onto each other, or touching themselves, encloses their union. Its volume is the
  reference (each overlap once), and mesh -> solid counts only corners on the union's
  boundary, not walls with the part on both sides, which no correct solid has (the
  generalized winding number's idea: Jacobson, Kavan and Sorkine-Hornung, 2013). The
  all-triangles figures stay on the card as volume_error_raw and mesh_to_solid_raw. On a
  mesh of separate, clean pieces nothing changes. Inside means a winding number other than
  zero (parity only for pieces whose triangles disagree), so a piece overlapping itself
  counts once and a cavity is hollow. Tiny voids (cavities under VOID_SHARE of their body,
  which a solid may drop) are left out of mesh -> solid.
- the mesh's volume with its winding made to agree first (agree_winding): a patch of
  triangles facing the wrong way makes the file's signed sum depend on the origin (a Pi case
  lid: 7059, 7920 or 8565 mm^3, truly 8409), which graded a right solid +12.5%.
- a second volume, only on demand (true_rays, second_volume): where a closed, valid solid
  fails the volume test and its faces don't close (Sum n dA isn't 0, so OCC's integral
  depends on the origin), rays through the true trimmed faces against the same rays through
  the mesh. Stray edges also lift the tessellation, so neither first figure can be trusted
  there: pinecil lids read -1.3 to -1.8%, truly within 0.2%.

A result is good (verdict()) when it reads back as valid closed solids with no open
shell, its volume within GOOD_VOLUME of the mesh's, and CLOSE of the points both ways
within CLOSE_MM of the other surface: what a CAD program can use as is. The benchmark
grades every program by it.
(A copy of compare/bench/judge.py, the benchmark's own, plus verdict(): keep them the same.)

usage: python -m mms2c.judge MESH STEP OUT.json [--no-detail]      (trimesh + cadquery-ocp)"""
import json
import os
import sys
import time

import numpy as np
import trimesh
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import (GeomAbs_BezierSurface, GeomAbs_BSplineSurface, GeomAbs_Cone, GeomAbs_Cylinder,
                         GeomAbs_OffsetSurface, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_SurfaceOfExtrusion,
                         GeomAbs_SurfaceOfRevolution, GeomAbs_Torus)
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Pnt
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

DEFLECTION = 0.002   # mm, tessellation for the distances and the tessellated volume
GOOD_VOLUME = 0.01   # share of the mesh's volume the solid may differ by
RAYS = 512           # rays a side across the part for the enclosed volume (Enclosure)
CELLS = 128          # cells a side of the xy grid the rays look up triangles in
VOID_SHARE = 1e-3    # share of its body's volume under which a cavity is a tiny void (as small as
                     # the engines drop: mmm/brep.make_solid_checked max_void_fraction)
ENCLOSED_TOL = 0.003 # share of the volume by which the rays' figure may differ from the exact one
                     # (their method error) before the mesh counts as enclosing less than its triangles
CRACK = 0.05         # mm: a mesh corner with the part this far off on both sides is on a crack's wall
CRACK_SHARE = 0.02   # share of the corners from which those walls are left out of mesh -> solid
SAMPLES = 20000      # points each way (5,000 on meshes over BIG triangles)
BIG = 300_000
RECHECK = 0.01       # mm: tessellation distances above this are measured exactly
RECHECK_MOST = 4000  # at most this many points each way, the farthest first
RETRIES = ((0.01, 0.2), (0.05, 0.5), (0.2, 0.8))   # coarser tessellations for faces BRepMesh gave up on
COVERED = 0.98       # a face whose triangles cover less of its area than this gets exact checks
TRUE_RAYS = 192      # rays a side across the part for the second volume (true_rays)
TRUE_RAYS_MOST = 3_000_000  # ray-face tests it may make; fewer rays above that (a count, not a clock: the
                     # judge must grade alike on every machine)
TRUE_SEAM = 0.25     # mm: two crossings this close on a ray with an odd count are one crossing of a seam
TRUE_ODD_SHARE = 0.01  # share of the rays that hit something which may cross a solid an odd number of times
                     # (a gap or a graze) before the second volume says nothing
TRUE_REFERENCE = 0.01  # share by which the mesh's rays may differ from the mesh's volume before the rays
                     # are taken to be too few for this part
CLOSURE_SHARE = 0.0005 # share of the volume by which a solid's integral may move across the part (from
                     # Sum n dA) before its faces count as not closing: a closed solid moves it by nothing

try:
    from mmm import faults, meshguard
except ImportError:                 # (run as a script: compare/bench/judge.py)
    from mmm import faults, meshguard

TYPES = {GeomAbs_Plane: "plane", GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone", GeomAbs_Sphere: "sphere",
         GeomAbs_Torus: "torus", GeomAbs_BezierSurface: "bezier", GeomAbs_BSplineSurface: "bspline",
         GeomAbs_SurfaceOfRevolution: "revolution", GeomAbs_SurfaceOfExtrusion: "extrusion",
         GeomAbs_OffsetSurface: "offset"}


def quiet(fn, *a):
    """Run fn with OCC's console chatter (stdout at the file-descriptor level) muted."""
    saved, devnull = os.dup(1), os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        return fn(*a)
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)


def read(path):
    r = STEPControl_Reader()
    if r.ReadFile(path) != IFSelect_RetDone:
        return None
    r.TransferRoots()
    return r.OneShape()


def listed(shape, kind):
    out, ex = [], TopExp_Explorer(shape, kind)
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out


def tessellate(shape, faces, deflection=DEFLECTION, notes=None):
    """Triangles of every face (outward, by the face's orientation), the face each came
    from, and the faces the tessellation doesn't cover (none, or under COVERED of the face's
    area: the distances look past those, so they get exact checks of their own). OCC's mesher
    holds Python's lock, and on the drybox axle's 119-face solid 0.005 mm took 1.3 s and 0.002 mm
    was still going after 20 minutes, so it is called through meshguard: a face that doesn't
    finish is measured rougher, on its own. notes (a dict, if given) gets "coarse_faces" and
    "unmeshed_faces", how many such faces there were."""
    meshed = quiet(meshguard.mesh, shape, deflection, False, 0.1, True)
    if notes is not None:
        notes["coarse_faces"] = len(meshed.coarse)
        notes["unmeshed_faces"] = len(meshed.unmeshed)
    V, F, owner, base, area = [], [], [], 0, np.zeros(len(faces))
    for k, face in enumerate(faces):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        # BRepMesh gives up on some faces at a fine deflection; retry coarser
        for lin, ang in RETRIES:
            if tri is not None:
                break
            quiet(meshguard.mesh, face, lin, False, ang, False)
            tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        n = tri.NbNodes()
        P = np.array([tri.Node(i).Transformed(trsf).Coord() for i in range(1, n + 1)])
        T = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]).reshape(-1, 3) - 1
        if face.Orientation() == TopAbs_REVERSED:
            T = T[:, [0, 2, 1]]
        area[k] = 0.5 * np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1).sum()
        V.append(P)
        F.append(T + base)
        owner.extend([k] * len(T))
        base += n
    mesh = trimesh.Trimesh(np.vstack(V) if V else np.zeros((0, 3)), np.vstack(F) if F else np.zeros((0, 3), int),
                           process=False)
    suspects = []
    for k, face in enumerate(faces):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        if area[k] < COVERED * props.Mass() - 1e-4:
            box = Bnd_Box()
            BRepBndLib.Add_s(face, box)
            suspects.append((k, np.array(box.CornerMin().Coord()), np.array(box.CornerMax().Coord())))
    return mesh, np.array(owner, dtype=np.int64), suspects


SMOOTH_BEND = (1.0, 40.0)   # degrees: a bend between neighbouring triangles counted as curvature


def chordal_sag(mesh):
    """How far a true curved surface may bulge past each flat triangle of the mesh: across
    an edge where neighbouring triangles bend by th (within SMOOTH_BEND), the sagitta of
    the arc is about h * th / 8, h the smaller of the two triangles' heights across that
    edge (MMM's fidelity.chord_deflection: where a long facet meets a tiny triangle, the
    long one says nothing about curvature); each triangle takes its largest. Sharper bends
    are edges of the design, not curvature."""
    out = np.zeros(len(mesh.faces))
    if len(mesh.faces) < 2:
        return out
    f, g = mesh.face_adjacency.T
    e = mesh.face_adjacency_edges
    th = mesh.face_adjacency_angles
    L = np.maximum(np.linalg.norm(mesh.vertices[e[:, 0]] - mesh.vertices[e[:, 1]], axis=1), 1e-30)
    A = mesh.area_faces
    sag = np.minimum(2 * A[f] / L, 2 * A[g] / L) * th / 8.0
    sag[(th < np.radians(SMOOTH_BEND[0])) | (th > np.radians(SMOOTH_BEND[1]))] = 0.0
    np.maximum.at(out, f, sag)
    np.maximum.at(out, g, sag)
    return out


def touches_itself(mesh):
    """Whether an edge of the mesh has more than two triangles."""
    e = np.sort(np.asarray(mesh.edges), axis=1)
    return bool(len(e)) and int(np.unique(e, axis=0, return_counts=True)[1].max()) > 2


def stats(d):
    d = np.asarray(d)
    if not len(d):
        return None
    return {"median": float(np.median(d)), "p99": float(np.percentile(d, 99)), "max": float(d.max()),
            "within_0.02": float((d <= 0.02).mean()), "within_0.05": float((d <= 0.05).mean()),
            "within_0.1": float((d <= 0.1).mean())}


def exact_to_faces(points, dist, face_ids, faces, suspects):
    """Distances above RECHECK measured exactly: to the face the tessellation found
    nearest, and to any face the tessellation doesn't cover whose box is nearer than
    that; each kept only if smaller (the tessellation's is a true distance too)."""
    d = dist.copy()
    far = np.nonzero(d > RECHECK)[0]
    far = far[np.argsort(-d[far])][:RECHECK_MOST]
    for i in far:
        p = points[i]
        near = [k for k, lo, hi in suspects if np.linalg.norm(np.maximum(0, np.maximum(lo - p, p - hi))) < d[i]]
        v = BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex()
        for k in [face_ids[i]] + near:
            ext = BRepExtrema_DistShapeShape(v, faces[k])
            if ext.IsDone():
                d[i] = min(d[i], ext.Value())
    return d, len(far)


def on_surfaces(points, face_ids, faces):
    """Points sampled on tessellation triangles moved onto their face's true surface."""
    out = points.copy()
    projectors = {}
    for i, k in enumerate(face_ids):
        pr = projectors.get(k)
        if pr is None:
            pr = projectors[k] = GeomAPI_ProjectPointOnSurf()
            u0, u1, v0, v1 = BRepTools.UVBounds_s(faces[k])
            pr.Init(BRep_Tool.Surface_s(faces[k]), u0, u1, v0, v1)
        pr.Perform(gp_Pnt(*points[i]))
        if pr.IsDone() and pr.NbPoints() > 0:
            out[i] = pr.NearestPoint().Coord()
    return out


def _rotation():
    """A fixed turn of the axes, so no ray runs along a CAD part's walls or edges."""
    a, b, c = 0.3172, 0.4511, 0.2733
    Rx = np.array([[1, 0, 0], [0, np.cos(a), -np.sin(a)], [0, np.sin(a), np.cos(a)]])
    Ry = np.array([[np.cos(b), 0, np.sin(b)], [0, 1, 0], [-np.sin(b), 0, np.cos(b)]])
    Rz = np.array([[np.cos(c), -np.sin(c), 0], [np.sin(c), np.cos(c), 0], [0, 0, 1]])
    return Rz @ Ry @ Rx


def agree_winding(F, V=None):
    """Triangles turned so that neighbours agree which way they face: (F, how many turned).
    Two triangles meeting at an edge agree when they run along it in opposite directions; an
    edge of any other count says nothing. Each patch joined by such edges keeps the way most of
    its area faces, so a mesh that already agrees comes back unchanged and a body wholly inside
    out stays as it is. A patch that can't agree (a one-sided surface) is left as it was.

    Why turning is safe: on a closed surface only one winding gives a volume that doesn't
    depend on the origin, and it is the region's own, so what it hides is only the file's
    wrong triangles, not a wrong solid: the solid is still compared with that volume."""
    F = np.asarray(F, np.int64)
    m = len(F)
    if not m:
        return F, 0
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    # (each directed edge, its triangle, and which way it runs between the sorted ends)
    e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
    owner = np.tile(np.arange(m), 3)
    forward = e[:, 0] < e[:, 1]
    lo, hi = e.min(axis=1), e.max(axis=1)
    _, inv, cnt = np.unique(lo * (int(F.max()) + 1) + hi, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    two = np.nonzero(cnt[inv] == 2)[0]
    two = two[np.argsort(inv[two], kind="stable")]
    a, b = two[::2], two[1::2]
    if (forward[a] != forward[b]).all():      # (every neighbour agrees already: nearly every mesh)
        return F, 0
    # every triangle twice, as it is (t) and turned (t + m); neighbours that agree link like with
    # like, those that don't link it with turned. A patch that can agree makes two separate
    # copies of itself; one that can't joins them.
    ta, tb = owner[a], owner[b]
    same = forward[a] != forward[b]
    u = np.concatenate([ta[same], ta[same] + m, ta[~same], ta[~same] + m])
    v = np.concatenate([tb[same], tb[same] + m, tb[~same] + m, tb[~same]])
    _, label = connected_components(coo_matrix((np.ones(len(u)), (u, v)), shape=(2 * m, 2 * m)), directed=False)
    as_is, turned = label[:m], label[m:]
    can = as_is != turned
    first = np.minimum(as_is, turned)         # (the two copies' one name)
    flip = can & (as_is != first)             # (one way of agreeing, the copy with the smaller label)
    if V is not None:
        P = np.asarray(V, float)
        weight = 0.5 * np.linalg.norm(np.cross(P[F[:, 1]] - P[F[:, 0]], P[F[:, 2]] - P[F[:, 0]]), axis=1)
    else:
        weight = np.ones(m)
    _, patch = np.unique(first, return_inverse=True)
    patch = patch.ravel()
    turned_area = np.bincount(patch, weight * flip)
    whole_area = np.bincount(patch, weight)
    other = turned_area > 0.5 * whole_area    # (most of it would turn: turn the others instead)
    flip = can & (flip != other[patch])
    if not flip.any():
        return F, 0
    out = F.copy()
    out[flip] = out[flip][:, [0, 2, 1]]
    return out, int(flip.sum())


class Enclosure:
    """The region a mesh encloses, from plain ray counting on its own triangles (nothing of
    any converter's code). A closed piece is one whose every edge has an even number of
    triangles, however they face, so open sheets enclose nothing. Rays run straight up in
    turned axes (_rotation), each tested against the triangles over its own cell of an xy
    grid only.

    A point is inside where the winding number is not zero: each crossing counts +1 or -1
    by which way its triangle faces (each piece turned as its outermost piece must be to
    face out), so a piece overlapping itself counts twice there and stays inside, a wall of
    zero thickness (+1 then -1) changes nothing, a cavity (facing in) cancels the body
    round it and a solid sunk in a body adds to it. A piece whose triangles disagree about
    which way they face can't be counted with signs: its crossings count by parity (odd:
    inside) instead (Tuna's Claude's review on #2: parity alone missed a piece overlapping
    itself, -26% on two boxes sharing an edge).

    volume: the length inside along RAYS x RAYS parallel rays across the part, times their
    spacing squared; error: how far half as many rays a side differ from it (the method's
    own error, large where walls are thin against the part's size). None if no piece is
    closed. tiny_voids: corners of cavities holding under VOID_SHARE of their body.

    agree: for a mesh, whose triangles a program may have left facing the wrong way: on the
    shortcut for separate clean pieces, the winding is made to agree (agree_winding) and each
    piece counts by its size. The signed sum alone moved with the origin on a Pi case lid.
    Off for a solid's tessellation, where a face turned the wrong way is the solid's own fault.
    flipped: how many triangles that turned."""

    def __init__(self, V, F, agree=False):
        V = np.asarray(V, float)
        F = np.asarray(F, np.int64)
        F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 2] != F[:, 0])]
        self.volume = self.error = None
        self.simple = False
        self.tiny_voids = np.zeros(len(V), bool)
        self.tiny_void_count = 0
        self.flipped = 0
        self.rays_ready = False
        if not len(F):
            return
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        n = len(V)
        e = np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]])
        _, label = connected_components(coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)), directed=False)
        piece = label[F[:, 0]]
        es = np.sort(e, axis=1)
        key = es[:, 0] * n + es[:, 1]
        _, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
        odd = (cnt % 2 == 1)[inv.ravel()]                  # (edges of an odd count: an open piece)
        open_piece = np.unique(np.tile(piece, 3)[odd])
        keep = ~np.isin(piece, open_piece)
        if not keep.any():
            return
        # (open pieces holding real volume, a mesh with cracks say: the rays can't count
        # them, so the judge keeps the mesh's own figures)
        a, b, c = (V[F[:, k]] for k in range(3))
        tet = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6
        if abs(tet[~keep].sum()) > ENCLOSED_TOL * max(abs(tet.sum()), 1e-12):
            return
        self.piece = np.unique(piece[keep], return_inverse=True)[1].ravel()
        self.closed_pieces = int(self.piece.max()) + 1
        # (every edge of two triangles and no two pieces' boxes overlapping: nothing can lie
        # in or on anything else, so the region is the mesh's own; no rays needed. Most
        # meshes, among them a 1.1M-triangle scan the rays took 550 s on)
        if (cnt == 2).all():
            boxes = [(V[np.unique(F[keep][self.piece == k])].min(axis=0), V[np.unique(F[keep][self.piece == k])].max(axis=0))
                     for k in range(self.closed_pieces)]
            if not any(np.all(boxes[i][0] < boxes[j][1]) and np.all(boxes[j][0] < boxes[i][1])
                       for i in range(len(boxes)) for j in range(i + 1, len(boxes))):
                self.simple = True
                self.volume = abs(float(tet.sum()))
                if agree:
                    F, self.flipped = agree_winding(F, V)
                    a, b, c = (V[F[:, k]] for k in range(3))
                    tet = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6
                    # (no piece lies in another here, so none is a cavity: each counts by its size)
                    self.volume = float(np.abs(np.bincount(self.piece, tet)).sum())
                self._later = (V, F[keep], self.piece)
                return
        self._prepare(V, F[keep])
        if not self.ambiguous:
            hi = (V @ _rotation().T)[F[keep]][:, :, :2].max(axis=(0, 1))
            self.volume = self._volume(hi, RAYS)
            self.error = abs(self._volume(hi, RAYS // 2) - self.volume)

    def _prepare(self, V, Fk):
        """What the rays need: the triangles in turned axes over their grid, each piece's role."""
        Vr = V @ _rotation().T
        self.T = Vr[Fk]
        lo = self.T[:, :, :2].min(axis=(0, 1))
        hi = self.T[:, :, :2].max(axis=(0, 1))
        self.lo, self.size = lo, float((hi - lo).max()) / CELLS + 1e-12
        tlo = np.floor((self.T[:, :, :2].min(axis=1) - lo) / self.size).astype(int)
        thi = np.floor((self.T[:, :, :2].max(axis=1) - lo) / self.size).astype(int)
        self.grid = {}
        for t in range(len(self.T)):
            for i in range(tlo[t, 0], thi[t, 0] + 1):
                for j in range(tlo[t, 1], thi[t, 1] + 1):
                    self.grid.setdefault((i, j), []).append(t)
        # which way each triangle faces along the rays (+1: up), as the file has it
        nz = np.cross(self.T[:, 1] - self.T[:, 0], self.T[:, 2] - self.T[:, 0])[:, 2]
        self.up = np.sign(nz).astype(np.int64)
        self.ambiguous = False
        self._bodies(Vr, Fk)
        self.rays_ready = True

    def rays(self):
        """Whether ray_lengths can count this mesh: the shortcut for clean pieces builds the
        rays' tables only now, on demand. Not where a mesh's pieces can't be told apart."""
        if not self.rays_ready and getattr(self, "_later", None) is not None:
            self._prepare(*self._later[:2])
        return self.rays_ready and not self.ambiguous

    def _bodies(self, Vr, Fk):
        """Each piece's role. A piece wholly inside another (every sampled corner inside it,
        by its own crossings) is nested in the tightest such piece. Nested and facing the
        other way it is a cavity, or a solid in a cavity; facing the same way, a solid sunk
        in it (toolbox obj_3_top: six boxes inside its lid). Every piece is turned as its
        outermost piece must be to face out, so the signs add up (self.sign). A piece whose
        triangles disagree is counted by parity (self.parity); if such a piece is nested,
        cavity or solid can't be told: self.ambiguous, and the judge keeps the mesh's own
        figures. Cavities under VOID_SHARE of their body: self.tiny_voids (their corners)."""
        n = self.closed_pieces
        corners = [np.unique(Fk[self.piece == k]) for k in range(n)]
        sample = [Vr[c[:: max(1, len(c) // 64)]] for c in corners]
        lo = np.array([Vr[c].min(axis=0) for c in corners])
        hi = np.array([Vr[c].max(axis=0) for c in corners])
        sign, agree, vol = np.zeros(n), np.zeros(n, bool), np.zeros(n)
        for k in range(n):
            P = Fk[self.piece == k]
            a, b, c = (Vr[P[:, i]] for i in range(3))
            vol[k] = np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6
            sign[k] = np.sign(vol[k])
            d = np.concatenate([P[:, [0, 1]], P[:, [1, 2]], P[:, [2, 0]]])
            fwd = d[:, 0] * len(Vr) + d[:, 1]
            agree[k] = np.isin(d[:, 1] * len(Vr) + d[:, 0], fwd).all()
        self.parity = ~agree
        parent = np.full(n, -1)
        for k in range(n):
            best = None
            for j in range(n):
                if j == k or not (np.all(lo[j] <= lo[k]) and np.all(hi[k] <= hi[j])):
                    continue
                if self._inside_piece(sample[k], j, agree[j]).all():
                    size = float(np.prod(hi[j] - lo[j]))
                    if best is None or size < best[0]:
                        best = (size, j)
            if best:
                parent[k] = best[1]
        self.ambiguous = bool(((parent >= 0) & ~agree).any())
        root = np.arange(n)
        for k in range(n):
            while parent[root[k]] >= 0:
                root[k] = parent[root[k]]
        turn = np.where(sign[root] < 0, -1, 1)
        self.sign = self.up * turn[self.piece]
        void = (parent >= 0) & agree & (sign != sign[np.maximum(parent, 0)]) \
            & (np.abs(vol) < VOID_SHARE * np.abs(vol[root]))
        for k in np.nonzero(void)[0]:
            self.tiny_voids[corners[k]] = True
        self.tiny_void_count = int(void.sum())

    def _inside_piece(self, P, k, signed):
        """Which points (turned axes) are inside piece k by its own crossings only: winding
        number not zero, or parity where its triangles disagree."""
        result = np.zeros(len(P), bool)
        for who, ts in self._cells(P):
            ts = ts[self.piece[ts] == k]
            if not len(ts):
                continue
            hit, z = self._hits(P[who], ts)
            above = hit & (z > P[who][:, None, 2])
            if signed:
                result[who] = (above * self.up[ts]).sum(axis=1) != 0
            else:
                result[who] = (above.sum(axis=1) % 2) == 1
        return result

    def _hits(self, P, ts):
        """For rays straight up from the points P (m, 3) against triangles ts: (hit (m, k), z (m, k))."""
        A, B, C = self.T[ts, 0], self.T[ts, 1], self.T[ts, 2]
        p = P[:, None, :]
        d = (B[:, 1] - C[:, 1]) * (A[:, 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (A[:, 1] - C[:, 1])
        # (an absolute floor, in the mesh's units squared: fine for millimetres or metres,
        # too high for a part saved in microns)
        ok = np.abs(d) > 1e-18
        d = np.where(ok, d, 1.0)
        l1 = ((B[:, 1] - C[:, 1]) * (p[..., 0] - C[:, 0]) + (C[:, 0] - B[:, 0]) * (p[..., 1] - C[:, 1])) / d
        l2 = ((C[:, 1] - A[:, 1]) * (p[..., 0] - C[:, 0]) + (A[:, 0] - C[:, 0]) * (p[..., 1] - C[:, 1])) / d
        l3 = 1 - l1 - l2
        z = l1 * A[:, 2] + l2 * B[:, 2] + l3 * C[:, 2]
        return ok & (l1 >= 0) & (l2 >= 0) & (l3 >= 0), z

    def _cells(self, P):
        cell = np.floor((P[:, :2] - self.lo) / self.size).astype(int)
        keys = cell[:, 0] * 100003 + cell[:, 1]
        for key in np.unique(keys):
            who = np.nonzero(keys == key)[0]
            ts = self.grid.get((int(cell[who[0], 0]), int(cell[who[0], 1])))
            if ts:
                yield who, np.array(ts)

    def inside(self, points):
        """Which points (in the mesh's own axes) lie inside the enclosed region."""
        P = np.asarray(points, float) @ _rotation().T
        result = np.zeros(len(P), bool)
        for who, ts in self._cells(P):
            hit, z = self._hits(P[who], ts)
            hit &= z > P[who][:, None, 2]
            par = self.parity[self.piece[ts]]
            wind = (hit & ~par) @ self.sign[ts]
            counts = np.zeros((len(who), self.closed_pieces), np.int64)
            rows, cols = np.nonzero(hit & par)
            np.add.at(counts, (rows, self.piece[ts][cols]), 1)
            result[who] = (wind != 0) | (counts % 2 == 1).any(axis=1)
        return result

    def _volume(self, hi, rays):
        lo = self.lo
        span = (hi - lo).max()
        h = span / rays
        g = (np.arange(rays) + 0.5) * h
        X, Y = np.meshgrid(lo[0] + g, lo[1] + g, indexing="ij")
        keep = (X.ravel() <= hi[0]) & (Y.ravel() <= hi[1])
        P = np.column_stack([X.ravel()[keep], Y.ravel()[keep], np.zeros(int(keep.sum()))])
        return float(self.ray_lengths(P).sum()) * h * h

    def ray_lengths(self, P):
        """The length inside along a ray straight up (turned axes) through each of the points P."""
        out = np.zeros(len(P))
        for who, ts in self._cells(P):
            hit, z = self._hits(P[who], ts)
            pc, sg = self.piece[ts], self.sign[ts]
            for r in range(len(who)):
                m = hit[r]
                if not m.any():
                    continue
                zs, ps, ss = z[r, m], pc[m], sg[m]
                use = np.ones(len(zs), bool)
                for q in np.unique(ps):
                    on = ps == q
                    # (grazing a piece's rim: its crossings don't add up, so it is left out)
                    if (on.sum() % 2) if self.parity[q] else ss[on].sum():
                        use[on] = False
                if not use.any():
                    continue
                order = np.argsort(zs[use], kind="stable")
                zs, ps, ss = zs[use][order], ps[use][order], ss[use][order]
                # up the ray: below every crossing the winding is 0; past each it drops by its sign
                wind, odd, start, inside = 0, set(), 0.0, False
                for zz, q, s in zip(zs, ps, ss):
                    if self.parity[q]:
                        odd ^= {q}
                    else:
                        wind -= s
                    now = wind != 0 or bool(odd)
                    if now and not inside:
                        start = zz
                    elif inside and not now:
                        out[who[r]] += zz - start
                    inside = now
        return out

    def on_boundary(self, corners, normals, scale):
        """Which mesh corners lie on the enclosed region's boundary: nudged a hair either way
        along the normal of a triangle at the corner, inside on exactly one side (a wall with
        the part on both sides, or a sheet with it on neither, isn't surface a solid has)."""
        # (a wall thinner than 2 x eps, 0.06 mm on a 300 mm part, reads as inside on both
        # sides and is left out of mesh -> solid: harmless at today's sizes)
        eps = 1e-4 * max(scale, 1.0)
        return self.inside(corners + eps * normals) != self.inside(corners - eps * normals)


def closure(solids):
    """Sum n dA over each solid's true faces, in mm^2 (the size of the largest solid's vector):
    0 on a closed solid. Read from how OCC's own volume integral moves with its origin, V(P) =
    V(0) - P.C/3, face by face, since the integral about a chosen origin is all it offers."""
    from OCP.BRepGProp import BRepGProp_Domain, BRepGProp_Face, BRepGProp_Vinert
    worst = 0.0
    step = 100.0

    def about(face, P):
        v = BRepGProp_Vinert()
        v.SetLocation(gp_Pnt(*P))
        v.Perform(BRepGProp_Face(face), BRepGProp_Domain(face))
        return v.Mass()

    for solid in solids:
        vec = np.zeros(3)
        for f in listed(solid, TopAbs_FACE):
            f = TopoDS.Face(f)
            base = about(f, (0, 0, 0))
            for k in range(3):
                vec[k] += -3 * (about(f, tuple(step * (i == k) for i in range(3))) - base) / step
        worst = max(worst, float(np.linalg.norm(vec)))
    return worst


def _crossings_inside(ts):
    """The spans inside along a ray from its crossings with one solid, by parity: None if odd.
    The same crossing found on two faces at their shared edge counts once. So does a pair within
    TRUE_SEAM of each other when the count is odd: where edges stray, the two faces of a seam
    overlap in plan by their gap, and a ray through the overlap crosses both (on a pinecil lid,
    planes 0.09 mm apart at one seam: most of its odd rays)."""
    ts = np.sort(np.asarray(ts, float))
    if len(ts) > 1:
        ts = ts[np.concatenate([[True], np.diff(ts) > 1e-6])]
    if len(ts) % 2 and len(ts) > 1:
        gaps = np.diff(ts)
        k = int(np.argmin(gaps))
        if gaps[k] <= TRUE_SEAM:
            ts = np.delete(ts, k + 1)
    if len(ts) % 2:
        return None
    return ts.reshape(-1, 2)


def true_rays(solids, lo, h, nx, ny, R, top, most=TRUE_RAYS_MOST):
    """The length inside the solids along parallel rays, straight up in the turned axes R,
    through the true trimmed faces (IntCurvesFace: no tessellation, no integral). Grid: nx by
    ny rays from lo (x, y) at spacing h, started below top - span: every crossing counted.
    Several solids: the union on each ray, overlaps once. Returns (lengths, odd: rays with an
    odd count on any solid, to be left out) or None if the faces would take more than most tests."""
    from OCP.gp import gp_Dir, gp_Lin
    from OCP.IntCurvesFace import IntCurvesFace_Intersector
    d = R[2]
    work = []
    tests = 0
    for s, solid in enumerate(solids):
        for f in listed(solid, TopAbs_FACE):
            f = TopoDS.Face(f)
            # (the loose box, shape tolerance and all: a stray face still falls within it)
            box = Bnd_Box()
            BRepBndLib.Add_s(f, box)
            c0, c1 = np.array(box.CornerMin().Coord()), np.array(box.CornerMax().Coord())
            c = np.array([[x, y, z] for x in (c0[0], c1[0]) for y in (c0[1], c1[1]) for z in (c0[2], c1[2])]) @ R.T
            i0, i1 = np.floor((c[:, :2].min(axis=0) - lo) / h - 0.5).astype(int), np.ceil((c[:, :2].max(axis=0) - lo) / h - 0.5).astype(int)
            i0 = np.maximum(i0, 0)
            i1 = np.minimum(i1, (nx - 1, ny - 1))
            if (i1 < i0).any():
                continue
            tests += int(np.prod(i1 - i0 + 1))
            work.append((s, f, i0, i1, float(c[:, 2].min())))
    if tests > most:
        return None
    bottom = min((w[4] for w in work), default=0.0) - 1.0
    far = float(top - bottom) + 2.0
    hits = [{} for _ in solids]
    pd = gp_Dir(*d)
    for s, f, i0, i1, _ in work:
        it = IntCurvesFace_Intersector(f, 1e-7)
        for i in range(i0[0], i1[0] + 1):
            x = lo[0] + (i + 0.5) * h
            for j in range(i0[1], i1[1] + 1):
                o = (x * R[0] + (lo[1] + (j + 0.5) * h) * R[1] + bottom * R[2])
                # (OCC throws on the odd ray and face: a crossing not counted, which the odd count sees)
                try:
                    it.Perform(gp_Lin(gp_Pnt(*o), pd), 0.0, far)
                except Exception as e:  # not engine work: a measuring tool, and a bug here must end it
                    if isinstance(e, faults.CODE_ERRORS):
                        raise
                    continue
                if it.IsDone() and it.NbPnt():
                    hits[s].setdefault((i, j), []).extend(it.WParameter(k) for k in range(1, it.NbPnt() + 1))
    out = np.zeros((nx, ny))
    odd = np.zeros((nx, ny), bool)
    for key in set().union(*hits) if hits else ():
        spans = []
        for h_s in hits:
            ts = h_s.get(key)
            if ts:
                pairs = _crossings_inside(ts)
                if pairs is None:
                    odd[key] = True
                else:
                    spans.extend(pairs.tolist())
        if odd[key] or not spans:
            continue
        spans.sort()
        total, cur0, cur1 = 0.0, spans[0][0], spans[0][1]
        for a, b in spans[1:]:
            if a > cur1:
                total += cur1 - cur0
                cur0, cur1 = a, b
            else:
                cur1 = max(cur1, b)
        out[key] = total + cur1 - cur0
    return out, odd


def second_volume(solids, region, reference, rays=TRUE_RAYS):
    """The solid's volume against the mesh's, along the same rays through the solid's true
    faces and through the mesh's triangles: what each ray gets wrong by its place in a thin wall
    it gets wrong on both. Returns a dictionary for the card: error (share of the mesh's volume),
    spread (half the difference between the rays of two chequerboard halves: how sure), odd
    (share of rays with an odd count, left out), and usable (whether it may be believed: few odd
    rays, and the mesh's rays find the mesh's own volume). None if the rays can't count the mesh."""
    if region.volume is None or not region.rays():
        return None
    R = _rotation()
    box = Bnd_Box()
    for s in solids:
        BRepBndLib.Add_s(s, box)
    c0, c1 = np.array(box.CornerMin().Coord()), np.array(box.CornerMax().Coord())
    c = np.array([[x, y, z] for x in (c0[0], c1[0]) for y in (c0[1], c1[1]) for z in (c0[2], c1[2])]) @ R.T
    # (both boxes: material outside the mesh's box must be seen)
    lo = np.minimum(region.lo, c[:, :2].min(axis=0))
    hi = np.maximum(region.T[:, :, :2].max(axis=(0, 1)), c[:, :2].max(axis=0))
    top = float(max(region.T[:, :, 2].max(), c[:, 2].max()))
    for n in (rays, rays // 2, rays // 4):
        h = float((hi - lo).max()) / n
        nx, ny = (int(np.ceil((hi[k] - lo[k]) / h)) for k in (0, 1))
        got = true_rays(solids, lo, h, nx, ny, R, top)
        if got is not None:
            break
    if got is None:
        return None
    step_len, odd = got
    X, Y = np.meshgrid(lo[0] + (np.arange(nx) + 0.5) * h, lo[1] + (np.arange(ny) + 0.5) * h, indexing="ij")
    P = np.column_stack([X.ravel(), Y.ravel(), np.zeros(X.size)])
    mesh_len = region.ray_lengths(P).reshape(nx, ny)
    hit = (step_len > 0) | (mesh_len > 0) | odd
    use = ~odd
    a = float(mesh_len[use].sum())
    if a <= 0:
        return None
    error = float((step_len - mesh_len)[use].sum()) / a
    I, J = np.meshgrid(np.arange(nx), np.arange(ny), indexing="ij")
    half = ((I + J) % 2 == 0)
    a0, a1 = float(mesh_len[use & half].sum()), float(mesh_len[use & ~half].sum())
    if a0 <= 0 or a1 <= 0:
        return None
    e0 = float((step_len - mesh_len)[use & half].sum()) / a0
    e1 = float((step_len - mesh_len)[use & ~half].sum()) / a1
    odd_share = float(odd.sum()) / max(int(hit.sum()), 1)
    found = float(mesh_len.sum()) * h * h
    usable = odd_share <= TRUE_ODD_SHARE and abs(found - reference) <= TRUE_REFERENCE * reference
    return {"error": error, "spread": abs(e0 - e1) / 2, "odd": odd_share, "rays": int(hit.sum()), "spacing": h,
            "mesh": found, "usable": bool(usable)}


CLOSEST_BATCH = 5000    # query points per trimesh closest-point call (memory: candidates per point x batch)


def _closest(mesh, points):
    """trimesh's closest point, a batch of points at a time. Its candidate triangles are
    every triangle whose box holds a point's search box, so a flat face with a long outline,
    tessellated into a few huge thin triangles, hands each point hundreds of them: the P1S
    chute's walls built as planes asked for 40 million pairs at once (932 MB per array) and the
    judge was killed."""
    points = np.asarray(points, dtype=np.float64)
    if len(points) <= CLOSEST_BATCH:
        return trimesh.proximity.closest_point(mesh, points)
    parts = [trimesh.proximity.closest_point(mesh, points[i:i + CLOSEST_BATCH])
             for i in range(0, len(points), CLOSEST_BATCH)]
    return tuple(np.concatenate([p[k] for p in parts]) for k in range(3))


def judge(mesh_path, step_path, detail=True):
    t0 = time.time()
    card = {"step": step_path, "exists": os.path.exists(step_path)}
    if not card["exists"]:
        return card
    card["file_mb"] = round(os.path.getsize(step_path) / 1e6, 3)
    shape = quiet(read, step_path)
    card["read_ok"] = shape is not None and not shape.IsNull()
    if not card["read_ok"]:
        return card
    faces = [TopoDS.Face(f) for f in listed(shape, TopAbs_FACE)]
    shells = listed(shape, TopAbs_SHELL)
    solids = listed(shape, TopAbs_SOLID)
    card.update(faces=len(faces), shells=len(shells), solids=len(solids),
                open_shells=sum(not BRep_Tool.IsClosed_s(s) for s in shells))
    t = time.time()
    card["valid"] = bool(BRepCheck_Analyzer(shape).IsValid())
    card["check_s"] = round(time.time() - t, 1)

    types, triangles = {}, 0
    for f in faces:
        kind = TYPES.get(BRepAdaptor_Surface(f, False).GetType(), "other")
        types[kind] = types.get(kind, 0) + 1
        if kind == "plane":
            wires = listed(f, TopAbs_WIRE)
            if len(wires) == 1 and len(listed(wires[0], TopAbs_EDGE)) == 3:
                triangles += 1
    card["types"] = types
    card["triangles"] = triangles

    # (each solid's own: VolumeProperties starts its props afresh, so one props across a loop
    # held only the last solid's: 4.09 mm^3 of two, 4,832 of 5,050 on a multi-solid part)
    card["volume_occ"] = 0.0
    for s in solids:
        props = GProp_GProps()
        BRepGProp.VolumeProperties_s(s, props)
        card["volume_occ"] += props.Mass()

    original = load_mesh(mesh_path)
    card["mesh_triangles"] = len(original.faces)
    card["mesh_volume"] = card["mesh_volume_raw"] = abs(float(original.volume))
    t = time.time()
    region = Enclosure(original.vertices, original.faces, agree=True)
    card["enclosed_volume"] = region.volume
    # (the mesh's own volume with its winding agreed first: a patch facing the wrong way moves
    # the file's signed sum with the origin. The file's figure stays on the card)
    agreed, flipped = (None, region.flipped) if region.simple else agree_winding(original.faces, original.vertices)
    if region.simple:
        file_figure, fixed = card["mesh_volume"], region.volume
    elif flipped:
        P = np.asarray(original.vertices)
        file_figure = card["mesh_volume"]
        fixed = abs(float(np.einsum("ij,ij->i", P[agreed[:, 0]], np.cross(P[agreed[:, 1]], P[agreed[:, 2]])).sum()) / 6)
    else:
        file_figure = fixed = card["mesh_volume"]
    card["mesh_flipped"] = int(flipped)
    # (only if it moves: a mesh that agrees keeps trimesh's figure to the last digit)
    if abs(fixed - file_figure) > 1e-9 * max(file_figure, 1e-12):
        card["mesh_volume_file"] = file_figure
        card["mesh_volume"] = card["mesh_volume_raw"] = fixed
    # (the rays' own error: the same count with half as many rays a side; large where walls
    # are thin against the part's size)
    card["enclosed_error"] = region.error
    # (the rays' figure only where it differs from the exact one by more than their method
    # error: a mesh of separate clean pieces keeps its exact volume and every corner)
    tangled = bool(region.volume) and not region.simple and abs(region.volume - card["mesh_volume_raw"]) > ENCLOSED_TOL * card["mesh_volume_raw"]
    if tangled:
        card["mesh_volume"] = region.volume
    card["enclosed_s"] = round(time.time() - t, 1)
    card["deflection"] = DEFLECTION
    # (coarse_faces, unmeshed_faces: what these measurements stand on, when the mesher couldn't
    # finish a face at DEFLECTION)
    tess, owner, suspects = tessellate(shape, faces, card["deflection"], notes=card)
    card["uncovered_faces"] = len(suspects)
    # (a diagnostic only: BRepMesh can cover a face partly, and a sum over a surface
    # with a hole depends on the origin: Rak N 48,543 mm^3 here, OCC's own 49,215)
    card["volume_tess"] = abs(float(tess.volume)) if len(tess.faces) else 0.0
    # OCC's volume from the same triangulation, closed shells only: right where the exact
    # integration goes astray (solids sewn across gaps: a bracket 46,537 vs 62,669)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props, True, False, True)
    card["volume_occ_tri"] = abs(props.Mass())
    mv = card["mesh_volume"]
    if mv and solids:
        raw = card["mesh_volume_raw"]
        sums = (card["volume_occ"], card["volume_occ_tri"])
        card["volume_error_raw"] = min(((v - raw) / raw for v in sums), key=abs) if raw else None
        # (several solids: the volume they enclose together, overlaps once, against what the
        # mesh encloses; a sum would count solids pressed into each other twice)
        together = None
        measured = True
        if len(solids) > 1:
            welded = trimesh.Trimesh(tess.vertices, tess.faces, process=False)
            welded.merge_vertices(digits_vertex=7)
            together = Enclosure(welded.vertices, welded.faces).volume
            measured = together is not None
            # (as for the mesh: the rays' figure only where solids overlap by more than its error)
            if together and abs(together - card["volume_occ_tri"]) <= ENCLOSED_TOL * card["volume_occ_tri"]:
                together = None
        card["volume_solids_enclosed"] = together
        errors = [(v - mv) / mv for v in ((together,) if together else sums)]
        if tangled and not measured:
            # (the solids' tessellation leaves gaps, so the rays can't count their overlaps,
            # and their sum is right against the mesh's union only if they don't overlap:
            # against the triangles' sum too, like with like, and the nearer counts. The
            # companion enclosure's twelve solids, which copy the mesh's overlaps, read +13%
            # against its union alone; stl2curves' three on obj_1_Assembly, which don't,
            # -1.3% against the triangles' sum alone. OCC's Boolean fuse is no way round it:
            # on the companion 24,907, 0 or 15,031 mm^3 by its fuzzy value)
            errors += [(v - raw) / raw for v in sums]
        card["volume_errors"] = errors
        # the nearer of the two: each goes astray on its own, a missing body moves both
        card["volume_error"] = min(errors, key=abs)
        # a second look only if the first figures would fail a closed, valid solid and its faces
        # don't close: there both figures go astray (the integral moves with its origin, and the
        # tessellation's edges sit up to their tolerance off the surface)
        if abs(card["volume_error"]) > GOOD_VOLUME and card["valid"] and not card["open_shells"]:
            t = time.time()
            box = Bnd_Box()
            BRepBndLib.Add_s(shape, box)
            reach = 0.5 * float(np.linalg.norm(np.array(box.CornerMax().Coord()) - np.array(box.CornerMin().Coord())))
            shut = closure(solids)
            card["closure_mm2"] = shut
            card["closure_share"] = shut * reach / 3 / mv
            if card["closure_share"] > CLOSURE_SHARE:
                rays = second_volume(solids, region, mv)
                card["second_volume"] = rays
                # (if it can be believed it replaces the first figures, since they are what's
                # astray. It passes only if it is sure, its spread included: the rays measure the
                # true faces, so a solid truly off still fails on them)
                if rays and rays["usable"]:
                    card["volume_error_first"] = card["volume_error"]
                    sure = abs(rays["error"]) + rays["spread"]
                    card["volume_error"] = rays["error"] if sure <= GOOD_VOLUME else float(np.copysign(sure, rays["error"]))
            card["second_volume_s"] = round(time.time() - t, 1)
    else:
        card["volume_error"] = card["volume_error_raw"] = None

    if len(tess.faces):
        n = SAMPLES if len(original.faces) < BIG else SAMPLES // 4
        rng = np.random.default_rng(0)
        corners = original.vertices
        pick = rng.choice(len(corners), n, replace=False) if len(corners) > n else np.arange(len(corners))
        corners = corners[pick]
        _, d1, tri1 = _closest(tess, corners)
        d1, re1 = exact_to_faces(corners, d1, owner[tri1], faces, suspects)
        card["internal_share"] = 0.0
        # (tiny voids, cavities under VOID_SHARE of their body, are left out: a solid may drop
        # them, as both engines do where they touch other surfaces. 341board: twelve turns of
        # thread running on past six blind holes' floors, 0.02% of its volume but 6.8% of its
        # corners, failed the part though the rest lies within 0.12 mm)
        void = region.tiny_voids[pick]
        card["tiny_voids"] = region.tiny_void_count
        card["void_share"] = round(float(void.mean()), 4)
        if tangled or void.any():
            card["mesh_to_solid_raw"] = stats(d1)
        if void.any() and not void.all():
            corners, d1, pick = corners[~void], d1[~void], pick[~void]
        if tangled:
            # (only corners on the enclosed region's boundary: walls inside the part aren't
            # surface a correct solid has)
            # (a triangle's normal at each corner, either way up: the boundary test tries both
            # sides; found only here, trimesh's corner-to-triangle table is slow on big meshes)
            F = np.asarray(original.faces)
            first = np.full(len(original.vertices), -1)
            first[F[::-1].ravel()] = np.repeat(np.arange(len(F))[::-1], 3)
            normals = original.face_normals[np.maximum(first[pick], 0)]
            keep = region.on_boundary(corners, normals, float(np.ptp(original.vertices, axis=0).max()))
            card["internal_share"] = round(1 - float(keep.mean()), 4)
            if keep.any():
                d1 = d1[keep]
        elif region.volume and not region.simple and touches_itself(original):
            # (a hairline crack's walls have the part on both sides, a little way off, and so
            # aren't surface a correct solid has either: on the drybox axle 6% of the corners,
            # where the thread and the tube it was pressed on lie 0.01 mm apart. Too few count
            # for nothing, which leaves ordinary thin slots alone. See cracks.py)
            F = np.asarray(original.faces)
            first = np.full(len(original.vertices), -1)
            first[F[::-1].ravel()] = np.repeat(np.arange(len(F))[::-1], 3)
            normals = original.face_normals[np.maximum(first[pick], 0)]
            walls = region.inside(corners + CRACK * normals) & region.inside(corners - CRACK * normals)
            card["crack_share"] = round(float(walls.mean()), 4)
            if CRACK_SHARE <= walls.mean() < 1:
                card["mesh_to_solid_raw"] = stats(d1)
                d1 = d1[~walls]
                # mesh -> solid by area: points spread over the mesh's triangles by area, as solid ->
        # mesh is, with each triangle's chordal sag allowed for (a point inside a curved
        # facet lies off the true surface by up to that). Corners alone miss an error inside
        # a big triangle (mmm thinmix mix_b016: flat triangles 0.5-1.4 mm off the solid,
        # corners clean) and count a crumpled patch of tiny triangles many times over (Solar
        # Arm Mount: 360 triangles, 15% of its corners): found by Duvdivan's Claude, 10-31.
        sp, stri = trimesh.sample.sample_surface(original, n, seed=0)
        sp, stri = np.asarray(sp), np.asarray(stri)
        _, d3, tri3 = _closest(tess, sp)
        d3, _ = exact_to_faces(sp, d3, owner[tri3], faces, suspects)
        d3 = np.maximum(0.0, d3 - chordal_sag(original)[stri])
        corners_of = np.asarray(original.faces)[stri]
        svoid = region.tiny_voids[corners_of].all(axis=1)
        if svoid.any() and not svoid.all():
            sp, d3, stri = sp[~svoid], d3[~svoid], stri[~svoid]
        # (only points on the enclosed region's boundary, on every mesh the rays counted, not
        # only tangled ones: a big triangle of a wall inside the part has its corners on the solid's
        # surface but its middle in the material, so area points there read far off. P1S
        # riser bodies: 9-11% of the area points 3-8 mm off, 99.8% of them inside the solid,
        # corners clean; the mesh isn't "tangled" because its volume still adds up)
        # (a simple mesh, every edge on two triangles and no pieces overlapping, has no such
        # walls; one the rays couldn't count, with open pieces holding volume, can't be told)
        card["internal_area_share"] = 0.0
        if region.volume is not None and not region.simple:
            skeep = region.on_boundary(sp, original.face_normals[stri], float(np.ptp(original.vertices, axis=0).max()))
            card["internal_area_share"] = round(1 - float(skeep.mean()), 4)
            if skeep.any():
                d3 = d3[skeep]
        if region.volume and not region.simple and "crack_share" in card and CRACK_SHARE <= card["crack_share"] < 1:
            # (a crack's walls by area too: they are 41% of the drybox axle's mesh)
            sn = original.face_normals[stri]
            swall = region.inside(sp + CRACK * sn) & region.inside(sp - CRACK * sn)
            d3 = d3[~swall] if swall.size == d3.size and not swall.all() else d3
        pts, tri2 = trimesh.sample.sample_surface(tess, n, seed=0)
        pts = on_surfaces(np.asarray(pts), owner[tri2], faces)
        _, d2, near2 = _closest(original, pts)
        card["mesh_to_solid"] = stats(d1)
        card["mesh_to_solid_area"] = stats(d3)
        card["solid_to_mesh"] = stats(d2)
        card["solid_to_mesh_sag"] = stats(np.maximum(0.0, d2 - chordal_sag(original)[near2]))
        card["rechecked"] = [int(re1)]
        # (design detail lost: reported only, verdict() doesn't read it; never the reason a card fails)
        if detail:
            try:
                try:
                    from .lost_detail import measure
                except ImportError:         # (run as a script: compare/bench/judge.py)
                    from mms2c.lost_detail import measure
                size = float(np.ptp(original.vertices, axis=0).max())
                walls = None if region.volume is None or region.simple else (lambda p, n: region.on_boundary(p, n, size))
                card["lost_detail"] = measure(original, tess, owner, faces, _closest, walls)
            except Exception as e:
                faults.caught(e)
                card["lost_detail"] = {"error": f"{type(e).__name__}: {e}"}
    card["judge_s"] = round(time.time() - t0, 1)
    return card


CLOSE_MM = 0.1       # mm, and the share of points both ways that must lie within it:
CLOSE = 0.99
SCORE_MM = 0.02      # mm of p99 distance per unit of score (closeness decides ties by a
                     # clear margin, pipeline.CLEARLY_CLOSER units: 0.02 mm)


def load_mesh(path):
    """The mesh as a trimesh. A 3MF project is read with the engine's own reader (every
    object its build prints, placed and scaled as printed): trimesh's 3MF reader needs
    networkx, which the pinned environment doesn't have, so every 3MF part got an error
    scorecard, graded bad (found by Duvdivan's Claude on Bravo, 2026-10-30)."""
    if str(path).lower().endswith(".3mf"):
        try:
            from .read3mf import read_3mf
        except ImportError:                 # (run as a script: compare/bench/judge.py)
            from mms2c.read3mf import read_3mf
        objects = read_3mf(path)
        if not objects:
            raise ValueError(f"{path}: no printed object in the 3MF file")
        pts, tris, start = [], [], 0
        for _, P, T, _ in objects:
            pts.append(P)
            tris.append(T + start)
            start += len(P)
        return trimesh.Trimesh(np.vstack(pts), np.vstack(tris))
    return trimesh.load(path, force="mesh")


def verdict(card):
    """(good, close, sound, score) for a scorecard: as compare/bench/summary.py grades."""
    vol = card.get("volume_error")
    sound = bool(card.get("read_ok") and card.get("valid") and card.get("solids") and not card.get("open_shells")
                 and vol is not None and abs(vol) <= GOOD_VOLUME)
    ways = [card.get("mesh_to_solid") or {}, card.get("solid_to_mesh_sag") or card.get("solid_to_mesh") or {}]
    close = all(w.get(f"within_{CLOSE_MM:g}", 0.0) >= CLOSE for w in ways)
    p99 = max((w.get("p99") if w.get("p99") is not None else 1e9) for w in ways)
    return sound and close, close, sound, -p99 / SCORE_MM


def main(argv):
    mesh_path, step_path, out = argv[:3]
    try:
        card = judge(mesh_path, step_path, "--no-detail" not in argv[3:])
    except Exception as e:  # not engine work: a scorecard that says why, rather than none
        card = {"step": step_path, "error": f"{type(e).__name__}: {e}"}
    with open(out, "w") as f:
        json.dump(card, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
