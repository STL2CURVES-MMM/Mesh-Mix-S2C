"""Screw threads found from the surface's own screw motion, whatever made the mesh.

A thread's defining property: moved by its own screw motion, through any angle, a point of it lands back on the
surface. Moved by plain rotation, it doesn't. That tells a thread from a cylinder or cone, which rotation leaves
where they are, from a gear or ribs, which repeat only at their spacing, and from a passing resemblance, which
stops landing on longer moves. It holds for any profile, any number of starts, either hand, inside or out, and it
asks nothing of how the mesh is cut. The work is in finding what to test, and in testing it fairly:

1. Read. A helical surface is left in place by one screw motion, and its normals say so: for a point x with normal n,
   n . (c x (x - a) + p c) = 0, which is linear in the motion (Pottmann and Wallner, "Computational Line Geometry").
   So one flank tells its own axis and lead, however small the thread is on the part. A surface with more motions
   than one (a plane, a sphere) shows as a second small eigenvalue, and p = 0 is a surface of revolution.
   Regions are found on surface points, not through the mesh's edges: common threads from OpenSCAD are built from
   pieces that don't quite meet, each flank facet parted from the next by a step 0.02 mm wide, facing somewhere else.
   Points that are near each other and face the same way don't care how the mesh is stitched.
2. Refine. Facet normals are good to about a degree. Landing distances aren't limited by that, so the axis, the lead
   and any taper are adjusted together until points land closest, on moves that start short and grow.
3. Judge. Pure rotation through the same angles must move those points away. No limit here is in millimetres: the
   rotation's own displacement of each point is the scale its landing is judged by.
4. Scan, on demand. If no reading of an axis lands surely, the lead is scanned for by unwrapping the surface about it.

The profile is then one curve: points that land, turned back along the helix into a plane through the axis, with the
median radius taken at each degree of phase. Only points shown to land are used, so a shank or a chamfer can't bend it,
and none has to be a mesh corner, so it can't invent a corner where a mesh happens to have them.
"""

import math
import os

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

# Reading
READ_POINTS = 120000    # surface points a part is read through
NEIGHBOURS = 8          # each point is compared with this many nearest
SHARP_DEG = 35.0        # degrees: points facing further apart are on different surfaces. A thread's corner bends 60 or more, a chord across a ten-sided curve 36
MIN_POINTS = 60         # a region with fewer points can't say which way it twists
ONE_MOTION = 5.0        # a region has one motion if the next eigenvalue is this many times the smallest
STEADY = 4.0            # a flank's median reading stands out from nothing by this many standard errors. A facet's normal is scattered by half the angle between facets, far more than a helix leans on a coarse mesh, but over many facets the median is sure
MIN_READ_FACETS = 8     # triangles a region's reading must rest on
SETTLED_LEFT = 0.5      # share: a fit leaving more than this of its points off hasn't settled on the region's surface
MAX_CUTS = 8            # times a region that won't settle may be cut in two: down to 1/256 of it
READ_AGAIN = 4          # times a region is read, on what the last reading left off
TRIMS = 8               # times a region's fit is trimmed to the points near it
FIT_FLOOR = 4.0         # degrees: points this close to a fit stay in it, whatever the rest do. About what a facet's normal is off on a coarse mesh
FLAT = 0.02             # a region whose normals vary less than this is flat: any motion in its plane fits it
SLOW = 0.002            # share of the circumference: a lead under this moves nothing a facet can show, so the surface reads as one of revolution
LONG = 200.0            # times the radius: a lead over this is a surface with no twist a facet can show either
CLEAR = 0.15            # share of its largest radius: how far a surface winding round an axis stays clear of it. A patch's own fit can put the axis through the patch
READINGS = 4            # readings of the lead tried per axis, largest first

# Gathering readings into hypotheses
GROUP_ANGLE = 6.0       # degrees: pieces this close in direction may read one axis. A small piece of a fine thread reads its axis poorly
GROUP_LINE = 0.5        # share of the radius: and whose axes pass this near each other at the piece
SNAP_ANGLE = 5.0        # degrees: a surface of revolution this close in direction is what a twisting region's axis snaps to
SNAP_LINE = 0.5         # share of the radius: and this close as a line
SAME_ANGLE = 3.0        # degrees: axes this close in direction are one axis
SAME_LINE = 0.15        # share of the radius: and this close as lines
RING_BINS = 64          # radii are counted in this many bins to find where a surface's points gather
RING_SHARE = 0.15       # of the fullest bin: a bin holding this much is still part of the ring
RING_GAP = 0.15         # share of the radius: rings this near may be one. Small pieces of one flank each have a narrow ring of their own
RING_GAP_LEAD = 0.5     # share of the lead: a flank is about this deep, so what lies further off is something else, however large the radius
MIN_AROUND = 90.0       # degrees: surfaces reading an axis must go this far round it between them. A speck of blend reads a screw motion too
LEAN = 0.3              # a surface whose normal has this much along the axis (17 degrees' lean) feels a change of lead; a crest or root band slides along itself
RINGS = 3               # leaning surfaces of revolution stacked on one axis: a fine thread's flanks can't show their twist, but one chamfer isn't a stack
SAMPLE_POINTS = 200     # a hypothesis's own points kept, to tell if it's a piece of a thread already found
PIECE_OF = 0.5          # share of them landing under a thread found: it's that thread, read poorly from a small piece
MAX_AXES = 16           # hypotheses tried per part, most points first: an end to them, above any part's (one board has six threaded holes)
WHOLE_SHARE = 0.5       # share of the part fitting one motion that makes its axis worth a try if no surface's own reading found a thread
WHOLE_FIRST = 0.9       # share that makes it worth trying first, if no reading sits on it. Crossing threads leave only patches, each reading a tilted axis of its own, while the whole part knows the true one

# Testing a lead
SCREW_DEG = (97.3, 163.7, 251.1)    # degrees: unrelated to each other and to common tooth counts. No whole turn: that is only a shift along the axis, which any tooth or long twist survives
ROTATE_DEG = (97.0, 137.0, 211.0)   # degrees: pure rotation, in the same range. The largest of the three counts, since a gear repeats at some angles
RADIUS_BANDS = 4        # bands of radius a thread's leaning surface is followed round in: a cut not through the axis ends a thread at another angle at each radius
GAP_CHANCE = 0.001      # a leaning surface ends where its points leave a gap round the axis wider than random sampling leaves on one band in this many
MEMBER_RATIO = 0.4      # landed: within this share of how far pure rotation takes the same point
TWIST_FLOOR = 0.03      # of the lead or radius, whichever is less: pure rotation moves a surface of revolution less than this
TWIST_DEPTHS = 2.0      # ring depths: the floor's scale is at most this many depths of the surface's ring (_twist_scale)
TWIST_FALLBACK = 0.05   # share of the leaning points: if fewer twist under the radius's floor, the ring depth's is tried (_judge)
TIGHT = 0.1             # a landing this share of how far rotation takes the same points, about 5% of the thread's depth, is sure
FLANKS_IF_TIGHT = 0.1   # of the leaning surface rotation moves, what a tightly landing thread must explain: it may share its ring, and crossing sweeps divide it
FLANKS_IF_LOOSE = 0.55  # and one that lands loosely: most of it
LOOSE_AROUND = 300.0    # degrees: how far round a thread landing loosely must go. A coarse mesh's thread goes all the way; a loose landing on part of a circle is chance
MIN_FLANK_MEMBERS = 30  # flank points that must land: 1% of those tested, which all lie in the thread's ring
MIN_MEMBERS = 40        # points that must land, so that their reach means something
MIN_WINDING = 0.5       # turns along the axis times the share of a circle covered: how far one flank travels round its helix
SWEEP_TIGHT = 0.01      # a screw sweep lands this share of rotation's reach or nearer, ten times a thread's TIGHT: exactly, on a clean mesh
SWEEP_SHARE = 0.9       # of the leaning surface rotation moves, what a screw sweep's landing points must be
SWEEP_AROUND = 300.0    # degrees: how far round a screw sweep's landing points must go (a helical gear's teeth go all round)
DEPTH_OF_LEAD = 1.5     # a thread is no deeper than this many of its leads: 0.6 for a V, 1 or so for a square thread with a narrow groove
TEST_POINTS = 3000      # surface points tested for each lead
QUICK_EVERY = 6         # one point in this many gets the quick look at a motion
QUICK_MARGIN = 0.5      # share: the quick look holds a motion to this share of what the verdict asks of its flank shares and winding, since a few points scatter

# Refining
REFINE_POINTS = 600     # points a motion is refined on
REFINE_LEVERS = (0.15, 0.4, 1.0, 2.5, 6.0, 15.0)    # turns: lengths of move, each no longer than half the thread
REFINE_STEPS = 3        # steps at each length
REFINE_GAIN = 0.95      # a step is kept if the flanks land at least this much closer
REFINE_TILT = 0.052     # radians, 3 degrees: as far as an axis may turn from its reading
REFINE_PLACE = 0.25     # share of the radius: as far as an axis may move from its reading
REFINE_LEAD = 2.0       # times: as far as a lead may go from its reading. A flank that hardly twists lands better and better as the lead goes to nothing
BAND_RADIAL = 0.95      # a crest or root band's point: its normal points this nearly straight out from the axis (and doesn't lean)
BAND_POINTS = 10        # points each band, crest and root, needs before the axis is read from them
BAND_FIT = 0.05         # share of the thread's depth: how far, by the median, the bands' points may lie off the cylinders fitted round the axis read from them
MIN_TAPER = 0.005       # slope of a crest (0.3 degrees) worth trying a taper for. Pipe threads have 0.03
MAX_TAPER = 0.3         # slope of radius on length a thread's taper can have (17 degrees)
TAPER_GAIN = 0.75       # a taper is taken only if the flanks land this much closer with it: left free, it soaks up whatever else is wrong
TAPER_AGREES = 0.5      # share of the taper: the crest's own slope along the thread must be within this of it

# Scanning, on demand
SCAN_POINTS = 40000     # surface points the scan reads
MAX_SCANS = 4           # hypotheses of a part the scan may run on, largest first
SCAN_SHARE = 0.25       # of the largest hypothesis: one smaller than this gets no scan once a thread has been found on the part
SCAN_LEADS = 700        # leads tried, evenly in ratio, from the shortest to the longest a thread on this ring can have
SHORTEST_LEAD = 0.15    # mm: the shortest lead scanned for: below any printed or machined thread
SCAN_TOP = 3            # dips tried
DIP_SPAN = 0.15         # share of the lead: a dip in the scan is judged against the leads this far either side
SCAN_MIN = 200          # leaning points a scan needs
LEAN_TELL = 4.0         # degrees: a helix leaning less than this along a thread's flank is a lead facet normals can't tell (_untold)
FLAT_TWIST = 0.0175     # sine of 1 degree: a nearly flat facet turned this far from the axial plane is a thread's flank, not a flat ring
PHASE_BINS = 72         # the scan's bins of phase

# After a thread is found
SAME_LEAD = 0.03        # share: a candidate this close to a thread already found, same hand, is that thread
EXPLAINED = 0.85        # of the leaning surface: a thread explaining this much leaves no room for a second, so no rescan looks for one
REMOVE_RATIO = 0.4      # a point landing like a member belongs to the thread found, and leaves the rescan
MAX_THREADS = 4         # threads looked for on one axis: an end to the rescans, far above any part's
RING_MARGIN = 0.1       # share of a ring's width searched either side of it
RING_MARGIN_MM = 0.05   # mm: and at least this

# Joint seeds: threads on part of a circle, whose flank strips each read an axis tens of degrees off (_joint_seeds)
JOINT_SEEDS = os.environ.get("MM_S2C_JOINT_SEEDS", "1") != "0"   # on by default (the owner's choice, 2026-11-07: about 2% of conversion time); 0 turns them off
POOL_TRIMS = 12         # times a pool of regions' fit is trimmed to the points near it
POOL_FLOOR = 3.0        # degrees: points this close to a pool's fit stay in it, whatever the rest do
POOL_KEEP = 30          # points: a trim leaving fewer keeps the pool as it was
RING_LEADS = os.environ.get("MM_S2C_RING_LEADS", "1") != "0"   # (experimental, 2026-11-19, wt_129) a pool reading no lead has its rings read for one (_ring_leads)

# The profile
PROFILE_POINTS = 60000  # surface points the profile is read from
COLUMNS_PER_LEAD = 360  # phase columns: a column is a degree of the helix, whatever the size of the thread
TIGHT_LAND = 0.25       # of how far rotation takes the same points: landing this close counts for the profile
MIN_IN_COLUMN = 4       # points a column needs to say anything
DEPTH_TOL = 0.01        # share of the depth: the outline is held this close, or to three times its own thickness if that is more
ARC_MIN_PIECES = 3      # pieces in a row turning one way make an arc
ARC_ROWS = 8            # rows of the outline standing for an arc, for what needs a polyline


def frame(d):
    """Unit d and two unit directions square to it."""
    d = np.asarray(d, float) / np.linalg.norm(d)
    e1 = np.cross(d, [1.0, 0, 0] if abs(d[0]) < 0.9 else [0, 1.0, 0])
    e1 /= np.linalg.norm(e1)
    return d, e1, np.cross(d, e1)


def points(V, F, rng, n, with_tri=False):
    """Surface points and their normals, drawn by the square root of each triangle's area, with that as their weights. By area alone a thread small on a big part gets almost none, and by
    triangle alone slivers with meaningless normals get as many as anything."""
    T = V[F]
    cr = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ar = 0.5 * np.linalg.norm(cr, axis=1)
    ok = np.nonzero(ar > 0)[0]
    w = np.sqrt(ar[ok])
    tri = rng.choice(ok, n, p=w / w.sum())
    u, v = rng.random(n), rng.random(n)
    flip = u + v > 1
    u[flip], v[flip] = 1 - u[flip], 1 - v[flip]
    X = T[tri, 0] + u[:, None] * (T[tri, 1] - T[tri, 0]) + v[:, None] * (T[tri, 2] - T[tri, 0])
    out = X, cr[tri] / (2 * ar[tri])[:, None], np.sqrt(ar[tri])
    return (*out, tri) if with_tri else out


