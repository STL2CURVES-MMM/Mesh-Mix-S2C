"""Smooth preset: organic shapes as watertight bicubic surface patches.

Pipeline
  1. Quad-remesh the triangle mesh (Instant Meshes).
  2. Treat the quad mesh as a Catmull-Clark control cage and convert every
     quad into a bicubic Bezier patch (Loop & Schaefer "ACC" construction).
     Patches share boundary curves exactly, so the surface is watertight;
     regular regions reproduce the exact C2 subdivision limit surface.
  3. Least-squares fit the cage so the patches hug the original mesh.
  4. Build the B-rep directly (shared vertices / edges / exact pcurves).
"""
# Part of Manifold Mesh Mix (c) 2026 Tuna-The-First and Claude, AGPL-3.0, commit 4cf72ea.
# Modified 2026-10-26 for the stl2curves merged engine: quad_remesh moves a mesh far from
# the origin to it before Instant Meshes' float32 copy (CENTRE_AT), and back after.
# Modified 2026-10-28: the remesher runs in a watched child process (remesh_guard.py): one
# that gets stuck (Instant Meshes' Step 5 can loop for ever) is stopped and run again at a
# nudged target; when every try sticks, smooth_shell's simpler layouts are used (a shared
# Budget of FREEZE_CAP stuck runs per smooth_shell call).
import os
import sys

import numpy as np
from scipy import sparse
from scipy.sparse.linalg import factorized
from scipy.spatial import cKDTree

from OCP.BRep import BRep_Builder
from OCP.Geom import Geom_BezierCurve, Geom_BezierSurface
from OCP.Geom2d import Geom2d_Line
from OCP.gp import gp_Dir2d, gp_Pnt, gp_Pnt2d
from OCP.TopoDS import TopoDS_Edge, TopoDS_Face, TopoDS_Shell, TopoDS_Vertex, TopoDS_Wire
from OCP.collections import Array1_gp_Pnt, Array2_gp_Pnt

from .meshio import face_normals_areas, signed_volume
from .report import NULL


class SmoothError(Exception):
    pass


# ----------------------------------------------------------------------------
# Quad remeshing
# ----------------------------------------------------------------------------


class _Silence:
    """Redirect C-level stdout to stderr (Instant Meshes is chatty)."""

    def __enter__(self):
        sys.stdout.flush()
        self.saved = os.dup(1)
        os.dup2(2, 1)
        return self

    def __exit__(self, *a):
        sys.stdout.flush()
        os.dup2(self.saved, 1)
        os.close(self.saved)


# A mesh farther from the origin than this many times its size is moved to the origin
# for the remesher, which works in float32: a 35 mm sphere 1e7 mm out (float32 spacing 1 mm
# there) sent its edge subdivision into a loop that took 14 GB in 90 s; centred, 0.1 s.
# Nearer meshes are passed as they are, so their results stay as they were.
CENTRE_AT = 100.0


def quad_remesh(V, F, target_verts, crease_deg=0.0, seed_tries=3, report=NULL, budget=None):
    """Quad mesh of (V, F) near target_verts vertices, checked (check_quad_mesh). The
    remesher runs watched (remesh_guard): ``budget`` is the stuck runs still allowed,
    shared by the calls of one smooth_shell."""
    from . import remesh_guard

    lo, hi = V.min(0), V.max(0)
    shift = (lo + hi) / 2 if np.abs(V).max() > CENTRE_AT * np.linalg.norm(hi - lo) else np.zeros(3)
    budget = budget if budget is not None else remesh_guard.Budget()
    last_err = None
    for k in range(seed_tries):
        # finer resolutions resolve thin features the remesher tripped over
        tv = int(target_verts * (1.0, 1.6, 2.6)[min(k, 2)])
        try:
            qv, qf, _ = remesh_guard.remesh(V - shift, F, tv, crease_deg, report, budget)
        except remesh_guard.Stuck as e:
            raise SmoothError(str(e))
        Vq = np.asarray(qv, dtype=np.float64) + shift
        Q = np.asarray(qf, dtype=np.int64)
        ok, why = check_quad_mesh(Vq, Q)
        if not ok and why == "inconsistent orientation":
            Q2 = orient_quads(Q, len(Vq))
            if Q2 is not None:
                ok, why = check_quad_mesh(Vq, Q2)
                if ok:
                    Q = Q2
        if ok:
            return Vq, Q
        last_err = why
    raise SmoothError(f"quad remeshing failed ({last_err})")


