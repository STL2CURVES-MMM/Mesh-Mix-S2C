"""Segmenter._tied_side (group E, 2026-10-28): triangles that fit two surfaces and leave
an equally short border either way go to a side chosen from the shape alone, never by
which region or triangle came first."""
from types import SimpleNamespace

import numpy as np

from mmm.cad import Segmenter
from mmm.fitting import Cylinder, Plane


def _strip():
    """Two thin triangles along y = 12, z ~ 1.5: the first facet row of an r 0.5 fillet
    (axis along x at y = 12, z = 1) next to the flat top z = 1.5, tilted 11 degrees."""
    a = np.radians(11.0)
    V = np.array([[0.0, 12.0, 1.5], [10.0, 12.0, 1.5],
                  [0.0, 12.0 + 0.5 * np.sin(a), 1.0 + 0.5 * np.cos(a)],
                  [10.0, 12.0 + 0.5 * np.sin(a), 1.0 + 0.5 * np.cos(a)]])
    F = np.array([[0, 2, 1], [1, 2, 3]])
    n = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    fa = 0.5 * np.linalg.norm(n, axis=1)
    fn = n / np.linalg.norm(n, axis=1)[:, None]
    if fn[0, 2] < 0:
        fn = -fn
    return V, F, fn, fa


def _seg(regions, V, F, fn, fa):
    s = Segmenter.__new__(Segmenter)
    s.V, s.F, s.fn, s.fa, s.regions = V, F, fn, fa, regions
    return s


def _at(regions, r):
    return next(i for i, x in enumerate(regions) if x is r)


def _region(prim, kind, faces):
    return SimpleNamespace(prim=prim, sense=1.0, kind=kind, faces=np.asarray(faces))


def test_better_normal_wins_either_order():
    V, F, fn, fa = _strip()
    plane = _region(Plane(np.array([0.0, 0.0, 1.5]), np.array([0.0, 0.0, 1.0])), "plane", [])
    cyl = _region(Cylinder(np.array([0.0, 12.0, 1.0]), np.array([1.0, 0.0, 0.0]), 0.5), "cylinder", [])
    for regions in ([plane, cyl], [cyl, plane]):
        s = _seg(regions, V, F, fn, fa)
        ci = _at(regions, cyl)
        assert s._tied_side(np.array([0, 1]), 0, 1) == ci
        assert s._tied_side(np.array([0, 1]), 1, 0) == ci


def test_full_tie_is_settled_by_shape_not_order():
    V, F, fn, fa = _strip()
    # two identical planes (same normal, same kind): area, then position decide
    p = Plane(np.array([0.0, 0.0, 1.5]), np.array([0.0, 0.0, 1.0]))
    big = _region(p, "plane", [0, 1])
    small = _region(p, "plane", [0])
    for regions in ([big, small], [small, big]):
        s = _seg(regions, V, F, fn, fa)
        bi = _at(regions, big)
        assert s._tied_side(np.array([0]), 0, 1) == bi
        assert s._tied_side(np.array([0]), 1, 0) == bi
    left = _region(p, "plane", [0])
    right = _region(p, "plane", [1])   # same area, another centre: position decides
    for regions in ([left, right], [right, left]):
        s = _seg(regions, V, F, fn, fa)
        assert s._tied_side(np.array([0]), 0, 1) == s._tied_side(np.array([0]), 1, 0)
