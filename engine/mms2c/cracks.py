"""Seal hairline cracks: bodies joined in a mesh with a crack left between them, made one solid.

A file can hold two shapes pressed together whose touching walls were faceted apart. The
drybox axle is a tube of radius 12.5 with a thread on it whose foot is also radius 12.5, each
a polygon with its own corners. Where the thread's polygon lies inside the tube's they
overlap and the exporter's union removed both walls; everywhere else (86% of the foot) it kept
both, as a crack 0.01 mm wide reaching the outside along the thread's foot. The mesh closes,
its volume is right, and 42% of its area is those walls, inside the part. Built as they are,
they make a solid whose faces nearly coincide and cross: valid to OpenCascade, but Fusion
refused half its cuts, and fitting surfaces to them took the conversion 18 minutes.

So: walls with another wall right across them, within GAP along their outward normal (found by
short rays), are told from the rest. The surface is cut where it folds back at those walls, and
along the concave creases that continue from them, which leaves the shapes that were pressed
together. Each is closed, one has its wall pushed DEPTH into the other, with the corners along
the wall's rim moving in the plane of the neighbouring face so that the outside doesn't shift,
and they are united (manifold3d's exact Boolean, as in Zhou et al., "Mesh Arrangements for Solid
Geometry", 2016). The cracks' thin space is filled and the walls drop out.

Nothing is kept unless the checks hold: the pieces hold the mesh's volume between them, the
union only gained what the cracks held, and the area fell by the walls it lost. Otherwise the
mesh goes on unchanged. Only called on meshes our repair can't close (see merged._mended).
"""

import numpy as np

GAP = 0.05          # mm: walls closer than this, facing each other across empty space, are a crack
DEPTH = 0.05        # mm: how far a wall is pushed into the shape behind it (more than the crack's width)
ANTI = -0.9         # cosine between two facing walls' normals, at or below which they are opposite
WORTH = 0.05        # share of the mesh's area in crack walls from which sealing is worth doing
FOLD = -0.97        # cosine between two triangles' normals at an edge, below which the surface folds back
CREASE = 0.87       # cosine between normals at a concave edge below which it counts as a crease (30 degrees)
VOLUME_SLACK = 1e-3 # share of the volume the pieces' sum may stray from the mesh's
SPECK = 1e-5        # share of the volume under which a piece of the union is dropped
SIMPLIFY = 0.003    # mm: what the union's cuts may be tidied by (its slivers cost faces downstream)
SHEET = 0.9         # cosine between neighbouring triangles' normals within one sheet of a wall
FLAT_MAX = 5.0      # a moved corner may travel at most this many times DEPTH (a shallow neighbouring face)


def _manifold(V, F):
    import manifold3d as m3

    return m3.Manifold(m3.Mesh64(np.asarray(V, float), np.asarray(F, np.uint64)))


