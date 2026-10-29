"""The quad remesher's stall guard (mmm/remesh_guard.py, 2026-10-28), the order of the
nudged targets, the rule for keeping a healed shape (mmm/pipeline.py fix_acceptable,
orient_like_mesh) and the wording of a failure by direction (mmm/fidelity.py
Verdict.summary). Everything is made in the tests: no meshes."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))

from mmm import remesh_guard as G  # noqa: E402

# a child that prints the remesher's first steps, then loops in Step 5 for ever (as
# Instant Meshes does on some inputs), keeping a core busy and printing nothing more
HANG = r"""
import sys
sys.stdout.write("Step 4: Assigning positions to vertices .. done. (took 21.0ms)\n"
                 "Step 5: Snapping and removing unnecessary edges ..")
sys.stdout.flush()
while True:
    pass
"""

# a child that goes through the steps and writes a result, as the real one does
FINISH = r"""
import sys, json
sys.path[:0] = json.loads(sys.argv[1])
import numpy as np
dst = sys.argv[3]
print("Step 5: Snapping and removing unnecessary edges .... done. (took 3.0ms)", flush=True)
print("Step 12: Reordering mesh for efficient access .. done. (took 1.0ms)", flush=True)
print("\nREMESH_DONE", flush=True)
np.savez(dst, V=np.zeros((4, 3)), F=np.array([[0, 1, 2, 3]]))
"""


def hang(args):
    return [G.python(), "-s", "-c", HANG] + args


def finish(args):
    return [G.python(), "-s", "-c", FINISH, G.child_paths()] + args


class Log:
    def __init__(self):
        self.lines = []

    def log(self, m):
        self.lines.append(m)

    def note(self, m):
        self.lines.append(m)


V = np.random.default_rng(0).random((10, 3))
F = np.array([[0, 1, 2], [0, 2, 3]])


def test_a_child_stuck_in_step_5_is_stopped_soon():
    import time

    t = time.time()
    with pytest.raises(G.Stalled) as e:
        G.run_child(V, F, 100, command=hang)
    assert e.value.step.startswith("Step 5")
    assert time.time() - t < G.STEP_FLOOR_S + 4


def test_a_child_that_finishes_gives_its_quads():
    qv, qf = G.run_child(V, F, 100, command=finish)
    assert qv.shape == (4, 3) and qf.tolist() == [[0, 1, 2, 3]]


def test_stuck_runs_are_nudged_then_given_up_within_the_budget():
    log = Log()
    budget = G.Budget(2)
    with pytest.raises(G.Stuck):
        G.remesh(V, F, 914, report=log, budget=budget, command=hang)
    assert budget.left == 0 and budget.used == 2
    assert any("run again with 913 cage vertices" in m for m in log.lines)
    with pytest.raises(G.Stuck):            # (spent: no more tries)
        G.remesh(V, F, 914, report=log, budget=budget, command=finish)


def test_the_nudges_go_one_and_two_either_side_then_one_prime_jump():
    assert G.big_jump(914) == 67                # round(63.98) = 64, the next prime is 67
    assert G.nudged_targets(914) == [914, 913, 915, 912, 916, 981]
    assert G.big_jump(10) == 3                  # (at least 3, to clear the small nudges)
    assert G.big_jump(100) == 7
    assert all(G._prime(G.big_jump(t)) for t in range(4, 3000, 37))
    assert min(G.nudged_targets(5)) >= 4


def test_the_real_remesher_runs_watched_on_a_small_sphere():
    pytest.importorskip("pynanoinstantmeshes")
    trimesh = pytest.importorskip("trimesh")
    m = trimesh.creation.icosphere(4, 10.0)
    qv, qf, used = G.remesh(np.asarray(m.vertices), np.asarray(m.faces), 300)
    assert used == 300 and len(qf) > 100 and np.asarray(qf).shape[1] == 4


def _box(reversed_=False):
    from OCP.BRep import BRep_Builder
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Solid

    box = BRepPrimAPI_MakeBox(1.0, 2.0, 3.0).Solid()
    if not reversed_:
        return box
    B = BRep_Builder()
    out = TopoDS_Solid()
    B.MakeSolid(out)
    B.Add(out, TopExp_Explorer(box, TopAbs_SHELL).Current().Reversed())
    return out


def test_a_healed_shape_is_kept_only_with_the_meshes_volume_and_sign():
    from mmm import pipeline, stepio

    box, inside_out = _box(), _box(True)
    assert stepio.volume(inside_out) < 0
    assert not pipeline.fix_acceptable(inside_out, box, 6.0)       # (sign turned)
    assert pipeline.fix_acceptable(box, inside_out, 6.0)
    assert not pipeline.fix_acceptable(box, box, 6.2)              # (3% off the mesh)
    assert pipeline.fix_acceptable(box, box, None)                 # (no mesh: its own volume)


def test_a_solid_inside_out_is_turned_the_right_way():
    from mmm import pipeline, stepio

    turned = pipeline.orient_like_mesh(_box(True), 6.0)
    assert stepio.volume(turned) == pytest.approx(6.0) and stepio.is_valid(turned)
    box = _box()
    assert pipeline.orient_like_mesh(box, 6.0) is box
    assert stepio.volume(pipeline.orient_like_mesh(_box(True), None)) < 0     # (open mesh: left)


def test_a_failure_by_direction_is_named():
    from mmm.fidelity import Verdict

    r = {"to_shape": {"p99": 0.01, "p999": 0.02}, "to_mesh": {"p99": 0.01, "p999": 0.02},
         "within2": 1.0, "worst_distance": 0.047, "wrong_way": 0.989, "wrong_way_shape": 0.98}
    v = Verdict(r, 0.04)
    assert not v.ok and "inside out" in v.summary()
    v = Verdict(dict(r, wrong_way=0.01, wrong_way_shape=0.0), 0.04)
    assert "faces another way" in v.summary()
    v = Verdict(dict(r, wrong_way=0.0, wrong_way_shape=0.0), 0.04)
    assert v.ok and "way" not in v.summary()
