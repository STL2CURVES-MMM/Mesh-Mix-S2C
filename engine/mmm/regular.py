"""Surfaces that agree with each other, as a designer's do. Written for the merged engine, 2026-11-02.

Each surface is fitted to its own triangles, so two walls drawn parallel differ by a few thousandths
of a degree, a fillet drawn tangent to a plane stands a few microns off it, and the face beside it
has no line where the two exactly meet. Reading such a file, a CAD program takes the difference as a
gap to be tolerated, and most edits then fail: on a designer's own file, planes tilted by 0.01 degrees
cost a quarter of the face moves and fillets Fusion could do (see CLAUDE_context.md, "edits").

So, once the regions are settled and before faces are built, directions that agree to within the
fit's own noise become one direction (the coordinate axes when they're that close), surfaces that
agree in place become one place, and a cylinder, torus or cone that nearly touches its neighbour
smoothly is moved to touch it exactly. (Constraint recovery after fitting, as in GlobFit, Li et al.
2011, here only for relations found between neighbours and only within small limits.) A surface whose
own triangles would no longer fit after the change keeps its fit."""
# Modified 2026-11-14, night (design intent; only with DESIGN_TIERS=1): _touch_cylinder holds a radius that lies on the design grid and moves only the axis to touch the neighbours (patch 87; docs/design-intent/probes/patch_touchkeeps87.py).
# Modified 2026-11-15 (design intent; with DESIGN_DIR_CONSENSUS=1): a group's direction is the one the most member area still fits, not the area-weighted mean (_directions).
# Modified 2026-11-12 (design intent; with DESIGN_TIERS=1): a cone is settled in one move, its neighbour's axis, a whole-degree angle and its apex to suit, when its corners allow (_settle_cone).
import copy

import numpy as np

SAME_DEG = 0.1          # degrees: directions this close are one direction
AXIS_DEG = 0.05         # degrees: a direction this close to a coordinate axis is that axis
SAME_PLACE = 0.05       # tolerances: planes, axes and radii this close are the same
TOUCH = 0.3             # tolerances: a surface this far from touching its neighbour smoothly touches it
TOUCH_DEG = 1.0         # degrees: a cone's angle may change this much to touch a plane smoothly
RADIUS_COST = 0.1       # how much a radius gives way against an axis's position, when both must change to touch
KEEP_FIT = 1.5          # tolerances: its own triangles' corners stay this close to a surface it changes
DIR_CANDIDATES = 8      # members (largest first) whose own direction is tried as their group's (DESIGN_DIR_CONSENSUS)


def _on_faces(seg, reg):
    return seg.V[np.unique(seg.F[np.asarray(reg.faces, dtype=np.int64)])]


def _plain(reg):
    return reg.kind in ("plane", "cylinder", "cone", "torus", "sphere") and hasattr(reg.prim, "distance") \
        and not hasattr(reg.prim, "geom") and len(reg.faces) > 0


def _direction(prim):
    return prim.n if prim.kind == "plane" else getattr(prim, "d", None)


def _still_fits(seg, reg, prim, lim):
    P = _on_faces(seg, reg)
    # measurement switch, exact-joins step (2026-11-11), not for merging: a surface moves only as far as its own
    # corners can't tell (NOISE_K times how far they stand off it now, NOISE_FLOOR at least), not up to the tolerance
    import os
    if os.environ.get("DESIGN_NOISE_VETO") == "1":
        own = float(np.abs(reg.prim.distance(P)).max())
        lim = min(lim, max(NOISE_K * own, NOISE_FLOOR))
    return float(np.abs(prim.distance(P)).max()) <= lim


NOISE_K = 3.0           # times its corners' present worst distance a changed surface may stand off them
NOISE_FLOOR = 2e-5      # mm: ... and this much at least (a float32 file's rounding at 100 mm)


