"""Flat walls of a mesh lying on each other, facing each other with no gap between them.

A mesh can hold two flat walls in one plane, one facing each way, over the same area: the bridging plates in a
hex-nut pocket, whose tops lie on the pocket's ceiling (the Raspberry Pi 5 case: 12.6 mm^2 at each of its four
bosses); a body resting flush on another (the P1S risers); a zero-thickness double wall (the repeater mount).
There is material on both sides, or none on either, so neither wall is surface. repair._duplicates already drops
such a pair when both triangles have the same three corners; here their triangles differ. Built as they are, they
make a solid with two faces on top of each other. OpenCascade calls it valid, and Fusion refuses every face move
and delete on the whole body: on the Pi case 0 of 80, with any one boss left; with them gone, 58 of 80.

Everything in such a plane is treated as a chain. Split each triangle's edges at every corner on them and add them up with
direction: an edge met both ways cancels. A wall facing up runs counter-clockwise seen from above and one facing down clockwise,
so inside either wall the edges cancel, and so do two walls lying on each other, along with the edges they shared. What
survives is the outline of what's covered by one facing more than the other, closed by construction (the boundary of a
boundary is nothing). It's made only of the mesh's own corners, plus the points where outlines truly cross, so nothing is
rounded: a 2D polygon library's integer grid left slivers along every shared edge, each a hole. What is left is triangulated
again, each piece facing as it did. A neighbour with a corner new to it along an edge (a crossing) is split there, so that
the mesh stays joined.

This is only for walls of one body as bodies.split_bodies makes them, the surfaces the engine builds together. Bodies it builds
apart may rest flush as they are, each sound on its own, and may be what the designer drew: the repeater mount's 5 stacked
plates build valid. Its main body is several closed surfaces built as one, with 420 mm^2 of walls on each other, invalid until sealed.
When a body's plane can't be rebuilt as a whole, each pair of its closed surfaces resting on each other (a surface with itself
among them) is tried on its own (_by_surface): the engine sews each closed surface into a shell of its own, and surfaces that
run through each other are two shells whatever their faces do. The mount's plates overlap each other by 0.1 to 0.4 mm and
cover their overlaps twice with one facing, which no rebuild can mend; its main body's 570 mm^2 of double wall is its own.

Nothing is kept unless it holds: every remaining triangle covered once, the area as the overlap says, no new open
edge, and the volume unmoved. Otherwise that plane stays as it was. A mesh without such walls comes back untouched.
"""

import numpy as np

NORMAL_DIGITS = 3     # planes share a direction when their unit normals round the same at this many digits
PLANE_MM = 1e-4       # mm: corners this close to a plane are in it (a file's rounding is about 1e-5 at 100 mm)
ON_EDGE_MM = 1e-4     # mm: a corner this close to an edge lies on it
JOIN_MM = 1e-3        # mm: corners in the plane this close are one, if its outlines don't meet otherwise (as repair.CRACK)
OVERLAP_MM2 = 1e-3    # mm^2: walls overlapping by less are left alone
AREA_SLACK = 1e-3     # share of the overlap (and at least OVERLAP_MM2) the rebuilt plane's area may be off by
VOLUME_SLACK = 1e-6   # share of the mesh's volume sealing may move it by
FLAT_NORMAL = 1e-9    # twice a triangle's area in mm^2 below which it has no direction of its own


