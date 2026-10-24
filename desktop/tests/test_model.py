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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_show_in_explorer(tmp_path):
    """Explorer opens with the files selected (the shell call doesn't fail)."""
    from mmm_app import system

    a = tmp_path / "a.step"
    b = tmp_path / "Peça b.step"
    a.write_text("x")
    b.write_text("x")
    assert system._windows_reveal([str(a), str(b)])
    assert system.reveal([str(a)])


# ---------------------------------------------------------------- mesh check

def test_mesh_check_from_event():
    mc = M.MeshCheck.from_event({"status": "action", "issues": [
        {"code": "holes", "count": 2, "status": "fixed", "problem": "2 holes", "result": "Filled them."},
        {"code": "overlaps", "count": 2, "status": "action", "problem": "2 pieces overlapping", "result": "Kept.",
         "part": "Left"}, "junk"], "verified": {"ready": True, "problems": []}})
    assert mc.title == "Your choice needed" and mc.ready is True
    assert [i.code for i in mc.issues] == ["holes", "overlaps"] and mc.issues[1].part == "Left"
    assert mc.has_overlaps and mc.fixed_anything
    assert M.MeshCheck.from_event(None) is None
    odd = M.MeshCheck.from_event({"issues": "x", "verified": {"ready": "yes"}})
    assert odd.status == "ok" and odd.issues == [] and odd.ready is None


def test_inspect_reports_mesh_check(model, settings, meshes, tmp_path, monkeypatch):
    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(log))
    settings.repair = "off"
    a, b = add_and_read(model, meshes["holes.stl"], meshes["one.stl"])
    assert a.mesh_check.status == "fixed" and a.mesh_check.fixed_anything and a.mesh_check.ready
    assert b.mesh_check.status == "ok" and not b.mesh_check.issues
    # the check repairs the way the conversion will
    inspect = [line for line in log.read_text().splitlines() if line.startswith('["inspect"')]
    assert all('"--repair", "off"' in line for line in inspect)


def test_merge_overlapping_pieces(model, settings, meshes, tmp_path, monkeypatch):
    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(log))
    settings.convertOnDrop = True
    (job,) = add_and_read(model, meshes["overlap.stl"])
    assert wait_until(lambda: job.status == JobStatus.DONE)
    assert job.mesh_check.has_overlaps and not job.merge_overlaps
    first_preview = job.input_preview
    model.set_merge_overlaps(job, True)
    assert job.status == JobStatus.INSPECTING
    assert wait_until(lambda: job.status == JobStatus.READY)
    # read again (a new preview), not converted again on its own
    assert job.merge_overlaps and job.result is None and job.input_preview != first_preview
    assert not job.mesh_check.has_overlaps and job.mesh_check.issues[0].status == "fixed"
    assert not os.path.exists(first_preview)
    model.enqueue(job)
    assert wait_until(lambda: job.status == JobStatus.DONE)
    lines = log.read_text().splitlines()
    assert any(line.startswith('["inspect"') and "--merge-overlaps" in line for line in lines)
    assert any(line.startswith('["convert"') and "--merge-overlaps" in line for line in lines)
    model.set_merge_overlaps(job, False)
    assert wait_until(lambda: job.status == JobStatus.READY)
    assert job.mesh_check.has_overlaps


def test_merge_failure_reads_the_file_again(model, meshes):
    (job,) = add_and_read(model, meshes["overlap_mergefail.stl"])
    model.set_merge_overlaps(job, True)
    assert wait_until(lambda: job.status == JobStatus.READY and not job.merge_overlaps)
    assert job.readable and job.merge_error == "The pieces couldn't be joined."
    assert job.mesh_check.has_overlaps and job.error is None


def test_save_fixed_mesh(model, meshes, tmp_path, monkeypatch):
    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(log))
    (job,) = add_and_read(model, meshes["holes.stl"])
    done = []
    dest = str(tmp_path / "fixed.stl")
    model.save_fixed_mesh(job, dest, done.append)
    assert wait_until(lambda: done)
    assert done == [None] and os.path.exists(dest)
    fix = [line for line in log.read_text().splitlines() if line.startswith('["fix"')][0]
    assert dest.replace("\\", "\\\\") in fix and '"--repair", "auto"' in fix


