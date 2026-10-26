"""Saved stages of a conversion, for trying out changes quickly (a development aid).

With the environment variable STL2CURVES_CACHE set to a folder, a conversion saves what
each of its first stages hands on, and a later conversion of the same file with the same
options takes it up from there instead of working it out again:

  mesh      the mended (and thinned) mesh, split into bodies
  analysis  the features found on each body, sized and snapped
  blends    with pipes, freeform surfaces and smooth blends added

Each saved stage is keyed to the input file's bytes, the options, and the source of the
code that produces it (and everything before it), so a stale one is never used: change
build.py and all three are reused, blends.py the first two, features.py only the mesh.
The building, sewing and checking that follow are always run.
"""

import hashlib
import inspect
import os
import pickle
import sys
from pathlib import Path

FOLDER = os.environ.get("STL2CURVES_CACHE")

# what each stage's result depends on, besides what the stages before it depend on:
# modules of this package (their whole source) or (module, function) for single functions
STAGES = {
    "mesh": ["repair", "simplify", "bodies", "read3mf", ("features", "load_stl"), ("features", "grid_noise"),
             ("convert", "_repair_parts")],
    "analysis": ["features", "fillets", "threads", "sizing"],
    "blends": ["blends", "pipes", "freeform", "extrude"],
}
ORDER = list(STAGES)
TRANSIENT = ("shared_as",)      # (a handle on a worker processes' copy: not to be kept)


def _source(item):
    package = __name__.rsplit(".", 1)[0]
    if isinstance(item, tuple):
        module = sys.modules.get(f"{package}.{item[0]}") or __import__(f"{package}.{item[0]}", fromlist=["_"])
        return inspect.getsource(getattr(module, item[1]))
    module = sys.modules.get(f"{package}.{item}") or __import__(f"{package}.{item}", fromlist=["_"])
    return Path(module.__file__).read_text(encoding="utf-8")


class Cache:
    """The saved stages of one conversion (a file and its options), or a no-op one."""

    def __init__(self, path, options):
        self.on = bool(FOLDER) and isinstance(path, (str, os.PathLike))
        if not self.on:
            return
        self.folder = Path(FOLDER)
        self.folder.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256(Path(path).read_bytes())
        h.update(repr(options).encode())
        self.keys = {}
        for stage in ORDER:
            for item in STAGES[stage]:
                h.update(_source(item).encode())
            self.keys[stage] = h.copy().hexdigest()[:24]
        self.name = Path(path).stem

    def _file(self, stage):
        return self.folder / f"{self.name}.{stage}.{self.keys[stage]}.pkl"

    def load(self, stage):
        """The saved result of a stage, or None."""
        if not self.on or not self._file(stage).exists():
            return None
        with open(self._file(stage), "rb") as f:
            return pickle.load(f)

    def save(self, stage, value, meshes=()):
        """Keep a stage's result (meshes: the Mesh objects in it, whose transient handles
        are left out of the copy)."""
        if not self.on:
            return
        held = [(m, {k: m.__dict__.pop(k) for k in TRANSIENT if k in m.__dict__}) for m in meshes]
        try:
            tmp = self._file(stage).with_suffix(".tmp")
            with open(tmp, "wb") as f:
                pickle.dump(value, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, self._file(stage))
        finally:
            for m, kept in held:
                m.__dict__.update(kept)
