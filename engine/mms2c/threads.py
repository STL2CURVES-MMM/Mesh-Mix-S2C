"""Screw threads: helical surfaces rebuilt exactly.

A thread is swept by its profile (the V of the flanks, with the crest and root between)
turning about an axis while it moves along it. screws.py finds the motion and reads the
profile from the surface itself. Here they become a model: the corners of the mesh, which
lie on the thread to the file's precision where facet normals are good only to a degree or
so, then fix the axis, the pitch and the profile's corners exactly.

The thread is rebuilt one face per straight piece of its profile (each flank, the
crest, the root): the piece swept along the helix, a ruled B-spline surface between
two helices (exact for a straight piece), cut to the outline of its facets. A thread
that tapers grows in radius by k for each unit along its axis, and is otherwise the same.

A screw sweep is the same motion carrying a profile that needn't wind round: a helical
gear's teeth, a twisted extrusion. Its many starts and long lead leave each flank a
fraction of a turn, so it isn't judged as a thread (screws.sweep_verdict), and its
profile is read from the mesh's own corners, which lie on the design to the file's
precision (_swept_profile). A flank of it is a smooth curve, neither straight nor an
arc (an involute), swept along the helix as one face (CurveFlank).
"""

import math
import os

import numpy as np

from .accuracy import scale
ACCURACY = scale("threads")

MIN_FACETS = 30        # a thread has at least this many facets
HELIX_FIT = 2.5e-5     # mm: an edge's B-spline stays this close to its helix, a quarter of how far an edge may stand off its surfaces (mmm/cad.py EDGE_FIT)
HELIX_SURFACE_FIT = 1e-4   # mm: a thread surface's helices stay this close to the true ones: that same distance, the edge's tolerance taking in both (1.5 x EDGE_FIT)
CARVE_MIN = 100        # facets: a piece this big blamed for trouble gives up only its facets near it
ON_TOL = 0.01          # mm (plus the file's rounding): corners this near lie on the thread
PIECE_TOL = 0.03 * ACCURACY    # mm: at most this (plus the rounding) ...
PIECE_SHARE = 0.01     # ... and this share of its lead (but at least ON_TOL): a found
                       # thread's facet whose corners all lie this near one piece of its
                       # profile is built on it (see piece_tol)
PROFILE_TOL = 0.004    # mm: profile simplified to straight pieces within this
EPS_STEP = np.finfo(float).eps ** 0.5   # relative step of the axis fit's differences (scipy's)
HOLD = 0.01            # weight (mm per mm moved) keeping profile corners where they started:
                       # one with no corners near it was otherwise free to drift (100+ mm)
MAX_FACET_TURN = 90    # deg: a facet of a piece spans less than this along its helix (a plain
                       # bore a whole number of leads long has every corner on the thread)
HOMES_CHUNK_PAIRS = 2_500_000  # (facet corner, piece) pairs homes.bulk reads at once: about 200 MB of work arrays (_on_pieces)
CLOSER_BY = 0.001     # mm (plus the file's rounding): of two pieces each holding all of a facet's corners, the one holding them
                       # closer than the other by more than this takes it; held alike, its neighbours decide (_closest_home)
REPEAT_SHARE = 0.5     # of the lead's columns a reading on part of a circle covers: most must be read by two turns or more
REPEAT_SPREAD = 0.05   # of the reading's depth: its turns read the same radius to within what a sure landing allows (screws.TIGHT,
                       # about 5% of a thread's depth)
SWEEP_MISFIT_DEG = 2.0  # degrees: a triangle whose normal strays further from square to a screw sweep's motion isn't on it
SWEEP_RING_MARGIN = 0.1 # share of the ring's depth: a sweep's corners are looked for this far past the ring its flanks read
SWEEP_MIN_ROW = 3       # corners a point of a swept mesh's profile gathers at least (a helix of its tessellation, one per row)
SWEEP_PHASE_GAP = 1e-4  # share of the lead: corners closer than this in phase are one row of the mesh (or 10 x the median gap)
SWEEP_CORNER_DEG = 25.0 # degrees: a sweep's profile turning this much at one of its points has a corner there, between pieces
SWEEP_MIN_ROWS = 24     # profile points a sweep's outline must have, or it isn't read
SWEEP_STEP_SHARE = 0.005 # share of the ring's depth: a row's corners further apart in radius than this are two profile points
SWEEP_REFINE_ROUNDS = 4 # times a screw sweep's motion is refitted to its rows, the rows found again each time (_refined_sweep)
SHARPEN_SPAN = 0.01    # of the lead, either way: how far round a motion's lead its sharpest is looked for (motions' leads were up to 0.5% off)
SHARPEN_COARSE = 21    # leads tried across the span
SHARPEN_FINE = 21      # leads tried across two coarse steps round the best
SHARPEN_LANDS = os.environ.get("MM_S2C_SHARPEN_LANDS", "1") != "0"   # (experimental, wt_129c, 2026-11-19) a sharpened lead must land as the read one does (_lands_as_read)
SHARPEN_LAND = 4.0     # times the read lead's landing: the most a sharpened lead's may be. Over the 22 moves of 2026-11-19, right ones 0.32-1.03, wrong ones 16-35; 4 is their geometric middle
SHARPEN_SEED = 3       # the fixed draw of the ring's points that _lands_as_read lands
READER_FALLBACK = os.environ.get("MM_S2C_READER_FALLBACK", "1") != "0"   # (experimental, wt_129d, 2026-11-19) where the thread's reader reads nothing on a motion that is a sweep too, read it as the sweep (_model_of)
NEAREST_CELLS = 2_000_000  # point-by-piece pairs a profile distance works on at once (Helical._nearest: about 16 MB an array)


