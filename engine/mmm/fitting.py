"""Weighted least squares fitting of analytic surface primitives.

Every primitive exposes:
    distance(P)          -> unsigned distance of points to the surface
    normal(P)            -> unit "natural" normal at the projection of P
    fit(P, N, ext, W)    -> new primitive (static), or None
    refit(P, N, W)       -> new primitive fitted to the points (or None)

W are per-point weights (typically the mesh area a vertex represents), so a
long strip of a cylinder outweighs many tiny triangles of a neighbouring
fillet in the same neighbourhood.

A surface turned about a known axis (on_axis: a hole's chamfer, a cap on a
pin) has only its profile free: the radius and slope of a line, or a circle
in the plane through the axis. Fitted that way it needs only a band of a few
triangles, where a free fit needs a neighbourhood big enough to pin down the
axis too, and that neighbourhood takes in the faces beside a narrow band.
"""
# Part of Manifold Mesh Mix (c) 2026 Tuna-The-First and Claude, AGPL-3.0, commit 4cf72ea.
# Modified 2026-10-28 (b13): surfaces on a known axis (on_axis, AxisCylinder, AxisCone,
# AxisTorus, AxisSphere): only the profile is fitted, refits keep the axis, and two on one
# axis are the same surface when their profiles agree (Segmenter._axis_hypotheses).
import numpy as np
from scipy.optimize import least_squares

EPS = 1e-12


def unit(v):
    n = np.linalg.norm(v)
    return v / n if n > EPS else v


def perp_basis(d):
    a = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 else np.array([0, 1.0, 0])
    u = unit(np.cross(d, a))
    v = np.cross(d, u)
    return u, v


def dir_from_angles(th, ph):
    return np.array([np.sin(th) * np.cos(ph), np.sin(th) * np.sin(ph), np.cos(th)])


def ddir_dangles(th, ph):
    """Partial derivatives of dir_from_angles w.r.t. theta and phi."""
    return (
        np.array([np.cos(th) * np.cos(ph), np.cos(th) * np.sin(ph), -np.sin(th)]),
        np.array([-np.sin(th) * np.sin(ph), np.sin(th) * np.cos(ph), 0.0]),
    )


def angles_from_dir(d):
    d = unit(d)
    return np.arccos(np.clip(d[2], -1, 1)), np.arctan2(d[1], d[0])


def _norm_w(W, n):
    if W is None:
        return np.ones(n)
    W = np.asarray(W, dtype=np.float64)
    s = W.sum()
    if not np.isfinite(s) or s <= 0:
        return np.ones(n)
    return W * (n / s)


def _subsample(P, N=None, W=None, k=500, seed=0):
    if len(P) <= k:
        return P, N, W
    rng = np.random.default_rng(seed)
    if W is not None:
        p = np.asarray(W, dtype=np.float64)
        p = p / p.sum()
        idx = rng.choice(len(P), k, replace=False, p=p)
        # importance sampled: equal weights afterwards
        return P[idx], (None if N is None else N[idx]), None
    idx = rng.choice(len(P), k, replace=False)
    return P[idx], (None if N is None else N[idx]), None


def fit_circle_2d(Q, refine=True, W=None):
    """Algebraic (Kasa) circle fit, optionally refined geometrically."""
    x, y = Q[:, 0], Q[:, 1]
    w = np.sqrt(_norm_w(W, len(Q)))
    A = np.stack([2 * x, 2 * y, np.ones_like(x)], axis=1) * w[:, None]
    b = (x * x + y * y) * w
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy, k = sol
    r2 = k + cx * cx + cy * cy
    if r2 <= 0:
        return None
    r = np.sqrt(r2)
    if not refine:
        return np.array([cx, cy]), abs(r)

    def res(p):
        return (np.hypot(x - p[0], y - p[1]) - p[2]) * w

    try:
        out = least_squares(res, [cx, cy, r], method="lm", max_nfev=60)
        cx, cy, r = out.x
    except Exception:
        pass
    return np.array([cx, cy]), abs(r)


