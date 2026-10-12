"""Planar region extraction (merging coplanar triangles)."""
from collections import deque

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components


def fit_plane(P, weights=None, normal_hint=None):
    """Least squares plane. Returns (origin, unit normal, max_abs_distance)."""
    P = np.asarray(P, dtype=np.float64)
    if weights is None:
        c = P.mean(0)
        Q = P - c
    else:
        w = weights / weights.sum()
        c = (P * w[:, None]).sum(0)
        Q = (P - c) * np.sqrt(w)[:, None]
    if len(P) < 3:
        n = normal_hint if normal_hint is not None else np.array([0, 0, 1.0])
    else:
        _, _, vt = np.linalg.svd(Q, full_matrices=False)
        n = vt[-1]
    if normal_hint is not None and np.dot(n, normal_hint) < 0:
        n = -n
    d = np.abs((P - c) @ n)
    return c, n, float(d.max()) if len(d) else 0.0


def coplanar_edge_mask(V, F, topo, normals, areas, tol, cos_tol=None):
    """For every manifold edge (face pair), whether the two faces are coplanar."""
    twin = topo.twin
    h = np.nonzero((twin >= 0) & ~topo.same_dir)[0]
    t = twin[h]
    m = h < t
    h, t = h[m], t[m]
    f, g = h // 3, t // 3
    of = F[f, (h % 3 + 2) % 3]
    og = F[g, (t % 3 + 2) % 3]
    a = topo.he_from[h]
    Va = V[a]
    df = np.abs(np.einsum("ij,ij->i", normals[f], V[og] - Va))
    dg = np.abs(np.einsum("ij,ij->i", normals[g], V[of] - Va))
    ref_f = areas[f] >= areas[g]
    d = np.where(ref_f, df, dg)
    ok = d <= tol
    if cos_tol is not None:
        nf_ok = areas[f] > 0
        ng_ok = areas[g] > 0
        cosang = np.einsum("ij,ij->i", normals[f], normals[g])
        ok &= (cosang >= cos_tol) | ~(nf_ok & ng_ok)
    else:
        # always reject folded-over faces
        cosang = np.einsum("ij,ij->i", normals[f], normals[g])
        ok &= cosang > 0
    return f[ok], g[ok]


def planar_regions(V, F, topo, normals, areas, tol, angle_tol_deg=None, report=None):
    """Label faces with planar region ids.

    Faces are merged across edges when they are coplanar within ``tol``.
    Each resulting region is verified against a single least squares plane
    and re-split by region growing when it drifts (gently curved surfaces).

    Returns (labels, planes) where planes[r] = (origin, normal, max_dev).
    """
    nf = len(F)
    cos_tol = None if angle_tol_deg is None else np.cos(np.radians(angle_tol_deg))
    f, g = coplanar_edge_mask(V, F, topo, normals, areas, tol, cos_tol)
    if len(f):
        G = sparse.coo_matrix((np.ones(len(f), dtype=np.int8), (f, g)), shape=(nf, nf))
        n_comp, comp = connected_components(G, directed=False)
    else:
        n_comp, comp = nf, np.arange(nf)

    labels = -np.ones(nf, dtype=np.int64)
    planes = []
    order = np.argsort(comp, kind="stable")
    sc = comp[order]
    starts = np.r_[0, np.nonzero(np.diff(sc))[0] + 1, nf]
    neighbours = None
    for k in range(len(starts) - 1):
        faces = order[starts[k]:starts[k + 1]]
        if len(faces) == 1:
            fi = faces[0]
            p0 = V[F[fi, 0]]
            n = normals[fi]
            if not np.any(n):
                n = _fallback_normal(V, F[fi])
            labels[fi] = len(planes)
            planes.append((V[F[fi]].mean(0), n, 0.0))
            continue
        vid = np.unique(F[faces])
        nh = (normals[faces] * areas[faces, None]).sum(0)
        nrm = np.linalg.norm(nh)
        nh = nh / nrm if nrm > 0 else None
        c, n, dev = fit_plane(V[vid], normal_hint=nh)
        if dev <= 2.0 * tol:
            labels[faces] = len(planes)
            planes.append((c, n, dev))
            continue
        # Region drifted (e.g. a finely tessellated large radius). Re-split.
        if neighbours is None:
            neighbours = _face_neighbours(topo, nf)
        for sub in _regrow(V, F, faces, normals, areas, neighbours, tol, cos_tol):
            vid = np.unique(F[sub])
            nh = (normals[sub] * areas[sub, None]).sum(0)
            nrm = np.linalg.norm(nh)
            nh = nh / nrm if nrm > 0 else normals[sub[0]]
            c, n, dev = fit_plane(V[vid], normal_hint=nh)
            labels[sub] = len(planes)
            planes.append((c, n, dev))
    return labels, planes


def _fallback_normal(V, tri):
    a, b, c = V[tri]
    e = [b - a, c - b, a - c]
    k = int(np.argmax([np.dot(x, x) for x in e]))
    d = e[k]
    # any vector perpendicular to the (degenerate) triangle line
    t = np.array([1.0, 0, 0]) if abs(d[0]) < 0.9 * np.linalg.norm(d) else np.array([0, 1.0, 0])
    n = np.cross(d, t)
    return n / np.linalg.norm(n)


def _face_neighbours(topo, nf):
    t = topo.twin.reshape(nf, 3)
    nb = np.where(t >= 0, t // 3, -1)
    return nb


def _regrow(V, F, faces, normals, areas, neighbours, tol, cos_tol):
    """Region growing restricted to ``faces`` with a fixed seed plane."""
    member = set(int(x) for x in faces)
    done = set()
    order = sorted(member, key=lambda x: -areas[x])
    out = []
    for seed in order:
        if seed in done:
            continue
        n = normals[seed]
        p = V[F[seed]].mean(0)
        region = [seed]
        done.add(seed)
        dq = deque([seed])
        while dq:
            x = dq.popleft()
            for y in neighbours[x]:
                y = int(y)
                if y < 0 or y in done or y not in member:
                    continue
                d = np.abs((V[F[y]] - p) @ n).max()
                if d > tol:
                    continue
                if cos_tol is not None and areas[y] > 0 and np.dot(normals[y], n) < cos_tol:
                    continue
                done.add(y)
                region.append(y)
                dq.append(y)
        out.append(np.array(region, dtype=np.int64))
    return out
