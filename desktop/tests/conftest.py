"""Shared test setup: no real window system, the fake engine, a throwaway
settings file."""
import os
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DESKTOP = os.path.dirname(HERE)
sys.path.insert(0, DESKTOP)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["MMM_ENGINE_PYTHON"] = sys.executable
os.environ["MMM_ENGINE_SCRIPT"] = os.path.join(HERE, "fake_engine.py")
os.environ["MMM_NO_SINGLE_INSTANCE"] = "1"
os.environ.pop("MMM_SELFTEST", None)
os.environ.pop("MMM_UPDATE_URL", None)
os.environ.pop("APPIMAGE", None)


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def settings(qapp, tmp_path):
    from PySide6.QtCore import QSettings

    from mmm_app.settings import Settings

    return Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


class FakeUI:
    def __init__(self):
        self.confirm = True
        self.asked = []
        self.folder = None
        self.touching = "split"

    def confirm_discard(self, count, action):
        self.asked.append((count, action))
        return self.confirm

    def choose_output_folder(self):
        return self.folder

    def ask_touching(self, job):
        self.asked.append(("touching", job.file_name))
        return self.touching


@pytest.fixture
def model(qapp, settings):
    from mmm_app.model import AppModel

    m = AppModel(settings, ui=FakeUI())
    yield m
    m.shutdown()


def wait_until(cond, timeout=20.0):
    from PySide6.QtWidgets import QApplication

    end = time.time() + timeout
    while time.time() < end:
        QApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    QApplication.processEvents()
    return cond()


@pytest.fixture
def meshes(tmp_path):
    """Input files the fake engine reacts to (by name)."""
    d = tmp_path / "in"
    d.mkdir()
    out = {}
    for name in ("one.stl", "my_parts.3mf", "will_crash.stl", "has_error.stl", "very_slow.stl", "broken.stl",
                 "with_issues.stl", "Part.stl", "Part.3mf", "dense_sculpt.stl",
                 "was_repaired.stl", "repaired_issues.stl", "holes.stl", "overlap.stl", "overlap_mergefail.stl",
                 "crossing.stl", "touching.3mf"):
        p = d / name
        p.write_text("solid x\nendsolid x\n")
        out[name] = str(p)
    return out
