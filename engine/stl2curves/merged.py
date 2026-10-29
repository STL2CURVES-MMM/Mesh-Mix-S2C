"""
The merged engine: stl2curves and Manifold Mesh Mix (MMM, the mmm package, AGPL-3.0) as one
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

Meanwhile stl2curves alone and MMM alone convert the same file in processes of their
own, and the best of the three finished results is saved (rivals.py).
"""
import math
import os
import shutil
import tempfile
import time

import numpy as np

from mmm import cad as mcad
from mmm import fitting as mfit
from mmm import meshio as mio
from mmm import pipeline as mpipe
from mmm.report import NULL, Reporter

from . import build, convert, rivals, stages, workers
from . import features as F

# the app's Auto quality by triangle count (MMM desktop/mmm_app/settings.py)
AUTO_QUALITY_STEPS = ((50_000, 0.9), (300_000, 0.8), (1_000_000, 0.7), (3_000_000, 0.6))
SPINDLE = 1.0   # a torus whose centre circle is no bigger than this times its tube stays ours
# stl2curves alone and MMM alone convert the file alongside, as rivals (rivals.py);
# STL2CURVES_RIVALS=0 runs the merged pipeline on its own (diagnostics, quick tests)
RIVALS = os.environ.get("STL2CURVES_RIVALS") != "0"
# our smooth blends, freeform patches and pipes as seeds too (add_blends: on the cam and
# tube MMM test models most of the front end's time); STL2CURVES_FRONT_BLENDS=0 seeds with
# the exact features only, and our own conversion alongside fits the blends itself.
# Measured 2026-10-25 (28-part sample, no rivals): without them 10,483 faces instead of
# 7,934 (+32%; test13 106 -> 407, the hook 154 -> 316) for 5% less time: keep them
FRONT_BLENDS = os.environ.get("STL2CURVES_FRONT_BLENDS") != "0"


class OurSurface:
    """One of our surface models as an MMM region surface ("freeform" to MMM: it builds
    the face on geom() and measures with distance()/normal()). normal() follows the OCC
    surface's own orientation, as MMM's freeform cells do, so a region's sense says which
    way the face is turned."""

    kind = "freeform"
    rank = 5
    locked = True
    dev = 0.0

    def __init__(self, model, geom, flip):
        self.model, self._geom, self.flip = model, geom, flip

    def distance(self, P):
        return np.abs(self.model.signed(np.atleast_2d(P)))

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
        prim.locked = True
        return prim
    geom = _occ_surface(feature, mesh)
    if geom is None:
        return None
    return OurSurface(m, geom, _orientation(m, geom, mesh, feature))


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
    except Exception:
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
            if future.exception() is None:
                build._surface_from(m, p)
    except Exception:
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
    of our prepared parts it came from, alone: whether it is all of that part."""

    def __init__(self):
        self.shells, self.seeds = [], []
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


def bodies_from(parts, groups, pts):
    """MMM bodies from our prepared parts: (bodies, seeds) where seeds[k] lists, for
    body k's shells, the locked regions our features give each."""
    bodies = []
    biggest = max(_area(pts, g) for g in groups)
    for gi, ((mesh, features, _), g) in enumerate(zip(parts, groups)):
        # (our Mesh drops zero-area triangles: theirs map back through this)
        a, b, c = (pts[g[:, k]] for k in range(3))
        kept = np.nonzero(np.linalg.norm(np.cross(b - a, c - a), axis=1) > 1e-12)[0]
        if len(kept) != len(mesh.tris) or not np.array_equal(g[kept], mesh.tris):
            raise RuntimeError("our mesh's triangles don't map onto the body's")
        pieces = _shells_of(pts, g)
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
        made = _nest([p[0] for p in pieces], seeds)
        made = [b for b in made if not _debris(b.shells[0], biggest)]
        for b in made:
            # (our own build of this part is a candidate for the body only when the
            # part is that one body)
            b.part, b.alone = gi, len(made) == 1
        bodies += made
    return bodies


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
    out = []
    for i, b in bodies.items():
        # (the outer shell first)
        k = b.shells.index(shells[i])
        b.shells.insert(0, b.shells.pop(k))
        b.seeds.insert(0, b.seeds.pop(k))
        out.append(b)
    return out


