"""A facet within reach of two pieces of a thread's profile goes to the one holding its corners clearly closer (threads._closest_home):
a flank facet beside a narrow crest flat is the flank's, though the flat holds it within the thread's tolerance too; a sliver on the
corner the two meet in is held by both alike, and left to its neighbours; a crumb no larger than the tolerance is left to them too
(threads._crumb); and a facet closeness would leave as an island of a piece inside another's goes back to its neighbours' vote
(threads._no_islands)."""
import math

import numpy as np

import mms2c  # noqa: F401  (pins OpenBLAS before numpy is used)
from mms2c import threads
from mms2c.threads import Helical

LEAD = 3.0
# (root, rising flank, a crest flat 0.1 mm wide, falling flank, root: as a wood screw's, the crest narrower than its facets)
OUTLINE = np.array([[0.0, 8.6], [0.6, 8.6], [1.4, 10.0], [1.5, 10.0], [2.3, 8.6], [3.0, 8.6]])
RISING, CREST = 1, 2


def pieces():
    return threads.pieces_of(Helical(np.zeros(3), np.array([0, 0, 1.0]), LEAD / (2 * math.pi), OUTLINE, False))


def at(points):
    """Profile points (z, rho) at the angle where the profile lies in the plane x = 0, as the mesh's corners there."""
    return np.array([[0.0, r, z] for z, r in points])


def along(a, b, t):
    a, b = np.asarray(a, float), np.asarray(b, float)
    return a + t * (b - a) / np.linalg.norm(b - a)


def test_the_flank_facet_beside_a_narrow_flat_is_the_flanks():
    p = pieces()
    corner, low = OUTLINE[2], OUTLINE[1]
    P = at([along(corner, low, t) for t in (0.005, 0.012, 0.02)])      # on the rising flank, within 0.02 of the crest
    on_flat = float(np.abs(p[CREST].place(P)[1]).max())
    assert 0.01 < on_flat < 0.03                                         # (the flat holds it within a wide tolerance too)
    assert threads._closest_home(p, P, [CREST, RISING], 0.001) == RISING
    assert threads._closest_home(p, P, [RISING, CREST], 0.001) == RISING


def test_a_sliver_on_the_corner_is_left_to_its_neighbours():
    p = pieces()
    corner = OUTLINE[2]
    P = at([corner, along(corner, OUTLINE[3], 0.0002), along(corner, OUTLINE[1], 0.0002)])
    assert threads._closest_home(p, P, [CREST, RISING], 0.001) is None


def test_a_facet_on_the_flat_is_the_flats():
    p = pieces()
    P = at([along(OUTLINE[2], OUTLINE[3], t) for t in (0.02, 0.05, 0.08)])
    assert threads._closest_home(p, P, [RISING, CREST], 0.001) == CREST


# (threads._no_islands: facets 0-3 the crest flat's alone, 10-12 the rising flank's alone; 5 and 7 are slivers on the corner whose
# neighbours are the flat's and each other, 6 a facet beside the flank. Closeness gave all three the flank, the vote the flat.)
HOME = {0: CREST, 1: CREST, 2: CREST, 3: CREST, 10: RISING, 11: RISING, 12: RISING}
NBRS = {0: [1, 5], 1: [0, 2, 5], 2: [1, 3, 6, 7], 3: [2, 10], 5: [0, 1, 7], 6: [2, 11], 7: [2, 5],
        10: [3, 11], 11: [6, 10, 12], 12: [11]}


def test_a_facet_closeness_would_strand_inside_another_piece_goes_back_to_the_vote():
    choice, voted = {5: RISING, 6: RISING, 7: RISING}, {5: CREST, 6: CREST, 7: CREST}
    threads._no_islands(HOME, choice, voted, NBRS)
    assert choice[5] == CREST and choice[7] == CREST        # (two slivers joined to each other, neither to the flank: an island)
    assert choice[6] == RISING                              # (it joins a facet of the flank)


def test_a_crumb_inside_the_tolerance_is_left_to_its_neighbours():
    corner, low = OUTLINE[2], OUTLINE[1]
    sliver = at([along(corner, low, t) for t in (0.005, 0.006, 0.013)])        # (OpenSCAD's: 0.008 mm long, at the corner)
    facet = at([along(corner, low, t) for t in (0.02, 0.15, 0.40)])          # (00009858's: 0.37 mm across)
    assert threads._crumb(sliver, 0.03)
    assert not threads._crumb(facet, 0.03)


def test_with_nothing_moved_the_vote_stands():
    choice, voted = {5: CREST, 6: RISING}, {5: CREST, 6: RISING}
    threads._no_islands(HOME, choice, voted, NBRS)
    assert choice == {5: CREST, 6: RISING}
