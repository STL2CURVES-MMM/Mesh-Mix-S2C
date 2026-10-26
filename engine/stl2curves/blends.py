"""
Smooth blends: curved areas that no cylinder, cone, sphere or torus explains.

Where fillets meet at a corner, or a fillet follows a spline-shaped edge, the mesh
has a dense patch of small facets that detection either leaves flat or covers with
small mismatched pieces. Each such area becomes one smooth freeform face instead:
an N-sided patch spanning the area's outline (shared exactly with its neighbours) and
passing through its mesh corners.
"""
import copy
import math
from collections import deque

import numpy as np

from . import extrude
from . import freeform
from . import pipes
from .accuracy import scale
ACCURACY = scale("blends")
from .features import Feature, Revolved, Sphere, _least_pair_dot, _right_vectors

SMOOTH_DEG = 50       # facets bending less than this meet smoothly
SPREAD_DEG = 40       # one blend face turns at most this far from its first facet and its mean direction
TANGENT_DEG = 35      # a neighbour this close in direction is rolled into tangentially
MIN_BEND_DEG = 2      # an unrecognised facet must turn at least this far against a neighbour
MAX_FACETS = 60       # a bigger smooth area is covered by several blends (each a manageable fit)
GROW_EXACT_FACETS = 40   # a cylinder fitted to a blend's corners takes in neighbouring patches up to this size
PARALLEL_ROLLED = 8     # pairs of walls a fillet may roll between: this many go to the workers
PARALLEL_EXACT = 8      # blends checked for a cylinder through their corners: this many go to the workers
BETWEEN_WALL_FACETS = 10 # a cylinder or cone of this many facets is a wall a fillet can roll along ...
BETWEEN_PIECE_FACETS = 400   # ... between it and another, over pieces of up to this many facets touching both ...
BETWEEN_TOL = 0.01       # mm: ... a facet with every corner this near the rolling ball is the fillet's ...
BETWEEN_MIN_FACETS = 6   # ... at least this many of them (and half of the pieces' facets)
CORNER_SIZE = 1.5     # mm: a blend no bigger across than this is a corner...
CORNER_SPREAD_DEG = 85  # ...and may turn this far
MAX_SPLITS = 2        # a blend whose face won't fit is cut in two, at most this many times over
SMALL_FACET = 0.02    # unrecognised facets smaller than this share of the biggest can be blends
MAX_DEVIATION = 0.02 * ACCURACY  # a blend face must pass this close to every mesh corner (mm)
MAX_BULGE = 0.15      # ...and bow away from a facet by at most this share of its size
MAX_EDGE_GAP = 0.01 * ACCURACY   # ...and follow its outline edges this closely (mm)
CREASE_DEG = 20       # a freeform area (freeform.py) doesn't reach across a sharper bend than this
FREEFORM_TURN_DEG = 3  # ...and turns at least this far from its mean direction somewhere
FREEFORM_MIN_RADIUS = 2.0   # mm: a piece rounder than this (a small rounded edge) stays out of one
FREEFORM_SPLITS = 4   # an area no surface fits is halved by facet direction at most this many times over
STRIP_DEG = 45        # an exact patch turning less than this beside freeform pieces may join them
FREEFORM_MIN_SIZE = 5.0     # mm across: a smaller area of MAX_FACETS or fewer is left to the blends
GROW_COS = math.cos(math.radians(10))   # a facet a freeform piece takes in faces within 10 deg of it
CARVE_MIN = 200       # facets: a freeform patch this big blamed for trouble loses just its facets near it
TRANSITION_REACH = 3.0  # radii: a piece reaching no further than this many of its radii may be part of a transition
TRANSITION_MIN_FILLETS = 2   # ...which runs between at least this many fillets (cylinders, tori, pipes)
TRANSITION_RADII = 2.0       # ...its pieces' radii within this factor of theirs ...
TRANSITION_ODD = 0.2         # ... all but this share of their facets
TRANSITION_SLIVER = 2.0      # radii: no loose facet of one runs further than this
TRANSITION_SPECK = 8         # facets: a piece this small may be part of one whatever its radius
ISLAND_FACETS = 4     # facets: a group no patch has, closed in by one patch and on it, is the patch's


class Blend:
    """Stand-in model for a blend: the surface itself is fitted when the face is built."""
    kind = "blend"
    line = circle = None

    def __init__(self, normal):
        self.normal_hint = normal


def _bend(mesh, a, b):
    return math.degrees(math.acos(float(np.clip(mesh.fn[a] @ mesh.fn[b], -1, 1))))


def _radius(model, P=None):
    """The surface's radius (a cone's: its mean radius at the points P)."""
    if isinstance(model, Sphere):
        return model.r
    if isinstance(model, Revolved):
        if model.line:
            if model.line[1] == 0:
                return model.line[0]
            return float(model.local(P)[0].mean()) if P is not None else None
        return model.circle[2]
    return None


def _scrap(mesh, f, any_count=False):
    """A small piece standing in for part of a surface it doesn't really fit: few facets
    (or any number, any_count: on a finely meshed part a narrow strip has dozens),
    turning through a small angle, small for its radius."""
    r = _radius(f.model, mesh.fcent[f.facets])
    if r is None or f.kind in ("ball", "wedge") or (len(f.facets) >= 40 and not any_count) or f.__dict__.get("vouched"):
        return False
    N = mesh.fn[f.facets]
    turn = math.degrees(math.acos(float(np.clip(_least_pair_dot(N, math.cos(math.radians(45))), -1, 1))))
    return turn < 45 and float(mesh.farea[f.facets].sum()) < r * r


