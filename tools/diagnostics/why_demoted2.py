"""why_demoted2.py mesh tol q - which regions the CAD build replaces each round, and why (failed to build / inaccurate)"""
import sys; sys.path.insert(0, "engine")
import numpy as np
from mmm.pipeline import load_and_prepare
from mmm import cad
b, info = load_and_prepare(sys.argv[1]); s = b[0][0]; tol = float(sys.argv[2])
seg = cad.Segmenter(s.V, s.F, s.topo, tol); seg.detail = float(sys.argv[3]); seg.run()
regs0 = list(seg.regions)
orig_inacc = cad._inaccurate_regions
def inacc(seg_, labels, asm, tol_, **kw):
    out = orig_inacc(seg_, labels, asm, tol_, **kw)
    from mmm.fidelity import Surface, Verdict, mesh_surface, sample, tessellate_faces
    items = [(r, f) for r, f in asm.region_faces.items() if r not in kw.get("skip", ())]
    rng = np.random.default_rng(0)
    SV, SF, owner = tessellate_faces([f for _, f in items], tol_ * 0.25)
    own_region = np.array([r for r, _ in items], dtype=np.int64)[owner]
    mesh, _, spacing = mesh_surface(seg_.V, seg_.F)
    allow = max(tol_, mesh.deflection, mesh.noise)
    shape = Surface(SV, SF, max(spacing, tol_ * 0.5), rng)
    n = int(np.clip(20 * len(seg_.F), 20_000, 150_000))
    Q, qt = sample(SV, SF, n, rng); dq, _ = mesh.nearest(Q, exact_above=0.5 * allow)
    P, pt = sample(seg_.V, seg_.F, n, rng); dp, _ = shape.nearest(P, exact_above=0.5 * allow)
    print("allow", allow, "tol", tol_)
    for name, reg_of, d, X in (("face->mesh", own_region[qt], dq, Q), ("mesh->shape", labels[pt], dp, P)):
        for r in sorted(out):
            sel = reg_of == r
            if not sel.any(): continue
            dd = d[sel] / allow
            k = np.argmax(dd)
            print(f"   {name} region {r}: {sel.sum()} pts, >3: {(dd>3).sum()}, >8: {(dd>8).sum()}, max {dd.max():.1f} at {np.round(X[sel][k],2)}")
        print(f"   {name} whole shell: >3 {(d/allow>3).sum()} (limit {0.008*len(d):.0f}), >8 {(d/allow>8).sum()} (limit {0.0008*len(d):.0f})")
    from OCP.BRepCheck import BRepCheck_Analyzer
    inv = [r for r, f in asm.region_faces.items() if not BRepCheck_Analyzer(f).IsValid()]
    print("invalid faces:", [(r, asm_regs[0][r].kind, len(asm_regs[0][r].faces)) for r in inv])
    import os
    save = os.environ.get("SAVE_DIR")
    if save:
        from OCP.BRepTools import BRepTools
        rnd = len([n for n in os.listdir(save) if n.startswith("round")]) if os.path.isdir(save) else 0
        d = os.path.join(save, "round%d" % rnd); os.makedirs(d, exist_ok=True)
        for r in set(inv) | set(out):
            if r in asm.region_faces:
                BRepTools.Write_s(asm.region_faces[r], os.path.join(d, "face%d.brep" % r))
    print("failed to build:", [(r, asm_regs[0][r].kind, hasattr(asm_regs[0][r].prim, 'motion'), err) for r, err in asm.failed_regions])
    print("inaccurate:", [(r, asm_regs[0][r].kind, hasattr(asm_regs[0][r].prim, 'motion'), len(asm_regs[0][r].faces)) for r in sorted(out)])
    return out
cad._inaccurate_regions = inacc
asm_regs = [None]
orig_asm = cad.CADAssembler
class A(orig_asm):
    def __init__(self, V, rg, regions, *a, **k):
        asm_regs[0] = regions
        super().__init__(V, rg, regions, *a, **k)
cad.CADAssembler = A
cad.build_cad_shell(s, tol, detail=float(sys.argv[3]), seg=seg)
