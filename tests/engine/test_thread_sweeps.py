"""A screw sweep's pieces (threads.CurveFlank, the arcs of threads._swept_profile) end where their rows end, and the profile
distance gives the same answer however many points it is asked about at once."""
import math

import numpy as np
import pytest

import mms2c  # noqa: F401  (pins OpenBLAS before numpy is used)
from mms2c import threads
from mms2c.threads import ArcFlank, CurveFlank, Helical

LEAD = 6.0


def model(inside=True):
    outline = np.array([[0.0, 8.0], [1.0, 9.0], [3.0, 9.0], [4.0, 8.0], [6.0, 8.0]])
    return Helical(np.zeros(3), np.array([0, 0, 1.0]), LEAD / (2 * math.pi), outline, inside)


def curve_rows():
    # (a quarter of an ellipse in the profile's plane, phase 0.5 to 2.5: neither straight nor an arc)
    t = np.linspace(0.0, math.pi / 2, 25)
    return np.c_[0.5 + 2.0 * np.sin(t), 8.0 + 1.0 * (1 - np.cos(t))]


def at_phase(h, z0, rho, u=0.3):
    """The surface's point over profile point (z0, rho), turned u round the helix."""
    return np.array([h.point(z, r, u) for z, r in zip(np.atleast_1d(z0), np.atleast_1d(rho))])


@pytest.mark.parametrize("inside", [True, False])
def test_a_curve_piece_places_points_past_its_ends_past_them(inside):
    h = model(inside)
    rows = curve_rows()
    c = CurveFlank(h, rows)
    # along the last segment's line, 2 mm past the end; along the first's, 1 mm before the start
    t_end = (rows[-1] - rows[-2]) / np.linalg.norm(rows[-1] - rows[-2])
    t_start = (rows[1] - rows[0]) / np.linalg.norm(rows[1] - rows[0])
    past, before = rows[-1] + 2.0 * t_end, rows[0] - 1.0 * t_start
    s, off, _ = c.place(at_phase(h, [past[0], before[0]], [past[1], before[1]]))
    assert s[0] == pytest.approx(c.length + 2.0, abs=1e-9)
    assert s[1] == pytest.approx(-1.0, abs=1e-9)
    assert np.abs(off).max() < 1e-9
    # so a corner that far past is no longer within a piece's reach
    tol = 0.03
    assert not ((s >= -tol) & (s <= c.length + tol)).any()


def test_a_curve_piece_places_its_own_rows_as_before():
    h = model()
    rows = curve_rows()
    c = CurveFlank(h, rows)
    s, off, _ = c.place(at_phase(h, rows[:, 0], rows[:, 1]))
    assert np.allclose(s, c.cum, atol=1e-9)
    assert np.abs(off).max() < 1e-9


def test_a_sweeps_arc_passes_through_its_end_rows():
    rng = np.random.default_rng(3)
    centre, radius = np.array([2.0, 7.0]), 1.2
    ang = np.linspace(math.radians(-150), math.radians(-30), 15)
    run = np.c_[centre[0] + radius * np.cos(ang), centre[1] + radius * np.sin(ang)]
    run[1:-1] += rng.normal(0, 2e-4, (len(run) - 2, 2))     # (rows a hair off the circle, the ends where neighbours end)
    got = threads._arc_through_ends(run, 1e-3)
    assert got is not None
    arc = ArcFlank(model(), run[0], run[-1], *got)
    assert np.allclose(np.array(arc.at(0.0), float), run[0], atol=1e-12)
    assert np.allclose(np.array(arc.at(arc.length), float), run[-1], atol=1e-12)


def test_a_stretch_no_circle_through_its_ends_holds_is_not_an_arc():
    t = np.linspace(0.0, 1.0, 15)
    run = np.c_[t, t ** 3]                   # (curving, but no circle through its ends holds it)
    assert threads._arc_through_ends(run, 1e-3) is None


def test_a_stretch_the_long_way_round_is_not_an_arc():
    centre, radius = np.array([0.0, 0.0]), 1.0
    ang = np.linspace(math.radians(-30), math.radians(210), 25)   # 240 degrees: ArcFlank turns the short way
    run = np.c_[centre[0] + radius * np.cos(ang), centre[1] + radius * np.sin(ang)]
    assert threads._arc_through_ends(run, 1e-3) is None


def test_the_profile_distance_is_the_same_in_batches(monkeypatch):
    rng = np.random.default_rng(5)
    rows = curve_rows()
    prof = np.r_[[[0.0, 8.0]], rows, [[4.0, 9.0], [5.0, 8.0], [6.0, 8.0]]]
    h = Helical(np.zeros(3), np.array([0, 0, 1.0]), LEAD / (2 * math.pi), prof, True)
    z0, rho = rng.uniform(0, LEAD, 3000), rng.uniform(7.5, 9.5, 3000)
    whole = h._nearest(z0, rho)
    monkeypatch.setattr(threads, "NEAREST_CELLS", 1000)
    parts = h._nearest(z0, rho)
    for x, y in zip(whole, parts):
        assert np.array_equal(x, y)


