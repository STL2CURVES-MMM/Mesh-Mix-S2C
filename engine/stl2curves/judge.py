"""The benchmark's judge: one scorecard for a STEP made from a mesh, the same for every
program. Reads the file back as a CAD program would and reports:

- solids, shells (and how many are open), faces, BRepCheck validity
- volume (OCC's integration and a tessellation's) against the mesh's
- faces by surface type, and flat triangles (planar faces with three edges: facets
  left as they were)
- distance mesh -> solid (sampled mesh corners) and solid -> mesh (points sampled on
  the faces), both measured exactly where the tessellation reads high: OCC tessellates
  big B-spline faces coarsely whatever deflection is asked (0.07 mm off at 0.002), so
  the tessellation alone would mark down spline-heavy results.
- solid -> mesh again with each mesh triangle's chordal sag allowed for (chordal_sag): a
  true curve bulges past a coarse mesh's flat facets, by 0.134 mm on MMM's
  cad_plate_holes_coarse (16-gon holes), so the right answer, true cylinders, failed the
  0.1 mm test where a copy of the facets passed (found by MMM's author's Claude, 10-25).
  Mesh corners lie on the true surface, so mesh -> solid needs no allowance.

A result is good (verdict()) when it reads back as valid closed solids with no open
shell, its volume within GOOD_VOLUME of the mesh's, and CLOSE of the points both ways
within CLOSE_MM of the other surface: what a CAD program can use as is. The merged engine
judges its rivals with it (rivals.py), and the benchmark grades every program by it.
(A copy of compare/bench/judge.py, the benchmark's own, plus verdict(): keep them the same.)

usage: python -m stl2curves.judge MESH STEP OUT.json      (trimesh + cadquery-ocp)"""
import json
import os
import sys
import time

import numpy as np
import trimesh
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepTools import BRepTools
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.GeomAbs import (GeomAbs_BezierSurface, GeomAbs_BSplineSurface, GeomAbs_Cone, GeomAbs_Cylinder,
                         GeomAbs_OffsetSurface, GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_SurfaceOfExtrusion,
                         GeomAbs_SurfaceOfRevolution, GeomAbs_Torus)
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Pnt
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID, TopAbs_WIRE
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

DEFLECTION = 0.002   # mm, tessellation for the distances and the tessellated volume
SAMPLES = 20000      # points each way (5,000 on meshes over BIG triangles)
BIG = 300_000
RECHECK = 0.01       # mm: tessellation distances above this are measured exactly
RECHECK_MOST = 4000  # at most this many points each way, the farthest first
RETRIES = ((0.01, 0.2), (0.05, 0.5), (0.2, 0.8))   # coarser tessellations for faces BRepMesh gave up on
COVERED = 0.98       # a face whose triangles cover less of its area than this gets exact checks

TYPES = {GeomAbs_Plane: "plane", GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone", GeomAbs_Sphere: "sphere",
         GeomAbs_Torus: "torus", GeomAbs_BezierSurface: "bezier", GeomAbs_BSplineSurface: "bspline",
         GeomAbs_SurfaceOfRevolution: "revolution", GeomAbs_SurfaceOfExtrusion: "extrusion",
         GeomAbs_OffsetSurface: "offset"}


def quiet(fn, *a):
    """Run fn with OCC's console chatter (stdout at the file-descriptor level) muted."""
    saved, devnull = os.dup(1), os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        return fn(*a)
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)


def read(path):
    r = STEPControl_Reader()
    if r.ReadFile(path) != IFSelect_RetDone:
        return None
    r.TransferRoots()
    return r.OneShape()


def listed(shape, kind):
    out, ex = [], TopExp_Explorer(shape, kind)
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out


def tessellate(shape, faces):
    """Triangles of every face (outward, by the face's orientation), the face each came
    from, and the faces the tessellation doesn't cover (none, or under COVERED of the
    face's area): the distances look past those, so they get exact checks of their own."""
    quiet(BRepMesh_IncrementalMesh, shape, DEFLECTION, False, 0.1, True)
    V, F, owner, base, area = [], [], [], 0, np.zeros(len(faces))
    for k, face in enumerate(faces):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        # BRepMesh gives up on some faces at a fine deflection; retry coarser
        for lin, ang in RETRIES:
            if tri is not None:
                break
            quiet(BRepMesh_IncrementalMesh, face, lin, False, ang, False)
            tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        n = tri.NbNodes()
        P = np.array([tri.Node(i).Transformed(trsf).Coord() for i in range(1, n + 1)])
        T = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]).reshape(-1, 3) - 1
        if face.Orientation() == TopAbs_REVERSED:
            T = T[:, [0, 2, 1]]
        area[k] = 0.5 * np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1).sum()
        V.append(P)
        F.append(T + base)
        owner.extend([k] * len(T))
        base += n
    mesh = trimesh.Trimesh(np.vstack(V) if V else np.zeros((0, 3)), np.vstack(F) if F else np.zeros((0, 3), int),
                           process=False)
    suspects = []
    for k, face in enumerate(faces):
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        if area[k] < COVERED * props.Mass() - 1e-4:
            box = Bnd_Box()
            BRepBndLib.Add_s(face, box)
            suspects.append((k, np.array(box.CornerMin().Coord()), np.array(box.CornerMax().Coord())))
    return mesh, np.array(owner, dtype=np.int64), suspects


