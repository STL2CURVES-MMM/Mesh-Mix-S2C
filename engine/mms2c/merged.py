"""
The merged engine: stl2curves and Manifold Mesh Mix (MMM, mms2c/mmm, AGPL-3.0) as one
pipeline, meeting on one labelling of each shell's triangles.

stl2curves' front end reads and mends the mesh (repair, thinning, bodies split where they
touch) and finds what MMM can't: screw threads, rolling-ball fillets, radii snapped to
the design size, freeform patches, pipes along curved edges. Each feature becomes a
region of MMM's Segmenter whose surface is locked: MMM may move its border but never
refits it. MMM's own seeds then cover only the triangles we left, and its border
clean-up (teeth, min-cut straightening, slivers) runs over all of them, ours included.
From there MMM's pipeline carries on: the B-rep is assembled without sewing (each border
one fitted edge shared by both faces), faces that won't build or stray from the mesh fall
back face by face (freeform, then flat), every result is judged against the mesh, organic
bodies get smooth surfaces, and the STEP file is written, read back and repaired.

Surfaces cross over as MMM's own types where it has them (planes, cylinders, cones,
spheres, tori: our snapped values) and otherwise wrapped (OurSurface: a thread piece,
a freeform patch, a pipe, an extrusion, with the OCC surface our builder would cut).
Our N-sided blends are left to MMM, which fits its own freeform cells there.

Every file passes one entrance first (convert_file -> _entrance): the paths are checked
(pipeline.check_paths), the mesh is read by MMM's reader and given its refusals
(meshio.check_triangles), so a broken file ends in a message for the user, not a
traceback from deep inside a stage. OBJ, PLY and 3MF files go to MMM's pipeline alone,
as in the app; and if our front end fails on a file that passed, MMM's own reading and
mending take over (front), with a warning saying so.
"""
import math
from mmm import faults
from mmm import meshguard
import os
import shutil
import tempfile
import time

import numpy as np
from scipy import sparse
from scipy.sparse.csgraph import connected_components

from . import build, convert, cracks, progress, workers
from . import features as F
from mmm import cad as mcad
from mmm import fitting as mfit
from mmm import meshio as mio
from mmm import pipeline as mpipe
from mmm.report import NULL, Reporter

# the app's Auto quality by triangle count (MMM desktop/mmm_app/settings.py)
AUTO_QUALITY_STEPS = ((50_000, 0.9), (300_000, 0.8), (1_000_000, 0.7), (3_000_000, 0.6))
FLAT_MM = 0.002          # mm: a surface whose points all lie this near one plane is that plane
SPINDLE = 1.0   # a torus whose centre circle is no bigger than this times its tube stays ours
JUDGE_FLOOR = 0.02         # mm: MMM's fit check inside the pipeline never tighter than this
                           # (its own is 5e-4 x the body's diagonal: 0.009 mm on the 20 mm
                           # tensioner, tighter than a printer or stl2curves' own fits, which
                           # made it 1,706 flat faces)
# our smooth blends, freeform patches and pipes as seeds too (add_blends: on the cam and
# tube MMM test models most of the front end's time); MM_S2C_FRONT_BLENDS=0 seeds with
# the exact features only, and our own conversion alongside fits the blends itself.
# Measured 2026-10-25 (28-part sample, no rivals): without them 10,483 faces instead of
# 7,934 (+32%; test13 106 -> 407, the hook 154 -> 316) for 5% less time: keep them
FRONT_BLENDS = os.environ.get("MM_S2C_FRONT_BLENDS") != "0"
# formats MMM's reader takes on its own (everything else is read as an STL, as before):
# these go to MMM's pipeline without our front end
OTHER_FORMATS = (".obj", ".ply", ".off")
# 3MF projects go through our front end like an STL (owners' choice, 2026-10-30): before,
# they took MMM's path alone and lost every feature (Bravo's skittles nut, a thread, came
# out as 3,272 freeform faces). The plate is read with our 3MF reader, every object placed
# and scaled as printed, so the STEP is laid out as on the plate (also the owners' choice;
# MMM's reader gave each part in its own frame, and a 13-part plate came out in a pile)
PLATE_FORMATS = (".3mf",)
# model units across (mm unless chosen otherwise) our front end works within: smaller,
# its 1e-4 mm weld crushes the shape (a 2 mm sphere came out in 16 faces); larger, its
# thread search's 0.02 mm bands run away (a 1e8 mm sphere asked for 303 GiB). A model
# outside goes to MMM's pipeline alone, which welds relative to the size
FRONT_SIZES = (0.1, 1e5)
# sizes that suggest the wrong unit: MMM's mesh check's note (meshcheck step 13), given
# here when our front end reads the file instead
UNIT_NOTE = (1.0, 3000.0)
FLAT = 1e-9         # a closed body enclosing less than this times the model's diagonal cubed: no volume
INSIDE_OWN = 0.99   # share of a feature's own corners that must pass its surface's domain test to use it
# our seeds against MMM's own segmentation where they are unsure (seeded_segmenter):
PATCH_SEEDS = 3         # seeds of ours (threads aside) in one smooth area that call for the comparison
PATCH_CREASE_DEG = 30.0  # deg: a smooth area ends where triangles meet at more than this (as MMM's _find_sweeps)
SURE_SHARE = 0.99       # share of a seed's triangles MMM's own accept test must take for the seed to be sure
FALSE_SHARE = 0.5       # of a seed's area: taken by that test on less than this, the seed is compared however few are near it
EXACT_KINDS = ("plane", "cylinder", "cone", "sphere", "torus")     # the surfaces a designer draws
FREEFORM_SAVES = 0.75   # a way using freeform surfaces beats an all-exact one only with at most this share of its regions
ON_SURFACE = 0.25       # share of MMM's tolerance the mesh's corners may stand off its exact surfaces in an area for them to win there
MOTIF_GROUP = 0.05      # mm: cylinders whose radii lie this close are one recurrent motif's copies (_motifs)
MOTIF_ROUND = 0.05      # mm: round radii, on this grid, are tried first for a motif ...
MOTIF_ROUND_NEAR = 0.01 # mm: ... when the motif's own value lies this close to one
LOOSE_WINS = 0.5        # a way whose area off the design is at most this share of the other's wins that cluster
ON_DESIGN = 0.002       # mm: a triangle whose corners are all this near its exact surface is on the design (see _off_design)
ON_DESIGN_SIZE = 2e-5   # ... or this share of the mesh's size, for a big part's float32 noise


class OurSurface:
    """One of our surface models as an MMM region surface ("freeform" to MMM: it builds
    the face on geom() and measures with distance()/normal()). normal() follows the OCC
    surface's own orientation, as MMM's freeform cells do, so a region's sense says which
    way the face is turned.

    distance() measures the surface the face is built on: our model's own distance where
    the point lies over geom()'s domain (inside, see _inside), and none at all (infinite)
    beyond it. The model alone is valid anywhere (a freeform height field extends its
    spline, a pipe measures to its end's ball), but geom() covers only the fitted patch and
    a margin: on the GPS case back a flat triangle of 119 mm2 lay within 0.1 mm of a
    freeform patch's model but 39 mm off its surface, MMM took it into that region, the
    face couldn't cover it, and the whole body (518 faces) was saved as 11,690 triangles."""

    kind = "freeform"
    rank = 5
    locked = True
    dev = 0.0

    def __init__(self, model, geom, flip, inside=None):
        self.model, self._geom, self.flip = model, geom, flip
        self.inside = inside

    def distance(self, P):
        P = np.atleast_2d(P)
        d = np.abs(self.model.signed(P))
        if self.inside is None:
            return d
        return np.where(self.inside(P), d, np.inf)

    def normal(self, P):
        return self.flip * self.model.normal(np.atleast_2d(P))

    def degenerate(self, ext):
        return False

    def same_as(self, o, tol, ext):
        return o is self

    def describe(self):
        return f"stl2curves {self.model.kind}"

    def geom(self):
        return self._geom


def quality(n):
    return next((q for top, q in AUTO_QUALITY_STEPS if n < top), 0.5)


def _plane_if_flat(prim, feature, mesh):
    """A plane, if a surface's own points lie on one. A flat ring whose triangles fan round it fits a cone: bevel_big's 440 mm2 base
    was one of half-angle 0.014 degrees with its apex 340 km away, flat to 0.00001 mm, and a surface like that can't be built, so
    it went to B-spline cells. Whatever found it, the simplest surface that fits is the right one."""
    facets = getattr(feature, "facets", None)
    if facets is None or not len(facets):
        return prim
    P = mesh.pts[np.unique(mesh.tris[np.concatenate([mesh.ftris[f] for f in facets])])]
    if len(P) < 3:
        return prim
    c = P.mean(axis=0)
    n = np.linalg.svd(P - c, full_matrices=False)[2][2]
    return mfit.Plane(c, n) if float(np.abs((P - c) @ n).max()) <= FLAT_MM else prim


