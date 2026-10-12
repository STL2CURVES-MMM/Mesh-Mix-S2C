"""Settings, preview files, version, and the window itself."""
import os
import struct

import numpy as np
import pytest
from PySide6.QtCore import QSettings

from conftest import wait_until
from mmm_app import __version__
from mmm_app.model import JobStatus
from mmm_app.settings import Settings
from mmm_app.viewport import PreviewMesh

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_version_matches_version_file():
    with open(os.path.join(ROOT, "VERSION"), encoding="utf-8") as fh:
        assert fh.read().strip() == __version__


def test_settings_defaults_and_persistence(qapp, tmp_path):
    path = str(tmp_path / "s.ini")
    s = Settings(QSettings(path, QSettings.Format.IniFormat))
    assert (s.preset, s.quality, s.subfolderName, s.splitParts, s.parallelJobs, s.checkUpdates) == \
        ("auto", 0.7, "STEP Files", True, 2, True)
    changed = []
    s.changed.connect(changed.append)
    s.preset = "smooth"
    s.quality = 0.33
    s.replaceExisting = True
    s.parallelJobs = 99
    s.preset = "smooth"  # no change, no signal
    s.sync()
    assert changed == ["preset", "quality", "replaceExisting", "parallelJobs"]
    t = Settings(QSettings(path, QSettings.Format.IniFormat))
    assert (t.preset, t.quality, t.replaceExisting, t.parallelJobs) == ("smooth", 0.33, True, 6)


def test_settings_ignore_bad_values(qapp, tmp_path):
    q = QSettings(str(tmp_path / "bad.ini"), QSettings.Format.IniFormat)
    for k, v in {"preset": "turbo", "quality": "abc", "replaceExisting": "maybe", "parallelJobs": "-3",
                 "unit": "furlong", "maxFaces": "x"}.items():
        q.setValue(k, v)
    s = Settings(q)
    assert (s.preset, s.quality, s.replaceExisting, s.parallelJobs, s.unit, s.maxFaces) == ("auto", 0.7, False, 1, "mm", 0)


def write_preview(path, P, I, L=(), N=None, C=None):
    P = np.asarray(P, np.float32).reshape(-1, 3)
    I = np.asarray(I, np.uint32).reshape(-1, 3)
    L = np.asarray(L, np.float32).reshape(-1, 3)
    N = np.zeros_like(P) if N is None else N
    C = np.full((len(P), 4), 255, np.uint8) if C is None else C
    with open(path, "wb") as fh:
        fh.write(b"MMMP" + struct.pack("<IIII", 1, len(P), len(I), len(L)))
        for a in (P, N, C, I, L):
            fh.write(np.ascontiguousarray(a).tobytes())


def test_preview_mesh_load(tmp_path):
    p = tmp_path / "ok.mmmp"
    write_preview(p, [0, 0, 0, 1, 0, 0, 0, 2, 3], [0, 1, 2], [0, 0, 0, 1, 0, 0])
    m = PreviewMesh.load(str(p))
    assert (m.vertex_count, m.triangle_count, m.line_count) == (3, 1, 2)
    assert list(m.bbox_min) == [0, 0, 0] and list(m.bbox_max) == [1, 2, 3]


def test_preview_mesh_rejects_damaged_files(tmp_path):
    bad_index = tmp_path / "idx.mmmp"
    write_preview(bad_index, [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 7])
    assert PreviewMesh.load(str(bad_index)) is None
    short = tmp_path / "short.mmmp"
    write_preview(short, [0, 0, 0, 1, 0, 0, 0, 1, 0], [0, 1, 2])
    short.write_bytes(short.read_bytes()[:-5])
    assert PreviewMesh.load(str(short)) is None
    (tmp_path / "junk.mmmp").write_bytes(b"not a preview at all")
    assert PreviewMesh.load(str(tmp_path / "junk.mmmp")) is None
    assert PreviewMesh.load(str(tmp_path / "missing.mmmp")) is None


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    monkeypatch.setenv("MMM_SETTINGS_FILE", str(tmp_path / "window.ini"))
    from mmm_app import theme
    from mmm_app.main_window import MainWindow

    s = Settings()
    qapp.setStyleSheet(theme.stylesheet())
    w = MainWindow(s)
    w.show()
    yield w
    w.updating = True  # skip the "quit without exporting" question
    w.close()