def _lsq(res, x0, nfev, jac=None):
    try:
        if jac is None:
            return least_squares(res, x0, method="lm", max_nfev=nfev).x
        return least_squares(res, x0, jac=jac, method="lm", max_nfev=nfev).x
    except Exception:
        return None


# ----------------------------------------------------------------------------
class Plane:
    kind = "plane"
    rank = 0

    def __init__(self, origin, normal):
        self.origin = np.asarray(origin, float)
        self.n = unit(np.asarray(normal, float))

    def distance(self, P):
        return np.abs((P - self.origin) @ self.n)

    def normal(self, P):
        return np.tile(self.n, (len(P), 1))

    @staticmethod
    def fit(P, N=None, ext=None, W=None):
        w = _norm_w(W, len(P))
        c = (P * w[:, None]).sum(0) / w.sum()
        Q = (P - c) * np.sqrt(w)[:, None]
        _, _, vt = np.linalg.svd(Q, full_matrices=False)
        n = vt[-1]
        if N is not None and np.dot(n, np.asarray(N).sum(0)) < 0:
            n = -n
        return Plane(c, n)

    def refit(self, P, N=None, W=None):
        p = Plane.fit(P, None, W=W)
        if np.dot(p.n, self.n) < 0:
            p.n = -p.n
        return p

    def degenerate(self, ext):
        return False

    def same_as(self, o, tol, ext):
        return o.kind == "plane" and abs(abs(np.dot(self.n, o.n)) - 1) < 1e-6 + (tol / ext) ** 2 and o.distance(self.origin[None])[0] < tol

    def describe(self):
        return "plane"


class Sphere:
    kind = "sphere"
    rank = 3

    def __init__(self, c, r):
        self.c = np.asarray(c, float)
        self.r = float(r)

    def distance(self, P):
        return np.abs(np.linalg.norm(P - self.c, axis=1) - self.r)

    def normal(self, P):
        v = P - self.c
        return v / np.maximum(np.linalg.norm(v, axis=1), EPS)[:, None]

    @staticmethod
    def fit(P, N=None, ext=None, W=None):
        if len(P) < 4:
            return None
        w = np.sqrt(_norm_w(W, len(P)))
        A = np.hstack([2 * P, np.ones((len(P), 1))]) * w[:, None]
        b = (P * P).sum(1) * w
        sol, *_ = np.linalg.lstsq(A, b, rcond=None)
        c = sol[:3]
        r2 = sol[3] + c @ c
        if r2 <= 0:
            return None
        return Sphere(c, np.sqrt(r2))._refine(P, W)

    def _refine(self, P, W=None):
        Ps, _, Ws = _subsample(P, None, W)
        w = np.sqrt(_norm_w(Ws, len(Ps)))

        def res(p):
            return (np.linalg.norm(Ps - p[:3], axis=1) - p[3]) * w

        def jac(p):
            v = Ps - p[:3]
            n = np.maximum(np.linalg.norm(v, axis=1), EPS)
            J = np.empty((len(Ps), 4))
            J[:, :3] = -(v / n[:, None])
            J[:, 3] = -1.0
            return J * w[:, None]

        x = _lsq(res, np.r_[self.c, self.r], 80, jac)
        return self if x is None else Sphere(x[:3], abs(x[3]))

    def refit(self, P, N=None, W=None):
        return self._refine(P, W)

    def degenerate(self, ext):
        return self.r > 30 * ext or self.r < 1e-6 * ext

    def same_as(self, o, tol, ext):
        return o.kind == "sphere" and np.linalg.norm(self.c - o.c) < tol and abs(self.r - o.r) < tol

    def describe(self):
        return f"sphere r={self.r:.4g}"


