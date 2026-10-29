"""
studio - look at what stl2curves finds on a mesh, paint areas that should be one face,
and convert, in the browser.

  python -m stl2curves.studio [part.stl] [--port 8765] [--no-browser]

A small web server on this computer only (127.0.0.1) serves the page (studio.html) and
runs the conversion. Analyze shows the mesh coloured by what was found on it; paint
over areas that should become one face (each group is fitted with one freeform surface,
or a smooth blend), then Convert builds the solid with them and shows its faces, and
the STEP file can be saved. The page draws with three.js, fetched from a CDN.

The first stages of each conversion are saved (stages.py) in a folder of its own, so
converting again after painting skips the analysis.
"""
import argparse
import base64
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import numpy as np

from . import accuracy, convert, progress, stages, workers
from . import features as features_mod
from .blends import merge

PAGE = Path(__file__).with_name("studio.html")
BIGGEST_UPLOAD = 500 * 2 ** 20      # bytes
TESS_DEFLECTION = 0.02              # mm: how finely the result is tessellated for the view
EDGE_ANGLE = 0.1                    # rad: edges drawn in straight segments turning at most this ...
EDGE_SAG = 0.01                     # ... and bowing at most this far (mm)
SURFACES = {1: "cylinder", 2: "cone", 3: "sphere", 4: "torus"}    # OCC surface types (0: plane)
# the result view's colours, as the page's KIND table names them
VIEW_KINDS = ["none", "left", "cylinder", "cone", "sphere", "torus", "pipe", "freeform", "blend", "thread", "extrusion"]
SPLINE_KINDS = {"pipe", "freeform", "blend", "thread", "extrusion"}     # features built as splines
FOLDER_PREFIX = "stl2curves_studio_"    # each studio's working folder, in the temp folder
OLD_FOLDER_HOURS = 48               # hours untouched after which another studio deletes one


def _b64(a):
    return base64.b64encode(np.ascontiguousarray(a).tobytes()).decode()


