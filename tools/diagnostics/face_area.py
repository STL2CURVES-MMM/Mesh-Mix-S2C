"""face_area.py mesh tol q region... - build the shell once; per region: triangle area, face area (GProp), tessellated area, validity; save faces"""
import sys; sys.path.insert(0, "engine")
import numpy as np
from mmm.pipeline import load_and_prepare
from mmm import cad, fidelity
from mmm.topology import RegionGraph
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.BRepCheck import BRepCheck_Analyzer
b, info = load_and_prepare(sys.argv[1]); s = b[0][0]; tol = float(sys.argv[2])
seg = cad.Segmenter(s.V, s.F, s.topo, tol); seg.detail = float(sys.argv[3]); seg.run()
labels, regions = cad.split_pinched_regions(seg, seg.label.copy(), seg.regions)
rg = RegionGraph(s.V, s.F, labels, s.topo)
asm = cad.CADAssembler(s.V, rg, regions, tol)
faces = asm.build(lambda f: None)
cad._settle_tolerances(asm.shell_from_faces(faces), tol)
out = sys.argv[5] if len(sys.argv) > 5 else "/tmp"
for R in map(int, sys.argv[4].split(",")):
    f = asm.region_faces.get(R)
    tri = seg.fa[labels == R].sum()
    if f is None:
        print(R, "no face"); continue
    g = GProp_GProps(); BRepGProp.SurfaceProperties_s(f, g)
    V, F, _ = fidelity.tessellate_faces([f], tol * 0.25)
    ta = fidelity._tri_normals(V, F)[1].sum() if len(F) else 0
    print(f"region {R} {regions[R].kind}: triangles {tri:.2f} mm2, face {g.Mass():.2f}, tessellated {ta:.2f} ({len(F)} tris), valid {BRepCheck_Analyzer(f).IsValid()}")
    BRepTools.Write_s(f, f"{out}/face{R}.brep")