def _with_axis(prim, d, pivot):
    """A copy of a curved surface turned to the direction d about the point of its axis level with pivot."""
    p = copy.copy(prim)
    d = d / np.linalg.norm(d)
    if float(d @ prim.d) < 0:
        d = -d
    if prim.kind == "cylinder":
        h = float((pivot - prim.a) @ prim.d)
        on = prim.a + h * prim.d
        p.a = on - float(on @ d) * d
        p.d = d
    elif prim.kind == "cone":
        h = float((pivot - prim.apex) @ prim.d)
        on = prim.apex + h * prim.d
        p.apex = on - h * d
        p.d = d
    elif prim.kind == "torus":
        p.d = d
    _note_axis(p)
    return p


def _note_axis(p):
    """A surface on a known axis refits on it: the axis it remembers moves with it."""
    if hasattr(p, "kd") and hasattr(p, "ka"):
        if p.kind == "cylinder":
            a = p.a
        elif p.kind == "cone":
            a = p.apex
        else:
            a = p.c
        p.ka, p.kd = a - float(a @ p.d) * p.d, p.d


def _set(seg, reg, prim, lim):
    if _still_fits(seg, reg, prim, lim):
        if getattr(reg.prim, "exact", None) is not None and prim is not reg.prim:
            prim.exact = reg.prim.exact      # measurement switch, two tiers: tier 1 stays tier 1 when tidied
        reg.prim = prim
        return True
    return False


def _directions(seg, regs, lim):
    """Directions that agree become one, weighted by area, biggest first."""
    items = []
    for reg in regs:
        d = _direction(reg.prim)
        if d is not None:
            items.append((float(seg.fa[np.asarray(reg.faces, dtype=np.int64)].sum()), reg, np.asarray(d, float)))
    items.sort(key=lambda t: -t[0])
    cos_same = np.cos(np.radians(SAME_DEG))
    groups = []
    for area, reg, d in items:
        for g in groups:
            if abs(float(d @ g["d"])) >= cos_same:
                s = 1.0 if float(d @ g["d"]) > 0 else -1.0
                g["sum"] = g["sum"] + area * s * d
                g["members"].append(reg)
                break
        else:
            groups.append({"d": d, "sum": area * d, "members": [reg]})
    sin_axis = np.sin(np.radians(AXIS_DEG))

    def snapped(c):
        c = c / np.linalg.norm(c)
        # (a wall drawn square to the floor has nothing along the floor's normal, whichever way it faces)
        c = np.where(np.abs(c) < sin_axis, 0.0, c)
        return c / np.linalg.norm(c)

    def turned(reg, c, mid):
        if reg.kind == "plane":
            p = copy.copy(reg.prim)
            p.n = c if float(c @ reg.prim.n) > 0 else -c
            p.origin = mid
            return p
        return _with_axis(reg.prim, c, mid)

    import os as _os_dir
    consensus = _os_dir.environ.get("DESIGN_DIR_CONSENSUS") == "1"
    area_of = {id(reg): area for area, reg, _ in items}
    for g in groups:
        c = snapped(g["sum"])
        members = g["members"]
        mids = [_on_faces(seg, reg).mean(0) for reg in members]
        if consensus and len(members) > 1:
            # (the direction the members' corners agree on: the candidate the most area still fits; see
            # probes/patch_dirconsensus89.py for the trace on part 001)
            ref = g["d"]
            cands = [c]
            for reg in members[:DIR_CANDIDATES]:
                d = np.asarray(_direction(reg.prim), float)
                d = d if float(d @ ref) > 0 else -d
                cands.append(snapped(d))
            best = None
            for k, cand in enumerate(cands):
                if any(float(abs(cand @ o)) > 1.0 - 1e-15 for o in cands[:k]):
                    continue
                score = sum(area_of[id(reg)] for reg, mid in zip(members, mids)
                            if _still_fits(seg, reg, turned(reg, cand, mid), lim))
                if best is None or score > best[0]:
                    best = (score, cand)
            c = best[1]
        for reg, mid in zip(members, mids):
            _set(seg, reg, turned(reg, c, mid), lim)


