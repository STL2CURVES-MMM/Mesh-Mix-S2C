"""How closely a converted shape follows the mesh it came from.

Being a valid solid with the right volume isn't enough: a shape can pass
those checks and still be wrinkled, have dents, bumps or flaps, or be
missing small features. So every conversion is also compared with the mesh
itself, the way you would by laying one over the other:

1. The shape is cut into fine triangles again (finer than the fit tolerance).
2. Points are spread evenly over the mesh, and for each one the exact
   distance to the shape's surface is measured; then the other way round,
   from the shape to the mesh (that catches extra material and flaps that
   no mesh point is near).
3. At every mesh point the direction the surface faces is compared with the
   shape's direction at the nearest point. Wrinkles and dents keep close to
   the mesh but tilt the surface, so they show up here even when the
   distances look fine.

The result says how much of the surface is within the tolerance, the
largest gap, how much of the surface faces the wrong way, and where the
worst spot is, so a conversion can be rejected (and another method tried)
before it is saved.
"""
import numpy as np
from scipy.spatial import cKDTree

# a surface point "faces the wrong way" when its direction differs by more
# than this from the mesh (faceted meshes of round shapes differ by a few
# degrees; real wrinkles and dents by much more)
WRONG_WAY_DEG = 30.0


def _round_trip(shape):
    """A copy of a shape written out and read back in OpenCASCADE's own
    format: what a saved file holds. Meshed as it is in memory, faces whose
    edges stand a little off their surface (within their tolerance) came
    out in part (a printed mount's cylinder 85% covered, read as a gap 2.5
    tolerances wide); the copy read back, like the saved STEP file, is
    meshed whole."""
    import os
    import tempfile

    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.BRepTools import BRepTools
    from OCP.TopoDS import TopoDS_Shape

    fd, path = tempfile.mkstemp(suffix=".brep")
    os.close(fd)
    try:
        BRepTools.Write_s(shape, path)
        out = TopoDS_Shape()
        if BRepTools.Read_s(out, path, BRep_Builder()) and not out.IsNull():
            return out
    except Exception:
        pass
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    return BRepBuilderAPI_Copy(shape).Shape()


