"""Mesh loading, cleaning and repair."""
import os
import re
import struct

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .report import NULL


class MeshError(Exception):
    pass


# ----------------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------------

_BIN_DTYPE = np.dtype([("n", "<f4", (3,)), ("v", "<f4", (3, 3)), ("attr", "<u2")])
_ASCII_VERTEX = re.compile(
    rb"vertex\s+([-+0-9.eEnNaAiIfF]+)\s+([-+0-9.eEnNaAiIfF]+)\s+([-+0-9.eEnNaAiIfF]+)"
)


def read_stl(path):
    """Return an (n, 3, 3) float64 array of triangle corners."""
    with open(path, "rb") as fh:
        data = fh.read()
    size = len(data)
    if size < 15:
        raise MeshError("This file is empty or too small to be an STL.")

    binary_count = struct.unpack("<I", data[80:84])[0] if size >= 84 else -1
    exact_binary = size >= 84 and 84 + binary_count * 50 == size
    head = data[:1024].lstrip().lower()
    looks_ascii = head.startswith(b"solid") and (b"facet" in head or b"endsolid" in head)

    if exact_binary or not looks_ascii:
        if size < 84:
            raise MeshError("This doesn't look like a valid STL file (it may be damaged or incomplete).")
        count = binary_count
        avail = (size - 84) // 50
        if count > avail or count == 0:
            # Header count is wrong (common with some exporters); trust the size.
            count = avail
        if count <= 0:
            if looks_ascii:
                return _read_ascii(data)
            raise MeshError("This STL file contains no triangles.")
        arr = np.frombuffer(data, dtype=_BIN_DTYPE, count=count, offset=84)
        tris = arr["v"].astype(np.float64)
    else:
        tris = _read_ascii(data)
    return tris


