"""The engine's mesh check and fixer (engine/mmm/meshcheck.py): one broken
mesh per problem, what the check reports, and the second check of the
fixed mesh."""
import os
import subprocess
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
pytest.importorskip("trimesh")
pytest.importorskip("pymeshfix")

from broken_meshes import cases  # noqa: E402
from mmm import meshcheck  # noqa: E402
from mmm.meshio import load_and_prepare, read_parts, winding_number, write_3mf, write_stl  # noqa: E402
from mmm.report import NULL  # noqa: E402

CASES = cases()

# name -> (issue codes with their status, bodies, closed bodies, ready after fixing)
EXPECTED = {
    "clean_box": ({}, 1, 1, True),
    "glued_internal_wall": ({"internal_walls": "fixed"}, 1, 1, True),
    "hole": ({"holes": "fixed"}, 1, 1, True),
    "flipped": ({"flipped": "fixed"}, 1, 1, True),
    "inside_out": ({"inside_out": "fixed"}, 1, 1, True),
    "duplicates": ({"duplicates": "fixed"}, 1, 1, True),
    "nan": ({"bad_numbers": "fixed"}, 1, 1, True),
    "corner_touch": ({"pinched": "fixed"}, 2, 2, True),
    "self_crossing": ({"self_intersections": "not_fixed"}, 1, 1, False),
    "overlap": ({"overlaps": "action"}, 2, 2, True),
    "nested": ({}, 2, 2, True),
    "t_junction": ({"cracks": "fixed"}, 1, 1, True),
    "tiny_crack": ({"cracks": "fixed"}, 1, 1, True),
    "open_sheet": ({"open_surface": "not_fixed"}, 1, 0, True),
    "tiny_scale": ({"scale": "note"}, 1, 1, True),
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_problem_is_found_and_fixed(name):
    want, n_bodies, n_closed, ready = EXPECTED[name]
    bodies, info, issues = meshcheck.prepare_part(CASES[name], "auto", True, NULL, False)
    got = {i["code"]: i["status"] for i in issues}
    assert got == want
    assert len(bodies) == n_bodies
    assert sum(all(s.closed for s in b) for b in bodies) == n_closed
    ok, problems = meshcheck.verify_bodies(bodies, info.diag)
    assert ok == ready, problems
    for i in issues:
        assert i["problem"] and i["result"] and i["count"] >= 0


def test_fixed_box_keeps_its_volume():
    for name in ("glued_internal_wall", "flipped", "inside_out", "duplicates", "t_junction", "tiny_crack"):
        bodies, _, _ = meshcheck.prepare_part(CASES[name], "auto", True, NULL, False)
        vol = sum(s.volume for b in bodies for s in b)
        expected = 2000.0 if name == "glued_internal_wall" else 1000.0
        assert vol == pytest.approx(expected, rel=1e-3), name


def test_merge_overlaps_on_request():
    bodies, _, issues = meshcheck.prepare_part(CASES["overlap"], "auto", True, NULL, True)
    assert len(bodies) == 1 and bodies[0][0].closed
    assert {i["code"]: i["status"] for i in issues} == {"overlaps": "fixed"}
    # two 10 mm cubes overlapping by 5 x 5 x 10
    assert bodies[0][0].volume == pytest.approx(1750.0, rel=1e-4)


def test_repair_off_only_reports():
    _, _, issues = meshcheck.prepare_part(CASES["corner_touch"], "off", True, NULL, False)
    assert {i["code"]: i["status"] for i in issues} == {"pinched": "not_fixed"}


def test_summary_order_and_status():
    issues = [meshcheck._issue("a", 1, "fixed", "p", "r"), meshcheck._issue("b", 1, "not_fixed", "p", "r"),
              meshcheck._issue("c", 1, "action", "p", "r")]
    s = meshcheck.summarize(issues, {"ready": False, "problems": ["x"]})
    assert s["status"] == "problems" and [i["code"] for i in s["issues"]] == ["b", "c", "a"]
    assert meshcheck.summarize([])["status"] == "ok"
    assert meshcheck.summarize(issues[:1])["status"] == "fixed"
    assert meshcheck.summarize(issues[::2])["status"] == "action"


def test_self_intersection_count():
    t = CASES["self_crossing"]
    from mmm.meshio import triangles_to_indexed

    V, F = triangles_to_indexed(t)
    k, faces = meshcheck.self_intersections(V, F)
    assert k > 0 and len(faces) > 0
    V, F = triangles_to_indexed(CASES["clean_box"])
    assert meshcheck.self_intersections(V, F)[0] == 0


def test_winding_by_segment_crossings():
    """The inside test the self-crossing repair uses to compare solids."""
    from mmm.meshio import triangles_to_indexed

    V, F = triangles_to_indexed(CASES["clean_box"])
    rng = np.random.default_rng(1)
    P = rng.uniform(-8, 8, (500, 3))
    q = np.array([0.01, 0.02, 0.03])
    w = np.rint(winding_number(V, F, q[None])[0]) + meshcheck._signed_hits(q, P, V[F])
    assert np.array_equal(w >= 0.5, (np.abs(P) < 5).all(1))


def test_self_crossing_repair_never_changes_the_solid():
    """MeshFix's result is only kept where it doesn't add or remove material."""
    from mmm.meshio import Shell, triangles_to_indexed

    V, F = triangles_to_indexed(CASES["self_crossing"])
    s = Shell(V, F)
    fixed = meshcheck.fix_self_intersections(s)
    if fixed is not None:
        rng = np.random.default_rng(2)
        P = rng.uniform(V.min(0), V.max(0), (3000, 3))
        inside_before = np.abs(winding_number(V, F, P)) >= 0.5
        inside_after = np.abs(winding_number(fixed.V, fixed.F, P)) >= 0.5
        assert (inside_before != inside_after).mean() < 0.002


def test_write_stl_and_3mf_round_trip(tmp_path):
    bodies, _, _ = meshcheck.prepare_part(CASES["overlap"], "auto", True, NULL, False)
    stl = str(tmp_path / "fixed.stl")
    write_stl(stl, bodies)
    parts = read_parts(stl, NULL, "parts")
    assert len(parts) == 1 and np.asarray(parts[0][1]).reshape(-1, 9).shape[0] == 24
    tmf = str(tmp_path / "fixed.3mf")
    write_3mf(tmf, [("Left", bodies[:1]), ("Right", bodies[1:])])
    parts = read_parts(tmf, NULL, "parts")
    assert [p[0] for p in parts] == ["Left", "Right"]
    bodies2, info = load_and_prepare(tmf)
    assert info.mesh_check["verified"]["ready"] and len(bodies2) == 2


def test_cli_fix_and_inspect(tmp_path):
    src = str(tmp_path / "holey.stl")
    write_stl(src, [[_shell(CASES["hole"])]])
    out = str(tmp_path / "out.stl")
    env = dict(os.environ, PYTHONPATH=os.path.join(ROOT, "engine"))
    r = subprocess.run([sys.executable, "-m", "mmm.cli", "fix", src, "-o", out, "--json"], capture_output=True,
                       text=True, env=env, timeout=300)
    assert r.returncode == 0, r.stderr
    assert os.path.exists(out)
    assert '"mesh_check"' in r.stdout and '"holes"' in r.stdout
    r = subprocess.run([sys.executable, "-m", "mmm.cli", "inspect", out, "--json"], capture_output=True, text=True,
                       env=env, timeout=300)
    assert r.returncode == 0 and '"status": "ok"' in r.stdout


def _shell(tris):
    """An open shell straight from triangles (no fixing)."""
    from mmm.meshio import Shell, triangles_to_indexed

    return Shell(*triangles_to_indexed(tris))
