"""Mesh check and repair, run on every part before it's converted (and when
a file is added, so the app can show what it found).

prepare_part() looks for each problem below, counts it, and fixes what can
be fixed, in an order where each fix makes the next ones easier:

   1. broken numbers (NaN / infinite coordinates)
   2. squashed, duplicate and back-to-back triangles
   3. zero-thickness internal walls (left inside parts by generators and
      slicer projects where shapes were glued together)
   4. tiny cracks between edges that should be joined
   5. flipped triangles
   6. pinched points (the surface touching itself at a single corner)
   7. loose debris
   8. holes and tangled edges (edges shared by 3+ triangles)
   9. self-intersections (the surface passing through itself)
  10. inside-out parts
  11. pieces that overlap each other (merged only when asked to)
  12. open surfaces (no thickness: can't become a solid)
  13. scale (a note when the size suggests the wrong unit)

Then the fixed mesh is checked again from scratch, with no fixing, and it's
only called ready for conversion when that second check is clean.

The report is a list of Issue dicts (see _issue) for the apps. Nothing here
changes the file on disk; "Save fixed mesh" writes a copy.
"""
import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .meshio import (EdgeTopology, MeshError, MeshInfo, compact, face_components, face_normals_areas, winding_number,
                     fill_small_holes, fix_winding, organise_bodies, remove_degenerate, repair_shell_light,
                     repair_shell_meshfix, Shell, signed_volume, split_shells, triangles_to_indexed)
from .report import NULL

# a wall counts as having no material when it's thinner than this (x the
# part's size): 0.003 mm on a 140 mm part, far below anything printable
WALL_THICKNESS_REL = 2e-5
# gaps narrower than this (x size) are cracks to close
CRACK_REL = 1e-5
# self-intersection repair rewrites the whole shell; above this it takes
# too long (the conversion copes with a few crossings anyway)
SELF_FIX_MAX_FACES = 600_000
# ...or when the surface crosses itself in more places than this (absolute,
# or x the number of triangles): heavily folded scans would take many
# minutes and gain nothing (the conversion smooths over small folds)
SELF_FIX_MAX_CROSSINGS = 2000
SELF_FIX_MAX_CROSSINGS_REL = 0.01
# the most repaired patches that are compared with the original one by one
SELF_FIX_MAX_PATCHES = 400
# patches of a repair touching more triangles than this aren't measured
# (it would take minutes); they're put back instead
MAX_CHECK_TRIS = 40_000
# where a repair changed the surface, it may move it by at most this much on
# average (x the part's size): 0.03 mm on a 140 mm part
FIX_DEVIATION_REL = 2e-4
# a repair may add or remove at most this much material (x the part's
# volume), in each place it changed and in all of them together
MAX_FIX_VOLUME_CHANGE = 1e-4

STATUS_ORDER = ("not_fixed", "action", "fixed", "note")


def _issue(code, count, status, problem, result):
    """status: fixed | not_fixed | action (the user can fix it with a button)
    | note (information only)."""
    return {"code": code, "count": int(count), "status": status, "problem": problem, "result": result}


def _plural(n, one, many=None):
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


def summarize(issues, verified=None):
    """Overall status for the apps: ok | fixed | action | problems."""
    st = [i["status"] for i in issues]
    if "not_fixed" in st or (verified is not None and verified.get("ready") is False):
        status = "problems"
    elif "action" in st:
        status = "action"
    elif "fixed" in st:
        status = "fixed"
    else:
        status = "ok"
    out = {"status": status, "issues": sorted(issues, key=lambda i: STATUS_ORDER.index(i["status"]))}
    if verified is not None:
        out["verified"] = verified
    return out


# ----------------------------------------------------------------------------
# Detectors (they only look, never change anything)
# ----------------------------------------------------------------------------


def wall_components(V, F, diag, topo=None):
    """Faces of zero-thickness internal walls: pieces (other than the biggest)
    that have no open edges of their own but enclose no material - two
    sheets lying on top of each other, facing opposite ways."""
    if len(F) == 0:
        return np.zeros(0, bool), 0
    topo = topo or EdgeTopology(V, F)
    n, lab = face_components(F, topo)
    if n < 2:
        return np.zeros(len(F), bool), 0
    sizes = np.bincount(lab, minlength=n)
    main = int(np.argmax(sizes))
    _, areas = face_normals_areas(V, F)
    area_c = np.bincount(lab, weights=areas, minlength=n)
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    vol_f = np.einsum("ij,ij->i", a, np.cross(b, c)) / 6.0
    vol_c = np.bincount(lab, weights=vol_f, minlength=n)
    # a piece's own open edges: edges used once within that piece
    nv = len(V)
    he_a, he_b = F.reshape(-1), np.roll(F, -1, axis=1).reshape(-1)
    key = np.minimum(he_a, he_b) * nv + np.maximum(he_a, he_b)
    comp = np.repeat(lab, 3)
    ck = comp.astype(np.int64) * (nv * nv + 1) + key
    _, inv, cnt = np.unique(ck, return_inverse=True, return_counts=True)
    open_he = cnt[inv.reshape(-1)] == 1
    has_open = np.bincount(comp[open_he], minlength=n) > 0
    thin = np.abs(vol_c) < WALL_THICKNESS_REL * diag * np.maximum(area_c, 1e-30)
    # a piece with triangles facing different ways can measure no volume
    # and still be a solid (it's turned the right way later)
    mixed = np.bincount(comp[topo.same_dir.reshape(-1)], minlength=n) > 0 if topo.same_dir.any() \
        else np.zeros(n, bool)
    wall = thin & ~has_open & ~mixed
    wall[main] = False
    return wall[lab], int(wall.sum())


def _thin(V, F, diag):
    """True when the faces enclose (practically) no material."""
    _, areas = face_normals_areas(V, F)
    return abs(signed_volume(V, F)) < WALL_THICKNESS_REL * diag * max(float(areas.sum()), 1e-30)


def glued_sheets(V, F, diag, topo=None):
    """Faces of internal double walls made of two separate sheets: open
    pieces whose every open edge is a tangled edge (attached to the rest)
    and whose faces lie exactly on an opposite-facing face of another piece
    - what's left where two shapes were glued face to face."""
    topo = topo or EdgeTopology(V, F)
    n, lab = face_components(F, topo)
    if n < 2:
        return np.zeros(len(F), bool), 0
    sizes = np.bincount(lab, minlength=n)
    main = int(np.argmax(sizes))
    nv = len(V)
    he_a, he_b = F.reshape(-1), np.roll(F, -1, axis=1).reshape(-1)
    key = np.minimum(he_a, he_b) * nv + np.maximum(he_a, he_b)
    comp = np.repeat(lab, 3)
    ck = comp.astype(np.int64) * (nv * nv + 1) + key
    _, inv, cnt = np.unique(ck, return_inverse=True, return_counts=True)
    own_open = cnt[inv.reshape(-1)] == 1
    attached = topo.edge_count > 2
    loose = own_open & ~attached  # open edges not attached to anything
    n_open = np.bincount(comp[own_open], minlength=n)
    n_loose = np.bincount(comp[loose], minlength=n)
    cand = np.nonzero((n_open > 0) & (n_loose == 0) & (sizes < 0.2 * len(F)))[0]
    cand = cand[cand != main]
    if not len(cand):
        return np.zeros(len(F), bool), 0
    normals, areas = face_normals_areas(V, F)
    cen = V[F].mean(1)
    tree = cKDTree(cen)
    T = V[F]
    tol = WALL_THICKNESS_REL * diag
    wall = np.zeros(n, bool)
    for c in cand:
        faces = np.nonzero(lab == c)[0]
        faces = faces[np.argsort(-areas[faces])][:32]
        ok = True
        for f in faces:
            _, near = tree.query(cen[f], k=min(24, len(F)))
            near = np.atleast_1d(near)
            near = near[lab[near] != c]
            if not len(near):
                ok = False
                break
            # opposite-facing, in the same plane, containing the centroid
            opp = normals[near] @ normals[f] < -0.99
            dist = np.abs(np.einsum("ij,ij->i", cen[f] - T[near, 0], normals[near]))
            hit = False
            for g in near[opp & (dist < tol)]:
                a, b, cc = T[g]
                v0, v1, v2 = b - a, cc - a, cen[f] - a
                d00, d01, d11 = v0 @ v0, v0 @ v1, v1 @ v1
                d20, d21 = v2 @ v0, v2 @ v1
                den = d00 * d11 - d01 * d01
                if den <= 0:
                    continue
                v = (d11 * d20 - d01 * d21) / den
                w = (d00 * d21 - d01 * d20) / den
                if v >= -1e-9 and w >= -1e-9 and v + w <= 1 + 1e-9:
                    hit = True
                    break
            if not hit:
                ok = False
                break
        wall[c] = ok
    return wall[lab], int(wall.sum())


