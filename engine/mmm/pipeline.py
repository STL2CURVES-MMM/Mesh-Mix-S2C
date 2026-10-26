"""Conversion pipeline and presets."""
# Part of Manifold Mesh Mix (c) 2026 Tuna-The-First and Claude, AGPL-3.0, commit 4cf72ea.
# Modified 2026-10-25 for the stl2curves merged engine (stl2curves/merged.py): convert()
# takes a front end (stl2curves' reading, mending and features) and seeded segmentations;
# Options.judge_floor (a least tolerance for the fit check), Options.fewest_faces (of
# the CAD results that pass, the one with the fewest faces), Options.enough (no
# fallback methods once a rival conversion passes) and Options.smooth_elsewhere (no
# smooth surfaces for seeded bodies), all off by default.
import os
import time

import numpy as np

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
from OCP.ShapeFix import ShapeFix_Shape
from OCP.TopAbs import TopAbs_SHELL
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from . import stepio
from .brep import BRepAssembler, PlanarSurface, make_compound, make_solid
from .meshio import face_normals_areas, load_and_prepare
from .planar import planar_regions
from .report import NULL
from .topology import RegionGraph

PRESETS = {
    "precise": {
        "label": "Precise",
        "description": "Real CAD surfaces that follow the mesh as closely as possible (tight tolerance).",
    },
    "cad": {
        "label": "CAD",
        "description": "Recognises planes, cylinders, cones, spheres and tori as editable CAD faces.",
    },
    "smooth": {
        "label": "Smooth",
        "description": "Fits smooth NURBS surfaces for organic and sculpted shapes.",
    },
}


class ConversionError(Exception):
    """A conversion that can't produce a result; the message is for the user."""


class Options:
    def __init__(self, **kw):
        self.preset = kw.get("preset", "auto")
        self.repair = kw.get("repair", "auto")
        self.remove_debris = kw.get("remove_debris", True)
        self.unit = kw.get("unit", "mm")
        self.tolerance = kw.get("tolerance", None)  # absolute, None = auto
        # 0 = fastest, 1 = best quality (also controls surface density)
        self.quality = float(kw.get("quality", kw.get("detail", 0.7)))
        self.detail = self.quality
        # the quality the user picked (Precise raises ``quality`` for organic
        # parts); its top end also keeps the fine detail of dense meshes
        self.fine_quality = float(kw.get("fine_quality", self.quality))
        self.layout = kw.get("layout", "parts")  # 3MF: "parts" or "plate"
        self.max_faces = int(kw.get("max_faces", 0))  # 0 = unlimited (exact)
        self.preview = kw.get("preview", None)  # path for preview file
        self.validate = kw.get("validate", True)
        self.product_name = kw.get("product_name", None)
        # 3MF projects: one STEP file per slicer part instead of one file
        self.split = bool(kw.get("split", False))
        self.part_outputs = list(kw.get("part_outputs") or [])  # by part index
        self.part_previews = list(kw.get("part_previews") or [])
        # join overlapping pieces of a part into one solid (mesh check)
        self.merge_overlaps = bool(kw.get("merge_overlaps", False))
        # 3MF parts that touch each other: "ask" / "split" (keep separate) / "merge"
        self.touching_parts = kw.get("touching_parts") or "ask"
        # (stl2curves merged engine) the fit check's tolerance never below this (model
        # units; 0 = MMM's own, from the body's size), and of the CAD results that pass,
        # the one with the fewest faces kept (see _fewest)
        self.judge_floor = float(kw.get("judge_floor", 0.0))
        self.fewest_faces = bool(kw.get("fewest_faces", False))
        # (stl2curves merged engine) a callable, true once a rival conversion of the whole
        # file passes: a body's fallback methods are then not tried (rivals.Judges.passing)
        self.enough = kw.get("enough")
        # (stl2curves merged engine) smooth surfaces for seeded bodies are tried elsewhere
        # (MMM alone runs alongside): not here
        self.smooth_elsewhere = bool(kw.get("smooth_elsewhere", False))


# ----------------------------------------------------------------------------
# Exact (faceted) builder
# ----------------------------------------------------------------------------


def build_faceted_shell(shell, tol, report=NULL, progress=None):
    V, F = shell.V, shell.F
    topo = shell.topo
    normals, areas = face_normals_areas(V, F)
    labels, planes = planar_regions(V, F, topo, normals, areas, tol)
    rg = RegionGraph(V, F, labels, topo)
    surfaces = [PlanarSurface(c, n) for (c, n, _) in planes]
    asm = BRepAssembler(V, rg, surfaces, tol, report)
    faces = asm.build(progress)
    if asm.failed_regions:
        report.warn(f"{len(asm.failed_regions)} face(s) could not be built: {asm.failed_regions[:3]}")
    return asm, faces


def assemble_body(B, built_shells, closed_flags, tol, report=NULL):
    """built_shells: list of TopoDS_Shell (outer first). Returns a shape."""
    if all(closed_flags) and built_shells:
        return make_solid(B, built_shells)
    return make_compound(built_shells)


def sew_faces(faces, tol):
    sew = BRepBuilderAPI_Sewing(tol)
    for f in faces:
        sew.Add(f)
    sew.Perform()
    return sew.SewedShape()


def fix_shape(shape, tol):
    """OpenCASCADE's general shape healing. Returns the input unchanged if
    healing would lose solids (it can turn a nearly valid solid into a
    "valid" open shell, which is worse for CAD use)."""
    from OCP.TopAbs import TopAbs_SOLID

    sf = ShapeFix_Shape(shape)
    sf.SetPrecision(tol)
    sf.SetMaxTolerance(max(tol * 10, 1e-3))
    sf.Perform()
    out = sf.Shape()
    if stepio.count(out, TopAbs_SOLID) < stepio.count(shape, TopAbs_SOLID):
        return shape
    return out


