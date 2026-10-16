"""Jobs, the conversion queue and exporting: everything the window shows,
without the window itself (a port of the Mac app's AppModel)."""
import errno
import math
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

from PySide6.QtCore import QObject, Qt, QTimer, Signal

from . import system
from .engine import EngineMissing, EngineTask, engine_command
from .settings import preset_title

ACCEPTED_EXTENSIONS = {"stl", "obj", "ply", "off", "3mf", "glb", "gltf"}
# Reading a file loads the whole mesh; a few at a time keeps memory in check
# when a big folder is dropped.
INSPECT_LIMIT = 3

# what a conversion run used, kept with the job
RUN_SETTINGS = ("preset", "quality", "autoQuality", "repair", "unit", "plateLayout", "removeDebris",
                "useCustomTolerance", "customTolerance", "maxFaces")

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


@dataclass(frozen=True)
class MeshIssue:
    """One thing the mesh check found, and what it did about it."""

    code: str
    count: int
    status: str  # fixed / not_fixed / action / note
    problem: str
    result: str
    part: str = ""


MESH_STATUS_TITLES = {
    "ok": "Mesh is clean",
    "fixed": "Mesh fixed",
    "action": "Your choice needed",
    "problems": "Mesh has problems",
}


@dataclass
class MeshCheck:
    """The engine's mesh check: what it found and fixed, and whether a
    second check of the fixed mesh found it ready for conversion."""

    status: str = "ok"  # ok / fixed / action / problems
    issues: list = field(default_factory=list)
    ready: object = None  # True / False / None (not reported)
    problems: list = field(default_factory=list)

    @classmethod
    def from_event(cls, d):
        if not isinstance(d, dict):
            return None
        mc = cls(status=str(d.get("status") or "ok"))
        for i in d.get("issues") or []:
            if isinstance(i, dict):
                mc.issues.append(MeshIssue(code=str(i.get("code", "")), count=_int(i.get("count")),
                                           status=str(i.get("status", "note")), problem=str(i.get("problem", "")),
                                           result=str(i.get("result", "")), part=str(i.get("part") or "")))
        v = d.get("verified")
        if isinstance(v, dict):
            mc.ready = v.get("ready") if isinstance(v.get("ready"), bool) else None
            mc.problems = [str(x) for x in v.get("problems") or [] if isinstance(x, str)]
        return mc

    @property
    def title(self):
        return MESH_STATUS_TITLES.get(self.status, "Mesh checked")

    @property
    def has_overlaps(self):
        """Overlapping pieces the user can choose to merge."""
        return any(i.code == "overlaps" and i.status == "action" for i in self.issues)

    @property
    def fixed_anything(self):
        return any(i.status == "fixed" for i in self.issues)

    def kept_parts_separate(self):
        """The user chose to keep touching parts separate: the engine reports
        that as a note (the same words as engine/mmm/meshcheck.py)."""
        self.issues = [MeshIssue(i.code, i.count, "note", i.problem,
                                 "Kept as separate parts, as you chose. Use “Merge Touching Parts” to join them "
                                 "into one solid.", i.part) if i.code == "touching_parts" else i for i in self.issues]
        st = {i.status for i in self.issues}
        self.status = ("problems" if "not_fixed" in st else "action" if "action" in st else
                       "fixed" if "fixed" in st else "ok")