def _ball_chains(mesh, features):
    """Sphere pieces of one radius touching one another but centred in different places:
    a rolling-ball fillet whose path isn't a circle (each short stretch fits a ball of the
    fillet radius, the balls' centres wander along the path). A real ball corner is one
    sphere, so three or more such pieces are all stand-ins."""
    balls = [k for k, f in enumerate(features) if isinstance(f.model, Sphere) and f.kind != "ball"]
    verts = {k: set(np.concatenate([mesh.fverts[x] for x in features[k].facets]).tolist()) for k in balls}
    link = {k: [] for k in balls}
    for i, a in enumerate(balls):
        for b in balls[i + 1:]:
            ma, mb = features[a].model, features[b].model
            if (abs(ma.r - mb.r) <= 0.05 * ma.r and np.linalg.norm(ma.c - mb.c) > 0.05 * ma.r
                    and verts[a] & verts[b]):
                link[a].append(b)
                link[b].append(a)
    out, seen = set(), set()
    for k in balls:
        if k in seen:
            continue
        group, stack = [], [k]
        seen.add(k)
        while stack:
            a = stack.pop()
            group.append(a)
            for b in link[a]:
                if b not in seen:
                    seen.add(b)
                    stack.append(b)
        if len(group) >= 3:
            out.update(group)
    return out


def add_blends(mesh, features):
    """Features with small mismatched pieces and unrecognised curved facets regrouped into
    smooth blends. Each blend remembers the pieces it replaced (`parts`), so they can be
    put back if its face can't be built."""
    # (first the pieces of fillets along curved edges joined into pipes: they are pieces
    # of tori, cylinders and spheres the passes below would take for scraps)
    features = pipes.add_pipes(mesh, features)
    features = _transitions(mesh, features)
    nf = len(mesh.farea)
    owner = np.full(nf, -1)
    for k, f in enumerate(features):
        owner[f.facets] = k
    scraps = {k for k, f in enumerate(features) if _scrap(mesh, f)} | _ball_chains(mesh, features)
    small = SMALL_FACET * mesh.farea.max()

    def candidate(a):
        if owner[a] >= 0:
            return owner[a] in scraps
        # a facet that barely turns against any neighbour belongs to a flat face, not a blend
        bends = [_bend(mesh, a, b) for b in mesh.nbrs[a]]
        return mesh.farea[a] <= small and any(MIN_BEND_DEG <= x < SMOOTH_DEG for x in bends)

    cand = np.array([candidate(a) for a in range(nf)])

    # a unit: an unrecognised facet, or a whole piece (so a piece can be put back whole)
    def unit(a):
        return [int(x) for x in features[owner[a]].facets] if owner[a] >= 0 else [int(a)]

    free = set(np.nonzero(cand)[0].tolist())
    out, dropped = [], set()
    # Big smooth areas first, each as one freeform surface if one fits; the rest (and
    # any that don't fit) as smooth blends of up to MAX_FACETS facets. (A freeform area
    # also takes small facets that bend only slightly against their neighbours: on a
    # finely meshed surface every bend is slight.)
    stand_ins = scraps | {k for k, f in enumerate(features) if _scrap(mesh, f, any_count=True)}
    stand_ins = {k for k in stand_ins
                 if (_radius(features[k].model, mesh.fcent[features[k].facets]) or 0) >= FREEFORM_MIN_RADIUS}
    soft = {a for a in range(nf) if (owner[a] in stand_ins if owner[a] >= 0
                                     else mesh.farea[a] <= small and mesh.nbrs[a])}
    # (profiles pushed along a design direction first: a freeform patch would take
    # their strips too, but as a surface that isn't one)
    ext, ext_parts, ext_taken = extrude.add_extrusions(mesh, features, soft, owner, unit)
    out += ext
    dropped.update(ext_parts)
    soft -= ext_taken
    free -= ext_taken
    pieces =[p for area in _areas(mesh, soft, unit) for p in _freeform_pieces(mesh, area, unit)]
    pieces = _absorb_strips(mesh, features, owner, pieces, ext_parts)
    pieces = _grown(mesh, pieces, soft, unit)
    for facets, model, dev in pieces:
        parts = sorted({int(owner[a]) for a in facets if owner[a] >= 0})
        out.append(freeform.feature(mesh, model, facets, tuple(features[k] for k in parts), dev))
        dropped.update(parts)
        free.difference_update(facets.tolist())
    for facets, parts in _regions(mesh, free, unit, lambda a: owner[a]):
        out.append(_blend(mesh, facets, tuple(features[k] for k in parts)))
        dropped.update(parts)
    # (a blend's area may be one cylinder after all, cut into a few big slanting
    # triangles that no guess from their normals found: see features.corner_surface)
    found = iter(_exact_all(mesh, [f for f in out if f.kind == "blend"]))
    out = [(next(found) or f) if f.kind == "blend" else f for f in out]
    out = _grow_exact(mesh, out)
    kept = [f for k, f in enumerate(features) if k not in dropped] + out
    return _islands(mesh, _between_walls(mesh, kept))


