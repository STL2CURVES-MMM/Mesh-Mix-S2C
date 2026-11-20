"""A flat facet lying on a thread across several pieces of its profile (where two of OpenSCAD's thread segments meet) is parted
along the pieces (threads.parting, features.Mesh.parted); one that only has its corners on the thread is not."""
import math

import numpy as np

import mms2c  # noqa: F401  (pins OpenBLAS before numpy is used)
from mms2c import threads
from mms2c.features import Mesh
from mms2c.threads import Helical

LEAD = 3.0
# (root, rising flank, crest, falling flank: four pieces over a lead)
OUTLINE = np.array([[0.0, 8.6], [0.6, 8.6], [1.4, 10.0], [1.9, 10.0], [3.0, 8.6]])
STEP = 0.01     # mm: the band's width, in its axial plane


def thread():
    return Helical(np.zeros(3), np.array([0, 0, 1.0]), LEAD / (2 * math.pi), OUTLINE, False)


def band(extra=None):
    """The profile at angle 0 (along +y, for an axis along z), as a band STEP wide in the plane x = 0: each corner and a point STEP inward of it."""
    pts, tris = [], []
    for z, r in OUTLINE:
        pts += [[0.0, r, z], [0.0, r - STEP, z]]
    for i in range(len(OUTLINE) - 1):
        p, pin, q, qin = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        tris += [[p, pin, q], [pin, qin, q]]
    if extra is not None:
        tris.append(extra)
    pts, tris = np.array(pts, float), np.array(tris)
    # (every triangle wound as the first, so all are one flat facet)
    n0 = np.cross(pts[tris[0, 1]] - pts[tris[0, 0]], pts[tris[0, 2]] - pts[tris[0, 0]])
    for t in tris:
        if np.cross(pts[t[1]] - pts[t[0]], pts[t[2]] - pts[t[0]]) @ n0 < 0:
            t[1], t[2] = t[2], t[1]
    return pts, tris


def test_a_band_round_the_profile_is_parted_along_its_pieces():
    h = thread()
    mesh = Mesh(*band())
    assert len(mesh.fn) == 1
    apart = threads.parting(mesh, h, np.arange(1))
    assert list(apart) == [0]
    assert sorted(set(apart[0])) == [0, 1, 2, 3]
    parted, old = mesh.parted(apart)
    assert len(parted.fn) == 4
    assert (old == 0).all()
    assert np.array_equal(parted.tris, mesh.tris)
    _, _, homes, _ = threads._on_pieces(parted, h, np.arange(4))
    assert sorted(homes(parted.fverts[f])[0] for f in range(4)) == [0, 1, 2, 3]


def test_a_flat_facet_with_only_its_corners_on_the_thread_is_left_whole():
    """A triangle from the root's start to the crest's end lies across the profile, far from the thread between its corners."""
    h = thread()
    mesh = Mesh(*band(extra=[0, 6, 4]))
    assert len(mesh.fn) == 1
    assert threads.parting(mesh, h, np.arange(1)) == {}


def test_a_facet_already_on_one_piece_is_left_whole():
    h = thread()
    pts, tris = band()
    mesh = Mesh(pts, tris[2:4])     # (the rising flank's two triangles)
    assert len(mesh.fn) == 1
    assert threads.parting(mesh, h, np.arange(1)) == {}


def test_a_mesh_rebuilt_from_its_own_facets_is_the_same():
    pts, tris = band()
    mesh = Mesh(pts, tris)
    again = Mesh(mesh.pts, mesh.tris, mesh.facet_of)
    assert np.array_equal(again.facet_of, mesh.facet_of)
    assert np.allclose(again.fn, mesh.fn) and np.allclose(again.farea, mesh.farea)
    assert [sorted(s) for s in again.nbrs] == [sorted(s) for s in mesh.nbrs]
