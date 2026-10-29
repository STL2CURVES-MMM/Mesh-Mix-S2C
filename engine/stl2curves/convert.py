"""
stl2curves - convert STL mesh files into solid STEP files for Fusion / any CAD.

What it does:
  1. Reads the STL triangles and groups them into flat facets.
  2. Finds curved areas and works out the exact surface each lies on: holes, pins,
     rounded edges (fillets), chamfers/countersinks around round edges, cones,
     domes, dimples, balls and rounded corners.
  3. Builds a real solid from exact faces: one flat face per flat region (a box
     wall is one face you can select, offset, sketch on, press/pull...) and one
     true curved face per curved area, so Fusion sees real circular edges you can
     select, measure, dimension and fillet.
  4. Writes a .step file next to the STL (or into --out folder).

Straight chamfers and flat faces are exact already. Curved areas that don't match
one of the recognised shapes cleanly (freeform surfaces, variable-radius blends,
odd corner blends) stay as small flat facets.

Usage:
  stl2curves file1.stl [file2.stl ...] [--out FOLDER] [--merge NAME]
  stl2curves some_folder            (converts every .stl and .3mf in it)

  (or "python -m stl2curves ..." when it isn't installed as a command)

  --merge NAME     also write all inputs together into one NAME.step
  --tol X          sewing tolerance in mm (default 0.01)
  --no-fuse        keep overlapping bodies within one STL as separate bodies
  --no-curves      skip curve detection (leave everything faceted)
  --true-size      rebuild at the size the part appears to have been designed at
                   (undoing e.g. a 99% slicer scale or an inch/cm export), with radii
                   snapped to round values
  --details        list every rebuilt feature with its size
  --simplify MM    thin out an over-dense mesh first, moving its surface by at most MM
                   (0: never; by default meshes over 1,000,000 triangles, at 0.005 mm)
  --no-repair      don't mend mesh defects first
"""
import argparse
import math
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Iterator, TopoDS_Shape, TopoDS_Shell
from OCP.BRepTools import BRepTools, BRepTools_WireExplorer
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
from OCP.BRepClass3d import BRepClass3d_SolidClassifier
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.ShapeFix import ShapeFix_Solid, ShapeFix_Shape
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.TopAbs import (TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID, TopAbs_VERTEX, TopAbs_WIRE, TopAbs_IN,
                        TopAbs_FORWARD, TopAbs_REVERSED)
from OCP.STEPControl import STEPControl_Writer, STEPControl_Reader, STEPControl_AsIs
from OCP.Interface import Interface_Static
from OCP.IFSelect import IFSelect_RetDone
from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopLoc import TopLoc_Location
from OCP.collections import (List_TopoDS_Shape, IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher,
                             IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher)

from . import __version__
from . import features as features_mod
from .features import load_stl, analyze, summarize, snap, half_rings, Mesh, TOL, time_left
from .sizing import guess_size
from .build import build_faces, sew, features_near, point_facet_distance, settle_blends, TRYING
from . import workers
from .bodies import split_bodies
from .threads import carve as thread_carve
from .features import carve_exact
from .blends import add_blends, split as split_blend, _blend as as_blend, fallback as blend_fallback, carve, pipe_fallback
from .repair import repair, leftovers
from . import accuracy, progress, stages
from .simplify import simplify
from .read3mf import read_3mf

SEARCH_SECONDS = 600    # time allowed for hunting down patches that spoil the solid
TROUBLE_SECONDS = 600   # time for dropping troublemakers one by one, per body; then every patch
HURRY_REACH = 0.5       # mm: this near trouble goes at once, four times as far each further round ...
HURRY_WIDEST = 3        # ... up to this round's reach (8 mm) ...
HURRY_ROUNDS = 10       # ... for this many rounds; past them, wider again without end
BLAME_TIE = 0.01        # mm: patches this much nearer trouble than another count as level with it
TIME_LIMIT = 600        # seconds of building after which the optional refinements stop (--time-limit); see stl_to_solid
SEW_PER_WORST = 1.5     # a part is sewn at this many times its loosest patch's distance off the mesh ...
SEW_MOST = 0.2          # mm: ... but never wider than this ...
SEW_EDGE = 2.0          # ... nor than this many times the mesh's median edge (see sewing_cap)
LOOSE_ROUNDS = 3        # out of time with nothing blamed: tries without the loosest patches before none
SWAP_ROUNDS = 6        # STL2CURVES_TRY=swap: rounds of taking blamed patches out again
FIX_PRECISIONS = (1e-5, 1e-4)   # mm: precisions ShapeFix tries on an invalid solid after its default
EMPTY_SHELL = 1e-6      # closed shells with less volume than this share of the biggest are dropped
SPECK_AREA = 1e-6       # mm^2: faces joined to nothing else in their shell, this small in all, are dropped
FUSE_SECONDS = 30       # bodies whose fuse takes longer than this are handed over side by side
TIDY_SECONDS = 120      # a final tidy-up taking longer than this is given up (the part is kept as checked)
TIDY_WORKER_FACES = 20000   # ...on a part of at least this many faces (smaller ones are tidied in place)
TIDY_TRIES = 3          # tidy-ups tried, each keeping apart the faces whose merge came out invalid
BARE_AHEAD = 5000       # facets from which the bare-facet build is made ahead, in a worker process
WORKERS_FROM = 5000     # triangles from which a part is worth starting worker processes for
BARE_DRIFT = 1e-4       # share of a sound mesh's volume its bare facets may differ by and still be a reference
TESS_DEFLECTION = 0.0005    # mm: how closely a solid's faces are tessellated to measure its volume
UV_LIMIT = 1e5              # a face's parameters (mm, or radians) beyond this: broken, not tessellated
VOLUME_EPS = 1e-5       # relative precision of the volume integration
VOLUMES_KEPT = 4        # shapes whose volume is remembered (see volume)
TESS_SPECK = 0.05       # mm^2: a face this small that won't tessellate is left out of that measure
LOOSE_EDGE = 3.0        # an edge whose tolerance is this many times the sewing's strays from its faces
FILE_VOLUME = 1e-3      # share of the volume a finished part's STEP file may read back off by
FILE_MATCH = 0.05       # mm (and share): how far a face's middle or outline may move read back from the file
FILE_AREA = 0.5         # mm^2 (plus half a percent): how much a face's area may change read back from the file
BLAME_GUIDED = True     # second chances follow what a failed try blames, before halving the group
FILE_SECOND_CHANCE = False    # patches dropped for what their STEP file does get no second chance
FILE_ROUNDS = 3         # rounds of patches dropped for what their STEP file does, at most, per body
FILE_CHECK_FACES = 50000    # a part of more faces (facets, while building) isn't written and read back:
                            # on 218k faces that took longer than the whole build
                            # where OCC's integration and the expected volume disagree
UNIFY_TOL = 0.001       # mm: faces this close to one surface are merged at the end ...
UNIFY_DEG = 0.1         # ... if their normals agree this closely
UNIFY_VOLUME = 1e-3     # and kept if the volume stays this close (the integration itself
                        # wobbles by a few mm^3 on a big part)
AUTO_SIMPLIFY = 1000000 # meshes with more triangles than this are thinned out first ...
                        # (only for memory and time: thinned, a fine CAD export's rows of corners
                        # along each curve become slivers across it, which no surface is found on,
                        # and analysis grinds; 2026-10-15, ten meshes of 89k-396k triangles each
                        # converted both ways: unthinned did better in nine, mostly far better (a
                        # 396k plate 13,476 faces in 1,366 s vs 29,968 in 3,661 s; a 159k board
                        # 1,101 vs 3,663; the instrument body 205 vs 20,881), the tenth (the toolbox,
                        # 2,446 thinned vs 5,801) only because its dropped fillets cost fewer faces)
SIMPLIFY_ERROR = 0.005  # ... moving the surface by at most this (mm)


def count(shape, kind):
    n, ex = 0, TopExp_Explorer(shape, kind)
    while ex.More():
        n += 1
        ex.Next()
    return n


def volume(shape):
    # adaptive integration: the default's fixed sample points undercount long spline
    # faces (a thread flank winding five turns came out ~80 mm^3 short); 1e-5 is within
    # 0.005 mm^3 of 1e-6 on a 16,000 mm^3 part, and a fifth quicker
    for known, v in _volumes:
        if known.IsSame(shape):
            if known.Orientation() == shape.Orientation():
                return v
            if {known.Orientation(), shape.Orientation()} == {TopAbs_FORWARD, TopAbs_REVERSED}:
                return -v       # (the same solid turned inside out)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props, VOLUME_EPS)
    _volumes.append((shape, props.Mass()))
    del _volumes[:-VOLUMES_KEPT]
    return props.Mass()


# The last few shapes measured, with their volumes: an attempt measures its solid, and
# the STEP check measures it again (4 s each on a 9,000-face part). Kept as references,
# so a shape can't be freed and another made in its place; same shape means same
# TShape and location (and the opposite orientation, the opposite volume).
_volumes = []


def _quick_volume(shape):
    """OCC's volume with its default (fixed) integration: quick, but off on long spline
    faces (see volume). For comparing a shape with a copy of itself."""
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def tessellated_volume(shape):
    """The volume a fine tessellation of the shape's faces encloses, or None if a face
    won't tessellate. OCC integrates face by face, each over its own surface; where the
    sewing joined edges a few hundredths of a millimetre apart (a curved patch's outline
    bowed onto its surface, the flat facet beside it keeping the straight chord) the
    faces don't quite meet, and the integration can come out several mm^3 off, one way
    or the other. The tessellation follows the shared edges, so it closes up.
    tessellated_volume.untessellated: a point on each face (bar specks) that wouldn't."""
    tessellated_volume.untessellated = []
    # (a face sewn shut across a wide gap can come out with a parameter range of millions,
    # a torus wound round a million times: OCC's mesher then never returns. Such a face
    # is untessellated, for the blame, and nothing is meshed)
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        ex.Next()
        if max(abs(x) for x in BRepTools.UVBounds_s(face)) > UV_LIMIT:
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            c = props.CentreOfMass()
            tessellated_volume.untessellated.append((c.X(), c.Y(), c.Z()))
    if tessellated_volume.untessellated:
        return None
    BRepTools.Clean_s(shape)
    BRepMesh_IncrementalMesh(shape, TESS_DEFLECTION, False, 0.1, True)
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        ex.Next()
        if BRep_Tool.Triangulation_s(face, TopLoc_Location()) is None:
            # (a speck of a face, a sliver the sewing all but closed: nothing to measure)
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            if abs(props.Mass()) >= TESS_SPECK:
                c = props.CentreOfMass()
                tessellated_volume.untessellated.append((c.X(), c.Y(), c.Z()))
    # (summed over the triangles by OCC itself: reading half a million nodes into numpy
    # took twice as long as the meshing)
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, props, False, False, True)
    total = props.Mass()
    BRepTools.Clean_s(shape)
    return None if tessellated_volume.untessellated else float(total)


