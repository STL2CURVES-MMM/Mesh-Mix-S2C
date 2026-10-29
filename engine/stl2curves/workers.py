"""
Worker processes for the slow steps that split into independent pieces: the surface
passes over separate smooth regions (features.py) and the blend fills (build.py).

OpenCascade, and numpy on the small arrays this program mostly handles, keep hold of
Python's lock while they work, so threads take turns rather than run at once; separate
processes don't. Starting them takes a second or so each on Windows, so they are started
in the background as a conversion begins (start), while the mesh is repaired and its
first surfaces found. Big data they all need (the mesh) is written once to a file
(share) and read by each worker once (load).
"""
import atexit
import multiprocessing
import os
import pickle
import tempfile
import threading
# (imported before stop is registered to run at exit: exit handlers run last-registered
# first, and multiprocessing's own one waits for every worker to end by itself, which
# workers still waiting for jobs never do)
from concurrent.futures import ProcessPoolExecutor

WORKERS = max(1, min(16, (os.cpu_count() or 2) - 2))     # processes working at once, at most
# Each worker holds its own copy of the mesh and what is worked out from it; on a big
# mesh that is gigabytes apiece. They are only as many as the free memory takes
# (measured 2026-10-18 on a 920k-triangle mesh, every triangle nearly a facet of its
# own: the mesh read back in a worker 3.2 GB, 0.6 GB once packed, 1.35 GB with its edge
# tables rebuilt, see features.Mesh.__getstate__; at 1.5 KB a triangle and unpacked, 16
# workers took 5-7 GB each, and the machine ran out of memory and page file; packed,
# each peaked at 2.34 GB in its seed passes):
WORKER_MB = 300             # MB a worker takes besides the mesh (its working set, imports done)
WORKER_KB = 2.6             # KB a worker takes per triangle of the part (2.34 GB seen at 920k)
MAIN_KB = 6.0               # KB this process takes per triangle (4.6 GB seen at 920k)
MEMORY_SHARE = 0.75         # share of the free memory the conversion may take

_pool = None                        # the pool, once its workers are all up
_launching = None                   # the pool while its workers are being launched
_starting = None                    # the thread launching them
_cancel = False                     # stop() was called while they were being launched
_leftovers = False
_files = []
_job = None                         # (Windows) the job object the workers are put in
_guarded = set()                    # pids of the workers put in it
_loaded = {}                        # (in a worker) key -> object, the few read last
memo = {}                           # (in a worker) anything worth keeping between jobs


def start(triangles=0):
    """Start the worker processes in the background (no-op if running or not wanted),
    as many as fit in the free memory for a part of this many triangles. They are kept
    for further parts and stopped when the program ends."""
    global _starting, _cancel
    n = _workers_for(triangles)
    if n > 1 and _pool is None and _starting is None:
        _cancel = False
        _starting = threading.Thread(target=_start, args=(n,), daemon=True)
        _starting.start()


