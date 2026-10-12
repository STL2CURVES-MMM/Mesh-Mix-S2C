"""The engine's automatic STEP repair (engine/mmm/repair.py)."""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
pytest.importorskip("OCP")

from mmm import repair, stepio  # noqa: E402


def box():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox

    return BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Solid()


def unsewn_box():
    """The box's six faces each with their own edges (not stitched
    together): open seams everywhere, as in a badly exported file."""
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shell, TopoDS_Solid

    B = BRep_Builder()
    shell = TopoDS_Shell()
    B.MakeShell(shell)
    exp = TopExp_Explorer(box(), TopAbs_FACE)
    while exp.More():
        B.Add(shell, BRepBuilderAPI_Copy(exp.Current()).Shape())
        exp.Next()
    solid = TopoDS_Solid()
    B.MakeSolid(solid)
    B.Add(solid, shell)
    return solid


def write(items, path):
    stepio.write_step(items[0][0], path)


def verify(path):
    return stepio.verify_step(path, expected_solids=1, mesh_volume=6000.0)


def test_clean_file_is_left_alone(tmp_path):
    p = str(tmp_path / "box.step")
    items = [(box(), None)]
    write(items, p)
    check = verify(p)
    assert not repair.needs_repair(check) and check["valid"]
    before = os.path.getmtime(p)
    out, check2, done = repair.repair(items, p, check, write, verify, 0.01)
    assert done == [] and out is items and check2 is check and os.path.getmtime(p) == before


def test_unsewn_solid_is_repaired(tmp_path):
    p = str(tmp_path / "broken.step")
    items = [(unsewn_box(), None)]
    write(items, p)
    check = verify(p)
    assert repair.needs_repair(check), check["issues"]
    out, check2, done = repair.repair(items, p, check, write, verify, 0.01)
    assert done, "no repair step helped"
    assert not repair.needs_repair(check2) and check2["valid"], check2["issues"]
    assert verify(p)["issues"] == []  # the file on disk was replaced
    assert abs(stepio.volume(out[0][0]) - 6000.0) < 1.0


def test_better_never_accepts_a_changed_shape():
    old = {"issues": [{"code": "invalid", "level": "warning", "message": ""}], "solids": 1, "volume": 100.0}
    new = {"issues": [], "solids": 1, "volume": 90.0, "valid": True}
    assert not repair._better(new, old)
    assert repair._better({**new, "volume": 100.05}, old)
    assert not repair._better({**new, "volume": 100.2}, old)
    assert not repair._better({**new, "volume": 100.0, "solids": 0}, old)