class Cylinder:
    kind = "cylinder"
    rank = 1

    def __init__(self, a, d, r):
        d = unit(np.asarray(d, float))
        a = np.asarray(a, float)
        # store the axis point closest to the origin for a canonical form
        self.a = a - (a @ d) * d
        self.d = d
        self.r = float(r)

    def _radial(self, P):
        rel = P - self.a
        h = rel @ self.d
        rad = rel - np.outer(h, self.d)
        return rad, h

    def distance(self, P):
        rad, _ = self._radial(P)
        return np.abs(np.linalg.norm(rad, axis=1) - self.r)

    def normal(self, P):
        rad, _ = self._radial(P)
        return rad / np.maximum(np.linalg.norm(rad, axis=1), EPS)[:, None]

    @staticmethod
    def axis_from_normals(N, W=None):
        w = _norm_w(W, len(N))
        M = (N * w[:, None]).T @ N
        ev, v = np.linalg.eigh(M)
        return v[:, 0], ev / max(ev.sum(), EPS)

    @staticmethod
    def fit(P, N, ext=None, W=None, d=None):
        if len(P) < 5:
            return None
        if d is None:
            if N is None or len(N) < 3:
                return None
            d, ev = Cylinder.axis_from_normals(N, W)
            # normals must span a plane (second eigenvalue not tiny)
            if ev[1] < 1e-6:
                return None
        u, v = perp_basis(d)
        Q = np.stack([P @ u, P @ v], axis=1)
        fc = fit_circle_2d(Q, W=W)
        if fc is None:
            return None
        c2, r = fc
        a = c2[0] * u + c2[1] * v
        return Cylinder(a, d, r)._refine(P, W)

    def _refine(self, P, W=None):
        Ps, _, Ws = _subsample(P, None, W)
        w = np.sqrt(_norm_w(Ws, len(Ps)))
        th0, ph0 = angles_from_dir(self.d)
        u, v = perp_basis(self.d)
        a0 = self.a

        def unpack(p):
            return a0 + p[2] * u + p[3] * v, dir_from_angles(p[0], p[1]), p[4]

        def res(p):
            a, d, r = unpack(p)
            rel = Ps - a
            h = rel @ d
            return (np.linalg.norm(rel - np.outer(h, d), axis=1) - r) * w

        def jac(p):
            a, d, r = unpack(p)
            rel = Ps - a
            h = rel @ d
            wv = rel - np.outer(h, d)
            rho = np.maximum(np.linalg.norm(wv, axis=1), EPS)
            wh = wv / rho[:, None]
            dth, dph = ddir_dangles(p[0], p[1])
            J = np.empty((len(Ps), 5))
            J[:, 0] = -h * (wh @ dth)
            J[:, 1] = -h * (wh @ dph)
            J[:, 2] = -(wh @ u)
            J[:, 3] = -(wh @ v)
            J[:, 4] = -1.0
            return J * w[:, None]

        x = _lsq(res, [th0, ph0, 0.0, 0.0, self.r], 60, jac)
        if x is None:
            return self
        a, d, r = unpack(x)
        return Cylinder(a, d, abs(r))

    def refit(self, P, N=None, W=None):
        return self._refine(P, W)

    def degenerate(self, ext):
        return self.r > 20 * ext or self.r < 1e-6 * ext

    def same_as(self, o, tol, ext):
        if o.kind != "cylinder" or abs(self.r - o.r) > tol:
            return False
        if abs(abs(np.dot(self.d, o.d)) - 1) > (tol / max(ext, EPS)) ** 2 + 1e-7:
            return False
        rel = o.a - self.a
        off = rel - (rel @ self.d) * self.d
        return np.linalg.norm(off) < tol

    def describe(self):
        return f"cylinder r={self.r:.4g}"


