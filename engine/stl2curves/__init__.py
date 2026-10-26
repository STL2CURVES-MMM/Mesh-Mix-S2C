"""stl2curves: convert STL meshes and 3MF projects into solid STEP files with true
curved surfaces (see README.md). The command line is `stl2curves` (convert.main)."""

__version__ = "0.4.0"


def __getattr__(name):
    # stl_to_solid and friends live in convert, which loads OpenCascade: import it only
    # when asked, so `import stl2curves` (a worker process, --version) stays light.
    if name in ("stl_to_solid", "write_step", "count", "volume", "main"):
        from . import convert
        return getattr(convert, name)
    raise AttributeError(f"module 'stl2curves' has no attribute {name!r}")
