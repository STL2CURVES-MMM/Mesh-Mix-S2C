"""A rounded piece of a thread's profile is an arc swept along the helix (threads.ArcFlank), and its surface follows it (build._thread_surface), on a thread whose every point is known."""
import math
from types import SimpleNamespace

import numpy as np
import pytest

import mms2c  # noqa: F401  (pins OpenBLAS before numpy is used)
from mms2c import screws
from mms2c.build import _thread_surface
from mms2c.threads import ArcFlank, Flank, Helical, pieces_of

LEAD = 3.0
RADIUS = 0.6
CENTRE = np.array([1.5, 7.6])       # a root rounded by a circle below the flanks' meeting point: it turns downward through its lowest point
HALF = math.radians(60)             # the arc's half angle either side of straight down
P = CENTRE + RADIUS * np.array([-math.sin(HALF), -math.cos(HALF)])
Q = CENTRE + RADIUS * np.array([math.sin(HALF), -math.cos(HALF)])


def thread(hand=1, inside=True, k=0.0):
    outline = np.array([[0.0, 8.6], [P[0], P[1]], [Q[0], Q[1]], [3.0, 8.6]])
    return Helical(np.array([1.0, 2.0, 0.0]), np.array([0, 0, 1.0]), hand * LEAD / (2 * math.pi), outline, inside, k, 0.0)


def arc(**kw):
    return ArcFlank(thread(**kw), P, Q, CENTRE, RADIUS)


def test_its_length_and_ends():
    a = arc()
    assert a.length == pytest.approx(2 * HALF * RADIUS)
    assert np.allclose(np.array(a.at(0.0), float), P)
    assert np.allclose(np.array(a.at(a.length), float), Q)
    # (the middle of it is the root's lowest point)
    assert float(a.at(a.length / 2)[1]) == pytest.approx(CENTRE[1] - RADIUS)


@pytest.mark.parametrize("hand", [1, -1])
@pytest.mark.parametrize("k", [0.0, 0.03])
def test_a_point_of_it_is_placed_back_where_it_came_from(hand, k):
    a = arc(hand=hand, k=k)
    s = np.linspace(0.02, a.length - 0.02, 7)
    U = np.linspace(-9.0, 21.0, 7)
    got_s, off, got_U = a.place(a.point(s, U))
    assert np.allclose(got_s, s, atol=1e-9)
    assert np.allclose(off, 0.0, atol=1e-9)
    assert np.allclose(got_U, U, atol=1e-9)


@pytest.mark.parametrize("inside", [True, False])
def test_away_from_the_material_is_positive(inside):
    """Outward from the root of a bolt is out of its material only upward: into the groove's open space, towards the circle's centre. On a nut it is the other way."""
    a = arc(inside=inside)
    h = a.thread
    # (at angle 0 round the axis, where the thread's own e1 points)
    point_on = a.point(np.array([a.length / 2]), np.array([0.0]))
    outward = point_on + 0.05 * h.e1
    expected = 0.05 if inside else -0.05
    assert float(a.signed(outward)[0]) == pytest.approx(expected, abs=1e-3)


def test_its_normal_is_square_to_the_surface():
    a = arc()
    s, U = np.array([0.1, a.length / 2, a.length - 0.1]), np.array([0.3, 2.0, 5.0])
    n = a.normal(a.point(s, U))
    eps = 1e-5
    along = (a.point(s + eps, U) - a.point(s - eps, U)) / (2 * eps)
    round_it = (a.point(s, U + eps) - a.point(s, U - eps)) / (2 * eps)
    assert np.abs(np.einsum("ij,ij->i", n, along)).max() < 1e-6
    assert np.abs(np.einsum("ij,ij->i", n, round_it)).max() < 1e-6


@pytest.mark.parametrize("hand", [1, -1])
def test_its_surface_follows_it(hand):
    """Built as a surface, it stays within the fit a thread's surface is held to, off its own helices."""
    from OCP.GeomAdaptor import GeomAdaptor_Surface
    a = arc(hand=hand)
    feature = SimpleNamespace(u0=0.0, span=2 * math.pi * 4, lo=0.0, hi=a.length)
    surface = _thread_surface(feature, a)
    assert surface is not None
    g = GeomAdaptor_Surface(surface)
    worst = 0.0
    for s in np.linspace(0.0, a.length, 11):
        for u in np.linspace(0.05, 2 * math.pi * 4 - 0.05, 97):
            p = g.Value(float(u), float(s))
            worst = max(worst, float(np.linalg.norm(np.array([p.X(), p.Y(), p.Z()]) - a.point(float(s), float(u)))))
    assert worst < 3e-4


def test_a_profile_with_a_rounded_stretch_is_read_as_lines_and_an_arc():
    z = np.linspace(0, LEAD, 40001)[:-1]
    # (a V rounded at the root, with flanks running up at 30 degrees from square to the axis)
    t = math.radians(30)
    half = RADIUS * math.cos(t)
    root = 7.0
    centre = LEAD / 2
    q = np.where(np.abs(z - centre) <= half, root + RADIUS - np.sqrt(np.maximum(RADIUS * RADIUS - (z - centre) ** 2, 0.0)), root + RADIUS - RADIUS * math.sin(t) + (np.abs(z - centre) - half) / math.tan(t))
    reduced = np.c_[z, np.minimum(q, root + 1.5)]
    got = screws.profile_of(reduced, LEAD)
    assert got is not None
    corners = got[0]
    ring = np.array(sorted((float(a) % LEAD, float(b)) for a, b in corners[:-1]))
    prof = np.r_[ring, ring[:1] + [LEAD, 0]]
    full, arcs = screws.with_arcs(reduced, prof, LEAD)
    assert len(arcs) == 1
    first, last, c, r = arcs[0]
    assert r == pytest.approx(RADIUS, abs=0.04)
    assert c[1] == pytest.approx(root + RADIUS, abs=0.04)
    assert last > first
    model = Helical(np.zeros(3), np.array([0, 0, 1.0]), LEAD / (2 * math.pi), full, True)
    model.arcs = arcs
    pieces = pieces_of(model)
    assert sum(isinstance(p, ArcFlank) for p in pieces) == 1
    assert all(isinstance(p, Flank) for p in pieces)
    # (the pieces meet end to end, and close over a lead)
    for a, b in zip(pieces, pieces[1:]):
        assert np.allclose(a.q, b.p)
    assert np.allclose(pieces[-1].q - pieces[0].p, [LEAD, 0.0])
