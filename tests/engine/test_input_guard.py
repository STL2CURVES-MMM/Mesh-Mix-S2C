"""The entrance checks of 2026-10-26 (an adversarial test's findings): every broken file,
path or setting ends in a message for the user before any work is done, not in a
traceback, a runaway or a destroyed input. Meshes are generated here; none are committed.

mms2c/merged.py _entrance and main, mmm/pipeline.py check_paths and Options,
mmm/meshio.py check_triangles and check_size, mmm/threemf.py's limits,
mms2c/features.py load_stl."""
import json
import os
import struct
import sys
import zipfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
pytest.importorskip("trimesh")

from mms2c import features, merged  # noqa: E402
from mmm import cli, meshio, pipeline, threemf  # noqa: E402

CUBE = [((0, 0, 0), (1, 1, 0), (1, 0, 0)), ((0, 0, 0), (0, 1, 0), (1, 1, 0)),
        ((0, 0, 1), (1, 0, 1), (1, 1, 1)), ((0, 0, 1), (1, 1, 1), (0, 1, 1)),
        ((0, 0, 0), (1, 0, 0), (1, 0, 1)), ((0, 0, 0), (1, 0, 1), (0, 0, 1)),
        ((0, 1, 0), (1, 1, 1), (1, 1, 0)), ((0, 1, 0), (0, 1, 1), (1, 1, 1)),
        ((0, 0, 0), (0, 0, 1), (0, 1, 1)), ((0, 0, 0), (0, 1, 1), (0, 1, 0)),
        ((1, 0, 0), (1, 1, 0), (1, 1, 1)), ((1, 0, 0), (1, 1, 1), (1, 0, 1))]


def cube(size=10.0, at=(0.0, 0.0, 0.0)):
    return np.array(CUBE, dtype=float) * size + np.array(at)


def binary_stl(tris, count=None):
    tris = np.asarray(tris, dtype=float)
    body = b"".join(struct.pack("<12fH", 0, 0, 0, *t.ravel(), 0) for t in tris)
    return b"binary".ljust(80, b" ") + struct.pack("<I", len(tris) if count is None else count) + body


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


# ---------------------------------------------------------------- the mesh


@pytest.mark.parametrize("data, words", [
    (b"", "empty"),
    (b"solid x\nendsolid x\n" + b" " * 80, "no triangles"),
    (b"x" * 80 + struct.pack("<I", 0), "no triangles"),
    (b"solid x\n facet normal 0 0 0\n outer loop\n vertex 0 0\n vertex 1 0 0\n vertex 0 1 0\n endloop\n endfacet\n"
     b"endsolid x\n", "triangles"),
    (b"v 0 0 0\nv 1 0 0\nv 0 1 0\n" * 10 + b"f 1 2 3\n" * 10, "another kind of mesh file"),
    (b"solid x\n facet normal 0 0 0\n outer loop\n vertex a b c\n vertex 1 0 0\n vertex 0 1 0\n endloop\n"
     b" endfacet\nendsolid x\n", "triangles"),
])
def test_broken_stl_files_are_refused_with_a_message(tmp_path, data, words):
    path = write(tmp_path, "bad.stl", data)
    with pytest.raises(meshio.MeshError, match=words):
        merged._entrance(path, str(tmp_path / "bad.step"))
    # our own reader (the stl2curves command line, its regression tests) refuses them too
    with pytest.raises(meshio.MeshError):
        features.load_stl(path)


def test_a_wrong_triangle_count_is_read_by_the_files_size(tmp_path):
    path = write(tmp_path, "count.stl", binary_stl(cube(), count=1000))
    assert merged._entrance(path, str(tmp_path / "count.step"))[:2] == (True, 12)
    pts, tris = features.load_stl(path)
    assert len(tris) == 12 and len(pts) == 8


def test_a_well_formed_stl_reads_as_before(tmp_path):
    """The same welded points and triangles as our former reader gave."""
    path = write(tmp_path, "ok.stl", binary_stl(cube(7.3, (1.1, 2.2, 3.3))))
    pts, tris = features.load_stl(path)
    data = open(path, "rb").read()
    rec = np.frombuffer(data, offset=84, count=12, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)),
                                                                     ("a", "<u2")]))
    verts = rec["v"].reshape(-1, 3).astype(float)
    uniq, inv = np.unique(np.round(verts * 1e4).astype(np.int64), axis=0, return_inverse=True)
    assert np.array_equal(tris, inv.reshape(-1, 3))
    assert np.allclose(pts[tris].reshape(-1, 3), verts)


