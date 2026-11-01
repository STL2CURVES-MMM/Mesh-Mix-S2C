"""Faces that wrap all the way round or meet at a point (2026-10-28, group C): a cone face
round its apex (mmm/cad.py CADAssembler._pole_cap_wire) and a surface winding more than
one turn round its axis (split_turns), and a torus face round its tube but part way round
its axis (CADAssembler._plan_tube_seam); and a closed region that can't be built is
reported (mmm/brep.py BRepAssembler.build). Each shape is made here with OpenCASCADE or numpy;
no mesh is stored."""
import math
import os
import sys

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "engine"))

from OCP.BRep import BRep_Builder  # noqa: E402
from OCP.BRepAdaptor import BRepAdaptor_Surface  # noqa: E402
from OCP.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse  # noqa: E402
from OCP.BRepCheck import BRepCheck_Analyzer  # noqa: E402
from OCP.BRepMesh import BRepMesh_IncrementalMesh  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone, BRepPrimAPI_MakeCylinder, BRepPrimAPI_MakeTorus  # noqa: E402
from OCP.GeomAbs import GeomAbs_Cone, GeomAbs_Torus  # noqa: E402
from OCP.StlAPI import StlAPI_Writer  # noqa: E402
from OCP.TopAbs import TopAbs_FACE  # noqa: E402
from OCP.TopExp import TopExp_Explorer  # noqa: E402
from OCP.TopoDS import TopoDS  # noqa: E402
from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt  # noqa: E402

from mmm import fidelity  # noqa: E402
from mmm.cad import convert_body_cad  # noqa: E402
from mmm.meshio import load_and_prepare  # noqa: E402
from mmm.pipeline import Options, auto_tolerance  # noqa: E402
from mmm.report import NULL  # noqa: E402


def _axis(z=0.0):
    return gp_Ax2(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1))


def _convert(shape, path, deflection=0.01, angle=0.2):
    BRepMesh_IncrementalMesh(shape, deflection, False, angle, True)
    StlAPI_Writer().Write(shape, str(path))
    bodies, _ = load_and_prepare(str(path), report=NULL)
    body = bodies[0]
    P = np.vstack([s.V for s in body])
    tol = auto_tolerance(float(np.linalg.norm(P.max(0) - P.min(0))), "cad", 0.9)
    out = convert_body_cad(BRep_Builder(), body, tol, Options(quality=0.9), NULL)
    return out, body, tol


def _faces(shape):
    out = []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        out.append(TopoDS.Face(ex.Current()))
        ex.Next()
    return out


def _of_type(shape, kind):
    return [f for f in _faces(shape) if BRepAdaptor_Surface(f).GetType() == kind]


def test_a_pointed_pin_and_a_drill_point_are_one_cone_face_each(tmp_path):
    """A pin with a 30-degree point, and a blind hole ending in a drill point: each tip is
    one cone face round its apex (it was a few dozen freeform cells or flat triangles)."""
    pin = BRepAlgoAPI_Fuse(BRepPrimAPI_MakeCylinder(_axis(), 3.0, 10.0).Shape(),
                           BRepPrimAPI_MakeCone(_axis(10.0), 3.0, 0.0, 3.0 / math.tan(math.radians(30))).Shape()).Shape()
    drill = BRepAlgoAPI_Fuse(BRepPrimAPI_MakeCylinder(_axis(-1.0), 1.2, 5.0).Shape(),
                             BRepPrimAPI_MakeCone(_axis(4.0), 1.2, 0.0, 1.2 / math.tan(math.radians(59))).Shape()).Shape()
    shape, body, tol = _convert(BRepAlgoAPI_Cut(pin, drill).Shape(), tmp_path / "pin.stl")
    assert BRepCheck_Analyzer(shape).IsValid()
    assert len(_of_type(shape, GeomAbs_Cone)) == 2
    assert len(_faces(shape)) <= 6
    v = fidelity.judge(shape, body, tol)
    assert v.ok, v.summary()


