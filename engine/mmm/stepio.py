"""STEP writing, validation and preview export."""
# Part of Manifold Mesh Mix (c) 2026 Tuna-The-First and Claude, AGPL-3.0, commit 4cf72ea.
# Modified 2026-11-04: messages and file metadata users see name the current program (Mesh Mix-S2C).
# Modified 2026-11-03 for the stl2curves merged engine: spindle tori and surfaces of revolution are saved
# as B-spline surfaces (_readable_everywhere), because Fusion misreads what OCC's writer makes of them.
# Modified 2026-11-02 for the stl2curves merged engine: verify_step also says whether the file
# reads back as sound solids (open_shells, sound), final_word turns that into the report's
# valid and an error the user sees, and open_up saves what doesn't close as OPEN_SHELL: a
# file of 2 tiny solids and 3 open shells, written as 5 CLOSED_SHELL, came back valid.
import os
# Modified 2026-11-02: the mesher goes through meshguard; broad handlers call faults.caught.
from . import faults
from . import meshguard
import struct

import numpy as np

from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_RetDone
from OCP.Interface import Interface_Static
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shell

def quiet_occ():
    """Silence OpenCASCADE's console chatter (it would corrupt JSON output)."""
    try:
        from OCP.Message import Message, Message_Gravity

        for pr in list(Message.DefaultMessenger_s().Printers()):
            pr.SetTraceLevel(Message_Gravity.Message_Fail)
    except Exception as fault:
        faults.caught(fault)


quiet_occ()

# Creating the first STEP writer initialises the translator and resets all
# write.* parameters to their defaults, so do it once up front; settings
# made afterwards (no pcurves, units, product name) then actually apply.
STEPControl_Writer()

# Write each edge's 2D curve on its faces too. They are optional in STEP,
# but readers that rebuild them by projection can trim small faces
# differently from the original; with them the model reads back exactly.
# Costs a larger file.
PCURVES = True

UNIT_NAMES = {"mm": "MM", "cm": "CM", "m": "M", "in": "INCH", "inch": "INCH", "um": "UM", "ft": "FT"}


def count(shape, kind):
    """Number of distinct sub-shapes of a kind (shared edges counted once)."""
    try:
        from OCP.TopExp import TopExp
        from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as _Map

        m = _Map()
        TopExp.MapShapes_s(shape, kind, m)
        return int(m.Size())
    except Exception as fault:
        faults.caught(fault)
        exp = TopExp_Explorer(shape, kind)
        n = 0
        while exp.More():
            n += 1
            exp.Next()
        return n


def shape_stats(shape):
    return {
        "solids": count(shape, TopAbs_SOLID),
        "shells": count(shape, TopAbs_SHELL),
        "faces": count(shape, TopAbs_FACE),
        "edges": count(shape, TopAbs_EDGE),
    }


def is_valid(shape):
    try:
        return bool(BRepCheck_Analyzer(shape).IsValid())
    except Exception as fault:
        faults.caught(fault)
        return False


def volume(shape, eps=1e-5):
    # adaptive integration: the default fixed-order rule is inaccurate on
    # trimmed periodic faces (tori, cones with seams)
    p = GProp_GProps()
    try:
        BRepGProp.VolumeProperties_s(shape, p, eps)
    except Exception as fault:
        faults.caught(fault)
        BRepGProp.VolumeProperties_s(shape, p)
    return float(p.Mass())


def area(shape, eps=1e-5):
    p = GProp_GProps()
    try:
        BRepGProp.SurfaceProperties_s(shape, p, eps)
    except Exception as fault:
        faults.caught(fault)
        BRepGProp.SurfaceProperties_s(shape, p)
    return float(p.Mass())


# metres per unit of the model's numbers
METRES_PER_UNIT = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048, "um": 1e-6}


_model_unit = "mm"


def set_model_unit(unit):
    """The unit the model's numbers are in. STEP files are written in it
    (the numbers unchanged, labelled with the unit), and files read back to
    check them come back in it, so they compare with the mesh."""
    global _model_unit
    _model_unit = unit if unit in METRES_PER_UNIT else "mm"


