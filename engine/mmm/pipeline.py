"""Conversion pipeline and presets."""
import os
import time

import numpy as np

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import BRepBuilderAPI_Sewing
from OCP.ShapeFix import ShapeFix_Shape
from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS

from . import stepio
from .brep import BRepAssembler, PlanarSurface, make_compound, make_solid
from .meshio import EdgeTopology, face_normals_areas, load_and_prepare
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
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid

    out = []
    exp = TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        sh = TopoDS.Shell(exp.Current())
        exp.Next()
        if sh.Closed() or True:
            mk = BRepBuilderAPI_MakeSolid(sh)
            if mk.IsDone():
                out.append(mk.Solid())
                continue
        out.append(sh)
    return out


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


def convert(input_path, output_path, opts=None, report=NULL):
    opts = opts or Options()
    t0 = time.time()
    report.stage("Reading mesh", 0.0, 0.08)
    bodies, info = load_and_prepare(
        input_path, repair=opts.repair, remove_debris=opts.remove_debris, report=report, layout=opts.layout
    )
    diag = info.diag
    q = opts.quality
    req = opts.preset
    if req == "exact":
        req = "precise"  # faceted output was retired: everything is real surfaces
    tol = opts.tolerance if opts.tolerance else auto_tolerance(diag, req if req != "auto" else "cad", q)
    report.log(f"Model size {diag:.4g} {opts.unit} (diagonal), quality {int(round(q * 100))}%")

    def body_tol(body, preset):
        if opts.tolerance:
            return opts.tolerance
        P = np.vstack([s.V for s in body])
        d = float(np.linalg.norm(P.max(0) - P.min(0)))
        return auto_tolerance(d, preset, q)

    B = BRep_Builder()
    shapes = []
    shape_names = []
    used_presets = []
    stats_extra = {}
    body_names = info.body_names or [None] * len(bodies)
    n_bodies = len(bodies)

    def convert_one(bi, body, lo, hi, label):
        """Convert one body; returns (shape or None, preset used)."""
        preset = req
        segs = None
        body_opts = opts
        if preset in ("auto", "precise"):
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
        if preset in ("cad", "precise"):
            from .cad import convert_body_cad

            report.stage(("Recognising CAD features" if preset == "cad" else "Fitting precise surfaces") + label, lo, hi)
            shape = convert_body_cad(B, body, btol, body_opts, report, segs=segs)
            if shape is not None and opts.validate and not stepio.is_valid(shape):
                # Try smooth surfaces as an alternative, but only take them if
                # they are valid *and* accurate; otherwise the (nearly valid)
                # CAD model is the more useful result.
                from .smooth import convert_body_smooth

                report.log("CAD model of this part needs healing; trying smooth surfaces as an alternative")
                report.stage("Fitting smooth surfaces" + label, lo, hi)
                try:
                    alt = convert_body_smooth(B, body, btol, body_opts, report)
                except Exception as e:
                    alt = None
                    report.log(f"Smooth surfaces didn't work either ({e})")
                mesh_vol = sum(sh.volume for sh in body if sh.closed)
                ok_alt = False
                if alt is not None and stepio.is_valid(alt) and abs(mesh_vol) > 0:
                    ok_alt = abs(stepio.volume(alt) / mesh_vol - 1.0) <= 0.02
                if ok_alt:
                    shape = alt
                    preset = "smooth"
                    report.warn("One part was saved with smooth surfaces instead of CAD faces, because its CAD model had errors.")
                else:
                    report.warn("One part's CAD model has small errors. Fusion and most CAD programs repair these automatically when importing.")
        elif preset == "smooth":
            from .smooth import convert_body_smooth

            report.stage("Fitting smooth surfaces" + label, lo, hi)
            shape = convert_body_smooth(B, body, btol, body_opts, report)
        else:
            raise ValueError(f"unknown preset {preset}")
        return shape, preset

    if opts.split and info.parts > 1 and len(info.body_part) == n_bodies:
        return _convert_split(input_path, output_path, opts, report, t0, bodies, info, tol, convert_one)

    for bi, body in enumerate(bodies):
        lo = 0.1 + 0.75 * bi / n_bodies
        hi = 0.1 + 0.75 * (bi + 1) / n_bodies
        label = f" (body {bi + 1}/{n_bodies})" if n_bodies > 1 else ""
        shape, preset = convert_one(bi, body, lo, hi, label)
        if shape is not None:
            shapes.append(shape)
            shape_names.append(body_names[bi] if bi < len(body_names) else None)
            used_presets.append(preset)
    preset = used_presets[0] if used_presets and all(p == used_presets[0] for p in used_presets) else "mixed"

    if not shapes:
        raise ConversionError("None of the parts could be converted.")
    result = shapes[0] if len(shapes) == 1 else make_compound(shapes)

    report.stage("Checking result", 0.85, 0.9)
    valid = stepio.is_valid(result) if opts.validate else None
    if valid is False:
        report.log("The result had errors, so automatic repair was run on it.")
        fixed = fix_shape(result, tol)
        if stepio.is_valid(fixed):
            result = fixed
            valid = True
    st = stepio.shape_stats(result)
    vol = stepio.volume(result) if st["solids"] else 0.0
    mesh_vol = sum(s.volume for b in bodies for s in b if s.closed)

    report.stage("Writing STEP", 0.9, 0.97)
    name = opts.product_name or os.path.splitext(os.path.basename(input_path))[0]
    if len(shapes) > 1 and valid is not False:
        try:
            stepio.write_step_named(list(zip(shapes, shape_names)), output_path, product_name=name, unit=opts.unit)
        except Exception as e:
            report.log(f"Couldn't store part names in the STEP file ({e}); it was saved without them.")
            stepio.write_step(result, output_path, product_name=name, unit=opts.unit)
    else:
        stepio.write_step(result, output_path, product_name=name, unit=opts.unit)

    report.stage("Checking the STEP file", 0.93, 0.97)
    expected = sum(1 for b in bodies if b and all(sh.closed for sh in b))
    check = stepio.verify_step(output_path, expected_solids=expected, mesh_volume=mesh_vol,
                               open_parts=len(bodies) - expected)

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
        "tolerance": tol,
        "checked": check["checked"],
        "check_ok": check["ok"],
        "checks": check["issues"],
    }
    res.update(stats_extra)
    report.result(res)
    return res


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


