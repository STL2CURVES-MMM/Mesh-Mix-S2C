"""The logic behind the window: names, messages, the queue, exporting."""
import errno
import os
import sys

import pytest

from conftest import wait_until
from mmm_app import model as M
from mmm_app.model import ConversionResult, ExportFailure, ExportItem, JobStatus, ReplaceError, safe_file_name


# ---------------------------------------------------------------- pure helpers

@pytest.mark.parametrize("raw, want", [
    ("Bracket", "Bracket"),
    ('a/b\\c:d*e?f"g<h>i|j', "a-b-c-d-e-f-g-h-i-j"),
    ("  .hidden. ", "hidden"),
    ("", "Part"),
    ("...", "Part"),
    ("tab\there", "tab-here"),
    ("CON", "CON_"),
    ("lpt1.part", "lpt1.part_"),
    ("Peça ção", "Peça ção"),
    ("x" * 150, "x" * 100),
])
def test_safe_file_name(raw, want):
    assert safe_file_name(raw) == want


def test_friendly_strips_exception_names():
    assert M.friendly("MeshError: The mesh is empty.") == "The mesh is empty."
    assert M.friendly("ValueError: bad") == "bad"
    assert M.friendly("Just text: here") == "Just text: here"
    assert M.friendly(None) is None


def test_crash_messages():
    assert "low on memory" in M.crash_message(-9)
    assert "crashed" in M.crash_message(-11)
    assert "signal 15" in M.crash_message(-15)
    assert "without saving" in M.crash_message(0)
    assert "code 3" in M.crash_message(3)
    assert "too big" in M.unreadable_message(-9)
    assert "couldn't be read" in M.unreadable_message(1)


def test_crash_messages_windows(monkeypatch):
    monkeypatch.setattr(M.system, "IS_WINDOWS", True)
    monkeypatch.setattr(M.system, "OS_NAME", "Windows")
    assert "Windows stopped" in M.crash_message(0xC0000017)
    assert "crashed" in M.crash_message(0xC0000005)
    assert "code 1" in M.crash_message(1)


def test_conversion_result_from_event():
    r = ConversionResult.from_event({"preset": "mixed", "valid": True, "faces": 12, "solids": 1, "file_size": 2048,
                                     "seconds": 3, "checked": True,
                                     "checks": [{"code": "gaps", "level": "warning", "message": "gap"},
                                                {"code": "x", "level": "error", "message": "bad"}, "junk"]})
    assert r.preset_title == "CAD + Smooth"
    assert r.faces == 12 and r.seconds == 3.0 and r.checked
    assert [c.message for c in r.checks] == ["gap", "bad"]
    assert r.has_errors and r.has_issues
    empty = ConversionResult.from_event({"faces": "lots", "valid": "yes"})
    assert empty.faces == 0 and empty.valid is None and not empty.has_issues


def test_export_failure_messages(monkeypatch):
    item = ExportItem(job=None, part=None, source="s", name="n")
    dest = os.path.join("some", "Folder", "n.step")
    monkeypatch.setattr(M.system, "IS_WINDOWS", False)
    assert "permission" in ExportFailure(item, dest, PermissionError(errno.EACCES, "no")).message
    # a replace that fails is only "open in another program" on Windows
    assert "permission" in ExportFailure(item, dest, ReplaceError(errno.EACCES, "denied", dest)).message
    assert "disk is full" in ExportFailure(item, dest, OSError(errno.ENOSPC, "full")).message
    assert "can't be written" in ExportFailure(item, dest, OSError(errno.EROFS, "ro")).message
    assert "couldn't be saved (boom)" in ExportFailure(item, dest, OSError(errno.EIO, "boom")).message
    monkeypatch.setattr(M.system, "IS_WINDOWS", True)
    f = ExportFailure(item, dest, ReplaceError(errno.EACCES, "denied", dest))
    assert f.in_use and not f.is_permission and "open in another program" in f.message
    assert "Windows didn't allow" in ExportFailure(item, dest, PermissionError(errno.EACCES, "no")).message


def test_copy_result_is_atomic(tmp_path):
    src = tmp_path / "src.step"
    src.write_text("NEW")
    dest = tmp_path / "out" / "deep" / "x.step"
    M.AppModel.copy_result(str(src), str(dest))
    assert dest.read_text() == "NEW"
    dest.write_text("OLD")
    M.AppModel.copy_result(str(src), str(dest))
    assert dest.read_text() == "NEW"
    assert [p.name for p in dest.parent.iterdir()] == ["x.step"]  # no temporary files left


