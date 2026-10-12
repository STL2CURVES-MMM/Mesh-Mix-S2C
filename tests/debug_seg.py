"""Segmentation + demotion statistics for a model."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "engine"))
import numpy as np
from mmm.meshio import load_and_prepare
from mmm.cad import segment_shell, build_cad_shell, summarize, CADAssembler
from mmm.topology import RegionGraph
from mmm.report import Reporter
from OCP.BRepCheck import BRepCheck_Analyzer

path = sys.argv[1]
tolf = float(sys.argv[2]) if len(sys.argv) > 2 else 5e-4
rep = Reporter(quiet=True)
bodies, info = load_and_prepare(path, report=rep)
tol = tolf * info.diag
for shell in [s for b in bodies for s in b]:
    seg, regions = segment_shell(shell, tol, rep)
    print("segmentation:", summarize(regions), "weak:", sum(r.weak for r in regions))
    big = sorted(regions, key=lambda r: -len(r.faces))[:12]
    for r in big:
        print("   ", r.prim.describe(), len(r.faces), "tris", "weak" if r.weak else "")
    rg = RegionGraph(shell.V, shell.F, seg.label, shell.topo)
    asm = CADAssembler(shell.V, rg, regions, tol, rep)
    faces = asm.build()
    bad = {}
    for r, f in asm.region_faces.items():
        if not BRepCheck_Analyzer(f).IsValid():
            k = regions[r].kind
            bad[k] = bad.get(k, 0) + 1
    print("failed:", len(asm.failed_regions), asm.failed_regions[:4])
    print("invalid faces by kind:", bad, " unseamed:", [regions[r].prim.describe() for r in asm.unseamed][:5])
