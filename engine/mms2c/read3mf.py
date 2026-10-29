"""
Reading 3MF files (the 3D Manufacturing Format slicers save projects in).

A 3MF file is a zip of XML parts. Its build lists the objects to print, each placed by
a transform; an object is a mesh, or made of components (other objects, each with its
own transform, possibly kept in other files of the zip, as Bambu Studio and OrcaSlicer
do). Every printed object comes out as one mesh, in millimetres, laid out as on the
plate. Bambu Studio also marks the parts of an object as modifiers, negative volumes or
support blockers/enforcers (Metadata/model_settings.config); only the real parts
(normal_part) count, the others aren't material. Each triangle is labelled with the
part (mesh) it came from: parts may overlap, and are then joined by a boolean union,
not by mending one mesh that crosses itself.
"""
import re
import zipfile
import xml.etree.ElementTree as ET

import numpy as np

CORE = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"
PRODUCTION = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}"
UNITS = {"micron": 0.001, "millimeter": 1.0, "centimeter": 10.0, "inch": 25.4, "foot": 304.8, "meter": 1000.0}


def _matrix(text):
    """A 3MF transform ("m00 m01 m02 m10 ... m32") as a 4 x 4 matrix acting on row vectors."""
    if not text:
        return np.eye(4)
    m = np.array([float(x) for x in text.split()]).reshape(4, 3)
    return np.c_[m, [0, 0, 0, 1]]


def read_3mf(path):
    """[(name, points (n, 3), triangles (m, 3), part of each triangle (m,))]: every object
    the file's build prints."""
    z = zipfile.ZipFile(path)
    names = set(z.namelist())
    root = "3D/3dmodel.model"
    if "_rels/.rels" in names:
        for rel in ET.fromstring(z.read("_rels/.rels")).iter():
            if rel.get("Type", "").endswith("/3dmodel") and rel.get("Target"):
                root = rel.get("Target").lstrip("/")
    models = {}

    def model(part):
        if part not in models:
            xml = ET.fromstring(z.read(part))
            models[part] = (UNITS.get(xml.get("unit", "millimeter"), 1.0),
                            {o.get("id"): o for o in xml.iter(CORE + "object")}, xml)
        return models[part]

    # Bambu Studio's notes on each object: its name, and what each of its parts is
    titles, kinds = {}, {}
    if "Metadata/model_settings.config" in names:
        for obj in ET.fromstring(z.read("Metadata/model_settings.config")).iter("object"):
            for md in obj.findall("metadata"):
                if md.get("key") == "name":
                    titles[obj.get("id")] = md.get("value")
            for part in obj.iter("part"):
                kinds[(obj.get("id"), part.get("id"))] = part.get("subtype", "normal_part")

    def collect(part, oid, M, top, P, T, L):
        scale, objects, _ = model(part)
        obj = objects.get(oid)
        if obj is None or obj.get("type", "model") != "model":
            return
        mesh = obj.find(CORE + "mesh")
        if mesh is not None:
            V = np.array([[float(v.get(a)) for a in "xyz"] for v in mesh.iter(CORE + "vertex")]) * scale
            F = np.array([[int(t.get(a)) for a in ("v1", "v2", "v3")] for t in mesh.iter(CORE + "triangle")])
            if len(V) and len(F):
                T.append(F + sum(len(p) for p in P))
                P.append((np.c_[V, np.ones(len(V))] @ M)[:, :3])
                L.append(np.full(len(F), len(L)))
        comps = obj.find(CORE + "components")
        for c in comps if comps is not None else []:
            if kinds.get((top, c.get("objectid")), "normal_part") != "normal_part":
                continue                    # a modifier, negative volume or support helper
            sub = (c.get(PRODUCTION + "path") or part).lstrip("/")
            collect(sub, c.get("objectid"), _matrix(c.get("transform")) @ M, top, P, T, L)

    out, seen = [], {}
    for item in model(root)[2].iter(CORE + "item"):
        oid = item.get("objectid")
        P, T, L = [], [], []
        collect(root, oid, _matrix(item.get("transform")), oid, P, T, L)
        if not T:
            continue
        name = titles.get(oid) or model(root)[1][oid].get("name") or f"object {oid}"
        name = re.sub(r'[<>:"/\\|?*]+', "_", name).strip() or f"object {oid}"
        seen[name] = seen.get(name, 0) + 1
        if seen[name] > 1:
            name = f"{name} ({seen[name]})"
        out.append((name,) + _weld(np.vstack(P), np.vstack(T), np.concatenate(L)))
    return out


def _weld(verts, tris, labels):
    """Merge corners that coincide (as load_stl does for an STL's loose triangles)."""
    key = np.round(verts * 1e4).astype(np.int64)
    uniq, inv = np.unique(key, axis=0, return_inverse=True)
    pts = np.zeros((len(uniq), 3))
    pts[inv.ravel()] = verts
    tris = inv.ravel()[tris]
    keep = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 2] != tris[:, 0])
    return pts, tris[keep], labels[keep]
