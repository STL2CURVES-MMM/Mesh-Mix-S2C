"""The handover between stl2curves' front end and MMM's segmenter (merged engine, group A,
2026-10-28): what a locked surface of ours measures (merged.OurSurface: only over the
surface its face is built on), the closed-surface feature (features "closed"), the
competition between our seeds and MMM's own segmentation in unsure smooth areas
(merged.seeded_segmenter), and the up-front choice of method per body (merged.route).
The shapes are made here; no mesh is stored."""
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))

from mms2c import freeform, pipes  # noqa: E402
from mms2c import merged  # noqa: E402


def _flat_freeform(size=10.0, n=4):
    """A flat height field over the plane z = 0, its domain [0, size] each way."""
    t = freeform._knots(0.0, size, n)
    C = np.zeros((len(t) - 4, len(t) - 4))
    return freeform.Freeform("plane", np.zeros(3), np.array([0.0, 0, 1]), np.array([1.0, 0, 0]),
                             0.0, t, t, C)


def _straight_pipe(length=10.0, r=1.0):
    """A tube of radius r round the x axis from 0 to length."""
    t = pipes._knots(0.0, length, 2)
    xs = np.linspace(0.0, length, len(t) - 4)
    C = np.c_[xs, np.zeros_like(xs), np.zeros_like(xs)]
    m = pipes.Pipe(t, C, r)
    m.angles = (-math.pi, math.pi)
    return m


def test_freeform_model_reaches_past_its_surface_but_inside_does_not():
    m = _flat_freeform()
    P = np.array([[5.0, 5.0, 0.0], [40.0, 5.0, 0.0]])
    # the model's distance says both points lie on it (the spline extends) ...
    assert np.allclose(m.signed(P), 0.0, atol=1e-9)
    # ... but only the first lies over the surface a face is built on
    assert m.inside(P).tolist() == [True, False]
    u0, u1, v0, v1 = m.surface().Bounds()
    assert u0 <= 5.0 <= u1 and not (u0 <= 40.0 <= u1)


def test_pipe_inside_stops_at_the_spine_ends():
    m = _straight_pipe()
    P = np.array([[5.0, 1.0, 0.0], [10.5, 1.0, 0.0], [-0.5, 0.0, 1.0]])
    assert m.inside(P).tolist() == [True, False, False]


def test_our_surface_measures_only_over_its_domain():
    m = _flat_freeform()
    s = merged.OurSurface(m, m.surface(), 1.0, m.inside)
    d = s.distance(np.array([[5.0, 5.0, 0.01], [40.0, 5.0, 0.0]]))
    assert d[0] == pytest.approx(0.01, abs=1e-6)
    assert np.isinf(d[1])
    # (without a test, as before: the model's distance everywhere)
    assert np.isfinite(merged.OurSurface(m, m.surface(), 1.0).distance(np.array([[40.0, 5.0, 0.0]]))).all()


def _torus_mesh(R=25.0, r=6.0, nu=96, nv=40):
    """A closed torus round the z axis, its corners on the true surface."""
    u = np.linspace(0, 2 * np.pi, nu, endpoint=False)
    v = np.linspace(0, 2 * np.pi, nv, endpoint=False)
    U, V = np.meshgrid(u, v, indexing="ij")
    pts = np.c_[((R + r * np.cos(V)) * np.cos(U)).ravel(), ((R + r * np.cos(V)) * np.sin(U)).ravel(),
                (r * np.sin(V)).ravel()]
    idx = lambda i, j: (i % nu) * nv + (j % nv)  # noqa: E731
    tris = []
    for i in range(nu):
        for j in range(nv):
            a, b, c, d = idx(i, j), idx(i + 1, j), idx(i + 1, j + 1), idx(i, j + 1)
            tris += [(a, b, c), (a, c, d)]
    return pts, np.array(tris, dtype=np.int64)


