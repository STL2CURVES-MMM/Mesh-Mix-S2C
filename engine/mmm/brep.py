"""Assemble OpenCASCADE B-rep shapes from a segmented mesh.

Regions -> faces, chains -> edges, corners -> vertices. Edges are shared
between neighbouring faces, so the result is a watertight shell without any
sewing step.
"""
import numpy as np

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
from OCP.TopoDS import TopoDS_Compound, TopoDS_Shell, TopoDS_Solid, TopoDS_Wire, TopoDS_Vertex

from .report import NULL

MIN_TOL = 1e-7


def _pnt(p):
    return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))


def plane_basis(n):
    n = n / np.linalg.norm(n)
    a = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(n, a)
    u /= np.linalg.norm(u)
    v = np.cross(n, u)
    return u, v


def signed_area_2d(P2):
    x, y = P2[:, 0], P2[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


class PlanarSurface:
    kind = "plane"

    def __init__(self, origin, normal):
        self.origin = np.asarray(origin, dtype=np.float64)
        n = np.asarray(normal, dtype=np.float64)
        self.normal = n / np.linalg.norm(n)

    def distance(self, P):
        return np.abs((np.asarray(P) - self.origin) @ self.normal)


class BRepAssembler:
    """Builds faces for every region of a RegionGraph.

    surfaces[r] must provide ``kind`` ('plane' or anything the subclass
    supports). The base implementation handles planes with straight line
    edges, which is all the exact/faceted preset needs.
    """

    def __init__(self, V, rg, surfaces, tol, report=NULL):
        self.V = V
        self.rg = rg
        self.surfaces = surfaces
        self.tol = float(tol)
        self.report = report
        self.B = BRep_Builder()
        self._verts = {}
        self.vtol = np.zeros(len(V))
        self.failed_regions = []

    # ------------------------------------------------------------------
    def compute_vertex_tolerances(self):
        rg = self.rg
        for r, loops in enumerate(rg.region_loops):
            s = self.surfaces[r]
            for loop in loops:
                vs = rg.loop_vertices(loop)
                if not vs:
                    continue
                d = s.distance(self.V[vs])
                np.maximum.at(self.vtol, vs, d)

    def vertex(self, vi):
        v = self._verts.get(vi)
        if v is None:
            v = TopoDS_Vertex()
            tol = max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9)
            self.B.MakeVertex(v, _pnt(self.V[vi]), tol)
            self._verts[vi] = v
        return v

    # ------------------------------------------------------------------
    def split_thin_loops(self, factor=6.0):
        """A loop made of one or two chains that all run within a few
        tolerances of one straight line (a needle-thin sliver) would get two
        identical straight edges: a zero-area face. Give such loops extra
        corners at the points farthest from that line so that every edge
        stays distinct."""
        rg = self.rg
        lim = factor * self.tol
        for loops in rg.region_loops:
            for loop in loops:
                for _ in range(2):
                    if len(loop) > 2:
                        break
                    vs = rg.loop_vertices(loop)
                    if len(vs) < 3:
                        break
                    P = self.V[vs]
                    D = np.linalg.norm(P[:, None] - P[None], axis=2) if len(P) <= 400 else None
                    if D is None:
                        break
                    i, j = np.unravel_index(int(np.argmax(D)), D.shape)
                    d = P[j] - P[i]
                    L = np.linalg.norm(d)
                    if L <= MIN_TOL:
                        break
                    d /= L
                    best = None
                    for cid, _ in loop:
                        c = rg.chains[cid]
                        if c.left == c.right or len(c.verts) < 3:
                            continue
                        Q = self.V[c.verts[1:-1]] - P[i]
                        off = np.linalg.norm(Q - np.outer(Q @ d, d), axis=1)
                        # far from both chain ends, so the split is not a sliver itself
                        ends = np.minimum(
                            np.linalg.norm(self.V[c.verts[1:-1]] - self.V[c.verts[0]], axis=1),
                            np.linalg.norm(self.V[c.verts[1:-1]] - self.V[c.verts[-1]], axis=1),
                        )
                        score = off + 1e-3 * ends
                        k = int(np.argmax(score))
                        if best is None or score[k] > best[0]:
                            best = (score[k], cid, k + 1, off.max())
                    if best is None:
                        break
                    Qall = P - P[i]
                    if np.linalg.norm(Qall - np.outer(Qall @ d, d), axis=1).max() > lim:
                        break
                    rg.split_chain(best[1], best[2])

    def chain_is_straight(self, chain):
        P = self.V[chain.verts]
        if chain.closed:
            return False
        a, b = P[0], P[-1]
        d = b - a
        L = np.linalg.norm(d)
        if L <= MIN_TOL:
            return False
        if len(P) == 2:
            return True
        d /= L
        Q = P[1:-1] - a
        t = Q @ d
        off = np.linalg.norm(Q - np.outer(t, d), axis=1)
        return off.max() <= self.tol and (t > 0).all() and (t < L).all()

    def build_chain_edges(self, chain):
        """Default: one line if straight, otherwise a polyline."""
        if self.chain_is_straight(chain):
            chain.edges = [self._line_edge(chain.verts[0], chain.verts[-1])]
        else:
            chain.edges = self._polyline_edges(chain.verts)

    def _line_edge(self, i, j):
        mk = BRepBuilderAPI_MakeEdge(self.vertex(i), self.vertex(j))
        if not mk.IsDone():
            raise RuntimeError(f"edge {i}-{j} failed")
        e = mk.Edge()
        t = max(self.vtol[i], self.vtol[j])
        if t > MIN_TOL:
            self.B.UpdateEdge(e, 1.5 * t + 1e-9)
        return e

    def _polyline_edges(self, verts):
        out = []
        for a, b in zip(verts[:-1], verts[1:]):
            if a == b:
                continue
            out.append(self._line_edge(a, b))
        return out

    # ------------------------------------------------------------------
    def wire_for_loop(self, loop):
        w = TopoDS_Wire()
        self.B.MakeWire(w)
        for cid, fwd in loop:
            edges = self.rg.chains[cid].edges
            if fwd:
                for e in edges:
                    self.B.Add(w, e)
            else:
                for e in reversed(edges):
                    self.B.Add(w, e.Reversed())
        w.Closed(True)
        return w

    def closed_region_ok(self, r):
        """Whether a region without boundary loops can still become a face."""
        return False

    def build_face(self, r):
        s = self.surfaces[r]
        loops = self.rg.region_loops[r]
        if s.kind == "plane":
            return self._planar_face(r, s, loops)
        raise NotImplementedError(s.kind)

    def _planar_face(self, r, s, loops):
        u, v = plane_basis(s.normal)
        areas = []
        for loop in loops:
            P = self.V[self.rg.loop_vertices(loop)]
            Q = P - s.origin
            areas.append(signed_area_2d(np.stack([Q @ u, Q @ v], axis=1)))
        outer = int(np.argmax(areas))
        pln = gp_Pln(_pnt(s.origin), gp_Dir(*map(float, s.normal)))
        mf = BRepBuilderAPI_MakeFace(pln, self.wire_for_loop(loops[outer]), True)
        for k, loop in enumerate(loops):
            if k != outer:
                mf.Add(self.wire_for_loop(loop))
        if not mf.IsDone():
            raise RuntimeError(f"planar face {r} failed ({mf.Error()})")
        return mf.Face()

    # ------------------------------------------------------------------
    def build(self, progress=None):
        rg = self.rg
        self.compute_vertex_tolerances()
        nch = len(rg.chains)
        for k, c in enumerate(rg.chains):
            self.build_chain_edges(c)
            if progress and k % 2000 == 0:
                progress(0.3 * k / max(1, nch))
        faces = []
        self.region_faces = {}
        nr = rg.n_regions
        for r in range(nr):
            if not rg.region_loops[r] and not self.closed_region_ok(r):
                continue
            try:
                f = self.build_face(r)
                faces.append(f)
                self.region_faces[r] = f
            except Exception as e:  # keep going; caller decides what to do
                self.failed_regions.append((r, str(e)))
            if progress and r % 2000 == 0:
                progress(0.3 + 0.7 * r / max(1, nr))
        return faces

    def shell_from_faces(self, faces):
        sh = TopoDS_Shell()
        self.B.MakeShell(sh)
        for f in faces:
            self.B.Add(sh, f)
        return sh