def _read_in_model_unit():
    # set right before reading: creating the first reader resets it
    Interface_Static.SetCVal_s("xstep.cascade.unit", UNIT_NAMES[_model_unit])


def _revolved_when_written(shape):
    """Whether the file would hold a surface of revolution. OpenCascade's writer makes one of any torus
    wider than its ring (a spindle torus, R < r), with a spline for its profile."""
    from OCP.BRep import BRep_Tool
    from OCP.Geom import Geom_RectangularTrimmedSurface, Geom_SurfaceOfRevolution, Geom_ToroidalSurface
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        s = BRep_Tool.Surface_s(TopoDS.Face(ex.Current()))
        ex.Next()
        if isinstance(s, Geom_RectangularTrimmedSurface):
            s = s.BasisSurface()
        if isinstance(s, Geom_SurfaceOfRevolution) or (isinstance(s, Geom_ToroidalSurface) and s.MajorRadius() < s.MinorRadius()):
            return True
    return False


def _readable_everywhere(shape):
    """The shape with what would be saved as a surface of revolution made a B-spline surface instead, the
    same shape exactly. Fusion turns a SURFACE_OF_REVOLUTION back into a torus as it imports it and can get
    that badly wrong where OpenCascade reads it correctly, so no check of ours sees it: stl2step's test14,
    127,622 mm3, had a volume of 358,236 in Fusion from one such face of 62 mm2, and 127,539 with it as a
    B-spline. ShapeCustom converts surfaces of revolution but not tori, so the shape goes through the writer
    first. Call this before setting the writer's unit: the file in between is plain millimetres, read straight
    back, which leaves the numbers as they are in any unit. A shape without such a surface comes back
    untouched, so its file doesn't change."""
    if not _revolved_when_written(shape):
        return shape
    import tempfile

    from OCP.ShapeCustom import ShapeCustom

    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1 if PCURVES else 0)
    fd, tmp = tempfile.mkstemp(suffix=".step")
    os.close(fd)
    try:
        w = STEPControl_Writer()
        if w.Transfer(shape, STEPControl_AsIs) != IFSelect_RetDone or w.Write(tmp) != IFSelect_RetDone:
            raise RuntimeError("STEP write failed on the way to B-spline surfaces")
        r = STEPControl_Reader()
        if r.ReadFile(tmp) != IFSelect_RetDone or not r.TransferRoots():
            raise RuntimeError("STEP read failed on the way to B-spline surfaces")
        return ShapeCustom.ConvertToBSpline_s(r.OneShape(), False, True, False, False)
    finally:
        os.remove(tmp)


def write_step_named(items, path, product_name="Part", unit="mm"):
    """Write [(shape, name)] as named parts (an assembly when there are
    several), so CAD browsers show meaningful part names."""
    items = [(_readable_everywhere(s), n) for s, n in items]
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    Interface_Static.SetCVal_s("write.step.schema", "AP214IS")
    Interface_Static.SetCVal_s("write.step.unit", UNIT_NAMES.get(unit, "MM"))
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1 if PCURVES else 0)
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    # the document's numbers are in the model's unit (without this they'd
    # be taken as millimetres and scaled)
    XCAFDoc_DocumentTool.SetLengthUnit_s(doc, METRES_PER_UNIT.get(unit, 0.001))
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    if len(items) == 1:
        lab = st.AddShape(items[0][0], False)
        TDataStd_Name.Set_s(lab, TCollection_ExtendedString(_clean_name(items[0][1] or product_name), True))
    else:
        asm = st.NewShape()
        TDataStd_Name.Set_s(asm, TCollection_ExtendedString(_clean_name(product_name), True))
        used = {}
        for shape, name in items:
            nm = _clean_name(name or "Part")
            used[nm] = used.get(nm, 0) + 1
            if used[nm] > 1:
                nm = f"{nm} ({used[nm]})"
            lab = st.AddShape(shape, False)
            TDataStd_Name.Set_s(lab, TCollection_ExtendedString(nm, True))
            st.AddComponent(asm, lab, TopLoc_Location())
        st.UpdateAssemblies()
    w = STEPCAFControl_Writer()
    w.SetNameMode(True)
    w.SetColorMode(False)
    w.SetLayerMode(False)
    if not w.Transfer(doc, STEPControl_AsIs):
        raise RuntimeError("STEP transfer failed")
    tmp = path + ".part"
    st_ = w.Write(tmp)
    if st_ != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed ({st_})")
    _patch_header(tmp, product_name)
    os.replace(tmp, path)


