"""Test files for the desktop app's tests and self-test.

    python desktop/tests/make_fixtures.py OUTPUT_DIR

Needs tests/models (python tests/make_models.py). Writes:
  two_parts.3mf        a plain 3MF with two parts (for "Split to parts")
  Peça ção teste.stl   an STL with accents in its name (non-ASCII paths)
  Messy cube.stl       a cube with problems the mesh check fixes (two
                       triangles facing the wrong way, a duplicate one)
"""
import os
import shutil
import struct
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "..", "..", "tests", "models")


def read_stl(path):
    """Binary or ASCII STL -> list of triangles ((x,y,z) * 3)."""
    with open(path, "rb") as fh:
        data = fh.read()
    if data[:5].lower() == b"solid" and b"facet" in data[:400]:
        tris, cur = [], []
        for line in data.decode("ascii", "replace").splitlines():
            parts = line.split()
            if parts[:1] == ["vertex"]:
                cur.append(tuple(float(v) for v in parts[1:4]))
                if len(cur) == 3:
                    tris.append(cur)
                    cur = []
        return tris
    n = struct.unpack_from("<I", data, 80)[0]
    tris = []
    for i in range(n):
        v = struct.unpack_from("<12f", data, 84 + i * 50)
        tris.append([v[3:6], v[6:9], v[9:12]])
    return tris


def mesh_xml(tris):
    index, verts, faces = {}, [], []
    for t in tris:
        ids = []
        for p in t:
            k = tuple(round(c, 6) for c in p)
            if k not in index:
                index[k] = len(verts)
                verts.append(k)
            ids.append(index[k])
        if len(set(ids)) == 3:
            faces.append(ids)
    v = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in verts)
    f = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in faces)
    return f"<mesh><vertices>{v}</vertices><triangles>{f}</triangles></mesh>"


def write_3mf(path, parts):
    """parts: [(name, triangles, (dx, dy, dz))] -> a plain 3MF."""
    objs, items = [], []
    for i, (name, tris, (dx, dy, dz)) in enumerate(parts, start=1):
        objs.append(f'<object id="{i}" name="{name}" type="model">{mesh_xml(tris)}</object>')
        items.append(f'<item objectid="{i}" transform="1 0 0 0 1 0 0 0 1 {dx} {dy} {dz}"/>')
    model = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<model unit="millimeter" xml:lang="en-US" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">'
             f'<resources>{"".join(objs)}</resources><build>{"".join(items)}</build></model>')
    types = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
             '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
             '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>')
    rels = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
            'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", types)
        z.writestr("_rels/.rels", rels)
        z.writestr("3D/3dmodel.model", model)


def write_stl(path, tris):
    """Binary STL (normals left at zero: readers work them out)."""
    with open(path, "wb") as fh:
        fh.write(b"Manifold Mesh Mix test file".ljust(80, b" "))
        fh.write(struct.pack("<I", len(tris)))
        for t in tris:
            fh.write(struct.pack("<12fH", 0, 0, 0, *t[0], *t[1], *t[2], 0))


def main(out):
    os.makedirs(out, exist_ok=True)
    cube = read_stl(os.path.join(MODELS, "cad_cube.stl"))
    cyl = read_stl(os.path.join(MODELS, "cad_cylinder.stl"))
    write_3mf(os.path.join(out, "two_parts.3mf"), [("Cube", cube, (0, 0, 0)), ("Cylinder", cyl, (80, 0, 0))])
    shutil.copyfile(os.path.join(MODELS, "cad_cube.stl"), os.path.join(out, "Peça ção teste.stl"))
    messy = [list(t) for t in cube]
    for k in (0, len(messy) // 2):
        messy[k] = messy[k][::-1]
    messy.append(messy[1])
    write_stl(os.path.join(out, "Messy cube.stl"), messy)
    print("\n".join(sorted(os.listdir(out))))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "fixtures"))
