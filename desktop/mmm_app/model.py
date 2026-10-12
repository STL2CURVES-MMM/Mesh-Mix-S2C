"""Jobs, the conversion queue and exporting: everything the window shows,
without the window itself (a port of the Mac app's AppModel)."""
import errno
import os
import re
import shutil
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import QObject, Qt, Signal

from . import system
from .engine import EngineMissing, EngineTask, engine_command
from .settings import preset_title

ACCEPTED_EXTENSIONS = {"stl", "obj", "ply", "off", "3mf", "glb", "gltf"}
# Reading a file loads the whole mesh; a few at a time keeps memory in check
# when a big folder is dropped.
INSPECT_LIMIT = 3

ENGINE_MISSING_MESSAGE = "Part of the app is missing (the converter). Please reinstall Manifold Mesh Mix."


class JobStatus(Enum):
    INSPECTING = "Reading"
    READY = "Ready"
    QUEUED = "Queued"
    CONVERTING = "Converting"
    DONE = "Done"
    FAILED = "Failed"
    CANCELLED = "Cancelled"

    @property
    def title(self):
        return self.value

    @property
    def is_busy(self):
        return self in (JobStatus.INSPECTING, JobStatus.CONVERTING)


def _int(v, default=0):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return int(v)


def _float(v, default=0.0):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return default
    return float(v)


def _bbox_size(bb):
    if isinstance(bb, list) and len(bb) == 2 and all(isinstance(r, list) and len(r) == 3 for r in bb):
        try:
            return [float(b) - float(a) for a, b in zip(bb[0], bb[1])]
        except (TypeError, ValueError):
            return None
    return None


@dataclass(frozen=True)
class CheckIssue:
    """One problem found when the saved STEP file was checked."""

    code: str
    level: str
    message: str

    @property
    def is_error(self):
        return self.level == "error"


@dataclass
class ConversionResult:
    preset: str = ""
    valid: object = None  # True / False / None (not reported)
    solids: int = 0
    shells: int = 0
    faces: int = 0
    file_size: int = 0
    seconds: float = 0.0
    volume: float = 0.0
    mesh_volume: float = 0.0
    tolerance: float = 0.0
    # The saved STEP file was opened again and checked
    checked: bool = False
    checks: list = field(default_factory=list)
    # repair steps the engine took because the first check found problems
    repaired: list = field(default_factory=list)

    @classmethod
    def from_event(cls, ev):
        valid = ev.get("valid")
        r = cls(
            preset=ev.get("preset") if isinstance(ev.get("preset"), str) else "",
            valid=valid if isinstance(valid, bool) else None,
            solids=_int(ev.get("solids")),
            shells=_int(ev.get("shells")),
            faces=_int(ev.get("faces")),
            file_size=_int(ev.get("file_size")),
            seconds=_float(ev.get("seconds")),
            volume=_float(ev.get("volume")),
            mesh_volume=_float(ev.get("mesh_volume")),
            tolerance=_float(ev.get("tolerance")),
        )
        r.checked = ev.get("checked") is True
        r.repaired = [str(x) for x in ev.get("repaired") or [] if isinstance(x, str)]
        for c in ev.get("checks") or []:
            if isinstance(c, dict):
                r.checks.append(CheckIssue(str(c.get("code", "")), str(c.get("level", "warning")),
                                           str(c.get("message", ""))))
        return r

    @property
    def preset_title(self):
        return preset_title(self.preset)

    @property
    def has_errors(self):
        return any(c.is_error for c in self.checks)

    @property
    def has_issues(self):
        return bool(self.checks) or self.valid is False


@dataclass
class JobPart:
    """One part of a 3MF project (a separate STL in the slicer)."""

    index: int
    name: str
    triangles: int
    bbox_size: object = None
    input_preview: object = None
    output_preview: object = None
    # The converted STEP file, kept in the app's temporary folder until it is exported
    output_path: object = None
    # Where the user exported it
    exported_path: object = None
    result: object = None