def _frame(d):
    u = np.cross(d, [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
    u = u / np.linalg.norm(u)
    return u, np.cross(d, u)


def _planes(pts, tris):
    """[(direction, offset, triangles facing along it, triangles facing against it)] for each plane holding both facings."""
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    n = np.cross(b - a, c - a)
    area2 = np.linalg.norm(n, axis=1)
    idx = np.nonzero(area2 > FLAT_NORMAL)[0]
    if not len(idx):
        return []
    n = n[idx] / area2[idx, None]
    # (a direction's sign fixed by its first component that rounds to more than zero: a file's rounding leaves 1e-8 in the others)
    first = np.argmax(np.abs(n) > 0.5 * 10.0 ** -NORMAL_DIGITS, axis=1)
    s = np.sign(n[np.arange(len(n)), first])
    d = n * s[:, None]
    off = ((a[idx] + b[idx] + c[idx]) / 3 * d).sum(1)
    key = np.round(d, NORMAL_DIGITS) + 0.0
    order = np.lexsort((off, key[:, 2], key[:, 1], key[:, 0]))
    new_dir = np.any(np.diff(key[order], axis=0) != 0, axis=1)
    # (offsets in one direction split where they jump, so a file's rounding can't part one plane at a bin's edge)
    new_off = np.diff(off[order]) > PLANE_MM
    cut = np.r_[0, np.nonzero(new_dir | new_off)[0] + 1, len(order)]
    out = []
    for i, j in zip(cut[:-1], cut[1:]):
        g = order[i:j]
        up, down = idx[g[s[g] > 0]], idx[g[s[g] < 0]]
        if len(up) and len(down):
            out.append((d[g[0]], float(off[g].mean()), up, down))
    return out


def _overlap(P2):
    """The area two sets of triangles (arrays n x 3 x 2) share, on 2D booleans: good enough to decide, not to build from."""
    from manifold3d import CrossSection, FillRule

    def union(t):
        flip = (t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 1, 1] - t[:, 0, 1]) * (t[:, 2, 0] - t[:, 0, 0]) < 0
        return CrossSection([x[::-1] if f else x for x, f in zip(t, flip)], FillRule.Positive)

    return (union(P2[0]) ^ union(P2[1])).area()


def _once_area(A, B):
    """The area covered an odd number of times by two sets of triangles: what should be left of them."""
    from manifold3d import CrossSection, FillRule

    t = np.concatenate([A, B])
    flip = (t[:, 1, 0] - t[:, 0, 0]) * (t[:, 2, 1] - t[:, 0, 1]) - (t[:, 1, 1] - t[:, 0, 1]) * (t[:, 2, 0] - t[:, 0, 0]) < 0
    return CrossSection([x[::-1] if f else x for x, f in zip(t, flip)], FillRule.EvenOdd).area()


def _on_edge(P, a, b):
    """Indices of points on the edge a-b, in order along it, its ends left out."""
    ab = P[b] - P[a]
    L2 = float(ab @ ab)
    rel = P - P[a]
    t = rel @ ab / L2
    dist = np.abs(rel[:, 0] * ab[1] - rel[:, 1] * ab[0]) / np.sqrt(L2)
    on = np.nonzero((t > 1e-9) & (t < 1 - 1e-9) & (dist < ON_EDGE_MM))[0]
    return on[np.argsort(t[on])]


def _chain(F, P):
    """Edges covered an odd number of times once split at every corner on them: the outline of triangles F."""
    seen = {}
    for tri in F:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            if a == b:
                continue
            seq = [int(a)] + [int(i) for i in _on_edge(P, a, b)] + [int(b)]
            for p, q in zip(seq[:-1], seq[1:]):
                k = (min(p, q), max(p, q))
                seen[k] = seen.get(k, 0) + 1
    return {k for k, n in seen.items() if n % 2}


def _crossings(A, B, P):
    """Where outline edges of A and B truly cross: [(edge in A, edge in B, point)]. Touching doesn't count."""
    out = []
    if not A or not B:
        return out
    E = np.array(sorted(B))
    p, r = P[E[:, 0]], P[E[:, 1]] - P[E[:, 0]]
    for a, b in A:
        q, s = P[a], P[b] - P[a]
        den = r[:, 0] * s[1] - r[:, 1] * s[0]
        ok = np.abs(den) > 1e-14
        w = q - p
        t = np.full(len(E), -1.0)
        u = np.full(len(E), -1.0)
        t[ok] = (w[ok, 0] * s[1] - w[ok, 1] * s[0]) / den[ok]
        u[ok] = (w[ok, 0] * r[ok, 1] - w[ok, 1] * r[ok, 0]) / den[ok]
        lb = ON_EDGE_MM / np.maximum(np.linalg.norm(r, axis=1), 1e-30)
        lu = ON_EDGE_MM / max(float(np.linalg.norm(s)), 1e-30)
        for i in np.nonzero((t > lb) & (t < 1 - lb) & (u > lu) & (u < 1 - lu))[0]:
            out.append(((a, b), tuple(E[i]), p[i] + t[i] * r[i]))
    return out


def _loop_area(L, P):
    x, y = P[L, 0], P[L, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _inside_loop(q, L, P):
    x, y = P[L, 0], P[L, 1]
    x2, y2 = np.roll(x, -1), np.roll(y, -1)
    crosses = (y > q[1]) != (y2 > q[1])
    at = x[crosses] + (q[1] - y[crosses]) * (x2[crosses] - x[crosses]) / (y2[crosses] - y[crosses])
    return bool((q[0] < at).sum() % 2)


def _regions(loops, P):
    """[(outer loop counter-clockwise, its holes clockwise, whether it faces along the plane's direction)] from the outline's
    loops. Which is which follows from nesting alone, with no test of any point: every outline edge has one more wall
    facing up to its left than to its right, so a loop going counter-clockwise holds one more than what's around it, and one
    going clockwise one less. (Testing points beside the edges failed at this scale: a point lands on an edge two triangles
    share, as on a hexagon's middle line, and tiny edges have nothing beside them to test.)"""
    n = len(loops)
    areas = np.array([_loop_area(L, P) for L in loops])
    order = np.argsort(-np.abs(areas), kind="stable")
    probe = []
    for L in loops:
        k = max(range(len(L)), key=lambda i: ((P[L[(i + 1) % len(L)]] - P[L[i]]) ** 2).sum())
        probe.append((P[L[k]] + P[L[(k + 1) % len(L)]]) / 2)
    parent = [-1] * n
    within = [0] * n
    children = {i: [] for i in range(-1, n)}
    for pos, i in enumerate(order):
        # the smallest loop around this one is the last of the larger ones that holds its probe
        for j in order[:pos][::-1]:
            if _inside_loop(probe[i], loops[j], P):
                parent[i] = int(j)
                break
        children[parent[i]].append(int(i))
        around = within[parent[i]] if parent[i] >= 0 else 0
        within[i] = around + (1 if areas[i] > 0 else -1)
    out = []
    for i in range(n):
        around = within[parent[i]] if parent[i] >= 0 else 0
        if around != 0 or within[i] == 0:
            continue
        holes = [loops[c] if areas[c] < 0 else loops[c][::-1] for c in children[i] if within[c] == 0]
        out.append((loops[i] if areas[i] > 0 else loops[i][::-1], holes, within[i] > 0))
    return out


def _trace(edges, P):
    """Closed loops from directed edges with the surface on their left. Where loops touch at a corner, take the first edge
    clockwise from the way back, which stays in the same pocket: loops come out simple, outer ones counter-clockwise,
    holes clockwise, as the triangulator wants. None if they don't close."""
    leaving = {}
    for a, b in edges:
        leaving.setdefault(a, []).append(b)
    used = set()
    loops = []
    for a, b in edges:
        if (a, b) in used:
            continue
        used.add((a, b))
        loop = [a]
        prev, cur = a, b
        while cur != a:
            loop.append(cur)
            ops = [n for n in leaving.get(cur, []) if (cur, n) not in used]
            if not ops:
                return None
            if len(ops) > 1:
                r = P[prev] - P[cur]
                back = np.arctan2(r[1], r[0])
                ops.sort(key=lambda n: (back - np.arctan2(P[n][1] - P[cur][1], P[n][0] - P[cur][0])) % (2 * np.pi))
            used.add((cur, ops[0]))
            prev, cur = cur, ops[0]
        loops.append(loop)
    return loops


def _simple(loop):
    """A closed walk cut at each corner it comes back to: where the outlines of two pieces cross, a walk can pass from one to
    the other like a figure of eight, and each piece has a side of its own."""
    out, stack, at = [], [], {}
    for c in loop:
        if c in at:
            i = at[c]
            piece = stack[i:]
            for x in piece[1:]:
                del at[x]
            del stack[i + 1:]
            if len(piece) >= 3:
                out.append(piece)
        else:
            at[c] = len(stack)
            stack.append(c)
    if len(stack) >= 3:
        out.append(stack)
    return out


def _rebuild(pts, tris, d, off, ids, up, down, kept_corners, base):
    """One plane's triangles ids (of which up and down are the ones with a facing): (new points, new triangles as indices into
    pts then new points numbered from base, triangles of it replaced), or None."""
    from manifold3d import triangulate

    u, v = _frame(d)
    is_up, is_down = np.isin(ids, up), np.isin(ids, down)
    # zero-area triangles in the plane go with the rest: they hold the file's T-junctions, and cancel out in the chain
    corners, F = np.unique(tris[ids], return_inverse=True)
    F = F.reshape(-1, 3)
    P = np.c_[pts[corners] @ u, pts[corners] @ v]
    n0 = len(P)
    extra = []
    for _, _, x in _crossings(_chain(F[~is_down], P), _chain(F[is_down], P), P):
        # (several edges can cross at one point: one corner for them all, or they'd be joined by edges of no length)
        if extra and (((np.array(extra) - x) ** 2).sum(axis=1) < 1e-14).any():
            continue
        extra.append(x)
    if extra:
        P = np.vstack([P, np.array(extra)])
    net = {}
    for tri in F:
        for a, b in ((tri[0], tri[1]), (tri[1], tri[2]), (tri[2], tri[0])):
            if a == b:
                continue
            seq = [int(a)] + [int(i) for i in _on_edge(P, a, b)] + [int(b)]
            for p, q in zip(seq[:-1], seq[1:]):
                net[(p, q)] = net.get((p, q), 0) + 1
    outline = []
    for (p, q), n in net.items():
        left = n - net.get((q, p), 0)
        if left > 1:
            return None         # the two facings side by side, with no wall between: a fold, not a double wall
        if left == 1:
            outline.append((p, q))
    if not outline:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), ids
    loops = _trace(outline, P)
    if loops is None:
        return None
    loops = [simple for L in loops for simple in _simple(L)]
    # a corner on a straight run that nothing outside the plane uses isn't needed
    world = corners.tolist()
    clean = []
    for L in loops:
        keep = []
        n = len(L)
        for i, c in enumerate(L):
            p, q = P[L[i - 1]], P[L[(i + 1) % n]]
            cr = (P[c] - p)[0] * (q - p)[1] - (P[c] - p)[1] * (q - p)[0]
            if abs(cr) > 1e-9 or c >= n0 or world[c] in kept_corners:
                keep.append(c)
        if len(keep) >= 3 and _loop_area(keep, P) != 0:
            clean.append(keep)
    if not clean:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), ids
    new = []
    for outer, holes, facing_up in _regions(clean, P):
        polys = [outer] + holes
        flat = np.concatenate(polys)
        for a, b, c in np.asarray(triangulate([P[L] for L in polys])):
            i, j, k = flat[a], flat[b], flat[c]
            pa, pb, pc = P[i], P[j], P[k]
            if (pb[0] - pa[0]) * (pc[1] - pa[1]) - (pb[1] - pa[1]) * (pc[0] - pa[0]) <= 0:
                continue
            new.append((i, j, k) if facing_up else (i, k, j))
    if not new:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), ids
    new = np.array(new)
    idx = np.r_[corners, base + np.arange(len(extra))]
    X = np.array([u * x[0] + v * x[1] + d * off for x in extra]) if extra else np.zeros((0, 3))
    return X, idx[new], ids