def _edges(F):
    """(triangle pairs across each undirected edge used exactly twice, each edge's two corners)."""
    e = np.sort(np.concatenate([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]]), axis=1)
    own = np.tile(np.arange(len(F)), 3)
    _, inv, cnt = np.unique(e, axis=0, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    order = np.argsort(inv, kind="stable")
    first = np.concatenate([[0], np.cumsum(cnt)[:-1]])
    two = cnt == 2
    a = own[order[first[two]]]
    b = own[order[first[two] + 1]]
    corners = e[order[first[two]]]
    return a, b, corners


def _walls(M, V, F, normal, area):
    """For each triangle how many of its 4 probes find a facing wall within GAP."""
    # (the centre and three points towards the corners)
    w = np.array([[2, 2, 2], [4, 1, 1], [1, 4, 1], [1, 1, 4]], float) / 6
    hits = np.zeros(len(F), np.int8)
    probes = np.einsum("ij,tjk->tik", w, V[F])
    for t in np.nonzero(area > 1e-7)[0].tolist():
        n = normal[t]
        for p in probes[t]:
            if M.ray_cast(p + 1e-6 * n, p + GAP * n):
                hits[t] += 1
    return hits


def _cut(V, F, normal, hits):
    """Triangles' piece numbers: the surface cut where it folds back and along the concave
    creases that continue from a crack's wall."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    a, b, corners = _edges(F)
    cosine = np.einsum("ij,ij->i", normal[a], normal[b])
    third = F[b].sum(1) - corners.sum(1)        # (b's corner off the edge)
    side = np.einsum("ij,ij->i", normal[a], V[third] - V[corners[:, 0]])
    fold = cosine < FOLD
    crease = (cosine < CREASE) & (side > 0)     # (b rises above a's plane: concave)
    # crease edges that reach a wall's corner, and those joined to them along the crease
    wall_corners = np.zeros(len(V), bool)
    wall_corners[F[hits > 0].ravel()] = True
    on = np.zeros(len(corners), bool)
    seed = crease & wall_corners[corners].any(1)
    n = len(V)
    keep = np.nonzero(crease)[0]
    if len(keep):
        lab_n, lab = connected_components(coo_matrix((np.ones(len(keep)), (corners[keep, 0], corners[keep, 1])), shape=(n, n)), directed=False)
        reached = np.zeros(lab_n, bool)
        reached[lab[corners[seed, 0]]] = True
        on[keep] = reached[lab[corners[keep, 0]]]
    cut = fold | on
    sew = ~cut
    return connected_components(coo_matrix((np.ones(sew.sum()), (a[sew], b[sew])), shape=(len(F), len(F))), directed=False)[1]


def _closed(V, F):
    """A piece's corners and triangles, closed (its holes are the strips where the two shapes
    overlapped, and the slits zero-area triangles left), or None."""
    import pymeshfix

    used, loc = np.unique(F, return_inverse=True)
    fix = pymeshfix.PyTMesh()
    fix.set_quiet(True)
    fix.load_array(V[used], loc.reshape(-1, 3))
    fix.fill_small_boundaries(nbe=0, refine=False)
    if fix.n_boundaries:
        fix.clean(max_iters=5, inner_loops=2)
    if fix.n_boundaries:
        return None
    P, Q = fix.return_arrays()
    return np.asarray(P, float), np.asarray(Q, np.int64)


def _sheet(M, P, Q):
    """Which triangles of a closed piece are its wall: the smooth sheets that mostly face another
    wall of the mesh M. (A whole sheet, so that what closed the strips where the shapes overlapped
    moves with it.)"""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    A = np.cross(P[Q[:, 1]] - P[Q[:, 0]], P[Q[:, 2]] - P[Q[:, 0]])
    area = np.linalg.norm(A, axis=1) / 2
    normal = A / np.maximum(2 * area[:, None], 1e-15)
    a, b, _ = _edges(Q)
    smooth = np.einsum("ij,ij->i", normal[a], normal[b]) > SHEET
    label = connected_components(coo_matrix((np.ones(smooth.sum()), (a[smooth], b[smooth])), shape=(len(Q), len(Q))),
                                 directed=False)[1]
    centre = P[Q].mean(1)
    facing = np.zeros(len(Q), bool)
    for t in np.nonzero(area > 1e-7)[0].tolist():
        facing[t] = bool(M.ray_cast(centre[t] + 1e-6 * normal[t], centre[t] + GAP * normal[t]))
    share = np.bincount(label, area * facing) / np.maximum(np.bincount(label, area), 1e-15)
    return share[label] > 0.5


def _pushed(P, Q, walled):
    """The piece's corners with its wall triangles (walled) moved DEPTH out along their normals,
    the corners on the wall's rim in the plane of the face that continues from them."""
    A = np.cross(P[Q[:, 1]] - P[Q[:, 0]], P[Q[:, 2]] - P[Q[:, 0]])
    wall, rest = np.zeros((len(P), 3)), np.zeros((len(P), 3))
    for k in range(3):
        np.add.at(wall, Q[walled, k], A[walled])
        np.add.at(rest, Q[~walled, k], A[~walled])
    moved = np.linalg.norm(wall, axis=1) > 0
    n = wall[moved] / np.linalg.norm(wall[moved], axis=1, keepdims=True)
    go = n.copy()
    size = np.linalg.norm(rest[moved], axis=1)
    rim = size > 1e-12
    other = rest[moved][rim] / size[rim, None]
    s = np.einsum("ij,ij->i", n[rim], other)
    sideways = other - s[:, None] * n[rim]
    length = np.linalg.norm(sideways, axis=1, keepdims=True)
    sideways = sideways / np.maximum(length, 1e-9)
    t = -s / np.maximum(np.einsum("ij,ij->i", sideways, other), 1 / FLAT_MAX)
    go[rim] = n[rim] + np.clip(t, -FLAT_MAX, FLAT_MAX)[:, None] * sideways
    out = P.copy()
    out[moved] += DEPTH * go
    return out