def read_step_named(path):
    """Read a STEP file the way write_step_named writes it: [(shape, part
    name)], one entry per part of an assembly (placed where it sits)."""
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDF import TDF_Label
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
    from OCP.collections import Sequence_TDF_Label

    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.InitDocument(doc)
    r = STEPCAFControl_Reader()
    r.SetNameMode(True)
    r.SetColorMode(False)
    r.SetLayerMode(False)
    _read_in_model_unit()
    XCAFDoc_DocumentTool.SetLengthUnit_s(doc, METRES_PER_UNIT[_model_unit])
    if r.ReadFile(path) != IFSelect_RetDone or not r.Transfer(doc):
        raise RuntimeError("STEP read failed")
    tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def name_of(lab):
        attr = TDataStd_Name()
        if lab.FindAttribute(TDataStd_Name.GetID_s(), attr):
            return attr.Get().ToExtString()
        return None

    items = []
    free = Sequence_TDF_Label()
    tool.GetFreeShapes(free)
    for i in range(1, free.Length() + 1):
        lab = free.Value(i)
        if XCAFDoc_ShapeTool.IsAssembly_s(lab):
            comps = Sequence_TDF_Label()
            XCAFDoc_ShapeTool.GetComponents_s(lab, comps, False)
            for j in range(1, comps.Length() + 1):
                comp = comps.Value(j)
                ref = TDF_Label()
                XCAFDoc_ShapeTool.GetReferredShape_s(comp, ref)
                items.append((XCAFDoc_ShapeTool.GetShape_s(comp), name_of(ref) or name_of(comp)))
        else:
            items.append((XCAFDoc_ShapeTool.GetShape_s(lab), name_of(lab)))
    if not items:
        raise RuntimeError("the STEP file has no shapes")
    return items


def _clean_name(s):
    s = str(s).strip() or "Part"
    return "".join(ch for ch in s if ch.isprintable())[:120]


def write_step(shape, path, product_name="Part", unit="mm", schema="AP214"):
    shape = _readable_everywhere(shape)
    Interface_Static.SetCVal_s("write.step.schema", "AP214IS" if schema == "AP214" else schema)
    Interface_Static.SetCVal_s("write.step.unit", UNIT_NAMES.get(unit, "MM"))
    Interface_Static.SetIVal_s("write.surfacecurve.mode", 1 if PCURVES else 0)
    try:
        Interface_Static.SetCVal_s("write.step.product.name", product_name)
    except Exception as fault:
        faults.caught(fault)
    w = STEPControl_Writer()
    try:
        w.Model().SetWriteLengthUnit(1.0)
    except Exception as fault:
        faults.caught(fault)
    st = w.Transfer(shape, STEPControl_AsIs)
    if st != IFSelect_RetDone:
        raise RuntimeError(f"STEP transfer failed ({st})")
    tmp = path + ".part"
    st = w.Write(tmp)
    if st != IFSelect_RetDone:
        raise RuntimeError(f"STEP write failed ({st})")
    _patch_header(tmp, product_name)
    os.replace(tmp, path)


def _patch_header(path, product_name):
    """Replace OpenCASCADE's generic header strings with our own."""
    with open(path, "rb") as fh:
        data = fh.read()
    head_end = data.find(b"ENDSEC;")
    if head_end < 0:
        return
    head = data[:head_end].decode("latin-1")
    head = head.replace("'Open CASCADE Model'", "'Converted from mesh by Mesh Mix-S2C'")
    head = head.replace("'Open CASCADE Shape Model'", "'" + _step_str(product_name) + "'")
    data = head.encode("latin-1") + data[head_end:]
    with open(path, "wb") as fh:
        fh.write(data)


