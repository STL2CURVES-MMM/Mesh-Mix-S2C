"""Manifold Mesh Mix conversion engine."""
__version__ = "1.5.1"


def build_id():
    """The commit the engine was built from (written into _build.py when it
    is frozen; read from git when it runs from the source tree), or ''."""
    try:
        from ._build import BUILD

        if BUILD:
            return BUILD
    except ImportError:
        pass
    try:
        import os
        import subprocess

        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=os.path.dirname(__file__),
                              capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return ""
