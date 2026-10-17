"""Generate a varied library of test STL files.

CAD-like parts are modelled with OpenCASCADE and tessellated (so we know the
true geometry), organic shapes come from implicit surfaces / marching cubes,
and a few files are deliberately broken.
"""
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "models")
os.makedirs(OUT, exist_ok=True)

from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCone,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
    BRepPrimAPI_MakeSphere,
    BRepPrimAPI_MakeTorus,
)
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon, BRepBuilderAPI_MakeFace, BRepBuilderAPI_Transform
from OCP.StlAPI import StlAPI_Writer
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.collections import List_TopoDS_Shape as TopTools_ListOfShape
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec


def ax(x=0, y=0, z=0, dx=0, dy=0, dz=1):
    return gp_Ax2(gp_Pnt(x, y, z), gp_Dir(dx, dy, dz))


def cut(a, b):
    return BRepAlgoAPI_Cut(a, b).Shape()


def fuse(a, b):
    return BRepAlgoAPI_Fuse(a, b).Shape()


def move(s, x, y, z):
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(x, y, z))
    return BRepBuilderAPI_Transform(s, t, True).Shape()


def fillet_all(shape, r, pred=None):
    mk = BRepFilletAPI_MakeFillet(shape)
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    seen = set()
    while exp.More():
        e = TopoDS.Edge(exp.Current())
        exp.Next()
        h = e.__hash__()
        if h in seen:
            continue
        seen.add(h)
        if pred is None or pred(e):
            mk.Add(r, e)
    return mk.Shape()


def save_shape(shape, name, lin=0.05, ang=0.3, ascii=False):
    from OCP.BRepTools import BRepTools

    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, lin, False, ang, True)
    w = StlAPI_Writer()
    w.ASCIIMode = ascii
    path = os.path.join(OUT, name + ".stl")
    w.Write(shape, path)
    print("wrote", name)


def save_mesh(V, F, name):
    import trimesh

    m = trimesh.Trimesh(V, F, process=False)
    m.export(os.path.join(OUT, name + ".stl"))
    print("wrote", name, len(F), "tris")


def edge_z_range(e):
    from OCP.BRepAdaptor import BRepAdaptor_Curve

    c = BRepAdaptor_Curve(e)
    a = c.Value(c.FirstParameter())
    b = c.Value(c.LastParameter())
    return a, b


# ----------------------------------------------------------------------------
# CAD-like parts
# ----------------------------------------------------------------------------