def _places(seg, regs, tol, lim):
    """Planes on one level and axes through one place become one level and one place; coaxial
    cylinders of one radius, one radius."""
    same = SAME_PLACE * tol
    planes = [r for r in regs if r.kind == "plane"]
    planes.sort(key=lambda r: -float(seg.fa[np.asarray(r.faces, dtype=np.int64)].sum()))
    used = set()
    for i, a in enumerate(planes):
        if i in used:
            continue
        for j in range(i + 1, len(planes)):
            b = planes[j]
            if j in used or abs(float(a.prim.n @ b.prim.n)) < 1.0 - 1e-12:
                continue
            if abs(float((b.prim.origin - a.prim.origin) @ a.prim.n)) <= same:
                p = copy.copy(b.prim)
                p.origin = b.prim.origin + float((a.prim.origin - b.prim.origin) @ a.prim.n) * a.prim.n
                if _set(seg, b, p, lim):
                    used.add(j)
    curved = [r for r in regs if r.kind in ("cylinder", "cone", "torus")]
    curved.sort(key=lambda r: -float(seg.fa[np.asarray(r.faces, dtype=np.int64)].sum()))
    for i, a in enumerate(curved):
        pa = _axis_point(a.prim)
        for b in curved[i + 1:]:
            if abs(float(a.prim.d @ b.prim.d)) < 1.0 - 1e-12:
                continue
            pb = _axis_point(b.prim)
            off = pa - pb
            off = off - float(off @ a.prim.d) * a.prim.d
            if float(np.linalg.norm(off)) > same:
                continue
            p = copy.copy(b.prim)
            _shift(p, off)
            if a.kind == "cylinder" and b.kind == "cylinder" and abs(a.prim.r - p.r) <= same:
                p.r = a.prim.r
            _set(seg, b, p, lim)


def _axis_point(p):
    return p.a if p.kind == "cylinder" else p.apex if p.kind == "cone" else p.c


def _shift(p, by):
    if p.kind == "cylinder":
        p.a = p.a + by
    elif p.kind == "cone":
        p.apex = p.apex + by
    else:
        p.c = p.c + by
    _note_axis(p)


def _next_to(seg, labels, regions, r):
    f = np.asarray(regions[r].faces, dtype=np.int64)
    nb = seg.nb[f].reshape(-1)
    nb = labels[nb[nb >= 0]]
    return [int(q) for q in np.unique(nb) if q != r and q >= 0]


