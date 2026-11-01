"""The benchmark judge's Enclosure (mms2c/judge.py) on meshes with known answers, each
against manifold3d's Boolean union: overlapping pieces, a piece overlapping itself, a mesh
saved inside out, a cavity, a sunk solid, a zero-thickness double wall and a thin plate
(Tuna's Claude's review on #2 suggested keeping these as a test)."""
import numpy as np
import pytest
import trimesh

mf = pytest.importorskip("manifold3d")
from mms2c.judge import Enclosure  # noqa: E402


def box(lo, hi):
    return trimesh.creation.box(bounds=[lo, hi])


def turned(m, rad):
    m = m.copy()
    m.apply_transform(trimesh.transformations.rotation_matrix(rad, (0, 0, 1), (0, 0, 0)))
    return m


def joined(meshes, flip=()):
    """One mesh of the pieces, corners welded where they coincide; pieces in flip face in."""
    V, F, off = [], [], 0
    for i, m in enumerate(meshes):
        f = np.asarray(m.faces)
        V.append(m.vertices)
        F.append((f[:, ::-1] if i in flip else f) + off)
        off += len(m.vertices)
    V = np.vstack(V)
    _, first, inv = np.unique(np.round(V, 9), axis=0, return_index=True, return_inverse=True)
    return V[first], inv.ravel()[np.vstack(F)]


def union(meshes):
    out = None
    for m in meshes:
        x = mf.Manifold(mf.Mesh(np.asarray(m.vertices, np.float32), np.asarray(m.faces, np.uint32)))
        out = x if out is None else out + x
    return out.volume()


A = box([0, 0, 0], [2, 2, 2])
B = turned(A, np.pi / 4)          # shares the edge x = y = 0 with A: welded, one connected piece
C = box([0.5, 0.5, 0.5], [1.5, 1.5, 1.5])


@pytest.mark.parametrize("name, mesh, want", [
    ("one piece overlapping itself", joined([A, B]), union([A, B])),
    ("the same, inside out", joined([A, B], flip=(0, 1)), union([A, B])),
    ("a cavity", joined([A, C], flip=(1,)), 7.0),
    ("a solid sunk in a body", joined([A, C]), 8.0),
    ("a zero-thickness double wall", joined([A, box([2, 0, 0], [4, 2, 2])]), 16.0),
])
def test_enclosed_volume(name, mesh, want):
    e = Enclosure(*mesh)
    assert e.volume == pytest.approx(want, rel=1e-4), name
    assert e.error < 1e-3 * want


def test_thin_plate():
    plate, block = box([0, 0, 0], [200, 120, 0.5]), box([95, 55, -5], [105, 65, 5])
    e = Enclosure(*joined([plate, block]))
    assert e.volume == pytest.approx(union([plate, block]), rel=1e-3)


def test_parity_where_triangles_disagree():
    V, F = joined([A, B])
    F = F.copy()
    F[::7] = F[::7, ::-1]
    e = Enclosure(V, F)
    assert e.parity.any()
    # (parity can't see a piece overlapping itself: the overlap counts as outside)
    assert e.volume < union([A, B])


def test_tiny_voids():
    big, void = box([0, 0, 0], [20, 20, 20]), box([5, 5, 5], [5.5, 5.5, 5.5])
    e = Enclosure(*joined([big, void], flip=(1,)))
    assert e.tiny_void_count == 1
    assert e.volume == pytest.approx(8000 - 0.125, rel=1e-4)