def test_error_reason_and_report(model, settings, meshes):
    from mmm_app import bugreport

    (job,) = add_and_read(model, meshes["has_error.stl"])
    model.enqueue(job)
    assert wait_until(lambda: job.status == JobStatus.FAILED)
    assert job.error == "The mesh has no usable faces."
    assert job.error_reason == "The file itself is the problem, not the app."
    assert "Traceback" in job.error_details
    text = bugreport.build(job, settings)
    assert "has_error.stl" in text and "Why: The file itself is the problem" in text
    assert "Technical details" in text and "Mesh check" in text
    # no folders, no user name
    folder = os.path.dirname(job.path)
    assert folder not in text and os.path.expanduser("~") not in text
    assert "<file folder>" in text
    # enqueueing again clears the old reason
    model.enqueue(job)
    assert job.error_reason is None and job.error_details is None


def test_report_scrubs_paths(monkeypatch, tmp_path):
    from mmm_app import bugreport

    home = os.path.expanduser("~")
    job_path = os.path.join(str(tmp_path), "model.stl")
    text = (f"at {job_path} and {os.path.join(home, 'Desktop', 'x.stl')} and C:\\Users\\Maria\\AppData\\x.py "
            "and /home/joao/src/a.py and /Users/ana/b.py")
    out = bugreport.scrub(text, job_path)
    assert str(tmp_path) not in out and home not in out
    assert "Maria" not in out and "joao" not in out and "ana/" not in out
    assert "<file folder>" in out and "model.stl" in out


# ---------------------------------------------------------------- auto quality

def test_auto_quality_steps(settings):
    from mmm_app.settings import auto_quality

    assert [auto_quality(n) for n in (1, 49_999, 50_000, 299_999, 300_000, 999_999, 1_000_000, 2_999_999,
                                      3_000_000, 50_000_000)] == [0.9, 0.9, 0.8, 0.8, 0.7, 0.7, 0.6, 0.6, 0.5, 0.5]
    settings.preset = "auto"
    settings.quality = 0.3
    assert settings.auto_quality_on and settings.quality_for(2_000_000) == 0.6
    assert settings.quality_for(None) == 0.3  # not read yet: the slider's quality
    settings.autoQuality = False
    assert settings.quality_for(2_000_000) == 0.3
    settings.autoQuality = True
    settings.preset = "smooth"
    assert not settings.auto_quality_on and settings.quality_for(2_000_000) == 0.3


def test_auto_quality_per_file(model, settings, meshes, tmp_path, monkeypatch):
    import json

    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(log))
    settings.preset = "auto"
    settings.quality = 0.4
    small, dense = add_and_read(model, meshes["one.stl"], meshes["dense_sculpt.stl"])
    model.convert_all()
    assert wait_until(lambda: small.status == JobStatus.DONE and dense.status == JobStatus.DONE)
    runs = [json.loads(line) for line in log.read_text().splitlines() if line.startswith('["convert"')]
    q = {os.path.basename(r[1]): r[r.index("--quality") + 1] for r in runs}
    assert q == {"one.stl": "0.900", "dense_sculpt.stl": "0.600"}


# ---------------------------------------------------------------- touching 3MF parts

def test_touching_parts_ask_and_split(model, settings, meshes):
    (job,) = add_and_read(model, meshes["touching.3mf"])
    assert wait_until(lambda: job.touching_choice is not None)
    assert ("touching", "touching.3mf") in model.ui.asked
    assert job.touching_choice == "split" and job.is_split and len(job.part_list) == 2
    issue = next(i for i in job.mesh_check.issues if i.code == "touching_parts")
    assert issue.status == "note" and job.mesh_check.status != "action"