SMOOTH_BEND = (1.0, 40.0)   # degrees: a bend between neighbouring triangles counted as curvature


def chordal_sag(mesh):
    """How far a true curved surface may bulge past each flat triangle of the mesh: across
    an edge where neighbouring triangles bend by th (within SMOOTH_BEND), the sagitta of
    the arc is about h * th / 8, h the smaller of the two triangles' heights across that
    edge (MMM's fidelity.chord_deflection: where a long facet meets a tiny triangle, the
    long one says nothing about curvature); each triangle takes its largest. Sharper bends
    are edges of the design, not curvature."""
    out = np.zeros(len(mesh.faces))
    if len(mesh.faces) < 2:
        return out
    f, g = mesh.face_adjacency.T
    e = mesh.face_adjacency_edges
    th = mesh.face_adjacency_angles
    L = np.maximum(np.linalg.norm(mesh.vertices[e[:, 0]] - mesh.vertices[e[:, 1]], axis=1), 1e-30)
    A = mesh.area_faces
    sag = np.minimum(2 * A[f] / L, 2 * A[g] / L) * th / 8.0
    sag[(th < np.radians(SMOOTH_BEND[0])) | (th > np.radians(SMOOTH_BEND[1]))] = 0.0
    np.maximum.at(out, f, sag)
    np.maximum.at(out, g, sag)
    return out


def stats(d):
    d = np.asarray(d)
    if not len(d):
        return None
    return {"median": float(np.median(d)), "p99": float(np.percentile(d, 99)), "max": float(d.max()),
            "within_0.02": float((d <= 0.02).mean()), "within_0.05": float((d <= 0.05).mean()),
            "within_0.1": float((d <= 0.1).mean())}


def exact_to_faces(points, dist, face_ids, faces, suspects):
    """Distances above RECHECK measured exactly: to the face the tessellation found
    nearest, and to any face the tessellation doesn't cover whose box is nearer than
    that; each kept only if smaller (the tessellation's is a true distance too)."""
    d = dist.copy()
    far = np.nonzero(d > RECHECK)[0]
    far = far[np.argsort(-d[far])][:RECHECK_MOST]
    for i in far:
        p = points[i]
        near = [k for k, lo, hi in suspects if np.linalg.norm(np.maximum(0, np.maximum(lo - p, p - hi))) < d[i]]
        v = BRepBuilderAPI_MakeVertex(gp_Pnt(*p)).Vertex()
        for k in [face_ids[i]] + near:
            ext = BRepExtrema_DistShapeShape(v, faces[k])
            if ext.IsDone():
                d[i] = min(d[i], ext.Value())
    return d, len(far)


def on_surfaces(points, face_ids, faces):
    """Points sampled on tessellation triangles moved onto their face's true surface."""
    out = points.copy()
    projectors = {}
    for i, k in enumerate(face_ids):
        pr = projectors.get(k)
        if pr is None:
            pr = projectors[k] = GeomAPI_ProjectPointOnSurf()
            u0, u1, v0, v1 = BRepTools.UVBounds_s(faces[k])
            pr.Init(BRep_Tool.Surface_s(faces[k]), u0, u1, v0, v1)
        pr.Perform(gp_Pnt(*points[i]))
        if pr.IsDone() and pr.NbPoints() > 0:
            out[i] = pr.NearestPoint().Coord()
    return out