tessellated_volume.untessellated = []


def fixed(solid):
    fix = ShapeFix_Solid(solid)
    fix.Perform()
    return fix.Solid()


def compound(shapes):
    comp = TopoDS_Compound()
    b = BRep_Builder()
    b.MakeCompound(comp)
    for s in shapes:
        b.Add(comp, s)
    return comp


def boolean(op, args, tools):
    lst = lambda xs: (l := List_TopoDS_Shape(), [l.Append(x) for x in xs])[0]
    algo = op()
    algo.SetArguments(lst(args))
    algo.SetTools(lst(tools))
    algo.SetFuzzyValue(1e-5)
    algo.SetRunParallel(True)
    algo.SetUseOBB(True)
    algo.Build()
    if not algo.IsDone():
        raise RuntimeError("boolean operation failed")
    return algo.Shape()


def mesh_volume(mesh):
    a, b, c = (mesh.pts[mesh.tris[:, k]] for k in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6)


def solids_from_shells(sewn, inward=True, specks=False):
    """Turn sewn shells into solids. A shell inside another is a cavity, the rest are bodies
    (inward False: the mesh has no inward-facing shell, so every shell is a body;
    specks True: each shell less its specks, see _without_specks; None if no shell
    has any, the solids then being the same as without).

    Returns (shape, number of bodies, number of cavities, total volume of material).
    """
    found, ex = [], TopExp_Explorer(sewn, TopAbs_SHELL)
    while ex.More():
        found.append(TopoDS.Shell(ex.Current()))
        ex.Next()
    if specks:
        trimmed = [_without_specks(shell) for shell in found]
        if all(t is s for t, s in zip(trimmed, found)):
            # (nothing to leave out: made again, the solid would only be the same one,
            # at the cost of a whole part's solids and volumes, 45 s on 46,000 faces)
            return None
        found = trimmed
    shells = []
    for shell in found:
        solid = fixed(BRepBuilderAPI_MakeSolid(shell).Solid())   # oriented outwards
        if solid.ShapeType() != TopAbs_SOLID:
            ex2 = TopExp_Explorer(solid, TopAbs_SOLID)
            if not ex2.More():
                continue                    # not a closed piece: ignore it
            solid = TopoDS.Solid(ex2.Current())
        solid = _without_empty_shells(solid)
        v = volume(solid)
        if v < 0:
            solid = TopoDS.Solid(solid.Reversed())
            v = -v
        shells.append((shell, solid, v))
    if not shells:
        raise RuntimeError("no closed body found")
    # (a closed shell with next to no volume is two faces lying on each other, where the
    # mesh had a wall of no thickness: no material, and not a body)
    biggest = max(abs(v) for _, _, v in shells)
    shells = [x for x in shells if abs(x[2]) > EMPTY_SHELL * biggest]
    shells.sort(key=lambda x: -x[2])        # biggest first: an enclosing shell comes first

    def points_of(shape, most=24):
        # corners and face centres (a rib spanning wall to wall has every corner buried
        # in the walls, but the middle of its top face is out in the open)
        faces, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
        while ex.More():
            faces.append(ex.Current())
            ex.Next()
        corners, ex = [], TopExp_Explorer(shape, TopAbs_VERTEX)
        while ex.More():
            corners.append(ex.Current())
            ex.Next()
        pts = []
        for k in range(0, len(faces) + len(corners), max(1, (len(faces) + len(corners)) // most)):
            if k < len(faces):
                # (only the faces sampled: working out every face's centre is slow)
                props = GProp_GProps()
                BRepGProp.SurfaceProperties_s(faces[k], props)
                pts.append(props.CentreOfMass())
            else:
                pts.append(BRep_Tool.Pnt_s(TopoDS.Vertex(corners[k - len(faces)])))
        return pts

    def inside(body, pts):
        # (one classifier per body, set up once: making one per point re-reads a big
        # body's faces each time; a point outside its box is outside it)
        lo, hi = body[2]
        if any(not (lo[0] <= p.X() <= hi[0] and lo[1] <= p.Y() <= hi[1] and lo[2] <= p.Z() <= hi[2])
               for p in pts):
            return False
        if body[3] is None:
            body[3] = BRepClass3d_SolidClassifier(body[0])
        out = []
        for p in pts:
            body[3].Perform(p, 1e-6)
            if body[3].State() != TopAbs_IN:
                out.append((p.X(), p.Y(), p.Z()))
        if out:
            # (a shell wholly within a body's box but not wholly inside it: a cavity
            # that a stray face let out, the likeliest culprit if it now counts as a
            # body; see attempt)
            straying.extend(out)
        return not out

    def box(shape):
        b = Bnd_Box()
        BRepBndLib.Add_s(shape, b)
        p, q = b.CornerMin(), b.CornerMax()
        return (p.X(), p.Y(), p.Z()), (q.X(), q.Y(), q.Z())

    # A cavity lies wholly inside its host; an overlapping body (a rib sunk into a
    # floor, say) has some corners outside, and gets fused on instead.
    bodies, cavities, total = [], [], 0.0      # bodies: [outer solid, [cavity solids], box, classifier]
    straying = solids_from_shells.straying = []
    for shell, solid, v in shells:
        pts = points_of(shell) if bodies and inward else None
        host = next((b for b in bodies if inside(b, pts)), None) if pts else None
        if host is None:
            bodies.append([solid, [], box(solid), None])
            total += v
        else:
            host[1].append(solid)
            cavities.append(solid)
            total -= v
    solids = []
    for outer, holes, _, _ in bodies:
        if not holes:
            solids.append(outer)
            continue
        maker = BRepBuilderAPI_MakeSolid(TopoDS.Shell(TopExp_Explorer(outer, TopAbs_SHELL).Current()))
        for h in holes:
            inner = TopoDS.Shell(TopExp_Explorer(h, TopAbs_SHELL).Current())
            maker.Add(TopoDS.Shell(inner.Reversed()))
        solids.append(fixed(maker.Solid()))
    # (bodies side by side: fusing the ones that overlap is left until the part has
    # checked out, see fuse_overlapping; on an assembly of many bodies it is slow)
    solid = solids[0] if len(solids) == 1 else compound(solids)
    return solid, len(solids), len(cavities), total


def _without_specks(shell):
    """The shell less any faces of next to no area joined by no edge to a face with area:
    flat facets of zero area that sewing left in the part's shell made it 'not
    connected', so the solid failed the check with every face valid and nothing to blame
    (a part with six slots inside: four such faces cost the halving search half its
    curves)."""
    tiny, ex = [], TopExp_Explorer(shell, TopAbs_FACE)
    while ex.More():
        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(ex.Current(), props)
        if abs(props.Mass()) <= SPECK_AREA:
            tiny.append(ex.Current())
        ex.Next()
    if not tiny:
        return shell
    emap = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(shell, TopAbs_EDGE, TopAbs_FACE, emap)

    def joined(face):       # (to a face with area, through any of its edges)
        ee = TopExp_Explorer(face, TopAbs_EDGE)
        while ee.More():
            if emap.Contains(ee.Current()):
                for other in emap.FindFromKey(ee.Current()):
                    if not any(other.IsSame(t) for t in tiny):
                        return True
            ee.Next()
        return False
    drop = [t for t in tiny if not joined(t)]
    if not drop:
        return shell
    out = TopoDS_Shell()
    builder = BRep_Builder()
    builder.MakeShell(out)
    ex = TopExp_Explorer(shell, TopAbs_FACE)
    while ex.More():
        if not any(ex.Current().IsSame(t) for t in drop):
            builder.Add(out, ex.Current())
        ex.Next()
    out.Closed(shell.Closed())
    return out


def _without_empty_shells(solid):
    """The solid less any shell in it with next to no volume. Sewing can join a bubble of
    two faces lying on each other (tiny sphere pieces, a fifth of a millimetre across) to
    the part's shell; ShapeFix then splits it off as a second shell of the same solid,
    which makes the solid invalid with every face in it valid, and nothing to blame."""
    shells, ex = [], TopExp_Explorer(solid, TopAbs_SHELL)
    while ex.More():
        shells.append(TopoDS.Shell(ex.Current()))
        ex.Next()
    if len(shells) < 2:
        return solid
    sizes = [abs(volume(BRepBuilderAPI_MakeSolid(s).Solid())) for s in shells]
    keep = [s for s, v in zip(shells, sizes) if v > EMPTY_SHELL * max(sizes)]
    if len(keep) == len(shells):
        return solid
    maker = BRepBuilderAPI_MakeSolid()
    for s in keep:
        maker.Add(s)
    return maker.Solid()


def inward_shells(mesh):
    """Does any closed shell of the mesh face inward (a cavity)? Without one, no shell
    built from it can be a cavity, and sorting cavities from bodies (slow when a part is
    many bodies) is skipped. A shell here: triangles joined through edges they share."""
    if "inward" not in mesh.__dict__:
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components
        T = mesh.tris
        e = np.sort(np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]]), axis=1)
        owner = np.tile(np.arange(len(T)), 3)
        key = e[:, 0].astype(np.int64) * (len(mesh.pts) + 1) + e[:, 1]
        order = np.argsort(key, kind="stable")
        k = key[order]
        same = np.nonzero(k[1:] == k[:-1])[0]
        a, b = owner[order[same]], owner[order[same + 1]]
        graph = coo_matrix((np.ones(len(a)), (a, b)), shape=(len(T), len(T)))
        n, label = connected_components(graph, directed=False)
        p, q, r = (mesh.pts[T[:, i]] for i in range(3))
        vol = np.bincount(label, np.einsum("ij,ij->i", p, np.cross(q, r)), n)
        mesh.inward = bool((vol < 0).any())
    return mesh.inward


