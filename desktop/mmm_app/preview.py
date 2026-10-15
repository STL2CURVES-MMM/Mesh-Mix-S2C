"""Middle column: the 3D preview of the selected file (Mesh / STEP), its
part tabs, the check result and the export buttons."""
import html
import os
import threading
from collections import OrderedDict

from PySide6.QtCore import QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QScrollArea, QSizePolicy, QVBoxLayout, QWidget

from . import icons, system, theme
from .model import JobStatus, result_unit
from .sidebar import format_bytes, format_int
from .theme import BOLD, MEDIUM, SEMIBOLD
from .viewport import PreviewMesh, Viewport
from .widgets import (AccentButton, Card, ElidedLabel, FeatureChip, FileTab, GhostButton, IconLabel, Label, Pill,
                      Segmented, StatChip, ThinProgress, clear_layout, hbox, vbox)

MESH_TINT = (0.62, 0.66, 0.72)
STEP_TINT = (0.78, 0.78, 0.78)

REPAIRED_TIP = ("Reading the STEP file back found small errors, so the app repaired them automatically, the way "
                "CAD programs like Fusion do when they import a file. The repaired file was checked again: it is a "
                "valid solid with the mesh's volume.")


MESH_PILL = {
    "ok": (theme.GOOD, "check"),
    "fixed": (theme.GOOD, "check"),
    "action": (theme.accent, "info"),
    "problems": (theme.WARN, "exclamation"),
}

ISSUE_ICONS = {
    "fixed": ("check-circle", theme.GOOD),
    "not_fixed": ("warning", theme.WARN),
    "action": ("info", theme.accent),
    "note": ("info", theme.TEXT_DIM),
}


def mesh_summary(mc):
    """Tooltip of the mesh check pill."""
    if not mc.issues:
        return "The mesh was checked for holes, flipped or tangled triangles, internal walls, self-crossings and " \
               "overlapping pieces, and nothing was wrong."
    lines = [f"• {i.problem[:1].upper()}{i.problem[1:]}: {i.result}" for i in mc.issues]
    if mc.ready is True:
        lines.append("Checked again after fixing: ready to convert.")
    elif mc.ready is False:
        lines.append("Checked again after fixing: " + "; ".join(mc.problems) + ".")
    return "\n".join(lines)


def repair_note(r):
    """First line of the check tooltip when a repair was tried but problems remain."""
    return "The app repaired what it could automatically; this is left:\n" if r.repaired else ""


class MeshCache(QObject):
    """Loads preview files in the background and keeps the last few."""

    loaded = Signal(str, object)
    _relay = Signal(str, object)

    def __init__(self, limit_bytes=600_000_000):
        super().__init__()
        self.limit = limit_bytes
        self.items = OrderedDict()
        self.loading = set()
        self._relay.connect(self._done, Qt.ConnectionType.QueuedConnection)

    def get(self, path):
        m = self.items.get(path)
        if m is not None:
            self.items.move_to_end(path)
        return m

    def request(self, path):
        if path in self.items:
            self.loaded.emit(path, self.items[path])
            return
        if path in self.loading:
            return
        self.loading.add(path)
        threading.Thread(target=lambda: self._relay.emit(path, PreviewMesh.load(path)), daemon=True).start()

    def _done(self, path, mesh):
        self.loading.discard(path)
        if mesh is not None:
            self.items[path] = mesh
            while len(self.items) > 1 and sum(m.nbytes for m in self.items.values()) > self.limit:
                self.items.popitem(last=False)
        self.loaded.emit(path, mesh)


class EmptyPreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.ring = _Ring()
        title = Label("Convert 3D-print meshes into editable STEP files", 20, BOLD)
        sub = Label("Drop STL or 3MF files (or a whole folder) anywhere in this window.\n"
                    "Choose a preset on the right, then press Convert.", 13, color=theme.TEXT_DIM)
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        chips = hbox(None, FeatureChip("cube-transparent", "Editable CAD faces"), FeatureChip("drop", "Smooth surfaces"),
                     FeatureChip("seal-check", "Every file checked"), None, spacing=10)
        lay = vbox(None, hbox(None, self.ring, None), title, sub, 6, chips, None, spacing=14, margins=(24, 24, 24, 24))
        self.setLayout(lay)

    def paintEvent(self, _):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        g.setColorAt(0, theme.VIEWPORT_TOP)
        g.setColorAt(1, theme.VIEWPORT_BOTTOM)
        p.fillRect(self.rect(), g)
        p.setPen(QPen(theme.white(0.035), 1))
        step = 32
        for x in range(0, self.width(), step):
            p.drawLine(x, 0, x, self.height())
        for y in range(0, self.height(), step):
            p.drawLine(0, y, self.width(), y)