def regions(X, N):
    """Labels: points joined to near points facing nearly the same way."""
    _, idx = cKDTree(X).query(X, k=NEIGHBOURS + 1, workers=1)
    a = np.repeat(np.arange(len(X)), NEIGHBOURS)
    b = idx[:, 1:].ravel()
    ok = np.einsum("ij,ij->i", N[a], N[b]) > math.cos(math.radians(SHARP_DEG))
    g = coo_matrix((np.ones(int(ok.sum())), (a[ok], b[ok])), shape=(len(X), len(X)))
    return connected_components(g, directed=False)[1]


def motion(X, N, w):
    """A surface's motion: axis direction c, a point on it, parameter p (lead = 2 pi |p|, its sign the hand), and the eigenvalues."""
    o = (X * w[:, None]).sum(0) / w.sum()
    M = np.c_[np.cross(X - o, N), N]
    A = (M * w[:, None]).T @ M / w.sum()
    A11, A12, A22 = A[:3, :3], A[:3, 3:], A[3:, 3:]
    A22i = np.linalg.pinv(A22, rcond=1e-9)
    lam, Q = np.linalg.eigh(A11 - A12 @ A22i @ A12.T)
    c = Q[:, 0]
    cbar = -A22i @ A12.T @ c
    return c, np.cross(c, cbar) + o, float(c @ cbar), lam


def misfit(X, N, c, a, p):
    """Degrees between each normal and the plane square to the motion's path there."""
    v = np.cross(c, X - a) + p * c
    s = np.abs(np.einsum("ij,ij->i", v, N)) / np.maximum(np.linalg.norm(v, axis=1), 1e-12)
    return np.degrees(np.arcsin(np.clip(s, 0, 1)))


def _read_one(x, n, q, tris):
    """One surface's reading from these points, if it has one motion, and which points are off it."""
    none = np.zeros(len(x), bool)
    if np.linalg.norm(n - n.mean(axis=0), axis=1).max() < FLAT:
        return None, none
    # (trimmed until it settles on the region's main surface: fitted once to a mix, the motion is nobody's)
    keep = np.ones(len(x), bool)
    for _ in range(TRIMS):
        c, a, p, lam = motion(x[keep], n[keep], q[keep])
        e = misfit(x, n, c, a, p)
        new = e <= max(FIT_FLOOR, 2 * float(np.median(e[keep])))
        if new.sum() < MIN_POINTS or (new == keep).all():
            break
        keep = new
    left = ~keep
    if keep.sum() < MIN_POINTS or lam[1] < ONE_MOTION * max(lam[0], 1e-12):
        return None, left
    x, n, tris = x[keep], n[keep], tris[keep]
    facets = len(np.unique(tris))
    v = x - a
    rad = v - np.outer(v @ c, c)
    rho = np.linalg.norm(rad, axis=1)
    if np.percentile(rho, 1) < CLEAR * np.percentile(rho, 99):
        return None, left
    nz = n @ c
    lean = float(np.median(np.abs(nz)))
    lead = 2 * math.pi * abs(p)
    hand = 1 if p > 0 else -1
    steady = False
    if lean >= LEAN:
        # (each point's own reading: rho n_theta + p n_z = 0 on the motion's surface. The median is the region's, which a corner it ran round can't move as it moves a least-squares fit)
        use = np.abs(nz) >= LEAN
        nth = np.einsum("ij,ij->i", n, np.cross(c, rad / np.maximum(rho, 1e-12)[:, None]))
        own = -rho[use] * nth[use] / nz[use]
        mid = float(np.median(own))
        steady = facets >= MIN_READ_FACETS and abs(mid) > STEADY * 1.4826 * float(np.median(np.abs(own - mid))) / math.sqrt(facets)
        if steady:
            lead = 2 * math.pi * abs(mid)
            hand = 1 if mid > 0 else -1
    return dict(c=c, a=a, lead=lead, hand=hand, rho=float(np.median(rho)), n=len(x), x=x, nrm=n, w=q[keep], lean=lean, steady=steady), left


def _read_regions(X, N, w, tri):
    """For each surface with one motion in each region: its axis, point, lead, size. A region can hold more than one: a rounded thread has no sharp edge to end it, and runs on smoothly into whatever
    lies at its ends. So what the fit leaves off is read again, and a region that won't settle is cut in two."""
    lab = regions(X, N)
    order = np.argsort(lab, kind="stable")
    cut = np.nonzero(np.diff(lab[order]))[0] + 1
    out = []

    def read(idx, cuts):
        for _ in range(READ_AGAIN):
            if len(idx) < MIN_POINTS:
                return
            r, left = _read_one(X[idx], N[idx], w[idx], tri[idx])
            if r is not None:
                out.append(r)
            # (it settled on most of them: what's left is whatever else it ran into)
            if not left.any() or left.all():
                return
            if r is None or left.mean() > SETTLED_LEFT:
                break
            idx = idx[left]
        else:
            return
        # (it won't settle: smooth blends can join a thread to surfaces all over a part, and no fit of the whole is any of them. Cut across its longest side, a compact thread soon has a cell
        # nearly to itself. Each half is a small sum, so this costs little however large the region is)
        if cuts <= 0 or len(idx) < 2 * MIN_POINTS:
            return
        box = X[idx]
        side = int(np.argmax(np.ptp(box, axis=0)))
        low = box[:, side] <= np.median(box[:, side])
        if low.all() or not low.any():
            return
        read(idx[low], cuts - 1)
        read(idx[~low], cuts - 1)

    for idx in np.split(order, cut):
        read(idx, MAX_CUTS)
    return out


def same_axis(c1, a1, c2, a2, rho):
    """Near enough one line, for a surface at radius rho."""
    if abs(c1 @ c2) < math.cos(math.radians(SAME_ANGLE)):
        return False
    o = a2 - a1
    return np.linalg.norm(o - (o @ c1) * c1) <= SAME_LINE * rho


def _around(pts, c, a):
    """How far round the axis these points go, in degrees."""
    _, e, f = frame(c)
    v = pts - a
    b = ((np.arctan2(v @ f, v @ e) + math.pi) / (2 * math.pi) * 72).astype(int) % 72
    return 5.0 * len(np.unique(b))


def _ring_of(pts, c, a):
    """Where these points gather round the axis: (smallest radius, largest). The run of radii round the fullest one, each holding a good share of what it holds: a flank's points lie evenly over its
    depth, and whatever else its fit took in is thinly spread over a far wider range."""
    v = pts - a
    rho = np.linalg.norm(v - np.outer(v @ c, c), axis=1)
    lo, hi = float(np.percentile(rho, 1)), float(np.percentile(rho, 99))
    if hi <= lo:
        return lo, hi
    h, e = np.histogram(rho, bins=RING_BINS, range=(lo, hi))
    top = int(np.argmax(h))
    i = j = top
    while i > 0 and h[i - 1] >= RING_SHARE * h[top]:
        i -= 1
    while j < RING_BINS - 1 and h[j + 1] >= RING_SHARE * h[top]:
        j += 1
    return float(e[i]), float(e[j + 1])


def _leaning(m, c):
    """A region's points that lean along the axis, where its ring is read: its flanks. A crest or root band fits any screw motion about its axis, so a flank's fit
    takes in a band it runs smoothly into, as a rounded thread's does; and the band, all at one radius, fills one bin of the count so full that the flank's points,
    spread over its depth, fell below RING_SHARE of it: a rounded M7's ring came out as its root alone, and its thread was tested on a band that hardly twists."""
    lean = np.abs(m["nrm"] @ c) >= LEAN
    return m["x"][lean] if lean.sum() >= MIN_POINTS else m["x"]


def _readings_of(members):
    """Lead and hand as the leaning regions read them, largest first, one of each. A crest or root band's normal has nothing along the axis, so it says nothing of the lead."""
    out = []
    for m in sorted(members, key=lambda m: -m["n"]):
        if m["lean"] < LEAN or m["lead"] <= 0:
            continue
        if not any(h == m["hand"] and abs(L - m["lead"]) < SAME_LEAD * L for L, h in out):
            out.append((m["lead"], m["hand"]))
    return out[:READINGS]


def _rings(members, c, a):
    """The members grouped by where they lie round the axis: one group for each ring they don't share. Threads of different sizes on one axis, or a thread on a helical gear's axis, are separate
    things, and tested together each is lost among the other's points."""
    spans = sorted(((*_ring_of(_leaning(m, c), c, a), m) for m in members), key=lambda t: t[0])
    out = []
    for lo, hi, m in spans:
        if out and lo <= out[-1][1] + min(RING_GAP * out[-1][1], RING_GAP_LEAD * max(float(np.median([q["lead"] for q in out[-1][2]])), m["lead"])):
            out[-1][1] = max(out[-1][1], hi)
            out[-1][2].append(m)
        else:
            out.append([lo, hi, [m]])
    return out


def _grouped(rows):
    """Pieces that loosely agree on an axis, fitted again together. Each piece sees a little of the circle, and the fit is far better over all of it."""
    groups = []
    for r in rows:
        at = r["x"].mean(axis=0)
        for g in groups:
            if abs(g["c"] @ r["c"]) < math.cos(math.radians(GROUP_ANGLE)):
                continue
            p1 = g["a"] + ((at - g["a"]) @ g["c"]) * g["c"]
            p2 = r["a"] + ((at - r["a"]) @ r["c"]) * r["c"]
            if np.linalg.norm(p1 - p2) <= GROUP_LINE * max(g["rho"], r["rho"]):
                g["n"] += r["n"]
                g["members"].append(r)
                break
        else:
            groups.append(dict(c=r["c"], a=r["a"], rho=r["rho"], n=r["n"], members=[r]))
    for g in groups:
        if len(g["members"]) > 1:
            x = np.vstack([m["x"] for m in g["members"]])
            n = np.vstack([m["nrm"] for m in g["members"]])
            q = np.concatenate([m["w"] for m in g["members"]])
            c, a, _, lam = motion(x, n, q)
            p = float(np.median([m["lead"] for m in g["members"]])) / (2 * math.pi) * g["members"][0]["hand"]
            # (kept only if it fits the pieces about as well as they fit themselves: pieces of two threads side by side have no one motion)
            if lam[1] >= ONE_MOTION * max(lam[0], 1e-12) and np.median(misfit(x, n, c, a, p)) <= FIT_FLOOR:
                g["c"], g["a"] = c, a
    return groups


def hypotheses(V, F, rng, twisting=None):
    """What to test for a thread, best first: an axis and a ring round it. From regions that twist, steadily, snapped to a surface of revolution along them where there is one, and with the unsnapped
    reading after it if that was a real move. Then stacks of leaning surfaces of revolution, which a thread too fine for a facet to show its twist looks like. All of them: motions tests the first
    MAX_AXES, and the rest only where a thread it found may repeat. twisting: a list the twisting regions are added to, in the order they were read (for _joint_seeds)."""
    X, N, w, tri = points(V, F, rng, READ_POINTS, with_tri=True)
    twist, turned = [], []
    for r in _read_regions(X, N, w, tri):
        if r["rho"] <= 0:
            continue
        if r["steady"] and SLOW * 2 * math.pi * r["rho"] < r["lead"] < LONG * r["rho"]:
            twist.append(r)
            if twisting is not None:
                twisting.append(r)
        elif not r["steady"]:
            # (a crest or root band, or a flank too fine to show its twist: round its axis it is a surface of revolution)
            turned.append(r)
    twist.sort(key=lambda r: -r["n"])
    turned.sort(key=lambda r: -r["n"])

    def each_ring(c, a, members, snapped):
        for lo, hi, ms in _rings(members, c, a):
            if _around(np.vstack([m["x"] for m in ms]), c, a) >= MIN_AROUND:
                x = np.vstack([m["x"] for m in ms])
                yield dict(c=c, a=a, rho=float(np.median([m["rho"] for m in ms])), n=int(sum(m["n"] for m in ms)), regions=len(ms), snapped=snapped, ring=(lo, hi), readings=_readings_of(ms),
                           sample=x[np.linspace(0, len(x) - 1, min(len(x), SAMPLE_POINTS)).astype(int)])

    out = []
    for g in _grouped(twist):
        tries = [(g["c"], g["a"], 0)]
        # (the surface of revolution nearest in radius to the thread: its own crest or root band if it has one. Holes elsewhere in the part may lie nearly along it, and be larger)
        for t in sorted(turned, key=lambda t: abs(t["rho"] - g["rho"])):
            if abs(g["c"] @ t["c"]) >= math.cos(math.radians(SNAP_ANGLE)):
                o = t["a"] - g["a"]
                if np.linalg.norm(o - (o @ g["c"]) * g["c"]) <= SNAP_LINE * max(g["rho"], t["rho"]):
                    # (the reading the snap moved from is tried after it if the move matters to a thread of this lead, not only to a surface of this radius: a piece of a
                    # nut's own flank, whose twist couldn't be read, passed for a surface of revolution, its axis 1.1 mm off; within 15% of a 17 mm radius that was "the
                    # same axis", the group's own axis, 0.05 mm off, was dropped, and a thread 1.5 mm deep with a 4 mm lead landed nowhere. The judge decides between them)
                    scale = min(max(g["rho"], t["rho"]), float(g["members"][0]["lead"]))
                    close = same_axis(g["c"], g["a"], t["c"], t["a"], scale)
                    tries = [(t["c"], t["a"], t["n"])] + ([] if close else tries)
                    break
        for c, a, sn in tries:
            out.extend(each_ring(c, a, g["members"], sn))
    for g in _grouped([r for r in turned if LEAN <= r["lean"] < 0.985]):
        out.extend(h for h in each_ring(g["c"], g["a"], g["members"], -1) if h["regions"] >= RINGS)
    # (twisting first, by size; then the stacks. Readings of one ring on one axis are one hypothesis, now that axes have snapped)
    kept = []
    for g in sorted(out, key=lambda g: (g["snapped"] < 0, -g["n"])):
        for h in kept:
            if same_axis(h["c"], h["a"], g["c"], g["a"], 0.1 * max(h["rho"], g["rho"])) and min(h["ring"][1], g["ring"][1]) > max(h["ring"][0], g["ring"][0]):
                h["ring"] = (min(h["ring"][0], g["ring"][0]), max(h["ring"][1], g["ring"][1]))
                h["readings"] += [q for q in g["readings"] if not any(q[1] == x[1] and abs(q[0] - x[0]) < SAME_LEAD * x[0] for x in h["readings"])]
                break
        else:
            kept.append(g)
    return kept


