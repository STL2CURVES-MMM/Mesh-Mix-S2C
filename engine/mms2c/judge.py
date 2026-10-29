"""The benchmark's judge: one scorecard for a STEP made from a mesh, the same for every
program. Reads the file back as a CAD program would and reports:

- solids, shells (and how many are open), faces, BRepCheck validity
- volume (OCC's integration and a tessellation's) against the mesh's
- faces by surface type, and flat triangles (planar faces with three edges: facets
  left as they were)
- distance mesh -> solid (sampled mesh corners) and solid -> mesh (points sampled on
  the faces), both measured exactly where the tessellation reads high: OCC tessellates
  big B-spline faces coarsely whatever deflection is asked (0.07 mm off at 0.002), so
  the tessellation alone would mark down spline-heavy results.
- solid -> mesh again with each mesh triangle's chordal sag allowed for (chordal_sag): a
  true curve bulges past a coarse mesh's flat facets, by 0.134 mm on MMM's
  cad_plate_holes_coarse (16-gon holes), so the right answer, true cylinders, failed the
  0.1 mm test where a copy of the facets passed (found by MMM's author's Claude, 10-25).
  Mesh corners lie on the true surface, so mesh -> solid needs no allowance.
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

A result is good (verdict()) when it reads back as valid closed solids with no open
shell, its volume within GOOD_VOLUME of the mesh's, and CLOSE of the points both ways
within CLOSE_MM of the other surface: what a CAD program can use as is. The merged engine
judges its rivals with it (rivals.py), and the benchmark grades every program by it.
(A copy of compare/bench/judge.py, the benchmark's own, plus verdict(): keep them the same.)

usage: python -m mms2c.judge MESH STEP OUT.json      (trimesh + cadquery-ocp)"""
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
from OCP.BRepMesh import BRepMesh_IncrementalMesh
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
RAYS = 512           # rays a side across the part for the enclosed volume (Enclosure)
CELLS = 128          # cells a side of the xy grid the rays look up triangles in
VOID_SHARE = 1e-3    # share of its body's volume under which a cavity is a tiny void (as small as
                     # the engines drop: mmm/brep.make_solid_checked max_void_fraction)
ENCLOSED_TOL = 0.003 # share of the volume by which the rays' figure may differ from the exact one
                     # (their method error) before the mesh counts as enclosing less than its triangles
SAMPLES = 20000      # points each way (5,000 on meshes over BIG triangles)
BIG = 300_000
RECHECK = 0.01       # mm: tessellation distances above this are measured exactly
RECHECK_MOST = 4000  # at most this many points each way, the farthest first
RETRIES = ((0.01, 0.2), (0.05, 0.5), (0.2, 0.8))   # coarser tessellations for faces BRepMesh gave up on
COVERED = 0.98       # a face whose triangles cover less of its area than this gets exact checks

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


def tessellate(shape, faces):
    """Triangles of every face (outward, by the face's orientation), the face each came
    from, and the faces the tessellation doesn't cover (none, or under COVERED of the
    face's area): the distances look past those, so they get exact checks of their own."""
    quiet(BRepMesh_IncrementalMesh, shape, DEFLECTION, False, 0.1, True)
    V, F, owner, base, area = [], [], [], 0, np.zeros(len(faces))
    for k, face in enumerate(faces):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        # BRepMesh gives up on some faces at a fine deflection; retry coarser
        for lin, ang in RETRIES:
            if tri is not None:
                break
            quiet(BRepMesh_IncrementalMesh, face, lin, False, ang, False)
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
    closed. tiny_voids: corners of cavities holding under VOID_SHARE of their body."""

    def __init__(self, V, F):
        V = np.asarray(V, float)
        F = np.asarray(F, np.int64)
        F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 2] != F[:, 0])]
        self.volume = self.error = None
        self.simple = False
        self.tiny_voids = np.zeros(len(V), bool)
        self.tiny_void_count = 0
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
                return
        Vr = V @ _rotation().T
        Fk = F[keep]
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
        if not self.ambiguous:
            self.volume = self._volume(hi, RAYS)
            self.error = abs(self._volume(hi, RAYS // 2) - self.volume)

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
        total = 0.0
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
                        total += zz - start
                    inside = now
        return total * h * h

    def on_boundary(self, corners, normals, scale):
        """Which mesh corners lie on the enclosed region's boundary: nudged a hair either way
        along the normal of a triangle at the corner, inside on exactly one side (a wall with
        the part on both sides, or a sheet with it on neither, isn't surface a solid has)."""
        # (a wall thinner than 2 x eps, 0.06 mm on a 300 mm part, reads as inside on both
        # sides and is left out of mesh -> solid: harmless at today's sizes)
        eps = 1e-4 * max(scale, 1.0)
        return self.inside(corners + eps * normals) != self.inside(corners - eps * normals)


def judge(mesh_path, step_path):
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

    props = GProp_GProps()
    for s in solids:
        BRepGProp.VolumeProperties_s(s, props)
    card["volume_occ"] = props.Mass() if solids else 0.0

    original = trimesh.load(mesh_path, force="mesh")
    card["mesh_triangles"] = len(original.faces)
    card["mesh_volume"] = card["mesh_volume_raw"] = abs(float(original.volume))
    t = time.time()
    region = Enclosure(original.vertices, original.faces)
    card["enclosed_volume"] = region.volume
    # (the rays' own error: the same count with half as many rays a side; large where walls
    # are thin against the part's size)
    card["enclosed_error"] = region.error
    # (the rays' figure only where it differs from the exact one by more than their method
    # error: a mesh of separate clean pieces keeps its exact volume and every corner)
    tangled = bool(region.volume) and not region.simple and abs(region.volume - card["mesh_volume_raw"]) > ENCLOSED_TOL * card["mesh_volume_raw"]
    if tangled:
        card["mesh_volume"] = region.volume
    card["enclosed_s"] = round(time.time() - t, 1)
    tess, owner, suspects = tessellate(shape, faces)
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
    else:
        card["volume_error"] = card["volume_error_raw"] = None

    if len(tess.faces):
        n = SAMPLES if len(original.faces) < BIG else SAMPLES // 4
        rng = np.random.default_rng(0)
        corners = original.vertices
        pick = rng.choice(len(corners), n, replace=False) if len(corners) > n else np.arange(len(corners))
        corners = corners[pick]
        _, d1, tri1 = trimesh.proximity.closest_point(tess, corners)
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
        pts, tri2 = trimesh.sample.sample_surface(tess, n, seed=0)
        pts = on_surfaces(np.asarray(pts), owner[tri2], faces)
        _, d2, near2 = trimesh.proximity.closest_point(original, pts)
        card["mesh_to_solid"] = stats(d1)
        card["solid_to_mesh"] = stats(d2)
        card["solid_to_mesh_sag"] = stats(np.maximum(0.0, d2 - chordal_sag(original)[near2]))
        card["rechecked"] = [int(re1)]
    card["judge_s"] = round(time.time() - t0, 1)
    return card


GOOD_VOLUME = 0.01   # share of the mesh's volume the solid may differ by
CLOSE_MM = 0.1       # mm, and the share of points both ways that must lie within it:
CLOSE = 0.99
SCORE_MM = 0.02      # mm of p99 distance per unit of score (closeness decides ties by a
                     # clear margin, pipeline.CLEARLY_CLOSER units: 0.02 mm)


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
        card = judge(mesh_path, step_path)
    except Exception as e:  # a scorecard that says why, rather than none
        card = {"step": step_path, "error": f"{type(e).__name__}: {e}"}
    with open(out, "w") as f:
        json.dump(card, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
