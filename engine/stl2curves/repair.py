"""Mend the defects downloaded STLs often have, before anything else looks at them.

Each step changes the mesh only where something is wrong, and says what it did:

- triangles with a repeated corner, identical copies of a triangle, and pairs of the
  same triangle facing opposite ways (a wall of zero thickness) are dropped;
- sliver triangles (three corners in a line) are split away;
- stray flaps: at an edge used three times, of two triangles in one plane running
  along it the same way, the smaller goes;
- cracks: corners along open edges that nearly meet are joined;
- knots: where an edge has three or four triangles because a few corners a micron or
  so apart are joined by tiny triangles of all facings, those corners become one;
- orientation: triangles are turned to agree with their neighbours across every edge,
  and each closed piece faces outward (or, if it lies inside another piece, as a
  cavity, inward);
- holes: each loop of open edges that remains is filled (never running an edge the
  mesh already has a second time);
- specks that cross themselves (small knots of triangles passing through each other)
  are cut out with a margin and the hole filled, as in MeshFix (Attene, "A lightweight
  approach to repairing digitized polygon meshes", 2010), the margin narrowed where the
  patch would otherwise move the surface.

What can't be mended is left in repair.left, to be reported.
"""

import math

import numpy as np

from .features import grid_noise, _right_vectors

CRACK = 1e-3            # mm (and a small share of the part's size): open edges this close are joined
SPECK = 0.1             # a self-crossing cluster smaller than this share of the part is cut out
MARGIN_RINGS = 2        # ... together with this many rings of triangles around it
SPECK_SHIFT = 0.05      # mm: ... if patching the hole moves the surface no more than this on average
SPECK_SHIFT_MOST = 0.5  # mm: ... or, failing that, the gentler of two cuts if it moves it no more than this
NARROW_GAIN = 0.5       # ... one ring of margin instead of two only if that moves it less than this share as far
SPECK_RIM = 500         # ... and leaves a hole with no more corners round it than this
SPECK_TRIALS = 30       # ... each tried alone (when all together fail) if there are no more of them than this
FLAP_COS = 0.9999       # triangles at an overused edge this near one plane (either way up) are in one plane
KNOT = 0.005            # mm: corners joined by edges this short round an edge of 3+ triangles are one corner
KNOT_REACH = 4          # ... gathered no further than this many KNOTs from the edge's own corner
KNOT_REST = 0.25        # ... kept only if no more than this share of such edges is left after


def repair(pts, tris):
    """(pts, tris, notes): the mended mesh and a list of what was done."""
    notes = []
    size = float(np.ptp(pts, axis=0).max()) if len(pts) else 1.0
    tris = np.asarray(tris, dtype=np.int64)

    k = len(tris)
    tris = tris[(tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 2] != tris[:, 0])]
    tris, same, opposite = _duplicates(tris)
    if same:
        notes.append(f"{same} duplicate triangles removed")
    if opposite:
        notes.append(f"{opposite} zero-thickness wall triangles removed")
    tris, slivers = remove_slivers(pts, tris)
    # where triangles cross each other, also the slivers the file's rounding has made
    # not quite flat (only there: elsewhere they do no harm)
    crossing = crossing_triangles(pts, tris)
    if len(crossing):
        zone = np.zeros(len(pts), bool)
        zone[np.unique(tris[np.unique(crossing)])] = True
        for _ in range(MARGIN_RINGS):
            zone[np.unique(tris[zone[tris].any(axis=1)])] = True
        tris, more = remove_slivers(pts, tris, grid_noise(pts), zone)
        slivers += more
    if slivers:
        notes.append(f"{slivers} sliver triangles split away")

    if len(_open_edges(tris)):
        pts, tris, joined = _close_cracks(pts, tris, max(CRACK, 1e-5 * size))
        if joined:
            notes.append(f"{joined} corners along cracks joined")
    if len(_open_edges(tris)):
        # (only on a mesh still open: closed ones have such flaps too, crossing
        # themselves say, and convert as they are)
        tris, flaps = _folded_flaps(pts, tris)
        if flaps:
            notes.append(f"{flaps} stray flap triangle{'s' if flaps > 1 else ''} removed")

    tris, knots = _untie_knots(pts, tris)
    if knots:
        notes.append(f"{knots} knot{'s' if knots > 1 else ''} of tiny triangles pulled to a point")

    tris, flipped = _orient(pts, tris)
    if flipped:
        notes.append(f"{flipped} triangles turned to face the right way")

    specks = 0
    for _ in range(3):
        cut = _self_crossing_specks(pts, tris, SPECK * size)
        if cut is None:
            break
        holes, over = len(_open_edges(tris)), len(_overused(tris))
        patched_pts, patched, filled = _fill_holes(pts, tris[~cut])
        # (the hole it leaves must patch: on a fine mesh a knot a few millimetres across
        # is thousands of triangles. Better a mesh crossing itself there, which still
        # converts, than one with a hole, which makes no solid at all. Nor may it add
        # edges of three or more triangles, unless only separate pieces cross there:
        # such a cut lies inside the other body, fused away later, as it always was)
        if len(_open_edges(patched)) > holes or (
                not _self_crossing_specks.between and len(_overused(patched)) > over):
            break
        pts, tris = patched_pts, patched
        specks += 1
    if specks:
        notes.append(f"{specks} self-crossing speck{'s' if specks > 1 else ''} cut out and patched")

    pts, tris, filled = _fill_holes(pts, tris)
    if filled:
        notes.append(f"{filled} hole{'s' if filled > 1 else ''} filled")
    if flipped or filled or specks:
        tris, _ = _orient(pts, tris)
    # what is still wrong, said up front: a part built from such a mesh can't come out
    # a clean solid there, however long the build tries
    left = [(len(_overused(tris)), "edge{} shared by three or more triangles"),
            (_self_crossing_specks.__dict__.get("pairs", 0), "pair{} of triangles crossing each other"),
            (len(_open_edges(tris)), "open edge{}")]
    repair.left = [f"{n} {text.format('s' if n > 1 else '')}" for n, text in left if n]
    return pts, tris, notes


