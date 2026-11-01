"""
Mirror symmetry: a mesh that is its own mirror image across a plane.

Many printed parts (boxes, lids, clasps) are drawn symmetric about a midplane. Detection
then only needs to search one half: every surface found there, mirrored, is tried on the
other half's facets (growing a known surface is cheap; finding one is not), and what is
left there is searched as usual. The whole part is still built as one, so nothing is cut
or sewn along the plane, and a part whose two sides differ in places (a thread, which
mirrored turns the other way; a logo) just has those places searched normally.

A plane is a candidate if it passes through the surface's area-weighted centroid square
to one of its principal axes or to a coordinate axis (CAD parts sit square to the axes,
and a symmetric surface's centroid lies on its mirror plane); it is taken if the mesh
corners reflected across it land back on the mesh. The two halves' triangles needn't
mirror each other, so a corner is measured to the nearest point of the surface, not to
the nearest corner, and allowed the tessellation's own chord error.
"""
import math

import numpy as np
from scipy.spatial import cKDTree

SAMPLES = 4000          # mesh corners reflected to test a plane, landing back on the mesh ...
TOL = 0.02              # mm: ... within this (a curved face's chords stray about as far)
SPACING = 1.0           # mm: points laid over each triangle to find the triangles near a point
SAME_DEG = 1.0          # candidate planes closer than this in direction are one
MIN_SHARE = 0.5         # a plane mapping less of the surface onto itself isn't worth it
BAND = 0.5              # mm: facets this near the plane are searched from both sides


def _point_triangle(P, A, B, C):
    """Distance from each point P[i] to triangle (A[i], B[i], C[i])."""
    AB, AC, AP = B - A, C - A, P - A
    n = np.cross(AB, AC)
    nn = np.einsum("ij,ij->i", n, n)
    good = nn > 1e-24
    # (inside the triangle, seen square to it: the distance to its plane)
    w = np.zeros((len(P), 3))
    d00, d01, d11 = (np.einsum("ij,ij->i", AB, AB), np.einsum("ij,ij->i", AB, AC), np.einsum("ij,ij->i", AC, AC))
    d20, d21 = np.einsum("ij,ij->i", AP, AB), np.einsum("ij,ij->i", AP, AC)
    den = np.where(good, d00 * d11 - d01 * d01, 1.0)
    v = (d11 * d20 - d01 * d21) / den
    u = (d00 * d21 - d01 * d20) / den
    inside = good & (v >= 0) & (u >= 0) & (u + v <= 1)
    plane = np.abs(np.einsum("ij,ij->i", AP, n)) / np.sqrt(np.where(good, nn, 1.0))
    # (else the nearest of its three edges)
    def seg(P, X, Y):
        D = Y - X
        t = np.clip(np.einsum("ij,ij->i", P - X, D) / np.maximum(np.einsum("ij,ij->i", D, D), 1e-24), 0, 1)
        return np.linalg.norm(P - (X + t[:, None] * D), axis=1)
    edge = np.minimum(np.minimum(seg(P, A, B), seg(P, B, C)), seg(P, C, A))
    return np.where(inside, plane, edge)


class _Surface:
    """Distances from points to a triangle mesh's surface."""

    def __init__(self, pts, tris):
        self.T = pts[tris]
        A, B, C = self.T[:, 0], self.T[:, 1], self.T[:, 2]
        # (points along each edge every SPACING, and inside as many as its area holds
        # at that spacing: a grid over the triangle put 45,000 on one long sliver)
        owner, samples = [np.arange(len(tris))] * 3, [A, B, C]
        for X, Y in ((A, B), (B, C), (C, A)):
            n = np.floor(np.linalg.norm(Y - X, axis=1) / SPACING).astype(int)
            k = np.repeat(np.arange(len(tris)), n)
            t = (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n) + 1) / np.repeat(n + 1, n)
            samples.append(X[k] + t[:, None] * (Y - X)[k])
            owner.append(k)
        area = 0.5 * np.linalg.norm(np.cross(B - A, C - A), axis=1)
        n = np.floor(2 * area / SPACING ** 2).astype(int)
        k = np.repeat(np.arange(len(tris)), n)
        u, v = np.random.default_rng(0).random((2, len(k)))
        flip = u + v > 1
        u, v = np.where(flip, 1 - u, u), np.where(flip, 1 - v, v)
        samples.append(A[k] + u[:, None] * (B - A)[k] + v[:, None] * (C - A)[k])
        owner.append(k)
        self.owner = np.concatenate(owner)
        self.tree = cKDTree(np.concatenate(samples))

    def distance(self, P, k=8):
        _, idx = self.tree.query(P, k=k)
        best = np.full(len(P), np.inf)
        for col in range(k):
            t = self.owner[idx[:, col]]
            best = np.minimum(best, _point_triangle(P, self.T[t, 0], self.T[t, 1], self.T[t, 2]))
        return best


