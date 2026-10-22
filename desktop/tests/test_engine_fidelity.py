"""How closely converted shapes follow their meshes (engine/mmm/fidelity.py),
and the conversion choices built on it (engine/mmm/pipeline.py, cad.py)."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
trimesh = pytest.importorskip("trimesh")
m3 = pytest.importorskip("manifold3d")

from mmm import fidelity  # noqa: E402
from mmm.meshio import load_and_prepare  # noqa: E402
from mmm.report import NULL  # noqa: E402


def test_point_triangle_distance_matches_reference():
    rng = np.random.default_rng(1)
    n = 20000
    A, B, C = rng.normal(size=(3, n, 3))
    B[:100] = A[:100]  # squashed to a line
    B[100:200] = A[100:200]
    C[100:200] = A[100:200]  # squashed to a point
    C[200:300] = A[200:300] + 2 * (B[200:300] - A[200:300])  # three corners in a row
    P = rng.normal(size=(n, 3)) * 2
    d, Q = fidelity.point_triangle_distance(P, A, B, C)
    ref = trimesh.triangles.closest_point(np.stack([A, B, C], 1), P)
    assert np.abs(d - np.linalg.norm(P - ref, axis=1)).max() < 1e-9
    assert np.allclose(np.linalg.norm(P - Q, axis=1), d)


@pytest.mark.parametrize("sections", [16, 96])
def test_distances_are_exact_next_to_long_thin_triangles(sections):
    """The quick nearest-triangle guess missed the closest triangle next to
    long, thin ones (a cylinder meshed in strips): a point 0.016 mm from
    the shape measured 0.18 mm. Every distance must match measuring all
    triangles."""
    cyl = trimesh.creation.cylinder(radius=0.9, height=30.0, sections=sections)
    V, F = cyl.vertices, cyl.faces
    rng = np.random.default_rng(2)
    surf = fidelity.Surface(V, F, 0.3, rng)
    P, _ = fidelity.sample(V, F, 3000, rng)
    P = P + rng.normal(size=P.shape) * 0.2
    d, _ = surf.nearest(P)
    A, B, C = (np.repeat(V[F[:, k]][None], len(P), 0).reshape(-1, 3) for k in range(3))
    ref, _ = fidelity.point_triangle_distance(np.repeat(P, len(F), 0), A, B, C)
    ref = ref.reshape(len(P), len(F)).min(1)
    assert np.abs(d - ref).max() < 1e-9


def test_measured_gaps_match_the_exact_distance_to_the_shape(tmp_path):
    """The largest gaps the check reports are real: the same points
    measured exactly against the shape (not its triangles) give the same
    distances, within the fineness the shape is cut into."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt
    from OCP.ShapeFix import ShapeFix_ShapeTolerance

    shape = _hollow_sphere()
    ShapeFix_ShapeTolerance().SetTolerance(shape, 0.05)  # a loose fit tolerance, as converted faces have
    path = _stl(_hollow_sphere(), str(tmp_path / "s.stl"), 0.2, 0.5)
    m = trimesh.load(path)
    tol = 0.05
    r = fidelity.compare(shape, [(m.vertices, m.faces)], tol)
    pts = r["points"]
    for X, D in ((pts["mesh"], pts["mesh_dist"]), (pts["shape"], pts["shape_dist"])):
        for i in np.argsort(-D)[:5]:
            if X is pts["shape"]:
                exact = float(np.min(fidelity.point_triangle_distance(
                    np.repeat(X[i:i + 1], len(m.faces), 0), *(m.vertices[m.faces[:, k]] for k in range(3)))[0]))
            else:
                v = BRepBuilderAPI_MakeVertex(gp_Pnt(*map(float, X[i]))).Vertex()
                exact = BRepExtrema_DistShapeShape(v, shape).Value()
            assert abs(D[i] - exact) <= 0.25 * tol + 1e-6


def _box_mesh(size):
    m = trimesh.creation.box(extents=size)
    m.apply_translation(np.array(size) / 2)
    return m.vertices, m.faces


def _box_shape(size):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(*map(float, size)).Shape()


def test_a_shape_that_matches_its_mesh_passes():
    V, F = _box_mesh((10, 8, 6))
    v = fidelity.Verdict(_strip(fidelity.compare(_box_shape((10, 8, 6)), [(V, F)], 0.01)), 0.01)
    assert v.ok
    assert v.r["to_shape"]["max"] < 1e-6 and v.r["to_mesh"]["max"] < 1e-6 and v.r["wrong_way"] == 0


def test_a_shape_that_strays_from_its_mesh_fails():
    # half a millimetre too tall: the whole top face is off, in both directions
    V, F = _box_mesh((10, 8, 6))
    r = _strip(fidelity.compare(_box_shape((10, 8, 6.5)), [(V, F)], 0.01))
    v = fidelity.Verdict(r, 0.01)
    assert not v.ok
    assert abs(r["worst_distance"] - 0.5) < 0.05
    assert "largest gap 0.5 mm" in v.summary()


def test_a_shape_with_a_missing_feature_fails():
    # a mesh with a 1 mm boss the shape doesn't have
    box = m3.Manifold.cube([10, 8, 6]) + m3.Manifold.cube([3, 3, 1]).translate([3, 2, 6])
    mesh = box.to_mesh()
    V, F = np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts)
    r = _strip(fidelity.compare(_box_shape((10, 8, 6)), [(V, F)], 0.01))
    assert not fidelity.Verdict(r, 0.01).ok
    assert r["to_shape"]["max"] > 0.9  # mesh points the shape doesn't cover


