"""The merged engine's fixes of 2026-10-26: edges four triangles share are split before
the shells go to MMM (mms2c/merged.py _split_pinches), the faces are checked once
written as a shell (mmm/cad.py _broken_when_saved), and the direction thin triangles must
face (mmm/cad.py Segmenter.cos_ok) is unchanged on meshes not taken for scans."""
import os
import sys
from types import SimpleNamespace

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))
pytest.importorskip("trimesh")

from mms2c import merged  # noqa: E402
from mmm import cad, meshio  # noqa: E402
from mmm.report import NULL  # noqa: E402

CUBE = [((0, 0, 0), (1, 1, 0), (1, 0, 0)), ((0, 0, 0), (0, 1, 0), (1, 1, 0)),
        ((0, 0, 1), (1, 0, 1), (1, 1, 1)), ((0, 0, 1), (1, 1, 1), (0, 1, 1)),
        ((0, 0, 0), (1, 0, 0), (1, 0, 1)), ((0, 0, 0), (1, 0, 1), (0, 0, 1)),
        ((0, 1, 0), (1, 1, 1), (1, 1, 0)), ((0, 1, 0), (0, 1, 1), (1, 1, 1)),
        ((0, 0, 0), (0, 0, 1), (0, 1, 1)), ((0, 0, 0), (0, 1, 1), (0, 1, 0)),
        ((1, 0, 0), (1, 1, 0), (1, 1, 1)), ((1, 0, 0), (1, 1, 1), (1, 0, 1))]


def _welded(*corners):
    """Unit cubes at the given corners, as one mesh with shared points welded."""
    P = np.array([p for at in corners for t in CUBE for p in t], dtype=float)
    P += np.repeat(np.array(corners, dtype=float), 3 * len(CUBE), axis=0)
    V, inv = np.unique(P, axis=0, return_inverse=True)
    return V, inv.reshape(-1, 3)


def test_two_cubes_sharing_an_edge_are_split_apart_along_it():
    """Two cubes touching along one edge: its four triangles pair up within each cube
    (across material, not crosswise), so the edge is two-sided again, each cube is a
    closed piece of its own, and every triangle keeps its row."""
    V, F = _welded((0, 0, 0), (1, 1, 0))
    before = meshio.EdgeTopology(V, F)
    assert before.nonmanifold_he.sum() == 4
    V2, F2 = merged._split_pinches(V, F)
    after = meshio.EdgeTopology(V2, F2)
    assert after.nonmanifold_he.sum() == 0 and after.boundary_he.sum() == 0
    assert len(V2) == len(V) + 2
    assert np.allclose(V2[F2], V[F])  # same triangles, same rows
    n, lab = meshio.face_components(F2, after)
    assert n == 2 and set(lab[:12]) != set(lab[12:])


def test_a_mesh_without_shared_edges_is_left_as_it_is():
    V, F = _welded((0, 0, 0))
    V2, F2 = merged._split_pinches(V, F)
    assert V2 is V and F2 is F


def test_a_box_written_as_a_shell_reads_back_with_no_broken_faces():
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    faces = {}
    exp = TopExp_Explorer(BRepPrimAPI_MakeBox(10.0, 20.0, 30.0).Shape(), TopAbs_FACE)
    while exp.More():
        faces[2 * len(faces)] = TopoDS.Face(exp.Current())
        exp.Next()
    assert cad._broken_when_saved(SimpleNamespace(region_faces=faces), shell=True) == set()


def test_thin_triangles_must_face_the_surface_as_before_on_a_clean_mesh():
    """Only a mesh taken for a scan gives thin triangles a looser limit; on a clean one
    every triangle faces within normal_deg, tiny ones any way, as MMM had it."""
    import trimesh

    m = trimesh.creation.cylinder(radius=5.0, height=10.0, sections=64)
    sh = meshio.Shell(np.asarray(m.vertices, float), np.asarray(m.faces, np.int64))
    seg = cad.Segmenter(sh.V, sh.F, sh.topo, 0.01, NULL)
    assert not seg.scan
    assert np.array_equal(seg.cos_ok, np.where(seg.tiny, -1.0, seg.cos_n))