def _workers_for(triangles):
    free = _free_memory()
    if free is None:
        return WORKERS
    each = WORKER_MB * 2**20 + WORKER_KB * 2**10 * triangles
    return max(1, min(WORKERS, int((MEMORY_SHARE * free - MAIN_KB * 2**10 * triangles) // each)))


def _free_memory():
    """Bytes of physical memory free now, or None if that can't be found out."""
    try:
        if os.name == "nt":
            import ctypes

            class Status(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in ("total", "free", "total_page", "free_page",
                                                             "total_virtual", "free_virtual", "free_extended")]
            status = Status()
            status.length = ctypes.sizeof(Status)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return status.free
        return os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, OSError, ValueError):
        return None


def _start(n):
    global _pool, _launching
    # (one BLAS thread in each worker: the parallel work is the processes themselves, and
    # OpenBLAS sets aside buffers for a thread per core as numpy loads, 1.5 GB of
    # committed memory per worker that was never touched, which with 16 workers on a big
    # mesh ran Windows out of page file. Read by the workers as they start; numpy here
    # in this process has loaded already and keeps its own threads)
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    try:
        pool = _launching = ProcessPoolExecutor(n, initializer=_watch_parent)
        # (on Windows a worker is only launched when a job finds none idle, one at a
        # time, in the middle of sending work out: they are all launched here instead,
        # one by one, minding stop() meanwhile. Each launch takes a second or so, however
        # it is done (16 took 16-18 s), so the pool is handed out as soon as its first
        # worker answers and the rest join it as they come up: waiting for all of them,
        # a 7,600-triangle part spent 11 of its 30 s waiting)
        pool._adjust_process_count = lambda *args: None
        try:
            while len(pool._processes) < n and not _cancel:
                pool._spawn_process()
                _guard(pool)
                if _pool is None and not _cancel:
                    pool.submit(int).result()
                    _pool = pool
        except Exception:
            pass
        _guard(pool)
        if _pool is None and not _cancel and pool._processes:
            pool.submit(int).result()
            _pool = pool
    except Exception:
        _pool = None                # (no worker processes to be had: the work is done here)


def get(wait=True):
    """The pool of worker processes once started, or None. If it is still starting, wait
    for it (wait True) or not (None: small jobs are quicker done in this process)."""
    global _starting
    if _starting is not None:
        # (usable once its first worker is up: see _start)
        while _pool is None and _starting.is_alive():
            if not wait:
                return None
            _starting.join(0.02)
        if not _starting.is_alive():
            _starting = None
    if _pool is not None:
        _guard(_pool)               # (the pool replaces a worker that died)
    return _pool


def abandoned():
    """A job was given up while still running (an overrunning fill or fuse): the workers
    are restarted after this part (see finish), not left grinding into the next one."""
    global _leftovers
    _leftovers = True


def finish():
    """At the end of a part: keep the workers for the next one, unless jobs were given up."""
    global _leftovers
    if _leftovers:
        _leftovers = False
        stop()


def broken():
    """The pool failed: stop using it (the work falls back to this process)."""
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        _terminate(pool)


def stop():
    """Stop the worker processes (any still grinding on an overrunning job included, and
    any still being launched)."""
    global _pool, _launching, _starting, _cancel
    _cancel = True
    if _starting is not None:
        _starting.join()            # (at most the one launch under way)
        _starting = None
    pools = {id(p): p for p in (_pool, _launching) if p is not None}
    _pool = _launching = None
    for pool in pools.values():
        _terminate(pool)
    for path in _files:
        try:
            os.remove(path)
        except OSError:
            pass
    _files.clear()
    _guarded.clear()


atexit.register(stop)


def _stop_launching():
    """At interpreter shutdown, before concurrent.futures' own handler: stop launching
    workers (the one under way finished) and stop them. That handler tells the workers it knows of to
    quit and waits for them all, so one launched after it looked never heard and the
    program never ended (a part converted in 11 s, its workers still coming up, then
    hung)."""
    global _cancel
    _cancel = True
    if _starting is not None:
        _starting.join()
    # (and the workers stopped outright: told to quit, each still coming up finished
    # loading first, 5 s on a small part's exit)
    stop()


# (threading's exit handlers run last-registered first: this one before the pool's)
threading._register_atexit(_stop_launching)


def _terminate(pool):
    for p in list((getattr(pool, "_processes", None) or {}).values()):
        p.terminate()
    pool.shutdown(wait=False, cancel_futures=True)


def _guard(pool):
    """Tie the pool's workers to this process's life. Exit handlers (stop) don't run when
    this process is killed outright (Task Manager, a closed window, a stopped batch), and
    Windows leaves children running when their parent dies: the workers sat waiting for
    jobs for good, each holding its copy of the mesh. On Windows they go in a job object
    that kills them when its last handle, held only by this process, closes (only the
    workers: put in the job, a host program like FreeCAD would take its own later
    children down with it). Elsewhere each worker watches for its parent itself."""
    global _job
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        if _job is None:
            kernel.CreateJobObjectW.restype = wintypes.HANDLE
            job = kernel.CreateJobObjectW(None, None)      # (handle not inherited)
            if not job:
                return

            class Limits(ctypes.Structure):
                _fields_ = [("per_process_time", ctypes.c_int64), ("per_job_time", ctypes.c_int64),
                            ("flags", wintypes.DWORD), ("min_ws", ctypes.c_size_t),
                            ("max_ws", ctypes.c_size_t), ("active", wintypes.DWORD),
                            ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                            ("scheduling", wintypes.DWORD)]

            class Extended(ctypes.Structure):
                _fields_ = [("basic", Limits), ("io", ctypes.c_uint64 * 6)] + [
                    (name, ctypes.c_size_t) for name in ("process_memory", "job_memory",
                                                         "peak_process_memory", "peak_job_memory")]
            info = Extended()
            info.basic.flags = 0x2000           # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(wintypes.HANDLE(job), 9,  # extended limits
                                                  ctypes.byref(info), ctypes.sizeof(info)):
                kernel.CloseHandle(wintypes.HANDLE(job))
                return
            _job = job
        for pid, p in list((getattr(pool, "_processes", None) or {}).items()):
            if pid not in _guarded and p.is_alive():
                # (a Process's sentinel on Windows is its process handle)
                if kernel.AssignProcessToJobObject(wintypes.HANDLE(_job), wintypes.HANDLE(p.sentinel)):
                    _guarded.add(pid)
    except Exception:
        pass                        # (no job: the workers are only stopped at a normal exit)


def _watch_parent():
    """In a worker: end when the parent process does (outside Windows the job object of
    _guard isn't there; the wait is on the parent's handle or pipe, no polling)."""
    parent = multiprocessing.parent_process()
    if parent is not None and os.name != "nt":
        def watch():
            parent.join()
            os._exit(1)
        threading.Thread(target=watch, daemon=True).start()


def share(obj):
    """Write obj where every worker can read it (once each): its key for load()."""
    fd, path = tempfile.mkstemp(prefix="stl2curves_", suffix=".pkl")
    with os.fdopen(fd, "wb") as f:
        pickle.dump(obj, f, protocol=pickle.HIGHEST_PROTOCOL)
    _files.append(path)
    return path


def load(key):
    """In a worker: the object shared under this key (read once, then kept)."""
    if key not in _loaded:
        while len(_loaded) >= 3:    # (the mesh and what the current pass needs)
            del _loaded[next(iter(_loaded))]
        with open(key, "rb") as f:
            _loaded[key] = pickle.load(f)
    else:
        _loaded[key] = _loaded.pop(key)     # (most recently used last)
    return _loaded[key]
