"""Merge bicubic Bezier patches of a quad cage into large B-spline faces.

1. Trace separatrices from every extraordinary vertex (valence != 4) of the
   quad mesh, motorcycle-graph style: a trace runs straight through regular
   vertices and stops at another trace, a singularity, or itself.
2. The traces cut the quads into rectangular blocks. Each block gets grid
   coordinates; its Bezier patches are stitched into one bicubic B-spline
   surface (triple interior knots = exact), then knots are removed where the
   surface is smoother than that (C2 in regular regions), within tolerance.
3. B-rep: block corners are vertices, runs along block sides are edges
   (composite Bezier curves), pcurves are straight lines in block (u, v).
Blocks that don't form a clean grid fall back to one face per quad.
"""
from collections import deque

import numpy as np

from OCP.BRep import BRep_Builder
from OCP.Geom import Geom_BSplineCurve, Geom_BSplineSurface
from OCP.Geom2d import Geom2d_Line
from OCP.gp import gp_Dir2d, gp_Pnt, gp_Pnt2d
from OCP.TopoDS import TopoDS_Edge, TopoDS_Face, TopoDS_Shell, TopoDS_Vertex, TopoDS_Wire
from OCP.collections import Array1_double, Array1_gp_Pnt, Array1_int, Array2_gp_Pnt


DEBUG = None


def _P(p):
    return gp_Pnt(float(p[0]), float(p[1]), float(p[2]))


class QuadTopology:
    def __init__(self, Q, nv):
        self.Q = Q
        nq = len(Q)
        self.nq = nq
        a = Q.reshape(-1)
        b = np.roll(Q, -1, axis=1).reshape(-1)
        self.tail = a
        self.head = b
        key = a.astype(np.int64) * nv + b
        order = np.argsort(key)
        sk = key[order]
        rkey = b.astype(np.int64) * nv + a
        idx = np.searchsorted(sk, rkey)
        idx = np.clip(idx, 0, len(sk) - 1)
        ok = sk[idx] == rkey
        if not ok.all():
            raise ValueError("quad mesh is not closed")
        self.twin = order[idx]
        self.valence = np.bincount(a, minlength=nv)
        # one outgoing half-edge per vertex
        self.out = np.full(nv, -1, dtype=np.int64)
        self.out[a] = np.arange(4 * nq)
        self.ekey = np.minimum(a, b).astype(np.int64) * nv + np.maximum(a, b)

    def rot(self, h):
        """Next outgoing half-edge around the tail vertex."""
        f, k = divmod(int(h), 4)
        prev = 4 * f + (k + 3) % 4
        return int(self.twin[prev])


def trace_layout(qt):
    """Return a boolean mask over half-edges: True where the edge is a cut."""
    nh = 4 * qt.nq
    cut_edge = {}
    traced_v = set()
    ev = np.nonzero(qt.valence != 4)[0]
    for v in ev:
        traced_v.add(int(v))
    for v in ev:
        h0 = int(qt.out[v])
        if h0 < 0:
            continue
        # all outgoing half-edges around v
        outs = [h0]
        h = qt.rot(h0)
        guard = 0
        while h != h0 and guard < 64:
            outs.append(h)
            h = qt.rot(h)
            guard += 1
        for h in outs:
            if cut_edge.get(int(qt.ekey[h])):
                continue
            steps = 0
            while steps < nh:
                cut_edge[int(qt.ekey[h])] = True
                w = int(qt.head[h])
                if w in traced_v:
                    break
                traced_v.add(w)
                # straight continuation at regular vertex w
                back = int(qt.twin[h])  # w -> previous vertex
                e1 = qt.rot(back)
                h = qt.rot(e1)
                steps += 1
    mask = np.array([bool(cut_edge.get(int(k), False)) for k in qt.ekey])
    return mask