def test_a_hole_in_the_checkers_own_triangles_is_not_a_gap(monkeypatch):
    """OpenCASCADE's triangles can leave part of a face uncovered (a
    printed mount's cylinder came out a third covered), and mesh points
    there looked 0.6 mm from a shape they were on. Points over the limit
    are measured again on the faces themselves."""
    V, F = _box_mesh((10, 8, 6))
    real = fidelity.tessellate

    def holed(shape, deflection, angle=0.25):
        SV, SF, owner = real(shape, deflection, angle)
        keep = owner != 0  # one whole face left out
        return SV, SF[keep], owner[keep]

    monkeypatch.setattr(fidelity, "tessellate", holed)
    r = _strip(fidelity.compare(_box_shape((10, 8, 6)), [(V, F)], 0.01, n_points=3000))
    # (points within the tolerance aren't measured again: those next to
    # the hole may read up to it)
    assert r["to_shape"]["max"] <= 0.01 and r["wrong_way"] == 0
    assert fidelity.Verdict(r, 0.01).ok


def test_triangles_that_cut_across_a_curved_face_are_split_along_it():
    """Asked for a fine mesh, OpenCASCADE kept triangles 4 mm long that cut
    0.6 mm inside a cylinder, reporting no error. Each face whose mesh
    missed the deflection asked for is split along its surface."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
    from OCP.GeomAbs import GeomAbs_Cylinder
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    shape = BRepPrimAPI_MakeCylinder(5.0, 10.0).Shape()
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while BRepAdaptor_Surface(TopoDS.Face(exp.Current())).GetType() != GeomAbs_Cylinder:
        exp.Next()
    face = TopoDS.Face(exp.Current())
    BRepMesh_IncrementalMesh(face, 0.5, False, 1.0, True)  # coarse on purpose
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    assert fidelity._follow_surface(face, tri, loc, 1.0) is None  # good enough as it is
    V, T = fidelity._follow_surface(face, tri, loc, 0.01)
    C = V[T].mean(1)
    assert np.abs(np.hypot(C[:, 0], C[:, 1]) - 5.0).max() < 0.01
    assert np.abs(np.hypot(V[:, 0], V[:, 1]) - 5.0).max() < 1e-9  # corners on the surface
    # the same area as the face, so no part is covered twice or left out
    a = 0.5 * np.linalg.norm(np.cross(V[T[:, 1]] - V[T[:, 0]], V[T[:, 2]] - V[T[:, 0]]), axis=1).sum()
    assert abs(a - 2 * np.pi * 5.0 * 10.0) < 0.01 * a


def test_exact_distances_to_the_faces():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    P = np.array([[0.0, 0.0, 7.0], [1.0, 1.0, 1.0], [0.0, 0.0, 0.0]])
    d, near = fidelity.exact_distances(BRepPrimAPI_MakeSphere(4.0).Shape(), P)
    # to the faces, not the solid: a point inside is as far as the surface
    assert np.allclose(d, [3.0, 4.0 - np.sqrt(3.0), 4.0], atol=1e-6)
    assert np.allclose(np.linalg.norm(near[:2], axis=1), 4.0, atol=1e-6)


def _strip(r):
    r.pop("points", None)
    return r


def test_faces_with_a_loose_tolerance_are_measured_accurately():
    """OpenCASCADE meshes a face no finer than its tolerance: a converted
    sphere (tolerance 0.02 mm) came out up to 0.03 mm inside its surface
    when 0.005 mm was asked for, so correct faces seemed to miss the mesh."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere
    from OCP.ShapeFix import ShapeFix_ShapeTolerance

    shape = BRepPrimAPI_MakeSphere(15.0).Shape()
    ShapeFix_ShapeTolerance().SetTolerance(shape, 0.02)
    V, F, _ = fidelity.tessellate(shape, 0.005)
    inside = 15.0 - np.linalg.norm(V[F].mean(1), axis=1)
    assert inside.max() < 0.01


def _stl(shape, path, lin, ang):
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.StlAPI import StlAPI_Writer

    BRepMesh_IncrementalMesh(shape, lin, False, ang, True)
    StlAPI_Writer().Write(shape, path)
    return path


def _hollow_sphere():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    return BRepAlgoAPI_Cut(BRepPrimAPI_MakeSphere(20.0).Shape(), BRepPrimAPI_MakeSphere(15.0).Shape()).Shape()


def _holed_plate():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    plate = BRepPrimAPI_MakeBox(80, 50, 8).Shape()
    for x, y, z, r in ((10, 10, -1, 3.2), (70, 10, -1, 3.2), (10, 40, -1, 3.2), (70, 40, -1, 3.2),
                       (40, 25, -1, 8), (40, 25, 5, 12)):
        hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, z), gp_Dir(0, 0, 1)), r, 10).Shape()
        plate = BRepAlgoAPI_Cut(plate, hole).Shape()
    return plate


def _open_bowl(path):
    s = trimesh.creation.icosphere(subdivisions=4, radius=15)
    s.update_faces(s.triangles_center[:, 2] > -8)
    s.export(path)
    return path