class Session:
    """The one mesh being worked on, and what was last made of it."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.lock = threading.Lock()
        self.path = None
        self.step = None
        self.auto = False           # (a mesh given on the command line: analyzed as the page opens)

    def options(self, req):
        simplify = req.get("simplify")
        return dict(tol=float(req.get("tol", 0.01)), fuse=True, curves=True,
                    true_size=bool(req.get("true_size", False)), blends=True, mend=True,
                    simplify_to=None if simplify in (None, "", "auto") else float(simplify))

    def prepared(self, req):
        """The conversion's first stages for these options (saved ones if any)."""
        opts = self.options(req)
        wanted = float(req.get("accuracy") or accuracy.DEFAULT)
        if wanted != accuracy.mm():
            workers.finish()        # (worker processes take the accuracy from when they start)
            accuracy.set_accuracy(wanted)
        limit = float(req.get("time_limit") or convert.TIME_LIMIT)
        features_mod._budget = limit
        features_mod._deadline = time.time() + limit
        t = time.time()
        parts, info, part = convert.prepare(str(self.path), **opts)
        return opts, parts, info, part, time.time() - t

    def analyze(self, req):
        with self.lock:
            try:
                opts, parts, info, part, secs = self.prepared(req)
            finally:
                features_mod._deadline = features_mod._budget = None
            bodies = []
            for mesh, features, _ in parts:
                owner = np.full(len(mesh.farea), -1, np.int32)
                for k, f in enumerate(features):
                    owner[f.facets] = k
                bodies.append({
                    "positions": _b64(mesh.pts[mesh.tris].astype(np.float32).ravel()),
                    "owner": _b64(owner[mesh.facet_of].astype(np.int32)),
                    "outline": _b64(_outline(mesh, owner).astype(np.float32).ravel()),
                    "features": [{"label": f.label, "detail": f.detail, "kind": f.model.kind, "facets": len(f.facets)}
                                 for f in features],
                })
            return {"bodies": bodies, "seconds": round(secs, 1), "triangles": info["triangles"],
                    "repairs": info["repairs"], "mesh_left": info.get("mesh_left", []),
                    "cached": info.get("cached", [])}

    def convert(self, req):
        with self.lock:
            t = time.time()
            try:
                opts, parts, info, part, _ = self.prepared(req)
                notes = []
                for g in req.get("groups", []):
                    b = int(g["body"])
                    mesh, features, mesh_tol = parts[b]
                    tris = np.asarray(g["tris"], int)
                    tris = tris[(tris >= 0) & (tris < len(mesh.facet_of))]
                    features, merged = merge(mesh, features, np.unique(mesh.facet_of[tris]))
                    parts[b] = (mesh, features, mesh_tol)
                    notes.append(None if merged is None else {"group": g.get("name"), "feature": merged})
                shape, info = convert.finish(parts, info, part, opts["tol"], opts["fuse"])
            finally:
                features_mod._deadline = features_mod._budget = None
            skipped, used = info.get("skipped", []), info.get("restored", [])
            groups = []
            for n in notes:
                if n is not None:
                    f = n["feature"]
                    # (one face if it went in as it was; a blend no fill fits is cut in two)
                    outcome = ("one face" if any(f is u for u in used) else
                               "failed, left as it was" if any(f is s for s in skipped) else "split into several faces")
                    groups.append({"group": n["group"], "label": f.label, "detail": f.detail, "outcome": outcome})
            path = self.folder / (self.path.stem + ".step")
            convert.write_step(shape, str(path))
            self.step = path.read_bytes()
            view, faces = _tessellated(shape)
            # what was left as triangles: the solid's flat three-sided faces, matched back to
            # the mesh triangles they are (what a person may want to paint and convert again)
            flags, left_faces = _left_as_triangles(faces, [p[0] for p in parts])
            kinds = _face_kinds(faces, left_faces, parts, info.get("used", []))
            view["kind"] = _b64(np.repeat(kinds, [f["triangles"] for f in faces]).astype(np.uint8))
            left = [_b64(x.astype(np.uint8)) for x in flags]
            return {**view, "kinds": VIEW_KINDS, "left": left, "faces": info["faces"], "volume": round(info["volume"], 2), "valid": info["valid"],
                    "file_ok": info.get("file_ok"), "seconds": round(time.time() - t, 1), "groups": groups,
                    "left_faceted": len(skipped), "step_kb": round(len(self.step) / 1024, 1)}


def _left_as_triangles(faces, meshes):
    """For each mesh, which of its triangles came out as a face of their own; and which
    of the result's faces those are."""
    from scipy.spatial import cKDTree
    picked = [i for i, f in enumerate(faces) if f["centre"] is not None]
    out, left = [], np.zeros(len(faces), bool)
    for mesh in meshes:
        flags = np.zeros(len(mesh.tris), bool)
        if picked:
            d, k = cKDTree(mesh.pts[mesh.tris].mean(axis=1)).query(np.array([faces[i]["centre"] for i in picked]))
            near = d < 0.01
            flags[k[near]] = True
            left[np.array(picked)[near]] = True
        out.append(flags)
    return out, left


def _face_kinds(faces, left, parts, used):
    """Each result face's colour (an index into VIEW_KINDS), as the analysis view colours
    the feature it came from: a flat face grey, or red if it is a mesh triangle left as
    it was; an exact surface by its type; a spline face by the feature it was built from
    (a thread's flanks, a swept fillet, a freeform patch and a blend are all splines)."""
    from scipy.spatial import cKDTree
    index = {k: i for i, k in enumerate(VIEW_KINDS)}
    pts, labels = [], []
    for (mesh, _, _), features in zip(parts, used):
        cent = mesh.pts[mesh.tris].mean(axis=1)
        for f in features:
            kind = getattr(f.model, "kind", None)
            if kind in SPLINE_KINDS:
                tris = np.nonzero(np.isin(mesh.facet_of, f.facets))[0]
                pts.append(cent[tris])
                labels.append(np.full(len(tris), index[kind]))
    tree = cKDTree(np.vstack(pts)) if pts else None
    labels = np.concatenate(labels) if labels else np.zeros(0, int)
    out = np.zeros(len(faces), np.uint8)
    for i, f in enumerate(faces):
        if f["type"] == 0:
            out[i] = index["left" if left[i] else "none"]
        elif f["type"] in SURFACES:
            out[i] = index[SURFACES[f["type"]]]
        elif tree is not None:
            out[i] = labels[tree.query(f["sample"])[1]]
        else:
            out[i] = index["freeform"]
    return out


