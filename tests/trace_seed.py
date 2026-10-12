import sys; sys.path.insert(0,'engine')
import numpy as np
from mmm.meshio import load_and_prepare
from mmm import cad
from mmm.cad import Segmenter
from mmm.report import Reporter
b,info=load_and_prepare('tests/models/cad_mixed_boolean.stl')
s=b[0][0]
seg=Segmenter(s.V,s.F,s.topo,5e-4*info.diag,Reporter(quiet=True))
C=s.V[s.F].mean(1)
rad=np.hypot(C[:,0],C[:,1])
boss=(np.abs(rad-12)<0.5)&(C[:,2]>16)&(C[:,2]<34)
orig=Segmenter._process_seed
def traced(self, faces, allow_defer=False):
    interesting = boss[faces].any()
    if interesting:
        fa=self.fa
        nbh=self.neighbourhood(faces)
        Pn,Nn,okn=self.region_points(nbh)
        print(f'seed {faces[:3]} n={len(faces)} nbh={len(nbh)} nbh_boss={boss[nbh].mean():.2f}')
        for cls in cad.CURVED:
            prim=self._robust_fit(cls,Pn[okn],Nn[okn])
            if prim is None: print('   ',cls.kind,'fit None'); continue
            sense=self.sense_of(prim,nbh); ok=self.accept(prim,sense,nbh)
            d,cs=self.face_error(prim,sense,nbh)
            reg,p2=self.grow(prim,sense,faces) if ok[:len(faces)].all() else (faces,prim)
            print(f'    {prim.describe():26s} okfrac={ok.mean():.2f} seedok={ok[:len(faces)].all()} dmax={d.max():.3g} grown={len(reg)} area={fa[reg].sum():.1f} -> {p2.describe()}')
    r=orig(self,faces,allow_defer)
    if interesting and self.regions: print('   => chosen', self.regions[-1].prim.describe(), len(self.regions[-1].faces), 'deferred' if r is False else '')
    return r
Segmenter._process_seed=traced
seg.run()