def _whole_part(V, F):
    """The screw motion of the whole part, trimmed to the triangles agreeing with it: (axis direction, a point on it, share of the triangles). For a part that's all one thread with nothing else to go by."""
    T = V[F]
    n = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    A = 0.5 * np.linalg.norm(n, axis=1)
    ok = A > 1e-9
    X, N, w = T[ok].mean(axis=1), n[ok] / (2 * A[ok])[:, None], A[ok]
    keep = np.ones(len(X), bool)
    c, a = np.array([0, 0, 1.0]), X.mean(axis=0)
    for _ in range(12):
        c, a, p, _ = motion(X[keep], N[keep], w[keep])
        e = misfit(X, N, c, a, p)
        new = e < max(3.0, 2 * float(np.median(e[keep])))
        if new.sum() < 30 or (new == keep).all():
            break
        keep = new
    return c, a, float(keep.mean())


def screw(X, a, c, angle, pitch, k=0.0):
    """Points X moved by the screw motion through an angle (one, or one for each), growing in radius by k for each unit along the axis if the thread tapers."""
    v = X - a
    z = v @ c
    rad = v - np.outer(z, c)
    across = np.cross(c, rad)
    if np.ndim(angle):
        moved = rad * np.cos(angle)[:, None] + across * np.sin(angle)[:, None]
    else:
        moved = rad * math.cos(angle) + across * math.sin(angle)
    dz = pitch * angle
    if k:
        rho = np.maximum(np.linalg.norm(rad, axis=1), 1e-12)
        moved = moved * ((rho + k * dz) / rho)[:, None]
    return a + np.outer(z + dz, c) + moved


def landing(S, X, a, c, pitch, k, scale=1.0):
    """How far each point lands: its worst move (every move has to land). With one scale for all, in the better of the two directions: near an end the moves one way run off it, but the other way
    stay on. With one for each point (_moves), signed: in that point's own direction, which already stays on."""
    if np.ndim(scale):
        return np.max([S.nearest(screw(X, a, c, scale * math.radians(deg), pitch, k), exact_above=np.inf)[0] for deg in SCREW_DEG], axis=0)
    ways = []
    for sign in (1, -1):
        worst = None
        for deg in SCREW_DEG:
            d, _ = S.nearest(screw(X, a, c, sign * scale * math.radians(deg), pitch, k), exact_above=np.inf)
            worst = d if worst is None else np.maximum(worst, d)
        ways.append(worst)
    return np.minimum(ways[0], ways[1])


def rotated(S, X, a, c, scale=1.0):
    """How far pure rotation can take each point, through angles this much of the full ones (one share, or a signed one for each point): the largest of the three. A repeating shape stays put only
    at some angles, and a 6-start nut's shortened moves fell near two of its 60 degree repeats, so that a median said rotation hardly moved it."""
    return np.max(np.array([S.nearest(screw(X, a, c, scale * math.radians(deg), 0.0), exact_above=np.inf)[0] for deg in ROTATE_DEG]), axis=0)


def verdict(r):
    """A thread or a screw sweep: the rows a part's screw motions are kept by."""
    return thread_verdict(r) or sweep_verdict(r)


def sweep_only(r):
    """A screw sweep that isn't a thread: its profile is read off the mesh's corners (threads._swept_profile)."""
    return not thread_verdict(r) and sweep_verdict(r)


def sweep_verdict(r):
    """Screw sweep: a surface the motion carries onto itself exactly, all round, which needn't travel round its helix. A helical
    gear has many starts and a long lead, so each flank is a fraction of a turn: 03_Planet_Gear's, 19 teeth on a lead of 72.7 mm and
    10 mm tall, travel 0.12 of a turn (MIN_WINDING asks a thread for half), yet its landing points go 352 degrees round and land at
    0.001 of rotation's reach, the tightest landing in the set. The winding test is what keeps a speck of a blend or a twisted rib,
    which read a screw motion over a narrow arc, from passing for a thread; a sweep is held instead to landing exactly (SWEEP_TIGHT,
    ten times a thread's tight), on whole moves (sc 1), by nearly all of the leaning surface (SWEEP_SHARE), all round (SWEEP_AROUND)."""
    if not (r["members"] >= MIN_MEMBERS and r["flank_members"] >= MIN_FLANK_MEMBERS):
        return False
    if r["depth"] > DEPTH_OF_LEAD * r["lead"] or float(r.get("sc", 1.0)) < 1.0:
        return False
    share = r["flank_members"] / max(r["flank_twisted"], 1)
    return r["D3"] <= SWEEP_TIGHT * r["Dr"] and share >= SWEEP_SHARE and r["around"] >= SWEEP_AROUND


def thread_verdict(r):
    """Thread: points that pure rotation moves away land under the screw motion, and travel far enough round the helix. No share of the part: a thread may be a small piece of it, and two crossing
    sweeps split their surface between them. Only tight can it be a modest share of the leaning surface: on 427 real parts true threads land tight, and false ones land loosely on 15% to 50% of
    it. A thread on part of a circle is judged the same way, on moves as long as its own surface allows (_moves)."""
    if not (r["members"] >= MIN_MEMBERS and r["turns"] * r["around"] / 360.0 >= MIN_WINDING and r["flank_members"] >= MIN_FLANK_MEMBERS):
        return False
    if r["depth"] > DEPTH_OF_LEAD * r["lead"]:
        return False
    share = r["flank_members"] / max(r["flank_twisted"], 1)
    if r["D3"] <= TIGHT * r["Dr"]:
        return share >= FLANKS_IF_TIGHT
    # (a loose landing only on a scan: on clean and coarse meshes every true thread measured lands tight, coarse OpenSCAD threads among them, while every loose pass in the
    # known answers, the part-thread sweep and 399 real parts was a resemblance, a twist drill's flutes, a bracket, a distributor (2026-11-06); a thread under a scan's noise does
    # land loosely, an M16x2 with 0.08 mm of it at 0.15 of rotation's reach. Scan as MMM tells it (_scan))
    return share >= FLANKS_IF_LOOSE and r["around"] >= LOOSE_AROUND and bool(r.get("scan"))


def _scan(S):
    """Whether the mesh is a scan, by MMM's own rule (fidelity.mesh_kind): its vertices scatter about the smooth surface through them by more than half the tolerance a CAD
    conversion of a part this size gets at the default quality, or too much of it is made of triangles thinner than that scatter. Measured once per mesh, on first asking (a
    few hundredths of a second on 40,000 triangles)."""
    if getattr(S, "_scan", None) is None:
        from types import SimpleNamespace

        from mmm import fidelity
        from mmm.pipeline import auto_tolerance
        noise = float(fidelity.mesh_noise(S.V, S.F))
        tol = auto_tolerance(float(np.linalg.norm(np.ptp(S.V, axis=0))), "cad")
        S._scan = fidelity.mesh_kind(SimpleNamespace(noise=noise, thin=fidelity.thin_share(S.V, S.F, noise), deflection=0.0), tol) == "scan"
    return S._scan


def _allowance(S):
    """What the conversion allows this mesh, mm, as the fidelity check does (fidelity.verdict): the CAD tolerance for a part this size,
    its chordal deflection or its noise, whichever is most. Measured once per mesh, on first asking."""
    if getattr(S, "_allow", None) is None:
        from mmm import fidelity
        from mmm.pipeline import auto_tolerance
        tol = float(auto_tolerance(float(np.linalg.norm(np.ptp(S.V, axis=0))), "cad"))
        S._allow = max(tol, float(fidelity.mesh_deflection(S.V, S.F)[0]), float(fidelity.mesh_noise(S.V, S.F)))
    return S._allow


def _cover(X, keep, c, a):
    """Where a thread's leaning surface lies round its axis, for _moves: in each of RADIUS_BANDS bands of radius its points' angles, sorted, and which gaps between them are the surface ending,
    not chance: n points at random round a circle leave a gap wider than x times their mean spacing with a chance of about n e^-x, so a gap counts if wider than x = ln(n / GAP_CHANCE) spacings.
    The bands hold equal numbers of points, so that a stray point at the ring's edge leaves none nearly empty. None if the surface goes all round in every band: nothing then limits the moves
    round the axis. None too with too few points to tell."""
    if keep.sum() < 4 * RADIUS_BANDS:
        return None
    _, e1, e2 = frame(c)
    v = X[keep] - a
    z = v @ c
    rho = np.linalg.norm(v - np.outer(z, c), axis=1)
    phi = np.arctan2(v @ e2, v @ e1) % (2 * math.pi)
    edges = np.quantile(rho, np.linspace(0.0, 1.0, RADIUS_BANDS + 1))
    band = np.clip(np.searchsorted(edges, rho, side="right") - 1, 0, RADIUS_BANDS - 1)
    bands, partial = [], False
    for j in range(RADIUS_BANDS):
        s = np.sort(phi[band == j])
        if len(s) < 2:
            bands.append(None)
            continue
        gap = np.diff(np.r_[s, s[0] + 2 * math.pi]) > math.log(len(s) / GAP_CHANCE) * 2 * math.pi / len(s)
        partial |= bool(gap.any())
        bands.append((s, gap))
    if not partial:
        return None
    return dict(c=np.asarray(c, float), a=np.asarray(a, float), edges=edges, bands=bands, z=(float(np.percentile(z, 2)), float(np.percentile(z, 98))))


def _room(cover, X, pitch):
    """Each point's room in radians, signed by its direction: as far round as its own run of the thread's leaning surface goes (in its band of radius, to the run's last point), and along the
    axis as far as the surface spans at this pitch, in whichever direction allows more. 0 off the runs: there the thread can't be tested. Also which points' bands go all round."""
    c, a = cover["c"], cover["a"]
    _, e1, e2 = frame(c)
    v = X - a
    z = v @ c
    rho = np.linalg.norm(v - np.outer(z, c), axis=1)
    phi = np.arctan2(v @ e2, v @ e1) % (2 * math.pi)
    band = np.clip(np.searchsorted(cover["edges"], rho, side="right") - 1, 0, RADIUS_BANDS - 1)
    up, down = np.zeros(len(X)), np.zeros(len(X))
    for j, b in enumerate(cover["bands"]):
        mine = np.nonzero(band == j)[0]
        if b is None or not len(mine):
            continue
        s, gap = b
        if not gap.any():
            up[mine] = down[mine] = np.inf
            continue
        n, p = len(s), phi[mine]
        at = (np.searchsorted(s, p, side="right") - 1) % n     # (the stretch between s[at] and the next point holds p)
        on = ~gap[at]
        G = np.nonzero(gap)[0]
        nxt = G[np.searchsorted(G, at) % len(G)]               # (the run ends at the first gap from here up, and starts after the last one below)
        prv = G[np.searchsorted(G, at) - 1]
        up[mine] = np.where(on, (s[nxt] - p) % (2 * math.pi), 0.0)
        down[mine] = np.where(on, (p - s[(prv + 1) % n]) % (2 * math.pi), 0.0)
    free = np.isinf(up) & np.isinf(down)
    if pitch:
        # (a move through +theta shifts a point pitch * theta along the axis)
        z0, z1 = cover["z"]
        above, below = np.maximum(z1 - z, 0.0) / abs(pitch), np.maximum(z - z0, 0.0) / abs(pitch)
        up = np.minimum(up, above if pitch > 0 else below)
        down = np.minimum(down, below if pitch > 0 else above)
    return np.where(up >= down, up, -down), free


def _moves(r, X):
    """A judged motion's moves for these points: its one share of the full moves (r["scale"]) if its leaning surface goes all round, or else for each point a signed share, as long as its room
    allows (_room) and no longer than the full ones; NaN for a point whose own band of radius goes all round, which moves as on a whole thread. A thread on part of a circle gives less room
    round it, and shortening every move instead let resemblances pass: on short enough moves anything lands, and a fillet bending round a duct passed at a quarter, while true part threads land
    as tightly on the longest moves their own surface allows."""
    if r.get("cover") is None:
        return r["scale"]
    room, free = _room(r["cover"], X, r["hand"] * r["lead"] / (2 * math.pi))
    return np.where(free, np.nan, np.sign(room) * np.minimum(1.0, np.abs(room) / math.radians(max(SCREW_DEG))))


def _land(S, X, r, Dr=None):
    """How far each point lands under a judged motion, and how far rotation through the same angles takes it, on the moves _moves gives: (D3, Ds, moves). Points that move as on a whole thread
    do so as one, the better of two ways (landing); the rest each its own way, as far as its room allows; one with no room isn't moved (0, 0). Dr: rotation's at full angles, if known."""
    pitch = r["hand"] * r["lead"] / (2 * math.pi)
    s = _moves(r, X)
    whole = np.isnan(s) if np.ndim(s) else np.ones(len(X), bool)
    D3, Ds = np.zeros(len(X)), np.zeros(len(X))
    if whole.any():
        D3[whole] = landing(S, X[whole], r["a"], r["c"], pitch, r["k"], r["scale"])
        Ds[whole] = Dr[whole] if Dr is not None and r["scale"] == 1.0 else rotated(S, X[whole], r["a"], r["c"], r["scale"])
    part = ~whole & (np.asarray(s) != 0)
    if part.any():
        D3[part] = landing(S, X[part], r["a"], r["c"], pitch, r["k"], s[part])
        Ds[part] = rotated(S, X[part], r["a"], r["c"], s[part])
    return D3, Ds, s


def _could_pass(r):
    """The verdict on one point in QUICK_EVERY (a row of _judge on them), held to QUICK_MARGIN of its flank shares and winding and to its counts in proportion, tightness aside, which a median
    of few points can't tell: whether the full test may pass. Most motions a part's surfaces suggest are specks of blend, or a fillet's facets twisting the way they were cut, and fail on
    winding, depth or share alike on few points or many. The share of rotation's moves landing, which the quick look counted alone before, told them from threads poorly: 97% of a doomed
    motion's points landed somewhere on a dense part. On 596 motions judged over 4 parts without threads and 30 known threads, no motion the full test passed failed this, and 466 of the
    495 it failed did, 88% of the time judging took."""
    if not (r["members"] >= MIN_MEMBERS / QUICK_EVERY and r["flank_members"] >= MIN_FLANK_MEMBERS / QUICK_EVERY):
        return False
    if r["turns"] * r["around"] / 360.0 < QUICK_MARGIN * MIN_WINDING:
        # (a screw sweep needn't wind round: the quick look holds it to landing as a sweep's verdict would, at ten times its
        # tightness, since a few points' median scatters, and QUICK_MARGIN of its share, sweep_verdict deciding on all the points)
        share = r["flank_members"] / max(r["flank_twisted"], 1)
        return (r["depth"] <= DEPTH_OF_LEAD * r["lead"] and float(r.get("sc", 1.0)) >= 1.0 and r["D3"] <= 10 * SWEEP_TIGHT * r["Dr"]
                and share >= QUICK_MARGIN * SWEEP_SHARE and r["around"] >= SWEEP_AROUND)
    if r["depth"] > DEPTH_OF_LEAD * r["lead"]:
        return False
    share = r["flank_members"] / max(r["flank_twisted"], 1)
    return share >= QUICK_MARGIN * (FLANKS_IF_TIGHT if r["D3"] <= TIGHT * r["Dr"] else FLANKS_IF_LOOSE)


