"""Whole-file rivals for the merged engine.

While the merged pipeline converts a file (our features seeding MMM's segmentation), the
two engines it came from convert the same file on their own, each in a process of its
own: stl2curves' sewn build (merged._OurRun, started once our first stages are saved)
and MMM's engine alone (MMMAlone, started at once). When the merged result is saved,
the rivals are waited for a while (GRACE_*) and every finished result is judged the same
way, each in a process of its own as soon as it is there, by the benchmark's judge
(judge.py: read back as a CAD program reads it, volume, distances both ways, mesh corners
included). The file saved is the best: good first, then the fewest faces, then the
closest by a clear margin. MMM's own fit check, which samples the surface evenly, passed
Rak N-USB's merged result with 1.4% of the mesh corners 0.1-0.2 mm off along its edges
(the benchmark: not close; stl2curves alone was), so it decides only inside the pipeline.

Each parent wins parts the other loses (benchmark, 2026-10-25: stl2curves on threads and
small curved features, MMM on enclosures and organic shapes), and the merged pipeline
wins others outright; judged side by side the merged engine should never be worse than
either. The rivals also use the cores MMM's single-threaded stages leave idle."""
import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from .judge import SCORE_MM, verdict

JUDGE_FLOOR = 0.02         # mm: MMM's fit check inside the pipeline never tighter than this
                           # (its own is 5e-4 x the body's diagonal: 0.009 mm on the 20 mm
                           # tensioner, tighter than a printer or stl2curves' own fits, which
                           # made it 1,706 flat faces)
GRACE_GOOD = (30.0, 0.5)   # s + share of the merged run's time: rivals waited for when the
                           # merged result passes
GRACE_BAD = (60.0, 2.0)    # ... and when it doesn't
JUDGE_SECONDS = (120.0, 1.0)   # s + share of the run's time a judge may take
FEWER_FACES = 0.05         # a result within this share of the fewest faces may win on closeness
BLAS_THREADS = "2"         # MMM alone: numpy's threads (its own work is one thread)
DEADLINE = (120.0, 3.0)    # s, factor: once a rival passes at time t (from the start), the merged
                           # pipeline may run until max(t + 120 s, 3 t), then it is stopped (so
                           # a slow front end or a stuck stage can't hold the file for an hour
                           # once a good result is in hand); STL2CURVES_DEADLINE=a,k to test
if os.environ.get("STL2CURVES_DEADLINE"):
    DEADLINE = tuple(float(x) for x in os.environ["STL2CURVES_DEADLINE"].split(","))
# the folder holding both packages this engine imports from, stl2curves and mmm (engine/ in
# the repository): the child processes below start there, so `python -m mmm.cli` and
# `python -m stl2curves.judge` find them without PYTHONPATH (which -E drops)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def command(args):
    """The command line for `python -m MODULE ...` (args: "-m", MODULE, ...): this
    engine's own Python from source (no user site, no PYTHON* settings: -E -s; not -I,
    which would drop the working folder, where the packages are, from the path); in the
    built app, the engine program itself, which runs the module (engine/mmm_engine.py's
    --run-module), since a frozen program has no Python to start with -m."""
    if args[0] != "-m":
        raise ValueError("expected -m MODULE ...")
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-module", *args[1:]]
    return [sys.executable, "-E", "-s", *args]


def popen(args, **kw):
    """subprocess.Popen of command(args), in a process group of its own off Windows, so
    kill() ends its children too (our own run has a pool of worker processes)."""
    if os.name != "nt":
        kw.setdefault("start_new_session", True)
    return subprocess.Popen(command(args), cwd=ROOT, **kw)


def kill(proc):
    """End a process started by popen and everything it started."""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    else:
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
    proc.wait()


def _python(args, out_log, env=None):
    with open(out_log, "wb") as log:
        return popen(args, env=env or os.environ.copy(), stdout=log, stderr=subprocess.STDOUT)


class MMMAlone:
    """MMM's engine on its own (its CLI, Auto preset, the merged engine's quality)."""
    name = "mmm"

    def __init__(self, path, quality, layout="parts"):
        self.dir = tempfile.mkdtemp(prefix="stl2curves_mmm_")
        self.out = os.path.join(self.dir, "mmm.step")
        env = dict(os.environ, OMP_NUM_THREADS=BLAS_THREADS, OPENBLAS_NUM_THREADS=BLAS_THREADS,
                   MKL_NUM_THREADS=BLAS_THREADS)
        # (--engine mmm: MMM's own pipeline, not the merged one the CLI's Auto now runs)
        args = ["-m", "mmm.cli", "convert", str(path), "-o", self.out, "--preset", "auto",
                "--quality", str(quality), "--json", "--engine", "mmm"]
        if str(path).lower().endswith(".3mf"):
            args += ["--layout", layout]
        self.proc = _python(args, os.path.join(self.dir, "mmm.jsonl"), env)

    def stop(self):
        kill(self.proc)
        shutil.rmtree(self.dir, ignore_errors=True)