def _zip(pts, tris):
    """Splits a triangle at the corners its neighbours have along one of its edges, so open edges meet again."""
    e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    k, c = np.unique(e, axis=0, return_counts=True)
    opened = k[c == 1]
    if not len(opened):
        return tris
    ends = np.unique(opened)
    Q = pts[ends]
    along = {}
    for a, b in opened:
        ab = pts[b] - pts[a]
        L2 = float(ab @ ab)
        rel = Q - pts[a]
        t = rel @ ab / L2
        dist = np.linalg.norm(rel - np.outer(t, ab), axis=1)
        on = np.nonzero((t > 1e-9) & (t < 1 - 1e-9) & (dist < ON_EDGE_MM))[0]
        if len(on):
            along[(int(a), int(b))] = [int(i) for i in ends[on[np.argsort(t[on])]]]
    if not along:
        return tris
    out = []
    for tri in tris.tolist():
        done = False
        for i in range(3):
            a, b, c = tri[i], tri[(i + 1) % 3], tri[(i + 2) % 3]
            key = (a, b) if a < b else (b, a)
            mids = along.get(key)
            if mids:
                seq = [a] + (mids if a < b else mids[::-1]) + [b]
                out.extend([p, q, c] for p, q in zip(seq[:-1], seq[1:]))
                done = True
                break
        if not done:
            out.append(tri)
    return np.array(out, dtype=np.int64)