@dataclass
class Fidelity:
    """How closely the STEP shape follows the mesh (engine/mmm/fidelity.py)."""

    ok: bool = True
    tolerance: float = 0.0
    within2: float = 1.0  # share of the surface within twice the tolerance
    p99: float = 0.0
    max: float = 0.0
    wrong_way: float = 0.0  # share of the surface facing another way than the mesh
    scan: bool = False  # judged as a noisy scan (its noise is allowed for)
    rechecked: bool = False  # the first check was close, so it was checked again

    # the engine's limit for wrong_way (engine/mmm/fidelity.py, Verdict)
    WRONG_WAY_MAX = 0.005

    @classmethod
    def from_event(cls, d):
        if not isinstance(d, dict):
            return None
        return cls(ok=d.get("ok") is not False, tolerance=_float(d.get("tolerance")),
                   within2=_float(d.get("within2"), 1.0), p99=_float(d.get("p99")), max=_float(d.get("max")),
                   wrong_way=_float(d.get("wrong_way")), scan=d.get("mesh_kind") == "scan",
                   rechecked=d.get("rechecked") is True)

    def summary(self, unit="mm"):
        if not self.tolerance > 0:
            return "the details weren't reported"
        # rounded down, so a result that fails never shows 100.0%
        pct = math.floor(1000 * min(max(self.within2, 0.0), 1.0) + 1e-6) / 10
        text = (f"{pct:.1f}% of the surface is within {2 * self.tolerance:.2g} {unit} of the mesh; "
                f"the largest gap is {self.max:.2g} {unit}")
        if self.wrong_way > self.WRONG_WAY_MAX:
            text += f"; {100 * self.wrong_way:.1f}% of it faces another way than the mesh (wrinkles or dents)"
        if self.scan:
            text += " (the mesh is a noisy scan, so its noise is allowed for)"
        if self.rechecked:
            text += " (checked twice, as the first check was close)"
        return text


def result_unit(job, settings):
    """The unit a job's result is in: 3MF files carry their own (the engine
    saves them in mm), others the unit the last conversion used."""
    if job.extension == "3mf":
        return "mm"
    run = job.run_settings or {}
    return run.get("unit") or settings.unit


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
    # how closely the shape follows the mesh (None: not measured)
    fidelity: object = None
    # When no method made a shape that follows the mesh, the engine saves
    # the closest one and offers a valid one too (a ConversionResult whose
    # output and preview are that file's): the user picks which to export
    alternative: object = None
    output: str = ""
    preview: str = ""
    use_alternative: bool = False

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
        r.fidelity = Fidelity.from_event(ev.get("fidelity"))
        for c in ev.get("checks") or []:
            if isinstance(c, dict):
                r.checks.append(CheckIssue(str(c.get("code", "")), str(c.get("level", "warning")),
                                           str(c.get("message", ""))))
        alt = ev.get("alternative")
        if isinstance(alt, dict) and isinstance(alt.get("output"), str) and os.path.exists(alt["output"]):
            a = cls.from_event({k: v for k, v in alt.items() if k != "alternative"})
            a.preset, a.seconds, a.mesh_volume, a.tolerance = r.preset, r.seconds, r.mesh_volume, r.tolerance
            a.output = alt["output"]
            pv = alt.get("preview")
            a.preview = pv if isinstance(pv, str) and os.path.exists(pv) else ""
            r.alternative = a
        return r

    @property
    def chosen(self):
        """The version the user picked (this one unless they picked the
        alternative)."""
        return self.alternative if self.use_alternative and self.alternative is not None else self

    @property
    def extra_files(self):
        """The alternative's files (to delete with the result)."""
        a = self.alternative
        return [f for f in (a.output, a.preview) if f] if a is not None else []

    @property
    def preset_title(self):
        return preset_title(self.preset)

    @property
    def has_errors(self):
        return any(c.is_error for c in self.checks)

    @property
    def off_mesh(self):
        """The shape doesn't follow the mesh everywhere."""
        return self.fidelity is not None and not self.fidelity.ok

    @property
    def has_issues(self):
        return bool(self.checks) or self.valid is False or self.off_mesh