def test_touching_parts_merge(model, settings, meshes, tmp_path, monkeypatch):
    import json

    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(log))
    model.ui.touching = "merge"
    settings.convertOnDrop = True
    (job,) = add_and_read(model, meshes["touching.3mf"])
    # merging reads the file again, then converts (convert on drop is on)
    assert wait_until(lambda: job.status == JobStatus.DONE, 30)
    assert job.touching_choice == "merge" and job.part_list == [] and not job.is_split
    assert next(i for i in job.mesh_check.issues if i.code == "touching_parts").status == "fixed"
    runs = [json.loads(line) for line in log.read_text().splitlines()]
    assert ["--touching-parts", "merge"] == [x for r in runs if r[0] == "convert" for x in r
                                             if x in ("--touching-parts", "merge")][:2]
    # back to separate parts from the Mesh view
    model.set_touching_choice(job, "split")
    assert wait_until(lambda: job.status == JobStatus.READY and len(job.part_list) == 2)
    assert job.touching_choice == "split"


def test_touching_parts_asked_once_per_file(model, meshes):
    (job,) = add_and_read(model, meshes["touching.3mf"])
    assert wait_until(lambda: job.touching_choice is not None)
    model.reconvert(job)
    assert wait_until(lambda: job.status == JobStatus.DONE)
    assert model.ui.asked.count(("touching", "touching.3mf")) == 1


def test_report_scrubs_windows_paths():
    from mmm_app import bugreport

    text = ("FileNotFoundError: [Errno 2] No such file: 'C:\\\\Users\\\\John Smith\\\\Documents\\\\b.stl' "
            "and C:\\Users\\Ana Lu\\x.py")
    out = bugreport.scrub(text, "/somewhere/else/model.stl")
    assert "John" not in out and "Smith" not in out and "Ana" not in out and "Lu" not in out


def test_report_uses_the_runs_settings(model, settings, meshes):
    from mmm_app import bugreport

    settings.preset = "cad"
    (job,) = add_and_read(model, meshes["has_error.stl"])
    model.enqueue(job)
    assert wait_until(lambda: job.status == JobStatus.FAILED)
    settings.preset = "smooth"  # changed after the run
    text = bugreport.build(job, settings)
    assert "Preset:        CAD" in text and "(of the last conversion)" in text


def test_clear_finished_only_removes_what_was_asked(model, meshes):
    a, b = add_and_read(model, meshes["one.stl"], meshes["Part.stl"])
    model.enqueue(a)
    assert wait_until(lambda: a.status == JobStatus.DONE)

    def finish_b_meanwhile(count, action):
        model.enqueue(b)
        assert wait_until(lambda: b.status == JobStatus.DONE)
        return True

    model.ui.confirm_discard = finish_b_meanwhile
    model.clear_finished()
    assert model.jobs == [b]


def test_save_fixed_mesh_engine_missing(model, meshes, monkeypatch):
    (job,) = add_and_read(model, meshes["holes.stl"])

    def missing(args):
        raise M.EngineMissing()

    monkeypatch.setattr(M, "EngineTask", missing)
    done = []
    model.save_fixed_mesh(job, "x.stl", done.append)
    assert done == []  # reported after the call returns, like a real run
    assert wait_until(lambda: done)
    assert done == [M.ENGINE_MISSING_MESSAGE]


def test_debris_setting_rechecks_files(model, settings, meshes, tmp_path, monkeypatch):
    import json

    log = tmp_path / "args.log"
    monkeypatch.setenv("FAKE_ENGINE_LOG", str(log))
    (job,) = add_and_read(model, meshes["one.stl"])
    settings.removeDebris = False
    model.recheck()
    assert wait_until(lambda: job.status == JobStatus.READY and len(log.read_text().splitlines()) >= 2)
    last = json.loads(log.read_text().splitlines()[-1])
    assert last[0] == "inspect" and "--keep-debris" in last


def test_reading_shows_what_the_check_is_doing(model, meshes):
    model.add([meshes["slowread.stl"]])
    (job,) = model.jobs
    # the engine's current step is shown while the file is read, and the bar moves
    assert wait_until(lambda: job.status == JobStatus.INSPECTING and job.stage == "Looking for hidden internal walls")
    assert wait_until(lambda: job.progress > 0.35)
    assert wait_until(lambda: job.status == JobStatus.READY)
    assert job.stage == "" and job.progress == 0.0