def _judged(S, X, N, c, a, L, h, k, rm, span, Dr=None):
    """A motion, judged: the row for it, at the longest moves it passes on. One point in QUICK_EVERY is judged first, and the rest only if that may pass (_could_pass).
    Dr: how far pure rotation about this same axis takes each of these points, if it was measured already (_judge)."""
    a = np.asarray(a, float)
    if not _could_pass(_judge(S, X[::QUICK_EVERY], N[::QUICK_EVERY], c, a, L, h, k, rm, span, None if Dr is None else Dr[::QUICK_EVERY])):
        return dict(lead=L, hand=h, k=k, c=c, a=a, scale=min(1.0, 0.5 * span / (L * max(SCREW_DEG) / 360.0)), cover=None, sc=0.0, share=0.0, member=np.zeros(len(X), bool),
                    flank=np.abs(N @ c) >= LEAN, flank_twisted=0, sharp=0.0, D3=0.0, Dr=0.0, turns=0.0, around=0.0, z=(0.0, 0.0), rho=(0.0, 0.0), depth=0.0, members=0, flank_members=0)
    return _judge(S, X, N, c, a, L, h, k, rm, span, Dr)


def _judge(S, X, N, c, a, L, h, k, rm, span, Dr=None):
    """A motion judged on these points: the row for it, on the longest moves its surface allows (_moves). Dr: rotation's reach about this axis for these points, if
    measured already; the surface measures each point on its own, so it is the same, bit for bit, as measuring it here (checked on strided subsets and single points)."""
    _, e1, e2 = frame(c)
    flank = np.abs(N @ c) >= LEAN
    # (moves that fit the thread: a point in the middle of a thread shorter than twice the longest move runs off whichever way it goes)
    fit_scale = min(1.0, 0.5 * span / (L * max(SCREW_DEG) / 360.0))
    Dr = rotated(S, X, a, c) if Dr is None else Dr
    floor = TWIST_FLOOR * min(L, rm)
    twisted = Dr > floor
    if (twisted & flank).sum() < TWIST_FALLBACK * max(int(flank.sum()), 1):
        # (on demand: on a large shallow ring rotation moves no point as far as the radius's floor, and nothing would be judged; the
        # ring depth's floor is tried then, and only then, so a thread that twists under the first keeps it: _twist_scale)
        v = X - a
        deeper = TWIST_FLOOR * min(L, _twist_scale(np.linalg.norm(v - np.outer(v @ c, c), axis=1)))
        if deeper < floor:
            floor = deeper
            twisted = Dr > floor
    # (where the thread can be followed round: the leaning surface rotation moves. Caps and chamfers, surfaces of revolution, would carry its runs across the gap a flat leaves)
    r = dict(lead=L, hand=h, k=k, c=c, a=a, scale=fit_scale, cover=_cover(X, twisted & flank, c, a))
    for again in (False, True):
        # (judged against rotation through the same angles: a short move lands close whatever the lead)
        D3, Ds, s = _land(S, X, r, Dr)
        part = ~np.isnan(s) if np.ndim(s) else np.zeros(len(X), bool)
        # (a point its own moves hardly turn tells nothing: off the thread's runs it isn't moved at all, and lands where it is)
        moved = twisted & ~(part & (Ds <= floor))
        member = moved & (D3 <= MEMBER_RATIO * Ds)
        # (a thread whose own landing points go all round is a whole thread, however its ring looked, once there are enough of them to tell: the leaning surface in its ring can hold
        # another surface's points too, and an M8x1 greaser's body corners, bunched at four angles beside the thread's crest, shrank the spacing its gaps were judged by until the
        # thread's own sparse gaps passed for its ends; judged as part of a circle, it came out 1% short in lead on one BLAS kernel (tc_greaser, 2026-11-06). Where the landing points
        # do end, the leaning surface's own ends stand: landing points stop a little short of them, and a third of a circle's moves limited to its landing points read 2.010 for 2.000)
        if again or r["cover"] is None or (member & flank).sum() < 4 * RADIUS_BANDS or _cover(X, member & flank, c, a) is not None:
            break
        r["cover"] = None
    # (flanks alone say how tightly it lands, and how far it reaches: a crest or root band lands under any lead near the true one, so a wrong lead looks tight on them)
    on = member & flank
    # (1 on a thread that goes all round, where it was judged on whole moves; less on part of a circle. Only that is told: readings of one surface get the same room, and how sharply they
    # land decides between them. Told as the median length of the moves, which shifts with the lead, a lead 6% short won on half a thread)
    sc = 0.5 if on.any() and part[on].mean() > 0.5 else 1.0
    r.update(sc=sc, share=float(member.mean()), member=member, flank=flank, flank_twisted=int((twisted & flank).sum()),
             sharp=float((moved & flank & (D3 <= TIGHT * Ds)).mean()), D3=float(np.median(D3[on])) if on.any() else 0.0, Dr=float(np.median(Ds[on])) if on.any() else 0.0,
             turns=0.0, around=0.0, z=(0.0, 0.0), rho=(0.0, 0.0), depth=0.0, members=int(member.sum()), flank_members=int(on.sum()))
    if on.sum() >= 20:
        v = X[on] - a
        zz = v @ c
        rad = np.linalg.norm(v - np.outer(zz, c), axis=1)
        ang = np.sort(np.arctan2(v @ e2, v @ e1))
        flat = rad - k * zz
        r.update(turns=float(np.percentile(zz, 98) - np.percentile(zz, 2)) / L, around=360.0 - math.degrees(float(np.diff(np.r_[ang, ang[0] + 2 * np.pi]).max())),
                 z=(float(np.percentile(zz, 2)), float(np.percentile(zz, 98))), rho=(float(np.percentile(rad, 2)), float(np.percentile(rad, 98))),
                 depth=float(np.percentile(flat, 98) - np.percentile(flat, 2)))
    # (whether the mesh is a scan, asked only of a motion that would pass loosely: see verdict)
    if r["D3"] > TIGHT * r["Dr"] and r["flank_members"] >= FLANKS_IF_LOOSE * max(r["flank_twisted"], 1) and r["around"] >= LOOSE_AROUND:
        r["scan"] = _scan(S)
    return r


def _crest_slope(Xm, c, a):
    """How the radius of a thread's crest changes along it: the slope through the high radii of slices along the axis. None if there are too few to say."""
    v = Xm - a
    z = v @ c
    rho = np.linalg.norm(v - np.outer(z, c), axis=1)
    edges = np.percentile(z, np.linspace(5, 95, 9))
    mid, top = [], []
    for e0, e1 in zip(edges[:-1], edges[1:]):
        inside = (z >= e0) & (z < e1)
        if inside.sum() >= 5:
            mid.append(0.5 * (e0 + e1))
            top.append(float(np.percentile(rho[inside], 90)))
    return float(np.polyfit(mid, top, 1)[0]) if len(mid) >= 4 else None


def _band_axis(X, N, c, a):
    """The axis read from a thread's crest and root bands, or (c, a) as given if they don't hold one. The bands are cylinders round the axis whatever the lead, so they place it
    without the lead's help, where the twisting surfaces' own reading can be a degree or two off on part of a circle: then the true lead lands nowhere, and the refinement and
    the scan settle on half of it, which lands loosely (6 of 27 part threads, 2026-11-06, read to 0.003-0.04 degrees from their bands and found). Points whose normals point
    straight out from the axis, split by radius into crest and root, each its own radius, fitted by Gauss-Newton and trimmed to the points near the fit. Taken only if both bands
    are there, their points lie on the fitted cylinders within BAND_FIT of the depth, and the axis moved no further than a reading can be wrong by (REFINE_TILT, REFINE_PLACE):
    an axis tried beside a thread, where a repeat may be, would otherwise wander off onto another cylinder."""
    c0, a0 = np.asarray(c, float) / np.linalg.norm(c), np.asarray(a, float)
    c, a = c0, a0
    size = None
    for _ in range(2):
        v = X - a
        z = v @ c
        rad = v - np.outer(z, c)
        rho = np.linalg.norm(rad, axis=1)
        if size is None:
            size = max(float(np.median(rho)), 1e-6)
        nr = np.einsum("ij,ij->i", N, rad / np.maximum(rho, 1e-12)[:, None])
        band = (np.abs(N @ c) < LEAN) & (np.abs(nr) >= BAND_RADIAL)
        P = X[band]
        if len(P) < 2 * BAND_POINTS:
            return c0, a0
        r0 = rho[band]
        crest = r0 > 0.5 * (np.percentile(r0, 10) + np.percentile(r0, 90))
        if min(int(crest.sum()), int((~crest).sum())) < BAND_POINTS:
            return c0, a0
        keep = np.ones(len(P), bool)
        R = np.array([np.median(r0[~crest]), np.median(r0[crest])])
        for step in range(12):
            _, e1, e2 = frame(c)

            def off(p, c=c, a=a, e1=e1, e2=e2):
                d = c + p[0] * e1 + p[1] * e2
                d = d / np.linalg.norm(d)
                w = P - (a + p[2] * e1 + p[3] * e2)
                return np.linalg.norm(w - np.outer(w @ d, d), axis=1) - np.where(crest, p[5], p[4])

            p0 = np.r_[0.0, 0.0, 0.0, 0.0, R]
            r = off(p0)
            if step % 4 == 3:
                # (trimmed to the points near the fit: a groove's end or a chamfer's edge also points straight out)
                keep = np.abs(r) <= max(3.0 * float(np.median(np.abs(r[keep]))), 1e-9)
            J = np.empty((len(P), 6))
            for i in range(6):
                p = p0.copy()
                p[i] += 1e-6
                J[:, i] = (off(p) - r) / 1e-6
            sol = np.linalg.lstsq(J[keep], -r[keep], rcond=None)[0]
            d = c + sol[0] * e1 + sol[1] * e2
            c, a, R = d / np.linalg.norm(d), a + sol[2] * e1 + sol[3] * e2, R + sol[4:]
            if np.abs(sol[:4]).max() < 1e-9 * size:
                break
    w = P - a
    r = np.linalg.norm(w - np.outer(w @ c, c), axis=1) - np.where(crest, R[1], R[0])
    depth = abs(float(R[1] - R[0]))
    res = float(np.median(np.abs(r[keep]))) if keep.any() else np.inf
    o = a - a0
    moved = np.linalg.norm(o - (o @ c0) * c0)
    if depth <= 0 or res > BAND_FIT * depth or np.linalg.norm(np.cross(c, c0)) > REFINE_TILT or moved > REFINE_PLACE * size:
        return c0, a0
    return (c if c @ c0 > 0 else -c), a


