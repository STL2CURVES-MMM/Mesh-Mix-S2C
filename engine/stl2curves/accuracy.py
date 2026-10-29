"""How far a rebuilt surface may stray from the mesh: one setting for the trade between a
faithful part and a finished one (a STEP whose every curve is a surface but half a
millimetre off can be worth more than one accurate to a micron and half triangles).

--accuracy MM (or the environment variable STL2CURVES_ACCURACY, which worker processes
inherit) scales every limit on the surfaces fitted where no exact design surface is sure:
smooth blends and freeform surfaces (how close to the corners, how far they may bow
between them), fillets on rough meshes, and the coarse-export cylinders and cones. Exact
surfaces on clean meshes are as true as the mesh whatever it is. The limits are written
as tuned at TUNED (0.02 mm); the default, 0.1 mm (2026-10-11), scales them by 5: a mesh
never holds an exact radius and a CAD program's own spline export strays a few
hundredths, so held to 0.02 real shapes were left as triangles and patches. Quick set
plus the gutter mount: 3,846 faces at 0.02, 3,352 at 0.05, 2,916 at 0.1, 3,978 at 0.15
(freeform surfaces fitted that loosely break the fillets beside them: CEILING), 2,993 at
0.2 with freeform held at 0.1, so 0.1 is where loosening stops paying. The measured
deviation stays small (99% of mesh corners within 0.02 mm of the solid on the gutter
mount, the GPS case back, the latch; Rak N-USB 0.008). Full regression (28 parts): 13,791
faces at 0.05, 11,664 at 0.1, 3,007 s and 2,947 s.

STL2CURVES_ACCURACY_OF ("fillets=0.05,features=0.1") holds one module's limits at an
accuracy of its own, to judge which limits gain from loosening and which only let wrong
shapes in (an experiment; it is part of the saved-stage key like the accuracy itself).
"""
import os
import sys

TUNED = 0.02        # mm: the accuracy every limit was tuned at (as written in the modules)
DEFAULT = 0.1       # mm: the accuracy used unless one is asked for
# the limits it scales, as (module, name); some are copies of others' (imported by name)
LIMITS = [("blends", "MAX_DEVIATION"), ("blends", "MAX_EDGE_GAP"),
          ("build", "MAX_DEVIATION"), ("build", "MAX_EDGE_GAP"), ("build", "HUG_SLACK"),
          ("freeform", "FIT_DEV"), ("freeform", "BULGE_MM"), ("pipes", "FIT_DEV"), ("extrude", "FIT_DEV"),
          ("features", "NOISY_MAX"), ("features", "NOISY_TYPICAL"),
          ("fillets", "FILLET_TOL"), ("fillets", "GROW_TOL"),
          ("threads", "PIECE_TOL"), ("features", "CORNER_FIT_DEV"), ("features", "SAME_TOL")]
# mm: the loosest accuracy a module's limits follow. Freeform surfaces fitted looser than
# 0.01 mm (accuracy 0.1) round over the tangent edges beside exact fillets, and the
# fillets then fail (2026-10-11: quick set + gutter mount 2,916 faces at 0.1, 3,978 at
# 0.15, 3,946 at 0.2; at 0.2 with freeform held at 0.1, 2,993)
CEILING = {"freeform": 0.1, "pipes": 0.1}
# the module a copied limit comes from (its accuracy is that module's)
HOME = {("build", "MAX_DEVIATION"): "blends", ("build", "MAX_EDGE_GAP"): "blends",
        ("extrude", "FIT_DEV"): "freeform"}


def of():
    """The modules given an accuracy of their own: {module: mm}."""
    out = {}
    for item in (os.environ.get("STL2CURVES_ACCURACY_OF") or "").split(","):
        if "=" in item:
            name, value = item.split("=", 1)
            out[name.strip()] = float(value)
    return out


def mm(module=None):
    """The accuracy asked for (mm), or the one a module's limits are held at."""
    whole = float(os.environ.get("STL2CURVES_ACCURACY") or DEFAULT)
    if module is None:
        return whole
    own = of().get(module)
    return own if own is not None else min(whole, CEILING.get(module, whole))


def scale(module):
    """What a module's limits are scaled by: its tuned values times this."""
    return mm(module) / TUNED


SCALE = mm() / TUNED        # (the modules' limits are their tuned values times this)
# module: the scale its limits were last set to (at import, what scale() gives)
_held = {module: scale(module) for module in {m for m, _ in LIMITS} | set(HOME.values())}


def set_accuracy(value):
    """Use this accuracy (mm) from now on, here and in worker processes started after
    (stop running ones first: workers.finish)."""
    global SCALE
    value = float(value)
    if value <= 0:
        raise ValueError("accuracy must be more than 0 mm")
    os.environ["STL2CURVES_ACCURACY"] = repr(value)
    for module, name in LIMITS:
        m = sys.modules.get(f"{__package__}.{module}")
        home = HOME.get((module, name), module)
        if m is not None and hasattr(m, name):
            setattr(m, name, getattr(m, name) / _held.get(home, SCALE) * scale(home))
    _held.update((module, scale(module)) for module in _held)
    SCALE = value / TUNED
