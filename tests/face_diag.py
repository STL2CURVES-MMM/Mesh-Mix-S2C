"""Detailed validity diagnostics for faces of a CAD build (round 0)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import numpy as np
from mmm.meshio import load_and_prepare
from mmm.cad import Segmenter, CADAssembler, summarize, _settle_tolerances, fit_freeform_cells
from mmm.topology import RegionGraph
from mmm.report import Reporter
from OCP.BRepCheck import BRepCheck_Analyzer, BRepCheck_Face, BRepCheck_Wire, BRepCheck_Edge, BRepCheck_Vertex
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_EDGE, TopAbs_WIRE, TopAbs_VERTEX
from OCP.TopoDS import TopoDS
from OCP.BRep import BRep_Tool

def diag_face(f, label):
    out = []
    bf = BRepCheck_Face(f)
    for name in ("IntersectWires", "ClassifyWires", "OrientationOfWires"):
        st = str(getattr(bf, name)(False)).split("_")[-1]
        if st != "NoError": out.append(f"{name}={st}")
    e = TopExp_Explorer(f, TopAbs_WIRE)
    while e.More():
        w = TopoDS.Wire(e.Current()); bw = BRepCheck_Wire(w)
        for name, args in (("Closed", (False,)), ("Closed2d", (f, False)), ("Orientation", (f, False)), ("SelfIntersect", (f,))):
            try:
                r = getattr(bw, name)(*args)
                st = str(r if not isinstance(r, tuple) else r[0]).split("_")[-1]
                if st != "NoError": out.append(f"wire.{name}={st}")
            except Exception as ex:
                pass
        e.Next()
    e = TopExp_Explorer(f, TopAbs_EDGE); k = 0
    while e.More():
        ed = TopoDS.Edge(e.Current())
        be = BRepCheck_Edge(ed)
        try:
            be.InContext(f)
        except Exception:
            pass
        if not BRepCheck_Analyzer(ed).IsValid(): out.append(f"edge{k} invalid")
        tol = BRep_Tool.Tolerance_s(ed)
        if tol > 0.05: out.append(f"edge{k} tol={tol:.3g}")
        k += 1
        e.Next()
    print(label, "VALID" if BRepCheck_Analyzer(f).IsValid() else "BAD", out[:8])

if __name__ == "__main__":
    path = sys.argv[1]; mode = sys.argv[2] if len(sys.argv) > 2 else "normal"
    b, info = load_and_prepare(path)
    s = b[0][0]; tol = 5e-4 * info.diag
    seg = Segmenter(s.V, s.F, s.topo, tol, Reporter(quiet=True)); regs = seg.run()
    labels = seg.label
    if mode == "freeform":
        # replace all curved regions by freeform cells
        new, lab = [], np.empty_like(labels)
        for r in regs:
            if r.kind == "plane":
                lab[r.faces] = len(new); new.append(r)
            else:
                for c in fit_freeform_cells(seg, r.faces):
                    lab[c.faces] = len(new); new.append(c)
        regs, labels = new, lab
    print(summarize(regs))
    rg = RegionGraph(s.V, s.F, labels, s.topo)
    asm = CADAssembler(s.V, rg, regs, tol, Reporter(quiet=True))
    faces = asm.build()
    _settle_tolerances(asm.shell_from_faces(faces), tol)
    print("failed", asm.failed_regions[:3], "pole caps", list(asm.pole_caps), "sphere modes", asm.sphere_mode)
    for r, f in asm.region_faces.items():
        if regs[r].kind != "plane" or not BRepCheck_Analyzer(f).IsValid():
            diag_face(f, f"face {r} {regs[r].prim.describe()} tris={len(regs[r].faces)}")