def _refined(S, Xm, Nm, c, a, L, hand, k, taper, cover=None):
    """The motion that lands a thread's own points closest: axis direction (2 numbers), axis place (2), lead (1) and taper (1), solved together by Gauss-Newton on the distance of each moved point from
    the plane of the triangle it lands on. Moves start short, which lands whatever the first reading's errors, and grow, which sharpens it. Every point moves towards the thread's middle, so it stays on
    the thread: going the better of two ways favours a shorter lead, with less travel and fewer points running off the end. On a thread on part of a circle (cover, as _judge found it) each point
    moves the way its own run of the thread leaves more room, and no further than that room (_room): longer moves ran off a third of a circle, and left its axis 0.26 degrees and 0.055 mm off.

    Flanks and bands are kept apart. Only a flank feels the lead, and a band tells the axis best, so each is trimmed against its own kind: against all together, the bands' tiny distances made every
    flank look like a stray, and the lead wandered with nothing to hold it. A step is kept if the flanks land closer."""
    if len(Xm) > REFINE_POINTS:
        pick = np.linspace(0, len(Xm) - 1, REFINE_POINTS).astype(int)
        Xm, Nm = Xm[pick], Nm[pick]
    a = np.asarray(a, float)
    room = None
    if cover is not None:
        room, free = _room(cover, Xm, hand * L / (2 * math.pi))
        if free.all():
            room = None
        else:
            # (a point whose band of radius goes all round moves as on a whole thread)
            room = np.where(free, np.nan, room)
            keep = free | (room != 0)
            Xm, Nm, room = Xm[keep], Nm[keep], room[keep]
            if len(Xm) < MIN_MEMBERS:
                return c, a, L, k
    span = float(np.percentile((Xm - a) @ c, 98) - np.percentile((Xm - a) @ c, 2))
    if span <= 0:
        return c, a, L, k
    pitch = hand * L / (2 * math.pi)
    done = set()
    start_c, start_a, start_pitch = c, a, pitch
    size0 = max(float(np.median(np.linalg.norm((Xm - a) - np.outer((Xm - a) @ c, c), axis=1))), 1e-6)
    # (each point its own length of move: with one length for all, moving the axis can stand in for changing the lead, and the fit goes off with whichever it likes)
    own = np.random.default_rng(11).uniform(0.4, 1.0, len(Xm))
    first = True
    last = 0.0
    for lever in REFINE_LEVERS:
        turns = min(lever, max(0.1, 0.5 * span / abs(2 * math.pi * pitch)))
        if round(turns, 3) in done:
            continue
        done.add(round(turns, 3))
        angle = hand * 2 * math.pi * turns
        if room is not None and not np.isnan(room).any() and last >= np.abs(room).max():
            # (every point's room was used on the last length already: a longer one is the same moves again)
            break
        last = abs(angle)
        for _ in range(REFINE_STEPS):
            _, e1, e2 = frame(c)

            def motion_of(params, c=c, a=a, pitch=pitch, k=k):
                d = c + params[0] * e1 + params[1] * e2
                return d / np.linalg.norm(d), a + params[2] * e1 + params[3] * e2, pitch + params[4], k + params[5]

            z = (Xm - a) @ c
            mid = 0.5 * (np.percentile(z, 2) + np.percentile(z, 98))
            th = np.where(z < mid, angle, -angle) * own
            if room is not None:
                th = np.where(np.isnan(room), th, np.sign(room) * np.minimum(abs(angle), np.abs(room)) * own)
            leaning = np.abs(Nm @ c) >= LEAN
            if leaning.sum() < 20:
                return c, a, abs(2 * math.pi * pitch), k

            def land(params):
                d, q, p, kk = motion_of(params)
                return screw(Xm, q, d, th, p, kk)

            zero = np.zeros(6)
            Y = land(zero)
            dist, tri = S.nearest(Y, exact_above=np.inf)
            n = S.normals[tri]
            r0 = np.einsum("ij,ij->i", n, Y - S.V[S.F[tri, 0]])
            size = max(float(np.median(np.linalg.norm((Xm - a) - np.outer((Xm - a) @ c, c), axis=1))), 1e-6)
            steps = np.array([1e-4, 1e-4, 1e-4 * size, 1e-4 * size, 1e-4 * max(abs(pitch), 1e-6), 1e-4])
            J = np.empty((len(Xm), 6))
            for i in range(6):
                p = zero.copy()
                p[i] = steps[i]
                J[:, i] = np.einsum("ij,ij->i", n, land(p) - Y) / steps[i]
            # (points that landed on another turn or a crossing groove are left out, by how far they are from the plane, each kind against its own)
            keep = np.zeros(len(Xm), bool)
            for kind in (leaning, ~leaning):
                if kind.any():
                    keep |= kind & (np.abs(r0) <= max(3.0 * float(np.median(np.abs(r0[kind]))), 1e-9))
            if keep.sum() < 20:
                break
            A_ = J[keep]
            scale = np.maximum(np.linalg.norm(A_, axis=0), 1e-12)
            # (the lead alone on the shortest moves: a first reading's lead is its poorest number, and until it is near, nothing lands for the axis to be judged by)
            free = [4] if first else ([0, 1, 2, 3, 4, 5] if taper else [0, 1, 2, 3, 4])
            sol = np.zeros(6)
            sol[free] = np.linalg.lstsq((A_ / scale)[:, free], -r0[keep], rcond=1e-6)[0] / scale[free]
            sol[:2] = np.clip(sol[:2], -0.02, 0.02)
            sol[2:4] = np.clip(sol[2:4], -0.05 * size, 0.05 * size)
            sol[4] = float(np.clip(sol[4], -0.3 * abs(pitch), 0.3 * abs(pitch)))
            sol[5] = float(np.clip(sol[5], -0.02, 0.02))
            nc, na, np_, nk = motion_of(sol)
            # (no further than a reading can be wrong by)
            o = na - start_a
            if (np.linalg.norm(np.cross(nc, start_c)) > REFINE_TILT
                    or np.linalg.norm(o - (o @ start_c) * start_c) > REFINE_PLACE * size0
                    or not 1 / REFINE_LEAD <= abs(np_ / start_pitch) <= REFINE_LEAD):
                break
            d2, _ = S.nearest(land(sol)[leaning], exact_above=np.inf)
            if float(np.median(d2)) > REFINE_GAIN * float(np.median(dist[leaning])):
                break
            c, a, pitch, k = nc, na, np_, nk
        first = False
    return c, a, abs(2 * math.pi * pitch), k


def _with_taper(S, Xm, Nm, c, a, L, hand):
    """The motion with the taper free, or None if a taper doesn't earn its place. Free, it soaks up whatever else is wrong, and read 1.784 for a true 1.800. It must bring the flanks clearly closer,
    and show in the thread itself: the radius of its crest changes along it."""
    c2, a2, L2, k2 = _refined(S, Xm, Nm, c, a, L, hand, 0.0, True)
    if not k2 or abs(k2) > MAX_TAPER:
        return None
    if len(Xm) > REFINE_POINTS:
        pick = np.linspace(0, len(Xm) - 1, REFINE_POINTS).astype(int)
        Xm, Nm = Xm[pick], Nm[pick]
    leaning = np.abs(Nm @ c) >= LEAN
    if leaning.sum() < 20:
        return None

    def closeness(c_, a_, L_, k_):
        z = (Xm[leaning] - a_) @ c_
        mid = 0.5 * (np.percentile(z, 2) + np.percentile(z, 98))
        turns = min(1.0, max(0.1, 0.5 * float(np.ptp(z)) / L_))
        th = np.where(z < mid, 1.0, -1.0) * hand * 2 * math.pi * turns
        return float(np.median(S.nearest(screw(Xm[leaning], a_, c_, th, hand * L_ / (2 * math.pi), k_), exact_above=np.inf)[0]))

    if closeness(c2, a2, L2, k2) > TAPER_GAIN * closeness(c, a, L, 0.0):
        return None
    slope = _crest_slope(Xm, c2, a2)
    if slope is None or abs(slope - k2) > TAPER_AGREES * abs(k2):
        return None
    return c2, a2, L2, k2


def _occupied(rho, z, th, L, hand, cell):
    """Cells of (phase, radius) the points fill, unwrapped at this lead. At a thread's true lead its turns fall on each other, and fill the fewest."""
    ph = (z - hand * L * th / (2 * math.pi)) / L
    k = np.minimum(((ph - np.floor(ph)) * PHASE_BINS).astype(np.intp), PHASE_BINS - 1)
    r = ((rho - rho.min()) / cell).astype(np.intp)
    return np.unique(k * (int(r.max()) + 1) + r).size


def _dips(rho, z, th, lo, hi, cell):
    """The scan's deepest few dips, over both hands, each sharpened. Deepest against the leads around it, not lowest: a long lead fills fewer cells on any flat part, because the phase hardly wraps, so the
    lowest values were all long leads and the true one never got tested."""
    leads = np.geomspace(lo, hi, SCAN_LEADS)
    out = []
    for h in (1, -1):
        s = np.array([_occupied(rho, z, th, L, h, cell) for L in leads], float)
        for i in np.nonzero((s[1:-1] <= s[:-2]) & (s[1:-1] <= s[2:]))[0] + 1:
            near = (leads > leads[i] * (1 - DIP_SPAN)) & (leads < leads[i] * (1 + DIP_SPAN))
            out.append((float(s[i]) / max(float(np.median(s[near])), 1.0), float(leads[i]), h))
    out.sort()
    picked = []
    for _, L, h in out:
        if any(abs(L - q[0]) < SAME_LEAD * L and h == q[1] for q in picked):
            continue
        for fr in (0.01, 0.002):
            f = np.linspace(L * (1 - fr), L * (1 + fr), 41)
            L = float(f[int(np.argmin([_occupied(rho, z, th, x, h, cell) for x in f]))])
        picked.append((L, h))
        if len(picked) >= SCAN_TOP:
            break
    return picked


def _best_taper(rho, z, th, L, h, cell, k0):
    """The taper round k0 that makes this lead fill the fewest cells."""
    ks = np.linspace(k0 - 0.05, k0 + 0.05, 41)
    return float(ks[int(np.argmin([_occupied(rho - k * z, z, th, L, h, cell) for k in ks]))])


def _twist_scale(rho):
    """What the twist floor is a share of, with the lead (TWIST_FLOOR x min(lead, this)): how far pure rotation can move a point of a
    twisting surface in this ring. The ring's largest radius stood for it, which a thread's lead usually undercuts; but rotated, a point
    lands within about half its ring's depth of the surface whatever the radius, and on a large shallow ring nothing cleared the
    floor: 01_Ring_Gear's teeth, 2.7 mm deep at r 36.7, move 0.76 mm (median, 0.92 at 90%) against a floor of 1.10, so none of its
    points counted as twisting and its screw sweep (lead 216.2) was judged on none. So at most TWIST_DEPTHS depths of the ring (99% to
    1% of its points' radii). Used only when the radius's floor leaves fewer than TWIST_FALLBACK of the leaning points twisting (_judge):
    used always, it moved 15 threads' readings in the 550 (a sine thread's pieces 8,460 -> 7,200), threads whose lead is more than
    two of their depths."""
    rho = np.asarray(rho, float)
    if not len(rho):
        return 0.0
    return min(float(rho.max()), TWIST_DEPTHS * float(np.percentile(rho, 99) - np.percentile(rho, 1)))


def _went_round(r):
    """A reading that failed only on how tightly it lands: enough of its points and its flanks' land, it travels round its helix and
    goes all round (LOOSE_AROUND), no deeper than a thread is, and most of the leaning surface lands (FLANKS_IF_LOOSE). It is near a
    thread, and only its lead is off. A lead read from facet normals can be: on a fine thread at a large radius the lead is a lean of
    a degree or two along the helix, which normals good to about a degree can't tell. gutter-mount's internal M30x3.5 (r 13.25 to
    15.20) read 3.150 and 2.970 from its two flanks, the refit ran on to half the lead, and every reading failed with the thread's
    turns, round and flanks all there (4.5 turns, 353 to 359 degrees, 0.90 of the flanks); the scan, skipped on an axis whose
    readings had all failed, finds 3.4994 landing at 0.006 of rotation's reach, and the thread builds (9,233 facets in pieces)."""
    if not r or r.get("members", 0) < MIN_MEMBERS or r.get("flank_members", 0) < MIN_FLANK_MEMBERS:
        return False
    if r["turns"] * r["around"] / 360.0 < MIN_WINDING or r["around"] < LOOSE_AROUND or r["depth"] > DEPTH_OF_LEAD * r["lead"]:
        return False
    return r["flank_members"] / max(r["flank_twisted"], 1) >= FLANKS_IF_LOOSE


def _no_slight_taper(k):
    """A refined taper under MIN_TAPER is no taper: refined from a tapered try, a straight thread's taper is driven towards nothing
    and stops a hair short, and a taper of any size builds the crest and root as cones (merged._thread_flat). funnel's screw-on rim,
    tried at the slope of the flare round it (_best_taper, -0.0076), was refined to lead 2.9994 and a taper of -0.000125, and its
    crest and root, designed cylinders, came out as cones of 0.007 degrees. MIN_TAPER is the least slope worth trying a taper for
    (0.3 degrees; pipe threads have 1.8): under it the thread is straight, and judged so."""
    return 0.0 if abs(float(k)) < MIN_TAPER else k


def _untold(r, rho):
    """A reading that went round as a thread does (enough of its points and flanks land, half a turn of winding, LOOSE_AROUND, no
    deeper than a thread) at a lead facet normals can't tell at this radius: its helix leans under LEAN_TELL degrees, and normals are
    good to about one, so the reading may be far off and its failure says nothing of the axis. universal-clamp-side-top-40x40-nut's
    internal thread of lead 2.5, on a coarse mesh at r 8.7, read 1.689 (a lean of 1.76 degrees) and only 0.39 of its flanks landed, so
    _went_round didn't hold; scanned, it lands at 2.4964 (0.008 of rotation's reach) and builds 1,961 facets in pieces. On the 550
    parts this scans 3 more axes in all (5.9 s), each finding a thread that lands: that one, gutter-mount-vb-ntype's internal M30
    and funnel's 3.0 lead at r 69, which neither finder had found."""
    if not r or r.get("members", 0) < MIN_MEMBERS or r.get("flank_members", 0) < MIN_FLANK_MEMBERS:
        return False
    if r["turns"] * r["around"] / 360.0 < MIN_WINDING or r["around"] < LOOSE_AROUND or r["depth"] > DEPTH_OF_LEAD * r["lead"]:
        return False
    return math.degrees(math.atan(r["lead"] / (2 * math.pi * max(rho, 1e-9)))) < LEAN_TELL


def _test_axis(S, X, N, c, a, scanned, readings, taken, first_flanks, may_scan):
    """Readings first, the scan on demand. Returns the rows, none of them a thread already found."""
    rho, z, th = scanned
    if not len(z) or (len(z) < SCAN_MIN and not readings):
        return []
    rm, span = float(rho.max()), float(np.ptp(z))
    if span <= 0:
        return []
    rows = []
    fresh = lambda L, h: not any(h == h2 and abs(L - L2) < SAME_LEAD * L for L2, h2 in taken)
    sure = lambda r: verdict(dict(r, flank_twisted=max(r["flank_twisted"], first_flanks))) and r["sc"] == 1.0 and r["D3"] <= TIGHT * r["Dr"]
    Dr0 = rotated(S, X, a, c)
    flank0 = np.abs(N @ c) >= LEAN

    def tried(L, h, k):
        start = len(rows)
        # (the reading as read is judged about this axis, on these points: rotation's reach there is Dr0, measured once. Measured again in the judge, it was a tenth of
        # the whole search's time, timed side by side on 54 parts, with every reading the same)
        as_read = _judged(S, X, N, c, a, L, h, k, rm, span, Dr0)
        rows.append(as_read)
        if sure(as_read) and not k:
            slope = _crest_slope(X, c, a)
            if slope is None or abs(slope) < MIN_TAPER:
                return
        # (then refined, so that an axis a degree off, as facet normals leave it, has a chance: on a fine thread it lands nowhere until it has been. Leaning surfaces rotation doesn't move, cones,
        # stay out: they land better and better as the lead goes to nothing, which pulled a true 1.000 down to 0.617. Cylinders stay, since they say where the axis is and nothing of the lead)
        use = ~(flank0 & ~(Dr0 > TWIST_FLOOR * min(L, rm)))
        if use.sum() < MIN_MEMBERS:
            return
        nc, na, nL, nk = _refined(S, X[use], N[use], c, a, L, h, k, bool(k), as_read.get("cover"))
        nk = _no_slight_taper(nk)
        if fresh(nL, h):
            rows.append(_judged(S, X, N, nc, na, nL, h, nk, rm, span))
        # (a taper only if the crest slopes, which is measured, not guessed from how a straight thread lands: on a thread that tapers, a straight reading at a slightly wrong lead can land well)
        if not nk:
            slope = _crest_slope(X[use], nc, na)
            if slope is not None and abs(slope) >= MIN_TAPER:
                t = _with_taper(S, X[use], N[use], nc, na, nL, h)
                if t is not None and fresh(t[2], h):
                    tapered = _judged(S, X, N, t[0], t[1], t[2], h, t[3], rm, span)
                    if verdict(tapered):
                        # (a taper that earned its place is this thread better told, so the straight readings it grew from leave the choice: on a coarse mesh one of them lands
                        # nearly as well, and won on sharpness by chance, so a 1:16 Acme came out straight at 3.994 for 4.000)
                        del rows[start:]
                    rows.append(tapered)

    for L, h in readings:
        if fresh(L, h) and L >= 0.1:
            tried(L, h, 0.0)
    # (a reading that passes on long moves and lands tight is sure. One that passes loosely, or only on short moves, may be near the true lead, not on it: the scan looks too. An axis
    # whose surfaces read leads that all failed outright has nothing near a thread to look round, and on parts with none, scanning such axes cost 10 s of every 27; an axis with no
    # reading at all, the whole part's or a stack of cones', is scanned as before, and every known thread only the scan finds came from one. A reading that failed only on how
    # tightly it lands, though, went round as a thread does and is near one: _went_round; and one that went round whose lead facet
    # normals can't tell says nothing by failing: _untold)
    rho_mid = float(np.median(rho))
    near = not readings or any(verdict(r) or _went_round(r) or _untold(r, rho_mid) for r in rows)
    if may_scan and near and not any(sure(r) for r in rows) and len(z) >= SCAN_MIN:
        # (straight, and with the plain slope of radius on z taken out, since a shank or a head can give a straight thread a false slope)
        cell = max(0.02, 0.004 * rm)
        fit = float(np.polyfit(z, rho, 1)[0])
        longest = min(max(6 * rm, 3.0), max(span / MIN_WINDING, 0.3))
        for k0 in ((0.0, fit) if abs(fit) >= 0.004 else (0.0,)):
            for L, h in _dips(rho - k0 * z, z, th, SHORTEST_LEAD, longest, cell):
                if fresh(L, h):
                    tried(L, h, _best_taper(rho, z, th, L, h, cell, k0) if k0 else 0.0)
    rows = _without_unearned_tapers(rows)
    for r in rows:
        r["flank_twisted"] = max(r["flank_twisted"], first_flanks)
    return rows