def _finished(rival):
    return rival.proc.poll() is not None and rival.proc.returncode == 0 and os.path.exists(rival.out)


def _unshaped(card):
    """Faces of a result that aren't designed shapes: spline faces and triangles left as
    faces (judge.py's "types" and "triangles")."""
    types = card.get("types") or {}
    return sum(types.get(k, 0) for k in ("bspline", "bezier", "offset", "other")) + (card.get("triangles") or 0)


def choose(cards):
    """The best of the scorecards (name -> card; the merged result first in order, when
    it was judged): good (sound and close, judge.verdict) first, then the fewest faces,
    then the closest."""
    from mmm.pipeline import CLEARLY_CLOSER

    # (good first; when none is, sound before close: a sound result reads back valid and
    # closed with the volume within 1%, so nothing big is missing, while one that is only
    # close may be an invalid copy of the mesh's facets. On the solar arm mount, whose
    # repair took off needle spikes, close-first kept MMM's invalid 2,550 flat faces over
    # a valid 264-face solid)
    v = {n: verdict(c) for n, c in cards.items()}
    tier = {n: (x[0], x[2], x[1]) for n, x in v.items()}
    top = max(tier.values())
    if not any(top):
        return next(iter(cards))   # (none sound or close: the merged result stays)
    best = [n for n in cards if tier[n] == top]
    least = min(cards[n]["faces"] for n in best)
    near = [n for n in best if cards[n]["faces"] <= least * (1 + FEWER_FACES)]
    # (within the band, the most "designed" result: fewest spline faces and triangles
    # left as faces, then fewest faces; MMM's author's suggestion)
    fewest = min(near, key=lambda n: (_unshaped(cards[n]), cards[n]["faces"]))
    closest = max(near, key=lambda n: v[n][3])
    # (closeness decides only by a clear margin, MMM's own: measurement noise mustn't)
    return closest if v[closest][3] > v[fewest][3] + CLEARLY_CLOSER else fewest


class Overtime(BaseException):
    """Raised in the thread running the merged pipeline when it overruns its deadline
    (Judges._deadline): past MMM's and our own "except Exception"."""


class Judges:
    """Every finished result judged as soon as it is there, each in a process of its own:
    the rivals usually finish while the merged pipeline is still running (on the
    tensioner, judging them after it took 29 s of a 62 s run). rivals is watched as a
    list, so a rival added later (our own run, started by the front end) is seen too."""

    def __init__(self, path, rivals):
        self.path, self.rivals = path, rivals
        # (the merged pipeline's deadline: see DEADLINE and _deadline; run_lock makes sure
        # Overtime is only sent while it runs)
        self.t0, self.caller = time.time(), threading.get_ident()
        self.running, self.passed_at, self.overtime = False, None, False
        self.run_lock = threading.Lock()
        self.work = tempfile.mkdtemp(prefix="stl2curves_judge_")
        self.procs = {}   # name -> (process, card file, STEP judged)
        self.cards = {}   # name -> its scorecard, once read
        self.lock = threading.Lock()
        self.done = threading.Event()
        self.thread = threading.Thread(target=self._watch, daemon=True)
        self.thread.start()

    def _watch(self):
        while not self.done.wait(0.5):
            for r in list(self.rivals):
                if r.name not in self.procs and _finished(r):
                    self.start(r.name, r.out)
            self._deadline()

    def _deadline(self):
        """Stop the merged pipeline (Overtime in its thread, at its next Python step) once a
        rival has passed and the pipeline has run past DEADLINE."""
        if self.passed_at is None and self.passing():
            self.passed_at = time.time() - self.t0
        if self.passed_at is None or self.overtime:
            return
        a, k = DEADLINE
        if time.time() - self.t0 > max(self.passed_at + a, k * self.passed_at):
            with self.run_lock:
                if self.running:
                    ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self.caller), ctypes.py_object(Overtime))
                    self.overtime = True

    def start(self, name, step):
        with self.lock:
            if name not in self.procs:
                card = os.path.join(self.work, name + ".json")
                self.procs[name] = (_python(["-m", "stl2curves.judge", str(self.path), step, card],
                                            os.path.join(self.work, name + ".log")), card, step)

    def card(self, name):
        """name's scorecard once its judge has finished (None before, or if it failed)."""
        if name in self.cards:
            return self.cards[name]
        proc, path, _ = self.procs[name]
        if proc.poll() is None:
            return None
        try:
            with open(path) as f:
                self.cards[name] = json.load(f)
        except (OSError, ValueError):
            self.cards[name] = {"error": f"no scorecard (exit code {proc.returncode})"}
        return self.cards[name]

    def passing(self):
        """A rival's result (not the merged one) has been judged good: the merged
        pipeline's per-body fallbacks are then not worth their time."""
        for name in list(self.procs):
            if name != "merged":
                c = self.card(name)
                if c and "error" not in c and verdict(c)[0]:
                    return True
        return False

    def stop(self, name):
        kill(self.procs[name][0])

    def close(self):
        self.done.set()
        self.thread.join()
        for proc, _, _ in self.procs.values():
            kill(proc)
        shutil.rmtree(self.work, ignore_errors=True)