def _touch_cylinder(seg, labels, regions, r, done, tol, lim):
    """A cylinder touching its neighbours smoothly: planes along its axis, and cylinders on axes
    parallel to it that have been settled already."""
    reg = regions[r]
    p = reg.prim
    near = TOUCH * tol
    # (the axis of a narrow fillet is the least sure thing about it: the planes it touches say where it runs)
    sin_turn = np.sin(np.radians(TOUCH_DEG))
    touched = []
    for q in _next_to(seg, labels, regions, r):
        o = regions[q]
        if o.kind == "plane" and abs(float(o.prim.n @ p.d)) <= sin_turn:
            if abs(abs(float((p.a - o.prim.origin) @ o.prim.n)) - p.r) <= near:
                touched.append(o.prim.n)
    if touched:
        d = p.d
        for i, a in enumerate(touched):
            for b in touched[i + 1:]:
                c = np.cross(a, b)
                L = float(np.linalg.norm(c))
                if L > np.sin(np.radians(10.0)):
                    d = c / L
                    break
            else:
                continue
            break
        else:
            d = p.d - float(p.d @ touched[0]) * touched[0]
        turned = _with_axis(p, d, _on_faces(seg, reg).mean(0))
        if _still_fits(seg, reg, turned, lim):
            p = turned
    rows, rhs = [], []
    for q in _next_to(seg, labels, regions, r):
        o = regions[q]
        if o.kind == "plane":
            if abs(float(o.prim.n @ p.d)) > 1e-9:
                continue
            s = float((p.a - o.prim.origin) @ o.prim.n)
            sg = 1.0 if s > 0 else -1.0
            if abs(abs(s) - p.r) <= near:
                # n . (a + x - o) = sg (r + dr)
                rows.append(np.r_[o.prim.n, -sg * RADIUS_COST])
                rhs.append(sg * p.r - s)
        elif o.kind == "cylinder" and q in done:
            if abs(float(o.prim.d @ p.d)) < 1.0 - 1e-12:
                continue
            c = p.a - o.prim.a
            c = c - float(c @ p.d) * p.d
            dist = float(np.linalg.norm(c))
            if dist <= 1e-9:
                continue
            u = c / dist
            for want, sg in ((o.prim.r + p.r, 1.0), (abs(o.prim.r - p.r), 1.0 if p.r > o.prim.r else -1.0)):
                if abs(dist - want) <= near:
                    if want == o.prim.r + p.r:
                        rows.append(np.r_[u, -RADIUS_COST])
                    else:
                        rows.append(np.r_[u, -sg * RADIUS_COST])
                    rhs.append(want - dist)
                    break
    if not rows:
        return _set(seg, reg, p, lim) if p is not reg.prim else False
    rows_ = np.array(rows)
    import os as _os
    grid_ = float(_os.environ.get("DESIGN_ROUND_GRID", "0.001"))
    held_ = (_os.environ.get("DESIGN_TIERS") == "1" and _os.environ.get("DESIGN_TOUCH_KEEPS", "1") == "1"
             and abs(p.r - round(p.r / grid_) * grid_) <= 1e-12)
    if held_:
        # (a radius on the design grid is the designer's: the cylinder is moved to touch, at that radius)
        rows_[:, 3] = 0.0
    x = np.linalg.lstsq(rows_, np.array(rhs), rcond=None)[0]
    n = copy.copy(p)
    n.a = p.a + x[:3] - float(x[:3] @ p.d) * p.d
    n.r = float(p.r) if held_ else float(p.r + x[3] * RADIUS_COST)
    if n.r <= 0:
        return False
    _note_axis(n)
    return _set(seg, reg, n, lim)


def _touch_torus(seg, labels, regions, r, tol, lim):
    """A torus round an axis: a fillet at the foot of a boss touches the floor at one height and the
    boss at one radius."""
    reg = regions[r]
    p = copy.copy(reg.prim)
    near = TOUCH * tol
    changed = False
    for q in _next_to(seg, labels, regions, r):
        o = regions[q]
        if o.kind == "plane" and abs(abs(float(o.prim.n @ p.d)) - 1.0) <= 1e-12:
            s = float((p.c - o.prim.origin) @ o.prim.n)
            if abs(abs(s) - p.r) <= near:
                p.c = p.c + (np.sign(s) * p.r - s) * o.prim.n
                changed = True
        elif o.kind == "cylinder" and abs(float(o.prim.d @ p.d)) >= 1.0 - 1e-12:
            off = p.c - o.prim.a
            off = off - float(off @ p.d) * p.d
            if float(np.linalg.norm(off)) > near:
                continue
            for want in (o.prim.r + p.r, o.prim.r - p.r):
                if abs(p.R - want) <= near:
                    p.c = p.c - off
                    p.R = float(want)
                    changed = True
                    break
    if changed:
        _note_axis(p)
        return _set(seg, reg, p, lim)
    return False


def _touch_cone(seg, labels, regions, r, tol, lim):
    """A cone beside a flat chamfer running on from it touches it along one line: its angle is the
    plane's angle to the axis, and its apex lies on the plane."""
    reg = regions[r]
    prim = reg.prim
    near = TOUCH * tol
    for q in _next_to(seg, labels, regions, r):
        o = regions[q]
        if o.kind != "plane":
            continue
        c = float(o.prim.n @ prim.d)
        alpha = float(np.arcsin(min(1.0, abs(c))))
        if abs(alpha - prim.alpha) > np.radians(TOUCH_DEG) or abs(c) < 1e-6:
            continue
        s = float((prim.apex - o.prim.origin) @ o.prim.n)
        if abs(s) > near:
            continue
        p = copy.copy(prim)
        p.alpha = alpha
        p.apex = prim.apex - (s / c) * prim.d
        _note_axis(p)
        if _set(seg, reg, p, lim):
            return True
    return False