def _islands(mesh, features):
    """The features with each island taken in by the patch round it: a group of at most
    ISLAND_FACETS facets no patch has, every edge of it shared with one patch, lying on
    that patch's surface (corners and middles as near as its own facets), or for a
    blend, needle-thin or meeting it smoothly. Such a hole in a patch broke its face:
    a blend takes one outline only, and a cut face had a needle 0.004 mm wide to fit
    inside it (Rak N-USB: 47 islands, mostly needles left out of curved patches because
    a needle's facing means nothing; four freeform pieces and two fillets with one
    failed, and their blends after them). Facing isn't asked."""
    from .features import _tol
    owner = np.full(len(mesh.farea), -1)
    for k, f in enumerate(features):
        owner[f.facets] = k
    beside = {}
    for (a, b), fs in mesh.edge_facets.items():
        for x in fs:
            beside.setdefault(x, []).extend((y, (a, b)) for y in fs if y != x)
    grown, seen = {}, set()
    for g0 in np.nonzero(owner < 0)[0].tolist():
        if g0 in seen:
            continue
        group, stack, around = [], [g0], set()
        seen.add(g0)
        while stack and len(group) <= ISLAND_FACETS:
            g = stack.pop()
            group.append(g)
            for h, _ in beside.get(g, ()):
                if owner[h] >= 0:
                    around.add(int(owner[h]))
                elif h not in seen:
                    seen.add(h)
                    stack.append(h)
        if stack or len(group) > ISLAND_FACETS or len(around) != 1:
            continue
        k = around.pop()
        f = features[k]
        if f.kind == "blend":
            T = mesh.pts[mesh.tris[np.concatenate([mesh.ftris[g] for g in group])]]
            long = np.linalg.norm(T - T[:, [1, 2, 0]], axis=2).max(axis=1)
            height = 2 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1) / np.maximum(long, 1e-12)
            smooth = all(_bend(mesh, g, h) < TANGENT_DEG for g in group for h, _ in beside.get(g, ()) if owner[h] == k)
            if not (smooth or (height <= MAX_DEVIATION).all()):
                continue
        else:
            V = np.unique(np.concatenate([mesh.fverts[g] for g in group]))
            near = max(f.worst, _tol(f.model))
            if (np.abs(f.model.signed(mesh.pts[V])).max() > near
                    or np.abs(f.model.signed(mesh.fcent[group])).max() > near):
                continue
        grown.setdefault(k, []).extend(group)
    out = list(features)
    for k, extra in grown.items():
        f = copy.copy(out[k])
        f.__dict__.pop("_across", None)
        f.facets = np.r_[f.facets, extra]
        out[k] = f
    return out


def _transitions(mesh, features):
    """The features with each transition between fillets made one freeform patch.

    Where fillets of different radii meet (a box's r 2 bottom edges and r 1.5 upright
    edge at a corner), CAD fills the corner with a surface of its own, swept by a ball
    whose path bends off the fillets' and whose radius changes along it. Detection cuts
    it into narrow slices that each fit a small sphere, cylinder or torus centred
    somewhere else, plus facets nothing explains: a box corner came out as seven r 1.5
    sphere slices whose centres drift 0.04 mm apart, a dozen cylinders tilted every way
    and a torus, and was built as fifteen faces and some triangles. The whole area,
    from where each fillet stops fitting, is one smooth surface (a P-spline height field
    over the plane facing its mean direction fits such a corner within 0.003 mm).

    A transition: a smooth area (no crease) of small pieces (Revolved or Sphere,
    reaching no further than TRANSITION_REACH radii: a fillet runs on further) and
    loose curved facets, holding two or more pieces no one surface of theirs fits all
    of, and bordered by TRANSITION_MIN_FILLETS other curved patches or more (the fillets
    it joins). Its pieces are remembered, as freeform patches', and given back if its
    face can't be built."""
    nf = len(mesh.farea)
    owner = np.full(nf, -1)
    for k, f in enumerate(features):
        owner[f.facets] = k
    small = SMALL_FACET * mesh.farea.max()

    def minor(k):
        f = features[k]
        if f.kind not in ("trimmed", "revolve") or f.__dict__.get("vouched") or f.parts:
            return False
        if not isinstance(f.model, (Revolved, Sphere)):
            return False
        r = _radius(f.model, mesh.fcent[f.facets])
        if not r or r > pipes.MAX_RADIUS:
            return False
        if len(f.facets) <= TRANSITION_SPECK:
            return True         # (a speck: its radius, fitted to a sliver or two, means nothing)
        P = mesh.pts[np.unique(np.concatenate([mesh.fverts[a] for a in f.facets]))]
        return float(np.linalg.norm(np.ptp(P, axis=0))) <= TRANSITION_REACH * r

    pieces = {k for k in range(len(features)) if minor(k)}

    def fillet_radius(k):
        """A fillet's radius (a cylinder's, a torus's tube's, a pipe's), else None."""
        m = features[k].model
        r = m.r if m.kind == "pipe" else pipes.tube_radius(m) if isinstance(m, Revolved) else None
        return r if r and r <= pipes.MAX_RADIUS else None

    def loose(a):
        # (a small facet nothing took, however little it bends against its neighbours:
        # on a finely meshed corner patch two neighbouring triangles can be coplanar
        # enough to be one facet, and were left as holes in it)
        return owner[a] < 0 and mesh.farea[a] <= small and bool(mesh.nbrs[a])

    def unit(a):
        return [int(x) for x in features[owner[a]].facets] if owner[a] >= 0 else [int(a)]

    member = np.zeros(nf, bool)
    for k in pieces:
        member[features[k].facets] = True
    for a in np.nonzero(owner < 0)[0]:
        if loose(a):
            member[a] = True
    limit = math.cos(math.radians(CREASE_DEG))
    seen = np.zeros(nf, bool)
    out, used = [], set()
    for seed in np.nonzero(member & (owner >= 0))[0]:
        if seen[seed]:
            continue
        area = unit(seed)
        seen[area] = True
        stack = list(area)
        while stack:
            a = stack.pop()
            for b in mesh.nbrs[a]:
                if seen[b] or not member[b] or mesh.fn[a] @ mesh.fn[b] < limit:
                    continue
                u = unit(b)
                seen[u] = True
                area += u
                stack += u
        facets = np.array(sorted(set(area)))
        parts = sorted({int(owner[a]) for a in facets if owner[a] >= 0})
        if len(parts) < 2:
            continue
        # (between real fillets: long ones, of a radius each, and the pieces of about
        # their radii, the area no bigger across than a few of them. On a freeform
        # part, specks of scrap strips 0.07 to 1.2 mm in radius and flat stretches of
        # spline with stray tori on them met every other test, and went the same way)
        border = {int(owner[b]) for a in facets for b in mesh.nbrs[a] if owner[b] >= 0 and not member[b]}
        radii = [r for r in (fillet_radius(k) for k in border) if r]
        if len(radii) < TRANSITION_MIN_FILLETS:
            continue
        lo, hi = min(radii), max(radii)
        # (no loose facet longer than a couple of the fillets' radii: a CAD mesh cuts a
        # straight fillet into slivers running its whole length, and a corner patch
        # spanning one of those bowed 0.35 mm off the part between its corners; then
        # the stretch of what is left holding the most of the pieces)
        long = {int(a) for a in facets if owner[a] < 0
                and np.ptp(mesh.pts[mesh.fverts[a]], axis=0).max() > TRANSITION_SLIVER * hi}
        if long:
            groups = _connected_units(mesh, [int(a) for a in facets if int(a) not in long], unit)
            if not groups:
                continue
            facets = max(groups, key=lambda g: (sum(owner[a] >= 0 for a in g), len(g)))
            parts = sorted({int(owner[a]) for a in facets if owner[a] >= 0})
            if len(parts) < 2:
                continue
        # (most of the pieces, by facets: specks of junk radius don't count)
        weight = [len(features[k].facets) for k in parts]
        odd = [len(features[k].facets) for k in parts
               if not lo / TRANSITION_RADII <= (_radius(features[k].model, mesh.fcent[features[k].facets]) or 0)
               <= hi * TRANSITION_RADII]
        if sum(odd) > TRANSITION_ODD * sum(weight):
            continue
        P = mesh.pts[np.unique(np.concatenate([mesh.fverts[a] for a in facets]))]
        size = float(np.linalg.norm(np.ptp(P, axis=0)))
        if not lo <= size <= TRANSITION_REACH * 2 * hi:
            continue
        if any(np.abs(features[k].model.signed(P)).max() <= freeform.FIT_DEV for k in parts):
            continue        # (one surface after all: pieces of it, not a transition)
        # (or one cylinder or cone through every corner: specks and loose rows of a
        # straight fillet, which came out as freeform patches 0.0001 mm off a cylinder)
        from .features import corner_surface
        try:
            exact = corner_surface(mesh, facets)
        except Exception:
            exact = None
        if exact is not None:
            exact.corner_fit = True
            exact.parts = tuple(features[k] for k in parts)     # (put back if its face can't be built)
            out.append(exact)
            used.update(parts)
            continue
        model, dev = freeform.fit(mesh, facets)
        if model is None:
            continue
        out.append(freeform.feature(mesh, model, facets, tuple(features[k] for k in parts), dev))
        used.update(parts)
    return [f for k, f in enumerate(features) if k not in used] + out