def settle(out, res, judges, elapsed, report):
    """Wait for the rivals a while, judge every finished result side by side and save the
    best as out; res (MMM's result for the merged file, None when the merged pipeline was
    stopped: Overtime) updated to match."""
    rivals = judges.rivals
    # (the merged result is judged while the rivals are waited for; they are waited for
    # less once a good result is in hand: the merged one, by its verdict once there and
    # MMM's own check's until then, or a rival's. On the companion enclosure, MMM alone
    # had passed and the merged result hadn't: waiting the longer while cost 187 s)
    if res is not None:
        merged = os.path.join(judges.work, "merged.step")
        shutil.copyfile(out, merged)
        judges.start("merged", merged)
    t0 = time.time()
    while any(r.proc.poll() is None for r in rivals):
        c = judges.card("merged") if res is not None else None
        if c is not None and "error" not in c:
            good = verdict(c)[0]
        else:
            good = res is not None and bool(res.get("valid")) and bool((res.get("fidelity") or {}).get("ok"))
        a, k = GRACE_GOOD if good or judges.passing() else GRACE_BAD
        if time.time() - t0 > a + k * elapsed:
            break
        time.sleep(0.5)
    done = [r for r in rivals if _finished(r)]
    report.log("Rivals: " + ", ".join(f"{r.name} {'finished' if r in done else 'not finished, stopped'}"
                                      for r in rivals))
    if not done:
        if res is None:
            raise RuntimeError("the merged pipeline was stopped and no rival finished")
        return res
    report.note("Comparing the result with stl2curves' and MMM's own")
    for r in done:
        judges.start(r.name, r.out)
    names = ([] if res is None else ["merged"]) + [r.name for r in done]
    a, k = JUDGE_SECONDS
    deadline = time.time() + a + k * elapsed
    cards, hopeless = {}, False
    while time.time() < deadline and len(cards) < len(names):
        for n in names:
            if n not in cards:
                c = judges.card(n)
                if c is not None:
                    cards[n] = c
        # (a rival that passes with clearly fewer faces: the merged result can't win, so
        # its judge isn't waited for)
        beaten = res is not None and [
            c for n, c in cards.items() if n != "merged" and "error" not in c and verdict(c)[0]
            and c["faces"] * (1 + FEWER_FACES) < (res.get("faces") or 0)]
        if beaten and "merged" not in cards:
            judges.stop("merged")
            hopeless = True
            cards["merged"] = {"error": "not needed: a rival passes with fewer faces"}
        time.sleep(0.2)
    for n, c in list(cards.items()):
        if "error" in c:
            if not (n == "merged" and hopeless):
                report.log(f"The {n} result couldn't be judged ({c['error']})")
            del cards[n]
    if res is not None and "merged" not in cards and not hopeless:
        return res
    cards = {n: cards[n] for n in names if n in cards}
    if not cards:
        raise RuntimeError("the merged pipeline was stopped and no rival's result could be judged")
    win = choose(cards)
    if res is None:
        res = {"output": str(out), "tolerance": None, "fidelity": None, "stopped": True}
    v = {n: verdict(c) for n, c in cards.items()}
    res["rivals"] = {n: {"faces": c.get("faces"), "good": v[n][0], "close": v[n][1], "sound": v[n][2],
                         "p99": -v[n][3] * SCORE_MM, "triangles": c.get("triangles")} for n, c in cards.items()}
    report.log("Side by side: " + "; ".join(
        f"{n} {c.get('faces', 0)} faces{'' if v[n][2] else ', not sound'}{'' if v[n][1] else ', off the mesh'}"
        for n, c in cards.items()) + (f"; merged {res.get('faces')} faces, not judged" if hopeless else "")
        + ("; the merged pipeline was stopped at its deadline" if res.get("stopped") else "")
        + f": {win} kept")
    res["kept"] = win
    if win != "merged":
        c = cards[win]
        shutil.copyfile(judges.procs[win][2], out)
        # (preset: what the apps show Auto chose, so the kept result's own: our sewn faces
        # are CAD faces; MMM alone says which it used in its log. The merged pipeline's
        # second version, if it offered one, is its own, not the kept file's)
        res.update(faces=c["faces"], solids=c["solids"], valid=c["valid"], fidelity=None,
                   preset=_preset_of(win, judges), file_size=os.path.getsize(out))
        res.pop("alternative", None)
    return res


def _preset_of(name, judges):
    """The preset the rival called name used ("cad" unless its log says otherwise)."""
    import json
    for r in judges.rivals:
        log = os.path.join(r.dir, "mmm.jsonl") if r.name == name else None
        if log and os.path.exists(log):
            preset = "cad"
            with open(log, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if '"result"' in line:
                        try:
                            preset = json.loads(line).get("preset") or preset
                        except ValueError:
                            pass
            return preset
    return "cad"