class _Ring(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(110, 110)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = theme.accent()
        p.setBrush(theme.with_alpha(a, 0.10))
        p.setPen(QPen(theme.with_alpha(a, 0.35), 1))
        p.drawEllipse(QRectF(0.5, 0.5, 109, 109))
        p.drawPixmap(33, 33, icons.pixmap("cube-transparent", a, 44, max(1.0, self.devicePixelRatioF()), 1.0))


class _Bar(QWidget):
    """Overlay strip with a fade into the 3D view."""

    def __init__(self, top, parent=None):
        super().__init__(parent)
        self.top = top

    def paintEvent(self, _):
        p = QPainter(self)
        g = QLinearGradient(0, 0, 0, self.height())
        if self.top:
            g.setColorAt(0, theme.with_alpha(theme.BG, 0.9))
            g.setColorAt(1, theme.with_alpha(theme.BG, 0.0))
        else:
            g.setColorAt(0, theme.with_alpha(theme.BG, 0.0))
            g.setColorAt(1, theme.with_alpha(theme.BG, 0.85))
        p.fillRect(self.rect(), g)


class Placeholder(QWidget):
    """Centered message over the 3D view (reading, converting, failed, ...)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.icon = IconLabel("cube-transparent", theme.TEXT_FAINT, 30, weight=1.2)
        self.spinner = ThinProgress()
        self.spinner.setFixedWidth(160)
        self.title = Label("", 13, SEMIBOLD)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text = Label("", 12, color=theme.TEXT_DIM, wrap=True)
        self.text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.text.setMaximumWidth(380)
        self.setLayout(vbox(None, hbox(None, self.icon, None), hbox(None, self.spinner, None), hbox(None, self.title, None),
                            hbox(None, self.text, None), None, spacing=10))

    def show_state(self, icon=None, color=None, title="", text="", busy=False):
        self.icon.setVisible(icon is not None and not busy)
        if icon:
            self.icon.set(icon, color or theme.TEXT_FAINT)
        self.spinner.setVisible(busy)
        self.spinner.set_value(0, indeterminate=busy)
        self.title.setVisible(bool(title))
        self.title.setText(title)
        self.text.setVisible(bool(text))
        self.text.setText(text)
        self.show()


class JobPreview(QWidget):
    def __init__(self, model, settings, cache, parent=None):
        super().__init__(parent)
        self.model = model
        self.settings = settings
        self.cache = cache
        self.job = None
        self.mode = "mesh"
        self.show_edges = True
        self.show_log = False
        self._tabs_key = None
        self._chips_key = None
        self._mesh_paths = (None, None)
        self.meshes = {}  # path -> PreviewMesh for the current job

        self.viewport = Viewport(self)
        self.placeholder = Placeholder(self)
        self.top = _Bar(True, self)
        self.bottom = _Bar(False, self)

        # --- top bar: tabs + view controls, then path / badges / split
        self.tab_area = QScrollArea()
        self.tab_area.setWidgetResizable(True)
        self.tab_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tab_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tab_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.tab_area.setStyleSheet("background: transparent;")
        self.tab_holder = QWidget()
        self.tab_holder.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.tab_lay = hbox(spacing=4)
        self.tab_holder.setLayout(self.tab_lay)
        self.tab_area.setWidget(self.tab_holder)
        self.tab_area.setFixedHeight(32)
        self.segment = Segmented(["Mesh", "STEP"])
        self.segment.changed.connect(lambda i: self.set_mode("result" if i else "mesh"))
        self.palette_btn = GhostButton(icon="palette", checkable=True)
        self.palette_btn.setToolTip("Give every face its own color, to see how the surfaces were split up")
        self.palette_btn.toggled.connect(self._toggle_colors)
        self.edges_btn = GhostButton(icon="square-dashed", checkable=True)
        self.edges_btn.setToolTip("Show edges")
        self.edges_btn.setChecked(True)
        self.edges_btn.toggled.connect(self._toggle_edges)
        self.reset_btn = GhostButton(icon="scope")
        self.reset_btn.setToolTip("Reset view (or double-click the view)")
        self.reset_btn.clicked.connect(self.viewport.reset_view)
        self.path = ElidedLabel("", 10, mono=True, color=theme.TEXT_FAINT, mode=Qt.TextElideMode.ElideLeft)
        self.check_pill = Pill()
        self.mesh_pill = Pill()
        self.export_pill = Pill()
        self.split_btn = GhostButton("Split to parts", "split", checkable=True)
        self.split_btn.setFont(theme.font(11.5, MEDIUM))
        self.split_btn.clicked.connect(self._toggle_split)
        row1 = hbox(self.tab_area, self.segment, self.palette_btn, self.edges_btn, self.reset_btn, spacing=6)
        row1.setStretch(0, 1)
        row2 = hbox(self.path, self.mesh_pill, self.check_pill, self.export_pill, self.split_btn, spacing=8)
        row2.setStretch(0, 1)
        self.top.setLayout(vbox(row1, row2, spacing=6, margins=(12, 12, 12, 12)))

        # --- bottom bar: mesh check / error, issues, log, stats and buttons
        self.mesh_card = Card(padding=10, spacing=6)
        self.mesh_rows = vbox(spacing=5)
        self.mesh_card.lay.addLayout(self.mesh_rows)
        self.merge_btn = GhostButton("Merge Overlapping Pieces", "stack", checkable=True)
        self.merge_btn.setFont(theme.font(11.5, MEDIUM))
        self.merge_btn.clicked.connect(self._toggle_merge)
        self.touch_btn = GhostButton("Merge Touching Parts", "split", checkable=True)
        self.touch_btn.setFont(theme.font(11.5, MEDIUM))
        self.touch_btn.clicked.connect(self._toggle_touching)
        self.fixed_btn = GhostButton("Save Fixed Mesh…", "import")
        self.fixed_btn.setFont(theme.font(11.5, MEDIUM))
        self.fixed_btn.setToolTip("Save the checked and fixed mesh as an STL or 3MF file, for example for your slicer")
        self.fixed_btn.clicked.connect(self._save_fixed)
        self.mesh_buttons = hbox(None, self.touch_btn, self.merge_btn, self.fixed_btn, spacing=8)
        self.mesh_card.lay.addLayout(self.mesh_buttons)
        self._saving_fixed = None  # job whose fixed mesh is being saved
        self.error_card = Card(padding=10, spacing=6)
        self.error_title = Label("", 12, SEMIBOLD, wrap=True)
        self.error_reason = Label("", 11.5, color=theme.TEXT_DIM, wrap=True)
        self.report_btn2 = GhostButton("Save Report…", "doc")
        self.report_btn2.setFont(theme.font(11.5, MEDIUM))
        self.report_btn2.setToolTip("Save a text file about this problem that you can send to the developer")
        self.report_btn2.clicked.connect(self._save_report)
        err_head = hbox(IconLabel("x-octagon", theme.BAD, 13), self.error_title, spacing=7)
        err_head.setStretch(1, 1)
        self.error_card.lay.addLayout(err_head)
        self.error_card.lay.addWidget(self.error_reason)
        self.error_card.lay.addLayout(hbox(None, self.report_btn2))
        self.issues = Card(padding=10, spacing=6)
        self.log_card = Card(padding=10, spacing=0)
        self.log_scroll = QScrollArea()
        self.log_scroll.setWidgetResizable(True)
        self.log_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.log_body = QWidget()
        self.log_lay = QVBoxLayout(self.log_body)
        self.log_lay.setContentsMargins(0, 0, 0, 0)
        self.log_lay.setSpacing(3)
        self.log_scroll.setWidget(self.log_body)
        self.log_card.lay.addWidget(self.log_scroll)
        self.log_card.setFixedHeight(150)
        self.chip_area = QScrollArea()
        self.chip_area.setWidgetResizable(True)
        self.chip_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chip_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.chip_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self.chip_area.setStyleSheet("background: transparent;")
        self.chip_holder = QWidget()
        self.chip_holder.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.chip_lay = hbox(spacing=8)
        self.chip_holder.setLayout(self.chip_lay)
        self.chip_area.setWidget(self.chip_holder)
        self.chip_area.setFixedHeight(40)
        self.log_btn = GhostButton(icon="text-left")
        self.log_btn.setToolTip("Show the conversion log")
        self.log_btn.clicked.connect(self._toggle_log)
        self.report_btn = GhostButton(icon="doc")
        self.report_btn.setToolTip("Save a report about this file that you can send to the developer")
        self.report_btn.clicked.connect(self._save_report)
        self.reveal_btn = GhostButton(system.REVEAL_LABEL, "folder")
        self.reveal_btn.clicked.connect(lambda: self.job and self.model.reveal_current(self.job))
        self.export_again_btn = GhostButton("Export Again…", "upload", tint="accent")
        self.export_again_btn.clicked.connect(lambda: self.job and self.model.request_export([self.job]))
        self.export_btn = AccentButton("Export…", "upload", expand=False)
        self.export_btn.clicked.connect(lambda: self.job and self.model.request_export([self.job]))
        row = hbox(self.chip_area, self.log_btn, self.report_btn, self.reveal_btn, self.export_again_btn,
                   self.export_btn, spacing=8)
        row.setStretch(0, 1)
        self.bottom.setLayout(vbox(self.mesh_card, self.error_card, self.issues, self.log_card, row, spacing=8,
                                   margins=(12, 12, 12, 12)))

        cache.loaded.connect(self._mesh_loaded)
        settings.changed.connect(self._setting_changed)

    # ------------------------------------------------------------------ layout

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._layout()

    def _layout(self):
        w, h = self.width(), self.height()
        self.viewport.setGeometry(0, 0, w, h)
        self.placeholder.setGeometry(0, 0, w, h)
        th = self.top.layout().sizeHint().height()
        self.top.setGeometry(0, 0, w, th)
        bh = self.bottom.layout().heightForWidth(w) if self.bottom.layout().hasHeightForWidth() else self.bottom.layout().sizeHint().height()
        self.bottom.setGeometry(0, h - bh, w, bh)
        self.top.raise_()
        self.bottom.raise_()

    # ------------------------------------------------------------------ job

    def set_job(self, job):
        if self.job is job:
            return
        if self.job is not None:
            try:
                self.job.changed.disconnect(self.refresh)
            except (RuntimeError, TypeError):
                pass
        self.job = job
        self.meshes = {}
        self._tabs_key = None
        self._chips_key = None
        self.show_log = False
        self.show_edges = True
        self.edges_btn.blockSignals(True)
        self.edges_btn.setChecked(True)
        self.edges_btn.blockSignals(False)
        self._last_status = job.status if job else None
        self._last_part_preview = None
        self.mode = "result" if job is not None and job.status == JobStatus.DONE else "mesh"
        if job is not None:
            job.changed.connect(self.refresh)
        self.viewport.set_mesh(None, None)
        self.refresh()

    def set_mode(self, mode):
        if mode != self.mode:
            self.mode = mode
            self.refresh()

    def _part(self):
        return self.job.current_part if self.job else None

    def in_path(self):
        p = self._part()
        return (p.input_preview if p else None) or (self.job.input_preview if self.job else None)

    def out_path(self):
        j = self.job
        if j is None:
            return None
        if j.is_split:
            p = self._part()
            return p.output_preview if p else None
        return j.output_preview

    def shown_result(self):
        j = self.job
        if j is None:
            return None
        if j.is_split:
            p = self._part()
            return p.result if p else None
        return j.result

    def refresh(self):
        j = self.job
        if j is None:
            return
        # switch to the STEP view when the file (or a part of it) is ready
        if j.status != self._last_status:
            if j.status == JobStatus.DONE:
                self.mode = "result"
            self._last_status = j.status
        pp = self._part().output_preview if self._part() else None
        if pp != self._last_part_preview:
            if pp and j.status == JobStatus.CONVERTING:
                self.mode = "result"
            self._last_part_preview = pp
        self.segment.set_index(1 if self.mode == "result" else 0)
        result_mode = self.mode == "result"
        self.palette_btn.setVisible(result_mode)
        self.edges_btn.setVisible(result_mode)
        self.palette_btn.blockSignals(True)
        self.palette_btn.setChecked(self.settings.colorFaces)
        self.palette_btn.blockSignals(False)
        self._refresh_tabs()
        self._refresh_badges()
        self._refresh_bottom()
        self._refresh_meshes()
        self._layout()
        # rows just added to a card are shown a moment later (Qt shows new
        # children of a visible widget from the event loop); size again then
        QTimer.singleShot(0, self._layout)

    def _setting_changed(self, key):
        if key in ("colorFaces", "accent", "unit", "plateLayout"):
            self._chips_key = None
            self._tabs_key = None
            self.refresh()

    # tabs
    def _refresh_tabs(self):
        j = self.job
        if j.is_split:
            key = ("split", j.selected_part, tuple((p.name, self._tab_icon(p), self._tab_color(p).name(), p.exported_path)
                                                   for p in j.part_list), theme.accent().name())
        else:
            key = ("one", j.file_name, j.can_split, len(j.part_list), theme.accent().name())
        if key == self._tabs_key:
            return
        self._tabs_key = key
        clear_layout(self.tab_lay)
        if j.is_split:
            for i, p in enumerate(j.part_list):
                t = FileTab(p.name, self._tab_icon(p), self._tab_color(p), i == j.selected_part, max_width=170)
                tip = f"{p.name} · {format_int(p.triangles)} triangles"
                if p.exported_path:
                    tip += f" → {os.path.basename(p.exported_path)}"
                t.setToolTip(tip)
                t.clicked.connect(lambda _=False, k=i: self._select_part(k))
                self.tab_lay.addWidget(t)
        else:
            n = len(j.part_list)
            t = FileTab(j.file_name, "stack" if j.can_split else "doc", theme.TEXT_DIM, True,
                        badge=f"{n} parts" if j.can_split else None)
            t.setToolTip(f"This 3MF has {n} parts, saved together in one STEP file." if j.can_split else j.file_name)
            self.tab_lay.addWidget(t)
        self.tab_lay.addStretch(1)
        if j.is_split and 0 <= j.selected_part < self.tab_lay.count() - 1:
            w = self.tab_lay.itemAt(j.selected_part).widget()
            if w is not None:
                self.tab_area.ensureWidgetVisible(w, 20, 0)

    def _select_part(self, k):
        if self.job is not None and k != self.job.selected_part:
            self.job.selected_part = k
            self.job.touch()

    def _tab_icon(self, p):
        if p.result is not None:
            return "exclamation-circle" if p.result.has_issues else "check-circle"
        if self.job.status in (JobStatus.CONVERTING, JobStatus.QUEUED):
            return "clock"
        return "cube"

    def _tab_color(self, p):
        r = p.result
        if r is not None:
            return theme.BAD if r.has_errors else (theme.WARN if r.has_issues else theme.GOOD)
        return theme.TEXT_DIM

    def _unit(self):
        return result_unit(self.job, self.settings)

    def _fit_line(self, r):
        if r.fidelity is None:
            return ""
        return ("\nCompared with the mesh: " + r.fidelity.summary(self._unit()) + "."
                + ("" if r.fidelity.ok else " It doesn't follow the mesh closely everywhere."))

    # badges
    def _refresh_badges(self):
        j = self.job
        self.path.setText(j.path)
        r = self.shown_result()
        pill = self.check_pill
        if r is None:
            pill.hide()
        elif r.has_errors:
            pill.set("Problem found", theme.BAD, "x")
            pill.setToolTip(repair_note(r) + "\n".join(c.message for c in r.checks) + self._fit_line(r))
            pill.show()
        elif r.checks:
            n = len(r.checks)
            pill.set("1 thing to check" if n == 1 else f"{n} things to check", theme.WARN, "exclamation")
            pill.setToolTip(repair_note(r) + "\n".join(c.message for c in r.checks) + self._fit_line(r))
            pill.show()
        elif r.off_mesh:
            pill.set("Doesn't match the mesh everywhere", theme.WARN, "exclamation")
            pill.setToolTip("The shape was compared with the mesh: " + r.fidelity.summary(self._unit())
                            + ".\nNo method followed it closely everywhere; this is the closest result. "
                              "A higher quality, or another preset, may do better.")
            pill.show()
        elif r.checked and r.repaired:
            pill.set("Repaired automatically", theme.GOOD, "check")
            pill.setToolTip(REPAIRED_TIP + self._fit_line(r))
            pill.show()
        elif r.checked:
            pill.set("File checked", theme.GOOD, "check")
            pill.setToolTip("The STEP file was read back and checked: it is a valid solid with the mesh's volume."
                            + self._fit_line(r))
            pill.show()
        elif r.valid is not None:
            pill.set("Valid solid" if r.valid else "Has small errors", theme.GOOD if r.valid else theme.WARN,
                     "check" if r.valid else "exclamation")
            pill.setToolTip("")
            pill.show()
        else:
            pill.hide()
        mc = j.mesh_check
        if mc is not None and self.mode == "mesh":
            color, icon = MESH_PILL.get(mc.status, (theme.TEXT_DIM, "info"))
            self.mesh_pill.set(mc.title, color() if callable(color) else color, icon)
            self.mesh_pill.setToolTip(mesh_summary(mc))
            self.mesh_pill.show()
        else:
            self.mesh_pill.hide()
        done = j.status == JobStatus.DONE and bool(j.result_files)
        if done and j.is_exported:
            self.export_pill.set("Exported", theme.TEXT_DIM, "upload")
            self.export_pill.setToolTip("Saved to:\n" + "\n".join(j.exported_files))
            self.export_pill.show()
        elif done:
            self.export_pill.set("Not exported yet", theme.accent(), "clock")
            self.export_pill.setToolTip(f"The STEP {'files are' if j.is_split else 'file is'} ready. "
                                        "Look it over, then press Export to save it.")
            self.export_pill.show()
        else:
            self.export_pill.hide()
        show_split = j.can_split and self.model.split_available
        self.split_btn.setVisible(show_split)
        if show_split:
            self.split_btn.setChecked(j.split)
            self.split_btn.setEnabled(not (j.status.is_busy or j.status == JobStatus.QUEUED))
            self.split_btn.setToolTip(
                "Each part of this 3MF becomes its own STEP file. Click to put all parts in one STEP file instead."
                if j.split else "Click to save each part of this 3MF as its own STEP file, like “Split to parts” in the slicer.")

    def _toggle_merge(self):
        if self.job is not None:
            self.model.set_merge_overlaps(self.job, self.merge_btn.isChecked())
            self.merge_btn.setChecked(self.job.merge_overlaps)

    def _toggle_touching(self):
        if self.job is not None:
            self.model.set_touching_choice(self.job, "merge" if self.touch_btn.isChecked() else "split")
            self.touch_btn.setChecked(self.job.touching_choice == "merge")

    def _save_fixed(self):
        j = self.job
        win = self.window()
        if j is None or self._saving_fixed is not None or not hasattr(win, "save_fixed_mesh"):
            return

        def done(err, path):
            self._saving_fixed = None
            if not err:
                j.saved_fixed_mesh = path
            self.refresh()
        self._saving_fixed = j
        if not win.save_fixed_mesh(j, done):
            self._saving_fixed = None
        self.refresh()

    def _save_report(self):
        win = self.window()
        if self.job is not None and hasattr(win, "save_report"):
            win.save_report(self.job)

    def _toggle_split(self):
        if self.job is not None:
            self.model.set_split(self.job, self.split_btn.isChecked())
            self.split_btn.setChecked(self.job.split)

    def _toggle_colors(self, on):
        self.settings.colorFaces = on

    def _toggle_edges(self, on):
        self.show_edges = on
        self._refresh_meshes()

    def _toggle_log(self):
        self.show_log = not self.show_log
        self.refresh()

    # bottom
    def _refresh_mesh_card(self):
        j = self.job
        mc = j.mesh_check
        show = (self.mode == "mesh" and mc is not None and (mc.issues or mc.problems or j.merge_error)
                and j.status != JobStatus.INSPECTING)
        self.mesh_card.setVisible(bool(show))
        if not show:
            return
        clear_layout(self.mesh_rows)
        for i in mc.issues:
            icon, color = ISSUE_ICONS.get(i.status, ("info", theme.TEXT_DIM))
            ic = IconLabel(icon, color() if callable(color) else color, 12)
            where = f"{html.escape(i.part)}: " if i.part else ""
            problem = html.escape(i.problem[:1].upper() + i.problem[1:])
            t = Label(f"{where}{problem}. <span style='color:{theme.css(theme.TEXT_DIM)}'>{html.escape(i.result)}</span>",
                      11.5, wrap=True)
            t.setTextFormat(Qt.TextFormat.RichText)
            row = hbox(ic, t, spacing=7)
            row.setStretch(1, 1)
            row.setAlignment(ic, Qt.AlignmentFlag.AlignTop)
            self.mesh_rows.addLayout(row)
        if j.merge_error:
            self.mesh_rows.addWidget(Label(f"Merging the overlapping pieces didn't work: {j.merge_error}", 11.5,
                                           color=theme.WARN, wrap=True))
        if mc.ready is not None and (mc.fixed_anything or mc.problems):
            if mc.ready:
                text, color = "Checked again after fixing: ready to convert.", theme.GOOD
            else:
                text, color = "Checked again after fixing: " + "; ".join(mc.problems) + ".", theme.WARN
            self.mesh_rows.addWidget(Label(text, 11, SEMIBOLD, color=color, wrap=True))
        busy = j.status.is_busy or j.status == JobStatus.QUEUED
        self.merge_btn.setVisible(mc.has_overlaps or j.merge_overlaps)
        self.merge_btn.setChecked(j.merge_overlaps)
        self.merge_btn.setEnabled(not busy)
        self.merge_btn.setToolTip(
            "The overlapping pieces are joined into one solid. Click to keep them as separate solids again."
            if j.merge_overlaps else
            "Join the pieces that overlap each other into one solid (the file is read again). Pieces that "
            "overlap on purpose, like parts of an assembly, are better kept separate.")
        self.touch_btn.setVisible(bool(j.touching))
        self.touch_btn.setChecked(j.touching_choice == "merge")
        self.touch_btn.setEnabled(not busy)
        self.touch_btn.setToolTip(
            "The touching parts are merged into one solid. Click to keep them as separate parts again."
            if j.touching_choice == "merge" else
            "Merge the parts that touch each other into one solid (the file is read again)")
        saving = self._saving_fixed is j
        self.fixed_btn.setVisible(mc.fixed_anything or j.merge_overlaps or j.touching_choice == "merge")
        self.fixed_btn.setEnabled(not saving and j.readable and j.status != JobStatus.INSPECTING)
        self.fixed_btn.setText("Saving…" if saving else ("Saved ✓  Save Again…" if j.saved_fixed_mesh
                                                          else "Save Fixed Mesh…"))

    def _refresh_error_card(self):
        j = self.job
        show = j.status == JobStatus.FAILED and bool(j.error) and j.readable
        self.error_card.setVisible(show)
        if show:
            self.error_title.setText(j.error)
            self.error_reason.setText(j.error_reason or "")
            self.error_reason.setVisible(bool(j.error_reason))

    def _refresh_bottom(self):
        j = self.job
        r = self.shown_result()
        self._refresh_mesh_card()
        self._refresh_error_card()
        clear_layout(self.issues.lay)
        if r is not None and r.checks and self.mode == "result":
            for c in r.checks:
                ic = IconLabel("x-octagon" if c.is_error else "warning", theme.BAD if c.is_error else theme.WARN, 12)
                t = Label(c.message, 11.5, wrap=True)
                row = hbox(ic, t, spacing=7)
                row.setStretch(1, 1)
                row.setAlignment(ic, Qt.AlignmentFlag.AlignTop)
                self.issues.lay.addLayout(row)
            self.issues.show()
        else:
            self.issues.hide()
        has_log = bool(j.log or j.warnings)
        self.log_btn.setVisible(has_log)
        trouble = (j.status == JobStatus.FAILED or bool(j.warnings) or (r is not None and r.has_issues)
                   or (j.mesh_check is not None and j.mesh_check.ready is False))
        self.report_btn.setVisible(trouble and not self.error_card.isVisibleTo(self))
        self.log_btn.setText(str(len(j.warnings)) if j.warnings else "")
        self.log_btn.tint = theme.WARN if j.warnings else None
        if self.show_log and has_log:
            clear_layout(self.log_lay)
            for line in j.log:
                lab = Label(line, 10.5, color=theme.TEXT_DIM, mono=True, wrap=True)
                lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.log_lay.addWidget(lab)
            for w in j.warnings:
                lab = Label("⚠ " + w, 10.5, color=theme.WARN, mono=True, wrap=True)
                lab.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                self.log_lay.addWidget(lab)
            self.log_lay.addStretch(1)
            self.log_card.show()
        else:
            self.log_card.hide()
        done = j.status == JobStatus.DONE and bool(j.result_files)
        self.reveal_btn.setVisible(j.status == JobStatus.DONE and j.is_exported)
        self.reveal_btn.setToolTip(f"Show this part's exported STEP file in {system.FILE_MANAGER}" if j.is_split
                                   else f"Show the exported STEP file in {system.FILE_MANAGER}")
        self.export_again_btn.setVisible(done and j.is_exported)
        self.export_again_btn.setToolTip(f"Save the STEP {'files' if j.is_split else 'file'} somewhere else, or under another name")
        self.export_btn.setVisible(done and not j.is_exported)
        self.export_btn.setText(f"Export {len(j.result_files)} Files…" if j.is_split else "Export…")
        self.export_btn.setToolTip(f"Choose where to save the STEP {'files' if j.is_split else 'file'} ({system.MOD}+E)")
        self.export_btn.updateGeometry()
        self._refresh_chips()

    def _refresh_chips(self):
        j = self.job
        p = self._part()
        r = self.shown_result()
        chips = []
        tri = p.triangles if p else j.triangles
        if tri is not None:
            chips.append(("Triangles", format_int(tri), False))
        bb = (p.bbox_size if p else None) or j.bbox_size
        if bb and len(bb) == 3:
            chips.append(("Size", "×".join(f"{v:.0f}" if v >= 100 else f"{v:.1f}" for v in bb) + " " + self.settings.unit, False))
        if r is not None:
            chips += [("Preset", r.preset_title, True), ("Faces", format_int(r.faces), True),
                      ("Solids" if r.solids > 0 else "Shells", str(r.solids if r.solids > 0 else r.shells), False),
                      ("STEP", format_bytes(r.file_size), False), ("Time", f"{r.seconds:.1f}s", False)]
        key = (tuple(chips), theme.accent().name())
        if key == self._chips_key:
            return
        self._chips_key = key
        clear_layout(self.chip_lay)
        for label, value, acc in chips:
            self.chip_lay.addWidget(StatChip(label, value, acc))
        self.chip_lay.addStretch(1)

    # meshes
    def _refresh_meshes(self):
        a, b = self.in_path(), self.out_path()
        for path in (a, b):
            if path and path not in self.meshes:
                m = self.cache.get(path)
                if m is not None:
                    self.meshes[path] = m
                else:
                    self.cache.request(path)
        result_mode = self.mode == "result"
        path = b if result_mode else a
        mesh = self.meshes.get(path) if path else None
        self.viewport.set_style(result_mode and self.settings.colorFaces, result_mode and self.show_edges,
                                STEP_TINT if result_mode else MESH_TINT)
        self.viewport.set_mesh(mesh, f"{self.mode}-{path}" if mesh is not None else None)
        self._refresh_placeholder(mesh is not None)

    def _mesh_loaded(self, path, mesh):
        if mesh is not None and path in (self.in_path(), self.out_path()):
            self.meshes[path] = mesh
            self._refresh_meshes()

    def _refresh_placeholder(self, has_mesh):
        j = self.job
        ph = self.placeholder
        if has_mesh:
            ph.hide()
            return
        if self.mode == "result":
            if j.status in (JobStatus.CONVERTING, JobStatus.QUEUED):
                ph.show_state(busy=True, text=j.stage or "Waiting…")
            elif j.status == JobStatus.FAILED:
                # the details are in the card below
                ph.show_state("x-octagon-line", theme.BAD, text="Conversion failed" if j.readable else
                              (j.error or "Conversion failed"))
            elif j.status == JobStatus.DONE and j.is_split:
                ph.show_state("warning-line", theme.WARN, text="This part couldn't be converted")
            elif j.status == JobStatus.DONE and self.out_path():
                ph.show_state(busy=True, text="Loading the preview…")
            else:
                ph.show_state("cube-transparent", theme.TEXT_FAINT, text="Convert to see the STEP result")
        elif j.status == JobStatus.INSPECTING:
            ph.show_state(busy=True, title="Checking the mesh", text=(j.stage + "…") if j.stage else "Reading the file…")
        elif not j.readable:
            ph.show_state("warning-line", theme.BAD, title="This file can't be read", text=j.error or "")
        elif self.in_path():
            ph.show_state(busy=True, text="Loading the preview…")
        else:
            ph.hide()
        self.placeholder.lower()
        self.viewport.lower()


class PreviewPanel(QWidget):
    def __init__(self, model, settings, parent=None):
        super().__init__(parent)
        self.model = model
        self.cache = MeshCache()
        self.empty = EmptyPreview()
        self.preview = JobPreview(model, settings, self.cache)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.empty)
        lay.addWidget(self.preview)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        model.selection_changed.connect(self.refresh)
        model.jobs_changed.connect(self.refresh)
        self.refresh()

    def refresh(self):
        job = self.model.selected_job
        self.empty.setVisible(job is None)
        self.preview.setVisible(job is not None)
        self.preview.set_job(job)

    @property
    def viewport(self):
        return self.preview.viewport