class Job(QObject):
    changed = Signal()

    def __init__(self, path):
        super().__init__()
        self.id = uuid.uuid4().hex
        self.path = os.path.abspath(path)
        self.status = JobStatus.INSPECTING
        self.stage = ""
        self.progress = 0.0
        self.triangles = None
        self.bodies = None
        self.open_shells = None
        self.parts = None
        # False when the file could not even be read (no point retrying)
        self.readable = True
        self.bbox_size = None
        self.result = None
        self.error = None
        self.warnings = []
        self.log = []
        self.input_preview = None
        self.output_preview = None
        self.output_path = None
        self.exported_path = None
        self.used_preset = None
        # Parts of a 3MF project (empty for plain mesh files)
        self.part_list = []
        # Save each part as its own STEP file
        self.split = True
        # Selected tab when the parts are split
        self.selected_part = 0
        self.task = None
        self.started_at = None

    def touch(self):
        self.changed.emit()

    @property
    def can_split(self):
        return len(self.part_list) > 1

    @property
    def is_split(self):
        return self.split and len(self.part_list) > 1

    @property
    def current_part(self):
        if self.is_split and 0 <= self.selected_part < len(self.part_list):
            return self.part_list[self.selected_part]
        return None

    @property
    def result_files(self):
        """The converted STEP files waiting in the temporary folder, as
        (source, part index) — the part index is None unless split."""
        if self.is_split:
            return [(p.output_path, i) for i, p in enumerate(self.part_list) if p.output_path]
        return [(self.output_path, None)] if self.output_path else []

    @property
    def exported_files(self):
        """Every STEP file the user exported from this job."""
        if self.is_split:
            return [p.exported_path for p in self.part_list if p.exported_path]
        return [self.exported_path] if self.exported_path else []

    @property
    def is_exported(self):
        return bool(self.exported_files)

    @property
    def needs_export(self):
        """Converted, but not saved anywhere yet."""
        return self.status == JobStatus.DONE and bool(self.result_files) and not self.is_exported

    def clear_results(self):
        """Drops the results of a previous conversion and returns the
        temporary files to delete (exported files stay where they are)."""
        old = [self.output_preview, self.output_path]
        for p in self.part_list:
            old += [p.output_preview, p.output_path]
            p.output_preview = p.output_path = p.exported_path = p.result = None
        self.result = None
        self.output_preview = self.output_path = self.exported_path = None
        return [o for o in old if o]

    @property
    def name(self):
        return os.path.splitext(os.path.basename(self.path))[0]

    @property
    def file_name(self):
        return os.path.basename(self.path)

    @property
    def extension(self):
        return os.path.splitext(self.path)[1][1:].lower()

    @property
    def can_convert(self):
        return self.readable and self.status in (JobStatus.READY, JobStatus.FAILED, JobStatus.CANCELLED)


@dataclass
class ExportItem:
    """One STEP file to export."""

    job: Job
    part: object  # part index of a split 3MF, or None
    source: str
    name: str  # file name without .step
    id: str = field(default_factory=lambda: uuid.uuid4().hex)


class ReplaceError(OSError):
    """The new file was written next to the old one, but the old one
    couldn't be replaced (on Windows: it is open in another program)."""