def _connected_units(mesh, facets, unit):
    """The facets in groups joined by shared edges (a piece's facets all in one)."""
    left, groups = set(facets), []
    while left:
        group = set(unit(next(iter(left)))) & left
        left -= group
        stack = list(group)
        while stack:
            a = stack.pop()
            for b in mesh.nbrs[a]:
                if b in left:
                    u = set(unit(b)) & left
                    left -= u
                    group |= u
                    stack += list(u)
        groups.append(np.array(sorted(group)))
    return groups


def _exact(mesh, blend):
    from .features import corner_surface
    try:
        found = corner_surface(mesh, blend.facets)
    except Exception:
        return None
    if found is not None:
        found.corner_fit = True
        found.parts = tuple(blend.parts)   # (put back if its face can't be built)
    return found


def _exact_all(mesh, blends):
    """_exact for each of the blends, in the worker processes when there are enough:
    each depends only on its own facets, and one after another the toolbox's 222 took a
    minute (twelve least-squares fits each)."""
    from . import workers
    from . import features as F
    pool = workers.get() if len(blends) >= PARALLEL_EXACT else None
    if pool is not None:
        try:
            if "shared_as" not in mesh.__dict__:
                mesh.shared_as = workers.share(mesh)
            jobs = [pool.submit(_exact_job, (mesh.shared_as, f.facets, F._mesh_tol)) for f in blends]
            out = []
            for blend, job in zip(blends, jobs):
                found = job.result()
                if found is not None:
                    found.corner_fit = True
                    found.parts = tuple(blend.parts)   # (put back if its face can't be built)
                out.append(found)
            return out
        except Exception:
            workers.broken()            # (then done here)
    return [_exact(mesh, f) for f in blends]


def _exact_job(job):
    """In a worker: features.corner_surface for one blend's facets (see _exact_all)."""
    from . import workers
    from . import features as F
    mesh_key, facets, tol = job
    mesh = workers.load(mesh_key)
    saved, F._mesh_tol = F._mesh_tol, tol
    try:
        return F.corner_surface(mesh, facets)
    except Exception:
        return None
    finally:
        F._mesh_tol = saved


def _grow_exact(mesh, out):
    """Cylinders found from a blend's corners (_exact) taking in the small blends and
    freeform patches beside them that lie on them too, refitted together (a coarse
    slope cut into several patches, each a few big facets)."""
    verts = [set(np.concatenate([mesh.fverts[a] for a in f.facets]).tolist()) for f in out]
    alive = [True] * len(out)
    for i, f in enumerate(out):
        if not f.__dict__.get("corner_fit"):
            continue
        grown = True
        while grown:
            grown = False
            for j, g in enumerate(out):
                if (j == i or not alive[j] or len(g.facets) > GROW_EXACT_FACETS
                        or not (g.kind == "blend" or g.model.kind == "freeform") or not verts[i] & verts[j]):
                    continue
                from .features import corner_surface
                try:
                    trial = corner_surface(mesh, np.r_[out[i].facets, g.facets])
                except Exception:
                    trial = None
                if trial is not None:
                    trial.corner_fit = True
                    trial.parts = tuple(out[i].parts) + tuple(g.parts)
                    out[i] = trial
                    verts[i] |= verts[j]
                    alive[j] = False
                    grown = True
    return [f for f, keep in zip(out, alive) if keep]