def leftovers():
    """What the last repair() couldn't mend, in plain words (for the report)."""
    return list(getattr(repair, "left", []))


# ---------------------------------------------------------------- simple clean-ups

def _duplicates(tris):
    """Drop repeated triangles (keeping one) and opposite pairs (dropping both)."""
    key = np.sort(tris, axis=1)
    _, first, inv, count = np.unique(key, axis=0, return_index=True, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    if count.max(initial=1) == 1:
        return tris, 0, 0
    # orientation of each triangle relative to its sorted corners: even or odd permutation
    rot = np.argmin(tris, axis=1)
    rolled = np.take_along_axis(tris, (np.arange(3)[None] + rot[:, None]) % 3, axis=1)
    parity = rolled[:, 1] < rolled[:, 2]        # same winding as the sorted order?
    keep = np.ones(len(tris), bool)
    same = opposite = 0
    for g in np.nonzero(count > 1)[0]:
        members = np.nonzero(inv == g)[0]
        up = members[parity[members]]
        down = members[~parity[members]]
        pairs = min(len(up), len(down))
        # opposite pairs cancel; of what's left, one copy stays
        keep[members] = False
        opposite += 2 * pairs
        rest = up[pairs:] if len(up) > len(down) else down[pairs:]
        if len(rest):
            keep[rest[0]] = True
            same += len(rest) - 1
    return tris[keep], same, opposite


def _folded_flaps(pts, tris):
    """Drop stray flaps: at an edge used three times or more, of two triangles in one
    plane that both run along it the same way (which no two neighbours across an edge
    do), the smaller. A Fusion export (189k triangles) had one at its only overused
    edge, lying flat beside a big triangle and facing the other way; it hid one end of a
    hole, whose open edges then never closed into a loop to fill, and the part made no
    solid at all."""
    if not len(tris):
        return tris, 0
    pairs = np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])
    e = np.sort(pairs, axis=1)
    forward = pairs[:, 0] < pairs[:, 1]
    owner = np.tile(np.arange(len(tris)), 3)
    _, inv, count = np.unique(e, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    over = np.nonzero(count[inv] >= 3)[0]
    if not len(over):
        return tris, 0
    a, b, c = (pts[tris[:, i]] for i in range(3))
    n = np.cross(b - a, c - a)
    area = np.linalg.norm(n, axis=1)
    unit = n / np.maximum(area, 1e-300)[:, None]
    groups = {}
    for k in over:
        groups.setdefault(int(inv[k]), []).append((int(owner[k]), bool(forward[k])))
    drop = set()
    for members in groups.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                (s, fs), (t, ft) = members[i], members[j]
                if fs == ft and s not in drop and t not in drop and abs(unit[s] @ unit[t]) > FLAP_COS:
                    drop.add(s if area[s] < area[t] else t)
    if not drop:
        return tris, 0
    keep = np.ones(len(tris), bool)
    keep[list(drop)] = False
    return tris[keep], len(drop)


def remove_slivers(pts, tris, noise=0.0, zone=None):
    """Remove slivers: triangles with one corner lying on the opposite side (all three
    corners in a line, give or take the file's rounding noise). Their outline doubles
    back on itself, which spoils the face they belong to, and a chain of them folds
    over itself. The triangle across that side is split at the corner instead, which
    keeps the surface as it was (to within the noise) and the mesh closed. The noise
    allowance applies only to corners in zone (all if None). Returns (tris, count)."""
    tris = tris.copy()
    total = 0
    # (each triangle's test depends on its own corners only: worked out once, then for the
    # triangles each round makes. A 1.25M-triangle hub with 38,000 slivers spent 20 s
    # testing every triangle again round after round)
    flags = _sliver_flags(pts, tris, noise, zone)
    for _ in range(20):
        T = tris
        ks, ii = np.nonzero(flags)          # (by corner, then by triangle)
        if not len(ii):
            break
        # the triangle running each directed edge (the last one, if several do), looked
        # up in a sorted table, for every sliver at once
        n = np.int64(len(pts))
        code = np.concatenate([T[:, 0] * n + T[:, 1], T[:, 1] * n + T[:, 2], T[:, 2] * n + T[:, 0]])
        owner = np.tile(np.arange(len(T)), 3)
        order = np.lexsort((owner, code))
        code, owner = code[order], owner[order]

        def across(a, b):
            c = a * n + b
            at = np.searchsorted(code, c, side="right") - 1
            hit = (at >= 0) & (code[np.maximum(at, 0)] == c)
            return np.where(hit, owner[np.maximum(at, 0)], -1)

        V, U, W = T[ii, ks], T[ii, (ks + 1) % 3], T[ii, (ks + 2) % 3]
        J = across(W, U)
        X = np.where(J >= 0, T[np.maximum(J, 0)].sum(axis=1) - U - W, -1)
        taken = (across(V, X) >= 0) | (across(X, V) >= 0)
        drop, add, touched, made = set(), [], set(), set()
        for i, v, u, w, j, x, t in zip(ii.tolist(), V.tolist(), U.tolist(), W.tolist(), J.tolist(),
                                       X.tolist(), taken.tolist()):
            if j < 0 or i in touched or j in touched:
                continue
            if x != v and (t or (min(v, x), max(v, x)) in made):
                continue        # (splitting would add an edge that's already there: a fold)
            made.add((min(v, x), max(v, x)))
            touched |= {i, j}
            drop |= {i, j}
            total += 1
            if x != v:          # (x == v: two slivers folded onto each other; both go)
                add += [(w, v, x), (v, u, x)]
        keep = np.ones(len(T), bool)
        keep[list(drop)] = False
        new = np.array(add, dtype=T.dtype).reshape(-1, 3)
        tris = np.vstack([T[keep], new])
        flags = np.hstack([flags[:, keep], _sliver_flags(pts, new, noise, zone)])
    return tris, total


def _sliver_flags(pts, T, noise, zone):
    """(3, triangles): is corner k of each triangle on its opposite side, within it?"""
    out = np.zeros((3, len(T)), bool)
    for k in range(3):
        v, u, w = pts[T[:, k]], pts[T[:, (k + 1) % 3]], pts[T[:, (k + 2) % 3]]
        e = w - u
        length = np.linalg.norm(e, axis=1)
        s = np.einsum("ij,ij->i", v - u, e) / np.maximum(length, 1e-12) ** 2
        h = np.linalg.norm(np.cross(v - u, e), axis=1) / np.maximum(length, 1e-12)
        allow = noise if zone is None else noise * zone[T[:, k]]
        thin = (h <= np.maximum(1e-5 * length, 1.5 * allow)) & (h < np.maximum(1e-4, 2 * allow))
        out[k] = thin & (s > 0) & (s < 1)
    return out


def _edges(tris):
    return np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]])