def test_a_whole_torus_is_one_closed_feature_and_builds():
    from mms2c import convert, features
    pts, tris = _torus_mesh()
    mesh, feats, _ = features.analyze(pts, tris)
    rings = [f for f in feats if f.kind == "closed"]
    assert len(rings) == 1 and rings[0].model.kind == "torus"
    rc, _, r = rings[0].model.circle
    assert rc == pytest.approx(25.0, abs=1e-3) and r == pytest.approx(6.0, abs=1e-3)
    assert len(rings[0].facets) == len(mesh.fn)
    # handed to MMM as one locked torus
    surf = merged.mmm_surface(rings[0], mesh)
    assert surf.kind == "torus" and surf.locked
    # and our own builder makes it one face, a valid solid of the torus's volume
    shape, _ = convert.stl_to_solid((pts, tris), 0.01)
    from OCP.TopAbs import TopAbs_FACE
    assert convert.count(shape, TopAbs_FACE) == 1
    assert convert.volume(shape) == pytest.approx(2 * np.pi ** 2 * 25.0 * 6.0 ** 2, rel=1e-3)


# ---- our seeds against MMM's own segmentation (merged.seeded_segmenter) ----


def test_winner_prefers_fewer_regions_but_exact_surfaces_unless_freeform_saves_enough():
    # (third: the area left freeform; equal where both use some, so the counts decide)
    w = merged._winner
    assert w((12, True, 0.0), (3, True, 0.0)) == "mmm"          # fewer, both exact
    assert w((3, True, 0.0), (3, True, 0.0)) == "ours"          # a tie keeps ours
    assert w((9, False, 5.0), (4, False, 5.0)) == "mmm"
    assert w((5, False, 5.0), (8, False, 5.0)) == "ours"


def test_winner_prefers_the_way_with_less_freeform_area():
    """Closer to the design beats fewer regions (Tuna and Duvdivan, 2026-11-01)."""
    w = merged._winner
    assert w((1, False, 120.0), (8, True, 0.0)) == "mmm"        # a frame: 8 exact faces beat 1 freeform
    assert w((3, False, 10.0), (12, False, 4.0)) == "mmm"       # less than half as much freeform
    assert w((12, False, 4.0), (3, False, 10.0)) == "ours"
    assert w((4, False, 10.0), (3, False, 6.0)) == "mmm"        # 0.6 of it: the counts decide
    assert w((1, False, 50.0), (9, True, 0.0), True) == "mmm"   # all exact with the corners on them


def test_held_report_passes_progress_and_holds_messages():
    seen = []

    class Rep:
        def log(self, m):
            seen.append(("log", m))

        def progress(self, f):
            seen.append(("progress", f))
    h = merged._Held(Rep())
    h.log("found")
    h.progress(0.5)
    assert seen == [("progress", 0.5)]
    h.replay()
    assert seen[-1] == ("log", "found")


def _torus_shell():
    from mmm import meshio
    pts, tris = _torus_mesh(nu=72, nv=24)
    return meshio.Shell(pts, tris)


def _seg_tol():
    return 0.034


def test_sure_seed_is_segmented_once_as_before():
    from mmm import fitting
    shell = _torus_shell()
    prim = fitting.Torus(np.zeros(3), np.array([0.0, 0, 1]), 25.0, 6.0)
    prim.locked = True
    seeds = [(prim, np.arange(len(shell.F)))]
    calls = []
    orig = merged._segment

    def counted(*a, **k):
        calls.append(1)
        return orig(*a, **k)
    merged._segment = counted
    try:
        seg = merged.seeded_segmenter(shell, seeds, _seg_tol(), 0.9)
    finally:
        merged._segment = orig
    assert len(calls) == 1
    assert len([r for r in seg.regions if len(r.faces)]) == 1


def test_unsure_pieces_of_one_smooth_area_give_way_to_mmms_one_surface():
    """Three straight cylinders laid over thirds of a ring (each fits it only near its
    middle, as our short pieces fit a curving fillet), locked: MMM's own segmentation of
    the ring, one torus, has fewer regions and is kept."""
    from mmm import fitting
    shell = _torus_shell()
    cen = shell.V[shell.F].mean(1)
    ang = np.mod(np.arctan2(cen[:, 1], cen[:, 0]), 2 * np.pi)
    seeds = []
    for k in range(3):
        mid = (k + 0.5) * 2 * np.pi / 3
        a = 25.0 * np.array([np.cos(mid), np.sin(mid), 0.0])
        d = np.array([-np.sin(mid), np.cos(mid), 0.0])
        prim = fitting.Cylinder(a, d, 6.0)
        prim.locked = True
        seeds.append((prim, np.nonzero((ang >= k * 2 * np.pi / 3) & (ang < (k + 1) * 2 * np.pi / 3))[0]))
    logs = []

    class Rep:
        def log(self, m):
            logs.append(m)

        def __getattr__(self, name):
            return lambda *a, **k: None
    seg = merged.seeded_segmenter(shell, seeds, _seg_tol(), 0.9, Rep())
    regs = [r for r in seg.regions if len(r.faces)]
    assert not any(getattr(r.prim, "locked", False) for r in regs)
    assert len(regs) < 3
    assert any("tried there as well" in m and "kept in 1," in m for m in logs)