class Helical:
    """Profile (z, rho) pairs over one lead, swept by the screw motion: axis through a
    along unit d, rising `pitch` per radian. Points outside the material are positive.
    A thread that tapers grows in radius by k for each unit along the axis. Its profile's
    radii are then reduced ones, what they'd be at zref along the axis, so that the same
    outline holds on every turn and nothing else need know."""

    def __init__(self, a, d, pitch, profile, inside, k=0.0, zref=0.0):
        self.d = d / np.linalg.norm(d)
        self.e1 = np.cross(self.d, [1.0, 0, 0] if abs(self.d[0]) < 0.9 else [0, 1.0, 0])
        self.e1 /= np.linalg.norm(self.e1)
        self.e2 = np.cross(self.d, self.e1)
        self.a = np.asarray(a, float) - (np.asarray(a, float) @ self.d) * self.d
        self.pitch = float(pitch)
        self.lead = 2 * math.pi * abs(self.pitch)
        self.profile = np.asarray(profile, float)       # closed over one lead: z from 0 to lead
        self.inside = inside                            # material nearer the axis (a bolt)
        self.k, self.zref = float(k), float(zref)
        self.outer = float(self.profile[:, 1].max())    # the largest true radius (find sets it, on a taper)
        self.arcs = []                                  # rounded stretches of the profile: (first corner's row, last corner's row, centre, radius)
        self.curves = []                                # smooth stretches (a screw sweep's): (first row, last row), the rows between on the curve
        self.swept = False                              # a screw sweep (screws.sweep_verdict), not a thread
        self.kind = "thread"

    def local(self, p):
        """(rho, z reduced to the profile's plane, angle) of points p. On a taper, rho is reduced too."""
        v = np.atleast_2d(p) - self.a
        z = v @ self.d
        w = v - np.outer(z, self.d)
        u = np.arctan2(w @ self.e2, w @ self.e1)
        return np.linalg.norm(w, axis=1) - self.k * (z - self.zref), np.mod(z - self.pitch * u, self.lead), u

    def signed(self, p):
        rho, z0, _ = self.local(p)
        return self._profile_distance(z0, rho)

    def _profile_distance(self, z0, rho):
        best, sign = self._nearest(z0, rho)[:2]
        return best * (sign if self.inside else -sign)

    def _nearest(self, z0, rho):
        """Distance from (z0, rho) to the profile, the side (+1 above it), and where its
        foot is: the profile piece, how far along it, which copy of the profile (-1, 0,
        1 leads on), and the unit direction from the foot to the point (what moving the
        point or the piece's ends does to the distance)."""
        P = self.profile
        rho = np.asarray(rho)
        # (in batches of points when points x pieces is large: each point's answer is its own, so batches give the same bits. A
        # screw sweep's profile keeps every row of its curves, about 1,500 on a helical gear, and over 20,000 corners at once
        # this took 1.46 GB)
        z0 = np.asarray(z0)
        if z0.ndim == 1 and rho.shape == z0.shape and len(z0) * (len(P) - 1) > NEAREST_CELLS:
            step = max(1, NEAREST_CELLS // (len(P) - 1))
            parts = [self._nearest(z0[i:i + step], rho[i:i + step]) for i in range(0, len(z0), step)]
            return tuple(np.concatenate(x) for x in zip(*parts))
        # (the profile and its copies a lead either side as one row of pieces, the nearest
        # the first smallest: the earlier copy wins a tie, as when each copy was a pass of
        # its own. Worked in z and rho apart, the same sums in the same order, without
        # the arrays of points by pieces by two the passes made: the profile refits call
        # this thousands of times. And copy -2: a point's phase is in [0, lead) and the
        # profile starts anywhere in the first lead, so one starting just under a lead
        # has copy -2 just below a point at phase 0, out of reach of the other three. It
        # is tried only for the points it could be nearer to, and wins only if strictly
        # nearer: a tie with copy -1 is at the corner they share, the same foot)
        m = len(P) - 1
        A = np.concatenate([P[:-1] + [copy * self.lead, 0] for copy in (-2, -1, 0, 1)])
        B = np.concatenate([P[1:] + [copy * self.lead, 0] for copy in (-2, -1, 0, 1)])
        abz, abr = B[:, 0] - A[:, 0], B[:, 1] - A[:, 1]
        ab2 = np.maximum(abz * abz + abr * abr, 1e-300)

        def nearest(cols, qz, qr):
            t = np.clip(((qz[:, None] - A[cols, 0]) * abz[cols] + (qr[:, None] - A[cols, 1]) * abr[cols])
                        / ab2[cols], 0, 1)
            dz, dr = qz[:, None] - (A[cols, 0] + abz[cols] * t), qr[:, None] - (A[cols, 1] + abr[cols] * t)
            dist = np.sqrt(dz * dz + dr * dr)
            j = dist.argmin(axis=1)
            r = np.arange(len(qz))
            return j, dist[r, j], t[r, j], dz[r, j], dr[r, j]

        # (the profile's own copy first, then all three only for the points a copy could
        # be as near to: no nearer than the gap in z to the span that copy covers. Twice
        # as quick, the same answers)
        j, dist, along, dzk, drk = nearest(np.arange(2 * m, 3 * m), z0, rho)
        k = j + 2 * m
        slack = dist * (1 + 1e-9) + 1e-12
        lo, hi = P[:, 0].min(), P[:, 0].max()
        before = np.maximum(0.0, np.maximum(lo - self.lead - z0, z0 - (hi - self.lead)))
        after = np.maximum(0.0, np.maximum(lo + self.lead - z0, z0 - (hi + self.lead)))
        before2 = np.maximum(0.0, np.maximum(lo - 2 * self.lead - z0, z0 - (hi - 2 * self.lead)))
        redo = np.flatnonzero((before <= slack) | (after <= slack) | (before2 <= slack))
        if len(redo):
            j3, d3, a3, z3, r3 = nearest(np.arange(m, 4 * m), z0[redo], rho[redo])
            j2, d2, a2, z2, r2 = nearest(np.arange(m), z0[redo], rho[redo])
            two = d2 < d3
            k[redo] = np.where(two, j2, j3 + m)
            along[redo], dzk[redo], drk[redo] = np.where(two, a2, a3), np.where(two, z2, z3), np.where(two, r2, r3)
        best = np.sqrt(dzk * dzk + drk * drk)
        # side: rho above the profile at that z is away from a bolt's material
        sign = np.where(drk >= 0, 1.0, -1.0)
        piece, wrap = k % m, (k // m - 2).astype(float)
        away = np.c_[dzk, drk]
        d = np.linalg.norm(away, axis=1)
        on = d < 1e-15
        away[~on] /= d[~on, None]
        if on.any():        # on the profile: square to its piece, on the side counted +
            ab = P[piece[on] + 1] - P[piece[on]]
            nrm = np.c_[-ab[:, 1], ab[:, 0]] / np.maximum(np.linalg.norm(ab, axis=1), 1e-300)[:, None]
            away[on] = nrm * np.where(nrm[:, 1] >= 0, 1.0, -1.0)[:, None]
        return best, sign, piece, along, wrap, away

    def samples_per_turn(self, rho, fit=None):
        """Points a turn for a cubic B-spline through this helix at radius rho to stay within fit (HELIX_FIT unless given) of it,
        ends included. A cubic spline through points h apart stands off a curve by at most 5/384 h^4 times the curve's fourth
        derivative (Hall and Meyer, J. Approx. Theory 16, 1976), which on a circle of radius r is 5/384 r h^4 with h in radians. So the count grows with the
        radius, slowly (50 at radius 7, 169 at radius 1000), and no fixed count holds at every size."""
        fit = HELIX_FIT if fit is None else fit
        return int(math.ceil(2 * math.pi / (384.0 * fit / (5.0 * max(float(rho), 1e-9))) ** 0.25))

    def point(self, z0, rho, u):
        """The point of the surface over profile point (z0, rho) at angle u."""
        z = z0 + self.pitch * u
        return self.a + z * self.d + (rho + self.k * (z - self.zref)) * (math.cos(u) * self.e1 + math.sin(u) * self.e2)


class Flank:
    """One straight piece of a thread's profile, from (z, rho) p to q, swept along the
    thread's helix: a flank (a helicoid), or the crest or root between (a helical band
    round a cylinder). A point of it is (s, U): s mm along the piece from p, on the
    helix turned U radians round (unwrapped: U tells the turns apart). Points away
    from the material are positive."""

    kind = "thread"

    def __init__(self, thread, p, q):
        self.thread = thread
        self.p, self.q = np.asarray(p, float), np.asarray(q, float)
        self.length = float(np.linalg.norm(self.q - self.p))
        self.t = (self.q - self.p) / self.length            # along the piece, as (z, rho)
        side = 1.0 if thread.inside else -1.0
        # (the profile runs with z rising, the material below it for a bolt)
        self.n = side * np.array([-self.t[1], self.t[0]])   # away from the material
        self.a, self.d = thread.a, thread.d

    def at(self, s):
        """(z, rho) of the piece's own point s along it, in the profile's plane."""
        s = np.asarray(s, float)
        return self.p[0] + s * self.t[0], self.p[1] + s * self.t[1]

    def slope(self, s):
        """Unit direction (z, rho) along the piece at s."""
        s = np.asarray(s, float)
        return np.broadcast_to(self.t[0], s.shape), np.broadcast_to(self.t[1], s.shape)

    def place(self, x):
        """(s, distance off the piece's line in the profile's plane, U) of points x,
        turned back along the helix onto the piece (the nearest of a lead either way of
        the copy nearest the piece's middle).

        A point's phase z0 is in [0, lead) and the profile starts anywhere in the first
        lead, so its pieces run up to two leads. Tried only a lead either side of z0, a
        profile starting just under a lead could not be reached past two leads: nut-m6x1's
        crest corner lies within half a micron of phase 0, and read at -0.0005 the profile
        started at 0.9995, its last flank ending at almost two leads. A point just past the
        seam then took the copy a turn away, its facet's U spread a whole turn, 4,400
        facets found no piece and a flank came out in 20 pieces, under some draws of the
        profile's points and not others. Where the copies were already round the piece the
        sums are the same to the bit (the shift adds 0)."""
        h = self.thread
        v = np.atleast_2d(x) - h.a
        z = v @ h.d
        w = v - np.outer(z, h.d)
        rho = np.linalg.norm(w, axis=1) - h.k * (z - h.zref)
        z0 = np.mod(z - h.pitch * np.arctan2(w @ h.e2, w @ h.e1), h.lead)
        z0 = z0 + h.lead * np.round((0.5 * (self.p[0] + self.q[0]) - z0) / h.lead)
        best = None
        for shift in (-h.lead, 0.0, h.lead):
            q = np.c_[z0 + shift, rho] - self.p
            s, off = q @ self.t, q @ self.n
            gap = np.hypot(np.maximum(0, np.maximum(-s, s - self.length)), off)  # to the piece
            here = (s, off, z0 + shift, gap)
            if best is None:
                best = here
            else:
                better = gap < best[3]
                best = tuple(np.where(better, x1, x0) for x0, x1 in zip(best, here))
        s, off, zp, _ = best
        return s, off, (z - zp) / h.pitch

    def point(self, s, U):
        """Points of the surface at (s, U)."""
        h = self.thread
        s, U = np.broadcast_arrays(np.asarray(s, float), np.asarray(U, float))
        z0, rho0 = self.at(s)
        z = z0 + h.pitch * U
        rho = rho0 + h.k * (z - h.zref)
        r = np.cos(U)[..., None] * h.e1 + np.sin(U)[..., None] * h.e2
        return h.a + z[..., None] * h.d + rho[..., None] * r

    def along_helix(self, s, U):
        """The surface's rate of change round its helix at (s, U), per radian."""
        h = self.thread
        z0, rho0 = self.at(s)
        z = z0 + h.pitch * U
        rho = rho0 + h.k * (z - h.zref)
        r = math.cos(U) * h.e1 + math.sin(U) * h.e2
        return h.pitch * h.d + rho * (math.cos(U) * h.e2 - math.sin(U) * h.e1) + h.k * h.pitch * r

    def _normals(self, s, U):
        """Unit normals at (s, U) (away from the material), and the profile plane's own
        normal there (the distance measured in that plane is a hair more than the
        true one: the helix leans it)."""
        h = self.thread
        z0, rho0 = self.at(s)
        tz, tr = self.slope(s)
        z = z0 + h.pitch * U
        rho = rho0 + h.k * (z - h.zref)
        r = np.cos(U)[:, None] * h.e1 + np.sin(U)[:, None] * h.e2
        across = -np.sin(U)[:, None] * h.e1 + np.cos(U)[:, None] * h.e2
        n = np.cross(tz[:, None] * h.d + (tr + h.k * tz)[:, None] * r, h.pitch * h.d + rho[:, None] * across + h.k * h.pitch * r)
        n /= np.maximum(np.linalg.norm(n, axis=1), 1e-300)[:, None]
        side = 1.0 if h.inside else -1.0
        flat = (-side * tr)[:, None] * h.d + (side * tz)[:, None] * r
        n[np.einsum("ij,ij->i", n, flat) < 0] *= -1
        return n, flat

    def signed(self, x):
        s, off, U = self.place(x)
        n, flat = self._normals(s, U)
        return off * np.einsum("ij,ij->i", n, flat)

    def normal(self, x):
        s, _, U = self.place(x)
        return self._normals(s, U)[0]


class ArcFlank(Flank):
    """A rounded piece of a thread's profile, a circular arc from (z, rho) p to q round a centre, swept along the helix. A point of it is (s, U) as on any piece, s being mm along the arc."""

    def __init__(self, thread, p, q, centre, radius):
        super().__init__(thread, p, q)
        self.centre, self.radius = np.asarray(centre, float), float(radius)
        v0, v1 = self.p - self.centre, self.q - self.centre
        self.a0 = math.atan2(v0[1], v0[0])
        # the short way round: a thread's rounded root or crest turns less than half a circle, and its profile rises in z
        turn = (math.atan2(v1[1], v1[0]) - self.a0 + math.pi) % (2 * math.pi) - math.pi
        self.way = 1.0 if turn >= 0 else -1.0
        self.length = abs(turn) * self.radius
        # (which way off the arc is away from the material: out from the centre, or in towards it)
        self.out = float(self.way * (1.0 if thread.inside else -1.0))

    def at(self, s):
        angle = self.a0 + self.way * np.asarray(s, float) / self.radius
        return self.centre[0] + self.radius * np.cos(angle), self.centre[1] + self.radius * np.sin(angle)

    def slope(self, s):
        angle = self.a0 + self.way * np.asarray(s, float) / self.radius
        return -self.way * np.sin(angle), self.way * np.cos(angle)

    def place(self, x):
        h = self.thread
        v = np.atleast_2d(x) - h.a
        z = v @ h.d
        w = v - np.outer(z, h.d)
        rho = np.linalg.norm(w, axis=1) - h.k * (z - h.zref)
        z0 = np.mod(z - h.pitch * np.arctan2(w @ h.e2, w @ h.e1), h.lead)
        z0 = z0 + h.lead * np.round((0.5 * (self.p[0] + self.q[0]) - z0) / h.lead)     # (as Flank.place: the copies round the piece)
        best = None
        mid = self.a0 + self.way * 0.5 * self.length / self.radius
        for shift in (-h.lead, 0.0, h.lead):
            dz, dr = z0 + shift - self.centre[0], rho - self.centre[1]
            angle = (np.arctan2(dr, dz) - mid + math.pi) % (2 * math.pi) - math.pi
            s = 0.5 * self.length + self.way * angle * self.radius
            off = -self.out * (np.hypot(dz, dr) - self.radius)
            gap = np.hypot(np.maximum(0, np.maximum(-s, s - self.length)), off)
            here = (s, off, z0 + shift, gap)
            if best is None:
                best = here
            else:
                better = gap < best[3]
                best = tuple(np.where(better, x1, x0) for x0, x1 in zip(best, here))
        s, off, zp, _ = best
        return s, off, (z - zp) / h.pitch


class CurveFlank(Flank):
    """A smooth piece of a screw sweep's profile, neither straight nor an arc (a helical gear's involute flank), through its rows
    (z, rho), swept along the helix. A point of it is (s, U) as on any piece, s being mm along the polyline through its rows: the
    curve itself is a cubic spline through them in that parameter, so a point's place on the polyline and the spline's point at
    the same s are a row's sag apart at most, which with rows a mesh's tessellation apart is far below its accuracy."""

    def __init__(self, thread, rows):
        from scipy.interpolate import CubicSpline
        Q = np.asarray(rows, float)
        super().__init__(thread, Q[0], Q[-1])
        self.rows = Q
        seg = np.diff(Q, axis=0)
        self.seg_len = np.linalg.norm(seg, axis=1)
        self.cum = np.r_[0.0, np.cumsum(self.seg_len)]
        self.length = float(self.cum[-1])
        self.seg_t = seg / np.maximum(self.seg_len, 1e-300)[:, None]
        self._z = CubicSpline(self.cum, Q[:, 0])
        self._r = CubicSpline(self.cum, Q[:, 1])
        # (the tightest bend, for how many helices the face needs across it: build._thread_surface, as for an arc's radius)
        S = np.linspace(0, self.length, 8 * len(Q))
        dz, dr, ddz, ddr = self._z(S, 1), self._r(S, 1), self._z(S, 2), self._r(S, 2)
        bend = np.abs(dz * ddr - dr * ddz) / np.maximum((dz * dz + dr * dr) ** 1.5, 1e-300)
        self.radius = float(1.0 / max(bend.max(), 1e-9))

    def at(self, s):
        """The curve at s; past either end it runs straight on along its end's tangent, as a straight piece does. A piece's surface
        is built a little past its facets each way (build._thread_surface), and held at its ends the helices there were one and the
        same, and OpenCascade's interpolation across them refused (three of a twisted elliptical rod's four pieces)."""
        s = np.asarray(s, float)
        c = np.clip(s, 0.0, self.length)
        z, r = self._z(c), self._r(c)
        tz, tr = self.slope(c)
        past = s - c
        return z + past * tz, r + past * tr

    def slope(self, s):
        s = np.clip(np.asarray(s, float), 0.0, self.length)
        dz, dr = self._z(s, 1), self._r(s, 1)
        n = np.maximum(np.hypot(dz, dr), 1e-300)
        return dz / n, dr / n

    def place(self, x):
        h = self.thread
        v = np.atleast_2d(x) - h.a
        z = v @ h.d
        w = v - np.outer(z, h.d)
        rho = np.linalg.norm(w, axis=1) - h.k * (z - h.zref)
        z0 = np.mod(z - h.pitch * np.arctan2(w @ h.e2, w @ h.e1), h.lead)
        z0 = z0 + h.lead * np.round((0.5 * (self.p[0] + self.q[0]) - z0) / h.lead)     # (as Flank.place: the copies round the piece)
        side = 1.0 if h.inside else -1.0
        A, T, L = self.rows[:-1], self.seg_t, self.seg_len
        best = None
        for shift in (-h.lead, 0.0, h.lead):
            qz, qr = z0 + shift, rho
            # (the nearest point of the polyline through the rows: each point against each segment, the rows a few dozen)
            dz, dr = qz[:, None] - A[None, :, 0], qr[:, None] - A[None, :, 1]
            t = np.clip(dz * T[None, :, 0] + dr * T[None, :, 1], 0.0, L[None, :])
            ez, er = dz - t * T[None, :, 0], dr - t * T[None, :, 1]
            dist = np.hypot(ez, er)
            j = dist.argmin(axis=1)
            r = np.arange(len(qz))
            # (past either end, s runs on past it along the end's segment, as on a straight piece: a point's place there is how far
            # past the end it lies, and the tests that a point is within a piece's reach (threads._on_pieces, merged._inside) need
            # it. Held to [0, length], a curve piece took every corner near its tangent run-on however far past its end it lay: on
            # a twisted elliptical rod, where one smooth piece runs on into the next, its faces reached 1.6 to 2.7 mm onto the next
            # piece's facets and stood up to 0.095 mm off the design there)
            raw = dz[r, j] * T[j, 0] + dr[r, j] * T[j, 1]
            last = len(L) - 1
            t_j = np.where((j == 0) & (raw < 0), raw, np.where((j == last) & (raw > L[last]), raw, t[r, j]))
            s = self.cum[j] + t_j
            # (off the curve, away from the material positive, as on a straight piece: square to its segment there)
            off = side * (-T[j, 1] * dz[r, j] + T[j, 0] * dr[r, j])
            # (the gap to the polyline itself, overshoot included: the nearest of its points, an end's past it)
            gap = dist[r, j]
            here = (s, off, qz, gap)
            if best is None:
                best = here
            else:
                better = gap < best[3]
                best = tuple(np.where(better, x1, x0) for x0, x1 in zip(best, here))
        s, off, zp, _ = best
        return s, off, (z - zp) / h.pitch


def find(mesh, candidates):
    """[(Helical, facets)] for the threads among the candidate facets. The motion and the profile come from the whole
    surface, whatever made the mesh (screws.py). Only the facets a thread takes are limited to the candidates."""
    from . import screws
    pool = np.asarray(candidates)
    if len(pool) < MIN_FACETS:
        return []
    V, F = np.asarray(mesh.pts, float), np.asarray(mesh.tris)
    S, rows = screws.motions(V, F)
    found = []
    for r in rows:
        got = _read(mesh, pool, V, F, S, r)
        if got is None:
            continue
        found.append(got)
        pool = np.setdiff1d(pool, got[1])
        if len(pool) < MIN_FACETS:
            break
    return found


def _read(mesh, pool, V, F, S, r):
    """(thread, facets) from a motion, or None: _model_of's reading, read again with the motion it fitted when the first motion lay
    further off it than the profile's own tolerance, the better of the two kept; and a reading on part of a circle kept only if its
    profile repeats from turn to turn."""
    from . import screws
    got = _model_of(mesh, pool, V, F, S, *_sharpened(S, V, F, r))
    if got is None:
        return None
    used = r
    # (the profile is read off the points that land, unwrapped about the motion as found, and only then fitted to the mesh's corners.
    # A motion off by more than the profile's tolerance anywhere on its stretch smears it: clamp-screw's lead, 2.512 for 2.500 over 22.7
    # turns, slid the turns 0.27 mm apart, and only one piece of the profile took its facets (513 faces); obj_2_REAR's second M3 hole,
    # read about an axis leaning 0.19 degrees, lost its 0.04 mm crest flat to a peak, and 566 facets went to 41 revolved strips. Read
    # again about the fitted motion, both came out whole. The second reading is kept only if it puts more facets in pieces, what the
    # thread is built from: on a drift just past the tolerance it differs mainly by its own points, and taken whatever it was, a coarse
    # Acme thread lost three quarters of its pieces)
    thread = got[0]
    drift, d = _drift(r, thread)
    if drift > PROFILE_TOL:
        a2 = thread.a + (float(np.asarray(r["a"], float) @ d) - float(np.asarray(thread.a, float) @ d)) * d
        r2 = dict(r, c=d, a=a2, lead=thread.lead)
        again = _model_of(mesh, pool, V, F, S, *_sharpened(S, V, F, r2))
        if again is not None and _in_pieces(mesh, again) > _in_pieces(mesh, got):
            got, used = again, r2
    # (a thread is one tooth over and over: where two of its turns cover the same stretch of the lead they read the same radius. A
    # reading judged on whole moves has gone all round and shown that; one on part of a circle, judged on its own room, may be a strip
    # that twists like a screw over part of a turn: a fillet round a bore, read at a long lead (04_Carrier's three, a cross bore's 11.961
    # on a greaser, all false). True ones on 60 to 308 degrees repeat on 97-100% of their columns, turns apart by 2.4% of their depth at
    # most; the false 0-19%, and the greaser's turns 12% apart. Under one turn a reading can't repeat, and isn't taken: every such
    # reading in all our runs was false, but a true lug or a short segment of a partial thread would be lost)
    if float(r.get("sc", 1.0)) < 1.0:
        X = screws.landed(S, V, F, used)
        share, spread = (0.0, None) if X is None else _repeats(got[0], X)
        if share < REPEAT_SHARE or spread is None or spread > REPEAT_SPREAD:
            return None
    return got


def _drift(r, thread):
    """(how far the motion r lies from the thread's fitted one at most over its stretch: the axis lines' separation at its two ends plus
    the turns' slide from the lead's change, mm; the fitted axis's direction, turned the motion's way)."""
    c, a = np.asarray(r["c"], float), np.asarray(r["a"], float)
    d = thread.d if float(thread.d @ c) > 0 else -thread.d
    ta = np.asarray(thread.a, float)
    z_at = float(a @ c)
    axis = 0.0
    for h in (r["z"][0] + z_at, r["z"][1] + z_at):
        p2 = ta + (h - float(ta @ d)) * d
        p1 = a + (h - float(a @ d)) / float(c @ d) * c
        axis = max(axis, float(np.linalg.norm(p1 - p2)))
    return axis + abs(thread.lead - float(r["lead"])) * max(float(r["turns"]), 1.0), d


def _sharpened(S, V, F, r):
    """(the motion r with its lead sharpened on the points that land, the points if the lead stands, else None): the profile is
    never read at an unrefined lead. Unwrapped at its true lead a thread stacks every turn on one curve, so its outline is thinnest
    there; a lead a little off smears it (clamp-screw: 2.512 for 2.500 over 22.7 turns, the turns 0.27 mm apart), and whether a
    smeared reading survived to be read again turned on the random draw of points: under another draw clamp-screw's thread was lost.
    The thinness is the mean within-column spread of radius over the lead's columns (_thickness); the median, mostly flat bands on
    many threads, barely moved with the lead, and its least was noise. The lead moves only for a gain over PROFILE_TOL, the
    tolerance the profile is read to, below which the profile can't tell the two leads apart: a flat curve keeps the motion's lead.
    Measured on 551 parts: 4 change, every one with more facets in pieces (341board's sixth hole found, 501 -> 273 faces), none worse."""
    from . import screws
    X = screws.landed(S, V, F, r)
    if X is None:
        return r, None
    L0 = float(r["lead"])
    leads = L0 * (1 + np.linspace(-SHARPEN_SPAN, SHARPEN_SPAN, SHARPEN_COARSE))
    th = np.array([_thickness(X, r, L) for L in leads])
    i = int(np.argmin(th))
    if i in (0, SHARPEN_COARSE - 1):
        return r, X                         # (least at the span's edge: no minimum within it)
    step = leads[1] - leads[0]
    fine = np.linspace(leads[i] - step, leads[i] + step, SHARPEN_FINE)
    tf = np.array([_thickness(X, r, L) for L in fine])
    j = int(np.argmin(tf))
    best = fine[j]
    if 0 < j < SHARPEN_FINE - 1:
        y0, y1, y2 = tf[j - 1], tf[j], tf[j + 1]
        if y0 - 2 * y1 + y2 > 0:
            best = fine[j] + 0.5 * (y0 - y2) / (y0 - 2 * y1 + y2) * (fine[1] - fine[0])
    if _thickness(X, r, L0) - float(tf[j]) <= PROFILE_TOL:
        return r, X
    if SHARPEN_LANDS and not _lands_as_read(S, V, F, r, float(best)):
        return r, X
    return dict(r, lead=float(best)), None


def _lands_as_read(S, V, F, r, lead):
    """Whether lead lands the motion's flank points about as well as its read lead does, within SHARPEN_LAND times, on the
    motion's own moves (screws._land) over its ring's leaning points, drawn as _judge draws them. A read lead is known as well as
    its moves land (they turn up to 251 degrees: a lead error moves a point along the axis by that share of it), and the search
    for the thinnest outline spans 1% of the lead whatever that is; where the outline has no least, on a section with several
    radii a phase, the search moves anyway, and beyond what the read allows: ABC 054's sweep read 13.0008 landing 0.0026 and was
    moved to 12.8733, landing 0.049 (19 times), so neither reader built it; a helical gear read at its designed lead
    (125.6639 for 125.6637) was moved 0.26%, landing 35 times worse; a spring 16 times. Right moves land as the read lead or
    closer (0.32-1.03): the read lead's error over one move is within its landing, and only the turns pooled show it (clamp-screw
    over 22.7 turns). Where there is nothing to land, the move stands, as before."""
    ratio = _landing_ratio(S, V, F, r, lead)
    return ratio is None or ratio <= SHARPEN_LAND


def _landing_ratio(S, V, F, r, lead):
    """How many times as far as at its read lead the motion's flank points land at lead (_lands_as_read), or None where there is
    nothing to land."""
    from . import screws
    c, a = np.asarray(r["c"], float), np.asarray(r["a"], float)
    Fa = screws._in_ring(V, F, c, a, (float(r["rho"][0]), float(r["rho"][1])))
    if len(Fa) < MIN_FACETS:
        return None
    X, N, _ = screws.points(V, Fa, np.random.default_rng(SHARPEN_SEED), screws.TEST_POINTS)
    X = X[np.abs(N @ c) >= screws.LEAN]
    got = []
    for L in (float(r["lead"]), lead):
        D3, Ds, s = screws._land(S, X, dict(r, lead=L))
        moved = ((np.asarray(s) != 0) & (Ds > 0)) if np.ndim(s) else Ds > 0
        if not moved.any():
            return None
        got.append(float(np.median(D3[moved])))
    if got[0] > 0:
        return got[1] / got[0]
    return 1.0 if got[1] <= 0 else math.inf


def _thickness(X, r, lead):
    """The mean within-column interquartile range of radius over the lead's columns (every turn pooled, columns of
    screws.MIN_IN_COLUMN points or more) of points X unwrapped about the motion r at this lead."""
    from . import screws
    c, a = r["c"], np.asarray(r["a"], float)
    m = Helical(a, c, r["hand"] * lead / (2 * math.pi), [[0, 1], [1, 1]], True, float(r["k"]), float(np.mean(r["z"])) + float(a @ c))
    rho, z0, _ = m.local(X)
    nb = screws.COLUMNS_PER_LEAD
    col = np.minimum((z0 / lead * nb).astype(np.int64), nb - 1)
    order = np.argsort(col, kind="stable")
    _, starts = np.unique(col[order], return_index=True)
    bounds = np.r_[starts, len(order)]
    iqr = [np.subtract(*np.percentile(rho[order[s0:s1]], [75, 25])) for s0, s1 in zip(bounds[:-1], bounds[1:]) if s1 - s0 >= screws.MIN_IN_COLUMN]
    return float(np.mean(iqr)) if iqr else float("inf")


def _in_pieces(mesh, got):
    """How many of a reading's facets its pieces take."""
    return sum(len(p.facets) for p in patches(mesh, got[0], got[1]))


def _repeats(thread, X):
    """(the share of the lead's columns read by two turns or more among those read at all, the median spread of the turns' median radii
    in those columns as a share of the depth, or None) for landed points X about the thread's own motion."""
    from . import screws
    nb = screws.COLUMNS_PER_LEAD
    rho, z0, u = thread.local(X)
    depth = float(np.percentile(rho, 98) - np.percentile(rho, 2))
    z = (X - thread.a) @ thread.d
    turn = np.floor((z - thread.pitch * u) / thread.lead).astype(np.int64)
    turn -= turn.min()
    turns = int(turn.max()) + 1
    col = np.minimum((z0 / thread.lead * nb).astype(np.int64), nb - 1)
    key = col * turns + turn
    order = np.argsort(key, kind="stable")
    keys, starts = np.unique(key[order], return_index=True)
    bounds = np.r_[starts, len(order)]
    medians = {}
    for k, s0, s1 in zip(keys, bounds[:-1], bounds[1:]):
        if s1 - s0 >= screws.MIN_IN_COLUMN:
            medians.setdefault(int(k // turns), []).append(float(np.median(rho[order[s0:s1]])))
    if not medians:
        return 0.0, None
    spreads = [max(m) - min(m) for m in medians.values() if len(m) >= 2]
    if not spreads or depth <= 0:
        return len(spreads) / len(medians), None
    return len(spreads) / len(medians), float(np.median(spreads)) / depth


def _closed(Q, lead):
    """Corners read from a curve, as a profile: z rising from within the first lead, closed by the first corner a lead on."""
    ring = np.array(sorted((float(z) % lead, float(r)) for z, r in Q[:-1]))
    return np.r_[ring, ring[:1] + [lead, 0]]


def _unfolded(fit, lead, near):
    """A fitted closed profile with each pair of neighbouring corners that fold (z not rising) merged at their middle, if they lie
    within near of each other; None if any fold is wider than that, or too few corners would be left.

    The first reading of a profile comes from the outline of the points that land, and can hold false corners: f_taper_steep's sharp
    crest and root corners read as small chamfers, two corners 0.04 mm apart each. Fitted to the mesh's corners, every false corner
    slides onto its true one, and all six draws of landing points fit the same trapezoid to 0.0001 mm. Where a false corner slid just
    past its true one (0-0.001 mm in z), the fit was taken to fold over itself and dropped whole: under four draws of six the crest
    then became an arc, or a false corner stayed and cut the thread into hundreds of scraps. Two corners nearer each other than the
    distance the fit took its mesh corners from are one corner at that resolution; a wider fold is a real one."""
    ring = [np.asarray(x, float) for x in fit[:-1]]
    while True:
        Q = ring + [ring[0] + [lead, 0.0]]
        bad = [i for i in range(len(Q) - 1) if Q[i + 1][0] - Q[i][0] <= 0]
        if not bad:
            break
        i = bad[0]
        a, b = Q[i], Q[i + 1]
        if float(np.hypot(*(b - a))) > near or len(ring) <= 3:
            return None
        mid = 0.5 * (a + b)
        if i + 1 < len(ring):
            ring[i:i + 2] = [mid]
        else:                                   # (the last corner and the first, a lead on)
            ring[0] = mid - [lead, 0.0]
            del ring[-1]
    R = np.array(ring)
    return np.r_[R, R[:1] + [lead, 0.0]]


def _swept_profile(mesh, model, ring):
    """(profile, arcs, curves, motion) of a screw sweep, read off the mesh's own corners about its motion refined to them
    (_refined_sweep), or None.

    A thread's profile is read from points sampled on its facets (screws.profile_of), which sit inside a convex design by the
    chord's sag and, on a steep outline, spread a column of the outline as high as the wall is steep: 03_Planet_Gear's read at a
    tolerance of 0.52 mm, its rounded flanks became one straight piece each, and the fit pushed corners 0.7 mm off the mesh. A
    swept mesh's corners lie on the design to the file's precision instead, and its tessellation places them along helices, one
    row of corners for each point of the designer's profile: mapped by the motion into the profile's plane (phase, radius), each
    row falls on one point (the planet gear's 1,520 rows, 33 corners each, agree in radius to 0.0015 mm).

    The corners are those of the triangles whose normals are square to the motion (an end plane's aren't), within the ring the
    flanks read (a bore, which any motion along its axis leaves in place, isn't). Each row is a profile point: its mean phase,
    its median radius. The profile is cut at its corners (where it turns SWEEP_CORNER_DEG or more at a point), and each stretch
    between two corners is a straight piece if it lies within the rows' own spread of its chord, an arc if one circle through its
    end rows holds it so, and otherwise a smooth curve through its rows (CurveFlank)."""
    P3 = _sweep_corners(mesh, model, ring)
    if P3 is None:
        return None
    model = _refined_sweep(model, P3)
    rho, z0, _ = model.local(P3)
    groups, z0 = _sweep_rows(z0, model.lead)
    # (a profile point is one phase AND one radius: where the profile steps straight out from the axis, two of its points share a
    # phase, and as one row they read a radius between them. gear_hel_m1z40_b45's polygon steps 0.044 mm at each root corner, from
    # r 18.750 to 18.794 at one angle; merged, its roots read 18.7719, and the end planes' corners stood 0.027 off the mesh)
    lo_r, hi_r = np.percentile(rho, 1), np.percentile(rho, 99)
    step = max(SWEEP_STEP_SHARE * float(hi_r - lo_r), 2e-3)
    rows = []
    for g in groups:
        if len(g) < SWEEP_MIN_ROW:
            continue
        o = g[np.argsort(rho[g])]
        for part in np.split(o, np.flatnonzero(np.diff(rho[o]) > step) + 1):
            if len(part) >= SWEEP_MIN_ROW:
                rows.append((float(z0[part].mean()), float(np.median(rho[part])), float(np.ptp(rho[part]))))
    if len(rows) < SWEEP_MIN_ROWS:
        return None
    R = np.array(rows)
    R = R[np.argsort(R[:, 0], kind="stable")]
    # (rows at one phase run in or out as the profile goes on from the row before them)
    # (two rows within one phase gap can only be the parts of one group: _sweep_rows parts groups wider apart)
    same = np.r_[False, np.diff(R[:, 0]) <= SWEEP_PHASE_GAP * model.lead]
    i = 0
    while i < len(R):
        j = i
        while j + 1 < len(R) and same[j + 1]:
            j += 1
        if j > i:
            run = R[i:j + 1]
            before = R[i - 1, 1] if i > 0 else R[-1, 1]
            R[i:j + 1] = run[np.argsort(run[:, 1] if before <= run[:, 1].mean() else -run[:, 1])]
        i = j + 1
    P = R[:, :2]
    # (how far the rows themselves agree: the tolerance a piece is judged by)
    tol = max(3.0 * float(np.median(R[:, 2])), 2 * mesh.noise, 1e-4)
    # corners: turning at least SWEEP_CORNER_DEG, the profile closed over the lead
    ring_pts = np.r_[P[-1:] - [model.lead, 0], P, P[:1] + [model.lead, 0]]
    din, dout = ring_pts[1:-1] - ring_pts[:-2], ring_pts[2:] - ring_pts[1:-1]
    ang = np.degrees(np.abs(np.arctan2(din[:, 0] * dout[:, 1] - din[:, 1] * dout[:, 0], np.einsum("ij,ij->i", din, dout))))
    corners = np.flatnonzero(ang >= SWEEP_CORNER_DEG)
    if len(corners) < 2:
        return None
    # (the profile starts at a corner, as a thread's does: screws.real_start)
    k0 = int(corners[0])
    P = np.r_[P[k0:], P[:k0] + [model.lead, 0]]
    corners = np.r_[corners[corners >= k0] - k0, corners[corners < k0] + len(R) - k0, len(R)]
    P = np.r_[P, P[:1] + [model.lead, 0]]

    def straight(run):
        chord = run[-1] - run[0]
        L = max(float(np.hypot(*chord)), 1e-12)
        dev = np.abs((run[:, 0] - run[0, 0]) * chord[1] - (run[:, 1] - run[0, 1]) * chord[0]) / L
        return len(run) <= 2 or dev.max() <= tol

    # (a tip or root at one radius first: exactly flat at its rows' own radius, as a thread's crest is (_model_of), so it is built
    # as the designer's cylinder (merged._thread_flat). Its corner rows, shared with the flanks, read a hair off it, where the
    # flank's last row meets the band: the planet gear's tips 12.3474 at the corners, 12.3464 along the band. They are moved before
    # the pieces either side are fitted, so an arc or a curve ending there passes through the corner where it now stands: moved
    # after, each arc missed the row its neighbour ended on, and their shared edge lay on one face only)
    runs = list(zip(corners[:-1], corners[1:]))
    flat = [straight(P[a:b + 1]) and np.ptp(P[a:b + 1, 1]) <= tol for a, b in runs]
    level = [float(np.median(P[a:b + 1, 1])) for a, b in runs]
    for (a, b), is_flat, rm in zip(runs, flat, level):
        if is_flat:
            P[a, 1] = P[b, 1] = rm
    # (the profile closes a lead on: its last row is its first)
    if flat[-1]:
        P[0, 1] = P[-1, 1]
    else:
        P[-1, 1] = P[0, 1]
    rows_out, arcs, curves = [P[0].copy()], [], []
    for (a, b), is_flat in zip(runs, flat):
        run = P[a:b + 1]
        if is_flat or straight(run):
            rows_out.append(run[-1].copy())
            continue
        arc = _arc_through_ends(run, tol)
        first = len(rows_out) - 1
        rows_out.extend(run[1:])
        if arc is not None:
            arcs.append((first, len(rows_out) - 1) + arc)
        else:
            curves.append((first, len(rows_out) - 1))
    prof = np.array(rows_out)
    prof[:, 0] -= model.lead * math.floor(float(prof[0, 0]) / model.lead)
    return prof, arcs, curves, model


def _arc_through_ends(run, tol):
    """(centre, radius) of the circle through a stretch's two end rows that fits the rows between best, if every row lies within
    tol of it and the stretch goes the short way round it (as ArcFlank turns); else None. Through its ends, as a thread's arcs are
    (screws._circle_centre): the pieces either side end on those rows, and the arc must meet them there."""
    from . import screws
    a, b = run[0], run[-1]
    centre, radius = screws._circle_centre(run, a, b)
    if float(np.abs(np.hypot(run[:, 0] - centre[0], run[:, 1] - centre[1]) - radius).max()) > tol:
        return None
    # (the short way round: the rows bulge out across the chord, away from the centre)
    chord = b - a
    rows = chord[0] * (run[1:-1, 1] - a[1]) - chord[1] * (run[1:-1, 0] - a[0])
    mid = chord[0] * (centre[1] - a[1]) - chord[1] * (centre[0] - a[0])
    if not (rows * mid < 0).all():
        return None
    return centre, radius


def _sweep_corners(mesh, model, ring):
    """The corners a screw sweep's profile is read from (_swept_profile): those of the triangles whose normals are square to the
    motion and that are joined, edge to edge through such triangles, to one within the ring its flanks read (widened by
    SWEEP_RING_MARGIN); None if too few.

    Joined, not within the ring: the ring is where the surface leans enough to be a flank, and a profile can run on past it where
    it leans less. twist_drill_s2's flutes run from r 3 to 6, its ring read 4.21 to 5.26, and the corners kept in the ring made 16
    of its 48 rows: too few, and no sweep. A surface any motion along the axis leaves in place (a bore, a shaft) agrees with the
    motion too, but it is kept out unless it is joined to the sweep through triangles that agree: an end face or a chamfer between
    them doesn't.

    For a screw sweep only, never a thread (screws.sweep_only keeps threads out). An end face misfits a motion by atan(pitch / its
    radius), tens of degrees on a sweep's long lead but under SWEEP_MISFIT_DEG on a fine thread (1.2 deg at r 22 on a lead of 3), so
    on a thread this joining runs on over the end faces to the part's other surfaces about the axis: read this way, 86 of the 225
    threads in the 550 parts took corners past their own profile's radii, 78 by more than a tenth of the thread's depth
    (2026-11-18, compare/threads/runs/rowread)."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    T = mesh.pts[mesh.tris]
    X = T.mean(axis=1)
    vel = np.cross(model.d, X - model.a) + model.pitch * model.d
    vel /= np.maximum(np.linalg.norm(vel, axis=1), 1e-300)[:, None]
    idx = np.flatnonzero(np.abs(np.einsum("ij,ij->i", mesh.tn, vel)) < math.sin(math.radians(SWEEP_MISFIT_DEG)))
    if not len(idx):
        return None
    lo, hi = ring
    lo, hi = lo - SWEEP_RING_MARGIN * (hi - lo), hi + SWEEP_RING_MARGIN * (hi - lo)
    rho, _, _ = model.local(X[idx])
    seed = (rho >= lo) & (rho <= hi)
    if not seed.any():
        return None
    # (the agreeing triangles joined by shared edges: each edge's two triangles, as a graph)
    tri = mesh.tris[idx]
    e = np.sort(np.r_[tri[:, [0, 1]], tri[:, [1, 2]], tri[:, [2, 0]]], axis=1).astype(np.int64)
    owner = np.tile(np.arange(len(idx)), 3)
    key = e[:, 0] * (int(e.max()) + 1) + e[:, 1]
    order = np.argsort(key, kind="stable")
    same = key[order][1:] == key[order][:-1]
    a, b = owner[order][:-1][same], owner[order][1:][same]
    graph = coo_matrix((np.ones(len(a)), (a, b)), shape=(len(idx), len(idx)))
    _, label = connected_components(graph, directed=False)
    keep = np.isin(label, np.unique(label[seed]))
    P = mesh.pts[np.unique(tri[keep].ravel())]
    return P if len(P) >= SWEEP_MIN_ROW * SWEEP_MIN_ROWS else None


def _sweep_rows(z0, lead):
    """(groups, phases): a swept mesh's corners grouped into its rows by phase, each group the indices of one row's corners (a row
    across phase 0 kept whole, its phases there a lead back)."""
    o = np.argsort(z0)
    gaps = np.diff(z0[o])
    gap = max(SWEEP_PHASE_GAP * lead, 10 * float(np.median(gaps))) if len(gaps) else SWEEP_PHASE_GAP * lead
    groups = np.split(o, np.flatnonzero(gaps > gap) + 1)
    z0 = np.asarray(z0, float).copy()
    if len(groups) > 1 and z0[groups[0][0]] + lead - z0[groups[-1][-1]] <= gap:
        last = groups.pop()
        z0[last] -= lead
        groups[0] = np.r_[last, groups[0]]
    return groups, z0


def _refined_sweep(model, P):
    """The screw sweep's motion refined so that each row of its corners comes to one point of the profile (rows by _sweep_rows):
    the axis's tilt (two angles) and place (two) and the pitch, least squares over every corner's offset from its row's mean phase
    and radius, the rows found again after each fit, SWEEP_REFINE_ROUNDS times at most. The motion as screws reads it lands within its
    allowance but can be off in lead by a few parts in a thousand on a short sweep of long lead: gear_hel_m1z40_b45 (OpenSCAD, lead
    125.664, 0.32 of a turn) read 125.333, so each row's corners smeared along its height (radius spread 0.29 mm), half the rows
    merged, and its flanks came out as 186 faces instead of 80. Refined, its 480 rows agree exactly and the lead is 125.6637; on an
    exact reading (03_Planet_Gear) the fit moves nothing (0.002 deg, 0.0006 mm, a lead 1.000003 times)."""
    from scipy.optimize import least_squares
    d0, e1, e2 = model.d, model.e1, model.e2
    a0, p0 = model.a, model.pitch

    def build(x):
        d = d0 + x[0] * e1 + x[1] * e2
        return Helical(a0 + x[2] * e1 + x[3] * e2, d / np.linalg.norm(d), p0 * (1 + x[4]), model.profile, True, model.k, model.zref)

    x = np.zeros(5)
    for _ in range(SWEEP_REFINE_ROUNDS):
        m = build(x)
        _, z0, _ = m.local(P)
        groups, _ = _sweep_rows(z0, m.lead)
        groups = [g for g in groups if len(g) >= SWEEP_MIN_ROW]
        if len(groups) < SWEEP_MIN_ROWS:
            break
        idx = np.concatenate(groups)
        lab = np.concatenate([np.full(len(g), k) for k, g in enumerate(groups)])
        cnt = np.bincount(lab).astype(float)
        first = np.array([g[0] for g in groups])

        def residual(xx):
            mm = build(xx)
            rho, z, _ = mm.local(P)
            # (each corner's phase against its row's first corner's, round the lead: a row across phase 0 stays one row)
            dz = (z[idx] - z[first][lab] + 0.5 * mm.lead) % mm.lead - 0.5 * mm.lead
            r = rho[idx]
            return np.r_[dz - (np.bincount(lab, dz) / cnt)[lab], r - (np.bincount(lab, r) / cnt)[lab]]

        fit = least_squares(residual, x, x_scale=[1e-4, 1e-4, 1e-3, 1e-3, 1e-5], max_nfev=60)
        moved = np.abs(fit.x - x).max()
        x = fit.x
        if moved < 1e-9:
            break
    return build(x)


def _model_of(mesh, pool, V, F, S, r, X=None):
    """(thread, facets) from a motion found on the surface, or None: read as a thread (_thread_model), or as a screw sweep where
    it is only that; and, where the thread's reader reads nothing on a motion that is a screw sweep too, read as the sweep
    (_read_as_sweep). A section with several radii a phase has no outline for the thread's reader: ABC 054's filament winder (a
    hub, the flight's faces and its tip; column IQR 2.0 of a 5.3 mm ring) passed both verdicts, its outline collapsed to two
    corners, and nothing else was tried, while the sweep reader reads it at lead 13.00000 (464,695 of 489,214 facets in pieces).
    Over 2,070 parts (the 551 gate meshes, 129 on both meshes, the easy ABC set, 054) this reads two motions, both true sweeps:
    054 and spring_r10 (5.99996 for 6.0); no motion of a sweep alone went unread (2026-11-19, wt_129d)."""
    from . import screws
    got = _thread_model(mesh, pool, V, F, S, r, X)
    if got is None and READER_FALLBACK and not screws.sweep_only(r) and screws.sweep_verdict(r):
        return _read_as_sweep(mesh, pool, r)
    return got


def _read_as_sweep(mesh, pool, r):
    """The reader fallback: a motion the thread's reader read nothing on, read as the screw sweep it also is (_model_of)."""
    return _swept_model(mesh, pool, r)


def _swept_model(mesh, pool, r):
    """(thread, facets) for a screw sweep: its profile from the mesh's own corners, as the motion found it (_swept_profile)."""
    c, a = r["c"], np.asarray(r["a"], float)
    z_at = float(a @ c)
    zref = float(np.mean(r["z"])) + z_at
    pitch = r["hand"] * r["lead"] / (2 * math.pi)
    k = float(r["k"])
    model = Helical(a, c, pitch, [[0, 1], [1, 1]], True, k, zref)
    low, high = r["z"][0] + z_at - model.lead, r["z"][1] + z_at + model.lead
    read = _swept_profile(mesh, model, r["rho"])
    if read is None:
        return None
    prof, arcs, curves, motion = read
    model = Helical(motion.a, motion.d, motion.pitch, prof, True, k, zref)
    corners = np.unique(np.concatenate([mesh.fverts[f] for f in pool]))
    return _with_facets(mesh, pool, model, prof, arcs, curves, corners, low, high, k, zref, swept=True)


def _thread_model(mesh, pool, V, F, S, r, X=None):
    """(thread, facets) from a motion found on the surface, or None. The profile is read off the points that land (X, if they
    are already known for this motion), then the axis, pitch and corners are fitted to the mesh's corners, which lie on the
    thread to the file's precision."""
    from . import screws
    c, a = r["c"], np.asarray(r["a"], float)
    z_at = float(a @ c)                     # (what the motion's own z, from a, is on the model's, from the plane through the origin)
    zref = float(np.mean(r["z"])) + z_at
    pitch = r["hand"] * r["lead"] / (2 * math.pi)
    k = float(r["k"])
    model = Helical(a, c, pitch, [[0, 1], [1, 1]], True, k, zref)
    if screws.sweep_only(r):
        # (a screw sweep: its profile from the mesh's own corners, as the motion found it; see _swept_profile)
        return _swept_model(mesh, pool, r)
    X = screws.landed(S, V, F, r) if X is None else X
    if X is None:
        return None
    rho, z0, _ = model.local(X)
    read = screws.profile_of(np.c_[z0, rho], model.lead)
    if read is None:
        return None
    prof = _closed(read[0], model.lead)
    # corners of the thread's own stretch: elsewhere on the axis a plain bore or shank can lie on the profile by chance
    low, high = r["z"][0] + z_at - model.lead, r["z"][1] + z_at + model.lead
    corners = np.unique(np.concatenate([mesh.fverts[f] for f in pool]))
    h = mesh.pts[corners] @ model.d
    P = mesh.pts[corners[(h >= low) & (h <= high)]]
    if not len(P):
        return None
    for near in (min(0.05, 0.04 * model.lead), min(0.02, 0.016 * model.lead)):
        trial = Helical(model.a, model.d, model.pitch, prof, True, k, zref)
        q, z_, _ = trial.local(P)
        on = np.abs(trial._profile_distance(z_, q)) <= near
        if on.sum() < 3 * len(prof):
            break
        fitted, fit = _refine(trial, prof, P[on])
        if (np.diff(fit[:, 0]) <= 0).any():
            # (two corners that slid onto one, a hair past each other, are one corner at this
            # resolution: merged, the fit stands; a wider fold drops it, and the reading stands)
            fit = _unfolded(fit, fitted.lead, near)
            if fit is None:
                break
            fitted = Helical(fitted.a, fitted.d, fitted.pitch, fit, True, k, zref)
        model, prof = fitted, fit
    if np.ptp(prof[:, 1]) > screws.DEPTH_OF_LEAD * model.lead:
        return None                         # deeper than its lead: no thread is
    prof = screws.real_start(_simplify(prof, PROFILE_TOL), model.lead, PROFILE_TOL)
    # a crest or root flat to within the file's rounding: exactly flat (only then: a
    # thread's own crest can slope or bow by a few microns, and flattening it would
    # push its corners off)
    ring = prof[:-1].copy()
    for i in range(len(ring)):
        j = (i + 1) % len(ring)
        if abs(ring[j, 1] - ring[i, 1]) <= max(2 * mesh.noise, 0.001):
            ring[i, 1] = ring[j, 1] = (ring[i, 1] + ring[j, 1]) / 2
    prof = np.r_[ring, ring[:1] + [model.lead, 0]]
    model = Helical(model.a, model.d, model.pitch, prof, True, k, zref)
    # the rounded stretches, read off the outline about the motion as it now stands, each a true arc with the straight pieces either side meeting it
    q, z_, _ = model.local(X)
    prof, arcs = screws.with_arcs(np.c_[z_, q], prof, model.lead)
    model = Helical(model.a, model.d, model.pitch, prof, True, k, zref)
    return _with_facets(mesh, pool, model, prof, arcs, [], corners, low, high, k, zref)


def _with_facets(mesh, pool, model, prof, arcs, curves, corners, low, high, k, zref, swept=False):
    """(thread, facets) for a motion and its profile: the pool's facets lying on it (_carried_on), which side is material, and the
    thread's outer radius; None if too few facets lie on it."""
    # (as near as its pieces take them: held to ON_TOL, the facets of a flank wandering a hundredth or two off true were left out)
    model.arcs, model.curves = arcs, curves
    tol = piece_tol(model) + mesh.noise
    off = np.full(len(mesh.pts), np.inf)
    off[corners] = np.abs(model.signed(mesh.pts[corners]))
    height = mesh.pts @ model.d
    on = np.array([f for f in pool if (off[mesh.fverts[f]] <= tol).all()], int)
    facets = _carried_on(mesh, model, on, height, low, high)
    if len(facets) < MIN_FACETS:
        return None
    # which side is material: facets' normals point away from it
    Xf, Nf = mesh.fcent[facets], mesh.fn[facets]
    radial = Xf - model.a - np.outer((Xf - model.a) @ model.d, model.d)
    inside = (np.einsum("ij,ij->i", radial, Nf) * mesh.farea[facets]).sum() > 0
    thread = Helical(model.a, model.d, model.pitch, prof, inside, k, zref)
    thread.arcs, thread.curves, thread.swept = arcs, curves, swept
    Q = mesh.pts[np.unique(np.concatenate([mesh.fverts[f] for f in facets]))]
    w = Q - thread.a - np.outer((Q - thread.a) @ thread.d, thread.d)
    thread.outer = float(np.linalg.norm(w, axis=1).max())
    return thread, facets


def _carried_on(mesh, model, on, height, low, high):
    """The facets lying on a thread (`on`) that are its: those within its motion's own stretch [low, high], and those beyond it
    where the thread itself goes on. The stretch is where the motion's points land (screws: a point moved by the motion lands back on
    the surface), a lead either way. On a thread that goes only part way round, a move runs off the missing part, so the points stop
    landing short of where the thread ends: test14's thread, cut by a slope over its last eight turns (all round at one end, 8 of 36
    ten-degree steps at the other), landed only on its whole turns, and its facets on the rest went to 88 narrow cylinder strips round
    its axis (1,534 faces against 1,063 when they were the thread's, and 34% more time). So the stretch is carried on along the axis
    while the thread's leaning facets (its flanks: a plain bore or shank at the crest's radius, which the stretch keeps out, has none)
    lying on it continue, with no step over one lead; and a facet beyond the stretch is taken only if it is reached from the stretch's
    own through shared edges of facets lying on the thread. Carried by flanks alone, a reading on a long lead jumped 16 mm to facets
    that lay on it by chance (04_Carrier's, lead 17)."""
    from . import screws
    if not len(on):
        return on
    hmin = np.array([height[mesh.fverts[f]].min() for f in on])
    hmax = np.array([height[mesh.fverts[f]].max() for f in on])
    inside = (hmin >= low) & (hmax <= high)
    lean = np.abs(mesh.fn[on] @ model.d) >= screws.LEAN
    lo, hi = low, high
    for k in np.argsort(-hmax):
        if lean[k] and hmin[k] < low:
            if lo - hmax[k] > model.lead:
                break
            lo = min(lo, hmin[k])
    for k in np.argsort(hmin):
        if lean[k] and hmax[k] > high:
            if hmin[k] - hi > model.lead:
                break
            hi = max(hi, hmax[k])
    if lo == low and hi == high:
        return on[inside]
    carried = on[(hmin >= lo) & (hmax <= hi)]
    reached = _reach(mesh, on[inside], on)
    return np.array(sorted(set(on[inside].tolist()) | (set(carried.tolist()) & set(reached.tolist()))), int)


METRIC_PITCHES = (0.2, 0.25, 0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.75, 0.8, 1.0, 1.25, 1.5,
                  1.75, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0)


def name(thread):
    """The thread in plain words, e.g. "M12 x 1.5, right-hand, external"."""
    major = 2 * thread.outer
    lead = thread.lead
    tpi = 25.4 / lead
    nearest = min(METRIC_PITCHES, key=lambda p: abs(p - lead))
    if abs(nearest - lead) <= 0.01 * lead and abs(major - round(major)) <= 0.4:
        size = f"M{round(major)} x {nearest:g}"
    elif abs(tpi - round(tpi)) <= 0.01 * tpi:
        size = f'{major / 25.4:.3f}" x {round(tpi)} TPI'
    else:
        size = f"dia {major:.2f} x pitch {lead:.3f}"
    hand = "right-hand" if thread.pitch > 0 else "left-hand"
    return f"{size}, {hand}, {'tapered, ' if thread.k else ''}{'external' if thread.inside else 'internal'}"


def _on_pieces(mesh, thread, facets):
    """(pieces, tol, homes, across): the thread's pieces, its tolerance, homes(corners), the pieces holding every one of these
    corners (of the facets'): each within the tolerance of the piece, and all turning less than MAX_FACET_TURN along it; and
    across(corners), the two neighbouring pieces that hold them between them, each holding at least one, or None. homes.bulk(vs)
    gives homes(v) for each list of corners v of vs at once (patches, parting): one call on a facet costs as much as on many.

    Held sparsely (2026-11-19, speed only, the answers unchanged): which corners each piece holds, and its U at the corners it or
    a neighbouring piece holds (all that homes and across read), not a piece by every mesh point. As two full arrays of pieces x
    mesh points, ABC 054's screw sweep (985 pieces, 283,647 points) took 2.5 GB, and homes, a loop over every piece for every
    facet in Python, took 2.0-2.4 us a facet-piece: 19 minutes over its 489,214 facets, paid twice by _read's comparison.
    Each piece still places the same array of the facets' corners in one call, as before: placed in chunks, the last rows of
    an array can take another path through the BLAS kernel and round otherwise."""
    tol = piece_tol(thread) + mesh.noise
    pieces = pieces_of(thread)
    m = len(pieces)
    corners = np.unique(np.concatenate([mesh.fverts[f] for f in facets]))
    pos = np.full(len(mesh.pts), -1, np.int64)
    pos[corners] = np.arange(len(corners))
    P = mesh.pts[corners]
    held = [None] * m          # (per piece: the corners it holds, as indices into corners, ascending)
    keep = [None] * m          # (per piece: (indices, U there) at the corners it or a neighbouring piece holds)
    pair_c, pair_k, pair_u = [], [], []
    full = {}                  # (U over every corner, for the pieces still waiting for a neighbour's holdings)
    mark = np.zeros(len(corners), bool)

    def kept(k):
        # (the corners piece k or a neighbour holds, ascending: marked and read back, in time linear in the corners; np.unique
        # sorted them, a third of the whole build on a part of 4 pieces)
        for j in ((k - 1) % m, k, (k + 1) % m) if m > 1 else (k,):
            mark[held[j]] = True
        idx = np.flatnonzero(mark)
        mark[idx] = False
        keep[k] = (idx, full[k][idx])

    for k, piece in enumerate(pieces):
        s, off, u = piece.place(P)
        held[k] = np.flatnonzero((np.abs(off) <= tol) & (s >= -tol) & (s <= piece.length + tol))
        full[k] = np.array(u, dtype=float)
        pair_c.append(held[k])
        pair_k.append(np.full(len(held[k]), k, np.int64))
        pair_u.append(full[k][held[k]])
        if k >= 2 and k - 1 != m - 1:
            kept(k - 1)
            del full[k - 1]
    for k in sorted(full):
        kept(k)
    full.clear()
    pc = np.concatenate(pair_c) if pair_c else np.zeros(0, np.int64)
    order = np.argsort(pc, kind="stable")          # (by corner, the pieces ascending within each)
    hc, hk = pc[order], np.concatenate(pair_k)[order]
    hu = np.concatenate(pair_u)[order] if pair_u else np.zeros(0)
    start = np.searchsorted(hc, np.arange(len(corners) + 1))
    turn = math.radians(MAX_FACET_TURN)

    def holds(k, c):
        h = held[k]
        i = np.minimum(np.searchsorted(h, c), len(h) - 1) if len(h) else np.zeros(len(c), np.int64)
        return h[i] == c if len(h) else np.zeros(len(c), bool)

    def u_at(k, c):
        idx, val = keep[k]
        return val[np.searchsorted(idx, c)]

    def homes(v):
        return bulk([v])[0]

    def bulk(vs):
        V = [np.asarray(v, np.int64).ravel() for v in vs]
        out = [[] for _ in V]
        if not V:
            return out
        lens = np.array([len(x) for x in V], np.int64)
        flat = pos[np.concatenate(V)]
        inst_f = np.repeat(np.arange(len(V)), lens)
        lone = np.unique(inst_f[flat < 0])          # (a corner outside the facets': none of its list's homes)
        allc = np.where(flat < 0, 0, flat)
        cnt = np.where(flat < 0, 0, start[allc + 1] - start[allc])
        per_f = np.bincount(inst_f, weights=cnt, minlength=len(V)).astype(np.int64)
        first_inst = np.r_[0, np.cumsum(lens)]
        cum = np.r_[0, np.cumsum(per_f)]
        a = 0
        while a < len(V):
            # (lists taken while their pairs of (corner, piece) stay within HOMES_CHUNK_PAIRS, at least one)
            b = max(a + 1, int(np.searchsorted(cum, cum[a] + HOMES_CHUNK_PAIRS, side="right")) - 1)
            b = min(b, len(V))
            ia, ib = first_inst[a], first_inst[b]
            c, n, fl = allc[ia:ib], cnt[ia:ib], inst_f[ia:ib]
            tot = int(n.sum())
            if tot:
                pidx = np.repeat(start[c], n) + (np.arange(tot) - np.repeat(np.cumsum(n) - n, n))
                key = np.repeat(fl, n).astype(np.int64) * m + hk[pidx]
                o = np.argsort(key, kind="stable")
                ks, us = key[o], hu[pidx][o]
                brk = np.flatnonzero(np.r_[True, ks[1:] != ks[:-1]])
                many = np.diff(np.r_[brk, len(ks)])
                spread = np.maximum.reduceat(us, brk) - np.minimum.reduceat(us, brk)
                kf, kk = ks[brk] // m, ks[brk] % m
                ok = (many == lens[kf]) & (spread < turn)
                for f_i, k in zip(kf[ok].tolist(), kk[ok].tolist()):
                    out[f_i].append(int(k))
            a = b
        for f_i in lone.tolist():
            out[f_i] = []
        return out

    def across(v):
        c = pos[np.asarray(v)]
        if m < 2 or (c < 0).any():
            return None
        cand = sorted({int(k) for ci in np.unique(c) for k in hk[start[ci]:start[ci + 1]]})
        limit = m if m > 2 else m - 1
        for k in cand:
            if k >= limit:
                break
            j = (k + 1) % m        # (the profile closes over a lead: its last piece meets its first)
            on_k, on_j = holds(k, c), holds(j, c)
            if (on_k | on_j).all() and on_k.any() and on_j.any() and np.ptp(u_at(k, c)) < turn and np.ptp(u_at(j, c)) < turn:
                return k, j, int(on_k.sum()), int(on_j.sum())
        return None
    homes.bulk = bulk
    return pieces, tol, homes, across


def parting(mesh, thread, facets):
    """{facet: the piece each of its triangles lies on, in the order of mesh.ftris}, for the flat facets that lie on the thread
    across more than one piece of its profile. Where two of OpenSCAD's thread segments meet, a flat band 0.02 mm wide runs
    round the whole profile; its triangles are coplanar, so they were grouped as one facet before anything knew of the
    thread. Each of its triangles lies on one piece; the band as a whole lies on none, and was left over: a sliver face
    between the thread's faces at every segment, 950 of them on one M20. Only a facet whose every triangle lies on one piece,
    and on the thread's surface between its corners too, is parted: a flat face that merely has its corners on the thread
    (an end cap) is a face of its own."""
    if not len(facets):
        return {}
    pieces, tol, homes_of, _ = _on_pieces(mesh, thread, facets)
    many = [int(f) for f in facets if len(mesh.ftris[int(f)]) >= 2]
    # (the facets' homes, then their triangles', each read at once: homes_of.bulk; one by one, a facet of hundreds of corners
    # cost as much as hundreds of calls)
    whole = homes_of.bulk([mesh.fverts[f] for f in many])
    split = [f for f, h in zip(many, whole) if not h]
    by_tri = homes_of.bulk([mesh.tris[t] for f in split for t in mesh.ftris[f]])
    out = {}
    i = 0
    for f in split:
        hs = by_tri[i:i + len(mesh.ftris[f])]
        i += len(mesh.ftris[f])
        if not all(hs):
            continue
        labels = [h[0] for h in hs]
        if len(set(labels)) < 2:
            continue
        if _lies_on(mesh, thread, f, tol):
            out[f] = labels
    return out


def _lies_on(mesh, thread, f, tol):
    """Whether facet f lies on the thread's surface between its corners too: its triangles' edge middles and centres within tol of it. A facet with its corners on the thread
    can still cut across it: a flat end cap, or a long chord across a sharp corner of the profile, which stood 0.15 mm off an M6's thread 0.65 mm deep."""
    T = mesh.pts[mesh.tris[mesh.ftris[f]]]
    between = np.concatenate([(T[:, 0] + T[:, 1]) / 2, (T[:, 1] + T[:, 2]) / 2, (T[:, 2] + T[:, 0]) / 2, T.mean(axis=1)])
    return bool(np.abs(thread.signed(between)).max() <= tol)


def pieces_of(thread):
    """A thread's profile as pieces: an arc for each rounded stretch, a smooth curve for each of a screw sweep's curved stretches,
    a straight piece for each other row of the outline."""
    P = thread.profile
    out, i = [], 0
    by_start = {a[0]: a for a in thread.arcs}
    curve_at = {c[0]: c[1] for c in getattr(thread, "curves", [])}
    while i < len(P) - 1:
        if i in curve_at:
            last = curve_at[i]
            out.append(CurveFlank(thread, P[i:last + 1]))
            i = last
        elif i in by_start:
            _, last, centre, radius = by_start[i]
            out.append(ArcFlank(thread, P[i], P[last], centre, radius))
            i = last
        else:
            out.append(Flank(thread, P[i], P[i + 1]))
            i += 1
    return out


def patches(mesh, thread, facets):
    """The thread's facets as features, one per connected stretch of each straight piece
    of its profile (a facet must lie wholly on one piece; any that don't are left as
    they are)."""
    pieces, tol, homes_of, across = _on_pieces(mesh, thread, facets)
    groups = [[] for _ in pieces]
    both = {}
    home = {}
    whole = set()       # (the facets two or more pieces each hold every corner of: the only ones closeness can decide)
    for f, homes in zip(facets, homes_of.bulk([mesh.fverts[f] for f in facets])):     # (homes_of(mesh.fverts[f]) for each, at once)
        v = mesh.fverts[f]
        if len(homes) == 1:
            groups[homes[0]].append(int(f))
            home[int(f)] = homes[0]
        elif homes:
            both[int(f)] = homes
            whole.add(int(f))
        else:
            # (a facet across the line two pieces meet in: where a mesh's corners don't lie on the profile's corners, its
            # profile sampled at steps of its own, a row of facets crosses every such line, each with its corners on the two
            # pieces either side. Left out, each row was cut into a cylinder face at every turn: 432 faces on a rounded M20 of
            # 6. Such a facet goes to the piece holding more of its corners, and the border between the two is their exact
            # helix whatever way the facets zigzag across it (mmm.cad._helix_between); but only if it lies on the thread
            # between its corners too: across a sharp corner a facet is a chord cutting it, and taken in, chords 0.15 mm off
            # an M6's thread ended the bolt's CAD faces in an exception, where left out they had built)
            pair = across(v)
            if pair is not None and _lies_on(mesh, thread, f, tol):
                k, j, nk, nj = pair
                both[int(f)] = [k] if nk > nj else ([j] if nj > nk else [k, j])
    # (a facet within reach of two pieces goes to the one holding its corners clearly closer, _closest_home; a sliver lying on the
    # corner they meet in is held by both alike, and left out it parts the facets of a flank that it joins, so it goes to the piece
    # most of its neighbours are on. A facet across two pieces, each holding as many of its corners, isn't held whole by either, so
    # how far its other corners stand off each piece says nothing of which it lies on: it goes to the vote, as before the rule, which
    # had moved 498 such facets on ms2_m10x15's two-start thread. Nor can closeness tell two pieces apart for a crumb no larger than
    # the tolerance: it lies wholly in the band where both hold it, _crumb)
    nbrs = _adjacent(mesh)
    choice, voted = {}, {}
    for f, homes in both.items():
        votes = [sum(home.get(g) == k for g in nbrs[f]) for k in homes]
        voted[f] = homes[int(np.argmax(votes))]
        P = mesh.pts[mesh.fverts[f]]
        k = _closest_home(pieces, P, homes, CLOSER_BY + mesh.noise) if len(homes) > 1 and f in whole and not _crumb(P, tol) else None
        choice[f] = voted[f] if k is None else k
    _no_islands(home, choice, voted, nbrs)
    for f in both:
        groups[choice[f]].append(f)
    out = []
    for piece, group in zip(pieces, groups):
        # each connected stretch of a piece is a face of its own (other features may
        # have taken facets out of the middle, and the thread's run-outs leave scraps)
        for part in _joined_across_steps(mesh, group, tol):
            if len(part) >= 3:
                out.append(_piece_feature(mesh, thread, piece, part))
    return out


def _crumb(P, tol):
    """Whether a facet with corners P is no larger than the thread's tolerance tol: then every corner of it lies in the band where
    two pieces meeting at a corner of the profile both hold it, and how much closer it is to one says nothing of which it lies on;
    its neighbours decide (the vote).

    OpenSCAD's threads leave micro-slivers along the corners of their profiles, 0.007-0.013 mm long and under 0.001 mm wide,
    0.005-0.0065 mm from the corner and facing the flank. Given to the flank by closeness, the root flat of an internal thread lost
    its edge: lhi_m10x15 and vi_m8x125 went from 27 and 25 faces to over 2,400, invalid; a partial thread's crest band ended in a
    small plane at every half turn (pe_flat_m10 48 -> 70 faces). The flank facets closeness is for are far larger than the
    tolerance (ABC 00009858: 0.045-0.49 mm across, 10th to 90th percentile, against 0.0254; 380 of its 390 moves stand)."""
    P = np.asarray(P, float)
    return float(np.linalg.norm(P[:, None] - P[None], axis=2).max()) <= tol


def _no_islands(home, choice, voted, nbrs):
    """Return to the neighbours' vote each facet closeness gave a piece (choice[f] other than voted[f]) that can't reach that
    piece's own facets (those it alone holds, in home) through facets given the same piece: an island of it inside another's.

    At a sharp corner of a profile a mesh can leave slivers lying on the corner itself, every corner of them within 0.002 mm of
    it (OpenSCAD's square and rectangular threads: 232 of 234 on sq_d12x2, 344 of 346 on rect_d16x3), surrounded by the
    flat's facets. Both pieces hold them, the flank by a hair closer (0.0011-0.0014 mm, just over CLOSER_BY); given to the flank,
    each was an island of the flank inside the crest flat, joined to the flank across the step (_joined_across_steps), and the
    flank's face crossed itself: sq_d12x2 22 -> 454 faces, 436 of them freeform, rect_d16x3 18 -> 43. The flank facets closeness
    is for (by a narrow crest flat, ABC 00009858, all 390 of them) lie beside their flank and reach it."""
    moved = [f for f, k in choice.items() if k != voted[f]]
    if not moved:
        return
    on = {}
    for f, k in home.items():
        on.setdefault(k, set()).add(f)
    for f, k in choice.items():
        on.setdefault(k, set()).add(f)
    for k in sorted({choice[f] for f in moved}):
        S = on[k]
        seen = {f for f, kk in home.items() if kk == k}
        stack = list(seen)
        while stack:
            for g in nbrs[stack.pop()]:
                if g in S and g not in seen:
                    seen.add(g)
                    stack.append(g)
        for f in moved:
            if choice[f] == k and f not in seen:
                choice[f] = voted[f]


def _closest_home(pieces, P, homes, by):
    """Of the pieces in homes, each holding all of a facet's corners P within the thread's tolerance, the one holding them
    closest (by the worst corner's distance to each piece) when it holds them closer than every other by more than `by`; None
    when two hold them alike, as a sliver lying on the corner two pieces meet in is held by both.

    The tolerance that lets a piece hold a facet is wide (piece_tol: a hundredth of the lead, at least ON_TOL), for CAD
    programs' threads stand a hundredth or two off a true sweep. So by a narrow crest or root flat, a flank facet beside the
    corner is within it of the flat too, and the neighbours' vote that used to settle every such facet went to the flat: its
    other neighbours, beside the corner as well, were undecided themselves and didn't count, while the crest's slivers did.
    ABC 00009858 (a wood screw, a crest flat 0.32 mm wide on a 2.54 lead, the facets beside it 0.37 mm across): 387 of the
    flat's 909 facets were flank facets the vote gave it, 0.0162 off it and 0.0028 off their flank (worst corner, medians).
    Built on the flat, one turn of it crossed itself, every face of the thread failed, and the part fell back to 380 freeform
    faces; given by closeness, it builds 37 faces, the designer's crest and root cylinders and flanks among them."""
    off = np.array([float(np.abs(pieces[k].place(P)[1]).max()) for k in homes])
    order = np.argsort(off, kind="stable")
    if off[order[1]] - off[order[0]] > by:
        return homes[int(order[0])]
    return None


def _joined_across_steps(mesh, group, gap):
    """The connected stretches of a piece's facets, those parted only by a gap narrower than this being one. Threads from OpenSCAD
    are built from pieces that don't quite meet: each flank facet is parted from the next by a step 0.02 mm wide, facing somewhere
    else, which belongs to no piece (one thread of 12 pieces came out in 2,000 stretches, most too small to keep). A real cut across
    a thread, such as a flat or a slot, is far wider than the thread's tolerance, and still parts it."""
    from scipy.spatial import cKDTree
    left = set(group)
    parts = []
    while left:
        part = _reach(mesh, [min(left)], list(left))
        left -= set(part.tolist())
        parts.append(part)
    if len(parts) < 2:
        return parts
    corners = [np.unique(np.concatenate([mesh.fverts[f] for f in part])) for part in parts]
    owner = np.concatenate([np.full(len(c), i) for i, c in enumerate(corners)])
    near = cKDTree(mesh.pts[np.concatenate(corners)]).query_pairs(gap, output_type="ndarray")
    parent = list(range(len(parts)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    a, b = owner[near[:, 0]], owner[near[:, 1]]
    for i, j in zip(a[a != b].tolist(), b[a != b].tolist()):
        parent[root(i)] = root(j)
    joined = {}
    for i, part in enumerate(parts):
        joined.setdefault(root(i), []).append(part)
    return [np.sort(np.concatenate(v)) for v in joined.values()]


def carve(mesh, feature, points, reach):
    """A thread piece blamed for trouble at these points (a gap at its outline) less
    its facets within reach of them, which are left flat: its connected stretches, each
    a piece of its own on the same surface; None if it would lose half of itself.
    Dropped whole, a flank running all of a thread's turns took thousands of facets
    with it (the gutter mount's screw at --accuracy 0.05: 5,936 facets, 6,184 faces)."""
    if points is None or not len(points) or len(feature.facets) < CARVE_MIN or feature.__dict__.get("carved"):
        return None                     # (carved once already: blamed again, it goes)
    from scipy.spatial import cKDTree
    facets = feature.facets
    size = np.array([np.linalg.norm(mesh.pts[mesh.fverts[a]] - mesh.fcent[a], axis=1).max() for a in facets])
    d, _ = cKDTree(np.asarray(points, float)).query(mesh.fcent[facets])
    keep = facets[d > reach + size]
    if len(keep) == len(facets) or len(keep) < len(facets) / 2:
        return None
    piece = feature.model
    out = [_piece_feature(mesh, piece.thread, piece, part) for part in _stretches(mesh, keep.tolist())]
    for f in out:
        f.carved = True
    return out


def _stretches(mesh, group):
    """The connected stretches (3 facets or more) of a piece's facets."""
    left = set(group)
    while left:
        part = _reach(mesh, [min(left)], list(left))
        left -= set(part.tolist())
        if len(part) >= 3:
            yield part


def piece_tol(thread):
    """How near one piece of a found thread's profile a facet's corners must lie to be
    built on it. CAD programs store threads as splines a hundredth or two off a true
    helical sweep (an M30 x 3.5's flanks scatter +-0.015 mm, its crests exact), and held
    to ON_TOL half of each flank went to freeform patches. But on a small thread that
    is a good share of its depth: an M3 x 0.5's narrow crest and root bands took in
    facets that weren't theirs, and failed (the GPS case back: 1,502 -> 2,548 faces)."""
    return min(PIECE_TOL, max(ON_TOL, PIECE_SHARE * thread.lead))


def _piece_feature(mesh, thread, piece, fids):
    from .features import Feature
    tids = np.concatenate([mesh.ftris[f] for f in fids])
    T = mesh.tris[tids]
    # how far each facet sits from the true surface, at its edge midpoints
    corners = mesh.pts[T]
    gap = piece.signed(((corners + corners[:, [1, 2, 0]]) / 2).reshape(-1, 3)).reshape(-1, 3)
    area = mesh.tarea[tids]
    s, _, U = piece.place(mesh.pts[np.unique(T)])
    rho = thread.profile[:, 1]
    if abs(piece.t[1]) > 0.1:
        part = "flank"
    elif piece.p[1] > (rho.min() + rho.max()) / 2:
        part = "crest" if thread.inside else "root"
    else:
        part = "root" if thread.inside else "crest"
    return Feature(piece, "screw thread", f"{name(thread)}: {part}", convex=thread.inside,
                   kind="trimmed", facets=fids,
                   change=-float((area * gap.mean(axis=1)).sum()),
                   tolerance=0.5 * float((area * np.abs(gap).mean(axis=1)).sum()) + 1e-3,
                   worst=float(np.abs(gap).max()),
                   u0=float(U.min()), span=float(np.ptp(U)), lo=float(s.min()), hi=float(s.max()))


def _reach(mesh, start, allowed):
    """Facets of `allowed` reached from those of `start` among them through shared edges."""
    allowed = set(np.asarray(allowed).tolist())
    todo = [f for f in np.asarray(start).tolist() if f in allowed]
    seen = set(todo)
    while todo:
        f = todo.pop()
        for g in _adjacent(mesh)[f]:
            if g in allowed and g not in seen:
                seen.add(g)
                todo.append(g)
    return np.array(sorted(seen), int)


def _adjacent(mesh):
    """Facets sharing an edge with each facet, however sharply they meet (cached)."""
    if "all_nbrs" not in mesh.__dict__:
        nbrs = [set() for _ in range(len(mesh.fn))]
        for sides in mesh.edge_facets.values():
            for f in sides:
                nbrs[f].update(int(g) for g in sides if g != f)
        mesh.all_nbrs = [sorted(x) for x in nbrs]
    return mesh.all_nbrs


def _refine(model, prof, P):
    """Axis (tilt and position), pitch and profile corners fitted together (least
    squares) to corners P that lie on the thread."""
    from scipy.optimize import least_squares
    d0, e1, e2 = model.d, model.e1, model.e2
    k = len(prof) - 1                   # corners (the last repeats the first a lead on)

    def build(x):
        d = d0 + x[0] * e1 + x[1] * e2
        a = model.a + x[2] * e1 + x[3] * e2
        pitch = model.pitch * (1 + x[4])
        corners = prof[:-1] + x[5:].reshape(k, 2)
        lead = 2 * math.pi * abs(pitch)
        closed = np.r_[corners, corners[:1] + [lead, 0]]
        return Helical(a, d, pitch, closed, True, model.k, model.zref)

    last = {}

    def at(x):
        """The model at x, its points' places and their nearest profile pieces, kept
        for the Jacobian: scipy asks for it at the x it has just had the residual of,
        and working them out twice was half of each refit (the gutter mount's screw
        spent 80 of its 100 s of thread search refitting)."""
        key = x.tobytes()
        if last.get("key") != key:
            m = build(x)
            rho, z0, _ = m.local(P)
            last.update(key=key, m=m, rho=rho, z0=z0, near=m._nearest(z0, rho))
        return last["m"], last["rho"], last["z0"], last["near"]

    def residual(x):
        m, rho, z0, near = at(x)
        best, sign = near[:2]
        return np.r_[best * (sign if m.inside else -sign), HOLD * x[5:]]

    def jac(x):
        # Left to scipy, the whole residual was recomputed once per unknown: 30 s a
        # refit on a 7,000-corner thread, and a part tries up to 300. A point's
        # distance moves with the point itself (its place in the profile's plane), and
        # with the two ends of the piece its foot is on, by how far along it the foot
        # is. Only the points' places are taken by differences, for the axis and pitch.
        m, rho, z0, (best, sign, piece, along, wrap, away) = at(x)
        g = -sign[:, None] * away                   # d(distance)/d(foot) = -d/d(point)
        J = np.zeros((len(P) + 2 * k, len(x)))
        for i in range(5):
            h = EPS_STEP * max(1.0, abs(x[i]))
            xi = x.copy()
            xi[i] += h
            mi = build(xi)
            rho_i, z0_i, _ = mi.local(P)
            dz = z0_i - z0
            dz -= mi.lead * np.round(dz / mi.lead)  # (a point wrapping round the lead)
            col = -(g[:, 0] * dz + g[:, 1] * (rho_i - rho))
            if i == 4:      # the pitch also moves the profile's copies, and its closing corner
                col += g[:, 0] * (wrap + np.where(piece == k - 1, along, 0.0)) * (mi.lead - m.lead)
            J[:len(P), i] = col / h
        rows = np.arange(len(P))
        for end, share in ((piece, 1 - along), ((piece + 1) % k, along)):
            for c in range(2):
                np.add.at(J, (rows, 5 + 2 * end + c), share * g[:, c])
        J[len(P):, 5:] = HOLD * np.eye(2 * k)
        return J

    fit = least_squares(residual, np.zeros(5 + 2 * k), jac=jac, x_scale="jac", max_nfev=200)
    m = build(fit.x)
    return m, m.profile


def _simplify(P, tol):
    """Douglas-Peucker on an open polyline (keeps both ends)."""
    keep = np.zeros(len(P), bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(P) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = P[i], P[j]
        ab = b - a
        t = np.clip(((P[i + 1:j] - a) @ ab) / max(ab @ ab, 1e-300), 0, 1)
        d = np.linalg.norm(P[i + 1:j] - (a + np.outer(t, ab)), axis=1)
        k = int(np.argmax(d))
        if d[k] > tol:
            m = i + 1 + k
            keep[m] = True
            stack += [(i, m), (m, j)]
    return P[keep]