def shells_to_solid(shape):
    """Wrap closed shells of a sewn shape into solids."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid

    out = []
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        sh = TopoDS.Shell(exp.Current())
        exp.Next()
        if BRep_Tool.IsClosed_s(sh):  # an open shell can't be a solid
            mk = BRepBuilderAPI_MakeSolid(sh)
            if mk.IsDone():
                out.append(mk.Solid())
                continue
        out.append(sh)
    return out


def _volume_ratio(shape, body):
    """Solid volume / mesh volume (None when the mesh isn't closed)."""
    mesh_vol = sum(sh.volume for sh in body if sh.closed)
    if not mesh_vol or not all(sh.closed for sh in body):
        return None
    try:
        return abs(stepio.volume(shape)) / abs(mesh_vol)
    except Exception:
        return None


def _smooth_ok(shape, body, tol):
    """A smooth result is usable when it is a valid solid (or general
    healing makes it one) and holds the mesh's volume."""
    r = _volume_ratio(shape, body)
    if r is not None and abs(r - 1.0) > 0.03:
        return False
    return stepio.is_valid(shape) or stepio.is_valid(fix_shape(shape, tol))


RECONVERTED = "part converted again with another method"

# flat faces exactly like the mesh, as the last resort, only up to this many
# triangles (bigger meshes would give huge files that CAD programs choke on)
FACETED_MAX_TRIANGLES = 20_000
# a second, denser smooth fit is only tried up to this many triangles
DENSER_MAX_TRIANGLES = 200_000


def flat_share(body, tol):
    """Share of the surface in big flat regions, flat within ``tol``."""
    tot = flat = 0.0
    for s in body:
        normals, areas = face_normals_areas(s.V, s.F)
        labels, _ = planar_regions(s.V, s.F, s.topo, normals, areas, tol)
        ar = np.bincount(labels, weights=areas)
        a_tot = areas.sum()
        flat += ar[ar >= 0.01 * a_tot].sum()
        tot += a_tot
    return flat / max(tot, 1e-30)


def _diag(body):
    P = np.vstack([s.V for s in body])
    return float(np.linalg.norm(P.max(0) - P.min(0)))


class _Candidate:
    """One way a body was converted, and how good it is."""

    def __init__(self, shape, kind, valid, verdict):
        self.shape = shape
        self.kind = kind
        self.valid = valid
        self.verdict = verdict

    @property
    def good(self):
        return self.valid and (self.verdict is None or self.verdict.ok)

    def rank(self):
        # following the mesh comes first (a valid but wrinkled shape is
        # useless), then following it nearly everywhere, then validity,
        # then how closely it follows: a rim's CAD faces, 99.8% within the
        # tolerance but with a few faulty faces, lost to smooth surfaces
        # only 40% within it because those had no faults
        v = self.verdict
        nearly = v is None or v.ok or getattr(v, "r", {}).get("within", 0.0) >= NEARLY_ALL
        return (v is None or v.ok, nearly, self.valid, 0.0 if v is None else v.score)


# share of the mesh surface within the tolerance for a result that doesn't
# pass to still rank above one that is far off (see _Candidate.rank)
NEARLY_ALL = 0.98

# when two results both follow the mesh, the other method's replaces the
# first only when it is this much closer (in Verdict.score: about one
# tolerance of the 99th-percentile gap)
CLEARLY_CLOSER = 1.0

KIND_WORDS = {"smooth": "smooth surfaces", "cad": "CAD faces", "precise": "precise CAD faces",
              "faceted": "flat faces (exactly like the mesh)", "stl2curves": "stl2curves' sewn CAD faces"}


def _judge(shape, body, tol, kind, report):
    """Validity (after general healing if needed) and closeness to the mesh."""
    from .fidelity import Verdict, judge

    valid = stepio.is_valid(shape)
    if not valid:
        fixed = fix_shape(shape, tol)
        if stepio.is_valid(fixed):
            shape, valid = fixed, True
    report.note(f"Comparing the {KIND_WORDS[kind].split(' (')[0]} with the mesh")

    def again(why):
        report.note(f"Checking again, more closely: {why}")
        report.log(f"The first check of the {KIND_WORDS[kind].split(' (')[0]} was not clear-cut ({why}), "
                   "so it is checked again with more points and finer detail.")

    try:
        verdict = judge(shape, body, tol, on_recheck=again)
    except MemoryError:
        raise
    except Exception as e:  # the check itself must never stop a conversion
        report.log(f"The shape couldn't be compared with the mesh ({e})")
        verdict = Verdict({"unknown": True}, tol)
    return _Candidate(shape, kind, valid, verdict)


def fidelity_summary(items):
    """One verdict for several bodies or parts (the worst of each measure),
    or None when none was measured."""
    items = [v for v in items if v and not v.get("empty")]
    if not items:
        return None
    return {
        "ok": all(v["ok"] for v in items),
        "tolerance": max(v["tolerance"] for v in items),
        "within": min(v["within"] for v in items),
        "within2": min(v["within2"] for v in items),
        "p99": max(v["p99"] for v in items),
        "max": max(v["max"] for v in items),
        "wrong_way": max(v["wrong_way"] for v in items),
        "mesh_kind": "scan" if any(v.get("mesh_kind") == "scan" for v in items) else items[0].get("mesh_kind", "clean"),
        "certainty": min(v.get("certainty", 1.0) for v in items),
        "rechecked": any(v.get("rechecked") for v in items),
        "local_max": max(v.get("local_max", 0.0) for v in items),
    }


def _best(cands):
    cands = [c for c in cands if c is not None]
    return max(cands, key=lambda c: c.rank()) if cands else None


# (stl2curves merged engine) the kinds of result made of CAD faces, and how much closer to
# the fewest faces a passing result must come to be kept for following the mesh better
CAD_KINDS = ("cad", "precise", "stl2curves")
FEWER_FACES = 0.05


def _face_count(shape):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    n, ex = 0, TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        n += 1
        ex.Next()
    return n


def _fewest(cands, best):
    """Of the CAD results that pass the fit check, the one with the fewest faces (the
    closer one when their counts are within FEWER_FACES): both pass, so the one easier
    to edit wins. On Rak's N-type tensioner MMM's closest-first choice took 1,706 flat
    faces over CAD faces 163-253 strong (stl2curves merged engine)."""
    if best is None or not best.good or best.kind not in CAD_KINDS:
        return best
    rivals = [c for c in cands if c is not None and c.good and c.kind in CAD_KINDS]
    if len(rivals) < 2:
        return best
    n = {id(c): _face_count(c.shape) for c in rivals}
    fewest = min(rivals, key=lambda c: n[id(c)])
    near = [c for c in rivals if n[id(c)] <= n[id(fewest)] * (1 + FEWER_FACES)]
    closest = max(near, key=lambda c: c.verdict.score)
    # (closeness decides only by a clear margin: measurement noise mustn't)
    return closest if closest.verdict.score > fewest.verdict.score + CLEARLY_CLOSER else fewest


def _broken(check):
    """The saved file has problems that need fixing (a wrong volume means
    the shape itself is off, which no repair can fix)."""
    from .repair import needs_repair

    return needs_repair(check) or any(i["code"] == "volume" for i in check["issues"])


def _check_shape(shape, body, report):
    """The read-back check of one body's shape on its own."""
    import tempfile

    closed = bool(body) and all(sh.closed for sh in body)
    fd, p = tempfile.mkstemp(suffix=".step", prefix="mmm-part-")
    os.close(fd)
    try:
        stepio.write_step(shape, p)
        return stepio.verify_step(p, expected_solids=1 if closed else 0,
                                  mesh_volume=sum(sh.volume for sh in body if sh.closed), open_parts=0 if closed else 1)
    finally:
        try:
            os.remove(p)
        except OSError:
            pass


def _fix_broken_parts(items, item_bodies, item_presets, bodies, path, check, write, verify, convert_alt, tol, report, stage,
                      on_swap=None):
    """Last resort, when the saved file still has problems after repairing:
    convert the broken parts again with another method (see convert_alt)
    and keep a part's new version only if it checks out clean on its own.
    Returns (items, presets, check, steps taken)."""
    if not _broken(check) or len(items) != len(item_bodies):
        return items, item_presets, check, []
    report.log("The STEP file still has problems, so the parts with errors are converted again with another method.")
    new_items, new_presets, changed = list(items), list(item_presets), False
    for k, ((shape, name), bi, pr) in enumerate(zip(items, item_bodies, item_presets)):
        body = bodies[bi]
        if len(items) > 1 and not _broken(_check_shape(shape, body, report)):
            continue
        alt, alt_preset, how = convert_alt(bi, pr)
        if alt is None:
            continue
        if _broken(_check_shape(alt, body, report)):
            report.log("The other method had problems too, so the first version was kept.")
            continue
        new_items[k] = (alt, name)
        new_presets[k] = alt_preset
        changed = True
        if on_swap is not None:
            on_swap(bi)
        report.warn(f"One part was saved with {how}, because its first version had errors that couldn't be repaired.")
    if not changed:
        return items, item_presets, check, []
    new_items, check, done = _save_checked(new_items, path, write, verify, tol, report, stage)
    return new_items, new_presets, check, [RECONVERTED] + done


def repair_steps(results):
    """The repair steps any of the parts needed, in order, once each."""
    out = []
    for r in results:
        for step in r.get("repaired") or []:
            if step not in out:
                out.append(step)
    return out


def _step_writer(name, unit, report):
    """write(items, path) for a list of (shape, part name): one named part
    each (several make an assembly), or a single unnamed shape."""

    def write(items, path):
        if len(items) > 1:
            try:
                stepio.write_step_named(items, path, product_name=name, unit=unit)
                return
            except Exception as e:
                report.log(f"Couldn't store part names in the STEP file ({e}); it was saved without them.")
            stepio.write_step(make_compound([sh for sh, _ in items]), path, product_name=name, unit=unit)
        elif items[0][1] is not None:
            stepio.write_step_named(items, path, product_name=name, unit=unit)
        else:
            stepio.write_step(items[0][0], path, product_name=name, unit=unit)

    return write


def _save_checked(items, path, write, verify, tol, report, stage):
    """Write the STEP file, read it back to check it and, only when that
    finds problems, repair it the way CAD programs do on import.
    Returns (items, check, repair steps taken)."""
    from .repair import needs_repair, repair

    write(items, path)
    report.stage(*stage[0])
    check = verify(path)
    done = []
    if needs_repair(check):
        report.stage(*stage[1])
        report.log("Reading the STEP file back found problems, so it is being repaired "
                   "(the way CAD programs repair files when they import them).")
        items, check, done = repair(items, path, check, write, verify, tol, report)
        if done and not needs_repair(check):
            report.log("The STEP file was repaired and now reads back without problems.")
        elif needs_repair(check):
            report.log("Some problems couldn't be repaired automatically.")
    return items, check, done


# ----------------------------------------------------------------------------
# Main entry point
# ----------------------------------------------------------------------------


def auto_tolerance(diag, preset, quality=0.7):
    """Fit tolerance from the body size; higher quality = tighter."""
    q = float(np.clip(quality, 0.0, 1.0))
    factor = 1.4 - 0.6 * q  # 1.4 at fastest, 0.8 at best quality
    if preset == "exact":
        return 1e-5 * diag
    if preset == "precise":
        return 1.6e-4 * diag * factor
    return 5e-4 * diag * factor


def convert(input_path, output_path, opts=None, report=NULL, front=None):
    """front (stl2curves' merged engine, see stl2curves/merged.py): reads and prepares
    the mesh instead of load_and_prepare, returning (bodies, info, seeded), seeded[k]
    (if there) making body k's segmentation, seeded with stl2curves' features, for a
    tolerance: (preset, segs) in place of choose_preset_body's."""
    opts = opts or Options()
    t0 = time.time()
    seeded, extra = {}, {}
    with report.section(0.0, 0.08):
        if front is not None:
            bodies, info, seeded, extra = front(input_path, opts, report)
        else:
            bodies, info = load_and_prepare(
                input_path, repair=opts.repair, remove_debris=opts.remove_debris, report=report, layout=opts.layout,
                merge=opts.merge_overlaps, touching=opts.touching_parts,
            )
    diag = info.diag
    # 3MF files store their unit, and the reader turns it into millimetres;
    # other formats have none, so their numbers are in the chosen unit
    unit = "mm" if input_path.lower().endswith(".3mf") else opts.unit
    if unit != opts.unit:
        report.log("3MF files carry their own unit, so the STEP file is in millimetres")
    stepio.set_model_unit(unit)
    q = opts.quality
    req = opts.preset
    if req == "exact":
        req = "precise"  # faceted output was retired: everything is real surfaces
    tol = opts.tolerance if opts.tolerance else auto_tolerance(diag, req if req != "auto" else "cad", q)
    report.log(f"Model size {diag:.4g} {unit} (diagonal), quality {int(round(q * 100))}%")

    def body_tol(body, preset):
        if opts.tolerance:
            return opts.tolerance
        P = np.vstack([s.V for s in body])
        d = float(np.linalg.norm(P.max(0) - P.min(0)))
        return auto_tolerance(d, preset, q)

    B = BRep_Builder()
    shapes = []
    shape_names = []
    shape_bodies = []
    used_presets = []
    stats_extra = {}
    # how closely each body's shape follows its mesh (by body index)
    verdicts = {}
    # every candidate convert_one made for a body, and an alternative that
    # replaces it once the saved file is checked (by body index)
    tried_by_body = {}
    pending = {}
    # a valid second version of a body, offered when no method follows its
    # mesh (by body index; see alternative_version)
    offered = {}

    def on_swap(bi):
        """Body bi's alternative made it into the file."""
        if bi in pending:
            _record(bi, pending.pop(bi))
    body_names = info.body_names or [None] * len(bodies)
    n_bodies = len(bodies)

    def looks_mechanical(body):
        """CAD faces only make sense as a fallback for mechanical parts (on
        organic shapes they're slow and no better). A scanned mechanical
        part has no exactly flat faces (the noise hides them), but much of
        it is flat within the fit tolerance."""
        if choose_preset_body(body, body_tol(body, "cad"), q, NULL)[0] == "cad":
            return True
        return flat_share(body, body_tol(body, "cad")) >= 0.3

    def convert_one(bi, body, lo, hi, label):
        """Convert one body; returns (shape or None, preset used).

        Every result is judged by how closely it follows the mesh (see
        fidelity.py) as well as by being a valid solid. When the first
        method's result falls short, the others are tried, and the best
        result wins: one that follows the mesh beats one that is merely
        valid, so a part is never saved as a wrinkled or dented shape when a
        closer one was made."""
        preset = req
        segs = None
        body_opts = opts
        if preset == "auto" and bi in seeded:
            # (stl2curves found features here: CAD faces, segmented from them)
            report.stage("Segmenting what stl2curves left" + label, lo, lo + 0.2 * (hi - lo))
            preset, segs = seeded[bi](body_tol(body, "cad"))
            report.log("Using CAD (stl2curves' features, MMM's segmentation of the rest)")
        elif preset in ("auto", "precise"):
            report.stage("Analysing shape" + label, lo, lo + 0.2 * (hi - lo))
            kind, segs = choose_preset_body(body, body_tol(body, "cad" if preset == "auto" else "precise"), q, report)
            if preset == "precise":
                if kind == "smooth":
                    # organic body: precise means more, tighter surface patches
                    preset = "smooth"
                    body_opts = Options(**{**opts.__dict__, "quality": min(1.0, q + 0.2)})
                    body_opts.max_faces = opts.max_faces
                    segs = None
                else:
                    preset = "precise"
            else:
                preset = kind
            report.log(f"Using {PRESETS[preset]['label']}" + (f" for {body_names[bi]}" if n_bodies > 1 and body_names[bi] else ""))
        btol = body_tol(body, preset)
        name = (body_names[bi] if n_bodies > 1 and body_names[bi] else None)
        tried = []
        errors = []

        def attempt(kind, make, how=None):
            """Build one candidate and judge it; None if it couldn't be built."""
            try:
                shape = make()
            except (ConversionError, MemoryError):
                raise
            except Exception as e:
                errors.append(e)
                report.log(f"{how or kind} didn't work ({e})")
                return None
            if shape is None:
                return None
            c = (_judge(shape, body, max(btol, opts.judge_floor), kind, report) if opts.validate
                 else _Candidate(shape, kind, True, None))
            tried.append(c)
            return c

        def cad(tol_kind="cad"):
            from .cad import convert_body_cad

            return convert_body_cad(B, body, body_tol(body, tol_kind), body_opts, report,
                                    segs=segs if tol_kind == preset else None)

        def smooth(o=None):
            from .smooth import convert_body_smooth

            return convert_body_smooth(B, body, btol, o or body_opts, report)

        def plan():
            """The other methods, in the order they're tried (a front end's own
            candidates first: stl2curves' sewn faces, see merged.py)."""
            if preset in ("cad", "precise"):
                return list(extra.get(bi, [])) + [("smooth", "Fitting smooth surfaces", lambda: smooth())]
            out = list(extra.get(bi, []))
            if looks_mechanical(body):
                out.append(("cad", "Recognising CAD features", lambda: cad("cad")))
            denser = min(1.0, body_opts.quality + 0.2)
            # (on very big meshes a second, denser fit takes far too long)
            if denser > body_opts.quality and sum(len(s.F) for s in body) <= DENSER_MAX_TRIANGLES:
                dense_opts = Options(**{**opts.__dict__, "quality": denser})
                dense_opts.max_faces = opts.max_faces
                out.append(("smooth", "Fitting denser smooth surfaces", lambda: smooth(dense_opts)))
            return out

        if preset in ("cad", "precise"):
            report.stage(("Recognising CAD features" if preset == "cad" else "Fitting precise surfaces") + label, lo, hi)
            first = attempt(preset, lambda: cad(preset))
        elif preset == "smooth":
            report.stage("Fitting smooth surfaces" + label, lo, hi)
            first = attempt("smooth", lambda: smooth())
        else:
            raise ValueError(f"unknown preset {preset}")
        tried_by_body[bi] = tried
        if not opts.validate:
            if first is None and errors:
                raise errors[0]
            return (first.shape, first.kind) if first is not None else (None, preset)
        borderline = first is not None and first.good and first.verdict.certainty < first.verdict.RECHECK_BELOW
        if first is not None and first.good and not borderline:
            _record(bi, first)
            return first.shape, first.kind
        if (first is not None and first.verdict.ok and not borderline
                and not _broken(_check_shape(first.shape, body, report))):
            # it follows the mesh and its small errors disappear once saved
            # and read back (as CAD programs read it): nothing to gain
            _record(bi, first)
            return first.shape, first.kind
        if borderline:
            # it passes, but only just: the other way is tried as well, and
            # the closer one is kept
            report.log(f"The {KIND_WORDS[first.kind].split(' (')[0]} follow the mesh, but only just "
                       f"({first.verdict.doubt}), so another method is tried as well and the closer result is kept.")
        elif first is not None:
            report.log(f"Check against the mesh: {first.verdict.summary(unit)}"
                       + ("" if first.verdict.ok else ", which isn't close enough")
                       + ("" if first.valid else "; the shape also has errors") + ".")
        for kind, stage, make in plan():
            if opts.enough is not None and opts.enough():
                report.log("Another conversion of the whole file already follows the mesh, so no other "
                           "method is tried here (the results are compared at the end)")
                break
            if kind == "smooth" and bi in seeded and (any(c.good for c in tried) or opts.smooth_elsewhere):
                # (a mechanical body, seeded with stl2curves' features, that already has
                # good CAD faces: smooth surfaces never win there, and on Rak N-USB
                # trying them took over 100 s; nor when MMM alone runs alongside, which
                # tries them itself wherever its own rules call for them: in six tries
                # on seeded bodies they never won, and Rak Uni-USB spent 380 s on them)
                continue
            if borderline:
                report.log(f"Also trying {stage[0].lower() + stage[1:]}, to keep whichever follows the mesh better")
                report.stage(f"{stage} too (the first result only just passed)" + label, lo, hi)
            else:
                report.log(f"Trying {stage[0].lower() + stage[1:]} instead")
                report.stage(stage + label, lo, hi)
            c = attempt(kind, make, stage)
            if c is not None and c.good and not borderline:
                break
        if not any(c.good for c in tried) and not (opts.enough is not None and opts.enough()):
            # the faceted shape follows the mesh exactly: the last resort
            # when nothing smoother does (only for meshes small enough
            # to give a file of reasonable size)
            n_tri = sum(len(s.F) for s in body)
            if n_tri <= FACETED_MAX_TRIANGLES:
                report.stage("Building flat faces" + label, lo, hi)
                attempt("faceted", lambda: convert_body_exact(B, body, auto_tolerance(_diag(body), "exact", q), report),
                        "Flat faces")
        best = _best(tried)
        if borderline and best is not first and best.good and best.verdict.score < first.verdict.score + CLEARLY_CLOSER:
            # both follow the mesh: the kind Auto chose for this part (true
            # CAD faces for a mechanical part) stays, unless the other is
            # clearly closer
            best = first
        closest = best
        if opts.fewest_faces:
            best = _fewest(tried, best)
        if best is None:
            if errors:
                raise errors[0]  # a fault in the converter, not in the file
            return None, preset
        if best is not first:
            report.warn(("One part was" if name is None else f"“{name}” was")
                        + f" saved with {KIND_WORDS[best.kind]}, because "
                        + ("they follow the mesh more closely" if best is closest else
                           "they follow the mesh closely enough with fewer faces")
                        + ("." if first is None or first.valid else " and have no errors."))
        if not best.verdict.ok:
            report.warn(("The" if name is None else f"“{name}”:") + " converted shape doesn't follow the mesh everywhere: "
                        + best.verdict.summary(unit) + ". Try a higher quality, or another preset.")
        _record(bi, best)
        return best.shape, best.kind

    def _record(bi, c):
        d = c.verdict.as_dict() if c.verdict is not None else None
        if d and not d.get("empty"):
            verdicts[bi] = d

    def convert_alt(bi, preset):
        """One body with another method: smooth surfaces instead of CAD
        faces; CAD faces instead of smooth surfaces for mechanical parts;
        denser smooth surfaces for organic ones (thin walls and lattices
        need them). Returns (shape or None, preset, how it was made); None
        also when the new shape doesn't follow the mesh closely enough (a
        valid but wrinkled shape is worse than one with small errors)."""
        body = bodies[bi]
        # the other methods convert_one already tried (the same conversion
        # again would give the same result)
        done = [c for c in tried_by_body.get(bi, []) if c.kind != preset]
        if done:
            c = _best(done)
            how = f"{KIND_WORDS[c.kind]} instead"
        elif bi in seeded and preset in ("cad", "precise") and (
                opts.smooth_elsewhere or (opts.enough is not None and opts.enough())):
            # (stl2curves merged engine: a seeded body's other method would be smooth
            # surfaces, which MMM alone, running alongside, tries itself; Rak Uni-USB
            # spent 140 s on them here after its saved file broke, and lost anyway)
            report.log("No other method is tried for this part here: the conversions running alongside cover it")
            return None, preset, ""
        else:
            shape, kind, how = _alt_shape(bi, preset)
            if shape is None or not opts.validate:
                return shape, kind, how
            c = _judge(shape, body, max(body_tol(body, kind if kind != "faceted" else "cad"), opts.judge_floor),
                       kind, report)
        if c.verdict is not None and not c.verdict.ok:
            report.log(f"The other method doesn't follow the mesh closely enough ({c.verdict.summary(unit)}), "
                       "so the first version was kept.")
            if c.valid:
                # neither follows the mesh: the closer one is saved, and this
                # valid one is offered as a second version to choose from
                offered[bi] = c
            return None, preset, ""
        if c.verdict is not None:
            pending[bi] = c
        return c.shape, c.kind, how

    def _alt_shape(bi, preset):
        from .cad import convert_body_cad
        from .smooth import convert_body_smooth

        body = bodies[bi]
        name = body_names[bi] or "the part"
        try:
            if preset in ("cad", "precise"):
                report.log(f"Converting {name} again with smooth surfaces")
                return (convert_body_smooth(B, body, body_tol(body, "smooth"), opts, report), "smooth",
                        "smooth surfaces instead of CAD faces")
            if looks_mechanical(body):
                report.log(f"Converting {name} again with CAD faces")
                return (convert_body_cad(B, body, body_tol(body, "cad"), opts, report), "cad",
                        "CAD faces instead of smooth surfaces")
            used = min(1.0, q + 0.2) if req == "precise" else q
            denser = min(1.0, used + 0.2)
            if denser <= used:
                report.log("This part is organic and already has the densest smooth surfaces.")
                return None, preset, ""
            report.log("This part is organic, so it is fitted again with denser smooth surfaces")
            dense_opts = Options(**{**opts.__dict__, "quality": denser})
            dense_opts.max_faces = opts.max_faces
            return (convert_body_smooth(B, body, body_tol(body, "precise"), dense_opts, report), "smooth",
                    "denser smooth surfaces")
        except Exception as e:
            report.log(f"That didn't work either ({e})")
            return None, preset, ""

    if opts.split and info.parts > 1 and len(info.body_part) == n_bodies:
        return _convert_split(input_path, output_path, opts, report, t0, bodies, info, tol, convert_one, convert_alt,
                              verdicts, on_swap, offered)

    for bi, body in enumerate(bodies):
        lo = 0.1 + 0.75 * bi / n_bodies
        hi = 0.1 + 0.75 * (bi + 1) / n_bodies
        label = f" (body {bi + 1}/{n_bodies})" if n_bodies > 1 else ""
        shape, preset = convert_one(bi, body, lo, hi, label)
        if shape is None:
            report.warn(f"{body_names[bi] or f'Body {bi + 1}'} couldn't be converted and was left out.")
        if shape is not None:
            shapes.append(shape)
            shape_names.append(body_names[bi] if bi < len(body_names) else None)
            shape_bodies.append(bi)
            used_presets.append(preset)
    preset = used_presets[0] if used_presets and all(p == used_presets[0] for p in used_presets) else "mixed"

    if not shapes:
        raise ConversionError("None of the parts could be converted.")
    result = shapes[0] if len(shapes) == 1 else make_compound(shapes)

    report.stage("Checking result", 0.85, 0.9)
    valid = stepio.is_valid(result) if opts.validate else None
    # one named part per body (the parts' names show in CAD browsers)
    items = list(zip(shapes, shape_names)) if len(shapes) > 1 else [(result, None)]
    if valid is False:
        report.log("The result had errors, so automatic repair was run on it.")
        fixed = fix_shape(result, tol)
        if stepio.is_valid(fixed):
            result = fixed
            valid = True
            items = [(result, None)]
    mesh_vol = sum(s.volume for b in bodies for s in b if s.closed)

    report.stage("Writing STEP", 0.9, 0.93)
    name = opts.product_name or os.path.splitext(os.path.basename(input_path))[0]
    expected = sum(1 for b in bodies if b and all(sh.closed for sh in b))

    def verify(path):
        return stepio.verify_step(path, expected_solids=expected, mesh_volume=mesh_vol, open_parts=len(bodies) - expected)

    write = _step_writer(name, unit, report)
    stages = (("Checking the STEP file", 0.93, 0.95), ("Repairing the STEP file", 0.95, 0.97))
    items, check, repaired = _save_checked(items, output_path, write, verify, tol, report, stages)
    if opts.validate:
        n_items = len(items)
        items, item_presets, check, more = _fix_broken_parts(
            items, shape_bodies if n_items == len(shapes) else [], used_presets, bodies, output_path, check,
            write, verify, convert_alt, tol, report, stages, on_swap)
        if more:
            repaired = more + [st_ for st_ in repaired if st_ not in more]
            preset = item_presets[0] if all(x == item_presets[0] for x in item_presets) else "mixed"
    if repaired:
        result = items[0][0] if len(items) == 1 else make_compound([sh for sh, _ in items])
    if opts.validate and "valid" in check:
        valid = check["valid"]  # what CAD programs will find in the saved file
    st = stepio.shape_stats(result)
    vol = check["volume"] if "volume" in check else (stepio.volume(result) if st["solids"] else 0.0)

    if opts.preview:
        report.stage("Generating preview", 0.97, 1.0)
        try:
            stepio.export_preview(result, opts.preview, diag)
        except Exception as e:
            report.warn(f"The 3D preview couldn't be made ({e}); the STEP file itself is fine.")

    res = {
        "output": output_path,
        "preset": preset,
        "valid": valid,
        "solids": st["solids"],
        "shells": st["shells"],
        "faces": st["faces"],
        "edges": st["edges"],
        "volume": vol,
        "mesh_volume": mesh_vol,
        "file_size": os.path.getsize(output_path),
        "seconds": round(time.time() - t0, 2),
        "input": info.as_dict(),
        "mesh_check": info.mesh_check,
        "tolerance": tol,
        "checked": check["checked"],
        "check_ok": check["ok"],
        "checks": check["issues"],
        "repaired": repaired,
    }
    res.update(stats_extra)
    res["fidelity"] = fidelity_summary(list(verdicts.values()))
    if offered:
        item_bodies = shape_bodies if len(items) == len(shape_bodies) else []
        res["alternative"] = alternative_version(items, item_bodies, offered, verdicts, output_path, opts.preview,
                                                 write, verify, diag, report)
    report.result(res)
    return res


def alternative_version(items, item_bodies, offered, verdicts, path, preview, write, verify, diag, report):
    """When no method makes a body that follows its mesh, the closer result
    is saved, and the valid one (that some programs open more readily) is
    saved next to it, for the user to choose: "<name>.valid.step". Returns
    what the app shows about it, or None."""
    alt = list(items)
    marks = {}
    for k, bi in enumerate(item_bodies):
        if bi in offered:
            alt[k] = (offered[bi].shape, items[k][1])
            marks[bi] = offered[bi].verdict.as_dict()
    if not marks:
        return None
    alt_path = os.path.splitext(path)[0] + ".valid.step"
    try:
        write(alt, alt_path)
        check = verify(alt_path)
        shape = alt[0][0] if len(alt) == 1 else make_compound([sh for sh, _ in alt])
        st = stepio.shape_stats(shape)
        alt_preview = None
        if preview:
            alt_preview = os.path.splitext(preview)[0] + ".valid.mmmp"
            stepio.export_preview(shape, alt_preview, diag)
    except Exception as e:
        report.log(f"The second version couldn't be saved ({e})")
        return None
    report.log("No method made a shape that follows the mesh everywhere, so two versions were saved to choose "
               "from: the closest one, and a valid one that is less close.")
    return {
        "output": alt_path,
        "preview": alt_preview,
        "valid": check.get("valid"),
        "checked": check.get("checked", False),
        "checks": check.get("issues", []),
        "solids": st["solids"],
        "shells": st["shells"],
        "faces": st["faces"],
        "volume": check.get("volume", 0.0),
        "file_size": os.path.getsize(alt_path),
        "fidelity": fidelity_summary([marks.get(bi) or verdicts.get(bi) for bi in item_bodies if bi in marks or bi in verdicts]),
    }


def _safe_name(name):
    bad = '/\\:*?"<>|'
    out = "".join("-" if ch in bad or ord(ch) < 32 else ch for ch in str(name)).strip(" .")
    return out[:100] or "Part"


def _part_output_path(output_path, name, used):
    folder = os.path.dirname(output_path) or "."
    stem = os.path.splitext(os.path.basename(output_path))[0]
    base = f"{stem} - {_safe_name(name)}"
    path = os.path.join(folder, base + ".step")
    n = 2
    while path in used:
        path = os.path.join(folder, f"{base} ({n}).step")
        n += 1
    used.add(path)
    return path


def _convert_split(input_path, output_path, opts, report, t0, bodies, info, tol, convert_one, convert_alt, verdicts=None,
                   on_swap=None, offered=None):
    """3MF "split to parts": every slicer part becomes its own STEP file
    (parts made of several separate solids keep them together)."""
    unit = "mm" if input_path.lower().endswith(".3mf") else opts.unit
    groups = {}
    for bi, p in enumerate(info.body_part):
        groups.setdefault(p, []).append(bi)
    order = sorted(groups)
    n_parts = len(order)
    total = max(1, len(bodies))
    done = 0
    used = set(o for o in opts.part_outputs if o)
    results = []
    for k, p in enumerate(order):
        name = info.part_names[p] if p < len(info.part_names) else f"Part {p + 1}"
        t_part = time.time()
        shapes, names, presets, part_bodies = [], [], [], []
        for bi in groups[p]:
            lo = 0.08 + 0.87 * done / total
            hi = 0.08 + 0.87 * (done + 1) / total
            shape, preset = convert_one(bi, bodies[bi], lo, hi, f" ({name}, part {k + 1}/{n_parts})")
            done += 1
            if shape is not None:
                shapes.append(shape)
                names.append(info.body_names[bi] if bi < len(info.body_names) else name)
                presets.append(preset)
                part_bodies.append(bi)
        if not shapes:
            report.warn(f"The part “{name}” couldn't be converted and was left out.")
            continue
        shape = shapes[0] if len(shapes) == 1 else make_compound(shapes)
        valid = stepio.is_valid(shape) if opts.validate else None
        items = [(shape, name)] if len(shapes) == 1 else list(zip(shapes, names))
        if valid is False:
            fixed = fix_shape(shape, tol)
            if stepio.is_valid(fixed):
                shape, valid = fixed, True
                items = [(shape, name)]
        out = opts.part_outputs[p] if p < len(opts.part_outputs) and opts.part_outputs[p] else _part_output_path(output_path, name, used)
        part_mesh_vol = sum(s.volume for bi in groups[p] for s in bodies[bi] if s.closed)
        expected = sum(1 for bi in groups[p] if all(sh.closed for sh in bodies[bi]))

        def verify(path, expected=expected, part_mesh_vol=part_mesh_vol, n=len(groups[p])):
            return stepio.verify_step(path, expected_solids=expected, mesh_volume=part_mesh_vol, open_parts=n - expected)

        at = 0.08 + 0.87 * done / total
        write = _step_writer(name, unit, report)
        stages = ((f"Checking {name}.step", at, at), (f"Repairing {name}.step", at, at))
        items, check, repaired = _save_checked(items, out, write, verify, tol, report, stages)
        if opts.validate:
            items, presets, check, more = _fix_broken_parts(
                items, part_bodies if len(items) == len(shapes) else [], presets, bodies, out, check,
                write, verify, convert_alt, tol, report, stages, on_swap)
            if more:
                repaired = more + [st_ for st_ in repaired if st_ not in more]
        if repaired:
            shape = items[0][0] if len(items) == 1 else make_compound([sh for sh, _ in items])
        if opts.validate and "valid" in check:
            valid = check["valid"]
        prev = opts.part_previews[p] if p < len(opts.part_previews) and opts.part_previews[p] else None
        if prev:
            try:
                P = np.vstack([s.V for bi in groups[p] for s in bodies[bi]])
                stepio.export_preview(shape, prev, float(np.linalg.norm(P.max(0) - P.min(0))))
            except Exception as e:
                report.warn(f"The 3D preview couldn't be made ({e}); the STEP file itself is fine.")
                prev = None
        st = stepio.shape_stats(shape)
        r = {
            "index": k,
            "part": p,
            "name": name,
            "output": out,
            "preview": prev,
            "preset": presets[0] if all(x == presets[0] for x in presets) else "mixed",
            "valid": valid,
            "solids": st["solids"],
            "shells": st["shells"],
            "faces": st["faces"],
            "edges": st["edges"],
            "volume": stepio.volume(shape) if st["solids"] else 0.0,
            "mesh_volume": part_mesh_vol,
            "file_size": os.path.getsize(out),
            "seconds": round(time.time() - t_part, 2),
            "triangles": info.part_triangles[p] if p < len(info.part_triangles) else 0,
            "fidelity": fidelity_summary([verdicts[bi] for bi in groups[p] if verdicts and bi in verdicts]),
            "checked": check["checked"],
            "check_ok": check["ok"],
            "checks": check["issues"],
            "repaired": repaired,
        }
        if offered and any(bi in offered for bi in groups[p]):
            P = np.vstack([s.V for bi in groups[p] for s in bodies[bi]])
            r["alternative"] = alternative_version(
                items, part_bodies if len(items) == len(part_bodies) else [], offered, verdicts or {}, out, prev,
                write, verify, float(np.linalg.norm(P.max(0) - P.min(0))), report)
        report.part(r)
        results.append(r)
    if not results:
        raise ConversionError("None of the parts could be converted.")
    presets = [r["preset"] for r in results]
    res = {
        "output": results[0]["output"],
        "outputs": [r["output"] for r in results],
        "split": True,
        "parts": results,
        "preset": presets[0] if all(x == presets[0] for x in presets) else "mixed",
        "valid": (all(r["valid"] is not False for r in results)) if opts.validate else None,
        "solids": sum(r["solids"] for r in results),
        "shells": sum(r["shells"] for r in results),
        "faces": sum(r["faces"] for r in results),
        "edges": sum(r["edges"] for r in results),
        "volume": sum(r["volume"] for r in results),
        "mesh_volume": sum(r["mesh_volume"] for r in results),
        "file_size": sum(r["file_size"] for r in results),
        "seconds": round(time.time() - t0, 2),
        "input": info.as_dict(),
        "mesh_check": info.mesh_check,
        "tolerance": tol,
        "checked": all(r["checked"] for r in results),
        "check_ok": all(r["check_ok"] for r in results),
        "checks": [{**i, "message": f"{r['name']}: {i['message']}"} for r in results for i in r["checks"]],
        "repaired": repair_steps(results),
        "fidelity": fidelity_summary([r["fidelity"] for r in results if r.get("fidelity")]),
    }
    report.result(res)
    return res


def convert_body_exact(B, body, tol, report):
    built = []
    flags = []
    all_faces = []
    failed = False
    for k, shell in enumerate(body):
        asm, faces = build_faceted_shell(shell, tol, report, progress=report.progress)
        if asm.failed_regions:
            failed = True
        all_faces.extend(faces)
        built.append(asm.shell_from_faces(faces))
        flags.append(shell.closed)
    if failed:
        report.warn("Falling back to sewing for this body.")
        sewn = sew_faces(all_faces, tol * 10)
        parts = shells_to_solid(sewn)
        return parts[0] if len(parts) == 1 else make_compound(parts)
    return assemble_body(B, built, flags, tol, report)


def swept_share(curved, max_tris=60_000, max_deg=3.0):
    """Share of curved area (list of (centres, normals, areas)) whose
    normals are square to one rigid motion (see sweep.py)."""
    from .sweep import fit_motion

    C = np.vstack([c for c, _, _ in curved]) if curved else np.zeros((0, 3))
    N = np.vstack([n for _, n, _ in curved]) if curved else np.zeros((0, 3))
    A = np.concatenate([a for _, _, a in curved]) if curved else np.zeros(0)
    if len(C) < 50 or A.sum() <= 0:
        return 0.0
    idx = np.arange(len(C))
    if len(idx) > max_tris:
        idx = np.random.default_rng(0).choice(len(C), max_tris, replace=False, p=A / A.sum())
    # a robust fit: parts that aren't swept (a gear's tabs, its holes)
    # pull a plain fit off, so the triangles that agree are kept and the
    # motion fitted again on them
    sel = idx
    ok = None
    for _ in range(4):
        mot = fit_motion(C[sel], N[sel], A[sel], min_gap=1.0, max_rms=90.0)
        if mot is None:
            return 0.0
        V = mot.velocity(C)
        sp = np.maximum(np.linalg.norm(V, axis=1), 1e-30)
        ok = np.abs((N * V).sum(1)) / sp <= np.sin(np.radians(max_deg))
        sel = np.intersect1d(idx, np.nonzero(ok)[0])
        if len(sel) < 50:
            return 0.0
    return float(A[ok].sum() / A.sum())


def choose_preset_body(body, tol, quality, report, big=400_000):
    """CAD for parts made of planes/cylinders/etc., Smooth for organic ones.

    Flat faces are the hallmark of mechanical parts; organic shapes have
    none worth mentioning. Pure analytic shapes without flats (a ball, a
    ring) are recognised by one primitive fitting each shell. Returns
    (preset, None)."""
    tot = 0.0
    planar = 0.0
    curved = []  # (centres, normals, areas) of the triangles not on big flats
    for s in body:
        normals, areas = face_normals_areas(s.V, s.F)
        d = max(1e-9, float(np.linalg.norm(s.V.max(0) - s.V.min(0))))
        labels, planes = planar_regions(s.V, s.F, s.topo, normals, areas, 2e-5 * d)
        ar = np.bincount(labels, weights=areas)
        a_tot = areas.sum()
        flat = ar >= 0.002 * a_tot
        planar += ar[flat].sum()
        tot += a_tot
        rest = ~flat[labels]
        curved.append((s.V[s.F[rest]].mean(1), normals[rest], areas[rest]))
    frac = planar / max(tot, 1e-30)
    report.log(f"Flat faces cover {frac:.0%} of the surface")
    if frac >= 0.15:
        return "cad", None
    # Gear teeth, threads, a turned profile: few flats, but most of the
    # curved surface is swept along one motion (all teeth screw about the
    # gear's axis), which organic shapes never are
    from . import cad as _cad

    if _cad.FIND_SWEEPS:
        share = swept_share(curved)
        if share >= 0.6:
            report.log(f"{share:.0%} of the curved surface is swept along one motion (like gear teeth or a thread)")
            return "cad", None
    # Pure analytic shapes without flats (a ball, a ring, a pin, a hollow
    # sphere) are still CAD parts: one primitive explains each shell.
    from .fitting import Cone, Cylinder, Sphere, Torus

    def one_primitive(sh):
        idx = np.random.default_rng(0).choice(len(sh.V), min(len(sh.V), 3000), replace=False)
        P = sh.V[idx]
        normals, areas = face_normals_areas(sh.V, sh.F)
        VN = np.zeros_like(sh.V)
        for k in range(3):
            np.add.at(VN, sh.F[:, k], normals * areas[:, None])
        VN /= np.maximum(np.linalg.norm(VN, axis=1), 1e-30)[:, None]
        N = VN[idx]
        ext = float(np.linalg.norm(sh.V.max(0) - sh.V.min(0)))
        for cls in (Sphere, Cylinder, Cone, Torus):
            try:
                prim = cls.fit(P, N, ext=ext)
            except Exception:
                prim = None
            if prim is not None and not prim.degenerate(ext) and prim.distance(P).max() <= tol:
                return prim.kind
        return None

    if len(body) <= 4:
        kinds = [one_primitive(sh) for sh in body]
        if all(kinds):
            report.log("Each shell is a single " + "/".join(sorted(set(kinds))))
            return "cad", None
    return "smooth", None