def _step_str(s):
    s = "".join(ch if 32 <= ord(ch) < 127 else "_" for ch in s)
    return s.replace("'", "''")


def read_step(path):
    r = STEPControl_Reader()
    _read_in_model_unit()
    st = r.ReadFile(path)
    if st != IFSelect_RetDone:
        raise RuntimeError("STEP read failed")
    r.TransferRoots()
    return r.OneShape()


# ----------------------------------------------------------------------------
# Checking a written STEP file
# ----------------------------------------------------------------------------


def _issue(code, level, message):
    return {"code": code, "level": level, "message": message}


SOUND_VOLUME = 0.05  # share of the mesh's volume a solid may differ by, on both figures, before it is called unsound


def _shells(shape):
    out, exp = [], TopExp_Explorer(shape, TopAbs_SHELL)
    while exp.More():
        out.append(exp.Current())
        exp.Next()
    return out


def _tessellated_volume(shape):
    """The volume of a coarse tessellation of the shape's closed shells: a second figure, right
    where the exact integral goes astray on faces whose edges stray."""
    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    size = box.CornerMin().Distance(box.CornerMax())
    if meshguard.mesh(shape, max(size * 1e-3, 1e-4), False, 0.3, True).unmeshed:
        raise meshguard.Stuck("a face couldn't be meshed, so the sum over the triangles would be short")
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p, True, False, True)
    return float(p.Mass())


def final_word(check, path, open_parts=0):
    """What the report says of the saved file, once every repair has been tried: check with
    "valid" false, "ok" false and an error for the user if the file isn't sound solids (see
    verify_step's "sound"), or if its volume is far from the mesh's on both the exact and a
    tessellated figure. The volume warning goes where it says nothing: its comparison takes
    the whole file against the mesh's closed pieces only (open_parts), which read 16,000,000%
    on a Pi case, and where only the exact integral is far.

    It runs last because verify_step's result decides the repairs, and those must not change."""
    if not check.get("checked") or "sound" not in check:
        return check
    out = dict(check)
    issues = list(check["issues"])
    sound = bool(check["sound"])
    if any(i["code"] == "volume" for i in issues):
        try:
            r = None if open_parts else _tessellated_volume(read_step(path)) / abs(check["mesh_volume"])
        except Exception as fault:
            faults.caught(fault)
            r = None
        if open_parts or (r is not None and abs(r - 1.0) <= SOUND_VOLUME):
            issues = [i for i in issues if i["code"] != "volume"]
        else:
            sound = False
    solids, opened = check.get("solids", 0), check.get("open_shells", 0)
    out["sound"] = sound
    if not sound:
        edges = check.get("open_edges", 0)
        gaps = f" ({edges} open edge{'s' if edges != 1 else ''})" if edges else ""
        if not solids:
            word = "This is not a solid: the file holds only surfaces that don't close up" + gaps + ", with no volume."
        elif opened:
            word = (f"This is not a sound solid: {opened} of its {check.get('shells', opened)} shells "
                    + ("doesn't" if opened == 1 else "don't") + " close up" + gaps
                    + f", and only {solids} solid{'s' if solids != 1 else ''} read{'s' if solids == 1 else ''} back, "
                    "so its volume and any Boolean with it can't be trusted.")
        elif check.get("expected_solids", 0) > solids:
            word = f"This is not a sound result: only {solids} of {check['expected_solids']} parts read back as solids."
        else:
            word = "This is not a sound solid: its volume is far from the mesh's, on both ways of measuring it."
        issues.append(_issue("not_sound", "error", word + " Check it in your CAD program before using it."))
        out["valid"] = False
        out["ok"] = False
    out["issues"] = issues
    return out


