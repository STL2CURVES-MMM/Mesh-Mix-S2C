"""A thread's crest and root flats go to MMM as the designer's surfaces (mms2c/merged.py _thread_flat): an exactly flat piece of
its profile becomes a locked cylinder, or a cone on a tapered thread; a flank, and anything that isn't a thread, stays as it was."""
import math

import numpy as np

import mms2c  # noqa: F401  (pins OpenBLAS before numpy is used)
from mms2c import merged, threads
from mmm import fitting as mfit

PROFILE = [[0.0, 7.0], [0.5, 7.0], [1.0, 8.0], [1.5, 8.0], [2.0, 7.0]]


def thread(k=0.0):
    return threads.Helical(np.zeros(3), np.array([0.0, 0.0, 1.0]), 2.0 / (2 * math.pi), PROFILE, True, k, 5.0)


def test_a_crest_flat_is_a_locked_cylinder_at_its_radius():
    crest = threads.Flank(thread(), [1.0, 8.0], [1.5, 8.0])
    prim = merged._thread_flat(crest)
    assert isinstance(prim, mfit.Cylinder) and prim.locked
    assert abs(float(prim.r) - 8.0) < 1e-12


def test_a_flank_and_a_tapered_flat():
    assert merged._thread_flat(threads.Flank(thread(), [0.5, 7.0], [1.0, 8.0])) is None
    cone = merged._thread_flat(threads.Flank(thread(k=0.05), [0.0, 7.0], [0.5, 7.0]))
    assert isinstance(cone, mfit.Cone) and cone.locked


def test_what_isnt_a_thread_is_left_alone():
    class Other:
        kind = "revolve"

    assert merged._thread_flat(Other()) is None
