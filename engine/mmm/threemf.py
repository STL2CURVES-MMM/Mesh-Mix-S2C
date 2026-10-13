"""3MF reader (core spec + production extension + slicer project files).

Handles project files from Bambu Studio, OrcaSlicer / Snapmaker Orca and
PrusaSlicer, as well as plain 3MF exports:

* objects stored in separate model files (``p:path`` components)
* nested components and their transforms
* build items (plate instances) and their transforms
* slicer part types: only *normal* parts are geometry; modifiers, support
  blockers/enforcers are skipped and negative parts are reported
* unit attribute (micron ... meter), converted to millimetres

What "one part" means for CAD: identical copies on the build plate are
converted once, in the part's original orientation (the slicer's plate
placement is removed), with any scaling done in the slicer kept. Each part
becomes its own named body.
"""
import os
import re
import zipfile
import xml.etree.ElementTree as ET

import numpy as np

CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PROD = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"

UNIT_MM = {
    "micron": 0.001,
    "millimeter": 1.0,
    "centimeter": 10.0,
    "inch": 25.4,
    "foot": 304.8,
    "meter": 1000.0,
}

SKIP_PART_TYPES = {
    "modifier_part",
    "support_blocker",
    "support_enforcer",
    "parametermodifier",
    "supportblocker",
    "supportenforcer",
    "modifier",
}
NEGATIVE_PART_TYPES = {"negative_part", "negativevolume"}


class ThreeMFError(Exception):
    pass


class Part:
    def __init__(self, name, tris, frame=0):
        self.name = name
        self.tris = tris  # (n, 3, 3) float64, millimetres
        # parts with the same frame share coordinates (the parts of one
        # object; with the whole build plate, every part)
        self.frame = frame


def _parse_transform(s):
    if not s:
        return np.eye(4)
    v = [float(x) for x in s.split()]
    if len(v) != 12:
        return np.eye(4)
    M = np.eye(4)
    # 3MF stores the 3x4 matrix row-major for row vectors: p' = p * M
    M[:3, :3] = np.array(v[:9]).reshape(3, 3).T
    M[:3, 3] = v[9:12]
    return M


def _apply(M, P):
    return P @ M[:3, :3].T + M[:3, 3]


class _ModelFile:
    """Objects of one .model file: id -> (mesh or components, attributes)."""

    def __init__(self, zf, path):
        self.path = path
        self.objects = {}
        self.build = []
        self.unit = 1.0
        with zf.open(path.lstrip("/")) as fh:
            self._parse(fh)

    def _parse(self, fh):
        cur = None
        verts = []
        tris = []
        comps = []
        for event, el in ET.iterparse(fh, events=("start", "end")):
            tag = el.tag
            if event == "start":
                if tag == CORE + "model":
                    self.unit = UNIT_MM.get(el.get("unit", "millimeter"), 1.0)
                elif tag == CORE + "object":
                    cur = dict(el.attrib)
                    verts, tris, comps = [], [], []
                continue
            # end events
            if tag == CORE + "vertex":
                verts.append((el.get("x"), el.get("y"), el.get("z")))
            elif tag == CORE + "triangle":
                tris.append((el.get("v1"), el.get("v2"), el.get("v3")))
            elif tag == CORE + "component":
                comps.append(
                    {
                        "objectid": el.get("objectid"),
                        "path": el.get(PROD + "path"),
                        "transform": _parse_transform(el.get("transform")),
                    }
                )
            elif tag == CORE + "object" and cur is not None:
                oid = cur.get("id")
                entry = {"attrib": cur, "mesh": None, "components": comps}
                if verts and tris:
                    V = np.array(verts, dtype=np.float64)
                    T = np.array(tris, dtype=np.int64)
                    entry["mesh"] = (V, T)
                self.objects[oid] = entry
                cur = None
                el.clear()
            elif tag == CORE + "item":
                self.build.append(
                    {
                        "objectid": el.get("objectid"),
                        "transform": _parse_transform(el.get("transform")),
                        "printable": el.get("printable", "1"),
                    }
                )
            if tag in (CORE + "vertex", CORE + "triangle"):
                el.clear()


