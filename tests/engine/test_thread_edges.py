"""The edge between two pieces of one thread's profile is the helix they meet in (cad._helix_between), worked out from the
thread rather than fitted to the mesh's corners, on a thread whose every point is known."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

import mms2c  # noqa: F401  (pins OpenBLAS before numpy is used)
from mms2c.build import _thread_surface
from mmm.cad import EDGE_FIT, _helix_between, _sample_curve
from mms2c.threads import Flank, Helical

LEAD = 2.5
PROFILE = np.array([[0.12, 7.27], [0.62, 7.27], [1.43, 8.70], [1.80, 8.70], [2.62, 7.27]])     # closed over one lead
REACH = 0.06    # mm: how far a corner may stand off the helix (4 tolerances)
CLOSE = EDGE_FIT / 4    # mm: an edge has to lie within EDGE_FIT of both its surfaces, which themselves stand near the same helix


class Surface:
    """All the edge asks of a surface: its model."""

    def __init__(self, model):
        self.model = model


def thread(hand=1, profile=PROFILE, at=(1.0, 2.0, 0.0)):
    return Helical(np.array(at, float), np.array([0, 0, 1.0]), hand * LEAD / (2 * math.pi), profile, True)


def pieces(h):
    return [Flank(h, h.profile[i], h.profile[i + 1]) for i in range(len(h.profile) - 1)]


def corners(piece, s, u0, u1, n, noise=0.0, seed=3):
    U = np.linspace(u0, u1, n)
    P = piece.point(np.full(n, float(s)), U)
    return P + np.random.default_rng(seed).uniform(-noise, noise, P.shape)


def off_helix(curve, piece, s):
    """Distance of a curve from the helix at s along the piece, in the profile's plane."""
    X = _sample_curve(curve, 4000)
    got, off, _ = piece.place(X)
    return float(np.hypot(got - s, off).max())


def test_two_neighbours_meet_in_the_exact_helix_for_many_turns():
    a, b = pieces(thread())[:2]
    P = corners(a, a.length, 0.0, 24 * 2 * math.pi, 1500, noise=0.004)
    curve = _helix_between([Surface(a), Surface(b)], P, REACH)
    assert curve is not None
    # (the interior is the true helix however the corners wander; only the end points are the corners')
    X = _sample_curve(curve, 4000)
    inner = X[20:-20]
    s, off, _ = a.place(inner)
    assert np.hypot(s - a.length, off).max() < 1e-5
    assert np.allclose(X[0], P[0]) and np.allclose(X[-1], P[-1])


def test_the_curve_runs_the_way_the_chain_does():
    a, b = pieces(thread())[:2]
    P = corners(a, a.length, 3 * 2 * math.pi, 0.0, 200)
    curve = _helix_between([Surface(a), Surface(b)], P, REACH)
    X = _sample_curve(curve, 50)
    assert np.allclose(X[0], P[0], atol=1e-9) and np.allclose(X[-1], P[-1], atol=1e-9)


def test_either_order_of_the_two_surfaces_gives_the_same_helix():
    a, b = pieces(thread())[:2]
    P = corners(a, a.length, 0.0, 2 * 2 * math.pi, 100)
    c1 = _helix_between([Surface(a), Surface(b)], P, REACH)
    c2 = _helix_between([Surface(b), Surface(a)], P, REACH)
    assert np.abs(_sample_curve(c1, 300) - _sample_curve(c2, 300)).max() < 1e-6


def test_the_last_piece_meets_the_first_a_lead_along():
    ps = pieces(thread())
    a, b = ps[-1], ps[0]
    P = corners(a, a.length, 0.0, 2 * 2 * math.pi, 100)
    curve = _helix_between([Surface(a), Surface(b)], P, REACH)
    assert curve is not None and off_helix(curve, a, a.length) < CLOSE


def test_a_left_hand_thread_has_its_helix_too():
    a, b = pieces(thread(hand=-1))[:2]
    P = corners(a, a.length, 0.0, 5 * 2 * math.pi, 300)
    curve = _helix_between([Surface(a), Surface(b)], P, REACH)
    assert curve is not None and off_helix(curve, a, a.length) < CLOSE


