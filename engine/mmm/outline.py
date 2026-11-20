"""Stated lines and arcs of a sketch outline (2026-11-13). Written by probes/patch_stated81.py from
probes/outline_fit.py: change that, not this file. A line or an arc is STATED where it fits the outline's points
within QUIET of the mesh's exact limit and is left by a jump of JUMP limits when the next point is added, or ends
with the outline. Measured on 57 designers' parts (probes/arc_jump.py): designers' arcs fit at 0.045 of the limit
and are left by 458 limits; arcs that only approximate a free curve fill 0.81 and are left by 1.2. Part of the
merged engine's design-intent prototype (AGPL-3.0)."""
import numpy as np

QUIET = 0.25                        # a stated line or arc fits within this share of the tolerance
JUMP = 10.0                         # and is left by a misfit of this many tolerances with the next point added


def line_fit(Q):
    c = Q - Q.mean(0)
    return float(np.abs(c @ np.linalg.svd(c, full_matrices=False)[2][1]).max())


def arc_fit(Q):
    """Worst misfit of one circle through Q, turning one way and less than a full turn (inf otherwise)."""
    A = np.c_[2 * Q, np.ones(len(Q))]
    sol = np.linalg.lstsq(A, (Q * Q).sum(1), rcond=None)[0]
    c = sol[:2]
    r2 = sol[2] + c @ c
    if r2 <= 0:
        return np.inf
    r = np.sqrt(r2)
    a = np.unwrap(np.arctan2(Q[:, 1] - c[1], Q[:, 0] - c[0]))
    da = np.diff(a)
    if abs(a[-1] - a[0]) >= 2 * np.pi or not ((da >= -1e-9).all() or (da <= 1e-9).all()):
        return np.inf
    return float(np.abs(np.linalg.norm(Q - c, axis=1) - r).max())


def stated_pieces(P, fit, least, tau):
    """The longest stretches one line (or circle) passes within tau that are STATED: the fit is quiet (its worst
    misfit under QUIET * tau, the level of the mesh's noise) and each end is a jump (with the next point added the
    misfit is over JUMP * tau) or the outline's end. Measured on 58 parts (arc_jump.py): on designers' arcs the
    longest arc's own misfit is 0.05 of the limit at the median and the next point's 458; on designers' free curves
    0.81 and 1.24. A piece that fills its tolerance and is left gradually only approximates a curve."""
    n = len(P)
    out = []
    i = 0
    while i + least <= n:
        if fit(P[i:i + least]) > tau:
            i += 1
            continue
        j = i + least - 1
        while j + 1 < n and fit(P[i:j + 2]) <= tau:
            j += 1
        quiet = fit(P[i:j + 1]) <= QUIET * tau
        right = j + 1 >= n or fit(P[i:j + 2]) >= JUMP * tau
        left = i == 0 or fit(P[i - 1:j + 1]) >= JUMP * tau
        if quiet and left and right:
            out.append((i, j))
        i = j
    return out


def overlay(P, kind, lim):
    """The chords' kinds (0 curve, 1 arc, 2 line) with every stated arc and line laid over them."""
    k = np.asarray(kind).copy()
    P = np.asarray(P, dtype=float)
    n_arc = n_line = 0
    for i0, j0 in stated_pieces(P, arc_fit, 5, lim):
        n_arc += int((k[i0:j0] != 1).sum())
        k[i0:j0] = 1
    for i0, j0 in stated_pieces(P, line_fit, 3, lim):
        n_line += int((k[i0:j0] != 2).sum())
        k[i0:j0] = 2
    return k, n_arc, n_line