class Cone:
    kind = "cone"
    rank = 2

    def __init__(self, apex, d, alpha):
        self.apex = np.asarray(apex, float)
        self.d = unit(np.asarray(d, float))  # from apex toward the opening
        self.alpha = float(alpha)  # half angle

    def _parts(self, P):
        rel = P - self.apex
        h = rel @ self.d
        rad = rel - np.outer(h, self.d)
        rho = np.linalg.norm(rad, axis=1)
        return rel, h, rad, rho

    def signed(self, P):
        _, h, _, rho = self._parts(P)
        return rho * np.cos(self.alpha) - h * np.sin(self.alpha)

    def distance(self, P):
        rel, h, rad, rho = self._parts(P)
        s = rho * np.cos(self.alpha) - h * np.sin(self.alpha)
        # points behind the apex (other nappe) measure to the apex instead
        along = rho * np.sin(self.alpha) + h * np.cos(self.alpha)
        return np.where(along >= 0, np.abs(s), np.linalg.norm(rel, axis=1))

    def normal(self, P):
        _, h, rad, rho = self._parts(P)
        rd = rad / np.maximum(rho, EPS)[:, None]
        return rd * np.cos(self.alpha) - np.outer(np.full(len(P), np.sin(self.alpha)), self.d)

    @staticmethod
    def fit(P, N, ext=None, W=None):
        if len(P) < 6:
            return None
        best = None
        cands = []
        c1 = Cone._fit_from_normals(P, N, W)
        if c1 is not None:
            cands.append(c1)
            spread = float(np.linalg.norm(P.max(0) - P.min(0)))
            w0 = _norm_w(W, len(P))
            if np.sqrt(np.average(c1.distance(P) ** 2, weights=w0)) <= 1e-4 * max(spread, EPS):
                return c1  # already an excellent fit
        # Narrow strips of a cone look like a tilted cylinder to the normal
        # based estimate; start from a cylinder fit and let the solver open
        # up the cone instead.
        cyl = Cylinder.fit(P, N, ext=ext, W=W) if N is not None else None
        if cyl is not None:
            for sgn in (1.0, -1.0):
                al = np.radians(3.0)
                d = sgn * cyl.d
                h = (P - cyl.a) @ d
                # apex behind the points so they sit on the opening side
                apex = cyl.a + (h.min() - cyl.r / np.tan(al)) * d
                c2 = Cone(apex, d, al)._refine(P, W)
                if c2 is not None:
                    cands.append(c2)
        w = _norm_w(W, len(P))
        for c in cands:
            if c is None or not np.isfinite(c.apex).all():
                continue
            e = float(np.sqrt(np.average(c.distance(P) ** 2, weights=w)))
            if best is None or e < best[0]:
                best = (e, c)
        return None if best is None else best[1]

    @staticmethod
    def _fit_from_normals(P, N, W):
        if N is None or len(N) < 6:
            return None
        w = _norm_w(W, len(N))
        mu = (N * w[:, None]).sum(0) / w.sum()
        C = ((N - mu) * w[:, None]).T @ (N - mu)
        ev, v = np.linalg.eigh(C)
        if ev[1] < 1e-9 * max(ev[2], EPS):
            return None
        e = v[:, 0]
        m = float(np.average(N @ e, weights=w))
        if abs(m) < np.sin(np.radians(1.0)) or abs(m) > np.sin(np.radians(88.0)):
            return None  # cylinder-like or plane-like
        d = -e if m > 0 else e
        alpha = np.arcsin(min(abs(m), 0.999999))
        # apex: tangent planes pass through it
        M = (N * w[:, None]).T @ N
        b = (N * (w * (N * P).sum(1))[:, None]).sum(0)
        try:
            apex = np.linalg.solve(M, b)
        except np.linalg.LinAlgError:
            return None
        # the axis runs from the apex toward the points (the sign from the
        # normals alone is wrong for an inward-facing chamfer, a countersink:
        # the points then sat behind the apex and the refinement ran off)
        if float(np.average((P - apex) @ d, weights=w)) < 0:
            d = -d
        return Cone(apex, d, alpha)._refine(P, W)

    def _refine(self, P, W=None):
        Ps, _, Ws = _subsample(P, None, W)
        w = np.sqrt(_norm_w(Ws, len(Ps)))
        th0, ph0 = angles_from_dir(self.d)

        def unpack(p):
            return p[:3], dir_from_angles(p[3], p[4]), p[5]

        def res(p):
            a, d, al = unpack(p)
            rel = Ps - a
            h = rel @ d
            rho = np.linalg.norm(rel - np.outer(h, d), axis=1)
            return (rho * np.cos(al) - h * np.sin(al)) * w

        def jac(p):
            a, d, al = unpack(p)
            rel = Ps - a
            h = rel @ d
            wv = rel - np.outer(h, d)
            rho = np.maximum(np.linalg.norm(wv, axis=1), EPS)
            wh = wv / rho[:, None]
            ca, sa = np.cos(al), np.sin(al)
            dth, dph = ddir_dangles(p[3], p[4])
            gd = -ca * h[:, None] * wh - sa * rel  # d f / d d
            J = np.empty((len(Ps), 6))
            J[:, :3] = -ca * wh + sa * d[None, :]
            J[:, 3] = gd @ dth
            J[:, 4] = gd @ dph
            J[:, 5] = -rho * sa - h * ca
            return J * w[:, None]

        x = _lsq(res, np.r_[self.apex, th0, ph0, self.alpha], 80, jac)
        if x is None:
            return self
        a, d, al = unpack(x)
        if al < 0:
            al = -al
        if al > np.pi / 2:
            # flipped solution: same cone with the axis reversed
            al = np.pi - al
            d = -d
        return Cone(a, d, al)

    def refit(self, P, N=None, W=None):
        return self._refine(P, W)

    def degenerate(self, ext):
        return (
            not np.isfinite(self.apex).all()
            or self.alpha < np.radians(0.5)
            or self.alpha > np.radians(89.0)
            or np.linalg.norm(self.apex) > 1e3 * max(ext, 1.0)
        )

    def same_as(self, o, tol, ext):
        return (
            o.kind == "cone"
            and np.linalg.norm(self.apex - o.apex) < tol
            and np.dot(self.d, o.d) > 1 - 1e-6
            and abs(self.alpha - o.alpha) < tol / max(ext, EPS)
        )

    def describe(self):
        return f"cone {np.degrees(self.alpha):.3g} deg"