def _convert_split(input_path, output_path, opts, report, t0, bodies, info, tol, convert_one):
    """3MF "split to parts": every slicer part becomes its own STEP file
    (parts made of several separate solids keep them together)."""
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
        shapes, names, presets = [], [], []
        for bi in groups[p]:
            lo = 0.08 + 0.87 * done / total
            hi = 0.08 + 0.87 * (done + 1) / total
            shape, preset = convert_one(bi, bodies[bi], lo, hi, f" ({name}, part {k + 1}/{n_parts})")
            done += 1
            if shape is not None:
                shapes.append(shape)
                names.append(info.body_names[bi] if bi < len(info.body_names) else name)
                presets.append(preset)
        if not shapes:
            report.warn(f"The part “{name}” couldn't be converted and was left out.")
            continue
        shape = shapes[0] if len(shapes) == 1 else make_compound(shapes)
        valid = stepio.is_valid(shape) if opts.validate else None
        if valid is False:
            fixed = fix_shape(shape, tol)
            if stepio.is_valid(fixed):
                shape, valid = fixed, True
        out = opts.part_outputs[p] if p < len(opts.part_outputs) and opts.part_outputs[p] else _part_output_path(output_path, name, used)
        try:
            items = [(shape, name)] if len(shapes) == 1 else list(zip(shapes, names))
            stepio.write_step_named(items, out, product_name=name, unit=opts.unit)
        except Exception as e:
            report.log(f"Couldn't store part names in the STEP file ({e}); it was saved without them.")
            stepio.write_step(shape, out, product_name=name, unit=opts.unit)
        prev = opts.part_previews[p] if p < len(opts.part_previews) and opts.part_previews[p] else None
        if prev:
            try:
                P = np.vstack([s.V for bi in groups[p] for s in bodies[bi]])
                stepio.export_preview(shape, prev, float(np.linalg.norm(P.max(0) - P.min(0))))
            except Exception as e:
                report.warn(f"The 3D preview couldn't be made ({e}); the STEP file itself is fine.")
                prev = None
        part_mesh_vol = sum(s.volume for bi in groups[p] for s in bodies[bi] if s.closed)
        report.stage(f"Checking {name}.step", 0.08 + 0.87 * done / total, 0.08 + 0.87 * done / total)
        expected = sum(1 for bi in groups[p] if all(sh.closed for sh in bodies[bi]))
        check = stepio.verify_step(out, expected_solids=expected, mesh_volume=part_mesh_vol,
                                   open_parts=len(groups[p]) - expected)
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
            "checked": check["checked"],
            "check_ok": check["ok"],
            "checks": check["issues"],
        }
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
        "tolerance": tol,
        "checked": all(r["checked"] for r in results),
        "check_ok": all(r["check_ok"] for r in results),
        "checks": [{**i, "message": f"{r['name']}: {i['message']}"} for r in results for i in r["checks"]],
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


def choose_preset_body(body, tol, quality, report, big=400_000):
    """CAD for parts made of planes/cylinders/etc., Smooth for organic ones.

    Flat faces are the hallmark of mechanical parts; organic shapes have
    none worth mentioning. Pure analytic shapes without flats (a ball, a
    ring) are recognised by a few surfaces explaining almost everything.
    Returns (preset, segmentations); the CAD segmentation computed for the
    decision is handed on so it isn't done twice."""
    from .cad import Segmenter

    n = sum(len(s.F) for s in body)
    tot = 0.0
    planar = 0.0
    for s in body:
        normals, areas = face_normals_areas(s.V, s.F)
        d = max(1e-9, float(np.linalg.norm(s.V.max(0) - s.V.min(0))))
        labels, planes = planar_regions(s.V, s.F, s.topo, normals, areas, 2e-5 * d)
        ar = np.bincount(labels, weights=areas)
        a_tot = areas.sum()
        planar += ar[ar >= 0.002 * a_tot].sum()
        tot += a_tot
    frac = planar / max(tot, 1e-30)
    report.log(f"Flat faces cover {frac:.0%} of the surface")
    if frac >= 0.15:
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
    segs = {}
    covered = 0.0
    nreg = 0
    for s in body:
        seg = Segmenter(s.V, s.F, s.topo, tol, report)
        seg.detail = quality
        regs = seg.run()
        segs[id(s)] = seg
        for r in regs:
            if r.kind != "freeform":
                covered += seg.fa[r.faces].sum()
                nreg += 1
    cov = covered / max(tot, 1e-30)
    report.log(f"Analytic surfaces cover {cov:.0%} of the area with {nreg} faces")
    if cov >= 0.9 and nreg <= 60:
        return "cad", segs
    return "smooth", None