@pytest.mark.parametrize("tris, words", [
    (np.zeros((12, 3, 3)), "same spot"),
    (np.full((12, 3, 3), np.nan), "broken"),
    (cube(1e300), "too large"),
    (cube(1e-8), "too small for CAD"),
    # (2e9 mm out, float32 spacing is 2 m: the 10 mm cube is noise, as MMM alone says)
    (cube(10.0, (2e9, 0, 0)), "squashed"),
])
def test_meshes_with_no_shape_or_incalculable_coordinates_are_refused(tris, words):
    with pytest.raises(meshio.MeshError, match=words):
        meshio.check_triangles(tris)


@pytest.mark.parametrize("size", [0.002, 0.05, 5e9])
def test_any_finite_size_is_accepted(size):
    """A model in the wrong unit is still a valid shape (MMM's policy: a note, not a refusal)."""
    meshio.check_triangles(cube(size))


def test_a_model_outside_our_front_ends_sizes_goes_to_mmm_alone(tmp_path, monkeypatch):
    monkeypatch.setattr(merged, "RIVALS", False)
    src = write(tmp_path, "tiny.stl", binary_stl(cube(0.05)))
    warnings = []

    class Rep(merged.Reporter):
        def _emit(self, obj):
            if obj.get("event") == "warning":
                warnings.append(obj["message"])

    res = merged.convert_file(src, str(tmp_path / "tiny.step"), None, Rep())
    assert res["faces"] == 6
    assert any("on its own" in m for m in warnings)


def test_a_mesh_every_triangle_of_which_the_weld_squashes_is_refused():
    """A 20 mm ball 1e7 mm from the origin, stored in float32 (1 mm steps there): MMM's
    weld joins its corners, and the merged engine now refuses it as MMM does."""
    import trimesh
    ball = trimesh.creation.icosphere(subdivisions=3, radius=10.0)
    tris = (ball.vertices[ball.faces] + 1e7).astype(np.float32).astype(float)
    with pytest.raises(meshio.MeshError, match="squashed"):
        meshio.check_triangles(tris)
    meshio.check_triangles(ball.vertices[ball.faces] + 1e4)      # nearer: fine


def test_other_formats_go_to_mmms_pipeline(tmp_path):
    path = write(tmp_path, "part.obj", b"v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n")
    assert merged._entrance(path, str(tmp_path / "part.step")) == (False, 0, None)


# ---------------------------------------------------------------- the paths


def test_the_paths_are_checked_before_any_work(tmp_path):
    good = write(tmp_path, "part.stl", binary_stl(cube()))
    check = pipeline.check_paths
    with pytest.raises(FileNotFoundError):
        check(str(tmp_path / "missing.stl"), str(tmp_path / "x.step"))
    with pytest.raises(pipeline.PathError, match="folder"):
        check(str(tmp_path), str(tmp_path / "x.step"))
    with pytest.raises(pipeline.PathError, match="over the mesh file"):
        check(good, good)
    with pytest.raises(pipeline.PathError, match="over the mesh file"):
        check(good, str(tmp_path / "." / "part.stl"))
    with pytest.raises(pipeline.PathError, match="extension"):
        check(good, str(tmp_path / "other.3mf"))
    with pytest.raises(pipeline.PathError, match="is a folder"):
        check(good, str(tmp_path))
    with pytest.raises(pipeline.PathError, match="doesn't exist"):
        check(good, str(tmp_path / "no" / "such" / "x.step"))
    ro = tmp_path / "ro.step"
    ro.write_bytes(b"")
    os.chmod(ro, 0o444)
    try:
        if not os.access(ro, os.W_OK):
            with pytest.raises(PermissionError):
                check(good, str(ro))
    finally:
        os.chmod(ro, 0o666)
    check(good, str(tmp_path / "x.step"))
    assert sorted(os.listdir(tmp_path)) == ["part.stl", "ro.step"]     # (the write probe is gone)
    assert open(good, "rb").read() == binary_stl(cube())


