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


def test_repaired_badge(window, meshes):
    """A file the engine had to repair says so; one it couldn't fully repair
    lists what's left."""
    from mmm_app.preview import REPAIRED_TIP

    m = window.model
    m.add([meshes["was_repaired.stl"], meshes["repaired_issues.stl"], meshes["one.stl"]])
    assert wait_until(lambda: all(j.status == JobStatus.READY for j in m.jobs))
    m.convert_all()
    assert wait_until(lambda: m.is_idle)
    jp = window.preview.preview
    by_name = {j.file_name: j for j in m.jobs}
    assert by_name["was_repaired.stl"].result.repaired == ["read back and saved again"]
    assert by_name["one.stl"].result.repaired == []
    m.select(by_name["was_repaired.stl"].id)
    assert wait_until(lambda: jp.check_pill._text == "Repaired automatically")
    tip = jp.check_pill.toolTip()
    assert tip.startswith(REPAIRED_TIP) and "Compared with the mesh: 99.8% of the surface" in tip
    m.select(by_name["repaired_issues.stl"].id)
    assert wait_until(lambda: jp.check_pill._text == "1 thing to check")
    assert jp.check_pill.toolTip().startswith("The app repaired what it could")
    m.select(by_name["one.stl"].id)
    assert wait_until(lambda: jp.check_pill._text == "File checked")


def test_badge_when_the_shape_strays_from_the_mesh(window, meshes):
    m = window.model
    m.add([meshes["rough.stl"]])
    assert wait_until(lambda: all(j.status == JobStatus.READY for j in m.jobs))
    m.convert_all()
    assert wait_until(lambda: m.is_idle)
    jp = window.preview.preview
    m.select(m.jobs[0].id)
    assert wait_until(lambda: jp.check_pill._text == "Doesn't match the mesh everywhere")
    assert "the largest gap is 1.3 mm" in jp.check_pill.toolTip()


def test_fine_detail_estimate_matches_engine():
    import sys

    sys.path.insert(0, os.path.join(ROOT, "engine"))
    smooth = pytest.importorskip("mmm.smooth")
    from mmm_app import detail

    assert (detail.FINE_START, detail.FINE_TRIS_PER_CAGE_VERT, detail.FINE_MAX_CAGE) == (
        smooth.FINE_START, smooth.FINE_TRIS_PER_CAGE_VERT, smooth.FINE_MAX_CAGE)
    for n in (10_000, 600_000, 2_060_000, 20_000_000):
        for q in (0.0, 0.7, 0.85, 0.9, 0.97, 1.0):
            assert detail.fine_patches(n, q) == 4 * smooth.fine_cage_budget(n, q)


def test_fine_detail_mac_app_matches():
    """The Mac app has its own copy of the numbers (app/Sources/Models.swift)."""
    import re

    from mmm_app import detail

    with open(os.path.join(ROOT, "app", "Sources", "Models.swift"), encoding="utf-8") as fh:
        swift = fh.read()

    def const(name):
        return float(re.search(rf"static let {name} = ([0-9.]+)", swift).group(1))

    assert const("start") == detail.FINE_START
    assert const("trianglesPerCageVertex") == detail.FINE_TRIS_PER_CAGE_VERT
    assert const("maxCage") == detail.FINE_MAX_CAGE
    assert const("kbPerPatch") == detail.KB_PER_PATCH
    assert const("normalPatches") == detail.NORMAL_PATCHES
    assert const("warnRatio") == detail.WARN_RATIO


def test_fine_detail_estimate():
    from mmm_app import detail

    assert detail.estimate(2_060_000, 0.7, "precise") is None  # default quality: no fine detail
    patches, mb = detail.estimate(2_060_000, 1.0, "precise")
    assert patches > 150_000 and 80 < mb < 200
    assert detail.estimate(2_060_000, 1.0, "cad") is None
    assert detail.estimate(2_060_000, 1.0, "smooth", max_faces=4000) is None
    assert detail.estimate(100_000, 1.0, "smooth") is None  # small meshes stay normal
    assert detail.estimate(None, 1.0, "smooth") is None