def fuse_overlapping(shape):
    """Fuse a checked part's bodies where they overlap (a rib sunk into a floor). Kept
    only if the result is a valid solid whose volume lies between the biggest body's
    and all of them together; otherwise they are handed over side by side."""
    solids, ex = [], TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        solids.append(ex.Current())
        ex.Next()
    if len(solids) < 2:
        return shape
    vols = [volume(s) for s in solids]
    slack = UNIFY_VOLUME * sum(vols) + 1e-3
    fused = fuse_checked(solids[:1], solids[1:], max(vols) - slack, sum(vols) + slack)
    return shape if fused is None else fused


def fuse_checked(args, tools, low, high):
    """The fuse of the shapes if it is a valid solid with a volume between low and high,
    else None. Done in a worker process when there are any, and given up after
    FUSE_SECONDS (a boolean on a part of many thousand faces can grind on for minutes;
    bodies merely touching or apart gain nothing from it anyway)."""
    pool = workers.get()
    if pool is None:
        return _fuse_checked(args, tools, low, high)
    import shutil
    import tempfile
    from concurrent.futures import TimeoutError as Timeout
    tmp = tempfile.mkdtemp(prefix="stl2curves_")
    try:
        a, t, out = (os.path.join(tmp, n) for n in ("args.brep", "tools.brep", "fused.brep"))
        BRepTools.Write_s(compound(args), a)
        BRepTools.Write_s(compound(tools), t)
        job = pool.submit(_fuse_job, (a, t, low, high, out))
        try:
            if not job.result(timeout=FUSE_SECONDS):
                return None
        except Timeout:
            workers.abandoned()     # (the worker is left to finish; then restarted)
            return None
        except Exception:
            workers.broken()
            return None
        fused = TopoDS_Shape()
        BRepTools.Read_s(fused, out, BRep_Builder())
        return fused
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _fuse_job(job):
    """In a worker process: _fuse_checked on shapes read from BRep files, the result
    written to one. True if there is a result."""
    a, t, low, high, out = job
    shapes = []
    for path in (a, t):
        comp = TopoDS_Shape()
        BRepTools.Read_s(comp, path, BRep_Builder())
        it, items = TopoDS_Iterator(comp), []
        while it.More():
            items.append(it.Value())
            it.Next()
        shapes.append(items)
    fused = _fuse_checked(shapes[0], shapes[1], low, high)
    return fused is not None and BRepTools.Write_s(fused, out)


def _fuse_checked(args, tools, low, high):
    try:
        fused = boolean(BRepAlgoAPI_Fuse, args, tools)
    except RuntimeError:
        return None
    if count(fused, TopAbs_FACE) and BRepCheck_Analyzer(fused, True, True).IsValid() and low <= volume(fused) <= high:
        return fused
    return None


def attempt(mesh, features, mesh_tol, tol, fuse, faceted_volume):
    """Build and sew the part with these features. Returns (result, to_drop): result is
    (shape, bodies, cavities), or None if it fails the checks; to_drop lists the patches
    to leave out next time (faces that couldn't be built, or those blamed for a gap or an
    invalid face), and attempt.blamed says whether they were only blamed. attempt.why
    names the check that failed ("build", "free", "nosolid", "bodies", "volume",
    "volume2", "invalid"; "ok"), for diagnosing a slow or poor conversion."""
    attempt.blamed = False
    attempt.why = "ok"
    mesh.tries = mesh.__dict__.get("tries", 0) + 1
    mesh.pool_tries = mesh.__dict__.get("pool_tries", 0) + 1
    progress.step(f"building the solid and checking it (try {mesh.tries}, {len(features)} curved areas)")
    comp, shells, failed = build_faces(mesh, features, mesh_tol)
    if failed:
        attempt.why = "build"
        return None, failed
    attempt.blamed = True
    worst = max((f.worst for f in features), default=0.0)
    sewing = max(tol, min(SEW_PER_WORST * worst, sewing_cap(mesh, tol)))
    sewn, free = sew(comp, sewing, shells, mesh.pts)
    if free:
        # patches whose faces left gaps: drop just those and try again
        attempt.why = "free"
        blame = culprits(mesh, features, free)
        gaps = {tuple(np.round(p, 2)) for p in free}
        mesh.last_gaps = gaps
        known = mesh.__dict__.setdefault("lasting_gaps", set())
        if not blame and mesh.pool_tries > 1 and not gaps <= known:
            # Gaps near no patch: the corners shared across attempts (build.Edges) have
            # gathered the small widenings each flat face is allowed (SHARED_TOLERANCE)
            # attempt after attempt, until sewing lost edges between slivers a few
            # microns apart (Rak Uni: the same 8 gaps whatever was left, even with no
            # curves at all, and no solid; built bare first it was fine). Afresh, once.
            mesh.__dict__.pop("corner_pool", None)
            mesh.__dict__.pop("face_cache", None)
            mesh.pool_tries = 0
            got = attempt(mesh, features, mesh_tol, tol, fuse, faceted_volume)
            if attempt.why == "free" and not got[1]:
                # (built afresh, the same gaps: the mesh's own, not the shared corners'.
                # Not built afresh again for them: the solar bracket rebuilt every face
                # 83 times, on every other try, for gaps that stayed)
                known |= mesh.last_gaps
            return got
        return None, blame
    try:
        shape, nb, nv, signed = solids_from_shells(sewn, inward_shells(mesh))
    except RuntimeError:
        attempt.why = "nosolid"
        return None, []
    # (the bare facets' build is waited for only where its answer can matter: one body
    # can't be more than the bare facets make, and on a big mesh that build takes a
    # while: the instrument body's first try waited 69 s for it)
    if nb > 1:
        _mesh_defective(mesh, tol, fuse)
    if nb > 1 and mesh.bare_bodies and nb > mesh.bare_bodies:
        # A body the bare facets don't make: a face ran off somewhere, or a cavity passed
        # for a body because edges beside it had their tolerance widened by millimetres
        # (20 mm on a part with six slots inside: every slot's corners classified as on
        # its surface, not inside it). Blamed as for the volume; blaming nothing left the
        # halving search to throw away every curve of the part (53k triangles -> 42,925 faces).
        attempt.why = "bodies"
        # (first the patches where a shell inside a body's box came out of it: blaming
        # only invalid faces and loose edges, a 159k-triangle board with 12 cavities had
        # nothing to blame, and the halving search ran out of time with every curve of
        # the part left out: 65,273 faces)
        return None, (culprits(mesh, features, solids_from_shells.straying)
                      or _invalid_blame(mesh, features, shape, sewing))
    change = sum(f.change for f in features)
    expected = faceted_volume + change
    allowed = sum(f.tolerance for f in features) + 1e-6 * abs(faceted_volume) + 1e-3
    tessellated_volume.untessellated = []
    if abs(signed - expected) > allowed:
        # (OCC's integration can be off where the sewing closed wide gaps: measured
        # again from the faces' tessellation, which follows the edges they share)
        tess = tessellated_volume(shape)
        if tess is not None and abs(tess - expected) <= allowed:
            signed = tess
    if abs(signed - expected) > allowed:
        # a mesh whose bare facets already don't add up to its volume (it crosses itself,
        # or is many bodies touching at corners, some too thin to make a solid) is
        # measured against what the bare facets give instead
        defective = _mesh_defective(mesh, tol, fuse)
        drift = abs(mesh.bare_volume - faceted_volume) if mesh.bare_volume is not None else None
        # (a sound mesh only by what flattening its facets moves: anything more would be
        # the bare build itself going wrong, and is no reference)
        if drift is None or (not defective and drift > BARE_DRIFT * abs(faceted_volume) + 1e-3):
            attempt.why = "volume"
            return None, _invalid_blame(mesh, features, shape, sewing)
        expected = mesh.bare_volume + change
        # (a defective mesh give or take what the defect itself does: it sews a little
        # differently each time)
        slack = drift if defective else 0.0
        if abs(signed - expected) > allowed + slack:
            attempt.why = "volume2"
            return None, _invalid_blame(mesh, features, shape, sewing)
    check = BRepCheck_Analyzer(shape, True, True)
    if not check.IsValid():
        _mesh_defective(mesh, tol, fuse)        # (mesh.defective, below)
        mended = None
        # (zero-area specks that sewing left in a shell, joined to nothing: looked for only
        # now, finding them takes a second or so on a few thousand faces)
        try:
            respun = solids_from_shells(sewn, inward_shells(mesh), specks=True)
        except RuntimeError:
            respun = None       # (no closed shell left without them: the check's verdict stands)
        # (the same part, only valid: as many bodies and cavities, the same volume. On a
        # mesh of two bodies crossing each other one came back otherwise, valid with a
        # quarter of the part gone)
        if (respun is not None and respun[1:3] == (nb, nv) and abs(volume(respun[0]) - signed) <= allowed
                and BRepCheck_Analyzer(respun[0], True, True).IsValid()):
            mended = respun[0]
        if mended is None and not mesh.__dict__.get("defective"):      # (no repair mends a mesh that crosses itself)
            # (a straight edge running on tangent into an arc can pass for a crossing at
            # the default precision; a coarser one, still far below the sewing, mends it)
            for precision in (None,) + FIX_PRECISIONS:
                fix = ShapeFix_Shape(shape)
                if precision:
                    fix.SetPrecision(precision)
                fix.Perform()
                fixed_shape = fix.Shape()
                if BRepCheck_Analyzer(fixed_shape, True, True).IsValid() and abs(volume(fixed_shape) - expected) <= allowed:
                    mended = fixed_shape
                    break
        if mended is not None:
            shape = mended
        else:
            # patches next to faces that came out invalid: drop just those and try again
            blame = culprits(mesh, features, invalid_face_points(shape, check))
            if blame or not _mesh_defective(mesh, tol, fuse):
                attempt.why = "invalid"
                return None, blame
            # nothing to blame and the mesh as bare facets fails the same way: the
            # defect is in the mesh itself (it touches itself, say), not in the curves
    trial = mesh.__dict__.get("file_trial", False)
    if (features and not mesh.__dict__.get("file_off") and len(mesh.farea) <= FILE_CHECK_FACES
            and (trial or mesh.__dict__.get("file_rounds", 0) < FILE_ROUNDS)):
        # The solid is only as good as its STEP file. A file keeps no tolerances, and a
        # reader works them out again from the geometry, tight: a face that checked out
        # only within the wide tolerance sewing gave its edges can come back crossing
        # itself, split up, or (an edge closed up by sewing) running round a whole circle.
        trouble = file_trouble(shape, allowed)
        if trouble:
            blame = culprits(mesh, features, trouble)
            if blame:
                # (a few rounds at most: trouble the patches nearby don't cure is left to
                # the final check's warning rather than costing every patch round it)
                if not trial:
                    mesh.file_rounds = mesh.__dict__.get("file_rounds", 0) + 1
                attempt.why = "file"
                return None, blame
            # (nothing near to blame: the bare facets themselves, kept as they are)
    return (shape, nb, nv), []