def _overused(tris):
    """Edges (low, high) of three or more triangles."""
    if not len(tris):
        return np.zeros((0, 2), np.int64)
    d = _edges(tris)
    n = np.int64(tris.max()) + 1
    und = np.minimum(d[:, 0], d[:, 1]) * n + np.maximum(d[:, 0], d[:, 1])
    u, count = np.unique(und, return_counts=True)
    u = u[count > 2]
    return np.c_[u // n, u % n]


# ---------------------------------------------------------------- knots

def _untie_knots(pts, tris):
    """Pull knots of tiny triangles to a point. An export can leave, where several
    surfaces meet, a few corners a micron or so apart joined by triangles of all
    facings, so that edges there have three or four triangles (the ratchet: 29 such
    edges, after which every try at building it fell short of a valid solid, 99 tries).
    Corners linked by edges under KNOT around such an edge become the one corner the
    biggest triangles use; kept only where that leaves fewer such edges, opens nothing
    and turns no triangle wider than a knot over; all of it only if few such edges
    are left (else they are where bodies touch). Returns (tris, knots pulled)."""
    over = _overused(tris)
    if not len(over):
        return tris, 0
    d = _edges(tris)
    length = np.linalg.norm(pts[d[:, 0]] - pts[d[:, 1]], axis=1)
    adj = {}
    for a, b in d[length <= KNOT].tolist():
        adj.setdefault(a, set()).add(b)
        adj.setdefault(b, set()).add(a)
    knots, seen = [], set()
    for s in np.unique(over).tolist():
        if s in seen or s not in adj:
            continue
        knot, stack = [], [s]
        seen.add(s)
        while stack:
            v = stack.pop()
            knot.append(v)
            for w in sorted(adj.get(v, ())):
                if w not in seen and np.linalg.norm(pts[w] - pts[s]) <= KNOT_REACH * KNOT:
                    seen.add(w)
                    stack.append(w)
        if len(knot) > 1:
            knots.append(knot)
    if not knots:
        return tris, 0
    original, tris = tris, tris.copy()
    a, b, c = (pts[tris[:, i]] for i in range(3))
    normal = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(normal, axis=1)
    weight = np.bincount(tris.ravel(), weights=np.repeat(area, 3), minlength=len(pts))
    # (a needle a micron wide turns over when a corner moves a micron: that's no harm)
    width = 2 * area / np.maximum(np.linalg.norm(np.stack([b - a, c - b, a - c]), axis=2).max(axis=0), 1e-300)
    alive = np.ones(len(tris), bool)
    pulled = 0
    for knot in knots:
        keep = max(knot, key=lambda v: weight[v])
        mine = np.zeros(len(pts), bool)
        mine[knot] = True
        near = np.nonzero(alive & mine[tris].any(axis=1))[0]
        before = tris[near]
        after = before.copy()
        after[mine[after]] = keep
        ok = (after[:, 0] != after[:, 1]) & (after[:, 1] != after[:, 2]) & (after[:, 2] != after[:, 0])
        rest, _, _ = _duplicates(after[ok])
        # (only edges at the knot's corners can change, and all their triangles are here)
        def counts(t, at):
            e = _edges(t)
            e = e[at[e].any(axis=1)]
            key = np.minimum(e[:, 0], e[:, 1]) * np.int64(len(pts)) + np.maximum(e[:, 0], e[:, 1])
            _, k = np.unique(key, return_counts=True)
            return (k > 2).sum(), (k == 1).sum()
        at = np.zeros(len(pts), bool)
        at[keep] = True
        over0, open0 = counts(before, mine)
        over1, open1 = counts(rest, at)
        if over1 >= over0 or open1 > open0:
            continue
        # no triangle (bar the knot's own specks) turned over by the move
        moved = ok & (after != before).any(axis=1)
        q = pts[after[moved]]
        n1 = np.cross(q[:, 1] - q[:, 0], q[:, 2] - q[:, 0])
        if np.any((np.einsum("ij,ij->i", n1, normal[near[moved]]) <= 0) & (width[near[moved]] > KNOT)):
            continue
        # in place, the mesh's order kept (seeds are tried in it): the knot's triangles
        # that remain take their new corners, the rest go
        survivors = set(map(tuple, rest.tolist()))
        for i, t in zip(near.tolist(), after.tolist()):
            if tuple(t) in survivors:
                tris[i] = t
                survivors.discard(tuple(t))
            else:
                alive[i] = False
        pulled += 1
    tris = tris[alive]
    if len(_overused(tris)) > KNOT_REST * len(over):
        # (knots that explain few of the edges of three or more triangles are part of
        # where bodies really touch: pulling them only reshaped the contact. Toolbox,
        # 636 -> 630 such edges: 4,334 faces -> 8,253; repeater mount 70 -> 57: worse.
        # Ratchet 44 -> 0, solar bracket 13 -> 2: both far better)
        return original, 0
    return tris, pulled


def _open_edges(tris):
    """Directed edges with no partner running the other way (the edges of holes)."""
    d = _edges(tris)
    n = int(tris.max()) + 1 if len(tris) else 1
    fwd = d[:, 0] * n + d[:, 1]
    back = d[:, 1] * n + d[:, 0]
    und = np.minimum(fwd, back)
    _, inv, count = np.unique(und, return_inverse=True, return_counts=True)
    return d[count[inv.ravel()] == 1]


# ---------------------------------------------------------------- cracks

def _close_cracks(pts, tris, tol):
    """Join corners on open edges that lie within tol of each other (a crack where two
    patches of triangles were never welded)."""
    open_ = _open_edges(tris)
    ends = np.unique(open_.ravel())
    if len(ends) < 2:
        return pts, tris, 0
    P = pts[ends]
    key = np.floor(P / tol).astype(np.int64)
    # cluster corners within tol: grid neighbours
    order = np.lexsort(key.T[::-1])
    parent = np.arange(len(ends))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    cells = {}
    for i in order:
        cells.setdefault(tuple(key[i]), []).append(i)
    for (x, y, z), members in cells.items():
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    for j in cells.get((x + dx, y + dy, z + dz), ()):
                        for i in members:
                            if i < j and np.linalg.norm(P[i] - P[j]) <= tol:
                                ri, rj = root(i), root(j)
                                if ri != rj:
                                    parent[ri] = rj
    roots = np.array([root(i) for i in range(len(ends))])
    joined = int((roots != np.arange(len(ends))).sum())
    if not joined:
        return pts, tris, 0
    remap = np.arange(len(pts))
    remap[ends] = ends[roots]
    tris = remap[tris]
    tris = tris[(tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 2] != tris[:, 0])]
    tris, _, _ = _duplicates(tris)
    return pts, tris, joined


