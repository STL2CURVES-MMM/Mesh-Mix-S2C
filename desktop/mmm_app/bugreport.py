"""A plain-text report about one file, for sending to the developer when
something went wrong: the app and system, the settings, what the mesh check
found, and the error with its technical details.

Nothing personal goes in: the file's folder, the home folder (which holds
the user name) and the temporary folder are replaced everywhere, so only
the file's own name is left.
"""
import os
import platform
import re
import sys
import tempfile
import time
from types import SimpleNamespace

from . import system
from .model import RUN_SETTINGS, result_unit
from .settings import MAX_FACES, PLATE_LAYOUTS, PRESETS, REPAIR_LEVELS, UNITS, choice
from .version import __version__

STATUS_WORDS = {"fixed": "Fixed", "not_fixed": "Not fixed", "action": "Your choice", "note": "Note"}


def _variants(path):
    """A folder path as it may appear in text: as is, normalized, and with
    the other kind of slashes."""
    out = set()
    if not path:
        return out
    for p in (path, os.path.normpath(path), os.path.abspath(path)):
        p = p.rstrip("/\\")
        if len(p) < 3:  # never replace "/" or "C:"
            continue
        out.add(p)
        out.add(p.replace("\\", "/"))
        back = p.replace("/", "\\")
        out.add(back)
        # as Python prints paths inside error messages (repr): C:\\Users\\...
        out.add(back.replace("\\", "\\\\"))
    return out


def scrub(text, job_path):
    """Removes folder names that could identify the user."""
    if not text:
        return text
    places = [(os.path.dirname(os.path.abspath(job_path)), "<file folder>"),
              (tempfile.gettempdir(), "<temp>"),
              (os.path.expanduser("~"), "<home>")]
    # longest first, so a folder inside the home folder wins over the home folder
    pairs = sorted(((v, label) for path, label in places for v in _variants(path)), key=lambda x: -len(x[0]))
    for v, label in pairs:
        text = re.sub(re.escape(v), label.replace("\\", "\\\\"), text, flags=re.IGNORECASE if system.IS_WINDOWS else 0)
    # a user name left in some other path (C:\Users\name\..., /home/name/...)
    # (names can have spaces: up to the next separator or quote)
    text = re.sub(r"(?i)([A-Z]:[\\/]+Users[\\/]+)[^\\/\"'\r\n]+", r"\1<user>", text)
    text = re.sub(r"(/(?:home|Users)/)[^/\"'\r\n]+", r"\1<user>", text)
    return text


def _system():
    if system.IS_MAC:
        name = f"macOS {platform.mac_ver()[0]}"
    elif system.IS_WINDOWS:
        v = sys.getwindowsversion()
        # Windows 11 still reports itself as 10.0; build 22000 and later is 11
        name = f"Windows {'11' if v.major == 10 and v.build >= 22000 else platform.release()} (build {v.build})"
    else:
        name = "Linux"
        try:
            info = platform.freedesktop_os_release()
            name = info.get("PRETTY_NAME") or name
        except (OSError, AttributeError):
            pass
    return f"{name}, {platform.machine() or 'unknown processor'}"


def _qt():
    try:
        from PySide6 import __version__ as pyside
        from PySide6.QtCore import qVersion
        return f"Qt {qVersion()} (PySide6 {pyside})"
    except Exception:  # noqa: BLE001 - only informative
        return "Qt unknown"


def _size(n):
    if n is None:
        return "unknown size"
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,.0f} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024.0
    return ""


