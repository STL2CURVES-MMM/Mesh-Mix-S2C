"""Progress / log reporting.

The macOS app runs the engine as a subprocess and reads one JSON object per
line from stdout. When run by hand, messages are printed as plain text.
"""
import json
import sys
import time


class Reporter:
    def __init__(self, json_mode=False, quiet=False, stream=None):
        self.json_mode = json_mode
        self.stream = stream or sys.stdout
        self.quiet = quiet
        self.t0 = time.time()
        self._stage = ""
        self._lo = 0.0
        self._hi = 1.0
        self._last_emit = 0.0

    def _emit(self, obj):
        if self.json_mode:
            self.stream.write(json.dumps(obj) + "\n")
            self.stream.flush()
        elif not self.quiet:
            kind = obj.get("event")
            if kind == "stage":
                print(f"[{time.time() - self.t0:7.2f}s] {obj['stage']}", flush=True)
            elif kind == "log":
                print(f"           {obj['message']}", flush=True)
            elif kind == "warning":
                print(f"  WARNING  {obj['message']}", flush=True)

    def stage(self, name, lo, hi):
        """Start a stage that covers the [lo, hi] slice of overall progress."""
        self._stage, self._lo, self._hi = name, lo, hi
        self._emit({"event": "stage", "stage": name, "progress": lo})

    def progress(self, frac):
        """Fractional progress within the current stage (rate limited)."""
        now = time.time()
        if now - self._last_emit < 0.1 and frac < 1.0:
            return
        self._last_emit = now
        p = self._lo + (self._hi - self._lo) * max(0.0, min(1.0, frac))
        if self.json_mode:
            self._emit({"event": "progress", "stage": self._stage, "progress": p})

    def log(self, message):
        self._emit({"event": "log", "message": message})

    def warn(self, message):
        self._emit({"event": "warning", "message": message})

    def result(self, data):
        self._emit({"event": "result", **data})

    def part(self, data):
        """One part of a split conversion is finished (its STEP is written)."""
        self._emit({"event": "part", **data})
        if not self.json_mode and not self.quiet:
            print(f"           Part {data.get('name')}: {data.get('output')}", flush=True)

    def error(self, message, reason=None, details=None):
        """message: what went wrong; reason: why, in plain words; details:
        technical information for a bug report."""
        ev = {"event": "error", "message": message}
        if reason:
            ev["reason"] = reason
        if details:
            ev["details"] = details
        self._emit(ev)


NULL = Reporter(json_mode=False, quiet=True)
