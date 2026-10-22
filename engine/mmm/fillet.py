"""Fillets along curved edges: a ball of radius r rolled along the edge
between two faces. Its centre runs along a curve (the spine), and the
fillet is every point r from that curve: a pipe. Along a straight edge the
pipe is a cylinder and along a circular one a torus, which the analytic
surfaces already cover; along any other curve (a cam's outline, the oval
where a pipe meets a sloping wall) the segmenter cuts it into many small
tori and cylinders of the same radius, which this joins into one face.

The spine is sampled from those pieces themselves (a torus's centre circle,
a cylinder's axis), so it comes from measurements of the mesh, and the
radius from the corners, which lie on the fillet."""
import os

import numpy as np


def _dbgp(*a):
    if os.environ.get("MMM_DEBUG_GROW"):
        print("PIPE", *a)

from .sweep import ProfileCurve, SweptSurface, _is_loop, _on_polyline, order_curve, samples_for


class PipeSurface(SweptSurface):
    """Points r from a spine curve. Looks like a freeform face to the rest
    of the converter (geom, distance, normal, uv, pcurve). Parameters: s
    along the spine (0..1 over its points, on past them for a closed one)
    and the angle round it, measured in a frame that turns as little as
    possible along the spine."""

    kind = "freeform"
    rank = 4

    def __init__(self, spine, r, s_range, th_range, tol):
        from scipy.spatial import cKDTree

        self.motion = None
        self.curve = spine
        self.r = float(r)
        self.s_range = s_range
        self.t_range = th_range
        self.tol = tol
        self.dev = 0.0
        self._geom = None
        self.twin = None
        # (distances are measured on the segments between samples, so a
        # few thousand are plenty)
        self.ss = np.linspace(s_range[0], s_range[1], int(np.clip(2 * len(spine.ts), 400, 4000)))
        self.SS = spine.at(self.ss)
        self.tree = cKDTree(self.SS)
        T = np.gradient(self.SS, axis=0)
        T /= np.maximum(np.linalg.norm(T, axis=1), 1e-300)[:, None]
        self.T, self.N = T, _rmf(self.SS, T)

    def describe(self):
        return f"fillet of radius {self.r:.4g}"

    def _frame(self, s):
        """Spine point, and the frame's N and B, at parameters s."""
        s = np.asarray(s, float)
        x = (s - self.ss[0]) / (self.ss[-1] - self.ss[0]) * (len(self.ss) - 1)
        i = np.clip(np.floor(x).astype(int), 0, len(self.ss) - 2)
        f = np.clip(x - i, 0.0, 1.0)[:, None]
        N = (1 - f) * self.N[i] + f * self.N[i + 1]
        T = (1 - f) * self.T[i] + f * self.T[i + 1]
        T /= np.maximum(np.linalg.norm(T, axis=1), 1e-300)[:, None]
        N -= (N * T).sum(1)[:, None] * T
        N /= np.maximum(np.linalg.norm(N, axis=1), 1e-300)[:, None]
        return self.curve.at(s), N, np.cross(T, N)

    def X(self, s, th):
        s = np.atleast_1d(np.asarray(s, float))
        th = np.broadcast_to(np.asarray(th, float), s.shape)
        C, N, B = self._frame(s)
        return C + self.r * (np.cos(th)[:, None] * N + np.sin(th)[:, None] * B)

    def foot(self, P, iters=0):
        P = np.atleast_2d(np.asarray(P, float))
        _, s = _on_polyline(self.tree, self.SS, self.ss, P, False)
        C, N, B = self._frame(s)
        w = P - C
        th = np.arctan2((w * B).sum(1), (w * N).sum(1))
        mid = getattr(self, "th_mid", 0.0)
        th = mid + np.mod(th - mid + np.pi, 2 * np.pi) - np.pi
        return s, th

    def distance(self, P):
        P = np.atleast_2d(np.asarray(P, float))
        d, _ = _on_polyline(self.tree, self.SS, self.ss, P, False)
        return np.abs(d - self.r)

    def normal(self, P):
        P = np.atleast_2d(np.asarray(P, float))
        s, th = self.foot(P)
        return self._normal_at(s, th)

    def _normal_at(self, s, th):
        # (towards the spine: the way the surface's parameters turn, along
        # the spine then round it, as geom's normal points; the region's
        # sense says which side the solid is on)
        _, N, B = self._frame(np.atleast_1d(s))
        return -(np.cos(th)[:, None] * N + np.sin(th)[:, None] * B)

    def halves(self, margin=0.05):
        """See SweptSurface.halves. Each half has its own frame, so its
        angles are measured again on the corners (self.X_on) it holds."""
        out = []
        o = self.seam()
        first = self.half_of(self.X_on)
        for k, (lo, hi) in enumerate(((o - margin, o + 0.5 + margin), (o + 0.5 - margin, o + 1.0 + margin))):
            h = PipeSurface(self.curve, self.r, (lo, hi), (0.0, 1.0), self.tol)
            mine = first if k == 0 else ~first
            if mine.sum() < 3 or not _angles(h, self.X_on[mine]):
                return None
            h.dev = self.dev
            out.append(h)
        out[0].twin, out[1].twin = out[1], out[0]
        return out

    def grid(self, nu=40, nv=24):
        s = np.linspace(*self.s_range, nu)
        th = np.linspace(*self.t_range, nv)
        return np.stack([self.X(s, np.full(nu, tj)) for tj in th], axis=1)

    def geom(self):
        if self._geom is None:
            from OCP.Approx import Approx_IsoParametric
            from OCP.GeomAbs import GeomAbs_C2
            from OCP.GeomAPI import GeomAPI_PointsToBSplineSurface
            from OCP.collections import Array2_gp_Pnt as TColgp_Array2OfPnt
            from OCP.gp import gp_Pnt

            # along the spine by how sharply it bends (see samples_for);
            # round it by the angle (10 degrees)
            nu = samples_for(self.curve, self.s_range)
            nv = int(np.clip(np.degrees(self.t_range[1] - self.t_range[0]) / 10.0 + 4, 6, 40))
            G = self.grid(nu, nv)
            arr = TColgp_Array2OfPnt(1, nu, 1, nv)
            for i in range(nu):
                for j in range(nv):
                    arr.SetValue(i + 1, j + 1, gp_Pnt(*map(float, G[i, j])))
            ap = GeomAPI_PointsToBSplineSurface(arr, Approx_IsoParametric, 3, 3, GeomAbs_C2, max(0.05 * self.tol, 1e-7))
            self._geom = ap.Surface()
        return self._geom