@pytest.mark.parametrize("kw", [{"quality": float("nan")}, {"quality": float("inf")}, {"quality": 2.0},
                                {"quality": -0.1}, {"tolerance": float("nan")}, {"tolerance": -1.0},
                                {"tolerance": float("inf")}])
def test_settings_out_of_range_are_refused(kw):
    with pytest.raises(pipeline.OptionError):
        pipeline.Options(**kw)


def test_settings_in_range_are_taken():
    assert pipeline.Options(quality=0.0, tolerance=0).tolerance == 0
    assert pipeline.Options(quality=1.0, tolerance=0.05).quality == 1.0


def test_the_command_line_reports_a_refusal_as_a_json_error_event(tmp_path, capsys):
    path = write(tmp_path, "part.stl", binary_stl(cube()))
    assert merged.main([path, "-o", path, "--json"]) == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert events[-1]["event"] == "error" and "over the mesh file" in events[-1]["message"]
    assert open(path, "rb").read() == binary_stl(cube())
    empty = write(tmp_path, "empty.stl", b"")
    assert merged.main([empty, "--json"]) == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.startswith("{")]
    assert events[-1]["event"] == "error" and "empty" in events[-1]["message"]
    assert not os.path.exists(tmp_path / "empty.step")


def test_mmms_command_line_refuses_to_overwrite_the_input(tmp_path):
    path = write(tmp_path, "part.stl", binary_stl(cube()))
    assert cli.main(["convert", path, "-o", path]) == 1
    assert cli.main(["convert", path, "--quality", "nan"]) == 1
    assert open(path, "rb").read() == binary_stl(cube())


# ---------------------------------------------------------------- 3MF projects


def project(path, model):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", '<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" '
                   'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">' + model + "</model>")
    return str(path)


TET = ('<mesh><vertices><vertex x="0" y="0" z="0"/><vertex x="1" y="0" z="0"/><vertex x="0" y="1" z="0"/>'
       '<vertex x="0" y="0" z="1"/></vertices><triangles>{}</triangles></mesh>')
TET_FACES = '<triangle v1="0" v2="2" v3="1"/><triangle v1="0" v2="1" v3="3"/>' \
            '<triangle v1="1" v2="2" v3="3"/><triangle v1="0" v2="3" v3="2"/>'


def test_a_3mf_triangle_with_a_corner_not_in_the_file_is_refused(tmp_path):
    bad = TET.format(TET_FACES.replace('v1="0" v2="2"', 'v1="-1" v2="2"', 1))
    path = project(tmp_path / "neg.3mf", f'<resources><object id="1" type="model">{bad}</object></resources>'
                                         '<build><item objectid="1"/></build>')
    with pytest.raises(meshio.MeshError, match="corners"):
        meshio.read_parts(path)


def test_a_3mf_that_copies_its_components_without_end_is_refused(tmp_path, monkeypatch):
    """Ten copies of the one below, nested ten deep: 1e10 tetrahedra from a 2 kB file."""
    monkeypatch.setattr(threemf, "MAX_PIECES", 1000)
    objs = f'<object id="1" type="model">{TET.format(TET_FACES)}</object>'
    for k in range(2, 12):
        objs += f'<object id="{k}" type="model"><components>' + f'<component objectid="{k - 1}"/>' * 10 + \
                "</components></object>"
    path = project(tmp_path / "fan.3mf", f'<resources>{objs}</resources><build><item objectid="11"/></build>')
    with pytest.raises(meshio.MeshError, match="too big"):
        meshio.read_parts(path)


def test_a_3mf_reads_as_before(tmp_path):
    path = project(tmp_path / "ok.3mf", f'<resources><object id="1" type="model">{TET.format(TET_FACES)}</object>'
                                        '</resources><build><item objectid="1"/></build>')
    (name, tris), = meshio.read_parts(path)
    assert tris.shape == (4, 3, 3) and tris.dtype == np.float64
    assert np.array_equal(tris[0], [[0, 0, 0], [0, 1, 0], [1, 0, 0]])