def pinched_vertices(F, topo, nv):
    """Corner groups per vertex: a vertex whose triangles form more than one
    fan (joined through shared edges) is a pinch point. Returns
    (corner_labels, n_pinched) where corner_labels gives each face corner
    (3*f + i) the id of its fan."""
    nf = len(F)
    if nf == 0:
        return np.zeros(0, np.int64), 0
    vert = F.reshape(-1)
    twin = topo.twin
    rows, cols = [], []
    for i in range(3):
        h = 3 * np.arange(nf) + i  # half-edge from corner i to corner i+1
        t = twin[h]
        ok = t >= 0
        f, tf, ti = h[ok] // 3, t[ok] // 3, t[ok] % 3
        # the faces share the edge's two corners; match them by vertex
        for mine in (i, (i + 1) % 3):
            for theirs in (ti, (ti + 1) % 3):
                r = 3 * f + mine
                c = 3 * tf + theirs
                same = vert[r] == vert[c]
                rows.append(r[same])
                cols.append(c[same])
    rows = np.concatenate(rows)
    cols = np.concatenate(cols)
    g = sparse.coo_matrix((np.ones(len(rows), np.int8), (rows, cols)), shape=(3 * nf, 3 * nf))
    _, lab = connected_components(g, directed=False)
    pairs = np.unique(np.stack([vert, lab], 1), axis=0)
    fans = np.bincount(pairs[:, 0], minlength=nv)
    return lab, int((fans > 1).sum())


def _segment_hits(P0, P1, A, B, C):
    """Segments P0-P1 crossing the interior of triangles ABC (touching only
    at an edge or corner doesn't count)."""
    eps = 1e-9
    e1, e2 = B - A, C - A
    d = P1 - P0
    p = np.cross(d, e2)
    det = np.einsum("ij,ij->i", e1, p)
    ok = np.abs(det) > 1e-14 * (np.linalg.norm(e1, axis=1) * np.linalg.norm(e2, axis=1) * np.linalg.norm(d, axis=1) + 1e-300)
    inv = np.zeros_like(det)
    inv[ok] = 1.0 / det[ok]
    tv = P0 - A
    u = np.einsum("ij,ij->i", tv, p) * inv
    q = np.cross(tv, e1)
    v = np.einsum("ij,ij->i", d, q) * inv
    t = np.einsum("ij,ij->i", e2, q) * inv
    return ok & (u > eps) & (v > eps) & (u + v < 1 - eps) & (t > eps) & (t < 1 - eps)


def _crossing(T, lo, hi, F, pr):
    """Which candidate pairs (i, j) of triangles really cross."""
    i, j = pr[:, 0], pr[:, 1]
    pr = pr[(lo[i] <= hi[j]).all(1) & (lo[j] <= hi[i]).all(1)]
    if not len(pr):
        return pr
    # neighbours sharing a corner touch there; that isn't a crossing
    i, j = pr[:, 0], pr[:, 1]
    pr = pr[~(F[i][:, :, None] == F[j][:, None, :]).any((1, 2))]
    if not len(pr):
        return pr
    i, j = pr[:, 0], pr[:, 1]
    Ti, Tj = T[i], T[j]
    # quick rejection: one triangle completely on one side of the other's plane
    for first in (True, False):
        P, Q = (Ti, Tj) if first else (Tj, Ti)
        n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
        d = np.einsum("ikj,ij->ik", Q - P[:, :1], n)
        tol = 1e-12 * np.einsum("ij,ij->i", n, n)[:, None]
        apart = (d > tol).all(1) | (d < -tol).all(1)
        pr, Ti, Tj = pr[~apart], Ti[~apart], Tj[~apart]
        if not len(pr):
            return pr
    hit = np.zeros(len(pr), bool)
    for a in range(3):
        b = (a + 1) % 3
        hit |= _segment_hits(Ti[:, a], Ti[:, b], Tj[:, 0], Tj[:, 1], Tj[:, 2])
        hit |= _segment_hits(Tj[:, a], Tj[:, b], Ti[:, 0], Ti[:, 1], Ti[:, 2])
    return pr[hit]


def _box_neighbours(lo, hi, which):
    """For each triangle in which (big ones: long or huge), the triangles
    whose boxes overlap its box: yields (triangle, neighbour indices). An
    R-tree finds them directly; without one, a KD-tree over box centres
    does (slower on long, thin triangles, whose spheres are much bigger
    than their boxes)."""
    if not len(which):
        return
    try:
        from rtree import index

        prop = index.Property()
        prop.dimension = 3
        tree = index.Index((np.arange(len(lo), dtype=np.int64), np.ascontiguousarray(lo), np.ascontiguousarray(hi)),
                           properties=prop)
        for s in range(0, len(which), 2000):
            w = which[s:s + 2000]
            ids, counts = tree.intersection_v(np.ascontiguousarray(lo[w]), np.ascontiguousarray(hi[w]))
            ids = np.asarray(ids, dtype=np.int64)
            ends = np.cumsum(counts)
            for b, a, e in zip(w, ends - counts, ends):
                near = ids[a:e]
                yield int(b), near[near != b]
        return
    except ImportError:
        pass
    cen = (lo + hi) / 2
    tree = cKDTree(cen)
    rad = np.linalg.norm(hi - lo, axis=1) / 2
    rmax = float(rad.max())
    for b in which:
        near = np.asarray(tree.query_ball_point(cen[b], rad[b] + rmax), dtype=np.int64)
        yield int(b), near[near != b]


