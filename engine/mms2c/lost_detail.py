"""Design detail a STEP lost against its mesh: the lost_detail fields of the judge's card
(mms2c/judge.py). Closeness alone misses it. A cone through a hexagon's corners passes
every test at the mesh's corners, yet six flat bevels are gone; and no freeform at all isn't
automatically good, since a smooth blended corner forced into many small exact pieces is worse
than one freeform face. Nothing here feeds verdict(): that is the owners' call.

Two measures, each read from the mesh first and only then compared with the file.

Lost design surfaces. What the designer made shows in the mesh without any help: a CAD export
puts every corner exactly on the true surface, so
- a sizeable group of connected triangles on one plane within FLAT_TOL is a designed plane,
  unless it reads as one facet of something curved (bent smoothly into a neighbour of like size);
- a run of at least FAN_STRIPS flat strips bending smoothly into each other, all corners on one
  cylinder or cone within FAN_TOL and most far closer (FAN_EXACT), not running on past its fit
  (_runs_on), is a designed cylinder or cone. Fans are found first, so a flat land beside a
  fillet stops the fan and stays a plane.
A smooth blend whose corners fit no such surface is not a design surface, so a B-spline over it
loses nothing. Points spread by area over each surface go to the nearest face of the read-back
solid's tessellation; a face matches if it is the right kind and the surface's corners lie on
its true (analytic) surface within ON_TOL, so a cone through a hexagon's corners fails on kind
and a wrong radius on distance. A surface is kept when COVER of its points lie on matching
faces; otherwise the share that doesn't counts as lost, in two buckets: gone to a B-spline, or
gone to a wrong surface (merged across a crease, swallowed, or left as facets). More than one
matching face may cover a surface, because CAD systems cut whole cylinders along seams; a
surface cut too finely shows under shredding instead.

Shredding. The surfaces above are taken out of the mesh, and what remains is split at creases
(a bend over SMOOTH[1]) into smooth blend regions. Now each small STEP face is asked where it
lies: its points go to the nearest mesh triangle, and the face belongs to the region (or design
surface) holding most of them. Faces the mesh itself vouches for don't count: an analytic face
with enough corners under it to over-determine it (EVIDENCE beyond its parameters), all on its
surface within TIGHT_TOL. Those are designed faces, however fine; a piece forced onto a blend
has few corners or misses them. Only small pieces count (under SMALL of the region's area, and not wrapping
round, _wraps): a few large faces, or a thread's turns, are a fair division. A blend region with n >= SHRED_BLEND such pieces, or a design surface
cut into n >= SHRED_SPLIT of them, scores their area * (1 - 1/n), so many pieces approach their
whole area, and the card sums that against the mesh's area, beside the count of excess faces,
which a small shredded corner on a big part would otherwise hide.

Sizes are capped for big meshes, in order of area so what's dropped matters least, and the card
says so (capped): FAN_SECONDS for the fan search, POINTS and FACE_POINTS for the distances."""
import heapq
import time

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import (GeomAbs_BezierSurface, GeomAbs_BSplineSurface, GeomAbs_Cone, GeomAbs_Cylinder,
                         GeomAbs_Plane, GeomAbs_Sphere, GeomAbs_Torus)
from OCP.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCP.gp import gp_Pnt
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

FLAT_TOL = 0.001      # mm, corners on one plane (or 1e-5 of the part's size, for float32 files' noise)
SMOOTH = (0.05, 30.0) # degrees, a bend between flat groups read as curvature (a 12-sided hole at most);
                      # above it, a crease (the Raspberry Pi case's port bevels stand 38 degrees off the wall)
FAN_TOL = 0.002       # mm, largest distance of a fan's corners from their cylinder or cone
FAN_EXACT = 1e-4      # mm, median distance: exact surfaces read under 3e-5, stretches of a thread's flank
                      # or a blend that merely fit read 2e-4 and up (four parts, 760 fans)