# ---- the method per body, chosen before any feature is looked for (merged._Route) ----


def _box_mesh(size=20.0):
    pts = np.array([[x, y, z] for x in (0, size) for y in (0, size) for z in (0, size)], float)
    tris = np.array([[0, 1, 3], [0, 3, 2], [4, 6, 7], [4, 7, 5], [0, 4, 5], [0, 5, 1],
                     [2, 3, 7], [2, 7, 6], [0, 2, 6], [0, 6, 4], [1, 5, 7], [1, 7, 3]], dtype=np.int64)
    return pts, tris


def _blob_mesh(n=40):
    """A lumpy ball (an organic shape): no flat faces, no single surface, no sweep."""
    u = np.linspace(0, 2 * np.pi, n, endpoint=False)
    v = np.linspace(0.15, np.pi - 0.15, n // 2)
    U, V = np.meshgrid(u, v, indexing="ij")
    R = 20.0 + 3.0 * np.sin(3 * U) * np.sin(2 * V) + 2.0 * np.cos(5 * V)
    pts = np.c_[(R * np.sin(V) * np.cos(U)).ravel(), (R * np.sin(V) * np.sin(U)).ravel(), (R * np.cos(V)).ravel()]
    m = len(v)
    tris = []
    for i in range(n):
        for j in range(m - 1):
            a, b = i * m + j, ((i + 1) % n) * m + j
            tris += [(a, b, b + 1), (a, b + 1, a + 1)]
    top = len(pts)
    bottom = top + 1
    pts = np.vstack([pts, [[0, 0, (20.0 + 2.0) * 1.0]], [[0, 0, -(20.0 - 2.0)]]])
    for i in range(n):
        a, b = i * m, ((i + 1) % n) * m
        tris.append((b, a, top))
        tris.append((a + m - 1, b + m - 1, bottom))
    return pts, np.array(tris, dtype=np.int64)


class _Log:
    def __init__(self):
        self.lines = []

    def log(self, m):
        self.lines.append(m)

    def __getattr__(self, name):
        return lambda *a, **k: None


def _route(preset, *meshes):
    from mmm import pipeline
    off = 0
    P, G = [], []
    for pts, tris in meshes:
        P.append(pts)
        G.append(tris + off)
        off += len(pts)
    opts = pipeline.Options(preset=preset, quality=0.9)
    log = _Log()
    r = merged._Route(opts, log)
    return r(np.vstack(P), G), r, log


def test_auto_routes_each_body_by_mmms_own_choice():
    want, r, log = _route("auto", _box_mesh(), _blob_mesh())
    assert want == [True, False]
    assert len(r.pieces) == 2
    assert any("smooth surfaces" in m and "skipped" in m for m in log.lines)


def test_a_chosen_preset_applies_to_every_body():
    assert _route("cad", _box_mesh(), _blob_mesh())[0] == [True, True]
    want, _, log = _route("smooth", _box_mesh(), _blob_mesh())
    assert want == [False, False] and any("as chosen" in m for m in log.lines)


def test_prepare_skips_the_bodies_not_chosen():
    from mms2c import convert
    pts, tris = _torus_mesh(nu=48, nv=16)
    parts, _, _ = convert.prepare((pts, tris), 0.01, analyze=lambda p, g: [False] * len(g))
    assert [len(f) for _, f, _ in parts] == [0]
    parts, _, _ = convert.prepare((pts, tris), 0.01, analyze=lambda p, g: [True] * len(g))
    assert [f.kind for f in parts[0][1]] == ["closed"]


class _Plane:
    kind = "plane"

    def distance(self, P):
        return np.abs(np.asarray(P)[:, 2])


class _Seg:
    """Just what the comparison reads of a segmentation: two triangles, each a region on z = 0,
    the second with one corner 0.03 mm off it."""

    def __init__(self):
        self.V = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0.03]], dtype=float)
        self.F = np.array([[0, 1, 2], [1, 3, 2]])
        self.fa = np.array([0.5, 0.5])
        self.tol = 0.05
        self.label = np.array([0, 1])
        self.regions = [merged.mcad.Region(_Plane(), np.array([0]), 1), merged.mcad.Region(_Plane(), np.array([1]), 1)]
        self.strays = np.array([False, False])

    def face_error(self, prim, sense, faces):
        return np.where(self.strays[faces], np.inf, 0.0), np.ones(len(faces))


