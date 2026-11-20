# New 2026-11-15 for Mesh-Mix-S2C's merged engine (AGPL-3.0), design intent.
"""The fault file: what failed during one conversion, why, and what was done about it.

The owner (2026-11-15): good work must not be thrown away for a few stray faces; when a face fails the program must
say so plainly, and leave a data file with the exact details needed to find and fix the cause, so that every failure
is something learned instead of a lucky trace later. This module gathers those details while a conversion runs and
writes them next to the saved file as NAME.faults.json, whenever anything failed:

- every surface whose face failed in a build round, and at which check: no face could be built (with the builder's
  reason), the face is invalid, it crosses itself, it is broken once saved and read back, or it strays from its
  triangles; then what the build did with it (fitted again as freeform cells, made flat faces of its own triangles,
  given to its neighbours, or sent back to the pieces it was pooled from) and what its triangles finally became;
- for each such face, the causes as measured: its area against its triangles' (inside out, none, or wrapped the
  wrong way round its surface), where its outline crosses itself (the edges and the 3D point), each edge's curve
  on the face against the edge itself at STRAY_POINTS points (BRepCheck looks at 23 and misses spikes between
  them), which edges fail on their own, and for a face that strays, how many of its points stand how far off, and
  where the worst ones are;
- the surface (kind and numbers), its triangles (ids and corner coordinates) and the ring of triangles round them,
  and the face itself as BREP text, so a fault can be traced from this file alone;
- each method tried for each part, whether it was valid and followed the mesh, which was kept and why;
- the faces still broken in the saved file as read back, named one by one.

The file holds coordinates of the mesh near each failure (part geometry): say so before anyone is asked to share it.
Nothing here changes a result: it only reads. Every entry point catches its own errors (faults.caught), so a fault
in the record can never cost a conversion. One conversion at a time per process (the pipeline converts its parts
one after another in the main process)."""
import hashlib
import json
import os
import tempfile
import time

import numpy as np

from . import faults

FORMAT = "mms2c-faults/1"
TRI_IDS_MAX = 5000      # triangle ids kept per failing region (its whole set is used for the outcome)
TRI_XYZ_MAX = 600       # triangles of a failing region kept with their corner coordinates (and as many of its ring)
BREP_MAX = 400_000      # characters of a failing face's BREP text kept
DETAIL_MAX = 400        # failing regions measured in full per conversion; past that, recorded without measures
FILE_MAX = 60_000_000   # characters of the whole file; past that, geometry (coordinates, BREP) is left out
STRAY_POINTS = 101      # points along an edge where its curve on the face is checked against the edge
CHECK_POINTS = 23       # the points BRepCheck itself looks at
CROSS_SEGMENTS = 6000   # pieces of outline, at most, searched for crossings per face
WORST_POINTS = 20       # worst points kept for a face that strays from its triangles
SLIVER_AREA = 1e-4      # mm2: a face this small is a sliver (0.01 x 0.01 mm), whatever else is wrong with it
TRUSTED_FLAGS = ("self_crossing", "small", "not_closed", "not_connected", "lacking", "degenerate")
# (ShapeAnalysis_Wire's order and gaps flags read True on a plain box face: not trusted, so not recorded)

STAGE_WORDS = {
    "failed": "no face could be built",
    "invalid": "the face is invalid",
    "crossing": "its outline crosses itself",
    "broken_saved": "it is broken once saved and read back",
    "off": "it strays from its triangles",
}

_S = None   # the conversion being recorded


def _safe(fn):
    """Run one entry point; a fault in the record is caught and recorded, never passed on."""
    def run(*a, **k):
        if _S is None and fn.__name__ != "begin":
            return None
        try:
            return fn(*a, **k)
        except Exception as e:
            faults.caught(e)
            try:
                _S["record_errors"].append(f"{fn.__name__}: {type(e).__name__}: {e}")
            except Exception as e2:
                faults.caught(e2)
            return None
    run.__name__ = fn.__name__
    run.__doc__ = fn.__doc__
    return run


