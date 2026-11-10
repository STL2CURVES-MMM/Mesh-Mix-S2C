"""OpenCascade's mesher, with a time limit it can't hold up.

BRepMesh_IncrementalMesh holds Python's lock while it works, so no timer in the same process
can stop it, and on some faces its time grows out of all proportion to the fineness asked for.
One thread flank of the drybox axle (889 x 2 poles, 1,257 outline edges) took 6 s at 0.1 mm,
over 150 s at 0.05 and at Precise's 0.0028 ran for 34 minutes up to 22.8 GB, with the other
1,223 faces of the same call done in 11 s: the user saw nothing, for as long as they waited.

Every call in the engine and the judge comes here (mesh below), and this is how it stays out of
such a call:

1. The same call is first tried in a watched child process: the shape is written out, exactly,
   in OpenCascade's binary format, and the child (kept up between calls, as starting one takes a
   second, and started at the start of a conversion) meshes it and answers. Only when it has
   finished does this process make the very same call on the very same shape. So a result is
   the same as without any of this: nothing of the child's is used but its word that the call
   ends. The cost is the time of the meshing once more, in another process, and the
   writing out (see "Native hangs" in CLAUDE_context.md for the measurements).
2. A child still at it after a time limit, and busy (a child starved of CPU on a loaded machine
   isn't called stuck, up to a limit of its own), or holding too much memory, is killed. Then the
   faces are tried one by one, each with a far shorter limit, which finds the ones to blame.
3. A face to blame is tried without the mesher's control of the surface's distance, which is
   where the time goes (the flank at 0.05 mm: over 90 s with it, whatever the edges' tolerance
   and however few inner points, 0.22 s without; at 0.0028 mm 6 s and 10,037 triangles, median
   0.0027 mm off the surface, where the 0.2 mm that finishes with it on gives 0.016), then
   coarser, again in the child, until it finishes or COARSER runs out. This process then meshes
   every face the way the child found it can take. What the call returns says which faces are
   rougher than asked, and which have no triangles at all, so whoever measures with them can say
   so instead of taking a rough face's triangles (or no triangles) for the surface.
4. What is learned about a face is kept for this conversion, so that the next call on the same
   face doesn't wait all over again.

The user is told at each stage (use() names the reporter). A worker process doesn't come here for
its child: its parent's time limit on the job already covers it, and it may be one of 16.
A child that dies (a crash inside OCC would take the conversion with it, here it doesn't) counts
as stuck.

OCC's wire check (BRepCheck_Wire.SelfIntersect) can crash the same way on odd geometry: on a
thread's helical borders read back from STEP it ended a conversion in an access violation
(f_long_d8_l12, 2026-11-08). So self_crossings() runs it in the same child, all of a call's faces
in one job, and answers which edges of each face's wires cross, by their place in the face; the
caller picks the same edges from its own copy (an exact copy, walked in the same order). A face
the check dies on fails only itself: it counts as crossing, and the child goes on from the next
face. (CLAUDE.md: one bad edge or face fails only itself. Design by Duvdivan's side, #9.)

Written 2026-11-02 for the merged engine (not part of Manifold Mesh Mix as published; see
NOTICE.md). Frozen builds start the child as the program itself with --mesh-child, as
remesh_guard does with --remesh-child: the program's entry point must call child_main().
"""
import atexit
import json
import multiprocessing
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time

from . import faults
from . import remesh_guard
from .report import NULL

GUARD = True            # False: call OCC directly, as before (for timing the guard against)
SECONDS = 120.0         # s a whole call gets at least, as the judge's limit has been
SECONDS_FACE = 0.1      # s per face it gets, if that's more
FACE_SECONDS = 20.0     # s one face alone gets (one of 1,224 took 0.9 s at most on the axle, 1M triangles in all)
WALL_K = 5.0            # a starved child gets this many times the limit before it is stopped anyway
BUSY_SHARE = 0.5        # CPU seconds per wall second a child must have used to be called stuck
MEMORY_MB = 8192        # MB the child may hold (a whole 1M-triangle tessellation takes well under 1 GB)
NOTE_AFTER = 15.0       # s of waiting before the user is told the meshing is taking long
COARSER = 4.0           # how much coarser a stuck face is tried at each time
COARSER_TRIES = 5       # how many times (0.0028 mm reaches 2.9 mm)
MOST_STUCK = 6          # faces stopped in one call before the search stops, and the call's other faces all go coarser:
                        # when many don't finish, it is the fineness that is wrong for this shape, and their
                        # time limits would add up (the axle's 119 faces at 0.002 mm, each taking 30 s alone)
