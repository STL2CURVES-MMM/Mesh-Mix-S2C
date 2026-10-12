"""Manifold Mesh Mix for Windows and Linux.

The window is written with Qt (PySide6). The conversion itself is done by
the same engine as the Mac app (engine/), which runs as a separate program
and reports its progress as JSON lines.
"""
from .version import __version__

APP_NAME = "Manifold Mesh Mix"
REPO = "TUNA-THE-FIRST/Manifold-Mesh-Mix"
REPO_URL = f"https://github.com/{REPO}"

__all__ = ["APP_NAME", "REPO", "REPO_URL", "__version__"]