def _onto_spine(P, walls, targets, steps=20):
    """The points P moved onto the curve where each wall's signed distance is its
    target (least moves: Gauss-Newton on the two distances). A target may be one value
    per point, so many radii are tried in one go (_rolled)."""
    C = np.array(P, float)
    T = [np.broadcast_to(np.asarray(t, float), (len(C),)) for t in targets]
    live = np.arange(len(C))
    for _ in range(steps):
        if not len(live):
            break
        X = C[live]
        f = np.stack([w.signed(X) - t[live] for w, t in zip(walls, T)], axis=1)
        J = np.stack([w.normal(X) for w in walls], axis=1)
        move = np.einsum("nij,ni->nj", J, _solve2(J, f))
        C[live] = X - move
        # (each point stops once settled: Gauss-Newton gets there in a few steps; always
        # 20 cost 70 s on a big part)
        live = live[np.abs(move).max(axis=1) >= 1e-10]
    return C


def _solve2(J, f):
    """Per point, pinv(J J^T) f for the 2 x 2 matrix J J^T: in closed form, and by numpy's
    pinv only where the two walls' normals are (nearly) parallel (pinv on every point was
    most of the time _between_walls took: 367 s on the toolbox)."""
    a = np.einsum("ni,ni->n", J[:, 0], J[:, 0])
    b = np.einsum("ni,ni->n", J[:, 0], J[:, 1])
    c = np.einsum("ni,ni->n", J[:, 1], J[:, 1])
    det = a * c - b * b
    ok = det > 1e-9 * np.maximum(a * c, 1e-300)
    d = np.where(ok, det, 1.0)
    out = np.stack([(c * f[:, 0] - b * f[:, 1]) / d, (a * f[:, 1] - b * f[:, 0]) / d], axis=1)
    if not ok.all():
        bad = ~ok
        out[bad] = np.einsum("nij,nj->ni", np.linalg.pinv(np.einsum("nij,nkj->nik", J[bad], J[bad])), f[bad])
    return out


def _between_walls(mesh, features):
    """Fillets rolled between two walls found already (cylinders and cones), built as
    pipes round the curve a ball of the fillet's radius rolls along touching both: each
    of its centres lies the radius off both walls. Detection leaves such a fillet as
    pieces (tori, spheres, freeform strips): a ball's path between a cylinder and a
    slanting one is neither a line nor a circle (the gutter mount's slope, r 21, meets
    an r 6 cylinder each side with a 2 mm fillet). The radius is the one the corners
    fit best; facets of the pieces off that ball stay as they were (blends)."""
    walls = [k for k, f in enumerate(features)
             if isinstance(f.model, Revolved) and f.model.line and len(f.facets) >= BETWEEN_WALL_FACETS]
    if len(walls) < 2:
        return features
    wall_set = set(walls)
    # (each piece listed under every pair of walls it touches both of, worked out from
    # the walls round its corners: going through every feature for every pair of walls
    # took 150 s on a part with 200 walls and 1,200 features)
    wall_of = {}
    for A in walls:
        for v in np.unique(np.concatenate([mesh.fverts[a] for a in features[A].facets])).tolist():
            wall_of.setdefault(v, []).append(A)
    order = {A: i for i, A in enumerate(walls)}
    by_pair = {}
    for k, f in enumerate(features):
        if k in wall_set or f.kind in ("ball", "wedge") or f.model.kind == "thread" or len(f.facets) > BETWEEN_PIECE_FACETS:
            continue
        near = sorted({A for v in np.unique(np.concatenate([mesh.fverts[a] for a in f.facets])).tolist()
                       for A in wall_of.get(v, ())}, key=order.get)
        for i, A in enumerate(near):
            for B in near[i + 1:]:
                by_pair.setdefault((A, B), []).append(k)
    # (every pair worked out ahead in the worker processes, from all its pieces; going
    # through them in order here, a pair whose pieces an earlier one took is worked out
    # again here: the same answers as one after another, which took the toolbox 35 s)
    pairs = [((A, B), by_pair[A, B]) for i, A in enumerate(walls) for B in walls[i + 1:] if (A, B) in by_pair]
    ahead = _rolled_ahead(mesh, features, pairs)
    used = set()
    out = []
    for pair, all_pieces in pairs:
        pieces = [k for k in all_pieces if k not in used]
        if not pieces:
            continue
        got = ahead.get(pair) if pieces == all_pieces else None
        if got is not None:
            got = got[1]
            if got is not None:
                got[0].parts = tuple(features[k] for k in pieces)   # (this process's own pieces)
        else:
            got = _rolled(mesh, features, pair, pieces)
        if got is None:
            continue
        pipe, rest = got
        used.update(pieces)
        out.append(pipe)
        out += rest
    return [f for k, f in enumerate(features) if k not in used] + out


def _rolled_ahead(mesh, features, pairs):
    """{pair: (True, what _rolled gives)} for the pairs of walls with all their pieces,
    worked out in the worker processes (empty without them, or for a few pairs)."""
    from . import workers
    from . import features as F
    pool = workers.get() if len(pairs) >= PARALLEL_ROLLED else None
    if pool is None:
        return {}
    try:
        if "shared_as" not in mesh.__dict__:
            mesh.shared_as = workers.share(mesh)
        jobs = {pair: pool.submit(_rolled_job, (mesh.shared_as, {k: features[k] for k in pair + tuple(pieces)},
                                                pair, pieces, F._mesh_tol))
                for pair, pieces in pairs}
        return {pair: (True, job.result()) for pair, job in jobs.items()}
    except Exception:
        workers.broken()                # (then each worked out here)
        return {}


