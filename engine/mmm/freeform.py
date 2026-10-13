"""Freeform surfaces: fitted, trimmed B-spline patches.

Parts of a mesh that are not planes, cylinders, cones, spheres or tori are
split into "cells" that are

  * topological disks (one boundary loop), and
  * height fields over their average plane (every triangle faces roughly the
    same way, so projecting onto that plane is one-to-one).

Each cell gets a bicubic B-spline surface fitted by penalised least squares
over the projected (u, v) coordinates. The surface extends a little beyond
the cell and is trimmed by the cell's boundary edges, exactly like a
reverse-engineered CAD surface.
"""
import heapq

import numpy as np
from scipy import sparse
from scipy.interpolate import BSpline
from scipy.sparse.linalg import lsqr, spsolve

from OCP.Geom import Geom_BSplineSurface
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt
from OCP.collections import Array1_double, Array1_int, Array2_gp_Pnt

from .fitting import perp_basis


# ----------------------------------------------------------------------------
# Cell splitting
# ----------------------------------------------------------------------------


def _cell_ok(V, F, faces, fn, fa, nb, max_angle_deg, max_faces):
    """Return (ok, normal) for a candidate cell."""
    if len(faces) > max_faces:
        return False, None
    n = (fn[faces] * fa[faces, None]).sum(0)
    ln = np.linalg.norm(n)
    if ln <= 0:
        return False, None
    n /= ln
    good = fa[faces] > 0
    if good.any() and (fn[faces][good] @ n).min() < np.cos(np.radians(max_angle_deg)):
        return False, n
    # projected triangles must keep their orientation (no fold-over)
    u, v = perp_basis(n)
    P = V[F[faces]]
    a = P[:, 1] - P[:, 0]
    b = P[:, 2] - P[:, 0]
    cross = (a @ u) * (b @ v) - (a @ v) * (b @ u)
    if (cross[good] <= 0).any():
        return False, n
    # disk topology: one boundary loop and Euler characteristic 1
    if not _is_disk(F, faces, nb):
        return False, n
    return True, n


def _is_disk(F, faces, nb):
    member = np.zeros(nb.shape[0], dtype=bool)
    member[faces] = True
    sub = F[faces]
    nvert = len(np.unique(sub))
    # edges: interior edges are shared by two member faces
    nbs = nb[faces]
    inner = (nbs >= 0) & member[np.maximum(nbs, 0)]
    n_inner_half = int(inner.sum())
    n_bnd = 3 * len(faces) - n_inner_half
    n_edges = n_inner_half // 2 + n_bnd
    chi = nvert - n_edges + len(faces)
    if chi != 1:
        return False
    # count boundary loops via boundary half-edges
    he_from = sub.reshape(-1)
    he_to = np.roll(sub, -1, axis=1).reshape(-1)
    bmask = ~inner.reshape(-1)
    bf = he_from[bmask]
    bt = he_to[bmask]
    if len(bf) == 0:
        return False  # closed surface
    nxt = {}
    for a, b in zip(bf.tolist(), bt.tolist()):
        if a in nxt:
            return False  # pinched boundary vertex
        nxt[a] = b
    start = bf[0]
    cur = nxt[start]
    steps = 1
    while cur != start and steps <= len(bf):
        cur = nxt.get(cur, None)
        if cur is None:
            return False
        steps += 1
    return steps == len(bf)


def _geodesic_split(faces, cen, nb, k=2):
    """Split ``faces`` into k parts with farthest point seeds and a multi
    source Dijkstra over face adjacency."""
    faces = np.asarray(faces)
    member = {int(f): i for i, f in enumerate(faces)}
    n = len(faces)

    def dijkstra(sources):
        dist = np.full(n, np.inf)
        owner = np.full(n, -1)
        heap = []
        for si, s in enumerate(sources):
            dist[s] = 0.0
            owner[s] = si
            heap.append((0.0, s))
        heapq.heapify(heap)
        while heap:
            d, i = heapq.heappop(heap)
            if d > dist[i]:
                continue
            f = faces[i]
            for g in nb[f]:
                j = member.get(int(g))
                if j is None:
                    continue
                nd = d + float(np.linalg.norm(cen[faces[j]] - cen[f]))
                if nd < dist[j]:
                    dist[j] = nd
                    owner[j] = owner[i]
                    heapq.heappush(heap, (nd, j))
        return dist, owner

    # farthest point sampling: first seed = farthest from an arbitrary face
    d0, _ = dijkstra([0])
    d0[~np.isfinite(d0)] = -1
    seeds = [int(np.argmax(d0))]
    for _ in range(k - 1):
        d, _ = dijkstra(seeds)
        d[~np.isfinite(d)] = -1
        seeds.append(int(np.argmax(d)))
    _, owner = dijkstra(seeds)
    parts = []
    for si in range(k):
        p = faces[owner == si]
        if len(p):
            parts.append(p)
    # faces unreachable (disconnected) go into their own parts
    rest = faces[owner < 0]
    if len(rest):
        parts.append(rest)
    return parts