CHILD_CPU_S = 1800      # s of CPU the child may use on one job (POSIX self-limit, against orphans)

_report = NULL
_learned = {}           # face's signature -> (asked this fine it doesn't finish, the way it does, or None)
_lock = threading.Lock()


class Result:
    """What a call did besides the plain mesh: faces (their index in the order TopExp_Explorer
    goes through the shape's faces) meshed coarser than asked, and left with no triangles."""
    __slots__ = ("coarse", "unmeshed")

    def __init__(self):
        self.coarse = {}        # index -> deflection used (the one asked for, with the surface's distance not controlled)
        self.unmeshed = []      # indices

    def __bool__(self):
        return bool(self.coarse or self.unmeshed)


class Stuck(Exception):
    """A caller that can't use a shape with faces left out: the mesher didn't finish on it."""


class ChildFault(AssertionError):
    """The child's Python failed (not OCC's mesher, in which case it just doesn't answer): a bug
    of ours, which says nothing about the shape. An AssertionError, so that faults counts it."""


def use(report):
    """The reporter for messages to the user, and a new conversion's fresh start."""
    global _report
    _report = report or NULL
    _learned.clear()


def start():
    """Start the child now, in the background of a conversion, where its start-up costs nothing."""
    if GUARD and not _in_worker():
        with _lock:
            _Child.get()


def _in_worker():
    return multiprocessing.parent_process() is not None


# ----------------------------------------------------------------------------
# The child
# ----------------------------------------------------------------------------


def child_main(argv=None):
    """In the child: one job per line on stdin, a line on stdout as each is done. It meshes as the
    parent will. A job's "from" set means face by face from that index, a line for each."""
    try:
        import resource
    except ImportError:
        resource = None         # (Windows: the job object ends it with its parent)
    from OCP.BinTools import BinTools
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shape

    # (OCC's own console chatter mustn't get into the answers)
    out = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    out.write("ready\n")
    for line in sys.stdin:
        try:
            if resource is not None:
                # (a child stuck in OCC can't notice its parent is gone, and stdin's end only
                # reaches it between jobs. The kernel's limit holds even then)
                used = resource.getrusage(resource.RUSAGE_SELF)
                limit = int(used.ru_utime + used.ru_stime) + CHILD_CPU_S
                # (the soft limit alone: this child lives through many jobs, and once lowered a hard
                # limit can't be raised again, which failed every later job on Linux)
                resource.setrlimit(resource.RLIMIT_CPU, (limit, resource.getrlimit(resource.RLIMIT_CPU)[1]))
            job = json.loads(line)
            shape = TopoDS_Shape()
            BinTools.Read_s(shape, job["path"])
            if job.get("kind") == "check":
                _check_faces(shape, job.get("from", 0), job.get("flag", False), out)
                out.write("done\n")
                continue
            args = (job["deflection"], job["relative"], job["angle"], job["parallel"], job.get("control", True))
            first = job.get("from")
            if first is None:
                _native(shape, *args)
            else:
                ex = TopExp_Explorer(shape, TopAbs_FACE)
                i = 0
                while ex.More():
                    if i >= first:
                        out.write(f"start {i}\n")
                        _native(ex.Current(), *args)
                    i += 1
                    ex.Next()
            out.write("done\n")
        except Exception as e:  # not engine work: told to the parent, which records it
            out.write(f"error {type(e).__name__}: {e}\n".replace("\n", " ").rstrip() + "\n")