def build(job, settings):
    """The report for one job, as text."""
    s = settings
    lines = []
    add = lines.append
    add("Mesh Mix-S2C - problem report")
    add("=" * 34)
    add(f"Created:   {time.strftime('%Y-%m-%d %H:%M')}")
    add(f"App:       Mesh Mix-S2C {__version__} ({'installed app' if system.FROZEN else 'from source'})")
    add(f"System:    {_system()}")
    add(f"Toolkit:   Python {platform.python_version()}, {_qt()}")
    add("")
    add("File")
    add(f"  Name:      {job.file_name}   (the folder isn't included)")
    add(f"  Size:      {_size(job.file_size)}")
    if job.triangles is not None:
        add(f"  Triangles: {job.triangles:,}")
    if job.bodies is not None:
        add(f"  Pieces:    {job.bodies:,}" + (f" ({job.open_shells:,} not closed)" if job.open_shells else ""))
    if job.parts:
        add(f"  Parts:     {job.parts}")
    if job.bbox_size:
        add("  Dimensions: " + " x ".join(f"{v:.4g}" for v in job.bbox_size) + f" {s.unit}")
    add("")
    # the settings the last conversion used (else the current ones)
    run = job.run_settings or {}
    r = SimpleNamespace(**{k: run.get(k, getattr(s, k)) for k in RUN_SETTINGS})
    add("Settings" + (" (of the last conversion)" if run else " (current)"))
    add(f"  Preset:        {choice(PRESETS, r.preset).title}")
    q = run["quality"] if run else s.quality_for(job.triangles)
    auto = r.preset == "auto" and r.autoQuality
    add(f"  Quality:       {'Auto, ' if auto else ''}{round(q * 100)}%")
    add(f"  Mesh repair:   {choice(REPAIR_LEVELS, r.repair).title}")
    add(f"  Units:         {choice(UNITS, r.unit).title}")
    add(f"  3MF projects:  {choice(PLATE_LAYOUTS, r.plateLayout).title}")
    add(f"  Remove debris: {'yes' if r.removeDebris else 'no'}")
    add(f"  Merge overlapping pieces: {'yes' if job.merge_overlaps else 'no'}")
    if job.touching:
        add(f"  Touching parts: {job.touching_choice or 'not asked yet'}")
    add(f"  Tolerance:     {r.customTolerance:g} {r.unit}" if r.useCustomTolerance and r.customTolerance > 0
        else "  Tolerance:     automatic")
    add(f"  Max faces:     {choice(MAX_FACES, r.maxFaces).title}")
    add(f"  Split to parts: {'yes' if job.is_split else 'no'}")
    add("")
    mc = job.mesh_check
    add("Mesh check")
    if mc is None:
        add("  (not reported)")
    else:
        ready = {True: "yes", False: "no", None: "not reported"}[mc.ready]
        add(f"  Result: {mc.title}; ready for conversion: {ready}")
        for i in mc.issues:
            where = f" [{i.part}]" if i.part else ""
            add(f"  - {STATUS_WORDS.get(i.status, i.status)}{where}: {i.problem} -> {i.result}")
        for p in mc.problems:
            add(f"  ! Still wrong after fixing: {p}")
        if job.merge_error:
            add(f"  ! Merging the overlapping pieces failed: {job.merge_error}")
    add("")
    add("Conversion")
    add(f"  Status: {job.status.title}" + (f" ({job.stage})" if job.stage and job.stage != job.status.title else ""))
    r = job.result
    if r is not None:
        add(f"  Preset used: {r.preset_title}; faces {r.faces:,}; solids {r.solids}; shells {r.shells}; "
            f"STEP {_size(r.file_size)}; {r.seconds:.1f} s")
        if r.fidelity is not None:
            add(f"  Compared with the mesh: {r.fidelity.summary(result_unit(job, s))}"
                + ("" if r.fidelity.ok else " (doesn't follow it closely everywhere)"))
        if r.checked:
            add("  STEP file read back and checked" + (" (repaired automatically: " + ", ".join(r.repaired) + ")"
                                                      if r.repaired else ""))
        for c in r.checks:
            add(f"  - {c.level}: {c.message}")
        a = r.alternative
        if a is not None:
            add(f"  Second (valid) version offered: valid {a.valid}; faces {a.faces:,}"
                + (f"; compared with the mesh: {a.fidelity.summary(result_unit(job, s))}" if a.fidelity else "")
                + ("; the user picked it" if r.use_alternative else "; the user kept the closest one"))
            for c in a.checks:
                add(f"    - {c.level}: {c.message}")
    if job.error:
        add(f"  What went wrong: {job.error}")
        if job.error_reason:
            add(f"  Why: {job.error_reason}")
    if job.warnings:
        add("")
        add("Warnings")
        for w in job.warnings:
            add(f"  - {w}")
    if job.error_details:
        add("")
        add("Technical details")
        for ln in job.error_details.rstrip().splitlines():
            add("  " + ln)
    if job.log:
        add("")
        add("Log")
        for ln in job.log[-400:]:
            add("  " + ln)
    add("")
    return scrub("\n".join(lines), job.path)


def default_name(job):
    from .model import safe_file_name
    return safe_file_name(f"Mesh Mix-S2C report - {job.name}") + ".txt"


def save(job, settings, path):
    text = build(job, settings)
    tmp = path + ".part"
    with open(tmp, "w", encoding="utf-8", newline="\r\n" if system.IS_WINDOWS else "\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
    return text