def seeded_partition(faces, seeds, cen, nb):
    """Assign every face of ``faces`` to the nearest seed face (multi-source
    Dijkstra over face adjacency). Returns one face array per seed (faces
    no seed can reach join the first part)."""
    faces = np.asarray(faces)
    member = {int(f): i for i, f in enumerate(faces)}
    n = len(faces)
    dist = np.full(n, np.inf)
    owner = np.full(n, -1)
    heap = []
    for si, sf in enumerate(seeds):
        i = member.get(int(sf))
        if i is None:
            continue
        dist[i] = 0.0
        owner[i] = si
        heap.append((0.0, i))
    heapq.heapify(heap)
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i]:
            continue
        f = faces[i]
        for g in nb[f]:
            j = member.get(int(g))
            if j is None:
                continue
            nd = d + float(np.linalg.norm(cen[faces[j]] - cen[f]))
            if nd < dist[j]:
                dist[j] = nd
                owner[j] = owner[i]
                heapq.heappush(heap, (nd, j))
    owner[owner < 0] = 0
    return [faces[owner == si] for si in range(len(seeds))]


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


def merge_small_cells(V, F, cells, fn, fa, nb, min_faces=8, max_angle_deg=65.0, max_faces=6000):
    """Fold tiny cells into an adjacent cell when the union is still a
    valid height-field disk."""
    cells = [(np.asarray(c), n) for c, n in cells]
    changed = True
    while changed:
        changed = False
        owner = {}
        for i, (c, _) in enumerate(cells):
            for f in c:
                owner[int(f)] = i
        order = sorted(range(len(cells)), key=lambda i: len(cells[i][0]))
        for i in order:
            c, _ = cells[i]
            if c is None or len(c) >= min_faces:
                continue
            counts = {}
            for f in c:
                for g in nb[f]:
                    j = owner.get(int(g))
                    if j is not None and j != i and cells[j][0] is not None:
                        counts[j] = counts.get(j, 0) + 1
            for j, _ in sorted(counts.items(), key=lambda t: -t[1]):
                union = np.concatenate([cells[j][0], c])
                ok, n = _cell_ok(V, F, union, fn, fa, nb, max_angle_deg, max_faces)
                if ok:
                    cells[j] = (union, n)
                    cells[i] = (None, None)
                    for f in c:
                        owner[int(f)] = j
                    changed = True
                    break
        cells = [x for x in cells if x[0] is not None]
    return cells


def split_cells(V, F, faces, fn, fa, nb, max_angle_deg=65.0, max_faces=6000, max_depth=24):
    """Recursively bisect a face set into valid freeform cells.

    Returns a list of (faces, normal)."""
    cen = V[F].mean(1)
    out = []
    stack = [(np.asarray(faces), 0)]
    while stack:
        fs, depth = stack.pop()
        for comp in _components(fs, nb):
            ok, n = _cell_ok(V, F, comp, fn, fa, nb, max_angle_deg, max_faces)
            if ok:
                out.append((comp, n))
                continue
            if len(comp) <= 1 or depth >= max_depth:
                # give up splitting: accept a single triangle as a cell
                for f in comp:
                    nn = fn[f] if fa[f] > 0 else np.array([0, 0, 1.0])
                    out.append((np.array([f]), nn))
                continue
            parts = _geodesic_split(comp, cen, nb, 2)
            if len(parts) < 2:
                half = len(comp) // 2
                parts = [comp[:half], comp[half:]]
            for p in parts:
                stack.append((p, depth + 1))
    return merge_small_cells(V, F, out, fn, fa, nb, max_angle_deg=max_angle_deg, max_faces=max_faces)


# ----------------------------------------------------------------------------
# B-spline fitting
# ----------------------------------------------------------------------------


