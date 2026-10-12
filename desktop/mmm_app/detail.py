"""The heads-up for very detailed conversions.

At the top of the quality slider the engine keeps the fine detail of dense
meshes (engine/mmm/smooth.py, fine_cage_budget): the number of surfaces then
grows with the mesh instead of stopping at a fixed limit. For a big sculpt
that means a much bigger STEP file and a longer conversion, so the settings
panel says so before converting. The numbers here mirror the engine's.
"""
from PySide6.QtCore import QLocale

FINE_START = 0.85
FINE_TRIS_PER_CAGE_VERT = 43
FINE_MAX_CAGE = 60000
PATCHES_PER_CAGE_VERT = 4
KB_PER_PATCH = 0.65        # STEP file size per fitted surface patch (measured)
NORMAL_PATCHES = 16000     # about the most one piece gets without fine detail
WARN_RATIO = 3             # warn when a file gets this much bigger than usual
PRESETS = ("auto", "smooth", "precise")  # the presets that make smooth surfaces


def fine_patches(triangles, quality):
    """Surface patches the engine uses for one piece of this many triangles
    (0 when fine detail is off at this quality)."""
    q = min(max(float(quality), 0.0), 1.0)
    if q <= FINE_START or not triangles:
        return 0
    c = 10.0 ** ((q - 1.0) / (1.0 - FINE_START)) / FINE_TRIS_PER_CAGE_VERT
    return PATCHES_PER_CAGE_VERT * int(min(FINE_MAX_CAGE, triangles * c))


def estimate(triangles, quality, preset, max_faces=0):
    """(patches, megabytes) when this file will be far bigger and slower
    than usual at these settings, else None. An upper bound: CAD-like parts
    and small pieces don't use fine detail."""
    if preset not in PRESETS or max_faces:
        return None
    n = fine_patches(triangles, quality)
    if n < WARN_RATIO * NORMAL_PATCHES:
        return None
    return n, n * KB_PER_PATCH / 1000.0


def warning_text(jobs, quality, preset, max_faces=0):
    """The panel's warning for the files in the list, or None."""
    hits = []
    for j in jobs:
        e = estimate(j.triangles, quality, preset, max_faces)
        if e:
            hits.append((e[1], j))
    if not hits:
        return None
    hits.sort(key=lambda h: -h[0])
    mb, job = hits[0]
    loc = QLocale()
    size = loc.toString(float(round(mb)), "f", 0) + " MB"
    millions = job.triangles / 1e6
    tris = loc.toString(millions, "f", 1) + " million" if millions >= 1 else loc.toString(int(job.triangles))
    others = len(hits) - 1
    text = (f"Best quality keeps the fine surface detail of dense meshes. “{job.file_name}” has {tris} triangles, "
            f"so its STEP file can grow to about {size}, several times the usual size, and converting it takes "
            f"a lot longer.")
    if others:
        text += f" {others} more file{'s are' if others != 1 else ' is'} very dense too."
    return text + " Move the slider a little to the left for a smaller, faster file."
