"""Mesh Mix-S2C for Windows and Linux.

The window is written with Qt (PySide6). The conversion itself is done by
the same engine as the Mac app (engine/), which runs as a separate program
and reports its progress as JSON lines.
"""
from .version import __version__

APP_NAME = "Mesh Mix-S2C"
REPO = "STL2CURVES-MMM/Mesh-Mix-S2C"
REPO_URL = f"https://github.com/{REPO}"
# The repository's permanent number on GitHub. Update checks ask by it, because GitHub's
# API answers 404 for an organization's old name after a rename (web links and git
# redirect, the API doesn't), which would leave every installed app without updates.
REPO_ID = 1415704694
# Help → Support the Project (Buy Me a Coffee)
SUPPORT_URL = "https://buymeacoffee.com/tunathefirst"

__all__ = ["APP_NAME", "REPO", "REPO_ID", "REPO_URL", "SUPPORT_URL", "__version__"]