def _without_unearned_tapers(rows):
    """The rows, less each tapered reading that doesn't land clearly closer than a straight reading of the same thread that passes (same hand, lead within
    SAME_LEAD): its flanks have to land TAPER_GAIN as far, the bar _with_taper sets. A designer draws straight unless told otherwise. The scan's own tapered
    readings (_best_taper) competed with the straight ones on sharpness alone: on a half thread 1.9 turns long one with a taper of -0.027 landed as the
    straight one did (0.0029 against 0.0030) and won by 0.417 against 0.413, and another's +0.004 stood the same way (sweep, 2026-11-07)."""
    straight = [q for q in rows if not q["k"] and verdict(q)]
    return [r for r in rows if not r["k"] or not any(q["hand"] == r["hand"] and abs(q["lead"] - r["lead"]) < SAME_LEAD * r["lead"] and r["D3"] > TAPER_GAIN * q["D3"]
                                                     for q in straight)]


def _tried_as_copy(tried, d, at, L, hand, R):
    """Whether this thread (lead L, hand, outer radius R) was tried already as a copy on this axis (direction d through at): the same thread, of the same hand
    and a lead within SAME_LEAD, on the same line, whatever digits its reading carries. tried holds (direction, point, lead, hand, outer radius) of each copy.
    Keyed by rounded numbers instead, every re-read of a thread was a new thread whose copies were all tried again: 95 axes tested on a hand screw with one
    thread, and 86 on a greaser. Skipping a copy on its own source's axis as well cut that, but lost the better reading: the copy there reads the thread again
    with its own radii as the ring, and on tc_greaser that reading (sharp 0.337, rho 2.94-4.08) beat the first (0.293, 3.02-4.15), whose conversion came out
    with 680 faces, 18 of them freeform, against 573 and 3 (2026-11-07)."""
    return any(h == hand and abs(L2 - L) < SAME_LEAD * L and same_axis(c2, a2, d, at, max(R, R2)) for c2, a2, L2, h, R2 in tried)


def _same_thread(r, q):
    """Two axes, near enough the same, read one thread: same hand, on the same line, overlapping in radius and along it."""
    if r["hand"] != q["hand"] or abs(r["c"] @ q["c"]) < math.cos(math.radians(3)):
        return False
    off = r["a"] - q["a"]
    if np.linalg.norm(off - (off @ r["c"]) * r["c"]) > 0.2 * max(r["rho"][0], q["rho"][0], 0.5):
        return False
    if min(r["rho"][1], q["rho"][1]) <= max(r["rho"][0], q["rho"][0]):
        return False
    way = 1.0 if r["c"] @ q["c"] > 0 else -1.0
    shift = float(off @ r["c"])
    z0, z1 = sorted((way * q["z"][0] - shift, way * q["z"][1] - shift))
    return min(r["z"][1], z1) > max(r["z"][0], z0)


def _one_each(S, V, F, rows, seed=13):
    """One reading of each thread over the part's axes. Taken in order of those judged on whole moves first, then the sharpest, but two readings of one thread (_same_thread) are
    compared on the same points, and the one that lands them closer is kept (_lands_closer): each reading's sharpness was counted on its own ring, and a reading from an axis 0.18
    degrees tilted, 0.3% short in lead, counted sharper on its narrower ring and was kept, landing 0.066 of rotation's reach on tc_greaser against the true one's 0.0007."""
    rng = np.random.default_rng(seed)
    kept = []
    for r in sorted((r for r in rows if verdict(r)), key=lambda r: (-r["sc"], -r["sharp"])):
        for i, q in enumerate(kept):
            if _same_thread(r, q):
                if _lands_closer(S, V, F, r, q, rng):
                    kept[i] = r
                break
        else:
            kept.append(r)
    return kept


def _lands_closer(S, V, F, r, q, rng):
    """Whether reading r lands the points of the ring both readings lie in closer than reading q does: by the median, over the leaning surface both motions move, of how far each
    point lands for how far rotation through the same angles takes it. Points sampled afresh, so neither reading is judged on the points it was found on."""
    c, a = q["c"] / np.linalg.norm(q["c"]), np.asarray(q["a"], float)
    lo, hi = min(r["rho"][0], q["rho"][0]), max(r["rho"][1], q["rho"][1])
    Fa = _in_ring(V, F, c, a, (lo, hi))
    if len(Fa) < 30:
        return False
    X, N, _ = points(V, Fa, rng, TEST_POINTS)
    (D3r, Dsr, sr), (D3q, Dsq, sq) = _land(S, X, r), _land(S, X, q)
    use = ((Dsr > TWIST_FLOOR * min(r["lead"], hi)) & (Dsq > TWIST_FLOOR * min(q["lead"], hi)) & (np.abs(N @ c) >= LEAN)
           & (np.asarray(sr) != 0) & (np.asarray(sq) != 0))
    if use.sum() < MIN_MEMBERS:
        return False
    return float(np.median(D3r[use] / Dsr[use])) < float(np.median(D3q[use] / Dsq[use]))


def _in_ring(V, F, c, a, ring, reaching=False):
    """The triangles lying wholly in a ring round the axis; with reaching, also (apart, second) those that only reach into it."""
    lo, hi = ring
    m = RING_MARGIN * (hi - lo) + RING_MARGIN_MM
    v = V[F].reshape(-1, 3) - a
    r3 = np.linalg.norm(v - np.outer(v @ c, c), axis=1).reshape(-1, 3)
    inside = (r3.min(axis=1) >= lo - m) & (r3.max(axis=1) <= hi + m)
    if reaching:
        return F[inside], F[(r3.max(axis=1) >= lo - m) & (r3.min(axis=1) <= hi + m) & ~inside]
    return F[inside]


def surface_of(V, F, seed=5):
    """The mesh as a surface points can be measured against. Every triangle gets a sample point of its own, so a small thread on a big part is as well served as anything."""
    from mmm.fidelity import Surface
    rng = np.random.default_rng(seed)
    area = float(0.5 * np.linalg.norm(np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]]), axis=1).sum())
    S = Surface(V, F, math.sqrt(area / 400000), rng, 400000)
    # (thousands of small queries: starting every core for each costs more than it brings)
    S.workers = 1
    return S


def _explains(S, r, X):
    """Do most of these points land under a thread found, where pure rotation would move them? (Off a part thread's own runs a point isn't moved, and isn't under it.)"""
    D3, Ds, s = _land(S, X, r)
    return float(((D3 <= MEMBER_RATIO * Ds) & (np.asarray(s) != 0)).mean()) >= PIECE_OF


def _centre_along(P, d):
    """The point round which these points lie, seen along unit direction d: the centre of the circle through them in the plane square to d, at their middle along d. None if they
    lie on no circle (all in a line, or too few)."""
    if len(P) < 3:
        return None
    _, e1, e2 = frame(d)
    x, y = P @ e1, P @ e2
    A = np.c_[2 * x, 2 * y, np.ones(len(x))]
    sol, _, rank, _ = np.linalg.lstsq(A, x * x + y * y, rcond=None)
    if rank < 3:
        return None
    return sol[0] * e1 + sol[1] * e2 + float(np.mean(P @ d)) * d


def _pooled(regs):
    """One screw motion read from these regions' points together, trimmed to the points near it: (c, a, p, eigenvalues, points kept, their normals, the misfit limit)."""
    X = np.vstack([r["x"] for r in regs])
    N = np.vstack([r["nrm"] for r in regs])
    W = np.concatenate([r["w"] for r in regs])
    keep = np.ones(len(X), bool)
    for _ in range(POOL_TRIMS):
        c, a, p, lam = motion(X[keep], N[keep], W[keep])
        m = misfit(X, N, c, a, p)
        limit = max(POOL_FLOOR, 2 * float(np.median(m[keep])))
        new = m < limit
        if new.sum() < POOL_KEEP or (new == keep).all():
            break
        keep = new
    return c, a, p, lam, X[keep], N[keep], limit


def _flank_pitch(X, N, c, a):
    """The pitch the leaning points read about this axis, each its own (rho n_theta + p n_z = 0, as _read_one reads a region's), by the median; None if too few lean. A crest
    or root band's normal has nothing along the axis and says nothing of the lead: in a least-squares fit of a pool they pulled the lead 25% to 89% short."""
    v = X - a
    rad = v - np.outer(v @ c, c)
    rho = np.linalg.norm(rad, axis=1)
    nz = N @ c
    use = (np.abs(nz) >= LEAN) & (rho > 0)
    if use.sum() < MIN_POINTS:
        return None
    nth = np.einsum("ij,ij->i", N[use], np.cross(c, rad[use] / rho[use][:, None]))
    return float(np.median(-rho[use] * nth / nz[use]))


def _ring_leads(V, F, c, a):
    """The rings round an axis that twist steadily: (smallest radius, largest, lead, hand, the facets' centres there), largest
    first. A fine thread's flank leans so little along its helix that a pool of the surfaces round it reads the axis well and the
    lead as nothing: ABC 129's lens thread (lead 0.699 at radius 8.4) moves a facet's normal 0.65 degrees from where rotation
    would, and the lens's own surfaces of revolution on that axis, outnumbering its flanks, held the pool's lead to 0.008. Each
    ring's facets read the lead themselves, each its own (rho n_theta + p n_z = 0, as _read_one reads a region's), by the median,
    and a ring is steady by _read_one's test over its facets: there the thread's two rings read 0.710 and 0.683, 52 and 60
    standard errors from nothing, and every other ring of the part under 1. Rings are RING_BINS bins of the leaning facets'
    radius; neighbouring steady bins of one hand and lead are one ring."""
    T = V[F]
    nrm = np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0])
    ln = np.linalg.norm(nrm, axis=1)
    ok = ln > 1e-12
    X, N = T[ok].mean(axis=1), nrm[ok] / ln[ok][:, None]
    v = X - a
    rad = v - np.outer(v @ c, c)
    rho = np.linalg.norm(rad, axis=1)
    nz = N @ c
    use = (np.abs(nz) >= LEAN) & (rho > 0)
    if use.sum() < MIN_POINTS:
        return []
    X, rho, rad, N, nz = X[use], rho[use], rad[use], N[use], nz[use]
    own = -rho * np.einsum("ij,ij->i", N, np.cross(c, rad / rho[:, None])) / nz
    hi = float(np.percentile(rho, 99.5))
    k = np.minimum((rho / (hi / RING_BINS)).astype(int), RING_BINS)
    bins = []
    for b in np.unique(k):
        m = k == b
        if m.sum() < MIN_POINTS:
            continue
        o = own[m]
        mid = float(np.median(o))
        L = 2 * math.pi * abs(mid)
        r = (b + 0.5) * hi / RING_BINS
        steady = abs(mid) > STEADY * 1.4826 * float(np.median(np.abs(o - mid))) / math.sqrt(int(m.sum()))
        # (and only a lead facet normals can't tell, its helix leaning under LEAN_TELL as _untold measures it: that is the lead a
        # pool misses, where a steeper helix is read by its own regions. Steady by its size alone, a ring of thousands of facets on a
        # part with no thread read leads of 0.06-0.53 of its circumference (4-28 degrees), and each cost a test of up to 19 s for
        # nothing: ABC 00008955 2.7 -> 20.2 s. 129's lens thread leans 0.75 degrees. wt_129b, 2026-11-19)
        if steady and SLOW * 2 * math.pi * r < L < 2 * math.pi * r * math.tan(math.radians(LEAN_TELL)):
            bins.append((int(b), L, 1 if mid > 0 else -1, m))
    out = []
    for b, L, h, m in bins:
        if out and out[-1][4] == b - 1 and out[-1][3] == h and abs(out[-1][2] - L) < 0.5 * out[-1][2]:
            lo, _, _, _, _, mm = out[-1]
            mm = mm | m
            out[-1] = (lo, (b + 1) * hi / RING_BINS, 2 * math.pi * abs(float(np.median(own[mm]))), h, b, mm)
        else:
            out.append((b * hi / RING_BINS, (b + 1) * hi / RING_BINS, L, h, b, m))
    return sorted(((lo, up, L, h, X[mm]) for lo, up, L, h, _, mm in out), key=lambda t: -len(t[4]))