@_safe
def begin(input_path, output_path, opts=None):
    """A conversion starts: forget the last one's record."""
    global _S
    from mms2c import __version__

    try:
        with open(input_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
        size = os.path.getsize(input_path)
    except OSError:
        digest, size = None, None
    _S = {
        "format": FORMAT,
        "made": time.strftime("%Y-%m-%d %H:%M:%S"),
        "engine": {"version": __version__,
                   "switches": {k: v for k, v in sorted(os.environ.items())
                                if k.startswith(("DESIGN_", "MM_S2C_", "MMM_", "MM_S2C_"))}},
        "input": {"file": os.path.basename(input_path), "bytes": size, "sha256": digest},
        "output": os.path.basename(output_path),
        "settings": {k: v for k, v in (getattr(opts, "__dict__", {}) or {}).items()
                     if isinstance(v, (int, float, str, bool, type(None)))},
        "contains": "coordinates of the mesh's triangles near each failure, and the failing faces themselves",
        "parts": [],
        "saved_files": [],
        "record_errors": [],
        "_detail_left": DETAIL_MAX,
        "_size": 0,
    }


def _part():
    if not _S["parts"]:
        _S["parts"].append({"part": 0, "name": None, "attempts": [], "chosen": None})
    return _S["parts"][-1]


def _attempt():
    p = _part()
    if not p["attempts"]:
        p["attempts"].append({"method": None, "builds": []})
    return p["attempts"][-1]


def _build():
    a = _attempt()
    if not a["builds"]:
        a["builds"].append({"shell": 0, "events": [], "actions": [], "_open": {}})
    return a["builds"][-1]


@_safe
def part(index, name=None, preset=None, tol=None):
    """The pipeline starts on one part (one body) of the file."""
    _S["parts"].append({"part": int(index), "name": name, "preset": preset, "tolerance": tol, "attempts": [],
                        "chosen": None})


@_safe
def attempt_begin(method, how=None):
    """One method is tried on the current part."""
    _part()["attempts"].append({"method": method, "how": how, "builds": []})


@_safe
def attempt_end(candidate=None, error=None):
    """What the method gave: valid or not, whether it follows the mesh, and how many of its faces are invalid."""
    a = _attempt()
    if error is not None:
        a["error"] = f"{type(error).__name__}: {error}"
        return
    if candidate is None:
        a["result"] = None
        return
    from OCP.BRepCheck import BRepCheck_Analyzer

    bad = sum(1 for f in _faces(candidate.shape) if not BRepCheck_Analyzer(f).IsValid())
    v = candidate.verdict
    a["result"] = {"valid": bool(candidate.valid), "invalid_faces": bad,
                   "follows_mesh": None if v is None else bool(v.ok),
                   "fit": None if v is None else v.summary("mm"),
                   "faces": len(_faces(candidate.shape))}


@_safe
def chosen(method, why=None):
    """Which method's result was kept for the current part, and why."""
    p = _part()
    p["chosen"] = method
    if why:
        p["why"] = why


@_safe
def note(text):
    """Something done about a failure that has no place of its own (a part converted again, and so on)."""
    _part().setdefault("notes", []).append(text)


@_safe
def build_begin(shell_index, n_triangles, tol):
    """One shell's CAD build starts (build_cad_shell): its rounds follow."""
    _attempt()["builds"].append({"shell": int(shell_index), "triangles": int(n_triangles), "tolerance": float(tol),
                                 "events": [], "actions": [], "_open": {}, "_phase": "round 0",
                                 "phases": [["round 0", 0.0]], "_t0": time.time()})


@_safe
def phase(text):
    """Where in the build the next failures happen: 'round N', 'trial: ...', 'end: ...'. Each is timed (seconds since
    the build began), so a round that only repeats the one before shows what it cost (patch 107)."""
    b = _build()
    b["_phase"] = text
    if "_t0" in b:
        b.setdefault("phases", []).append([text, round(time.time() - b["_t0"], 2)])


_pending_off = {}


@_safe
def off_points(region, direction, n_points, far_limit, near_limit, n_far, n_near, worst_xyz, worst_d, rule):
    """Called by the off-mesh check for each region it flags: how many points, how far, where the worst are."""
    _pending_off.setdefault(int(region), []).append({
        "direction": direction, "points": int(n_points), "far_limit": float(far_limit), "near_limit": float(near_limit),
        "far": int(n_far), "near": int(n_near), "rule": rule,
        "worst": [[round(float(x), 5) for x in p] + [round(float(d), 5)] for p, d in zip(worst_xyz, worst_d)]})


@_safe
def off_region(r, direction, reg_of, d, pts, cnt, n_far, n_near, far, near, rule):
    """The off-mesh check flagged region ``r``: its counts, and its WORST_POINTS farthest points with distances."""
    r = int(r)
    ix = np.nonzero(reg_of == r)[0]
    worst = ix[np.argsort(-d[ix])[:WORST_POINTS]] if len(ix) else ix
    off_points(r, direction, cnt[r] if r < len(cnt) else 0, far, near, n_far[r] if r < len(n_far) else 0,
               n_near[r] if r < len(n_near) else 0, pts[worst] if pts is not None else [], d[worst], rule)


@_safe
def failures(stage, asm, regions, ids, seg, tol, reasons=None):
    """Regions ``ids`` failed ``stage`` in the build's current phase; the same region at several stages of one phase
    is one event."""
    if not ids:
        return
    b = _build()
    ph = b.get("_phase", "")
    rf = getattr(asm, "region_faces", {})
    reasons = dict(reasons or {})
    for r in sorted(int(x) for x in ids):
        key = (ph, r)
        ev = b["_open"].get(key)
        if ev is None:
            ev = {"phase": ph, "region": r, "stages": []}
            ev.update(_region_info(regions, r, seg))
            ev["_tris"] = np.asarray(regions[r].faces, dtype=np.int64) if r < len(regions) else np.zeros(0, np.int64)
            ev["_prim"] = regions[r].prim if r < len(regions) else None
            b["_open"][key] = ev
            b["events"].append(ev)
        if stage not in ev["stages"]:
            ev["stages"].append(stage)
        if stage == "failed" and r in reasons:
            ev["builder_said"] = str(reasons[r])[:500]
        if stage == "off" and r in _pending_off:
            ev["off"] = _pending_off.pop(r)
        face = rf.get(r)
        if face is not None and "face" not in ev:
            if _S["_detail_left"] > 0:
                _S["_detail_left"] -= 1
                ev["face"] = _face_detail(face, asm, regions, r, float(tol), ev.get("mesh_area"))
                ev["face"]["brep"] = _brep_text(face)
            else:
                ev["face"] = {"measured": False, "why": f"over {DETAIL_MAX} failing faces in this conversion"}
        ev["why"] = _why(ev)
    _pending_off.clear()


def _why(ev):
    """The event in plain words: what flagged it, and what was measured."""
    out = []
    face = ev.get("face") or {}
    found = face.get("findings") or []
    for st in ev["stages"]:
        if st == "failed":
            out.append("no face could be built" + (f" ({ev['builder_said']})" if ev.get("builder_said") else ""))
        elif st == "off":
            for o in ev.get("off", []):
                w = o["worst"][0] if o["worst"] else None
                out.append(f"it strays from its triangles: of {o['points']} {o['direction']}, {o['far']} stand more than "
                           f"{o['far_limit']:.3g} mm off and {o['near']} more than {o['near_limit']:.3g}"
                           + (f" (worst {w[3]:.3g} mm, at {w[:3]})" if w else "") + f"; flagged by {o['rule']}")
        else:
            out.append(STAGE_WORDS.get(st, st) + (": " + "; ".join(found) if found and not face.get("valid") else ""))
    if found and face.get("valid"):
        out.append("also measured on its face, which is valid: " + "; ".join(found))
    if face.get("measured") and not face.get("valid") and not found:
        out.append("its face fails OpenCascade's check for a reason not measured here")
    return out


@_safe
def action(what, ids, regions=None, detail=None):
    """What the build did with failing regions: 'refit as freeform cells', 'made flat faces of its own triangles',
    'given to neighbouring surfaces', 'sent back to the pieces it was pooled from', 'spared: its small neighbours
    are rebuilt instead'."""
    if not ids:
        return
    b = _build()
    rec = {"phase": b.get("_phase", ""), "what": what, "regions": sorted(int(r) for r in ids)}
    if regions is not None:
        rec["kinds"] = [getattr(regions[r], "kind", None) if r < len(regions) else None for r in rec["regions"]]
    if detail:
        rec["detail"] = detail
    b["actions"].append(rec)


@_safe
def build_end(labels, regions, asm=None):
    """The build is done: for each failing region, what its triangles became (which surfaces, how many faces), and
    whether its own surface survived on most of them."""
    b = _build()
    labels = np.asarray(labels)
    for ev in b["events"]:
        t = ev.pop("_tris", None)
        prim = ev.pop("_prim", None)
        if t is None or not len(t):
            continue
        lab = labels[t]
        out, kept = {}, 0
        for r in np.unique(lab):
            n = int((lab == r).sum())
            if r < 0 or r >= len(regions):
                k = "left out"
            else:
                g = regions[r]
                k = "flat facets" if getattr(g.prim, "facet", False) else str(getattr(g, "kind", "?"))
                if prim is not None and g.prim is prim:
                    kept += n
            o = out.setdefault(k, {"faces": 0, "triangles": 0})
            o["faces"] += 1
            o["triangles"] += n
        ev["became"] = out
        ev["kept_its_surface"] = round(kept / len(t), 4)
    b.pop("_open", None)
    b.pop("_phase", None)
    if "_t0" in b:
        b["seconds"] = round(time.time() - b.pop("_t0"), 2)


@_safe
def saved(check, path):
    """A saved file as read back (after every repair and fallback): its faces that are still broken
    (stepio.verify_step's bad_face_list) and its issues."""
    _S["saved_files"].append({"file": os.path.basename(path), "valid": check.get("valid"), "sound": check.get("sound"),
                              "bad_faces": check.get("bad_faces"), "bad_face_list": check.get("bad_face_list", []),
                              "issues": [{k: v for k, v in i.items() if k in ("code", "level", "message")}
                                         for i in check.get("issues", [])]})


def active():
    """Whether a conversion is being recorded (callers skip costly gathering otherwise)."""
    return _S is not None


@_safe
def save_problems(check, items):
    """The saved file read back found problems, and repair starts (pipeline._save_checked): what was found and, for a
    solid that is invalid while none of its faces is, its shells one by one (faces, volume, box, and whether each is
    valid in the solid's own check), since the fault then lies between the shells, not in a face."""
    rec = {"valid": check.get("valid"), "bad_faces": check.get("bad_faces"), "bad_face_list": check.get("bad_face_list", []),
           "issues": [{k: v for k, v in i.items() if k in ("code", "level", "message")} for i in check.get("issues", [])]}
    if not check.get("bad_faces") and check.get("valid") is False:
        rec["shells"] = _shells(items)
    _S.setdefault("repairs", []).append({"found": rec, "steps": [], "_t0": time.time()})


@_safe
def repair_step(label, outcome, check=None, seconds=None):
    """One repair step (mmm.repair.STEPS) and what it did: 'helped', "didn't help" or 'failed: ...'."""
    if not _S.get("repairs"):
        return
    st = {"step": label, "outcome": outcome, "seconds": None if seconds is None else round(float(seconds), 2)}
    if check is not None:
        st["issues_after"] = [i["code"] for i in check.get("issues", [])]
    _S["repairs"][-1]["steps"].append(st)


@_safe
def repaired(check):
    """Repair is over: what is left, and how long it took."""
    if not _S.get("repairs"):
        return
    last = _S["repairs"][-1]
    last["seconds"] = round(time.time() - last.pop("_t0", time.time()), 2)
    last["left"] = [i["code"] for i in check.get("issues", [])]


def _shells(items):
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    out = []
    for shape, name in items:
        ex = TopExp_Explorer(shape, TopAbs_SOLID)
        while ex.More():
            solid = ex.Current()
            ex.Next()
            an = BRepCheck_Analyzer(solid)
            rec = {"part": name, "solid_valid": bool(an.IsValid()), "shells": []}
            es = TopExp_Explorer(solid, TopAbs_SHELL)
            while es.More():
                sh = es.Current()
                es.Next()
                nf, ef = 0, TopExp_Explorer(sh, TopAbs_FACE)
                while ef.More():
                    nf += 1
                    ef.Next()
                g = GProp_GProps()
                BRepGProp.VolumeProperties_s(sh, g)
                b = Bnd_Box()
                BRepBndLib.Add_s(sh, b)
                lo, hi = b.CornerMin(), b.CornerMax()
                rec["shells"].append({"faces": nf, "volume": round(float(g.Mass()), 6),
                                      "reversed": sh.Orientation() == TopAbs_REVERSED,
                                      "valid_in_solid": bool(an.IsValid(sh)),
                                      "box": [[round(lo.X(), 4), round(lo.Y(), 4), round(lo.Z(), 4)],
                                              [round(hi.X(), 4), round(hi.Y(), 4), round(hi.Z(), 4)]]})
            if len(rec["shells"]) > 1 and not rec["solid_valid"]:
                rec["misclassified"] = _misclassified(solid)
            out.append(rec)
    return out


PAIR_SHELLS = 24        # shells of one solid, at most, whose pairs are tested (_misclassified)
PAIR_POINTS = 24        # distinct corners of a shell, at most, classified against another shell's own solid


def _misclassified(solid):
    """Pairs of a solid's shells where OpenCascade's point classifier puts corners of one inside the other's own solid
    though they lie outside its box, so they can't be: BRepCheck_Solid's nesting test runs on that classifier, and such a
    pair makes a solid invalid that isn't (found by the threads session on board_adapter3: corners of void 5 IN void 3's
    solid, 72.7 mm away: 2 of its 14 corners; sewing rebuilt it after 78 s of repair). Only a shell's distinct corners
    outside the other's box are asked (to PAIR_POINTS; a sample of six missed that pair). Each: the two shells and how
    many of the corners asked misfired."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBndLib import BRepBndLib
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.BRepLib import BRepLib
    from OCP.Bnd import Bnd_Box
    from OCP.TopAbs import TopAbs_IN, TopAbs_ON, TopAbs_SHELL, TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    shells, es = [], TopExp_Explorer(solid, TopAbs_SHELL)
    while es.More() and len(shells) < PAIR_SHELLS:
        shells.append(TopoDS.Shell(es.Current()))
        es.Next()
    pts, boxes = [], []
    for sh in shells:
        p, seen, ev = [], set(), TopExp_Explorer(sh, TopAbs_VERTEX)
        while ev.More() and len(p) < PAIR_POINTS:
            q = BRep_Tool.Pnt_s(TopoDS.Vertex(ev.Current()))
            ev.Next()
            k = (round(q.X(), 9), round(q.Y(), 9), round(q.Z(), 9))
            if k not in seen:
                seen.add(k)
                p.append(q)
        pts.append(p)
        b = Bnd_Box()
        BRepBndLib.Add_s(sh, b)
        lo, hi = b.CornerMin(), b.CornerMax()
        boxes.append(((lo.X(), lo.Y(), lo.Z()), (hi.X(), hi.Y(), hi.Z())))
    own = [None] * len(shells)

    def outside(q, box, pad=1e-6):
        (x0, y0, z0), (x1, y1, z1) = box
        return q.X() < x0 - pad or q.X() > x1 + pad or q.Y() < y0 - pad or q.Y() > y1 + pad or q.Z() < z0 - pad or q.Z() > z1 + pad

    def misfires(i, qs):
        if own[i] is None:
            # (turned to face outward: a void's shell faces in, and a solid of it alone would be its infinite outside)
            own[i] = BRepBuilderAPI_MakeSolid(shells[i]).Solid()
            BRepLib.OrientClosedSolid_s(own[i])
        return sum(BRepClass3d_SolidClassifier(own[i], q, 1e-7).State() in (TopAbs_IN, TopAbs_ON) for q in qs)

    out = []
    for i in range(len(shells)):
        for j in range(len(shells)):
            if i == j:
                continue
            far = [q for q in pts[j] if outside(q, boxes[i])]
            bad = misfires(i, far) if far else 0
            if bad:
                out.append({"shell": i, "corners_of": j, "in_or_on": bad, "of_outside_its_box": len(far)})
    return out


def summary_words():
    """The record in plain words for the user, or None when nothing failed."""
    if _S is None:
        return None
    by_stage, n_ev, kept_whole, refit = {}, 0, 0, 0
    for p in _S["parts"]:
        for a in p["attempts"]:
            for b in a["builds"]:
                for ev in b["events"]:
                    if str(ev.get("phase", "")).startswith("trial"):
                        continue
                    n_ev += 1
                    st = ev["stages"][0] if ev["stages"] else "?"
                    by_stage[st] = by_stage.get(st, 0) + 1
                    kept_whole += ev.get("kept_its_surface", 0) >= 0.99
                    refit += ev.get("kept_its_surface", 1) < 0.99
    broken = sum(int(f.get("bad_faces") or 0) for f in _S.get("saved_files", []))
    fell = [p for p in _S["parts"] if p.get("chosen") and p["attempts"] and p["chosen"] != p["attempts"][0]["method"]]
    reps = _S.get("repairs", [])
    if not n_ev and not broken and not fell and not reps:
        return None
    bits = []
    if n_ev:
        bits.append(f"{n_ev} face(s) failed while the shape was built ("
                    + ", ".join(f"{n}: {STAGE_WORDS.get(s, s)}" for s, n in sorted(by_stage.items(), key=lambda x: -x[1]))
                    + f"); {refit} of them were rebuilt with other surfaces")
    if fell:
        bits.append(f"{len(fell)} part(s) were saved with another method than the first")
    if reps:
        r0 = reps[0]
        what = ("the solid was invalid while every face was valid" if r0["found"].get("shells") is not None
                else ", ".join(i["code"] for i in r0["found"]["issues"]) or "problems")
        tried = sum(len(r["steps"]) for r in reps)
        secs = sum(float(r.get("seconds") or 0.0) for r in reps)
        bits.append(f"the saved file needed repair ({what}; {tried} repair step(s) tried, {secs:.0f} s)")
    if broken:
        bits.append(f"{broken} face(s) are still broken in the saved file")
    return "; ".join(bits) + "."


@_safe
def finish(res, report, output_path):
    """Write the record next to the saved file when anything failed; say so, and name the file in the result."""
    global _S
    words = summary_words()
    if words is None:
        _S = None
        return None
    path = os.path.splitext(output_path)[0] + ".faults.json"
    _S["summary"] = words
    _S["result"] = {k: res.get(k) for k in ("valid", "sound", "faces", "solids", "shells", "preset", "seconds",
                                             "tolerance", "fidelity", "mesh_check", "input")}
    rec = _clean(_S)
    text = json.dumps(rec, default=_plain)
    if len(text) > FILE_MAX:
        _drop_geometry(rec)
        rec["trimmed"] = f"geometry left out: the full record was {len(text):,} characters"
        text = json.dumps(rec, default=_plain)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    res["faults_file"] = path
    res["faults_summary"] = words
    if report is not None:
        report.warn(words + f" The details are in {os.path.basename(path)}.")
    _S = None
    return path


# ---------------------------------------------------------------- measures (read-only)

def _faces(shape):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    out, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        out.append(TopoDS.Face(ex.Current()))
        ex.Next()
    return out


def _prim_params(prim):
    out = {}
    for k in ("r", "R", "d", "a", "c", "n", "origin", "apex", "half_angle", "angle", "sense", "lead", "pitch"):
        v = getattr(prim, k, None)
        if v is None:
            continue
        try:
            out[k] = np.asarray(v, float).round(9).tolist()
        except (TypeError, ValueError):
            out[k] = str(v)
    return out


def _region_info(regions, r, seg):
    if r >= len(regions):
        return {"kind": None}
    g = regions[r]
    prim = getattr(g, "prim", None)
    tri = np.asarray(getattr(g, "faces", []), dtype=np.int64)
    info = {"kind": getattr(g, "kind", None), "surface": type(prim).__name__ if prim is not None else None,
            "numbers": _prim_params(prim) if prim is not None else {},
            "locked": bool(getattr(prim, "locked", False)), "designed": bool(getattr(prim, "designed", False)),
            "facet": bool(getattr(prim, "facet", False)), "pooled": bool(getattr(g, "pooled", False)),
            "triangles": int(len(tri))}
    if seg is None or not len(tri):
        return info
    V, F = seg.V, seg.F
    fa = getattr(seg, "fa", None)
    if fa is not None:
        info["mesh_area"] = round(float(np.asarray(fa)[tri].sum()), 6)
    P = V[F[tri]]
    info["centre"] = np.round(P.reshape(-1, 3).mean(0), 4).tolist()
    info["box"] = [np.round(P.reshape(-1, 3).min(0), 4).tolist(), np.round(P.reshape(-1, 3).max(0), 4).tolist()]
    if _S["_size"] < FILE_MAX:
        info["tri_ids"] = tri[:TRI_IDS_MAX].tolist()
        keep = tri[:TRI_XYZ_MAX]
        info["tri_xyz"] = np.round(V[F[keep]], 6).tolist()
        ring = set()
        nb = getattr(seg, "nb", None)
        if nb is not None:
            mine = set(tri.tolist())
            for f in tri.tolist():
                for g2 in nb[f]:
                    if g2 >= 0 and g2 not in mine:
                        ring.add(int(g2))
            ring = sorted(ring)[:TRI_XYZ_MAX]
            info["ring_ids"] = ring
            info["ring_xyz"] = np.round(V[F[np.asarray(ring, dtype=np.int64)]], 6).tolist() if ring else []
        _S["_size"] += 120 * (len(keep) + len(ring)) + 8 * min(len(tri), TRI_IDS_MAX)
    return info


def _brep_text(face):
    if _S["_size"] >= FILE_MAX:
        return None
    from OCP.BRepTools import BRepTools

    fd, p = tempfile.mkstemp(suffix=".brep", prefix="mms2c-fault-")
    os.close(fd)
    try:
        BRepTools.Write_s(face, p)
        with open(p, encoding="utf-8", errors="replace") as f:
            text = f.read()
    finally:
        try:
            os.remove(p)
        except OSError:
            pass
    if len(text) > BREP_MAX:
        return None
    _S["_size"] += len(text)
    return text


def _face_detail(face, asm, regions, r, tol, mesh_area=None):
    """The measured causes of one failing face (see the module docstring)."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Curve2d, BRepAdaptor_Surface
    from OCP.BRepCheck import BRepCheck_Analyzer
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepTools import BRepTools_WireExplorer
    from OCP.GProp import GProp_GProps
    from OCP.ShapeAnalysis import ShapeAnalysis_Wire
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    S = BRepAdaptor_Surface(face, False)
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    area = float(g.Mass())
    c = g.CentreOfMass()
    out = {"measured": True, "valid": bool(BRepCheck_Analyzer(face).IsValid()), "area": round(area, 6),
           "centre": [round(c.X(), 4), round(c.Y(), 4), round(c.Z(), 4)],
           "surface_type": str(S.GetType()).split(".")[-1].replace("GeomAbs_", "")}
    other = _edge_owner(asm)
    an = BRepCheck_Analyzer(face)
    prec = max(tol * 0.1, 1e-7)
    wires, polys, causes = [], [], []
    wex = TopExp_Explorer(face, TopAbs_WIRE)
    wi = 0
    while wex.More():
        w = TopoDS.Wire(wex.Current())
        wex.Next()
        sw = ShapeAnalysis_Wire(w, face, prec)
        flags = {"self_crossing": bool(sw.CheckSelfIntersection()), "small": bool(sw.CheckSmall(prec)),
                 "not_closed": bool(sw.CheckClosed()), "not_connected": bool(sw.CheckConnected()),
                 "lacking": bool(sw.CheckLacking()), "degenerate": bool(sw.CheckDegenerated())}
        edges = []
        we = BRepTools_WireExplorer(w, face)
        ei = 0
        while we.More():
            e = we.Current()
            we.Next()
            er = {"edge": ei, "valid": bool(BRepCheck_Analyzer(e).IsValid()), "valid_on_face": bool(an.IsValid(e)),
                  "tol": float(BRep_Tool.Tolerance_s(e)),
                  "degenerate": bool(BRep_Tool.Degenerated_s(e))}
            nb = other.get(_key(e), [])
            er["other_regions"] = [int(x) for x in nb if x != r]
            er["other_kinds"] = [getattr(regions[x], "kind", None) if x < len(regions) else None for x in er["other_regions"]]
            if not er["degenerate"]:
                try:
                    c3, c2 = BRepAdaptor_Curve(e), BRepAdaptor_Curve2d(e, face)
                    a3, b3, a2, b2 = c3.FirstParameter(), c3.LastParameter(), c2.FirstParameter(), c2.LastParameter()
                    t = np.linspace(0.0, 1.0, STRAY_POINTS)
                    p3 = np.array([_xyz(c3.Value(a3 + s * (b3 - a3))) for s in t])
                    uv = np.array([_uv(c2.Value(a2 + s * (b2 - a2))) for s in t])
                    on = np.array([_xyz(S.Value(u, v)) for u, v in uv])
                    d = np.linalg.norm(p3 - on, axis=1)
                    t23 = np.linspace(0.0, 1.0, CHECK_POINTS)
                    d23 = max(_xyz_dist(c3.Value(a3 + s * (b3 - a3)), S.Value(*_uv(c2.Value(a2 + s * (b2 - a2)))))
                              for s in t23)
                    k = int(d.argmax())
                    er.update({"length": round(float(np.linalg.norm(np.diff(p3, axis=0), axis=1).sum()), 6),
                               "stray": round(float(d[k]), 6), "stray_at": round(float(t[k]), 4),
                               "stray_xyz": np.round(p3[k], 4).tolist(), "stray_at_check_points": round(float(d23), 6)})
                    polys.append((wi, ei, uv, p3))
                except Exception as e2:  # a curve that can't be read is itself a finding
                    faults.caught(e2)
                    er["unreadable"] = f"{type(e2).__name__}: {e2}"
            edges.append(er)
            ei += 1
        wires.append({"wire": wi, "flags": flags, "valid_on_face": bool(an.IsValid(w)), "edges": edges})
        wi += 1
    out["wires"] = wires
    # (a corner's reach: two sample spacings of the edge, at least its tolerance)
    reach = {}
    for wr in wires:
        for er in wr["edges"]:
            if er.get("length") is not None:
                reach[(wr["wire"], er["edge"])] = max(er["tol"], 2.0 * er["length"] / (STRAY_POINTS - 1))
    crossings = _crossings(polys, reach)
    if crossings:
        out["crossings"] = crossings
    # (the causes in plain words, from the measures above)
    if mesh_area:
        out["area_over_triangles"] = round(area / mesh_area, 4)
    if abs(area) < 1e-9:
        causes.append("it has no area")
    elif area < 0:
        causes.append(f"it is inside out (area {area:.4g} mm2" + (f" against {mesh_area:.4g} of its triangles)" if mesh_area else ")"))
    elif mesh_area and not 0.5 <= area / mesh_area <= 2.0:
        causes.append(f"its area is {area / mesh_area:.3g} times its triangles' ({area:.4g} against {mesh_area:.4g} mm2): "
                      "its outline takes the wrong way round its surface")
    if any(wr["flags"]["self_crossing"] for wr in wires) or crossings:
        if crossings:
            x = crossings[0]
            causes.append(f"its outline crosses itself ({len(crossings)} crossing(s); first: edge {x['edges'][0]} "
                          f"x edge {x['edges'][1]} near {x['xyz']})")
        else:
            causes.append("its outline crosses itself")
    strays = [(er["stray"], er) for wr in wires for er in wr["edges"] if er.get("stray", 0.0) > er["tol"]]
    if strays:
        s, er = max(strays, key=lambda x: x[0])
        causes.append(f"{len(strays)} edge curve(s) on it stray from their edges; worst {s:.3g} mm against a tolerance of "
                      f"{er['tol']:.3g} (at its {CHECK_POINTS} check points {er['stray_at_check_points']:.3g}) near "
                      f"{er['stray_xyz']}")
    if abs(area) >= 1e-9 and abs(area) < SLIVER_AREA:
        causes.append(f"it has almost no area ({abs(area):.2g} mm2: a sliver)")
    bad_edges = sum(1 for wr in wires for er in wr["edges"] if not er["valid"])
    if bad_edges:
        causes.append(f"{bad_edges} of its edges fail the check on their own")
    on_face = sum(1 for wr in wires for er in wr["edges"] if er["valid"] and not er["valid_on_face"])
    if on_face:
        causes.append(f"{on_face} of its edges fail the check on this face only")
    bad_wires = sum(1 for wr in wires if not wr["valid_on_face"])
    if bad_wires:
        causes.append(f"{bad_wires} of its {len(wires)} outline(s) fail the check on this face")
    from OCP.TopAbs import TopAbs_VERTEX

    nv = bv = 0
    ex = TopExp_Explorer(face, TopAbs_VERTEX)
    while ex.More():
        nv += 1
        bv += not an.IsValid(ex.Current())
        ex.Next()
    out["corners"] = nv
    if bv:
        out["corners_failing"] = bv
        causes.append(f"{bv} of its {nv} corners fail the check on this face")
    out["findings"] = causes
    return out


def _crossings(polys, corner_reach=None):
    """Where the face's outline (all its wires) crosses itself in (u, v): pairs of outline pieces that cross, not
    counting neighbours along the outline. Each: the two edges (wire, edge), the (u, v) point, the 3D point of each
    edge there and how far apart they are (far apart: only the curves on the surface cross)."""
    if not polys:
        return []
    corner_reach = corner_reach or {}
    per = max(4, min(STRAY_POINTS, CROSS_SEGMENTS // max(1, len(polys))))
    A, B, own, P3a, P3b = [], [], [], [], []
    ends = {(wi, ei): (p3[0], p3[-1]) for wi, ei, uv, p3 in polys}
    n_in = {}
    for wi, ei, uv, p3 in polys:
        n_in[wi] = max(n_in.get(wi, 0), ei + 1)
    idx = 0
    order = {}
    for wi, ei, uv, p3 in polys:
        sel = np.linspace(0, len(uv) - 1, per).round().astype(int)
        u, q = uv[sel], p3[sel]
        for k in range(len(u) - 1):
            A.append(u[k]); B.append(u[k + 1]); own.append((wi, ei)); P3a.append(q[k]); P3b.append(q[k + 1])
            order.setdefault(wi, []).append(idx)
            idx += 1
    A, B, P3a, P3b = map(np.asarray, (A, B, P3a, P3b))
    n = len(A)
    seq = np.full(n, -1)
    pos = np.zeros(n, dtype=np.int64)
    for wi, ids in order.items():
        for j, i in enumerate(ids):
            seq[i] = wi
            pos[i] = j
    size = np.zeros(max(order) + 2, dtype=np.int64)
    for wi, ids in order.items():
        size[wi] = len(ids)
    lo, hi = np.minimum(A, B), np.maximum(A, B)
    out = []
    for s0 in range(0, n, 400):
        i = np.arange(s0, min(n, s0 + 400))[:, None]
        j = np.arange(n)[None, :]
        cand = (j > i) & (lo[i, 0] <= hi[j, 0]) & (lo[j, 0] <= hi[i, 0]) & (lo[i, 1] <= hi[j, 1]) & (lo[j, 1] <= hi[i, 1])
        same = seq[i] == seq[j]
        gap = np.abs(pos[i] - pos[j])
        m = size[seq[i]]
        cand &= ~(same & ((gap <= 1) | (gap == m - 1)))
        ii, jj = np.nonzero(cand)
        if not len(ii):
            continue
        ii = ii + s0
        a, b, c, d = A[ii], B[ii], A[jj], B[jj]
        r, q = b - a, d - c
        den = r[:, 0] * q[:, 1] - r[:, 1] * q[:, 0]
        ok = np.abs(den) > 1e-18
        w = c - a
        with np.errstate(divide="ignore", invalid="ignore"):
            s = (w[:, 0] * q[:, 1] - w[:, 1] * q[:, 0]) / den
            t = (w[:, 0] * r[:, 1] - w[:, 1] * r[:, 0]) / den
        hit = ok & (s > 0) & (s < 1) & (t > 0) & (t < 1)
        for k in np.nonzero(hit)[0]:
            if len(out) >= 20:
                break
            x, y = int(ii[k]), int(jj[k])
            pa = P3a[x] + s[k] * (P3b[x] - P3a[x])
            pb = P3a[y] + t[k] * (P3b[y] - P3a[y])
            (wa, ea), (wb, eb) = own[x], own[y]
            if wa == wb and (abs(ea - eb) == 1 or abs(ea - eb) == n_in.get(wa, 0) - 1 or ea == eb):
                # (two neighbouring edges, or an edge's own two ends, meeting at their shared corner: within a
                # corner's reach of it, a crossing is the sampling's)
                corners = [*ends[(wa, ea)], *ends[(wb, eb)]]
                reach = corner_reach.get((wa, ea), 0.0) + corner_reach.get((wb, eb), 0.0)
                if min(float(np.linalg.norm(pa - c)) for c in corners) <= max(reach, 1e-6):
                    continue
            out.append({"edges": [list(own[x]), list(own[y])], "uv": np.round(a[k] + s[k] * r[k], 6).tolist(),
                        "xyz": np.round(pa, 4).tolist(), "apart": round(float(np.linalg.norm(pa - pb)), 6)})
        if len(out) >= 20:
            break
    return out


def _edge_owner(asm):
    """Edge -> the regions whose faces use it (from the assembler's faces)."""
    cached = getattr(asm, "_fault_edge_owner", None)
    if cached is not None:
        return cached
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    out = {}
    for rr, f in getattr(asm, "region_faces", {}).items():
        ex = TopExp_Explorer(f, TopAbs_EDGE)
        while ex.More():
            out.setdefault(_key(ex.Current()), set()).add(int(rr))
            ex.Next()
    try:
        asm._fault_edge_owner = out
    except AttributeError:
        pass
    return out


def _key(edge):
    try:
        return hash(edge)
    except TypeError:
        return edge.HashCode(1 << 30)


def _xyz(p):
    return (p.X(), p.Y(), p.Z())


def _uv(p):
    return (p.X(), p.Y())


def _xyz_dist(p, q):
    return p.Distance(q)


def _plain(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def _clean(o):
    """The record without its working keys (those starting with '_')."""
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items() if not str(k).startswith("_")}
    if isinstance(o, list):
        return [_clean(v) for v in o]
    return o


def _drop_geometry(o):
    if isinstance(o, dict):
        for k in ("tri_xyz", "ring_xyz", "brep", "tri_ids", "ring_ids"):
            o.pop(k, None)
        for v in o.values():
            _drop_geometry(v)
    elif isinstance(o, list):
        for v in o:
            _drop_geometry(v)