FAN_STRIPS = 4        # flat strips at least, so the surface's six numbers are over-determined
FAN_CORNERS = 10      # corners at least, for the same reason
MIN_AREA = 0.25       # mm^2, smallest surface or region counted (or MIN_SHARE of the mesh, if larger)
MIN_SHARE = 2e-5      # of the mesh's area
FACET_RATIO = 3.0     # a smooth neighbour within this factor of a group's area, either way, makes it read as a facet
FACET_EDGE = 0.5      # ... when at least this share of its outline bends smoothly
ON_TOL = 0.02         # mm, a surface's corners (p95) from a matching face's true surface
COVER = 0.9           # share of a surface's points on matching faces for it to count as kept
TIGHT_TOL = 0.002     # mm, mesh corners from a face's true surface for the mesh to vouch for it
EVIDENCE = 6          # corners beyond the surface's own parameter count needed to vouch
PARAMS = {"plane": 3, "sphere": 4, "cylinder": 5, "cone": 6, "torus": 7}   # numbers fixing each surface
SHRED_BLEND = 3       # unvouched faces in a blend region before it counts (two may be a fair split)
SHRED_SPLIT = 5       # faces on one design surface before it counts (seams cut cylinders in 2 to 4)
SMALL = 0.25          # a face under this share of its region's area counts as a small piece of it
WRAP = 0.2            # of a face's width across, the gap at its middle that shows it wraps round (see _wraps)
MAJORITY = 0.5        # share of a face's points on a region for it to belong there
POINTS = 20000        # points at most over the design surfaces
SURFACE_POINTS = (12, 64)  # points per surface, fewest and most
FACE_POINTS = 8       # points per STEP face (fewer, to at most 3, past POINTS in all)
FAN_SECONDS = 10.0    # seconds at most in the fan search, largest strips first
APART = 0.05          # of the mesh's size, the difference of the two boxes past which there's no comparing them
WORST = 15            # items in each worst list

PLAIN = {GeomAbs_Plane: "plane", GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone", GeomAbs_Sphere: "sphere",
         GeomAbs_Torus: "torus"}
SPLINE = (GeomAbs_BSplineSurface, GeomAbs_BezierSurface)


def flat_groups(mesh):
    """Each triangle's group, connected triangles on one plane, and each group's normal. Grown
    by corner distance from the largest triangles down, not by comparing normals: a sliver's
    normal is noise, while its corner is as good as any."""
    F = np.asarray(mesh.faces)
    V = np.asarray(mesh.vertices)
    size = float(np.linalg.norm(np.ptp(V, axis=0))) if len(V) else 1.0
    tol = max(FLAT_TOL, 1e-5 * size)
    nbr = [[] for _ in range(len(F))]
    for (f, g), (a, b) in zip(mesh.face_adjacency.tolist(), mesh.face_adjacency_unshared.tolist()):
        nbr[f].append((g, b))      # (b is g's corner off the shared edge)
        nbr[g].append((f, a))
    N = mesh.face_normals
    P = V.tolist()
    gids = [-1] * len(F)
    normals, g = [], 0
    for seed in np.argsort(-mesh.area_faces).tolist():
        if gids[seed] >= 0:
            continue
        nx, ny, nz = N[seed].tolist()
        x0, y0, z0 = P[F[seed, 0]]
        gids[seed] = g
        stack = [seed]
        while stack:
            for h, c in nbr[stack.pop()]:
                if gids[h] < 0:
                    x, y, z = P[c]
                    if abs((x - x0) * nx + (y - y0) * ny + (z - z0) * nz) <= tol:
                        gids[h] = g
                        stack.append(h)
        normals.append((nx, ny, nz))
        g += 1
    return np.array(gids), np.array(normals).reshape(-1, 3)


def group_graph(mesh, gid, normals):
    """For each flat group, its neighbours as {h: [bend in degrees, shared length]}."""
    f, g = mesh.face_adjacency.T
    a, b = gid[f], gid[g]
    keep = a != b
    e = mesh.face_adjacency_edges[keep]
    L = np.linalg.norm(mesh.vertices[e[:, 0]] - mesh.vertices[e[:, 1]], axis=1)
    a, b = a[keep], b[keep]
    bend = np.degrees(np.arccos(np.clip((normals[a] * normals[b]).sum(axis=1), -1, 1)))
    graph = [{} for _ in range(len(normals))]
    for x, y, th, l in zip(a.tolist(), b.tolist(), bend.tolist(), L.tolist()):
        for p, q in ((x, y), (y, x)):
            s = graph[p].get(q)
            if s is None:
                graph[p][q] = [th, l]
            else:
                s[1] += l
    return graph