def _rolled_job(job):
    """In a worker: _rolled for one pair of walls (see _rolled_ahead)."""
    from . import workers
    from . import features as F
    mesh_key, feats, pair, pieces, tol = job
    mesh = workers.load(mesh_key)
    saved, F._mesh_tol = F._mesh_tol, tol
    try:
        return _rolled(mesh, feats, pair, pieces)
    finally:
        F._mesh_tol = saved


def _rolled(mesh, features, pair, pieces):
    """(pipe, blends for the pieces' facets off it) for a fillet between two walls, or None."""
    walls = [features[k].model for k in pair]
    fac = np.concatenate([features[k].facets for k in pieces])
    V = np.unique(np.concatenate([mesh.fverts[a] for a in fac]))
    P = mesh.pts[V]
    # (each wall's air side: where its facets face)
    air = [float(np.sign(np.einsum("ij,ij->i", mesh.fn[features[k].facets],
                                   features[k].model.normal(mesh.fcent[features[k].facets])).mean())) for k in pair]

    def misfit(r, side):
        Q = _onto_spine(P, walls, [side * air[0] * r, side * air[1] * r])
        return np.abs(np.linalg.norm(P - Q, axis=1) - r)

    def scores(rs, side):
        """The median misfit at each radius of rs, all projected at once (one radius at
        a time, the case back spent 47 s here)."""
        R = np.repeat(rs, len(P))
        Ps = np.tile(P, (len(rs), 1))
        Q = _onto_spine(Ps, walls, [side * air[0] * R, side * air[1] * R])
        return np.median(np.abs(np.linalg.norm(Ps - Q, axis=1) - R).reshape(len(rs), len(P)), axis=1)
    best = None
    grid = np.geomspace(0.2, pipes.MAX_RADIUS, 60)
    for side in (1, -1):        # (the centre in the air: a fillet in a corner; in the material: a rounded edge)
        for r, score in zip(grid, scores(grid, side)):
            if best is None or score < best[0]:
                best = (float(score), r, side)
    _, r, side = best
    fine = np.linspace(0.9 * r, 1.1 * r, 41)
    for rr, score in zip(fine, scores(fine, side)):
        if score < best[0]:
            best = (float(score), rr, side)
    _, r, side = best
    off = dict(zip(V.tolist(), misfit(r, side)))
    on = np.array([max(off[v] for v in mesh.fverts[a]) <= BETWEEN_TOL for a in fac])
    if on.sum() < max(BETWEEN_MIN_FACETS, 0.5 * len(fac)):
        return None
    targets = [side * air[0] * r, side * air[1] * r]
    model, dev = pipes.fit(mesh, fac[on], r, lambda ids: _onto_spine(mesh.pts[ids], walls, targets))
    if model is None:
        return None
    pipe = pipes.feature(mesh, model, fac[on], tuple(features[k] for k in pieces), dev)
    rest = [_blend(mesh, list(g), ()) for g, _ in _regions(mesh, set(fac[~on].tolist()), lambda a: [a], lambda a: -1)]
    return pipe, rest


def merge(mesh, features, facets):
    """(features, merged): an area someone painted (stl2curves.studio: one the conversion
    left as triangles, a fillet it missed) made one face. A feature they cover half or
    more of goes into it whole, one they barely touch stays as it was, and flat facets
    the brush caught (none of whose neighbours meet it at a gentle bend) stay flat. The
    area is one cylinder, cone or sphere if it all lies on one, else one freeform surface
    if one fits, else one smooth blend (cut in two by the build if no fill fits); either
    way it gives back the features it took if its face can't be built. merged is None if
    there is nothing to merge."""
    from .features import Region, _whole_region
    chosen = {int(a) for a in facets if len(mesh.nbrs[int(a)])}
    keep, taken = [], []
    for f in features:
        inside = sum(int(a) in chosen for a in f.facets)
        if inside and 2 * inside >= len(f.facets):
            taken.append(f)
        else:
            keep.append(f)
            chosen.difference_update(int(a) for a in f.facets)
    for f in taken:
        chosen.update(int(a) for a in f.facets)
    if len(chosen) < 2:
        return features, None
    area = np.array(sorted(chosen))
    merged = None
    try:
        best = _whole_region(mesh, Region(mesh, area))
    except Exception:
        best = None
    if best is not None and len(best[1]) == len(area):
        merged = best[0]
        merged.parts = tuple(taken)
    if merged is None:
        model, dev = freeform.fit(mesh, area)
        merged = (freeform.feature(mesh, model, area, tuple(taken), dev) if model is not None
                  else _blend(mesh, area, tuple(taken)))
    merged.detail = "painted: " + merged.detail
    return keep + [merged], merged


def fallback(mesh, feature):
    """Smooth blends of up to MAX_FACETS facets over a freeform patch's area (for when its
    face can't be built), the pieces it replaced kept whole."""
    owner = np.full(len(mesh.farea), -1)
    for k, p in enumerate(feature.parts):
        owner[p.facets] = k

    def unit(a):
        return [int(x) for x in feature.parts[owner[a]].facets] if owner[a] >= 0 else [int(a)]

    free = set(int(a) for a in feature.facets)
    regions = _regions(mesh, free, unit, lambda a: owner[a])
    used = {k for _, parts in regions for k in parts}
    # (pieces no blend took, and the facets left over, stay as they were)
    return ([_blend(mesh, facets, tuple(feature.parts[k] for k in parts)) for facets, parts in regions]
            + [p for k, p in enumerate(feature.parts) if k not in used])