def _rmf(S, T):
    """A frame along points S with unit tangents T that turns as little as
    possible about them (double reflection; plain floats, as it runs
    point by point)."""
    a = np.eye(3)[int(np.argmin(np.abs(T[0])))]
    n = a - (a @ T[0]) * T[0]
    n = list(n / np.linalg.norm(n))
    Sl, Tl = S.tolist(), T.tolist()
    out = [n]
    for i in range(len(Sl) - 1):
        p, q, t0, t1 = Sl[i], Sl[i + 1], Tl[i], Tl[i + 1]
        v1 = [q[0] - p[0], q[1] - p[1], q[2] - p[2]]
        c1 = max(v1[0] * v1[0] + v1[1] * v1[1] + v1[2] * v1[2], 1e-300)
        k = 2.0 / c1 * (v1[0] * n[0] + v1[1] * n[1] + v1[2] * n[2])
        rL = [n[j] - k * v1[j] for j in range(3)]
        k = 2.0 / c1 * (v1[0] * t0[0] + v1[1] * t0[1] + v1[2] * t0[2])
        v2 = [t1[j] - (t0[j] - k * v1[j]) for j in range(3)]
        c2 = max(v2[0] * v2[0] + v2[1] * v2[1] + v2[2] * v2[2], 1e-300)
        k = 2.0 / c2 * (v2[0] * rL[0] + v2[1] * rL[1] + v2[2] * rL[2])
        n = [rL[j] - k * v2[j] for j in range(3)]
        d = n[0] * t1[0] + n[1] * t1[1] + n[2] * t1[2]
        n = [n[j] - d * t1[j] for j in range(3)]
        L = max((n[0] * n[0] + n[1] * n[1] + n[2] * n[2]) ** 0.5, 1e-300)
        n = [x / L for x in n]
        out.append(n)
    return np.array(out)


