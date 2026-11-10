"""Faults in this program, kept apart from the failures of geometry.

The engine has many fallbacks: a fit that doesn't come out, a face OpenCascade refuses, a
method that makes nothing usable. Those are expected, and each has its "except Exception"
and its next thing to try. The trouble is that such a handler catches a plain bug just the
same. On 2026-11-02 a new function's `name 'sparse' is not defined` was caught that way: the
log said only "cad didn't work", five parts fell back to flat facets, and the results still
scored valid and 100% close, with 3 of the 5 showing an open shell.

So every broad handler calls caught(e) first. It tells the two apart by the exception's type
(OpenCascade's own exceptions and numpy's LinAlgError derive from neither NameError nor its
kind), and for a programming error:

- it is written down, with where it was raised and where it was caught, also from a worker
  process (they append to one file, named in the environment, as a worker's memory
  dies with it);
- with MM_S2C_STRICT=1 (tests, benchmark runs) it is raised again, so that it stops the
  conversion and the run fails loudly;
- in the shipped app the handler goes on to its fallback, so the user still gets the best
  there is, and attach() puts the faults on the result and tells the user plainly.

Nothing else changes: with no programming error, caught() does nothing at all, so results stay
the same. tests/mmm/test_faults.py checks that every broad handler in the package calls it.

Written 2026-11-02 for the merged engine (not part of Manifold Mesh Mix as published; see
NOTICE.md). It imports nothing of the engine's, so any module can use it.
"""
import atexit
import json
import os
import re
import sys
import tempfile
import threading
import time
import traceback

STRICT = "MM_S2C_STRICT"            # environment: "1" raises programming errors again (tests, benchmarks)
FILE = "MM_S2C_FAULTS_FILE"         # environment: the file this run's processes record faults in
AUDIT = "MM_S2C_FAULT_AUDIT"        # environment: a file listing every exception a handler caught, of any kind
MESSAGE_CHARS = 300                     # characters of an exception's message kept

# What is never a failure of the geometry. (KeyError and IndexError are LookupError. A TypeError
# is also what OCP raises for arguments of the wrong type, which is a bug of the same kind.)
CODE_ERRORS = (NameError, AttributeError, TypeError, ImportError, LookupError, AssertionError, SyntaxError)
# ValueErrors are mostly data (a fit of too few points), except these, which say the code is
# handling its arrays wrongly, whatever the data.
_VALUE_BUGS = ("could not be broadcast", "values to unpack", "same number of dimensions", "array dimensions",
               "matmul", "truth value of an array")

_found = {}                             # (type, file, line) -> fault, in this process
_told = set()                           # the keys the user has been told of
_lock = threading.Lock()
_FRAME = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')


class CodeFault(RuntimeError):
    """In strict mode: a programming error was recorded somewhere that couldn't stop the
    conversion by raising it (a worker's thread)."""


def strict():
    return os.environ.get(STRICT) == "1"


def kind(e):
    """"code" for a programming error, "memory" for running out, None for what fallbacks are for."""
    if isinstance(e, MemoryError):
        return "memory"
    if isinstance(e, CODE_ERRORS):
        return "code"
    if isinstance(e, ValueError) and any(s in str(e) for s in _VALUE_BUGS):
        return "code"
    return None


def caught(e, _up=1):
    """To be called first thing in a broad exception handler, with what it caught. Records a
    programming error, and raises it again in strict mode. Anything else it leaves alone."""
    frame = sys._getframe(_up)
    at = f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_lineno}"
    what = kind(e)
    if os.environ.get(AUDIT):
        _audit(e, what, at)
    if what is None:
        return
    _record(e, what, at)
    if strict():
        raise e


def clean(future):
    """Whether a finished job of a worker process ended without an exception. A job's failure is
    looked at this way where no handler sees it, so it goes through caught() too."""
    e = future.exception()
    if e is None:
        return True
    caught(e, 2)
    return False


def event(what, detail=""):
    """Note something that went wrong around the engine rather than in it (the worker processes
    breaking down): reported on the result like a fault, never raised."""
    _store({"kind": "event", "type": what, "message": str(detail)[:MESSAGE_CHARS], "file": "", "line": 0,
            "function": "", "handled_at": "", "count": 1, "pid": os.getpid()})


def _raised_at(e):
    """Where e was raised, in this program's code rather than a library's: (file name, line,
    function, traceback's text). An exception from a worker process carries its worker's
    traceback as text, in its cause."""
    cause = e.__cause__
    remote = getattr(cause, "tb", None) if cause is not None else None
    if isinstance(remote, str):
        frames = _FRAME.findall(remote)
        text = remote
    else:
        frames = [(f.filename, f.lineno, f.name) for f in traceback.extract_tb(e.__traceback__)]
        text = "".join(traceback.format_tb(e.__traceback__))
    if not frames:
        return "", 0, "", ""
    # (a library raising what our call gave it: the place to look at is our call. Tests are ours too)
    ours = [x for x in frames if not any(s in x[0] for s in ("site-packages", "concurrent", "<string>", "<frozen"))]
    path, line, func = (ours or frames)[-1]
    return os.path.basename(path), int(line), func, text


