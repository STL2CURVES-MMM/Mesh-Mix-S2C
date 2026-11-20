"""A thread is found from the surface's own screw motion (mms2c.screws), on threads whose every number is known: built here as meshes, with a clean corner on each profile's piece."""
import math

import numpy as np
import pytest

import mms2c  # noqa: F401  (pins OpenBLAS before numpy is used)
from mms2c import screws

PROFILE = np.array([[0.0, 7.0], [0.5, 7.0], [1.0, 8.0], [1.5, 8.0], [2.0, 7.0]])    # closed over a lead of 2.0 with its first corner two later


def threaded(lead=2.0, turns=6.0, hand=1, per_turn=64, core=7.0, major=8.0, axis=None, at=(0.0, 0.0, 0.0), taper=0.0, starts=1):
    """A closed bolt: a trapezoid thread round a core, as triangles, with flat ends. Its axis is along z unless told otherwise."""
    pitch = lead / starts
    prof = np.array([[0.0, core], [0.25, core], [0.5, major], [0.75, major], [1.0, core]]) * [pitch, 1.0]
    n = int(per_turn * turns) + 1
    points, tris = [], []
    # (the sweep's surface, a band for each piece of the profile, once for each start, the next start a pitch along)
    for start in range(starts):
        rows = []
        for u in np.linspace(0, 2 * math.pi * turns, n):
            rise = hand * lead * u / (2 * math.pi) + start * pitch
            z = prof[:, 0] + rise
            rho = prof[:, 1] + taper * z
            rows.append(np.c_[rho * math.cos(u), rho * math.sin(u), z])
        grid = np.array(rows)
        m = grid.shape[1]
        base = sum(len(p) for p in points)
        idx = base + np.arange(n * m).reshape(n, m)
        for i in range(n - 1):
            for j in range(m - 1):
                tris += [(idx[i, j], idx[i + 1, j], idx[i + 1, j + 1]), (idx[i, j], idx[i + 1, j + 1], idx[i, j + 1])]
        points.append(grid.reshape(-1, 3))
    V = np.vstack(points)
    F = np.array(tris)
    if axis is not None:
        d = np.asarray(axis, float) / np.linalg.norm(axis)
        e1 = np.cross(d, [1.0, 0, 0] if abs(d[0]) < 0.9 else [0, 1.0, 0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(d, e1)
        V = V[:, [0]] * e1 + V[:, [1]] * e2 + V[:, [2]] * d
    return V + np.asarray(at, float), F


def cylinder(radius=8.0, height=12.0, around=96, along=24):
    """A plain surface of revolution, closed or not: no thread at all."""
    u = np.linspace(0, 2 * math.pi, around, endpoint=False)
    z = np.linspace(0, height, along)
    V = np.array([[radius * math.cos(a), radius * math.sin(a), h] for h in z for a in u])
    tris = []
    for i in range(along - 1):
        for j in range(around):
            a, b = i * around + j, i * around + (j + 1) % around
            c, d = a + around, b + around
            tris += [(a, b, d), (a, d, c)]
    return V, np.array(tris)


def bar(ribs=8, ring=8.0, height=12.0):
    """Straight ribs round an axis: repeats at its spacing, and doesn't move along it. No screw motion leaves it in place but a whole number of ribs round."""
    around = ribs * 16
    u = np.linspace(0, 2 * math.pi, around, endpoint=False)
    rho = ring + 0.6 * np.abs(((u * ribs / (2 * math.pi)) % 1.0) - 0.5) * 2
    z = np.linspace(0, height, 24)
    V = np.array([[rho[j] * math.cos(u[j]), rho[j] * math.sin(u[j]), h] for h in z for j in range(around)])
    tris = []
    for i in range(23):
        for j in range(around):
            a, b = i * around + j, i * around + (j + 1) % around
            c, d = a + around, b + around
            tris += [(a, b, d), (a, d, c)]
    return V, np.array(tris)


def one(V, F):
    _, rows = screws.motions(V, F)
    return rows


@pytest.mark.parametrize("hand", [1, -1])
def test_either_hand_reads_its_lead_and_axis(hand):
    rows = one(*threaded(hand=hand))
    assert len(rows) == 1
    r = rows[0]
    assert r["hand"] == hand
    assert r["lead"] == pytest.approx(2.0, abs=0.002)
    assert abs(r["c"][2]) > math.cos(math.radians(0.1))
    assert np.linalg.norm((r["a"] - (r["a"] @ r["c"]) * r["c"])) < 0.01


def test_the_axis_can_lie_anywhere():
    d = np.array([0.6, -0.48, 0.64])
    at = np.array([40.0, -25.0, 7.0])
    r = one(*threaded(axis=d, at=at))[0]
    assert r["lead"] == pytest.approx(2.0, abs=0.002)
    assert abs(r["c"] @ (d / np.linalg.norm(d))) > math.cos(math.radians(0.1))
    off = at - r["a"]
    assert np.linalg.norm(off - (off @ r["c"]) * r["c"]) < 0.02


@pytest.mark.parametrize("lead,starts", [(0.5, 1), (5.0, 1), (6.0, 3)])
def test_leads_and_starts(lead, starts):
    r = one(*threaded(lead=lead, turns=8.0 if lead < 2 else 4.0, starts=starts, core=7.0, major=7.0 + 0.5 * lead / starts))[0]
    assert r["lead"] == pytest.approx(lead, rel=0.002)


def test_a_thread_too_short_for_a_whole_turn():
    """A steep nut less than a turn long: long moves would run off both ends."""
    r = one(*threaded(lead=20.0, turns=0.75, per_turn=200, starts=5, core=9.0, major=10.5))[0]
    assert r["lead"] == pytest.approx(20.0, rel=0.003)


@pytest.mark.parametrize("cut", ["half", "third", "flat"])
def test_a_thread_on_part_of_a_circle(cut):
    """A thread that goes only part of the way round: half, a third, or a flat cut not through the axis, which ends it at another angle at each radius. Its moves are as long as its
    own surface allows (screws._moves), and its axis is read as exactly as a whole thread's: shortened moves left a third of a circle 0.26 degrees and 0.055 mm off."""
    V, F = threaded(turns=6.0)
    mid = V[F].mean(axis=1)
    angle = np.degrees(np.arctan2(mid[:, 1], mid[:, 0])) % 360.0
    keep = {"half": angle < 180.0, "third": angle < 120.0, "flat": mid[:, 0] < 7.5}[cut]
    r = one(V, F[keep])[0]
    assert r["lead"] == pytest.approx(2.0, rel=0.003)
    c = r["c"] / np.linalg.norm(r["c"])
    assert math.degrees(math.acos(min(1.0, abs(float(c[2]))))) < 0.05
    place = np.asarray(r["a"], float) - (np.asarray(r["a"], float) @ c) * c
    assert np.linalg.norm(place) < 0.01


def fine_arc(arc=120.0, per_turn=96):
    """A fine thread (lead 0.5 on radius 3, as M6x0.5) on `arc` degrees of a circle, with flat crests and roots."""
    V, F = threaded(lead=0.5, turns=12.0, core=2.7, major=3.0, per_turn=per_turn)
    mid = V[F].mean(axis=1)
    return V, F[np.degrees(np.arctan2(mid[:, 1], mid[:, 0])) % 360.0 < arc]


def test_the_axis_is_read_from_the_crest_and_root_bands():
    """A fine thread on a third of a circle, offered an axis 0.8 degrees and 0.05 mm off (as its twisting surfaces read it): its crest and root, cylinders round the axis whatever
    the lead, put it back. Offered one beside the thread (where a repeat may be tried), the bands don't hold one within a reading's error, and it is left as it was."""
    V, F = fine_arc()
    X, N, _ = screws.points(V, F, np.random.default_rng(4), screws.TEST_POINTS)
    t = math.radians(0.8)
    c, a = screws._band_axis(X, N, np.array([math.sin(t), 0.0, math.cos(t)]), np.array([0.05, 0.0, 0.0]))
    assert math.degrees(math.acos(min(1.0, abs(float(c[2]))))) < 0.05
    assert np.linalg.norm(a[:2]) < 0.01
    c2, a2 = screws._band_axis(X, N, np.array([0.0, 0.0, 1.0]), np.array([2.0, 0.0, 0.0]))
    assert np.array_equal(c2, [0.0, 0.0, 1.0]) and np.array_equal(a2, [2.0, 0.0, 0.0])


def test_a_fine_thread_on_a_quarter_circle_reads_its_lead():
    """Read from its twisting surfaces, a fine thread's axis on part of a circle is a degree off: OpenSCAD's M6x0.5 on 90 and 120 degrees went unfound, its true lead landing
    nowhere and the search settling on half of it (sweep, 2026-11-06), and this one, coarsely faceted, read 0.4947 for 0.5. With its axis read from the bands, the lead is right."""
    rows = one(*fine_arc(90.0, 48))
    assert any(abs(r["lead"] - 0.5) < 0.002 and r["hand"] == 1 for r in rows)


def test_joint_seeds_read_a_thread_on_sixty_degrees_from_its_pooled_strips():
    """The twisting regions of a fine thread on 60 degrees of a circle, pooled (_joint_seeds), read its axis, its hand, its lead from the flanks and its ring from root to
    crest: the sweep's OpenSCAD M6x0.5 on 60 degrees was found only so, its strips each reading an axis tens of degrees off. (This clean mesh is found by pass 1 too.)"""
    V, F = fine_arc(60.0)
    twisting = []
    screws.hypotheses(V, F, np.random.default_rng(5), twisting)
    s = screws._joint_seeds(twisting)[0]
    c, a = s["c"], s["a"]
    assert math.degrees(math.acos(min(1.0, abs(float(c[2]))))) < 0.5
    assert np.linalg.norm(a - (a @ c) * c) < 0.05
    (L, hand), = s["readings"]
    assert hand == 1 and L == pytest.approx(0.5, rel=0.03)
    assert 2.6 < s["ring"][0] < s["ring"][1] < 3.1


def test_another_surfaces_points_in_the_ring_dont_end_a_whole_thread():
    """A whole thread with another surface's leaning points in its ring, bunched at four angles just outside the crest (an M8x1 greaser's body corners beside its thread): they crowd one
    band of radius, so its points' spacing makes the thread's own sparse gaps look like ends. Judged by the points that land under it, the thread goes all round, and moves as a whole
    thread (tc_greaser read 0.990 for 1.000 on one BLAS kernel while judged as part of a circle)."""
    V, F = threaded(turns=6.0)
    S = screws.surface_of(V, F)
    rng = np.random.default_rng(3)
    X, N, _ = screws.points(V, F, rng, screws.TEST_POINTS)
    m = 150
    ang = np.radians(np.repeat([0.0, 90.0, 180.0, 270.0], m) + rng.uniform(-3.0, 3.0, 4 * m))
    rho, z = rng.uniform(8.3, 8.6, 4 * m), rng.uniform(1.0, 11.0, 4 * m)
    X = np.vstack([X, np.c_[rho * np.cos(ang), rho * np.sin(ang), z]])
    N = np.vstack([N, np.c_[0.6 * np.cos(ang), 0.6 * np.sin(ang), np.full(4 * m, 0.8)]])
    c, a = np.array([0.0, 0.0, 1.0]), np.zeros(3)
    rm, span = float(np.linalg.norm(X[:, :2], axis=1).max()), float(np.ptp(X[:, 2]))
    # (by the leaning surface rotation moves alone, the ring looks ended: the case this is about)
    twisted = screws.rotated(S, X, a, c) > screws.TWIST_FLOOR * min(2.0, rm)
    assert screws._cover(X, twisted & (np.abs(N @ c) >= screws.LEAN), c, a) is not None
    r = screws._judge(S, X, N, c, a, 2.0, 1, 0.0, rm, span)
    assert r["cover"] is None and r["sc"] == 1.0 and screws.verdict(r)


def test_a_loose_landing_counts_only_on_a_scan():
    """A motion that lands only loosely, though most of the leaning surface follows it all round, is a thread only on a scan: on clean and coarse meshes every true thread measured
    lands tight, and every loose pass was a resemblance (a twist drill's flutes read 6.99 left-hand once its axis was right)."""
    r = dict(lead=2.0, members=1500, turns=5.0, around=350.0, flank_members=800, flank_twisted=1000, depth=1.0, D3=0.15, Dr=1.0)
    assert not screws.verdict(r)
    assert not screws.verdict(dict(r, scan=False))
    assert screws.verdict(dict(r, scan=True))


def test_a_thread_under_a_scans_noise_is_found():
    """A thread scanned, its vertices scattered by 0.06 mm (a smooth shank beside it shows the scatter): it lands loosely, and the mesh is a scan by MMM's rule, so it is found."""
    V, F = threaded(lead=2.0, turns=6.0)
    Vc, Fc = cylinder(radius=7.0, height=12.0, around=32, along=9)
    V2, F2 = np.vstack([V, Vc - [0.0, 0.0, 12.5]]), np.vstack([F, Fc + len(V)])
    V2 = V2 + np.random.default_rng(1).normal(0.0, 0.06, V2.shape)
    assert screws._scan(screws.surface_of(V2, F2))
    assert any(abs(r["lead"] - 2.0) < 0.02 and r["hand"] == 1 for r in one(V2, F2))


def test_of_two_readings_of_one_thread_the_closer_is_kept():
    """Two readings of one thread from two axes: the true motion, and one 0.2 degrees tilted and 0.3% short in lead that counted sharper on its own ring (as a pooled axis's did
    on tc_greaser). Compared on the same points, the true one lands closer and is kept, whichever counted sharper."""
    V, F = threaded(turns=6.0)
    S = screws.surface_of(V, F)
    X, N, _ = screws.points(V, F, np.random.default_rng(6), screws.TEST_POINTS)
    rm, span = float(np.linalg.norm(X[:, :2], axis=1).max()), float(np.ptp(X[:, 2]))
    t = math.radians(0.2)
    good = screws._judged(S, X, N, np.array([0.0, 0.0, 1.0]), np.zeros(3), 2.0, 1, 0.0, rm, span)
    bad = screws._judged(S, X, N, np.array([math.sin(t), 0.0, math.cos(t)]), np.zeros(3), 1.994, 1, 0.0, rm, span)
    assert screws.verdict(good) and screws.verdict(bad) and screws._same_thread(good, bad)
    bad["sharp"] = good["sharp"] + 0.1
    kept = screws._one_each(S, V, F, [bad, good])
    assert len(kept) == 1 and kept[0]["lead"] == 2.0


def test_rotation_measured_once_judges_the_same():
    """Rotation's reach handed to the judge, measured once for all the points, gives the same row bit for bit as the judge measuring it itself: the
    surface measures each point on its own, so a strided subset of it is what the quick look would have measured."""
    V, F = threaded(turns=6.0)
    S = screws.surface_of(V, F)
    X, N, _ = screws.points(V, F, np.random.default_rng(6), screws.TEST_POINTS)
    rm, span = float(np.linalg.norm(X[:, :2], axis=1).max()), float(np.ptp(X[:, 2]))
    c, a = np.array([0.0, 0.0, 1.0]), np.zeros(3)
    Dr = screws.rotated(S, X, a, c)
    assert np.array_equal(Dr[::screws.QUICK_EVERY], screws.rotated(S, X[::screws.QUICK_EVERY], a, c))
    for L in (2.0, 1.7):
        mine, own = screws._judged(S, X, N, c, a, L, 1, 0.0, rm, span, Dr), screws._judged(S, X, N, c, a, L, 1, 0.0, rm, span)
        for key in ("sc", "sharp", "D3", "Dr", "members", "flank_members", "turns", "around", "rho", "z", "depth"):
            assert mine[key] == own[key], key
        assert np.array_equal(mine["member"], own["member"])


def test_a_copy_is_tried_once_per_thread_and_axis():
    """A copy already tried is the same thread on the same line, whatever digits its reading carries: a re-read 0.03% off in lead, its axis pointing
    the other way and its point further along, is the same copy; the other hand, a lead 5% off, or an axis 1 mm aside is not."""
    up = np.array([0.0, 0.0, 1.0])
    tried = [(up, np.array([4.0, 4.0, 0.0]), 0.35, 1, 1.28)]
    assert screws._tried_as_copy(tried, -up, np.array([4.0, 4.0, 7.5]), 0.3501, 1, 1.279)
    assert not screws._tried_as_copy(tried, up, np.array([4.0, 4.0, 0.0]), 0.35, -1, 1.28)
    assert not screws._tried_as_copy(tried, up, np.array([4.0, 4.0, 0.0]), 0.3675, 1, 1.28)
    assert not screws._tried_as_copy(tried, up, np.array([5.0, 4.0, 0.0]), 0.35, 1, 1.28)


def test_a_taper_is_read_when_the_crest_slopes():
    r = one(*threaded(taper=-0.05))[0]
    assert r["lead"] == pytest.approx(2.0, abs=0.01)
    assert r["k"] == pytest.approx(-0.05, abs=0.005)


def test_a_straight_thread_has_no_taper():
    assert one(*threaded())[0]["k"] == 0.0


@pytest.mark.parametrize("taper", [0.0, -0.05])
def test_a_taper_stands_only_if_it_lands_clearly_closer_than_straight(taper):
    """On a straight thread a small taper can land a shade closer by chance (0.0018 against 0.0021 here); it isn't clearly closer, so it goes. On a tapered
    thread the true taper lands fifteen times closer than the straight reading, and stays."""
    V, F = threaded(turns=6.0, taper=taper)
    S = screws.surface_of(V, F)
    X, N, _ = screws.points(V, F, np.random.default_rng(6), screws.TEST_POINTS)
    rm, span = float(np.linalg.norm(X[:, :2], axis=1).max()), float(np.ptp(X[:, 2]))
    ks = (0.0, -0.002) if taper == 0.0 else (0.0, -0.05)
    rows = [screws._judged(S, X, N, np.array([0.0, 0.0, 1.0]), np.zeros(3), 2.0, 1, k, rm, span) for k in ks]
    assert all(screws.verdict(r) for r in rows)
    assert [r["k"] for r in screws._without_unearned_tapers(rows)] == ([0.0] if taper == 0.0 else [0.0, -0.05])


@pytest.mark.parametrize("shape", [cylinder, bar])
def test_what_no_screw_motion_leaves_in_place_is_no_thread(shape):
    assert one(*shape()) == []


def test_the_same_mesh_gives_the_same_answer():
    V, F = threaded()
    a, b = one(V, F)[0], one(V, F)[0]
    assert a["lead"] == b["lead"] and np.array_equal(a["c"], b["c"]) and np.array_equal(a["a"], b["a"])


def test_the_profile_is_read_as_one_curve():
    lead = 2.0
    z = np.linspace(0, lead, 20001)[:-1]
    # (a trapezoid: flat root, rising flank, flat crest, falling flank)
    q = np.interp(z, [0.0, 0.5, 1.0, 1.5, 2.0], [7.0, 7.0, 8.0, 8.0, 7.0])
    got = screws.profile_of(np.c_[z, q], lead)
    assert got is not None
    Q, pieces, _ = got
    assert len(Q) == 5
    assert all(p[0] == "line" for p in pieces)
    assert Q[-1, 0] - Q[0, 0] == pytest.approx(lead)
    # (each corner where it truly is, within a column's width)
    true = {(0.5, 7.0), (1.0, 8.0), (1.5, 8.0), (2.0, 7.0)}
    for x, y in Q[:-1]:
        assert any(abs((x % lead) - (a % lead)) < 2 * lead / 360 and abs(y - b) < 0.02 for a, b in true)


def test_a_rounded_root_is_read_as_an_arc_of_its_radius():
    lead = 3.0
    r = 0.6
    z = np.linspace(0, lead, 40001)[:-1]
    # (a V whose root is rounded: a circle of radius r running into straight flanks at 30 degrees from square)
    t = math.radians(30)
    half = r * math.cos(t)
    root = 7.0 + r - r * math.sin(t) * 0
    centre = lead / 2
    q = np.where(np.abs(z - centre) <= half, root - np.sqrt(np.maximum(r * r - (z - centre) ** 2, 0.0)), root - r * math.sin(t) + (np.abs(z - centre) - half) / math.tan(t))
    q = np.minimum(q, root + 0.9)
    got = screws.profile_of(np.c_[z, q], lead)
    arcs = [p for p in got[1] if p[0] == "arc"]
    assert len(arcs) == 1
    assert arcs[0][3] == pytest.approx(r, abs=0.03)


def test_the_allowance_copies_are_vouched_by_is_the_conversions_own(monkeypatch):
    """A thread vouches for its copies when it lands within what the conversion allows the mesh (screws._allowance): the fidelity check's
    CAD tolerance for the part's size, its chordal deflection or its noise, whichever is most, measured once per mesh."""
    from mmm import fidelity
    from mmm.pipeline import auto_tolerance
    V, F = threaded()
    S = screws.surface_of(V, F)
    want = max(float(auto_tolerance(float(np.linalg.norm(np.ptp(V, axis=0))), "cad")), float(fidelity.mesh_deflection(V, F)[0]),
               float(fidelity.mesh_noise(V, F)))
    assert screws._allowance(S) == pytest.approx(want)
    monkeypatch.setattr(fidelity, "mesh_noise", lambda *a: (_ for _ in ()).throw(AssertionError("measured twice")))
    assert screws._allowance(S) == pytest.approx(want)


def test_a_reading_that_went_round_but_landed_loosely_is_near_a_thread():
    # (gutter-mount's internal M30x3.5 read from its flank's facet normals at 3.15 for 3.5: round, deep and flanked as a thread,
    # only its landing loose, so the scan still looks on its axis)
    went_round = {"lead": 3.1501, "turns": 4.5195, "around": 358.8, "D3": 0.2051, "Dr": 0.8268, "members": 2704,
                  "flank_members": 2073, "flank_twisted": 2294, "depth": 1.8908, "sc": 1.0}
    assert not screws.verdict(went_round) and screws._went_round(went_round)
    # (readings of the same part's other axes that are no thread: too few flanks land, or not all round)
    for r in ({"lead": 1.7426, "turns": 9.0151, "around": 319.96, "D3": 0.2478, "Dr": 0.8908, "members": 135, "flank_members": 94,
               "flank_twisted": 405, "depth": 1.9058, "sc": 1.0},
              {"lead": 11.0588, "turns": 1.0177, "around": 193.9, "D3": 1.5383, "Dr": 4.893, "members": 40, "flank_members": 39,
               "flank_twisted": 374, "depth": 7.8528, "sc": 1.0}):
        assert not screws._went_round(r)


def test_a_reading_that_went_round_at_a_lead_normals_cant_tell_says_nothing():
    # (universal-clamp-side-top-40x40-nut's internal thread, lead 2.5, read 1.689 at r 8.73 from a coarse mesh's facet normals: a
    # helix lean of 1.76 degrees, and only 0.39 of its flanks landed)
    r = {"lead": 1.689, "turns": 4.8037, "around": 356.93, "D3": 0.1898, "Dr": 0.8, "members": 1339, "flank_members": 827,
         "flank_twisted": 2125, "depth": 1.3535, "sc": 1.0}
    assert not screws.verdict(r) and not screws._went_round(r)
    assert screws._untold(r, 8.73)
    assert not screws._untold(r, 1.0)        # (at r 1 the same lead leans 15 degrees: normals tell it, and its failure stands)
    false = {"lead": 11.0588, "turns": 1.0177, "around": 193.9, "D3": 1.5383, "Dr": 4.893, "members": 40, "flank_members": 39,
             "flank_twisted": 374, "depth": 7.8528, "sc": 1.0}
    assert not screws._untold(false, 14.0)   # (not all round)


def test_a_refined_taper_under_the_least_worth_trying_is_no_taper():
    # (funnel's straight screw-on rim, refined from a try at its flare's slope, stopped at -0.000125: cones of 0.007 degrees)
    assert screws._no_slight_taper(-0.000125) == 0.0
    assert screws._no_slight_taper(0.0) == 0.0
    # (a pipe thread's 1:16 stays)
    assert screws._no_slight_taper(-0.0313) == -0.0313