def fit_pipe(C, X, r, tol, margin=0.05):
    """A pipe of radius about r through spine samples C (in no order) and
    corners X lying on it, or (None, inf). Returns (surface, deviation of
    the corners from it); the radius is refitted on the corners."""
    # (the samples come from pieces that each only approximate the fillet,
    # so they stray about a tolerance either side of the spine: ordered and
    # smoothed at that scale; the corners decide whether the pipe fits)
    # (spine samples moved in from triangles along their normals stray
    # further: a coarser ordering is enough, the corners refine it below)
    for scale in (tol, 3 * tol, 0.5 * r):
        O = order_curve(C, scale)
        if O is not None:
            break
    if O is None:
        _dbgp("order_curve failed on", len(C))
        return None, np.inf
    closed = _is_loop(O, C, tol)
    if not closed:
        # a ring with a piece the segmenter fitted as something else (a
        # cone) is still a ring: the gap is bridged, and that piece joins
        # once the pipe is there (see Segmenter._find_fillets)
        gap = float(np.linalg.norm(O[-1] - O[0]))
        closed = len(O) >= 12 and gap < min(4 * r, 0.1 * np.linalg.norm(np.diff(O, axis=0), axis=1).sum())
    try:
        spine = ProfileCurve(O, 4 * tol, closed=closed)
    except Exception as e:
        _dbgp("spine curve failed", e)
        return None, np.inf
    rng = (0.0, 1.0) if closed else (-margin, 1.0 + margin)
    surf = PipeSurface(spine, r, rng, (0.0, 1.0), tol)
    d, s = _on_polyline(surf.tree, surf.SS, surf.ss, X, False)
    surf.r = float(np.median(d))
    # the corners lie on the fillet exactly: each puts the spine r back
    # along its radius; refit the spine on those points, ordered by where
    # they fall along it
    for _ in range(3):
        Cs = surf.curve.at(s)
        w = X - Cs
        L = np.maximum(np.linalg.norm(w, axis=1), 1e-300)
        Pn = X - surf.r * w / L[:, None]
        order = np.argsort(np.mod(s, 1.0) if closed else s)
        P2 = Pn[order]
        # (thinned to about one point per tolerance along the spine)
        key = np.round(np.cumsum(np.r_[0, np.linalg.norm(np.diff(P2, axis=0), axis=1)]) / tol).astype(int)
        _, first = np.unique(key, return_index=True)
        P2 = np.array([g.mean(0) for g in np.split(P2, first[1:])])
        if len(P2) < 6:
            break
        try:
            spine = ProfileCurve(P2, tol, closed=closed)
        except Exception:
            break
        surf = PipeSurface(spine, surf.r, rng, (0.0, 1.0), tol)
        d, s = _on_polyline(surf.tree, surf.SS, surf.ss, X, False)
        surf.r = float(np.median(d))
    if not closed:
        ds = max(s.max() - s.min(), 1e-6)
        surf = PipeSurface(spine, surf.r, (s.min() - margin * ds, s.max() + margin * ds), (0.0, 1.0), tol)
    # (round a closed spine the frame comes back turned a little, so the
    # angles of the whole loop say nothing: each half measures its own)
    if not _angles(surf, X) and not closed:
        _dbgp("angles: corners on it", int((surf.distance(X) <= 3 * surf.tol).sum()), "of", len(X))
        return None, np.inf
    surf.X_on = X
    dev = float(np.percentile(surf.distance(X), 99))
    surf.dev = dev
    return surf, dev


def _angles(surf, X, margin=0.2):
    """Set the range of angles round the spine that the corners X cover,
    and a good margin beyond (edges fitted along the fillet's borders
    stray past its corners, and must stay on the surface); False when it
    goes nearly all the way round."""
    # (only corners on it count, and not the odd stray one: near a sharp
    # bend of the spine a corner can be matched to the wrong spot on it)
    X = X[surf.distance(X) <= 3 * surf.tol]
    if len(X) < 3:
        return False
    surf.th_mid = 0.0
    _, th = surf.foot(X)
    surf.th_mid = float(np.arctan2(np.sin(th).mean(), np.cos(th).mean()))
    _, th = surf.foot(X)
    lo, hi = np.percentile(th, [0.5, 99.5])
    span = max(hi - lo, 1e-6)
    if span > 1.5 * np.pi:
        s, _ = surf.foot(X)
        q = np.quantile(s, [0, 0.25, 0.5, 0.75, 1.0])
        _dbgp("angle span deg", round(float(np.degrees(span)), 1), "per quarter of the spine:",
              [(round(float(np.degrees(np.percentile(th[(s >= a) & (s <= b)], 2))), 0), round(float(np.degrees(np.percentile(th[(s >= a) & (s <= b)], 98))), 0)) for a, b in zip(q[:-1], q[1:]) if ((s >= a) & (s <= b)).sum() > 5])
        return False
    surf.t_range = (lo - margin * span, hi + margin * span)
    return True