def seeded_segmenter(shell, seeds, tol, quality, report=NULL):
    """MMM's Segmenter for one shell, our regions in first (locked, grown into the
    neighbouring triangles that fit them by MMM's own test), then MMM's run over the rest."""
    seg = mcad.Segmenter(shell.V, shell.F, shell.topo, tol, report)
    seg.detail = quality
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


WRECKED = 0.02      # our repair moving the enclosed volume more than this share: try MMM's


def _signed_volume(pts, tris):
    a, b, c = (pts[tris[:, k]] for k in range(3))
    return float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)


def _mended(path, opts, report):
    """Our prepared parts, on our mended mesh, or on MMM's where ours moved the enclosed
    volume by more than WRECKED and MMM's moved it less (a cap of thin walls: ours split
    4,825 of its 5,196 triangles away as slivers, -19% volume; MMM's kept it whole).
    Returns (parts, info, part, ours): ours, whether they are on our own mended mesh
    (saved as stages, which our own conversion alongside takes up again)."""
    parts, info, part = convert.prepare(path, 0.01, blends=FRONT_BLENDS)
    pts0, tris0 = convert.load_stl(path)
    raw = abs(_signed_volume(pts0, tris0))
    mine = abs(sum(_signed_volume(p[0].pts, p[0].body_tris) for p in parts))
    if not raw or abs(mine - raw) <= WRECKED * raw:
        return parts, info, part, True
    theirs_bodies, _ = mio.load_and_prepare(path, repair=opts.repair, remove_debris=opts.remove_debris, report=NULL)
    shells = [s for b in theirs_bodies for s in b]
    theirs = abs(sum(s.volume for s in shells))
    if abs(theirs - raw) >= abs(mine - raw):
        return parts, info, part, True
    report.log(f"stl2curves' repair moved the enclosed volume {100 * (mine - raw) / raw:+.1f}%, MMM's "
               f"{100 * (theirs - raw) / raw:+.1f}%: MMM's mended mesh is used, stl2curves' features found on it")
    offsets = np.cumsum([0] + [len(s.V) for s in shells])
    pts = np.vstack([s.V for s in shells])
    tris = np.vstack([s.F + o for s, o in zip(shells, offsets)])
    parts, info2, part = convert.prepare((pts, tris), 0.01, mend=False, blends=FRONT_BLENDS)
    info2["triangles"] = info["triangles"]
    info2["repairs"] = ["MMM's repair (stl2curves' moved the volume too far)"]
    return parts, info2, part, False


def front(path, opts, report):
    """stl2curves' first stages for MMM's pipeline.convert: (bodies, info, seeded,
    extra). seeded[k](tol) gives body k's ("cad", {id(shell): segmenter}); a body
    missing from it (an organic body with nothing of ours in it) gets MMM's own choice.
    extra[k]: more candidates for body k (our own sewn build)."""
    if str(path).lower().endswith(".3mf"):
        # (3MF projects: MMM's own reader and segmentation, for now)
        bodies, info = mio.load_and_prepare(path, repair=opts.repair, remove_debris=opts.remove_debris,
                                            report=report, layout=opts.layout, merge=opts.merge_overlaps,
                                            touching=opts.touching_parts)
        return bodies, info, {}, {}
    report.stage("Reading and mending the mesh (stl2curves)", 0.0, 0.3)
    parts, info, part, parts_on_file = _mended(path, opts, report)
    groups = [p[0].__dict__.get("body_tris") for p in parts]
    if any(g is None for g in groups):
        raise RuntimeError("prepare() didn't keep each body's triangles")
    pts = parts[0][0].pts
    report.stage("Handing the features over (stl2curves -> MMM)", 0.3, 0.35)
    _fit_surfaces(parts)
    ours = bodies_from(parts, groups, pts)
    bodies = [b.shells for b in ours]
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
    report.log("stl2curves mended the mesh: " + ("; ".join(info["repairs"]) if info.get("repairs") else "nothing to mend"))

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

    # Our own conversion, faces cut and sewn, runs alongside in a process of its own from
    # here (see _OurRun); our worker processes here aren't needed any more, it has its own.
    # They are stopped, not kept for a next part as stl2curves keeps them: each holds a copy
    # of the mesh, and three merged conversions at once, each with this pool and our own
    # run's, took 38 GB in 89 processes on medium meshes
    run = _OurRun(path) if parts_on_file and any(p[1] for p in parts) and getattr(opts, "rivals", RIVALS) else None
    workers.stop()

    def sewn(k):
        """Body k from our own conversion, as one more candidate for MMM's judge: tried
        first when the merged faces don't pass. A part of several bodies takes, for each,
        the solid matching its outer shell."""
        body = ours[k]

        def make():
            shape = run.shape(give_up=opts.enough)
            if shape is None:
                return None
            return shape if len(ours) == 1 else _nearest_solid(shape, body.shells[0])
        return [("stl2curves", "Building stl2curves' own (sewn) faces", make)]

    made = {k: seeded(k) for k in range(len(bodies))}
    extra = {k: sewn(k) for k in range(len(bodies)) if run is not None and parts[ours[k].part][1]}
    return bodies, minfo, {k: m for k, m in made.items() if m is not None}, extra