def blocks_from_cuts(qt, cut):
    nq = qt.nq
    parent = np.arange(nq)

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for h in range(4 * nq):
        if cut[h]:
            continue
        f, g = h // 4, int(qt.twin[h]) // 4
        rf, rg = find(f), find(g)
        if rf != rg:
            parent[rg] = rf
    roots = np.array([find(f) for f in range(nq)])
    blocks = {}
    for f, r in enumerate(roots):
        blocks.setdefault(int(r), []).append(f)
    return list(blocks.values())


_DELTA = [(0, -1), (1, 0), (0, 1), (-1, 0)]  # local edge l -> neighbour cell offset


def grid_coords(qt, cut, faces):
    """BFS grid placement: {face: (i, j, r)} or None if not a clean grid."""
    member = set(faces)
    f0 = faces[0]
    place = {f0: (0, 0, 0)}
    cells = {(0, 0): f0}
    dq = deque([f0])
    while dq:
        f = dq.popleft()
        i, j, r = place[f]
        for k in range(4):
            h = 4 * f + k
            if cut[h]:
                continue
            t = int(qt.twin[h])
            g, kk = divmod(t, 4)
            if g not in member:
                continue
            l = (k - r) % 4
            lg = (l + 2) % 4
            rg = (kk - lg) % 4
            di, dj = _DELTA[l]
            cg = (i + di, j + dj, rg)
            if g in place:
                if place[g] != cg:
                    return None
                continue
            if (cg[0], cg[1]) in cells:
                return None
            place[g] = cg
            cells[(cg[0], cg[1])] = g
            dq.append(g)
    if len(place) != len(faces):
        return None
    # a block must not touch itself across a cut (it would wrap around,
    # like a band around a cylinder, and need a seam)
    for f in faces:
        for k in range(4):
            h = 4 * f + k
            if cut[h] and int(qt.twin[h]) // 4 in member:
                return None
    I = [p[0] for p in place.values()]
    J = [p[1] for p in place.values()]
    i0, i1, j0, j1 = min(I), max(I), min(J), max(J)
    if (i1 - i0 + 1) * (j1 - j0 + 1) != len(faces):
        return None
    # proper embedding: grid neighbours must be mesh neighbours and vice versa
    for f, (i, j, r) in place.items():
        for l in range(4):
            k = (l + r) % 4
            g = int(qt.twin[4 * f + k]) // 4
            di, dj = _DELTA[l]
            nb_cell = cells.get((i + di, j + dj))
            if nb_cell is not None:
                if nb_cell != g:
                    return None
            elif g in member:
                return None
    return {f: (p[0] - i0, p[1] - j0, p[2]) for f, p in place.items()}, (i1 - i0 + 1, j1 - j0 + 1)


def _net(Bz_f, r):
    """4x4 Bezier net of a quad seen in its cell's orientation."""
    if r == 0:
        return Bz_f
    if r == 1:
        return np.transpose(Bz_f[::-1, :], (1, 0, 2))
    if r == 2:
        return Bz_f[::-1, ::-1]
    return np.transpose(Bz_f[:, ::-1], (1, 0, 2))


def _knots(n):
    k = Array1_double(1, n + 1)
    m = Array1_int(1, n + 1)
    for i in range(n + 1):
        k.SetValue(i + 1, float(i))
        m.SetValue(i + 1, 4 if i in (0, n) else 3)
    return k, m


