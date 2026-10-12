"""Trace hypotheses for seeds that end up in a region matching a predicate."""
import sys; sys.path.insert(0,'engine')
import numpy as np
from mmm.meshio import load_and_prepare
from mmm import cad
from mmm.cad import Segmenter
from mmm.report import Reporter
model = sys.argv[1]
b,info=load_and_prepare(model); s=b[0][0]
seg=Segmenter(s.V,s.F,s.topo,5e-4*info.diag,Reporter(quiet=True))
regs = seg.run()
target = [i for i,r in enumerate(regs) if r.kind=='torus' and r.prim.R > 50]
print('targets', [(regs[i].prim.describe(), len(regs[i].faces)) for i in target])
tfaces = set(int(f) for i in target for f in regs[i].faces)
seg2=Segmenter(s.V,s.F,s.topo,5e-4*info.diag,Reporter(quiet=True))
orig=Segmenter._process_seed
def traced(self, faces, allow_defer=False):
    interesting = any(int(f) in tfaces for f in faces)
    if interesting:
        nbh=self.neighbourhood(faces)
        Pn,Nn,okn,Wn=self.region_points(nbh, True)
        print(f'seed n={len(faces)} area={self.fa[faces].sum():.2f} nbh={len(nbh)} defer={allow_defer}')
        for cls in cad.CURVED:
            prim=self._robust_fit(cls,Pn[okn],Nn[okn],Wn[okn])
            if prim is None: print('   ',cls.kind,'fit None'); continue
            sense=self.sense_of(prim,nbh); ok=self.accept(prim,sense,nbh)
            if ok[:len(faces)].all() and ok.mean()>=0.5:
                reg,p2=self.grow(prim,sense,faces)
                p3,s3=self.simplify(p2,sense,reg)
                print(f'    {prim.describe():26s} okfrac={ok.mean():.2f} grown={len(reg)} area={self.fa[reg].sum():.1f} q={self.midpoint_dev(p2,reg):.3g} -> {p2.describe()} simplified->{p3.describe()}')
            else:
                print(f'    {prim.describe():26s} okfrac={ok.mean():.2f} seedok={ok[:len(faces)].all()} REJECT')
    r=orig(self,faces,allow_defer)
    if interesting: print('   => chosen', self.regions[-1].prim.describe() if r is not False else 'DEFERRED', len(self.regions[-1].faces) if r is not False else '')
    return r
Segmenter._process_seed=traced
seg2.run()