def _check_faces(shape, first, flag, out):
    """In the child: for each face from ``first``, "start i", then "x i w a b" for each of its wires
    (w, by order) that crosses itself, a and b the crossing edges' places in that wire."""
    for i, face in enumerate(_faces(shape)):
        if i < first:
            continue
        out.write(f"start {i}\n")
        for w, (wire, edges) in enumerate(_wires(face)):
            hit = _crossing(face, wire, flag)
            if hit is not None:
                out.write(f"x {i} {w} {_place(edges, hit[0])} {_place(edges, hit[1])}\n")


def _wires(face):
    """The face's wires in order, each with its edges in order."""
    from OCP.TopAbs import TopAbs_EDGE, TopAbs_WIRE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    out, ex = [], TopExp_Explorer(face, TopAbs_WIRE)
    while ex.More():
        wire = TopoDS.Wire(ex.Current())
        ex.Next()
        edges, ee = [], TopExp_Explorer(wire, TopAbs_EDGE)
        while ee.More():
            edges.append(TopoDS.Edge(ee.Current()))
            ee.Next()
        out.append((wire, edges))
    return out


def _crossing(face, wire, flag):
    """OCC's own check: the two edges of the wire that cross, or None."""
    from OCP.BRepCheck import BRepCheck_Status, BRepCheck_Wire
    from OCP.TopoDS import TopoDS, TopoDS_Edge

    e1, e2 = TopoDS_Edge(), TopoDS_Edge()
    if BRepCheck_Wire(wire).SelfIntersect(TopoDS.Face(face), e1, e2, flag) == BRepCheck_Status.BRepCheck_NoError:
        return None
    return e1, e2


def _place(edges, e):
    return next((k for k, x in enumerate(edges) if not e.IsNull() and x.IsSame(e)), -1)


class Crossing:
    """One wire of a face that crosses itself: the wire, and its two crossing edges (either may be
    None when OCC named no edge of the wire). ``died`` when the check itself ended the child on
    this face, so that nothing more is known than that it fails."""
    __slots__ = ("wire", "e1", "e2", "died")

    def __init__(self, wire=None, e1=None, e2=None, died=False):
        self.wire, self.e1, self.e2, self.died = wire, e1, e2, died


def self_crossings(faces, flag=False):
    """For each face, a list of Crossing, one for each of its wires that crosses itself (empty if
    none), as BRepCheck_Wire(wire).SelfIntersect(face, e1, e2, flag) finds them; a face the check
    dies on gets [Crossing(died=True)]. Run in the watched child, so that a crash inside OCC
    fails only that face; in a worker, or with the guard off, here as before."""
    faces = list(faces)
    out = [[] for _ in faces]
    if not faces:
        return out
    if not GUARD or _in_worker():
        for i, face in enumerate(faces):
            for wire, edges in _wires(face):
                hit = _crossing(face, wire, flag)
                if hit is not None:
                    out[i].append(Crossing(wire, *hit))
        return out
    found = {}                          # face -> [(wire place, edge place, edge place)]
    path = _write(_compound(faces))
    try:
        first = 0
        while first < len(faces):
            at = [first]

            def started(k):
                at[0] = k

            def line(text):
                _, i, w, a, b = text.split()
                found.setdefault(int(i), []).append((int(w), int(a), int(b)))

            why = _try(path, None, None, None, None, FACE_SECONDS, first=first, on_start=started, tell=False,
                       kind="check", flag=bool(flag), on_line=line)
            if why is None:
                break
            if why == "nochild":
                # (unwatched, then, as it always used to be: the check itself says nothing about the shape)
                faults.event("the wire check couldn't be watched, so a crash in it could end the conversion")
                for i in range(at[0], len(faces)):
                    found.pop(i, None)
                    for w, (wire, edges) in enumerate(_wires(faces[i])):
                        hit = _crossing(faces[i], wire, flag)
                        if hit is not None:
                            found.setdefault(i, []).append((w, _place(edges, hit[0]), _place(edges, hit[1])))
                break
            k = at[0]
            found[k] = None             # (the check ended the child on this face: it fails, and only it)
            _report.note(f"OpenCascade's wire check failed on one face ({why}); that face is built again, the others go on")
            faults.event("OpenCascade's wire check crashed or hung on a face, which failed only itself")
            first = k + 1
    finally:
        _remove(path)
    for i, hits in found.items():
        if hits is None:
            out[i] = [Crossing(died=True)]
            continue
        wires = _wires(faces[i])
        for w, a, b in hits:
            wire, edges = wires[w]
            out[i].append(Crossing(wire, edges[a] if a >= 0 else None, edges[b] if b >= 0 else None))
    return out