def _outline(mesh, owner):
    """Line segments (pairs of points) along the mesh edges between different features,
    and between flat facets no feature took: what the faces will be."""
    region = np.where(owner[mesh.facet_of] >= 0, owner[mesh.facet_of], -2 - mesh.facet_of)
    T = mesh.tris
    e = np.concatenate([T[:, [0, 1]], T[:, [1, 2]], T[:, [2, 0]]])
    who = np.tile(np.arange(len(T)), 3)
    e.sort(axis=1)
    order = np.lexsort((e[:, 1], e[:, 0]))
    e, who = e[order], who[order]
    same = np.nonzero((e[1:] == e[:-1]).all(axis=1))[0]
    cut = same[region[who[same]] != region[who[same + 1]]]
    return mesh.pts[e[cut]].reshape(-1, 3)


def _tessellated(shape):
    """The shape's faces as triangles and its edges as lines (for the view), and for each
    face: its surface type, how many of the triangles are its, a point on it, and (a
    flat three-sided face) its centre."""
    from OCP.BRep import BRep_Tool
    from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
    from OCP.BRepGProp import BRepGProp
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopExp import TopExp, TopExp_Explorer
    from OCP.TopLoc import TopLoc_Location
    from OCP.TopoDS import TopoDS
    from OCP.collections import IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher as Map
    BRepMesh_IncrementalMesh(shape, TESS_DEFLECTION, False, 0.3, True)
    pos, faces = [], []
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        ex.Next()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        nodes = np.array([tri.Node(i).Transformed(trsf).Coord() for i in range(1, tri.NbNodes() + 1)])
        ids = np.array([tri.Triangle(i).Get() for i in range(1, tri.NbTriangles() + 1)]) - 1
        if face.Orientation() == TopAbs_REVERSED:
            ids = ids[:, [0, 2, 1]]
        T = nodes[ids]
        pos.append(T.reshape(-1, 3))
        kind = int(BRepAdaptor_Surface(face).GetType())
        # (a point on the face, to find the feature it came from: the middle of its biggest
        # triangle; a curved face's centre of mass can lie off it, a cylinder's on its axis)
        big = int(np.linalg.norm(np.cross(T[:, 1] - T[:, 0], T[:, 2] - T[:, 0]), axis=1).argmax())
        centre = None
        if kind == 0:               # flat three-sided faces may be mesh triangles left as they were
            edges = Map()
            TopExp.MapShapes_s(face, TopAbs_EDGE, edges)
            if edges.Extent() == 3:
                g = GProp_GProps()
                BRepGProp.SurfaceProperties_s(face, g)
                centre = g.CentreOfMass().Coord()
        faces.append({"type": kind, "triangles": len(ids), "sample": T[big].mean(axis=0), "centre": centre})
    lines = []
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        edge = TopoDS.Edge(ex.Current())
        ex.Next()
        if BRep_Tool.Degenerated_s(edge):
            continue
        P = np.array([p.Coord() for p in edge_points(BRepAdaptor_Curve(edge))])
        lines.append(np.repeat(P, 2, axis=0)[1:-1])
    pos = np.vstack(pos) if pos else np.zeros((0, 3))
    lines = np.vstack(lines) if lines else np.zeros((0, 3))
    return {"positions": _b64(pos.astype(np.float32).ravel()), "edges": _b64(lines.astype(np.float32).ravel())}, faces


