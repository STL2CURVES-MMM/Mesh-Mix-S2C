"""Scripted UI walkthrough (same steps as the Mac app's SelfTest).

    MMM_SELFTEST=/path/to/output/dir
    MMM_SELFTEST_FILES=a.stl<sep>b.3mf      files to add and convert (sep: ";" on Windows, ":" elsewhere)
    MMM_SELFTEST_BIG=/big.stl               optional file for the cancel test
    MMM_SELFTEST_CONVERT_ONLY=1             only add, convert and export
    MMM_UPDATE_URL=http://.../latest        optional: also check for updates against this address

The app drives itself through the main flows, saves a screenshot of its
window after every step, writes report.json and quits. Python errors
raised anywhere in the app are recorded in the report.
"""
import json
import os
import sys
import tempfile
import time
import traceback

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication

from .model import JobStatus
from .settings import PRESETS


class SelfTest(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.w = window
        self.m = window.model
        self.s = window.settings
        self.dir = os.environ["MMM_SELFTEST"]
        os.makedirs(self.dir, exist_ok=True)
        self.log = []
        self.errors = []
        self.step = 0
        self.size = (int(os.environ.get("MMM_SELFTEST_WIDTH") or 1360), 860)
        self._hook = sys.excepthook
        sys.excepthook = self._excepthook

    def _excepthook(self, kind, value, tb):
        self.errors.append("".join(traceback.format_exception(kind, value, tb)))
        self._hook(kind, value, tb)

    # ------------------------------------------------------------------ driver

    def start(self):
        self.gen = self.walkthrough()
        QTimer.singleShot(0, lambda: self._advance(None))

    def _advance(self, value):
        try:
            cmd = self.gen.send(value)
        except StopIteration:
            self._finish()
            return
        except Exception:  # noqa: BLE001
            self.errors.append(traceback.format_exc())
            self._finish()
            return
        if isinstance(cmd, (int, float)):
            QTimer.singleShot(int(cmd * 1000), lambda: self._advance(None))
        else:
            _, cond, timeout = cmd
            start = time.time()

            def poll():
                try:
                    ok = cond()
                except Exception:  # noqa: BLE001
                    self.errors.append(traceback.format_exc())
                    ok = True
                if ok:
                    self._advance(True)
                elif time.time() - start > timeout:
                    self._advance(False)
                else:
                    QTimer.singleShot(250, poll)

            poll()

    def _write_report(self):
        self.note("errors", {"count": len(self.errors), "errors": self.errors})
        with open(os.path.join(self.dir, "report.json"), "w", encoding="utf-8") as fh:
            json.dump(self.log, fh, indent=1)

    def _finish(self):
        self._write_report()
        sys.excepthook = self._hook
        self.w.close()
        QApplication.instance().quit()

    @staticmethod
    def wait(cond, timeout):
        return ("wait", cond, timeout)

    def note(self, event, extra=None):
        e = dict(extra or {})
        e["event"] = event
        e["t"] = time.time()
        self.log.append(e)

    # ------------------------------------------------------------------ screenshots

    def shot(self, name):
        self.step += 1
        w = self.w
        if (w.width(), w.height()) != self.size:
            w.resize(*self.size)
        QApplication.processEvents()
        base = os.path.join(self.dir, f"{self.step:02d}-{name.replace('/', '_')}")
        w.grab().save(base + ".png")
        d = w.export_dialog
        if d is not None and d.isVisible():
            d.grab().save(base + "-sheet.png")
        for top in QApplication.topLevelWidgets():
            if top is not w and top is not d and top.isVisible() and top.windowTitle():
                top.grab().save(base + "-dialog.png")
        vp = w.preview.viewport
        if vp.isVisible() and vp.gl_ok:
            vp.grabFramebuffer().save(base + "-3d0.png")

    def summary(self):
        out = []
        for j in self.m.jobs:
            d = {"file": j.file_name, "status": j.status.title, "stage": j.stage, "readable": j.readable}
            if j.error:
                d["error"] = j.error
            if j.result is not None:
                r = j.result
                d.update(faces=r.faces, valid=r.valid, preset=r.preset, seconds=r.seconds, size=r.file_size,
                         checks=[c.message for c in r.checks], checked=r.checked)
            if j.triangles is not None:
                d["triangles"] = j.triangles
            if j.exported_files:
                d["exported"] = list(j.exported_files)
            if j.can_split:
                d["split"] = j.split
                d["parts"] = []
                for p in j.part_list:
                    e = {"name": p.name, "triangles": p.triangles}
                    if p.exported_path:
                        e["exported"] = p.exported_path
                    if p.result is not None:
                        e.update(faces=p.result.faces, valid=p.result.valid, preset=p.result.preset, seconds=p.result.seconds)
                    d["parts"].append(e)
            if j.warnings:
                d["warnings"] = list(j.warnings)
            out.append(d)
        return out

    # ------------------------------------------------------------------ walkthrough

    def walkthrough(self):
        w, m, s = self.w, self.m, self.s
        env = os.environ
        w.resize(*self.size)
        yield 1.0
        s.preset = "auto"
        s.quality = 0.7
        s.outputMode = "subfolder"
        s.subfolderName = env.get("MMM_SELFTEST_SUBFOLDER") or "SelfTest STEP"
        s.replaceExisting = True
        if env.get("MMM_SELFTEST_REPAIR"):
            s.repair = env["MMM_SELFTEST_REPAIR"]
        convert_only = bool(env.get("MMM_SELFTEST_CONVERT_ONLY"))
        s.parallelJobs = 2
        s.convertOnDrop = False
        yield 0.5
        self.shot("empty")

        files = [f for f in (env.get("MMM_SELFTEST_FILES") or "").split(os.pathsep) if f]
        m.add(files)
        self.note("added", {"count": len(m.jobs)})
        yield 0.3
        self.shot("inspecting")
        ok = yield self.wait(lambda: not any(j.status == JobStatus.INSPECTING for j in m.jobs), 300)
        self.note("inspected", {"ok": ok, "jobs": self.summary()})
        if m.jobs:
            m.select(m.jobs[0].id)
        yield 1.5
        self.shot("ready")

        if not convert_only:
            for p in PRESETS:
                s.preset = p.value
                yield 0.4
                self.shot(f"preset-{p.value}")
            s.preset = "auto"
            s.outputMode = "custom"
            s.outputFolder = ""
            yield 0.4
            self.shot("custom-folder-missing")
            s.outputMode = "subfolder"
            s.quality = 0.15
            yield 0.3
            self.shot("quality-fast")
            s.quality = 0.7

        m.convert_all()
        self.note("convert-all")
        yield 2.0
        self.shot("converting")
        ok = yield self.wait(lambda: m.is_idle, 3600)
        self.note("converted", {"ok": ok, "jobs": self.summary()})
        for i, job in enumerate(list(m.jobs)):
            m.select(job.id)
            yield 2.0
            self.shot(f"result-{i}-{job.name}")
            if job.is_split:
                for k in range(1, len(job.part_list)):
                    job.selected_part = k
                    job.touch()
                    yield 1.5
                    self.shot(f"result-{i}-{job.name}-part{k}")
                job.selected_part = 0
                job.touch()

        # Export window, then the export itself
        if m.jobs:
            m.select(m.jobs[0].id)
        m.request_export(m.unexported_jobs)
        yield 1.2
        self.shot("export-sheet")
        if w.export_dialog is not None:
            w.export_dialog.start_export()
        ok = yield self.wait(lambda: w.export_dialog is None, 120)
        yield 1.0
        self.note("exported", {"ok": ok, "jobs": self.summary()})
        self.shot("exported")

        if convert_only:
            yield from self.update_check()
            self.note("finished", {"jobs": self.summary()})
            return

        # Export again without "replace": the name clash question, then rename
        j = next((x for x in m.jobs if x.is_exported), None)
        if j is not None:
            before = list(j.exported_files)
            s.replaceExisting = False
            m.request_export([j])
            yield 1.0
            self.shot("export-again-sheet")
            if w.export_dialog is not None:
                w.export_dialog.start_export()
            yield 0.8
            clash = w.export_dialog is not None and w.export_dialog.clash.isVisible()
            self.shot("export-name-clash")
            if w.export_dialog is not None:
                w.export_dialog.rename_clash()
            ok = yield self.wait(lambda: w.export_dialog is None, 60)
            yield 0.5
            self.note("export-renamed", {"ok": ok, "clash_shown": clash, "before": before, "files": list(j.exported_files)})
            s.replaceExisting = True

        # Broken file
        broken = os.path.join(tempfile.gettempdir(), "broken-selftest.stl")
        with open(broken, "w", encoding="utf-8") as fh:
            fh.write("this is not an stl file at all")
        m.add([broken])
        yield self.wait(lambda: not any(x.status == JobStatus.INSPECTING for x in m.jobs), 60)
        if m.jobs:
            m.select(m.jobs[-1].id)
        yield 1.0
        self.shot("broken-file")
        self.note("broken", {"jobs": self.summary()})

        # Cancel test
        big = env.get("MMM_SELFTEST_BIG")
        if big:
            m.add([big])
            yield self.wait(lambda: not any(x.status == JobStatus.INSPECTING for x in m.jobs), 300)
            job = m.jobs[-1] if m.jobs else None
            if job is not None:
                m.select(job.id)
                m.enqueue(job)
                yield 4.0
                self.shot("big-converting")
                m.cancel(job)
                yield self.wait(lambda: job.status != JobStatus.CONVERTING, 30)
                yield 0.8
                self.shot("big-cancelled")
                self.note("cancel", {"status": job.status.title})

        yield from self.update_check()
        m.clear_finished()
        yield 0.6
        self.shot("after-clear")
        self.note("finished", {"jobs": self.summary()})

    def update_check(self):
        """With MMM_UPDATE_URL set: check against it and show the window."""
        if not os.environ.get("MMM_UPDATE_URL"):
            return
        from .update_ui import UpdateDialog

        c = self.w.updates
        c.check()
        yield self.wait(lambda: c.state != "checking", 60)
        dlg = UpdateDialog(c, self.w)
        dlg.show()
        yield 0.8
        self.shot("update-dialog")
        self.note("update-check", {"state": c.state, "error": c.error,
                                   "version": c.info.version if c.info else None,
                                   "asset": c.info.asset_name if c.info else None,
                                   "can_install": list(c.can_install())})
        dlg.close()
        yield 0.3
        if os.environ.get("MMM_SELFTEST_INSTALL_UPDATE") and c.state == "available":
            # Download, check and install for real: the app quits and the new
            # version starts by itself, so the report is written now.
            self.note("update-install-started", {"version": c.info.version})
            self._write_report()
            c.download()
            yield self.wait(lambda: False, 600)
            self.note("update-install-timeout", {"state": c.state, "error": c.error})