@dataclass
class ExportFailure:
    """A file that couldn't be exported, with a message in plain words."""

    item: ExportItem
    destination: str
    error: Exception

    @property
    def folder_name(self):
        return os.path.basename(os.path.dirname(self.destination)) or os.path.dirname(self.destination)

    @property
    def in_use(self):
        return isinstance(self.error, ReplaceError) and system.IS_WINDOWS

    @property
    def is_permission(self):
        if self.in_use:
            return False
        e = self.error
        return isinstance(e, PermissionError) or getattr(e, "errno", None) in (errno.EPERM, errno.EACCES)

    @property
    def message(self):
        file = os.path.basename(self.destination)
        code = getattr(self.error, "errno", None)
        if self.in_use:
            return f"“{file}” is open in another program, so it couldn't be replaced. Close it there, or export under another name."
        if self.is_permission:
            if system.IS_WINDOWS:
                return f"Windows didn't allow saving into the folder “{self.folder_name}”."
            return f"You don't have permission to save into the folder “{self.folder_name}”."
        if code == errno.ENOSPC:
            return f"The disk is full, so “{file}” couldn't be saved. Free up some space and export again."
        if code == errno.EROFS:
            return f"The folder “{self.folder_name}” is on a disk that can't be written to. Choose another folder."
        reason = getattr(self.error, "strerror", None) or str(self.error) or type(self.error).__name__
        return f"“{file}” couldn't be saved ({reason})."


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_file_name(s):
    """A name that works as a file name on every system."""
    out = []
    for ch in s or "":
        out.append("-" if ch in '/\\:*?"<>|' or unicodedata.category(ch) == "Cc" else ch)
    cleaned = "".join(out)

    def trim(ch):
        return ch == "." or ch == "\t" or unicodedata.category(ch) == "Zs"

    a, b = 0, len(cleaned)
    while a < b and trim(cleaned[a]):
        a += 1
    while b > a and trim(cleaned[b - 1]):
        b -= 1
    cleaned = cleaned[a:b]
    if not cleaned:
        return "Part"
    cleaned = cleaned[:100]
    if cleaned.split(".")[0].upper() in _RESERVED:
        cleaned += "_"
    return cleaned


def friendly(msg):
    """Strips Python exception class prefixes ("MeshError: ...")."""
    if not isinstance(msg, str):
        return msg
    m = msg.strip()
    if not m:
        return msg
    return re.sub(r"^[A-Za-z_][A-Za-z0-9_]*(Error|Exception): ", "", m, count=1)


SIGKILL, SIGSEGV, SIGBUS, SIGILL, SIGABRT, SIGFPE = 9, 11, 7, 4, 6, 8
WIN_OUT_OF_MEMORY = {0xC0000017, 0xC000012D}  # STATUS_NO_MEMORY, STATUS_COMMITMENT_LIMIT


def _memory_stop_message():
    return (f"{system.OS_NAME} stopped the conversion, most likely because the computer ran low on memory. "
            "Move the slider toward Faster, or convert one file at a time (Advanced → Files at once).")


def crash_message(code):
    """What to tell the user when the converter ends without a result."""
    if code is None:
        return "The converter stopped unexpectedly. Convert the file again."
    if code < 0:  # killed by a signal (Linux / macOS)
        sig = -code
        if sig == SIGKILL:
            return _memory_stop_message()
        if sig in (SIGSEGV, SIGBUS, SIGILL, SIGABRT, SIGFPE):
            return "The converter crashed on this file. Try another preset, or set Mesh repair to Thorough under Advanced."
        return f"The conversion was stopped unexpectedly (signal {sig})."
    if system.IS_WINDOWS and code >= 0xC0000000:
        if code in WIN_OUT_OF_MEMORY:
            return _memory_stop_message()
        return "The converter crashed on this file. Try another preset, or set Mesh repair to Thorough under Advanced."
    if code == 0:
        return "The converter finished without saving a STEP file. Convert the file again."
    return f"The converter stopped unexpectedly (code {code}). Convert the file again; if it keeps failing, try another preset."


def unreadable_message(code):
    if code is not None and ((code < 0 and -code == SIGKILL) or (system.IS_WINDOWS and code in WIN_OUT_OF_MEMORY)):
        return (f"{system.OS_NAME} stopped reading this file, most likely because it is too big for the free memory. "
                "Close other apps and try again.")
    return "This file couldn't be read. It may be damaged, or not a 3D mesh."


def same_path(a, b):
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


PREVIEW_PREFIX = "ManifoldMeshMix-"