def planes(pts, tris, tol=TOL):
    """The mesh's mirror planes, as (unit normal n, offset d, share) with n.x = d on
    them, the share of the surface's corners they map back onto it at least MIN_SHARE,
    best first; [] if it has none. (Most parts are symmetric only in part: a tray with a
    handle on one end, a box with a hole off centre.)"""
    pts = np.asarray(pts, float)
    tris = np.asarray(tris)
    T = pts[tris]
    area = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)
    if area.sum() <= 0:
        return []
    cent = T.mean(axis=1)
    c = (cent * area[:, None]).sum(axis=0) / area.sum()
    mid = (pts.min(axis=0) + pts.max(axis=0)) / 2
    X = cent - c
    cov = (X * area[:, None]).T @ X / area.sum()
    normals = []
    for n in list(np.eye(3)) + list(np.linalg.eigh(cov)[1].T):     # (the axes first)
        n = n / np.linalg.norm(n)
        if all(abs(n @ m) < math.cos(math.radians(SAME_DEG)) for m in normals):
            normals.append(n)
    rng = np.random.default_rng(0)
    used = np.unique(tris)
    sample = pts[rng.choice(used, min(SAMPLES, len(used)), replace=False)]
    surface = _Surface(pts, tris)
    found = []
    for n in normals:
        best = None
        for d in sorted({round(float(n @ c), 9), round(float(n @ mid), 9)}):
            share = float(np.mean(surface.distance(mirror_point(sample, (n, d))) <= tol))
            if best is None or share > best[1]:
                best = (d, share)
        if best[1] >= MIN_SHARE:
            found.append((n, best[0], best[1]))
    found.sort(key=lambda x: -x[2])
    return found


def mirrored(mesh, plane, tol=TOL, band=BAND):
    """Per facet: does it lie on the far side of the plane (beyond band), every corner of
    it mirroring onto the mesh within tol? (Its surface will be found from the near side.)"""
    pts = mesh.pts
    s = side(mesh.fcent, plane)
    far = np.nonzero(s < -band)[0]
    out = np.zeros(len(mesh.farea), bool)
    if not len(far):
        return out
    V = np.unique(np.concatenate([mesh.fverts[f] for f in far]))
    surface = _Surface(pts, mesh.tris)
    ok = np.zeros(len(pts), bool)
    ok[V] = surface.distance(mirror_point(pts[V], plane)) <= tol
    out[far] = [bool(ok[mesh.fverts[f]].all()) for f in far]
    return out


def side(P, plane):
    """Signed distance of points from the plane (positive on the side searched)."""
    n, d = plane[0], plane[1]
    return np.asarray(P) @ n - d


def mirror_point(P, plane):
    n, d = plane[0], plane[1]
    P = np.asarray(P, float)
    return P - 2 * ((P @ n) - d)[..., None] * n


def mirror_direction(v, plane):
    n = plane[0]
    v = np.asarray(v, float)
    return v - 2 * (v @ n)[..., None] * n if v.ndim > 1 else v - 2 * (v @ n) * n


def mirror_model(model, plane):
    """The surface model mirrored across the plane (cylinders, cones, spheres, tori), or
    None for any other."""
    from .features import Revolved, Sphere
    if isinstance(model, Sphere):
        return Sphere(mirror_point(model.c, plane), model.r)
    if isinstance(model, Revolved):
        a = mirror_point(model.a, plane)
        d = mirror_direction(model.d, plane)
        # (heights are measured from the axis point nearest the origin, which moves along
        # the mirrored axis: the profile is shifted by as much)
        shift = float(a @ (d / np.linalg.norm(d)))
        if model.line:
            c0, k = model.line
            return Revolved(a, d, line=(c0 - k * shift, k))
        rc, zc, r = model.circle
        return Revolved(a, d, circle=(rc, zc + shift, r))
    return None