# ---------------------------------------------------------------- orientation

def _orient(pts, tris):
    """Make neighbouring triangles agree (each edge run opposite ways by the two
    triangles on it), then make each closed piece face outward, or inward if it lies
    inside an odd number of other pieces (a cavity). Returns (tris, how many flipped)."""
    tris = tris.copy()
    m = len(tris)
    if not m:
        return tris, 0
    d = _edges(tris)
    owner = np.tile(np.arange(m), 3)
    n = int(tris.max()) + 1
    und = np.minimum(d[:, 0], d[:, 1]) * n + np.maximum(d[:, 0], d[:, 1])
    order = np.argsort(und, kind="stable")
    und_s, own_s, d_s = und[order], owner[order], d[order]
    # pairs of triangles sharing an edge used exactly twice
    _, first, count = np.unique(und_s, return_index=True, return_counts=True)
    same = first[count == 2]
    ta, tb = own_s[same], own_s[same + 1]
    agree = d_s[same, 0] != d_s[same + 1, 0]            # opposite directions: consistent
    # breadth-first over the pairs, flipping to agree with the first triangle reached
    adj = [[] for _ in range(m)]
    for a, b, ok in zip(ta.tolist(), tb.tolist(), agree.tolist()):
        adj[a].append((b, ok))
        adj[b].append((a, ok))
    # (plain lists: element access on numpy arrays is slow in a loop like this)
    flip = [False] * m
    seen = [False] * m
    comp = [-1] * m
    c = 0
    for start in range(m):
        if seen[start]:
            continue
        seen[start] = True
        comp[start] = c
        stack = [start]
        while stack:
            t = stack.pop()
            for u, ok in adj[t]:
                if not seen[u]:
                    seen[u] = True
                    comp[u] = c
                    flip[u] = flip[t] if ok else not flip[t]
                    stack.append(u)
        c += 1
    flip, comp = np.array(flip, bool), np.array(comp)
    # each piece: flip it wholesale if it has more triangles flipped than not
    wholesale = np.bincount(comp, weights=flip, minlength=c) * 2 > np.bincount(comp, minlength=c)
    flip ^= wholesale[comp]
    tris[flip] = tris[flip][:, ::-1]
    count = int(flip.sum())
    # facing: outward for pieces at even depth, inward inside an odd number of others
    a, b, cc = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    vol6 = np.einsum("ij,ij->i", a, np.cross(b, cc))
    by_piece = np.argsort(comp, kind="stable")
    pieces = np.split(by_piece, np.cumsum(np.bincount(comp, minlength=c))[:-1])
    closed = [p for p in pieces if len(p) >= 4 and not len(_open_edges(tris[p]))]
    if not closed:
        return tris, count
    lo = np.array([pts[tris[q]].min(axis=(0, 1)) for q in closed])
    hi = np.array([pts[tris[q]].max(axis=(0, 1)) for q in closed])
    rays = {}       # (each piece's triangles made ready for _inside, once: a part of 500
    # pieces asked 300,000 times; dropped when the piece is turned, so it's made again)
    for k, p in enumerate(closed):
        if vol6[p].sum() > 0:
            continue
        # inside out, unless it lies wholly inside another piece: then it may be a cavity
        # (or a body buried in another: either way the file's own facing is the best clue)
        corners = np.unique(tris[p])
        sample = pts[corners[:: max(1, len(corners) // 12)]]
        # (a piece whose box doesn't hold every sample point can't hold them all)
        holds = np.nonzero(np.all(sample.min(axis=0) >= lo, axis=1) & np.all(sample.max(axis=0) <= hi, axis=1))[0]
        if not any(all(_inside(rays.get(j) or rays.setdefault(j, _ray_ready(pts, tris[closed[j]])), x) for x in sample)
                   for j in holds.tolist() if j != k):
            tris[p] = tris[p][:, ::-1]
            count += len(p)
            rays.pop(k, None)
    return tris, count


_RAY = np.array([0.5773, 0.5774, 0.5775]) / np.linalg.norm([0.5773, 0.5774, 0.5775])


def _ray_ready(pts, tris):
    """What _inside needs of a closed mesh's triangles, worked out once."""
    a, b, c = pts[tris[:, 0]], pts[tris[:, 1]], pts[tris[:, 2]]
    e1, e2 = b - a, c - a
    p = np.cross(_RAY, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-15
    inv = np.where(ok, 1 / np.where(ok, det, 1), 0)
    top = np.maximum(np.maximum(a, b), c)
    return a, e1, e2, p, ok, inv, top, top.max(axis=0)


def _inside(ready, point):
    """Is the point inside the closed mesh (ray parity along a skewed direction)?
    ready: _ray_ready of its triangles."""
    a, e1, e2, p, ok, inv, top, high = ready
    if np.any(point > high):
        return False
    # (the ray climbs in x, y and z alike: a triangle wholly below the point in any of
    # them can't be hit, give or take a margin far beyond rounding)
    near = np.nonzero(np.all(top >= point - 1e-6, axis=1))[0]
    a, e1, e2, p, ok, inv = a[near], e1[near], e2[near], p[near], ok[near], inv[near]
    s = point - a
    u = np.einsum("ij,ij->i", s, p) * inv
    q = np.cross(s, e1)
    v = (q @ _RAY) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    hits = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-9)
    return bool(hits.sum() % 2)


# ---------------------------------------------------------------- holes

def _loops(open_):
    """Chains of open edges into closed loops of corners (in the edges' direction)."""
    nxt = {}
    for a, b in open_.tolist():
        nxt.setdefault(a, []).append(b)
    loops, used = [], set()
    for a, b in open_.tolist():
        if (a, b) in used:
            continue
        loop, cur, prev = [a], b, a
        used.add((a, b))
        while cur != a and len(loop) < 100000:
            loop.append(cur)
            options = [x for x in nxt.get(cur, []) if (cur, x) not in used]
            if not options:
                loop = None
                break
            prev, cur = cur, options[0]
            used.add((prev, cur))
        if loop:
            loops.append(loop)
    return loops


def _fill_holes(pts, tris):
    """Fill each loop of open edges with triangles. Returns (pts, tris, holes filled)."""
    open_ = _open_edges(tris)
    if not len(open_):
        return pts, tris, 0
    new, filled = [], 0
    loops = _loops(open_)
    # edges the mesh already has between corners of the loops: a patch running one of
    # them again would make an edge of three or four triangles (a patch across a hole
    # left by cutting out a speck, test14's holes)
    rim = np.zeros(int(tris.max()) + 1, bool)
    rim[[v for loop in loops for v in loop]] = True
    e = _edges(tris)
    e = e[rim[e].all(axis=1)]
    taken = set(zip(np.minimum(e[:, 0], e[:, 1]).tolist(), np.maximum(e[:, 0], e[:, 1]).tolist()))
    for loop in loops:
        # the patch runs the other way round the loop from the triangles around it
        patch = _triangulate(pts, loop[::-1], taken)
        if patch is None:
            # (no way round them: better an edge of three triangles than a hole, which
            # makes no solid at all; the companion cap built so before, not at all after)
            patch = _triangulate(pts, loop[::-1])
        if patch is not None:
            new.append(patch)
            filled += 1
            taken |= {(min(a, b), max(a, b)) for a, b in _edges(patch).tolist()}
    if new:
        tris = np.vstack([tris] + new)
    return pts, tris, filled


def _triangulate(pts, loop, taken=frozenset()):
    """Triangles covering a loop of corners: the smallest total area over all ways of
    cutting it into triangles (dynamic programming, as in Liepa's hole filling) for
    loops of up to 60 corners, otherwise ears cut off in the loop's best-fit plane.
    No new edge runs between corners joined already (taken: (low, high) pairs); None
    if there is no way round them."""
    def free(i, j):
        return (min(loop[i], loop[j]), max(loop[i], loop[j])) not in taken
    k = len(loop)
    if k < 3:
        return None
    if k == 3:
        return np.array([loop])
    P = pts[loop]
    if k <= 60:
        area = lambda i, j, m: 0.5 * np.linalg.norm(np.cross(P[j] - P[i], P[m] - P[i]))
        best = np.zeros((k, k))
        split = np.zeros((k, k), int)
        for gap in range(2, k):
            for i in range(k - gap):
                j = i + gap
                options = [best[i, m] + best[m, j] + area(i, m, j)
                           if (m == i + 1 or free(i, m)) and (m == j - 1 or free(m, j)) else np.inf
                           for m in range(i + 1, j)]
                m = int(np.argmin(options))
                best[i, j], split[i, j] = options[m], i + 1 + m
        out, stack = [], [(0, k - 1)]
        while stack:
            i, j = stack.pop()
            if j - i < 2:
                continue
            m = split[i, j]
            out.append((loop[i], loop[m], loop[j]))
            stack += [(i, m), (m, j)]
        return np.array(out) if np.isfinite(best[0, k - 1]) else None
    # large loop: ear clipping in the best-fit plane
    mid = P.mean(axis=0)
    V = _right_vectors(P - mid)
    xy = (P - mid) @ V[:2].T
    signed = 0.5 * np.sum(xy[:, 0] * np.roll(xy[:, 1], -1) - np.roll(xy[:, 0], -1) * xy[:, 1])
    idx = list(range(k)) if signed > 0 else list(range(k))[::-1]
    out = []
    guard = 0
    while len(idx) > 3 and guard < 10 * k:
        guard += 1
        for t in range(len(idx)):
            i0, i1, i2 = idx[t - 1], idx[t], idx[(t + 1) % len(idx)]
            a, b, c = xy[i0], xy[i1], xy[i2]
            if (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]) <= 0:
                continue                    # reflex corner
            if len(idx) > 3 and not free(i0, i2):
                continue
            others = [xy[x] for x in idx if x not in (i0, i1, i2)]
            if any(_in_triangle(p, a, b, c) for p in others):
                continue
            out.append((loop[i0], loop[i1], loop[i2]) if signed > 0 else (loop[i2], loop[i1], loop[i0]))
            idx.pop(t)
            break
        else:
            break
    if len(idx) == 3:
        i0, i1, i2 = idx
        out.append((loop[i0], loop[i1], loop[i2]) if signed > 0 else (loop[i2], loop[i1], loop[i0]))
    elif len(idx) > 3:
        # couldn't clip (a twisted loop): a fan from a new corner in the middle
        return None
    return np.array(out)


def _in_triangle(p, a, b, c):
    def side(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])
    d1, d2, d3 = side(p, a, b), side(p, b, c), side(p, c, a)
    return not ((d1 < 0 or d2 < 0 or d3 < 0) and (d1 > 0 or d2 > 0 or d3 > 0))


# ---------------------------------------------------------------- self-crossing specks

def crossing_triangles(pts, tris):
    """Pairs of triangles (sharing no corner) that pass through each other."""
    if len(tris) < 2:
        return np.zeros((0, 2), int)
    T = pts[tris]
    lo, hi = T.min(axis=1), T.max(axis=1)
    ext = (hi - lo).max(axis=1)
    # (only triangles up to twice the usual size are compared: specks are made of small
    # triangles, and a big triangle crossing another is a body sunk into another)
    h = max(float(np.percentile(ext, 95)) * 2, 1e-6)
    small = ext <= h
    cells_lo = np.floor(lo / h).astype(np.int64)
    cells_hi = np.floor(hi / h).astype(np.int64)
    # small triangles: into every grid cell their box touches (at most 3 x 3 x 3)
    entries_t, entries_c = [], []
    idx = np.nonzero(small)[0]
    for dx in range(3):
        for dy in range(3):
            for dz in range(3):
                c = cells_lo[idx] + [dx, dy, dz]
                ok = np.all(c <= cells_hi[idx], axis=1)
                entries_t.append(idx[ok])
                entries_c.append(c[ok])
    et = np.concatenate(entries_t)
    ec = np.concatenate(entries_c)
    key = (ec[:, 0] * 73856093) ^ (ec[:, 1] * 19349663) ^ (ec[:, 2] * 83492791)
    order = np.argsort(key, kind="stable")
    key, et = key[order], et[order]
    starts = np.r_[0, np.nonzero(key[1:] != key[:-1])[0] + 1]
    ends = np.r_[starts[1:], len(key)]
    # every entry with each one after it in its cell (up to 199 on: an overfull cell is
    # a pile of slivers, not a speck), written out directly rather than offset by offset
    pos = np.arange(len(key))
    end = np.repeat(ends, ends - starts)
    count = np.minimum(end - pos - 1, 199)
    if not count.sum():
        return np.zeros((0, 2), int)
    first = np.repeat(pos, count)
    second = first + 1 + np.arange(len(first)) - np.repeat(np.cumsum(count) - count, count)
    a, b = et[first], et[second]
    # boxes apart: not a crossing (checked before the duplicates are sorted out, as it
    # leaves far fewer to sort)
    # (one axis at a time, each on the pairs left by the one before: tens of millions of
    # pairs come from crowded cells, and most part on the first axis)
    for k in range(3):
        l, u = np.ascontiguousarray(lo[:, k]), np.ascontiguousarray(hi[:, k])
        ov = (l[a] <= u[b]) & (u[a] >= l[b])
        a, b = a[ov], b[ov]
    a, b = np.minimum(a, b), np.maximum(a, b)
    n = np.int64(len(tris))
    code = np.unique(a.astype(np.int64) * n + b)
    pairs = np.c_[code // n, code % n]
    pairs = pairs[pairs[:, 0] != pairs[:, 1]]
    # sharing a corner: not a crossing (neighbours meet along an edge or at a point)
    share = (tris[pairs[:, 0]][:, :, None] == tris[pairs[:, 1]][:, None, :]).any(axis=(1, 2))
    pairs = pairs[~share]
    if not len(pairs):
        return pairs
    hit = np.zeros(len(pairs), bool)
    A, B = T[pairs[:, 0]], T[pairs[:, 1]]
    for X, Y in ((A, B), (B, A)):
        for k in range(3):
            hit |= _segment_hits_triangle(X[:, k], X[:, (k + 1) % 3], Y)
    return pairs[hit]


def _segment_hits_triangle(p, q, T):
    """Does segment p-q (strictly) cross triangle T (Moller-Trumbore)?"""
    d = q - p
    e1, e2 = T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]
    h = np.cross(d, e2)
    a = np.einsum("ij,ij->i", e1, h)
    ok = np.abs(a) > 1e-14
    f = np.where(ok, 1 / np.where(ok, a, 1), 0)
    s = p - T[:, 0]
    u = f * np.einsum("ij,ij->i", s, h)
    qv = np.cross(s, e1)
    v = f * np.einsum("ij,ij->i", d, qv)
    t = f * np.einsum("ij,ij->i", e2, qv)
    eps = 1e-9
    return ok & (u > eps) & (v > eps) & (u + v < 1 - eps) & (t > eps) & (t < 1 - eps)


def _self_crossing_specks(pts, tris, max_size):
    """Triangles to cut out: the small clusters of self-crossing triangles, with a
    margin of rings around each. None if there are none (or only large crossings,
    which are left alone: a body sunk into another is fused later, not cut). Sets
    _self_crossing_specks.between: whether only separate pieces cross where it cuts."""
    pairs = crossing_triangles(pts, tris)
    _self_crossing_specks.pairs = len(pairs)     # (for repair.left)
    if len(pairs):
        piece = _pieces(tris)
        own = np.zeros(len(tris), bool)          # (crossing its own piece)
        own[pairs[piece[pairs[:, 0]] == piece[pairs[:, 1]]].ravel()] = True
    if not len(pairs):
        return None
    bad = np.zeros(len(tris), bool)
    bad[np.unique(pairs)] = True
    # clusters of crossing triangles (linked through shared corners); the small ones go,
    # with a margin of rings of triangles around them
    cut = np.zeros(len(tris), bool)
    for members in _components(tris, bad):
        if np.ptp(pts[np.unique(tris[members])], axis=0).max() <= max_size:
            cut[members] = True
    if not cut.any():
        return None
    core = cut.copy()
    for _ in range(MARGIN_RINGS):
        cut |= np.isin(tris, np.unique(tris[cut])).any(axis=1)
    # each cut (with its margin) only if the patch across it stays near the surface it
    # replaces: on a coarse mesh the margin's rings are big triangles, and a crossing of
    # two pairs had 240 mm^2 cut away and patched flat, 213 mm^3 off (a mount); test14
    # lost 1,874 mm^3 to a "speck" 122 mm across. True specks move it 0.04 mm at most
    # (all together first: the pieces of one knot can each move the surface a little
    # and together hardly at all, Solar Arm Mount's four 0.05-0.07 mm, together 0.016)
    # (worked out on the triangles round the cut only: each corner's triangles listed once)
    corner_of = np.argsort(tris.ravel(), kind="stable")
    first = np.searchsorted(tris.ravel()[corner_of], np.arange(len(pts) + 1))

    def around(members):
        """The triangles with a corner on these."""
        v = np.unique(tris[members])
        return np.unique(np.concatenate([corner_of[first[x]:first[x + 1]] for x in v.tolist()]) // 3)

    def shift(members):
        at = around(members)
        return _patch_shift(pts, tris[at], np.isin(at, members))

    _self_crossing_specks.between = not own[cut].any()
    if _self_crossing_specks.between or shift(np.nonzero(cut)[0]) <= SPECK_SHIFT:
        return cut
    pieces = _components(tris, cut)
    if len(pieces) > SPECK_TRIALS:
        return None     # (a mesh crossing itself all over: no speck to cut)
    keep = np.zeros(len(tris), bool)
    for members in pieces:
        moved = shift(members)
        if moved <= SPECK_SHIFT:
            keep[members] = True
            continue
        # else the cut with one ring of margin, if it moves the surface clearly less: a wall
        # poking 0.004 mm through a big flat triangle had 97 triangles cut round it with
        # two rings (0.89 mm off), 31 with one (0.02 mm). Still cut if it moves it up to
        # SPECK_SHIFT_MOST: a crossing left in the mesh costs the build far more (the GPS
        # body's, 0.22 mm off, left alone: 7,836 faces -> 29,458, not valid). Where only
        # pieces cross each other the cut lies inside the other body, fused away later:
        # it need only patch (Assembly 378 faces so, 423 left alone)
        if not own[members].any():
            keep[members] = True        # (patched with the rest, as before)
            continue
        # (the narrow cut only if clearly gentler: its patch runs right beside the
        # crossing, and on the GPS body, 0.22 mm against 0.26, curves round it then left
        # gaps every try: 7,836 faces -> 27,571)
        best = members
        inner = members[core[members]]
        if len(inner) and (narrow_moved := shift(narrow := around(inner))) < NARROW_GAIN * moved:
            moved, best = narrow_moved, narrow
        if moved <= SPECK_SHIFT_MOST:
            keep[best] = True
    _self_crossing_specks.between = not own[keep].any()
    return keep if keep.any() else None


def _pieces(tris):
    """Which connected piece (through shared corners) each triangle is in."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    n = int(tris.max()) + 1
    e = _edges(tris)
    _, label = connected_components(coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)), directed=False)
    return label[tris[:, 0]]


def _patch_shift(pts, tris, cut):
    """How far, on average, patching the hole left by cutting these triangles out moves
    the surface (mm: the volume between them over the area cut); inf if it won't patch."""
    rim = np.zeros(int(tris.max()) + 1, bool)
    rim[np.intersect1d(tris[cut], tris[~cut])] = True
    e = _edges(tris)
    rest = np.tile(~cut, 3) & rim[e].all(axis=1)
    e = e[rest]
    n = np.int64(len(rim))
    fwd, back = e[:, 0] * n + e[:, 1], e[:, 1] * n + e[:, 0]
    open_ = e[~np.isin(fwd, back)]
    if len(open_) > SPECK_RIM:
        return np.inf       # (no speck: and patching a loop of thousands took minutes)
    loops = _loops(open_)
    if not loops or sum(len(l) for l in loops) != len(open_):
        return np.inf
    taken = set(zip(np.minimum(e[:, 0], e[:, 1]).tolist(), np.maximum(e[:, 0], e[:, 1]).tolist()))
    patch = []
    for loop in loops:
        p = _triangulate(pts, loop[::-1], taken)
        if p is None:
            return np.inf
        patch.append(p)
        taken |= {(min(a, b), max(a, b)) for a, b in _edges(p).tolist()}
    T = pts[tris[cut]]
    P = pts[np.vstack(patch)]
    six = lambda X: np.einsum("ij,ij->i", X[:, 0], np.cross(X[:, 1], X[:, 2])).sum()
    area = 0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1).sum()
    return abs(six(P) - six(T)) / 6 / max(area, 1e-12)


def _components(tris, mask):
    """Groups of the masked triangles connected through shared corners."""
    idx = np.nonzero(mask)[0]
    parent = {int(i): int(i) for i in idx}

    def root(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by_corner = {}
    for i in idx.tolist():
        for v in tris[i].tolist():
            by_corner.setdefault(v, []).append(i)
    for members in by_corner.values():
        r0 = root(members[0])
        for m in members[1:]:
            r = root(m)
            if r != r0:
                parent[r] = r0
    groups = {}
    for i in idx.tolist():
        groups.setdefault(root(i), []).append(i)
    return [np.array(g) for g in groups.values()]