class Revolved:
    """A cylinder or cone: radius r0 + s * h about the line p + h * a."""

    def __init__(self, p, a, r0, s):
        self.p, self.a, self.r0, self.s = p, a, r0, s

    def off(self, X):
        v = X - self.p
        h = v @ self.a
        return np.linalg.norm(v - np.outer(h, self.a), axis=1) - (self.r0 + self.s * h)


def fit_revolved(X, normals, steps=6):
    """A cylinder or cone through points, by Gauss-Newton. It starts from the facets' normals,
    which on either surface all make one angle with the axis and so lie in a plane across it,
    and from a circle fit with the radius free to change along the axis."""
    a = np.linalg.svd(normals - normals.mean(axis=0), full_matrices=False)[2][-1]
    a = a / np.linalg.norm(a)
    e1 = np.cross(a, [1.0, 0, 0] if abs(a[0]) < 0.8 else [0, 1.0, 0])
    e1 /= np.linalg.norm(e1)
    e2 = np.cross(a, e1)
    x, y, h = X @ e1, X @ e2, X @ a
    h0 = h.mean()
    h = h - h0
    M = np.column_stack((2 * x, 2 * y, np.ones(len(x)), h, h * h))
    sol = np.linalg.lstsq(M, x * x + y * y, rcond=None)[0]
    p = sol[0] * e1 + sol[1] * e2 + h0 * a
    v = X - p
    hh = v @ a
    rho = np.linalg.norm(v - np.outer(hh, a), axis=1)
    s, r0 = np.polyfit(hh, rho, 1) if np.ptp(hh) > 1e-9 else (0.0, rho.mean())
    for _ in range(steps):
        e1 = np.cross(a, [1.0, 0, 0] if abs(a[0]) < 0.8 else [0, 1.0, 0])
        e1 /= np.linalg.norm(e1)
        e2 = np.cross(a, e1)
        v = X - p
        hh = v @ a
        vp = v - np.outer(hh, a)
        rho = np.maximum(np.linalg.norm(vp, axis=1), 1e-12)
        f = rho - r0 - s * hh
        q1, q2 = vp @ e1, vp @ e2
        J = np.column_stack((-q1 / rho, -q2 / rho,                                          # the line's point
                             -hh * q1 / rho - s * (v @ e1), -hh * q2 / rho - s * (v @ e2),  # its direction
                             -np.ones(len(X)), -hh))
        d = np.linalg.lstsq(J, -f, rcond=None)[0]
        p = p + d[0] * e1 + d[1] * e2
        a = a + d[2] * e1 + d[3] * e2
        a /= np.linalg.norm(a)
        r0 += d[4]
        s += d[5]
    return Revolved(p, a, r0, s)