def _knots(n_ctrl, lo, hi, deg=3):
    inner = np.linspace(lo, hi, n_ctrl - deg + 1)
    return np.r_[[lo] * deg, inner, [hi] * deg]


def _design(u, t, deg=3):
    return BSpline.design_matrix(np.clip(u, t[0], t[-1] - 1e-12 * (t[-1] - t[0])), t, deg)


class FreeformSurface:
    kind = "freeform"
    rank = 5

    def __init__(self, origin, n, e1, e2, tu, tv, ctrl, dev):
        self.origin = origin
        self.n = n
        self.e1 = e1
        self.e2 = e2
        self.tu = tu
        self.tv = tv
        self.ctrl = ctrl  # (nu, nv, 3)
        self.dev = dev
        self._geom = None

    def uv(self, P):
        Q = np.asarray(P) - self.origin
        return Q @ self.e1, Q @ self.e2

    def evaluate(self, u, v):
        Bu = _design(np.atleast_1d(u), self.tu).toarray()
        Bv = _design(np.atleast_1d(v), self.tv).toarray()
        return np.einsum("pi,pj,ijk->pk", Bu, Bv, self.ctrl)

    def distance(self, P):
        """Distance via exact projection onto the B-spline surface."""
        P = np.atleast_2d(P)
        g = self.geom()
        out = np.zeros(len(P))
        for i, p in enumerate(P):
            pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(*map(float, p)), g)
            if pr.NbPoints():
                out[i] = pr.LowerDistance()
            else:
                u, v = self.uv(p[None])
                out[i] = float(np.linalg.norm(self.evaluate(u, v)[0] - p))
        return out

    def normal(self, P):
        return np.tile(self.n, (len(np.atleast_2d(P)), 1))

    def degenerate(self, ext):
        return False

    def same_as(self, o, tol, ext):
        return False

    def describe(self):
        return f"freeform {self.ctrl.shape[0]}x{self.ctrl.shape[1]}"

    def geom(self):
        if self._geom is None:
            nu, nv, _ = self.ctrl.shape
            poles = Array2_gp_Pnt(1, nu, 1, nv)
            for i in range(nu):
                for j in range(nv):
                    poles.SetValue(i + 1, j + 1, gp_Pnt(*map(float, self.ctrl[i, j])))
            ku, mu = _occ_knots(self.tu)
            kv, mv = _occ_knots(self.tv)
            self._geom = Geom_BSplineSurface(poles, ku, kv, mu, mv, 3, 3)
        return self._geom


def _occ_knots(t):
    vals, counts = np.unique(np.round(t, 12), return_counts=True)
    k = Array1_double(1, len(vals))
    m = Array1_int(1, len(vals))
    for i, (a, c) in enumerate(zip(vals, counts)):
        k.SetValue(i + 1, float(a))
        m.SetValue(i + 1, int(c))
    return k, m