def test_the_face_check_finds_the_face_that_misses_the_mesh(tmp_path):
    """The CAD fitter's own safety net: a face that is valid but stands off
    its part of the mesh (a surface bulging between its edges, as on 1.5.1's
    saw-tooth liner) is found, and the faces that follow the mesh aren't."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform
    from OCP.gp import gp_Trsf, gp_Vec
    from OCP.TopoDS import TopoDS

    from mmm import cad
    from mmm.pipeline import auto_tolerance

    bodies, _ = load_and_prepare(_stl(_small_holed_plate(), str(tmp_path / "p.stl"), 0.02, 0.2), report=NULL)
    sh = bodies[0][0]
    tol = auto_tolerance(float(np.linalg.norm(sh.V.max(0) - sh.V.min(0))), "cad", 0.9)
    seg = cad.Segmenter(sh.V, sh.F, sh.topo, tol, NULL)
    regions = seg.run()
    labels, regions = cad.split_pinched_regions(seg, seg.label.copy(), regions)
    asm = cad.CADAssembler(sh.V, cad.RegionGraph(sh.V, sh.F, labels, sh.topo), regions, tol, NULL)
    asm.build(lambda f: None)
    assert cad._inaccurate_regions(seg, labels, asm, tol) == set()
    # the top face, 0.3 mm too high
    planes = [r for r in asm.region_faces if regions[r].kind == "plane"]
    top = max(planes, key=lambda r: float(seg.fa[regions[r].faces].sum()))
    t = gp_Trsf()
    t.SetTranslation(gp_Vec(*(0.3 * regions[top].prim.n)))
    asm.region_faces[top] = TopoDS.Face(BRepBuilderAPI_Transform(asm.region_faces[top], t, True).Shape())
    assert cad._inaccurate_regions(seg, labels, asm, tol) == {top}


@pytest.mark.parametrize("model,max_faces", [("hollow sphere", 2), ("coarse plate", 13), ("open bowl", 100)])
def test_coarse_meshes_keep_their_true_surfaces(tmp_path, model, max_faces):
    """A true sphere or cylinder stands off the flat sides of a coarse mesh,
    and that is right: checking such faces as if the mesh were exact broke
    them into hundreds of patches and flat faces (a hollow sphere: 501
    faces instead of 2)."""
    from mmm.pipeline import Options, convert

    path = str(tmp_path / "model.stl")
    if model == "hollow sphere":
        _stl(_hollow_sphere(), path, 0.1, 0.3)
    elif model == "coarse plate":
        _stl(_holed_plate(), path, 0.5, 0.6)
    else:
        _open_bowl(path)
    res = convert(path, str(tmp_path / "model.step"), Options(preset="auto", quality=0.9), NULL)
    assert res["preset"] == "cad" and res["valid"]
    assert res["faces"] <= max_faces
    assert res["fidelity"]["ok"] and res["fidelity"]["within2"] > 0.99


def grooved_liner(path):
    """A ring with 12 grooves inside (cone-shaped ends) and 6 tabs outside,
    tilted 20 degrees: like an OpenSCAD part, its grooves are a few long,
    thin facets that meet small triangles at a shallow crease."""
    def cyl(r, h, n, x=0.0, y=0.0):
        return m3.Manifold.cylinder(h, r, r, n).translate([x, y, 0])

    ring = cyl(10.0, 18.0, 96) - cyl(8.6, 18.0, 96)
    for k in range(12):
        a = 2 * np.pi * k / 12
        x, y = 8.6 * np.cos(a), 8.6 * np.sin(a)
        g = cyl(0.9, 12.0, 16, x, y).translate([0, 0, 3.0])
        g = g + m3.Manifold.cylinder(1.35, 0.0, 0.9, 16).translate([x, y, 1.65])
        g = g + m3.Manifold.cylinder(1.35, 0.9, 0.0, 16).translate([x, y, 15.0])
        ring = ring - g
    for k in range(6):
        tab = m3.Manifold.cube([1.6, 2.4, 14.0], True).translate([10.5, 0, 9.0])
        ring = ring + tab.rotate([0, 0, np.degrees(2 * np.pi * k / 6 + 0.26)])
    mesh = ring.to_mesh()
    t = trimesh.Trimesh(np.asarray(mesh.vert_properties)[:, :3], np.asarray(mesh.tri_verts), process=False)
    t.apply_transform(trimesh.transformations.rotation_matrix(np.radians(20), [1, 0, 0]))
    t.export(path)
    return path


def test_creases_dont_count_as_coarse_curves(tmp_path):
    """The groove ends' crease made the CAD fitter think the mesh was
    faceted 30x more coarsely than it is, so it accepted surfaces that miss
    the mesh between its vertices (the saw-tooth ribs of a TPU liner)."""
    from mmm.cad import Segmenter
    from mmm.pipeline import auto_tolerance

    bodies, _ = load_and_prepare(grooved_liner(str(tmp_path / "liner.stl")), report=NULL)
    sh = bodies[0][0]
    tol = auto_tolerance(float(np.linalg.norm(sh.V.max(0) - sh.V.min(0))), "cad", 0.9)
    seg = Segmenter(sh.V, sh.F, sh.topo, tol, NULL)
    # the grooves' 16-sided cylinders are faceted to about 0.017 mm
    assert seg.deflection < 0.03
    assert seg.tol_mid < 0.3


def test_cad_conversion_follows_the_mesh(tmp_path):
    from OCP.BRep import BRep_Builder

    from mmm.cad import convert_body_cad
    from mmm.pipeline import Options, auto_tolerance

    bodies, _ = load_and_prepare(grooved_liner(str(tmp_path / "liner.stl")), report=NULL)
    body = bodies[0]
    P = np.vstack([s.V for s in body])
    tol = auto_tolerance(float(np.linalg.norm(P.max(0) - P.min(0))), "cad", 0.9)
    shape = convert_body_cad(BRep_Builder(), body, tol, Options(quality=0.9), NULL)
    v = fidelity.judge(shape, body, tol)
    assert v.ok, v.summary()
    assert v.r["to_shape"]["max"] < 6 * tol and v.r["to_mesh"]["max"] < 6 * tol


def _noisy_sphere(r=10.0, sigma=0.03, seed=4):
    s = trimesh.creation.icosphere(subdivisions=4, radius=r)
    V = s.vertices + np.random.default_rng(seed).normal(0, sigma, size=s.vertices.shape)
    return V, s.faces


def _sphere_shape(r):
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeSphere

    return BRepPrimAPI_MakeSphere(float(r)).Shape()


def test_noise_is_measured_on_scans_but_not_on_small_features(tmp_path):
    """Scan noise is measured against a smooth patch through each vertex's
    neighbours. A clean part's small grooves and ribs are not noise (an
    earlier way of measuring took a TPU liner's grooves for 0.14 mm of
    noise, which would have let wrong surfaces through on exactly the part
    that had them)."""
    V, F = _noisy_sphere(sigma=0.03)
    noise = fidelity.mesh_noise(V, F)
    assert 0.5 * 0.03 < noise < 1.0 * 0.03
    s = trimesh.creation.icosphere(subdivisions=4, radius=10.0)
    assert fidelity.mesh_noise(s.vertices, s.faces) < 0.003
    bodies, _ = load_and_prepare(grooved_liner(str(tmp_path / "liner.stl")), report=NULL)
    sh = bodies[0][0]
    assert fidelity.mesh_noise(sh.V, sh.F) < 0.002


def test_each_kind_of_mesh_has_its_own_rules():
    from mmm.fidelity import mesh_kind, mesh_surface

    V, F = _box_mesh((10, 8, 6))
    assert mesh_kind(mesh_surface(V, F)[0], 0.01) == "clean"
    c = trimesh.creation.cylinder(radius=10.0, height=5.0, sections=16)  # sides 0.19 mm off the circle
    assert mesh_kind(mesh_surface(c.vertices, c.faces)[0], 0.02) == "coarse"
    V, F = _noisy_sphere(sigma=0.03)
    assert mesh_kind(mesh_surface(V, F)[0], 0.015) == "scan"


def test_a_scan_is_judged_by_its_true_shape_not_its_noise():
    # the noise (2 tolerances) is no reason to reject the true sphere...
    V, F = _noisy_sphere(sigma=0.03)
    mesh = [(V, F)]
    tol = 0.015
    r = _strip(fidelity.compare(_sphere_shape(10.0), mesh, tol))
    assert r["mesh_kind"] == "scan" and fidelity.Verdict(r, r["allow"]).ok
    # ...but a sphere a tenth of a millimetre off still fails
    r = _strip(fidelity.compare(_sphere_shape(10.1), mesh, tol))
    assert not fidelity.Verdict(r, r["allow"]).ok


def _small_holed_plate():
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakeCylinder
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    plate = BRepPrimAPI_MakeBox(40, 25, 4).Shape()
    for x, y, r in ((8, 8, 2.5), (32, 17, 4.0)):
        hole = BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt(x, y, -1), gp_Dir(0, 0, 1)), r, 6).Shape()
        plate = BRepAlgoAPI_Cut(plate, hole).Shape()
    return plate


def test_thin_noisy_triangles_dont_count_as_wrinkles(tmp_path):
    """A CAD mesh (long slivers around the holes) cut into small pieces
    and given 0.01 mm of noise, like a scanned part: the noise turns the
    slivers' directions every which way and flips some over, so 3.7% of the
    perfect plate seemed to face the wrong way. Lone points don't count on
    such a mesh, only patches."""
    from mmm.fidelity import mesh_surface

    m = trimesh.load(_stl(_small_holed_plate(), str(tmp_path / "p.stl"), 0.02, 0.2))
    V, F = trimesh.remesh.subdivide_to_size(m.vertices, m.faces, max_edge=0.8)
    V = V + np.random.default_rng(3).normal(0, 0.01, size=V.shape)
    assert mesh_surface(V, F)[0].thin > fidelity.Verdict.WRONG_WAY_MAX
    r = _strip(fidelity.compare(_small_holed_plate(), [(V, F)], 0.02))
    assert r["mesh_kind"] == "scan"
    assert max(r["wrong_way"], r["wrong_way_shape"]) <= fidelity.Verdict.WRONG_WAY_MAX


def test_a_verdict_near_its_limit_is_checked_again():
    """The whole top face 0.03 mm too high, judged at 0.01 mm: right at the
    3-tolerance limit, so a second, finer check with more points decides,
    and the user is told why it takes longer."""
    V, F = _box_mesh((10, 8, 6.03))
    told = []
    v = fidelity.judge(_box_shape((10, 8, 6)), [_Shell(V, F)], 0.01, on_recheck=told.append)
    assert v.rechecked and told == ["it is close to the limit"]
    assert v.r["n_points"] > 30_000
    # a clear verdict is not checked twice
    told.clear()
    V, F = _box_mesh((10, 8, 6))
    v = fidelity.judge(_box_shape((10, 8, 6)), [_Shell(V, F)], 0.01, on_recheck=told.append)
    assert v.ok and not v.rechecked and not told and v.certainty == 1.0


class _Shell:
    def __init__(self, V, F):
        self.V, self.F = V, F


def _cand(kind, valid, ok, score):
    from mmm.pipeline import _Candidate

    class V:
        pass

    v = V()
    v.ok, v.score = ok, score
    return _Candidate(None, kind, valid, v)


def test_following_the_mesh_beats_being_valid():
    from mmm.pipeline import _best

    cad_small_errors = _cand("cad", False, True, -0.8)
    smooth_wrinkled = _cand("smooth", True, False, -7.5)
    assert _best([cad_small_errors, smooth_wrinkled]) is cad_small_errors
    clean = _cand("smooth", True, True, -2.0)
    assert _best([cad_small_errors, smooth_wrinkled, clean]) is clean
    # when nothing follows the mesh, the closest one wins
    closer = _cand("smooth", True, False, -4.0)
    assert _best([smooth_wrinkled, closer]) is closer


def test_conversion_reports_how_closely_it_follows(tmp_path):
    from mmm.pipeline import Options, convert

    V, F = _box_mesh((20, 10, 5))
    path = str(tmp_path / "box.stl")
    trimesh.Trimesh(V, F).export(path)
    res = convert(path, str(tmp_path / "box.step"), Options(preset="auto", quality=0.7), NULL)
    fid = res["fidelity"]
    assert fid["ok"] and fid["within2"] > 0.999 and fid["max"] < 1e-3


class _FakeVerdict(fidelity.Verdict):
    def __init__(self, score, certainty):
        self.r, self.tol, self.ok, self.score = {}, 0.01, True, score
        self.certainty, self.doubt, self.rechecked = certainty, "it is close to the limit", True

    def summary(self, unit="mm"):
        return "test"

    def as_dict(self):
        return {"ok": True, "tolerance": 0.01, "within": 1.0, "within2": 1.0, "p99": 0.0, "max": 0.0,
                "worst": [0, 0, 0], "wrong_way": 0.0, "mesh_kind": "clean", "certainty": self.certainty,
                "rechecked": True}


@pytest.mark.parametrize("other_score,kept", [(-2.0, "cad"), (-1.0, "smooth")])
def test_a_result_that_only_just_passes_is_compared_with_another_method(tmp_path, monkeypatch, other_score, kept):
    """CAD faces that pass, but only just (even after the second check):
    smooth surfaces are tried too. The CAD faces stay unless the smooth
    surfaces are clearly closer, and the user is told what is going on."""
    from mmm.pipeline import Options, convert
    from mmm.report import Reporter

    calls = []

    def fake_judge(shape, shells, tol, n_points=None, recheck=True, on_recheck=None):
        calls.append(1)
        if len(calls) == 1:
            on_recheck("it is close to the limit")
            return _FakeVerdict(-2.5, 0.3)
        return _FakeVerdict(other_score, 1.0)

    monkeypatch.setattr(fidelity, "judge", fake_judge)

    class Rec(Reporter):
        def __init__(self):
            super().__init__(quiet=True)
            self.said = []

        def _emit(self, obj):
            self.said.append(obj.get("stage") or obj.get("message") or "")

    V, F = _box_mesh((20, 10, 5))
    path = str(tmp_path / "box.stl")
    trimesh.Trimesh(V, F).export(path)
    rep = Rec()
    res = convert(path, str(tmp_path / "box.step"), Options(preset="auto", quality=0.7), rep)
    assert len(calls) == 2 and res["preset"] == kept
    assert "Checking again, more closely: it is close to the limit" in rep.said
    assert any("too (the first result only just passed)" in t for t in rep.said)


def test_when_nothing_follows_the_mesh_both_versions_are_offered(tmp_path, monkeypatch):
    """No method follows the mesh, and the closest result's file has errors:
    it is saved, and the valid (less close) one is saved next to it, so
    the user can choose."""
    from mmm import stepio
    from mmm.pipeline import Options, convert

    class Bad(_FakeVerdict):
        def __init__(self, score):
            super().__init__(score, 1.0)
            self.ok = False

        def as_dict(self):
            return {**super().as_dict(), "ok": False}

    scores = iter([-5.0, -9.0, -9.0, -9.0])
    monkeypatch.setattr(fidelity, "judge", lambda *a, **k: Bad(next(scores, -9.0)))
    out = str(tmp_path / "box.step")
    real = stepio.verify_step

    def verify(path, **kw):
        r = real(path, **kw)
        # the closest version's file has errors no repair fixes (the part
        # checked on its own, and the valid version, are fine)
        if not path.endswith(".valid.step") and "mmm-part-" not in os.path.basename(path):
            r = {**r, "ok": False, "issues": r["issues"] + [{"code": "bad_faces", "level": "error", "message": "x"}]}
        return r

    monkeypatch.setattr(stepio, "verify_step", verify)
    V, F = _box_mesh((20, 10, 5))
    path = str(tmp_path / "box.stl")
    trimesh.Trimesh(V, F).export(path)
    res = convert(path, out, Options(preset="auto", quality=0.7, preview=str(tmp_path / "box.mmmp")), NULL)
    alt = res["alternative"]
    assert alt and alt["output"].endswith("box.valid.step") and os.path.exists(alt["output"])
    assert alt["valid"] and os.path.exists(alt["preview"])
    assert alt["fidelity"]["ok"] is False


def test_reading_reports_each_step(tmp_path):
    from mmm.report import Reporter

    class Rec(Reporter):
        def __init__(self):
            super().__init__(quiet=True)
            self.stages = []

        def stage(self, name, lo, hi):
            self.stages.append((name, lo))
            super().stage(name, lo, hi)

    V, F = _box_mesh((20, 10, 5))
    path = str(tmp_path / "box.stl")
    trimesh.Trimesh(V, F).export(path)
    rep = Rec()
    with rep.section(0.0, 0.5):
        load_and_prepare(path, report=rep)
    names = [n for n, _ in rep.stages]
    assert names[0] == "Reading the file" and "Looking for hidden internal walls" in names
    assert names[-1] == "Checking the fixed mesh again"
    progress = [p for _, p in rep.stages]
    assert progress == sorted(progress) and progress[-1] <= 0.5


def test_nothing_is_reported_when_not_checked(tmp_path):
    from mmm.pipeline import Options, convert

    V, F = _box_mesh((20, 10, 5))
    path = str(tmp_path / "box.stl")
    trimesh.Trimesh(V, F).export(path)
    res = convert(path, str(tmp_path / "box.step"), Options(quality=0.7, validate=False), NULL)
    assert res["fidelity"] is None


def test_a_blown_up_face_is_found_and_doesnt_hang_the_check():
    """A freeform patch from a scanned plate whose surface blew up (an area
    of about 10^40 mm^2 inside a few millimetres of edges) passed the
    validity check, and meshing it finely never finished: a conversion
    hung for an hour. It is recognised, and measured coarsely instead."""
    import time

    from OCP.BRep import BRep_Builder
    from OCP.BRepTools import BRepTools
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Shape

    shape = TopoDS_Shape()
    BRepTools.Read_s(shape, os.path.join(os.path.dirname(__file__), "data", "blown_up_face.brep"), BRep_Builder())
    assert fidelity.face_blown_up(TopoDS.Face(TopExp_Explorer(shape, TopAbs_FACE).Current()))
    t = time.time()
    fidelity.tessellate(shape, 0.0116)
    assert time.time() - t < 10
    # an ordinary face isn't
    box = _box_shape((10, 8, 6))
    assert not fidelity.face_blown_up(TopoDS.Face(TopExp_Explorer(box, TopAbs_FACE).Current()))
    assert not fidelity.face_blown_up(TopoDS.Face(TopExp_Explorer(_sphere_shape(5.0), TopAbs_FACE).Current()))


def test_checking_leaves_no_fine_mesh_on_the_shape():
    # the 3D preview meshes the shape again and would reuse a finer mesh
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS

    V, F = _box_mesh((10, 8, 6))
    shape = _box_shape((10, 8, 6))
    fidelity.compare(shape, [(V, F)], 0.01)
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        assert BRep_Tool.Triangulation_s(TopoDS.Face(exp.Current()), TopLoc_Location()) is None
        exp.Next()


@pytest.mark.parametrize("exc", [MemoryError, IndexError])
def test_converter_faults_are_not_blamed_on_the_file(tmp_path, monkeypatch, exc):
    """Out of memory, or a bug in the converter, stays what it is (the
    error report says so) instead of "None of the parts could be converted"."""
    from mmm import cad, pipeline, smooth
    from mmm.pipeline import Options, convert

    def boom(*a, **kw):
        raise exc("test")

    monkeypatch.setattr(cad, "convert_body_cad", boom)
    monkeypatch.setattr(smooth, "convert_body_smooth", boom)
    monkeypatch.setattr(pipeline, "convert_body_exact", boom)
    V, F = _box_mesh((20, 10, 5))
    path = str(tmp_path / "box.stl")
    trimesh.Trimesh(V, F).export(path)
    with pytest.raises(exc):
        convert(path, str(tmp_path / "box.step"), Options(quality=0.7), NULL)


def test_points_are_laid_onto_each_kind_of_surface():
    from mmm import cad
    from mmm.fitting import Cone, Cylinder, Plane, Sphere, Torus

    rng = np.random.default_rng(3)
    P = rng.normal(size=(200, 3)) * 5 + [7.0, 0.0, 0.0]
    for prim in (Plane([0, 0, 1], [0.2, 0.1, 1]), Cylinder([1, 2, 0], [0, 0.3, 1], 4.0), Sphere([0, 1, 2], 6.0),
                 Torus([0, 0, 0], [0, 0, 1], 8.0, 2.5), Cone([0, 0, -5], [0, 0, 1], np.radians(30))):
        # (points near the surface: an edge's points are)
        X = P[P[:, 2] > 0] if prim.kind == "cone" else P
        Q = cad._project(prim, X)
        assert Q is not None and prim.distance(Q).max() < 1e-6, prim.kind


def test_a_wrapped_face_touching_itself_is_cut_into_half_turns():
    """A cylinder band that wraps almost all the way round and touches itself
    at one point is cut into two halves of about half a turn each (room for
    the seam), not into a big C and a sliver."""
    from mmm import cad
    from mmm.fitting import Cylinder

    a = np.linspace(0, 2 * np.pi, 73)[:-1]
    cen = np.c_[13 * np.cos(a), 13 * np.sin(a), np.full(len(a), 5.0)]
    reg = cad.Region(Cylinder([0, 0, 0], [0, 0, 1], 13.0), np.arange(len(a)), 1)
    parts = cad._half_turns(reg, np.array([13.0, 0.0, 5.0]), cen)
    assert parts is not None and sorted(len(p) for p in parts) == [36, 36]


def test_a_small_deep_notch_makes_the_verdict_uncertain():
    """A notch too small to move the percentiles (the mount's 0.3 mm notch
    covered 0.04% of its surface) still makes the verdict uncertain, so the
    finer check and the other method run; the same shape without it stays
    certain."""
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.gp import gp_Pnt

    V, F = _box_mesh((10, 8, 6))
    good = fidelity.Verdict(_strip(fidelity.compare(_box_shape((10, 8, 6)), [(V, F)], 0.01)), 0.01)
    notch = BRepPrimAPI_MakeBox(gp_Pnt(4.9, 3.9, 5.94), 0.25, 0.25, 0.1).Shape()
    shape = BRepAlgoAPI_Cut(_box_shape((10, 8, 6)), notch).Shape()
    v = fidelity.Verdict(_strip(fidelity.compare(shape, [(V, F)], 0.01)), 0.01)
    assert good.certainty == 1.0 and good.local < 1.0
    assert v.ok and v.local > fidelity.Verdict.LOCAL_K
    assert v.certainty <= 0.4 and "small spot" in v.doubt
    assert v.score < good.score


def _helical_gear(path, twist):
    """A gear with trapezoid teeth (sharp corners, so each flank is its own
    smooth patch), extruded 10 mm with a twist."""
    pts = []
    teeth = 12
    for k in range(teeth):
        a0 = 2 * np.pi * k / teeth
        for da, r in ((0.0, 9.0), (0.10, 9.0), (0.17, 11.0), (0.33, 11.0), (0.40, 9.0)):
            pts.append((r * np.cos(a0 + da), r * np.sin(a0 + da)))
    cs = m3.CrossSection([np.array(pts)])
    m = m3.Manifold.extrude(cs, 10.0, n_divisions=60 if twist else 0, twist_degrees=twist).to_mesh()
    trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts)).export(path)
    return path


def test_motion_of_swept_surfaces_is_found():
    from mmm import sweep

    rng = np.random.default_rng(0)
    # points and normals on a helicoid-like surface: a line swept by a screw
    s, t = rng.uniform(5, 10, 400), rng.uniform(-0.6, 0.6, 400)
    pitch = 2.0
    P = np.c_[s * np.cos(t), s * np.sin(t), 0.3 * s + pitch * t]
    du = np.c_[np.cos(t), np.sin(t), np.full_like(t, 0.3)]
    dv = np.c_[-s * np.sin(t), s * np.cos(t), np.full_like(t, pitch)]
    N = np.cross(du, dv)
    N /= np.linalg.norm(N, axis=1)[:, None]
    mot = sweep.fit_motion(P, N)
    assert mot.kind == "helix" and abs(abs(mot.pitch) - pitch) < 1e-3 and mot.sureness > 0.8
    assert abs(abs(mot.d[2]) - 1) < 1e-6
    # a cylinder fits several motions equally well: left to the analytic fit
    a = rng.uniform(0, 2 * np.pi, 400)
    P2 = np.c_[5 * np.cos(a), 5 * np.sin(a), rng.uniform(0, 10, 400)]
    N2 = np.c_[np.cos(a), np.sin(a), np.zeros(400)]
    assert sweep.fit_motion(P2, N2) is None


def test_helical_gear_flanks_become_one_face_each(tmp_path, monkeypatch):
    from mmm import cad
    from mmm.pipeline import Options, convert

    monkeypatch.setattr(cad, "FIND_SWEEPS", True)

    path = _helical_gear(str(tmp_path / "gear.stl"), 30.0)
    res = convert(path, str(tmp_path / "gear.step"), Options(preset="cad", quality=0.8), NULL)
    assert res["valid"] and res["fidelity"]["ok"]
    # 12 teeth: 24 flanks, 12 tips, 12 roots, top and bottom (instead of
    # each twisted flank cut into strips)
    assert res["faces"] <= 70, res["faces"]


def _taper_points(twist, rng, n=3000):
    """Points and normals on lines through the point (3, -2, 40) along a wavy
    outline 40 below it, turned by twist radians as they grow."""
    from mmm import sweep

    apex = np.array([3.0, -2.0, 40.0])
    m = sweep.Motion("taper", apex, [0, 0, -1], twist=twist)
    m.z0 = 40.0

    def prof(s):
        P = np.c_[15 * np.cos(s) + 2 * np.cos(5 * s), 12 * np.sin(s), np.zeros_like(s)] - apex
        return np.c_[P @ m.u, P @ m.v]

    s, t = rng.uniform(0, 1.2, n), np.log(rng.uniform(0.6, 1.0, n))
    P = m.from_profile(prof(s), t)
    e = 1e-6
    N = np.cross(m.from_profile(prof(s + e), t) - P, m.from_profile(prof(s), t + e) - P)
    return P, N / np.linalg.norm(N, axis=1)[:, None], apex


def test_tapered_surfaces_are_found():
    """Lines through one point (a drafted side, a bevel gear's teeth), and
    the same turning as they grow: the motion's centre and twist come out
    exactly, and a centre placed roughly is moved back by the corners."""
    from mmm import sweep

    rng = np.random.default_rng(1)
    for twist in (0.0, 0.8):
        P, N, apex = _taper_points(twist, rng)
        mot = sweep.fit_motion(P, N)
        assert mot is not None and mot.kind == "taper", twist
        assert np.linalg.norm(mot.a - apex) < 1e-3 and abs(mot.twist - twist) < 1e-3
        off = sweep.copy_motion(mot)
        off.a = mot.a + np.array([0.12, 0.0, 0.0])
        assert np.linalg.norm(sweep.refine_taper(off, P[:800]).a - apex) < 0.01


def _outline(n=160):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.c_[15 * np.cos(t) + 3 * np.cos(3 * t), 10 * np.sin(t)]


def test_a_drafted_boss_becomes_few_faces(tmp_path, monkeypatch):
    """A smooth outline pulled up and shrunk towards a point above it: its
    side is one tapered surface (two faces, as it goes all the way round),
    not 30 cones and cylinders."""
    from mmm import cad
    from mmm.pipeline import Options, convert

    monkeypatch.setattr(cad, "FIND_SWEEPS", True)
    m = m3.Manifold.extrude(m3.CrossSection([_outline()]), 12.0, scale_top=(0.7, 0.7)).to_mesh()
    path = str(tmp_path / "boss.stl")
    trimesh.Trimesh(np.asarray(m.vert_properties)[:, :3], np.asarray(m.tri_verts)).export(path)
    res = convert(path, str(tmp_path / "boss.step"), Options(preset="cad", quality=0.9), NULL)
    assert res["valid"] and res["fidelity"]["ok"]
    assert res["faces"] <= 6, res["faces"]


def test_a_rounded_edge_along_a_curve_becomes_one_fillet(tmp_path, monkeypatch):
    """A cam (a smooth outline pulled up) with its top edge rounded: the
    fillet, a ball rolled along the curved edge, comes out as one pipe
    surface (two faces, as it goes all the way round) instead of 15 tori,
    and the side as one extruded surface, though fillet, side and top are
    one smooth patch."""
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCP.BRepFilletAPI import BRepFilletAPI_MakeFillet
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.GeomAPI import GeomAPI_Interpolate
    from OCP.StlAPI import StlAPI_Writer
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.collections import HArray1_gp_Pnt
    from OCP.gp import gp_Pnt, gp_Vec

    from mmm import cad
    from mmm.pipeline import Options, convert

    monkeypatch.setattr(cad, "FIND_SWEEPS", True)
    th = np.linspace(0, 2 * np.pi, 9)[:-1]
    arr = HArray1_gp_Pnt(1, len(th))
    for i, t in enumerate(th):
        arr.SetValue(i + 1, gp_Pnt(18 * np.cos(t) * (1 + 0.25 * np.cos(2 * t)), 12 * np.sin(t) * (1 + 0.15 * np.sin(t)), 0.0))
    it = GeomAPI_Interpolate(arr, True, 1e-7)
    it.Perform()
    wire = BRepBuilderAPI_MakeWire(BRepBuilderAPI_MakeEdge(it.Curve()).Edge()).Wire()
    body = BRepPrimAPI_MakePrism(BRepBuilderAPI_MakeFace(wire).Face(), gp_Vec(0, 0, 10)).Shape()
    mk = BRepFilletAPI_MakeFillet(body)
    ex = TopExp_Explorer(body, TopAbs_EDGE)
    while ex.More():
        e = TopoDS.Edge(ex.Current())
        c = BRepAdaptor_Curve(e)
        if min(c.Value(c.FirstParameter()).Z(), c.Value(c.LastParameter()).Z()) > 9.9:
            mk.Add(1.5, e)
        ex.Next()
    shape = mk.Shape()
    BRepMesh_IncrementalMesh(shape, 0.02, False, 0.2, True)
    path = str(tmp_path / "cam.stl")
    StlAPI_Writer().Write(shape, path)
    res = convert(path, str(tmp_path / "cam.step"), Options(preset="cad", quality=0.9), NULL)
    assert res["valid"] and res["fidelity"]["ok"]
    # the true part has 4 faces: side, fillet, top, bottom (55 without
    # fillets and tapers; 8 on Linux, 12 on Windows, whose OpenCASCADE
    # meshes the test part a little differently)
    assert res["faces"] <= 14, res["faces"]


def test_a_strip_too_small_for_a_face_joins_the_surface_it_lies_on(tmp_path):
    """A region smaller than any valid face (on a printed mount, a strip of
    18 needle triangles inside a cylinder) joins the surface it lies on,
    however many triangles it has: left alone, its outline cut a spike into
    the cylinder's, which crossed itself and made the face invalid."""
    import trimesh

    from mmm import cad
    from mmm.fitting import Plane

    path = str(tmp_path / "cyl.stl")
    trimesh.creation.cylinder(radius=10.0, height=2.0, sections=256).export(path)
    bodies, _ = load_and_prepare(path, report=NULL)
    sh = bodies[0][0]
    # (a coarse tolerance, so that a few of these triangles are smaller
    # than a face can be)
    tol = 0.5
    seg = cad.Segmenter(sh.V, sh.F, sh.topo, tol, NULL)
    seg.run()
    cyl = next(i for i, r in enumerate(seg.regions) if r.kind == "cylinder")
    faces = np.asarray(seg.regions[cyl].faces)
    centre = seg.V[seg.F[faces[0]]].mean(0)
    strip = faces[np.argsort(np.linalg.norm(seg.V[seg.F[faces]].mean(1) - centre, axis=1))][:8]
    # more triangles than slivers used to be limited to (3), less area than a face
    assert float(seg.fa[strip].sum()) < 25 * tol ** 2
    n = seg.fn[strip].mean(0)
    seg.label[strip] = len(seg.regions)
    seg.regions.append(cad.Region(Plane(seg.V[seg.F[strip]].reshape(-1, 3).mean(0), n), strip, 1.0))
    seg._rebuild_regions()
    assert seg._absorb_slivers()
    assert all(seg.regions[seg.label[f]].kind == "cylinder" for f in strip)
