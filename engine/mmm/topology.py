"""Region boundary topology.

Given a triangle mesh and a region label per face, compute for every region
its boundary loops, broken into *chains*: maximal runs of mesh edges that
separate the same two regions. Chains become B-rep edges, regions become
B-rep faces and chain end points ("corners") become B-rep vertices.
"""
import numpy as np


class Chain:
    __slots__ = ("id", "verts", "left", "right", "closed", "geom", "edges", "key")

    def __init__(self, cid, verts, left, right, key):
        self.id = cid
        self.verts = verts  # list of mesh vertex indices, start..end
        self.left = left  # region that traverses the chain forward
        self.right = right  # region on the other side (-1 = open boundary)
        self.closed = verts[0] == verts[-1]
        self.geom = None  # filled by the B-rep builder
        self.edges = None
        self.key = key


class RegionGraph:
    def __init__(self, V, F, labels, topo):
        self.V = V
        self.F = F
        self.labels = np.asarray(labels, dtype=np.int64)
        self.topo = topo
        self.n_regions = int(self.labels.max()) + 1 if len(self.labels) else 0
        nv = len(V)
        twin = topo.twin
        he_label = np.repeat(self.labels, 3)
        twin_label = np.where(twin >= 0, self.labels[np.maximum(twin, 0) // 3], -1)
        self.he_label = he_label
        self.twin_label = twin_label
        self.is_bnd = he_label != twin_label

        # --- corner detection -------------------------------------------
        fv = F.reshape(-1)
        pair = np.unique(fv * (self.n_regions + 1) + he_label)
        pv = pair // (self.n_regions + 1)
        distinct = np.bincount(pv, minlength=nv)
        on_open = np.zeros(nv, dtype=bool)
        on_open[topo.he_from[twin < 0]] = True
        on_open[topo.he_to[twin < 0]] = True
        distinct = distinct + on_open.astype(np.int64)
        n_out = np.bincount(topo.he_from[self.is_bnd], minlength=nv)
        self.corner = (distinct >= 3) | (n_out > 2)

        self._build_loops()

    # ------------------------------------------------------------------
    def _next_boundary(self, h):
        """Next boundary half-edge of the same region after h (rotating
        around h's end vertex inside the region)."""
        twin = self.topo.twin
        is_bnd = self.is_bnd
        f = h // 3
        g = 3 * f + (h - 3 * f + 1) % 3
        guard = 0
        while not is_bnd[g]:
            t = twin[g]
            f2 = t // 3
            g = 3 * f2 + (t - 3 * f2 + 1) % 3
            guard += 1
            if guard > 10000:
                raise RuntimeError("boundary walk did not terminate")
        return g

    def _build_loops(self):
        topo = self.topo
        nv = len(self.V)
        bnd = np.nonzero(self.is_bnd)[0]
        visited = np.zeros(len(self.is_bnd), dtype=bool)
        he_from = topo.he_from
        he_to = topo.he_to
        lab = self.he_label
        tlab = self.twin_label
        corner = self.corner

        self.chains = []
        chain_by_key = {}
        self.region_loops = [[] for _ in range(self.n_regions)]

        def ukey(a, b):
            return (a * nv + b) if a < b else (b * nv + a)

        for h0 in bnd:
            if visited[h0]:
                continue
            loop = []
            h = h0
            while not visited[h]:
                visited[h] = True
                loop.append(h)
                h = self._next_boundary(h)
            if h != h0:
                # Walk entered an existing cycle from outside (shouldn't happen
                # on manifold input). Keep only the cyclic part.
                if h in loop:
                    loop = loop[loop.index(h):]
                else:
                    continue
            r = int(lab[loop[0]])
            starts = np.array([he_from[x] for x in loop])
            cflags = corner[starts]
            # also break where the neighbour region changes (robustness)
            tl = tlab[loop]
            change = np.r_[tl[0] != tl[-1], tl[1:] != tl[:-1]]
            brk = cflags | change
            n = len(loop)
            if brk.any():
                k0 = int(np.argmax(brk))
            else:
                k0 = int(np.argmin(starts))
            loop = loop[k0:] + loop[:k0]
            brk = np.r_[brk[k0:], brk[:k0]]
            if not brk.any():
                brk[0] = True
            cuts = list(np.nonzero(brk)[0]) + [n]
            segs = []
            for a, b in zip(cuts[:-1], cuts[1:]):
                hs = loop[a:b]
                verts = [int(he_from[x]) for x in hs] + [int(he_to[hs[-1]])]
                e_first = ukey(verts[0], verts[1])
                e_last = ukey(verts[-2], verts[-1])
                key = (min(e_first, e_last), max(e_first, e_last), min(verts[0], verts[-1]), max(verts[0], verts[-1]))
                c = chain_by_key.get(key)
                if c is None:
                    c = Chain(len(self.chains), verts, r, int(tlab[hs[0]]), key)
                    self.chains.append(c)
                    chain_by_key[key] = c
                    segs.append((c.id, True))
                else:
                    # traversed by the second region in the opposite direction
                    fwd = c.verts[0] == verts[0] and (len(verts) < 3 or c.verts[1] == verts[1])
                    if fwd and c.left == r:
                        # same region traversing twice (seam-like) — treat as new
                        c = Chain(len(self.chains), verts, r, int(tlab[hs[0]]), key + (len(self.chains),))
                        self.chains.append(c)
                        segs.append((c.id, True))
                    else:
                        segs.append((c.id, fwd))
            self.region_loops[r].extend(self._split_pinched(segs))

    def _split_pinched(self, segs):
        """A boundary walk that passes the same corner twice (a hole touching
        the outer boundary at one vertex) is really two loops. A single
        figure-eight wire is accepted in memory but gets mangled by STEP
        readers, so split it at the shared corner."""
        out = []
        stack = [segs]
        while stack:
            s = stack.pop()
            if len(s) < 2:
                out.append(s)
                continue
            seen = {}
            cut = None
            for k, (cid, fwd) in enumerate(s):
                c = self.chains[cid]
                v = c.verts[0] if fwd else c.verts[-1]
                if v in seen:
                    cut = (seen[v], k)
                    break
                seen[v] = k
            if cut is None:
                out.append(s)
                continue
            i, j = cut
            stack.append(s[i:j])
            stack.append(s[j:] + s[:i])
        return out

    # ------------------------------------------------------------------
    def split_chain(self, cid, k):
        """Split an open chain at verts[k] (0 < k < len-1) into two chains."""
        c = self.chains[cid]
        a = c.verts[: k + 1]
        b = c.verts[k:]
        c.verts = a
        c.closed = False
        nc = Chain(len(self.chains), b, c.left, c.right, ("split", cid, len(self.chains)))
        self.chains.append(nc)
        for r in (c.left, c.right):
            if r < 0:
                continue
            for loop in self.region_loops[r]:
                for i, (cc, fwd) in enumerate(loop):
                    if cc == cid:
                        loop[i:i + 1] = [(cid, True), (nc.id, True)] if fwd else [(nc.id, False), (cid, False)]
                        break
        return nc.id

    def insert_vertex(self, cid, k, q):
        """Insert vertex index q before verts[k] of chain cid."""
        c = self.chains[cid]
        c.verts = c.verts[:k] + [q] + c.verts[k:]

    def rotate_closed_chain(self, cid, k):
        """Make verts[k] the start/end vertex of a closed chain."""
        c = self.chains[cid]
        vs = c.verts[:-1]
        vs = vs[k:] + vs[:k]
        c.verts = vs + [vs[0]]

    def chain_points(self, cid):
        return self.V[self.chains[cid].verts]

    def loop_vertices(self, loop):
        """Mesh vertex sequence of a loop (closed, first vertex not repeated)."""
        out = []
        for cid, fwd in loop:
            vs = self.chains[cid].verts
            if not fwd:
                vs = vs[::-1]
            out.extend(vs[:-1])
        return out