def open_up(shape):
    """The shape with every solid whose shell doesn't close taken apart into open shells, so the
    STEP writer saves OPEN_SHELL for them rather than a CLOSED_SHELL that isn't (the writer
    believes the shape's type and flag, and the reader then quietly drops the solid). The same
    faces; a shape with nothing to take apart comes back as it is."""
    keep, changed = [], False
    B = BRep_Builder()
    exp = TopExp_Explorer(shape, TopAbs_SOLID)
    while exp.More():
        solid = exp.Current()
        if all(BRep_Tool.IsClosed_s(sh) for sh in _shells(solid)):
            keep.append(solid)
        else:
            changed = True
            for sh in _shells(solid):
                new = TopoDS_Shell()
                B.MakeShell(new)
                f = TopExp_Explorer(sh, TopAbs_FACE)
                while f.More():
                    B.Add(new, f.Current())
                    f.Next()
                new.Closed(False)
                keep.append(new)
        exp.Next()
    if not changed:
        return shape
    exp = TopExp_Explorer(shape, TopAbs_SHELL, TopAbs_SOLID)
    while exp.More():
        keep.append(exp.Current())
        exp.Next()
    exp = TopExp_Explorer(shape, TopAbs_FACE, TopAbs_SHELL)
    while exp.More():
        keep.append(exp.Current())
        exp.Next()
    out = TopoDS_Compound()
    B.MakeCompound(out)
    for k in keep:
        B.Add(out, k)
    return out


def verify_step(path, expected_solids=1, mesh_volume=0.0, open_parts=0):
    """Read a STEP file back the way a CAD program would and check it.

    Returns {"checked": True, "ok": bool, "issues": [...], "solids", ...}.
    Every issue has a code, a level ("error" or "warning") and a message
    written for the user."""
    from OCP.ShapeAnalysis import ShapeAnalysis_FreeBounds

    out = {"checked": True, "ok": True, "issues": []}
    issues = out["issues"]
    try:
        shape = read_step(path)
    except Exception as fault:
        faults.caught(fault)
        issues.append(_issue("unreadable", "error",
                             "The saved STEP file couldn't be opened again, so it may be damaged. Convert it again."))
        out["ok"] = False
        return out
    st = shape_stats(shape)
    out.update(solids=st["solids"], faces=st["faces"], shells=st["shells"], expected_solids=expected_solids,
               mesh_volume=mesh_volume)
    # (what a CAD program can use as a solid: BRepCheck passes open shells, so "valid" alone said
    # nothing of them. This doesn't touch "issues": they decide the repairs. See final_word)
    out["open_shells"] = sum(not BRep_Tool.IsClosed_s(sh) for sh in _shells(shape))
    out["sound"] = bool(st["solids"]) and not out["open_shells"] and st["solids"] >= expected_solids
    if st["faces"] == 0:
        issues.append(_issue("empty", "error", "The saved STEP file has no surfaces in it."))
        out["ok"] = False
        return out
    if open_parts:
        issues.append(_issue("open_mesh", "warning",
                             f"The mesh has holes, so {'that part was' if open_parts == 1 else f'{open_parts} parts were'} "
                             "saved as surfaces instead of a solid. Turn on Mesh repair (Advanced) to close small holes automatically."))
    if expected_solids and st["solids"] == 0:
        issues.append(_issue("not_solid", "warning",
                             "Saved as surfaces, not as a solid: the surfaces don't close up completely. "
                             "CAD programs can open it, but you may have to stitch it into a solid before editing."))
    elif expected_solids and st["solids"] < expected_solids:
        issues.append(_issue("missing_solids", "warning",
                             f"Only {st['solids']} of {expected_solids} parts came out as solids; the others are open surfaces."))
    bad = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    while exp.More():
        if not BRepCheck_Analyzer(exp.Current()).IsValid():
            bad += 1
        exp.Next()
    out["bad_faces"] = bad
    out["valid"] = not bad and is_valid(shape)
    if bad:
        issues.append(_issue("bad_faces", "warning",
                             f"{bad} face{'s have' if bad > 1 else ' has'} small geometry errors. "
                             "Fusion and most CAD programs repair these automatically when importing."))
    elif not out["valid"]:
        issues.append(_issue("invalid", "warning",
                             "The solid's structure has a problem (for example faces that cross or don't line up). "
                             "Most CAD programs repair this when importing."))
    if expected_solids:
        try:
            fb = ShapeAnalysis_FreeBounds(shape)
            n_open = count(fb.GetClosedWires(), TopAbs_EDGE) + count(fb.GetOpenWires(), TopAbs_EDGE)
        except Exception as fault:
            faults.caught(fault)
            n_open = 0
        out["open_edges"] = n_open
        if n_open and st["solids"]:
            issues.append(_issue("gaps", "warning",
                                 f"There {'are' if n_open > 1 else 'is'} {n_open} open edge{'s' if n_open > 1 else ''} "
                                 "where neighbouring surfaces don't meet (small gaps in the surface)."))
    if st["solids"] and mesh_volume:
        v = volume(shape)
        out["volume"] = v
        if v < 0:
            issues.append(_issue("inside_out", "warning",
                                 "The solid is inside out (its surfaces face inward). Most CAD programs flip it automatically."))
        else:
            r = v / abs(mesh_volume)
            if abs(r - 1.0) > 0.05:
                issues.append(_issue("volume", "warning",
                                     f"The solid's volume is {r * 100:.0f}% of the mesh's volume, "
                                     "so part of the shape may be missing or distorted. Compare the Mesh and STEP views."))
    out["ok"] = not any(i["level"] == "error" for i in issues)
    return out