def _twisted_rod_and_bore(lead=60.0, height=20.0, n=180, rows=40):
    """A twisted elliptical rod's side wall (a 6 x 4 ellipse turned by z / pitch as it rises) and, apart from it, a coaxial bore of
    radius 2: (points, triangles, unit normals out of each)."""
    pitch = lead / (2 * math.pi)
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    walls = [(np.c_[6 * np.cos(t), 4 * np.sin(t)], pitch, 1.0), (np.c_[2 * np.cos(t), 2 * np.sin(t)], 0.0, -1.0)]
    pts, tris, normals = [], [], []
    for prof, p, out in walls:
        base = sum(len(x) for x in pts)
        V = []
        for k in range(rows + 1):
            z = height * k / rows
            a = z / p if p else 0.0
            c, s = math.cos(a), math.sin(a)
            V.append(np.c_[prof[:, 0] * c - prof[:, 1] * s, prof[:, 0] * s + prof[:, 1] * c, np.full(n, z)])
        V = np.vstack(V)
        F = []
        for k in range(rows):
            for i in range(n):
                q0, q1 = k * n + i, k * n + (i + 1) % n
                F += [[q0, q1, q1 + n], [q0, q1 + n, q0 + n]]
        F = np.array(F)
        N = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
        N /= np.linalg.norm(N, axis=1)[:, None]
        radial = V[F].mean(axis=1) * [1, 1, 0]
        N *= out * np.sign(np.einsum("ij,ij->i", N, radial))[:, None]
        pts.append(V)
        tris.append(F + base)
        normals.append(N)
    return np.vstack(pts), np.vstack(tris), np.vstack(normals)


def test_a_sweeps_corners_reach_past_its_flanks_ring_but_not_a_separate_bore():
    from types import SimpleNamespace
    V, F, N = _twisted_rod_and_bore()
    mesh = SimpleNamespace(pts=V, tris=F, tn=N)
    h = Helical(np.zeros(3), np.array([0, 0, 1.0]), 60.0 / (2 * math.pi), np.array([[0.0, 4.0], [30.0, 6.0], [60.0, 4.0]]), True)
    P = threads._sweep_corners(mesh, h, (5.0, 5.3))       # (a ring reading only the middle of the profile's radii)
    assert P is not None
    rho = np.hypot(P[:, 0], P[:, 1])
    assert rho.min() == pytest.approx(4.0, abs=0.01) and rho.max() == pytest.approx(6.0, abs=0.01)
    assert not (np.abs(rho - 2.0) < 0.01).any()           # (the bore agrees with any motion along the axis, but isn't joined)


def _cylinder_and_end_face(n=120, rows=10):
    """A cylinder of radius 15 rising 5 from z = 0, joined along its bottom circle to a flat end face out to radius 25:
    (points, triangles, unit normals out of the material, which lies inside the cylinder and above the face)."""
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    ring = lambda r, z: np.c_[r * np.cos(t), r * np.sin(t), np.full(n, z)]  # noqa: E731
    V = np.vstack([ring(15.0, 5.0 * k / rows) for k in range(rows + 1)] + [ring(25.0, 0.0)])
    F = []
    for k in range(rows):
        for i in range(n):
            q0, q1 = k * n + i, k * n + (i + 1) % n
            F += [[q0, q1, q1 + n], [q0, q1 + n, q0 + n]]
    outer = (rows + 1) * n
    for i in range(n):
        q0, q1 = i, (i + 1) % n                     # (the cylinder's bottom circle, shared with the end face)
        F += [[q0, outer + q1, q1], [q0, outer + i, outer + q1]]
    F = np.array(F)
    X = V[F].mean(axis=1)
    N = np.zeros((len(F), 3))
    side = np.arange(len(F)) < 2 * rows * n
    N[side] = X[side] * [1, 1, 0] / np.linalg.norm(X[side] * [1, 1, 0], axis=1)[:, None]
    N[~side] = [0, 0, -1.0]
    return V, F, N


@pytest.mark.parametrize("lead, reaches_the_end_face", [(3.0, True), (300.0, False)])
def test_the_joined_corners_run_over_an_end_face_on_a_fine_lead(lead, reaches_the_end_face):
    # (why only a screw sweep's profile is read this way: on a thread's fine lead an end face is nearly square to the motion too)
    from types import SimpleNamespace
    V, F, N = _cylinder_and_end_face()
    h = Helical(np.zeros(3), np.array([0, 0, 1.0]), lead / (2 * math.pi), np.array([[0.0, 15.0], [lead, 15.0]]), True)
    P = threads._sweep_corners(SimpleNamespace(pts=V, tris=F, tn=N), h, (14.9, 15.1))
    assert P is not None
    assert (np.hypot(P[:, 0], P[:, 1]).max() > 24.9) == reaches_the_end_face


def test_a_motion_that_is_a_thread_is_never_read_as_a_sweep():
    from mms2c import screws
    # (twist_drill_s2's motion: a thread by its winding, 1.19 turns, and an exact sweep too)
    r = {"members": 2964, "flank_members": 404, "flank_twisted": 418, "turns": 1.1944, "around": 353.857, "D3": 0.0009,
         "Dr": 1.3035, "depth": 1.043, "lead": 50.0003, "sc": 1.0}
    assert screws.thread_verdict(r) and screws.sweep_verdict(r)
    assert not screws.sweep_only(r)