def _open_count(tris):
    e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    _, c = np.unique(e, axis=0, return_counts=True)
    return int((c == 1).sum())


def _volume(pts, tris):
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    return float((a * np.cross(b, c)).sum() / 6.0)


def _apply(pts, tris, jobs):
    """The mesh with the rebuilt planes jobs (see _candidates) in place of what they were, and the area of wall gone, or None
    if it fails a check. Numbers in jobs are triangles of this tris: it must be the one _candidates saw."""
    new_pts, new_tris, gone = [], [], np.zeros(len(tris), bool)
    base = len(pts)
    before = expected = shared_all = 0.0
    for d, off, ids, up, down, shared in jobs:
        rest = np.ones(len(tris), bool)
        rest[ids] = False
        built = _rebuild(pts, tris, d, off, ids, up, down, set(tris[rest].ravel().tolist()), base)
        if built is None:
            return None
        X, new, ids = built
        base += len(X)
        new_pts.append(X)
        new_tris.append(new)
        gone[ids] = True
        before += float(_areas(pts, tris[ids]).sum())
        shared_all += shared
        u, v = _frame(d)
        expected += _once_area(np.stack([pts[tris[up]] @ u, pts[tris[up]] @ v], -1), np.stack([pts[tris[down]] @ u, pts[tris[down]] @ v], -1))
    P2 = np.vstack([pts] + new_pts)
    N = np.vstack(new_tris) if new_tris else np.zeros((0, 3), dtype=np.int64)
    after = float(_areas(P2, N).sum())
    trial = _zip(P2, np.vstack([tris[~gone], N]))
    slack = max(OVERLAP_MM2, AREA_SLACK * shared_all)
    v0, v1 = _volume(pts, tris), _volume(P2, trial)
    if abs(after - expected) > slack or _open_count(trial) > _open_count(tris) or abs(v1 - v0) > VOLUME_SLACK * max(abs(v0), 1.0):
        return None
    return P2, trial, before - after