_running = []   # our conversions started alongside (stopped when the file is done)


class _OurRun:
    """stl2curves' own conversion of the file (the MIT pipeline: faces cut and sewn,
    culprits dropped) in a process of its own, started once our stages are saved: it
    takes them up again (STL2CURVES_CACHE) and goes straight to building, on the cores
    MMM's single-threaded stages leave free, so its result is usually there by the time
    MMM's judge asks for it (Rak N-USB waited 110 s for it run afterwards). Also a rival
    to the merged result for the whole file (rivals.py)."""
    name = "stl2curves"

    def __init__(self, path):
        import subprocess
        import tempfile
        self.dir = tempfile.mkdtemp(prefix="stl2curves_ours_")
        self.out = os.path.join(self.dir, os.path.splitext(os.path.basename(str(path)))[0] + ".step")
        env = dict(os.environ, STL2CURVES_CACHE=stages.FOLDER)
        self.proc = rivals.popen(["-m", "stl2curves", str(path), "--out", self.dir], env=env,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._shape = False
        _running.append(self)

    def shape(self, give_up=None):
        """The finished conversion's shape, waiting for it (None if it failed, or if
        give_up() turns true first: the run itself goes on, as a rival)."""
        if self._shape is False:
            while self.proc.poll() is None:
                if give_up is not None and give_up():
                    return None
                time.sleep(0.5)
            self._shape = None
            if self.proc.returncode == 0 and os.path.exists(self.out):
                from mmm import stepio
                try:
                    self._shape = stepio.read_step(self.out)
                except Exception:
                    self._shape = None
        return self._shape

    def stop(self):
        import shutil
        rivals.kill(self.proc)
        shutil.rmtree(self.dir, ignore_errors=True)


BOX_MATCH = 0.05    # a solid stands for a body when its box is within this share of the body's size


def _nearest_solid(shape, shell):
    """The solid of shape whose box matches the shell's (None if none does: our build
    can fuse a small body into the one it touches, and the whole part judged against
    the small body's mesh took 14.7 GB and ran for over half an hour)."""
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.TopAbs import TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    lo, hi = shell.V.min(0), shell.V.max(0)
    best, gap = None, np.inf
    ex = TopExp_Explorer(shape, TopAbs_SOLID)
    while ex.More():
        s = ex.Current()
        ex.Next()
        box = Bnd_Box()
        BRepBndLib.Add_s(s, box)
        d = np.abs(np.array(box.CornerMin().Coord()) - lo).sum() + np.abs(np.array(box.CornerMax().Coord()) - hi).sum()
        if d < gap:
            best, gap = s, d
    return best if gap <= BOX_MATCH * float(np.linalg.norm(hi - lo)) * 6 else None


def _rivals_fit(base):
    """Whether the rivals can make what these settings ask for: they convert in
    millimetres at the engine's own tolerance with no face limit, so a file asked for in
    inches, at a tolerance or with a face limit is made by the merged pipeline alone."""
    return base is None or (base.unit == "mm" and not base.tolerance and not base.max_faces)


def convert_file(path, out, quality_=None, report=None, base=None):
    """Convert one mesh file with the merged engine into the STEP file out: MMM's result
    dict (faces, validity, how closely it follows the mesh...). base: the settings the
    engine's command line was given (mmm.pipeline.Options: unit, tolerance, repair,
    preview...), which the merged pipeline keeps."""
    report = report or Reporter()
    t0 = time.time()
    stl = str(path).lower().endswith(".stl")
    n = convert.load_stl(path)[1].shape[0] if stl else 0
    q = quality_ if quality_ is not None else quality(n)
    use = stl and RIVALS and _rivals_fit(base)
    # MMM's engine alone from the start, and our own conversion once our first stages are
    # saved (see _OurRun): rivals to the merged result (see rivals.py). (3MF projects go
    # to MMM's own reader unseeded, so MMM alone would only repeat them.)
    if use:
        _running.append(rivals.MMMAlone(path, q))
    judges = rivals.Judges(path, _running)
    opts = mpipe.Options(**(base.__dict__ if base is not None else {}))
    opts.preset = base.preset if base is not None and base.preset == "cad" else "auto"
    opts.quality = q
    opts.judge_floor, opts.fewest_faces, opts.enough = rivals.JUDGE_FLOOR, True, judges.passing
    opts.smooth_elsewhere = opts.rivals = use
    own_cache = not stages.FOLDER
    if own_cache:
        stages.FOLDER = tempfile.mkdtemp(prefix="stl2curves_stages_")
    try:
        try:
            try:
                with judges.run_lock:
                    judges.running = True
                res = mpipe.convert(str(path), str(out), opts, report, front=front)
            finally:
                with judges.run_lock:
                    judges.running = False
        except rivals.Overtime:
            # (a rival passed and the merged pipeline ran past its deadline: rivals.DEADLINE)
            report.log("The merged pipeline was stopped: a conversion alongside already follows the mesh, "
                       "and it had run past its deadline")
            res = None
            workers.stop()      # (its worker jobs may still be running)
        if _running:
            res = rivals.settle(out, res, judges, time.time() - t0, report)
            res["seconds"] = round(time.time() - t0, 2)
            if res.get("kept", "merged") != "merged" and opts.preview:
                _preview_again(out, opts.preview, report)
            report.result(res)
        return res
    finally:
        judges.close()
        while _running:
            _running.pop().stop()
        workers.finish()
        if own_cache:
            shutil.rmtree(stages.FOLDER, ignore_errors=True)
            stages.FOLDER = None


def _preview_again(step, preview, report):
    """The app's preview of the file a rival made (the merged pipeline's preview shows its
    own result)."""
    from mmm import stepio
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    try:
        shape = stepio.read_step(step)
        box = Bnd_Box()
        BRepBndLib.Add_s(shape, box)
        diag = float(np.linalg.norm(np.subtract(box.CornerMax().Coord(), box.CornerMin().Coord())))
        stepio.export_preview(shape, preview, diag)
    except Exception as e:
        report.warn(f"The 3D preview couldn't be made ({e}); the STEP file itself is fine.")


def main(argv=None):
    import argparse
    import json
    import os
    ap = argparse.ArgumentParser(prog="stl2curves (merged)", description=__doc__.strip().splitlines()[0])
    ap.add_argument("input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--quality", type=float, help="MMM's quality, 0-1 (default: by triangle count)")
    ap.add_argument("--json", action="store_true", help="progress as JSON lines on stdout")
    a = ap.parse_args(argv)
    out = a.output or os.path.splitext(a.input)[0] + ".step"
    report = Reporter(json_mode=a.json)
    res = convert_file(a.input, out, a.quality, report)
    if not a.json:
        print(json.dumps({k: v for k, v in res.items() if k != "input"}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
