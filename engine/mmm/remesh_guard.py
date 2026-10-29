"""The quad remesher (Instant Meshes), run in a child process that is watched step by step.

Written 2026-10-28 for the stl2curves merged engine (not part of Manifold Mesh Mix as
published; see NOTICE.md).

Why: Instant Meshes' Step 5 ("Snapping and removing unnecessary edges", extract.cpp) has an
inner loop with no round limit. On some inputs it never ends: the repeater hub's body 6 at a
target of 914 cage vertices ran for hours, while 913 and 915 finished in 2 s. The library
holds Python's lock all the while, so no thread in the same process can stop it. On that
mesh 4 targets in 31 (900-930) froze; on MMM's test models none in 125. The freeze is
repeatable: the same input and target always freeze, and a target that finishes always
gives the same quads (deterministic=True), so nudging the target is a deterministic cure.

How:
1. One child process per call (start-up 0.15-0.2 s). It loads only numpy and the
   remesher, and prints the remesher's own progress, one step name as each step starts.
2. This process reads that output live and the child's CPU time every POLL_S seconds.
   Each step has an expected time, from the mesh's size and the speeds measured on the
   owner's laptop (STEP_US). The child is stuck when it has been in one step for longer
   than max(STEP_FLOOR_S, STEP_K x expected) and has kept its CPU busy (at least
   BUSY_SHARE of that wall time): a child that is merely starved of CPU by a busy
   machine uses little CPU and is never taken for stuck. Step 5 normally takes 3-9 ms,
   so a stall is called after about 2 s.
3. Backups: a whole-call limit (max(BACKUP_S, BACKUP_K x the expected total)) and a
   memory limit (MEM_MB, the runaway subdivision of a mesh far from the origin took
   14 GB in 90 s).
4. A stuck child is killed and the remesher is run again at a nudged target, in a fixed
   order (nudged_targets). The child can't outlive this process: on Windows it is put
   in a job object that kills it when this process ends; elsewhere it limits its own
   CPU time (RLIMIT_CPU, enforced by the kernel even while the library holds the lock).

Frozen builds (PyInstaller) have no interpreter to run code with: the child is then the
program itself, started with --remesh-child. The program's entry point must call
child_main() for that flag before anything else (MMM's mmm_engine.py; packaging work).
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

import numpy as np

from .report import NULL

GUARD = True            # run the remesher in a watched child (False: in this process, unwatched)
POLL_S = 0.1            # s between looks at the child's output and CPU time
STEP_FLOOR_S = 2.0      # s, the least time any step is allowed before it can be called stuck
STEP_K = 50.0           # times a step's expected time it may take (slower machines, busy ones)
BUSY_SHARE = 0.5        # CPU seconds per wall second the child must use in a step to be stuck
BACKUP_S = 60.0         # s, the least time the whole call is allowed, whatever the steps say
BACKUP_K = 30.0         # times the expected total the whole call may take
TOTAL_US = 4.0          # µs per (subdivided) vertex, the whole call's expected time
MEM_MB = 4096           # MB of memory the child may hold
CHILD_CPU_S = 1800      # s of CPU the child may use in all (POSIX self-limit, against orphans)
FREEZE_CAP = 6          # stuck runs allowed per smooth_shell call before giving up remeshing
BIG_NUDGE = 0.07        # the last nudge: about this share of the target

# Expected time of each step, in µs per unit: per vertex of the (subdivided) input mesh
# ("in"), or per vertex of the output ("out", known from Step 3 on). Measured 2026-10-27
# on the hub (442k-922k subdivided vertices) and MMM's test models: Step 1 at most 0.43,
# Step 2 0.59, the position field 0.87, Step 5 2-3 per output vertex. Subdividing works
# on the input's triangles, before the subdivided size is known: its rate is per input
# vertex and generous (0.26 s for 61k vertices seen), as its runaway is caught by MEM_MB.
STEP_US = (("Step 5", 3.0, "out"), ("Step 4", 20.0, "out"), ("Step 1", 0.6, "in"), ("Step 2", 0.7, "in"),
           ("Optimizing position field", 1.0, "in"), ("Optimizing orientation field", 0.5, "in"),
           ("Subdividing mesh", 20.0, "in"))
OTHER_US = 0.5          # µs per input vertex, any other step

# a step starts where the remesher prints its name and " .."
_STEP = re.compile(r"(Step \d+a?: [^\n.]*?|[A-Z][A-Za-z &]+?) \.\.")
_SUB = re.compile(r"new V=(\d+)")
_OUT = re.compile(r"Step 3: Assigning vertices \.\. done\. \((\d+) vertices")
_DONE = "\nREMESH_DONE\n"


class Stalled(Exception):
    """The child was stopped: stuck in a step, past the backup limit, or out of memory."""

    def __init__(self, why, step=None, seconds=0.0):
        super().__init__(why)
        self.why, self.step, self.seconds = why, step, seconds


class Stuck(Exception):
    """No target gave a quad mesh: every try was stopped (or the budget ran out)."""


class Budget:
    """Stuck runs still allowed (shared by the calls of one smooth_shell)."""

    def __init__(self, cap=FREEZE_CAP):
        self.left = cap
        self.used = 0


def _prime(n):
    if n < 2:
        return False
    k = 2
    while k * k <= n:
        if n % k == 0:
            return False
        k += 1
    return True


def big_jump(tv):
    """The last nudge's step: the smallest prime of at least BIG_NUDGE x tv (and at least
    3, to clear the small nudges), so the jump lands well away from the stuck target."""
    j = max(3, int(round(BIG_NUDGE * tv)))
    while not _prime(j):
        j += 1
    return j


def nudged_targets(tv):
    """The targets tried, in order: tv itself, one and two vertices either side (freezes
    come in small clusters: 922 and 923 both froze on the hub, 921 and 924 didn't), then
    one big jump."""
    out = []
    for t in (tv, tv - 1, tv + 1, tv - 2, tv + 2, tv + big_jump(tv)):
        if t >= 4 and t not in out:
            out.append(t)
    return out


# ----------------------------------------------------------------------------
# The child
# ----------------------------------------------------------------------------


def child_main(argv):
    """In the child: remesh the input file's mesh and write the quads to the output file.
    argv: input .npz, output .npz, target, crease angle."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (CHILD_CPU_S, CHILD_CPU_S + 5))
    except Exception:
        pass                    # (Windows: the job object does this)
    import pynanoinstantmeshes as nim

    src, dst, tv, crease = argv[0], argv[1], int(argv[2]), float(argv[3])
    d = np.load(src)
    qv, qf = nim.remesh(d["V"], d["F"], tv, rosy=4, posy=4, creaseAngle=crease, deterministic=True)
    sys.stdout.write(_DONE)
    sys.stdout.flush()
    np.savez(dst, V=np.asarray(qv), F=np.asarray(qf))


_BOOT = ("import sys, json, site\n"
         "paths = json.loads(sys.argv[1])\n"
         "sys.path[:0] = paths\n"
         "[site.addsitedir(p) for p in paths if p.endswith('site-packages')]\n"
         "from {pkg}.remesh_guard import child_main\n"
         "child_main(sys.argv[2:])\n")


def python():
    """The interpreter to run a child with. (A Windows venv's python.exe is a launcher that
    starts the real interpreter as a child of its own: its CPU time reads 0, and killing it
    can leave the real one running; the real one is used, given this process's paths.)"""
    if os.name == "nt" and getattr(sys, "_base_executable", None) and sys.prefix != sys.base_prefix:
        return sys._base_executable
    return sys.executable


def child_paths():
    """This process's import paths, for a child started with python()."""
    return json.dumps([p for p in sys.path if p])


def _command(args):
    if getattr(sys, "frozen", False):
        return [sys.executable, "--remesh-child"] + args
    return [python(), "-s", "-u", "-c", _BOOT.format(pkg=__package__), child_paths()] + args


# ----------------------------------------------------------------------------
# Watching the child
# ----------------------------------------------------------------------------

_job = None


def _keep_with_us(p):
    """Windows: put the child in a job object that kills it when this process ends (its
    handle, held only here, closes then). Elsewhere the child limits its own CPU time."""
    global _job
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        if _job is None:
            k.CreateJobObjectW.restype = wintypes.HANDLE
            job = k.CreateJobObjectW(None, None)
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
                    (n, ctypes.c_size_t) for n in ("process_memory", "job_memory",
                                                   "peak_process_memory", "peak_job_memory")]
            info = Extended()
            info.basic.flags = 0x2000       # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k.SetInformationJobObject(wintypes.HANDLE(job), 9, ctypes.byref(info), ctypes.sizeof(info)):
                k.CloseHandle(wintypes.HANDLE(job))
                return
            _job = job
        k.AssignProcessToJobObject(wintypes.HANDLE(_job), wintypes.HANDLE(int(p._handle)))
    except Exception:
        pass