def _areas(pts, tris):
    return 0.5 * np.linalg.norm(np.cross(pts[tris[:, 1]] - pts[tris[:, 0]], pts[tris[:, 2]] - pts[tris[:, 0]]), axis=1)


def _join_close(pts, tris, d, off):
    """tris with corners in this plane closer than JOIN_MM made one corner, or None if there are none. As repair's
    CRACK joins open edges' corners: they are one corner the file wrote twice."""
    from scipy.spatial import cKDTree

    ids = np.nonzero(np.abs(pts[tris] @ d - off).max(axis=1) < 2 * PLANE_MM)[0]
    corners = np.unique(tris[ids])
    pairs = cKDTree(pts[corners]).query_pairs(JOIN_MM, output_type="ndarray")
    if not len(pairs):
        return None
    to = np.arange(len(pts))
    for a, b in pairs:
        i, j = corners[a], corners[b]
        while to[i] != i:
            i = to[i]
        while to[j] != j:
            j = to[j]
        if i != j:
            to[max(i, j)] = min(i, j)
    for k in corners:
        i = k
        while to[i] != i:
            i = to[i]
        to[k] = i
    joined = to[tris]
    return joined[(joined[:, 0] != joined[:, 1]) & (joined[:, 1] != joined[:, 2]) & (joined[:, 2] != joined[:, 0])]