def edge_points(curve):
    """Points along an edge (a BRepAdaptor_Curve) close enough to draw it with straight
    segments: spaced by how much it bends. A fixed 24 drew a thread's crest edge, two
    or three turns round a bore, as chords cutting across it."""
    from OCP.GCPnts import GCPnts_TangentialDeflection
    a, b = curve.FirstParameter(), curve.LastParameter()
    if int(curve.GetType()) == 0:
        return [curve.Value(a), curve.Value(b)]
    sample = GCPnts_TangentialDeflection(curve, EDGE_ANGLE, EDGE_SAG, 2)
    if sample.NbPoints() < 2:
        return [curve.Value(a + (b - a) * i / 23) for i in range(24)]
    return [sample.Value(i) for i in range(1, sample.NbPoints() + 1)]


def _handler(session):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body, kind="application/json", extra=()):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/state":
                self._send(200, {"name": session.path.name if session.path else None, "auto": session.auto})
            elif url.path == "/api/progress":
                text, seconds = progress.current()
                self._send(200, {"step": text, "seconds": round(seconds, 1)})
            elif url.path == "/api/step" and session.step:
                name = session.path.stem + ".step"
                self._send(200, session.step, "application/step",
                           [("Content-Disposition", f'attachment; filename="{name}"')])
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            n = int(self.headers.get("Content-Length") or 0)
            if n > BIGGEST_UPLOAD:
                return self._send(413, {"error": "file too big"})
            body = self.rfile.read(n)
            try:
                if url.path == "/api/upload":
                    name = Path(unquote(parse_qs(url.query).get("name", ["part.stl"])[0])).name
                    if Path(name).suffix.lower() != ".stl":
                        return self._send(400, {"error": "an .stl file, please"})
                    session.path = session.folder / name
                    session.path.write_bytes(body)
                    session.step = None
                    return self._send(200, {"name": name})
                req = json.loads(body or b"{}")
                if session.path is None:
                    return self._send(400, {"error": "open a mesh first"})
                if url.path == "/api/analyze":
                    return self._send(200, session.analyze(req))
                if url.path == "/api/convert":
                    return self._send(200, session.convert(req))
                self._send(404, {"error": "not found"})
            except Exception as e:
                traceback.print_exc()
                self._send(500, {"error": f"{type(e).__name__}: {e}"})

    return Handler


def _clear_old_folders():
    """Delete the folders of studios long gone: closing the studio's window kills it
    before it can delete its own (copies of the meshes, STEP files, saved stages)."""
    cutoff = time.time() - OLD_FOLDER_HOURS * 3600
    for old in Path(tempfile.gettempdir()).glob(FOLDER_PREFIX + "*"):
        try:
            newest = max([old.stat().st_mtime] + [p.stat().st_mtime for p in old.rglob("*")])
            if newest < cutoff:
                shutil.rmtree(old, ignore_errors=True)
        except OSError:
            pass


class _Server(ThreadingHTTPServer):
    # on Windows SO_REUSEADDR lets a second server bind a port in use, and requests then
    # go to either; refused, a second studio takes a port of its own
    allow_reuse_address = False
    allow_reuse_port = False


def main(argv=None):
    ap = argparse.ArgumentParser(prog="stl2curves.studio", description=__doc__.split("\n\n")[0])
    ap.add_argument("mesh", nargs="?", help="an STL file to open straight away")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true", help="don't open the page in a browser")
    a = ap.parse_args(argv)
    _clear_old_folders()
    folder = Path(tempfile.mkdtemp(prefix=FOLDER_PREFIX))
    if not stages.FOLDER:
        stages.FOLDER = str(folder / "stages")
    session = Session(folder)
    if a.mesh:
        session.path = folder / Path(a.mesh).name
        session.path.write_bytes(Path(a.mesh).read_bytes())
        session.auto = True
    try:
        server = _Server(("127.0.0.1", a.port), _handler(session))
    except OSError:     # a studio is already running there: take any free port
        server = _Server(("127.0.0.1", 0), _handler(session))
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"stl2curves studio at {url}  (Ctrl+C to stop)", flush=True)
    if not a.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        workers.finish()
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