def test_fine_detail_warning_in_panel(window, meshes):
    m, s, panel = window.model, window.settings, window.settings_panel
    s.preset = "precise"
    s.quality = 1.0
    m.add([meshes["one.stl"]])
    assert wait_until(lambda: all(j.status == JobStatus.READY for j in m.jobs))
    assert panel.detail_warning.isHidden()
    m.add([meshes["dense_sculpt.stl"]])
    assert wait_until(lambda: all(j.status == JobStatus.READY for j in m.jobs))
    assert not panel.detail_warning.isHidden()
    text = panel.detail_text.text()
    assert "dense_sculpt.stl" in text and "million triangles" in text and " MB" in text
    s.quality = 0.7
    assert panel.detail_warning.isHidden()
    s.quality = 1.0
    s.preset = "cad"
    assert panel.detail_warning.isHidden()
    s.preset = "smooth"
    assert not panel.detail_warning.isHidden()
    m.remove(next(j for j in m.jobs if "dense" in j.path))
    assert wait_until(lambda: panel.detail_warning.isHidden())


def test_auto_quality_slider(window, meshes):
    m, s, panel = window.model, window.settings, window.settings_panel
    s.preset = "auto"
    s.autoQuality = True
    s.quality = 0.4
    m.add([meshes["dense_sculpt.stl"]])
    assert wait_until(lambda: all(j.status == JobStatus.READY for j in m.jobs))
    m.select(m.jobs[0].id)
    panel.refresh()
    # the slider shows the quality picked for the selected file
    assert panel.slider.value() == 600 and panel.auto_q.isHidden()
    assert panel.quality_word.text() == "Auto · 60%" and "dense_sculpt.stl" in panel.quality_hint.text()
    # moving the slider: one quality for every file
    panel.slider.setValue(250)
    assert not s.autoQuality and s.quality == 0.25 and not panel.auto_q.isHidden()
    assert panel.quality_word.text() == "Fast"
    panel.auto_q.click()
    assert s.autoQuality and panel.slider.value() == 600 and panel.auto_q.isHidden()
    s.autoQuality = False
    s.preset = "cad"
    assert panel.auto_q.isHidden() and panel.slider.value() == 250


def test_auto_used_mark_only_for_auto_runs(window, meshes):
    m, s, panel = window.model, window.settings, window.settings_panel
    s.preset = "cad"
    m.add([meshes["one.stl"]])
    assert wait_until(lambda: m.jobs and m.jobs[0].status == JobStatus.READY)
    m.select(m.jobs[0].id)
    m.convert_all()
    assert wait_until(lambda: m.jobs[0].status == JobStatus.DONE)
    s.preset = "auto"
    panel.refresh()
    assert not any(c.auto_picked for c in panel.preset_cards)
    m.reconvert(m.jobs[0])
    assert wait_until(lambda: m.jobs[0].status == JobStatus.DONE)
    panel.refresh()
    assert [c.preset.value for c in panel.preset_cards if c.auto_picked] == ["cad"]



def test_choosing_the_valid_version(window, meshes):
    m = window.model
    m.add([meshes["rough_twoways.stl"], meshes["one.stl"]])
    assert wait_until(lambda: all(j.status == JobStatus.READY for j in m.jobs))
    m.convert_all()
    assert wait_until(lambda: m.is_idle)
    jp = window.preview.preview
    job = next(j for j in m.jobs if j.file_name == "rough_twoways.stl")
    m.select(job.id)
    assert wait_until(lambda: jp.choice_card.isVisible())
    assert "Two versions" in jp.choice_title.text() and "Valid solid" in jp.choice_text.text()
    assert jp.closest_btn.isChecked() and not jp.valid_btn.isChecked()
    assert jp.check_pill._text == "Problem found" and jp.out_path() == job.output_preview
    jp.valid_btn.click()
    assert job.result.use_alternative and jp.valid_btn.isChecked() and not jp.closest_btn.isChecked()
    assert jp.out_path() == job.result.alternative.preview
    assert wait_until(lambda: jp.check_pill._text != "Problem found")
    jp.closest_btn.click()
    assert not job.result.use_alternative
    # a file with nothing to choose shows no choice
    m.select(next(j for j in m.jobs if j.file_name == "one.stl").id)
    assert wait_until(lambda: not jp.choice_card.isVisible())


def test_long_lists_of_touching_parts_are_cut_short():
    """A 3MF of 334 touching parts listed every name in the question, and
    its buttons were off the screen."""
    from mmm_app.main_window import short_name_list

    assert short_name_list([["A", "B"], ["C"]]) == "A, B; C"
    names = [[f"Body{i}" for i in range(334)]]
    text = short_name_list(names)
    assert text == "Body0, Body1, Body2, Body3, Body4, Body5, Body6, Body7 and 326 more"
    assert short_name_list([["A", "B", "C"], ["D", "E"]], limit=4) == "A, B, C; D and 1 more"