def orient_quads(Q, nv):
    """Flip quads so neighbours agree on orientation (BFS over shared
    edges). Returns None if an edge is shared by more than two quads."""
    from collections import deque

    nq = len(Q)
    a = Q.reshape(-1)
    b = np.roll(Q, -1, axis=1).reshape(-1)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    key = lo * nv + hi
    order = np.argsort(key, kind="stable")
    sk = key[order]
    starts = np.r_[0, np.nonzero(np.diff(sk))[0] + 1, len(sk)]
    if (np.diff(starts) > 2).any():
        return None
    nbr = {}
    for i in range(len(starts) - 1):
        if starts[i + 1] - starts[i] == 2:
            h1, h2 = order[starts[i]], order[starts[i] + 1]
            same = a[h1] == a[h2]  # same direction => inconsistent
            nbr.setdefault(h1 // 4, []).append((h2 // 4, same))
            nbr.setdefault(h2 // 4, []).append((h1 // 4, same))
    flip = np.zeros(nq, dtype=bool)
    seen = np.zeros(nq, dtype=bool)
    for s0 in range(nq):
        if seen[s0]:
            continue
        seen[s0] = True
        dq = deque([s0])
        while dq:
            f = dq.popleft()
            for g, same in nbr.get(f, ()):
                want = flip[f] ^ same
                if not seen[g]:
                    seen[g] = True
                    flip[g] = want
                    dq.append(g)
                elif flip[g] != want:
                    return None  # non-orientable
    Q2 = Q.copy()
    Q2[flip] = Q2[flip][:, ::-1]
    return Q2


def check_quad_mesh(Vq, Q):
    if len(Q) < 6 or Q.ndim != 2 or Q.shape[1] != 4:
        return False, "empty"
    if (Q[:, 0] == Q[:, 1]).any() or (Q[:, 1] == Q[:, 2]).any() or (Q[:, 2] == Q[:, 3]).any() or (Q[:, 3] == Q[:, 0]).any():
        return False, "degenerate quads"
    e = np.stack([Q, np.roll(Q, -1, axis=1)], axis=2).reshape(-1, 2)
    nv = len(Vq)
    d = e[:, 0] * nv + e[:, 1]
    if len(np.unique(d)) != len(d):
        return False, "inconsistent orientation"
    r = e[:, 1] * nv + e[:, 0]
    if not np.isin(r, d).all():
        return False, "open boundary"
    # every vertex must have a single fan (no pinched vertices)
    used = np.unique(Q)
    if len(used) != nv:
        return False, "unused vertices"
    return True, ""


# ----------------------------------------------------------------------------
# ACC construction (sparse linear maps from cage vertices to Bezier points)
# ----------------------------------------------------------------------------


class QuadCage:
    def __init__(self, Vq, Q):
        self.V = Vq
        self.Q = Q
        nq = len(Q)
        nv = len(Vq)
        self.nq, self.nv = nq, nv
        val = np.bincount(Q.reshape(-1), minlength=nv)
        self.valence = val
        # directed edge -> (face, corner index of edge start)
        a = Q.reshape(-1)
        b = np.roll(Q, -1, axis=1).reshape(-1)
        key = a * nv + b
        order = np.argsort(key)
        self._dkeys = key[order]
        self._dpos = order  # position in flattened (face*4 + corner)
        self.a, self.b = a, b

        # P11 interior points: row f*4+i
        rows, cols, vals = [], [], []
        n = val[Q].astype(np.float64)  # (nq,4)
        for i in range(4):
            v = Q[:, i]
            en = Q[:, (i + 1) % 4]
            fd = Q[:, (i + 2) % 4]
            ep = Q[:, (i + 3) % 4]
            ni = n[:, i]
            r = np.arange(nq) * 4 + i
            for c, w in ((v, ni), (en, 2.0 * np.ones(nq)), (ep, 2.0 * np.ones(nq)), (fd, np.ones(nq))):
                rows.append(r)
                cols.append(c)
                vals.append(w / (ni + 5.0))
        P11 = sparse.csr_matrix(
            (np.concatenate(vals), (np.concatenate(rows), np.concatenate(cols))), shape=(4 * nq, nv)
        )
        self.P11 = P11

        # twin corner: for directed edge (a->b) at flattened pos p (face f,
        # corner i = start a), the other face has edge (b->a) at pos q; the
        # corner of a in that face is (q's corner + 1) % 4.
        rkey = b * nv + a
        idx = np.searchsorted(self._dkeys, rkey)
        idx = np.clip(idx, 0, len(self._dkeys) - 1)
        if not (self._dkeys[idx] == rkey).all():
            raise SmoothError("cage is not closed")
        q = self._dpos[idx]
        twin_corner = (q // 4) * 4 + ((q % 4) + 1) % 4
        # edge point near corner (f,i) toward (f,i+1)
        E = 0.5 * (P11 + P11[twin_corner])
        self.Enext = E  # row f*4+i: near Q[f,i], on edge to Q[f,i+1]
        # edge point near corner (f,i) toward previous corner (f,i-1):
        # that's the edge (Q[f,i] -> Q[f,i-1]) seen from the other face, which
        # equals the average of P11[f,i] and the P11 of the face across edge
        # (Q[f,i-1] -> Q[f,i]).
        prev_pos = np.arange(4 * nq)
        prev_pos = (prev_pos // 4) * 4 + (prev_pos % 4 + 3) % 4  # pos of edge (i-1 -> i)
        # twin of edge (i-1 -> i) is (i -> i-1) in another face at q2; corner of
        # vertex i there is q2's start corner.
        a2 = a[prev_pos]
        b2 = b[prev_pos]
        rkey2 = b2 * nv + a2
        idx2 = np.searchsorted(self._dkeys, rkey2)
        q2 = self._dpos[idx2]
        self.Eprev = 0.5 * (P11 + P11[q2])
        # corners: average of P11 rows around each vertex
        cr = Q.reshape(-1)
        Cm = sparse.csr_matrix((1.0 / val[cr], (cr, np.arange(4 * nq))), shape=(nv, 4 * nq))
        self.Corner = Cm @ P11  # (nv, nv)

        self.bezier = self._bezier_matrix()

    def _bezier_matrix(self):
        """Sparse (16*nq, nv): Bezier control points B[f, i, j] (i=u, j=v)."""
        nq = self.nq
        Q = self.Q
        f4 = np.arange(nq) * 4
        blocks = [None] * 16

        def put(i, j, M):
            blocks[i * 4 + j] = M

        C = self.Corner
        put(0, 0, C[Q[:, 0]])
        put(3, 0, C[Q[:, 1]])
        put(3, 3, C[Q[:, 2]])
        put(0, 3, C[Q[:, 3]])
        P = self.P11
        put(1, 1, P[f4 + 0])
        put(2, 1, P[f4 + 1])
        put(2, 2, P[f4 + 2])
        put(1, 2, P[f4 + 3])
        En, Ep = self.Enext, self.Eprev
        # edge c0->c1 (v=0)
        put(1, 0, En[f4 + 0])
        put(2, 0, Ep[f4 + 1])
        # edge c1->c2 (u=1)
        put(3, 1, En[f4 + 1])
        put(3, 2, Ep[f4 + 2])
        # edge c2->c3 (v=1, u decreasing)
        put(2, 3, En[f4 + 2])
        put(1, 3, Ep[f4 + 3])
        # edge c3->c0 (u=0, v decreasing)
        put(0, 2, En[f4 + 3])
        put(0, 1, Ep[f4 + 0])
        # interleave so row = f*16 + i*4 + j
        stacked = sparse.vstack(blocks).tocsr()  # rows: k*nq + f
        k = np.arange(16)
        f = np.arange(nq)
        perm = (k[None, :] * nq + f[:, None]).reshape(-1)
        return stacked[perm]

    def laplacian(self):
        e = np.stack([self.a, self.b], axis=1)
        e = np.unique(np.sort(e, axis=1), axis=0)
        nv = self.nv
        A = sparse.coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(nv, nv))
        A = (A + A.T).tocsr()
        deg = np.asarray(A.sum(1)).ravel()
        return sparse.diags(np.ones(nv)) - sparse.diags(1.0 / np.maximum(deg, 1)) @ A


# ----------------------------------------------------------------------------
# Fitting
# ----------------------------------------------------------------------------


def _vertex_normals(V, F):
    fn, fa = face_normals_areas(V, F)
    N = _scatter_add(F, fn * fa[:, None], len(V))
    ln = np.linalg.norm(N, axis=1)
    ln[ln == 0] = 1
    return N / ln[:, None]


def _scatter_add(idx, vals, n):
    """out[i] = sum of vals rows whose idx is i (idx may be (m,) or (m, k):
    every column of idx gets the same vals row). Much faster than np.add.at."""
    idx = idx.reshape(len(vals), -1)
    out = np.zeros((n, vals.shape[1]))
    for c in range(idx.shape[1]):
        for d in range(vals.shape[1]):
            out[:, d] += np.bincount(idx[:, c], weights=vals[:, d], minlength=n)
    return out


def _unit_rows(A):
    n = np.linalg.norm(A, axis=1)
    return A / np.maximum(n, 1e-30)[:, None]


def _same_side_nearest(tree, Q, NQ, NT, k=8, min_cos=0.2):
    """For each query point, the nearest tree point whose normal agrees
    with the query's normal (falls back to the plain nearest one)."""
    k = min(k, tree.n)
    out = np.empty(len(Q), dtype=np.int64)
    step = 1_000_000  # bounds the memory of the k-neighbour arrays
    for a in range(0, len(Q), step):
        q = Q[a:a + step]
        _, idx = tree.query(q, k=k, workers=-1)
        if k == 1:
            out[a:a + step] = idx
            continue
        ok = np.einsum("mkd,md->mk", NT[idx], NQ[a:a + step]) > min_cos
        first = np.argmax(ok, axis=1)
        first[~ok.any(1)] = 0
        out[a:a + step] = idx[np.arange(len(q)), first]
    return out


def _patch_weights(n, inner=True, deriv=None):
    """(n*n, 16): weights of a patch's 16 Bezier points at an n x n grid of
    (u, v) (deriv "u" or "v": the partial derivative there instead)."""
    if inner:
        t = (np.arange(n) + 0.5) / n
    else:
        t = np.linspace(0, 1, n)
    B = np.stack([(1 - t) ** 3, 3 * t * (1 - t) ** 2, 3 * t ** 2 * (1 - t), t ** 3], axis=1)  # (n,4)
    D = np.stack([-3 * (1 - t) ** 2, 3 * (1 - t) ** 2 - 6 * t * (1 - t), 6 * t * (1 - t) - 3 * t ** 2, 3 * t ** 2], axis=1)
    Bu, Bv = (D, B) if deriv == "u" else (B, D) if deriv == "v" else (B, B)
    return np.einsum("ai,bj->abij", Bu, Bv).reshape(n * n, 16)


# cages with more vertices than this are solved iteratively (a direct
# factorization of very fine cages needs a lot of memory and time)
DIRECT_SOLVE_MAX = 40000


def _solve(A, rhs, X0):
    if A.shape[0] <= DIRECT_SOLVE_MAX:
        solve = factorized(A.tocsc())
        return np.column_stack([solve(np.ascontiguousarray(rhs[:, k])) for k in range(3)])
    from scipy.sparse.linalg import LinearOperator, cg

    A = A.tocsr()
    d = A.diagonal()
    M = LinearOperator(A.shape, matvec=lambda x: x / d, dtype=np.float64)
    out = []
    for k in range(3):
        x, _ = cg(A, rhs[:, k], x0=X0[:, k], rtol=1e-9, maxiter=2000, M=M)
        out.append(x)
    return np.column_stack(out)


def fit_cage(cage, V, F, iters=5, report=NULL, max_ref_pts=150000):
    """Move cage vertices so the limit surface fits the original mesh.

    Surface samples are evaluated patch by patch from the Bezier points
    (dense weights shared by all patches), and the least-squares system is
    assembled from 16 x 16 blocks per patch, so memory grows gently with
    the number of patches."""
    ext = float(np.linalg.norm(V.max(0) - V.min(0)))
    nq = cage.nq
    Bm = cage.bezier  # (16 nq, nv)
    W, Wu, Wv = (_patch_weights(4, True, d) for d in (None, "u", "v"))
    Wd, Wdu, Wdv = (_patch_weights(7, False, d) for d in (None, "u", "v"))
    ns, nsd = len(W), len(Wd)
    G = W.T @ W
    L = cage.laplacian()
    VN = _vertex_normals(V, F)
    # reference points: original vertices plus face centroids for coarse meshes
    ref = V
    refn = VN
    if len(V) < 20 * nq:
        fn, _ = face_normals_areas(V, F)
        ref = np.vstack([V, V[F].mean(1)])
        refn = np.vstack([VN, fn])
    # fine cages need the mesh's own detail, so they keep more points
    max_ref_pts = max(max_ref_pts, min(8 * nq, 4_000_000))
    if len(ref) > max_ref_pts:
        sel = np.random.default_rng(0).choice(len(ref), max_ref_pts, replace=False)
        ref, refn = ref[sel], refn[sel]
    tree = cKDTree(ref)
    X = cage.V.copy()
    lam0 = 0.05
    LtL = (L.T @ L).tocsc()
    n_s = nq * ns
    chunk = 20000  # patches per slice when assembling the system

    def block_diag(Kc):
        """CSR matrix with one 16 x 16 block per patch, from (m, 256) rows."""
        m = len(Kc)
        cols = np.repeat(np.arange(16 * m, dtype=np.int64).reshape(m, 1, 16), 16, axis=1).reshape(-1)
        return sparse.csr_matrix((Kc.reshape(-1), cols, np.arange(0, 256 * m + 1, 16)), shape=(16 * m, 16 * m))

    def at(Wt, Bz):
        # (nq, 16, 3) Bezier points -> (nq * len(Wt), 3) surface samples
        return np.ascontiguousarray(np.tensordot(Wt, Bz, axes=([1], [1])).transpose(1, 0, 2)).reshape(-1, 3)

    def back(Wt, P):
        # transpose of at(): (nq * len(Wt), 3) -> (nq, 16, 3)
        return np.tensordot(P.reshape(nq, len(Wt), 3), Wt, axes=([1], [0])).transpose(0, 2, 1)

    Wd2 = (Wd[:, :, None] * Wd[:, None, :]).reshape(nsd, 256)

    sign = None
    for it in range(iters):
        report.progress(0.2 + 0.6 * it / iters)
        Bz = (Bm @ X).reshape(nq, 16, 3)
        Y = at(W, Bz)
        NY = _unit_rows(np.cross(at(Wu, Bz), at(Wv, Bz)))
        if sign is None:
            _, i0 = tree.query(Y, workers=-1)
            sign = 1.0 if np.nanmean((NY * refn[i0]).sum(1)) >= 0 else -1.0
        NY *= sign
        # Nearest mesh point on the same side: on thin walls and in deep
        # grooves the plain nearest point can lie on the opposite face and
        # would drag the surface across.
        idx = _same_side_nearest(tree, Y, NY, refn)
        p = ref[idx]
        nrm = refn[idx]
        # point-to-plane projection onto the original surface
        T = Y - ((Y - p) * nrm).sum(1)[:, None] * nrm
        del Y, NY, p, nrm
        # reverse term: every reference point pulls its nearest dense sample
        # (again one facing the same way)
        Yd = at(Wd, Bz)
        NYd = sign * _unit_rows(np.cross(at(Wdu, Bz), at(Wdv, Bz)))
        dtree = cKDTree(Yd)
        j = _same_side_nearest(dtree, ref, refn, NYd)
        del dtree, Yd, NYd
        cnt = np.bincount(j, minlength=nq * nsd).astype(np.float64).reshape(nq, nsd)
        sums = _scatter_add(j, ref, nq * nsd)
        wr = n_s / max(len(ref), 1)
        lam = lam0 * (0.35 ** it)
        # per patch: W^T W + wr * Wd^T diag(cnt) Wd, and the matching right side
        K = G.reshape(1, 256) + wr * (cnt @ Wd2)  # (nq, 256)
        r = back(W, T) + wr * back(Wd, sums)
        del T, cnt, sums
        A = lam * LtL
        # assembled a slice of patches at a time (bounds the memory)
        for a in range(0, nq, chunk):
            Bs = Bm[16 * a:16 * min(nq, a + chunk)]
            A = A + Bs.T @ (block_diag(K[a:a + chunk]) @ Bs)
        del K, Bs
        rhs = Bm.T @ r.reshape(-1, 3) + lam * (LtL @ X)
        # (the Laplacian term keeps the cage close to its current fairness)
        Xn = _solve(A, rhs, X)
        del A
        if not np.isfinite(Xn).all():
            break
        X = Xn
    cage.V = X
    # final deviation estimate (both directions)
    Yd = at(Wd, (Bm @ X).reshape(nq, 16, 3))
    dtree = cKDTree(Yd)
    d1, _ = dtree.query(V if len(V) <= max_ref_pts else V[np.random.default_rng(1).choice(len(V), max_ref_pts, replace=False)],
                        workers=-1)
    return X, float(np.percentile(d1, 99)), float(d1.max()), ext


# ----------------------------------------------------------------------------
# B-rep construction
# ----------------------------------------------------------------------------


def _P(p):
    return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))


def build_bezier_shell(cage, X, tol):
    B = BRep_Builder()
    Bz = (cage.bezier @ X).reshape(cage.nq, 4, 4, 3)  # [f, i(u), j(v)]
    Q = cage.Q
    nv = cage.nv
    corners = cage.Corner @ X
    tol = max(tol, 1e-7)
    verts = [None] * nv
    for v in range(nv):
        tv = TopoDS_Vertex()
        B.MakeVertex(tv, _P(corners[v]), tol)
        verts[v] = tv
    edges = {}

    def get_edge(f, k):
        """Edge from corner k to k+1 of face f; returns (edge, forward)."""
        a, b = int(Q[f, k]), int(Q[f, (k + 1) % 4])
        key = (a, b) if a < b else (b, a)
        e = edges.get(key)
        if e is None:
            # control points along this side of face f, from a to b
            P = _side_points(Bz[f], k)
            if a > b:
                P = P[::-1]
            arr = Array1_gp_Pnt(1, 4)
            for i in range(4):
                arr.SetValue(i + 1, _P(P[i]))
            crv = Geom_BezierCurve(arr)
            e = TopoDS_Edge()
            B.MakeEdge(e, crv, tol)
            vb = verts[key[1]].Reversed()
            B.Add(e, verts[key[0]])
            B.Add(e, vb)
            B.Range(e, 0.0, 1.0)
            edges[key] = e
        return e, a < b

    faces = []
    starts = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    dirs = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0)]
    for f in range(cage.nq):
        poles = Array2_gp_Pnt(1, 4, 1, 4)
        for i in range(4):
            for j in range(4):
                poles.SetValue(i + 1, j + 1, _P(Bz[f, i, j]))
        surf = Geom_BezierSurface(poles)
        face = TopoDS_Face()
        B.MakeFace(face, surf, tol)
        w = TopoDS_Wire()
        B.MakeWire(w)
        for k in range(4):
            e, fwd = get_edge(f, k)
            s0 = starts[k]
            d0 = dirs[k]
            if fwd:
                line = Geom2d_Line(gp_Pnt2d(*s0), gp_Dir2d(*d0))
            else:
                s1 = starts[(k + 1) % 4]
                line = Geom2d_Line(gp_Pnt2d(*s1), gp_Dir2d(-d0[0], -d0[1]))
            B.UpdateEdge(e, line, face, tol)
            B.Range(e, face, 0.0, 1.0)
            B.Add(w, e if fwd else e.Reversed())
        w.Closed(True)
        B.Add(face, w)
        faces.append(face)
    for e in edges.values():
        B.SameParameter(e, True)
        B.SameRange(e, True)
    sh = TopoDS_Shell()
    B.MakeShell(sh)
    for fc in faces:
        B.Add(sh, fc)
    sh.Closed(True)
    return sh, len(faces)


