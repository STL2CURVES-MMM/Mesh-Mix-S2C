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