def _chosen_file(result, path):
    """The STEP file to export: the alternative when the user picked it."""
    if result is not None and result.use_alternative and result.alternative is not None:
        return result.alternative.output
    return path


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
        # why it went wrong, in plain words, and the technical details for
        # a bug report (from the engine's error event)
        self.error_reason = None
        self.error_details = None
        # what the mesh check found (MeshCheck), and whether overlapping
        # pieces are merged into one solid (the user's choice, per file)
        self.mesh_check = None
        self.merge_overlaps = False
        self.merge_error = None
        # 3MF parts that touch each other ([{"parts": [...], "names": [...]}])
        # and what to do with them: None (not asked yet), "split" or "merge"
        self.touching = []
        self.touching_choice = None
        # convert as soon as it has been read again (the choice came first)
        self.convert_after_read = False
        # the settings of the last conversion run (see RUN_SETTINGS)
        self.run_settings = None
        # where "Save Fixed Mesh" last saved this file's fixed mesh
        self.saved_fixed_mesh = None
        # read again after a change (no automatic conversion then)
        self.reinspecting = False
        self.file_size = None
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
            return [(_chosen_file(p.result, p.output_path), i) for i, p in enumerate(self.part_list) if p.output_path]
        return [(_chosen_file(self.result, self.output_path), None)] if self.output_path else []

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
        old = [self.output_preview, self.output_path] + (self.result.extra_files if self.result else [])
        for p in self.part_list:
            old += [p.output_preview, p.output_path] + (p.result.extra_files if p.result else [])
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


