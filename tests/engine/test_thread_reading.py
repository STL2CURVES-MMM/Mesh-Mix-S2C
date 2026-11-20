"""How a motion becomes a thread (mms2c/threads.py _read): its lead sharpened on its points first (_sharpened), read again about the
fitted motion when the first lay off it (_drift), a sharpened lead kept only if it lands about as well as the read one
(_lands_as_read), a motion the reader reads nothing on read as the sweep it also is (_model_of), and on part of a circle taken only if the profile repeats from turn to turn (_repeats)."""
import math

import numpy as np

import mms2c  # noqa: F401  (pins OpenBLAS before numpy is used)
from mms2c import screws, threads

LEAD = 2.0
PROFILE = [[0.0, 7.0], [0.5, 7.0], [1.0, 8.0], [1.5, 8.0], [2.0, 7.0]]


def model(lead=LEAD):
    return threads.Helical(np.zeros(3), np.array([0.0, 0.0, 1.0]), lead / (2 * math.pi), PROFILE, True)


def surface(turns, around=360.0, per_turn=2.0, n=40000, seed=3):
    """Points on the thread's surface over this many turns and this much of a circle; per_turn(t) the radius added on turn t."""
    rng = np.random.default_rng(seed)
    P = np.asarray(PROFILE)
    s = rng.random(n) * LEAD
    u = rng.random(n) * math.radians(around)
    t = np.floor(rng.random(n) * turns)
    rho = np.interp(s, P[:, 0], P[:, 1]) + (per_turn(t) if callable(per_turn) else 0.0)
    z = s + LEAD * u / (2 * math.pi) + t * LEAD
    return np.c_[rho * np.cos(u), rho * np.sin(u), z]


def test_a_thread_repeats_on_part_of_a_circle():
    share, spread = threads._repeats(model(), surface(6, around=90.0))
    assert share > 0.95 and spread is not None and spread < 0.01


def test_a_strip_under_one_turn_has_nothing_to_repeat():
    share, spread = threads._repeats(model(), surface(1, around=200.0))
    assert share == 0.0 and spread is None


def test_turns_of_different_shapes_dont_repeat():
    share, spread = threads._repeats(model(), surface(5, around=180.0, per_turn=lambda t: 0.15 * t))
    assert share > 0.95 and spread > threads.REPEAT_SPREAD


def row(lead=LEAD, at=(0.0, 0.0, 0.0), c=(0.0, 0.0, 1.0), turns=6.0, sc=1.0):
    return dict(lead=lead, a=np.asarray(at, float), c=np.asarray(c, float), z=(0.0, turns * lead), turns=turns, sc=sc, hand=1, k=0.0,
                ring=None, rho=(7.0, 8.0))


def test_the_drift_of_a_motion_from_its_fitted_thread():
    assert threads._drift(row(), model())[0] < 1e-12
    assert abs(threads._drift(row(at=(0.02, 0.0, 0.0)), model())[0] - 0.02) < 1e-9
    assert abs(threads._drift(row(lead=LEAD * 1.005, turns=10.0), model())[0] - 0.005 * LEAD * 10.0) < 1e-9


def test_a_reading_on_part_of_a_circle_that_doesnt_repeat_isnt_taken(monkeypatch):
    got = (model(), np.arange(100))
    monkeypatch.setattr(threads, "_model_of", lambda *a: got)
    monkeypatch.setattr(screws, "landed", lambda *a: surface(1, around=250.0))
    assert threads._read(None, None, None, None, None, row(sc=0.5)) is None
    monkeypatch.setattr(screws, "landed", lambda *a: surface(6, around=250.0))
    assert threads._read(None, None, None, None, None, row(sc=0.5)) is got


def test_a_whole_reading_isnt_asked_to_repeat(monkeypatch):
    got = (model(), np.arange(100))
    monkeypatch.setattr(threads, "_model_of", lambda *a: got)
    monkeypatch.setattr(threads, "_sharpened", lambda S, V, F, r: (r, None))

    def never(*a):
        raise AssertionError("a whole reading's points were landed for the repeat test")

    monkeypatch.setattr(screws, "landed", never)
    assert threads._read(None, None, None, None, None, row(sc=1.0)) is got


def test_a_lead_a_little_off_is_sharpened_and_a_true_one_stands(monkeypatch):
    X = surface(20)
    monkeypatch.setattr(screws, "landed", lambda *a: X)
    monkeypatch.setattr(threads, "_landing_ratio", lambda *a: 1.0)     # (the move lands as the read lead does: _lands_as_read)
    moved, points = threads._sharpened(None, None, None, row(lead=LEAD * 1.005, turns=20.0))
    assert points is None and abs(moved["lead"] - LEAD) < 0.001 * LEAD
    stood, points = threads._sharpened(None, None, None, row(turns=20.0))
    assert points is X and stood["lead"] == LEAD


def test_a_sharpened_lead_that_lands_far_worse_than_the_read_one_is_refused(monkeypatch):
    X = surface(20)
    monkeypatch.setattr(screws, "landed", lambda *a: X)
    r = row(lead=LEAD * 1.005, turns=20.0)
    monkeypatch.setattr(threads, "_landing_ratio", lambda *a: threads.SHARPEN_LAND * 1.5)
    kept, points = threads._sharpened(None, None, None, r)
    assert kept is r and points is X
    monkeypatch.setattr(threads, "_landing_ratio", lambda *a: None)     # (nothing to land: the move stands, as before)
    moved, points = threads._sharpened(None, None, None, r)
    assert points is None and abs(moved["lead"] - LEAD) < 0.001 * LEAD


def test_a_motion_the_reader_reads_nothing_on_is_read_as_its_sweep(monkeypatch):
    swept = (model(), np.arange(50))
    monkeypatch.setattr(threads, "_thread_model", lambda *a: None)
    monkeypatch.setattr(threads, "_read_as_sweep", lambda mesh, pool, r: swept)
    monkeypatch.setattr(screws, "sweep_only", lambda r: False)
    monkeypatch.setattr(screws, "sweep_verdict", lambda r: True)
    assert threads._model_of(None, None, None, None, None, row()) is swept
    monkeypatch.setattr(screws, "sweep_verdict", lambda r: False)      # (not a sweep: nothing else is tried)
    assert threads._model_of(None, None, None, None, None, row()) is None
    monkeypatch.setattr(screws, "sweep_verdict", lambda r: True)
    monkeypatch.setattr(threads, "READER_FALLBACK", False)
    assert threads._model_of(None, None, None, None, None, row()) is None


def test_a_reading_the_reader_makes_is_kept_without_the_fallback(monkeypatch):
    got = (model(), np.arange(100))
    monkeypatch.setattr(threads, "_thread_model", lambda *a: got)

    def never(*a):
        raise AssertionError("the sweep reader was asked although the thread's reader read the motion")

    monkeypatch.setattr(threads, "_read_as_sweep", never)
    assert threads._model_of(None, None, None, None, None, row()) is got