def mmm_surface(feature, mesh):
    """The MMM surface for one of our features, locked; None to leave its triangles to
    MMM (an N-sided blend, a surface that can't be made)."""
    m = feature.model
    if feature.kind == "blend" or m is None:
        return None
    prim = None
    if isinstance(m, F.Sphere):
        prim = mfit.Sphere(m.c, m.r)
    elif isinstance(m, F.Revolved):
        a, d = np.asarray(m.a, float), np.asarray(m.d, float)
        if m.line:
            c0, k = m.line
            if k == 0:
                prim = mfit.Cylinder(a, d, c0)
            else:
                z = -c0 / k              # the apex, where the profile meets the axis
                # (MMM's cone opens along d from its apex: up the axis when the radius grows)
                prim = mfit.Cone(a + z * d, d if k > 0 else -d, math.atan(abs(k)))
        else:
            rc, zc, r = m.circle
            centre = a + zc * d
            if rc == 0:
                prim = mfit.Sphere(centre, r)
            elif rc > SPINDLE * r:
                prim = mfit.Torus(centre, d, rc, r)
    if prim is not None:
        prim = _plane_if_flat(prim, feature, mesh)
        prim.locked = True
        return prim
    geom = _occ_surface(feature, mesh)
    if geom is None:
        return None
    return OurSurface(m, geom, _orientation(m, geom, mesh, feature), _inside(feature, mesh))


def _inside(feature, mesh):
    """The test whether points lie over the domain of the OCC surface this feature's face
    is built on (_occ_surface), in the model's own parameters; None (no test) when the
    feature's own corners don't pass it (INSIDE_OWN): then the test, not the surface, is
    wrong, and the model's distance stands as before."""
    m = feature.model
    if m.kind == "thread":
        (u0, u1), (lo, hi) = build.thread_reach(feature)

        def test(P):
            s, _, U = m.place(P)
            return (s >= lo) & (s <= hi) & (U >= u0) & (U <= u1)
    elif hasattr(m, "inside"):
        test = m.inside
    else:
        return None
    tids = np.concatenate([mesh.ftris[f] for f in feature.facets])
    own = mesh.pts[np.unique(mesh.tris[tids])]
    try:
        if test(own).mean() < INSIDE_OWN:
            return None
    except Exception as fault:
        faults.caught(fault)
        return None
    return test


def _occ_surface(feature, mesh):
    """The OCC surface our builder cuts this feature's face from (None if it has none)."""
    from OCP.BRep import BRep_Tool
    m = feature.model
    try:
        if m.kind == "thread":
            return build._thread_surface(feature, m)
        if m.kind in ("freeform", "pipe", "extrusion"):
            face = m.face()
            return None if face is None else BRep_Tool.Surface_s(face)
    except Exception as fault:
        faults.caught(fault)
        return None
    return None


def _fit_surfaces(parts):
    """Fit the OCC surfaces of our freeform patches, pipes and extrusions in the worker
    processes, all at once, before the handover asks for them one by one (test12: 20 s
    of its 22.5 on one core). Each comes back in a BRep file (build._surface_to /
    _surface_from, as our builder hands them about); any that fails is fitted here."""
    models = [f.model for mesh, features, _ in parts for f in features
              if f.kind != "blend" and getattr(f.model, "kind", None) in ("freeform", "pipe", "extrusion")
              and getattr(f.model, "_surface", 0) is None]
    pool = workers.get() if len(models) > 1 else None
    if pool is None:
        return
    tmp = tempfile.mkdtemp(prefix="s2c_surfaces_")
    try:
        paths = [os.path.join(tmp, f"{i}.brep") for i in range(len(models))]
        futures = [pool.submit(build._surface_to, m, p) for m, p in zip(models, paths)]
        for m, p, future in zip(models, paths, futures):
            if faults.clean(future):
                build._surface_from(m, p)
    except Exception as fault:
        faults.caught(fault)
        workers.broken()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _orientation(model, geom, mesh, feature, n=24):
    """+1 when the model's normal() points the way the OCC surface's own normal does,
    else -1 (measured at a few of the feature's corners)."""
    from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
    from OCP.GeomLProp import GeomLProp_SLProps
    from OCP.gp import gp_Pnt
    tids = np.concatenate([mesh.ftris[f] for f in feature.facets])
    P = mesh.pts[np.unique(mesh.tris[tids])]
    P = P[np.linspace(0, len(P) - 1, min(n, len(P))).astype(int)]
    N = model.normal(P)
    votes = 0.0
    for p, nm in zip(P, N):
        pr = GeomAPI_ProjectPointOnSurf(gp_Pnt(*map(float, p)), geom)
        if not pr.NbPoints():
            continue
        u, v = pr.LowerDistanceParameters()
        props = GeomLProp_SLProps(geom, u, v, 1, 1e-9)
        if props.IsNormalDefined():
            votes += float(np.dot(nm, props.Normal().Coord()))
    return 1.0 if votes >= 0 else -1.0


# ----------------------------------------------------------------------------
# Our bodies as MMM shells, our features as their regions
# ----------------------------------------------------------------------------


class _Body:
    """One body for MMM: its shells (outer first, then cavities), and for each shell
    the regions our features give it: [(surface, shell triangle numbers)]; part: which
    of our prepared parts it came from, alone: whether it is all of that part; pieces:
    which of the part's pieces (_shells_of) each shell is."""

    def __init__(self):
        self.shells, self.seeds, self.pieces = [], [], []
        self.part, self.alone = None, False