def find_fans(mesh, gid, normals, graph, area, seconds=FAN_SECONDS):
    """Exact cylinder and cone fans as (groups, corner ids), largest strips first, and whether
    the time ran out."""
    F = np.asarray(mesh.faces)
    V = np.asarray(mesh.vertices)
    order = np.argsort(gid, kind="stable")
    cuts = np.searchsorted(gid[order], np.arange(len(normals) + 1))
    cached = {}

    def corners(g):
        # (of its main triangles: a strip takes in any little triangle of the next surface whose
        # corner lies on its plane within FLAT_TOL, and that corner is off the true curve by the
        # strip's sag, 0.004 mm on a 6 mm fillet at 4 degrees a strip, which would end the fan there)
        c = cached.get(g)
        if c is None:
            tris = order[cuts[g]:cuts[g + 1]]
            big = mesh.area_faces[tris]
            c = cached[g] = np.unique(F[tris[big >= 0.1 * big.max()]].ravel())
        return c

    def corners_of(gs):
        return np.unique(np.concatenate([corners(g) for g in gs]))

    smooth = [[h for h, (th, _) in sorted(graph[g].items(), key=lambda kv: -kv[1][1])
               if SMOOTH[0] <= th <= SMOOTH[1] and area[h] > 1e-6] for g in range(len(normals))]
    used = np.zeros(len(normals), bool)
    tried = np.zeros(len(normals), bool)     # (in a run found to go on past its fit)
    fans = []
    t0 = time.time()
    for seed in np.argsort(-area).tolist():
        if area[seed] <= 1e-6:
            break
        if used[seed] or tried[seed] or not smooth[seed]:
            continue
        if time.time() - t0 > seconds:
            return fans, True
        # its nearest strips, across the longest shared edges first: those are the rulings, and
        # the short edges lead to whatever closes the fan's ends. Not a flat much larger than
        # the strip, either: a fillet's strips touch their planes.
        members, seen, front = [seed], {seed}, []
        for h in smooth[seed]:
            heapq.heappush(front, (-graph[seed][h][1], h))
        while len(members) < FAN_STRIPS and front:
            h = heapq.heappop(front)[1]
            if h not in seen and not used[h] and area[h] <= FACET_RATIO * area[seed]:
                seen.add(h)
                members.append(h)
                for x in smooth[h]:
                    heapq.heappush(front, (-graph[h][x][1], x))
        if len(members) < FAN_STRIPS:
            continue
        ids = corners_of(members)
        if len(ids) < FAN_STRIPS * 2:
            continue
        try:
            fit = fit_revolved(V[ids], normals[members])
        except np.linalg.LinAlgError:
            continue
        if not np.isfinite(fit.r0 + fit.s) or np.abs(fit.off(V[ids])).max() > FAN_TOL:
            continue
        # grow along every smooth edge, refitting when a strip doesn't quite lie on the fit
        # (rulings first again: the first ring of a corner sphere lies on the fillet within FAN_TOL
        # while the fit is young, and once in would spoil the refit for the fillet's own strips)
        inset = set(members)
        front = []
        for g in members:
            for h in smooth[g]:
                if h not in inset:
                    heapq.heappush(front, (-graph[g][h][1], h))
        while front:
            h = heapq.heappop(front)[1]
            if h in inset or used[h]:
                continue
            if np.abs(fit.off(V[corners(h)])).max() > FAN_TOL:
                gs = list(inset) + [h]
                ids = corners_of(gs)
                try:
                    trial = fit_revolved(V[ids], normals[gs])
                except np.linalg.LinAlgError:
                    continue
                if np.abs(trial.off(V[ids])).max() > FAN_TOL:
                    continue
                fit = trial
            inset.add(h)
            for x in smooth[h]:
                if x not in inset:
                    heapq.heappush(front, (-graph[h][x][1], x))
        # judged on its strips with real rulings, then rid of the tiny ones that only crept in
        # while the fit gave them room: a sphere's rings past a fillet's end, row after row
        members = sorted(inset)
        tried[members] = True
        rulings = {g: max((graph[g][h][1] for h in smooth[g] if h in inset), default=0.0) for g in members}
        longest = max(rulings.values())
        core = [g for g in members if rulings[g] >= 0.25 * longest]
        ids = corners_of(core)
        if len(core) < FAN_STRIPS or len(ids) < FAN_CORNERS:
            continue
        fit = fit_revolved(V[ids], normals[core])
        if np.median(np.abs(fit.off(V[ids]))) > FAN_EXACT:
            continue
        members = [g for g in members if np.abs(fit.off(V[corners(g)])).max() <= FAN_TOL]
        if not _runs_on(members, set(members), smooth, graph, area):
            used[members] = True
            fans.append((members, corners_of(members)))
    return fans, False