def read_step(path):
    """The shape in a STEP file (OpenCascade's transfer statistics silenced)."""
    sys.stdout.flush()
    saved, devnull = os.dup(1), os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        r = STEPControl_Reader()
        r.ReadFile(str(path))
        r.TransferRoots()
        return r.OneShape()
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)


def file_trouble(shape, allowed):
    """Write the shape to STEP and read it back: None if it comes back a valid solid of
    the same volume (within allowed, mm^3), else points where it doesn't (maybe none
    found). Only real changes count: a face that comes back invalid, goes missing or
    changes its area by more than FILE_AREA (small faces read back a percent or so off,
    their tolerant edges laid out afresh, and that is harmless); and on a big face only
    the outline (wire) that changed, not every hole in it."""
    tmp = tempfile.mkdtemp(prefix="stl2curves_")
    try:
        path = os.path.join(tmp, "check.step")
        write_step(shape, path)
        back = read_step(path)
    except Exception:
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    check = BRepCheck_Analyzer(back, True, True)
    # (a reader may split a face in two: harmless, so faces aren't counted)
    if check.IsValid():
        # (first OCC's quick integration on both: off on long spline faces, but the same
        # way on a copy that came back the same, and a tenth of the time (Rak N-USB:
        # 0.4 s, not 5, on each of its seven accepted tries); if they differ, as they do
        # by more than the adaptive volumes where the reader lays out edges afresh, the
        # adaptive ones decide)
        if abs(_quick_volume(back) - _quick_volume(shape)) <= allowed:
            return None
        if abs(volume(back) - volume(shape)) <= allowed:
            return None
        # OCC's integration goes astray on long helical spline faces, and differently in
        # the shape and in its copy read back (a thread flank of 25.3 mm^2 by tessellation
        # integrated to 20.5, its copy elsewhere): the gutter mount's threaded hole lost
        # 550 facets of thread to the blame. Measured by tessellation, as attempt does.
        mine, theirs = tessellated_volume(shape), tessellated_volume(back)
        if mine is not None and theirs is not None and abs(mine - theirs) <= allowed:
            return None

    def faces(s):
        out, ex = [], TopExp_Explorer(s, TopAbs_FACE)
        while ex.More():
            face = TopoDS.Face(ex.Current())
            ex.Next()
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            c = props.CentreOfMass()
            out.append((face, int(BRepAdaptor_Surface(face).GetType()), props.Mass(), np.array([c.X(), c.Y(), c.Z()])))
        return out

    def partner(item, pool, table):
        # the face in the other shape most like this one: same kind of surface, middle
        # nearby (a big face's middle moves further when it changes), area closest
        _, kind, area, centre = item
        kinds, areas, centres, tree = table
        reach = FILE_MATCH + 0.02 * math.sqrt(abs(area))
        if tree is None:
            return None
        # a hair wider than reach: the tree may round differently from the exact test below
        cand = np.array(sorted(tree.query_ball_point(centre, reach * (1 + 1e-9))), dtype=int)
        near = cand[(kinds[cand] == kind) & (np.linalg.norm(centres[cand] - centre, axis=1) <= reach)]
        return pool[near[np.argmin(np.abs(areas[near] - area))]] if len(near) else None

    def table(pool):
        from scipy.spatial import cKDTree
        centres = np.array([x[3] for x in pool]).reshape(-1, 3)
        return (np.array([x[1] for x in pool]), np.array([x[2] for x in pool]), centres,
                cKDTree(centres) if len(pool) else None)

    mine, theirs = faces(shape), faces(back)
    mine_t, theirs_t = table(mine), table(theirs)
    points = []
    for item in mine:
        other = partner(item, theirs, theirs_t)
        if other is None or abs(other[2] - item[2]) > FILE_AREA + 0.005 * abs(item[2]):
            points += _changed_outline(item[0], None if other is None else other[0])
    for item in theirs:
        if not check.IsValid(item[0]):
            other = partner(item, mine, mine_t)
            points += (_changed_outline(other[0], item[0]) if other is not None
                       else _vertex_points(item[0]))
    return points or invalid_face_points(back, check)


def _vertex_points(shape):
    out, vx = [], TopExp_Explorer(shape, TopAbs_VERTEX)
    while vx.More():
        p = BRep_Tool.Pnt_s(TopoDS.Vertex(vx.Current()))
        out.append((p.X(), p.Y(), p.Z()))
        vx.Next()
    return out


def _outlines(face):
    """(wire, length, middle, vector area) of each of the face's wires, from points along
    its edges in order (the vector area turns round when the wire does)."""
    out, wx = [], TopExp_Explorer(face, TopAbs_WIRE)
    while wx.More():
        wire = TopoDS.Wire(wx.Current())
        wx.Next()
        pts, we = [], BRepTools_WireExplorer(wire, face)
        while we.More():
            edge = we.Current()
            we.Next()
            c = BRepAdaptor_Curve(edge)
            t = np.linspace(c.FirstParameter(), c.LastParameter(), 9)
            if edge.Orientation() == TopAbs_REVERSED:
                t = t[::-1]
            pts += [(p.X(), p.Y(), p.Z()) for p in (c.Value(x) for x in t[:-1])]
        if not pts:
            continue
        P = np.array(pts)
        Q = np.roll(P, -1, axis=0)
        step = np.linalg.norm(Q - P, axis=1)
        # (the middle weighted by length: a reader may split an edge in two)
        middle = ((P + Q) / 2 * step[:, None]).sum(axis=0) / max(step.sum(), 1e-12)
        out.append((wire, float(step.sum()), middle, 0.5 * np.cross(P, Q).sum(axis=0)))
    return out


def _changed_outline(face, other):
    """Points on each wire of the face that the other face (its version read back from
    the file, or None) doesn't have alike; all the face's corners if every wire matches."""
    theirs = _outlines(other) if other is not None else []
    out = []
    for wire, length, middle, turn in _outlines(face):
        if not any(abs(l - length) <= FILE_MATCH * (1 + length) and np.linalg.norm(m - middle) <= FILE_MATCH
                   and np.linalg.norm(t - turn) <= FILE_MATCH * (1 + np.linalg.norm(turn))
                   for _, l, m, t in theirs):
            out += _vertex_points(wire)
    return out or _vertex_points(face)


def _mesh_defective(mesh, tol, fuse):
    """Is the mesh, built from bare facets, already not a valid solid? Sets
    mesh.bare_volume and mesh.bare_bodies to the volume and number of bodies the bare
    facets give (None and 0 if they make no solid)."""
    if "defective" not in mesh.__dict__:
        got, ahead = None, mesh.__dict__.pop("bare_ahead", None)
        if ahead is not None:
            try:
                got = ahead.result()
            except Exception:
                got = None
        mesh.defective, mesh.bare_volume, mesh.bare_bodies = got or _bare(mesh, tol)
    return mesh.defective


def _bare(mesh, tol):
    """(defective, volume, bodies) of the mesh built from bare facets."""
    comp, shells, _ = build_faces(mesh, [], TOL)
    sewn, free = sew(comp, tol, shells, mesh.pts)
    try:
        shape, bodies, _, vol = solids_from_shells(sewn, inward_shells(mesh))
        return bool(free) or not BRepCheck_Analyzer(shape, True, True).IsValid(), vol, bodies
    except RuntimeError:
        return True, None, 0


def _bare_job(job):
    """In a worker process: _bare on the shared mesh."""
    key, tol = job
    return _bare(workers.load(key), tol)


def _bare_ahead(mesh, tol):
    """Start building a big mesh from bare facets in a worker process (_mesh_defective
    picks it up), so it is ready by the time the first attempt is checked."""
    pool = workers.get() if len(mesh.farea) >= BARE_AHEAD else None
    if pool is not None and "defective" not in mesh.__dict__:
        try:
            if "shared_as" not in mesh.__dict__:
                mesh.shared_as = workers.share(mesh)
            mesh.bare_ahead = pool.submit(_bare_job, (mesh.shared_as, tol))
        except Exception:
            mesh.__dict__.pop("bare_ahead", None)


def _hurry_reach(hurry):
    """How near trouble a patch goes in hurry round `hurry` (mm; 0: not hurrying). The
    reach grows only to HURRY_WIDEST's for HURRY_ROUNDS rounds: growing four times every
    round, a 159k-triangle board whose trouble kept turning up somewhere new (one cavity
    let out after another) lost 1,765 curved patches in three rounds, every curve of the
    part (65,175 faces; capped, 48,044: the rest was the time limit, see finish)."""
    if hurry <= 0:
        return HURRY_REACH      # (what carving uses before any hurry)
    return HURRY_REACH * 4 ** ((min(hurry, HURRY_WIDEST) if hurry <= HURRY_ROUNDS else hurry - HURRY_ROUNDS + HURRY_WIDEST) - 1)


