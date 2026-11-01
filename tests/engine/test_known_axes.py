"""Surfaces on known axes (2026-10-28, b13): MMM's segmenter also fits each seed on the
axes of the surfaces found so far, with only the profile free (mmm/fitting.py on_axis,
mmm/cad.py Segmenter._axis_hypotheses), so a narrow band such as a hole's chamfer is
found whole from a few of its own triangles. The shapes are made here; no mesh is
stored."""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
m3 = pytest.importorskip("manifold3d")

from mmm import cad, fitting, meshio  # noqa: E402
from mmm.pipeline import auto_tolerance  # noqa: E402
from mmm.report import NULL  # noqa: E402

AXIS = (np.zeros(3), np.array([0.0, 0.0, 1.0]))


def _ring(r, z, n=24):
    t = np.linspace(0, 2 * np.pi, n, endpoint=False)
    return np.c_[r * np.cos(t), r * np.sin(t), np.full(n, z)]


@pytest.mark.parametrize("kind,points,check", [
    ("cylinder", np.r_[_ring(5, 0), _ring(5, 2)], lambda p: abs(p.r - 5) < 1e-9),
    ("cone", np.r_[_ring(5, 0), _ring(6, 1)], lambda p: abs(np.degrees(p.alpha) - 45) < 1e-6),
    ("torus", np.r_[[_ring(10 + 2 * np.cos(a), 2 * np.sin(a)) for a in (0.2, 0.7, 1.2)]].reshape(-1, 3),
     lambda p: abs(p.R - 10) < 1e-6 and abs(p.r - 2) < 1e-6),
    ("sphere", np.r_[[_ring(3 * np.cos(a), 3 * np.sin(a) + 1) for a in (0.1, 0.5, 0.9)]].reshape(-1, 3),
     lambda p: abs(p.r - 3) < 1e-6 and np.allclose(p.c, [0, 0, 1])),
])
def test_a_profile_on_a_known_axis_fits_its_points(kind, points, check):
    p = fitting.on_axis(kind, *AXIS, points)
    assert p.kind == kind and check(p)
    assert p.distance(points).max() < 1e-6
    # a refit keeps the axis, and is the same surface
    q = p.refit(points)
    assert np.allclose(q.kd, AXIS[1]) and p.same_as(q, 1e-3, 20.0)


def test_pieces_of_one_cone_on_one_axis_are_one_surface():
    """Two pieces of one chamfer, fitted apart, merge (_merge_equal asks same_as); a cone
    on another axis, or of another slope, doesn't."""
    a = fitting.on_axis("cone", *AXIS, np.r_[_ring(5, 0)[:6], _ring(6, 1)[:6]])
    b = fitting.on_axis("cone", *AXIS, np.r_[_ring(5, 0)[12:18], _ring(6, 1)[12:18]])
    assert a.same_as(b, 1e-3, 20.0)
    c = fitting.on_axis("cone", *AXIS, np.r_[_ring(5, 0), _ring(6.5, 1)])
    assert not a.same_as(c, 1e-3, 20.0)
    d = fitting.on_axis("cone", np.array([1.0, 0, 0]), AXIS[1], np.r_[_ring(5, 0), _ring(6, 1)] + [1, 0, 0])
    assert not a.same_as(d, 1e-3, 20.0)


def _chamfered_hole(sides=96, chamfer=0.3):
    """An 80 x 80 x 6 plate with a hole of radius 4 and a narrow 45-degree chamfer at
    each end."""
    p = m3.Manifold.cube([80.0, 80.0, 6.0]).translate([-40.0, -40.0, 0.0])
    p = p - m3.Manifold.cylinder(8.0, 4.0, 4.0, sides).translate([0.0, 0.0, -1.0])
    e = 0.001
    p = p - m3.Manifold.cylinder(chamfer + e, 4.0, 4.0 + chamfer + e, sides).translate([0.0, 0.0, 6.0 - chamfer])
    p = p - m3.Manifold.cylinder(chamfer + e, 4.0 + chamfer + e, 4.0, sides).translate([0.0, 0.0, -e])
    mm = p.to_mesh()
    V, F = np.asarray(mm.vert_properties, dtype=float)[:, :3], np.asarray(mm.tri_verts, dtype=np.int64)
    sh = meshio.Shell(V, F)
    tol = auto_tolerance(float(np.linalg.norm(V.max(0) - V.min(0))), "cad", 0.9)
    return cad.Segmenter(sh.V, sh.F, sh.topo, tol, NULL)


def _band(seg, top):
    """The top (or bottom) chamfer's triangles: facing 45 degrees from the axis."""
    c = seg.V[seg.F].mean(1)
    rho = np.hypot(c[:, 0], c[:, 1])
    return np.nonzero((np.abs(np.abs(seg.fn[:, 2]) - np.sqrt(0.5)) < 0.05) & (rho < 5)
                      & ((c[:, 2] > 3) if top else (c[:, 2] < 3)))[0]


def test_a_chamfer_is_found_whole_from_one_quad_on_its_holes_axis():
    """With the hole's wall found (all the way round), one quad of the chamfer beside it
    is enough: a cone on the wall's axis, only its radius and slope fitted, takes in the
    whole band, and nothing else."""
    seg = _chamfered_hole()
    wall = np.nonzero((np.abs(seg.fn[:, 2]) < 0.05) & (np.hypot(*seg.V[seg.F].mean(1)[:, :2].T) < 4.2))[0]
    cyl = fitting.Cylinder(np.array([0.1, 0.0, 0.0]), np.array([0.0, 0.01, 1.0]), 4.0).refit(
        seg.V[np.unique(seg.F[wall])])
    seg.regions.append(cad.Region(cyl, wall, seg.sense_of(cyl, wall)))
    seg.label[wall] = 0
    band = _band(seg, top=True)
    seed = band[:2]
    hyps = seg._axis_hypotheses(seed)
    cones = [h for h in hyps if h[2].kind == "cone"]
    assert cones
    area, _, cone, _, reg = max(cones, key=lambda h: h[0])
    assert set(reg.tolist()) == set(band.tolist())
    assert abs(np.degrees(cone.alpha) - 45) < 1.0 and abs(abs(cone.d @ [0, 0, 1]) - 1) < 1e-6


def test_a_narrow_arc_lends_no_axis():
    """A cylinder fitted over a narrow arc may have its axis far off: it lends it only
    once it reaches AXIS_AROUND_DEG round."""
    seg = _chamfered_hole()
    c = seg.V[seg.F].mean(1)
    wall = np.nonzero((np.abs(seg.fn[:, 2]) < 0.05) & (np.hypot(c[:, 0], c[:, 1]) < 4.2))[0]
    ang = np.degrees(np.arctan2(c[wall, 1], c[wall, 0]))
    narrow = wall[(ang > 0) & (ang < 60)]
    cyl = fitting.Cylinder(np.zeros(3), np.array([0.0, 0.0, 1.0]), 4.0)
    seg.regions.append(cad.Region(cyl, narrow, 1.0))
    assert seg._known_axes() == []
    seg.regions.append(cad.Region(cyl, wall, 1.0))
    assert len(seg._known_axes()) == 1


def test_the_plate_converts_as_before():
    """The whole segmentation: the hole, both chamfers and the faces, nine regions."""
    seg = _chamfered_hole()
    kinds = sorted(r.kind for r in seg.run())
    assert kinds.count("cone") == 2 and kinds.count("cylinder") == 1 and len(kinds) == 9