# ----------------------------------------------------------------------------
# Preview export (simple binary format read by the macOS app)
# ----------------------------------------------------------------------------
#
#   magic  b"MMMP"            4 bytes
#   version uint32            = 1
#   n_verts uint32, n_tris uint32, n_line_pts uint32
#   positions float32[n_verts*3]
#   normals   float32[n_verts*3]
#   colors    uint8[n_verts*4]
#   indices   uint32[n_tris*3]
#   lines     float32[n_line_pts*3]   (pairs of points = segments)


def _face_color(i):
    # Pleasant, well separated hues (golden-ratio walk), moderate saturation.
    h = (i * 0.61803398875 + 0.13) % 1.0
    s, v = 0.42, 0.86
    k = int(h * 6)
    f = h * 6 - k
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][k % 6]
    return int(r * 255), int(g * 255), int(b * 255)


def export_preview(shape, path, diag, max_faces_colored=200000):
    lin = max(diag * 0.0015, 1e-6)
    meshguard.mesh(shape, lin, False, 0.35, True)
    pos, nrm, col, idx = [], [], [], []
    base = 0
    exp = TopExp_Explorer(shape, TopAbs_FACE)
    fi = 0
    while exp.More():
        face = TopoDS.Face(exp.Current())
        face, tri, loc = _face_triangulation(face, lin)
        if tri is not None:
            n = tri.NbNodes()
            trsf = loc.Transformation()
            P = np.empty((n, 3))
            for i in range(1, n + 1):
                p = tri.Node(i).Transformed(trsf)
                P[i - 1] = (p.X(), p.Y(), p.Z())
            m = tri.NbTriangles()
            T = np.empty((m, 3), dtype=np.int64)
            for i in range(1, m + 1):
                a, b, c = tri.Triangle(i).Get()
                T[i - 1] = (a - 1, b - 1, c - 1)
            if face.Orientation() == TopAbs_REVERSED:
                T = T[:, ::-1]
            N = _surface_normals(face, tri, trsf, n)
            if N is not None and len(T):
                # Along a face's edges OpenCASCADE can leave tiny folded
                # slivers (the edge curve is a hair off the surface, within
                # its tolerance): seen from outside they are back faces and
                # showed as dark specks. They are turned to face the way the
                # surface does; at their size the shape doesn't change.
                g = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
                folded = (g * N[T].sum(1)).sum(1) < 0
                T[folded] = T[folded][:, ::-1]
            if N is None:
                # fall back to normals averaged from the triangles
                N = np.zeros_like(P)
                fn = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
                for k in range(3):
                    np.add.at(N, T[:, k], fn)
                ln = np.linalg.norm(N, axis=1)
                ln[ln == 0] = 1
                N /= ln[:, None]
            c = _face_color(fi) if fi < max_faces_colored else (200, 200, 200)
            pos.append(P)
            nrm.append(N)
            col.append(np.tile(np.array([*c, 255], dtype=np.uint8), (n, 1)))
            idx.append(T + base)
            base += n
        fi += 1
        exp.Next()
    lines = _edge_polylines(shape)
    _write_preview(path, pos, nrm, col, idx, lines)


