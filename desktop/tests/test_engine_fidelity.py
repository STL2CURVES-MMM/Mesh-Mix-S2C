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