def judge(mesh_path, step_path):
    t0 = time.time()
    card = {"step": step_path, "exists": os.path.exists(step_path)}
    if not card["exists"]:
        return card
    card["file_mb"] = round(os.path.getsize(step_path) / 1e6, 3)
    shape = quiet(read, step_path)
    card["read_ok"] = shape is not None and not shape.IsNull()
    if not card["read_ok"]:
        return card
    faces = [TopoDS.Face(f) for f in listed(shape, TopAbs_FACE)]
    shells = listed(shape, TopAbs_SHELL)
    solids = listed(shape, TopAbs_SOLID)
    card.update(faces=len(faces), shells=len(shells), solids=len(solids),
                open_shells=sum(not BRep_Tool.IsClosed_s(s) for s in shells))
    t = time.time()
    card["valid"] = bool(BRepCheck_Analyzer(shape).IsValid())
    card["check_s"] = round(time.time() - t, 1)

    types, triangles = {}, 0
    for f in faces:
        kind = TYPES.get(BRepAdaptor_Surface(f, False).GetType(), "other")
        types[kind] = types.get(kind, 0) + 1
        if kind == "plane":
            wires = listed(f, TopAbs_WIRE)
            if len(wires) == 1 and len(listed(wires[0], TopAbs_EDGE)) == 3:
                triangles += 1
    card["types"] = types
    card["triangles"] = triangles

    props = GProp_GProps()
    for s in solids:
        BRepGProp.VolumeProperties_s(s, props)
    card["volume_occ"] = props.Mass() if solids else 0.0

    original = trimesh.load(mesh_path, force="mesh")
    card["mesh_triangles"] = len(original.faces)
    card["mesh_volume"] = abs(float(original.volume))
    tess, owner, suspects = tessellate(shape, faces)
    card["uncovered_faces"] = len(suspects)
    # (a diagnostic only: BRepMesh can cover a face partly, and a sum over a surface
    # with a hole depends on the origin: Rak N 48,543 mm^3 here, OCC's own 49,215)
    card["volume_tess"] = abs(float(tess.volume)) if len(tess.faces) else 0.0
    # OCC's volume from the same triangulation, closed shells only: right where the exact
    # integration goes astray (solids sewn across gaps: a bracket 46,537 vs 62,669)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props, True, False, True)
    card["volume_occ_tri"] = abs(props.Mass())
    mv = card["mesh_volume"]
    if mv and solids:
        errors = [(v - mv) / mv for v in (card["volume_occ"], card["volume_occ_tri"])]
        card["volume_errors"] = errors
        # the nearer of the two: each goes astray on its own, a missing body moves both
        card["volume_error"] = min(errors, key=abs)
    else:
        card["volume_error"] = None

    if len(tess.faces):
        n = SAMPLES if len(original.faces) < BIG else SAMPLES // 4
        rng = np.random.default_rng(0)
        corners = original.vertices
        if len(corners) > n:
            corners = corners[rng.choice(len(corners), n, replace=False)]
        _, d1, tri1 = trimesh.proximity.closest_point(tess, corners)
        d1, re1 = exact_to_faces(corners, d1, owner[tri1], faces, suspects)
        pts, tri2 = trimesh.sample.sample_surface(tess, n, seed=0)
        pts = on_surfaces(np.asarray(pts), owner[tri2], faces)
        _, d2, near2 = trimesh.proximity.closest_point(original, pts)
        card["mesh_to_solid"] = stats(d1)
        card["solid_to_mesh"] = stats(d2)
        card["solid_to_mesh_sag"] = stats(np.maximum(0.0, d2 - chordal_sag(original)[near2]))
        card["rechecked"] = [int(re1)]
    card["judge_s"] = round(time.time() - t0, 1)
    return card


GOOD_VOLUME = 0.01   # share of the mesh's volume the solid may differ by
CLOSE_MM = 0.1       # mm, and the share of points both ways that must lie within it:
CLOSE = 0.99
SCORE_MM = 0.02      # mm of p99 distance per unit of score (closeness decides ties by a
                     # clear margin, pipeline.CLEARLY_CLOSER units: 0.02 mm)


def verdict(card):
    """(good, close, sound, score) for a scorecard: as compare/bench/summary.py grades."""
    vol = card.get("volume_error")
    sound = bool(card.get("read_ok") and card.get("valid") and card.get("solids") and not card.get("open_shells")
                 and vol is not None and abs(vol) <= GOOD_VOLUME)
    ways = [card.get("mesh_to_solid") or {}, card.get("solid_to_mesh_sag") or card.get("solid_to_mesh") or {}]
    close = all(w.get(f"within_{CLOSE_MM:g}", 0.0) >= CLOSE for w in ways)
    p99 = max((w.get("p99") if w.get("p99") is not None else 1e9) for w in ways)
    return sound and close, close, sound, -p99 / SCORE_MM


def main(argv):
    mesh_path, step_path, out = argv[:3]
    try:
        card = judge(mesh_path, step_path)
    except Exception as e:  # a scorecard that says why, rather than none
        card = {"step": step_path, "error": f"{type(e).__name__}: {e}"}
    with open(out, "w") as f:
        json.dump(card, f, indent=1)


if __name__ == "__main__":
    main(sys.argv[1:])