def _read_config(zf):
    """Slicer part types/names: {(object_id, part_id): (type, name)} plus
    object names, from Bambu/Orca or PrusaSlicer config files."""
    names = {}
    parts = {}
    offsets = {}
    prusa_volumes = {}
    sources = {}
    files = set(zf.namelist())
    if "Metadata/model_settings.config" in files:
        try:
            root = ET.fromstring(zf.read("Metadata/model_settings.config"))
            for obj in root.findall("object"):
                oid = obj.get("id")
                for md in obj.findall("metadata"):
                    if md.get("key") == "name":
                        names[oid] = md.get("value")
                for part in obj.findall("part"):
                    pid = part.get("id")
                    ptype = (part.get("subtype") or "normal_part").lower()
                    pname = None
                    off = np.zeros(3)
                    for md in part.findall("metadata"):
                        k = md.get("key")
                        if k == "name":
                            pname = md.get("value")
                        elif k == "source_file" and md.get("value"):
                            sources.setdefault(oid, md.get("value"))
                        elif k in ("source_offset_x", "source_offset_y", "source_offset_z"):
                            try:
                                off["xyz".index(k[-1])] = float(md.get("value"))
                            except (TypeError, ValueError):
                                pass
                    parts[(oid, pid)] = (ptype, pname)
                    offsets[(oid, pid)] = off
        except ET.ParseError:
            pass
    for cfg in ("Metadata/Slic3r_PE_model.config", "Metadata/PrusaSlicer_model.config"):
        if cfg in files:
            try:
                root = ET.fromstring(zf.read(cfg))
                for obj in root.findall("object"):
                    oid = obj.get("id")
                    for md in obj.findall("metadata"):
                        if md.get("key") == "name":
                            names[oid] = md.get("value")
                    vols = []
                    for vol in obj.findall("volume"):
                        first = int(vol.get("firstid", "0"))
                        last = int(vol.get("lastid", "-1"))
                        vtype, vname = "modelpart", None
                        for md in vol.findall("metadata"):
                            k = md.get("key")
                            if k == "volume_type":
                                vtype = (md.get("value") or "ModelPart").lower()
                            elif k == "modifier" and md.get("value") == "1":
                                vtype = "parametermodifier"
                            elif k == "name":
                                vname = md.get("value")
                        vols.append((first, last, vtype, vname))
                    prusa_volumes[oid] = vols
            except ET.ParseError:
                pass
    # objects with a meaningless name ("export", "Object 3", "Body") are
    # better known by the file they were imported from
    for oid, src in sources.items():
        if _generic_name(names.get(oid)):
            names[oid] = os.path.basename(src)
    return names, parts, prusa_volumes, offsets


def _generic_name(name):
    import re

    if not name:
        return True
    stem = os.path.splitext(name.strip())[0]
    return bool(re.fullmatch(r"(export|object|body|mesh|part|model|untitled|default|solid|shape|component|stl)[\s_-]*\d*", stem, re.I))


def _mkey(M):
    return tuple(np.round(M[:3, :4].ravel(), 4).tolist())


def _polar_scale(M):
    """Symmetric stretch part S of the linear map L = R S (keeps slicer
    scaling, drops rotation and translation)."""
    L = M[:3, :3]
    out = np.eye(4)
    if np.linalg.det(L) < 0:
        # mirrored in the slicer: keep the mirror (and so the plate's turn too)
        out[:3, :3] = L
        return out
    U, sv, Vt = np.linalg.svd(L)
    S = Vt.T @ np.diag(sv) @ Vt
    if np.abs(S - np.eye(3)).max() > 1e-4:  # ignore float noise
        out[:3, :3] = S
    return out