def _tri_area(tri):
    if tri is None or tri.NbTriangles() == 0:
        return 0.0
    P = np.array([tri.Node(i).Coord() for i in range(1, tri.NbNodes() + 1)])
    T = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]) - 1
    return 0.5 * float(np.linalg.norm(np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]]), axis=1).sum())


def _face_triangulation(face, lin, share=0.8):
    """The face (or a copy of it), its triangles and their location from the shape's
    meshing. OpenCASCADE can leave a valid face partly or wholly without
    triangles at one fineness and mesh it fully at another (a thin strip on
    a wheel rim showed as a hole in the preview though the STEP was closed),
    so such a face is meshed again on its own, as a copy (meshing it in place
    reuses its edges' points and fails the same way), at other finenesses,
    and the most complete result is kept."""
    loc = TopLoc_Location()
    tri = BRep_Tool.Triangulation_s(face, loc)
    props = GProp_GProps()
    # (at its default precision OpenCASCADE gave a printed mount's thin face
    # 48 mm2 for its 15.5)
    BRepGProp.SurfaceProperties_s(face, props, 1e-6)
    area = abs(props.Mass())
    if not np.isfinite(area) or area <= 0 or _tri_area(tri) >= share * area:
        return face, tri, loc
    best, best_err = (face, tri, loc), abs(_tri_area(tri) / area - 1)
    for k in (1.0, 0.5, 0.25, 2.0, 4.0, 8.0):
        copy = TopoDS.Face(BRepBuilderAPI_Copy(face).Shape())
        meshguard.mesh(copy, lin * k, False, 0.35, True)
        cloc = TopLoc_Location()
        ctri = BRep_Tool.Triangulation_s(copy, cloc)
        got = _tri_area(ctri) / area
        # (a result much bigger than the face has triangles outside it)
        if got <= 1.1 and abs(got - 1) < best_err:
            best, best_err = (copy, ctri, cloc), abs(got - 1)
            if best_err < 0.03:
                break
    return best


def _surface_normals(face, tri, trsf, n):
    """Exact surface normals at the triangulation nodes, so neighbouring
    smooth faces shade as one continuous surface."""
    try:
        from OCP.BRepLib import BRepLib_ToolTriangulatedShape

        if not tri.HasNormals():
            BRepLib_ToolTriangulatedShape.ComputeNormals_s(face, tri)
        if not tri.HasNormals():
            return None
        N = np.empty((n, 3))
        for i in range(1, n + 1):
            d = tri.Normal(i).Transformed(trsf)
            N[i - 1] = (d.X(), d.Y(), d.Z())
        if face.Orientation() == TopAbs_REVERSED:
            N = -N
        return N
    except Exception as fault:
        faults.caught(fault)
        return None


def _face_normal_at(edge, face, surfaces):
    """Outward normal of a face in the middle of one of its edges
    (``surfaces`` caches one surface evaluator per face)."""
    from OCP.GeomLProp import GeomLProp_SLProps

    try:
        key = hash(face)
        surf = surfaces.get(key)
        if surf is None:
            surf = surfaces[key] = BRep_Tool.Surface_s(face)
        pc = BRep_Tool.CurveOnSurface_s(edge, face, 0.0, 0.0)
        if pc is None:
            return None
        a, b = BRep_Tool.Range_s(edge, face)
        uv = pc.Value(0.5 * (a + b))
        props = GeomLProp_SLProps(surf, uv.X(), uv.Y(), 1, 1e-7)
        if not props.IsNormalDefined():
            return None
        d = props.Normal()
        v = np.array([d.X(), d.Y(), d.Z()])
        return -v if face.Orientation() == TopAbs_REVERSED else v
    except Exception as fault:
        faults.caught(fault)
        return None


