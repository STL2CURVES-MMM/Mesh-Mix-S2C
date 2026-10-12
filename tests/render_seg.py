"""Render the CAD segmentation of a model: one color per region,
hue by surface kind (plane=gray-blue, cylinder=green, cone=orange,
sphere=red, torus=purple)."""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "engine"))
sys.path.insert(0, HERE)
import numpy as np
from mmm.meshio import load_and_prepare
from mmm.cad import segment_shell, summarize
from mmm.report import Reporter
from mmm.stepio import _write_preview
from render import render, read_preview

path, out = sys.argv[1], sys.argv[2]
tolf = float(sys.argv[3]) if len(sys.argv) > 3 else 5e-4
view = sys.argv[4] if len(sys.argv) > 4 else "iso"
rep = Reporter(quiet=True)
bodies, info = load_and_prepare(path, report=rep)
tol = tolf * info.diag
base = {"plane": (0.55, 0.62, 0.75), "cylinder": (0.3, 0.8, 0.4), "cone": (1.0, 0.6, 0.2),
        "sphere": (0.95, 0.3, 0.3), "torus": (0.7, 0.4, 0.95)}
P_all, N_all, C_all, I_all = [], [], [], []
off = 0
rng = np.random.default_rng(0)
only = int(os.environ.get("SHELL_INDEX", "-1"))
shells = [s for b in bodies for s in b]
if only >= 0:
    shells = [shells[only]]
for shell in shells:
    seg, regions = segment_shell(shell, tol, rep)
    print(summarize(regions))
    V, F = shell.V, shell.F
    lab = seg.label
    cols = np.zeros((len(regions), 3))
    for i, r in enumerate(regions):
        b = np.array(base.get(r.kind, (0.8, 0.8, 0.8)))
        cols[i] = np.clip(b * rng.uniform(0.6, 1.15), 0, 1)
        if r.weak:
            cols[i] = (0.25, 0.25, 0.25)
    P = V[F].reshape(-1, 3)
    fn = np.cross(V[F[:, 1]] - V[F[:, 0]], V[F[:, 2]] - V[F[:, 0]])
    fn /= np.maximum(np.linalg.norm(fn, axis=1), 1e-30)[:, None]
    N = np.repeat(fn, 3, axis=0)
    C = np.repeat((cols[lab] * 255).astype(np.uint8), 3, axis=0)
    C = np.hstack([C, np.full((len(C), 1), 255, np.uint8)])
    I = np.arange(len(P)).reshape(-1, 3) + off
    off += len(P)
    P_all.append(P); N_all.append(N); C_all.append(C); I_all.append(I)
_write_preview(out + ".mmmp", P_all, N_all, C_all, I_all, np.zeros((0, 3)))
vd = {"iso": (1.0, -1.3, 0.9), "top": (0.25, -0.45, 1.6), "bottom": (0.6, -0.9, -1.2), "back": (-1.0, 1.3, 0.7)}[view]
render(*read_preview(out + ".mmmp"), out, os.path.basename(path), view_dir=vd)