def simplify_layout(qt, cut, blocks, max_passes=12):
    """Merge neighbouring blocks whose union is still a clean rectangle
    (removes the thin strips motorcycle traces leave between each other)."""
    cut = cut.copy()
    grids = {}
    for bi, faces in enumerate(blocks):
        g = grid_coords(qt, cut, faces) if len(faces) > 1 else ({faces[0]: (0, 0, 0)}, (1, 1))
        grids[bi] = (faces, g)
    alive = {bi for bi, (_, g) in grids.items() if g is not None}
    block_of = {}
    for bi, (faces, _) in grids.items():
        for f in faces:
            block_of[f] = bi
    for _ in range(max_passes):
        merged_any = False
        for a in sorted(alive, key=lambda b: len(grids[b][0])):
            if a not in alive:
                continue
            fa, _ = grids[a]
            nbr = {}
            for f in fa:
                for k in range(4):
                    h = 4 * f + k
                    if not cut[h]:
                        continue
                    g = int(qt.twin[h]) // 4
                    b = block_of[g]
                    if b != a and b in alive:
                        nbr.setdefault(b, []).append(h)
            for b, hs in sorted(nbr.items(), key=lambda t: -len(t[1])):
                shared = []
                for h in hs:
                    shared.append(h)
                    shared.append(int(qt.twin[h]))
                for h in shared:
                    cut[h] = False
                union = list(fa) + list(grids[b][0])
                g = grid_coords(qt, cut, union)
                if g is None:
                    for h in shared:
                        cut[h] = True
                    continue
                grids[a] = (union, g)
                for f in grids[b][0]:
                    block_of[f] = a
                alive.discard(b)
                merged_any = True
                break
        if not merged_any:
            break
    out_blocks = [grids[b][0] for b in sorted(alive)]
    # blocks whose grid failed originally stay as they were (fallback later)
    out_blocks += [grids[b][0] for b, (_, g) in grids.items() if g is None]
    return cut, out_blocks