def _edge_polylines(shape, max_edges=60000, sharp_deg=20.0):
    """Feature lines for the preview: sharp edges and open boundaries.
    Smooth joins (between merged surface patches, fillet tangents) and
    the seams of round faces are left out, like a CAD program shows a
    solid body."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GCPnts import GCPnts_TangentialDeflection

    cos_sharp = np.cos(np.radians(sharp_deg))
    surfaces = {}
    info = {}  # edge hash -> [edge, [normals...], seam]
    fexp = TopExp_Explorer(shape, TopAbs_FACE)
    while fexp.More():
        face = TopoDS.Face(fexp.Current())
        fexp.Next()
        eexp = TopExp_Explorer(face, TopAbs_EDGE)
        while eexp.More():
            e = TopoDS.Edge(eexp.Current())
            eexp.Next()
            h = hash(e)  # orientation independent in OCP
            rec = info.get(h)
            if rec is None:
                if len(info) >= max_edges:
                    continue
                rec = info[h] = [e, [], False, set()]
            fh = hash(face)
            if fh in rec[3]:
                rec[2] = True  # the same face on both sides: a seam
                continue
            rec[3].add(fh)
            rec[1].append(_face_normal_at(e, face, surfaces))
    segs = []
    for e, normals, seam, _ in info.values():
        try:
            if BRep_Tool.Degenerated_s(e):
                continue
            if seam and len(normals) == 1:
                continue  # seam of a cylinder, sphere, ...
            if len(normals) == 2:
                n1, n2 = normals
                if n1 is not None and n2 is not None and float(n1 @ n2) > cos_sharp:
                    continue  # smooth join
            c = BRepAdaptor_Curve(e)
            d = GCPnts_TangentialDeflection(c, 0.15, 1e-3 * 1.0, 2)
            n = d.NbPoints()
            pts = [d.Value(i) for i in range(1, n + 1)]
            for a, b in zip(pts[:-1], pts[1:]):
                segs.append((a.X(), a.Y(), a.Z(), b.X(), b.Y(), b.Z()))
        except Exception as fault:
            faults.caught(fault)
            continue
    if not segs:
        return np.zeros((0, 3), dtype=np.float32)
    return np.asarray(segs, dtype=np.float32).reshape(-1, 3)


def _write_preview(path, pos, nrm, col, idx, lines):
    P = np.vstack(pos).astype(np.float32) if pos else np.zeros((0, 3), np.float32)
    N = np.vstack(nrm).astype(np.float32) if nrm else np.zeros((0, 3), np.float32)
    C = np.vstack(col).astype(np.uint8) if col else np.zeros((0, 4), np.uint8)
    I = np.vstack(idx).astype(np.uint32) if idx else np.zeros((0, 3), np.uint32)
    L = np.asarray(lines, dtype=np.float32).reshape(-1, 3)
    with open(path, "wb") as fh:
        fh.write(b"MMMP")
        fh.write(struct.pack("<IIII", 1, len(P), len(I), len(L)))
        fh.write(P.tobytes())
        fh.write(N.tobytes())
        fh.write(C.tobytes())
        fh.write(I.tobytes())
        fh.write(L.tobytes())


def export_mesh_preview(V, F, path, color=(150, 160, 175), edges=None):
    """Preview of a raw triangle mesh (input view)."""
    V = np.asarray(V, dtype=np.float64)
    F = np.asarray(F, dtype=np.int64)
    P = V[F].reshape(-1, 3)
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    ln = np.linalg.norm(fn, axis=1)
    ln[ln == 0] = 1
    fn /= ln[:, None]
    N = np.repeat(fn, 3, axis=0)
    C = np.tile(np.array([*color, 255], dtype=np.uint8), (len(P), 1))
    I = np.arange(len(P), dtype=np.int64).reshape(-1, 3)
    L = np.zeros((0, 3)) if edges is None else edges
    _write_preview(path, [P], [N], [C], [I], L)