_BOOT = ("import sys, json, site\n"
         "paths = json.loads(sys.argv[1])\n"
         "sys.path[:0] = paths\n"
         "[site.addsitedir(p) for p in paths if p.endswith('site-packages')]\n"
         "from {pkg}.meshguard import child_main\n"
         "child_main(sys.argv[2:])\n")


def _command():
    if getattr(sys, "frozen", False):
        return [sys.executable, "--mesh-child"]
    return [remesh_guard.python(), "-s", "-u", "-c", _BOOT.format(pkg=__package__), remesh_guard.child_paths()]


class _Child:
    """The child process, and the lines it has sent."""
    current = None

    @classmethod
    def get(cls):
        c = cls.current
        if c is None or c.p.poll() is not None:
            c = cls.current = cls()
        return c

    @classmethod
    def drop(cls):
        c, cls.current = cls.current, None
        if c is not None:
            c.kill()

    def __init__(self):
        # (no OpenBLAS threads: it loads no numpy, but the variable costs nothing should that change)
        env = dict(os.environ, OPENBLAS_NUM_THREADS="1")
        self.p = subprocess.Popen(_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, env=env, text=True, bufsize=1)
        remesh_guard._keep_with_us(self.p)
        self.lines = queue.Queue()
        self.ready = False
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        try:
            for line in self.p.stdout:
                self.lines.put(line.strip())
        except (OSError, ValueError):
            pass
        self.lines.put(None)

    def kill(self):
        try:
            self.p.kill()
            self.p.wait(10)
        except (OSError, subprocess.SubprocessError):
            pass

    def send(self, job):
        self.p.stdin.write(json.dumps(job) + "\n")
        self.p.stdin.flush()


atexit.register(_Child.drop)


def _write(shape):
    from OCP.BinTools import BinTools

    fd, path = tempfile.mkstemp(prefix="stl2curves_mesh_", suffix=".bin")
    os.close(fd)
    BinTools.Write_s(shape, path)
    return path


def _try(*args, **kwargs):
    """The child's run of one job (_watch). Whatever cuts it short (the merged pipeline's Overtime,
    raised in this thread) takes the child with it: its answer mustn't pass for the next job's."""
    try:
        return _watch(*args, **kwargs)
    except BaseException:  # not engine work: nothing is kept from it
        _Child.drop()
        raise


def _watch(path, deflection, relative, angle, parallel, limit, first=None, on_start=None, tell=True, control=True,
           kind=None, flag=False, on_line=None):
    """None once the job is done, else why not: "time", "memory", "died", or "nochild" when no
    child could be had at all, which says nothing about the shape. on_start(i) is called as face
    i starts, and the limit counts from then (from=first). kind "check" is the wire check
    (self_crossings), whose answers go to on_line."""
    try:
        child = _Child.get()
        if kind == "check":
            job = {"kind": "check", "path": path, "flag": flag}
        else:
            job = {"path": path, "deflection": deflection, "relative": relative, "angle": angle,
                   "parallel": parallel, "control": control}
        if first is not None:
            job["from"] = first
        child.send(job)
    except (OSError, ValueError):
        ready = _Child.current is not None and _Child.current.ready
        _Child.drop()
        return "died" if ready else "nochild"
    began = time.time()
    cpu0 = remesh_guard._usage(child.p)[0]
    told = False
    while True:
        try:
            line = child.lines.get(timeout=0.25)
        except queue.Empty:
            line = ""
        if line is None:
            ready = child.ready
            _Child.drop()
            return "died" if ready else "nochild"
        if line == "ready":
            child.ready = True
            began, cpu0 = time.time(), remesh_guard._usage(child.p)[0]     # (the limit is for the mesher)
            continue
        if line == "done":
            return None
        if line.startswith("error "):
            try:
                raise ChildFault(line[6:])
            except ChildFault as e:
                faults.caught(e)
            return "nochild"
        if line.startswith("x ") and on_line is not None:
            on_line(line)
            continue
        if line.startswith("start "):
            on_start(int(line[6:]))
            began, cpu0 = time.time(), remesh_guard._usage(child.p)[0]
            continue
        if line:
            continue
        now = time.time() - began
        cpu, mem = remesh_guard._usage(child.p)
        if mem is not None and mem > MEMORY_MB:
            _Child.drop()
            return "memory"
        busy = cpu is None or cpu0 is None or cpu - cpu0 > BUSY_SHARE * now
        if (now > limit and busy) or now > WALL_K * limit:
            _Child.drop()
            return "time"
        if tell and first is None and now > NOTE_AFTER and not told:
            told = True
            _report.note(f"Meshing the shape to measure it is taking long, so it is watched: it is stopped after "
                         f"{limit:.0f} s and looked at face by face")