def _runs_on(members, inset, smooth, graph, area):
    """Does the surface go on past the fan, curving just as before, where the fit gave up? Then
    the fan is a stretch of something else that happens to fit: a screw thread's flank is a cone
    for a few degrees (120 'fans' on an M6 bolt), so is a band of a torus or a sphere between two
    of its circles, and a blend for a little way. A true cylinder ends at a crease, a flat, or
    a surface of another curvature, where the bend or the strips' size changes."""
    bends = [graph[g][h][0] for g in members for h in smooth[g] if h in inset]
    if not bends:
        return False
    usual = float(np.median(bends))
    # (the fan's usual ruling, not each strip's own longest edge: the first ring of a corner sphere
    # lies on the fillet it touches within FAN_TOL, and its tiny strips run on into the sphere)
    ruling = float(np.median([max(l for _, l in graph[g].values()) for g in members]))
    for g in members:
        for h in smooth[g]:
            if h not in inset:
                th, l = graph[g][h]
                if (l >= 0.5 * ruling and usual / 2 <= th <= usual * 2
                        and area[g] / FACET_RATIO <= area[h] <= area[g] * FACET_RATIO):
                    return True
    return False


def reads_as_facet(g, graph, area, in_fan):
    """One facet of something curved: most of its outline bends smoothly, into a neighbour of
    like size that isn't a fan's strip (a flat beside a fan is where the fan stopped). A real flat
    beside a fillet is usually many times wider than the fillet's strips anyway."""
    out = sum(l for _, l in graph[g].values())
    bent = [(h, l) for h, (th, l) in graph[g].items() if SMOOTH[0] <= th <= SMOOTH[1] and not in_fan[h]]
    if not out or sum(l for _, l in bent) < FACET_EDGE * out:
        return False
    return any(area[g] / FACET_RATIO <= area[h] <= area[g] * FACET_RATIO for h, _ in bent)


class FaceSurface:
    """A STEP face's true surface: its kind and the distance from points to it."""

    def __init__(self, face):
        self.face = face
        s = BRepAdaptor_Surface(face, True)
        t = s.GetType()
        self.spline = t in SPLINE
        self.kind = "bspline" if self.spline else PLAIN.get(t, "other")
        if self.kind == "plane":
            pl = s.Plane()
            self.p, self.a = np.array(pl.Location().Coord()), np.array(pl.Axis().Direction().Coord())
        elif self.kind == "cylinder":
            c = s.Cylinder()
            self.p, self.a = np.array(c.Axis().Location().Coord()), np.array(c.Axis().Direction().Coord())
            self.r, self.slope = c.Radius(), 0.0
        elif self.kind == "cone":
            c = s.Cone()
            self.p, self.a = np.array(c.Axis().Location().Coord()), np.array(c.Axis().Direction().Coord())
            self.r, self.slope = c.RefRadius(), float(np.tan(c.SemiAngle()))
        elif self.kind == "sphere":
            c = s.Sphere()
            self.p, self.r = np.array(c.Location().Coord()), c.Radius()
        elif self.kind == "torus":
            c = s.Torus()
            self.p, self.a = np.array(c.Axis().Location().Coord()), np.array(c.Axis().Direction().Coord())
            self.r, self.minor = c.MajorRadius(), c.MinorRadius()

    def distance(self, X):
        """Distance to the untrimmed surface. A kind with no formula is projected point by
        point, at most 32 of them (they answer for the whole set)."""
        X = np.asarray(X, float).reshape(-1, 3)
        if self.kind == "plane":
            return np.abs((X - self.p) @ self.a)
        if self.kind in ("cylinder", "cone"):
            v = X - self.p
            h = v @ self.a
            return np.abs(np.linalg.norm(v - np.outer(h, self.a), axis=1) - (self.r + self.slope * h)) \
                / np.sqrt(1 + self.slope ** 2)
        if self.kind == "sphere":
            return np.abs(np.linalg.norm(X - self.p, axis=1) - self.r)
        if self.kind == "torus":
            v = X - self.p
            h = v @ self.a
            rho = np.linalg.norm(v - np.outer(h, self.a), axis=1)
            return np.abs(np.hypot(rho - self.r, h) - self.minor)
        out = []
        pr = GeomAPI_ProjectPointOnSurf()
        u0, u1, v0, v1 = BRepTools.UVBounds_s(self.face)
        pr.Init(BRep_Tool.Surface_s(self.face), u0, u1, v0, v1)
        for x in X[np.linspace(0, len(X) - 1, min(32, len(X))).astype(int)]:
            pr.Perform(gp_Pnt(*x))
            out.append(pr.LowerDistance() if pr.IsDone() and pr.NbPoints() > 0 else np.inf)
        return np.array(out)

    def matches(self, kind, X):
        """Does a design surface of this kind ("plane" or "fan") with these corners lie on it?"""
        if self.spline or (self.kind == "plane") != (kind == "plane"):
            return False
        return bool(np.percentile(self.distance(X), 95) <= ON_TOL)


