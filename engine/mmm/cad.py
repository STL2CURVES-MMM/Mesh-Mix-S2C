"""CAD preset: recognise analytic surfaces and build an editable B-rep."""
import numpy as np

from OCP.BRep import BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
from OCP.BRepLib import BRepLib
from OCP.Geom import (
    Geom_ConicalSurface,
    Geom_CylindricalSurface,
    Geom_SphericalSurface,
    Geom_ToroidalSurface,
)
from OCP.GeomAbs import GeomAbs_C2
from OCP.GeomAPI import GeomAPI_PointsToBSpline
from OCP.ShapeFix import ShapeFix_Edge, ShapeFix_Wire
from OCP.TopAbs import TopAbs_EDGE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Edge, TopoDS_Face, TopoDS_Vertex, TopoDS_Wire
from OCP.Geom2d import Geom2d_Line
from OCP.gp import gp_Ax2, gp_Ax3, gp_Circ, gp_Dir, gp_Dir2d, gp_Pnt2d
from OCP.collections import Array1_gp_Pnt

from .brep import MIN_TOL, BRepAssembler, _pnt, make_compound
from .fitting import CURVED, Cone, Cylinder, Plane, Sphere, perp_basis, unit
from .meshio import face_normals_areas
from .planar import planar_regions
from .report import NULL
from .topology import RegionGraph


# ----------------------------------------------------------------------------
# Segmentation
# ----------------------------------------------------------------------------


class Region:
    __slots__ = ("prim", "faces", "sense", "weak", "facet")

    def __init__(self, prim, faces, sense, weak=False):
        self.prim = prim
        self.faces = faces
        self.sense = sense
        self.weak = weak
        self.facet = False

    @property
    def kind(self):
        return self.prim.kind