def face_blown_up(face, k=10.0):
    """A face whose surface is far bigger than its own outline allows (a
    freeform fit gone wild: one came out with an area of 10^40 mm^2 inside
    a few millimetres of edges). Such a face passes OpenCASCADE's validity
    check, but it is garbage, and meshing it finely never finishes."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    box = Bnd_Box()
    exp = TopExp_Explorer(face, TopAbs_EDGE)
    while exp.More():
        BRepBndLib.Add_s(exp.Current(), box)
        exp.Next()
    if box.IsVoid():
        return False
    lo, hi = box.CornerMin(), box.CornerMax()
    diag2 = (hi.X() - lo.X()) ** 2 + (hi.Y() - lo.Y()) ** 2 + (hi.Z() - lo.Z()) ** 2
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    area = props.Mass()
    return not np.isfinite(area) or abs(area) > k * max(diag2, 1e-30)


def _loosen_far_edges(shape, limit, n=24):
    """Edges whose curve stands further than ``limit`` from a face they bound
    get that distance back as their tolerance. Left at 1e-7, an edge 0.27 mm
    off a fitted strip on a wheel rim kept OpenCASCADE's mesher refining for
    minutes without end (the conversion hung); the other edges keep the
    smallest tolerance, so correct faces are still meshed finely. Returns
    the places (in the shape's face order) of the faces such edges bound:
    their triangles may stand off the surface by as much, and tessellate
    puts them back on it."""
    from OCP.BRep import BRep_Builder, BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Curve2d
    from OCP.BRepLib import BRepLib
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    B = BRep_Builder()
    loose_edges = []
    faces = TopExp_Explorer(shape, TopAbs_FACE)
    while faces.More():
        face = TopoDS.Face(faces.Current())
        faces.Next()
        surf = BRep_Tool.Surface_s(face)
        edges = TopExp_Explorer(face, TopAbs_EDGE)
        while edges.More():
            edge = TopoDS.Edge(edges.Current())
            edges.Next()
            if BRep_Tool.Degenerated_s(edge):
                continue
            try:
                c3 = BRepAdaptor_Curve(edge)
                c2 = BRepAdaptor_Curve2d(edge, face)
                ts = np.linspace(c3.FirstParameter(), c3.LastParameter(), n)
                gap = max(surf.Value(*c2.Value(t).Coord()).Distance(c3.Value(t)) for t in ts)
            except Exception:
                continue
            if gap > limit and gap > BRep_Tool.Tolerance_s(edge):
                B.UpdateEdge(edge, 1.1 * gap)
                loose_edges.append(edge)
    if loose_edges:
        # (their vertices must be at least as loose as the edges)
        BRepLib.UpdateTolerances_s(shape)
    # the faces such an edge bounds, by their place in the shape
    touched = set()
    if loose_edges:
        faces = TopExp_Explorer(shape, TopAbs_FACE)
        k = 0
        while faces.More():
            edges = TopExp_Explorer(faces.Current(), TopAbs_EDGE)
            while edges.More():
                if any(edges.Current().IsSame(e) for e in loose_edges):
                    touched.add(k)
                    break
                edges.Next()
            k += 1
            faces.Next()
    return touched


def _onto_surface(face, P):
    """Points moved onto the face's surface (to its nearest point)."""
    from OCP.BRep import BRep_Tool
    from OCP.ShapeAnalysis import ShapeAnalysis_Surface
    from OCP.gp import gp_Pnt

    surf = BRep_Tool.Surface_s(face)
    sas = ShapeAnalysis_Surface(surf)
    out = np.array(P, dtype=float)
    for i, p in enumerate(P):
        try:
            uv = sas.ValueOfUV(gp_Pnt(*p), 1e-7)
            out[i] = surf.Value(uv.X(), uv.Y()).Coord()
        except Exception:
            pass
    return out


def tessellate(shape, deflection, angle=0.25, loose=None):
    """The shape's faces as triangles: (V, F, face index of each triangle).

    OpenCASCADE never meshes a face finer than its tolerance, and converted
    faces carry the fit tolerance: a sphere asked for 0.005 mm came out
    0.03 mm inside its surface, so correct faces looked like they missed
    the mesh. A copy with the smallest tolerances is meshed instead (which
    also leaves no fine mesh on the shape itself for the 3D preview to
    reuse); faces that copy can't mesh are meshed from the shape."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.ShapeFix import ShapeFix_ShapeTolerance
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.BRepTools import BRepTools

    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    deflection = max(float(deflection), 1e-7)
    faces = []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        faces.append(TopoDS.Face(exp.Current()))
        exp.Next()
    # a blown-up face is left out of the fine meshing (it would never end),
    # and meshed as it is: coarsely, and its distances from the mesh show
    # what it is
    blown = [face_blown_up(f) for f in faces]
    B = BRep_Builder()
    comp = TopoDS_Compound()
    B.MakeCompound(comp)
    for f, b in zip(faces, blown):
        if not b:
            B.Add(comp, f)
    copy = _round_trip(comp)
    ShapeFix_ShapeTolerance().SetTolerance(copy, 1e-7)
    # (edges further off than ``loose`` keep their real distance as
    # tolerance; None leaves every edge at 1e-7, as the build loop needs:
    # loosened there, a worm gear's correct faces looked off the mesh and
    # were rebuilt as patches, 141 -> 273 faces)
    touched = _loosen_far_edges(copy, loose) if loose is not None else set()
    # (the copy holds the faces that aren't blown up, in order)
    kept = [fi for fi, b in enumerate(blown) if not b]
    touched_orig = {kept[k] for k in touched if k < len(kept)}
    BRepMesh_IncrementalMesh(copy, deflection, False, angle, True)
    copied = TopExp_Explorer(copy, TopAbs_FACE)
    retry = []
    Vs, Fs, ids = [], [], []
    base = 0
    for fi, (orig, gone) in enumerate(zip(faces, blown)):
        loc = TopLoc_Location()
        tri = None
        if not gone:
            face = TopoDS.Face(copied.Current())
            copied.Next()
            tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None or tri.NbTriangles() == 0:
            # mesh the face as it is (at its own tolerance) instead, coarser
            # each time if need be: a face read back from a STEP file has
            # almost no tolerance, and OpenCASCADE can fail to mesh it finely
            # (a missing face would count as a gap the size of the face)
            face = orig
            for k in range(6):
                BRepTools.Clean_s(face)
                BRepMesh_IncrementalMesh(face, deflection * 4 ** k, False, angle, True)
                tri = BRep_Tool.Triangulation_s(face, loc)
                if tri is not None and tri.NbTriangles() > 0:
                    break
            retry.append(face)
        if tri is not None and tri.NbTriangles() > 0:
            trsf = loc.Transformation()
            n = tri.NbNodes()
            P = np.empty((n, 3))
            for i in range(1, n + 1):
                p = tri.Node(i).Transformed(trsf)
                P[i - 1] = (p.X(), p.Y(), p.Z())
            if fi in touched_orig:
                # (meshed at a loose edge's tolerance: back onto the surface,
                # or a worm gear's correct faces looked off the mesh and were
                # rebuilt as patches, 141 -> 273 faces)
                P = _onto_surface(face, P)
            m = tri.NbTriangles()
            T = np.empty((m, 3), dtype=np.int64)
            for i in range(1, m + 1):
                a, b, c = tri.Triangle(i).Get()
                T[i - 1] = (a - 1, b - 1, c - 1)
            if face.Orientation() == TopAbs_REVERSED:
                T = T[:, ::-1]
            Vs.append(P)
            Fs.append(T + base)
            ids.append(np.full(m, fi, dtype=np.int64))
            base += n
    # (a fine triangulation would stay on the shape, and the 3D preview, or
    # anything else that meshes it, would reuse it)
    for face in retry:
        BRepTools.Clean_s(face)
    if not Vs:
        return np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros(0, dtype=np.int64)
    return np.vstack(Vs), np.vstack(Fs), np.concatenate(ids)


def tessellate_faces(faces, deflection, angle=0.25, loose=None):
    """Like tessellate, for a list of faces: (V, F, index into ``faces`` of
    each triangle)."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    B = BRep_Builder()
    comp = TopoDS_Compound()
    B.MakeCompound(comp)
    for f in faces:
        B.Add(comp, f)
    # a compound keeps its faces in the order they were added
    return tessellate(comp, deflection, angle, loose)


def _tri_normals(V, F):
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    area = np.linalg.norm(n, axis=1)
    return n / np.maximum(area, 1e-300)[:, None], 0.5 * area


def weighted_percentile(x, w, q):
    order = np.argsort(x)
    x, w = x[order], np.maximum(w[order], 0.0)
    cw = np.cumsum(w)
    if cw[-1] <= 0:
        return float(np.percentile(x, q))
    return float(x[min(len(x) - 1, int(np.searchsorted(cw, q / 100.0 * cw[-1])))])


def chord_deflection(N, A, f, g, L, tiny=None):
    """How far a mesh's flat triangles stand off the curved surfaces they
    approximate (the chordal deflection it was tessellated with), from the
    bends between neighbouring triangles ``f`` and ``g`` across edges of
    length ``L``: across a smoothly curved edge with dihedral angle theta
    between triangles of height h, the sagitta is about h * theta / 8.

    Shallow creases are not curvature: where the long facets of a groove
    meet the small triangles of its sloped end, the dihedral angle is large
    but the surface isn't coarsely curved. Counted like curvature, such
    creases made a part faceted to 0.006 mm look like a 0.3 mm
    tessellation. So each edge uses the smaller of its two triangle heights
    (on a curved surface they are alike; where a long facet meets a tiny
    triangle the long one says nothing about curvature), and the
    percentiles are weighted by edge length (a crease often has only a few
    tiny edges).

    Returns (typical, high): the 95th and 99.5th percentiles."""
    th = np.arccos(np.clip((N[f] * N[g]).sum(1), -1, 1))
    smooth = (th > np.radians(1.0)) & (th < np.radians(40.0))
    if tiny is not None:
        smooth &= ~tiny[f] & ~tiny[g]
    if smooth.sum() < 10:
        return 0.0, 0.0
    Ls = np.maximum(L, 1e-30)
    sag = np.minimum(2 * A[f] / Ls, 2 * A[g] / Ls) * th / 8.0
    return (weighted_percentile(sag[smooth], L[smooth], 95),
            weighted_percentile(sag[smooth], L[smooth], 99.5))


def mesh_deflection(V, F):
    """chord_deflection of a triangle list (vertices at the same position
    are joined first)."""
    if len(F) < 2:
        return 0.0, 0.0
    W = _welded(V, F)
    e = np.stack([W[:, [0, 1]], W[:, [1, 2]], W[:, [2, 0]]], axis=1).reshape(-1, 2)
    e.sort(axis=1)
    key = e[:, 0].astype(np.int64) * (int(W.max()) + 1) + e[:, 1]
    order = np.argsort(key, kind="stable")
    ks = key[order]
    # edges with exactly two triangles
    same = ks[1:] == ks[:-1]
    lone = np.ones(len(same), dtype=bool)
    lone[1:] &= ~same[:-1]
    lone[:-1] &= ~same[1:]
    pair = same & lone
    a, b = order[:-1][pair], order[1:][pair]
    N, A = _tri_normals(V, F)
    L = np.linalg.norm(V[F.reshape(-1)[a]] - V[F[a // 3, (a % 3 + 1) % 3]], axis=1)
    ext = float(np.linalg.norm(V.max(0) - V.min(0)))
    return chord_deflection(N, A, a // 3, b // 3, L, tiny=A < 1e-10 * ext ** 2)


def local_deflection(V, F):
    """How far a true curved surface may stand off each of the mesh's flat
    triangles, from the bends across its edges to smoothly curved
    neighbours. A spot where the shape is further from
    the mesh than this (and than the tolerance) is a real miss, not the
    mesh's own coarseness: a coarse sphere's big pole triangles may stand
    0.2 mm off, a CAD export's fine fillet triangles only 0.01 mm."""
    out = np.zeros(len(F))
    if len(F) < 2:
        return out
    W = _welded(V, F)
    e = np.stack([W[:, [0, 1]], W[:, [1, 2]], W[:, [2, 0]]], axis=1).reshape(-1, 2)
    e.sort(axis=1)
    key = e[:, 0].astype(np.int64) * (int(W.max()) + 1) + e[:, 1]
    order = np.argsort(key, kind="stable")
    ks = key[order]
    same = ks[1:] == ks[:-1]
    lone = np.ones(len(same), dtype=bool)
    lone[1:] &= ~same[:-1]
    lone[:-1] &= ~same[1:]
    pair = same & lone
    a, b = order[:-1][pair], order[1:][pair]
    f, g = a // 3, b // 3
    N, A = _tri_normals(V, F)
    th = np.arccos(np.clip((N[f] * N[g]).sum(1), -1.0, 1.0))
    th = np.where((th > np.radians(1.0)) & (th < np.radians(40.0)), th, 0.0)
    # a flat triangle's centre stands off a curve bending by th along it by
    # about its longest edge * th / 6 (more than an edge's sagitta)
    T = V[F]
    Lmax = np.linalg.norm(T - np.roll(T, -1, axis=1), axis=2).max(1)
    np.maximum.at(out, f, Lmax[f] * th / 6.0)
    np.maximum.at(out, g, Lmax[g] * th / 6.0)
    return out


def mesh_noise(V, F, n_v=5000, cap=40, sharp_deg=40.0, seed=0):
    """How far the mesh's vertices scatter around the smooth surface they
    lie on (the noise of a 3D scan). Each of up to ``n_v`` vertices is
    compared with a quadric fitted through its neighbours up to two edges
    away (the vertex itself left out), so curvature doesn't count; vertices
    within two edges of a sharp or open edge are skipped, so edges and small
    features (a groove, a rib) don't count either. Returns the median; 0
    when too few vertices qualify (a part made of flat faces and sharp edges
    has nothing to measure, and isn't a scan)."""
    import scipy.sparse as sp

    if len(F) < 2:
        return 0.0
    W = _welded(V, F)
    nv = int(W.max()) + 1
    P = np.zeros((nv, 3))
    P[W.reshape(-1)] = V[F.reshape(-1)]
    N, _ = _tri_normals(V, F)
    adj = _edge_neighbours(W)
    sharp = np.zeros(nv, dtype=bool)
    cos_s = np.cos(np.radians(sharp_deg))
    for j in range(3):
        g = adj[:, j]
        bad = (g < 0) | ((N * N[np.maximum(g, 0)]).sum(1) < cos_s)
        sharp[W[bad, j]] = True
        sharp[W[bad, (j + 1) % 3]] = True
    e = np.concatenate([W[:, [0, 1]], W[:, [1, 2]], W[:, [2, 0]]])
    A1 = sp.coo_matrix((np.ones(len(e), np.float32), (e[:, 0], e[:, 1])), shape=(nv, nv)).tocsr()
    A1 = ((A1 + A1.T) > 0).astype(np.float32)
    A2 = ((A1 + A1 @ A1) > 0).astype(np.float32).tocsr()
    near_sharp = (A2 @ sharp.astype(np.float32)) > 0
    cand = np.nonzero(~near_sharp)[0]
    if len(cand) < 50:
        return 0.0
    rng = np.random.default_rng(seed)
    pick = rng.choice(cand, min(n_v, len(cand)), replace=False)
    rows = A2[pick].tocoo()
    r, c = rows.row, rows.col
    keep = c != pick[r]
    r, c = r[keep], c[keep]
    # at most ``cap`` neighbours each, picked at random
    order = np.lexsort((rng.random(len(r)), r))
    r, c = r[order], c[order]
    start = np.searchsorted(r, np.arange(len(pick)))
    rank = np.arange(len(r)) - start[r]
    sel = rank < cap
    r, c, rank = r[sel], c[sel], rank[sel]
    m = len(pick)
    X = np.zeros((m, cap, 3))
    mask = np.zeros((m, cap))
    X[r, rank] = P[c]
    mask[r, rank] = 1.0
    cnt = mask.sum(1)
    ok = cnt >= 8
    X, mask, cnt, pv = X[ok], mask[ok], cnt[ok], P[pick[ok]]
    if not len(X):
        return 0.0
    cen = (X * mask[..., None]).sum(1) / cnt[:, None]
    D = (X - cen[:, None]) * mask[..., None]
    _, U = np.linalg.eigh(np.einsum("mki,mkj->mij", D, D))
    loc = np.einsum("mki,mij->mkj", D, U)  # (normal, tangent, tangent)
    z, x, y = loc[..., 0], loc[..., 1], loc[..., 2]
    sc = np.sqrt((mask * (x * x + y * y)).sum(1) / cnt)[:, None] + 1e-30
    x, y = x / sc, y / sc
    Mq = np.stack([x * x, x * y, y * y, x, y, np.ones_like(x)], axis=2) * mask[..., None]
    AtA = np.einsum("mki,mkj->mij", Mq, Mq) + 1e-9 * np.eye(6)
    coef = np.linalg.solve(AtA, np.einsum("mki,mk->mi", Mq, z)[..., None])[..., 0]
    q = np.einsum("mi,mij->mj", pv - cen, U)
    qx, qy = q[:, 1] / sc[:, 0], q[:, 2] / sc[:, 0]
    zq = (np.stack([qx * qx, qx * qy, qy * qy, qx, qy, np.ones_like(qx)], axis=1) * coef).sum(1)
    return float(np.median(np.abs(q[:, 0] - zq)))


def thin_share(V, F, noise, k=4.0):
    """The share of the surface made of triangles thinner than ``k`` times
    the noise: their directions are mostly noise (a scan re-meshed into
    slivers can have whole patches of them, some flipped over)."""
    if noise <= 0 or len(F) == 0:
        return 0.0
    _, A = _tri_normals(V, F)
    T = V[F]
    L = np.linalg.norm(T - np.roll(T, -1, axis=1), axis=2).max(1)
    h = 2 * A / np.maximum(L, 1e-30)
    return float(A[h < k * noise].sum() / max(A.sum(), 1e-30))


def sample(V, F, n, rng):
    """About n points spread evenly (by area) over the triangles: (points,
    triangle of each point)."""
    _, area = _tri_normals(V, F)
    tot = area.sum()
    if tot <= 0 or len(F) == 0:
        return np.zeros((0, 3)), np.zeros(0, dtype=np.int64)
    counts = rng.poisson(area / tot * n)
    tri = np.repeat(np.arange(len(F)), counts)
    r1 = np.sqrt(rng.random(len(tri)))
    r2 = rng.random(len(tri))
    A, B, C = V[F[tri, 0]], V[F[tri, 1]], V[F[tri, 2]]
    P = (1 - r1)[:, None] * A + (r1 * (1 - r2))[:, None] * B + (r1 * r2)[:, None] * C
    return P, tri


def point_triangle_distance(P, A, B, C):
    """Exact distance from each point to its triangle (all arrays (n, 3)),
    after Ericson, Real-Time Collision Detection 5.1.5. Returns (distance,
    closest point)."""
    ab, ac, ap = B - A, C - A, P - A
    d1 = np.einsum("ij,ij->i", ab, ap)
    d2 = np.einsum("ij,ij->i", ac, ap)
    bp = P - B
    d3 = np.einsum("ij,ij->i", ab, bp)
    d4 = np.einsum("ij,ij->i", ac, bp)
    cp = P - C
    d5 = np.einsum("ij,ij->i", ab, cp)
    d6 = np.einsum("ij,ij->i", ac, cp)
    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2
    denom = va + vb + vc
    with np.errstate(divide="ignore", invalid="ignore"):
        v = vb / denom
        w = vc / denom
    with np.errstate(invalid="ignore"):
        Q = A + v[:, None] * ab + w[:, None] * ac  # inside the face
    # the regions outside, in the order of the reference algorithm (later
    # ones are only used where the earlier ones didn't apply)
    done = np.zeros(len(P), dtype=bool)

    def put(mask, X):
        m = mask & ~done
        Q[m] = X[m]
        done[m] = True

    put((d1 <= 0) & (d2 <= 0), A)
    put((d3 >= 0) & (d4 <= d3), B)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = d1 / (d1 - d3)
    put((vc <= 0) & (d1 >= 0) & (d3 <= 0), A + t[:, None] * ab)
    put((d6 >= 0) & (d5 <= d6), C)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = d2 / (d2 - d6)
    put((vb <= 0) & (d2 >= 0) & (d6 <= 0), A + t[:, None] * ac)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (d4 - d3) / ((d4 - d3) + (d5 - d6))
    put((va <= 0) & ((d4 - d3) >= 0) & ((d5 - d6) >= 0), B + t[:, None] * (C - B))
    # squashed triangles (a line or a point): the nearest point on their edges
    cross = np.linalg.norm(np.cross(ab, ac), axis=1)
    scale = np.maximum(np.einsum("ij,ij->i", ab, ab), np.einsum("ij,ij->i", ac, ac))
    bad = ~np.isfinite(Q).all(axis=1) | (cross <= 1e-12 * np.maximum(scale, 1e-300))
    if bad.any():
        p, a, b, c = P[bad], A[bad], B[bad], C[bad]
        best_d = np.full(len(p), np.inf)
        best_q = np.zeros_like(p)
        for s, e in ((a, b), (b, c), (c, a)):
            q = _closest_on_segment(p, s, e)
            dd = np.linalg.norm(p - q, axis=1)
            m = dd < best_d
            best_d[m] = dd[m]
            best_q[m] = q[m]
        Q[bad] = best_q
    return np.linalg.norm(P - Q, axis=1), Q


def _closest_on_segment(P, S, E):
    d = E - S
    ll = np.einsum("ij,ij->i", d, d)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.einsum("ij,ij->i", P - S, d) / ll
    t = np.where(ll > 0, np.clip(t, 0.0, 1.0), 0.0)
    return S + t[:, None] * d


class Surface:
    """A triangle surface that answers "how far is this point from you, and
    which way do you face there"."""

    def __init__(self, V, F, spacing, rng, max_samples=3_000_000):
        self.V, self.F = V, F
        self.normals, area = _tri_normals(V, F)
        tot = float(area.sum())
        n = int(min(max_samples, max(len(F) * 2, tot / max(spacing, 1e-12) ** 2)))
        P, tri = sample(V, F, n, rng)
        # every triangle has at least its centre, so none is missed
        cen = V[F].mean(axis=1)
        self.points = np.vstack([cen, P])
        self.tri = np.concatenate([np.arange(len(F)), tri])
        self.tree = cKDTree(self.points)
        self.area = tot
        # faces of a tessellated shape each have their own copy of the
        # vertices on their edges: merge them so neighbours across an edge
        # of the shape are found too
        self.adj = _edge_neighbours(_welded(V, F))
        self.spacing = float(spacing)
        self._pc = None

    def nearest(self, P, k=8, chunk=100_000, exact_above=0.0):
        """Exact distance from each point to the surface, and the triangle
        it is nearest to.

        First a quick guess: the triangles of the nearest sample points and
        their neighbours (near a corner the nearest samples can all lie on
        other triangles than the thin wedge the point is on). The guess can
        miss: next to long, thin triangles (a cylinder meshed in strips) it
        made a point 0.016 mm from the shape look 0.18 mm away. So then
        every triangle that could be closer than the guess is measured too
        (see _refine), which makes the result exact. Only for points the
        guess puts further than ``exact_above`` away: closer ones are off
        by less than that at most."""
        dist = np.empty(len(P))
        tri = np.empty(len(P), dtype=np.int64)
        V, F = self.V, self.F
        for s in range(0, len(P), chunk):
            Q = P[s:s + chunk]
            _, idx = self.tree.query(Q, k=min(k, len(self.points)))
            idx = np.atleast_2d(idx)
            if idx.shape[0] != len(Q):
                idx = idx.T
            near = self.tri[idx]  # (m, k)
            nb = self.adj[near[:, :3]].reshape(len(Q), -1)
            cand = np.concatenate([near, np.where(nb >= 0, nb, near[:, :1])], axis=1)
            # each (point, triangle) pair once: the nearest samples are
            # mostly on the same few triangles
            cand.sort(axis=1)
            keep = np.ones(cand.shape, dtype=bool)
            keep[:, 1:] = cand[:, 1:] != cand[:, :-1]
            pi = np.nonzero(keep)[0]
            c = cand[keep]
            d, _ = point_triangle_distance(Q[pi], V[F[c, 0]], V[F[c, 1]], V[F[c, 2]])
            # the smallest distance of each point (rows are in order)
            order = np.lexsort((d, pi))
            first = np.ones(len(order), dtype=bool)
            first[1:] = pi[order][1:] != pi[order][:-1]
            best = order[first]
            dist[s:s + chunk] = d[best]
            tri[s:s + chunk] = c[best]
        far = np.nonzero(dist > exact_above)[0]
        if len(far):
            d, t = dist[far], tri[far]
            self._refine(P[far], d, t)
            dist[far], tri[far] = d, t
        return dist, tri

    def _pieces(self):
        """The triangles cut into small pieces (see _subdivide), for the
        exact search: (KD-tree of the pieces' centres, their corners, the
        triangle each came from, the largest centre-to-corner distance)."""
        if self._pc is None:
            area = float(self.area)
            # pieces about as big as the sample spacing, but not more than
            # about a million of them
            r_max = max(self.spacing, np.sqrt(area / 1e6), 1e-12)
            A, B, C, parent = _subdivide(self.V, self.F, r_max)
            cen = (A + B + C) / 3.0
            R = np.sqrt(np.max([((X - cen) ** 2).sum(1) for X in (A, B, C)], axis=0)) if len(cen) else np.zeros(0)
            self._pc = (cKDTree(cen), A, B, C, parent, float(R.max()) if len(R) else 0.0)
        return self._pc

    def _refine(self, P, dist, tri, max_pairs=4_000_000):
        """Makes the distances exact. A triangle closer to a point than the
        distance found so far has a piece within that distance of the point,
        so that piece's centre is within the distance plus the piece's size:
        all those pieces are measured."""
        import itertools

        if not len(P) or not len(self.F):
            return
        tree, A, B, C, parent, rp = self._pieces()
        r = dist + rp * (1 + 1e-9) + 1e-12
        counts = tree.query_ball_point(P, r, return_length=True)
        cum = np.cumsum(counts)
        start = 0
        while start < len(P):
            # points in runs of at most max_pairs candidate pairs
            before = cum[start - 1] if start else 0
            end = min(len(P), max(start + 1, int(np.searchsorted(cum, before + max_pairs, side="right"))))
            if end - start == 1 and counts[start] > max_pairs:
                start = end  # (a point far from everything: the first answer stays)
                continue
            res = tree.query_ball_point(P[start:end], r[start:end], return_sorted=False)
            lens = np.fromiter((len(x) for x in res), dtype=np.int64, count=len(res))
            if lens.sum():
                k = np.fromiter(itertools.chain.from_iterable(res), dtype=np.int64, count=int(lens.sum()))
                pi = np.repeat(np.arange(start, end), lens)
                d, _ = point_triangle_distance(P[pi], A[k], B[k], C[k])
                closer = d < dist[pi]
                if closer.any():
                    pi, k, d = pi[closer], k[closer], d[closer]
                    order = np.lexsort((d, pi))
                    first = np.ones(len(order), dtype=bool)
                    first[1:] = pi[order][1:] != pi[order][:-1]
                    best = order[first]
                    dist[pi[best]] = d[best]
                    tri[pi[best]] = parent[k[best]]
            start = end


def _subdivide(V, F, r_max):
    """The triangles, each cut in two across its longest edge until no
    piece reaches further than r_max from its centre: (corners A, B, C of
    the pieces, the triangle each piece came from). The pieces cover
    exactly the triangles, so the nearest piece is the nearest triangle."""
    A, B, C = V[F[:, 0]], V[F[:, 1]], V[F[:, 2]]
    parent = np.arange(len(F))
    out = []
    for _ in range(64):
        cen = (A + B + C) / 3.0
        R2 = np.max([((X - cen) ** 2).sum(1) for X in (A, B, C)], axis=0) if len(cen) else np.zeros(0)
        big = R2 > r_max * r_max
        out.append((A[~big], B[~big], C[~big], parent[~big]))
        if not big.any():
            break
        a, b, c, p = A[big], B[big], C[big], parent[big]
        e = np.argmax([((a - b) ** 2).sum(1), ((b - c) ** 2).sum(1), ((c - a) ** 2).sum(1)], axis=0)[:, None]
        # the longest edge as a2-b2
        a2 = np.where(e == 0, a, np.where(e == 1, b, c))
        b2 = np.where(e == 0, b, np.where(e == 1, c, a))
        c2 = np.where(e == 0, c, np.where(e == 1, a, b))
        m = 0.5 * (a2 + b2)
        A, B, C, parent = np.vstack([a2, m]), np.vstack([m, b2]), np.vstack([c2, c2]), np.concatenate([p, p])
    else:
        out.append((A, B, C, parent))
    return tuple(np.concatenate([o[i] for o in out]) for i in range(4))


class Verdict:
    """How a converted shape compares with its mesh, in a form the pipeline
    can rank and the app can show."""

    # a conversion follows the mesh when 99% of the surface (both ways) is
    # within P99_K tolerances, 99.9% within P999_K (so a small spot can't be
    # far off: a flap or a dent shows even when it is small), and almost none
    # of it faces the wrong way
    P99_K = 3.0
    P999_K = 8.0
    WRONG_WAY_MAX = 0.005
    # a spot further from the mesh than this many times what the mesh's own
    # coarseness there explains (see local_deflection) is too small to move
    # the percentiles but may be a real miss (a notch, a rounded-off tip):
    # the verdict is then uncertain, so the finer check and the other
    # method run. On the test models good results stay below 2.6; a 0.3 mm
    # notch measured 4.7.
    LOCAL_K = 3.5

    # within this share of a limit (either side), a verdict is uncertain
    MARGIN = 0.25
    # below this certainty, a second, finer check decides
    RECHECK_BELOW = 0.5

    def __init__(self, r, tol):
        self.r = r
        self.tol = tol
        self.rechecked = False
        self.certainty, self.doubt = 1.0, ""
        if r.get("unknown"):
            # the comparison itself failed: neither good nor bad
            self.ok, self.score = True, -3.0
            self.certainty, self.doubt = 0.0, "it couldn't be compared with the mesh"
            return
        if r.get("empty"):
            self.ok, self.score = False, -1e9
            return
        p99 = max(r["to_shape"]["p99"], r["to_mesh"]["p99"]) / tol
        p999 = max(r["to_shape"].get("p999", 0.0), r["to_mesh"].get("p999", 0.0)) / tol
        ww = max(r["wrong_way"], r["wrong_way_shape"])
        self.ok = p99 <= self.P99_K and p999 <= self.P999_K and ww <= self.WRONG_WAY_MAX
        # higher is better: tight fit, no far-off spots, few points facing
        # the wrong way
        self.local = float(r.get("local_max", 0.0))
        self.score = -p99 - 0.25 * p999 - 200.0 * ww - 0.25 * max(0.0, self.local - self.LOCAL_K)
        self.certainty, self.doubt = self._certainty(p99, p999, ww)

    def _certainty(self, p99, p999, ww):
        """How sure the verdict is (0 to 1), and why not (in words)."""
        r = self.r
        # how far each measure is from its limit, as a share of the limit
        margin = min(abs(p99 - self.P99_K) / self.P99_K, abs(p999 - self.P999_K) / self.P999_K,
                     abs(ww - self.WRONG_WAY_MAX) / self.WRONG_WAY_MAX)
        c, doubt = min(1.0, margin / self.MARGIN), "it is close to the limit"
        # a scan's own surface is uncertain
        if r.get("mesh_kind") == "scan" and c > 0.7:
            c, doubt = 0.7, "the mesh is noisy"
        # the two ways of counting wrong-facing points disagree (on a scan
        # they always do: its noise-tilted triangles are lone points)
        elif r.get("wrong_way_points", 0.0) > self.WRONG_WAY_MAX >= ww and c > 0.4:
            c, doubt = 0.4, "some spots face another way than the mesh, but only scattered ones"
        if self.local > self.LOCAL_K and c > 0.4:
            c, doubt = 0.4, "one small spot is further from the mesh than the rest"
        return c, doubt

    def summary(self, unit="mm"):
        r = self.r
        if r.get("unknown"):
            return "it couldn't be compared with the mesh"
        if r.get("empty"):
            return "the shape has no surface to compare with the mesh"
        within = 100.0 * r["within2"]
        return (f"{within:.1f}% of the surface is within {2 * self.tol:.2g} {unit} of the mesh; "
                f"largest gap {r['worst_distance']:.2g} {unit}")

    def as_dict(self):
        r = self.r
        if r.get("empty") or r.get("unknown"):
            return {"ok": bool(self.ok), "empty": True}
        return {
            "ok": bool(self.ok),
            "tolerance": self.tol,
            "within": r["within"],
            "within2": r["within2"],
            "p99": max(r["to_shape"]["p99"], r["to_mesh"]["p99"]),
            "max": r["worst_distance"],
            "worst": r["worst"],
            "wrong_way": max(r["wrong_way"], r["wrong_way_shape"]),
            "mesh_kind": r.get("mesh_kind", "clean"),
            "certainty": round(float(self.certainty), 3),
            "local_max": round(self.local, 3),
            "rechecked": bool(self.rechecked),
        }


def judge(shape, shells, tol, n_points=None, recheck=True, on_recheck=None):
    """Verdict for a converted shape against the mesh shells (objects with
    V and F) it was made from.

    When the verdict is uncertain (see Verdict._certainty), a second check
    decides: three times as many points, other ones, and the shape cut into
    finer triangles. ``on_recheck(reason)`` is called first, so the user
    can be told why it takes longer."""
    meshes = [(s.V, s.F) for s in shells]
    r = compare(shape, meshes, tol, n_points=n_points)
    r.pop("points", None)
    v = Verdict(r, max(float(r.get("allow", tol)), 1e-9))
    if not recheck or r.get("empty") or v.certainty >= Verdict.RECHECK_BELOW:
        return v
    if on_recheck is not None:
        on_recheck(v.doubt)
    n = int(min(3 * r.get("n_points", 50_000), 450_000))
    r2 = compare(shape, meshes, tol, n_points=n, seed=1, fine=True)
    r2.pop("points", None)
    v2 = Verdict(r2, max(float(r2.get("allow", tol)), 1e-9))
    v2.rechecked = True
    return v2


def _welded(V, F):
    """F with vertices at the same position merged."""
    if len(V) == 0:
        return F
    scale = max(float(np.abs(V).max()), 1e-12)
    _, inv = np.unique(np.round(V / scale, 10), axis=0, return_inverse=True)
    return inv.reshape(-1)[F]


def _edge_neighbours(F):
    """For each triangle, the triangles across its three edges (-1: none;
    with more than two triangles on an edge, one of them)."""
    n = len(F)
    if n == 0:
        return np.zeros((0, 3), dtype=np.int64)
    e = np.stack([F[:, [0, 1]], F[:, [1, 2]], F[:, [2, 0]]], axis=1).reshape(-1, 2)
    e.sort(axis=1)
    key = e[:, 0].astype(np.int64) * (int(F.max()) + 1) + e[:, 1]
    order = np.argsort(key, kind="stable")
    ks = key[order]
    out = np.full(3 * n, -1, dtype=np.int64)
    same = ks[1:] == ks[:-1]
    a, b = order[:-1][same], order[1:][same]
    out[a] = b // 3
    out[b] = a // 3
    return out.reshape(n, 3)


_MESH_CACHE = {}


def mesh_surface(V, F):
    """The mesh as a Surface, its smoothed normals and the sample spacing,
    kept for the next call with the same mesh (every candidate of a body
    is compared with the same mesh)."""
    import hashlib

    h = hashlib.blake2b(digest_size=16)
    h.update(np.ascontiguousarray(V).tobytes())
    h.update(np.ascontiguousarray(F).tobytes())
    key = h.hexdigest()
    hit = _MESH_CACHE.get(key)
    if hit is not None:
        return hit
    area = _tri_normals(V, F)[1]
    # reference points on each surface for the nearest-triangle search (the
    # distances themselves are exact); more for detailed meshes
    n_ref = np.clip(4 * len(F), 1e5, 5e5)
    spacing = float(np.sqrt(area.sum() / n_ref))
    M = Surface(V, F, spacing, np.random.default_rng(1))
    # the mesh's directions, smoothed over neighbouring triangles (not
    # across sharp edges): a scan's noisy triangles point every which way,
    # but a smooth surface through them is what a conversion should give
    MN = smoothed_normals(M.normals, area, M.adj)
    # what kind of mesh it is (see mesh_kind)
    M.deflection = mesh_deflection(V, F)[0]
    M.noise = mesh_noise(V, F)
    M.thin = thin_share(V, F, M.noise)
    T = V[F]
    M.edge = float(np.median(np.linalg.norm(T - np.roll(T, -1, axis=1), axis=2).max(1))) if len(F) else 0.0
    M.local_dev = local_deflection(V, F)
    _MESH_CACHE.clear()  # one mesh at a time (they can be big)
    _MESH_CACHE[key] = (M, MN, spacing)
    return M, MN, spacing


def smoothed_normals(N, A, adj, iters=3, keep_deg=30.0):
    """Triangle normals averaged with their neighbours' (by area), a few
    times over; neighbours across a sharp edge (more than ``keep_deg``
    apart) are left out, so edges stay sharp."""
    cos_keep = np.cos(np.radians(keep_deg))
    for _ in range(iters):
        acc = N * A[:, None]
        for j in range(3):
            nb = adj[:, j]
            nbs = np.maximum(nb, 0)
            use = (nb >= 0) & ((N[nbs] * N).sum(1) >= cos_keep)
            acc[use] += N[nbs[use]] * A[nbs[use], None]
        N = acc / np.maximum(np.linalg.norm(acc, axis=1), 1e-300)[:, None]
    return N


def _angle_deg(a, b):
    return np.degrees(np.arccos(np.clip(np.einsum("ij,ij->i", a, b), -1.0, 1.0)))


def mesh_kind(M, tol):
    """Which rules a mesh is judged by:

    clean   a CAD export or a sculpt: the checks as they are.
    coarse  a true curve stands off the mesh's flat triangles by up to its
            chordal deflection (an OpenSCAD 16-gon, a low-detail export),
            so the tolerance grows to that.
    scan    noisy: a smooth surface through the noise is right, so the
            tolerance grows to the noise; and where the mesh is made of
            triangles thinner than the noise, their directions are noise
            too, so only patches facing the wrong way count, not lone
            points (see _coherent)."""
    if M.noise > 0.5 * tol or M.thin > Verdict.WRONG_WAY_MAX:
        return "scan"
    if M.deflection > tol:
        return "coarse"
    return "clean"


def _coherent(P, flags, rho, k=48):
    """flags of points that most points within rho of them share: lone
    flagged points (noise) drop out, patches (a real wrinkle) stay."""
    if not flags.any():
        return flags
    tree = cKDTree(P)
    _, idx = tree.query(P[flags], k=min(k, len(P)), distance_upper_bound=rho)
    idx = np.atleast_2d(idx)
    valid = idx < len(P)
    hit = np.where(valid, flags[np.minimum(idx, len(P) - 1)], False)
    out = np.zeros_like(flags)
    out[np.nonzero(flags)[0]] = hit.sum(1) > 0.5 * valid.sum(1)
    return out


# at most this many points over the limit are measured again exactly
# (a few milliseconds each)
EXACT_CAP = 2000


def exact_distances(shape, X):
    """Distances from points ``X`` to the shape's faces, worked out on the
    faces themselves rather than on triangles, and each point's nearest
    point on them: (d, nearest)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.gp import gp_Pnt

    faces, lo, hi = [], [], []
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        f = TopoDS.Face(exp.Current())
        box = Bnd_Box()
        BRepBndLib.Add_s(f, box)
        if not box.IsVoid():
            faces.append(f)
            lo.append(box.CornerMin().Coord())
            hi.append(box.CornerMax().Coord())
        exp.Next()
    X = np.asarray(X, dtype=float)
    d = np.full(len(X), np.inf)
    near = X.copy()
    if not faces:
        return d, near
    lo, hi = np.array(lo), np.array(hi)
    for k, x in enumerate(X):
        # faces in order of their boxes' distance, until no box is nearer
        # than the best face so far
        gap = np.linalg.norm(np.maximum(0.0, np.maximum(lo - x, x - hi)), axis=1)
        v = BRepBuilderAPI_MakeVertex(gp_Pnt(*x)).Vertex()
        for i in np.argsort(gap):
            if gap[i] >= d[k]:
                break
            ext = BRepExtrema_DistShapeShape(v, faces[i])
            if ext.IsDone() and ext.NbSolution() and ext.Value() < d[k]:
                d[k] = ext.Value()
                near[k] = ext.PointOnShape2(1).Coord()
    return d, near


class _Faces:
    """The shape's faces, in the order tessellate numbers them, for laying
    points onto their own face's surface."""

    def __init__(self, shape):
        from OCP.TopAbs import TopAbs_FACE
        from OCP.TopExp import TopExp_Explorer
        from OCP.TopoDS import TopoDS

        self.faces = []
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            self.faces.append(TopoDS.Face(exp.Current()))
            exp.Next()
        self._onto = {}

    def project(self, fids, X, prec):
        """Each point laid onto the surface of its face ``fids[i]``: the
        points there and the surface's outward directions."""
        from OCP.BRep import BRep_Tool
        from OCP.GeomLProp import GeomLProp_SLProps
        from OCP.ShapeAnalysis import ShapeAnalysis_Surface
        from OCP.TopAbs import TopAbs_REVERSED
        from OCP.gp import gp_Pnt

        on = np.array(X, dtype=float)
        N = np.zeros_like(on)
        for i, (fi, x) in enumerate(zip(fids, X)):
            if fi not in self._onto:
                f = self.faces[fi]
                self._onto[fi] = (ShapeAnalysis_Surface(BRep_Tool.Surface_s(f)),
                                  -1.0 if f.Orientation() == TopAbs_REVERSED else 1.0)
            onto, sign = self._onto[fi]
            uv = onto.ValueOfUV(gp_Pnt(*map(float, x)), prec)
            props = GeomLProp_SLProps(onto.Surface(), uv.X(), uv.Y(), 1, 1e-9)
            on[i] = props.Value().Coord()
            if props.IsNormalDefined():
                N[i] = np.array(props.Normal().Coord()) * sign
        return on, N


def compare(shape, meshes, tol, n_points=None, seed=0, fine=False):
    """Compares a converted shape with the mesh pieces it came from
    (``meshes``: list of (V, F) with outward-facing triangles).

    Returns a dict:
      to_shape: distances from mesh points to the shape (p50, p99, max)
      to_mesh: distances from shape points to the mesh (p99, max)
      within: share of the mesh surface within ``allow`` of the shape
      wrong_way: share of the mesh surface whose direction differs from
          the shape's by more than WRONG_WAY_DEG
      worst: a point where the shape is furthest from the mesh
      allow: the tolerance the shape is judged by: ``tol``, or more for a
          coarse mesh (a true curve stands off its flat triangles by up to
          the mesh's own chordal deflection) or a noisy one (see mesh_kind)
      mesh_kind: clean, coarse or scan

    ``fine`` cuts the shape into finer triangles (a second check, when a
    result is close to a limit).
    """
    rng = np.random.default_rng(seed)
    if n_points is None:
        # enough points for the 99.9th percentile to mean something, more
        # for detailed meshes
        n_points = int(np.clip(30 * sum(len(F) for _, F in meshes), 30_000, 150_000))
    MV = np.vstack([V for V, _ in meshes])
    off = np.cumsum([0] + [len(V) for V, _ in meshes[:-1]])
    MF = np.vstack([F + o for (_, F), o in zip(meshes, off)])
    diag = float(np.linalg.norm(MV.max(0) - MV.min(0))) if len(MV) else 1.0
    tol = max(float(tol), 1e-9)
    M, MN, spacing = mesh_surface(MV, MF)
    kind = mesh_kind(M, tol)
    allow = max(tol, M.deflection, M.noise)
    SV, SF, owner = tessellate(shape, tol * (0.125 if fine else 0.25), loose=4 * tol)
    if len(SF) == 0:
        return {"empty": True}
    S = Surface(SV, SF, spacing, rng)
    # mesh -> shape: distances and directions
    P, ptri = sample(MV, MF, n_points, rng)
    # (distances under half the tolerance needn't be exact: they count as
    # close either way)
    d_ms, stri = S.nearest(P, exact_above=0.5 * allow)
    # The shape's triangles stand in for its faces, and OpenCASCADE's can
    # leave part of a face uncovered (a printed mount's cylinder came out
    # a third covered): a mesh point there is far from the triangles but
    # on the face. Points over the limit are measured again to the faces
    # themselves, which decides.
    over = np.argsort(-d_ms)[:EXACT_CAP]
    over = over[d_ms[over] > allow]
    missed = np.zeros(len(P), dtype=bool)
    if len(over):
        exact, _ = exact_distances(shape, P[over])
        missed[over] = exact < d_ms[over] - 0.5 * allow
        d_ms[over] = np.minimum(d_ms[over], exact)
    ang = _angle_deg(MN[ptri], S.normals[stri])
    # (the direction of a triangle on the far side of a hole says nothing)
    ang[missed] = 0.0
    # OpenCASCADE's triangles can also cut straight across a curved face
    # while it reports no error (on a printed mount, triangles 4 mm long
    # stood 0.6 mm inside a cylinder), so their directions are off and so
    # is a sample of the shape taken on them. Points that would count
    # against the shape take their direction from the face's own surface.
    faces = _Faces(shape)
    prec = 0.01 * allow
    turn = np.where((ang > WRONG_WAY_DEG) & ~missed)[0][: 5 * EXACT_CAP]
    if len(turn):
        _, N = faces.project(owner[stri[turn]], P[turn], prec)
        ok = np.linalg.norm(N, axis=1) > 0.5
        ang[turn[ok]] = _angle_deg(MN[ptri[turn[ok]]], N[ok])
    # shape -> mesh
    Q, qtri = sample(SV, SF, n_points, rng)
    d_sm, mtri = M.nearest(Q, exact_above=0.5 * allow)
    SN = S.normals[qtri]
    # a sample over the limit that isn't on its face (it lies on such a
    # triangle) is laid onto it and measured from there
    over = np.argsort(-d_sm)[:EXACT_CAP]
    over = over[d_sm[over] > allow]
    if len(over):
        on, N = faces.project(owner[qtri[over]], Q[over], prec)
        off = (np.linalg.norm(on - Q[over], axis=1) > 0.5 * allow) & (np.linalg.norm(N, axis=1) > 0.5)
        if off.any():
            moved = over[off]
            Q = Q.copy()
            SN = SN.copy()
            Q[moved], SN[moved] = on[off], N[off]
            d_sm[moved], mtri[moved] = M.nearest(Q[moved], exact_above=0.0)
    ang_sm = _angle_deg(SN, MN[mtri])
    turn = np.where(ang_sm > WRONG_WAY_DEG)[0][: 5 * EXACT_CAP]
    if len(turn):
        _, N = faces.project(owner[qtri[turn]], Q[turn], prec)
        ok = np.linalg.norm(N, axis=1) > 0.5
        ang_sm[turn[ok]] = _angle_deg(N[ok], MN[mtri[turn[ok]]])
    # a direction that is off only because the point lies right at a sharp
    # edge (its nearest point is on the neighbouring face) doesn't count
    near_edge = d_ms > 0.5 * allow
    wrong = (ang > WRONG_WAY_DEG) & ~(near_edge & (d_ms < 2 * allow))
    wrong_sm = (ang_sm > WRONG_WAY_DEG) & (d_sm < 2 * allow)
    # Two ways of counting, each blind to something: every point that
    # faces the wrong way (lone points sit at sharp edges, where the nearest
    # point of the shape is on the neighbouring face, or on noise-tilted
    # triangles), or only patches about a triangle across (a wrinkle, a
    # dent, a saw-tooth). Patches decide; when the two disagree the verdict
    # is uncertain (see Verdict._certainty).
    raw_ww = max(float(np.mean(wrong)), float(np.mean(wrong_sm))) if len(P) else 0.0
    step = np.sqrt(M.area / max(len(P), 1))
    rho = float(np.clip(M.edge, 2.5 * step, 6.0 * step))
    wrong = _coherent(P, wrong, rho)
    wrong_sm = _coherent(Q, wrong_sm, rho)
    # each spot judged against the mesh's own coarseness there
    loc_ms = d_ms / np.maximum(allow, M.local_dev[ptri])
    loc_sm = d_sm / np.maximum(allow, M.local_dev[mtri])
    local_max = float(max(loc_ms.max() if len(loc_ms) else 0.0, loc_sm.max() if len(loc_sm) else 0.0))
    k = int(np.argmax(d_ms)) if len(d_ms) else 0
    k2 = int(np.argmax(d_sm)) if len(d_sm) else 0
    worst, worst_d = (P[k], float(d_ms[k])) if d_ms[k] >= d_sm[k2] else (Q[k2], float(d_sm[k2]))
    return {
        "empty": False,
        "tol": tol,
        "allow": allow,
        "mesh_kind": kind,
        "noise": M.noise,
        "deflection": M.deflection,
        "n_points": int(len(P)),
        "diag": diag,
        "to_shape": {"p50": float(np.median(d_ms)), "p99": float(np.percentile(d_ms, 99)),
                     "p999": float(np.percentile(d_ms, 99.9)), "max": float(d_ms.max())},
        "to_mesh": {"p99": float(np.percentile(d_sm, 99)), "p999": float(np.percentile(d_sm, 99.9)),
                    "max": float(d_sm.max())},
        "within": float(np.mean(d_ms <= allow)),
        "within2": float(np.mean(np.maximum(d_ms, 0) <= 2 * allow)),
        "wrong_way": float(np.mean(wrong)),
        "wrong_way_shape": float(np.mean(wrong_sm)),
        "wrong_way_points": raw_ww,
        "local_max": local_max,
        "worst": [float(x) for x in worst],
        "worst_distance": worst_d,
        "points": {"mesh": P, "mesh_dist": d_ms, "mesh_angle": ang, "mesh_tri": ptri,
                   "shape": Q, "shape_dist": d_sm, "shape_mesh_tri": mtri},
    }