def build_merged_shell(cage, X, tol, report=None, merge=True):
    """Shell made of large B-spline faces (one per layout block)."""
    Q = cage.Q
    nv = cage.nv
    Bz = (cage.bezier @ X).reshape(cage.nq, 4, 4, 3)
    qt = QuadTopology(Q, nv)
    cut = trace_layout(qt) if merge else np.ones(4 * cage.nq, dtype=bool)
    blocks = blocks_from_cuts(qt, cut)
    if merge:
        cut, blocks = simplify_layout(qt, cut, blocks)
    # grid placement; failed blocks become single-quad blocks
    placed = []
    for faces in blocks:
        res = grid_coords(qt, cut, faces) if len(faces) > 1 else ({faces[0]: (0, 0, 0)}, (1, 1))
        if res is None:
            for f in faces:
                placed.append(({f: (0, 0, 0)}, (1, 1)))
        else:
            placed.append(res)
    # Keep faces a sensible size: very long or large curved B-spline faces
    # are hard for CAD importers, which have to project the boundary curves
    # onto them. Split blocks into chunks of at most MAX_CELLS per side.
    MAX_CELLS = 24
    sized = []
    for pl, (m, n) in placed:
        if m <= MAX_CELLS and n <= MAX_CELLS:
            sized.append((pl, (m, n)))
            continue
        us = np.unique(np.linspace(0, m, int(np.ceil(m / MAX_CELLS)) + 1).round().astype(int))
        vs = np.unique(np.linspace(0, n, int(np.ceil(n / MAX_CELLS)) + 1).round().astype(int))
        for a in range(len(us) - 1):
            for b in range(len(vs) - 1):
                sub = {
                    f: (i - us[a], j - vs[b], r)
                    for f, (i, j, r) in pl.items()
                    if us[a] <= i < us[a + 1] and vs[b] <= j < vs[b + 1]
                }
                if sub:
                    sized.append((sub, (int(us[a + 1] - us[a]), int(vs[b + 1] - vs[b]))))
    placed = sized
    # per-quad block id, so cut status of an edge = blocks differ
    block_of = np.empty(cage.nq, dtype=np.int64)
    for bi, (pl, _) in enumerate(placed):
        for f in pl:
            block_of[f] = bi

    B = BRep_Builder()
    tol = max(tol, 1e-7)
    ktol = max(0.05 * tol, 1e-7)
    corners = cage.Corner @ X
    vert = {}

    def vertex(v):
        tv = vert.get(v)
        if tv is None:
            tv = TopoDS_Vertex()
            B.MakeVertex(tv, _P(corners[v]), tol)
            vert[v] = tv
        return tv

    # mesh vertex at each block cell corner
    def corner_vertex(pl_inv, i, j):
        """mesh vertex at grid point (i, j) of a block."""
        for (ci, cj, corner_local) in ((i, j, 0), (i - 1, j, 1), (i - 1, j - 1, 2), (i, j - 1, 3)):
            f = pl_inv.get((ci, cj))
            if f is not None:
                fi, fj, r = f[1], f[2], f[3]
                return int(Q[f[0], (r + corner_local) % 4])
        raise KeyError((i, j))

    # every block corner (and every singular vertex) is a B-rep vertex for
    # all blocks touching it, so neighbouring blocks split shared sides at
    # the same points
    invs = []
    corner_set = set(int(v) for v in np.nonzero(qt.valence != 4)[0])
    for pl, (m, n) in placed:
        inv = {}
        for f, (i, j, r) in pl.items():
            inv[(i, j)] = (f, i, j, r)
        invs.append(inv)
        for gp in ((0, 0), (m, 0), (m, n), (0, n)):
            corner_set.add(corner_vertex(inv, *gp))

    edges = {}
    faces_out = []
    for bi, (pl, (m, n)) in enumerate(placed):
        inv = invs[bi]
        # control grid
        P = np.zeros((3 * m + 1, 3 * n + 1, 3))
        for f, (i, j, r) in pl.items():
            P[3 * i:3 * i + 4, 3 * j:3 * j + 4] = _net(Bz[f], r)
        poles = Array2_gp_Pnt(1, 3 * m + 1, 1, 3 * n + 1)
        for a in range(3 * m + 1):
            for b in range(3 * n + 1):
                poles.SetValue(a + 1, b + 1, _P(P[a, b]))
        uk, um = _knots(m)
        vk, vm = _knots(n)
        surf = Geom_BSplineSurface(poles, uk, vk, um, vm, 3, 3)
        if merge:
            for kidx in range(2, m + 1):
                for target in (1, 2):
                    if surf.RemoveUKnot(kidx, target, ktol):
                        break
            for kidx in range(2, n + 1):
                for target in (1, 2):
                    if surf.RemoveVKnot(kidx, target, ktol):
                        break
        face = TopoDS_Face()
        B.MakeFace(face, surf, tol)
        # B-rep vertices on this block's boundary: grid points where the
        # neighbouring block changes (or block corners)
        def neighbour_block(i, j, side):
            """block across the side of cell (i, j)."""
            f, fi, fj, r = inv[(i, j)]
            k = (side + r) % 4
            return int(block_of[int(qt.twin[4 * f + k]) // 4])

        boundary = []  # list of (grid point, side, cell) walking CCW
        for i in range(m):
            boundary.append(((i, 0), 0, (i, 0)))
        for j in range(n):
            boundary.append(((m, j), 1, (m - 1, j)))
        for i in range(m, 0, -1):
            boundary.append(((i, n), 2, (i - 1, n - 1)))
        for j in range(n, 0, -1):
            boundary.append(((0, j), 3, (0, j - 1)))
        nbrs = [neighbour_block(c[0], c[1], side) for (_, side, c) in boundary]
        L = len(boundary)
        is_v = []
        for idx in range(L):
            gp, side, cell = boundary[idx]
            prev_side = boundary[idx - 1][1]
            is_v.append(
                side != prev_side
                or nbrs[idx] != nbrs[idx - 1]
                or corner_vertex(inv, *gp) in corner_set
            )
        starts = [i for i in range(L) if is_v[i]]
        w = TopoDS_Wire()
        B.MakeWire(w)
        for si, s0 in enumerate(starts):
            s1 = starts[(si + 1) % len(starts)]
            run = list(range(s0, s1 if s1 > s0 else s1 + L))
            run = [x % L for x in run]
            gp0 = boundary[run[0]][0]
            side = boundary[run[0]][1]
            # end grid point of the run
            last = boundary[run[-1]]
            dirs = [(1, 0), (0, 1), (-1, 0), (0, -1)]
            d = dirs[side]
            gp1 = (last[0][0] + d[0], last[0][1] + d[1])
            v0 = corner_vertex(inv, *gp0)
            v1 = corner_vertex(inv, *gp1)
            # first quad edge of the run identifies the B-rep edge
            cell0 = boundary[run[0]][2]
            f0, _, _, r0 = inv[cell0]
            k0 = (side + r0) % 4
            hid = 4 * f0 + k0
            key = (min(hid, int(qt.twin[hid])),)
            # also key by the last quad edge to make it direction independent
            cellL = boundary[run[-1]][2]
            fL, _, _, rL = inv[cellL]
            hL = 4 * fL + (side + rL) % 4
            key = tuple(sorted((min(hid, int(qt.twin[hid])), min(hL, int(qt.twin[hL])))))
            nseg = len(run)
            if DEBUG is not None:
                qes = []
                for x in run:
                    _, sd, cell = boundary[x]
                    ff, _, _, rr = inv[cell]
                    hh = 4 * ff + (sd + rr) % 4
                    qes.append(min(hh, int(qt.twin[hh])))
                DEBUG.append({"block": bi, "side": side, "key": key, "qe": qes, "v0": v0, "v1": v1, "gp0": gp0, "gp1": gp1, "mn": (m, n)})
            e = edges.get(key)
            if e is None:
                # composite Bezier along the side, in walking direction
                pts = []
                for x in run:
                    gp, sd, cell = boundary[x]
                    ci, cj = cell
                    if sd == 0:
                        seg = P[3 * ci:3 * ci + 4, 0]
                    elif sd == 1:
                        seg = P[3 * m, 3 * cj:3 * cj + 4]
                    elif sd == 2:
                        seg = P[3 * ci:3 * ci + 4, 3 * n][::-1]
                    else:
                        seg = P[0, 3 * cj:3 * cj + 4][::-1]
                    pts.append(seg if not pts else seg[1:])
                pts = np.vstack(pts)
                arr = Array1_gp_Pnt(1, len(pts))
                for q, p in enumerate(pts):
                    arr.SetValue(q + 1, _P(p))
                ck = Array1_double(1, nseg + 1)
                cm = Array1_int(1, nseg + 1)
                for q in range(nseg + 1):
                    ck.SetValue(q + 1, float(q))
                    cm.SetValue(q + 1, 4 if q in (0, nseg) else 3)
                crv = Geom_BSplineCurve(arr, ck, cm, 3)
                if merge:
                    for q in range(2, nseg + 1):
                        for target in (1, 2):
                            if crv.RemoveKnot(q, target, ktol):
                                break
                e = TopoDS_Edge()
                B.MakeEdge(e, crv, tol)
                B.Add(e, vertex(v0))
                B.Add(e, vertex(v1).Reversed())
                B.Range(e, 0.0, float(nseg))
                edges[key] = (e, v0, v1)
                fwd = True
            else:
                e, ev0, ev1 = e
                fwd = ev0 == v0 and ev1 == v1
                if not fwd and not (ev0 == v1 and ev1 == v0):
                    # closed edge with same end vertex: decide by geometry
                    fwd = False
            # pcurve: straight line in this block's (u, v)
            if fwd:
                start, ddir = gp0, d
            else:
                start = gp1
                ddir = (-d[0], -d[1])
            line = Geom2d_Line(gp_Pnt2d(float(start[0]), float(start[1])), gp_Dir2d(float(ddir[0]), float(ddir[1])))
            B.UpdateEdge(e, line, face, tol)
            B.Range(e, face, 0.0, float(nseg))
            B.Add(w, e if fwd else e.Reversed())
        w.Closed(True)
        B.Add(face, w)
        faces_out.append(face)
    for e, _, _ in edges.values():
        B.SameParameter(e, True)
        B.SameRange(e, True)
    sh = TopoDS_Shell()
    B.MakeShell(sh)
    for f in faces_out:
        B.Add(sh, f)
    sh.Closed(True)
    return sh, len(faces_out)