class Segmenter:
    def __init__(self, V, F, topo, tol, report=NULL, normal_deg=25.0, smooth_deg=50.0, extent_deg=50.0):
        self.V = V
        self.F = F
        self.topo = topo
        self.tol = tol
        self.report = report
        self.fn, self.fa = face_normals_areas(V, F)
        nf = len(F)
        t = topo.twin.reshape(nf, 3)
        self.nb = np.where(t >= 0, t // 3, -1)
        self.ext = float(np.linalg.norm(V.max(0) - V.min(0)))
        self.cos_n = np.cos(np.radians(normal_deg))
        self.cos_smooth = np.cos(np.radians(smooth_deg))
        self.cos_extent = np.cos(np.radians(extent_deg))
        self.tiny = self.fa < 1e-10 * self.ext ** 2
        # needle triangles: thinner than the tolerance, so their normal says
        # little about the surface they belong to
        T = V[F]
        lmax = np.linalg.norm(T - np.roll(T, -1, axis=1), axis=2).max(1)
        self.needle = 2.0 * self.fa < 0.5 * tol * np.maximum(lmax, 1e-30)
        self.deflection, defl_hi = self._estimate_deflection()
        # surfaces must also follow the mesh between its vertices, within the
        # mesh's own tessellation coarseness
        self.tol_mid = max(3.0 * tol, 5.0 * self.deflection, 2.0 * defl_hi)
        # A scan's noise tilts thin triangles: one 0.03 mm high on a mesh with
        # 0.01 mm of noise points anywhere within about 40 degrees, so judged
        # by its direction it fits no surface, and a scanned flat face fell
        # apart into thousands of pieces (a plate with five holes: 38,830
        # faces). Measured, noise tilts a triangle of height h by about
        # atan(3.4 noise / h); where that exceeds the angle surfaces are
        # matched by, only the triangle's position counts. On such a mesh
        # flat faces are also fitted again as they grow (a small seed's own
        # plane is tilted by the noise). Clean meshes have no noise: none of
        # this applies to them.
        from .fidelity import mesh_noise

        self.noise = mesh_noise(V, F)
        h = 2.0 * self.fa / np.maximum(lmax, 1e-30)
        noisy = h < 3.4 * self.noise / np.tan(np.radians(normal_deg))
        self.scan = self.noise > 0.5 * tol or self.fa[noisy].sum() > 0.005 * max(self.fa.sum(), 1e-30)
        # triangles whose direction says nothing: tiny ones, and on a scan
        # the ones thin enough for the noise to tilt
        self.unsure = self.tiny | noisy if self.scan else self.tiny
        self.refit_planes = self.scan
        self.label = -np.ones(nf, dtype=np.int64)
        self._stamp = np.zeros(nf, dtype=np.int64)
        self._stamp_id = 0
        self.regions = []

    # --- helpers --------------------------------------------------------
    def _estimate_deflection(self):
        """The chordal deflection the mesh was tessellated with (see
        fidelity.chord_deflection): (typical, high)."""
        from .fidelity import chord_deflection

        twin = self.topo.twin
        h = np.nonzero(twin >= 0)[0]
        t = twin[h]
        m = h < t
        h, t = h[m], t[m]
        L = np.linalg.norm(self.V[self.topo.he_to[h]] - self.V[self.topo.he_from[h]], axis=1)
        return chord_deflection(self.fn, self.fa, h // 3, t // 3, L, tiny=self.tiny)

    def _new_stamp(self):
        self._stamp_id += 1
        return self._stamp_id

    def face_error(self, prim, sense, faces):
        """(max vertex distance, cosine to primitive normal) for faces.

        A triangle whose corners lie on the surface can still be a poor match
        if it spans a large piece of it (e.g. a chamfer whose corners happen
        to lie on a sphere). Such triangles get cosine -1 (rejected): the
        surface normals at its corners must agree within ``extent_deg``.
        """
        P = self.V[self.F[faces]]
        flat = P.reshape(-1, 3)
        d = prim.distance(flat).reshape(-1, 3).max(1)
        nrm = prim.normal(P.mean(1)) * sense
        cs = (self.fn[faces] * nrm).sum(1)
        if prim.kind != "plane":
            M = np.concatenate([(P[:, 0] + P[:, 1]) / 2, (P[:, 1] + P[:, 2]) / 2, (P[:, 2] + P[:, 0]) / 2])
            dm = prim.distance(M).reshape(3, -1).max(0)
            # a mid-edge miss beyond the tessellation coarseness disqualifies
            d = np.where(dm <= self.tol_mid, d, np.inf)
        if prim.kind != "plane":
            nv = prim.normal(flat).reshape(-1, 3, 3)
            c01 = (nv[:, 0] * nv[:, 1]).sum(1)
            c12 = (nv[:, 1] * nv[:, 2]).sum(1)
            c20 = (nv[:, 2] * nv[:, 0]).sum(1)
            span_ok = np.minimum(np.minimum(c01, c12), c20) >= self.cos_extent
            cs = np.where(span_ok, cs, -1.0)
        return d, cs

    def midpoint_dev(self, prim, faces):
        """Area weighted mean distance of triangle edge midpoints and
        centroids: how well the surface matches the mesh *between* its
        vertices (a torus can hit both ends of a long straight strip)."""
        T = self.V[self.F[faces]]
        M = np.concatenate([(T[:, 0] + T[:, 1]) / 2, (T[:, 1] + T[:, 2]) / 2, (T[:, 2] + T[:, 0]) / 2, T.mean(1)])
        d = prim.distance(M).reshape(4, -1).max(0)
        w = self.fa[faces]
        return float((d * w).sum() / max(w.sum(), 1e-30))

    def accept(self, prim, sense, faces, tol=None):
        tol = self.tol if tol is None else tol
        d, cs = self.face_error(prim, sense, faces)
        return (d <= tol) & ((cs >= self.cos_n) | self.unsure[faces])

    def region_points(self, faces, with_weights=False):
        """Vertices of faces with normals averaged over these faces only.

        With ``with_weights`` also returns per-vertex weights: the mesh area
        each vertex represents (a third of its incident face areas)."""
        Fs = self.F[faces]
        vid, inv = np.unique(Fs.reshape(-1), return_inverse=True)
        inv = inv.reshape(-1, 3)
        N = np.zeros((len(vid), 3))
        w = (self.fn[faces] * self.fa[faces, None])
        for k in range(3):
            np.add.at(N, inv[:, k], w)
        ln = np.linalg.norm(N, axis=1)
        ok = ln > 0
        N[ok] /= ln[ok, None]
        if not with_weights:
            return self.V[vid], N, ok
        W = np.zeros(len(vid))
        a3 = self.fa[faces] / 3.0
        for k in range(3):
            np.add.at(W, inv[:, k], a3)
        W = np.maximum(W, 1e-12 * max(W.max(), 1e-30))
        return self.V[vid], N, ok, W

    def sense_of(self, prim, faces):
        P = self.V[self.F[faces]].mean(1)
        s = (prim.normal(P) * self.fn[faces]).sum(1) @ self.fa[faces]
        return 1.0 if s >= 0 else -1.0

    def grow(self, prim, sense, seed, refit=True):
        stamp = self._new_stamp()
        st = self._stamp
        st[seed] = stamp
        region = [seed]
        frontier = seed
        total = len(seed)
        refit_at = max(2 * total, 24)
        label = self.label
        while frontier.size:
            cand = self.nb[frontier].reshape(-1)
            cand = cand[cand >= 0]
            if not cand.size:
                break
            cand = np.unique(cand)
            cand = cand[(label[cand] < 0) & (st[cand] != stamp)]
            if not cand.size:
                break
            ok = self.accept(prim, sense, cand)
            new = cand[ok]
            st[new] = stamp
            if new.size:
                region.append(new)
            frontier = new
            total += new.size
            if refit and (prim.kind != "plane" or self.refit_planes) and total >= refit_at:
                allf = np.concatenate(region)
                P, N, _, W = self.region_points(allf, True)
                p2 = prim.refit(P, N, W)
                if p2 is not None and not getattr(p2, "degenerate", lambda e: False)(self.ext):
                    okall = self.accept(p2, sense, allf)
                    if okall.mean() > 0.995:
                        prim = p2
                refit_at = 2 * total
        return np.concatenate(region), prim

    def neighbourhood(self, seed, min_verts=30, max_faces=1200, max_layers=8, min_layers=2, min_spread_deg=12.0):
        """Smoothly connected unassigned faces around a seed.

        Grows ring by ring until the patch is big enough to pin down a curved
        surface: at least ``min_layers`` rings, ``min_verts`` vertices and a
        normal spread of ``min_spread_deg`` (two rings of a torus or three
        strips of a cylinder are not enough to tell surface types apart).
        """
        stamp = self._new_stamp()
        st = self._stamp
        st[seed] = stamp
        out = [seed]
        frontier = seed
        total = len(seed)
        layers = 0
        nref = self.fn[seed[np.argmax(self.fa[seed])]]
        min_cos = 1.0
        cos_spread = np.cos(np.radians(min_spread_deg))
        while frontier.size and layers < max_layers and total < max_faces:
            layers += 1
            src = np.repeat(frontier, 3)
            cand = self.nb[frontier].reshape(-1)
            m = cand >= 0
            src, cand = src[m], cand[m]
            m = (self.label[cand] < 0) & (st[cand] != stamp)
            src, cand = src[m], cand[m]
            if not cand.size:
                break
            cs = (self.fn[src] * self.fn[cand]).sum(1)
            m = (cs >= self.cos_smooth) | self.unsure[cand] | self.unsure[src]
            cand = np.unique(cand[m])
            if not cand.size:
                break
            st[cand] = stamp
            out.append(cand)
            frontier = cand
            total += cand.size
            ok = ~self.unsure[cand]
            if ok.any():
                min_cos = min(min_cos, float((self.fn[cand[ok]] @ nref).min()))
            if layers >= min_layers and min_cos <= cos_spread:
                if len(np.unique(self.F[np.concatenate(out)])) >= min_verts:
                    break
        return np.concatenate(out)

    # --- main -----------------------------------------------------------
    def run(self):
        V, F, fn, fa = self.V, self.F, self.fn, self.fa
        tol = self.tol
        fine = min(tol * 0.05, 2e-6 * self.ext)
        sf_lab, sf_planes = planar_regions(V, F, self.topo, fn, fa, max(fine, 1e-9))
        self.sf_lab = sf_lab
        order_sf = np.argsort(sf_lab, kind="stable")
        counts = np.bincount(sf_lab)
        starts = np.r_[0, np.cumsum(counts)]
        sf_area = np.bincount(sf_lab, weights=fa)
        seeds = np.argsort(-sf_area, kind="stable")
        n_sf = len(seeds)
        rep = self.report
        deferred = []
        for k, s in enumerate(seeds):
            if k % 200 == 0:
                rep.progress(0.5 * k / max(1, n_sf))
            faces = order_sf[starts[s]:starts[s + 1]]
            faces = faces[self.label[faces] < 0]
            if not faces.size:
                continue
            if not self._process_seed(faces, allow_defer=True):
                deferred.append(s)
        # Seeds whose neighbourhood was ambiguous get a second chance once the
        # confident surfaces exist; whatever is left becomes small planes.
        for k, s in enumerate(deferred):
            if k % 200 == 0:
                rep.progress(0.5 + 0.1 * k / max(1, len(deferred)))
            faces = order_sf[starts[s]:starts[s + 1]]
            faces = faces[self.label[faces] < 0]
            if faces.size:
                self._process_seed(faces, allow_defer=False)
        self._refine()
        self._absorb_slivers()
        self._make_freeform()
        self._fix_apex_cones()
        return self.regions

    def _fix_apex_cones(self):
        """A cone whose region runs up to its apex without surrounding it
        (typically a long thin strip fitted by a nearly flat cone) is a
        misfit, and its boundary crosses the apex in parameter space, which
        breaks the face. Such regions become freeform cells instead."""
        regs = self.regions
        bad = []
        for ri, r in enumerate(regs):
            if r.kind != "cone":
                continue
            prim = r.prim
            P = self.V[np.unique(self.F[r.faces])]
            _, h, rad, rho = prim._parts(P)
            along = rho * np.sin(prim.alpha) + h * np.cos(prim.alpha)
            if along.min() > max(3.0 * self.tol, 0.05 * along.max()):
                continue
            # a real cone tip surrounds the apex at every distance from it
            # (near the apex any few points look like they do)
            outer = along > 0.5 * along.max()
            u, v = perp_basis(prim.d)
            ang = np.sort(np.arctan2(rad[outer] @ v, rad[outer] @ u))
            gaps = np.diff(np.r_[ang, ang[0] + 2 * np.pi])
            if len(ang) >= 3 and gaps.max() < np.radians(45.0):
                continue
            bad.append(ri)
        if not bad:
            return
        bad = set(bad)
        keep = [i for i in range(len(regs)) if i not in bad]
        new_regs = [regs[i] for i in keep]
        new_lab = self.label.copy()
        for k, i in enumerate(keep):
            new_lab[regs[i].faces] = k
        for i in sorted(bad):
            for cell in fit_freeform_cells(self, regs[i].faces):
                new_lab[cell.faces] = len(new_regs)
                new_regs.append(cell)
        self.regions = new_regs
        self.label = new_lab

    def _process_seed(self, faces, allow_defer=False):
        fa = self.fa
        seed_area = fa[faces].sum()
        P, N, _, W = self.region_points(faces, True)
        pl = Plane.fit(P, (self.fn[faces] * fa[faces, None]), W=W)
        best = None
        reg_p, pl = self.grow(pl, 1.0, faces, refit=self.refit_planes)
        area_p = fa[reg_p].sum()
        best = (area_p, 0, pl, 1.0, reg_p)

        nbh = self.neighbourhood(faces)
        nbh_area = fa[nbh].sum()
        hyps = []
        if nbh.size > faces.size and seed_area < 0.5 * nbh_area:
            Pn, Nn, okn, Wn = self.region_points(nbh, True)
            Pn_ok, Nn_ok, Wn_ok = Pn[okn], Nn[okn], Wn[okn]
            for cls in CURVED:
                prim = self._robust_fit(cls, Pn_ok, Nn_ok, Wn_ok)
                if prim is None:
                    continue
                sense = self.sense_of(prim, nbh)
                ok = self.accept(prim, sense, nbh)
                if ok.mean() < 0.3 or not ok[: faces.size].all():
                    continue
                explains_all = False
                if ok.all():
                    dn, _ = self.face_error(prim, sense, nbh)
                    explains_all = bool(dn.max() <= 0.1 * self.tol)
                reg, prim = self.grow(prim, sense, faces)
                # must have grown beyond the seed to be credible
                if reg.size <= faces.size:
                    continue
                p2, s2 = self.simplify(prim, sense, reg)
                if p2 is not prim:
                    if p2.kind == "plane":
                        continue  # the plane hypothesis already covers this
                    prim, sense = p2, s2
                    reg, prim = self.grow(prim, sense, faces)
                    if reg.size <= faces.size:
                        continue
                hyps.append((fa[reg].sum(), prim.rank, prim, sense, reg))
                if explains_all and nbh.size >= 3 * faces.size:
                    break  # a simpler surface already explains everything
        ext_h = self._extrusion_hypothesis(faces)
        if ext_h is not None:
            hyps.append(ext_h)
        if hyps:
            # Hypotheses that match the mesh much worse between vertices than
            # a competitor of similar size are dropped; then the largest area
            # wins, with simpler surface types preferred within 10%.
            amax = max(h[0] for h in hyps)
            q = [self.midpoint_dev(h[2], h[4]) for h in hyps]
            big = [i for i, h in enumerate(hyps) if h[0] >= 0.6 * amax]
            qmin = min(q[i] for i in big)
            hyps = [h for i, h in enumerate(hyps) if not (i in big and q[i] > max(2.5 * qmin, 0.5 * self.tol))]
            hyps.sort(key=lambda h: -h[0])
            cur = hyps[0]
            for h in hyps[1:]:
                if h[1] < cur[1] and h[0] >= 0.9 * cur[0]:
                    cur = h
            if cur[0] > 1.10 * best[0]:
                best = cur
        _, _, prim, sense, reg = best
        weak = prim.kind == "plane" and reg.size == faces.size
        if weak and allow_defer and nbh.size > faces.size:
            return False
        rid = len(self.regions)
        self.regions.append(Region(prim, reg, sense, weak))
        self.label[reg] = rid
        return True

    def _extrusion_hypothesis(self, faces, min_aspect=3.0, min_facets=3):
        """A cylinder for coarse prism-like facets (OpenSCAD cylinders and
        grooves): long, thin flat strips side by side along one direction.
        Their few vertices sit only at the strips' ends, so the usual
        neighbourhood has to grow far (into a groove's sloped ends, say)
        before it holds enough points, and the fit gets spoiled. Here the
        strips are gathered across the edges that run along their length,
        and a cylinder along that direction is fitted to them alone.
        Returns a hypothesis tuple like _process_seed's, or None."""
        V, F, fa, fn = self.V, self.F, self.fa, self.fn
        topo = self.topo
        # the direction of the seed's longest outline edge (the edges inside
        # it, like a quad's diagonal, don't count)
        he = (3 * faces[:, None] + np.arange(3)[None]).reshape(-1)
        tw = topo.twin[he]
        inside = np.zeros(len(F), dtype=bool)
        inside[faces] = True
        outline = he[(tw < 0) | ~inside[np.maximum(tw, 0) // 3]]
        if not outline.size:
            return None
        E = V[topo.he_to[outline]] - V[topo.he_from[outline]]
        L = np.linalg.norm(E, axis=1)
        k = int(np.argmax(L))
        lmax = float(L[k])
        if lmax <= 0:
            return None
        d = E[k] / lmax
        width = float(fa[faces].sum()) / lmax
        if lmax < min_aspect * max(width, 1e-12):
            return None
        sin_par = np.sin(np.radians(1.0))
        sin_perp = np.sin(np.radians(2.0))
        if np.abs(fn[faces] @ d).max() > sin_perp:
            return None
        stamp = self._new_stamp()
        st = self._stamp
        st[faces] = stamp
        strip = [faces]
        frontier = faces
        total = len(faces)
        while frontier.size and total < 4000:
            he = (3 * frontier[:, None] + np.arange(3)[None]).reshape(-1)
            tw = topo.twin[he]
            ok = tw >= 0
            he, tw = he[ok], tw[ok]
            g = tw // 3
            a, b = V[topo.he_from[he]], V[topo.he_to[he]]
            e = b - a
            el = np.maximum(np.linalg.norm(e, axis=1), 1e-30)
            parallel = np.linalg.norm(np.cross(e / el[:, None], d), axis=1) <= sin_par
            coplanar = (fn[he // 3] * fn[g]).sum(1) >= np.cos(np.radians(0.5))
            m = (parallel | coplanar) & (self.label[g] < 0) & (st[g] != stamp) & (np.abs(fn[g] @ d) <= sin_perp)
            new = np.unique(g[m])
            if not new.size:
                break
            st[new] = stamp
            strip.append(new)
            frontier = new
            total += len(new)
        strip = np.concatenate(strip)
        # at least a few distinct facets around the axis
        ang = np.round(np.degrees(np.arctan2(*(fn[strip] @ np.stack(perp_basis(d), 1)).T[::-1])), 0)
        if len(np.unique(ang)) < min_facets:
            return None
        P, N, okn, W = self.region_points(strip, True)
        try:
            prim = Cylinder.fit(P[okn], N[okn], ext=self.ext, W=W[okn], d=d)
        except Exception:
            return None
        if prim is None or prim.degenerate(self.ext):
            return None
        sense = self.sense_of(prim, strip)
        if not self.accept(prim, sense, strip).all():
            return None
        reg, prim = self.grow(prim, sense, faces)
        if reg.size <= faces.size:
            return None
        return (fa[reg].sum(), prim.rank, prim, sense, reg)

    SIMPLER = {
        "torus": ("plane", "cylinder", "sphere", "cone"),
        "cone": ("plane", "cylinder"),
        "sphere": ("plane",),
        "cylinder": ("plane",),
    }

    def simplify(self, prim, sense, faces):
        """Replace a surface by a simpler kind when that fits the same faces
        (a torus with a huge ring radius is really a cylinder, etc.)."""
        kinds = self.SIMPLER.get(prim.kind, ())
        if not kinds:
            return prim, sense
        P, N, ok, W = self.region_points(faces, True)
        classes = {"plane": Plane, "cylinder": Cylinder, "sphere": Sphere, "cone": Cone}
        for k in kinds:
            cls = classes[k]
            if cls is Plane:
                p2 = Plane.fit(P, (self.fn[faces] * self.fa[faces, None]), W=W)
            else:
                p2 = self._robust_fit(cls, P[ok], N[ok], W[ok])
            if p2 is None:
                continue
            s2 = 1.0 if cls is Plane else self.sense_of(p2, faces)
            if self.accept(p2, s2, faces).all():
                return p2, s2
        return prim, sense

    def _robust_fit(self, cls, P, N, W=None, iters=2):
        """Weighted fit, then refit on inliers (trimmed least squares) so
        faces of a tangent neighbour (e.g. a fillet next to a hole) don't
        skew the result."""
        try:
            prim = cls.fit(P, N, ext=self.ext, W=W)
        except Exception:
            return None
        if prim is None or prim.degenerate(self.ext):
            return None
        for _ in range(iters):
            d = prim.distance(P)
            inl = d <= 2.0 * self.tol
            wsum = W.sum() if W is not None else len(P)
            winl = W[inl].sum() if W is not None else inl.sum()
            if inl.all() or inl.sum() < 8 or winl < 0.3 * wsum:
                break
            try:
                p2 = cls.fit(P[inl], N[inl], ext=self.ext, W=None if W is None else W[inl])
            except Exception:
                break
            if p2 is None or p2.degenerate(self.ext):
                break
            prim = p2
        return prim

    # --- refinement -----------------------------------------------------
    def _refine(self, iters=3):
        regs = self.regions
        F = self.F
        for it in range(iters):
            moved = 0
            lab = self.label
            # boundary faces: any neighbour with another label
            nbl = np.where(self.nb >= 0, lab[np.maximum(self.nb, 0)], -1)
            bmask = ((nbl != lab[:, None]) & (nbl >= 0)).any(1)
            bfaces = np.nonzero(bmask)[0]
            if not bfaces.size:
                break
            # error to own primitive
            own = np.full(len(bfaces), np.inf)
            best_lab = lab[bfaces].copy()
            for r in np.unique(lab[bfaces]):
                m = lab[bfaces] == r
                if regs[r].weak:
                    continue  # weak (single facet) regions dissolve readily
                d, cs = self.face_error(regs[r].prim, regs[r].sense, bfaces[m])
                own[m] = np.where((cs >= self.cos_n) | self.unsure[bfaces[m]], d, np.inf)
            best_err = own.copy()
            for j in range(3):
                cl = nbl[bfaces, j]
                for r in np.unique(cl[cl >= 0]):
                    m = (cl == r) & (cl != lab[bfaces])
                    if not m.any():
                        continue
                    reg = regs[r]
                    if reg.weak:
                        continue
                    d, cs = self.face_error(reg.prim, reg.sense, bfaces[m])
                    e = np.where((cs >= self.cos_n) | self.unsure[bfaces[m]], d, np.inf)
                    e = np.where(e <= self.tol, e, np.inf)
                    idx = np.nonzero(m)[0]
                    better = e < best_err[idx] - 0.05 * self.tol
                    best_err[idx[better]] = e[better]
                    best_lab[idx[better]] = r
            ch = best_lab != lab[bfaces]
            # weak regions dissolve readily into neighbours that accept them
            if ch.any():
                self.label[bfaces[ch]] = best_lab[ch]
                moved = int(ch.sum())
            if moved == 0:
                break
        self._rebuild_regions()

    def _absorb_slivers(self, max_tris=3, tol_factor=3.0):
        """Tiny regions (slivers where three surfaces meet) join the
        neighbouring surface that fits them best. Genuine tiny faces, such
        as the corner triangle of a chamfered box, fit no neighbour and stay."""
        regs = self.regions
        lab = self.label
        changed = False
        cos_relaxed = np.cos(np.radians(60.0))
        areas = np.array([self.fa[r.faces].sum() for r in regs])
        big = areas[np.array([len(r.faces) > max_tris for r in regs], dtype=bool)]
        small_area = 0.02 * (np.median(big) if len(big) else areas.max())
        for ri, r in enumerate(regs):
            if not (len(r.faces) <= max_tris and (r.weak or areas[ri] <= small_area)):
                continue
            for f in r.faces:
                best, best_e = None, np.inf
                for g in self.nb[f]:
                    if g < 0:
                        continue
                    rj = lab[g]
                    if rj == ri or regs[rj].weak or len(regs[rj].faces) <= max_tris:
                        continue
                    d, cs = self.face_error(regs[rj].prim, regs[rj].sense, np.array([f]))
                    if cs[0] < cos_relaxed and not (self.unsure[f] or self.needle[f]):
                        continue
                    if d[0] <= tol_factor * self.tol and d[0] < best_e:
                        best, best_e = rj, d[0]
                if best is not None:
                    lab[f] = best
                    changed = True
        if changed:
            self._rebuild_regions()

    def _make_freeform(self, smooth_deg=35.0):
        """Clusters of facet-like leftovers become freeform B-spline cells.

        A weak region (one flat facet that no surface claimed) is facet-like
        when it meets another weak region across a smooth edge; isolated weak
        regions bounded by sharp edges are genuine small planar faces."""
        from scipy import sparse
        from scipy.sparse.csgraph import connected_components

        regs = self.regions
        lab = self.label
        weak = np.array([r.weak for r in regs], dtype=bool)
        if not weak.any():
            return
        nf = len(self.F)
        rows = np.repeat(np.arange(nf), 3)
        cols = self.nb.reshape(-1)
        m = cols >= 0
        rows, cols = rows[m], cols[m]
        cs = (self.fn[rows] * self.fn[cols]).sum(1)
        smooth = (cs >= np.cos(np.radians(smooth_deg))) | self.unsure[rows] | self.unsure[cols]
        lr, lc = lab[rows], lab[cols]
        m = smooth & (lr != lc) & weak[lr] & weak[lc]
        nr = len(regs)
        G = sparse.coo_matrix((np.ones(m.sum(), dtype=np.int8), (lr[m], lc[m])), shape=(nr, nr))
        ncomp, comp = connected_components(G, directed=False)
        sizes = np.bincount(comp, minlength=ncomp)
        ff = weak & (sizes[comp] >= 2)
        if not ff.any():
            return
        self.freeform_faces = np.nonzero(ff[lab])[0]
        keep = [i for i in range(nr) if not ff[i]]
        new_regs = [regs[i] for i in keep]
        new_lab = -np.ones(nf, dtype=np.int64)
        for k, i in enumerate(keep):
            new_lab[regs[i].faces] = k
        for cfaces in _cluster_components(self.freeform_faces, self.nb):
            for cell in fit_freeform_cells(self, cfaces):
                new_lab[cell.faces] = len(new_regs)
                new_regs.append(cell)
        self.regions = new_regs
        self.label = new_lab

    def _rebuild_regions(self):
        """Split disconnected labels, refit primitives, merge equal ones."""
        from scipy import sparse
        from scipy.sparse.csgraph import connected_components

        nf = len(self.F)
        lab = self.label
        rows = np.repeat(np.arange(nf), 3)
        cols = self.nb.reshape(-1)
        m = (cols >= 0) & (lab[rows] == lab[np.maximum(cols, 0)])
        G = sparse.coo_matrix((np.ones(m.sum(), dtype=np.int8), (rows[m], cols[m])), shape=(nf, nf))
        ncomp, comp = connected_components(G, directed=False)
        old = self.regions
        new_regions = []
        new_label = np.empty(nf, dtype=np.int64)
        order = np.argsort(comp, kind="stable")
        sc = comp[order]
        starts = np.r_[0, np.nonzero(np.diff(sc))[0] + 1, nf]
        for k in range(len(starts) - 1):
            faces = order[starts[k]:starts[k + 1]]
            r = old[lab[faces[0]]]
            prim, sense = r.prim, r.sense
            if prim.kind != "plane" and faces.size >= 3:
                P, N, ok, W = self.region_points(faces, True)
                p2 = prim.refit(P[ok], N[ok], W[ok])
                if p2 is not None and not p2.degenerate(self.ext) and self.accept(p2, sense, faces, 1.5 * self.tol).all():
                    prim = p2
                prim, sense = self.simplify(prim, sense, faces)
            elif prim.kind == "plane":
                P, N, ok, W = self.region_points(faces, True)
                p2 = prim.refit(P, None, W)
                if p2.distance(P).max() <= self.tol:
                    prim = p2
            weak = r.weak and prim.kind == "plane"
            new_label[faces] = len(new_regions)
            new_regions.append(Region(prim, faces, sense, weak))
        self.regions = new_regions
        self.label = new_label
        self._merge_equal()

    def _merge_equal(self):
        regs = self.regions
        lab = self.label
        nb = self.nb
        nbl = np.where(nb >= 0, lab[np.maximum(nb, 0)], -1)
        pairs = set()
        for j in range(3):
            a = lab
            b = nbl[:, j]
            m = (b >= 0) & (a != b)
            for x, y in zip(a[m], b[m]):
                pairs.add((min(x, y), max(x, y)))
        parent = list(range(len(regs)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for x, y in sorted(pairs):
            rx, ry = regs[x], regs[y]
            if rx.kind != ry.kind or rx.kind == "plane" and (rx.weak or ry.weak):
                continue
            if rx.sense != ry.sense:
                continue
            if rx.prim.same_as(ry.prim, self.tol, self.ext):
                fx, fy = find(x), find(y)
                if fx != fy:
                    parent[fy] = fx
        roots = {}
        out = []
        for i, r in enumerate(regs):
            f = find(i)
            if f not in roots:
                roots[f] = len(out)
                out.append(Region(regs[f].prim, [], regs[f].sense, regs[f].weak))
            out[roots[f]].faces.append(r.faces)
        newlab = np.empty_like(lab)
        for r in out:
            r.faces = np.concatenate(r.faces)
        for i, r in enumerate(out):
            newlab[r.faces] = i
        self.regions = out
        self.label = newlab


def _cluster_components(faces, nb):
    from .freeform import _components

    return _components(faces, nb)


def fit_freeform_cells(seg, faces, max_rounds=6):
    """Split a face set into height-field disk cells and fit each with a
    B-spline; cells whose fit misses the tolerance are split again."""
    from .freeform import fit_cell, split_cells, _geodesic_split

    V, F = seg.V, seg.F
    out = []
    todo = split_cells(V, F, faces, seg.fn, seg.fa, seg.nb)
    rounds = 0
    cen = None
    while todo:
        cfaces, n = todo.pop()
        q = float(getattr(seg, "detail", 0.7))
        surf = fit_cell(V, F, cfaces, n, seg.tol, detail=q, max_ctrl=int(16 + 30 * q))
        if surf is None:
            continue
        if surf.dev > seg.tol and len(cfaces) > 8 and rounds < 400:
            rounds += 1
            if cen is None:
                cen = V[F].mean(1)
            parts = _geodesic_split(cfaces, cen, seg.nb, 2)
            if len(parts) >= 2:
                for p in parts:
                    todo.extend(split_cells(V, F, p, seg.fn, seg.fa, seg.nb))
                continue
        reg = Region(surf, cfaces, 1.0, False)
        out.append(reg)
    return out


# ----------------------------------------------------------------------------
# B-rep construction with analytic surfaces
# ----------------------------------------------------------------------------


def _dir(v):
    return gp_Dir(float(v[0]), float(v[1]), float(v[2]))


class CADAssembler(BRepAssembler):
    def __init__(self, V, rg, regions, tol, report=NULL):
        surfaces = [_SurfAdapter(r) for r in regions]
        super().__init__(np.array(V, dtype=np.float64, copy=True), rg, surfaces, tol, report)
        self.regions = regions
        self.sfe = ShapeFix_Edge()
        self.frames = {}
        self.seams = {}
        self.surf_params = {}
        self.fixed_closed = set()
        self.unseamed = set()
        self.sphere_mode = {}
        self.pole_caps = {}
        self.ext = float(np.linalg.norm(V.max(0) - V.min(0)))

    # --- edges ----------------------------------------------------------
    def build_chain_edges(self, chain):
        L = self.regions[chain.left]
        R = self.regions[chain.right] if chain.right >= 0 else None
        both_planar = L.kind == "plane" and (R is None or R.kind == "plane")
        if len(chain.verts) == 2:
            return super().build_chain_edges(chain)
        if both_planar:
            if not chain.closed:
                P = self.V[chain.verts]
                a, b = P[0], P[-1]
                d = b - a
                L = np.linalg.norm(d)
                if L > MIN_TOL:
                    d = d / L
                    Q = P - a
                    off = np.linalg.norm(Q - np.outer(Q @ d, d), axis=1).max()
                    if off <= 5 * self.tol:
                        e = self._line_edge(chain.verts[0], chain.verts[-1])
                        self._set_edge_tol(e, chain, off)
                        chain.edges = [e]
                        return
            return super().build_chain_edges(chain)
        if self.chain_is_straight(chain):
            return super().build_chain_edges(chain)
        P = self.V[chain.verts]
        # circle?
        circ = _fit_circle_3d(P[:-1] if chain.closed else P)
        if circ is not None and circ[3] <= self.tol:
            c, n, r, _ = circ
            c, n, r = self._snap_circle(chain, c, n, r)
            e = self._circle_edge(chain, c, n, r)
            if e is not None:
                chain.edges = [e]
                return
        e = self._bspline_edge(chain, P)
        if e is not None:
            chain.edges = [e]
            return
        chain.edges = self._polyline_edges(chain.verts)

    def _set_edge_tol(self, e, chain, curve_dev):
        t = max(MIN_TOL, curve_dev * 1.5 + 1e-9)
        for vi in (chain.verts[0], chain.verts[-1]):
            self.vtol[vi] = max(self.vtol[vi], t)
        self.B.UpdateEdge(e, t)
        for vi in (chain.verts[0], chain.verts[-1]):
            self.B.UpdateVertex(self.vertex(vi), max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9))

    def _snap_circle(self, chain, c, n, r):
        """A circle around the axis of an adjacent cylinder or cone is one of
        that surface's own circles: take its exact axis and radius, so the
        edge lies exactly on the surface and its start lines up exactly with
        the face's seam (tiny holes and pins otherwise come out slightly
        crossed when other programs read the file)."""
        if not chain.closed:
            return c, n, r  # a short arc's fitted axis is too uncertain
        for rid in (chain.left, chain.right):
            if rid < 0:
                continue
            prim = self.regions[rid].prim
            if prim.kind not in ("cylinder", "cone"):
                continue
            d = prim.d
            if abs(float(n @ d)) < np.cos(np.radians(2.0)):
                continue
            if prim.kind == "cylinder":
                h = float((c - prim.a) @ d)
                c2, r2 = prim.a + h * d, prim.r
            else:
                h = float((c - prim.apex) @ d)
                if h <= 0:
                    continue
                c2, r2 = prim.apex + h * d, h * np.tan(prim.alpha)
            if np.linalg.norm(c2 - c) > self.tol or abs(r2 - r) > self.tol:
                continue
            return c2, (d if float(n @ d) > 0 else -d), float(r2)
        return c, n, r

    def _circle_edge(self, chain, c, n, r):
        P = self.V[chain.verts]
        # orient circle normal so that the chain runs counter-clockwise
        u = unit(P[0] - c - ((P[0] - c) @ n) * n)
        vv = np.cross(n, u)
        ang = np.unwrap(np.arctan2((P - c) @ vv, (P - c) @ u))
        if ang[-1] < ang[0]:
            n = -n
            vv = -vv
            ang = -ang
        ax = gp_Ax2(_pnt(c), _dir(n), _dir(u))
        circ = gp_Circ(ax, float(r))
        from OCP.Geom import Geom_Circle

        gc = Geom_Circle(circ)
        dev = float(np.abs(np.linalg.norm((P - c) - np.outer((P - c) @ n, n), axis=1) - r).max())
        dev = max(dev, float(np.abs((P - c) @ n).max()))
        v0 = self.vertex(chain.verts[0])
        if chain.closed:
            span = ang[-1] - ang[0]
            if abs(span - 2 * np.pi) > 0.3:
                return None
            # vertex position may not be exactly on the circle
            self.vtol[chain.verts[0]] = max(self.vtol[chain.verts[0]], dev)
            self.B.UpdateVertex(v0, max(MIN_TOL, 1.5 * self.vtol[chain.verts[0]] + 1e-9))
            mk = BRepBuilderAPI_MakeEdge(gc, v0, v0, 0.0, 2 * np.pi)
        else:
            v1 = self.vertex(chain.verts[-1])
            a0, a1 = float(ang[0]), float(ang[-1])
            if a1 - a0 > 2 * np.pi - 1e-6:
                return None
            for vi in (chain.verts[0], chain.verts[-1]):
                self.vtol[vi] = max(self.vtol[vi], dev)
                self.B.UpdateVertex(self.vertex(vi), max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9))
            mk = BRepBuilderAPI_MakeEdge(gc, v0, v1, a0, a1)
        if not mk.IsDone():
            return None
        # an arc through the points can still bulge away between them
        span = (2 * np.pi) if chain.closed else (ang[-1] - ang[0])
        a0 = 0.0 if chain.closed else float(ang[0])
        ts = np.linspace(a0, a0 + span, max(24, 3 * len(P)))
        S = c + r * (np.outer(np.cos(ts), u) + np.outer(np.sin(ts), vv))
        if _polyline_distance(S, P) > self.tol:
            return None
        e = mk.Edge()
        self._set_edge_tol(e, chain, dev)
        return e

    def _bspline_edge(self, chain, P):
        """Smooth curve through the chain points. Candidates are checked
        against the actual polyline (curve samples -> nearest segment) so an
        approximation that overshoots between points is never used."""
        curve, dev = fit_chain_curve(P, self.tol)
        if curve is None:
            return None
        v0 = self.vertex(chain.verts[0])
        v1 = self.vertex(chain.verts[-1])
        ends = [curve.Value(curve.FirstParameter()), curve.Value(curve.LastParameter())]
        for vi, pe in zip((chain.verts[0], chain.verts[-1]), ends):
            d = pe.Distance(_pnt(self.V[vi]))
            self.vtol[vi] = max(self.vtol[vi], d, dev)
            self.B.UpdateVertex(self.vertex(vi), max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9))
        mk = BRepBuilderAPI_MakeEdge(curve, v0, v1, curve.FirstParameter(), curve.LastParameter())
        if not mk.IsDone():
            return None
        e = mk.Edge()
        self._set_edge_tol(e, chain, dev)
        return e

    # --- seams on periodic surfaces --------------------------------------
    #
    # A face that wraps all the way around a cylinder, cone or torus needs a
    # seam edge: one wire made of [bottom loop, seam, top loop, seam
    # reversed]. Seams are planned before any edge is built so that the seam
    # end points can become real vertices on the (shared) boundary chains.

    def build(self, progress=None):
        self.split_thin_loops()
        self.plan_seams()
        return super().build(progress)

    def add_point(self, p):
        self.V = np.vstack([self.V, np.asarray(p, dtype=np.float64)[None]])
        self.vtol = np.r_[self.vtol, 0.0]
        return len(self.V) - 1

    def region_frame(self, r):
        fr = self.frames.get(r)
        if fr is not None:
            return fr
        reg = self.regions[r]
        prim = reg.prim
        P = self.V[np.unique(self.rg.F[reg.faces])]
        if prim.kind == "cylinder":
            o, d = prim.a, prim.d
        elif prim.kind == "cone":
            o, d = prim.apex, prim.d
        elif prim.kind == "torus":
            o, d = prim.c, prim.d
        elif prim.kind == "sphere":
            return self._sphere_frame(r, P)
        else:
            return None
        fr = _Frame(o, d, self._seam_dir(P, o, d))
        self.frames[r] = fr
        return fr

    def _sphere_frame(self, r, P):
        """Small sphere patches get their poles pushed out of the way; big
        caps and bands get the axis of their largest boundary loop so the
        boundary wraps around it (handled with a seam / pole edge)."""
        prim = self.regions[r].prim
        c = prim.c
        dirs = P - c
        dirs /= np.maximum(np.linalg.norm(dirs, axis=1), 1e-30)[:, None]
        m = unit(dirs.mean(0)) if np.any(dirs.mean(0)) else np.array([0, 0, 1.0])
        spread = float(np.degrees(np.arccos(np.clip(dirs @ m, -1, 1)).max())) if len(dirs) else 0.0
        loops = self.rg.region_loops[r]
        if loops and (spread > 70.0 or np.linalg.norm(dirs.mean(0)) < 0.3):
            big = max(loops, key=lambda l: len(self.rg.loop_vertices(l)))
            L = self.V[self.rg.loop_vertices(big)]
            if len(L) >= 3:
                Lc = L.mean(0)
                _, _, vt = np.linalg.svd(L - Lc, full_matrices=False)
                n = vt[2]
                if np.dot(n, m) < 0:
                    n = -n
                fr = _Frame(c, n, self._seam_dir(P, c, n))
                self.sphere_mode[r] = "axial"
                self.frames[r] = fr
                return fr
        u, v = perp_basis(m)
        fr = _Frame(c, u, -m)
        self.sphere_mode[r] = "patch"
        self.frames[r] = fr
        return fr

    def loop_anchors(self, loop):
        out = []
        for cid, fwd in loop:
            c = self.rg.chains[cid]
            if c.closed and cid not in self.fixed_closed:
                continue
            out.append(c.verts[0])
            out.append(c.verts[-1])
        return list(dict.fromkeys(out))

    def plan_seams(self):
        rg = self.rg
        for r, reg in enumerate(self.regions):
            if reg.kind not in PERIODIC and reg.kind != "sphere":
                continue
            loops = rg.region_loops[r]
            if not loops:
                continue
            fr = self.region_frame(r)
            if reg.kind == "sphere" and self.sphere_mode.get(r) != "axial":
                continue
            info = []
            for li, loop in enumerate(loops):
                vs = rg.loop_vertices(loop)
                ang = np.unwrap(fr.angle(self.V[vs + vs[:1]]))
                w = int(np.round((ang[-1] - ang[0]) / (2 * np.pi)))
                info.append((li, w, ang))
            wrap = [x for x in info if x[1] != 0]
            if not wrap:
                continue
            if reg.kind == "sphere" and len(wrap) == 1:
                self._plan_pole_cap(r, loops, info, wrap[0][0], fr)
                continue
            if len(wrap) != 2:
                self.unseamed.add(r)
                continue
            forb = [(a.min() - 0.03, a.max() + 0.03) for (_, w, a) in info if w == 0]

            def allowed(a):
                for lo, hi in forb:
                    k = np.floor((a - lo) / (2 * np.pi))
                    aa = a - k * 2 * np.pi
                    if lo <= aa <= hi:
                        return False
                return True

            def clearance(a):
                if not forb:
                    return np.pi
                best = np.pi
                for lo, hi in forb:
                    for edge in (lo, hi):
                        dd = abs(_wrap(a - edge))
                        best = min(best, dd)
                return best

            A, Bl = wrap[0][0], wrap[1][0]
            anchors = {A: self.loop_anchors(loops[A]), Bl: self.loop_anchors(loops[Bl])}
            cand = []
            for L in (A, Bl):
                other = Bl if L == A else A
                oang = [fr.angle(self.V[v][None])[0] for v in anchors[other]]
                for v in anchors[L]:
                    a = fr.angle(self.V[v][None])[0]
                    if not allowed(a):
                        continue
                    comp = (not anchors[other]) or any(abs(_wrap(b - a)) < 1e-9 for b in oang)
                    cand.append((comp, clearance(a), a))
            if cand:
                cand.sort(key=lambda t: (not t[0], -t[1]))
                u0 = cand[0][2]
            else:
                vs = rg.loop_vertices(loops[A])
                a = fr.angle(self.V[vs[:1]])[0]
                u0 = a if allowed(a) else _best_gap(forb)
            sv = []
            for L in (A, Bl):
                v = self.ensure_seam_vertex(r, loops[L], u0, fr)
                if v is None:
                    break
                sv.append(v)
            if len(sv) != 2 or sv[0] == sv[1]:
                self.unseamed.add(r)
                continue
            self.frames[r] = fr.rotated(u0)
            self.seams[r] = (A, Bl, sv[0], sv[1])

    def _plan_pole_cap(self, r, loops, info, A, fr):
        forb = [(a.min() - 0.03, a.max() + 0.03) for (_, w, a) in info if w == 0]
        anchors = self.loop_anchors(loops[A])
        u0 = None
        for v in anchors:
            a = fr.angle(self.V[v][None])[0]
            ok = True
            for lo, hi in forb:
                k = np.floor((a - lo) / (2 * np.pi))
                if lo <= a - k * 2 * np.pi <= hi:
                    ok = False
            if ok:
                u0 = a
                break
        if u0 is None:
            if anchors and forb:
                self.unseamed.add(r)
                return
            vs = self.rg.loop_vertices(loops[A])
            u0 = fr.angle(self.V[vs[:1]])[0] if not forb else _best_gap(forb)
        sv = self.ensure_seam_vertex(r, loops[A], u0, fr)
        if sv is None:
            self.unseamed.add(r)
            return
        self.frames[r] = fr.rotated(u0)
        self.pole_caps[r] = (A, sv)

    def ensure_seam_vertex(self, r, loop, u0, fr):
        rg = self.rg
        for cid, fwd in loop:
            c = rg.chains[cid]
            if c.closed and cid not in self.fixed_closed:
                continue
            for v in (c.verts[0], c.verts[-1]):
                if abs(_wrap(fr.angle(self.V[v][None])[0] - u0)) < 1e-9:
                    return v
        ext = self.ext
        for cid, fwd in loop:
            c = rg.chains[cid]
            P = self.V[c.verts]
            rel = _wrap(fr.angle(P) - u0)
            for i in range(len(P) - 1):
                a0, a1 = rel[i], rel[i + 1]
                if abs(a1 - a0) > np.pi:
                    continue  # jump across the opposite side
                if not ((a0 <= 0.0 < a1) or (a1 <= 0.0 < a0) or (a0 < 0.0 <= a1) or (a1 < 0.0 <= a0)):
                    continue
                if abs(a0) < 1e-12 and 0 < i:
                    k = i  # existing interior vertex exactly on the seam
                    q = c.verts[k]
                else:
                    t = a0 / (a0 - a1) if a0 != a1 else 0.0
                    p = P[i] + t * (P[i + 1] - P[i])
                    # a straight-line blend misses the seam angle slightly;
                    # put the point exactly on it (on tiny holes and pins
                    # the miss is enough to make the boundary cross the seam)
                    p = fr.at_angle(self.snap_to_surface(r, p), u0)
                    if np.linalg.norm(p - P[i]) < 1e-9 * ext and i > 0:
                        k, q = i, c.verts[i]
                    elif np.linalg.norm(p - P[i + 1]) < 1e-9 * ext and i + 1 < len(P) - 1:
                        k, q = i + 1, c.verts[i + 1]
                    else:
                        q = self.add_point(p)
                        rg.insert_vertex(cid, i + 1, q)
                        k = i + 1
                if c.closed:
                    rg.rotate_closed_chain(cid, k)
                    self.fixed_closed.add(cid)
                elif 0 < k < len(c.verts) - 1:
                    rg.split_chain(cid, k)
                return q
        return None

    def snap_to_surface(self, r, p):
        prim = self.regions[r].prim
        p = np.asarray(p, dtype=np.float64)
        if prim.kind in ("cylinder", "cone", "torus", "sphere"):
            d = prim.distance(p[None])[0]
            n = prim.normal(p[None])[0]
            if prim.kind == "cone":
                s = prim.signed(p[None])[0]
                return p - s * n
            # move along the surface normal by the signed offset
            if prim.kind == "cylinder":
                rel = p - prim.a
                h = rel @ prim.d
                rad = rel - h * prim.d
                return prim.a + h * prim.d + prim.r * unit(rad)
            if prim.kind == "sphere":
                return prim.c + prim.r * unit(p - prim.c)
            if prim.kind == "torus":
                q = prim._spine(p[None])[0]
                return q + prim.r * unit(p - q)
        return p

    # --- faces ----------------------------------------------------------
    def closed_region_ok(self, r):
        return self.regions[r].kind in ("sphere", "torus")

    def build_face(self, r):
        reg = self.regions[r]
        if reg.kind == "plane":
            return self._planar_face(r, self.surfaces[r], self.rg.region_loops[r])
        if reg.kind == "freeform":
            surf = reg.prim.geom()
        else:
            surf = self._make_surface(reg, r)
        loops = self.rg.region_loops[r]
        if not loops:
            # a closed surface on its own (full sphere or torus)
            from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace

            mf = BRepBuilderAPI_MakeFace(surf, 1e-7)
            if not mf.IsDone():
                raise RuntimeError("closed surface face failed")
            face = mf.Face()
            if reg.sense < 0:
                face.Reverse()
            return face
        tol = max(self.tol, MIN_TOL)
        nat = loops if reg.sense > 0 else [_reverse_loop(l) for l in loops]
        face = self._bounded_face(r, surf, nat, reg.sense < 0, tol)
        if reg.kind in PERIODIC + ("sphere",) and r not in self.seams and r not in self.pole_caps:
            # On a closed surface a boundary running the wrong way round
            # trims the complementary part of it: valid, but wrong. The
            # mesh area of the region tells which way is right.
            want = self.region_area(r)
            got = _face_area(face)
            if abs(got - want) > 0.15 * want:
                alt = self._bounded_face(r, surf, [_reverse_loop(l) for l in nat], reg.sense > 0, tol)
                if abs(_face_area(alt) - want) < abs(got - want):
                    face = alt
        return face

    def region_area(self, r):
        if getattr(self, "_fa", None) is None:
            T = self.V[self.rg.F]
            self._fa = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
        return float(self._fa[self.regions[r].faces].sum())

    def _bounded_face(self, r, surf, nat, reverse, tol):
        face = TopoDS_Face()
        self.B.MakeFace(face, surf, tol)
        wires = []
        seam = self.seams.get(r)
        used = set()
        if seam is not None:
            A, Bl, sA, sB = seam
            wires.append(self._seam_wire(r, face, nat, A, Bl, sA, sB))
            used = {A, Bl}
        elif r in self.pole_caps:
            A, sA = self.pole_caps[r]
            wires.append(self._pole_cap_wire(r, face, nat, A, sA))
            used = {A}
        for i, loop in enumerate(nat):
            if i not in used:
                wires.append(self.wire_for_loop(loop))
        for w in wires:
            exp = TopExp_Explorer(w, TopAbs_EDGE)
            while exp.More():
                e = TopoDS.Edge(exp.Current())
                exp.Next()
                if BRep_Tool.IsClosed_s(e, face):
                    continue  # seam: explicit pcurves already
                self.sfe.FixAddPCurve(e, face, False, tol)
            sfw = ShapeFix_Wire(w, face, tol)
            sfw.FixShifted()
            self.B.Add(face, sfw.Wire())
        if reverse:
            face.Reverse()
        return face

    def _loop_edges_from(self, loop, start):
        k = None
        for i, (cid, fwd) in enumerate(loop):
            c = self.rg.chains[cid]
            sv = c.verts[0] if fwd else c.verts[-1]
            if sv == start:
                k = i
                break
        if k is None:
            raise RuntimeError("seam vertex not on loop")
        loop = loop[k:] + loop[:k]
        out = []
        for cid, fwd in loop:
            edges = self.rg.chains[cid].edges
            if fwd:
                out.extend(edges)
            else:
                out.extend(e.Reversed() for e in reversed(edges))
        return out

    def _winding(self, r, loop):
        fr = self.frames[r]
        vs = self.rg.loop_vertices(loop)
        ang = np.unwrap(fr.angle(self.V[vs + vs[:1]]))
        return int(np.round((ang[-1] - ang[0]) / (2 * np.pi)))

    def _vparam(self, r, P):
        reg = self.regions[r]
        prim = reg.prim
        sp = self.surf_params[r]
        if prim.kind == "cylinder":
            return float((P - prim.a) @ prim.d)
        if prim.kind == "cone":
            return float(((P - sp["loc"]) @ prim.d) / np.cos(prim.alpha))
        if prim.kind == "torus":
            rel = P - prim.c
            h = rel @ prim.d
            rho = np.linalg.norm(rel - h * prim.d)
            v = np.arctan2(h, rho - prim.R)
            # express in the region's v branch
            v0 = sp["vcut"]
            return float(v0 + np.mod(v - v0, 2 * np.pi))
        if prim.kind == "sphere":
            fr = self.frames[r]
            return float(np.arcsin(np.clip(((P - prim.c) @ fr.d) / prim.r, -1.0, 1.0)))
        raise ValueError(prim.kind)

    def _meridian(self, r, v0, v1):
        from OCP.Geom import Geom_Circle

        prim = self.regions[r].prim
        fr = self.frames[r]
        n = np.cross(fr.x, fr.d)
        geom = Geom_Circle(gp_Ax2(_pnt(prim.c), _dir(n), _dir(fr.x)), prim.r)
        p = lambda v: prim.c + prim.r * (np.cos(v) * fr.x + np.sin(v) * fr.d)
        return geom, p(v0), p(v1)

    def _pole_cap_wire(self, r, face, nat, A, s):
        """Wire of a spherical cap containing a pole: [loop, seam, degenerate
        pole edge, seam reversed]."""
        prim = self.regions[r].prim
        fr = self.frames[r]
        tol = max(self.tol, MIN_TOL)
        two_pi = 2 * np.pi
        w_a = self._winding(r, nat[A])
        v0 = self._vparam(r, self.V[s])
        north = w_a > 0
        if w_a == 0:
            raise RuntimeError("cap loop does not wrap")
        vp = np.pi / 2 if north else -np.pi / 2
        pole_pt = prim.c + prim.r * np.sign(vp) * fr.d
        pole = TopoDS_Vertex()
        self.B.MakeVertex(pole, _pnt(pole_pt), tol)
        # seam: meridian at u=0 between the loop and the pole, oriented by
        # increasing v
        lo, hi = (v0, vp) if north else (vp, v0)
        geom, p_lo, p_hi = self._meridian(r, lo, hi)
        d = float(np.linalg.norm(self.V[s] - (p_lo if north else p_hi)))
        self.vtol[s] = max(self.vtol[s], d)
        self.B.UpdateVertex(self.vertex(s), max(MIN_TOL, 1.5 * self.vtol[s] + 1e-9))
        seam = TopoDS_Edge()
        self.B.MakeEdge(seam, geom, tol)
        if north:
            self.B.Add(seam, self.vertex(s))
            self.B.Add(seam, pole.Reversed())
        else:
            self.B.Add(seam, pole)
            self.B.Add(seam, self.vertex(s).Reversed())
        self.B.Range(seam, lo, hi)
        pc1 = Geom2d_Line(gp_Pnt2d(two_pi, 0.0), gp_Dir2d(0.0, 1.0))
        pc2 = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(0.0, 1.0))
        self.B.UpdateEdge(seam, pc1, pc2, face, tol)
        self.B.Range(seam, face, lo, hi)
        # degenerate edge at the pole
        deg = TopoDS_Edge()
        self.B.MakeEdge(deg)
        self.B.Add(deg, pole)
        self.B.Add(deg, pole.Reversed())
        self.B.Degenerated(deg, True)
        if north:
            pcd = Geom2d_Line(gp_Pnt2d(two_pi, vp), gp_Dir2d(-1.0, 0.0))
        else:
            pcd = Geom2d_Line(gp_Pnt2d(0.0, vp), gp_Dir2d(1.0, 0.0))
        self.B.UpdateEdge(deg, pcd, face, tol)
        self.B.Range(deg, face, 0.0, two_pi)
        self.B.Range(deg, 0.0, two_pi)
        w = TopoDS_Wire()
        self.B.MakeWire(w)
        for x in self._loop_edges_from(nat[A], s):
            self.B.Add(w, x)
        if north:
            self.B.Add(w, seam)
            self.B.Add(w, deg)
            self.B.Add(w, seam.Reversed())
        else:
            self.B.Add(w, seam.Reversed())
            self.B.Add(w, deg)
            self.B.Add(w, seam)
        w.Closed(True)
        return w

    def _seam_wire(self, r, face, nat, A, Bl, sA, sB):
        wA = self._winding(r, nat[A])
        wB = self._winding(r, nat[Bl])
        if wA > 0 > wB:
            bot, top, sbot, stop = A, Bl, sA, sB
        elif wB > 0 > wA:
            bot, top, sbot, stop = Bl, A, sB, sA
        else:
            raise RuntimeError("inconsistent wrapping loops")
        vb = self._vparam(r, self.V[sbot])
        vt = self._vparam(r, self.V[stop])
        if not vt > vb:
            raise RuntimeError("seam loops in unexpected order")
        e = self._seam_edge(r, face, sbot, stop, vb, vt)
        w = TopoDS_Wire()
        self.B.MakeWire(w)
        for x in self._loop_edges_from(nat[bot], sbot):
            self.B.Add(w, x)
        self.B.Add(w, e)
        for x in self._loop_edges_from(nat[top], stop):
            self.B.Add(w, x)
        self.B.Add(w, e.Reversed())
        w.Closed(True)
        return w

    def _seam_edge(self, r, face, sbot, stop, vb, vt):
        from OCP.Geom import Geom_Circle, Geom_Line

        prim = self.regions[r].prim
        fr = self.frames[r]
        sp = self.surf_params[r]
        tol = max(self.tol, MIN_TOL)
        two_pi = 2 * np.pi
        if prim.kind == "cylinder":
            p0 = prim.a + prim.r * fr.x + vb * prim.d
            p1 = prim.a + prim.r * fr.x + vt * prim.d
            geom = Geom_Line(_pnt(p0), _dir(prim.d))
            t0, t1 = 0.0, vt - vb
            pc1 = Geom2d_Line(gp_Pnt2d(two_pi, vb), gp_Dir2d(0.0, 1.0))
            pc2 = Geom2d_Line(gp_Pnt2d(0.0, vb), gp_Dir2d(0.0, 1.0))
        elif prim.kind == "cone":
            loc, R0 = sp["loc"], sp["R0"]
            sa, ca = np.sin(prim.alpha), np.cos(prim.alpha)
            g = sa * fr.x + ca * prim.d
            p0 = loc + (R0 + vb * sa) * fr.x + vb * ca * prim.d
            p1 = loc + (R0 + vt * sa) * fr.x + vt * ca * prim.d
            geom = Geom_Line(_pnt(p0), _dir(g))
            t0, t1 = 0.0, vt - vb
            pc1 = Geom2d_Line(gp_Pnt2d(two_pi, vb), gp_Dir2d(0.0, 1.0))
            pc2 = Geom2d_Line(gp_Pnt2d(0.0, vb), gp_Dir2d(0.0, 1.0))
        elif prim.kind == "sphere":
            geom, p0, p1 = self._meridian(r, vb, vt)
            t0, t1 = vb, vt
            pc1 = Geom2d_Line(gp_Pnt2d(two_pi, 0.0), gp_Dir2d(0.0, 1.0))
            pc2 = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(0.0, 1.0))
        elif prim.kind == "torus":
            center = prim.c + prim.R * fr.x
            n = np.cross(fr.x, prim.d)
            geom = Geom_Circle(gp_Ax2(_pnt(center), _dir(n), _dir(fr.x)), prim.r)
            p0 = center + prim.r * (np.cos(vb) * fr.x + np.sin(vb) * prim.d)
            p1 = center + prim.r * (np.cos(vt) * fr.x + np.sin(vt) * prim.d)
            t0, t1 = vb, vt
            pc1 = Geom2d_Line(gp_Pnt2d(two_pi, 0.0), gp_Dir2d(0.0, 1.0))
            pc2 = Geom2d_Line(gp_Pnt2d(0.0, 0.0), gp_Dir2d(0.0, 1.0))
        else:
            raise ValueError(prim.kind)
        for vi, pe in ((sbot, p0), (stop, p1)):
            d = float(np.linalg.norm(self.V[vi] - pe))
            self.vtol[vi] = max(self.vtol[vi], d)
            self.B.UpdateVertex(self.vertex(vi), max(MIN_TOL, 1.5 * self.vtol[vi] + 1e-9))
        e = TopoDS_Edge()
        self.B.MakeEdge(e, geom, tol)
        self.B.Add(e, self.vertex(sbot))
        self.B.Add(e, self.vertex(stop).Reversed())
        self.B.Range(e, t0, t1)
        self.B.UpdateEdge(e, pc1, pc2, face, tol)
        self.B.Range(e, face, t0, t1)
        self.B.SameParameter(e, True)
        self.B.SameRange(e, True)
        return e

    def _make_surface(self, reg, r=None):
        prim = reg.prim
        P = self.V[np.unique(self.F_of(reg))]
        fr = self.region_frame(r) if r is not None else None
        sp = {}
        if prim.kind == "cylinder":
            x = fr.x if fr is not None else self._seam_dir(P, prim.a, prim.d)
            ax3 = gp_Ax3(_pnt(prim.a), _dir(prim.d), _dir(x))
            surf = Geom_CylindricalSurface(ax3, prim.r)
        elif prim.kind == "cone":
            h = (P - prim.apex) @ prim.d
            h0 = float(np.clip(h.mean(), 1e-6, None))
            loc = prim.apex + h0 * prim.d
            x = fr.x if fr is not None else self._seam_dir(P, loc, prim.d)
            ax3 = gp_Ax3(_pnt(loc), _dir(prim.d), _dir(x))
            R0 = h0 * np.tan(prim.alpha)
            surf = Geom_ConicalSurface(ax3, prim.alpha, R0)
            sp = {"loc": loc, "R0": R0}
        elif prim.kind == "sphere":
            if fr is not None:
                ax3 = gp_Ax3(_pnt(prim.c), _dir(fr.d), _dir(fr.x))
            else:
                m = unit((P - prim.c).mean(0)) if len(P) else np.array([0, 0, 1.0])
                if not np.any(m):
                    m = np.array([0, 0, 1.0])
                u, v = perp_basis(m)
                ax3 = gp_Ax3(_pnt(prim.c), _dir(u), _dir(-m))
            surf = Geom_SphericalSurface(ax3, prim.r)
        elif prim.kind == "torus":
            x = fr.x if fr is not None else self._seam_dir(P, prim.c, prim.d)
            ax3 = gp_Ax3(_pnt(prim.c), _dir(prim.d), _dir(x))
            surf = Geom_ToroidalSurface(ax3, prim.R, prim.r)
            # branch cut for the minor angle: middle of the largest gap
            rel = P - prim.c
            hh = rel @ prim.d
            rho = np.linalg.norm(rel - np.outer(hh, prim.d), axis=1)
            vv = np.sort(np.arctan2(hh, rho - prim.R))
            gaps = np.diff(np.r_[vv, vv[0] + 2 * np.pi])
            k = int(np.argmax(gaps))
            sp = {"vcut": float(vv[k] + gaps[k] / 2 - 2 * np.pi)}
        else:
            raise ValueError(prim.kind)
        if r is not None:
            self.surf_params[r] = sp
        return surf

    def F_of(self, reg):
        return self.rg.F[reg.faces]

    @staticmethod
    def _seam_dir(P, origin, d):
        rel = P - origin
        rad = rel - np.outer(rel @ d, d)
        u, v = perp_basis(d)
        ang = np.arctan2(rad @ v, rad @ u)
        # place the seam in the middle of the largest angular gap
        a = np.sort(ang)
        gaps = np.diff(np.r_[a, a[0] + 2 * np.pi])
        k = int(np.argmax(gaps))
        mid = a[k] + gaps[k] / 2
        return np.cos(mid) * u + np.sin(mid) * v


PERIODIC = ("cylinder", "cone", "torus")
_PLURAL = {"plane": "planes", "cylinder": "cylinders", "cone": "cones", "sphere": "spheres", "torus": "tori", "freeform": "freeform patches"}


def _face_area(face):
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    try:
        g = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, g, 1e-4)
        return abs(float(g.Mass()))
    except Exception:
        return 0.0


def _wrap(a):
    """Wrap angle(s) to (-pi, pi]."""
    return (np.asarray(a) + np.pi) % (2 * np.pi) - np.pi


def _best_gap(forb):
    if not forb:
        return 0.0
    edges = sorted((lo % (2 * np.pi), (lo % (2 * np.pi)) + (hi - lo)) for lo, hi in forb)
    best, best_mid = -1.0, 0.0
    for i, (lo, hi) in enumerate(edges):
        nlo = edges[(i + 1) % len(edges)][0] + (2 * np.pi if i + 1 == len(edges) else 0.0)
        gap = nlo - hi
        if gap > best:
            best, best_mid = gap, hi + gap / 2
    return float(_wrap(best_mid))


def _reverse_loop(loop):
    return [(cid, not fwd) for cid, fwd in reversed(loop)]


class _Frame:
    def __init__(self, o, d, x):
        self.o = np.asarray(o, dtype=np.float64)
        self.d = unit(np.asarray(d, dtype=np.float64))
        x = np.asarray(x, dtype=np.float64)
        self.x = unit(x - (x @ self.d) * self.d)
        self.y = np.cross(self.d, self.x)

    def angle(self, P):
        rel = np.atleast_2d(P) - self.o
        return np.arctan2(rel @ self.y, rel @ self.x)

    def rotated(self, a):
        return _Frame(self.o, self.d, np.cos(a) * self.x + np.sin(a) * self.y)

    def at_angle(self, p, a):
        """Rotate point p about the axis so that its angle is exactly a
        (it stays on any surface of revolution about this axis)."""
        rel = np.asarray(p, dtype=np.float64) - self.o
        h = rel @ self.d
        rad = rel - h * self.d
        rho = float(np.linalg.norm(rad))
        return self.o + h * self.d + rho * (np.cos(a) * self.x + np.sin(a) * self.y)


class _SurfAdapter:
    """Gives the base assembler a distance() / normal for any region."""

    def __init__(self, reg):
        self.reg = reg
        self.kind = reg.kind
        if reg.kind == "plane":
            self.origin = reg.prim.origin
            self.normal = reg.prim.n * reg.sense

    def distance(self, P):
        return self.reg.prim.distance(np.asarray(P))


def _polyline_distance(S, P):
    """Max distance from sample points S to the polyline P."""
    A = P[:-1]
    B = P[1:]
    AB = B - A
    L2 = np.maximum((AB * AB).sum(1), 1e-30)
    best = np.full(len(S), np.inf)
    for i0 in range(0, len(A), 256):
        a = A[i0:i0 + 256]
        ab = AB[i0:i0 + 256]
        l2 = L2[i0:i0 + 256]
        t = np.clip(((S[:, None, :] - a[None]) * ab[None]).sum(2) / l2[None], 0, 1)
        proj = a[None] + t[..., None] * ab[None]
        d = np.linalg.norm(S[:, None, :] - proj, axis=2).min(1)
        best = np.minimum(best, d)
    return float(best.max())


def _polyline_arc_positions(S, P):
    """Arc-length position along polyline P of each sample's nearest point."""
    A = P[:-1]
    AB = P[1:] - A
    L2 = np.maximum((AB * AB).sum(1), 1e-30)
    seg_len = np.sqrt(L2)
    cum = np.r_[0.0, np.cumsum(seg_len)]
    t = np.clip(((S[:, None, :] - A[None]) * AB[None]).sum(2) / L2[None], 0, 1)
    proj = A[None] + t[..., None] * AB[None]
    d = np.linalg.norm(S[:, None, :] - proj, axis=2)
    i = np.argmin(d, axis=1)
    return cum[i] + t[np.arange(len(S)), i] * seg_len[i], cum[-1]


def _monotonic_along(S, P, slack):
    """True if samples S advance along polyline P without going backwards
    (an overshoot that doubles back along the polyline is invisible to a
    plain distance check but breaks faces on periodic surfaces)."""
    if len(P) < 3 or len(S) < 4:
        return True
    pos, total = _polyline_arc_positions(S[1:-1], P)
    return bool((np.diff(pos) >= -slack).all())


def _sample_curve(curve, n):
    a, b = curve.FirstParameter(), curve.LastParameter()
    out = np.empty((n, 3))
    for i, t in enumerate(np.linspace(a, b, n)):
        p = curve.Value(t)
        out[i] = (p.X(), p.Y(), p.Z())
    return out


def fit_chain_curve(P, tol):
    """Best smooth curve through a chain of points: chord-length cubic
    approximation, then cubic interpolation (periodic for closed chains),
    then an exact linear B-spline. Candidates must stay within ``tol`` of
    the polyline and advance monotonically along it.
    Returns (curve, deviation)."""
    from OCP.Approx import Approx_ChordLength
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.collections import HArray1_gp_Pnt

    P = np.asarray(P, dtype=np.float64)
    keep = np.r_[True, np.linalg.norm(np.diff(P, axis=0), axis=1) > 1e-9]
    P = P[keep]
    if len(P) < 2:
        return None, 0.0
    closed = len(P) > 3 and np.linalg.norm(P[0] - P[-1]) < 1e-9
    Q = P
    if len(Q) > 400:
        idx = np.unique(np.r_[np.linspace(0, len(Q) - 1, 400).astype(int), len(Q) - 1])
        Q = Q[idx]
    nsamp = max(40, 4 * len(Q))
    slack = 0.25 * tol
    cands = []
    arr = Array1_gp_Pnt(1, len(Q))
    for i, p in enumerate(Q):
        arr.SetValue(i + 1, _pnt(p))
    if closed and len(Q) >= 4:
        try:
            pts = Q[:-1]
            h = HArray1_gp_Pnt(1, len(pts))
            for i, p in enumerate(pts):
                h.SetValue(i + 1, _pnt(p))
            it = GeomAPI_Interpolate(h, True, 1e-9)
            it.Perform()
            if it.IsDone():
                cands.append(it.Curve())
        except Exception:
            pass
    if len(Q) >= 3 and not closed:
        try:
            ap = GeomAPI_PointsToBSpline(arr, Approx_ChordLength, 3, 3, GeomAbs_C2, max(tol * 0.25, 1e-7))
            if ap.IsDone():
                cands.append(ap.Curve())
        except Exception:
            pass
        try:
            h = HArray1_gp_Pnt(1, len(Q))
            for i, p in enumerate(Q):
                h.SetValue(i + 1, _pnt(p))
            it = GeomAPI_Interpolate(h, False, 1e-9)
            it.Perform()
            if it.IsDone():
                cands.append(it.Curve())
        except Exception:
            pass
    for c in cands:
        S = _sample_curve(c, nsamp)
        dev = _polyline_distance(S, P)
        if dev <= tol and _monotonic_along(S, P, slack):
            return c, dev
    # exact fallback: one linear B-spline edge through every point
    from OCP.Geom import Geom_BSplineCurve
    from OCP.collections import Array1_double, Array1_int

    n = len(Q)
    knots = Array1_double(1, n)
    mults = Array1_int(1, n)
    tpar = np.r_[0, np.cumsum(np.linalg.norm(np.diff(Q, axis=0), axis=1))]
    for i in range(n):
        knots.SetValue(i + 1, float(tpar[i]))
        mults.SetValue(i + 1, 2 if i in (0, n - 1) else 1)
    try:
        c = Geom_BSplineCurve(arr, knots, mults, 1)
    except Exception:
        return None, 0.0
    return c, (_polyline_distance(Q, P) if len(Q) != len(P) else 0.0)


def _fit_circle_3d(P):
    if len(P) < 3:
        return None
    c = P.mean(0)
    _, s, vt = np.linalg.svd(P - c, full_matrices=False)
    n = vt[2]
    u, v = vt[0], vt[1]
    Q = np.stack([(P - c) @ u, (P - c) @ v], axis=1)
    from .fitting import fit_circle_2d

    fc = fit_circle_2d(Q)
    if fc is None:
        return None
    c2, r = fc
    center = c + c2[0] * u + c2[1] * v
    rel = P - center
    radial = np.linalg.norm(rel - np.outer(rel @ n, n), axis=1)
    err = max(float(np.abs(radial - r).max()), float(np.abs(rel @ n).max()))
    return center, n, r, err


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------


def segment_shell(shell, tol, report=NULL):
    seg = Segmenter(shell.V, shell.F, shell.topo, tol, report)
    regions = seg.run()
    return seg, regions


def build_cad_shell(shell, tol, report=NULL, max_rounds=6, fit_rounds=3, detail=0.6, seg=None):
    """Segment + build. Analytic faces that cannot be built validly, or that
    don't follow the mesh (checked in the first ``fit_rounds`` rounds), are
    replaced by fitted freeform (B-spline) faces and the shell is rebuilt;
    freeform faces that fail are split into smaller cells. The later rounds
    only clean up faces that are still invalid (often tiny slivers left by
    the splitting, which go to a neighbouring face)."""
    from OCP.BRepCheck import BRepCheck_Analyzer

    if seg is None or abs(seg.tol - tol) > 1e-12 * max(1.0, tol):
        seg = Segmenter(shell.V, shell.F, shell.topo, tol, report)
        seg.detail = detail
        regions = seg.run()
    else:
        regions = seg.regions
    labels = seg.label.copy()
    replaced = 0
    inaccurate = 0

    def build(labels, regions):
        labels, regions = split_pinched_regions(seg, labels, regions)
        rg = RegionGraph(shell.V, shell.F, labels, shell.topo)
        asm = CADAssembler(shell.V, rg, regions, tol, report)
        faces = asm.build(lambda f: report.progress(0.6 + 0.35 * f))
        _settle_tolerances(asm.shell_from_faces(faces), tol)
        bad = set(r for r, _ in asm.failed_regions)
        for r, f in asm.region_faces.items():
            if not BRepCheck_Analyzer(f).IsValid():
                bad.add(r)
        return labels, regions, asm, faces, bad

    for rnd in range(max_rounds):
        labels, regions, asm, faces, bad = build(labels, regions)
        if rnd < min(fit_rounds, max_rounds - 1):
            # valid faces that still don't follow their part of the mesh (a
            # surface that bulges between the mesh's vertices, a face whose
            # edges tear it away from the mesh) are replaced the same way
            off = _inaccurate_regions(seg, labels, asm, tol, skip=bad) - bad
            if off:
                report.log(f"{len(off)} surface(s) don't follow the mesh closely enough and are fitted again")
                inaccurate += len(off)
                bad |= off
        else:
            off = set()
        if not bad or rnd == max_rounds - 1:
            break
        replaced += len(bad)
        # a freeform face that still strays from the mesh becomes flat faces
        # (when its triangles are few, and big enough to be faces of their own)
        flat = {r for r in off if r < len(regions) and regions[r].kind == "freeform"
                and _facets_ok(seg, regions[r].faces)}
        labels, regions = _demote(seg, labels, regions, bad - flat, split_more=rnd > 0, facet=flat)
    # Splitting freeform faces can leave slivers too small to be valid faces;
    # those still broken at the end go to a neighbouring face (no new faces
    # are made, so this can't leave new slivers) and the shell is built once more.
    tiny = {r for r in bad if r < len(regions) and _too_small_for_face(seg, labels, regions[r].faces)}
    if tiny:
        labels2, regions2 = _demote(seg, labels, regions, tiny)
        labels2, regions2, asm2, faces2, bad2 = build(labels2, regions2)
        if len(bad2) < len(bad):
            labels, regions, asm, faces, bad = labels2, regions2, asm2, faces2, bad2
    if replaced:
        report.log(f"{replaced} surface(s) rebuilt as freeform (B-spline) faces")
    bad_faces = [f for f in faces if not BRepCheck_Analyzer(f).IsValid()]
    if bad_faces:
        faces = _heal_faces(asm, faces, bad_faces, tol, report)
    return asm, faces, regions


def _inaccurate_regions(seg, labels, asm, tol, share=0.05, count=3, far_count=None, skip=()):
    """Regions whose built face strays from the mesh triangles it stands
    for, found by measuring both ways (see fidelity.py): points of the face
    far from the mesh (bulges, flaps, torn edges) and points of the region's
    triangles far from the shape (dents, missing material).

    Distances are in units of the tolerance the result is judged by
    (fidelity.Verdict): ``tol``, or the mesh's own chordal deflection when
    it is coarser (a true cylinder stands off the flat sides of a coarse
    mesh's 16-gon, and that is right). A region is inaccurate when at least
    ``count`` of its points are beyond Verdict.P999_K tolerances (a bulge
    or a flap), or more than ``share`` of them beyond Verdict.P99_K. When the
    shell as a whole would still fail the final check (too many far points
    spread thinly over many faces), the faces holding most of them count
    too, until it would pass: fixing those beats losing the whole CAD
    result, and the rest stay as they are."""
    from .fidelity import Surface, Verdict, mesh_surface, sample, tessellate_faces

    # (faces already known to be broken are replaced anyway; meshing them
    # can fail)
    items = [(r, f) for r, f in asm.region_faces.items() if r not in skip]
    if not items:
        return set()
    rng = np.random.default_rng(0)
    V, F = seg.V, seg.F
    SV, SF, owner = tessellate_faces([f for _, f in items], tol * 0.25)
    if len(SF) == 0:
        return set()
    own_region = np.array([r for r, _ in items], dtype=np.int64)[owner]
    mesh, _, spacing = mesh_surface(V, F)
    # (the tolerance the result is judged by: see fidelity.mesh_kind)
    allow = max(tol, mesh.deflection, mesh.noise)
    shape = Surface(SV, SF, max(spacing, tol * 0.5), rng)
    n = int(np.clip(20 * len(F), 20_000, 150_000))
    # face -> mesh
    Q, qt = sample(SV, SF, n, rng)
    dq, _ = mesh.nearest(Q, exact_above=0.5 * allow)
    # mesh -> shape (by the region the triangle belongs to)
    P, pt = sample(V, F, n, rng)
    dp, _ = shape.nearest(P, exact_above=0.5 * allow)
    near, far = Verdict.P99_K * allow, Verdict.P999_K * allow
    out = set()
    for reg_of, d in ((own_region[qt], dq), (labels[pt], dp)):
        ok = reg_of >= 0
        reg_of, d = reg_of[ok], d[ok]
        if not len(d):
            continue
        cnt = np.bincount(reg_of)
        n_near = np.bincount(reg_of, weights=(d > near).astype(float), minlength=len(cnt))
        n_far = np.bincount(reg_of, weights=(d > far).astype(float), minlength=len(cnt))
        out |= {int(r) for r in np.nonzero((n_far >= (far_count or count)) | (n_near > np.maximum(share * cnt, count)))[0]}
        # the whole shell: at most 1% of the points beyond P99_K, 0.1%
        # beyond P999_K (with a little margin)
        for nk, limit in ((n_near, 0.008 * len(d)), (n_far, 0.0008 * len(d))):
            left = nk.sum() - sum(nk[r] for r in out if r < len(nk))
            for r in np.argsort(-nk):
                if left <= limit or nk[r] <= 0:
                    break
                if int(r) not in out:
                    out.add(int(r))
                    left -= nk[r]
    return out


def _heal_faces(asm, faces, bad_faces, tol, report):
    """Repair the few faces that are still invalid with OpenCASCADE's face
    healer. A shared ReShape context propagates any edge it replaces to the
    neighbouring faces, so the shell stays watertight."""
    try:
        from OCP.BRepCheck import BRepCheck_Analyzer
        from OCP.ShapeBuild import ShapeBuild_ReShape
        from OCP.ShapeFix import ShapeFix_Face
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer

        sh = asm.shell_from_faces(faces)
        ctx = ShapeBuild_ReShape()
        for f in bad_faces:
            sff = ShapeFix_Face(f)
            sff.SetContext(ctx)
            sff.SetPrecision(max(tol * 0.1, 1e-7))
            sff.SetMaxTolerance(max(tol * 5, 1e-4))
            sff.Perform()
            fixed = sff.Face()
            if not fixed.IsSame(f):
                ctx.Replace(f, fixed)
        healed = ctx.Apply(sh)
        out = []
        exp = TopExp_Explorer(healed, TopAbs_FACE)
        while exp.More():
            out.append(TopoDS.Face(exp.Current()))
            exp.Next()
        still = sum(1 for f in out if not BRepCheck_Analyzer(f).IsValid())
        if len(out) >= len(faces) - len(bad_faces) and still < len(bad_faces):
            report.log(f"Repaired {len(bad_faces) - still} of {len(bad_faces)} remaining face(s)")
            return out
    except Exception as e:
        report.log(f"Automatic face repair was skipped ({e})")
    return faces


def split_pinched_regions(seg, labels, regions, max_passes=4):
    """A region whose triangles meet at a vertex in two separate fans (a
    hole touching the outer boundary at one point, two holes touching) is
    cut in two, so that no face's boundary touches itself. Such faces are
    valid in memory but STEP readers tend to mangle them."""
    from .freeform import seeded_partition

    F = seg.F
    nf = len(F)
    twin = seg.topo.twin
    k = np.arange(3 * nf)
    f = k // 3
    v = F.reshape(-1)
    h_in = 3 * f + (k % 3 + 2) % 3
    t = twin[h_in]
    g = np.where(t >= 0, t // 3, 0)
    cen = None
    labels = labels.copy()
    regions = list(regions)
    for _ in range(max_passes):
        lf = labels[f]
        # first triangle of each fan around a vertex (rotation order)
        start = (t < 0) | (labels[g] != lf)
        key = v[start].astype(np.int64) * (len(regions) + 1) + lf[start]
        ks, counts = np.unique(key, return_counts=True)
        pinch = ks[counts >= 2]
        if not len(pinch):
            break
        if cen is None:
            cen = seg.V[F].mean(1)
        changed = False
        done = set()
        for pk in pinch:
            vv, ll = int(pk // (len(regions) + 1)), int(pk % (len(regions) + 1))
            if ll in done:
                continue
            seeds = np.unique(f[start & (v == vv) & (lf == ll)])
            if len(seeds) < 2:
                continue
            done.add(ll)
            r = regions[ll]
            parts = seeded_partition(r.faces, seeds[:2], cen, seg.nb)
            if len(parts) < 2 or not len(parts[0]) or not len(parts[1]):
                continue
            a = Region(r.prim, parts[0], r.sense, r.weak)
            b = Region(r.prim, parts[1], r.sense, r.weak)
            a.facet = b.facet = r.facet
            regions[ll] = a
            labels[parts[1]] = len(regions)
            regions.append(b)
            changed = True
        if not changed:
            break
    return labels, regions


def _settle_tolerances(shape, tol):
    """Let OpenCASCADE measure how far each edge really is from its faces
    and raise tolerances to match (standard healing step). On some short
    arcs this over-estimates in memory; STEP files carry no tolerances, so
    the written geometry is unaffected (every result is re-read in tests)."""
    try:
        BRepLib.SameParameter_s(shape, max(tol * 0.01, 1e-7), True)
        BRepLib.UpdateTolerances_s(shape, False)
    except Exception:
        pass


def _demote(seg, labels, regions, bad, split_more=False, facet=()):
    """Replace regions in ``bad`` by freeform B-spline cells. Tiny ones
    (about the size of the tolerance, too small for a valid face of their
    own) hand their triangles to the neighbouring surface that fits them
    best instead. Regions in ``facet`` (freeform cells that still don't
    follow the mesh) become flat faces made from their own triangles, which
    follow it exactly."""
    bad = set(bad) | set(facet)
    facet = set(facet)
    tiny = {r for r in bad if _too_small_for_face(seg, labels, regions[r].faces)}
    keep = [r for r in range(len(regions)) if r not in bad]
    new_regions = [regions[r] for r in keep]
    new_labels = np.full_like(labels, -1)
    for i, r in enumerate(keep):
        new_labels[regions[r].faces] = i
    pending = [int(f) for r in tiny for f in regions[r].faces]
    added = {}
    for _ in range(8):
        left = []
        for f in pending:
            best, bd = -1, np.inf
            for g in seg.nb[f]:
                rr = new_labels[g] if g >= 0 else -1
                if rr < 0 or rr == best:
                    continue
                d = float(new_regions[rr].prim.distance(seg.V[seg.F[f]]).max())
                if d < bd:
                    best, bd = rr, d
            if best >= 0:
                new_labels[f] = best
                added.setdefault(best, []).append(f)
            else:
                left.append(f)
        if len(left) == len(pending):
            break
        pending = left
    for rr, fs in added.items():
        old = new_regions[rr]
        reg = Region(old.prim, np.concatenate([old.faces, np.asarray(fs, dtype=np.int64)]), old.sense, old.weak)
        reg.facet = old.facet
        new_regions[rr] = reg
    rest = [r for r in bad if r not in tiny]
    if pending:
        rest.append(None)
    for r in rest:
        faces = regions[r].faces if r is not None else np.asarray(pending, dtype=np.int64)
        if r in facet:
            for cell in _flat_facets(seg, faces):
                new_labels[cell.faces] = len(new_regions)
                new_regions.append(cell)
            continue
        cells = fit_freeform_cells(seg, faces)
        if split_more and len(cells) == 1 and len(faces) > 2:
            from .freeform import _geodesic_split

            cen = seg.V[seg.F].mean(1)
            cells = []
            for p in _geodesic_split(faces, cen, seg.nb, 2):
                cells.extend(fit_freeform_cells(seg, p))
        for cell in cells:
            new_labels[cell.faces] = len(new_regions)
            new_regions.append(cell)
    return new_labels, new_regions


def _facets_ok(seg, faces, max_tris=2000, area_k=25.0):
    """Whether a region can become flat faces from its own triangles: not
    too many of them, and on average big enough that a face of one or two
    triangles isn't a sliver (see _too_small_for_face)."""
    faces = np.asarray(faces)
    return 0 < len(faces) <= max_tris and float(seg.fa[faces].mean()) >= area_k * seg.tol ** 2


def _flat_facets(seg, faces, max_deg=0.5):
    """The triangles of ``faces`` as flat faces: neighbouring triangles that
    lie in one plane (within ``max_deg`` and a tenth of the tolerance)
    share a face. Exactly the mesh, so always a faithful last resort."""
    from .fitting import Plane

    faces = np.asarray(faces, dtype=np.int64)
    member = np.zeros(len(seg.F), dtype=bool)
    member[faces] = True
    group = np.full(len(seg.F), -1, dtype=np.int64)
    cos_max = np.cos(np.radians(max_deg))
    out = []
    for f0 in faces:
        if group[f0] >= 0:
            continue
        gid = len(out)
        group[f0] = gid
        stack, comp = [int(f0)], [int(f0)]
        n0 = seg.fn[f0]
        c0 = seg.V[seg.F[f0]].mean(0)
        while stack:
            f = stack.pop()
            for g in seg.nb[f]:
                if g < 0 or not member[g] or group[g] >= 0:
                    continue
                if seg.fn[g] @ n0 < cos_max:
                    continue
                if np.abs((seg.V[seg.F[g]] - c0) @ n0).max() > 0.1 * seg.tol:
                    continue
                group[g] = gid
                stack.append(int(g))
                comp.append(int(g))
        comp = np.array(comp, dtype=np.int64)
        P = seg.V[np.unique(seg.F[comp])]
        n = (seg.fn[comp] * seg.fa[comp, None]).sum(0)
        out.append(Region(Plane(P.mean(0), n if np.linalg.norm(n) > 0 else n0), comp, 1.0, False))
    return out


def _too_small_for_face(seg, labels, faces, area_k=25.0, width_k=6.0):
    """Whether a region is too small or too thin to become a valid face of
    its own: about the size of the tolerance, or a sliver whose sides run
    within a few tolerances of each other (their tolerance zones overlap
    and the boundary counts as self-intersecting)."""
    faces = np.asarray(faces)
    area = float(seg.fa[faces].sum())
    tol = seg.tol
    if area <= area_k * tol ** 2:
        return True
    if len(faces) > 64:
        return False
    he = (3 * faces[:, None] + np.arange(3)[None]).reshape(-1)
    twin = seg.topo.twin[he]
    lab = labels[faces[0]]
    outer = (twin < 0) | (labels[np.maximum(twin, 0) // 3] != lab)
    a = seg.V[seg.topo.he_from[he[outer]]]
    b = seg.V[seg.topo.he_to[he[outer]]]
    perim = float(np.linalg.norm(b - a, axis=1).sum())
    return perim > 0 and 2.0 * area / perim <= width_k * tol


def _components(faces, nb):
    faces = list(map(int, faces))
    member = set(faces)
    seen = set()
    out = []
    for f in faces:
        if f in seen:
            continue
        comp = [f]
        seen.add(f)
        stack = [f]
        while stack:
            x = stack.pop()
            for y in nb[x]:
                y = int(y)
                if y >= 0 and y in member and y not in seen:
                    seen.add(y)
                    comp.append(y)
                    stack.append(y)
        out.append(np.array(comp, dtype=np.int64))
    return out


def build_freeform_shell(shell, tol, report=NULL):
    """Every face a fitted B-spline cell (no analytic recognition)."""
    seg = Segmenter(shell.V, shell.F, shell.topo, tol, report)
    regions = fit_freeform_cells(seg, np.arange(len(shell.F)))
    labels = np.empty(len(shell.F), dtype=np.int64)
    for i, r in enumerate(regions):
        labels[r.faces] = i
    rg = RegionGraph(shell.V, shell.F, labels, shell.topo)
    asm = CADAssembler(shell.V, rg, regions, tol, report)
    faces = asm.build()
    sh = asm.shell_from_faces(faces)
    _settle_tolerances(sh, tol)
    return sh


def summarize(regions):
    kinds = {}
    for r in regions:
        kinds[r.kind] = kinds.get(r.kind, 0) + 1
    return kinds


def convert_body_cad(B, body, tol, opts, report=NULL, segs=None):
    from .pipeline import fix_shape, sew_faces, shells_to_solid
    from . import stepio

    built, flags, all_faces = [], [], []
    failed = False
    kinds = {}
    for shell in body:
        seg = segs.get(id(shell)) if segs else None
        asm, faces, regions = build_cad_shell(shell, tol, report, detail=opts.quality, seg=seg)
        for k, v in summarize(regions).items():
            kinds[k] = kinds.get(k, 0) + v
        if asm.failed_regions:
            failed = True
            report.warn(f"{len(asm.failed_regions)} surface(s) couldn't be built and were left out, so the result may have gaps.")
        all_faces.extend(faces)
        built.append(asm.shell_from_faces(faces))
        flags.append(shell.closed)
    report.log("Found " + ", ".join(f"{v} {_PLURAL.get(k, k) if v != 1 else k}" for k, v in sorted(kinds.items())))
    if failed:
        sewn = sew_faces(all_faces, tol * 2)
        parts = shells_to_solid(sewn)
        shape = parts[0] if len(parts) == 1 else make_compound(parts)
    elif all(flags):
        from .brep import make_solid_checked

        shape = make_solid_checked(B, built, report)
    else:
        shape = make_compound(built)
    if not stepio.is_valid(shape):
        shape2 = fix_shape(shape, tol)
        if stepio.is_valid(shape2):
            return shape2
        report.warn("The CAD model still has small errors after automatic repair.")
        return shape2
    return shape