def test_window_full_flow(window, meshes, tmp_path):
    """Add, convert, export with a name clash, in the real window."""
    m, s = window.model, window.settings
    s.outputMode = "custom"
    s.outputFolder = str(tmp_path / "out")
    m.add([meshes["one.stl"], meshes["my_parts.3mf"]])
    assert wait_until(lambda: all(j.status == JobStatus.READY for j in m.jobs))
    assert window.settings_panel.convert.text() == "Convert 2 files"
    m.convert_all()
    assert wait_until(lambda: m.is_idle)
    assert window.settings_panel.export.text() == "Export 3 STEP files…"
    os.makedirs(s.outputFolder)
    open(os.path.join(s.outputFolder, "one.step"), "w").close()
    s.replaceExisting = False
    m.request_export(m.unexported_jobs)
    d = window.export_dialog
    assert d is not None and len(d.items) == 3
    d.start_export()
    assert d.clash.isVisible() and d.clash.name.text() == "one 2"
    d.rename_clash()
    assert wait_until(lambda: window.export_dialog is None)
    assert sorted(os.listdir(s.outputFolder)) == ["my_parts - Left.step", "my_parts - Right.step", "one 2.step", "one.step"]
    assert not m.unexported_jobs


def test_export_dialog_replace_and_cancel(window, meshes, tmp_path):
    m, s = window.model, window.settings
    s.outputMode = "custom"
    s.outputFolder = str(tmp_path / "out")
    os.makedirs(s.outputFolder)
    target = os.path.join(s.outputFolder, "one.step")
    with open(target, "w") as fh:
        fh.write("old")
    m.add([meshes["one.stl"]])
    assert wait_until(lambda: m.jobs[0].status == JobStatus.READY)
    m.convert_all()
    assert wait_until(lambda: m.is_idle)
    s.replaceExisting = False
    m.request_export(m.jobs)
    d = window.export_dialog
    d.start_export()
    assert d.clash.isVisible()
    d.cancel_clash()
    assert not d.clash.isVisible() and d.isVisible()
    d.start_export()
    d.replace_clash()
    assert wait_until(lambda: window.export_dialog is None)
    with open(target) as fh:
        assert fh.read() != "old"


def test_export_dialog_needs_folder(window, meshes):
    m, s = window.model, window.settings
    m.add([meshes["one.stl"]])
    assert wait_until(lambda: m.jobs[0].status == JobStatus.READY)
    m.convert_all()
    assert wait_until(lambda: m.is_idle)
    s.outputMode = "custom"
    s.outputFolder = ""
    m.request_export(m.jobs)
    d = window.export_dialog
    assert not d.export_btn.isEnabled() and d.footer_text.text() == "Choose a folder first"
    d.items[0].name = "  "
    s.outputMode = "sameFolder"
    d.refresh_footer()
    assert not d.export_btn.isEnabled()
    d.reject()


def test_accent_change_restyles(window):
    window.settings.accent = "pink"
    from mmm_app import theme

    assert theme.accent().name() == theme.ACCENT_COLORS["pink"].name()


def test_preview_switches_to_step_view(window, meshes):
    m = window.model
    m.add([meshes["one.stl"]])
    assert wait_until(lambda: m.jobs[0].status == JobStatus.READY)
    jp = window.preview.preview
    assert jp.mode == "mesh"
    m.convert_all()
    assert wait_until(lambda: m.is_idle)
    assert jp.mode == "result" and jp.export_btn.isVisible() and jp.check_pill.isVisible()