def _record(e, what, at):
    file, line, func, text = _raised_at(e)
    fault = {"kind": what, "type": type(e).__name__, "message": str(e)[:MESSAGE_CHARS], "file": file, "line": line,
             "function": func, "handled_at": at, "count": 1, "traceback": text[-2000:], "pid": os.getpid()}
    new = _store(fault)
    if new:
        # (at once, to the log: a later hang or crash mustn't take it with it. stderr: stdout may be JSON)
        print(f"ENGINE FAULT, {what}: {fault['type']}: {fault['message']} at {file}:{line} in {func}, "
              f"caught at {at}", file=sys.stderr, flush=True)


def _store(fault):
    with _lock:
        key = (fault["type"], fault["file"], fault["line"])
        if key in _found:
            _found[key]["count"] += fault["count"]
            new = False
        else:
            _found[key] = fault
            new = True
    path = os.environ.get(FILE)
    if path and path != _own_file[0]:
        # (a worker: the process that reports it reads this)
        _append(path, fault)
    return new


def _append(path, row):
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
    except OSError:
        pass


def _audit(e, what, at):
    row = {"t": round(time.time(), 2), "type": type(e).__name__, "kind": what, "message": str(e)[:MESSAGE_CHARS],
           "raised": ":".join(str(x) for x in _raised_at(e)[:3]), "caught": at, "pid": os.getpid()}
    _append(os.environ[AUDIT], row)


# The file for this run: named once, by the first process, and handed down in the environment, so
# worker processes started later find it. _own_file is the name if this is that process.
_own_file = [None]


def _setup():
    if not os.environ.get(FILE):
        path = os.path.join(tempfile.gettempdir(), f"stl2curves_faults_{os.getpid()}_{time.time_ns()}.jsonl")
        os.environ[FILE] = path
        _own_file[0] = path
        atexit.register(_remove, path)


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


_setup()


def reset():
    """A new conversion in this process (the workers stay up and share the file)."""
    with _lock:
        _found.clear()
        _told.clear()
    path = _own_file[0]
    if path:
        _remove(path)


def collected():
    """Every fault recorded so far, by this process and those it started, one row per spot."""
    with _lock:
        merged = {k: dict(v) for k, v in _found.items()}
    path = _own_file[0]
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue            # (a line being written)
                    key = (row["type"], row["file"], row["line"])
                    if key in merged:
                        merged[key]["count"] += row["count"]
                    else:
                        merged[key] = row
        except OSError:
            pass
    return list(merged.values())


def message(found):
    """What the user is told. The file is saved, and it may be poorer than it should be."""
    bugs = [f for f in found if f["kind"] == "code"]
    parts = []
    if bugs:
        first = bugs[0]
        parts.append(f"A bug in this program, not a problem with your file, got in the way ({first['type']} in "
                     f"{first['file']} line {first['line']}" + (f", and {len(bugs) - 1} more" if len(bugs) > 1 else "")
                     + "). The best result it could make was saved, but it may be worse than it should be: "
                       "check it before relying on it, and please save a report and send it.")
    if any(f["kind"] == "memory" for f in found):
        parts.append("The computer ran short of memory during a step, which was given up, so the result may be "
                     "worse than it should be. Closing other programs and converting again may help.")
    events = sorted({f["type"] for f in found if f["kind"] == "event"})
    if events:
        parts.append("Also: " + "; ".join(events) + ".")
    return " ".join(parts)


def attach(res, report=None):
    """Put what has been recorded on a result, and tell the user once of each. In strict mode,
    what couldn't be raised where it happened is raised here."""
    found = collected()
    if not found:
        return res
    # (only "code" and "memory" can raise; an event says nothing is wrong with the engine's code)
    bad = [f for f in found if f["kind"] != "event"]
    if strict() and bad:
        raise CodeFault(summary(bad))
    res["code_errors"] = [{k: v for k, v in f.items() if k != "pid"} for f in bad]
    if any(f["kind"] == "event" for f in found):
        res["engine_events"] = [f["type"] for f in found if f["kind"] == "event"]
    fresh = [f for f in found if (f["type"], f["file"], f["line"]) not in _told]
    if fresh and report is not None:
        report.warn(message(fresh))
    _told.update((f["type"], f["file"], f["line"]) for f in found)
    return res


def summary(found):
    """The faults on a line or a few, for a bug report or an error message."""
    return "; ".join(f"{f['type']}: {f['message']} ({f['file']}:{f['line']}, caught at {f.get('handled_at', '')}, "
                     f"{f['count']}x)" for f in found)