def _settle_cone(seg, labels, regions, r, tol):
    """Design-intent prototype. A cone in one move: on its own axis, or on a neighbour's that runs beside it, with
    a whole-degree half-angle and its apex solved along the axis; the candidate its corners fit best is taken if
    every corner lies on it within the mesh's stated accuracy. A chamfer's two rings of corners fix neither its
    angle nor its axis to better than a few thousandths of a degree, and designers' cones are whole degrees on
    the hole's own axis (211 of 228 in the reference sample)."""
    import os
    lim = getattr(seg, "exact_lim", None)
    if lim is None or os.environ.get("DESIGN_TIERS") != "1" or os.environ.get("DESIGN_ROUND_CONE", "1") != "1":
        return False
    reg = regions[r]
    prim = reg.prim
    whole = float(np.radians(round(float(np.degrees(prim.alpha)))))
    if not (0.0 < whole < np.pi / 2):
        return False
    P = _on_faces(seg, reg)
    axes = [(prim.apex, prim.d)]
    cos_same = np.cos(np.radians(SAME_DEG))
    for q in _next_to(seg, labels, regions, r):
        o = regions[q]
        if not _plain(o) or o.kind not in ("cylinder", "cone", "torus"):
            continue
        od = np.asarray(o.prim.d, float)
        c = float(od @ prim.d)
        if abs(c) < cos_same:
            continue
        oa = np.asarray(_axis_point(o.prim), float)
        off = (prim.apex - oa) - float((prim.apex - oa) @ od) * od
        if float(np.linalg.norm(off)) <= SAME_PLACE * tol:
            axes.append((oa, od if c > 0 else -od))
    best = None
    for a0, d0 in axes:
        rel = P - a0
        h = rel @ d0
        rho = np.linalg.norm(rel - np.outer(h, d0), axis=1)
        p = copy.copy(prim)
        p.d = d0
        p.alpha = whole
        p.apex = a0 + float(np.mean(h - rho / np.tan(whole))) * d0
        worst = float(np.abs(p.distance(P)).max())
        if best is None or worst < best[0]:
            best = (worst, p)
    if best is None or best[0] > lim:
        return False
    p = best[1]
    if abs(p.alpha - prim.alpha) < 1e-12 and float(np.abs(p.d - prim.d).max()) < 1e-15 and float(np.abs(p.apex - prim.apex).max()) < 1e-12:
        return False
    _note_axis(p)
    if getattr(prim, "exact", None) is not None:
        p.exact = prim.exact
    reg.prim = p
    return True


def regularise(seg, labels, regions):
    """Settles the regions' simple surfaces with each other (see above). Regions' prims are replaced,
    never altered in place, so a surface shared with another pass stays as it was."""
    tol = seg.tol
    lim = KEEP_FIT * tol
    regs = [r for r in regions if _plain(r) and not getattr(r, "facet", False)]
    if not regs:
        return
    _directions(seg, regs, lim)
    _places(seg, regs, tol, lim)
    area = np.array([float(seg.fa[np.asarray(r.faces, dtype=np.int64)].sum()) if len(r.faces) else 0.0 for r in regions])
    done = set()
    for r in np.argsort(-area, kind="stable"):
        r = int(r)
        reg = regions[r]
        if not _plain(reg) or getattr(reg, "facet", False):
            continue
        if reg.kind == "cylinder":
            _touch_cylinder(seg, labels, regions, r, done, tol, lim)
            done.add(r)
    for r in np.argsort(-area, kind="stable"):
        r = int(r)
        reg = regions[r]
        if _plain(reg) and not getattr(reg, "facet", False):
            if reg.kind == "torus":
                _touch_torus(seg, labels, regions, r, tol, lim)
            elif reg.kind == "cone":
                _touch_cone(seg, labels, regions, r, tol, lim)
                _settle_cone(seg, labels, regions, r, tol)