def _tapered_strips(turns=2.5, n_u=96, n_h=60, r0=5.0, r1=3.0, height=10.0):
    """A cone frustum (radius r0 at z = 0, r1 at z = height) whose side is labelled as two
    helical strips winding `turns` times round the axis, as the cone between a screw's
    thread at its tapered tip is, and two flat ends: (shell, segmenter, strip region
    numbers). The segmenter holds these regions instead of finding its own."""
    from mmm import cad, meshio
    from mmm.fitting import Cone, Plane

    us = np.linspace(0.0, 2 * np.pi, n_u, endpoint=False)
    zs = np.linspace(0.0, height, n_h + 1)
    rad = r0 + (r1 - r0) * zs / height
    V = [np.c_[rad[k] * np.cos(us), rad[k] * np.sin(us), np.full(n_u, zs[k])] for k in range(n_h + 1)]
    V = np.vstack(V + [[[0.0, 0.0, 0.0]], [[0.0, 0.0, height]]])
    bot, top = len(V) - 2, len(V) - 1
    at = lambda k, i: k * n_u + i % n_u
    F = []
    for k in range(n_h):
        for i in range(n_u):
            F += [(at(k, i), at(k, i + 1), at(k + 1, i + 1)), (at(k, i), at(k + 1, i + 1), at(k + 1, i))]
    side = len(F)
    F += [(bot, at(0, i + 1), at(0, i)) for i in range(n_u)]
    F += [(top, at(n_h, i), at(n_h, i + 1)) for i in range(n_u)]
    F = np.array(F, dtype=np.int64)
    sh = meshio.Shell(V, F)
    tol = 0.02
    seg = cad.Segmenter(sh.V, sh.F, sh.topo, tol, NULL)
    # (the strip a triangle is on: by its height against a helix rising height/turns a turn)
    c = V[F[:side]].mean(1)
    pitch = height / turns
    on = np.mod(c[:, 2] / pitch - np.mod(np.arctan2(c[:, 1], c[:, 0]), 2 * np.pi) / (2 * np.pi), 1.0) < 0.5
    apex_z = height * r0 / (r0 - r1)
    cone = Cone(np.array([0.0, 0.0, apex_z]), np.array([0.0, 0.0, -1.0]), math.atan((r0 - r1) / height))
    # (each connected piece of a strip a region, as segmenting leaves them: where a strip
    # runs into an end it leaves a short piece of its own)
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    groups = []
    for mask in (on, ~on):
        cone = Cone(cone.apex, cone.d, cone.alpha)      # (a surface of its own)
        faces = np.nonzero(mask)[0]
        idx = -np.ones(len(F), dtype=np.int64)
        idx[faces] = np.arange(len(faces))
        J = idx[np.maximum(seg.nb[faces], 0)].reshape(-1)
        I = np.repeat(np.arange(len(faces)), 3)
        ok = (seg.nb[faces].reshape(-1) >= 0) & (J >= 0)
        _, comp = connected_components(coo_matrix((np.ones(ok.sum()), (I[ok], J[ok])), shape=(len(faces),) * 2),
                                       directed=False)
        groups += [(cone, faces[comp == c]) for c in np.unique(comp)]
    groups += [(Plane(np.zeros(3), np.array([0.0, 0, -1])), side + np.arange(n_u)),
              (Plane(np.array([0.0, 0, height]), np.array([0.0, 0, 1])), side + n_u + np.arange(n_u))]
    seg.label = -np.ones(len(F), dtype=np.int64)
    seg.regions = []
    for prim, faces in groups:
        seg.label[faces] = len(seg.regions)
        seg.regions.append(cad.Region(prim, faces, seg.sense_of(prim, faces)))
    return sh, seg, tol


def test_a_strip_winding_two_and_a_half_turns_is_one_face_a_turn(tmp_path, monkeypatch):
    """Each helical strip winds 2.5 turns round the cone: one face for it overlaps itself
    in the cone's parameters and is invalid (it fell back to freeform cells). Cut at a
    meridian into one piece a turn, every piece runs less than a turn and is a valid cone
    face."""
    from mmm import cad
    monkeypatch.setattr(cad, "TURN_SPLIT", True)     # (off in the engine for now: see cad.TURN_SPLIT)
    from mmm import cad

    sh, seg, tol = _tapered_strips()
    before = sum(r.kind == "cone" for r in seg.regions)
    labels, regions = cad.split_turns(seg, seg.label.copy(), seg.regions)
    after = sum(r.kind == "cone" for r in regions)
    # (single triangles a strip leaves at an end join a neighbour when built)
    pieces = sum(r.kind == "cone" and len(r.faces) > 1 for r in regions)
    assert after > before
    rg = cad.RegionGraph(seg.V, seg.F, labels, seg.topo)
    for ri, r in enumerate(regions):
        if r.kind == "cone":
            for loop in rg.region_loops[ri]:
                P = seg.V[rg.loop_vertices(loop)]
                assert np.ptp(np.unwrap(np.arctan2(P[:, 1], P[:, 0]))) < 2 * np.pi + cad.TURN_SLACK
    asm, faces, regions = cad.build_cad_shell(sh, tol, seg=seg)
    kinds = [r.kind for r in regions]
    assert kinds.count("cone") == pieces and "freeform" not in kinds
    assert all(BRepCheck_Analyzer(f).IsValid() for f in faces)
    assert BRepCheck_Analyzer(asm.shell_from_faces(faces)).IsValid()


def test_a_bent_rod_is_one_torus_face_round_its_tube(tmp_path):
    """A quarter of a ring (R 20, r 4): its torus face goes all the way round the tube but
    only part way round the axis, so it needs a seam along one of the torus's parallels
    (it was 22 freeform cells). Three faces: the torus and the two flat ends."""
    rod = BRepPrimAPI_MakeTorus(_axis(), 20.0, 4.0, math.pi / 2).Shape()
    shape, body, tol = _convert(rod, tmp_path / "elbow.stl", deflection=0.02, angle=0.3)
    assert BRepCheck_Analyzer(shape).IsValid()
    assert len(_of_type(shape, GeomAbs_Torus)) == 1
    assert len(_faces(shape)) == 3
    v = fidelity.judge(shape, body, tol)
    assert v.ok, v.summary()


def test_a_closed_region_that_cant_be_built_is_reported():
    """A region with no boundary whose surface can't be a face without one (here a plane
    holding a whole closed shell) is recorded as failed, for build_cad_shell to replace,
    instead of being left out of the shell without a word."""
    from mmm import cad, meshio
    from mmm.fitting import Plane
    from mmm.topology import RegionGraph

    V = np.array([[0.0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1]])
    F = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
    sh = meshio.Shell(V, F)
    reg = cad.Region(Plane(np.zeros(3), np.array([0.0, 0, 1])), np.arange(4), 1.0)
    rg = RegionGraph(sh.V, sh.F, np.zeros(4, dtype=np.int64), sh.topo)
    asm = cad.CADAssembler(sh.V, rg, [reg], 0.01)
    assert asm.build() == []
    assert [r for r, _ in asm.failed_regions] == [0]