def self_intersections(V, F, pairs=False):
    """Pairs of triangles (not sharing a corner) whose surfaces cross.
    Returns (count, faces involved), or with ``pairs`` (count, the crossing
    pairs as an (n, 2) array). A uniform grid finds candidates (tested as
    they're found, so memory stays small); the exact test checks every edge
    of each triangle against the other."""
    nf = len(F)
    if nf < 4:
        return 0, np.zeros((0, 2) if pairs else 0, np.int64)
    T = V[F]
    lo, hi = T.min(1), T.max(1)
    ext = (hi - lo).max(1)
    cell = max(float(np.median(ext)) * 2.0, 1e-12)
    clo = np.floor(lo / cell).astype(np.int64)
    chi = np.floor(hi / cell).astype(np.int64)
    span = chi - clo + 1
    ncell = span.prod(1)
    # very long triangles would land in too many cells: their neighbours
    # are found with a KD-tree instead
    big = ncell > 64
    small = np.nonzero(~big)[0]
    reps = ncell[small]
    tri = np.repeat(small, reps)
    start = np.repeat(np.cumsum(reps) - reps, reps)
    local = np.arange(len(tri)) - start
    sp = span[tri]
    dz = local % sp[:, 2]
    dy = (local // sp[:, 2]) % sp[:, 1]
    dx = local // (sp[:, 2] * sp[:, 1])
    key = ((clo[tri, 0] + dx) * 73856093) ^ ((clo[tri, 1] + dy) * 19349663) ^ ((clo[tri, 2] + dz) * 83492791)
    order = np.argsort(key, kind="stable")
    key, tri = key[order], tri[order]
    hits = []
    idx = np.arange(len(key))
    d = 1
    step = 1_000_000
    while len(idx):
        idx = idx[idx + d < len(key)]
        idx = idx[key[idx] == key[idx + d]]
        for s in range(0, len(idx), step):
            k = idx[s:s + step]
            pr = np.sort(np.stack([tri[k], tri[k + d]], 1), axis=1)
            pr = pr[pr[:, 0] != pr[:, 1]]
            if len(pr):
                hits.append(_crossing(T, lo, hi, F, pr))
        d += 1
    bigs = np.nonzero(big)[0]
    for b, near in _box_neighbours(lo, hi, bigs):
        for s in range(0, len(near), step):
            nb = near[s:s + step]
            hits.append(_crossing(T, lo, hi, F, np.sort(np.stack([np.full(len(nb), b), nb], 1), axis=1)))
    hits = [h for h in hits if len(h)]
    if not hits:
        return 0, np.zeros((0, 2) if pairs else 0, np.int64)
    hp = np.unique(np.concatenate(hits), axis=0)
    return len(hp), (hp if pairs else np.unique(hp.reshape(-1)))


def added_triangles(old, new):
    """Which triangles of shell ``new`` are not in shell ``old`` (made by a
    repair: a hole fill, say), matched by their corner positions."""
    def keys(V, F):
        P = np.round(V[F], 7).reshape(len(F), 3, 3)
        P = np.sort(P.view([("", P.dtype)] * 3).reshape(len(F), 3), axis=1)
        return P
    if len(old.F) == 0:
        return np.ones(len(new.F), bool)
    ko, kn = keys(old.V, old.F), keys(new.V, new.F)
    have = set(map(bytes, np.ascontiguousarray(ko)))
    return np.array([bytes(k) not in have for k in np.ascontiguousarray(kn)], bool)


def crossings_added(old, new):
    """Crossings in ``new`` that a repair made: pairs where at least one
    triangle is new (a fill that cuts through the part, say)."""
    k, pr = self_intersections(new.V, new.F, pairs=True)
    if not k:
        return 0
    new_tri = added_triangles(old, new)
    return int(new_tri[pr].any(1).sum())


def _hole_fills(V, loop):
    """Ways to close a hole (loop in fill order), simplest first: a fan
    around its centre, then a flat triangulation in the hole's own plane
    (which fits a hole whose centre lies outside it, or behind the part)."""
    loop = np.asarray(loop, np.int64)
    if len(loop) == 3:
        yield V, loop[None]
        return
    V2 = np.vstack([V, V[loop].mean(0)[None]])
    c = len(V)
    yield V2, np.stack([loop, np.roll(loop, -1), np.full(len(loop), c)], 1)
    import manifold3d

    P = V[loop]
    o = P.mean(0)
    _, _, vt = np.linalg.svd(P - o)
    Q = np.c_[(P - o) @ vt[0], (P - o) @ vt[1]]
    try:
        t = np.asarray(manifold3d.triangulate([Q]), np.int64).reshape(-1, 3)
    except Exception:
        return
    if len(t) != len(loop) - 2:
        return  # the outline overlaps itself in that plane
    tris = loop[t]
    # facing the same way as the loop runs
    n = np.cross(V[tris[:, 1]] - V[tris[:, 0]], V[tris[:, 2]] - V[tris[:, 0]]).sum(0)
    ref = np.cross(P - o, np.roll(P, -1, 0) - o).sum(0)
    yield V, (tris if n @ ref >= 0 else tris[:, ::-1])


def fill_holes_cleanly(shell):
    """Each hole closed on its own (see _hole_fills), keeping only the fills
    that cut through nothing: a big hole whose edge isn't flat (the
    open back of a wheel, a car's window) would otherwise be capped by a
    surface passing through the part behind it. Returns (shell, holes left
    open because every fill would cut through the part); the shell stays
    open when any hole is left."""
    topo = shell.topo
    if topo.n_nonmanifold_edges:
        return shell, 0
    loops = _hole_loops(shell)
    if not loops:
        return shell, 0
    V, F, left = shell.V, shell.F, 0
    E = np.sort(np.stack([F, np.roll(F, -1, 1)], 2).reshape(-1, 2), 1)
    edges = set(map(tuple, E.tolist()))
    # a corner where two holes meet (pinched) can't be followed reliably
    starts = topo.he_from[np.nonzero(topo.boundary_he)[0]]
    u, c = np.unique(starts, return_counts=True)
    pinched = set(u[c > 1].tolist())
    for loop in loops:
        loop = loop[::-1]  # boundary half-edges run a->b; the fill runs b->a
        if len(loop) < 3 or pinched.intersection(loop) or len(set(loop)) != len(loop):
            continue  # left open, but not because of a cut
        for V2, tris in _hole_fills(V, loop):
            # a diagonal between two corners that are already joined elsewhere
            # would leave an edge shared by three or more triangles
            e = np.sort(np.stack([tris, np.roll(tris, -1, 1)], 2).reshape(-1, 2), 1)
            ring = {(min(a, b), max(a, b)) for a, b in zip(loop, np.roll(loop, -1))}
            inner = [tuple(x) for x in e.tolist() if tuple(x) not in ring]
            if inner and (len(set(inner)) * 2 != len(inner) or any(x in edges for x in inner)):
                continue
            # tested against the triangles near it only (earlier fills included)
            T = V2[F]
            flo, fhi = V2[tris].min((0, 1)), V2[tris].max((0, 1))
            pad = 1e-9 * max(1.0, float(np.abs(V2).max()))
            near = np.nonzero(((T.min(1) <= fhi + pad) & (T.max(1) >= flo - pad)).all(1))[0]
            k, pr = self_intersections(V2, np.vstack([F[near], tris]), pairs=True)
            if not (k and (pr >= len(near)).any()):
                V, F = V2, np.vstack([F, tris])
                edges.update(inner)
                break
        else:
            left += 1  # every fill would cut through the part
    if len(F) == len(shell.F):
        return shell, left
    return Shell(V, F), left


def overlapping_bodies(bodies, exclude=None):
    """Groups of bodies whose solids overlap (sharing volume without one
    sitting completely inside the other). Returns a list of index lists.
    exclude(body) -> True leaves a body out."""
    closed = [k for k, b in enumerate(bodies) if all(s.closed for s in b) and not (exclude and exclude(b))]
    if len(closed) < 2:
        return []
    boxes = {k: (np.min([s.V.min(0) for s in bodies[k]], 0), np.max([s.V.max(0) for s in bodies[k]], 0)) for k in closed}
    mans = {}

    def man(k):
        if k not in mans:
            mans[k] = _to_manifold(bodies[k])
        return mans[k]

    edges = []
    for x in range(len(closed)):
        for y in range(x + 1, len(closed)):
            a, b = closed[x], closed[y]
            (la, ha), (lb, hb) = boxes[a], boxes[b]
            if (la > hb).any() or (lb > ha).any():
                continue
            ma, mb = man(a), man(b)
            if ma is None or mb is None:
                continue
            try:
                inter = (ma ^ mb).volume()
                va, vb = ma.volume(), mb.volume()
            except Exception:
                continue
            small = min(va, vb)
            if small <= 0:
                continue
            # touching only, or one fully inside the other: not an overlap
            if inter > 1e-6 * small and inter < 0.999 * small:
                edges.append((a, b))
    if not edges:
        return []
    n = len(bodies)
    g = sparse.coo_matrix((np.ones(len(edges)), ([e[0] for e in edges], [e[1] for e in edges])), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    groups = {}
    for a, b in edges:
        groups.setdefault(lab[a], set()).update((a, b))
    return [sorted(g) for g in groups.values()]


def touching_parts(parts, frames):
    """Groups of parts (lists of part indices) whose solids touch or overlap.
    parts: the bodies of each part; frames: an id per part for its
    coordinate system. Only parts that share one can touch: the parts of one
    3MF object, or everything on the build plate (separate objects keep
    their own coordinates when each part is converted once)."""
    n = len(parts)
    boxes = []
    for b in parts:
        shells = [s for body in b for s in body]
        if not shells or not all(s.closed for s in shells):
            boxes.append(None)
            continue
        boxes.append((np.min([s.V.min(0) for s in shells], 0), np.max([s.V.max(0) for s in shells], 0)))
    mans = {}

    def man(k):
        if k not in mans:
            mans[k] = _to_manifold([s for body in parts[k] for s in body])
        return mans[k]

    edges = []
    for i in range(n):
        for j in range(i + 1, n):
            if boxes[i] is None or boxes[j] is None or frames[i] != frames[j]:
                continue
            (li, hi), (lj, hj) = boxes[i], boxes[j]
            pad = 1e-4 * float(max(np.linalg.norm(hi - li), np.linalg.norm(hj - lj)))
            if (li - pad > hj).any() or (lj - pad > hi).any():
                continue
            mi, mj = man(i), man(j)
            if mi is None or mj is None:
                continue
            try:
                # touching solids join into one piece in an exact union
                joined = len((mi + mj).decompose()) < len(mi.decompose()) + len(mj.decompose())
            except Exception:
                continue
            if joined:
                edges.append((i, j))
    if not edges:
        return []
    g = sparse.coo_matrix((np.ones(len(edges)), ([e[0] for e in edges], [e[1] for e in edges])), shape=(n, n))
    _, lab = connected_components(g, directed=False)
    groups = {}
    for a, b in edges:
        groups.setdefault(lab[a], set()).update((a, b))
    return sorted(sorted(g) for g in groups.values())


def union_parts(parts, diag, report=NULL):
    """One set of bodies from several parts: their solids joined into one
    (None when a part can't be joined, e.g. it has holes)."""
    mans = [_to_manifold([s for body in b for s in body]) for b in parts]
    if any(m is None for m in mans):
        return None
    u = mans[0]
    for m in mans[1:]:
        u = u + m
    mesh = u.to_mesh()
    V = np.asarray(mesh.vert_properties)[:, :3].astype(np.float64)
    F = np.asarray(mesh.tri_verts).astype(np.int64)
    shells = [sh for sh in split_shells(V, F) if not (sh.closed and _thin(sh.V, sh.F, diag)) and sh.n_faces >= 4]
    return organise_bodies(shells, report)


def touching_issue(groups, names, choice):
    """The mesh check's report on touching parts (choice: ask/split/merge)."""
    n = sum(len(g) for g in groups)
    which = "; ".join(", ".join(names[k] for k in g) for g in groups)
    problem = f"{_plural(n, 'part')} touch each other ({which})"
    if choice == "merge":
        return _issue("touching_parts", n, "fixed", problem, "Merged them into one solid.")
    if choice == "split":
        return _issue("touching_parts", n, "note", problem,
                      "Kept as separate parts, as you chose. Use “Merge Touching Parts” to join them into one solid.")
    return _issue("touching_parts", n, "action", problem,
                  "Kept as separate parts for now. Choose “Split to Parts” to save each as its own STEP file, or "
                  "“Merge Touching Parts” to join them into one solid.")


def _to_manifold(body):
    try:
        import manifold3d as m3d

        V = np.vstack([s.V for s in body])
        off, Fs = 0, []
        for s in body:
            Fs.append(s.F + off)
            off += len(s.V)
        man = m3d.Manifold(m3d.Mesh(vert_properties=np.ascontiguousarray(V, dtype=np.float32),
                                    tri_verts=np.ascontiguousarray(np.vstack(Fs), dtype=np.uint32)))
        if man.status() != m3d.Error.NoError or man.is_empty():
            return None
        return man
    except Exception:
        return None


def _thin_sheet(shell, diag):
    """An open surface with no thickness to close (a single sheet): filling
    its edge would only make a flat, empty "solid"."""
    if not _hole_loops(shell):
        return False
    # the whole outline of a flat-ish sheet: closing it can't enclose volume
    filled = fill_small_holes(shell)
    return filled.closed and _thin(filled.V, filled.F, diag)


def _hole_loops(shell):
    """Boundary loops of a shell as vertex lists (best effort)."""
    topo = shell.topo
    bh = np.nonzero(topo.boundary_he)[0]
    nxt = {}
    for h in bh:
        nxt.setdefault(int(topo.he_from[h]), []).append(int(topo.he_to[h]))
    loops, seen = [], set()
    for s in list(nxt):
        if s in seen:
            continue
        loop, cur = [], s
        while cur in nxt and cur not in seen and len(loop) <= len(bh):
            seen.add(cur)
            loop.append(cur)
            cur = nxt[cur][0]
        if len(loop) >= 2:
            loops.append(loop)
    return loops


# ----------------------------------------------------------------------------
# Fixers
# ----------------------------------------------------------------------------


def close_cracks(V, F, diag):
    """Join open edges that should meet: open-edge corners closer than the
    crack size are merged, and a corner lying on another open edge splits
    that edge (a "T-junction", common in CAD exports). Returns (V, F,
    number of open edges that got closed)."""
    topo = EdgeTopology(V, F)
    before = topo.n_boundary_edges
    if before == 0:
        return V, F, 0
    tol = CRACK_REL * diag
    bh = np.nonzero(topo.boundary_he)[0]
    bv = np.unique(np.concatenate([topo.he_from[bh], topo.he_to[bh]]))
    # 1. merge open-edge corners that nearly coincide
    tree = cKDTree(V[bv])
    pairs = tree.query_pairs(tol, output_type="ndarray")
    if len(pairs):
        n = len(bv)
        g = sparse.coo_matrix((np.ones(len(pairs), np.int8), (pairs[:, 0], pairs[:, 1])), shape=(n, n))
        _, lab = connected_components(g, directed=False)
        first = np.full(lab.max() + 1, -1)
        for k in range(n):
            if first[lab[k]] < 0:
                first[lab[k]] = bv[k]
        remap = np.arange(len(V))
        remap[bv] = first[lab]
        F = remap[F]
        F = F[(F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])]
        V, F = compact(V, F)
        topo = EdgeTopology(V, F)
        bh = np.nonzero(topo.boundary_he)[0]
        bv = np.unique(np.concatenate([topo.he_from[bh], topo.he_to[bh]])) if len(bh) else np.zeros(0, np.int64)
    # 2. T-junctions: an open-edge corner lying on another open edge
    for _ in range(4):
        if not len(bh):
            break
        tree = cKDTree(V[bv])
        a, b = topo.he_from[bh], topo.he_to[bh]
        A, Bp = V[a], V[b]
        mid = (A + Bp) / 2
        half = np.linalg.norm(Bp - A, axis=1) / 2
        splits = {}
        split_faces = set()
        for k, h in enumerate(bh):
            f = int(h) // 3
            if f in split_faces:
                continue  # one edge per triangle per round (the fans would overlap)
            near = tree.query_ball_point(mid[k], half[k] + tol)
            if not near:
                continue
            cand = bv[near]
            cand = cand[(cand != a[k]) & (cand != b[k]) & (cand != F[f, (int(h) % 3 + 2) % 3])]
            if not len(cand):
                continue
            d = Bp[k] - A[k]
            L2 = float(d @ d)
            if L2 <= 0:
                continue
            t = (V[cand] - A[k]) @ d / L2
            proj = A[k] + t[:, None] * d
            dist = np.linalg.norm(V[cand] - proj, axis=1)
            ok = (t > 1e-6) & (t < 1 - 1e-6) & (dist <= tol)
            if ok.any():
                splits[int(h)] = sorted(zip(t[ok].tolist(), cand[ok].tolist()))
                split_faces.add(f)
        if not splits:
            break
        newF = []
        drop = np.zeros(len(F), bool)
        for h, pts in splits.items():
            f, i = divmod(h, 3)
            p, q, r = F[f, i], F[f, (i + 1) % 3], F[f, (i + 2) % 3]
            chain = [p] + [v for _, v in pts] + [q]
            for s in range(len(chain) - 1):
                newF.append((chain[s], chain[s + 1], r))
            drop[f] = True
        F = np.vstack([F[~drop], np.array(newF, dtype=np.int64)])
        topo = EdgeTopology(V, F)
        bh = np.nonzero(topo.boundary_he)[0]
        bv = np.unique(np.concatenate([topo.he_from[bh], topo.he_to[bh]])) if len(bh) else np.zeros(0, np.int64)
    return V, F, max(0, before - EdgeTopology(V, F).n_boundary_edges) // 2


def split_pinched(V, F, topo):
    """Give every fan of triangles around a pinched vertex its own copy of
    the vertex, so the surface no longer touches itself at a point."""
    lab, n = pinched_vertices(F, topo, len(V))
    if n == 0:
        return V, F, 0
    vert = F.reshape(-1)
    pairs, inv = np.unique(np.stack([vert, lab], 1), axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    # the first fan of each vertex keeps it; the others get new vertices
    firsts = np.r_[True, pairs[1:, 0] != pairs[:-1, 0]]
    newid = pairs[:, 0].copy()
    extra = pairs[~firsts, 0]
    newid[~firsts] = len(V) + np.arange(len(extra))
    F2 = newid[inv].reshape(-1, 3)
    return np.vstack([V, V[extra]]), F2, n


def _signed_hits(q, P, T, chunk=2_000_000):
    """For each segment q -> P[k]: the sum of +1 for every triangle of T it
    enters through (against the normal) and -1 for every one it leaves
    through. That's how much the winding number changes from q to P[k]."""
    out = np.zeros(len(P))
    if not len(T) or not len(P):
        return out
    A = T[:, 0]
    e1, e2 = T[:, 1] - A, T[:, 2] - A
    tv = q - A
    qv = np.cross(tv, e1)
    te = np.einsum("ij,ij->i", e2, qv)
    step = max(1, chunk // len(T))
    for s in range(0, len(P), step):
        d = P[s:s + step] - q
        p = np.cross(d[:, None, :], e2[None, :, :])
        det = np.einsum("kmj,mj->km", p, e1)
        ok = np.abs(det) > 1e-300
        inv = np.zeros_like(det)
        inv[ok] = 1.0 / det[ok]
        u = np.einsum("mj,kmj->km", tv, p) * inv
        v = (d @ qv.T) * inv
        t = te[None, :] * inv
        hit = ok & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 0) & (t < 1)
        out[s:s + step] = (np.sign(det) * hit).sum(1)
    return out


def _changes(a, b):
    """Which triangles of shell a are gone from shell b, and which of b's are
    new; plus a corner id for every triangle corner (equal ids = same
    point), to tell which changes belong together. MeshFix hands back
    coordinates rounded to single precision, so a vertex of b that close to
    one of a's is the same point."""
    _, ida = np.unique(a.V, axis=0, return_inverse=True)
    ida = ida.reshape(-1)
    tol = 8 * float(np.finfo(np.float32).eps) * max(float(np.abs(a.V).max()), 1e-30)
    d, near = cKDTree(a.V).query(b.V, distance_upper_bound=tol)
    hit = np.isfinite(d)
    idb = int(ida.max()) + 1 + np.arange(len(b.V))
    idb[hit] = ida[near[hit]]
    vid = np.vstack([ida[a.F], idb[b.F]])
    # the same triangle starting from another corner is still the same one
    rot = np.argmin(vid, axis=1)
    key = np.take_along_axis(vid, (rot[:, None] + np.arange(3)) % 3, axis=1)
    _, tid = np.unique(key, axis=0, return_inverse=True)
    tid = tid.reshape(-1)
    na = len(a.F)
    gone = ~np.isin(tid[:na], tid[na:])
    new = ~np.isin(tid[na:], tid[:na])
    return gone, new, vid, rot, tid


def _material_changed(a, b, Tr, Ta, rng, n=2000):
    """Volume where solid b has material and a doesn't, or the other way
    round, in the box around one patch of changes (Tr: a's triangles that
    are gone, Ta: b's new ones). Shell a may pass through itself: a point
    is inside it when its winding number is 1 or more."""
    pts = np.vstack([Tr.reshape(-1, 3), Ta.reshape(-1, 3)])
    lo, hi = pts.min(0), pts.max(0)
    pad = 1e-6 * float(np.linalg.norm(a.V.max(0) - a.V.min(0)))
    lo, hi = lo - pad, hi + pad
    box = float(np.prod(hi - lo))
    P = lo + rng.random((n, 3)) * (hi - lo)
    q = lo + (0.5 + 0.01 * rng.random(3)) * (hi - lo)
    inside = []
    for s in (a, b):
        T = s.V[s.F]
        near = (T.max(1) >= lo).all(1) & (T.min(1) <= hi).all(1)
        if near.sum() > MAX_CHECK_TRIS:
            return np.inf
        w0 = float(np.rint(winding_number(s.V, s.F, q[None])[0]))
        # inside-out parts and cavities wrap their inside the other way round
        sgn = 1.0 if signed_volume(s.V, s.F) >= 0 else -1.0
        inside.append(sgn * (w0 + _signed_hits(q, P, T[near])) >= 0.5)
    return float((inside[0] != inside[1]).mean()) * box


def too_many_crossings(k, n_faces):
    """Whether the self-crossing repair is skipped for this many crossings."""
    return k > SELF_FIX_MAX_CROSSINGS or (k > 50 and k > SELF_FIX_MAX_CROSSINGS_REL * n_faces)


def fix_self_intersections(shell, report=NULL):
    """MeshFix's full clean-up removes the crossing triangles and patches
    the gaps, but it can also eat into the part elsewhere. So every patch
    it changed is checked against the original solid (where the surface
    crosses itself, a point counts as inside when the surface wraps it at
    least once): a patch that adds or removes material is put back the way
    it was. Returns the fixed shell, or None."""
    if shell.n_faces > SELF_FIX_MAX_FACES:
        return None
    try:
        fixed = repair_shell_meshfix(shell, report)
    except Exception:
        return None
    if fixed is shell or not fixed.closed:
        return None
    gone, new, vid, rot, tid = _changes(shell, fixed)
    na = len(shell.F)
    ig, inew = np.nonzero(gone)[0], np.nonzero(new)[0]
    m = len(ig) + len(inew)
    if m == 0:
        return None
    # patches: changed triangles joined through shared corners
    tv = np.vstack([vid[:na][ig], vid[na:][inew]])
    nv = int(vid.max()) + 1
    g = sparse.coo_matrix((np.ones(3 * m), (np.repeat(np.arange(m), 3), m + tv.reshape(-1))),
                          shape=(m + nv, m + nv))
    _, lab = connected_components(g, directed=False)
    lab = lab[:m]
    if len(np.unique(lab)) > SELF_FIX_MAX_PATCHES:
        return None  # changed all over: not a local repair
    lab_g, lab_n = lab[:len(ig)], lab[len(ig):]
    # a patch may differ from the original solid by a sliver no thicker
    # than FIX_DEVIATION_REL x the part's size on average, and all of them
    # together by MAX_FIX_VOLUME_CHANGE of its volume
    size = float(np.linalg.norm(shell.V.max(0) - shell.V.min(0)))
    total_tol = MAX_FIX_VOLUME_CHANGE * abs(fixed.volume)
    rng = np.random.default_rng(0)
    Tg, Tn = shell.V[shell.F[ig]], fixed.V[fixed.F[inew]]
    _, ag = face_normals_areas(shell.V, shell.F[ig])
    _, an = face_normals_areas(fixed.V, fixed.F[inew])
    ok = {}
    for c in np.unique(lab):
        dv = _material_changed(shell, fixed, Tg[lab_g == c], Tn[lab_n == c], rng)
        area = max(float(ag[lab_g == c].sum()), float(an[lab_n == c].sum()))
        if dv <= min(FIX_DEVIATION_REL * size * area, total_tol):
            ok[c] = dv
    changed = 0.0
    for c in sorted(ok, key=ok.get):
        if changed + ok[c] > total_tol:
            del ok[c]
        else:
            changed += ok[c]
    bad = [c for c in np.unique(lab) if c not in ok]
    if not bad:
        return fixed
    if not ok:
        return None
    report.log(f"Mesh repair changed the shape in {_plural(len(bad), 'place')}; those were put back")
    # keep the fix only where it was safe: the original triangles plus the
    # good patches. Corners of the new triangles that the unchanged
    # triangles also use get the original's vertex ids.
    keep_g = np.isin(lab_g, bad)
    use_n = ~np.isin(lab_n, bad)
    to_old = -np.ones(len(fixed.V), np.int64)
    cb = np.nonzero(~new)[0]
    order = np.argsort(tid[:na])
    ca = order[np.searchsorted(tid[:na][order], tid[na:][cb])]
    for j in range(3):
        to_old[fixed.F[cb, (rot[na:][cb] + j) % 3]] = shell.F[ca, (rot[:na][ca] + j) % 3]
    Fn = fixed.F[inew[use_n]]
    fresh = np.unique(Fn[to_old[Fn] < 0])
    to_old[fresh] = len(shell.V) + np.arange(len(fresh))
    V = np.vstack([shell.V, fixed.V[fresh]])
    F = np.vstack([shell.F[~gone], shell.F[ig[keep_g]], to_old[Fn]])
    V, F = compact(V, F)
    out = Shell(V, F)
    return out if out.closed else None


def merge_overlaps(bodies, groups, diag, report=NULL, exclude=None):
    """Union each group of overlapping bodies into one solid (repeated
    while merged results still overlap something)."""
    total = 0
    for _ in range(3):
        if not groups:
            break
        bodies, n = _merge_once(bodies, groups, diag, report)
        total += n
        if not n:
            break
        groups = overlapping_bodies(bodies, exclude)
    return bodies, total, groups


def _merge_once(bodies, groups, diag, report):
    out, used = [], set()
    for g in groups:
        mans = [_to_manifold(bodies[k]) for k in g]
        if any(m is None for m in mans):
            continue
        u = mans[0]
        for m in mans[1:]:
            u = u + m
        mesh = u.to_mesh()
        V = np.asarray(mesh.vert_properties)[:, :3].astype(np.float64)
        F = np.asarray(mesh.tri_verts).astype(np.int64)
        # slivers with no material can fall out of a union
        shells = [sh for sh in split_shells(V, F) if not (sh.closed and _thin(sh.V, sh.F, diag)) and sh.n_faces >= 4]
        out.extend(organise_bodies(shells, report))
        used.update(g)
    rest = [b for k, b in enumerate(bodies) if k not in used]
    return rest + out, len(used)


# ----------------------------------------------------------------------------
# Check + fix one part
# ----------------------------------------------------------------------------


def prepare_part(tris, repair="auto", remove_debris=True, report=NULL, merge=False, label=""):
    """Check and fix one part. Returns (bodies, info, issues).
    repair: "off" (only check, plus the basic clean-up every conversion
    needs), "auto" or "aggressive" (MeshFix for every open shell).
    Each step is reported as a stage (with ``label`` after its name), so the
    app can show what is being checked and fixed."""
    fixing = repair != "off"

    def step(name, a, b):
        report.step(name + label, a, b)

    issues = []
    info = MeshInfo()
    tris = np.asarray(tris, dtype=np.float64).reshape(-1, 3, 3)
    info.input_triangles = int(len(tris))
    from . import __version__, build_id

    b = build_id()
    report.log(f"Engine {__version__}" + (f" (build {b})" if b else ""))
    report.log(f"Read {len(tris):,} triangles")

    # 1. broken numbers
    step("Checking for broken and squashed triangles", 0.0, 0.08)
    finite = np.isfinite(tris).all(axis=(1, 2))
    n_bad = int((~finite).sum())
    if n_bad:
        issues.append(_issue("bad_numbers", n_bad, "fixed", f"{_plural(n_bad, 'triangle')} had broken coordinates",
                             "Removed them."))
    if not finite.any():
        raise MeshError("This file's triangles have broken coordinates, so it can't be read. Try exporting it again.")
    V, F = triangles_to_indexed(tris[finite])

    # 2. squashed / duplicate / back-to-back triangles
    stats = {}
    V, F = remove_degenerate(V, F, stats)
    V, F = compact(V, F)
    if len(F) == 0:
        raise MeshError("Every triangle in this mesh is squashed flat, so there is no shape to convert.")
    if stats.get("squashed"):
        n = stats["squashed"]
        issues.append(_issue("squashed", n, "fixed", f"{_plural(n, 'squashed triangle')} (corners in the same spot)",
                             "Removed them."))
    if stats.get("duplicates"):
        n = stats["duplicates"]
        issues.append(_issue("duplicates", n, "fixed", f"{_plural(n, 'duplicate triangle')}", "Removed the extra copies."))
    if stats.get("back_to_back"):
        n = stats["back_to_back"]
        issues.append(_issue("back_to_back", n, "fixed",
                             f"{_plural(n, 'pair')} of triangles lying back to back (paper-thin double walls)",
                             "Removed them."))
    lo, hi = V.min(0), V.max(0)
    diag = float(np.linalg.norm(hi - lo))
    info.diag = diag
    info.bbox = (lo, hi)

    # 3. zero-thickness internal walls (closed flat pieces, and pairs of
    # sheets glued face to face)
    step("Looking for hidden internal walls", 0.08, 0.22)
    n_walls = n_wall_tris = 0
    for _ in range(4):
        topo = EdgeTopology(V, F)
        wall, k = wall_components(V, F, diag, topo)
        if not k:
            wall, k = glued_sheets(V, F, diag, topo)
            k = (k + 1) // 2  # two sheets make one wall
        if not k:
            break
        n_walls += k
        n_wall_tris += int(wall.sum())
        F = F[~wall]
        V, F = compact(V, F)
    if n_walls:
        issues.append(_issue("internal_walls", n_walls, "fixed",
                             f"{_plural(n_walls, 'paper-thin internal wall')} ({n_wall_tris:,} triangles) hidden inside "
                             "the part, usually left where shapes were glued together",
                             "Removed them, so the outer surface is one clean piece."))

    # 4. tiny cracks
    if fixing:
        step("Joining tiny cracks", 0.22, 0.28)
        V, F, n_cracks = close_cracks(V, F, diag)
        if n_cracks:
            issues.append(_issue("cracks", n_cracks, "fixed",
                                 f"tiny cracks along {_plural(n_cracks, 'edge')} that should be joined",
                                 "Joined them."))

    # 5. flipped triangles
    step("Checking which way the triangles face", 0.28, 0.34)
    topo = EdgeTopology(V, F)
    F, conflicts, n_flipped = fix_winding(V, F, topo, count_flips=True)
    if n_flipped:
        issues.append(_issue("flipped", n_flipped, "fixed", f"{_plural(n_flipped, 'triangle')} facing the wrong way",
                             "Turned them around."))
    if conflicts:
        issues.append(_issue("twisted", conflicts, "not_fixed",
                             f"{_plural(conflicts, 'place')} where the surface is twisted (like a Möbius strip), so "
                             "its inside and outside can't be told apart", "Left as it is."))

    # 6. pinched points
    step("Checking for pinched points", 0.34, 0.38)
    topo = EdgeTopology(V, F)
    if fixing:
        V, F, n_pinch = split_pinched(V, F, topo)
        if n_pinch:
            issues.append(_issue("pinched", n_pinch, "fixed",
                                 f"{_plural(n_pinch, 'point')} where the surface touches itself at a single corner",
                                 "Separated them."))
    else:
        _, n_pinch = pinched_vertices(F, topo, len(V))
        if n_pinch:
            issues.append(_issue("pinched", n_pinch, "not_fixed",
                                 f"{_plural(n_pinch, 'point')} where the surface touches itself at a single corner",
                                 "Mesh repair is off."))

    shells = split_shells(V, F)

    # 7. debris
    if remove_debris and len(shells) > 1:
        step("Removing loose debris", 0.38, 0.40)
        areas = [face_normals_areas(s.V, s.F)[1].sum() for s in shells]
        amax = max(areas)
        biggest = int(np.argmax(areas))
        keep = []
        for k, (s, a) in enumerate(zip(shells, areas)):
            flat = s.closed and abs(s.volume) < 1e-9 * max(a, 1e-30) ** 1.5
            # small pieces next to a much bigger one (the biggest always stays)
            if k != biggest and ((s.n_faces < 4 and a < 0.01 * amax) or flat or a < 5e-5 * amax):
                info.removed_debris += 1
            else:
                keep.append(s)
        shells = keep
        if info.removed_debris:
            n = info.removed_debris
            issues.append(_issue("debris", n, "fixed", f"{_plural(n, 'tiny loose piece')}", "Removed them."))

    # 8. holes and tangled edges
    n_holes = n_tangled = 0
    big_holes = 0
    holes_left = tangled_left = 0
    holes_cut = 0  # left open: filling them would cut through the part
    fixed = []
    if any(not s.closed for s in shells):
        step("Filling holes and untangling edges" if fixing else "Checking for holes", 0.40, 0.52)
    for s in shells:
        if s.closed:
            fixed.append(s)
            continue
        if _thin_sheet(s, diag):
            fixed.append(s)  # a surface without thickness: reported below
            continue
        loops = _hole_loops(s)
        size = float(np.linalg.norm(s.V.max(0) - s.V.min(0)))
        h, t = len(loops), s.topo.n_nonmanifold_edges // 3 or (1 if s.topo.n_nonmanifold_edges else 0)
        big = sum(1 for L in loops if np.linalg.norm(s.V[L].max(0) - s.V[L].min(0)) > 0.25 * size)
        n_holes += h
        n_tangled += t
        big_holes += big
        if not fixing:
            fixed.append(s)
            holes_left += h
            tangled_left += t
            continue
        s2 = fill_small_holes(s)
        if s2.closed and _thin(s2.V, s2.F, diag):
            s2 = s  # closing it would make a solid with no thickness
        if not s2.closed and not _thin_sheet(s, diag):
            try:
                s2 = repair_shell_light(s2, report)
            except Exception as e:  # pragma: no cover
                report.log(f"Quick mesh repair didn't work ({e})")
        if not s2.closed and not _thin_sheet(s, diag) and (repair == "aggressive" or s2.topo.n_nonmanifold_edges
                                                         or s2.topo.n_boundary_edges):
            report.log("Running full mesh repair (this can take a while on big meshes)")
            try:
                s2 = repair_shell_meshfix(s2, report)
            except Exception as e:  # pragma: no cover
                report.warn(f"Full mesh repair failed ({e}); the mesh was converted as it is.")
        if s2.closed and _thin(s2.V, s2.F, diag):
            s2 = s
        if s2 is not s and crossings_added(s, s2):
            # a fill cuts through the part (a big hole whose edge isn't flat):
            # each hole is filled on its own, and the ones that can't be
            # filled cleanly are left open
            s3, cut = fill_holes_cleanly(s)
            if s3.closed and _thin(s3.V, s3.F, diag):
                s3, cut = s, 0
            holes_cut += cut
            report.log(f"Filling {_plural(len(loops), 'hole')} would have cut through the part; "
                       + ("each was filled on its own instead" if not cut else
                          f"{_plural(cut, 'hole')} left open"))
            s2 = s3
        if s2.closed:
            info.repaired += 1
            fixed.extend(split_shells(s2.V, s2.F))
        else:
            holes_left += len(_hole_loops(s2))
            tangled_left += 1 if s2.topo.n_nonmanifold_edges else 0
            fixed.append(s2)
    shells = fixed
    if n_tangled:
        ok = tangled_left == 0
        issues.append(_issue("tangled_edges", n_tangled, "fixed" if ok else "not_fixed",
                             f"tangled edges (shared by 3 or more triangles) in {_plural(n_tangled, 'place')}",
                             "Untangled them." if ok else ("Mesh repair is off." if not fixing else
                                                           "Some couldn't be untangled.")))
    if n_holes:
        ok = holes_left == 0
        msg = f"{_plural(n_holes, 'hole')} in the surface"
        if big_holes:
            msg += f" ({_plural(big_holes, 'big one')})"
        res = "Filled them." if ok else ("Mesh repair is off." if not fixing else
                                         f"Filled {n_holes - holes_left:,}; {holes_left:,} couldn't be closed"
                                         + (f" ({holes_cut:,} because filling them would cut through the part)"
                                            if holes_cut else "") + ".")
        if ok and big_holes:
            res = "Filled them. Big holes are filled with a simple surface, so check that the shape looks right."
        issues.append(_issue("holes", n_holes, "fixed" if ok else "not_fixed", msg, res))

    # 9. self-intersections (reported after merging, which can remove some)
    n_cross = 0
    many = False  # too many crossings in a shell to repair them
    crossing = {}  # id(shell) -> crossings left in it
    cross_open = 0  # of them in open surfaces (not repaired)
    out = []
    step("Looking for surfaces that pass through themselves", 0.52, 0.70)
    for s in shells:
        k, _ = self_intersections(s.V, s.F)
        s.crossings = k
        if not k:
            out.append(s)
            continue
        n_cross += k
        if not s.closed:
            # an open surface that crosses itself is reported, not fixed
            # (the fixer needs a closed surface)
            cross_open += k
            crossing[id(s)] = k
            out.append(s)
            continue
        if too_many_crossings(k, s.n_faces):
            many = True
        if fixing and not too_many_crossings(k, s.n_faces):
            step("Fixing surfaces that pass through themselves", 0.70, 0.84)
        s2 = fix_self_intersections(s, report) if fixing and not too_many_crossings(k, s.n_faces) else None
        if s2 is not None:
            parts = split_shells(s2.V, s2.F)
            left = [self_intersections(p.V, p.F)[0] if p.closed else 0 for p in parts]
            if sum(left) < k:
                for p, kl in zip(parts, left):
                    p.crossings = kl if p.closed else None
                    if kl:
                        crossing[id(p)] = kl
                out.extend(parts)
                continue
        crossing[id(s)] = k
        out.append(s)
    shells = out

    # 10. inside-out parts (organise_bodies turns them; cavities stay inward)
    ostats = {}
    bodies = organise_bodies(shells, report, ostats)
    if ostats.get("inside_out"):
        n = ostats["inside_out"]
        issues.append(_issue("inside_out", n, "fixed", f"{_plural(n, 'part')} inside out (surfaces facing inward)",
                             "Turned them the right way."))

    # 11. overlapping pieces (a piece whose own surface crosses itself can't
    # be merged reliably, so those stay as they are)
    def broken(body):
        return any(id(sh) in crossing for sh in body)

    if len(bodies) > 1:
        step("Checking for overlapping pieces", 0.84, 0.92)
    groups = overlapping_bodies(bodies)
    if groups:
        n = sum(len(g) for g in groups)
        if merge:
            step("Merging overlapping pieces", 0.92, 1.0)
            clean = overlapping_bodies(bodies, exclude=broken)
            bodies, _, _ = merge_overlaps(bodies, clean, diag, report, exclude=broken)
            # what still overlaps after merging
            left = overlapping_bodies(bodies)
            n_left = sum(len(g) for g in left)
            n_broken = sum(1 for g in left for k in g if broken(bodies[k]))
            if not n_left:
                res = "Merged them into one solid."
            elif n_broken:
                res = (f"{_plural(n_left, 'piece')} still overlap: pieces whose own surfaces pass through "
                       "themselves can't be merged.")
            else:
                res = f"{_plural(n_left, 'piece')} still overlap; they couldn't be merged."
            issues.append(_issue("overlaps", n, "not_fixed" if n_left else "fixed",
                                 f"{_plural(n, 'piece')} overlapping each other", res))
        else:
            issues.append(_issue("overlaps", n, "action", f"{_plural(n, 'piece')} overlapping each other",
                                 "Kept as separate solids. Use “Merge overlapping pieces” to join them into one."))

    if n_cross:
        cross_left = sum(crossing.get(id(sh), 0) for b in bodies for sh in b)
        if cross_left == 0:
            issues.append(_issue("self_intersections", n_cross, "fixed",
                                 f"the surface passes through itself in {_plural(n_cross, 'place')}", "Fixed them."))
        else:
            if not fixing:
                why = "Mesh repair is off."
            elif cross_open == cross_left:
                why = ("They are in pieces that aren't closed, which can't be repaired automatically. The "
                       "conversion works around them, but check the result.")
            elif many:
                why = ("Too many to repair automatically without risking the shape. The conversion works around "
                       "them, but check the result.")
            else:
                why = ("They couldn't be removed without changing the shape. The conversion usually copes with a "
                       "few; if the result looks wrong, fix them in a mesh editor.")
            issues.append(_issue("self_intersections", n_cross, "not_fixed",
                                 f"the surface passes through itself in {_plural(n_cross, 'place')}"
                                 + (f" ({cross_left:,} left)" if cross_left != n_cross else ""), why))

    # 12. open surfaces
    n_open = sum(1 for b in bodies if not all(s.closed for s in b))
    if n_open and fixing:
        issues.append(_issue("open_surface", n_open, "not_fixed",
                             f"{_plural(n_open, 'piece')} can't be closed into a solid (a surface with no thickness, "
                             "or holes too big to fill)", "Converted as surfaces instead of a solid."))

    # 13. scale
    if diag < 1.0:
        issues.append(_issue("scale", 0, "note", f"The model is only {diag:.3g} units across",
                             "If it was made in inches or meters, change Units in the settings."))
    elif diag > 3000.0:
        issues.append(_issue("scale", 0, "note", f"The model is {diag:,.0f} units across",
                             "If it was made in a smaller unit than millimeters, change Units in the settings."))
    return bodies, info, issues


# ----------------------------------------------------------------------------
# Second check: is the fixed mesh ready for conversion?
# ----------------------------------------------------------------------------


def verify_bodies(bodies, diag, merge=False):
    """Check the fixed mesh again from scratch (no fixing). Returns
    (ready, problems): problems is a list of plain-language strings for
    anything still wrong. Open surfaces and overlaps the user chose to keep
    are expected and don't count."""
    problems = []
    if not any(len(s.F) for b in bodies for s in b):
        problems.append("nothing is left of the mesh")
    for b in bodies:
        for s in b:
            topo = EdgeTopology(s.V, s.F)
            stats = {}
            remove_degenerate(s.V, s.F, stats)
            if stats.get("squashed"):
                problems.append("squashed triangles are left")
            if stats.get("duplicates") or stats.get("back_to_back"):
                problems.append("duplicate triangles are left")
            if topo.n_nonmanifold_edges:
                problems.append("tangled edges are left")
            # (on an open surface they're twisted spots, reported as such)
            if topo.same_dir.any() and s.closed:
                problems.append("some triangles still face the wrong way")
            _, k = pinched_vertices(s.F, topo, len(s.V))
            if k:
                problems.append("pinched points are left")
            if s.closed and _thin(s.V, s.F, diag):
                problems.append("a paper-thin wall is left")
    for b in bodies:
        if b and b[0].closed and b[0].volume < 0:
            problems.append("a part is still inside out")
    # counted again unless the check already counted this very shell
    per_shell = {id(s): s.crossings if s.crossings is not None else self_intersections(s.V, s.F)[0]
                 for b in bodies for s in b}
    crossings = sum(per_shell.values())
    if crossings:
        problems.append(f"the surface still passes through itself in {_plural(crossings, 'place')}")
    # pieces whose own surface crosses itself can't be merged (reported above)
    if merge and overlapping_bodies(bodies, exclude=lambda b: any(per_shell.get(id(s)) for s in b)):
        problems.append("pieces still overlap")
    seen = []
    for p in problems:
        if p not in seen:
            seen.append(p)
    return not seen, seen
