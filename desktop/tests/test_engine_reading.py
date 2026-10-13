"""The engine reading odd and damaged files, units, mirrored 3MF copies,
touching 3MF parts and part names (fixes from the code review)."""
import os
import struct
import sys
import zipfile

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
pytest.importorskip("trimesh")

from mmm.meshio import MeshError, load_and_prepare, read_parts, read_stl  # noqa: E402
from mmm.report import NULL  # noqa: E402

CUBE = [((0, 0, 0), (1, 1, 0), (1, 0, 0)), ((0, 0, 0), (0, 1, 0), (1, 1, 0)),
        ((0, 0, 1), (1, 0, 1), (1, 1, 1)), ((0, 0, 1), (1, 1, 1), (0, 1, 1)),
        ((0, 0, 0), (1, 0, 0), (1, 0, 1)), ((0, 0, 0), (1, 0, 1), (0, 0, 1)),
        ((0, 1, 0), (1, 1, 1), (1, 1, 0)), ((0, 1, 0), (0, 1, 1), (1, 1, 1)),
        ((0, 0, 0), (0, 0, 1), (0, 1, 1)), ((0, 0, 0), (0, 1, 1), (0, 1, 0)),
        ((1, 0, 0), (1, 1, 0), (1, 1, 1)), ((1, 0, 0), (1, 1, 1), (1, 0, 1))]


def box(sx, sy, sz, at=(0, 0, 0)):
    return [[tuple(a + c * s for a, c, s in zip(at, p, (sx, sy, sz))) for p in t] for t in CUBE]


def ascii_stl(tris, bom=False):
    out = ["solid x"]
    for t in tris:
        out += [" facet normal 0 0 0", "  outer loop"] + [f"   vertex {x} {y} {z}" for x, y, z in t]
        out += ["  endloop", " endfacet"]
    out.append("endsolid x")
    return (b"\xef\xbb\xbf" if bom else b"") + ("\n".join(out) + "\n").encode()


def binary_stl(tris, header=b"binary"):
    body = b"".join(struct.pack("<12fH", 0, 0, 0, *t[0], *t[1], *t[2], 0) for t in tris)
    return header.ljust(80, b" ") + struct.pack("<I", len(tris)) + body


def write(tmp_path, name, data):
    p = tmp_path / name
    p.write_bytes(data)
    return str(p)


def test_ascii_stl_with_byte_order_mark(tmp_path):
    assert read_stl(write(tmp_path, "bom.stl", ascii_stl(CUBE, bom=True))).shape == (12, 3, 3)


def test_ascii_stl_with_unreadable_numbers(tmp_path):
    data = ascii_stl(CUBE).replace(b"vertex 1 1 0", b"vertex 1,5 1 0", 1)
    with pytest.raises(MeshError, match="commas"):
        read_stl(write(tmp_path, "comma.stl", data))


def test_binary_stl_whose_header_says_solid(tmp_path):
    # a wrong triangle count in the header too, so the size doesn't match exactly
    data = binary_stl(CUBE, header=b"solid exported facet endsolid")
    data = data[:80] + struct.pack("<I", 999) + data[84:] + b"\0" * 7
    assert read_stl(write(tmp_path, "solid.stl", data)).shape == (12, 3, 3)


def test_damaged_3mf_is_the_files_fault(tmp_path):
    p = tmp_path / "bad.3mf"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("3D/3dmodel.model", "<model xmlns='http://schemas.microsoft.com/3dmanufacturing/core/2015/02'>"
                                       "<resources><object id='1'><mesh><vertices>"
                                       "<vertex x='0' y='0' z='0'/></vertices><triangles>"
                                       "<triangle v1='0' v2='5' v3='9'/></triangles></mesh></object>"
                                       "</resources><build><item objectid='1'/></build></model>")
    with pytest.raises(MeshError, match="damaged"):
        read_parts(str(p), NULL)


def test_obj_with_accented_comment(tmp_path):
    data = "# café\nv 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\nf 1 3 2\nf 1 2 4\nf 2 3 4\nf 1 4 3\n".encode("latin-1")
    assert len(read_parts(write(tmp_path, "c.obj", data), NULL)[0][1]) == 4