def _faces(shape):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    out, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        out.append(ex.Current())
        ex.Next()
    return out


def _signature(face):
    """What tells one face from another between calls (a copy of a face is the same face here)."""
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    from OCP.TopAbs import TopAbs_EDGE
    from OCP.TopExp import TopExp_Explorer

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    n, ex = 0, TopExp_Explorer(face, TopAbs_EDGE)
    while ex.More():
        n += 1
        ex.Next()
    return n, round(float(props.Mass()), 3)


def _native(shape, deflection, relative, angle, parallel, control=True):
    from OCP.BRepMesh import BRepMesh_IncrementalMesh

    if control:
        BRepMesh_IncrementalMesh(shape, deflection, relative, angle, parallel)
        return
    from OCP.IMeshTools import IMeshTools_Parameters

    p = IMeshTools_Parameters()
    p.Deflection, p.Relative, p.Angle, p.InParallel = deflection, relative, angle, parallel
    p.ControlSurfaceDeflection = False
    BRepMesh_IncrementalMesh(shape, p)


def _compound(faces):
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    B = BRep_Builder()
    comp = TopoDS_Compound()
    B.MakeCompound(comp)
    for f in faces:
        B.Add(comp, f)
    return comp


def _say_stuck(sig, deflection, way):
    edges, area = sig
    what = f"One face ({edges:,} edges, area {area:,.1f})"
    if way is None:
        msg = (f"{what} couldn't be meshed at any fineness within the time limit. It is left out of the "
               "measurements of the result, and may show as a gap in a preview or in another program.")
    elif not way[1]:
        msg = (f"{what} couldn't be meshed fully at {deflection:.2g} within the time limit, so its triangles aren't "
               "held to its surface: how closely it follows the mesh is known less exactly there.")
    else:
        msg = (f"{what} couldn't be meshed at {deflection:.2g} within the time limit, so it was measured at "
               f"{way[0]:.2g}: how closely it follows the mesh is known only that finely.")
    _report.warn(msg)
    faults.event("a face could only be measured roughly, as the mesher could not finish it")


def _way_that_ends(path, deflection, relative, angle, parallel, limit):
    """How a stuck face does finish: (deflection, surface control on), or None."""
    if _try(path, deflection, relative, angle, parallel, limit, tell=False, control=False) is None:
        return deflection, False
    d = deflection
    for _ in range(COARSER_TRIES):
        d *= COARSER
        if _try(path, d, relative, angle, parallel, limit, tell=False) is None:
            return d, True
    return None