def _read_ascii(data):
    vals = _ASCII_VERTEX.findall(data)
    if len(vals) < 3:
        raise MeshError("This text STL file contains no triangles (it may be damaged).")
    n = (len(vals) // 3) * 3
    pts = np.array(vals[:n], dtype=np.float64)
    return pts.reshape(-1, 3, 3)


def read_parts(path, report=NULL, layout="parts"):
    """Return [(name, triangles)] — one entry per part in the file."""
    ext = os.path.splitext(path)[1].lower()
    stem = os.path.splitext(os.path.basename(path))[0]
    if ext == ".3mf":
        from .threemf import ThreeMFError, read_3mf

        try:
            parts = read_3mf(path, layout=layout, report=report)
        except ThreeMFError as e:
            raise MeshError(str(e))
        return [(p.name, p.tris) for p in parts]
    return [(stem, read_any(path))]


def read_any(path):
    """Read STL (own fast reader) or other mesh formats through trimesh."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".stl":
        return read_stl(path)
    import trimesh

    m = trimesh.load(path, force="mesh", process=False)
    if m is None or len(m.faces) == 0:
        raise MeshError("This file contains no triangles.")
    return np.asarray(m.vertices, dtype=np.float64)[np.asarray(m.faces)]


# ----------------------------------------------------------------------------
# Indexed mesh helpers
# ----------------------------------------------------------------------------


def _union_find_merge(pts, tol):
    """Merge points closer than tol. Returns (unique_points, inverse_index)."""
    # Exact merge first (cheap and handles the vast majority).
    uniq, inv = np.unique(pts, axis=0, return_inverse=True)
    inv = inv.reshape(-1)
    if tol > 0 and len(uniq) > 1:
        tree = cKDTree(uniq)
        pairs = tree.query_pairs(tol, output_type="ndarray")
        if len(pairs):
            n = len(uniq)
            g = sparse.coo_matrix(
                (np.ones(len(pairs), dtype=np.int8), (pairs[:, 0], pairs[:, 1])), shape=(n, n)
            )
            _, lab = connected_components(g, directed=False)
            # Representative = mean of the cluster.
            cnt = np.bincount(lab)
            rep = np.zeros((lab.max() + 1, 3))
            np.add.at(rep, lab, uniq)
            rep /= cnt[:, None]
            return rep, lab[inv]
    return uniq, inv


def triangles_to_indexed(tris, merge_tol_rel=1e-7):
    tris = np.asarray(tris, dtype=np.float64)
    good = np.isfinite(tris).all(axis=(1, 2))
    tris = tris[good]
    if len(tris) == 0:
        raise MeshError("This file's triangles have broken coordinates, so it can't be read. Try exporting it again.")
    pts = tris.reshape(-1, 3)
    lo, hi = pts.min(0), pts.max(0)
    diag = float(np.linalg.norm(hi - lo))
    if diag <= 0:
        raise MeshError("All of this mesh's points are in the same spot, so there is no shape to convert.")
    scale = float(np.abs(pts).max())
    tol = max(merge_tol_rel * diag, 8 * np.finfo(np.float32).eps * scale)
    V, inv = _union_find_merge(pts, tol)
    F = inv.reshape(-1, 3).astype(np.int64)
    return V, F


def remove_degenerate(V, F, stats=None):
    """Drop faces with repeated vertices and exact duplicate faces. stats
    (a dict) gets the counts: squashed, duplicates, back_to_back (pairs)."""
    ok = (F[:, 0] != F[:, 1]) & (F[:, 1] != F[:, 2]) & (F[:, 0] != F[:, 2])
    if stats is not None:
        stats["squashed"] = int((~ok).sum())
        stats["duplicates"] = stats["back_to_back"] = 0
    F = F[ok]
    # Duplicate faces (same vertex set). Same orientation -> keep one.
    # Opposite orientation -> internal double wall, drop both.
    key = np.sort(F, axis=1)
    _, first, inv, cnt = np.unique(key, axis=0, return_index=True, return_inverse=True, return_counts=True)
    inv = inv.reshape(-1)
    if (cnt > 1).any():
        keep = np.zeros(len(F), dtype=bool)
        keep[first] = True
        dup_groups = np.nonzero(cnt > 1)[0]
        for g in dup_groups:
            idx = np.nonzero(inv == g)[0]
            # Orientation sign by parity of the permutation relative to sorted.
            signs = [_perm_parity(F[i]) for i in idx]
            pos = [i for i, s in zip(idx, signs) if s > 0]
            neg = [i for i, s in zip(idx, signs) if s < 0]
            keep[idx] = False
            n_pairs = min(len(pos), len(neg))
            rest = pos[n_pairs:] + neg[n_pairs:]
            if rest:
                keep[rest[0]] = True
            if stats is not None:
                stats["back_to_back"] += n_pairs
                stats["duplicates"] += max(0, len(rest) - 1)
        F = F[keep]
    return V, F


def _perm_parity(tri):
    a, b, c = tri
    # cyclic rotations of increasing order have parity +1
    if (a < b < c) or (b < c < a) or (c < a < b):
        return 1
    return -1


def compact(V, F):
    used = np.unique(F)
    remap = -np.ones(len(V), dtype=np.int64)
    remap[used] = np.arange(len(used))
    return V[used], remap[F]


class EdgeTopology:
    """Half-edge style adjacency for a triangle mesh.

    Half-edge h = 3*f + i goes from F[f, i] to F[f, (i+1) % 3].
    twin[h] is the opposite half-edge or -1 (boundary / non-manifold).
    """

    def __init__(self, V, F):
        self.V = V
        self.F = F
        nf = len(F)
        nv = len(V)
        a = F.reshape(-1)
        b = np.roll(F, -1, axis=1).reshape(-1)
        self.he_from = a
        self.he_to = b
        lo = np.minimum(a, b)
        hi = np.maximum(a, b)
        ukey = lo * nv + hi
        order = np.argsort(ukey, kind="stable")
        sk = ukey[order]
        # group sizes for undirected edges
        starts = np.r_[0, np.nonzero(np.diff(sk))[0] + 1]
        counts = np.diff(np.r_[starts, len(sk)])
        self.edge_count = np.empty(len(ukey), dtype=np.int64)
        self.edge_count[order] = np.repeat(counts, counts)
        twin = -np.ones(3 * nf, dtype=np.int64)
        two = counts == 2
        s2 = starts[two]
        h1 = order[s2]
        h2 = order[s2 + 1]
        # only pair if opposite directions (consistent orientation);
        # same-direction pairs are still adjacency but flagged.
        twin[h1] = h2
        twin[h2] = h1
        self.twin = twin
        self.same_dir = np.zeros(3 * nf, dtype=bool)
        sd = a[h1] == a[h2]
        self.same_dir[h1[sd]] = True
        self.same_dir[h2[sd]] = True
        self.nonmanifold_he = self.edge_count > 2
        self.boundary_he = self.edge_count == 1

    @property
    def n_boundary_edges(self):
        return int(self.boundary_he.sum())

    @property
    def n_nonmanifold_edges(self):
        return int(self.nonmanifold_he.sum())

    def face_adjacency(self):
        """(k, 2) array of face pairs sharing a manifold edge."""
        h = np.nonzero(self.twin >= 0)[0]
        t = self.twin[h]
        m = h < t
        return np.stack([h[m] // 3, t[m] // 3], axis=1)


def face_components(F, topo):
    adj = topo.face_adjacency()
    n = len(F)
    if len(adj) == 0:
        return n, np.arange(n)
    g = sparse.coo_matrix((np.ones(len(adj), dtype=np.int8), (adj[:, 0], adj[:, 1])), shape=(n, n))
    return connected_components(g, directed=False)


def fix_winding(V, F, topo=None, count_flips=False):
    """Make face orientation consistent within each connected component.
    Returns (F, conflicts), plus the number of turned faces with
    count_flips (each piece keeps the direction most of its faces had, so
    that is the number of faces that faced the wrong way)."""
    if topo is None:
        topo = EdgeTopology(V, F)
    nf = len(F)
    if not topo.same_dir.any():
        return (F, 0, 0) if count_flips else (F, 0)
    from collections import deque

    F = F.copy()
    flipped = np.zeros(nf, dtype=bool)
    visited = np.zeros(nf, dtype=bool)
    twin = topo.twin
    same = topo.same_dir.copy()
    conflicts = 0
    for seed in range(nf):
        if visited[seed]:
            continue
        visited[seed] = True
        dq = deque([seed])
        while dq:
            f = dq.popleft()
            for i in range(3):
                h = 3 * f + i
                t = twin[h]
                if t < 0:
                    continue
                g = t // 3
                # inconsistent if the half-edges point the same way, accounting
                # for flips already applied.
                inconsistent = same[h] != (flipped[f] != flipped[g])
                if not visited[g]:
                    visited[g] = True
                    if same[h]:
                        flipped[g] = not flipped[f]
                    else:
                        flipped[g] = flipped[f]
                    dq.append(g)
                elif inconsistent:
                    conflicts += 1
    if count_flips:
        # keep the majority direction of every piece
        n, lab = face_components(F, topo)
        tot = np.bincount(lab, minlength=n)
        fl = np.bincount(lab, weights=flipped.astype(np.float64), minlength=n)
        invert = fl > tot / 2
        flipped ^= invert[lab]
    F[flipped] = F[flipped][:, ::-1]
    if count_flips:
        return F, conflicts // 2, int(flipped.sum())
    return F, conflicts // 2


def signed_volume(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def face_normals_areas(V, F):
    a, b, c = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    cr = np.cross(b - a, c - a)
    ar = np.linalg.norm(cr, axis=1)
    n = np.zeros_like(cr)
    ok = ar > 0
    n[ok] = cr[ok] / ar[ok, None]
    return n, ar * 0.5


def winding_number(V, F, pts):
    """Generalized winding number of points w.r.t. a triangle mesh."""
    pts = np.atleast_2d(pts)
    out = np.zeros(len(pts))
    A0, B0, C0 = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    for k, p in enumerate(pts):
        a = A0 - p
        b = B0 - p
        c = C0 - p
        la = np.linalg.norm(a, axis=1)
        lb = np.linalg.norm(b, axis=1)
        lc = np.linalg.norm(c, axis=1)
        det = np.einsum("ij,ij->i", a, np.cross(b, c))
        den = (
            la * lb * lc
            + np.einsum("ij,ij->i", a, b) * lc
            + np.einsum("ij,ij->i", b, c) * la
            + np.einsum("ij,ij->i", c, a) * lb
        )
        out[k] = np.arctan2(det, den).sum() / (2 * np.pi)
    return out


# ----------------------------------------------------------------------------
# Body / shell organisation
# ----------------------------------------------------------------------------


class Shell:
    def __init__(self, V, F):
        self.V = V
        self.F = F
        self.topo = EdgeTopology(V, F)
        self.closed = self.topo.n_boundary_edges == 0 and self.topo.n_nonmanifold_edges == 0
        self.volume = signed_volume(V, F) if self.closed else 0.0
        self.depth = 0
        self.parent = None
        # self-intersections counted by the mesh check (None: not counted);
        # turning the shell inside out doesn't change it
        self.crossings = None

    @property
    def n_faces(self):
        return len(self.F)


def split_shells(V, F):
    topo = EdgeTopology(V, F)
    n, lab = face_components(F, topo)
    shells = []
    for c in range(n):
        Fc = F[lab == c]
        Vc, Fc = compact(V, Fc)
        shells.append(Shell(Vc, Fc))
    return shells


def organise_bodies(shells, report=NULL, stats=None):
    """Orient shells and group them into bodies (outer shell + cavities).

    A shell inside another one is a *cavity* only when the file orients it
    the opposite way (inward-facing, like the inside of a hollow ball).
    Shells nested the same way round are separate parts that happen to sit
    inside each other — common in sculpts (eyes inside a head) and in
    multi-part models — and become bodies of their own.
    """
    closed = [s for s in shells if s.closed]
    opened = [s for s in shells if not s.closed]
    boxes = [(s.V.min(0), s.V.max(0)) for s in closed]
    order = sorted(range(len(closed)), key=lambda i: -abs(closed[i].volume))
    rng = np.random.default_rng(0)
    parent = {}
    coarse = {}

    def coarse_mesh(j):
        """Decimated copy for fast inside/outside tests on big shells."""
        if j not in coarse:
            sj = closed[j]
            V, F = sj.V, sj.F
            if len(F) > 40000:
                try:
                    import fast_simplification

                    v2, f2 = fast_simplification.simplify(
                        np.ascontiguousarray(V, dtype=np.float32),
                        np.ascontiguousarray(F, dtype=np.int32),
                        target_reduction=float(1 - 20000 / len(F)),
                    )
                    V, F = np.asarray(v2, dtype=np.float64), np.asarray(f2, dtype=np.int64)
                except Exception:
                    pass
            coarse[j] = (V, F)
        return coarse[j]
    for i in order:
        si = closed[i]
        lo_i, hi_i = boxes[i]
        k = min(len(si.V), 5)
        sample = si.V[rng.choice(len(si.V), k, replace=False)] if len(si.V) > 5 else si.V
        best, best_vol = None, np.inf
        for j in order:
            if j == i:
                continue
            sj = closed[j]
            if abs(sj.volume) <= abs(si.volume):
                continue
            lo_j, hi_j = boxes[j]
            if (lo_i < lo_j).any() or (hi_i > hi_j).any():
                continue
            if abs(sj.volume) >= best_vol:
                continue
            cV, cF = coarse_mesh(j)
            w = winding_number(cV, cF, sample)
            if (np.abs(np.abs(w) - 1) < 0.5).mean() > 0.5:
                best, best_vol = j, abs(sj.volume)
        parent[i] = best  # smallest shell that contains i
    # cavity iff opposite orientation to the container (as stored in the file)
    is_cavity = {}
    for i in order:
        p = parent[i]
        is_cavity[i] = p is not None and not is_cavity.get(p, False) and (closed[i].volume > 0) != (closed[p].volume > 0)
    # normalise orientation: solids outward, cavities inward
    for i, s in enumerate(closed):
        want_positive = not is_cavity[i]
        if (s.volume > 0) != want_positive:
            if stats is not None and want_positive:
                stats["inside_out"] = stats.get("inside_out", 0) + 1
            s.F = s.F[:, ::-1].copy()
            s.volume = -s.volume
            s.topo = EdgeTopology(s.V, s.F)
    bodies = []
    for i, s in enumerate(closed):
        if is_cavity[i]:
            continue
        body = [s]
        for j, c in enumerate(closed):
            if is_cavity[j] and parent[j] == i:
                body.append(c)
        bodies.append(body)
    n_nested = sum(1 for i in parent if parent[i] is not None and not is_cavity[i])
    if n_nested:
        report.log(f"{n_nested} part(s) sit inside other parts; they are kept as separate solids")
    for s in opened:
        bodies.append([s])
    return bodies


# ----------------------------------------------------------------------------
# Repair
# ----------------------------------------------------------------------------


def repair_shell_light(shell, report=NULL):
    """Fast repair: split non-manifold edges/vertices and close small holes
    (MeshFix's loader + hole filler, without its expensive self-intersection
    cleanup). Seconds instead of minutes on multi-million triangle sculpts."""
    import pymeshfix

    tm = pymeshfix.PyTMesh()
    try:
        tm.set_quiet(True)
    except Exception:
        pass
    tm.load_array(shell.V.astype(np.float64), shell.F.astype(np.int32))
    tm.fill_small_boundaries(nbe=0, refine=True)
    v, f = tm.return_arrays()
    V2 = np.asarray(v, dtype=np.float64)
    F2 = np.asarray(f, dtype=np.int64).reshape(-1, 3)
    if len(F2) < 0.5 * len(shell.F):
        return shell
    return Shell(V2, F2)


def repair_shell_meshfix(shell, report=NULL):
    """Close holes / remove non-manifold geometry with MeshFix."""
    import pymeshfix

    mf = pymeshfix.MeshFix(shell.V.astype(np.float64), shell.F.astype(np.int32))
    try:
        mf.repair(joincomp=False, remove_smallest_components=False)
    except TypeError:
        mf.repair()
    V2 = np.asarray(mf.points, dtype=np.float64)
    F2 = np.asarray(mf.faces, dtype=np.int64).reshape(-1, 3)
    if len(F2) < 0.5 * len(shell.F):
        report.warn("Mesh repair would have removed too much of the shape, so the original mesh was kept.")
        return shell
    return Shell(V2, F2)


def fill_small_holes(shell, max_edges=20000):
    """Fill simple boundary loops with a fan around their centroid.

    Fast and good enough for typical holes (an uncapped flat base, a missing
    patch); the surfaces fitted afterwards smooth over the fill. Pinched or
    tangled boundaries are left for MeshFix."""
    topo = shell.topo
    if topo.n_nonmanifold_edges:
        return shell
    bh = np.nonzero(topo.boundary_he)[0]
    if len(bh) == 0:
        return shell
    nxt = {}
    for h in bh:
        a, b = int(topo.he_from[h]), int(topo.he_to[h])
        if a in nxt:
            return shell  # pinched boundary; let MeshFix deal with it
        nxt[a] = b
    V = shell.V
    newV = [V]
    newF = [shell.F]
    seen = set()
    nv = len(V)
    for start in list(nxt.keys()):
        if start in seen:
            continue
        loop = [start]
        seen.add(start)
        cur = nxt[start]
        ok = True
        while cur != start:
            if cur in seen or cur not in nxt or len(loop) > max_edges:
                ok = False
                break
            loop.append(cur)
            seen.add(cur)
            cur = nxt[cur]
        if not ok or len(loop) < 3:
            continue
        # Boundary half-edges run a->b; the hole fill must run b->a.
        loop = loop[::-1]
        if len(loop) == 3:
            newF.append(np.array([loop], dtype=np.int64))
            continue
        c = V[loop].mean(0)
        ci = nv
        nv += 1
        newV.append(c[None])
        tris = [[loop[k], loop[(k + 1) % len(loop)], ci] for k in range(len(loop))]
        newF.append(np.array(tris, dtype=np.int64))
    if len(newF) == 1:
        return shell
    return Shell(np.vstack(newV), np.vstack(newF))


# ----------------------------------------------------------------------------
# Top level
# ----------------------------------------------------------------------------


class MeshInfo:
    def __init__(self):
        self.input_triangles = 0
        self.vertices = 0
        self.triangles = 0
        self.shells = 0
        self.open_shells = 0
        self.bodies = 0
        self.removed_debris = 0
        self.repaired = 0
        self.diag = 0.0
        self.bbox = None
        self.parts = 1
        self.body_names = []
        # 3MF projects: one entry per slicer part, and the part of each body
        self.part_names = []
        self.part_triangles = []
        self.part_bbox = []
        self.body_part = []
        # the mesh check report (meshcheck.summarize)
        self.mesh_check = None

    def as_dict(self):
        d = dict(self.__dict__)
        if self.bbox is not None:
            d["bbox"] = [list(map(float, self.bbox[0])), list(map(float, self.bbox[1]))]
        return d


def load_and_prepare(path, repair="auto", remove_debris=True, report=NULL, layout="parts", merge=False):
    """Read a mesh file and return (bodies, info).

    Every part in the file (3MF projects can hold several) is cleaned and
    repaired on its own, so touching parts never get fused together.
    ``info.body_names`` holds a name for every body.
    repair: "off" | "auto" | "aggressive"
    merge: join pieces of a part that overlap each other into one solid.
    ``info.mesh_check`` is the check report (see meshcheck.summarize).
    """
    from .meshcheck import summarize, verify_bodies as verify_part

    parts = read_parts(path, report, layout)
    info = MeshInfo()
    bodies = []
    names = []
    all_issues, unready = [], []
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for pidx, (name, tris) in enumerate(parts):
        b, pi, issues = _prepare_part(tris, repair, remove_debris, report, merge)
        if len(parts) > 1:
            for it in issues:
                it["part"] = name
        all_issues.extend(issues)
        ready, left = verify_part(b, pi.diag, merge)
        if not ready:
            unready.extend(f"{name}: {p}" if len(parts) > 1 else p for p in left)
        info.input_triangles += pi.input_triangles
        info.removed_debris += pi.removed_debris
        info.repaired += pi.repaired
        lo = np.minimum(lo, pi.bbox[0])
        hi = np.maximum(hi, pi.bbox[1])
        info.part_names.append(name)
        info.part_triangles.append(int(sum(len(s.F) for body in b for s in body)))
        info.part_bbox.append([list(map(float, pi.bbox[0])), list(map(float, pi.bbox[1]))])
        for k, body in enumerate(b):
            bodies.append(body)
            names.append(name if len(b) == 1 else f"{name} {k + 1}")
            info.body_part.append(pidx)
    info.bbox = (lo, hi)
    info.diag = float(np.linalg.norm(hi - lo))
    info.parts = len(parts)
    info.mesh_check = summarize(all_issues, {"ready": not unready, "problems": unready})
    info.body_names = names
    info.shells = sum(len(b) for b in bodies)
    info.open_shells = sum(1 for b in bodies for s in b if not s.closed)
    info.bodies = len(bodies)
    info.vertices = int(sum(len(s.V) for b in bodies for s in b))
    info.triangles = int(sum(len(s.F) for b in bodies for s in b))
    report.log(
        f"{info.bodies} bod{'y' if info.bodies == 1 else 'ies'}, {info.triangles:,} triangles after cleanup"
        + (f" ({info.open_shells} with holes)" if info.open_shells else "")
    )
    return bodies, info


def _prepare_part(tris, repair, remove_debris, report, merge=False):
    """Check and fix one part (see meshcheck.prepare_part)."""
    from .meshcheck import prepare_part

    bodies, info, issues = prepare_part(tris, repair, remove_debris, report, merge)
    return bodies, info, issues


# ----------------------------------------------------------------------------
# Writing (Save fixed mesh)
# ----------------------------------------------------------------------------


def _body_mesh(bodies):
    Vs, Fs, off = [], [], 0
    for b in bodies:
        for s in b:
            Vs.append(s.V)
            Fs.append(s.F + off)
            off += len(s.V)
    if not Vs:
        return np.zeros((0, 3)), np.zeros((0, 3), np.int64)
    return np.vstack(Vs), np.vstack(Fs)


def write_stl(path, bodies, name="Manifold Mesh Mix"):
    """Binary STL of all bodies."""
    V, F = _body_mesh(bodies)
    T = V[F].astype(np.float32)
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ln[ln == 0] = 1
    rec = np.zeros(len(F), dtype=_BIN_DTYPE)
    rec["n"] = n / ln[:, None]
    rec["v"] = T
    head = f"Fixed by {name}".encode("ascii", "replace")[:80].ljust(80, b" ")
    tmp = path + ".part"
    with open(tmp, "wb") as fh:
        fh.write(head)
        fh.write(struct.pack("<I", len(F)))
        fh.write(rec.tobytes())
    os.replace(tmp, path)


def write_3mf(path, parts):
    """3MF with one object per part: parts = [(name, bodies)]."""
    import zipfile
    from xml.sax.saxutils import quoteattr

    objs, items = [], []
    for k, (name, bodies) in enumerate(parts, start=1):
        V, F = _body_mesh(bodies)
        vs = "".join(f'<vertex x="{x:.9g}" y="{y:.9g}" z="{z:.9g}"/>' for x, y, z in V)
        ts = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in F)
        objs.append(f'<object id="{k}" name={quoteattr(str(name or f"Part {k}"))} type="model"><mesh>'
                    f"<vertices>{vs}</vertices><triangles>{ts}</triangles></mesh></object>")
        items.append(f'<item objectid="{k}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<model unit="millimeter" xml:lang="en-US" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             '<metadata name="Application">Manifold Mesh Mix</metadata>'
             f"<resources>{''.join(objs)}</resources><build>{''.join(items)}</build></model>")
    types = ('<?xml version="1.0" encoding="UTF-8"?>\n'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>'
             "</Types>")
    rels = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    tmp = path + ".part"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)
    os.replace(tmp, path)

