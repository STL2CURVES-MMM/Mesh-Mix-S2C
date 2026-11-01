"""Lone creases are not coarse curves (2026-10-28, b2): the chordal deflection counts a
bend only where the curve goes on through the flat facets on both sides
(mmm/fidelity.py _bend_continues), and a freeform cell over weak flat faces that can't
follow its own triangles goes back to those flat faces (mmm/cad.py
Segmenter._make_freeform). Toolbox bottom: a wall and its 30-degree chamfer became one
B-spline bulging 0.42 mm off them, and the part, finely meshed elsewhere, was taken for
one tessellated to 0.24 mm. The shapes are made here; no mesh is stored."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
m3 = pytest.importorskip("manifold3d")

from mmm import cad, fidelity, meshio  # noqa: E402
from mmm.pipeline import auto_tolerance  # noqa: E402
from mmm.report import NULL  # noqa: E402

CHAMFER_DEG = 30.0      # between the wall and its chamfer


def _mesh(m):
    mm = m.to_mesh()
    return np.asarray(mm.vert_properties, dtype=float)[:, :3], np.asarray(mm.tri_verts, dtype=np.int64)


def _chamfered_box(refine=1):
    """A 40 x 40 x 20 box with the edge at x = 0, z = 0 cut off by a face at CHAMFER_DEG to
    the wall (60 degrees to the floor: a crease the wall and chamfer meet across smoothly
    by MMM's 35-degree rule, the floor sharply)."""
    a = np.radians(CHAMFER_DEG)
    n = np.array([np.cos(a), 0.0, np.sin(a)])
    box = m3.Manifold.cube([40.0, 40.0, 20.0]).trim_by_plane(list(n), float(n @ [0.0, 0.0, 6.0]))
    return box.refine(refine) if refine > 1 else box


def _with_boss(sides):
    """The chamfered box with a cylinder boss of radius 8 on top, of so many sides."""
    return _chamfered_box(4) + m3.Manifold.cylinder(10.0, 8.0, 8.0, sides).translate([20.0, 20.0, 20.0])


def test_a_lone_crease_is_not_taken_for_a_coarse_curve():
    """Finely meshed but for the chamfer: the deflection is the boss's own (0.0006 mm),
    not the chamfer crease's sagitta (it was 0.11 mm at the 99.5th percentile)."""
    V, F = _mesh(_with_boss(256))
    typical, high = fidelity.mesh_deflection(V, F)
    assert typical < 0.002 and high < 0.002
    # and no spot is let off by the crease either
    assert fidelity.local_deflection(V, F).max() < 0.05


def test_a_coarse_curve_still_counts():
    """A 16-sided boss is a coarse curve: its strips bend on both sides, and its true
    cylinder stands 0.15 mm off them."""
    V, F = _mesh(_with_boss(16))
    typical, _ = fidelity.mesh_deflection(V, F)
    assert typical == pytest.approx(8.0 * (1 - np.cos(np.pi / 16)), rel=0.05)


def test_a_sphere_keeps_its_curvature():
    """Single triangles say nothing about where a bend stops: a triangulated sphere's
    deflection is counted from all its bends, as before (cad_hollow_sphere's cavity,
    counted from quads split into right triangles, lost 28% of them and six long
    triangles fell off its sphere)."""
    V, F = _mesh(m3.Manifold.sphere(15.0, 24))
    N, A = fidelity._tri_normals(V, F)
    typical, _ = fidelity.mesh_deflection(V, F)
    saved = fidelity._bend_continues
    try:
        fidelity._bend_continues = lambda N, A, f, g, th, bent: np.ones(len(f), dtype=bool)
        before, _ = fidelity.mesh_deflection(V, F)
    finally:
        fidelity._bend_continues = saved
    assert typical == pytest.approx(before, rel=1e-9) and typical > 0.05


def test_the_wall_and_its_chamfer_stay_flat():
    """The wall and the chamfer are one quad each, so neither grows past its seed
    ("weak"), and they meet at 30 degrees: _make_freeform clustered them into one
    B-spline cell, which no smooth surface can make follow both. Now they stay the two
    exact planes they are, and no freeform cell is left."""
    V, F = _mesh(_chamfered_box())
    sh = meshio.Shell(V, F)
    tol = auto_tolerance(float(np.linalg.norm(V.max(0) - V.min(0))), "cad", 0.9)
    seg = cad.Segmenter(sh.V, sh.F, sh.topo, tol, NULL)
    regions = seg.run()
    assert all(r.kind == "plane" for r in regions)
    a = np.radians(CHAMFER_DEG)
    chamfer = np.array([-np.cos(a), 0.0, -np.sin(a)])
    assert any(abs(abs(r.prim.n @ chamfer) - 1) < 1e-6 for r in regions)
    assert (seg.label >= 0).all()
