"""MMM's Auto choice of method (pipeline.choose_preset_body): a part whose curved surface
runs along one straight direction (parallel tubes) is a CAD part, even when its tubes
don't share an axis, so no single turn explains them (bass "body 5 holes", 2026-10-30).
The shapes are made here; no mesh is stored."""

import numpy as np


from mmm import pipeline as P  # noqa: E402


def _tube(cx, cz, r=10.0, length=150.0, n=48, m=40):
    """The side of a tube round a line parallel to y, as (centres, normals, areas)."""
    a = np.linspace(0, 2 * np.pi, n, endpoint=False) + np.pi / n
    y = (np.arange(m) + 0.5) * length / m
    A, Y = np.meshgrid(a, y)
    N = np.stack([np.cos(A), np.zeros_like(A), np.sin(A)], -1).reshape(-1, 3)
    C = np.stack([cx + r * np.cos(A), Y, cz + r * np.sin(A)], -1).reshape(-1, 3)
    w = np.full(len(C), 2 * np.pi * r / n * length / m)
    return C, N, w


def _cap(cx, cz, y0, r=10.0, n=400, seed=0):
    """A domed end: normals spread over a half sphere (what isn't swept)."""
    g = np.random.default_rng(seed)
    v = g.normal(size=(n, 3))
    v /= np.linalg.norm(v, axis=1)[:, None]
    v[:, 1] = -np.abs(v[:, 1])
    return np.array([cx, y0, cz]) + r * v, v, np.full(n, 2 * np.pi * r * r / n)


def test_parallel_tubes_count_as_swept():
    parts = [_tube(x, z) for x, z in ((0, 0), (21, 0), (0, 21), (21, 21))]
    parts += [_cap(x, z, 0.0, seed=k) for k, (x, z) in enumerate(((0, 0), (21, 0), (0, 21), (21, 21)))]
    share = P.swept_share(parts)
    assert share >= 0.8, share


def test_unswept_normals_stay_unswept():
    g = np.random.default_rng(1)
    v = g.normal(size=(5000, 3))
    v /= np.linalg.norm(v, axis=1)[:, None]
    share = P.swept_share([(30 * v, v, np.ones(len(v)))])
    assert share < 0.2, share
