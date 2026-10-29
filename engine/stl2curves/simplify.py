"""Thin out an over-dense mesh without moving any corner off the true surface.

A CAD export at very fine resolution (a million triangles for a part a few centimetres
across) is slow to convert and gains nothing from the extra triangles: the curves are
found from the corners, which already lie on the true surfaces. This removes corners by
collapsing each onto a neighbour (a "half-edge collapse": no corner is ever moved or
created, as in meshoptimizer), for as long as the surface stays within a set distance of
the original:

- each corner carries the planes of the original triangles around it (a quadric, as in
  Garland and Heckbert's simplification), which ranks the collapses: flat areas and
  sharp edges cost nothing to thin along and everything to cross, so walls lose their
  extra corners, straight edges keep their ends and creases stay;
- every corner removed so far stays attached to the triangle nearest to it, and a
  collapse that changes that triangle must find it another within the distance (so no
  original corner ever ends up further than that from the simplified surface);
- no triangle may flip, turn sharply or go needle-thin, and the mesh must stay a closed
  surface (the two ends of an edge share exactly the two corners across it).

Collapses are chosen in passes of non-interfering edges, cheapest first, which suits
numpy far better than one collapse at a time.
"""

import math

import numpy as np

MAX_TURN_DEG = 20      # a triangle's normal may turn at most this much in one collapse
MIN_SHAPE = 0.02       # nor may it end up thinner than this (area / longest side squared)
CHUNK = 50000          # collapses checked at a time (memory)


def simplify(pts, tris, max_error, target=0, log=None):
    """Triangles (indices into pts, which are left unchanged) of the simplified mesh:
    every original corner lies within max_error of it."""
    tris = np.asarray(tris, dtype=np.int64).copy()
    n = len(pts)
    Q, W = _quadrics(pts, tris, n)
    gone = np.zeros(0, np.int64)       # removed corners
    near = np.zeros(0, np.int64)       # the triangle each lies nearest to
    for _ in range(200):
        if target and len(tris) <= target:
            break
        done = _pass(pts, tris, Q, W, max_error, gone, near)
        if done is None:
            break
        tris, gone, near, removed = done
        if log:
            log(f"    simplify pass: {removed} corners removed, {len(tris)} triangles")
        if removed < 0.002 * len(tris):
            break
    return tris


def _quadrics(pts, tris, n):
    """Per corner: sum over its triangles of area * (distance to the triangle's plane)^2,
    as the 10 coefficients of a symmetric 4x4 matrix, and the summed area."""
    a, b, c = (pts[tris[:, k]] for k in range(3))
    cross = np.cross(b - a, c - a)
    area = np.linalg.norm(cross, axis=1) / 2
    nrm = cross / np.maximum(2 * area, 1e-300)[:, None]
    d = -np.einsum("ij,ij->i", nrm, a)
    x, y, z = nrm.T
    coeff = np.c_[x * x, x * y, x * z, x * d, y * y, y * z, y * d, z * z, z * d, d * d] * area[:, None]
    Q = np.zeros((n, 10))
    W = np.zeros(n)
    for k in range(3):
        np.add.at(Q, tris[:, k], coeff)
        np.add.at(W, tris[:, k], area)
    return Q, W


def _evaluate(Q, p):
    """Quadric Q (m, 10) at points p (m, 3)."""
    x, y, z = p.T
    return (Q[:, 0] * x * x + 2 * Q[:, 1] * x * y + 2 * Q[:, 2] * x * z + 2 * Q[:, 3] * x
            + Q[:, 4] * y * y + 2 * Q[:, 5] * y * z + 2 * Q[:, 6] * y
            + Q[:, 7] * z * z + 2 * Q[:, 8] * z + Q[:, 9])


def _csr(keys, n):
    """Order of the entries by key, and where each key's run starts (n + 1 entries)."""
    order = np.argsort(keys, kind="stable")
    return order, np.searchsorted(keys[order], np.arange(n + 1))


def _expand(ptr, rows):
    """For each r in rows, the positions ptr[r] .. ptr[r + 1] - 1, all concatenated, and
    for each position which entry of rows it came from."""
    size = ptr[rows + 1] - ptr[rows]
    owner = np.repeat(np.arange(len(rows)), size)
    pos = np.repeat(ptr[rows] - (np.cumsum(size) - size), size) + np.arange(size.sum())
    return pos, owner