def _split_pinches(V, F):
    """Where more than two triangles share an edge (two parts of a body touching along
    it), each vertex gets one copy per fan of triangles joined through two-sided edges,
    so every edge is two-sided again and the shell closed, as MMM's own repair leaves it
    (ours keeps such edges: on a GPS case back, one edge 0.6 mm long shared by four
    triangles left MMM's shell open, and the merged result was saved as surfaces).
    Triangle rows stay as they are, so our features still map onto them."""
    topo = mio.EdgeTopology(V, F)
    if not topo.nonmanifold_he.any():
        return V, F
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    Fr = F.reshape(-1)
    h = np.nonzero(topo.twin >= 0)[0]
    t = topo.twin[h]
    h, t = h[h < t], t[h < t]

    def nxt(x):
        return 3 * (x // 3) + (x % 3 + 1) % 3

    # a shared edge's triangles pair up around it: each with its neighbour by angle across
    # a wedge of material (the side away from both their normals), the two parts touching
    # there kept apart. The pairs count as two-sided edges below, so the fans at both ends
    # of the edge agree (fans found at each end alone paired them crosswise on that case
    # back, which left the edge open)
    pairs = []
    nm = np.nonzero(topo.nonmanifold_he)[0]
    ends = np.stack([Fr[nm], Fr[nxt(nm)]], axis=1)
    _, group = np.unique(np.sort(ends, axis=1), axis=0, return_inverse=True)
    N = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    for g in range(group.max() + 1):
        hs = nm[group.reshape(-1) == g]
        if len(hs) % 2:
            continue
        p0 = V[Fr[hs[0]]]
        d = V[Fr[nxt(hs[0])]] - p0
        d = d / max(np.linalg.norm(d), 1e-30)
        u = np.cross(d, [1.0, 0.0, 0.0] if abs(d[0]) < 0.9 else [0.0, 1.0, 0.0])
        u /= np.linalg.norm(u)
        w = np.cross(d, u)
        opp = V[Fr[nxt(nxt(hs))]] - p0
        ang = np.arctan2(opp @ w, opp @ u)
        order = np.argsort(ang)
        hs, ang = hs[order], ang[order]
        best = None
        for shift in (0, 1):
            ks = [(k + shift) % len(hs) for k in range(0, len(hs), 2)]
            material = 0
            for k in ks:
                k2 = (k + 1) % len(hs)
                a0, a1 = ang[k], ang[k2] + (2 * np.pi if k2 <= k else 0.0)
                mid = 0.5 * (a0 + a1)
                m = np.cos(mid) * u + np.sin(mid) * w
                material += int(N[hs[k] // 3] @ m < 0) + int(N[hs[k2] // 3] @ m < 0)
            if best is None or material > best[0]:
                best = (material, ks)
        pairs += [(hs[k], hs[(k + 1) % len(hs)]) for k in best[1]]
    if pairs:
        pairs = np.array(pairs, dtype=np.int64)
        h, t = np.concatenate([h, pairs[:, 0]]), np.concatenate([t, pairs[:, 1]])
    # corner 3f+k is vertex F[f, k] in triangle f; a two-sided edge joins the corners
    # of its two vertices in its two triangles
    same = Fr[h] == Fr[t]
    i = np.concatenate([h, nxt(h)])
    j = np.concatenate([np.where(same, t, nxt(t)), np.where(same, nxt(t), t)])
    n = 3 * len(F)
    _, comp = connected_components(coo_matrix((np.ones(len(i), dtype=np.int8), (i, j)), shape=(n, n)),
                                   directed=False)
    key = Fr.astype(np.int64) * n + comp
    uk, inv = np.unique(key, return_inverse=True)
    return V[uk // n], inv.reshape(-1, 3)


def _shells_of(pts, tris):
    """The connected pieces of one of our bodies as MMM shells, each with the rows of
    tris it holds (in order, so triangle numbers map across)."""
    V, Fc = _split_pinches(*mio.compact(pts, tris))
    topo = mio.EdgeTopology(V, Fc)
    n, lab = mio.face_components(Fc, topo)
    out = []
    for c in range(n):
        rows = np.nonzero(lab == c)[0]
        Vs, Fs = mio.compact(V, Fc[rows])
        out.append((mio.Shell(Vs, Fs), rows))
    return out


def bodies_from(parts, groups, pts, pieces_of=None):
    """MMM bodies from our prepared parts, each with, for its shells, the locked regions
    our features give each (_Body.seeds). pieces_of: each part's pieces (_shells_of), if
    made already (route)."""
    bodies = []
    biggest = max(_area(pts, g) for g in groups)
    for gi, ((mesh, features, _), g) in enumerate(zip(parts, groups)):
        pieces = pieces_of[gi] if pieces_of is not None else _shells_of(pts, g)
        seeds = part_seeds(mesh, features, pts, g, pieces)
        made = _nest([p[0] for p in pieces], seeds)
        made = [b for b in made if not _debris(b.shells[0], biggest)]
        for b in made:
            # (our own build of this part is a candidate for the body only when the
            # part is that one body)
            b.part, b.alone = gi, len(made) == 1
        bodies += made
    return bodies


def part_seeds(mesh, features, pts, g, pieces):
    """For each of a part's pieces (_shells_of), the locked regions our features give it:
    [(surface, piece triangle numbers)]."""
    # (our Mesh drops zero-area triangles: theirs map back through this)
    a, b, c = (pts[g[:, k]] for k in range(3))
    kept = np.nonzero(np.linalg.norm(np.cross(b - a, c - a), axis=1) > 1e-12)[0]
    if len(kept) != len(mesh.tris) or not np.array_equal(g[kept], mesh.tris):
        raise RuntimeError("our mesh's triangles don't map onto the body's")
    where = np.empty((len(g), 2), dtype=np.int64)       # body row -> (piece, triangle)
    for i, (_, rows) in enumerate(pieces):
        where[rows, 0] = i
        where[rows, 1] = np.arange(len(rows))
    seeds = [[] for _ in pieces]
    for feature in features:
        surf = mmm_surface(feature, mesh)
        if surf is None:
            continue
        tids = kept[np.concatenate([mesh.ftris[f] for f in feature.facets])]
        for i in np.unique(where[tids, 0]):
            seeds[i].append((surf, where[tids[where[tids, 0] == i], 1]))
    return seeds


def _area(pts, tris):
    a, b, c = (pts[tris[:, k]] for k in range(3))
    return float(np.linalg.norm(np.cross(b - a, c - a), axis=1).sum() / 2)


def _debris(shell, biggest):
    """A loose piece MMM's reader drops (meshcheck.prepare_part): flat (no volume to
    speak of), or tiny beside the biggest piece. Rak N-USB has one of 12 triangles that
    no method could build, and its faceted copy cost faces (the biggest always stays)."""
    a = _area(shell.V, shell.F)
    if a >= biggest:
        return False
    flat = shell.closed and abs(shell.volume) < 1e-9 * max(a, 1e-30) ** 1.5
    return (len(shell.F) < 4 and a < 0.01 * biggest) or flat or a < 5e-5 * biggest


def _nest(shells, seeds):
    """Shells into bodies, as MMM's organise_bodies does: a shell inside no other is a
    body's outer shell (turned outward if it faces in); one inside another is a cavity
    of the smallest one around it if it faces the other way, else a body of its own.
    (Turning a shell round keeps its triangles' numbers, so the seeds still fit.)"""
    box = [(s.V.min(0), s.V.max(0)) for s in shells]

    def inside(j, i):
        return i != j and (box[i][0] <= box[j][0]).all() and (box[i][1] >= box[j][1]).all() \
            and abs(shells[i].volume) > abs(shells[j].volume)

    around = {j: [i for i in range(len(shells)) if inside(j, i)] for j in range(len(shells))}
    for j, s in enumerate(shells):
        if s.closed and not around[j] and s.volume < 0:
            shells[j] = mio.Shell(s.V, s.F[:, ::-1].copy())
    home = {}
    for j, s in enumerate(shells):
        if s.closed and around[j]:
            i = min(around[j], key=lambda i: abs(shells[i].volume))
            if (shells[i].volume > 0) != (s.volume > 0):
                home[j] = i
    bodies = {}
    for j in range(len(shells)):
        if j not in home:
            bodies[j] = _Body()
    for j in range(len(shells)):
        i = j
        while i in home:
            i = home[i]
        bodies[i].shells.append(shells[j])
        bodies[i].seeds.append(seeds[j])
        bodies[i].pieces.append(j)
    out = []
    for i, b in bodies.items():
        # (the outer shell first)
        k = b.shells.index(shells[i])
        b.shells.insert(0, b.shells.pop(k))
        b.seeds.insert(0, b.seeds.pop(k))
        b.pieces.insert(0, b.pieces.pop(k))
        out.append(b)
    return out


def seeded_segmenter(shell, seeds, tol, quality, report=NULL):
    """_seeded_segmenter, then each recurrent radius brought to one value (_motifs)."""
    seg = _seeded_segmenter(shell, seeds, tol, quality, report)
    _motifs(seg, report)
    return seg


def _seeded_segmenter(shell, seeds, tol, quality, report=NULL):
    """MMM's Segmenter for one shell, seeded with our features (_segment), checked where
    they are unsure against MMM's own segmentation (on demand).

    Our front end fits each piece within its own limit (0.1 mm by default) and commits
    greedily, with no step that merges pieces up; MMM fits to a tighter tolerance and
    merges the pieces of one fillet or sweep into one face, but shatters narrow bands we
    find whole. Locked, our pieces of a curving fillet kept MMM from its one face
    (cad_tube_fillet 30 faces where MMM alone makes 15, the cam 22 where it makes 8);
    offered unlocked they still steered it (36 and 77); dropped wholesale, MMM shattered
    the countersinks we had right (Front Lid 53 -> 134). So a smooth area (bounded by
    creases sharper than PATCH_CREASE_DEG) holding PATCH_SEEDS or more of our seeds,
    threads aside, one of them unsure (_sure), is segmented both ways, with our seeds and
    by MMM alone (those seeds left out), and the way _winner picks is kept there. Where
    nothing is unsure, it is as before. (On 17 parts: tube 30 -> 15, cam 22 -> 8, hook
    154 -> 109, liner 141 -> 93; Front Lid, the 608 bearing, test13 unchanged.)

    The choice is made cluster by cluster inside the compared areas (see _clusters, _winner):
    one smooth area can hold both a slot's plane only MMM finds and a blended corner only we
    have whole (a Raspberry Pi case's whole inside was one area, won whole by MMM, which
    shredded the corners)."""
    held = _Held(report)
    A = _segment(shell, seeds, tol, quality, held)
    joined = _loose_onto_exact(A, seeds)
    if joined is not None:
        seeds, n = joined
        report.log(f"{n} freeform piece(s) found lie exactly on the surface beside them, so they join it")
        held = _Held(report)
        A = _segment(shell, seeds, tol, quality, held)
    comp = _smooth_areas(A)
    where = [int(np.bincount(comp[np.asarray(f, dtype=np.int64)]).argmax()) for _, f in seeds]
    count, unsure, loose, false = {}, set(), set(), set()
    for i, (surf, faces) in enumerate(seeds):
        if _thread(surf):
            continue
        a = where[i]
        count[a] = count.get(a, 0) + 1
        if a not in unsure and not _sure(A, surf, faces):
            unsure.add(a)
        if _freeform(surf):
            loose.add(a)
        if a not in false and _false_fit(A, surf, faces):
            false.add(a)
    # (an area holding a freeform piece of ours is compared too, however few pieces it
    # has: a Raspberry Pi case's port frame, chamfer planes and corner cones, was one
    # freeform piece, and never compared)
    areas = {a for a in unsure if count[a] >= PATCH_SEEDS or a in loose or a in false}
    if not areas:
        held.replay()
        return A
    mine = {i for i, (surf, _) in enumerate(seeds) if where[i] in areas and not _thread(surf)}
    held_b = _Held(report)
    B = _segment(shell, [s for i, s in enumerate(seeds) if i not in mine], tol, quality, held_b)
    cluster, compared = _clusters(A, B, comp, areas)
    off_a, off_b = _off_design(A), _off_design(B)
    theirs = np.zeros(int(cluster.max()) + 1, dtype=bool)
    for c in compared:
        t = np.nonzero(cluster == c)[0]
        theirs[c] = _winner(_census(A, t, off_a), _census(B, t, off_b), _on_surfaces(B, t)) == "mmm"
    won = int(theirs.sum())
    report.log(f"In {len(areas)} smooth area(s) the first surfaces found were unsure, so a second segmentation was "
               f"tried there as well: of {len(compared)} groups of surfaces, the second was kept in {won}, "
               f"the first in {len(compared) - won}")
    if not won:
        held.replay()
        return A
    (held_b if won == len(compared) else held).replay()
    return _compose(A, B, cluster, theirs)


def _loose_onto_exact(A, seeds):
    """(seeds with each freeform piece of ours that lies on a neighbouring exact seed's surface
    given to that seed, how many) or None when there is none.

    A designed surface whose end our feature finding didn't reach leaves a band of its own
    triangles as a freeform piece, and one freeform piece makes its whole smooth area unsure
    and compared, where MMM's segmentation can shred what was right (the feeding tube: as STL,
    81 triangles at the tangent end of a bend, on its torus within 0.000001 mm and all taken by
    MMM's accept test, were left as a freeform piece, the area was compared and the bend came
    out as 26 cylinders, 37 faces; as 3MF, the same mesh rounded 0.000007 mm differently, the
    torus took them: 20 faces). A piece goes whole, to the neighbour whose surface MMM's own
    accept test takes at least SURE_SHARE of its triangles on, the one taking most."""
    owner = np.full(len(A.F), -1, dtype=np.int64)
    for j, (_, faces) in enumerate(seeds):
        owner[np.asarray(faces, dtype=np.int64)] = j
    into = {}
    for i, (surf, faces) in enumerate(seeds):
        if not _freeform(surf):
            continue
        faces = np.asarray(faces, dtype=np.int64)
        near = owner[A.nb[faces].reshape(-1)]
        best = None
        for j in np.unique(near[(near >= 0) & (near != i)]).tolist():
            sj, fj = seeds[j]
            if isinstance(sj, OurSurface) or _thread(sj) or j in into:
                continue
            fj = np.asarray(fj, dtype=np.int64)
            share = float(A.accept(sj, A.sense_of(sj, fj), faces).mean())
            if share >= SURE_SHARE and (best is None or share > best[0]):
                best = (share, j)
        if best is not None:
            into[i] = best[1]
    if not into:
        return None
    out = [[surf, np.asarray(faces, dtype=np.int64)] for surf, faces in seeds]
    for i, j in into.items():
        out[j][1] = np.concatenate([out[j][1], out[i][1]])
    return [(surf, faces) for k, (surf, faces) in enumerate(out) if k not in into], len(into)


def _motifs(seg, report=NULL):
    """Recurrent motifs: a designer draws many holes, bosses and fillets at one radius, and MMM fits
    each copy on its own, a few hundredths apart (x-end-idler: 11 cylinders drawn at 3.0 mm came out
    from 2.95 to 3.04). Each of MMM's cylinders (ours, locked, are already equalised by
    features.snap and never moved here) is offered its motif's value: the median radius of every
    cylinder in the part within MOTIF_GROUP of its own, ours included, or the round value on
    MOTIF_ROUND's grid when that median lies within MOTIF_ROUND_NEAR of one. The cylinder keeps its
    axis direction, its axis line is refitted at that radius, and the change is kept only if MMM's
    accept test still takes all its triangles. The group, not whichever side found a copy, is the
    reference: copying the values of our locked surfaces pulled the knob's exact 0.3 mm surfaces to
    0.298. On the designers' own parts, the share of our cylinders at a radius the designer used
    went 69 -> 77% (x-end-idler), 57 -> 64% (el-box cover), 49 -> 83% (knob), with faces and
    closeness to the mesh unchanged."""
    regs = seg.regions
    cyl = [r for r in regs if r.kind == "cylinder"]
    if len(cyl) < 2:
        return 0
    radii = np.array([float(r.prim.r) for r in cyl])
    moved = 0
    for r in cyl:
        if mcad._locked(r.prim):
            continue
        own = float(r.prim.r)
        group = radii[np.abs(radii - own) <= MOTIF_GROUP]
        if len(group) < 2:
            continue
        value = float(np.median(group))
        rounded = round(value / MOTIF_ROUND) * MOTIF_ROUND
        tries = ([rounded] if abs(rounded - value) <= MOTIF_ROUND_NEAR else []) + [value]
        faces = np.asarray(r.faces, dtype=np.int64)
        P = None
        for v in tries:
            if abs(v - own) < 1e-9:
                break
            if P is None:
                P = seg.region_points(faces, True)[0]
            new = mfit.Cylinder(_axis_at_radius(P, r.prim.d, r.prim.a, v), r.prim.d, v)
            if seg.accept(new, r.sense, faces).all():
                r.prim = new
                moved += 1
                break
    if moved:
        report.log(f"{moved} cylinder(s) brought to the radius their copies share (a designer's recurring "
                   "hole, boss or fillet size)")
    return moved


def _axis_at_radius(P, d, a0, r, iters=8):
    """The axis line, in direction d, that puts the points P at distance r from it, near a0 (Gauss-Newton
    on the axis's offset across d)."""
    u, v = mfit.perp_basis(d)
    Q = np.c_[(P - a0) @ u, (P - a0) @ v]
    c = np.zeros(2)
    for _ in range(iters):
        D = Q - c
        rho = np.maximum(np.linalg.norm(D, axis=1), 1e-12)
        step = np.linalg.lstsq(-D / rho[:, None], -(rho - r), rcond=None)[0]
        c = c + step
        if np.linalg.norm(step) < 1e-10:
            break
    return a0 + c[0] * u + c[1] * v


def _clusters(A, B, comp, areas):
    """Which triangles belong together for choosing between the segmentations: (each triangle's
    cluster, or -1 where one way left it unlabelled; the clusters to compare).

    B is MMM's run over the whole shell with our seeds of the compared areas left out, and
    MMM's region growing is global, so B also differs from A in areas that were never
    compared, unchecked. Taken whole, it replaced our segmentation there too: on the P1S
    chute, B's version of the slide, two designed planes meeting at 20 deg that A had as two
    planes, was one freeform region 2.2 mm off the mesh (97.5% of the surface within 0.1 mm).
    Cutting each region at the area's border instead left pieces down to one triangle on
    both sides (Rak Uni: 76 regions cut, 43 failing at once, so the part fell back to flat
    facets; 2026-11-02). So regions of A and B sharing a triangle belong together, a cluster
    being a set of triangles that both ways divide into whole regions. A cluster is compared
    when most of its area lies in the compared areas, and those that aren't stay ours."""
    na, nb = len(A.regions), len(B.regions)
    both = (A.label >= 0) & (B.label >= 0)
    links = sparse.coo_matrix((np.ones(int(both.sum())), (A.label[both], B.label[both] + na)),
                              shape=(na + nb, na + nb))
    _, of_region = connected_components(links, directed=False)
    cluster = np.full(len(A.F), -1, dtype=np.int64)
    cluster[both] = of_region[A.label[both]]
    inside = np.isin(comp, np.fromiter(areas, dtype=np.int64))
    n = int(of_region.max()) + 1
    area = np.bincount(cluster[both], A.fa[both], minlength=n)
    area_in = np.bincount(cluster[both], A.fa[both] * inside[both], minlength=n)
    return cluster, np.nonzero(area_in > 0.5 * area)[0].tolist()


def _compose(A, B, cluster, theirs):
    """One segmentation: each cluster (see _clusters) all MMM's (B) where theirs, else all ours
    (A), without cutting a region of either."""
    na = len(A.regions)
    both = cluster >= 0
    of_region = np.full(na + len(B.regions), -1, dtype=np.int64)
    of_region[A.label[both]] = cluster[both]
    of_region[B.label[both] + na] = cluster[both]
    label = np.full(len(A.F), -1, dtype=np.int64)
    regions = []
    for src, base, side in ((A, 0, False), (B, na, True)):
        for k, reg in enumerate(src.regions):
            c = of_region[base + k]
            # (a region all of whose triangles the other way left unlabelled is in no cluster: ours)
            if bool(theirs[c]) == side if c >= 0 else not side:
                label[np.asarray(reg.faces, dtype=np.int64)] = len(regions)
                regions.append(reg)
    A.label, A.regions = label, regions
    return A


class _Held:
    """A report that holds a trial segmentation's messages (its progress goes through), to
    be passed on only if that segmentation is the one kept."""

    def __init__(self, report):
        self.report, self.calls = report, []

    def __getattr__(self, name):
        if name in ("progress", "stage", "step", "section"):
            return getattr(self.report, name)

        def hold(*a, **k):
            self.calls.append((name, a, k))
        return hold

    def replay(self):
        for name, a, k in self.calls:
            getattr(self.report, name)(*a, **k)


def _thread(surf):
    return getattr(getattr(surf, "model", None), "kind", None) == "thread"


def _freeform(surf):
    return isinstance(surf, OurSurface) and getattr(getattr(surf, "model", None), "kind", None) == "freeform"


def _exact(reg):
    """A surface a designer draws, of MMM's own types (not a wrapped approximation of ours)."""
    return reg.kind in EXACT_KINDS and not isinstance(reg.prim, OurSurface)


def _on_surfaces(seg, tris):
    """Whether every region among the triangles is an exact surface with the mesh's corners on
    it (within ON_SURFACE of the tolerance): a CAD export's vertices lie on the surfaces its
    designer drew, so there they are those surfaces, however many (a chamfered port frame:
    4 planes and 4 cones, 0.0000 and 0.0033 mm, against one freeform piece of ours)."""
    labels = seg.label[tris]
    found = False
    for r in np.unique(labels[labels >= 0]).tolist():
        reg = seg.regions[r]
        if not _exact(reg):
            return False
        corners = seg.V[np.unique(seg.F[tris[labels == r]])]
        if float(reg.prim.distance(corners).max()) > ON_SURFACE * seg.tol:
            return False
        found = True
    return found


def _off_design(seg):
    """Each triangle: is it off the designer's surfaces? A CAD export puts every corner on the
    true surface, so a triangle with a corner off its surface by more than ON_DESIGN isn't on
    one, whatever the surface's type: that exact surface was forced onto it, inside MMM's
    tolerance. Freeform surfaces never count as on it. At a Raspberry Pi case's blended corner
    both ways are off the design there, MMM's 20 small planes and tilted cylinders (0.01 to
    0.04 mm) as much as our one freeform piece; its slots' flat floors are on it only in MMM's
    way, its frames' corner cones only in ours (0.0000 mm, against tilted cylinders). Nor is a
    triangle on a surface its middle strays from, by MMM's own test (Segmenter.face_error): a
    torus through head's three rings of corners holds every one of them."""
    off = np.ones(len(seg.F), dtype=bool)
    near = max(ON_DESIGN, ON_DESIGN_SIZE * float(np.linalg.norm(np.ptp(seg.V, axis=0))))
    for reg in seg.regions:
        faces = np.asarray(reg.faces, dtype=np.int64)
        if len(faces) and _exact(reg):
            d = np.asarray(reg.prim.distance(seg.V[seg.F[faces].ravel()])).reshape(-1, 3)
            off[faces] = (d.max(axis=1) > near) | ~np.isfinite(seg.face_error(reg.prim, reg.sense, faces)[0])
    return off


def _sure(seg, surf, faces):
    """Whether one of our seeds is sure: an exact surface that MMM's own accept test, at
    its tolerance, takes at least SURE_SHARE of the triangles of (our approximating
    surfaces, freeform patches, pipes and extrusions, never are)."""
    if isinstance(surf, OurSurface):
        return False
    faces = np.asarray(faces, dtype=np.int64)
    return float(seg.accept(surf, seg.sense_of(surf, faces), faces).mean()) >= SURE_SHARE


def _false_fit(seg, surf, faces):
    """Whether one of our exact seeds holds its corners and little else: MMM's accept test takes under FALSE_SHARE of
    its area. A torus always passes through three rings of corners, so head's cylinder and cone, 9 and 7 mm long and
    0.21 mm from it between their corners, came in as one torus, and it went to B-splines when the fit check threw it
    out. Only a comparison can settle it, however few seeds are near: the Pi case's corner sphere fails the same
    test with its corners exactly on the surface, and nothing better is there to replace it."""
    if isinstance(surf, OurSurface):
        return False
    faces = np.asarray(faces, dtype=np.int64)
    area = seg.fa[faces]
    return float(area[seg.accept(surf, seg.sense_of(surf, faces), faces)].sum()) < FALSE_SHARE * float(area.sum())


def _smooth_areas(seg):
    """Each triangle's smooth area: connected across edges whose triangles meet at less
    than PATCH_CREASE_DEG (as MMM's _find_sweeps cuts its patches)."""
    from scipy import sparse
    from scipy.sparse.csgraph import connected_components
    nf = len(seg.F)
    rows = np.repeat(np.arange(nf), 3)
    cols = seg.nb.reshape(-1)
    m = cols >= 0
    smooth = m.copy()
    smooth[m] = (seg.fn[rows[m]] * seg.fn[cols[m]]).sum(1) >= math.cos(math.radians(PATCH_CREASE_DEG))
    G = sparse.coo_matrix((np.ones(int(smooth.sum()), np.int8), (rows[smooth], cols[smooth])), shape=(nf, nf))
    return connected_components(G, directed=False)[1]


def _census(seg, tris, off):
    """Among the triangles: (how many regions, whether all of those are exact surfaces with the
    mesh's corners on them, the area off the design, _off_design). Only surfaces on the design
    count as exact here: a way that forced exact pieces onto a blend gets no credit for their
    type (a Raspberry Pi case's end wall: its four corners differed by this alone, two torn,
    two clean, 8 regions of ours against 10 and 11 of MMM's)."""
    labels = seg.label[tris]
    regions = np.unique(labels[labels >= 0]).tolist()
    away = float(seg.fa[tris[off[tris]]].sum())
    return (len(regions), away == 0.0 and all(_exact(seg.regions[r]) for r in regions), away)


def _winner(ours, theirs, theirs_on_surfaces=False):
    """Which way a cluster keeps, "ours" or "mmm", from each way's census (regions, all exact
    and on the design, area off the design): the fewer regions, except that a way made only of
    exact surfaces on the design (what a designer draws, and what edits well) beats one that
    isn't unless that one has at most FREEFORM_SAVES of its regions. A tie keeps ours, as before. Before
    the counts, the way leaving at most LOOSE_WINS as much of the cluster off the design wins.
    MMM's way wins outright where its exact surfaces hold the mesh's corners
    (theirs_on_surfaces, see _on_surfaces) and ours uses freeform: the designer's surfaces,
    however many."""
    (na, ea, la), (nb, eb, lb) = ours, theirs
    if eb and not ea and theirs_on_surfaces:
        return "mmm"
    # the way that leaves less of the area off the design keeps closer to it, whatever its
    # count of regions (Tuna and Duvdivan, 2026-11-01: fewer faces isn't the aim; toolbox
    # Body474 kept 11 freeform pieces of ours over MMM's cylinders on a count). Measured on the
    # mesh's corners, not by surface type: exact pieces forced onto a blend are no more the
    # design than a freeform face over it, only more faces
    if lb <= LOOSE_WINS * la and la > 0:
        return "mmm"
    if la <= LOOSE_WINS * lb and lb > 0:
        return "ours"
    if ea and not eb:
        return "mmm" if nb <= FREEFORM_SAVES * na else "ours"
    if eb and not ea:
        return "ours" if na <= FREEFORM_SAVES * nb else "mmm"
    return "mmm" if nb < na else "ours"


def _accuracy(body):
    """How far a body's mesh corners stand off the curved surfaces our features found on it, in
    the model's unit: the worst corner of each, the most of the curved area lying within it
    (ACCURATE_SHARE). None with no such surfaces to tell. A CAD export's are a few microns off,
    whatever its triangles' size; a scan's are its noise. Measured, so it needs no guess at
    the kind of mesh."""
    off, area = [], []
    for shell, seeds in zip(body.shells, body.seeds):
        a, b, c = (shell.V[shell.F[:, k]] for k in range(3))
        fa = np.linalg.norm(np.cross(b - a, c - a), axis=1) / 2
        for surf, faces in seeds:
            if isinstance(surf, OurSurface) or surf.kind not in ("cylinder", "cone", "sphere", "torus"):
                continue
            faces = np.asarray(faces, dtype=np.int64)
            off.append(float(surf.distance(shell.V[np.unique(shell.F[faces])]).max()))
            area.append(float(fa[faces].sum()))
    if not off:
        return None
    order = np.argsort(off)
    run = np.cumsum(np.array(area)[order])
    return float(np.array(off)[order][int(np.searchsorted(run, ACCURATE_SHARE * run[-1]))])


def _segment(shell, seeds, tol, quality, report=NULL):
    """MMM's Segmenter for one shell, our regions in first (locked, grown into the
    neighbouring triangles that fit them by MMM's own test), then MMM's run over the rest."""
    seg = mcad.Segmenter(shell.V, shell.F, shell.topo, tol, report)
    seg.detail = quality
    # (no seeds on known axes here: our features hold those surfaces already, and the extra
    # hypotheses cost Solar Panel Bracket Large its CAD faces; see mmm/cad.py, b13 note)
    seg.known_axes = False
    for surf, faces in seeds:
        faces = np.asarray(faces, dtype=np.int64)
        faces = faces[seg.label[faces] < 0]
        if not len(faces):
            continue
        sense = seg.sense_of(surf, faces)
        seg.label[faces] = len(seg.regions)
        seg.regions.append(mcad.Region(surf, faces, sense))
    for rid, reg in enumerate(list(seg.regions)):
        grown, _ = seg.grow(reg.prim, reg.sense, reg.faces, refit=False)
        new = grown[seg.label[grown] < 0]
        if len(new):
            seg.label[new] = rid
            reg.faces = np.concatenate([reg.faces, new])
    seg.run()
    return seg


# ----------------------------------------------------------------------------
# The front end handed to MMM's pipeline
# ----------------------------------------------------------------------------


ACCURATE_SHARE = 0.75   # of a body's curved area, in surfaces whose corners lie this close: the mesh's accuracy (_accuracy)
WRECKED = 0.02      # our repair moving the enclosed volume more than this share: try MMM's


def _signed_volume(pts, tris):
    a, b, c = (pts[tris[:, k]] for k in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


class _Route:
    """The method for each of our bodies, chosen before any feature is looked for (called
    by convert.prepare with the bodies as split): which to look for features in.

    --preset cad: every body (a mechanical part: our features, then MMM's segmentation of
    the rest). --preset smooth: none (an artistic model: smooth surfaces only). Auto: MMM's own choice per body
    (pipeline.choose_preset_body: flat faces, one motion sweeping the curved surface, one
    surface a shell), as MMM alone makes it; a body routed to smooth surfaces is looked
    at only if they don't follow its mesh and CAD faces are tried (later). Before, any
    body we found anything in was forced to CAD: on organic_metaballs a patchwork of 342
    analytic pieces in 128 s, where MMM alone made 348 smooth surfaces in 17 s.
    pieces[k]: part k's pieces (_shells_of), made here and kept for bodies_from; want[k]:
    whether part k is looked at."""

    def __init__(self, opts, report):
        self.opts, self.report = opts, report
        self.pieces = self.want = None

    def __call__(self, pts, groups):
        self.pieces = [_shells_of(pts, g) for g in groups]
        self.want = self._choose(pts, groups)
        return self.want

    def _choose(self, pts, groups):
        preset = self.opts.preset
        if preset == "cad":
            self.report.log("CAD faces, as chosen (a mechanical part): features are looked for in every body")
            return [True] * len(groups)
        if preset != "auto":
            self.report.log(f"{mpipe.PRESETS.get(preset, {}).get('label', preset)} surfaces, as chosen: feature finding isn't used")
            return [False] * len(groups)
        biggest = max(_area(pts, g) for g in groups)
        out = []
        for gi, pieces in enumerate(self.pieces):
            made = _nest([p[0] for p in pieces], [[] for _ in pieces])
            kinds = []
            for b in made:
                if _debris(b.shells[0], biggest):
                    continue
                P = np.vstack([s.V for s in b.shells])
                tol = self.opts.tolerance or mpipe.auto_tolerance(float(np.linalg.norm(P.max(0) - P.min(0))), "cad",
                                                                  self.opts.quality)
                kinds.append(mpipe.choose_preset_body(b.shells, tol, self.opts.quality, NULL)[0])
            cad = "cad" in kinds or not kinds
            out.append(cad)
            which = f"Body {gi + 1}" if len(groups) > 1 else "The part"
            self.report.log(f"{which}: " + ("CAD faces (flat faces, a sweep, or one simple surface): features "
                                            "are looked for in it" if cad else
                                            "smooth surfaces (an organic shape: no flat faces to speak of): "
                                            "feature finding is skipped, and used only if CAD faces "
                                            "are tried after all"))
        return out


def _plate(path):
    """A 3MF project as (points, triangles, part of each triangle): every object its build
    prints, placed and scaled as printed (read3mf), each object's parts numbered apart so
    prepare() mends and builds each on its own (a part's pieces can overlap)."""
    objects = convert.read_3mf(path)
    if not objects:
        raise mio.MeshError("This 3MF project has nothing to print in it, so there's no shape to convert.")
    pts, tris, part, start, label = [], [], [], 0, 0
    for _, P, T, L in objects:
        pts.append(P)
        tris.append(T + start)
        part.append(np.asarray(L) + label)
        start += len(P)
        label += int(np.max(L)) + 1 if len(L) else 1
    return np.vstack(pts), np.vstack(tris), np.concatenate(part)


def _mended(path, opts, report, route=None):
    """Our prepared parts, on our mended mesh, or on MMM's where ours moved the enclosed
    volume by more than WRECKED and MMM's moved it less (a cap of thin walls: ours split
    4,825 of its 5,196 triangles away as slivers, -19% volume; MMM's kept it whole).
    Returns (parts, info, part)."""
    plate = str(path).lower().endswith(PLATE_FORMATS)
    source = _plate(path) if plate else path
    parts, info, part = convert.prepare(source, 0.01, blends=FRONT_BLENDS, analyze=route)
    pts0, tris0 = (source[0], source[1]) if plate else convert.load_stl(path)
    if not plate and any("three or more" in s for s in info.get("mesh_left") or []):
        # (edges of three or more triangles our repair couldn't remove: no solid closes there.
        # On a mesh finer than the weld grid they can be the grid's doing; read with only its
        # identical corners joined, if that is clean: see features.exact_weld)
        exact = F.exact_weld(path)
        if exact is not None:
            report.log("The mesh is finer than the usual corner weld, which joined corners that should stay apart, "
                       "so it is read again with only its identical corners joined")
            parts, info2, part = convert.prepare(exact, 0.01, blends=FRONT_BLENDS, analyze=route)
            info2["triangles"] = info["triangles"]
            info = info2
            pts0, tris0 = exact
    raw = abs(_signed_volume(pts0, tris0))
    mine = abs(sum(_signed_volume(p[0].pts, p[0].body_tris) for p in parts))
    # (edges our repair left of three or more triangles, or open: no solid closes there, even
    # with the volume unmoved. A file touching itself along a seam, like the drybox axle's thread
    # joined at its root radius (2,209 such edges), keeps its volume through our repair and still
    # came out as open surfaces, where MMM's separates the touching corners and closes it)
    unclosed = not plate and any("three or more" in s or "open edge" in s for s in info.get("mesh_left") or [])
    if not raw or (abs(mine - raw) <= WRECKED * raw and not unclosed):
        return parts, info, part
    theirs_bodies, _ = mio.load_and_prepare(path, repair=opts.repair, remove_debris=opts.remove_debris, report=NULL,
                                            **({"layout": "plate"} if plate else {}))
    shells = [s for b in theirs_bodies for s in b]
    theirs = abs(sum(s.volume for s in shells))
    if abs(mine - raw) > WRECKED * raw:
        if abs(theirs - raw) >= abs(mine - raw):
            return parts, info, part
        report.log(f"The first mesh repair moved the enclosed volume {100 * (mine - raw) / raw:+.1f}%, the second "
                   f"{100 * (theirs - raw) / raw:+.1f}%: the second repair's mesh is used, with features found on it")
    else:
        if not all(s.closed for s in shells) or abs(theirs - raw) > WRECKED * raw:
            return parts, info, part
        report.log("The first mesh repair couldn't close this mesh (it touches itself), the second did: "
                   "the second repair's mesh is used, with features found on it")
    offsets = np.cumsum([0] + [len(s.V) for s in shells])
    pts = np.vstack([s.V for s in shells])
    tris = np.vstack([s.F + o for s, o in zip(shells, offsets)])
    repairs = ["the second mesh repair (the first moved the volume too far)"]
    # (closed, but the file's own shapes may still lie apart by a hairline crack, whose walls
    # would be built as faces inside the part: the axle's solid held 42% of its area so, took 18
    # minutes against 2, and Fusion refused half the cuts on it)
    sealed = cracks.seal(pts, tris) if unclosed else None
    if sealed is not None:
        pts, tris, note = sealed
        report.log(f"The mesh's shapes were pressed together with cracks left between them, so they were made one solid: {note}")
        repairs.append(note)
    parts, info2, part = convert.prepare((pts, tris), 0.01, mend=False, blends=FRONT_BLENDS, analyze=route)
    info2["triangles"] = info["triangles"]
    info2["repairs"] = repairs
    return parts, info2, part


def front(path, opts, report):
    """stl2curves' first stages for MMM's pipeline.convert: (bodies, info, seeded).
    seeded[k](tol) gives body k's ("cad", {id(shell): segmenter}); a body
    missing from it (an organic body with nothing of ours in it) gets MMM's own choice.
    If our stages fail on the file (which passed the entrance, so MMM's reader takes it),
    MMM's own reading, mending and segmentation convert it instead, and the user is told.
    A mesh our repair leaves nothing of is refused (_NothingLeft)."""
    try:
        return _front(path, opts, report)
    except (MemoryError, _NothingLeft):
        raise
    except Exception as e:
        faults.caught(e)
        workers.stop()
        report.warn(f"Feature finding failed on this file ({type(e).__name__}: {e}), so it is converted without it, "
                    "with the other mesh repair and surface fitting.")
        bodies, info = mio.load_and_prepare(path, repair=opts.repair, remove_debris=opts.remove_debris, report=report,
                                            layout=opts.layout, merge=opts.merge_overlaps,
                                            touching=opts.touching_parts)
        return bodies, info, {}


class _NothingLeft(mio.MeshError):
    """Our repair left no body: every piece of the mesh was loose debris. 3,000 triangles
    lying apart (a triangle soup) crashed here; MMM alone takes each one for a body of
    its own and was still at it after 5 minutes, to save 3,000 loose flat faces."""


def _front(path, opts, report):
    if str(path).lower().endswith(OTHER_FORMATS):
        # (OBJ, PLY and OFF: MMM's own reader and segmentation, for now)
        bodies, info = mio.load_and_prepare(path, repair=opts.repair, remove_debris=opts.remove_debris,
                                            report=report, layout=opts.layout, merge=opts.merge_overlaps,
                                            touching=opts.touching_parts)
        return bodies, info, {}
    # (most of this stage is looking for features, not mending: a grid of 2,100 small walls, its mesh
    # needing no mending at all, stayed here 19 minutes with nothing to show, and looked stuck)
    report.stage("Reading the mesh and finding its features (stl2curves)", 0.0, 0.3)
    route = _Route(opts, report)
    progress.listen(report.log)
    try:
        parts, info, part = _mended(path, opts, report, route)
    finally:
        progress.listen(None)
    groups = [p[0].__dict__.get("body_tris") for p in parts]
    if any(g is None for g in groups):
        raise RuntimeError("prepare() didn't keep each body's triangles")
    pts = parts[0][0].pts
    report.stage("Handing the features over", 0.3, 0.35)
    _fit_surfaces(parts)
    ours = bodies_from(parts, groups, pts, route.pieces)
    bodies = [b.shells for b in ours]
    if not bodies:
        raise _NothingLeft(f"Nothing is left of this mesh once its loose bits are removed: its {info['triangles']:,} "
                           "triangles don't join up into a surface, so there's no shape to convert.")
    minfo = mio.MeshInfo()
    allP = np.vstack([s.V for b in bodies for s in b])
    minfo.diag = float(np.linalg.norm(allP.max(0) - allP.min(0)))
    minfo.bbox = (allP.min(0), allP.max(0))
    minfo.triangles = minfo.input_triangles = info["triangles"]
    minfo.vertices = len(allP)
    minfo.shells = sum(len(b) for b in bodies)
    minfo.open_shells = sum(not s.closed for b in bodies for s in b)
    minfo.bodies = len(bodies)
    minfo.body_names = [None] * len(bodies)
    minfo.part_names = ["part"]
    minfo.part_triangles = [info["triangles"]]
    minfo.body_part = [0] * len(bodies)
    minfo.mesh_check = {"status": "ok", "issues": [], "repairs": info.get("repairs", [])}
    n_open = sum(1 for b in bodies if not all(s.closed for s in b))
    if n_open:
        # (as MMM's mesh check says it: a sheet or a single triangle came back "valid" with
        # no word that the file holds a surface, not a solid)
        from mmm.meshcheck import _issue, _plural
        minfo.mesh_check["issues"].append(_issue(
            "open_surface", n_open, "not_fixed", f"{_plural(n_open, 'piece')} can't be closed into a solid (a "
            "surface with no thickness, or holes too big to fill)", "Converted as surfaces instead of a solid."))
        minfo.mesh_check["status"] = "problems"
        report.warn(f"{_plural(n_open, 'piece')} of this mesh can't be closed into a solid, so "
                    + ("it is" if n_open == 1 else "they are") + " converted as surfaces instead of a solid.")
    # (our repair can close a single triangle into two faces back to back: "a solid" of no
    # volume, which came back valid with no word)
    n_flat = sum(1 for b in bodies if all(s.closed for s in b)
                 and abs(sum(s.volume for s in b)) <= FLAT * minfo.diag ** 3)
    if n_flat:
        from mmm.meshcheck import _issue, _plural
        minfo.mesh_check["issues"].append(_issue(
            "no_volume", n_flat, "not_fixed", f"{_plural(n_flat, 'piece')} enclose{'s' if n_flat == 1 else ''} "
            "no volume (flat, or paper-thin)", "Converted, but not as a real solid."))
        minfo.mesh_check["status"] = "problems"
        report.warn(f"{_plural(n_flat, 'piece')} of this mesh enclose{'s' if n_flat == 1 else ''} no volume (flat, "
                    "or paper-thin), so the result is not a real solid.")
    report.log("Mesh repair: " + ("; ".join(info["repairs"]) if info.get("repairs") else "nothing to mend"))

    for b in ours:
        acc = _accuracy(b)
        if acc is not None:
            opts.mesh_accuracy[id(b.shells[0])] = acc

    def seeded(k):
        body = ours[k]
        if not any(body.seeds):
            return None

        def make(tol):
            segs = {}
            for shell, seeds in zip(body.shells, body.seeds):
                segs[id(shell)] = seeded_segmenter(shell, seeds, tol, opts.quality, report)
            return "cad", segs
        return make

    # Our worker processes here aren't needed any more. They are stopped, not kept for a next
    # part as stl2curves keeps them: each holds a copy of the mesh
    workers.stop()

    analysed = {}

    def later(k):
        """Body k's segmentation seeded with our features, looked for only now: for a body
        routed to smooth surfaces when CAD faces are tried after all (pipeline marks it by
        .later). ("cad", None) if we find nothing in it: MMM's own segmentation."""
        body = ours[k]
        gi = body.part

        def make(tol):
            if gi not in analysed:
                report.log("Features are looked for in this body now, as CAD faces are tried for it")
                g = groups[gi]
                if len(g) >= convert.WORKERS_FROM:
                    workers.start(len(g))
                try:
                    mesh, features, t = convert._analyze_body(pts, g, 0, 1)
                    convert.snap(mesh, features, None)
                    if FRONT_BLENDS:
                        features = convert.add_blends(mesh, features)
                finally:
                    workers.stop()
                analysed[gi] = part_seeds(mesh, features, pts, g, route.pieces[gi])
            seeds = analysed[gi]
            body.seeds = [seeds[j] for j in body.pieces]
            if not any(body.seeds):
                return "cad", None
            return make_segs(body, tol)
        make.later = True
        return make

    def make_segs(body, tol):
        segs = {}
        for shell, seeds in zip(body.shells, body.seeds):
            segs[id(shell)] = seeded_segmenter(shell, seeds, tol, opts.quality, report)
        return "cad", segs

    made = {k: seeded(k) if route.want[b.part] else (later(k) if opts.preset == "auto" else None)
            for k, b in enumerate(ours)}
    return bodies, minfo, {k: m for k, m in made.items() if m is not None}


def _entrance(path, out):
    """The checks every file passes before any work: (whether it is read as an STL, its
    triangle count, its diagonal; 0 and None for other formats). Raises what mmm/cli.py's failure() turns into
    a message for the user: the paths (pipeline.check_paths: an output naming the input
    overwrote the user's mesh), then the mesh, read by MMM's reader and given its
    refusals (meshio.check_triangles: our own reader crashed on empty and malformed
    files, and a sphere 1e7 mm from the origin, all corners joined in MMM's weld, got
    past to a quad remesh that took 14 GB). Other formats are checked by MMM's reader
    in its own pipeline."""
    mpipe.check_paths(path, out)
    if str(path).lower().endswith(OTHER_FORMATS):
        return False, 0, None
    if str(path).lower().endswith(PLATE_FORMATS):
        try:
            pts, tris, _ = _plate(path)
        except (mio.MeshError, OSError, MemoryError):
            raise
        except Exception as e:
            faults.caught(e)
            raise mio.MeshError(f"This 3MF project seems to be damaged, so it couldn't be read ({type(e).__name__}). "
                                "Try saving it again.") from e
        mio.check_triangles(pts[tris].astype(np.float32))
        return True, len(tris), float(np.linalg.norm(pts.max(0) - pts.min(0)))
    try:
        tris = mio.read_stl(str(path))
    except (mio.MeshError, OSError, MemoryError):
        raise
    except Exception as e:
        faults.caught(e)
        raise mio.MeshError(f"This file seems to be damaged, so it couldn't be read ({type(e).__name__}). "
                            "Try exporting it again.") from e
    mio.check_triangles(tris)
    pts = tris.reshape(-1, 3)
    pts = pts[np.isfinite(pts).all(axis=1)]
    return True, len(tris), float(np.linalg.norm(pts.max(0) - pts.min(0)))


def _write_stl(path, V, F):
    """A binary STL of (vertices, triangles)."""
    T = V[F].astype(np.float32)
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    rec = np.zeros(len(F), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    rec["n"], rec["v"] = n, T
    with open(path, "wb") as fh:
        fh.write(b"Mesh Mix-S2C".ljust(80, b" "))
        fh.write(np.uint32(len(F)).tobytes())
        fh.write(rec.tobytes())


class _PartReporter(Reporter):
    """The reporter for one part of a 3MF project split into parts (_convert_parts): its
    stages and progress go to the parent's, squeezed into the part's share [lo, hi] and
    named after the part; its result is kept, not sent (the parent sends a "part" event)."""

    def __init__(self, parent, lo, hi, name):
        super().__init__(parent.json_mode, parent.quiet, parent.stream)
        self.t0, self.parent, self.lo, self.hi, self.name, self.res = parent.t0, parent, lo, hi, name, None

    def _emit(self, obj):
        if obj.get("event") == "result":
            self.res = obj
            return
        if "progress" in obj:
            obj["progress"] = self.lo + (self.hi - self.lo) * obj["progress"]
        if obj.get("event") == "stage":
            obj["stage"] = f"{self.name}: {obj['stage']}"
        Reporter._emit(self.parent, obj)


def _convert_parts(path, out, quality_, report, preset, base):
    """A 3MF project split into parts: every part the app lists (MMM's reader, with the
    app's layout, overlap and touching-parts settings, so the parts and their numbers are the
    ones the app shows and names files by) converted on its own through the merged engine,
    one STEP each, as MMM's pipeline does for a split project (pipeline._convert_split).
    Each part goes in as MMM's mended mesh of it. None when the project has one part (the
    whole-plate path converts it)."""
    report = report or NULL
    t0 = time.time()
    report.stage("Reading the 3MF project", 0.0, 0.05)
    bodies, info = mio.load_and_prepare(path, repair=base.repair, remove_debris=base.remove_debris, report=NULL,
                                        layout=base.layout, merge=base.merge_overlaps, touching=base.touching_parts)
    if info.parts < 2:
        return None
    report.log(f"Split into {info.parts} parts: each is converted on its own with the merged engine")
    tmp = tempfile.mkdtemp(prefix="mms2c_parts_")
    used = set(o for o in base.part_outputs if o)
    results = []
    try:
        for p, name in enumerate(info.part_names):
            shells = [sh for b, bp in zip(bodies, info.body_part) if bp == p for sh in b]
            if not shells:
                continue
            offsets = np.cumsum([0] + [len(sh.V) for sh in shells])
            V = np.vstack([sh.V for sh in shells])
            tris = np.vstack([sh.F + o for sh, o in zip(shells, offsets)])
            src = os.path.join(tmp, f"part{p + 1}.stl")
            _write_stl(src, V, tris)
            dest = (base.part_outputs[p] if p < len(base.part_outputs) and base.part_outputs[p]
                    else mpipe._part_output_path(str(out), name, used))
            sub = mpipe.Options(**base.__dict__)
            sub.split, sub.part_outputs, sub.part_previews = False, [], []
            sub.preview = base.part_previews[p] if p < len(base.part_previews) and base.part_previews[p] else None
            sub.product_name = name
            lo, hi = 0.05 + 0.95 * p / info.parts, 0.05 + 0.95 * (p + 1) / info.parts
            rep = _PartReporter(report, lo, hi, name)
            t_part = time.time()
            try:
                got = convert_file(src, dest, quality_, rep, preset=preset, base=sub) or rep.res or {}
            except Exception as e:
                faults.caught(e)
                report.warn(f"The part “{name}” couldn't be converted and was left out ({type(e).__name__}: {e}).")
                continue
            r = {k: got.get(k) for k in ("preset", "presets", "valid", "solids", "shells", "faces", "edges", "volume",
                                         "mesh_volume", "file_size", "fidelity", "checked", "check_ok", "checks",
                                         "repaired")}
            r.update({"index": len(results), "part": p, "name": name, "output": dest, "preview": sub.preview,
                      "seconds": round(time.time() - t_part, 2),
                      "triangles": info.part_triangles[p] if p < len(info.part_triangles) else len(tris)})
            r["presets"] = r["presets"] or ([r["preset"]] if r["preset"] else [])
            report.part(r)
            results.append(r)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not results:
        raise mpipe.ConversionError("None of the parts could be converted.")
    presets = [r["preset"] for r in results]
    res = {
        "output": results[0]["output"],
        "outputs": [r["output"] for r in results],
        "split": True,
        "parts": results,
        "preset": presets[0] if all(x == presets[0] for x in presets) else "mixed",
        "presets": sorted({x for r in results for x in r["presets"]}),
        "valid": all(r["valid"] is not False for r in results) if base.validate else None,
        "seconds": round(time.time() - t0, 2),
        "input": info.as_dict(),
        "mesh_check": info.mesh_check,
        # (as pipeline._convert_split sums them up: the apps show these for the whole project)
        "checked": all(r.get("checked") for r in results),
        "check_ok": all(r.get("check_ok") for r in results),
        "checks": [{**i, "message": f"{r['name']}: {i.get('message', '')}"} for r in results for i in r.get("checks") or []],
        "repaired": mpipe.repair_steps(results),
        "fidelity": mpipe.fidelity_summary([r["fidelity"] for r in results if r.get("fidelity")]),
    }
    for k in ("solids", "shells", "faces", "edges", "volume", "mesh_volume", "file_size"):
        res[k] = sum(r[k] or 0 for r in results)
    report.result(res)
    return res


def _convert_other(path, out, quality_, report, preset, base=None):
    """An OBJ, PLY or OFF file through the merged engine for Auto and CAD (Tuna's choice,
    2026-10-31: the new engine for every mesh): its triangles, as MMM's reader reads them,
    are saved as an STL and converted as one. (The app does the same, and also sends a 3MF
    split into parts through it part by part: Mesh-Mix-S2C engine/mms2c/merged.py.)"""
    tmp = tempfile.mkdtemp(prefix="mms2c_mesh_")
    try:
        T = mio.read_any(str(path))
        src = os.path.join(tmp, os.path.splitext(os.path.basename(str(path)))[0] + ".stl")
        _write_stl(src, T.reshape(-1, 3), np.arange(3 * len(T)).reshape(-1, 3))
        sub = mpipe.Options(**(base.__dict__ if base is not None else {}))
        sub.product_name = sub.product_name or os.path.splitext(os.path.basename(str(path)))[0]
        return convert_file(src, out, quality_, report, preset=preset, base=sub)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def convert_file(path, out, quality_=None, report=None, preset="auto", base=None):
    """Convert one mesh file with the merged engine into the STEP file out: MMM's result
    dict (faces, validity, how closely it follows the mesh...). preset: "auto" (the
    method chosen per body, see _Route), "cad" (a mechanical part: our features and CAD
    faces for every body), "smooth" (an artistic model: smooth surfaces, no feature
    finding). The retired "precise" and "exact" get Auto, with a note (pipeline.RETIRED_PRESETS).
    base: the settings the engine's command line was given (mmm.pipeline.Options: unit, tolerance,
    repair, preview, a 3MF split into parts...), which the merged pipeline keeps."""
    report = report or Reporter()
    faults.reset()
    meshguard.start()
    if base is not None and base.split and str(path).lower().endswith(PLATE_FORMATS):
        res = _convert_parts(path, out, quality_, report, preset, base)
        if res is not None:
            return res
    if quality_ is not None:
        mpipe.Options(quality=quality_)     # (refuses NaN and out of range: OptionError)
    if str(path).lower().endswith(OTHER_FORMATS) and preset in ("auto", "cad") + mpipe.RETIRED_PRESETS:
        return _convert_other(path, out, quality_, report, preset, base)
    stl, n, diag = _entrance(path, out)
    if stl and not FRONT_SIZES[0] <= diag <= FRONT_SIZES[1]:
        # (MMM's mesh check then gives its own note on the unit)
        report.warn(f"This model is {diag:.3g} units across, outside the sizes feature finding "
                    "works at, so it is converted without it")
        stl = False
    elif stl and not UNIT_NOTE[0] <= diag <= UNIT_NOTE[1]:
        report.warn(f"The model is {diag:,.3g} units across. If it was made in "
                    + ("inches or meters" if diag < UNIT_NOTE[0] else "a smaller unit than millimeters")
                    + ", change Units in the settings.")
    q = quality_ if quality_ is not None else quality(n)
    opts = mpipe.Options(**(base.__dict__ if base is not None else {}))
    opts.preset, opts.quality, opts.judge_floor, opts.fewest_faces = preset, q, JUDGE_FLOOR, True
    try:
        return mpipe.convert(str(path), str(out), opts, report, front=front if stl else None)
    finally:
        workers.finish()


_job = None     # the job object of _end_with_me (its handle must live as long as the process)


def _end_with_me():
    """Put this process in a Windows job object that ends every process in it when its
    last handle closes, which is when this process ends, however it ends: ended from
    outside (the task manager, a test's time limit), the merged engine left its worker
    pools running (25 processes after a 10 GB conversion, with the rivals of the time).
    Children join the job as they start. Returns the handle, kept for the process's life
    (None if it couldn't be made, or off Windows). From Mesh-Mix-S2C's engine/mmm_engine.py
    (2026-10-26), where every engine process starts this way."""
    if os.name != "nt":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class Extended(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                        ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))  # extended limits
        if not ok or not k32.AssignProcessToJobObject(job, k32.GetCurrentProcess()):
            return None
        return job
    except Exception as fault:
        faults.caught(fault)
        return None


EXIT_DONE = 0           # saved, sound, and it follows the mesh
EXIT_FAILED = 1         # no file: the message says why (mmm.cli.failure)
EXIT_POOR = 3           # saved, but not sound or valid, or it doesn't follow the mesh
EXIT_FAULT = 4          # saved, but a bug in the engine got in the way (the result's "code_errors")


def exit_code(res):
    """The exit code for a saved result: a script or a benchmark that reads only this must not
    take a file that isn't sound for a success. (2 is argparse's, for a wrong command line.)"""
    if res.get("code_errors"):
        return EXIT_FAULT
    fit = res.get("fidelity") or {}
    if res.get("valid") is False or res.get("sound") is False or fit.get("ok") is False:
        return EXIT_POOR
    return EXIT_DONE


def main(argv=None):
    import argparse
    import json
    import os
    import sys
    ap = argparse.ArgumentParser(prog="stl2curves (merged)", description=__doc__.strip().splitlines()[0])
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--quality", type=float, help="MMM's quality, 0-1 (default: by triangle count)")
    ap.add_argument("--json", action="store_true", help="progress as JSON lines on stdout")
    ap.add_argument("--preset", choices=("auto", "cad", "smooth", "precise"), default="auto",
                    help="cad: a mechanical part; smooth: an artistic model; auto (default): chosen for each "
                         "body by its shape; precise: retired, converts with auto")
    a = ap.parse_args(argv)
    out = a.output or os.path.splitext(a.input)[0] + ".step"
    report = Reporter(json_mode=a.json)
    global _job
    if argv is None:            # (the command line, not a script or a test calling main)
        _job = _end_with_me()
    try:
        res = convert_file(a.input, out, a.quality, report, a.preset)
    except Exception as e:  # not engine work: the top, where a failure is reported
        # (the same messages as MMM's command line, and a JSON error event with --json)
        from mmm.cli import failure
        return failure(e, a.input, report)
    if not a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "input"}, indent=1, default=str))
    return exit_code(res)


if __name__ == "__main__":
    raise SystemExit(main())