def _joint_seeds(left, V=None, F=None):
    """Threads on part of a circle, read from the twisting regions no thread found explains, pooled: what to test for them, best first, as hypotheses() gives.

    On 60 degrees of a circle each flank strip, one a turn, reads a motion of its own with the axis 23 to 25 degrees off and the lead and radius far out, so no group of
    them makes a hypothesis near the axis. Read together, their axis comes out within a degree (median 0.02 over 134 seeds on known threads). So the regions are pooled:
    all of them, then each hand's; a motion is read from the pool, trimmed to its points (_pooled), and the regions it doesn't fit are pooled again for the next. Each
    motion with a lead a facet can show is a seed. Its lead is read from its flanks (_flank_pitch), and its ring is taken as a hypothesis's is (_ring_of over its
    leaning points): a percentile of every pooled point took in the other surfaces the pool caught, and on one part made a thread's reading run 12 mm past its end, so
    the conversion fell back to the mesh's facets."""
    out = []
    pools = [[r for r in left if r["hand"] > 0], [r for r in left if r["hand"] < 0]]
    for pool in [left] + pools:
        for _ in range(MAX_THREADS):
            if sum(r["n"] for r in pool) < MIN_POINTS:
                break
            c, a, p, lam, Xk, Nk, limit = _pooled(pool)
            if lam[1] < ONE_MOTION * max(lam[0], 1e-18) or not p:
                break
            c = c / np.linalg.norm(c)
            pf = _flank_pitch(Xk, Nk, c, np.asarray(a, float))
            q = pf if pf else p
            v = Xk - a
            rho = np.linalg.norm(v - np.outer(v @ c, c), axis=1)
            L = 2 * math.pi * abs(q)
            if (SLOW * 2 * math.pi * float(np.median(rho)) < L < LONG * float(np.median(rho))
                    and not any(same_axis(h["c"], h["a"], c, a, max(float(np.median(rho)), h["rho"])) for h in out)):
                out.append(dict(c=c, a=np.asarray(a, float), rho=float(np.median(rho)), n=len(Xk), ring=_ring_of(_leaning(dict(x=Xk, nrm=Nk), c), c, np.asarray(a, float)),
                                readings=[(L, 1 if q > 0 else -1)], sample=Xk[np.linspace(0, len(Xk) - 1, min(len(Xk), SAMPLE_POINTS)).astype(int)]))
            elif RING_LEADS and V is not None and L <= SLOW * 2 * math.pi * float(np.median(rho)):
                # (an axis the pool reads with no lead is still an axis: the rings round it may twist, too finely for the pool to tell; _ring_leads)
                for lo, up, Lr, hr, Xr in _ring_leads(V, F, c, np.asarray(a, float)):
                    r_mid = 0.5 * (lo + up)
                    if any(same_axis(h["c"], h["a"], c, a, max(r_mid, h["rho"])) and min(up, h["ring"][1]) > max(lo, h["ring"][0]) for h in out):
                        continue
                    out.append(dict(c=c, a=np.asarray(a, float), rho=r_mid, n=len(Xr), ring=(lo, up), readings=[(Lr, hr)],
                                    sample=Xr[np.linspace(0, len(Xr) - 1, min(len(Xr), SAMPLE_POINTS)).astype(int)]))
            # (the pool splits by the pooled motion: the regions it doesn't fit are the next pool)
            rest = [r for r in pool if float(np.median(misfit(r["x"], r["nrm"], c, a, p))) >= limit]
            if len(rest) == len(pool):
                break
            pool = rest
    return out


def motions(V, F, S=None, seed=5):
    """The threads of a mesh, as one row each: motion (c, a, lead, hand, k), ring, reach along the axis and how they were judged. Deterministic: its only randomness has a fixed seed."""
    V, F = np.asarray(V, float), np.asarray(F)
    rng = np.random.default_rng(seed)
    S = surface_of(V, F, seed) if S is None else S
    twisting = [] if JOINT_SEEDS else None
    every = hypotheses(V, F, rng, twisting)
    gs = every[:MAX_AXES]
    c0, a0, share0 = _whole_part(V, F)
    c0 = frame(c0)[0]
    out = []
    biggest = max([g["n"] for g in gs] or [1])

    whole = dict(c=c0, a=a0, n=-1, ring=None, readings=[])
    first = share0 >= WHOLE_FIRST and not any(same_axis(g["c"], g["a"], c0, a0, g["rho"]) for g in gs)

    def in_turn():
        if first:
            yield whole
        for g in gs:
            # (a piece of a thread already found reads its own axis and lead poorly, and is no thread of its own)
            if any(_explains(S, r, g["sample"]) for r in out if verdict(r)):
                continue
            yield dict(c=frame(g["c"])[0], a=g["a"], n=g["n"], ring=g["ring"], readings=g["readings"])
        # (a second try, on demand: only if nothing was found on the axes the surfaces read, and most of the part fits this one motion)
        if not first and not any(verdict(r) for r in out) and (share0 >= WHOLE_SHARE or not gs):
            yield whole
        # (threads repeat: a board with one tapped hole had six alike. A small thread on a large part reads its own lead poorly or not at all, and its axis ranks below the part's
        # other surfaces: four of the six were lost, two reading leads of 0.024 for 0.350 and two past the budget of hypotheses. So wherever a surface wraps round an axis
        # parallel to a thread found, past the budget too, that axis is tried as that thread: its lead the reading, its radii the ring, with no scan. The surface says only where
        # the axis is: seen along the thread's direction, which is exact, its points lie on a circle round it. Neither its own ring nor its own axis is asked to match: at one
        # hole the only surfaces were the counterbore round the thread, at another the drill's point below it, read from 60 points with axes 7 to 15 degrees off)
        # (only a thread found surely vouches for its copies: a loose reading on a window mount, at a lead of 54.5 on a surface no thread was built on, had every axis of
        # the part tried as its copy, 25 s for nothing. Surely is landing within what the conversion allows this mesh (_allowance), as the fidelity check judges a face,
        # whether the thread went all round or not: judged on whole moves as it was before, a board's first tapped hole, 308 degrees round past a gap in its wall,
        # landing 0.0002 mm of an allowance of 0.05, vouched for none of its five twins, and the board lost five of its six threads. A board's tapped holes land a
        # hundred times tighter than a resemblance)
        # (each thread is tried once on each axis, its own included: _tried_as_copy)
        tried = []
        for g in every:
            for r in [r for r in out if verdict(r) and r["D3"] <= _allowance(S)]:
                if any(_explains(S, q, g["sample"]) for q in out if verdict(q)):
                    break
                d = r["c"]
                at = _centre_along(g["sample"], d)
                if at is None:
                    continue
                L, R = float(r["lead"]), float(r["rho"][1])
                if _tried_as_copy(tried, d, at, L, r["hand"], R):
                    continue
                tried.append((d, at, L, r["hand"], R))
                yield dict(c=d, a=at, n=g["n"], ring=(float(r["rho"][0]), float(r["rho"][1])), readings=[(r["lead"], r["hand"])], repeat=True)
        # (threads on part of a circle, on demand (JOINT_SEEDS): the twisting regions no thread found explains, pooled into seeds (_joint_seeds), up to MAX_AXES of their
        # own. Out of pass 1's budget, a two-thread part had none left; the first MAX_SCANS seeds may scan, as pass 1's first hypotheses may. No seed is dropped on a failed
        # first look: on Acme and worm forms seeds 6% to 17% off in lead fail it and keep their thread after the refit)
        if JOINT_SEEDS:
            samp = lambda g: g["x"][np.linspace(0, len(g["x"]) - 1, min(len(g["x"]), SAMPLE_POINTS)).astype(int)]
            left = [g for g in twisting if not any(_explains(S, r, samp(g)) for r in out if verdict(r))]
            seeds = _joint_seeds(left, V, F)[:MAX_AXES] if sum(g["n"] for g in left) >= MIN_POINTS else []
            for i, s in enumerate(seeds):
                if any(_explains(S, r, s["sample"]) for r in out if verdict(r)):
                    continue
                yield dict(c=frame(s["c"])[0], a=s["a"], n=s["n"], ring=s["ring"], readings=s["readings"], seed_rank=i)

    rank = -1
    for ax in in_turn():
        rank = ax["seed_rank"] if "seed_rank" in ax else rank + (ax["n"] >= 0)
        c, a = ax["c"], ax["a"]
        Fa = F
        if ax["ring"]:
            # (a thread small on a big part: all the samples on the triangles in its ring, or it is lost among the rest)
            Fa = _in_ring(V, F, c, a, ax["ring"])
            if len(Fa) < 30:
                continue
        Xt, Nt, _ = points(V, Fa, rng, TEST_POINTS)
        Xa, Na, _ = points(V, Fa, rng, SCAN_POINTS)
        # (the axis from the thread's crest and root bands where they hold one: on part of a circle the twisting surfaces read it a degree or two off)
        c, a = _band_axis(Xt, Nt, c, a)
        _, e1, e2 = frame(c)
        v = Xa - a
        z = v @ c
        rad = v - np.outer(z, c)
        rho = np.linalg.norm(rad, axis=1)
        nz = np.abs(Na @ c)
        nr = np.abs(np.einsum("ij,ij->i", Na, rad / np.maximum(rho, 1e-12)[:, None]))
        # (a square thread's flanks are nearly flat rings, so they'd go with the real flat rings; but a real flat ring has no twist at all)
        keep = ((nz < 0.985) & (nr < 0.985)) | ((nz >= 0.985) & (np.sqrt(np.maximum(1 - nz * nz - nr * nr, 0.0)) > FLAT_TWIST))
        q, w, u = rho[keep], z[keep], np.arctan2(v[keep] @ e2, v[keep] @ e1)
        Xq, Nq = Xa[keep], Na[keep]
        if not len(q):
            continue
        taken = []
        first_flanks = 0
        for found in range(MAX_THREADS):
            # (the scan is the slow part: only on a part's largest few hypotheses, and not on a speck beside a thread already found)
            may_scan = not ax.get("repeat") and (ax["n"] < 0 or (rank < MAX_SCANS and (found > 0 or not any(verdict(r) for r in out) or ax["n"] >= SCAN_SHARE * biggest)))
            rows = _test_axis(S, Xt, Nt, c, a, (q, w, u), ax["readings"], taken, first_flanks, may_scan)
            if not rows:
                break
            # (longer moves first: short ones can't tell a lead from one near it, since they land close and rotation moves a steep flank about as far)
            best = max(rows, key=lambda r: (verdict(r), r["sc"], r["sharp"], r["share"]))
            if not verdict(best):
                break
            nc, na, nL, nk = _refined(S, Xt[best["member"]], Nt[best["member"]], best["c"], best["a"], best["lead"], best["hand"], best["k"], bool(best["k"]), best.get("cover"))
            nk = _no_slight_taper(nk)
            again = _judged(S, Xt, Nt, nc, na, nL, best["hand"], nk, float(q.max()), float(np.ptp(w)))
            again["flank_twisted"] = max(again["flank_twisted"], first_flanks)
            if verdict(again) and (again["sc"], again["sharp"]) >= (best["sc"], best["sharp"]):
                best = again
            best["ring"] = ax["ring"]
            out.append(best)
            taken.append((best["lead"], best["hand"]))
            first_flanks = max(first_flanks, best["flank_twisted"])
            if best["flank_members"] >= EXPLAINED * max(best["flank_twisted"], 1):
                break
            # (its flanks leave the scan and the test, so another thread on this axis can show: the other hand, or a crossing sweep. Only its flanks: bands land under any motion near the lead, and the
            # patches left between crossing sweeps land under both, so taking them all, the other sweep lost its members too)
            pick = np.linspace(0, len(Xq) - 1, min(len(Xq), 20000)).astype(int)
            D3, Dr, _ = _land(S, Xq[pick], best)
            mine = (Dr > TWIST_FLOOR * min(best["lead"], float(q.max()))) & (D3 <= REMOVE_RATIO * Dr) & (np.abs(Nq[pick] @ best["c"]) >= LEAN)
            q, w, u, Xq, Nq = q[pick][~mine], w[pick][~mine], u[pick][~mine], Xq[pick][~mine], Nq[pick][~mine]
            gone = best["member"] & best["flank"]
            Xt, Nt = Xt[~gone], Nt[~gone]
            if len(Xt) < MIN_MEMBERS or not len(q):
                break
    kept = _one_each(S, V, F, out)
    for r in kept:
        r.pop("member", None)
        r.pop("flank", None)
    return S, kept


def _landed_points(S, V, F, thread, rng):
    """Points on a thread that land under its motion and aren't left where they are by plain rotation, with the band points between them: (points, normals)."""
    c, a, L, k = thread["c"], thread["a"], thread["lead"], thread["k"]
    lo, hi = thread["ring"] or thread["rho"]
    # (the ring it was read on, or for a thread found on the whole part's axis, its flanks' ring and half as much again)
    Fa, Fs = _in_ring(V, F, c, a, (lo, hi) if thread["ring"] else (lo - 0.25 * (hi - lo), hi + 0.25 * (hi - lo)), reaching=True)
    if len(Fa) < 30:
        return None
    X, N, _ = points(V, Fa, rng, PROFILE_POINTS)
    # (and the triangles that only reach into the ring, drawn at the same rate after them, at most as many points again: the ring is where a region's points gather
    # most, and can stop short of the flanks' ends. 05_Pivot's rounded crest, 8,410 small triangles, outweighed its flanks, 2,199 long ones each running root to crest;
    # its ring came out 3.41-3.69 mm for a thread 3.00-3.69 deep, no flank triangle lay wholly in it, the profile was read from the crest alone, and the part was saved
    # with 98 faces, 21 with them. What isn't the thread's own surface doesn't land under its motion, and is left out below. Added after, not drawn instead: shared
    # out among more triangles, every thread's points came out otherwise, and 39 of 543 parts changed, as many for the worse; added, 12 changed, 9 for the better.
    # Counted by area instead, the ring took in a clamp's flat face, 8 to 47 mm round a thread 8 to 10 mm, and the thread was lost)
    if len(Fs):
        T = V[Fs]
        ws = float(np.sqrt(0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)).sum())
        T = V[Fa]
        wa = float(np.sqrt(0.5 * np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1)).sum())
        more = int(min(PROFILE_POINTS, round(PROFILE_POINTS * ws / max(wa, 1e-30))))
        if more > 0 and ws > 0:
            X2, N2, _ = points(V, Fs, rng, more)
            X, N = np.vstack([X, X2]), np.vstack([N, N2])
    D3, Dr, s = _land(S, X, thread)
    tested = (np.asarray(s) != 0)
    # (whether rotation moves a point at all is asked at full angles, as when it was judged. A short rotation moves a steep thread less than that limit, and every point of a 6-start nut was lost.
    # Off a part thread's own runs a point isn't moved at all, and tells nothing)
    moves = (rotated(S, X, a, c) > TWIST_FLOOR * min(L, float(np.linalg.norm((X - a) - np.outer((X - a) @ c, c), axis=1).max()))) & tested
    v = X - a
    z = v @ c
    rho = np.linalg.norm(v - np.outer(z, c), axis=1)
    sure = moves & (D3 <= TIGHT_LAND * Dr) & (np.abs(N @ c) >= LEAN)
    if sure.sum() < 50:
        return None
    # (the thread's own span and ring come from its flanks; bands inside them count too, as they land just as well though rotation may not move them)
    z0, z1 = np.percentile(z[sure], [1, 99])
    flat = rho - k * z
    r0, r1 = np.percentile(flat[sure], [1, 99])
    m = 0.05 * (r1 - r0) + 1e-6
    keep = (z >= z0) & (z <= z1) & (flat >= r0 - m) & (flat <= r1 + m) & (D3 <= TIGHT_LAND * max(float(np.median(Dr[sure])), 1e-9)) & tested
    return X[keep]