def _closed_surfaces(tris):
    """Which closed surface (triangles joined edge to edge) each triangle is on."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    e = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    _, inv = np.unique(e, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    owner = np.tile(np.arange(len(tris)), 3)
    order = np.argsort(inv, kind="stable")
    se, so = inv[order], owner[order]
    starts = np.r_[0, np.nonzero(np.diff(se))[0] + 1, len(se)]
    rows, cols = [np.zeros(0, np.int64)], [np.zeros(0, np.int64)]
    for i, j in zip(starts[:-1], starts[1:]):
        if j - i > 1:
            rows.append(np.full(j - i - 1, so[i]))
            cols.append(so[i + 1:j])
    r, c = np.concatenate(rows), np.concatenate(cols)
    return connected_components(coo_matrix((np.ones(len(r)), (r, c)), shape=(len(tris), len(tris))), directed=False)[1]


def _by_surface(pts, tris, job):
    """A body's job split into one for each pair of its closed surfaces (a surface with itself among them) whose walls lie
    on each other, for when the body's plane can't be rebuilt as a whole: bodies overlapping each other (the repeater
    mount's plates, by 0.1 to 0.4 mm) cover their overlaps twice with one facing, which no rebuild of the plane can mend,
    and that stopped the mount's main body, a closed surface of the same group, from losing its 570 mm^2 of double wall.
    The engine sews each closed surface into a shell of its own, so a pair's walls are the two shells' own business."""
    d, off, inplane, up, down, _ = job
    u, v = _frame(d)
    surface = _closed_surfaces(tris)

    def shared_by(a, b):
        if not len(a) or not len(b):
            return 0.0
        A = np.stack([pts[tris[a]] @ u, pts[tris[a]] @ v], -1)
        B = np.stack([pts[tris[b]] @ u, pts[tris[b]] @ v], -1)
        if (A[..., 0].max() < B[..., 0].min() or B[..., 0].max() < A[..., 0].min()
                or A[..., 1].max() < B[..., 1].min() or B[..., 1].max() < A[..., 1].min()):
            return 0.0
        return _overlap((A, B))

    # two surfaces' walls lie on each other only when the surfaces rest on each other: the one facing up (its material
    # below the plane) wholly below it, the one facing down wholly above. Surfaces running through each other (the
    # mount's sunk block and the plate it overlaps by 0.36 mm) are two shells however their faces touch; sealed, they
    # made one shell crossing itself (14 solids and 274 faces, against 15 and 261 left apart)
    height = pts @ d
    side = {}
    for sid in np.unique(surface):
        h = height[np.unique(tris[surface == sid])]
        side[sid] = (float(h.min()), float(h.max()))
    out = []
    for sa in np.unique(surface[up]):
        a = up[surface[up] == sa]
        for sb in np.unique(surface[down]):
            if sb != sa and (side[sa][1] > off + PLANE_MM or side[sb][0] < off - PLANE_MM):
                continue
            b = down[surface[down] == sb]
            shared = shared_by(a, b)
            if shared > OVERLAP_MM2:
                both = inplane[np.isin(surface[inplane], [sa, sb])]
                out.append((d, off, both, a, b, shared))
    return out


def _candidates(pts, tris, within_bodies=True):
    """[(direction, offset, triangles in the plane, those facing along it, those facing against it, area shared)]: one for each
    body with walls of its own on each other in a plane (or each plane, if not within_bodies)."""
    from .bodies import body_of

    surface = None
    out = []
    for d, off, up, down in _planes(pts, tris):
        u, v = _frame(d)

        def shared_by(a, b):
            A = np.stack([pts[tris[a]] @ u, pts[tris[a]] @ v], -1)
            B = np.stack([pts[tris[b]] @ u, pts[tris[b]] @ v], -1)
            # (boxes first: most planes with both facings have them apart)
            if (A[..., 0].max() < B[..., 0].min() or B[..., 0].max() < A[..., 0].min()
                    or A[..., 1].max() < B[..., 1].min() or B[..., 1].max() < A[..., 1].min()):
                return 0.0
            return _overlap((A, B))

        if shared_by(up, down) <= OVERLAP_MM2:
            continue
        inplane = np.nonzero(np.abs(pts[tris] @ d - off).max(axis=1) < 2 * PLANE_MM)[0]
        if not within_bodies:
            out.append((d, off, inplane, up, down, shared_by(up, down)))
            continue
        if surface is None:
            surface = body_of(pts, tris)
        for s in np.intersect1d(surface[up], surface[down]):
            if s < 0:
                continue
            a, b = up[surface[up] == s], down[surface[down] == s]
            shared = shared_by(a, b)
            if shared > OVERLAP_MM2:
                out.append((d, off, inplane[surface[inplane] == s], a, b, shared))
    return out


def seal(pts, tris, within_bodies=True):
    """(pts, tris, area of wall removed in mm^2, planes sealed). Triangles not in a sealed plane keep their order, new ones
    come after them. pts only gains points, and nothing changes if no walls lie on each other."""
    pts = np.asarray(pts, dtype=np.float64)
    tris = np.asarray(tris, dtype=np.int64)
    if not len(tris):
        return pts, tris, 0.0, 0
    jobs = _candidates(pts, tris, within_bodies)
    if not jobs:
        return pts, tris, 0.0, 0
    done = _apply(pts, tris, jobs)
    if done is not None:
        return done[0], done[1], done[2], len(jobs)
    # one failed its checks: one at a time, on the mesh each leaves. A plane whose two outlines a file's rounding has
    # left a hair apart (the repeater mount: 0.0007 mm, on 5 of 1,468 edges) gets a second try with such corners joined
    removed, planes, tried = 0.0, 0, set()

    while True:
        for job in _candidates(pts, tris, within_bodies):
            key = (tuple(np.round(job[0], NORMAL_DIGITS)), round(job[1] / PLANE_MM), len(job[3]), len(job[4]))
            if key in tried:
                continue
            tried.add(key)
            done = _apply(pts, tris, [job])
            if done is None:
                joined = _join_close(pts, tris, job[0], job[1])
                if joined is not None:
                    again = [j for j in _candidates(pts, joined, within_bodies)
                             if tuple(np.round(j[0], NORMAL_DIGITS)) == key[0] and round(j[1] / PLANE_MM) == key[1]]
                    done = _apply(pts, joined, again) if again else None
            if done is None and within_bodies:
                # (one pair of the body's closed surfaces at a time: the loop comes back for the next)
                for pair in _by_surface(pts, tris, job):
                    done = _apply(pts, tris, [pair])
                    if done is not None:
                        break
            if done is not None:
                pts, tris = done[0], done[1]
                removed += done[2]
                planes += 1
                break
        else:
            return pts, tris, removed, planes