def _side_points(Bf, k):
    """Control points along side k of a 4x4 Bezier net, in corner order."""
    if k == 0:
        return Bf[:, 0]  # c0 -> c1 (v=0, u increasing)
    if k == 1:
        return Bf[3, :]  # c1 -> c2 (u=1, v increasing)
    if k == 2:
        return Bf[::-1, 3]  # c2 -> c3 (v=1, u decreasing)
    return Bf[0, ::-1]  # c3 -> c0 (u=0, v decreasing)


# ----------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------


def total_cage_budget(detail, max_faces=0):
    """Cage vertex budget for a whole body (output faces ~ 4x this)."""
    detail = float(np.clip(detail, 0.0, 1.0))
    # 0 -> ~100 cage verts (~400 patches), 1 -> ~4000 (~16000 patches)
    tv = int(100 * 40.0 ** detail)
    if max_faces:
        tv = min(tv, max(40, max_faces // 4))
    return tv


# Fine detail: at the top of the quality slider the cage also grows with the
# mesh's own density, so dense sculpts and scans keep their fine texture
# (cracks, scales, tool marks). At best quality there is one surface patch
# per ~11 mesh triangles, which keeps the surface within a few hundredths of
# a millimetre of a typical sculpt. Slower and much bigger files, so the apps
# warn before converting.
FINE_START = 0.85  # where "Best quality" starts on the slider
FINE_TRIS_PER_CAGE_VERT = 43
FINE_MAX_CAGE = 60000  # per piece (~240k patches, a few GB of memory)


def fine_cage_budget(n_tris, quality):
    """Cage vertex budget for one piece from its triangle count (0 = off)."""
    q = float(np.clip(quality, 0.0, 1.0))
    if q <= FINE_START:
        return 0
    # x10 from the start of "Best quality" to the end of the slider
    c = 10.0 ** ((q - 1.0) / (1.0 - FINE_START)) / FINE_TRIS_PER_CAGE_VERT
    return int(min(FINE_MAX_CAGE, n_tris * c))


def _manifold_clean(V, F):
    """Round-trip through the Manifold library: returns a closed, manifold
    copy (it splits non-manifold edges the way a solid modeller would), or
    None if the mesh can't be interpreted as a solid."""
    try:
        import manifold3d as m3d

        man = m3d.Manifold(
            m3d.Mesh(
                vert_properties=np.ascontiguousarray(V, dtype=np.float32),
                tri_verts=np.ascontiguousarray(F, dtype=np.uint32),
            )
        )
        if man.status() != m3d.Error.NoError or man.is_empty():
            return None
        o = man.to_mesh()
        return np.asarray(o.vert_properties)[:, :3].astype(np.float64), np.asarray(o.tri_verts).astype(np.int64), man
    except Exception:
        return None


def robust_decimate(V, F, target_tris, report=NULL):
    """Decimate to about ``target_tris`` and guarantee a closed manifold
    result (aggressive decimation of thin features can create non-manifold
    spots; the Manifold round trip repairs them)."""
    import fast_simplification

    from .meshio import Shell, compact, repair_shell_light

    diag = float(np.linalg.norm(V.max(0) - V.min(0)))
    man = None
    if len(F) > 20 * target_tris:
        cl = _manifold_clean(V, F)
        if cl is not None:
            man = cl[2]
            try:
                # a clean pre-simplification within 0.1% of the size; it also
                # calms scan noise that stalls the quadric decimator
                o = man.simplify(1e-3 * diag).to_mesh()
                V2 = np.asarray(o.vert_properties)[:, :3].astype(np.float64)
                F2 = np.asarray(o.tri_verts).astype(np.int64)
                if len(F2) >= target_tris // 2:
                    V, F = V2, F2
            except Exception:
                pass
    if len(F) <= 1.15 * target_tris:
        sh = Shell(V, F)
        if sh.closed and not sh.topo.same_dir.any() and len(sh.F) >= 4:
            return sh.V, sh.F
    best = None
    for factor in (1.0, 0.7, 1.4, 2.0):
        tt = max(8, int(target_tris * factor))
        try:
            v2, f2 = fast_simplification.simplify(
                np.ascontiguousarray(V, dtype=np.float32),
                np.ascontiguousarray(F, dtype=np.int32),
                target_reduction=float(max(0.0, 1.0 - tt / max(len(F), 1))),
            )
        except Exception as e:
            report.log(f"Decimation attempt failed ({e})")
            continue
        v2, f2 = compact(np.asarray(v2, dtype=np.float64), np.asarray(f2, dtype=np.int64))
        cl = _manifold_clean(v2, f2)
        if cl is not None:
            v2, f2 = cl[0], cl[1]
        sh = Shell(v2, f2)
        if not sh.closed:
            sh = repair_shell_light(sh, report)
        if sh.closed and not sh.topo.same_dir.any() and len(sh.F) >= 4:
            if len(sh.F) <= 2 * target_tris:
                return sh.V, sh.F
            # the quadric decimator stalls on very noisy meshes
            if best is None or len(sh.F) < len(best.F):
                best = sh
    if man is None:
        cl = _manifold_clean(V, F)
        man = cl[2] if cl is not None else None
    if man is not None:
        # tolerance-driven simplification, coarser until the count fits
        for k in range(12):
            try:
                o = man.simplify(diag * 2e-3 * 1.6 ** k).to_mesh()
            except Exception:
                break
            sh = Shell(np.asarray(o.vert_properties)[:, :3].astype(np.float64), np.asarray(o.tri_verts).astype(np.int64))
            if len(sh.F) < 4:
                break
            if sh.closed and not sh.topo.same_dir.any() and (best is None or len(sh.F) < len(best.F)):
                best = sh
            if len(sh.F) <= 2 * target_tris:
                break
    if best is not None:
        return best.V, best.F
    raise SmoothError("could not simplify the mesh into a clean solid")


def tri_quad_cage(V, F, target_quads, report=NULL):
    """Remesher-free cage: decimate, then split every triangle into three
    quads (one Catmull-Clark topology step). Works for any closed manifold
    triangle mesh."""
    target_tris = max(8, target_quads // 3)
    if len(F) > target_tris * 1.15:
        V, F = robust_decimate(V, F, target_tris, report)
    nv = len(V)
    a = F.reshape(-1)
    b = np.roll(F, -1, axis=1).reshape(-1)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    ek, einv = np.unique(lo * nv + hi, return_inverse=True)
    ne = len(ek)
    mids = (V[ek // nv] + V[ek % nv]) / 2
    cents = V[F].mean(1)
    Vq = np.vstack([V, mids, cents])
    e = (einv.reshape(-1, 3) + nv)  # e[f,0]=edge(F0,F1), e[f,1]=edge(F1,F2), e[f,2]=edge(F2,F0)
    g = np.arange(len(F)) + nv + ne
    q0 = np.stack([F[:, 0], e[:, 0], g, e[:, 2]], axis=1)
    q1 = np.stack([F[:, 1], e[:, 1], g, e[:, 0]], axis=1)
    q2 = np.stack([F[:, 2], e[:, 2], g, e[:, 1]], axis=1)
    Q = np.vstack([q0, q1, q2])
    return Vq, Q


def smooth_shell(shell, tol, budget, report=NULL):
    V, F = shell.V, shell.F
    if not shell.closed:
        raise SmoothError("shell is open")
    tv = int(max(24, min(budget, len(V) // 2 if len(V) > 200 else budget)))
    Vq = Q = None
    if tv >= 60 and len(F) >= 200:
        from .remesh_guard import Budget

        stuck = Budget()        # (stuck remesher runs allowed for this piece, all tries together)
        try:
            report.log(f"Quad remeshing (target {tv} cage vertices)")
            Vq, Q = quad_remesh(V, F, tv, report=report, budget=stuck)
        except SmoothError as e:
            Vq = Q = None
            if len(F) > 30 * tv:
                # scan noise trips the remesher up; a simplified copy is calm
                # (the surfaces are still fitted to the original mesh)
                try:
                    Vd, Fd = robust_decimate(V, F, max(20 * tv, 4000), report)
                    Vq, Q = quad_remesh(Vd, Fd, tv, report=report, budget=stuck)
                    report.log("Quad remeshed a simplified copy of the mesh")
                except SmoothError:
                    Vq = Q = None
            if Vq is None:
                report.log(f"The quad remesher couldn't handle this mesh ({e}), so a simpler patch layout was used.")
    if Vq is None:
        Vq, Q = tri_quad_cage(V, F, 4 * tv, report)
        ok, why = check_quad_mesh(Vq, Q)
        if not ok:
            raise SmoothError(f"could not build a quad cage ({why})")
    # match orientation with the input
    tri = np.vstack([Q[:, [0, 1, 2]], Q[:, [0, 2, 3]]])
    if np.sign(signed_volume(Vq, tri)) != np.sign(shell.volume or 1.0):
        Q = Q[:, ::-1].copy()
    report.progress(0.15)
    cage = QuadCage(Vq, Q)
    report.log(f"Fitting {cage.nq} surface patches")
    X, d99, dmax, ext = fit_cage(cage, V, F, report=report)
    report.log(f"Surface deviation: 99% within {d99:.3g}, max {dmax:.3g} ({100 * dmax / ext:.2f}% of size)")
    report.progress(0.85)
    sh, nf = None, 0
    try:
        from OCP.BRepCheck import BRepCheck_Analyzer

        from .patchmerge import build_merged_shell

        sh, nf = build_merged_shell(cage, X, tol)
        if not BRepCheck_Analyzer(sh).IsValid():
            report.log("The merged surfaces failed the check, so the individual patches were kept.")
            sh = None
        else:
            report.log(f"Merged {cage.nq} patches into {nf} surfaces")
    except Exception as e:
        report.log(f"Patch merging skipped ({e})")
        sh = None
    if sh is None:
        sh, nf = build_bezier_shell(cage, X, tol)
    return sh, {"patches": nf, "dev99": d99, "devmax": dmax}


def _shell_ok(sh):
    from OCP.BRepCheck import BRepCheck_Analyzer

    try:
        return bool(BRepCheck_Analyzer(sh).IsValid())
    except Exception:
        return False


def convert_body_smooth(B, body, tol, opts, report=NULL):
    from .brep import make_compound, make_solid, make_solid_checked
    from .meshio import face_normals_areas, repair_shell_meshfix

    budget = total_cage_budget(opts.detail, opts.max_faces)
    areas = [float(face_normals_areas(s.V, s.F)[1].sum()) for s in body]
    tot = max(sum(areas), 1e-30)
    built, flags = [], []
    for shell, a in zip(body, areas):
        if not shell.closed:
            try:
                fixed = repair_shell_meshfix(shell, report)
                if fixed.closed:
                    shell = fixed
            except Exception:
                pass
        share = int(budget * a / tot)
        retry = 3 * share
        fine = 0 if opts.max_faces else fine_cage_budget(len(shell.F), getattr(opts, "fine_quality", opts.quality))
        if fine > share:
            report.log(f"Keeping fine detail: about {4 * fine} surface patches for this piece")
            share = fine
        sh = None
        ext = float(np.linalg.norm(shell.V.max(0) - shell.V.min(0)))

        def good(sh_, info_):
            # valid and actually close to the mesh (thin walls the cage
            # can't resolve show up as a large deviation or volume change)
            if sh_ is None or not _shell_ok(sh_) or info_["dev99"] > 0.012 * ext:
                return False
            if shell.closed and abs(shell.volume) > 0:
                from . import stepio

                try:
                    v = abs(stepio.volume(make_solid(B, [sh_])))
                    if abs(v / abs(shell.volume) - 1.0) > 0.03:
                        return False
                except Exception:
                    pass
            return True

        try:
            sh, info = smooth_shell(shell, tol, max(24, share), report)
            if not good(sh, info):
                sh = None
                # a denser cage often fixes tiny or awkward pieces
                sh2, info2 = smooth_shell(shell, tol, max(48, retry), report)
                if good(sh2, info2):
                    sh = sh2
        except SmoothError as e:
            report.log(f"Smooth surfaces didn't work for one piece ({e})")
        if sh is None:
            # last resort that is still made of real surfaces: freeform
            # B-spline cells fitted directly to the mesh (on a decimated copy
            # for very dense pieces, or this would take ages)
            report.log("Fitting freeform surfaces for one piece instead")
            from .cad import build_freeform_shell
            from .meshio import Shell

            work = shell
            if len(shell.F) > 150_000:
                try:
                    Vd, Fd = robust_decimate(shell.V, shell.F, 120_000, report)
                    work = Shell(Vd, Fd)
                except SmoothError:
                    pass
            sh = build_freeform_shell(work, tol, report)
        built.append(sh)
        flags.append(shell.closed)
    if all(flags):
        shape = make_solid_checked(B, built, report)
    else:
        shape = make_compound(built)
    return shape