class Torus:
    kind = "torus"
    rank = 4

    def __init__(self, c, d, R, r):
        self.c = np.asarray(c, float)
        self.d = unit(np.asarray(d, float))
        self.R = float(R)
        self.r = float(r)

    def _spine(self, P):
        rel = P - self.c
        h = rel @ self.d
        rad = rel - np.outer(h, self.d)
        rho = np.maximum(np.linalg.norm(rad, axis=1), EPS)
        return self.c + rad / rho[:, None] * self.R

    def distance(self, P):
        q = self._spine(P)
        return np.abs(np.linalg.norm(P - q, axis=1) - self.r)

    def normal(self, P):
        q = self._spine(P)
        v = P - q
        return v / np.maximum(np.linalg.norm(v, axis=1), EPS)[:, None]

    @staticmethod
    def _circle3d(Q, w=None):
        ww = _norm_w(w, len(Q))
        c = (Q * ww[:, None]).sum(0) / ww.sum()
        _, s, vt = np.linalg.svd((Q - c) * np.sqrt(ww)[:, None], full_matrices=False)
        d = vt[2]
        u, v = vt[0], vt[1]
        Q2 = np.stack([(Q - c) @ u, (Q - c) @ v], axis=1)
        fc = fit_circle_2d(Q2, refine=False, W=w)
        if fc is None:
            return None
        c2, R = fc
        center = c + c2[0] * u + c2[1] * v
        res = np.hypot(Q2[:, 0] - c2[0], Q2[:, 1] - c2[1]) - R
        planar = (Q - center) @ d
        err = np.sqrt(np.average(res ** 2 + planar ** 2, weights=ww))
        return center, d, R, err

    @staticmethod
    def fit(P, N, ext=None, W=None):
        if len(P) < 10 or N is None:
            return None
        if ext is None:
            ext = float(np.linalg.norm(P.max(0) - P.min(0)))
        Ps, Ns, Ws = _subsample(P, N, W, 300)
        best = None
        for sgn in (1.0, -1.0):
            def cost(lr):
                r = np.exp(lr)
                out = Torus._circle3d(Ps - sgn * r * Ns, Ws)
                return (np.inf, None) if out is None else (out[3], out)

            lo, hi = np.log(ext * 1e-3), np.log(ext * 3)
            grid = np.linspace(lo, hi, 20)
            vals = [cost(g)[0] for g in grid]
            k = int(np.argmin(vals))
            a = grid[max(k - 1, 0)]
            b = grid[min(k + 1, len(grid) - 1)]
            gr = (np.sqrt(5) - 1) / 2
            x1 = b - gr * (b - a)
            x2 = a + gr * (b - a)
            f1, f2 = cost(x1)[0], cost(x2)[0]
            for _ in range(22):
                if f1 < f2:
                    b, x2, f2 = x2, x1, f1
                    x1 = b - gr * (b - a)
                    f1 = cost(x1)[0]
                else:
                    a, x1, f1 = x1, x2, f2
                    x2 = a + gr * (b - a)
                    f2 = cost(x2)[0]
            lr = (a + b) / 2
            err, out = cost(lr)
            if out is not None and (best is None or err < best[0]):
                best = (err, np.exp(lr), out)
        if best is None:
            return None
        _, r, (c, d, R, _) = best
        return Torus(c, d, R, r)._refine(P, W)

    def _refine(self, P, W=None):
        Ps, _, Ws = _subsample(P, None, W)
        w = np.sqrt(_norm_w(Ws, len(Ps)))
        th0, ph0 = angles_from_dir(self.d)

        def unpack(p):
            return p[:3], dir_from_angles(p[3], p[4]), p[5], p[6]

        def res(p):
            c, d, R, r = unpack(p)
            rel = Ps - c
            h = rel @ d
            rho = np.linalg.norm(rel - np.outer(h, d), axis=1)
            return (np.hypot(rho - R, h) - r) * w

        def jac(p):
            c, d, R, r = unpack(p)
            rel = Ps - c
            h = rel @ d
            wv = rel - np.outer(h, d)
            rho = np.maximum(np.linalg.norm(wv, axis=1), EPS)
            wh = wv / rho[:, None]
            q = np.maximum(np.hypot(rho - R, h), EPS)
            dr = rho - R
            dth, dph = ddir_dangles(p[3], p[4])
            gd = (h / q)[:, None] * (rel - dr[:, None] * wh)
            J = np.empty((len(Ps), 7))
            J[:, :3] = -((dr / q)[:, None] * wh + (h / q)[:, None] * d[None, :])
            J[:, 3] = gd @ dth
            J[:, 4] = gd @ dph
            J[:, 5] = -dr / q
            J[:, 6] = -1.0
            return J * w[:, None]

        x = _lsq(res, np.r_[self.c, th0, ph0, self.R, self.r], 80, jac)
        if x is None:
            return self
        c, d, R, r = unpack(x)
        return Torus(c, d, abs(R), abs(r))

    def refit(self, P, N=None, W=None):
        return self._refine(P, W)

    def degenerate(self, ext):
        return self.R < 0.05 * self.r or self.R > 20 * ext or self.r > 20 * ext or self.r < 1e-6 * ext

    def same_as(self, o, tol, ext):
        return (
            o.kind == "torus"
            and np.linalg.norm(self.c - o.c) < tol
            and abs(abs(np.dot(self.d, o.d)) - 1) < 1e-6
            and abs(self.R - o.R) < tol
            and abs(self.r - o.r) < tol
        )

    def describe(self):
        return f"torus R={self.R:.4g} r={self.r:.4g}"


