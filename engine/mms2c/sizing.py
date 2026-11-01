"""
Work out the size a part was really designed at.

People design with round numbers (a 5 mm hole, a 0.5 mm fillet, a 1/4" slot). An STL can
be off from that by one overall factor: scaled 99% in a slicer for fit, exported in
inches or centimetres, and so on. Rather than round each measurement separately, find
the one factor that turns the most measurements into round numbers.
"""
import math
from dataclasses import dataclass

import numpy as np

INCH = 25.4

# (label, factor to multiply the file's numbers by to get the design's numbers, unit)
# The factor is applied first; "unit" says which round numbers to expect afterwards.
CANDIDATES = [("as is", 1.0, "mm")]
CANDIDATES += [(f"scaled to {p}%", 100.0 / p, "mm") for p in
               (90, 95, 97, 97.5, 98, 98.5, 99, 99.5, 100.5, 101, 101.5, 102, 102.5, 103, 105, 110)]
CANDIDATES += [("drawn in inches", 1.0, "inch"),
               ("file in inches", INCH, "mm"),
               ("file in centimetres", 10.0, "mm"),
               ("file in metres", 1000.0, "mm"),
               ("file in tenths of a mm", 0.1, "mm")]


def roundness(v, unit):
    """How round a value is: 1 for a multiple of 0.5 mm (1/16"), 0.5 for 0.1 mm (1/64"
    or 0.01"), else 0. Tolerance 0.15% of the value (min 0.002 mm), but never more than
    5% of the step, or every big number would count as round."""
    if v <= 0:
        return 0.0
    if unit == "inch":
        x, steps = v / INCH, ((1 / 16, 1.0), (0.05, 0.5), (1 / 64, 0.5), (0.01, 0.5))
    else:
        x, steps = v, ((0.5, 1.0), (0.1, 0.5))
    rel = max(0.0015 * x, 0.002 / (INCH if unit == "inch" else 1))
    for step, score in steps:
        n = round(x / step)
        if n != 0 and abs(x - step * n) <= min(rel, 0.05 * step):
            return score
    return 0.0


@dataclass
class SizeGuess:
    label: str
    factor: float
    unit: str
    score: float
    baseline: float
    examples: list        # (file value, design value) pairs that became round

    def describe(self):
        if self.label == "as is":
            return "design size: looks drawn at the file's own size (no hidden scaling found)"
        ex = ", ".join(f"{a:g} -> {b:g}" for a, b in self.examples[:5])
        unit = "inches" if self.unit == "inch" else "mm"
        return (f"design size: looks {self.label} (x{self.factor:.4g} gives round {unit}: {ex}). "
                f"Use --true-size to rebuild at that size.")


def measurements(parts):
    """(values, weights): radii of curved patches, and gaps between big parallel flat faces,
    over all (mesh, features) parts of a file."""
    values, weights = [], []
    for mesh, features in parts:
        _measure(mesh, features, values, weights)
    return _distinct(values, weights)


def _measure(mesh, features, values, weights):
    for f in features:
        m = f.model
        r = getattr(m, "r", None)
        if r is None and getattr(m, "line", None):
            r = m.line[0] if m.line[1] == 0 else None
        if r is None and getattr(m, "circle", None):
            r = m.circle[2]
        if r:
            values += [2 * r if f.kind in ("revolve", "trimmed") and f.span >= 2 * math.pi else r]
            weights.append(math.sqrt(float(mesh.farea[f.facets].sum())))
    big = np.argsort(-mesh.farea)[:40]
    big = [i for i in big if mesh.farea[i] > 1e-3 * mesh.farea.sum()]
    for i, a in enumerate(big):
        for b in big[i + 1:]:
            if mesh.fn[a] @ mesh.fn[b] < -0.99999:
                values.append(abs((mesh.fcent[b] - mesh.fcent[a]) @ mesh.fn[a]))
                weights.append(math.sqrt(min(mesh.farea[a], mesh.farea[b])))


def _distinct(values, weights):
    """One vote per distinct value."""
    order = np.argsort(values)
    v_out, w_out = [], []
    for k in order:
        if v_out and abs(values[k] - v_out[-1]) <= 1e-3 * max(values[k], 1):
            w_out[-1] = max(w_out[-1], weights[k])
        else:
            v_out.append(values[k])
            w_out.append(weights[k])
    return np.array(v_out), np.array(w_out)


def guess_size(parts):
    """The design size of a file from its (mesh, features) parts."""
    P = np.concatenate([mesh.pts[np.unique(mesh.tris)] for mesh, _ in parts])
    size = float(np.ptp(P, axis=0).max())
    values, weights = measurements(parts)
    if len(values) < 3:
        return None
    w = weights / weights.sum()

    def score(factor, unit):
        return float(sum(wi * roundness(v * factor, unit) for v, wi in zip(values, w)))

    baseline = score(1.0, "mm")
    # Only sizes a printed part could plausibly have (about 1 mm to 2 m across). A unit
    # mix-up (cm, inches, m) is only considered when the file doesn't already look well
    # designed as is: multiplying by 10 makes any 0.1 mm value "round", so it always
    # scores well otherwise.
    unusual = baseline < 0.35 or not 5 <= size <= 1000
    results = [(score(f, u), label, f, u) for label, f, u in CANDIDATES
               if 1 <= size * f <= 2000 and (unusual or not label.startswith("file in"))]
    best = max(results)
    s, label, factor, unit = best
    # Only claim hidden scaling if it explains clearly more than taking the file as is.
    if label != "as is" and not (s >= max(0.5, 1.5 * baseline) and s - baseline >= 0.2):
        s, label, factor, unit = baseline, "as is", 1.0, "mm"
    def target(v):
        """The round design value v becomes (in mm, or inches for an inch design)."""
        x = v * factor / (INCH if unit == "inch" else 1)
        for step in ((1 / 16, 0.05, 1 / 64, 0.01) if unit == "inch" else (0.5, 0.1)):
            if abs(x - step * round(x / step)) <= max(0.0015 * x, 0.002):
                return round(step * round(x / step), 4)
        return round(x, 4)

    examples = [(round(v, 4), target(v)) for v in values if roundness(v * factor, unit) >= 1.0][:8]
    return SizeGuess(label, factor, unit, s, baseline, examples)