def read_3mf(path, layout="parts", report=None):
    """Return a list of Part objects.

    layout="parts": each distinct part once, original orientation (default)
    layout="plate": every instance where it sits on the build plate
    """
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise ThreeMFError("This doesn't look like a valid 3MF file (it may be damaged or incomplete).")
    with zf:
        names_in = set(zf.namelist())
        main = None
        for cand in ("3D/3dmodel.model", "3D/3DModel.model"):
            if cand in names_in:
                main = cand
                break
        if main is None:
            # find the root model through the package relationships
            try:
                rels = zf.read("_rels/.rels").decode("utf-8", "replace")
                m = re.search(r'Target="([^"]+\.model)"', rels)
                if m:
                    main = m.group(1).lstrip("/")
            except KeyError:
                pass
        if main is None:
            models = [n for n in names_in if n.lower().endswith(".model")]
            if not models:
                raise ThreeMFError("This 3MF file has no 3D model in it.")
            main = models[0]
        files = {}

        def model_file(p):
            p = p.lstrip("/")
            if p not in files:
                files[p] = _ModelFile(zf, p)
            return files[p]

        root = model_file(main)
        cfg_names, cfg_parts, prusa_volumes, cfg_offsets = _read_config(zf)
        unit = root.unit

        def expand(mf, oid, M, top_oid, depth=0):
            """Yield (tris, name, part_type) for an object, recursing into
            components. ``top_oid`` is the build-level object id."""
            if depth > 16:
                return
            obj = mf.objects.get(oid)
            if obj is None:
                return
            otype = obj["attrib"].get("type", "model").lower()
            if otype in ("support", "solidsupport", "other"):
                return
            if obj["mesh"] is not None:
                V, T = obj["mesh"]
                Vt = _apply(M, V)
                if layout == "parts" and (top_oid, oid) in cfg_offsets:
                    # Bambu/Orca centre imported meshes and remember by how
                    # much: adding it back restores the original coordinates
                    Vt = Vt + cfg_offsets[(top_oid, oid)]
                Vt = Vt * unit
                ptype, pname = cfg_parts.get((top_oid, oid), ("normal_part", None))
                vols = prusa_volumes.get(top_oid)
                if vols:
                    for vi, (first, last, vtype, vname) in enumerate(vols):
                        sub = T[first:last + 1] if last >= first else T
                        yield Vt[sub], vname, vtype, (mf.path, oid, vi, _mkey(M))
                else:
                    yield Vt[T], pname, ptype, (mf.path, oid, 0, _mkey(M))
            for c in obj["components"]:
                cmf = model_file(c["path"]) if c["path"] else mf
                yield from expand(cmf, c["objectid"], M @ c["transform"], top_oid, depth + 1)

        items = root.build
        if not items:
            items = [{"objectid": oid, "transform": np.eye(4), "printable": "1"} for oid in root.objects]

        parts = []
        seen = {}
        negative = 0
        skipped = 0
        for item_no, it in enumerate(items):
            oid = it["objectid"]
            M_item = it["transform"]
            if layout == "parts":
                M_use = _polar_scale(M_item)
            else:
                M_use = M_item
            pieces = list(expand(root, oid, M_use, oid))
            normal = []
            idents = []
            for tris, pname, ptype, ident in pieces:
                pt = (ptype or "normal_part").lower()
                if pt in SKIP_PART_TYPES:
                    skipped += 1
                    continue
                if pt in NEGATIVE_PART_TYPES:
                    negative += 1
                    continue
                normal.append((tris, pname))
                idents.append(ident)
            if not normal:
                continue
            obj_name = cfg_names.get(oid) or root.objects.get(oid, {}).get("attrib", {}).get("name") or f"Part {oid}"
            obj_name = os.path.splitext(obj_name)[0] if obj_name.lower().endswith((".stl", ".obj", ".step", ".stp", ".3mf")) else obj_name
            if layout == "parts":
                # copies of the same part (same mesh, same scaling) once
                key = tuple(sorted(idents))
                if key in seen:
                    continue
                seen[key] = True
            for k, (tris, pname) in enumerate(normal):
                nm = obj_name if len(normal) == 1 else f"{obj_name} - {pname or f'part {k + 1}'}"
                parts.append(Part(nm, tris, item_no if layout == "parts" else 0))
        if report is not None:
            if skipped:
                report.log(f"Skipped {skipped} modifier or support part(s) from the slicer project")
            if negative:
                report.warn(f"{negative} negative part(s) from the slicer (shapes that cut holes) were ignored, so those holes are not in the STEP file.")
            report.log(f"3MF project: {len(parts)} part(s) from {len(items)} object(s) on the plate")
        if not parts:
            raise ThreeMFError("This 3MF project has no printable parts (only modifiers or support blockers).")
        return parts