def _spread(tris, area_faces, F, V, count, rng):
    """count points spread by area over the triangles, and the triangle each lies on."""
    w = area_faces[tris]
    total = w.sum()
    pick = tris[rng.choice(len(tris), count, p=w / total)] if total > 0 else tris[rng.integers(0, len(tris), count)]
    r = np.sqrt(rng.random(count))[:, None]
    q = rng.random(count)[:, None]
    a, b, c = V[F[pick, 0]], V[F[pick, 1]], V[F[pick, 2]]
    return (1 - r) * a + r * (1 - q) * b + r * q * c, pick


def _at(x):
    return [round(float(c), 2) for c in x]


def _wraps(points):
    """Does a face go half a turn or more round something? Then the middle of its points lies far
    from any of them, for its width across (its second largest extent, because a long thread's
    length says nothing about its turns): half of it at a full turn, under a tenth on a patch.
    Such a face is no small piece, however little area it has: a screw thread cut turn by turn,
    or a cylinder cut at its seams."""
    mid = points.mean(axis=0)
    return bool(np.linalg.norm(points - mid, axis=1).min() > WRAP * np.sort(np.ptp(points, axis=0))[1])


def _vouched(surface, ids, V):
    """The mesh vouches for a face: enough corners under it to over-determine its surface, all
    on that surface. A plane through one triangle's corners proves nothing."""
    if surface.spline or surface.kind == "other":
        return False
    return len(ids) >= PARAMS[surface.kind] + EVIDENCE and bool(np.percentile(surface.distance(V[ids]), 95) <= TIGHT_TOL)