def pipe_fallback(mesh, feature):
    """The pieces a pipe joined, as they were, and smooth blends of up to MAX_FACETS
    facets over the loose facets it took in besides (for when its face can't be built)."""
    taken = {int(a) for p in feature.parts for a in p.facets}
    free = {int(a) for a in feature.facets} - taken
    regions = _regions(mesh, free, lambda a: [a], lambda a: -1)
    return list(feature.parts) + [_blend(mesh, facets, ()) for facets, _ in regions]


def _freeform_pieces(mesh, facets, unit, depth=0):
    """[(facets, surface, deviation)]: the area as one freeform patch if a surface fits
    it, else its two halves by facet direction (a U-shaped area turns too far for one
    height field; a crease runs between faces facing different ways), each in turn.
    Pieces too small for a freeform patch are left to the blends."""
    if len(facets) < 3 or _turn(mesh, facets) < FREEFORM_TURN_DEG:
        return []
    if len(facets) <= MAX_FACETS:
        # (a few long facets can span a big area too)
        P = mesh.pts[np.unique(np.concatenate([mesh.fverts[a] for a in facets]))]
        if np.linalg.norm(np.ptp(P, axis=0)) < FREEFORM_MIN_SIZE:
            return []
    model, dev = freeform.fit(mesh, facets)
    if model is not None:
        return [(facets, model, dev)]
    if depth >= FREEFORM_SPLITS:
        return []
    # (split square to the direction the facets' normals vary most, through their mean)
    N, w = mesh.fn[facets], mesh.farea[facets]
    mean = N.T @ w / w.sum()
    X = N - mean
    axis = np.linalg.eigh((X * w[:, None]).T @ X)[1][:, -1]
    side = dict(zip(facets.tolist(), (X @ axis > 0).tolist()))
    halves = {True: set(), False: set()}
    for a in facets.tolist():
        if a not in halves[True] and a not in halves[False]:
            u = unit(a)         # (a piece goes whole to the side most of it is on)
            halves[sum(side[x] for x in u) * 2 > len(u)].update(u)
    out = []
    for members in halves.values():
        for piece in _areas(mesh, members, unit):
            out += _freeform_pieces(mesh, piece, unit, depth + 1)
    return out


def _absorb_strips(mesh, features, owner, pieces, skip=()):
    """Freeform pieces with the narrow exact patches beside them taken in, where one
    surface fits both: a curvature-continuous fillet (a spline in the original) fits a
    cylinder exactly along a narrow band in its middle, with freeform pieces either side;
    it is one face. (Only patches turning less than STRIP_DEG, met smoothly.)"""
    if not pieces:
        return pieces
    piece_of = np.full(len(mesh.farea), -1)
    for i, (facets, _, _) in enumerate(pieces):
        piece_of[facets] = i
    pieces = list(pieces)
    limit = math.cos(math.radians(CREASE_DEG))
    for k, f in enumerate(features):
        m = f.model
        if k in skip:
            continue
        if f.kind not in ("revolve", "trimmed") or not isinstance(m, (Revolved, Sphere))                 or _turn(mesh, f.facets) * 2 >= STRIP_DEG or (piece_of[f.facets] >= 0).any():
            continue
        # the pieces it meets smoothly
        touching = {int(piece_of[b]) for a in f.facets for b in mesh.nbrs[a]
                    if piece_of[b] >= 0 and pieces[piece_of[b]] is not None and mesh.fn[a] @ mesh.fn[b] >= limit}
        if not touching:
            continue
        union = np.unique(np.concatenate([f.facets] + [pieces[i][0] for i in touching]))
        model, dev = freeform.fit(mesh, union)
        if model is None:
            continue
        for i in touching:
            pieces[i] = None
        pieces.append((union, model, dev))
        piece_of[union] = len(pieces) - 1
    return [p for p in pieces if p is not None]


def _grown(mesh, pieces, soft, unit):
    """Freeform pieces with the loose facets beside them taken in where those already lie
    on the piece's surface: an area halved by direction leaves fringes too small to fit
    on their own, and a row of triangles a hundredth of a millimetre wide left between
    two faces has edges shorter than any sewing tolerance."""
    taken = set()
    for facets, _, _ in pieces:
        taken.update(facets.tolist())
    out = []
    for facets, model, dev in pieces:
        members = set(facets.tolist())
        stack = list(members)
        while stack:
            a = stack.pop()
            for b in mesh.nbrs[a]:
                if b in members or b in taken or b not in soft:
                    continue
                u = unit(b)
                if any(x in taken or x not in soft for x in u):
                    continue
                P = mesh.pts[np.unique(np.concatenate([mesh.fverts[x] for x in u]))]
                if np.abs(model.signed(P)).max() > freeform.FIT_DEV or                         (np.einsum("ij,ij->i", mesh.fn[u], model.normal(mesh.fcent[u])) ** 2).min() < GROW_COS ** 2:
                    continue
                members.update(u)
                taken.update(u)
                stack += u
        out.append((np.array(sorted(members)), model, dev))
    return out


def _turn(mesh, facets):
    """How far (degrees) the facets turn from their mean direction at most."""
    n = mesh.fn[facets].T @ mesh.farea[facets]
    if np.linalg.norm(n) < 1e-9 * mesh.farea[facets].sum():
        return 180.0            # (facing every way: a whole ring, say)
    n = n / np.linalg.norm(n)
    return math.degrees(math.acos(float(np.clip((mesh.fn[facets] @ n).min(), -1, 1))))