def _usage(p):
    """(CPU seconds, memory MB) the child has used so far; None for what can't be read."""
    try:
        if os.name == "nt":
            import ctypes

            k = ctypes.windll.kernel32
            c, e, kt, ut = (ctypes.c_ulonglong() for _ in range(4))
            h = ctypes.c_void_p(int(p._handle))
            cpu = (kt.value + ut.value) / 1e7 if k.GetProcessTimes(h, ctypes.byref(c), ctypes.byref(e),
                                                                   ctypes.byref(kt), ctypes.byref(ut)) else None

            class Mem(ctypes.Structure):
                _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
                    (n, ctypes.c_size_t) for n in ("peak_ws", "ws", "qpp", "qp", "qpnp", "qnp", "pf", "peak_pf")]
            m = Mem()
            m.cb = ctypes.sizeof(Mem)
            mem = m.ws / 2**20 if k.K32GetProcessMemoryInfo(h, ctypes.byref(m), m.cb) else None
            return cpu, mem
        if os.path.exists(f"/proc/{p.pid}/stat"):
            with open(f"/proc/{p.pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            tick = os.sysconf("SC_CLK_TCK")
            cpu = (int(fields[11]) + int(fields[12])) / tick
            mem = int(fields[21]) * os.sysconf("SC_PAGE_SIZE") / 2**20
            return cpu, mem
        out = subprocess.run(["ps", "-o", "time=,rss=", "-p", str(p.pid)], capture_output=True, text=True,
                             timeout=5).stdout.split()
        parts = [float(x) for x in out[0].replace("-", ":").split(":")]
        cpu = 0.0
        for x in parts:
            cpu = cpu * 60 + x
        return cpu, float(out[1]) / 1024
    except Exception:
        return None, None


def expected_step_s(step, n_in, n_out):
    """Seconds a step is expected to take (see STEP_US)."""
    for name, us, unit in STEP_US:
        if step.startswith(name):
            n = n_out if unit == "out" and n_out else n_in
            return us * n * 1e-6
    return OTHER_US * n_in * 1e-6


def run_child(V, F, tv, crease_deg=0.0, command=None):
    """Remesh in a watched child; (quad vertices, quads) or Stalled. ``command`` (tests)
    replaces the child's command line; it gets the input, output, target and crease."""
    tmp = tempfile.mkdtemp(prefix="mmm-remesh-")
    src, dst = os.path.join(tmp, "in.npz"), os.path.join(tmp, "out.npz")
    np.savez(src, V=np.ascontiguousarray(V, dtype=np.float32), F=np.ascontiguousarray(F, dtype=np.uint32))
    args = [src, dst, str(int(tv)), repr(float(crease_deg))]
    env = dict(os.environ, OPENBLAS_NUM_THREADS="1")
    p = subprocess.Popen((command or _command)(args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    _keep_with_us(p)
    chunks = []

    def reader():
        while True:
            try:
                b = os.read(p.stdout.fileno(), 65536)
            except OSError:
                return
            if not b:
                return
            chunks.append(b.decode("utf-8", "replace"))

    th = threading.Thread(target=reader, daemon=True)
    th.start()
    t0 = time.time()
    n_in, n_out = len(V), 0
    step, step_t, step_cpu, pos = None, t0, None, 0
    why = None
    try:
        while True:
            time.sleep(POLL_S)
            now = time.time()
            cpu, mem = _usage(p)
            done = p.poll() is not None
            text = "".join(chunks)
            m = _SUB.search(text)
            if m:
                n_in = max(n_in, int(m.group(1)))
            m = _OUT.search(text)
            if m:
                n_out = int(m.group(1))
            # (a step's name is whole once its " .." has come: steps found after the last
            # one start now)
            starts = list(_STEP.finditer(text, pos))
            if starts:
                step, step_t, step_cpu, pos = starts[-1].group(1), now, cpu, starts[-1].end()
            if _DONE in text:
                step = None                     # (the remesher is done; the child saves the result)
            if done:
                th.join(5)
                break
            if step is not None:
                in_step = now - step_t
                limit = max(STEP_FLOOR_S, STEP_K * expected_step_s(step, n_in, n_out))
                if cpu is None or step_cpu is None:
                    stuck = in_step > 2 * limit         # (no CPU reading: wall time only, doubled)
                else:
                    stuck = in_step > limit and cpu - step_cpu > BUSY_SHARE * in_step
                if stuck:
                    why = Stalled(f"stuck in “{step}” for {in_step:.1f} s", step, now - t0)
                    break
            if now - t0 > max(BACKUP_S, BACKUP_K * TOTAL_US * n_in * 1e-6):
                why = Stalled(f"still running after {now - t0:.0f} s", step, now - t0)
                break
            if mem is not None and mem > MEM_MB:
                why = Stalled(f"took {mem:.0f} MB of memory", step, now - t0)
                why.memory = True
                break
        if why is not None:
            raise why
        if p.returncode != 0 or not os.path.exists(dst):
            tail = "".join(chunks)[-300:].strip().replace("\n", " ")
            raise RuntimeError(f"the remesher's process ended with code {p.returncode} ({tail})")
        with np.load(dst) as d:
            return np.array(d["V"]), np.array(d["F"])
    finally:
        if p.poll() is None:
            try:
                p.kill()
                p.wait(10)
            except Exception:
                pass
        try:
            p.stdout.close()
        except Exception:
            pass
        for f in (src, dst):
            try:
                os.remove(f)
            except OSError:
                pass
        try:
            os.rmdir(tmp)
        except OSError:
            pass


def remesh(V, F, tv, crease_deg=0.0, report=NULL, budget=None, command=None):
    """Quad remesh with the stall guard: (quad vertices, quads, target used). Tries the
    targets of nudged_targets(tv) in order until one finishes; Stuck when none does, the
    child runs out of memory (a nudge can't help that) or the budget is spent."""
    if not GUARD:
        import pynanoinstantmeshes as nim

        qv, qf = nim.remesh(np.ascontiguousarray(V, dtype=np.float32), np.ascontiguousarray(F, dtype=np.uint32),
                            int(tv), rosy=4, posy=4, creaseAngle=float(crease_deg), deterministic=True)
        return qv, qf, tv
    budget = budget if budget is not None else Budget()
    targets = nudged_targets(int(tv))
    stuck = 0
    for i, t in enumerate(targets):
        if budget.left <= 0:
            break
        try:
            qv, qf = run_child(V, F, t, crease_deg, command)
            return qv, qf, t
        except Stalled as s:
            budget.left -= 1
            budget.used += 1
            stuck += 1
            if getattr(s, "memory", False):
                report.log(f"The quad remesher {s.why}, so it was stopped.")
                raise Stuck(f"it {s.why}")
            nxt = targets[i + 1] if i + 1 < len(targets) else None
            if nxt is not None and budget.left > 0:
                where = "in its snapping step" if (s.step or "").startswith("Step 5") else f"({s.why})"
                report.log(f"The quad remesher got stuck {where} (a known fault of Instant Meshes on some "
                           f"meshes), so it was stopped and run again with {nxt} cage vertices.")
                report.note(f"Quad remeshing again with {nxt} cage vertices (the last try got stuck)")
    raise Stuck(f"it got stuck {stuck} time{'s' if stuck != 1 else ''}" if stuck else "it got stuck too often")