def take_error(job, ev):
    """The engine's error event: what went wrong, why, and the details."""
    job.error = friendly(ev.get("message"))
    job.error_reason = ev.get("reason") if isinstance(ev.get("reason"), str) else None
    job.error_details = ev.get("details") if isinstance(ev.get("details"), str) else None


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
        self._touch_waiting = deque()
        self._fix_tasks = set()
        self._stamp = 0
        # a new private folder: a shared, predictable one would let another
        # user of the computer swap the converted files
        self.preview_dir = tempfile.mkdtemp(prefix=f"{PREVIEW_PREFIX}{os.getpid()}-")
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
                pid = int(n[len(PREVIEW_PREFIX):].split("-")[0])
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
            try:
                job.file_size = os.path.getsize(f)
            except OSError:
                pass
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
        job.stage = ""
        job.progress = 0.0
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
        # a new name every time: the preview cache goes by file name, and
        # merging overlapping pieces reads the file again
        stamp = self._next_stamp()
        prev = os.path.join(self.preview_dir, f"{job.id}-in-{stamp}.mmmp")
        part_dir = os.path.join(self.preview_dir, f"{job.id}-parts-{stamp}")
        s = self.settings
        args = ["inspect", job.path, "--json", "--preview", prev, "--part-previews", part_dir,
                "--repair", s.repair, "--layout", s.plateLayout, "--touching-parts", job.touching_choice or "ask"]
        if not s.removeDebris:
            args.append("--keep-debris")
        if job.merge_overlaps:
            args.append("--merge-overlaps")
        try:
            t = EngineTask(args)
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
            if kind == "stage":
                # what the mesh check is doing right now
                job.stage = str(ev.get("stage") or "")
                if isinstance(ev.get("progress"), (int, float)):
                    job.progress = max(job.progress, float(ev["progress"]))
                job.touch()
            elif kind == "progress":
                if isinstance(ev.get("progress"), (int, float)):
                    job.progress = max(job.progress, float(ev["progress"]))
                    job.touch()
            elif kind == "result":
                tri = ev.get("triangles", ev.get("input_triangles"))
                job.triangles = _int(tri, None) if tri is not None else None
                job.bodies = _int(ev.get("bodies"), None)
                job.open_shells = _int(ev.get("open_shells"), None)
                job.parts = _int(ev.get("parts"), None)
                job.bbox_size = _bbox_size(ev.get("bbox"))
                job.mesh_check = MeshCheck.from_event(ev.get("mesh_check"))
                t_ = ev.get("touching")
                job.touching = [g for g in t_ if isinstance(g, dict) and isinstance(g.get("names"), list)] \
                    if isinstance(t_, list) else []
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
                take_error(job, ev)
                job.touch()

        def on_exit(code, _stderr):
            job.task = None
            job.stage = ""
            job.progress = 0.0
            if job not in self.jobs:
                self.discard_previews([prev, part_dir])
                self._pump_inspect()
                return
            if code == 0 and not t.cancelled:
                job.input_preview = prev if os.path.exists(prev) else None
                self._set_status(job, JobStatus.READY)
                if job.touching and job.touching_choice is None:
                    # ask whether to keep the touching parts separate (then convert)
                    self._ask_touching(job)
                elif job.convert_after_read or (self.settings.convertOnDrop and not job.merge_error
                                                and not job.reinspecting):
                    self.enqueue(job)
                job.convert_after_read = False
                job.reinspecting = False
            elif job.merge_overlaps and not t.cancelled:
                # merging went wrong: read the file again the usual way
                job.merge_overlaps = False
                job.merge_error = job.error or "The overlapping pieces couldn't be merged."
                job.error = job.error_reason = job.error_details = None
                job.status = JobStatus.INSPECTING
                self._inspect_waiting.append(job)
                job.touch()
                self.state_changed.emit()
            else:
                job.reinspecting = False
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
        job.error = job.error_reason = job.error_details = None
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

    def set_merge_overlaps(self, job, on):
        """Joins the pieces of the file that overlap each other into one
        solid (or keeps them separate again). The file is read and checked
        again, and a finished file goes back to Ready."""
        if job.merge_overlaps == on or not job.readable or job.status.is_busy or job.status == JobStatus.QUEUED:
            return
        if job.needs_export and not self._confirm_discard(1, "Change"):
            return
        job.merge_overlaps = on
        job.merge_error = None
        self._reread(job)

    def _ask_touching(self, job):
        """Asks (one file at a time) whether parts that touch each other stay
        separate or are merged into one solid."""
        self._touch_waiting.append(job)
        if len(self._touch_waiting) == 1:
            QTimer.singleShot(0, self._ask_next_touching)

    def _ask_next_touching(self):
        while self._touch_waiting:
            job = self._touch_waiting[0]
            if job in self.jobs and job.touching_choice is None and job.status == JobStatus.READY:
                ask = getattr(self.ui, "ask_touching", None)
                choice = ask(job) if ask and not os.environ.get("MMM_SELFTEST") else "split"
                self.set_touching_choice(job, choice or "split", convert=self.settings.convertOnDrop)
            self._touch_waiting.popleft()

    def set_touching_choice(self, job, choice, convert=False):
        """Keeps parts that touch each other separate ("split") or merges them
        into one solid ("merge", the file is read again). A finished file
        goes back to Ready."""
        if choice not in ("split", "merge") or job.status.is_busy or job.status == JobStatus.QUEUED:
            return
        before = job.touching_choice
        if before is not None and before != choice and job.needs_export and not self._confirm_discard(1, "Change"):
            return
        job.touching_choice = choice
        if choice == "split":
            if before == "merge":
                self._reread(job, convert)
                return
            if self.split_available and len(job.part_list) > 1:
                job.split = True
            if job.mesh_check is not None:
                job.mesh_check.kept_parts_separate()
            job.touch()
            self.state_changed.emit()
            if convert:
                self.enqueue(job)
            return
        if before != "merge":
            self._reread(job, convert)

    def _reread(self, job, convert=False):
        """Reads the file again with the current choices (results are dropped)."""
        self.discard_previews(job.clear_results())
        old = [job.input_preview] + [p.input_preview for p in job.part_list]
        old += [os.path.dirname(p) for p in old if p and os.path.basename(os.path.dirname(p)).startswith(f"{job.id}-parts-")]
        self.discard_previews(old)
        job.input_preview = None
        job.part_list = []
        job.error = job.error_reason = job.error_details = None
        job.stage = ""
        job.reinspecting = True
        job.convert_after_read = convert
        self._inspect(job)
        job.touch()
        self.state_changed.emit()

    def save_fixed_mesh(self, job, dest, completion):
        """Checks and fixes the mesh the same way the conversion does and
        saves the fixed copy (.stl or .3mf). completion(error message or
        None) is called on the main thread."""
        s = self.settings
        args = ["fix", job.path, "-o", dest, "--json", "--repair", s.repair, "--layout", s.plateLayout]
        if not s.removeDebris:
            args.append("--keep-debris")
        if job.merge_overlaps:
            args.append("--merge-overlaps")
        if job.touching:
            args += ["--touching-parts", job.touching_choice or "split"]
        def later(msg):
            # after this call has returned, like a real result
            QTimer.singleShot(0, lambda: completion(msg))

        try:
            t = EngineTask(args)
        except EngineMissing:
            later(ENGINE_MISSING_MESSAGE)
            return
        state = {"error": None, "result": False}

        def on_event(ev):
            if ev.get("event") == "error":
                state["error"] = friendly(ev.get("message"))
            elif ev.get("event") == "result":
                state["result"] = True

        def on_exit(code, _stderr):
            self._fix_tasks.discard(t)
            if code == 0 and state["result"]:
                completion(None)
            else:
                completion(state["error"] or crash_message(code))

        t.event.connect(on_event)
        t.finished.connect(on_exit)
        self._fix_tasks.add(t)
        try:
            t.start()
        except OSError as e:
            self._fix_tasks.discard(t)
            later(f"The converter couldn't start ({e.strerror or e}). Try reinstalling Manifold Mesh Mix.")

    def recheck(self):
        """Mesh repair or Remove debris changed: files that aren't converted
        yet are checked again, so the Mesh view shows what will happen."""
        for j in self.jobs:
            if j.readable and j.status == JobStatus.READY and j.task is None:
                self._reread(j)

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
        # the settings this run uses (for the "Auto used" mark and reports)
        job.run_settings = {k: getattr(s, k) for k in RUN_SETTINGS}
        job.run_settings["quality"] = s.quality_for(job.triangles)
        # The STEP files go into the app's temporary folder first; nothing is
        # saved next to the originals until the user exports.
        result_dir = os.path.join(self.preview_dir, "results", f"{job.id}-{stamp}")
        os.makedirs(result_dir, exist_ok=True)
        part_outs = [os.path.join(result_dir, f"part{i}.step") for i in range(len(job.part_list))] if job.is_split else []
        out = part_outs[0] if part_outs else os.path.join(result_dir, "result.step")
        prev = os.path.join(self.preview_dir, f"{job.id}-out-{stamp}.mmmp")
        args = ["convert", job.path, "-o", out, "--json",
                "--preset", s.preset, "--repair", s.repair, "--unit", s.unit,
                "--quality", f"{s.quality_for(job.triangles):.3f}", "--layout", s.plateLayout, "--preview", prev]
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
        if job.merge_overlaps:
            args.append("--merge-overlaps")
        if job.touching:
            args += ["--touching-parts", job.touching_choice or "split"]
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
                take_error(job, ev)
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
                mc = MeshCheck.from_event(ev.get("mesh_check"))
                if mc is not None:
                    job.mesh_check = mc
            job.touch()

        def on_exit(code, stderr):
            job.task = None
            if job not in self.jobs:
                # removed while converting: nothing will show these files
                parts = [os.path.join(self.preview_dir, f"{job.id}-part{p.index}-out-{stamp}.mmmp") for p in job.part_list]
                extra = [f for r in [job.result] + [p.result for p in job.part_list] if r for f in r.extra_files]
                self.discard_previews([result_dir, prev] + parts + extra)
                self._pump()
                return
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
        tasks = [j.task for j in self.jobs if j.task is not None] + list(self._fix_tasks)
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
        files = [job.input_preview, job.output_preview, job.output_path] + (job.result.extra_files if job.result else [])
        for p in job.part_list:
            files += [p.input_preview, p.output_preview, p.output_path] + (p.result.extra_files if p.result else [])
        return [f for f in files if f]

    def clear_finished(self):
        done = [j for j in self.jobs if j.status == JobStatus.DONE]
        if not done:
            return
        unexported = sum(1 for j in done if j.needs_export)
        if unexported and not self._confirm_discard(unexported, "Remove"):
            return
        # only the files asked about (others may have finished while asking)
        self.jobs = [j for j in self.jobs if j not in done]
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