def culprits(mesh, features, points):
    """Which patches to drop for trouble at these points: the smooth blends right there if
    any (they give back the exact pieces they replaced), else just the patch nearest to
    each trouble spot (the next attempt shows whether that was enough). Out of time for
    that (mesh.hurry rounds past TROUBLE_SECONDS): every patch near any of them."""
    mesh.trouble = points       # (a big freeform patch blamed gives up just its facets near them)
    hurry = mesh.__dict__.get("hurry", 0)
    if hurry:
        return features_near(mesh, features, points, reach=_hurry_reach(hurry))
    near = features_near(mesh, features, points)
    if not near:
        return []
    P = np.asarray(points)
    dist = {}
    for k in near:
        # (per trouble spot; only distances up to 0.5 mm and a tie count below)
        dist[k] = point_facet_distance(mesh, features[k].facets, P, within=0.5 + BLAME_TIE)
    # blends are the likeliest cause and the cheapest loss: blame one nearby first
    blends = [k for k in near if features[k].kind == "blend"]
    out = set()
    for i in range(len(P)):
        pool = [k for k in blends if dist[k][i] <= 0.5] or near
        k = min(pool, key=lambda k: dist[k][i])
        if dist[k][i] <= 0.5:
            # (on an edge between patches every one beside it is as near: the smallest
            # goes first, the cheapest loss; the next attempt shows if that was enough)
            k = min((j for j in pool if dist[j][i] <= dist[k][i] + BLAME_TIE),
                    key=lambda j: (len(features[j].facets), dist[j][i]))
            out.add(k)
    return sorted(out)


def _invalid_blame(mesh, features, shape, sewing):
    """When the volume is off: the patches next to faces that fail the validity check, if
    any (OCC's volume of a solid with an invalid face can be thousands of cubic
    millimetres out; with nothing blamed, the halving search can leave half the part's
    curves out). Failing that, those next to edges far looser than the sewing: an edge
    that strays from the faces beside it (a spline swinging 0.9 mm off a straight run)
    leaves the solid valid, OCC having widened its tolerance to match, but its volume
    tens of cubic millimetres off. And those next to faces that wouldn't tessellate: the
    volume can't be measured again without them (OCC's integration 17 mm^3 off, the
    tessellation of the rest right)."""
    check = BRepCheck_Analyzer(shape, True, True)
    if not check.IsValid():
        blame = culprits(mesh, features, invalid_face_points(shape, check))
        if blame:
            return blame
    return culprits(mesh, features, loose_edge_points(shape, LOOSE_EDGE * sewing)
                    + tessellated_volume.untessellated)


def loose_edge_points(shape, tol):
    """Points along each edge whose tolerance is over tol."""
    out = []
    edges = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, edges)
    for i in range(1, edges.Extent() + 1):
        edge = TopoDS.Edge(edges.FindKey(i))
        if BRep_Tool.Tolerance_s(edge) > tol and not BRep_Tool.Degenerated_s(edge):
            c = BRepAdaptor_Curve(edge)
            for t in np.linspace(c.FirstParameter(), c.LastParameter(), 5):
                p = c.Value(t)
                out.append((p.X(), p.Y(), p.Z()))
    return out


def invalid_face_points(shape, check=None):
    """A point on each face of the shape that fails the validity check (check: the
    shape's BRepCheck_Analyzer, if already run: it has every face's verdict)."""
    out, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = ex.Current()
        bad = not (check.IsValid(face) if check is not None else BRepCheck_Analyzer(face).IsValid())
        if bad:
            props = GProp_GProps()
            BRepGProp.SurfaceProperties_s(face, props)
            c = props.CentreOfMass()
            out.append((c.X(), c.Y(), c.Z()))
            vx = TopExp_Explorer(face, TopAbs_VERTEX)
            while vx.More():
                p = BRep_Tool.Pnt_s(TopoDS.Vertex(vx.Current()))
                out.append((p.X(), p.Y(), p.Z()))
                vx.Next()
        ex.Next()
    return out + misjoined_edge_points(shape)


def misjoined_edge_points(shape):
    """The middle of each edge its shell uses wrongly: run the same way by both faces
    beside it (one of them is flipped), shared by more than two faces, or by one only
    (an open edge sewing let through: a part with six slots inside it failed the check
    on that alone, with nothing else to blame). Every face can check out on its own
    while its shell doesn't; this says where."""
    out, ex = [], TopExp_Explorer(shape, TopAbs_SHELL)
    while ex.More():
        edges = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
        TopExp.MapShapes_s(ex.Current(), TopAbs_EDGE, edges)
        uses = np.zeros((edges.Extent() + 1, 2), int)       # forward, reversed
        fx = TopExp_Explorer(ex.Current(), TopAbs_FACE)
        while fx.More():
            ee = TopExp_Explorer(fx.Current(), TopAbs_EDGE)
            while ee.More():
                o = ee.Current().Orientation()
                if o in (TopAbs_FORWARD, TopAbs_REVERSED):
                    uses[edges.FindIndex(ee.Current()), int(o == TopAbs_REVERSED)] += 1
                ee.Next()
            fx.Next()
        n = uses.sum(axis=1)
        for i in np.nonzero((n > 2) | (n == 1) | ((n == 2) & (uses[:, 0] != 1)))[0]:
            edge = TopoDS.Edge(edges.FindKey(int(i)))
            if not BRep_Tool.Degenerated_s(edge):
                c = BRepAdaptor_Curve(edge)
                p = c.Value((c.FirstParameter() + c.LastParameter()) / 2)
                out.append((p.X(), p.Y(), p.Z()))
        ex.Next()
    return out


def _swap_build(mesh, features, mesh_tol, tol, info, faceted_volume):
    """STL2CURVES_TRY=swap (an experiment): the body assembled patch by patch on the bare
    facets' shared edges (assemble.py) rather than built, sewn and checked whole in rounds.
    The solid gets attempt's checks (volume, validity, its STEP file); the patches blamed
    for trouble are taken out again on the spot, for up to SWAP_ROUNDS rounds.
    (shape, bodies, cavities), or None if it still doesn't check out (then the sewing
    build runs as usual)."""
    from .assemble import assemble, revert
    try:
        solid, kept, report = assemble(mesh, features, mesh_tol, tol)
    except Exception:
        return None
    reg = report.pop("registry")
    report["rounds"] = []
    for _ in range(SWAP_ROUNDS):
        try:
            shape, nb, nv, signed = solids_from_shells(solid, inward_shells(mesh))
        except Exception:
            return None
        expected = faceted_volume + sum(f.change for f in kept)
        allowed = sum(f.tolerance for f in kept) + 1e-6 * abs(faceted_volume) + 1e-3
        blame, why = [], None
        if abs(signed - expected) > allowed:
            tess = tessellated_volume(shape)
            if tess is None or abs(tess - expected) > allowed:
                why = "volume"
                blame = _invalid_blame(mesh, kept, shape, tol)
        if why is None:
            check = BRepCheck_Analyzer(shape, True, True)
            if not check.IsValid():
                mended = None
                for precision in (None,) + FIX_PRECISIONS:
                    fix = ShapeFix_Shape(shape)
                    if precision:
                        fix.SetPrecision(precision)
                    fix.Perform()
                    if BRepCheck_Analyzer(fix.Shape(), True, True).IsValid() and abs(volume(fix.Shape()) - expected) <= allowed:
                        mended = fix.Shape()
                        break
                if mended is None:
                    why = "invalid"
                    blame = culprits(mesh, kept, invalid_face_points(shape, check))
                else:
                    shape = mended
        if why is None and kept and len(mesh.farea) <= FILE_CHECK_FACES:
            trouble = file_trouble(shape, allowed)
            if trouble:
                why = "file"
                blame = culprits(mesh, kept, trouble)
        report["rounds"].append((why, len(blame)))
        if why is None:
            used = {id(f) for f in kept} | {id(report.get("ring_of", {}).get(id(f))) for f in kept}
            info["skipped"] += [f for f in features if id(f) not in used]
            info["assembly"] = report
            return shape, nb, nv
        if not blame:
            return None
        solid, kept = revert(reg, kept, blame)
    return None


def sewing_cap(mesh, tol):
    """The widest the part may be sewn (mm): SEW_MOST, and no more than SEW_EDGE times
    the mesh's middling edge. Sewing merges the ends of edges shorter than its tolerance
    and pairs edges up that far apart: on a fine mesh (a 189k-triangle frame, its
    median edge 0.075 mm, sewn at 0.2 mm: two edges in three shorter than that) it tore
    flat areas far from any curve, until a try failed with nothing to blame and every
    curve of the part was dropped. One 4-facet freeform patch 0.29 mm off at its edges'
    middles had set that 0.2: without it the same try sewed at 0.14 with no such gap;
    at 0.1 or 0.075, more patches left gaps beside them, to be dropped."""
    if "sew_cap" not in mesh.__dict__:
        T = mesh.tris
        e = np.linalg.norm(mesh.pts[T[:, [1, 2, 0]]] - mesh.pts[T], axis=2)
        mesh.sew_cap = max(tol, min(SEW_MOST, SEW_EDGE * float(np.median(e))))
    return mesh.sew_cap


def _within_sewing(mesh, features, tol, info):
    """On a mesh fine enough for sewing_cap to bite, the features less those whose
    sewing would need more (an attempt sews at SEW_PER_WORST times its loosest patch's
    worst): left faceted, the pieces they replaced tried in their place. Left to fail,
    each would cost a try of the whole part (half a minute or more on such a mesh)."""
    cap = sewing_cap(mesh, tol)
    if cap >= SEW_MOST:
        return features     # (as ever: a patch a little looser than that may still sew)
    kept, back = [], []

    def take(f, into):
        if SEW_PER_WORST * f.worst <= cap:
            into.append(f)
        else:
            info["skipped"].append(f)
            for p in f.parts:
                take(p, back)
    for f in features:
        take(f, kept)
    return kept + back


def _loosest(features, tol):
    """The patches setting the sewing tolerance (those within a hair of the loosest), if
    it is any wider than the bare facets'."""
    worst = max((f.worst for f in features), default=0.0)
    if SEW_PER_WORST * worst <= tol:
        return []
    return [f for f in features if f.worst >= 0.999 * worst]