def test_copy_result_reports_locked_destination(tmp_path, monkeypatch):
    src = tmp_path / "src.step"
    src.write_text("NEW")
    dest = tmp_path / "x.step"
    dest.write_text("OLD")

    def locked(a, b):
        raise PermissionError(errno.EACCES, "in use")

    monkeypatch.setattr(M.os, "replace", locked)
    with pytest.raises(ReplaceError):
        M.AppModel.copy_result(str(src), str(dest))
    assert dest.read_text() == "OLD"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["src.step", "x.step"]


def test_collect_files(tmp_path):
    (tmp_path / "a.STL").write_text("x")
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / ".hidden.stl").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "b.3mf").write_text("x")
    hid = tmp_path / ".git"
    hid.mkdir()
    (hid / "c.stl").write_text("x")
    files = M.AppModel.collect_files([str(tmp_path), str(tmp_path / "notes.txt"), str(tmp_path / "missing.stl")])
    assert [os.path.relpath(f, tmp_path) for f in files] == ["a.STL", os.path.join("sub", "b.3mf")]


# ---------------------------------------------------------------- queue (fake engine)

def add_and_read(model, *paths):
    model.add(list(paths))
    assert wait_until(lambda: not any(j.status == JobStatus.INSPECTING for j in model.jobs))
    return model.jobs


def test_add_inspect_convert_export(model, settings, meshes, tmp_path):
    (job,) = add_and_read(model, meshes["one.stl"])
    assert job.status == JobStatus.READY and job.triangles == 1234 and job.bbox_size == [40, 30, 20.5]
    assert job.input_preview and os.path.exists(job.input_preview)
    assert model.selection == job.id
    model.convert_all()
    assert wait_until(lambda: job.status == JobStatus.DONE)
    assert job.result.faces == 69 and job.result.checked and job.warnings == ["A small warning."]
    assert job.needs_export and model.unexported_jobs == [job]
    # nothing is saved next to the original before exporting
    assert not os.path.exists(os.path.join(os.path.dirname(job.path), "STEP Files"))
    settings.outputMode = "subfolder"
    settings.subfolderName = "Out"
    items = model.export_items([job])
    assert [i.name for i in items] == ["one"]
    done = []
    model.export(items, done.append)
    assert wait_until(lambda: done)
    assert done == [[]]
    dest = os.path.join(os.path.dirname(job.path), "Out", "one.step")
    assert os.path.exists(dest) and job.exported_path == dest and not job.needs_export


def test_engine_arguments_follow_settings(model, settings, meshes, tmp_path, monkeypatch):
    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(log))
    settings.preset = "smooth"
    settings.repair = "aggressive"
    settings.unit = "in"
    settings.quality = 0.25
    settings.removeDebris = False
    settings.useCustomTolerance = True
    settings.customTolerance = 0.02
    settings.maxFaces = 4000
    (job,) = add_and_read(model, meshes["one.stl"])
    model.enqueue(job)
    assert wait_until(lambda: job.status == JobStatus.DONE)
    import json

    convert = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('["convert"')][0]
    s = " ".join(convert)
    for part in ("--preset smooth", "--repair aggressive", "--unit in", "--quality 0.250", "--keep-debris",
                 "--tolerance 0.02", "--max-faces 4000", "--layout parts"):
        assert part in s, part


def test_split_3mf_parts(model, settings, meshes):
    (job,) = add_and_read(model, meshes["my_parts.3mf"])
    assert job.can_split and job.is_split and [p.name for p in job.part_list] == ["Left", "Right"]
    model.enqueue(job)
    assert wait_until(lambda: job.status == JobStatus.DONE)
    assert all(p.result is not None and os.path.exists(p.output_path) for p in job.part_list)
    assert [model.default_export_name(job, i) for i in (0, 1)] == ["my_parts - Left", "my_parts - Right"]
    assert len(job.result_files) == 2
    # switching to one file sends it back to Ready
    model.set_split(job, False)
    assert job.status == JobStatus.READY and not job.is_split and job.result is None


def test_split_default_follows_settings(model, settings, meshes):
    (job,) = add_and_read(model, meshes["my_parts.3mf"])
    settings.splitParts = False
    model.apply_split_default()
    assert not job.split
    settings.splitParts = True
    settings.plateLayout = "plate"
    model.apply_split_default()
    assert not job.split and not model.split_available


def test_same_name_project_and_mesh(model, meshes):
    a, b = add_and_read(model, meshes["Part.stl"], meshes["Part.3mf"])
    assert model.default_export_name(a, None) == "Part"
    assert model.default_export_name(b, None) == "Part (3MF)"