def contact_rows(prims, x):
    """For surfaces touching smoothly, which leave a point free to slide across their line of contact:
    rows (unit vector, amount) that hold it on that line. [] when none touch exactly."""
    out = []
    for i, a in enumerate(prims):
        for b in prims[i + 1:]:
            line = contact(a, b)
            if line is None:
                continue
            if line[0] == "line":
                _, q, d = line
                rel = q - x
                perp = rel - float(rel @ d) * d
                L = float(np.linalg.norm(perp))
                # (across the contact, whichever way that is: towards the line, and the surfaces
                # themselves hold the other direction)
                n = line_normal(a, b)
                t = np.cross(d, n)
                t = t / np.linalg.norm(t)
                out.append((t, float(t @ rel)))
            else:
                _, c, d, r = line
                rel = x - c
                h = float(rel @ d)
                rad = rel - h * d
                rho = float(np.linalg.norm(rad))
                if rho > 1e-12:
                    out.append((rad / rho, r - rho))
                out.append((d, -h))
    return out


TOUCHING = 1e-7         # mm: surfaces this close to touching along a line are touching there


def line_normal(a, b):
    if a.kind == "plane":
        return a.n
    if b.kind == "plane":
        return b.n
    c = a.a - b.a
    c = c - float(c @ a.d) * a.d
    return c / np.linalg.norm(c)


def contact(a, b):
    """Where two surfaces touch smoothly: ("line", point, direction), ("circle", centre, axis, radius),
    or None."""
    if a.kind != "plane" and b.kind == "plane":
        a, b = b, a
    if a.kind == "plane" and b.kind == "cylinder":
        if abs(float(a.n @ b.d)) > 1e-9:
            return None
        s = float((b.a - a.origin) @ a.n)
        if abs(abs(s) - b.r) > TOUCHING:
            return None
        return "line", b.a - s * a.n, b.d
    if a.kind == "plane" and b.kind == "cone":
        c = float(a.n @ b.d)
        if abs(abs(c) - np.sin(b.alpha)) > 1e-9 or abs(float((b.apex - a.origin) @ a.n)) > TOUCHING:
            return None
        g = np.cross(a.n, np.cross(b.d, a.n))
        L = float(np.linalg.norm(g))
        if L < 1e-9:
            return None
        return "line", b.apex, g / L
    if a.kind == "plane" and b.kind == "torus":
        if abs(abs(float(a.n @ b.d)) - 1.0) > 1e-12:
            return None
        s = float((b.c - a.origin) @ a.n)
        if abs(abs(s) - b.r) > TOUCHING:
            return None
        return "circle", b.c - s * a.n, b.d, b.R
    if a.kind == "cylinder" and b.kind == "cylinder":
        if abs(float(a.d @ b.d)) < 1.0 - 1e-12:
            return None
        c = b.a - a.a
        c = c - float(c @ a.d) * a.d
        dist = float(np.linalg.norm(c))
        if dist < 1e-9:
            return None
        u = c / dist
        if abs(dist - (a.r + b.r)) <= TOUCHING:
            return "line", a.a + a.r * u, a.d
        if abs(dist - abs(a.r - b.r)) <= TOUCHING:
            sg = 1.0 if a.r > b.r else -1.0
            return "line", a.a + sg * a.r * u, a.d
        return None
    if {a.kind, b.kind} == {"cylinder", "torus"}:
        cy, to = (a, b) if a.kind == "cylinder" else (b, a)
        if abs(float(cy.d @ to.d)) < 1.0 - 1e-12:
            return None
        off = to.c - cy.a
        off = off - float(off @ cy.d) * cy.d
        if float(np.linalg.norm(off)) > TOUCHING:
            return None
        if abs(to.R + to.r - cy.r) <= TOUCHING or abs(to.R - to.r - cy.r) <= TOUCHING:
            return "circle", to.c, to.d, cy.r
        return None
    return None