def test_exact_surface_forced_onto_a_blend_counts_as_off_the_design():
    """Inside the tolerance, but not on it: no closer to the design than a freeform face."""
    seg = _Seg()
    off = merged._off_design(seg)
    assert off.tolist() == [False, True]
    assert merged._census(seg, np.array([0, 1]), off) == (2, False, 0.5)     # exact, but not on the design
    assert merged._census(seg, np.array([0]), off) == (1, True, 0.0)
    assert merged._on_surfaces(seg, np.array([0]))
    assert not merged._on_surfaces(seg, np.array([1]))


def test_a_triangle_whose_middle_strays_from_its_surface_is_off_the_design_with_its_corners_on_it():
    """A torus through head's three rings of corners holds every one of them."""
    seg = _Seg()
    seg.strays = np.array([True, False])
    assert merged._off_design(seg).tolist() == [True, True]


def test_compose_keeps_each_cluster_whole():
    a, b = _Seg(), _Seg()
    # A has two regions, B one over both, and a further triangle-free region stays ours
    b.label = np.array([0, 0])
    b.regions = [merged.mcad.Region(_Plane(), np.array([0, 1]), 1)]
    cluster = np.array([0, 0])
    kept = merged._compose(a, b, cluster, np.array([True]))
    assert kept.label.tolist() == [0, 0] and len(kept.regions) == 1
    a2 = _Seg()
    kept = merged._compose(a2, b, cluster, np.array([False]))
    assert kept.label.tolist() == [0, 1] and len(kept.regions) == 2


class _Facets:
    """Just what blends._without_planes reads: flat strips 10 mm long in a row, of these areas, each bending 5 degrees into the next."""

    def __init__(self, sizes):
        n = len(sizes)
        turn = np.radians(5.0) * np.arange(n)
        at = np.r_[np.zeros((1, 2)), np.cumsum(np.c_[np.cos(turn), np.sin(turn)] * (np.array(sizes, dtype=float) / 10.0)[:, None], axis=0)]
        self.pts = np.vstack([np.c_[at, np.zeros(n + 1)], np.c_[at, np.full(n + 1, 10.0)]])
        self.tris = np.array([t for i in range(n) for t in ((i, i + 1, i + n + 2), (i, i + n + 2, i + n + 1))])
        a, b, c = (self.pts[self.tris[:, k]] for k in range(3))
        cross = np.cross(b - a, c - a)
        self.tarea = np.linalg.norm(cross, axis=1) / 2
        self.tn = cross / (2 * self.tarea)[:, None]
        self.facet_of = np.repeat(np.arange(n), 2)
        self.farea = np.bincount(self.facet_of, self.tarea, n)
        self.fn = self.tn[::2]
        self.ftris = [np.array([2 * i, 2 * i + 1]) for i in range(n)]
        self.nbrs = [[j for j in (i - 1, i + 1) if 0 <= j < n] for i in range(n)]


def test_a_designed_plane_comes_out_of_a_freeform_area_and_a_coarse_curve_does_not():
    from mms2c import blends
    mesh = _Facets([0.3, 0.2, 21.0, 0.3, 0.2])
    soft, free = set(range(5)), set(range(5))
    areas = blends._without_planes(mesh, soft, free, np.full(5, -1), lambda a: [a])
    assert sorted(sorted(a.tolist()) for a in areas) == [[0, 1], [3, 4]]
    assert 2 not in soft and 2 not in free
    # facets alike in size are a coarse curve's strips
    mesh = _Facets([3.0, 3.1, 3.0, 2.9])
    soft, free = set(range(4)), set(range(4))
    areas = blends._without_planes(mesh, soft, free, np.full(4, -1), lambda a: [a])
    assert [sorted(a.tolist()) for a in areas] == [[0, 1, 2, 3]] and soft == {0, 1, 2, 3}