def threemf(path, objects, items, components=None):
    """objects: {id: (name, tris)}; items: [(id, transform)]; components: {id: (name, [ids])}."""
    def mesh(tris):
        verts, idx = [], {}
        faces = []
        for t in tris:
            f = []
            for p in t:
                if p not in idx:
                    idx[p] = len(verts)
                    verts.append(p)
                f.append(idx[p])
            faces.append(f)
        v = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in verts)
        t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
        return f"<mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh>"
    objs = [f'<object id="{i}" name="{n}" type="model">{mesh(t)}</object>' for i, (n, t) in objects.items()]
    for i, (n, ids) in (components or {}).items():
        c = "".join(f'<component objectid="{k}"/>' for k in ids)
        objs.append(f'<object id="{i}" name="{n}" type="model"><components>{c}</components></object>')
    its = "".join(f'<item objectid="{i}" transform="{tr}"/>' for i, tr in items)
    model = ('<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" '
             'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(objs)}</resources><build>{its}</build></model>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", model)
    return str(path)


def test_mirrored_copy_keeps_its_mirror(tmp_path):
    L = box(3, 1, 1) + box(1, 2, 1, at=(0, 1, 0))  # an L: its mirror image differs
    p = threemf(tmp_path / "m.3mf", {1: ("L", L)},
                [(1, "1 0 0 0 1 0 0 0 1 0 0 0"), (1, "-1 0 0 0 1 0 0 0 1 50 0 0")])
    parts = read_parts(p, NULL)
    assert len(parts) == 2
    xs = [np.asarray(t).reshape(-1, 3)[:, 0] for _, t in parts]
    assert xs[0].min() >= 0 and xs[1].max() <= 0  # the second one is mirrored


def test_touching_parts_of_one_object(tmp_path):
    p = threemf(tmp_path / "t.3mf", {1: ("A", box(10, 10, 10)), 2: ("B", box(10, 10, 10, at=(10, 0, 0))),
                                     3: ("C", box(5, 5, 5))},
                [(4, "1 0 0 0 1 0 0 0 1 0 0 0"), (3, "1 0 0 0 1 0 0 0 1 100 0 0")],
                components={4: ("Assembly", [1, 2])})
    # the separate object C has its own coordinates: it doesn't count as touching A
    _, info = load_and_prepare(p)
    assert [g["parts"] for g in info.touching] == [[0, 1]] and info.parts == 3
    assert next(i for i in info.mesh_check["issues"] if i["code"] == "touching_parts")["status"] == "action"
    _, info = load_and_prepare(p, touching="split")
    assert info.parts == 3 and info.mesh_check["status"] != "action"
    bodies, info = load_and_prepare(p, touching="merge")
    assert info.parts == 2 and info.part_names[0] == "Assembly - part 1 + Assembly - part 2"
    assert sum(s.volume for s in bodies[info.body_part.index(0)]) == pytest.approx(2000.0)


@pytest.mark.parametrize("unit, mm", [("mm", 1.0), ("in", 25.4), ("cm", 10.0)])
def test_units_round_trip(tmp_path, unit, mm):
    pytest.importorskip("OCP")
    from mmm import stepio
    from mmm.pipeline import Options, convert

    src = write(tmp_path, "b.stl", binary_stl(box(4, 3, 2) + box(1, 1, 1, at=(10, 0, 0))))
    for split in (False, True):
        out = str(tmp_path / f"b-{unit}-{split}.step")
        res = convert(src, out, Options(preset="cad", unit=unit))
        assert res["check_ok"] and not res.get("checks"), res.get("checks")
        stepio.set_model_unit("mm")
        shape = stepio.read_step(out)
        from OCP.Bnd import Bnd_Box
        from OCP.BRepBndLib import BRepBndLib

        b = Bnd_Box()
        BRepBndLib.Add_s(shape, b)
        size = b.CornerMax().X() - b.CornerMin().X()
        assert size == pytest.approx(11 * mm, rel=1e-3)


def test_accented_part_names(tmp_path):
    pytest.importorskip("OCP")
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    from mmm import stepio

    stepio.set_model_unit("mm")
    p = str(tmp_path / "n.step")
    stepio.write_step_named([(BRepPrimAPI_MakeBox(1.0, 1.0, 1.0).Solid(), "Gehäuse"),
                             (BRepPrimAPI_MakeBox(2.0, 1.0, 1.0).Solid(), "Peça")], p)
    assert [n for _, n in stepio.read_step_named(p)] == ["Gehäuse", "Peça"]