def test_conversion_reports_how_closely_it_follows_the_mesh(model, settings, meshes):
    from mmm_app import bugreport

    good, rough = add_and_read(model, meshes["one.stl"], meshes["rough.stl"])
    model.convert_all()
    assert wait_until(lambda: good.status == JobStatus.DONE and rough.status == JobStatus.DONE)
    assert good.result.fidelity.ok and not good.result.off_mesh and not good.result.has_issues
    assert rough.result.off_mesh and rough.result.has_issues
    assert "99.8% of the surface is within 0.1 mm" in good.result.fidelity.summary()
    text = bugreport.build(rough, settings)
    assert "Compared with the mesh" in text and "doesn't follow it closely everywhere" in text


def test_fidelity_summary_never_overstates():
    from mmm_app.model import Fidelity

    f = Fidelity.from_event({"ok": False, "tolerance": 0.05, "within2": 0.9996, "max": 0.04, "wrong_way": 0.02})
    text = f.summary("mm")
    # a failing result never reads 100.0%, and says what failed
    assert text.startswith("99.9% of the surface") and "2.0% of it faces another way" in text
    assert "faces another way" not in Fidelity.from_event({"tolerance": 0.05, "wrong_way": 0.001}).summary()
    assert Fidelity.from_event({"ok": False}).summary() == "the details weren't reported"
    s = Fidelity.from_event({"tolerance": 0.05, "mesh_kind": "scan", "rechecked": True}).summary()
    assert "noisy scan" in s and "checked twice" in s
    assert "scan" not in Fidelity.from_event({"tolerance": 0.05, "mesh_kind": "coarse"}).summary()
    assert Fidelity.from_event(None) is None and Fidelity.from_event("x") is None


def test_result_unit(model, settings, meshes):
    from mmm_app import bugreport
    from mmm_app.model import result_unit

    settings.unit = "in"
    stl, project = add_and_read(model, meshes["one.stl"], meshes["my_parts.3mf"])
    # 3MF files carry their own unit; the engine saves them in mm
    assert result_unit(project, settings) == "mm" and result_unit(stl, settings) == "in"
    model.convert_all()
    assert wait_until(lambda: stl.status == JobStatus.DONE)
    settings.unit = "mm"  # changed after converting: the result is still in inches
    assert result_unit(stl, settings) == "in"
    assert "within 0.1 in of the mesh" in bugreport.build(stl, settings)


def test_two_versions_to_choose_from(model, settings, meshes):
    """When no method follows the mesh, the engine offers a valid second
    version: the closest one stays the default, and picking the other one
    changes what is exported (and both are cleaned up)."""
    from mmm_app import bugreport

    (job,) = add_and_read(model, meshes["rough_twoways.stl"])
    model.convert_all()
    assert wait_until(lambda: job.status == JobStatus.DONE)
    r = job.result
    a = r.alternative
    assert a is not None and a.output.endswith(".valid.step") and os.path.exists(a.output)
    assert a.preview and os.path.exists(a.preview) and a.valid is True and a.faces == 420
    assert a.preset == r.preset and a.seconds == r.seconds and a.fidelity.within2 == 0.9 and not a.fidelity.ok
    assert r.chosen is r and r.has_errors and not a.has_errors
    assert job.result_files == [(job.output_path, None)]
    r.use_alternative = True
    assert r.chosen is a and job.result_files == [(a.output, None)]
    text = bugreport.build(job, settings)
    assert "Second (valid) version offered" in text and "the user picked it" in text
    files = model.preview_files(job)
    assert a.output in files and a.preview in files
    assert a.output in job.clear_results()


def test_two_versions_per_part(model, meshes):
    (job,) = add_and_read(model, meshes["twoways_parts.3mf"])
    assert job.is_split
    model.convert_all()
    assert wait_until(lambda: job.status == JobStatus.DONE)
    left, right = job.part_list
    assert left.result.alternative is None and right.result.alternative is not None
    right.result.use_alternative = True
    assert job.result_files == [(left.output_path, 0), (right.result.alternative.output, 1)]


def test_alternative_needs_its_file():
    # an alternative whose file is missing is never offered
    r = ConversionResult.from_event({"alternative": {"output": "/nonexistent/x.valid.step", "valid": True}})
    assert r.alternative is None and r.extra_files == []