def test_crash_error_and_unreadable(model, meshes):
    crash, err, broken = add_and_read(model, meshes["will_crash.stl"], meshes["has_error.stl"], meshes["broken.stl"])
    assert broken.status == JobStatus.FAILED and not broken.readable
    assert broken.error == "This file isn't a mesh the app can read."
    model.convert_all()
    assert wait_until(lambda: model.is_idle)
    assert crash.status == JobStatus.FAILED and "code 3" in crash.error and crash.readable
    assert err.status == JobStatus.FAILED and err.error == "The mesh has no usable faces."
    assert not broken.can_convert and crash.can_convert


def test_cancel_running_and_queued(model, settings, meshes):
    settings.parallelJobs = 1
    slow, one = add_and_read(model, meshes["very_slow.stl"], meshes["one.stl"])
    model.convert_all()
    assert wait_until(lambda: slow.status == JobStatus.CONVERTING)
    assert one.status == JobStatus.QUEUED  # only one at a time
    model.cancel(one)
    assert one.status == JobStatus.CANCELLED
    model.cancel(slow)
    assert wait_until(lambda: slow.status == JobStatus.CANCELLED, 10)
    assert slow.stage == "Cancelled"


def test_parallel_limit(model, settings, meshes):
    settings.parallelJobs = 2
    jobs = add_and_read(model, meshes["very_slow.stl"], meshes["one.stl"], meshes["with_issues.stl"])
    names = {j.file_name: j for j in jobs}
    peak = [0]

    def watch():
        peak[0] = max(peak[0], model.running_count)
        return names["one.stl"].status == JobStatus.DONE and names["with_issues.stl"].status == JobStatus.DONE

    model.convert_all()
    assert wait_until(watch)
    assert peak[0] <= 2
    model.cancel_all()
    assert wait_until(lambda: model.is_idle, 10)


def test_remove_asks_before_discarding(model, meshes):
    (job,) = add_and_read(model, meshes["one.stl"])
    model.enqueue(job)
    assert wait_until(lambda: job.status == JobStatus.DONE)
    out = job.output_path
    model.ui.confirm = False
    model.remove(job)
    assert job in model.jobs and model.ui.asked == [(1, "Remove")]
    model.ui.confirm = True
    model.remove(job)
    assert job not in model.jobs and model.selection is None
    assert not os.path.exists(out)


def test_clear_finished(model, meshes):
    one, crash = add_and_read(model, meshes["one.stl"], meshes["will_crash.stl"])
    model.convert_all()
    assert wait_until(lambda: model.is_idle)
    model.clear_finished()
    assert model.jobs == [crash]


def test_duplicate_files_are_added_once(model, meshes):
    add_and_read(model, meshes["one.stl"])
    add_and_read(model, meshes["one.stl"])
    assert len(model.jobs) == 1


def test_engine_missing(qapp, settings, monkeypatch, meshes):
    from mmm_app import engine

    monkeypatch.setattr(engine, "engine_command", lambda: None)
    monkeypatch.setattr(M, "engine_command", lambda: None)
    m = M.AppModel(settings)
    assert m.engine_missing
    m.add([meshes["one.stl"]])
    assert m.jobs[0].status == JobStatus.FAILED and "missing" in m.jobs[0].error
    m.shutdown()


def test_output_directory(model, settings, meshes, tmp_path):
    (job,) = add_and_read(model, meshes["one.stl"])
    src = os.path.dirname(job.path)
    settings.outputMode = "sameFolder"
    assert model.output_directory(job) == src
    settings.outputMode = "subfolder"
    settings.subfolderName = "  "
    assert model.output_directory(job) == os.path.join(src, "STEP Files")
    settings.outputMode = "custom"
    settings.outputFolder = ""
    assert model.output_directory(job) == src
    settings.outputFolder = str(tmp_path)
    assert model.output_directory(job) == str(tmp_path)


def test_stale_preview_dirs_are_removed(tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    dead = tmp_path / "ManifoldMeshMix-999999999"
    dead.mkdir()
    alive = tmp_path / f"ManifoldMeshMix-{os.getpid()}"
    alive.mkdir()
    other = tmp_path / "ManifoldMeshMix-ui-abc"
    other.mkdir()
    M.AppModel.remove_stale_preview_dirs()
    assert not dead.exists() and alive.exists() and other.exists()


def test_pid_alive():
    from mmm_app import system

    assert system.pid_alive(os.getpid())
    assert not system.pid_alive(999999999 if sys.platform != "win32" else 0x7FFFFFF0)