def cad_models():
    box = BRepPrimAPI_MakeBox(40, 30, 20).Shape()
    save_shape(box, "cad_cube")

    # plate with holes and a counterbore
    plate = BRepPrimAPI_MakeBox(80, 50, 8).Shape()
    for (x, y) in [(10, 10), (70, 10), (10, 40), (70, 40)]:
        plate = cut(plate, BRepPrimAPI_MakeCylinder(ax(x, y, -1), 3.2, 10).Shape())
    plate = cut(plate, BRepPrimAPI_MakeCylinder(ax(40, 25, -1), 8, 10).Shape())
    plate = cut(plate, BRepPrimAPI_MakeCylinder(ax(40, 25, 5), 12, 10).Shape())
    save_shape(plate, "cad_plate_holes", lin=0.02, ang=0.2)
    save_shape(plate, "cad_plate_holes_coarse", lin=0.5, ang=0.6)

    # L bracket with fillets
    a = BRepPrimAPI_MakeBox(60, 40, 6).Shape()
    b = BRepPrimAPI_MakeBox(6, 40, 50).Shape()
    br = fuse(a, b)
    br = cut(br, BRepPrimAPI_MakeCylinder(ax(35, 20, -1), 5, 10).Shape())
    br = cut(br, BRepPrimAPI_MakeCylinder(ax(-1, 20, 32, 1, 0, 0), 6, 10).Shape())
    try:
        br = fillet_all(br, 1.5)
    except Exception as e:
        print("fillet failed", e)
    save_shape(br, "cad_bracket_fillets", lin=0.02, ang=0.25)

    # primitives
    save_shape(BRepPrimAPI_MakeCylinder(ax(), 15, 40).Shape(), "cad_cylinder", lin=0.02, ang=0.2)
    save_shape(BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 0), 20).Shape(), "cad_sphere", lin=0.05, ang=0.2)
    save_shape(BRepPrimAPI_MakeCone(ax(), 20, 5, 30).Shape(), "cad_cone", lin=0.02, ang=0.2)
    save_shape(BRepPrimAPI_MakeTorus(ax(), 25, 6).Shape(), "cad_torus", lin=0.05, ang=0.2)

    # gear-ish extrusion
    pts = []
    n_teeth = 18
    for i in range(n_teeth * 4):
        ang = 2 * math.pi * i / (n_teeth * 4)
        r = 30 if (i % 4) in (1, 2) else 26
        pts.append((r * math.cos(ang), r * math.sin(ang)))
    poly = BRepBuilderAPI_MakePolygon()
    for x, y in pts:
        poly.Add(gp_Pnt(x, y, 0))
    poly.Close()
    face = BRepBuilderAPI_MakeFace(poly.Wire()).Face()
    gear = BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, 10)).Shape()
    gear = cut(gear, BRepPrimAPI_MakeCylinder(ax(0, 0, -1), 6, 12).Shape())
    save_shape(gear, "cad_gear", lin=0.02, ang=0.2)

    # mixed boolean part
    m = BRepPrimAPI_MakeBox(gp_Pnt(-20, -20, 0), 40, 40, 15).Shape()
    m = fuse(m, BRepPrimAPI_MakeCylinder(ax(0, 0, 15), 12, 20).Shape())
    m = fuse(m, BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 35), 12).Shape())
    m = cut(m, BRepPrimAPI_MakeCylinder(ax(-25, 0, 7, 1, 0, 0), 4, 50).Shape())
    m = cut(m, BRepPrimAPI_MakeCone(ax(14, 14, -1), 5, 2, 17).Shape())
    save_shape(m, "cad_mixed_boolean", lin=0.03, ang=0.2)

    # shelled enclosure with fillets (open top)
    enc = BRepPrimAPI_MakeBox(60, 40, 25).Shape()
    try:
        enc = fillet_all(enc, 4)
    except Exception as e:
        print("enc fillet failed", e)
    # find top face
    exp = TopExp_Explorer(enc, TopAbs_FACE)
    top = None
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    best = -1e9
    while exp.More():
        f = TopoDS.Face(exp.Current())
        p = GProp_GProps()
        BRepGProp.SurfaceProperties_s(f, p)
        z = p.CentreOfMass().Z()
        if z > best:
            best = z
            top = f
        exp.Next()
    lst = TopTools_ListOfShape()
    lst.Append(top)
    try:
        mk = BRepOffsetAPI_MakeThickSolid()
        mk.MakeThickSolidByJoin(enc, lst, -2.0, 1e-3)
        enc2 = mk.Shape()
        save_shape(enc2, "cad_enclosure_shell", lin=0.03, ang=0.25)
    except Exception as e:
        print("shell failed", e)

    # hollow sphere (cavity -> nested shells)
    outer = BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 0), 20).Shape()
    inner = BRepPrimAPI_MakeSphere(gp_Pnt(0, 0, 0), 15).Shape()
    save_shape(cut(outer, inner), "cad_hollow_sphere", lin=0.1, ang=0.3)

    # two separate bodies
    two = fuse(BRepPrimAPI_MakeBox(10, 10, 10).Shape(), move(BRepPrimAPI_MakeCylinder(ax(), 5, 10).Shape(), 30, 5, 0))
    save_shape(two, "cad_two_bodies", lin=0.02, ang=0.2)

    # chamfered block with perpendicular cross holes
    blk = BRepPrimAPI_MakeBox(gp_Pnt(-15, -15, -15), 30, 30, 30).Shape()
    blk = cut(blk, BRepPrimAPI_MakeCylinder(ax(0, 0, -20), 6, 40).Shape())
    blk = cut(blk, BRepPrimAPI_MakeCylinder(ax(-20, 0, 0, 1, 0, 0), 5, 40).Shape())
    try:
        mk = BRepFilletAPI_MakeChamfer(blk)
        exp = TopExp_Explorer(blk, TopAbs_EDGE)
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Line

        while exp.More():
            e = TopoDS.Edge(exp.Current())
            if BRepAdaptor_Curve(e).GetType() == GeomAbs_Line:
                mk.Add(1.5, e)
            exp.Next()
        blk = mk.Shape()
    except Exception as e:
        print("chamfer failed", e)
    save_shape(blk, "cad_cross_holes_chamfer", lin=0.02, ang=0.2)

    # ascii version
    save_shape(plate, "messy_ascii_plate", lin=0.1, ang=0.4, ascii=True)