def _without_specks(solid):
    """The solid without pieces too small to be anything but the union's scraps."""
    import manifold3d as m3

    parts = solid.decompose()
    largest = max(p.volume() for p in parts)
    kept = [p for p in parts if p.volume() >= SPECK * largest]
    return m3.Manifold.batch_boolean(kept, m3.OpType.Add) if len(kept) > 1 else kept[0]


def seal(pts, tris):
    """(pts, tris, note) with the cracks sealed, or None when the mesh has none worth sealing,
    or sealing it doesn't pass the checks. pts, tris: a closed mesh."""
    try:
        import manifold3d as m3
    except ImportError:
        return None
    V = np.asarray(pts, float)
    F = np.asarray(tris, np.int64)
    M = _manifold(V, F)
    if M.status() != m3.Error.NoError:
        return None
    A = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    area = np.linalg.norm(A, axis=1) / 2
    normal = A / np.maximum(2 * area[:, None], 1e-15)
    hits = _walls(M, V, F, normal, area)
    total = area.sum()
    walled_area = area[hits == 4].sum()
    if walled_area < WORTH * total:
        return None
    volume = M.volume()
    label = _cut(V, F, normal, hits)
    closed = []
    for k in np.unique(label):
        s = label == k
        if area[s].sum() <= 1e-6 * total:
            continue                    # (scraps of zero-area triangles)
        c = _closed(V, F[s])
        if c is None:
            return None
        closed.append(c)
    if len(closed) < 2:
        return None
    pieces = [_manifold(*c) for c in closed]
    if any(p.status() != m3.Error.NoError for p in pieces):
        return None
    held = sum(p.volume() for p in pieces)
    if abs(held - volume) > VOLUME_SLACK * volume:
        return None

    def union(keeper):
        """All pieces united, with every other piece's walls pushed into the keeper's."""
        rows = []
        for i, (P, Q) in enumerate(closed):
            walled = _sheet(M, P, Q) if i != keeper else None
            rows.append(pieces[i] if walled is None or not walled.any() else _manifold(_pushed(P, Q, walled), Q))
        return m3.Manifold.batch_boolean(rows, m3.OpType.Add)

    # (the keeper is the piece that gains the least volume: a piece's wall can go on past the
    # crack and show, and pushing that one thickens the part wherever it shows, as the tube's
    # wall does between the thread's turns: 72 mm^3 on the axle against the 36 the cracks held)
    big = sorted(range(len(closed)), key=lambda i: -pieces[i].surface_area())[:2]
    best = min((union(i) for i in big), key=lambda u: u.volume())
    if best.status() != m3.Error.NoError or best.is_empty():
        return None
    best = _without_specks(best)
    # the checks: the cracks' space gained, the walls lost
    gained = best.volume() - volume
    if not 0 <= gained <= GAP * walled_area:
        return None
    if best.surface_area() > total - 0.5 * walled_area:
        return None
    # (tidying can pinch off a scrap again: the axle's came out as a second "body")
    best = _without_specks(best.simplify(SIMPLIFY))
    out = best.to_mesh64()
    return (np.asarray(out.vert_properties)[:, :3].astype(float), np.asarray(out.tri_verts).astype(np.int64),
            f"hairline cracks sealed where {len(closed)} shapes pressed together were left apart "
            f"({100 * walled_area / total:.0f}% of the mesh's area was walls of them, inside the part)")