def _simplify(P, tol):
    """Douglas and Peucker: the fewest points that stay within tol of the curve."""
    keep = np.zeros(len(P), bool)
    keep[[0, -1]] = True
    stack = [(0, len(P) - 1)]
    while stack:
        a, b = stack.pop()
        if b - a < 2:
            continue
        d = P[b] - P[a]
        L = np.hypot(*d)
        rel = P[a + 1:b] - P[a]
        off = np.abs(rel[:, 0] * d[1] - rel[:, 1] * d[0]) / L if L > 1e-12 else np.hypot(rel[:, 0], rel[:, 1])
        i = int(np.argmax(off))
        if off[i] > tol:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return P[keep]


def _without_false_corners(Q, tol):
    """Takes out any corner within tol of the line between its neighbours, the least needed first. Simplifying can split a flat run at a speck of noise before it reaches the real corner, and never
    comes back to it. The two ends stay: they are one corner, a lead apart."""
    Q = [tuple(x) for x in Q]
    while len(Q) > 2:
        best, at = None, None
        for i in range(1, len(Q) - 1):
            (x0, y0), (x1, y1), (x2, y2) = Q[i - 1], Q[i], Q[i + 1]
            L = math.hypot(x2 - x0, y2 - y0)
            off = abs((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0)) / max(L, 1e-12)
            if best is None or off < best:
                best, at = off, i
        if best > tol:
            break
        del Q[at]
    return np.array(Q)


def real_start(Q, lead, tol):
    """A closed profile (corners over one lead, the last a lead on from the first) whose start is a real corner. Simplified as an open line, it kept both its ends whatever they were, and where it
    had been cut open partway along a straight piece, a corner stayed there: a piece 0.1 mm long on a rounded M20, a sliver face winding the whole thread. While the start lies within tol of the
    line between the corners either side of it, round the lead, it goes, and the profile starts at the next corner; then whole leads back, to start within the first lead (Helical looks for
    a point's profile one lead either side of the point's phase, no further)."""
    Q = np.asarray(Q, float)
    while len(Q) > 4:
        prev, nxt = Q[-2] - [lead, 0.0], Q[1]
        ab = nxt - prev
        off = abs((Q[0, 0] - prev[0]) * ab[1] - (Q[0, 1] - prev[1]) * ab[0]) / max(math.hypot(ab[0], ab[1]), 1e-12)
        if off > tol:
            break
        Q = np.r_[Q[1:-1], Q[1:2] + [lead, 0.0]]
    Q = Q.copy()
    Q[:, 0] -= lead * math.floor(float(Q[0, 0]) / lead)
    return Q


def _circle_through(P):
    """Least-squares circle through outline points: (radius, largest distance of a point from it)."""
    x, y = P[:, 0], P[:, 1]
    sol = np.linalg.lstsq(np.c_[2 * x, 2 * y, np.ones(len(x))], x * x + y * y, rcond=None)[0]
    r = float(np.sqrt(max(sol[2] + sol[0] ** 2 + sol[1] ** 2, 0.0)))
    return r, float(np.abs(np.hypot(x - sol[0], y - sol[1]) - r).max())


def _pieces(Q, curve, tol):
    """Straight pieces, and arcs: a run of pieces turning one way is an arc only if one circle fits the outline there within the tolerance. A trapezoid's corners turn one way too, and no circle fits
    them. The arc is the longest stretch of a run that one circle fits, since a rounded root runs straight on into its flanks; what is left of the run either side may hold another (a trapezoid
    with both crest corners rounded turns one way all along). Cutting a run back from its longer end instead, a flank kept itself in behind a tiny piece at the far end, so the crest's circle was
    never tried alone, and a rounded crest that one reading found, the next lost. Returns ("line", a, b) or ("arc", a, b, radius)."""
    seg = np.diff(Q, axis=0)
    length = np.hypot(seg[:, 0], seg[:, 1])
    ang = np.degrees(np.arctan2(seg[:, 1], seg[:, 0]))
    turn = (np.diff(ang) + 180) % 360 - 180
    n = len(seg)
    arcs = []

    def arcs_in(lo, hi):
        if hi - lo + 1 < ARC_MIN_PIECES:
            return
        # (every stretch of the run of at least ARC_MIN_PIECES pieces, longest first: the first a circle fits is the arc. A run is a few pieces, so this is a few hundred small fits at most)
        stretches = sorted(((float(length[a:b + 1].sum()), a, b) for a in range(lo, hi + 1) for b in range(a + ARC_MIN_PIECES - 1, hi + 1)), key=lambda x: (-x[0], x[1]))
        for _, a, b in stretches:
            a0, b0 = Q[a], Q[b + 1]
            inside = curve[(curve[:, 0] >= a0[0]) & (curve[:, 0] <= b0[0])]
            if len(inside) < 5:
                continue
            r, off = _circle_through(inside)
            if off <= tol:
                arcs.append((a, b, r))
                arcs_in(lo, a - 1)
                arcs_in(b + 1, hi)
                return

    i = 0
    while i < n:
        j = i
        while j < n - 1 and (j == i or turn[j] * turn[j - 1] > 0):
            j += 1
        arcs_in(i, j)
        i = j + 1
    out, i = [], 0
    while i < n:
        arc = next((x for x in arcs if x[0] == i), None)
        if arc:
            out.append(("arc", Q[arc[0]], Q[arc[1] + 1], arc[2]))
            i = arc[1] + 1
        else:
            out.append(("line", Q[i], Q[i + 1]))
            i += 1
    return out


def outline(reduced, lead):
    """The outline over one lead from points given as (phase in mm of lead, radius): the median radius of each column of phase, as (phase, radius), and how thick the curve is. A steep wall needs nothing
    special: the median jumps between neighbouring columns. A column that disagrees with both neighbours is a few stray points, not the outline, so a running median of three takes it out, which can't
    blunt a real corner by more than a column. It starts at its sharpest corner, so cutting the lead open adds no corner of its own."""
    ph, q = reduced[:, 0], reduced[:, 1]
    nb = COLUMNS_PER_LEAD
    idx = np.minimum((ph / lead * nb).astype(int), nb - 1)
    cols = [q[idx == i] for i in range(nb)]
    ok = np.array([len(c) >= MIN_IN_COLUMN for c in cols])
    if not ok.any():
        return None, 0.0
    thick = max(float(np.median([np.percentile(c, 75) - np.percentile(c, 25) for c, y in zip(cols, ok) if y])), 1e-4)
    u = (np.arange(nb) + 0.5) / nb * lead
    good = np.nonzero(ok)[0]
    m = np.array([np.median(cols[i]) for i in good])
    # (empty columns take the line between their neighbours, round the lead)
    m = np.interp(np.arange(nb), np.r_[good - nb, good, good + nb], np.r_[m, m, m])
    f = np.median(np.stack([np.roll(m, 1), m, np.roll(m, -1)]), axis=0)
    step = lead / nb
    w = max(2, nb // 90)
    back, ahead = (f - np.roll(f, w)) / (w * step), (np.roll(f, -w) - f) / (w * step)
    start = int(np.argmax(np.abs(np.degrees(np.arctan(ahead) - np.arctan(back)))))
    order = np.arange(nb + 1) + start
    return np.c_[u[order % nb] + lead * (order // nb), f[order % nb]], thick


def profile_of(reduced, lead):
    """A thread's profile from its landed points given as (phase in mm of lead, radius): (corners over one lead, each running up in phase and the last a lead after the first; pieces; tolerance held to).
    None if there isn't enough to read."""
    curve, thick = outline(reduced, lead)
    if curve is None:
        return None
    depth = float(np.percentile(curve[:, 1], 99) - np.percentile(curve[:, 1], 1))
    tol = max(3 * thick, DEPTH_TOL * depth)
    Q = real_start(_without_false_corners(_simplify(curve, tol), tol), lead, tol)
    if len(Q) < 3:
        return None
    return Q, _pieces(Q, curve, tol), tol


def _open_at(ring, lead):
    """Which corner of a closed profile (its rows over one lead, in order) to cut it open at: the end of its longest piece among those running between a corner turning one way and a corner
    turning the other, a flank between a crest and a root. 0 if it has none."""
    Q = np.r_[ring, ring[:1] + [lead, 0]]
    seg = np.diff(Q, axis=0)
    ang = np.arctan2(seg[:, 1], seg[:, 0])
    turn = (ang - np.roll(ang, 1) + math.pi) % (2 * math.pi) - math.pi      # at corner i, from piece i - 1 to piece i
    length = np.hypot(seg[:, 0], seg[:, 1])
    best = None
    for i in range(len(seg)):
        if turn[i] * turn[i - 1] < 0 and (best is None or length[i - 1] > best[0]):
            best = (float(length[i - 1]), i)
    return 0 if best is None else best[1]


def with_arcs(reduced, prof, lead):
    """A closed profile with its rounded stretches made true arcs, from points given as (phase, radius): (the outline, with each arc in rows close enough to stand for it; [(first corner's row, last corner's
    row, centre, radius)]). A run of corners turning one way is an arc only if one circle fits the outline there (_pieces). The ends of an arc stay where they are, on the straight pieces either side."""
    curve, thick = outline(reduced, lead)
    if curve is None:
        return prof, []
    depth = float(np.percentile(curve[:, 1], 99) - np.percentile(curve[:, 1], 1))
    tol = max(3 * thick, DEPTH_TOL * depth)
    # (the outline and the profile, one over the other: both unrolled from the outline's own start)
    start = float(curve[0, 0])
    ring = prof[:-1].copy()
    ring[:, 0] = start + np.mod(ring[:, 0] - start, lead)
    ring = ring[np.argsort(ring[:, 0], kind="stable")]
    # (cut open where the profile turns from one way to the other, not inside a run turning one way: on a thread rounded all over, the outline's sharpest corner lay inside its
    # crest, the crest's arc was found without its first 0.15 mm, and that bit was a straight piece of its own, a sliver face at every turn)
    k = _open_at(ring, lead)
    if k:
        ring = np.r_[ring[k:], ring[:k] + [lead, 0]]
        # (and starting within the first lead, whole leads back: a point's phase is read within one lead, and the profile is looked for a lead either side of it only. Started past
        # a lead, the points in the gap found no profile near them, and a thread lost 28% of its facets)
        ring[:, 0] -= lead * math.floor(float(ring[0, 0]) / lead)
        start = float(ring[0, 0])
        curve = curve.copy()
        curve[:, 0] = start + np.mod(curve[:, 0] - start, lead)
        curve = curve[np.argsort(curve[:, 0], kind="stable")]
    Q = np.r_[ring, ring[:1] + [lead, 0]]
    found = [p for p in _pieces(Q, curve, tol) if p[0] == "arc"]
    if not found:
        return prof, []
    rows, arcs = [Q[0]], []
    i = 0
    while i < len(Q) - 1:
        here = next((p for p in found if np.array_equal(p[1], Q[i])), None)
        if here is None:
            rows.append(Q[i + 1])
            i += 1
            continue
        a, b = here[1], here[2]
        inside = curve[(curve[:, 0] >= a[0]) & (curve[:, 0] <= b[0])]
        centre, r = _circle_centre(inside, a, b)
        arcs.append((len(rows) - 1, centre, r, a, b))
        # (rows of the outline along it, for what needs a polyline: the distance to the profile, and its reach)
        angles = np.linspace(math.atan2(a[1] - centre[1], a[0] - centre[0]), math.atan2(a[1] - centre[1], a[0] - centre[0]) + _sweep(a, b, centre), ARC_ROWS + 1)[1:]
        rows.extend(np.c_[centre[0] + r * np.cos(angles[:-1]), centre[1] + r * np.sin(angles[:-1])])
        rows.append(b)
        i = next(j for j in range(i + 1, len(Q)) if np.array_equal(Q[j], b))
    P = np.array(rows)
    return P, [(row, row + ARC_ROWS, centre, r) for row, centre, r, _, _ in arcs]


def _sweep(a, b, centre):
    """The short way round from a to b about a centre: signed radians."""
    return (math.atan2(b[1] - centre[1], b[0] - centre[0]) - math.atan2(a[1] - centre[1], a[0] - centre[0]) + math.pi) % (2 * math.pi) - math.pi


def _circle_centre(P, a, b):
    """The circle through two ends a and b that fits the points between them best: (centre, radius). Its centre lies on the line square to the chord through its middle, so only how far along that line is free,
    and the arc meets the pieces either side where they end."""
    mid = 0.5 * (a + b)
    chord = b - a
    half = 0.5 * float(np.linalg.norm(chord))
    n = np.array([-chord[1], chord[0]]) / max(2 * half, 1e-12)
    # (each point says how far: |p - (mid + t n)| = |a - (mid + t n)| is linear in t)
    rel = P - mid
    denom = 2 * (rel @ n)
    use = np.abs(denom) > 1e-9
    if not use.any():
        return mid, half
    t = float(np.median(((rel[use] * rel[use]).sum(axis=1) - half * half) / denom[use]))
    centre = mid + t * n
    return centre, float(np.linalg.norm(a - centre))


def landed(S, V, F, thread, seed=7):
    """A thread's points that land, for its profile: (their points), or None."""
    return _landed_points(S, V, F, thread, np.random.default_rng(seed))