def measure(mesh, tess, owner, faces, closest, boundary=None):
    """The lost_detail fields. mesh is the original mesh, tess and owner the solid's
    tessellation and each of its triangles' face (judge.tessellate), closest a trimesh-style
    closest point (judge._closest), and boundary, if given, tells which points of the mesh lie
    on the enclosed region's boundary (walls inside the part aren't design surfaces)."""
    t0 = time.time()
    if not len(tess.faces):
        return {"skipped": "the solid has no tessellation"}
    # (a designer's mesh may be placed elsewhere than the STEP: nothing below would mean anything,
    # and searching for the nearest face from far away takes minutes)
    apart = float(np.abs(np.asarray(mesh.bounds) - np.asarray(tess.bounds)).max())
    if apart > APART * float(np.linalg.norm(np.ptp(mesh.bounds, axis=0))):
        return {"skipped": f"the mesh and the solid don't line up: their boxes differ by {apart:.3g} mm"}
    F, V = np.asarray(mesh.faces), np.asarray(mesh.vertices)
    A = mesh.area_faces
    total = float(A.sum())
    floor = max(MIN_AREA, MIN_SHARE * total)
    gid, normals = flat_groups(mesh)
    area = np.bincount(gid, A, len(normals))
    graph = group_graph(mesh, gid, normals)
    fans, capped = find_fans(mesh, gid, normals, graph, area)
    in_fan = np.zeros(len(normals), bool)
    for members, _ in fans:
        in_fan[members] = True

    # the design surfaces, as triangles
    order = np.argsort(gid, kind="stable")
    cuts = np.searchsorted(gid[order], np.arange(len(normals) + 1))
    surfaces = []
    for g in np.nonzero((area >= floor) & ~in_fan)[0].tolist():
        if not reads_as_facet(g, graph, area, in_fan):
            tris = order[cuts[g]:cuts[g + 1]]
            surfaces.append(("plane", tris, np.unique(F[tris].ravel())))
    for members, ids in fans:
        if area[members].sum() >= floor:
            tris = np.concatenate([order[cuts[g]:cuts[g + 1]] for g in members])
            surfaces.append(("fan", tris, ids))
    sizes = np.array([A[s[1]].sum() for s in surfaces])
    rng = np.random.default_rng(0)
    known = {}

    def surface_of(k):
        s = known.get(k)
        if s is None:
            s = known[k] = FaceSurface(faces[k])
        return s

    lost, lost_spline, kept, worst = 0.0, 0.0, 0, []
    if surfaces:
        wanted = np.clip(sizes / sizes.sum() * POINTS, *SURFACE_POINTS)
        counts = np.maximum(3, (wanted * min(1.0, POINTS / wanted.sum())).astype(int))
        points, picks, which = [], [], []
        for i, ((_, tris, _), n) in enumerate(zip(surfaces, counts.tolist())):
            p, pick = _spread(tris, A, F, V, n, rng)
            points.append(p)
            picks.append(pick)
            which.append(np.full(n, i))
        points, picks, which = np.vstack(points), np.concatenate(picks), np.concatenate(which)
        on_wall = np.ones(len(points), bool) if boundary is None else boundary(points, mesh.face_normals[picks])
        hit = owner[closest(tess, points)[2]]
        ends = np.cumsum(counts)
        for i, (kind, _, ids) in enumerate(surfaces):
            idx = np.arange(ends[i] - counts[i], ends[i])
            idx = idx[on_wall[idx]]
            if not len(idx):
                sizes[i] = 0.0     # (wholly inside the part)
                continue
            where = hit[idx]
            matched, spline, missed, onto = 0, 0, [], {}
            for k in np.unique(where).tolist():
                s = surface_of(k)
                m = where == k
                if s.matches(kind, V[ids]):
                    matched += int(m.sum())
                else:
                    spline += int(m.sum()) if s.spline else 0
                    missed.append(points[idx[m]])
                    onto[s.kind] = onto.get(s.kind, 0) + int(m.sum())
            share = matched / len(idx)
            if share >= COVER:
                kept += 1
                continue
            gone = float(sizes[i]) * (1 - share)
            to_spline = float(sizes[i]) * spline / len(idx)
            lost += gone
            lost_spline += to_spline
            worst.append({"kind": kind, "area": round(gone, 3), "at": _at(np.vstack(missed).mean(axis=0)),
                          "went_to": "bspline" if to_spline > 0.5 * gone else "wrong_surface",
                          "cover": round(share, 3), "onto": onto})
    design = float(sizes.sum())
    worst.sort(key=lambda w: -w["area"])

    # the smooth regions left when the design surfaces are taken out
    region = np.full(len(F), -1)
    for i, (_, tris, _) in enumerate(surfaces):
        region[tris] = i
    count = len(surfaces)
    rest = region < 0
    f, g = mesh.face_adjacency.T
    # (a triangle with no area has no normal to bend by, and only joins its neighbours)
    link = rest[f] & rest[g] & ((mesh.face_adjacency_angles <= np.radians(SMOOTH[1])) | (A[f] < 1e-9) | (A[g] < 1e-9))
    n = len(F)
    parts = connected_components(coo_matrix((np.ones(int(link.sum())), (f[link], g[link])), shape=(n, n)), directed=False)[1]
    blends = np.unique(parts[rest])
    if len(blends):
        region[rest] = count + np.searchsorted(blends, parts[rest])
    region_area = np.bincount(region, A, count + len(blends))
    blend_area = float(region_area[count:][region_area[count:] >= floor].sum())

    # where each STEP face lies, so every small face is counted however small
    spots, shred_area, shred_excess = [], 0.0, 0
    tA, tF, tV = tess.area_faces, np.asarray(tess.faces), np.asarray(tess.vertices)
    per = np.argsort(owner, kind="stable")
    tcuts = np.searchsorted(owner[per], np.arange(len(faces) + 1))
    each = int(np.clip(POINTS // max(len(faces), 1), 3, FACE_POINTS))
    face_area = np.bincount(owner, tA, len(faces))
    points, whose = [], []
    for k in range(len(faces)):
        tris = per[tcuts[k]:tcuts[k + 1]]
        if len(tris) and tA[tris].sum() > 0:
            points.append(_spread(tris, tA, tF, tV, each, rng)[0])
            whose.append(k)
    pieces = {}
    if points:
        near = closest(mesh, np.vstack(points))[2]
        lies = region[near]
        for j, k in enumerate(whose):
            r, c = np.unique(lies[j * each:(j + 1) * each], return_counts=True)
            if c.max() >= MAJORITY * each:
                home = int(r[c.argmax()])
                pieces.setdefault(home, []).append(k)
    # the mesh's corners in the blends, each under its nearest face: the evidence for vouching
    ids = np.unique(F[rest].ravel()) if rest.any() else np.zeros(0, int)
    if len(ids) > POINTS:
        ids = rng.choice(ids, POINTS, replace=False)
    under = {}
    if len(ids):
        for k, c in zip(owner[closest(tess, V[ids])[2]].tolist(), ids.tolist()):
            under.setdefault(k, []).append(c)
    for r, ks in pieces.items():
        if region_area[r] < floor:
            continue
        if r < count:
            need, what = SHRED_SPLIT, "split_" + surfaces[r][0]
        else:
            ks = [k for k in ks if not _vouched(surface_of(k), under.get(k, []), V)]
            need, what = SHRED_BLEND, "blend"
        # (a few large faces are a fair division of a large region, and so is a turn of a thread)
        ks = [k for k in ks if face_area[k] < SMALL * region_area[r] and (
            surface_of(k).kind == "plane" or not _wraps(_spread(per[tcuts[k]:tcuts[k + 1]], tA, tF, tV, 64, rng)[0]))]
        if len(ks) < need:
            continue
        tris = np.nonzero(region == r)[0]
        cut = float(min(face_area[ks].sum(), region_area[r]))
        score = cut * (1 - 1 / len(ks))
        shred_area += score
        shred_excess += len(ks) - 1
        types = {}
        for k in ks:
            types[surface_of(k).kind] = types.get(surface_of(k).kind, 0) + 1
        at = (tess.triangles_center[np.concatenate([per[tcuts[k]:tcuts[k + 1]] for k in ks])]).mean(axis=0)
        spots.append({"what": what, "faces": len(ks), "area": round(cut, 3), "region_area": round(float(region_area[r]), 3),
                      "score": round(score, 3), "at": _at(at), "types": types, "mesh_triangles": len(tris)})
    spots.sort(key=lambda s: -s["score"])
    return {
        "method": (f"planes within {FLAT_TOL} mm and cylinder/cone fans within {FAN_TOL} mm found in the mesh; "
                   f"kept when {COVER:g} of {SURFACE_POINTS[0]}-{SURFACE_POINTS[1]} area points per surface (at most "
                   f"{POINTS} in all) reach faces of the right kind with its corners within {ON_TOL} mm; shredding "
                   f"places {FACE_POINTS} points (3 past {POINTS}) of each STEP face on the mesh; fan search at most "
                   f"{FAN_SECONDS:g} s, largest strips first"),
        "capped": bool(capped),
        "design_area": round(design, 3),
        "design_share": round(design / total, 4) if total else 0.0,
        "planes": sum(s[0] == "plane" for s in surfaces),
        "fans": sum(s[0] == "fan" for s in surfaces),
        "kept": kept,
        "lost_area": round(lost, 3),
        "lost_share": round(lost / design, 5) if design else 0.0,
        "lost_to_bspline_area": round(lost_spline, 3),
        "lost_to_bspline_share": round(lost_spline / design, 5) if design else 0.0,
        "lost_to_wrong_area": round(lost - lost_spline, 3),
        "lost_to_wrong_share": round((lost - lost_spline) / design, 5) if design else 0.0,
        "lost_worst": worst[:WORST],
        "blend_area": round(blend_area, 3),
        "shred_area": round(shred_area, 3),
        "shred_share": round(shred_area / total, 5) if total else 0.0,
        "shred_regions": len(spots),
        "shred_excess_faces": int(shred_excess),
        "shred_worst": spots[:WORST],
        "seconds": round(time.time() - t0, 1),
    }