def fit_cell(V, F, faces, n, tol, detail=0.6, max_ctrl=40):
    """Fit a trimmed bicubic B-spline to a height-field cell."""
    e1, e2 = perp_basis(n)
    vid = np.unique(F[faces])
    P = V[vid]
    # extra samples at triangle centroids and edge midpoints help coarse cells
    tri = V[F[faces]]
    samples = [P, tri.mean(1)]
    if len(faces) < 400:
        samples += [(tri[:, 0] + tri[:, 1]) / 2, (tri[:, 1] + tri[:, 2]) / 2, (tri[:, 2] + tri[:, 0]) / 2]
    S = np.vstack(samples)
    origin = P.mean(0)
    Q = S - origin
    u = Q @ e1
    v = Q @ e2
    ulo, uhi = u.min(), u.max()
    vlo, vhi = v.min(), v.max()
    du = max(uhi - ulo, 1e-9)
    dv = max(vhi - vlo, 1e-9)
    # extend the domain so trimming curves lie strictly inside; small cells
    # get a generous margin (their neighbours' edge curves can stray a bit)
    m = 0.06 if len(faces) >= 30 else 0.3
    ulo, uhi = ulo - m * du, uhi + m * du
    vlo, vhi = vlo - m * dv, vhi + m * dv
    du, dv = uhi - ulo, vhi - vlo
    # initial control grid from point density, aspect-aware
    npts = len(P)
    base = int(np.clip(np.sqrt(npts) * (0.35 + 0.5 * detail), 4, max_ctrl))
    aspect = du / dv
    nu = int(np.clip(round(base * np.sqrt(aspect)), 4, max_ctrl))
    nv = int(np.clip(round(base / np.sqrt(aspect)), 4, max_ctrl))
    # never more control points than the data can pin down (a thin cell
    # with a handful of triangles would otherwise ripple between them)
    cap = max(16, len(S) // 3)
    while nu * nv > cap and (nu > 4 or nv > 4):
        if nu >= nv and nu > 4:
            nu -= 1
        elif nv > 4:
            nv -= 1
        else:
            break
    best = None
    for attempt in range(4):
        tu = _knots(nu, ulo, uhi)
        tv = _knots(nv, vlo, vhi)
        Bu = _design(u, tu)
        Bv = _design(v, tv)
        # tensor product rows (sparse): row p = kron(Bu[p], Bv[p])
        A = _tensor_rows(Bu, Bv, nu, nv)
        # second-difference smoothing penalty on the control grid
        D = _penalty(nu, nv)
        AtA = (A.T @ A).tocsc()
        DtD = (D.T @ D).tocsc()
        # balance the smoothing against the data term; sparse cells (few
        # points per control point) get relatively more smoothing so the
        # surface can't fly off between data points
        ratio = len(S) / float(nu * nv)
        lam = (2e-4 + 5e-3 / max(ratio, 0.25)) * AtA.diagonal().sum() / max(DtD.diagonal().sum(), 1e-30)
        M = (AtA + lam * DtD).tocsc()
        rhs = A.T @ S
        try:
            C = np.column_stack([spsolve(M, rhs[:, k]) for k in range(3)])
        except Exception:
            C = np.column_stack([lsqr(M, rhs[:, k])[0] for k in range(3)])
        if not np.isfinite(C).all():
            break
        res = np.linalg.norm(A @ C - S, axis=1)
        dev = float(res.max())
        surf = FreeformSurface(origin, n, e1, e2, tu, tv, C.reshape(nu, nv, 3), dev)
        if best is None or dev < best.dev:
            best = surf
        if dev <= tol or nu >= max_ctrl and nv >= max_ctrl or nu * nv >= len(S) // 2:
            break
        nu = min(max_ctrl, int(nu * 1.6) + 1)
        nv = min(max_ctrl, int(nv * 1.6) + 1)
    return best


def _tensor_rows(Bu, Bv, nu, nv):
    Bu = Bu.tocsr()
    Bv = Bv.tocsr()
    npts = Bu.shape[0]
    ku = np.diff(Bu.indptr)
    kv = np.diff(Bv.indptr)
    if len(ku) and (ku == ku[0]).all() and (kv == kv[0]).all():
        # every row has the same number of non-zeros (deg+1): vectorised
        a, b = int(ku[0]), int(kv[0])
        iu = Bu.indices.reshape(npts, a)
        wu = Bu.data.reshape(npts, a)
        iv = Bv.indices.reshape(npts, b)
        wv = Bv.data.reshape(npts, b)
        cols = (iu[:, :, None] * nv + iv[:, None, :]).reshape(npts, a * b)
        vals = (wu[:, :, None] * wv[:, None, :]).reshape(npts, a * b)
        rows = np.repeat(np.arange(npts), a * b)
        return sparse.csr_matrix((vals.reshape(-1), (rows, cols.reshape(-1))), shape=(npts, nu * nv))
    rows, cols, vals = [], [], []
    for p in range(npts):
        iu = Bu.indices[Bu.indptr[p]:Bu.indptr[p + 1]]
        wu = Bu.data[Bu.indptr[p]:Bu.indptr[p + 1]]
        iv = Bv.indices[Bv.indptr[p]:Bv.indptr[p + 1]]
        wv = Bv.data[Bv.indptr[p]:Bv.indptr[p + 1]]
        c = (iu[:, None] * nv + iv[None, :]).ravel()
        w = (wu[:, None] * wv[None, :]).ravel()
        rows.append(np.full(len(c), p))
        cols.append(c)
        vals.append(w)
    return sparse.csr_matrix((np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(npts, nu * nv))


def _penalty(nu, nv):
    def d2(n):
        if n < 3:
            return sparse.csr_matrix((0, n))
        return sparse.diags([np.ones(n - 2), -2 * np.ones(n - 2), np.ones(n - 2)], [0, 1, 2], shape=(n - 2, n))

    Iu = sparse.identity(nu)
    Iv = sparse.identity(nv)
    return sparse.vstack([sparse.kron(d2(nu), Iv), sparse.kron(Iu, d2(nv))]).tocsr()