class AppModel(QObject):
    jobs_changed = Signal()          # a job was added or removed
    selection_changed = Signal()
    state_changed = Signal()         # a job's status / export state changed
    export_requested = Signal(object)  # list of jobs for the export window
    engine_missing_changed = Signal()
    all_finished = Signal()          # the queue ran empty (flash the taskbar)
    _export_done = Signal(object)

    def __init__(self, settings, ui=None):
        super().__init__()
        self.settings = settings
        self.ui = ui  # confirm_discard(count, action) / choose_output_folder()
        self.jobs = []
        self.selection = None
        self.engine_missing = engine_command() is None
        self._inspect_waiting = deque()
        self._stamp = 0
        self.preview_dir = os.path.join(tempfile.gettempdir(), f"{PREVIEW_PREFIX}{os.getpid()}")
        os.makedirs(self.preview_dir, exist_ok=True)
        self._export_done.connect(self._finish_export, Qt.ConnectionType.QueuedConnection)
        threading.Thread(target=self.remove_stale_preview_dirs, daemon=True).start()

    # ------------------------------------------------------------------ previews

    def clean_up_previews(self):
        """Deletes this session's temporary folder (called when the app quits)."""
        shutil.rmtree(self.preview_dir, ignore_errors=True)

    @staticmethod
    def discard_previews(paths):
        for p in paths:
            if p:
                try:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)
                except OSError:
                    pass

    @staticmethod
    def remove_stale_preview_dirs():
        """Temporary folders of earlier sessions that didn't quit normally."""
        tmp = tempfile.gettempdir()
        try:
            names = os.listdir(tmp)
        except OSError:
            return
        for n in names:
            if not n.startswith(PREVIEW_PREFIX):
                continue
            try:
                pid = int(n[len(PREVIEW_PREFIX):])
            except ValueError:
                continue
            if pid != os.getpid() and not system.pid_alive(pid):
                shutil.rmtree(os.path.join(tmp, n), ignore_errors=True)

    def _next_stamp(self):
        self._stamp += 1
        return f"{int(time.time())}-{self._stamp}"

    # ------------------------------------------------------------------ queries

    @property
    def selected_job(self):
        for j in self.jobs:
            if j.id == self.selection:
                return j
        return None

    def select(self, job_id):
        if job_id != self.selection:
            self.selection = job_id
            self.selection_changed.emit()

    @property
    def pending_count(self):
        return sum(1 for j in self.jobs if j.can_convert)

    @property
    def running_count(self):
        return sum(1 for j in self.jobs if j.status == JobStatus.CONVERTING)

    @property
    def is_idle(self):
        return not any(j.status in (JobStatus.QUEUED, JobStatus.CONVERTING, JobStatus.INSPECTING) for j in self.jobs)

    @property
    def split_available(self):
        """Splitting only makes sense for "one of each part" (a whole build
        plate is kept together)."""
        return self.settings.plateLayout == "parts"

    def _set_status(self, job, status):
        job.status = status
        job.touch()
        self.state_changed.emit()

    # ------------------------------------------------------------------ adding files

    @staticmethod
    def collect_files(paths):
        files = []
        for p in paths:
            p = os.path.abspath(p)
            if os.path.isdir(p):
                for root, dirs, names in os.walk(p):
                    dirs[:] = sorted(d for d in dirs if not system.is_hidden(os.path.join(root, d)))
                    for n in sorted(names):
                        f = os.path.join(root, n)
                        if os.path.splitext(n)[1][1:].lower() in ACCEPTED_EXTENSIONS and not system.is_hidden(f):
                            files.append(f)
            elif os.path.isfile(p) and os.path.splitext(p)[1][1:].lower() in ACCEPTED_EXTENSIONS:
                files.append(p)
        return files

    def add(self, paths):
        first_new = None
        added = False
        for f in self.collect_files(paths):
            if any(same_path(j.path, f) and j.status != JobStatus.DONE for j in self.jobs):
                continue
            job = Job(f)
            self.jobs.append(job)
            added = True
            if first_new is None:
                first_new = job
            self._inspect(job)
        if added:
            self.jobs_changed.emit()
            self.state_changed.emit()
        if first_new is not None:
            self.select(first_new.id)
        return first_new is not None

    # ------------------------------------------------------------------ inspect

    def _inspect(self, job):
        job.status = JobStatus.INSPECTING
        self._inspect_waiting.append(job)
        self._pump_inspect()

    def _pump_inspect(self):
        def running():
            return sum(1 for j in self.jobs if j.status == JobStatus.INSPECTING and j.task is not None)

        while self._inspect_waiting and running() < INSPECT_LIMIT:
            job = self._inspect_waiting.popleft()
            if job not in self.jobs or job.status != JobStatus.INSPECTING:
                continue
            self._start_inspect(job)

    def _start_inspect(self, job):
        prev = os.path.join(self.preview_dir, f"{job.id}-in.mmmp")
        part_dir = os.path.join(self.preview_dir, f"{job.id}-parts")
        try:
            t = EngineTask(["inspect", job.path, "--json", "--preview", prev, "--part-previews", part_dir])
        except EngineMissing:
            job.error = ENGINE_MISSING_MESSAGE
            self._set_status(job, JobStatus.FAILED)
            if not self.engine_missing:
                self.engine_missing = True
                self.engine_missing_changed.emit()
            return
        job.task = t

        def on_event(ev):
            kind = ev.get("event")
            if kind == "result":
                tri = ev.get("triangles", ev.get("input_triangles"))
                job.triangles = _int(tri, None) if tri is not None else None
                job.bodies = _int(ev.get("bodies"), None)
                job.open_shells = _int(ev.get("open_shells"), None)
                job.parts = _int(ev.get("parts"), None)
                job.bbox_size = _bbox_size(ev.get("bbox"))
                plist = ev.get("part_list")
                if isinstance(plist, list) and len(plist) > 1:
                    parts = []
                    for k, e in enumerate(plist):
                        if not isinstance(e, dict):
                            continue
                        pv = e.get("preview")
                        parts.append(JobPart(index=_int(e.get("index"), k),
                                             name=str(e.get("name") or f"Part {k + 1}"),
                                             triangles=_int(e.get("triangles")),
                                             bbox_size=_bbox_size(e.get("bbox")),
                                             input_preview=pv if isinstance(pv, str) else None))
                    job.part_list = parts
                    job.split = self.settings.splitParts and self.settings.plateLayout == "parts"
                    job.selected_part = 0
                job.touch()
            elif kind == "error":
                job.error = friendly(ev.get("message"))
                job.touch()

        def on_exit(code, _stderr):
            job.task = None
            if job not in self.jobs:
                self._pump_inspect()
                return
            if code == 0 and not t.cancelled:
                job.input_preview = prev if os.path.exists(prev) else None
                self._set_status(job, JobStatus.READY)
                if self.settings.convertOnDrop:
                    self.enqueue(job)
            else:
                job.readable = False
                if job.error is None:
                    job.error = unreadable_message(code)
                self._set_status(job, JobStatus.FAILED)
            self._pump_inspect()

        t.event.connect(on_event)
        t.finished.connect(on_exit)
        try:
            t.start()
        except OSError as e:
            job.task = None
            job.error = f"The converter couldn't start ({e.strerror or e}). Try reinstalling Manifold Mesh Mix."
            self._set_status(job, JobStatus.FAILED)

    # ------------------------------------------------------------------ conversion queue

    def convert_all(self):
        for j in list(self.jobs):
            if j.can_convert:
                self.enqueue(j)

    def enqueue(self, job):
        if job.status.is_busy or job.status == JobStatus.QUEUED or not job.readable:
            return
        if job.status != JobStatus.DONE:
            self.discard_previews(job.clear_results())
        job.progress = 0.0
        job.stage = "Waiting"
        job.error = None
        job.warnings = []
        job.log = []
        self._set_status(job, JobStatus.QUEUED)
        self._pump()

    def reconvert(self, job):
        if job.status.is_busy or job.status == JobStatus.QUEUED:
            return
        self.discard_previews(job.clear_results())
        if job.status == JobStatus.DONE:
            job.status = JobStatus.READY
        self.enqueue(job)

    def set_split(self, job, on):
        """Turns "Split to parts" on or off for one file. A finished file goes
        back to Ready, so it can be converted the new way."""
        if not job.can_split or job.split == on or job.status.is_busy or job.status == JobStatus.QUEUED:
            return
        if on and not self.split_available:
            return
        job.split = on
        job.selected_part = 0
        if job.status == JobStatus.DONE:
            self.discard_previews(job.clear_results())
            job.stage = ""
            self._set_status(job, JobStatus.READY)
        else:
            job.touch()
            self.state_changed.emit()

    def apply_split_default(self):
        """The Settings default changed: files that aren't converted yet follow it."""
        for j in self.jobs:
            if j.can_split and j.status in (JobStatus.READY, JobStatus.FAILED, JobStatus.CANCELLED):
                j.split = self.settings.splitParts and self.split_available
                j.selected_part = 0
                j.touch()
        self.state_changed.emit()

    def _pump(self):
        limit = max(1, int(self.settings.parallelJobs))
        while self.running_count < limit:
            nxt = next((j for j in self.jobs if j.status == JobStatus.QUEUED), None)
            if nxt is None:
                break
            self._start(nxt)

    def output_directory(self, job):
        source = os.path.dirname(job.path)
        mode = self.settings.outputMode
        if mode == "sameFolder":
            return source
        if mode == "custom":
            return self.settings.outputFolder or source
        name = self.settings.subfolderName.strip()
        return os.path.join(source, safe_file_name(name) if name else "STEP Files")

    def default_export_name(self, job, part):
        """File name (without .step) a job's STEP file gets when exported."""
        if part is not None and 0 <= part < len(job.part_list):
            # "Project - Part" for every part of a split 3MF; parts with the same name get a number
            this = safe_file_name(job.part_list[part].name)
            base = f"{job.name} - {this}"
            same = sum(1 for p in job.part_list[:part] if safe_file_name(p.name).lower() == this.lower())
            return f"{base} {same + 1}" if same > 0 else base
        # a project file and a mesh with the same name (Part.3mf / Part.stl)
        # must not overwrite each other
        sibling = any(j is not job and j.name == job.name and same_path(os.path.dirname(j.path), os.path.dirname(job.path))
                      for j in self.jobs)
        if sibling and job.extension != "stl":
            return f"{job.name} ({job.extension.upper()})"
        return job.name

    def _start(self, job):
        s = self.settings
        stamp = self._next_stamp()
        # The STEP files go into the app's temporary folder first; nothing is
        # saved next to the originals until the user exports.
        result_dir = os.path.join(self.preview_dir, "results", f"{job.id}-{stamp}")
        os.makedirs(result_dir, exist_ok=True)
        part_outs = [os.path.join(result_dir, f"part{i}.step") for i in range(len(job.part_list))] if job.is_split else []
        out = part_outs[0] if part_outs else os.path.join(result_dir, "result.step")
        prev = os.path.join(self.preview_dir, f"{job.id}-out-{stamp}.mmmp")
        args = ["convert", job.path, "-o", out, "--json",
                "--preset", s.preset, "--repair", s.repair, "--unit", s.unit,
                "--quality", f"{s.quality:.3f}", "--layout", s.plateLayout, "--preview", prev]
        if job.is_split:
            args.append("--split")
            for u in part_outs:
                args += ["--part-output", u]
            for p in job.part_list:
                args += ["--part-preview", os.path.join(self.preview_dir, f"{job.id}-part{p.index}-out-{stamp}.mmmp")]
        if not s.removeDebris:
            args.append("--keep-debris")
        if s.useCustomTolerance and s.customTolerance > 0:
            args += ["--tolerance", repr(float(s.customTolerance))]
        if s.maxFaces > 0:
            args += ["--max-faces", str(int(s.maxFaces))]
        try:
            t = EngineTask(args)
        except EngineMissing:
            job.error = ENGINE_MISSING_MESSAGE
            self._set_status(job, JobStatus.FAILED)
            return
        job.stage = "Starting"
        job.progress = 0.01
        job.started_at = time.time()
        job.task = t
        state = {"result": False}

        def on_event(ev):
            kind = ev.get("event")
            if kind == "stage":
                job.stage = str(ev.get("stage") or "")
                if isinstance(ev.get("progress"), (int, float)):
                    job.progress = max(job.progress, float(ev["progress"]))
            elif kind == "progress":
                if isinstance(ev.get("progress"), (int, float)):
                    job.progress = max(job.progress, float(ev["progress"]))
            elif kind == "log":
                if isinstance(ev.get("message"), str):
                    job.log.append(ev["message"])
            elif kind == "warning":
                if isinstance(ev.get("message"), str):
                    job.warnings.append(ev["message"])
            elif kind == "error":
                job.error = friendly(ev.get("message"))
            elif kind == "part":
                # one part of a split 3MF is finished: show it right away
                p = ev.get("part")
                for part in job.part_list:
                    if part.index == p:
                        part.result = ConversionResult.from_event(ev)
                        if isinstance(ev.get("output"), str):
                            part.output_path = ev["output"]
                        pv = ev.get("preview")
                        if isinstance(pv, str) and os.path.exists(pv):
                            part.output_preview = pv
                        break
            elif kind == "result":
                state["result"] = True
                job.result = ConversionResult.from_event(ev)
                job.used_preset = job.result.preset
            job.touch()

        def on_exit(code, stderr):
            job.task = None
            if t.cancelled:
                job.stage = "Cancelled"
                self._set_status(job, JobStatus.CANCELLED)
            elif code == 0 and state["result"]:
                job.progress = 1.0
                job.stage = "Done"
                job.output_path = out
                if os.path.exists(prev):
                    job.output_preview = prev
                self._set_status(job, JobStatus.DONE)
            else:
                if job.error is None:
                    job.error = crash_message(code)
                    lines = [ln for ln in stderr.splitlines() if ln.strip()]
                    if lines:
                        job.log.append("Converter output: " + lines[-1].strip())
                self._set_status(job, JobStatus.FAILED)
            self._pump()
            if self.running_count == 0 and not any(j.status == JobStatus.QUEUED for j in self.jobs):
                self.all_finished.emit()

        t.event.connect(on_event)
        t.finished.connect(on_exit)
        self._set_status(job, JobStatus.CONVERTING)
        try:
            t.start()
        except OSError as e:
            job.task = None
            job.error = f"The converter couldn't start ({e.strerror or e}). Try reinstalling Manifold Mesh Mix."
            self._set_status(job, JobStatus.FAILED)
            self._pump()

    def cancel(self, job):
        if job.status == JobStatus.QUEUED:
            job.stage = "Cancelled"
            self._set_status(job, JobStatus.CANCELLED)
            return
        if job.task is not None:
            job.task.cancel()

    def cancel_all(self):
        for j in list(self.jobs):
            if j.status in (JobStatus.QUEUED, JobStatus.CONVERTING):
                self.cancel(j)

    def shutdown(self):
        """Stops every engine (the app is quitting) and removes the temporary files."""
        tasks = [j.task for j in self.jobs if j.task is not None]
        for t in tasks:
            t.cancelled = True
            t.kill()
        for t in tasks:
            t.wait(3)
        self.clean_up_previews()

    def _confirm_discard(self, count, action):
        if os.environ.get("MMM_SELFTEST") or self.ui is None:
            return True
        return self.ui.confirm_discard(count, action)

    def remove(self, job):
        if job not in self.jobs:
            return
        if job.needs_export and not self._confirm_discard(1, "Remove"):
            return
        if job.task is not None:
            job.task.cancel()
        idx = self.jobs.index(job)
        self.jobs.remove(job)
        if self.selection == job.id:
            nxt = self.jobs[min(idx, len(self.jobs) - 1)] if self.jobs else None
            self.select(nxt.id if nxt else None)
        self.discard_previews(self.preview_files(job))
        self.jobs_changed.emit()
        self.state_changed.emit()

    @staticmethod
    def preview_files(job):
        files = [job.input_preview, job.output_preview, job.output_path]
        for p in job.part_list:
            files += [p.input_preview, p.output_preview, p.output_path]
        return [f for f in files if f]

    def clear_finished(self):
        done = [j for j in self.jobs if j.status == JobStatus.DONE]
        if not done:
            return
        unexported = sum(1 for j in done if j.needs_export)
        if unexported and not self._confirm_discard(unexported, "Remove"):
            return
        self.jobs = [j for j in self.jobs if j.status != JobStatus.DONE]
        if self.selected_job is None:
            self.select(self.jobs[0].id if self.jobs else None)
        for j in done:
            self.discard_previews(self.preview_files(j))
        self.jobs_changed.emit()
        self.state_changed.emit()

    def reveal(self, job):
        files = job.exported_files
        if files:
            system.reveal(files)

    def reveal_current(self, job):
        """Shows one part's exported STEP file (or the file's only STEP)."""
        u = job.current_part.exported_path if (job.is_split and job.current_part) else (None if job.is_split else job.exported_path)
        if u:
            system.reveal([u])
        else:
            self.reveal(job)

    def open_output(self, job):
        u = (job.current_part.exported_path if (job.is_split and job.current_part) else job.exported_path)
        u = u or (job.exported_files[0] if job.exported_files else None)
        if u:
            system.open_path(u)

    # ------------------------------------------------------------------ export

    @property
    def exportable_jobs(self):
        return [j for j in self.jobs if j.status == JobStatus.DONE and j.result_files]

    @property
    def unexported_jobs(self):
        return [j for j in self.exportable_jobs if not j.is_exported]

    def request_export(self, jobs):
        js = [j for j in jobs if j.status == JobStatus.DONE and j.result_files]
        if js:
            self.export_requested.emit(js)

    def export_items(self, jobs):
        return [ExportItem(job=j, part=part, source=src, name=self.default_export_name(j, part))
                for j in jobs for src, part in j.result_files]

    def export_path(self, item):
        return os.path.join(self.output_directory(item.job), safe_file_name(item.name) + ".step")

    def choose_output_folder(self):
        if self.ui is None:
            return False
        folder = self.ui.choose_output_folder()
        if folder:
            self.settings.outputFolder = folder
            self.settings.outputMode = "custom"
            return True
        return False

    def export(self, items, completion):
        """Copies the converted files to their destinations in the background
        and reports the files that couldn't be saved."""
        plan = [(item, self.export_path(item)) for item in items]

        def work():
            done, failures = [], []
            for item, dest in plan:
                try:
                    self.copy_result(item.source, dest)
                    done.append((item, dest))
                except Exception as e:  # noqa: BLE001 - reported to the user
                    failures.append(ExportFailure(item, dest, e))
            self._export_done.emit((done, failures, completion))

        threading.Thread(target=work, daemon=True).start()

    def _finish_export(self, payload):
        done, failures, completion = payload
        touched = []
        for item, dest in done:
            if item.part is not None and 0 <= item.part < len(item.job.part_list):
                item.job.part_list[item.part].exported_path = dest
            else:
                item.job.exported_path = dest
            if item.job not in touched:
                touched.append(item.job)
        for j in touched:
            j.touch()
        if done:
            self.state_changed.emit()
        if done and self.settings.revealWhenDone and not os.environ.get("MMM_SELFTEST"):
            system.reveal([d for _, d in done])
        completion(failures)

    @staticmethod
    def copy_result(source, dest):
        """Writes a temporary copy next to the destination, then renames it
        into place, so a half-written STEP file never appears."""
        folder = os.path.dirname(dest)
        os.makedirs(folder, exist_ok=True)
        tmp = os.path.join(folder, f".{os.path.basename(dest)}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            shutil.copyfile(source, tmp)
        except BaseException:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise
        try:
            os.replace(tmp, dest)
        except OSError as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            if isinstance(e, PermissionError) and os.path.exists(dest):
                raise ReplaceError(e.errno, e.strerror, dest) from e
            raise