CURVED = [Cylinder, Cone, Sphere, Torus]


# ----------------------------------------------------------------------------
# Surfaces turned about a known axis


def _profile_coords(a, d, P):
    """(rho, z): distance from the axis through a along unit d, and height along it."""
    rel = P - a
    z = rel @ d
    return np.linalg.norm(rel - np.outer(z, d), axis=1), z


class _OnAxis:
    """A surface turned about a known axis (ka, kd): refits move only its
    profile, and on one axis two are the same surface when their profiles
    agree over the heights both cover (zr: the height range fitted)."""

    def refit(self, P, N=None, W=None):
        p = on_axis(self.kind, self.ka, self.kd, P, W)
        return self if p is None else p

    def same_as(self, o, tol, ext):
        if not isinstance(o, _OnAxis) or o.kind != self.kind:
            return super().same_as(o, tol, ext)
        if abs(self.kd @ o.kd) < np.cos(np.radians(0.5)):
            return False
        off = o.ka - self.ka
        if np.linalg.norm(off - (off @ self.kd) * self.kd) > 4 * tol:
            return False
        if self.kind == "cylinder":
            return abs(self.r - o.r) <= tol
        if self.kind == "cone":
            z = np.r_[self.zr, o.zr]
            return float(np.abs(self.c0 + self.k * z - (o.c0 + o.k * z)).max()) <= tol
        if self.kind == "torus":
            return abs(self.R - o.R) <= tol and abs(self.r - o.r) <= tol and np.linalg.norm(self.c - o.c) <= tol
        return np.linalg.norm(self.c - o.c) <= tol and abs(self.r - o.r) <= tol


