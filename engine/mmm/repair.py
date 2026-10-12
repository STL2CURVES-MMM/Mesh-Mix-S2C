"""Automatic repair of a saved STEP file.

CAD programs such as Fusion, SolidWorks and Onshape heal small errors when
they import a STEP file, but many other programs (slicers, CAM, viewers,
online converters) don't. So when reading the saved file back finds
problems, the engine repairs it the same way and saves it again:

1. Open the saved file and save it again, as a CAD program would. Reading
   rebuilds the tolerances and the curves on every face; on its own this
   clears most "structure" errors.
2. Shape healing (OpenCASCADE's ShapeFix): tolerances, small edges, wires
   that cross themselves, face and shell orientation.
3. Sewing: closes small gaps between neighbouring faces and turns closed
   shells back into solids.

Every step is kept only if the file reads back better than before, with
the same number of solids and the same volume, so a repair never makes a
file worse. Nothing runs when the first check finds no problems.
"""
import os
import shutil
import tempfile

from . import stepio
from .report import NULL

# problems these steps can fix (holes in the mesh or missing pieces need a
# different conversion instead)
REPAIRABLE = {"invalid", "bad_faces", "gaps", "inside_out", "not_solid", "missing_solids"}


def needs_repair(check):
    return bool(check.get("checked")) and any(i["code"] in REPAIRABLE for i in check["issues"])


def _score(check):
    """Lower is better."""
    issues = check["issues"]
    return (
        sum(1 for i in issues if i["level"] == "error"),
        sum(1 for i in issues if i["code"] in REPAIRABLE),
        check.get("bad_faces", 0),
        check.get("open_edges", 0),
        0 if check.get("valid") else 1,
    )


def _better(new, old):
    if _score(new) >= _score(old):
        return False
    if new.get("solids", 0) < old.get("solids", 0):
        return False
    vn, vo = new.get("volume"), old.get("volume")
    if vn and vo and abs(vn / vo - 1.0) > 0.001:
        return False  # a repair must not change the shape (or fill a cavity)
    return True


def _reread(items, path, tol):
    """The saved file as a CAD program reads it (whole, parts and all)."""
    read = stepio.read_step_named(path)
    if len(read) == len(items):
        read = [(shape, name) for (shape, _), (_, name) in zip(read, items)]
    return read


def _heal(items, path, tol):
    from OCP.ShapeFix import ShapeFix_Shape
    from OCP.TopAbs import TopAbs_SOLID

    out = []
    for shape, name in items:
        sf = ShapeFix_Shape(shape)
        sf.SetPrecision(tol)
        sf.SetMaxTolerance(max(tol * 10, 1e-3))
        sf.Perform()
        fixed = sf.Shape()
        # healing can turn a nearly valid solid into a "valid" open shell
        if stepio.count(fixed, TopAbs_SOLID) < stepio.count(shape, TopAbs_SOLID):
            fixed = shape
        out.append((fixed, name))
    return out


def _sew(items, path, tol):
    from OCP.BRep import BRep_Builder
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
    from OCP.ShapeFix import ShapeFix_Solid
    from OCP.TopAbs import TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS, TopoDS_Compound

    out = []
    for shape, name in items:
        sew = BRepBuilderAPI_Sewing(tol)
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        while exp.More():
            sew.Add(exp.Current())
            exp.Next()
        sew.Perform()
        sewn = sew.SewedShape()
        B = BRep_Builder()
        comp = TopoDS_Compound()
        B.MakeCompound(comp)
        exp = TopExp_Explorer(sewn, TopAbs_SHELL)
        while exp.More():
            sh = TopoDS.Shell(exp.Current())
            exp.Next()
            mk = BRepBuilderAPI_MakeSolid(sh)
            if mk.IsDone():
                fix = ShapeFix_Solid(mk.Solid())
                fix.Perform()
                B.Add(comp, fix.Solid())
            else:
                B.Add(comp, sh)
        if stepio.count(comp, TopAbs_SOLID) < stepio.count(shape, TopAbs_SOLID):
            comp = shape
        out.append((comp, name))
    return out


STEPS = (("read back and saved again", _reread), ("shape healing", _heal), ("sewing", _sew))


def repair(items, path, check, write, verify, tol, report=NULL):
    """Repairs the STEP file at ``path`` (written from ``items``, a list of
    (shape, name)) when ``check`` found problems.

    write(items, path) saves items; verify(path) reads a file back and
    checks it. Returns (items, check, steps): the best version found (the
    file at ``path`` is replaced only by a better one) and the names of the
    repair steps it took."""
    done = []
    if not needs_repair(check):
        return items, check, done
    tol = max(float(tol), 1e-6)
    tmpdir = tempfile.mkdtemp(prefix="mmm-repair-")
    try:
        for label, step in STEPS:
            if not needs_repair(check):
                break
            try:
                cand = step(items, path, tol)
                cpath = os.path.join(tmpdir, "candidate.step")
                write(cand, cpath)
                c = verify(cpath)
            except Exception as e:
                report.log(f"Repair step '{label}' didn't work ({e})")
                continue
            if _better(c, check):
                part = path + ".part"
                shutil.copyfile(cpath, part)
                os.replace(part, path)
                items, check = cand, c
                done.append(label)
                report.log(f"Repair: {label} - " + (", ".join(i["code"] for i in c["issues"]) or "no problems left"))
            else:
                report.log(f"Repair: {label} didn't help")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)
    return items, check, done