def make_solid_checked(B, shells, report=None, max_void_fraction=1e-3):
    """Solid from an outer shell + cavity shells. Tiny voids that touch the
    outer surface or each other (sculpting artefacts) make the solid
    invalid; they are dropped when they hold less than
    ``max_void_fraction`` of the volume."""
    from OCP.BRepCheck import BRepCheck_Analyzer

    solid = make_solid(B, shells)
    if len(shells) < 2 or BRepCheck_Analyzer(solid).IsValid():
        return solid
    from . import stepio

    outer = shells[0]
    vol_outer = abs(stepio.volume(make_solid(B, [outer])))
    cav = [(abs(stepio.volume(make_solid(B, [sh]))), sh) for sh in shells[1:]]
    cav.sort(key=lambda t: -t[0])  # big voids first: they matter most
    keep = [outer]
    dropped = 0
    for v, sh in cav:
        trial = make_solid(B, keep + [sh])
        if BRepCheck_Analyzer(trial).IsValid() or v > max_void_fraction * vol_outer:
            keep.append(sh)
        else:
            dropped += 1
    if dropped and report is not None:
        report.log(f"Removed {dropped} tiny internal void(s) that touched other surfaces")
    return make_solid(B, keep)


def make_solid(B, shells):
    so = TopoDS_Solid()
    B.MakeSolid(so)
    for sh in shells:
        sh.Closed(True)
        B.Add(so, sh)
    return so


def make_compound(shapes):
    B = BRep_Builder()
    c = TopoDS_Compound()
    B.MakeCompound(c)
    for s in shapes:
        B.Add(c, s)
    return c