class AxisCylinder(_OnAxis, Cylinder):
    pass


class AxisCone(_OnAxis, Cone):
    pass


class AxisTorus(_OnAxis, Torus):
    pass


class AxisSphere(_OnAxis, Sphere):
    pass


STRAIGHT_SLOPE = 1e-4   # a line profile this close to parallel to its axis is a cylinder's


def on_axis(kind, a, d, P, W=None):
    """A cylinder, cone, torus or sphere (kind) about the axis through a along
    d, fitted to points P (weights W): only the profile is free, so a narrow
    band's few corners are enough. None when the points can't give it (no
    height to slope over, a circle that doesn't keep clear of the axis)."""
    d = unit(np.asarray(d, float))
    a = np.asarray(a, float)
    a = a - (a @ d) * d
    rho, z = _profile_coords(a, d, P)
    w = _norm_w(W, len(P))
    if kind in ("cylinder", "cone"):
        if kind == "cone" and np.ptp(z) <= EPS * max(1.0, float(np.abs(z).max())):
            return None
        if kind == "cylinder":
            k, c0 = 0.0, float(np.average(rho, weights=w))
        else:
            k, c0 = np.polyfit(z, rho, 1, w=np.sqrt(w))
            k, c0 = float(k), float(c0)
        if abs(k) < STRAIGHT_SLOPE:
            if kind == "cone":
                return None     # (the cylinder is tried as itself)
            p = AxisCylinder(a, d, c0)
        else:
            z0 = -c0 / k        # where the profile meets the axis: the apex
            p = AxisCone(a + z0 * d, d if k > 0 else -d, float(np.arctan(abs(k))))
        p.c0, p.k = c0, k
    elif kind == "sphere":
        # rho^2 + (z - zc)^2 = r^2, linear in zc and r^2 - zc^2
        A = np.stack([2 * z, np.ones_like(z)], 1) * np.sqrt(w)[:, None]
        b = (rho * rho + z * z) * np.sqrt(w)
        (zc, e), *_ = np.linalg.lstsq(A, b, rcond=None)
        r2 = e + zc * zc
        if r2 <= 0:
            return None
        p = AxisSphere(a + zc * d, np.sqrt(r2))
    else:
        fc = fit_circle_2d(np.stack([rho, z], 1), W=W)
        if fc is None:
            return None
        (rc, zc), r = fc
        if rc <= 0.05 * r:
            return None         # (Torus.degenerate: a spindle, nearly a sphere)
        p = AxisTorus(a + zc * d, d, rc, r)
    p.ka, p.kd = a, d
    p.zr = np.array([z.min(), z.max()])
    return p