def spline_wire(pts, z=0.0):
    """A closed smooth (periodic B-spline) wire through 2D points at height z."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.collections import HArray1_gp_Pnt as TColgp_HArray1OfPnt

    arr = TColgp_HArray1OfPnt(1, len(pts))
    for i, (x, y) in enumerate(pts):
        arr.SetValue(i + 1, gp_Pnt(float(x), float(y), float(z)))
    it = GeomAPI_Interpolate(arr, True, 1e-7)
    it.Perform()
    return BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(it.Curve()).Edge()).Wire()


def loft(wires, ruled=True):
    from OCP.BRepOffsetAPI import BRepOffsetAPI_ThruSections

    mk = BRepOffsetAPI_ThruSections(True, ruled)
    for w in wires:
        mk.AddWire(w)
    mk.Build()
    return mk.Shape()


def sweep_models():
    """Parts whose faces are known swept surfaces: fillets along curved
    edges (a ball rolled along a curve: a pipe) and shapes that shrink as
    they sweep (lines through one point: drafted sides, bevel gears)."""
    # a cam: a smooth outline pulled up, its top edge rounded (the fillet is
    # a pipe around the outline moved in by the radius)
    th = np.linspace(0, 2 * np.pi, 9)[:-1]
    cam = [(18 * np.cos(t) * (1 + 0.25 * np.cos(2 * t)), 12 * np.sin(t) * (1 + 0.15 * np.sin(t))) for t in th]
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace as MkFace

    body = BRepPrimAPI_MakePrism(MkFace(spline_wire(cam)).Face(), gp_Vec(0, 0, 10)).Shape()
    try:
        body = fillet_all(body, 1.5, lambda e: min(edge_z_range(e)[0].Z(), edge_z_range(e)[1].Z()) > 9.9)
    except Exception as e:
        print("cam fillet failed", e)
    save_shape(body, "cad_cam_fillet", lin=0.02, ang=0.2)

    # a pipe through a sloping plate, the joint rounded (the fillet follows
    # the oval where the two meet)
    plate = BRepPrimAPI_MakeBox(gp_Pnt(-25, -25, -3), 50, 50, 3).Shape()
    t = gp_Trsf()
    t.SetRotation(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(1, 0, 0)).Axis(), math.radians(25))
    plate = BRepBuilderAPI_Transform(plate, t, True).Shape()
    tube = fuse(plate, BRepPrimAPI_MakeCylinder(ax(0, 0, -20), 6, 40).Shape())
    tube = cut(tube, BRepPrimAPI_MakeCylinder(ax(0, 0, -25), 4, 50).Shape())

    def joint(e):
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Ellipse

        return BRepAdaptor_Curve(e).GetType() == GeomAbs_Ellipse

    try:
        tube = fillet_all(tube, 2.0, joint)
    except Exception as e:
        print("tube fillet failed", e)
    save_shape(tube, "cad_tube_fillet", lin=0.02, ang=0.2)

    # a boss whose sides lean in (straight lines towards one point above it)
    th = np.linspace(0, 2 * np.pi, 7)[:-1]
    out = np.array([(15 * np.cos(t) + 3 * np.cos(3 * t), 10 * np.sin(t)) for t in th])
    apex, h = np.array([2.0, 1.0]), 12.0
    s = 1 - h / 40.0  # the apex 40 above the base
    top = apex + s * (out - apex)
    save_shape(loft([spline_wire(out), spline_wire(top, h)]), "cad_tapered_boss", lin=0.02, ang=0.2)

    # a straight bevel gear: tooth outline at the back, the same outline
    # shrunk towards the cone's apex at the front
    n, R, tooth = 14, 20.0, 2.2
    pts = []
    for k in range(n):
        for f, r in ((0.0, R - tooth), (0.18, R - tooth), (0.32, R + tooth), (0.5, R + tooth),
                     (0.68, R - tooth + 0.01), (0.82, R - tooth)):
            a = 2 * np.pi * (k + f) / n
            pts.append((r * np.cos(a), r * np.sin(a)))
    h, depth = 8.0, 30.0
    s = 1 - h / depth
    bevel = loft([spline_wire(pts), spline_wire([(s * x, s * y) for x, y in pts], h)])
    bevel = cut(bevel, BRepPrimAPI_MakeCylinder(ax(0, 0, -1), 5, h + 2).Shape())
    save_shape(bevel, "cad_bevel_gear", lin=0.02, ang=0.2)


# ----------------------------------------------------------------------------
# Organic shapes
# ----------------------------------------------------------------------------


def marching(fn, lo, hi, res, level=0.0):
    from skimage.measure import marching_cubes

    xs = [np.linspace(lo[i], hi[i], res) for i in range(3)]
    X, Y, Z = np.meshgrid(*xs, indexing="ij")
    vol = fn(X, Y, Z)
    sp = [(hi[i] - lo[i]) / (res - 1) for i in range(3)]
    V, F, _, _ = marching_cubes(vol, level, spacing=sp)
    V += np.array(lo)
    F = F[:, ::-1]  # outward normals for "inside = positive" fields
    return V, F


def organic_models():
    rng = np.random.default_rng(1)

    # metaballs
    centers = rng.uniform(-15, 15, size=(6, 3))
    radii = rng.uniform(6, 11, size=6)

    def blobs(X, Y, Z):
        s = 0
        for c, r in zip(centers, radii):
            s = s + np.exp(-((X - c[0]) ** 2 + (Y - c[1]) ** 2 + (Z - c[2]) ** 2) / (r * r))
        return s - 0.5

    V, F = marching(blobs, (-35, -35, -35), (35, 35, 35), 90)
    save_mesh(V, F, "organic_metaballs")

    # bumpy sculpt-like sphere (dense)
    def bumpy(X, Y, Z):
        R = np.sqrt(X * X + Y * Y + Z * Z) + 1e-9
        th = np.arccos(np.clip(Z / R, -1, 1))
        ph = np.arctan2(Y, X)
        disp = 2.0 * np.sin(5 * th) * np.cos(4 * ph) + 1.2 * np.sin(9 * ph + 3 * th)
        return (20 + disp) - R

    V, F = marching(bumpy, (-26, -26, -26), (26, 26, 26), 200)
    save_mesh(V, F, "organic_sculpt_dense")

    # torus knot tube
    t = np.linspace(0, 2 * np.pi, 600, endpoint=False)
    p, q = 2, 3
    r = np.cos(q * t) + 2
    curve = np.stack([r * np.cos(p * t), r * np.sin(p * t), -np.sin(q * t)], axis=1) * 10
    T = np.gradient(curve, axis=0)
    T /= np.linalg.norm(T, axis=1)[:, None]
    Nn = np.cross(T, np.array([0, 0, 1.0]))
    Nn /= np.linalg.norm(Nn, axis=1)[:, None]
    Bn = np.cross(T, Nn)
    m = 32
    a = np.linspace(0, 2 * np.pi, m, endpoint=False)
    rad = 3.0
    Vt = (curve[:, None, :] + rad * (np.cos(a)[None, :, None] * Nn[:, None, :] + np.sin(a)[None, :, None] * Bn[:, None, :])).reshape(-1, 3)
    n = len(t)
    Ft = []
    for i in range(n):
        for j in range(m):
            i2, j2 = (i + 1) % n, (j + 1) % m
            v00, v01, v10, v11 = i * m + j, i * m + j2, i2 * m + j, i2 * m + j2
            Ft.append([v00, v10, v11])
            Ft.append([v00, v11, v01])
    Ft = np.array(Ft)
    save_mesh(Vt, Ft, "organic_torus_knot")

    # gyroid lattice clipped to a sphere (high genus)
    def gyroid(X, Y, Z):
        k = 2 * np.pi / 12
        g = np.sin(k * X) * np.cos(k * Y) + np.sin(k * Y) * np.cos(k * Z) + np.sin(k * Z) * np.cos(k * X)
        sph = 22 - np.sqrt(X * X + Y * Y + Z * Z)
        return np.minimum(0.6 - np.abs(g), sph)

    V, F = marching(gyroid, (-25, -25, -25), (25, 25, 25), 110)
    save_mesh(V, F, "organic_gyroid")

    # noisy scan-like sphere
    import trimesh

    s = trimesh.creation.icosphere(subdivisions=5, radius=20)
    Vn = s.vertices + rng.normal(0, 0.05, size=s.vertices.shape)
    save_mesh(Vn, s.faces, "scan_noisy_sphere")


def scan_of_cad():
    """A CAD part re-meshed isotropically with noise, like a 3D scan."""
    import trimesh

    path = os.path.join(OUT, "cad_plate_holes.stl")
    m = trimesh.load(path)
    # subdivide to make dense isotropic-ish triangles
    v, f = m.vertices, m.faces
    v, f = trimesh.remesh.subdivide_to_size(v, f, max_edge=0.8)
    rng = np.random.default_rng(3)
    v = v + rng.normal(0, 0.01, size=v.shape)
    save_mesh(v, f, "scan_plate_noisy")


# ----------------------------------------------------------------------------
# Broken / messy meshes
# ----------------------------------------------------------------------------


def messy_models():
    import trimesh

    box = trimesh.creation.box((20, 20, 20))
    # open: remove two faces (one quad side)
    f = box.faces[2:]
    save_mesh(box.vertices, f, "messy_open_box")

    # flipped + duplicated faces
    s = trimesh.creation.icosphere(subdivisions=3, radius=10)
    F = s.faces.copy()
    rng = np.random.default_rng(5)
    flip = rng.random(len(F)) < 0.3
    F[flip] = F[flip][:, ::-1]
    F = np.vstack([F, F[:20]])
    save_mesh(s.vertices, F, "messy_flipped_dupes")

    # non-manifold: two cubes sharing an edge
    a = trimesh.creation.box((10, 10, 10))
    b = trimesh.creation.box((10, 10, 10))
    b.apply_translation((10, 10, 0))
    V = np.vstack([a.vertices, b.vertices])
    F = np.vstack([a.faces, b.faces + len(a.vertices)])
    save_mesh(V, F, "messy_nonmanifold_edge")

    # overlapping intersecting bodies (common in downloaded models)
    c = trimesh.creation.cylinder(radius=5, height=30, sections=48)
    d = trimesh.creation.box((20, 20, 8))
    V = np.vstack([c.vertices, d.vertices])
    F = np.vstack([c.faces, d.faces + len(c.vertices)])
    save_mesh(V, F, "messy_intersecting_bodies")

    # mesh with a large hole (scan-like open bottom)
    s = trimesh.creation.icosphere(subdivisions=4, radius=15)
    keep = s.triangles_center[:, 2] > -8
    save_mesh(s.vertices, s.faces[keep], "messy_open_scan_bowl")


def big_models():
    def blobs(X, Y, Z):
        return 18 - np.sqrt(X * X + Y * Y + Z * Z) + 1.5 * np.sin(X / 3) * np.cos(Y / 4) * np.sin(Z / 5)

    V, F = marching(blobs, (-22, -22, -22), (22, 22, 22), 420)
    save_mesh(V, F, "huge_scan_blob")


if __name__ == "__main__":
    which = sys.argv[1:] or ["cad", "sweeps", "organic", "scan", "messy", "big"]
    if "cad" in which:
        cad_models()
    if "sweeps" in which:
        sweep_models()
    if "organic" in which:
        organic_models()
    if "scan" in which:
        scan_of_cad()
    if "messy" in which:
        messy_models()
    if "big" in which:
        big_models()