def _pass(pts, tris, Q, W, max_error, gone, near):
    n = len(pts)
    e = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    und = np.sort(e, axis=1)
    keys, count = np.unique(und[:, 0] * n + und[:, 1], return_counts=True)
    edges = np.c_[keys // n, keys % n]
    # corners on an open or shared-by-more-than-two edge stay put
    locked = np.zeros(n, bool)
    locked[edges[count != 2].ravel()] = True
    edges = edges[count == 2]
    edges = edges[~locked[edges[:, 0]] & ~locked[edges[:, 1]]]
    if not len(edges):
        return None
    # every edge both ways (src moves onto dst), cheapest first
    src = np.r_[edges[:, 0], edges[:, 1]]
    dst = np.r_[edges[:, 1], edges[:, 0]]
    cost = np.maximum(_evaluate(Q[src] + Q[dst], pts[dst]) / np.maximum(W[src] + W[dst], 1e-300), 0)
    ok = cost <= max_error ** 2
    order = np.argsort(cost[ok], kind="stable")
    src, dst = src[ok][order], dst[ok][order]
    if not len(src):
        return None

    both_ways = np.unique(np.r_[und[:, 0] * n + und[:, 1], und[:, 1] * n + und[:, 0]])
    nb = both_ways % n
    nb_ptr = np.searchsorted(both_ways // n, np.arange(n + 1))
    tri_order, tri_ptr = _csr(tris.ravel(), n)
    tri_of = tri_order // 3
    gone_order, gone_ptr = _csr(near, len(tris))      # removed corners by triangle

    # non-interfering collapses, cheapest first: no triangle may hold two corners that
    # move, and no corner that moves or is moved onto may neighbour another that moves
    # (so each collapse sees the neighbourhood it was checked against); the two ends
    # must share exactly the two corners across their edge, or the surface would pinch.
    # Each is checked (in batches) before it is taken.
    touched = np.zeros(n, bool)
    chosen = []
    for start in range(0, len(src), CHUNK):
        s_, d_ = src[start:start + CHUNK], dst[start:start + CHUNK]
        free = ~touched[s_] & ~touched[d_]
        s_, d_ = s_[free], d_[free]
        if not len(s_):
            continue
        good = _check(pts, tris, tri_ptr, tri_of, gone, gone_order, gone_ptr, s_, d_, max_error)[0]
        for s, d in zip(s_[good].tolist(), d_[good].tolist()):
            if touched[s] or touched[d]:
                continue
            ns = nb[nb_ptr[s]:nb_ptr[s + 1]]
            if touched[ns].any():
                continue
            nd = nb[nb_ptr[d]:nb_ptr[d + 1]]
            if len(np.intersect1d(ns, nd, assume_unique=True)) != 2:
                continue
            touched[s] = True
            touched[ns] = True
            chosen.append((s, d))
    if not chosen:
        return None
    cs, cd = np.array(chosen, dtype=np.int64).T

    # apply: the triangles round each moved corner now use its new corner; the removed
    # corners attached to them (and the moved corner itself) go to the nearest of those
    _, fan, fan_owner, pts_idx, pts_tri = _check(pts, tris, tri_ptr, tri_of, gone, gone_order,
                                                 gone_ptr, cs, cd, max_error, attach=True)
    new_tris = tris.copy()
    for k in range(3):
        col = new_tris[fan, k]
        new_tris[fan, k] = np.where(col == cs[fan_owner], cd[fan_owner], col)
    alive = ((new_tris[:, 0] != new_tris[:, 1]) & (new_tris[:, 1] != new_tris[:, 2])
             & (new_tris[:, 2] != new_tris[:, 0]))
    renumber = np.cumsum(alive) - 1
    reattached = np.zeros(len(gone), bool)
    reattached[gone_order[_expand(gone_ptr, fan)[0]]] = True
    gone = np.r_[gone[~reattached], pts_idx]
    near = np.r_[renumber[near[~reattached]], renumber[pts_tri]]
    Q[cd] += Q[cs]
    W[cd] += W[cs]
    return new_tris[alive], gone, near, len(cs)


def _check(pts, tris, tri_ptr, tri_of, gone, gone_order, gone_ptr, cs, cd, max_error, attach=False):
    """Which collapses (cs[i] onto cd[i]) keep every changed triangle from flipping,
    turning sharply or going needle-thin, and keep the moved corner and the removed
    corners attached to the changed triangles within the error of the triangles now
    there. With attach, also: the changed triangles, the collapse each belongs to, and
    each checked corner with the (surviving) triangle now nearest to it."""
    pos, which = _expand(tri_ptr, cs)
    fan = tri_of[pos]
    T = tris[fan]
    keep_tri = ~(T == cd[which][:, None]).any(axis=1)       # the two across the edge vanish
    moved = np.where(T == cs[which][:, None], cd[which][:, None], T)
    before, after = pts[T], pts[moved]
    n0 = np.cross(before[:, 1] - before[:, 0], before[:, 2] - before[:, 0])
    n1 = np.cross(after[:, 1] - after[:, 0], after[:, 2] - after[:, 0])
    a0, a1 = np.linalg.norm(n0, axis=1), np.linalg.norm(n1, axis=1)
    turn = np.einsum("ij,ij->i", n0, n1) / np.maximum(a0 * a1, 1e-300)
    side = np.linalg.norm(after - after[:, [1, 2, 0]], axis=2).max(axis=1)
    shape = a1 / 2 / np.maximum(side ** 2, 1e-300)
    bad = keep_tri & ((turn < math.cos(math.radians(MAX_TURN_DEG))) | (shape < MIN_SHAPE))
    reject = np.zeros(len(cs), bool)
    np.logical_or.at(reject, which, bad)

    # corners to check per collapse: the moving one, and the removed ones attached to
    # any of its triangles; each against every surviving new triangle of its collapse
    gpos, gowner = _expand(gone_ptr, fan)
    pts_idx = np.r_[cs, gone[gone_order[gpos]]]
    pts_of = np.r_[np.arange(len(cs)), which[gowner]]
    kept = np.nonzero(keep_tri)[0]
    kept_ptr = np.searchsorted(which[kept], np.arange(len(cs) + 1))
    ppos, powner = _expand(kept_ptr, pts_of)
    tri_rep = kept[ppos]
    d = _point_triangle_pairs(pts[pts_idx[powner]], after[tri_rep])
    nearest = np.full(len(pts_idx), np.inf)
    np.minimum.at(nearest, powner, d)
    worst = np.zeros(len(cs))
    np.maximum.at(worst, pts_of, nearest)
    good = ~reject & (worst <= max_error)
    if not attach:
        return good, None
    best = np.full(len(pts_idx), -1)
    order = np.lexsort((d, powner))
    first = np.r_[True, powner[order][1:] != powner[order][:-1]]
    best[powner[order][first]] = fan[tri_rep[order][first]]
    return good, fan, which, pts_idx, best


def _point_triangle_pairs(P, T):
    """Distance from each point P[i] to the triangle T[i] (i-th with i-th)."""
    a, b, c = T[:, 0], T[:, 1], T[:, 2]
    ab, ac, ap = b - a, c - a, P - a
    d1, d2 = (ab * ap).sum(-1), (ac * ap).sum(-1)
    bp, cp = P - b, P - c
    d3, d4 = (ab * bp).sum(-1), (ac * bp).sum(-1)
    d5, d6 = (ab * cp).sum(-1), (ac * cp).sum(-1)
    va, vb, vc = d3 * d6 - d5 * d4, d5 * d2 - d1 * d6, d1 * d4 - d3 * d2
    denom = va + vb + vc
    denom = np.where(np.abs(denom) > 1e-300, denom, 1e-300)
    v, w = vb / denom, vc / denom
    inside = (v >= 0) & (w >= 0) & (v + w <= 1)
    dist = np.where(inside, np.linalg.norm(P - (a + ab * v[:, None] + ac * w[:, None]), axis=1), np.inf)
    for e0, e1 in ((a, b), (b, c), (c, a)):
        e = e1 - e0
        t = np.clip(((P - e0) * e).sum(-1) / np.maximum((e * e).sum(-1), 1e-300), 0, 1)
        dist = np.minimum(dist, np.linalg.norm(P - (e0 + e * t[:, None]), axis=1))
    return dist