def test_a_coarse_mesh_still_gets_its_helix():
    # (corners on the helix, chords far off it: 12 to a turn on radius 7.3 is 0.25 mm of sag, the corners are what count)
    a, b = pieces(thread())[:2]
    P = corners(a, a.length, 0.0, 4 * 2 * math.pi, 49)
    curve = _helix_between([Surface(a), Surface(b)], P, REACH)
    assert curve is not None and off_helix(curve, a, a.length) < CLOSE


@pytest.mark.parametrize("radius", [0.5, 7.0, 40.0, 130.0, 1000.0, 5000.0])
def test_the_helix_holds_at_any_radius(radius):
    profile = PROFILE + [0.0, radius - PROFILE[:, 1].min()]
    a, b = pieces(thread(profile=profile))[:2]
    P = corners(a, a.length, 0.0, 6 * 2 * math.pi, 400)
    curve = _helix_between([Surface(a), Surface(b)], P, 4 * radius)
    assert curve is not None and off_helix(curve, a, a.length) < CLOSE


@pytest.mark.parametrize("radius", [7.0, 40.0, 130.0, 1000.0, 5000.0])
def test_a_thread_surface_holds_at_any_radius(radius):
    profile = PROFILE + [0.0, radius - PROFILE[:, 1].min()]
    a = pieces(thread(profile=profile))[0]
    surface = _thread_surface(SimpleNamespace(u0=0.0, span=4 * math.pi, lo=0.0, hi=a.length), a)
    U = np.linspace(0.1, 4 * math.pi - 0.1, 3001)
    for s in (0.0, a.length / 2, a.length):
        X = np.array([[p.X(), p.Y(), p.Z()] for p in (surface.Value(float(u), float(s)) for u in U)])
        assert np.linalg.norm(X - a.point(np.full(len(U), s), U), axis=1).max() < EDGE_FIT


def test_two_stretches_of_one_piece_have_no_helix_between_them():
    a = pieces(thread())[0]
    other = Flank(a.thread, a.p, a.q)
    P = corners(a, a.length / 2, 0.0, 2 * math.pi, 40)
    assert _helix_between([Surface(a), Surface(other)], P, REACH) is None


def test_pieces_that_share_no_corner_have_no_helix_between_them():
    ps = pieces(thread())
    P = corners(ps[0], ps[0].length, 0.0, 2 * math.pi, 40)
    assert _helix_between([Surface(ps[0]), Surface(ps[2])], P, REACH) is None


def test_pieces_of_two_threads_have_no_helix_between_them():
    a = pieces(thread())[0]
    b = pieces(thread(at=(9.0, 2.0, 0.0)))[1]
    P = corners(a, a.length, 0.0, 2 * math.pi, 40)
    assert _helix_between([Surface(a), Surface(b)], P, REACH) is None


def test_other_surfaces_have_none():
    a = pieces(thread())[0]
    P = corners(a, a.length, 0.0, 2 * math.pi, 40)
    assert _helix_between([Surface(a), Surface(None)], P, REACH) is None
    assert _helix_between([Surface(None), object()], P, REACH) is None


def test_a_chain_off_the_helix_is_left_to_the_mesh():
    # (a border that leaves the helix, where another feature took a bite out of the piece: the helix would cut across it)
    a, b = pieces(thread())[:2]
    P = corners(a, a.length, 0.0, 3 * 2 * math.pi, 200)
    P[90:110] += [0.0, 0.0, 0.2]
    assert _helix_between([Surface(a), Surface(b)], P, REACH) is None


def test_a_chain_that_doubles_back_is_left_to_the_mesh():
    a, b = pieces(thread())[:2]
    P = np.vstack([corners(a, a.length, 0.0, 2 * math.pi, 40), corners(a, a.length, 2 * math.pi, 0.5, 40)])
    assert _helix_between([Surface(a), Surface(b)], P, REACH) is None


def test_two_corners_shared_are_told_apart_by_the_chain():
    # (a two-piece profile: each meets the other at both ends of the lead)
    h = thread(profile=np.array([[0.0, 7.0], [1.25, 8.0], [2.5, 7.0]]))
    a, b = pieces(h)
    for s in (0.0, a.length):
        P = corners(a, s, 0.0, 2 * 2 * math.pi, 100)
        curve = _helix_between([Surface(a), Surface(b)], P, REACH)
        assert curve is not None and off_helix(curve, a, s) < CLOSE