def carve(mesh, feature, points, reach):
    """A big freeform patch blamed for trouble at these points (a gap at its outline,
    say) less its facets within reach of them, which are left flat, and less any piece
    it took in that reaches them, given back whole: [the patch, the pieces...]; None if
    the patch is small or would lose half of itself (then its blends are better)."""
    if points is None or not len(points) or len(feature.facets) < CARVE_MIN:
        return None
    from scipy.spatial import cKDTree
    facets = feature.facets
    size = np.array([np.linalg.norm(mesh.pts[mesh.fverts[a]] - mesh.fcent[a], axis=1).max() for a in facets])
    d, _ = cKDTree(np.asarray(points, float)).query(mesh.fcent[facets])
    cut = set(facets[d <= reach + size].tolist())
    back = [p for p in feature.parts if cut & set(p.facets.tolist())]
    for p in back:
        cut.update(p.facets.tolist())
    keep = np.array([a for a in facets.tolist() if a not in cut])
    if not cut or len(keep) < len(facets) / 2:
        return None
    parts = tuple(p for p in feature.parts if all(p is not b for b in back))
    return [freeform.feature(mesh, feature.model, keep, parts, feature.model.dev)] + back


def _areas(mesh, free, unit):
    """Smooth areas of the free facets (whole units), not reaching across a crease."""
    out, seen = [], set()
    limit = math.cos(math.radians(CREASE_DEG))
    for seed in sorted(free):
        if seed in seen:
            continue
        area = unit(seed)
        if any(x not in free for x in area):
            continue
        seen.update(area)
        stack = list(area)
        while stack:
            a = stack.pop()
            for b in mesh.nbrs[a]:
                if b in seen or b not in free or mesh.fn[a] @ mesh.fn[b] < limit:
                    continue
                u = unit(b)
                if any(x not in free or x in seen for x in u):
                    continue
                seen.update(u)
                area += u
                stack += u
        out.append(np.array(sorted(area)))
    return out


def _regions(mesh, free, unit, owner_of):
    """Blend regions over the free facets: [(facets, indices of the pieces in it)]."""
    # Regions grow a unit at a time. Every facet must stay within SPREAD_DEG of the
    # region's first facet and of its mean direction: no further than one smooth face
    # can follow. A region with no curve in it (flat facets only) stays as it is.
    def angle(n, m):
        return math.degrees(math.acos(float(np.clip(n @ m, -1, 1))))

    blends = []
    free = set(free)
    while free:
        seed_unit = unit(max(free, key=lambda a: mesh.farea[a]))
        seed_n = mesh.fn[seed_unit].T @ mesh.farea[seed_unit]
        seed_n = seed_n / np.linalg.norm(seed_n)
        region, normal = list(seed_unit), seed_n * mesh.farea[seed_unit].sum()
        stack = deque(seed_unit)        # breadth first: compact regions
        free.difference_update(seed_unit)
        while stack:
            a = stack.popleft()
            for b in mesh.nbrs[a]:
                if b not in free or _bend(mesh, a, b) >= SMOOTH_DEG:
                    continue
                u = unit(b)
                if any(x not in free for x in u) or len(region) + len(u) > MAX_FACETS:
                    continue
                mean = normal / np.linalg.norm(normal)
                turn = max(max(angle(mesh.fn[x], seed_n), angle(mesh.fn[x], mean)) for x in u)
                if turn > SPREAD_DEG:
                    # a small corner (where rounded edges meet) may turn further: one
                    # N-sided patch spans it, bounded by the exact faces around it
                    if turn > CORNER_SPREAD_DEG:
                        continue
                    P = mesh.pts[np.concatenate([mesh.fverts[x] for x in region + u])]
                    if np.linalg.norm(np.ptp(P, axis=0)) > CORNER_SIZE:
                        continue
                free.difference_update(u)
                region += u
                stack += u
                normal = normal + mesh.fn[u].T @ mesh.farea[u]
        members = set(region)
        inner_bends = [_bend(mesh, a, b) for a in region for b in mesh.nbrs[a] if b in members]
        if len(region) < 3 or max(inner_bends, default=0) < 0.5:
            continue
        parts = sorted({int(owner_of(a)) for a in region if owner_of(a) >= 0})
        blends.append((np.array(sorted(region)), parts))
    return blends


def _blend(mesh, facets, parts, depth=0):
    N = mesh.fn[facets]
    turn = math.degrees(math.acos(float(np.clip((N @ N.T).min(), -1, 1))))
    normal = N.T @ mesh.farea[facets]
    area = float(mesh.farea[facets].sum())
    return Feature(model=Blend(normal / np.linalg.norm(normal)), label="smooth blend",
                   detail=f"{len(facets)} facets, turns {turn:.0f} deg", convex=True, kind="blend",
                   facets=np.asarray(facets), change=0.0, tolerance=0.02 * area, worst=0.0,
                   parts=parts, depth=depth)


def split(mesh, blend):
    """A blend cut in two across its longest direction (each piece it replaced going
    whole to the half holding most of it), or None if it can't be cut any further."""
    if blend.depth >= MAX_SPLITS or len(blend.facets) < 8:
        return None
    C = mesh.fcent[blend.facets]
    axis = _right_vectors(C - C.mean(axis=0))[0]
    t = (C - C.mean(axis=0)) @ axis
    cut = np.median(t)
    side = {int(f): bool(x > cut) for f, x in zip(blend.facets, t)}
    halves = [[], []], [[], []]          # (facets, parts) for each side
    in_part = set()
    for p in blend.parts:
        votes = [side[int(f)] for f in p.facets if int(f) in side]
        h = int(sum(votes) * 2 > len(votes))
        halves[h][0].extend(int(f) for f in p.facets)
        halves[h][1].append(p)
        in_part.update(int(f) for f in p.facets)
    for f in blend.facets:
        if int(f) not in in_part:
            halves[int(side[int(f)])][0].append(int(f))
    out = []
    for facets, parts in halves:
        if len(facets) < 3:
            return None
        out.append(_blend(mesh, np.array(sorted(facets)), tuple(parts), blend.depth + 1))
    return out