def _build(mesh, features, mesh_tol, tol, fuse, info):
    """Build and check one group of bodies, leaving faceted any feature that spoils it.
    Returns (shape, bodies, cavities)."""
    mesh.tries = 0
    progress.step("building the flat faces")
    faceted_volume = mesh_volume(mesh)
    _bare_ahead(mesh, tol)
    features = settle_blends(mesh, features, mesh_tol, split_blend, info["skipped"])
    features = _within_sewing(mesh, features, tol, info)
    if "swap" in TRYING:
        got = _swap_build(mesh, features, mesh_tol, tol, info, faceted_volume)
        if got is not None:
            return got
    # Each attempt builds and sews the whole body: on a mesh of a quarter million facets
    # that is minutes, and dropping troublemakers a few at a time took over an hour. So
    # after TROUBLE_SECONDS every patch near trouble goes at once (culprits), reaching
    # further each round: a few rounds at most, losing curves only round the trouble.
    deadline = time.time() + TROUBLE_SECONDS if features_mod._budget else math.inf
    mesh.hurry = 0

    def good(subset):
        result, failed = attempt(mesh, subset, mesh_tol, tol, fuse, faceted_volume)
        blamed = []
        while failed:  # patches whose face couldn't be built: drop them and retry
            # a smooth blend whose face won't fit is cut in two and tried again; failing
            # that (or if it was only blamed), it gives back the pieces it replaced
            back = []
            by_file = attempt.why == "file"
            late = time.time() > deadline
            for k in failed:
                f = subset[k]
                if late:
                    # (out of time: no more halves, refits or fallbacks to try one by one)
                    info["skipped"].append(f)
                    back += list(f.parts)
                    continue
                rings = half_rings(mesh, f)
                if rings:
                    # a whole ring cut to shape often fails where its outline crosses
                    # the surface's seam; the same surface as two half rings doesn't
                    back += rings
                    continue
                if f.model.kind == "freeform":
                    # a big freeform patch blamed for trouble at its edge gives up just its
                    # facets there; one that won't build (or would lose half of itself)
                    # the smooth blends its area would have had otherwise
                    carved = attempt.blamed and carve(mesh, f, mesh.__dict__.get("trouble"),
                                                      _hurry_reach(mesh.hurry))
                    if carved:
                        back += carved
                        continue
                    halves = blend_fallback(mesh, f)
                elif f.model.kind == "thread" and attempt.blamed and (carved := thread_carve(
                        mesh, f, mesh.__dict__.get("trouble"), _hurry_reach(mesh.hurry))):
                    back += carved      # (a thread piece likewise: only its facets near the trouble)
                    # (and whole again in the second chance below, if the part builds
                    # with it then: dropped pieces often do, carved ones never got to)
                    f.parts = tuple(carved)
                    blamed.append(f)
                    continue
                elif f.model.kind in ("pipe", "extrusion"):
                    halves = pipe_fallback(mesh, f)     # (its pieces as they were, blends between)
                elif f.kind != "blend" and len(f.parts) == 1 and f.parts[0].model.kind == "pipe":
                    halves = list(f.parts)      # (a pipe found to be a cylinder or torus: the pipe again)
                elif attempt.blamed and (carved := carve_exact(
                        mesh, f, mesh.__dict__.get("trouble"), _hurry_reach(mesh.hurry))):
                    back += carved      # (a big exact patch likewise: only its facets near the trouble,
                    f.parts = tuple(carved)     # and whole again in the second chance if the part takes it)
                    blamed.append(f)
                    continue
                elif attempt.blamed:
                    halves = None
                elif f.kind == "blend":
                    halves = split_blend(mesh, f)
                else:
                    # an exact surface whose face couldn't be built: try a smooth blend
                    # over its facets rather than leaving them flat
                    halves = [as_blend(mesh, f.facets, ())] if len(f.facets) >= 3 else None
                if halves:
                    back += halves
                    if f.kind != "blend":
                        info["skipped"].append(f)
                else:
                    info["skipped"].append(f)
                    back += list(f.parts)
                    if by_file:
                        mesh.__dict__.setdefault("file_dropped", []).append(f)
                    if attempt.blamed and not (by_file and not FILE_SECOND_CHANCE):
                        # (a patch its STEP file broke goes without: what the file does
                        # is the face's own, and back in it broke the file again, at the
                        # end of a whole try each time; it comes back below if the file
                        # stays broken without it)
                        blamed.append(f)
            gone = set(failed)      # (a set: thousands blamed at once in a hurry round)
            subset = [f for k, f in enumerate(subset) if k not in gone] + back
            if attempt.blamed and time.time() > deadline:
                # (a whole build and sew is at stake each time: blame widely from now on)
                mesh.hurry += 1
            result, failed = attempt(mesh, subset, mesh_tol, tol, fuse, faceted_volume)

        # Patches dropped for trouble nearby may have been innocent: once the part
        # builds, give them a second chance, all together first, halving on failure.
        def again(group):
            nonlocal result, subset
            if not group or time_left() < 0:
                return
            # (by identity, through a set of ids: a hurry round's group of hundreds,
            # each checked against every patch of the part, grew with their product)
            pieces = {id(p) for b in group for p in b.parts}
            trial = [f for f in subset if id(f) not in pieces] + group
            # (the STEP file checked whatever rounds are left: a patch dropped for it must
            # not come back unchecked)
            mesh.file_trial = True
            try:
                got, drop = attempt(mesh, trial, mesh_tol, tol, fuse, faceted_volume)
            finally:
                mesh.file_trial = False
            if got is not None and not drop:
                result, subset = got, trial
                back_in = {id(b) for b in group}
                info["skipped"] = [f for f in info["skipped"] if id(f) not in back_in]
            elif len(group) > 1:
                # (the try names its culprits: the rest of the group goes straight back
                # in, then each one named alone, as it may only clash with another.
                # Halving blindly took more tries, each building and sewing the whole
                # part: GPS case back 165 -> 131 s, Rak N-USB 173 -> 155 s, same faces;
                # leaving the named ones out for good lost 2% of the curves)
                named = {id(trial[k]) for k in drop if 0 <= k < len(trial)}
                rest = [g for g in group if id(g) not in named]
                if BLAME_GUIDED and 0 < len(rest) < len(group):
                    again(rest)
                    for g in group:
                        if id(g) in named:
                            again([g])
                else:
                    again(group[:len(group) // 2])
                    again(group[len(group) // 2:])

        if result is not None:
            again(blamed)
        return result, subset

    result, used = good(features)
    if result is None and used and not mesh.hurry:
        # Find the troublemakers by halving: keep every half that builds cleanly.
        accepted = []
        start = time.time()

        def search(group):
            nonlocal result
            if not group:
                return
            if time.time() - start > SEARCH_SECONDS or time_left() < 0:
                info["skipped"] += group      # out of time: leave the rest faceted
                return
            trial, kept = good(accepted + group)
            if trial is not None:
                accepted[:] = kept
                result = trial
                return
            if len(group) == 1:
                info["skipped"].append(group[0])
                return
            half = len(group) // 2
            search(group[:half])
            search(group[half:])

        search(used)
        used = accepted
    elif result is None:
        # Out of time, and the last try blamed nothing. If it left gaps near no patch
        # (built afresh too), the loosest patches may be what widened the sewing that
        # tore them: tried without those first, as all that is left to lose is every
        # curve of the part (a 189k-triangle frame did, for one gap 8 mm from any patch,
        # its sewing widened to 0.2 mm by a single 4-facet patch; without it, 0.14 and
        # no such gap)
        for _ in range(LOOSE_ROUNDS):
            loose = _loosest(used, tol) if attempt.why == "free" else []
            if not loose:
                break
            info["skipped"] += loose
            result, used = good([f for f in used if all(f is not g for g in loose)])
            if result is not None:
                break
        if result is None:
            info["skipped"] += used     # (the patches left stay faceted)
    if result is None:
        result, used = good([])
    if result is None:
        raise RuntimeError("could not build a closed solid from this mesh")
    dropped = [f for f in mesh.__dict__.pop("file_dropped", []) if all(f is not u for u in used)]
    if dropped:
        allowed = sum(f.tolerance for f in used) + 1e-6 * abs(faceted_volume) + 1e-3
        if file_trouble(result[0], allowed) is not None:
            # dropping the patches blamed for the STEP file didn't mend it: better have
            # them back (the final check warns about the file either way)
            mesh.file_off = True
            skipped = list(info["skipped"])
            info["skipped"] = [f for f in skipped if all(f is not b for b in dropped)]
            got, kept = good([f for f in used if all(f is not p for b in dropped for p in b.parts)] + dropped)
            if got is not None:
                result, used = got, kept
            else:
                info["skipped"] = skipped
    info["restored"] += used
    ahead = mesh.__dict__.pop("bare_ahead", None)
    if ahead is not None and not ahead.cancel() and not ahead.done():
        workers.abandoned()     # (the bare facets' build, never needed, still running: see attempt)
    shape, nb, nv = result
    if fuse and nb > 1:
        shape = fuse_overlapping(shape)
        nb = count(shape, TopAbs_SOLID)
    return shape, nb, nv


def stl_to_solid(*args, time_limit=TIME_LIMIT, **kwargs):
    """Convert one STL: (shape, info). See _stl_to_solid. time_limit (seconds, None for
    none, counted from the end of the analysis, see finish): fitting blends and hunting
    down troublemakers stop at their share of it, and whatever has checked out by then is
    handed over."""
    features_mod._budget = time_limit
    features_mod._deadline = time.time() + time_limit if time_limit else None

    try:
        return _stl_to_solid(*args, **kwargs)
    finally:
        features_mod._deadline = features_mod._budget = None
        workers.finish()


def _stl_to_solid(path, tol, fuse=True, curves=True, true_size=False, blends=True, mend=True,
                  simplify_to=None):
    """simplify_to: how far (mm) thinning out an over-dense mesh may move its surface;
    None: only above AUTO_SIMPLIFY triangles, at SIMPLIFY_ERROR; 0: never."""
    # (a mesh file, or (points, triangles) or (points, triangles, part of each triangle):
    # a 3MF object's parts can overlap, and are each mended and built on their own, then
    # joined by a boolean union)
    return finish(*prepare(path, tol, fuse, curves, true_size, blends, mend, simplify_to), tol, fuse)


def prepare(path, tol, fuse=True, curves=True, true_size=False, blends=True, mend=True,
                  simplify_to=None):
    """The first stages of a conversion: (parts, info, part), parts being (mesh,
    features, mesh tolerance) for each body, ready for finish(). simplify_to: how far
    (mm) thinning out an over-dense mesh may move its surface; None: only above
    AUTO_SIMPLIFY triangles, at SIMPLIFY_ERROR; 0: never."""
    # (a mesh file, or (points, triangles) or (points, triangles, part of each triangle):
    # a 3MF object's parts can overlap, and are each mended and built on their own, then
    # joined by a boolean union)
    part = None
    # (with STL2CURVES_CACHE set, the first stages are saved, and taken up again on the
    # next run of the same file if their code hasn't changed: see stages.py)
    # (blends isn't part of the key: the mesh and analysis stages don't depend on it, and the
    # blends stage is saved only when they ran, so a run without them leaves stages for one with)
    cache = stages.Cache(path, (tol, fuse, curves, true_size, mend, simplify_to, accuracy.mm()) + ((tuple(sorted(accuracy.of().items())),) if accuracy.of() else ()))
    saved = cache.load("mesh")
    if saved is not None:
        pts, tris, part, groups, kept = saved
        if kept["triangles"] >= WORKERS_FROM:
            workers.start(kept["triangles"])
        info = {"restored": [], "skipped": [], "size": None, "snapped": 0, "cached": ["mesh"], **kept}
    else:
        progress.step("reading the mesh")
        if isinstance(path, (str, os.PathLike)):
            pts, tris = load_stl(path)
        else:
            pts, tris = path[0], path[1]
            if len(path) > 2 and len(np.unique(path[2])) > 1:
                part = np.asarray(path[2])
        if len(tris) >= WORKERS_FROM:
            workers.start(len(tris))    # (worker processes, started while the mesh is repaired)
        info = {"triangles": len(tris), "restored": [], "skipped": [], "size": None, "snapped": 0,
                "repairs": [], "simplified": None, "mesh_left": []}
        if mend:
            progress.step("mending the mesh")
            if part is None:
                pts, tris, info["repairs"] = repair(pts, tris)
                info["mesh_left"] = leftovers()
            else:
                pts, tris, part, info["repairs"], info["mesh_left"] = _repair_parts(pts, tris, part)
        if simplify_to is None:
            simplify_to = SIMPLIFY_ERROR if len(tris) > AUTO_SIMPLIFY else 0
        if simplify_to:
            progress.step("thinning out the mesh")
            before = len(tris)
            if part is None:
                tris = simplify(pts, tris, simplify_to)
            else:
                labels = np.unique(part)
                pieces = [simplify(pts, tris[part == p], simplify_to) for p in labels]
                part = np.concatenate([np.full(len(t), p) for p, t in zip(labels, pieces)])
                tris = np.vstack(pieces)
            info["simplified"] = (before, len(tris), simplify_to)
        # bodies touching at an edge are built apart (and so are a 3MF object's parts)
        progress.step("separating the bodies")
        groups = (split_bodies(pts, tris) if part is None
                  else [g for p in np.unique(part) for g in split_bodies(pts, tris[part == p])])
        cache.save("mesh", (pts, tris, part, groups,
                            {k: info[k] for k in ("triangles", "repairs", "simplified", "mesh_left")}))
    if curves:
        saved = cache.load("blends" if blends else "analysis")
        if saved is None and blends:
            saved = cache.load("analysis")
            done = "analysis"
        else:
            done = "blends" if blends else "analysis"
        if saved is not None:
            parts, info["size"], info["snapped"] = saved
            info.setdefault("cached", []).append(done)
            # (the tolerance analyze() leaves set for the stages after it: the last body's)
            if parts:
                features_mod._mesh_tol = parts[-1][2]
        else:
            parts = [_analyze_body(pts, g, i, len(groups)) for i, g in enumerate(groups)]
            progress.step("guessing the design size")
            info["size"] = guess = guess_size([p[:2] for p in parts])
            round_unit = None
            if true_size and guess is not None:
                round_unit = guess.unit
                if guess.factor != 1.0:
                    parts = [_analyze_body(pts * guess.factor, g, i, len(groups)) for i, g in enumerate(groups)]
            for mesh, features, _ in parts:
                info["snapped"] += snap(mesh, features, round_unit)
            cache.save("analysis", (parts, info["size"], info["snapped"]), [p[0] for p in parts])
            done = "analysis"
        if blends and done == "analysis":
            progress.step("fitting smooth blends and freeform surfaces")
            parts = [(mesh, add_blends(mesh, features), t) for mesh, features, t in parts]
            cache.save("blends", (parts, info["size"], info["snapped"]), [p[0] for p in parts])
    else:
        parts = [(Mesh(pts, g), [], TOL) for g in groups]
    # (each body's triangles as split, zero-area ones included, which Mesh drops: the
    # merged engine hands the whole body to MMM, see merged.py)
    for (mesh, _, _), g in zip(parts, groups):
        mesh.body_tris = g
    return parts, info, part


def _analyze_body(pts, tris, i, n):
    progress.within(f"body {i + 1} of {n}" if n > 1 else "")
    try:
        return analyze(pts, tris)
    finally:
        progress.within("")


def finish(parts, info, part, tol, fuse=True):
    """The rest of a conversion, from prepare()'s parts (whose features may have been
    edited in between): each body built and checked, joined, tidied: (shape, info)."""
    # The time limit counts from here: analysis always runs to the end, and counted
    # against the limit it left a 159k-triangle board (1,618 s of analysis) no time to fit
    # blends or search for culprits, every curve of the part then lost (65,175 faces;
    # with the build's own time, 3,663)
    if features_mod._budget:
        features_mod._deadline = time.time() + features_mod._budget
    shapes, nb, nv = [], 0, 0
    info["used"] = []           # (each body's features as built, for stl2curves.studio)
    for i, (mesh, features, mesh_tol) in enumerate(parts):
        progress.within(f"body {i + 1} of {len(parts)}" if len(parts) > 1 else "")
        before = len(info["restored"])
        shape, b, v = _build(mesh, features, mesh_tol, tol, fuse, info)
        info["used"].append(info["restored"][before:])
        shapes.append(shape)
        nb, nv = nb + b, nv + v
        info["mesh_defects"] = info.get("mesh_defects", False) or mesh.__dict__.get("defective", False)
    progress.within("")
    shape = shapes[0]
    if len(shapes) > 1:
        progress.step("joining the bodies")
        # bodies that met at an edge were built apart; join them now, unless the join
        # comes out invalid or loses material (the boolean can return nothing at all
        # where bodies only touch): then they are handed over side by side
        shape = compound(shapes)
        if fuse:
            apart = sum(volume(s) for s in shapes)
            slack = UNIFY_VOLUME * abs(apart) + 1e-3
            # (a 3MF object's parts may overlap: then the union is less than their sum,
            # but no less than the biggest)
            low = apart if part is None else max(volume(s) for s in shapes)
            fused = fuse_checked(shapes[:1], shapes[1:], low - slack, apart + slack)
            if fused is not None:
                shape = fused
        nb = count(shape, TopAbs_SOLID)

    # Tidy up, but only keep the result if it is still a valid solid of the same volume;
    # otherwise hand over the checked shape as it was built. First faces on one surface
    # are merged into one (a flat face whose outline defeated the face builder, a step or
    # ridge a thousandth of a millimetre high in an export, was built triangle by
    # triangle), failing that just edges split along one line (see tidied: given up on
    # a part so big that it would take more than TIDY_SECONDS).
    progress.step("tidying up the faces")
    checked = shape
    target = volume(checked)
    shape, untidy = tidied(checked, target)
    if shape is None:
        shape = checked
    if not BRepCheck_Analyzer(shape, True, True).IsValid():
        if BRepCheck_Analyzer(checked, True, True).IsValid():
            shape = checked
        else:
            sf = ShapeFix_Shape(checked)
            sf.Perform()
            if BRepCheck_Analyzer(sf.Shape(), True, True).IsValid():
                shape = sf.Shape()
            elif untidy is not None:
                shape = untidy      # (no worse than the shape as checked, and tidier)
    # (and the STEP file must read back as built: the tidied shape failing that, the
    # shape as checked)
    slack = FILE_VOLUME * abs(target) + 1e-3
    if count(shape, TopAbs_FACE) <= FILE_CHECK_FACES:
        progress.step("checking the STEP file reads back")
        trouble = file_trouble(shape, slack)
        if trouble is not None and shape is not checked and file_trouble(checked, slack) is None:
            shape, trouble = checked, None
        info["file_ok"] = trouble is None
    else:
        info["file_ok"] = None      # (not checked: too big to write and read back in time)

    info["bodies"], info["voids"] = nb, nv
    info["faces"] = count(shape, TopAbs_FACE)
    if not info["faces"]:
        raise RuntimeError("the solid came out empty")
    info["volume"] = volume(shape)
    info["valid"] = BRepCheck_Analyzer(shape, True, True).IsValid()
    return shape, info


def tidied(shape, target):
    """(tidy, untidy): the shape tidied up (_tidy); on a part of TIDY_WORKER_FACES faces
    or more in a worker process, given up after TIDY_SECONDS (merging faces across a
    part of a hundred thousand faces can take the best part of an hour). (None, None)
    if nothing came. (Only then: read back from a BRep file in a worker, a shape no
    longer merged at all, Rak N kept 1780 faces instead of 1279.)"""
    pool = workers.get() if count(shape, TopAbs_FACE) >= TIDY_WORKER_FACES else None
    if pool is None:
        return _tidy(shape, target)
    from concurrent.futures import TimeoutError as Timeout
    tmp = tempfile.mkdtemp(prefix="stl2curves_")
    try:
        paths = [os.path.join(tmp, n) for n in ("shape.brep", "tidy.brep", "untidy.brep")]
        BRepTools.Write_s(shape, paths[0])
        job = pool.submit(_tidy_job, (paths, target))
        try:
            done = job.result(timeout=TIDY_SECONDS)
        except Timeout:
            workers.abandoned()     # (the worker is left to finish; then restarted)
            return None, None
        except Exception:
            workers.broken()
            return None, None
        out = []
        for path, there in zip(paths[1:], done):
            got = None
            if there:
                got = TopoDS_Shape()
                BRepTools.Read_s(got, path, BRep_Builder())
            out.append(got)
        return tuple(out)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _tidy_job(job):
    """In a worker process: _tidy on a shape read from a BRep file, the results written
    to two more. Which of them there are."""
    paths, target = job
    shape = TopoDS_Shape()
    BRepTools.Read_s(shape, paths[0], BRep_Builder())
    out = _tidy(shape, target)
    return tuple(x is not None and BRepTools.Write_s(x, path) for x, path in zip(out, paths[1:]))


def _tidy(checked, target):
    """(tidy, untidy): faces on one surface merged into one, failing that just edges
    split along one line, kept if still a valid solid of the same volume (tidy), or of
    the same volume but invalid (untidy, used only if nothing better turns up). A merged
    face that comes out invalid (a floor of 450 triangles beside a freeform surface)
    has its pieces kept apart in the next try, so the other merges still count."""
    untidy = None
    for faces in (True, False):
        apart = []          # edges the faces either side of which must stay apart
        for _ in range(TIDY_TRIES if faces else 1):
            try:
                unify = ShapeUpgrade_UnifySameDomain(checked, True, faces, False)
                unify.SetLinearTolerance(UNIFY_TOL)
                unify.SetAngularTolerance(math.radians(UNIFY_DEG))
                for edge in apart:
                    unify.KeepShape(edge)
                unify.Build()
                sf = ShapeFix_Shape(unify.Shape())
                sf.Perform()
                tidy = sf.Shape()
            except Exception:
                break
            if abs(volume(tidy) - target) <= UNIFY_VOLUME * abs(target) + 1e-3:
                if BRepCheck_Analyzer(tidy, True, True).IsValid():
                    return tidy, untidy
                untidy = untidy or tidy
            more = _bad_merges(checked, unify)
            if not faces or not more:
                break
            apart += more
    return None, untidy


def _bad_merges(checked, unify):
    """The edges inside each merged face that came out invalid (between the faces it
    was merged from)."""
    merged = unify.Shape()
    check = BRepCheck_Analyzer(merged, True, True)
    history = unify.History()
    index = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapes_s(merged, TopAbs_FACE, index)
    sources = {}
    ex = TopExp_Explorer(checked, TopAbs_FACE)
    while ex.More():
        face = ex.Current()
        ex.Next()
        for image in history.Modified(face):
            k = index.FindIndex(image)
            if k:
                sources.setdefault(k, []).append(face)
    out = []
    for k, group in sources.items():
        if len(group) < 2 or check.IsValid(index.FindKey(k)):
            continue
        edges = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
        seen = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
        for face in group:
            ee = TopExp_Explorer(face, TopAbs_EDGE)
            while ee.More():
                e = ee.Current()
                ee.Next()
                if seen.Contains(e):
                    edges.Add(e)
                else:
                    seen.Add(e)
        out += [edges.FindKey(i) for i in range(1, edges.Extent() + 1)]
    return out


def _repair_parts(pts, tris, part):
    """repair() on each part of a 3MF object on its own (two parts pressed together are
    no wall of zero thickness to cut out, nor overlapping ones a mesh crossing itself):
    (points, triangles, part of each triangle, what was done, what is still wrong)."""
    P, T, L, done, left = [], [], [], {}, {}
    base = 0
    for p in np.unique(part):
        used, inv = np.unique(tris[part == p], return_inverse=True)
        q, t, notes = repair(pts[used], inv.reshape(-1, 3))
        P.append(q)
        T.append(t + base)
        L.append(np.full(len(t), p))
        base += len(q)
        for said, note in [(done, n) for n in notes] + [(left, n) for n in leftovers()]:
            # (the same repair in two parts is reported once, with the counts added up)
            head, _, rest = note.partition(" ")
            if head.isdigit():
                said[rest] = said.get(rest, 0) + int(head)
            else:
                said[note] = None
    notes, left = ([note if n is None else f"{n} {note}" for note, n in said.items()] for said in (done, left))
    return np.vstack(P), np.vstack(T), np.concatenate(L), notes, left


def write_step(shape, path):
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    # OpenCascade prints a wall of transfer statistics to stdout; silence it.
    sys.stdout.flush()
    saved, devnull = os.dup(1), os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        w = STEPControl_Writer()
        w.Transfer(shape, STEPControl_AsIs)
        status = w.Write(str(path))
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)
    if status != IFSelect_RetDone:
        raise RuntimeError(f"failed writing {path}")


def describe_features(info, details=False):
    text = ""
    if info["restored"]:
        text = f"  rebuilt as true curves: {summarize(info['restored'])}"
    if info["skipped"]:
        text += f"\n  left faceted (failed checks): {summarize(info['skipped'])}"
    if details:
        for f in info["restored"]:
            text += f"\n    {f.describe()}"
        for f in info["skipped"]:
            text += f"\n    SKIPPED {f.describe()}"
    return text.lstrip("\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--version", action="version", version=f"stl2curves {__version__}")
    ap.add_argument("--out", help="output folder (default: next to each STL)")
    ap.add_argument("--merge", help="also write all parts into one STEP with this name")
    ap.add_argument("--tol", type=float, default=0.01)
    ap.add_argument("--no-fuse", action="store_true",
                    help="keep separate/overlapping bodies in a file separate instead of unioning them")
    ap.add_argument("--no-curves", action="store_true",
                    help="don't rebuild curved areas as true curves")
    ap.add_argument("--details", action="store_true", help="list every rebuilt feature")
    ap.add_argument("--no-blends", action="store_true",
                    help="don't turn curved areas no simple surface fits into smooth freeform faces")
    ap.add_argument("--no-repair", action="store_true",
                    help="don't mend mesh defects (duplicates, slivers, cracks, holes, flipped or crossing triangles)")
    ap.add_argument("--simplify", type=float, metavar="MM",
                    help=f"thin out the mesh first, moving its surface by at most MM (0: never; default: "
                         f"{SIMPLIFY_ERROR} mm for meshes over {AUTO_SIMPLIFY:,} triangles)")
    ap.add_argument("--time-limit", type=float, metavar="SECONDS", default=TIME_LIMIT,
                    help=f"stop fitting blends and hunting down troublemakers after building for about "
                         f"this long and keep what checks out (0: no limit; default {TIME_LIMIT})")
    ap.add_argument("--true-size", action="store_true",
                    help="rebuild at the apparent design size, with radii snapped to round values")
    ap.add_argument("--accuracy", type=float, metavar="MM", default=accuracy.mm(),
                    help=f"how far smooth surfaces, rough-mesh fillets and other fitted surfaces may stray "
                         f"from the mesh (default {accuracy.DEFAULT}): more rebuilds more of the part as "
                         f"curves, less keeps them closer to the mesh")
    args = ap.parse_args()
    accuracy.set_accuracy(args.accuracy)

    files = []
    for p in map(Path, args.inputs):
        files += sorted(f for f in p.iterdir() if f.suffix.lower() in (".stl", ".3mf")) if p.is_dir() else [p]
    if not files:
        sys.exit("No STL or 3MF files found.")
    # (a 3MF file holds any number of objects: each is converted to a STEP of its own)
    jobs = []
    for f in files:
        if f.suffix.lower() == ".3mf":
            objects = read_3mf(f)
            if not objects:
                print(f"{f.name}: no objects to convert", flush=True)
            jobs += [(f"{f.name}: {name}", (pts, tris, part), f, f"{f.stem} - {name}")
                     for name, pts, tris, part in objects]
        else:
            jobs.append((f.name, f, f, f.stem))

    shapes = []
    for label, source, f, stem in jobs:
        t = time.time()
        print(f"{label}: converting...", flush=True)
        try:
            shape, info = stl_to_solid(source, args.tol, not args.no_fuse, not args.no_curves, args.true_size,
                                       not args.no_blends, not args.no_repair, args.simplify,
                                       time_limit=args.time_limit or None)
        except Exception as e:
            # (one part that can't be converted shouldn't stop the rest of the batch)
            print(f"  FAILED: {e} ({time.time() - t:.1f}s); no STEP written", flush=True)
            continue
        out_dir = Path(args.out) if args.out else f.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / (stem + ".step")
        write_step(shape, out)
        shapes.append(shape)
        nb, nv = info["bodies"], info["voids"]
        extra = f" ({nb} bodies{', ' + str(nv) + ' cavities' if nv else ''})" if nb + nv > 1 else ""
        print(f"  {info['triangles']} triangles -> {info['faces']} faces{extra}, "
              f"volume {info['volume']:,.1f} mm^3, "
              f"{'valid solid' if info['valid'] else 'WARNING: check geometry' + (' (the STL itself is not a clean solid: it touches or crosses itself)' if info.get('mesh_defects') else '')}, "
              f"{'WARNING: the STEP file does not read back exactly as built, ' if info.get('file_ok') is False else ''}"
              f"{time.time() - t:.1f}s -> {out}")
        if info["repairs"]:
            print("  mended the mesh: " + "; ".join(info["repairs"]))
        if info.get("mesh_left"):
            print("  not mended (the solid may be rough or invalid there): " + "; ".join(info["mesh_left"]))
        if info.get("cached"):
            print("  saved stages taken up: " + ", ".join(info["cached"]))
        if info["simplified"]:
            before, after, err = info["simplified"]
            print(f"  thinned out: {before:,} -> {after:,} triangles (surface moved {err} mm at most)")
        if info["restored"] or info["skipped"]:
            print(describe_features(info, args.details))
        if info["size"] is not None:
            note = info["size"].describe()
            if args.true_size and info["size"].factor != 1.0:
                note = note.split(" Use --true-size")[0] + " Rebuilt at that size."
            print("  " + note)

    if args.merge:
        comp = compound(shapes)
        out_dir = Path(args.out) if args.out else files[0].parent
        out = out_dir / (Path(args.merge).stem + ".step")
        write_step(comp, out)
        print(f"Combined file -> {out}")


if __name__ == "__main__":
    main()