def _one_by_one(faces, deflection, relative, angle, parallel, signatures):
    """After a whole call was stopped: ({index: way} for the faces that don't finish at ``deflection``
    alone (the way they do finish, _way_that_ends, or None), the index the search stopped at,
    past which the faces weren't tried: see MOST_STUCK)."""
    stuck = {}
    reached = len(faces)
    path = _write(_compound(faces))
    try:
        i, at = 0, [0]

        def started(k):
            at[0] = k
            if k and k % 500 == 0:
                _report.note(f"Meshing face by face to find the one that doesn't finish: {k:,} of {len(faces):,}")

        while i < len(faces):
            at[0] = i
            why = _try(path, deflection, relative, angle, parallel, FACE_SECONDS, first=i, on_start=started)
            if why is None or why == "nochild":
                break
            k = at[0]
            one = _write(faces[k])
            try:
                _report.note(f"One face doesn't finish meshing ({why}); trying it another way")
                way = _way_that_ends(one, deflection, relative, angle, parallel, FACE_SECONDS)
            finally:
                _remove(one)
            stuck[k] = way
            _learned[signatures(k)] = (deflection, way) if way else (deflection * COARSER ** COARSER_TRIES, None)
            _say_stuck(signatures(k), deflection, way)
            i = k + 1
            if len(stuck) >= MOST_STUCK and i < len(faces):
                reached = i
                break
    finally:
        _remove(path)
    return stuck, reached


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def mesh(shape, deflection, relative=False, angle=0.5, parallel=True):
    """What BRepMesh_IncrementalMesh(shape, deflection, relative, angle, parallel) does, the
    triangles left on the shape's faces, in bounded time. A Result (false when every face was
    meshed as asked)."""
    res = Result()
    deflection = float(deflection)
    if not GUARD or _in_worker():
        _native(shape, deflection, relative, angle, parallel)
        return res
    faces = _faces(shape)
    if not faces:
        return res
    cache = {}

    def signature(k):
        if k not in cache:
            cache[k] = _signature(faces[k])
        return cache[k]

    # (faces known from an earlier call not to finish this fine go straight to what they do finish at)
    known = {}
    if _learned:
        for k in range(len(faces)):
            seen = _learned.get(signature(k))
            if seen is not None and deflection <= seen[0]:
                # (without the control, at what is asked now; coarser as it was found, or none)
                known[k] = (deflection, False) if seen[1] is not None and not seen[1][1] else seen[1]
    rest = [f for k, f in enumerate(faces) if k not in known]
    target = shape if not known else _compound(rest)
    stuck = {}
    stopped = False
    later = []                  # (faces the search didn't reach: see MOST_STUCK)
    if rest:
        path = _write(target)
        try:
            why = _try(path, deflection, relative, angle, parallel, max(SECONDS, SECONDS_FACE * len(rest))
                       if len(rest) > 1 else FACE_SECONDS)
        finally:
            _remove(path)
        if why == "nochild":
            # (unwatched, then, as it always used to be, rather than taking every face for stuck)
            faults.event("the meshing couldn't be watched, so a face it can't finish could hold the conversion up")
            why = None
        if why is not None:
            stopped = True
            _report.log(f"Meshing {len(rest):,} face{'s' if len(rest) != 1 else ''} at {deflection:.2g} was stopped "
                        f"({'too much memory' if why == 'memory' else 'it took too long' if why == 'time' else 'the mesher crashed'}), "
                        "so they are tried one by one.")
            order = [k for k in range(len(faces)) if k not in known]
            found, reached = _one_by_one(rest, deflection, relative, angle, parallel, lambda j: signature(order[j]))
            stuck = {order[j]: way for j, way in found.items()}
            later = order[reached:]
    together = {**known, **stuck}
    if stopped:
        # (one by one, what the child has shown to finish: the call as a whole didn't, even if no
        # face alone is to blame, as with edges shared at a fineness they can't take)
        for k, f in enumerate(faces):
            if k not in together and k not in later:
                _native(f, deflection, relative, angle, parallel)
        if later:
            _report.warn(f"Many faces of this shape can't be meshed at {deflection:.2g} within the time limit, so the "
                         f"other {len(later):,} were measured at {deflection * COARSER:.2g}.")
            again = mesh(_compound([faces[k] for k in later]), deflection * COARSER, relative, angle, parallel)
            for j, k in enumerate(later):
                if j in again.unmeshed:
                    res.unmeshed.append(k)
                else:
                    res.coarse[k] = again.coarse.get(j, deflection * COARSER)
    elif rest:
        _native(target, deflection, relative, angle, parallel)
    for k, way in together.items():
        if way is None:
            res.unmeshed.append(k)
        else:
            _native(faces[k], way[0], relative, angle, parallel, way[1])
            res.coarse[k] = way[0]
    return res
