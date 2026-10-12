"""Left column: brand, drop area, the queue of files."""
from PySide6.QtCore import QLocale, QRectF, Qt, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QMenu, QScrollArea, QVBoxLayout, QWidget

from . import icons, system, theme
from .model import JobStatus
from .theme import BOLD, SEMIBOLD
from .widgets import (Card, Clickable, ElidedLabel, GhostButton, IconButton, IconLabel, Label,
                      SectionLabel, ThinProgress, brand_mark, hbox, vbox)


def format_int(n):
    return QLocale().toString(int(n))


def format_bytes(n):
    """Like the Mac's file-size formatter (1 KB = 1000 bytes)."""
    n = int(n or 0)
    if n < 1000:
        return f"{n} bytes"
    loc = QLocale()
    for unit, div, digits in (("KB", 1e3, 0), ("MB", 1e6, 1), ("GB", 1e9, 2)):
        if n < div * 1000 or unit == "GB":
            return f"{loc.toString(n / div, 'f', digits)} {unit}"
    return f"{n} bytes"


class BrandHeader(QWidget):
    update_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        title = Label("Manifold Mesh Mix", 15, BOLD)
        sub = Label("STL → STEP converter", 11, color=theme.TEXT_DIM)
        self.update_button = IconButton("update", "Check for updates", size=26)
        self.update_button.clicked.connect(self.update_clicked)
        self.badge = False
        lay = hbox(brand_mark(), vbox(title, sub, spacing=1), None, self.update_button, spacing=10)
        lay.setContentsMargins(0, 0, 0, 2)
        self.setLayout(lay)

    def set_update_available(self, version):
        self.badge = bool(version)
        self.update_button.setToolTip(f"Version {version} is available — click to install" if version else "Check for updates")
        self.update_button.color = theme.accent() if version else None
        self.update_button.update()
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.badge:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            g = self.update_button.geometry()
            p.setPen(QPen(theme.PANEL, 2))
            p.setBrush(theme.accent())
            p.drawEllipse(QRectF(g.right() - 6, g.top() - 1, 8, 8))


class UpdateBanner(Clickable):
    """"Version X is available" note under the brand (shown after the quiet
    check at launch finds a newer version)."""

    def __init__(self, parent=None):
        super().__init__(parent, padding=10, vertical=False, spacing=10)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.icon = IconLabel("update", theme.accent(), 18, weight=1.8)
        self.title = Label("", 12, SEMIBOLD)
        self.sub = Label("Click to install. The app restarts by itself.", 10.5, color=theme.TEXT_DIM, wrap=True)
        self.lay.addWidget(self.icon)
        self.lay.addLayout(vbox(self.title, self.sub, spacing=2), 1)
        self.hide()

    def set_version(self, version):
        self.setVisible(bool(version))
        if version:
            self.title.setText(f"Version {version} is available")
            self.icon.set(color=theme.accent())
            self.set_highlighted(True, theme.accent())


class DropZone(Card):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, padding=0, radius=12)
        self.targeted = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(100)
        self.setAccessibleName("Add files")
        self.setToolTip("Click to choose files or folders")

    def set_targeted(self, on):
        self.targeted = on
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = theme.accent()
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        p.setBrush(theme.with_alpha(a, 0.08) if self.targeted else theme.CARD)
        pen = QPen(a if self.targeted else theme.STROKE_STRONG, 1.3)
        pen.setDashPattern([6 / 1.3, 5 / 1.3])
        p.setPen(pen)
        p.drawRoundedRect(r, 12, 12)
        ic = icons.pixmap("arrow-down-circle" if self.targeted else "import", a if self.targeted else theme.TEXT_DIM, 24,
                          max(1.0, self.devicePixelRatioF()), 1.6)
        p.drawPixmap(int((self.width() - 24) / 2), 18, ic)
        p.setFont(theme.font(13, SEMIBOLD))
        p.setPen(theme.TEXT)
        p.drawText(QRectF(0, 48, self.width(), 18), Qt.AlignmentFlag.AlignCenter,
                   "Release to add" if self.targeted else "Drop files or folders here")
        p.setFont(theme.font(11))
        p.setPen(theme.TEXT_FAINT)
        p.drawText(QRectF(0, 68, self.width(), 16), Qt.AlignmentFlag.AlignCenter, "or click to browse · STL, 3MF, OBJ, PLY")


def status_color(job):
    s = job.status
    if s == JobStatus.DONE:
        r = job.result
        if r is not None and r.has_errors:
            return theme.BAD
        if r is not None and r.has_issues:
            return theme.WARN
        return theme.GOOD
    if s == JobStatus.FAILED:
        return theme.BAD
    if s in (JobStatus.CONVERTING, JobStatus.INSPECTING):
        return theme.accent()
    if s == JobStatus.CANCELLED:
        return theme.TEXT_FAINT
    return theme.TEXT_DIM


def status_icon(job):
    s = job.status
    if s == JobStatus.DONE:
        return "exclamation-circle" if (job.result and job.result.has_issues) else "check-circle"
    return {JobStatus.FAILED: "x-octagon", JobStatus.CONVERTING: "gears", JobStatus.INSPECTING: "doc-search",
            JobStatus.QUEUED: "clock", JobStatus.CANCELLED: "stop-circle", JobStatus.READY: "cube"}[s]


def job_subtitle(job):
    if job.status == JobStatus.DONE and job.result is not None:
        r = job.result
        parts = f" · {len(job.part_list)} files" if job.is_split else ""
        state = "Ready to export · " if job.needs_export else ("Exported · " if job.is_exported else "")
        return f"{state}{r.preset_title}{parts} · {format_int(r.faces)} faces · {format_bytes(r.file_size)}"
    if job.triangles is not None:
        s = f"{format_int(job.triangles)} triangles"
        if job.can_split:
            n = len(job.part_list)
            s += f" · {n} parts → {f'{n} STEP files' if job.split else '1 STEP file'}"
        elif job.parts and job.parts > 1:
            s += f" · {job.parts} parts"
        elif job.bodies and job.bodies > 1:
            s += f" · {job.bodies} bodies"
        if job.open_shells:
            s += " · has holes"
        return s
    if job.status == JobStatus.FAILED and not job.readable:
        return "Can't be read"
    return job.status.title


class JobRow(Clickable):
    def __init__(self, model, job, parent=None):
        super().__init__(parent, padding=10, spacing=7)
        self.model = model
        self.job = job
        self.selected = False
        self.icon = IconLabel("cube", theme.TEXT_DIM, 14)
        self.name = ElidedLabel(job.file_name, 12.5, SEMIBOLD, mode=Qt.TextElideMode.ElideMiddle)
        self.sub = ElidedLabel("", 10, mono=True, color=theme.TEXT_FAINT)
        self.buttons = QWidget()
        self.buttons.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.button_lay = hbox(spacing=4)
        self.buttons.setLayout(self.button_lay)
        top = hbox(self.icon, vbox(self.name, self.sub, spacing=2), self.buttons, spacing=8)
        top.setStretch(1, 1)
        self.lay.addLayout(top)
        self.progress = ThinProgress()
        self.stage = ElidedLabel("", 10.5, color=theme.TEXT_DIM)
        self.error = Label("", 10.5, color=theme.with_alpha(theme.BAD, 0.9), wrap=True)
        self.error.setMaximumHeight(QFontMetrics(self.error.font()).lineSpacing() * 2)
        for w in (self.progress, self.stage, self.error):
            self.lay.addWidget(w)
        self._button_key = None
        self.clicked.connect(lambda: model.select(job.id))
        self.right_clicked.connect(self.show_menu)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.PreventContextMenu)
        job.changed.connect(self.refresh)
        self.refresh()

    def set_selected(self, on):
        self.selected = on
        self.refresh()

    def on_hover(self, on):
        self.refresh()

    def refresh(self):
        j = self.job
        self.icon.set(status_icon(j), status_color(j))
        self.name.setText(j.file_name)
        sub = job_subtitle(j)
        self.sub.setText(sub)
        self.sub.set_color(theme.with_alpha(theme.accent(), 0.85) if j.needs_export else theme.TEXT_FAINT)
        self.sub.extra_tooltip = None
        conv = j.status == JobStatus.CONVERTING
        self.progress.setVisible(conv or j.status == JobStatus.INSPECTING)
        self.progress.set_value(j.progress if conv else 0, indeterminate=j.status == JobStatus.INSPECTING)
        self.stage.setVisible(conv)
        self.stage.setText(j.stage)
        failed = j.status == JobStatus.FAILED and bool(j.error)
        self.error.setVisible(failed)
        if failed:
            self.error.setText(j.error)
            self.error.setToolTip(j.error)
        self._update_buttons()
        self.set_highlighted(self.selected, theme.accent())

    def _update_buttons(self):
        j = self.job
        show = self.hover or self.selected
        key = (show, j.status, j.needs_export, j.is_exported, j.readable)
        if key == self._button_key:
            return
        self._button_key = key
        while self.button_lay.count():
            w = self.button_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not show:
            return
        m = self.model
        specs = []
        if j.status == JobStatus.DONE:
            if j.needs_export:
                specs.append(("upload", "Export the STEP file", lambda: m.request_export([j])))
            elif j.is_exported:
                specs.append(("folder", f"Show the exported STEP file in {system.FILE_MANAGER}", lambda: m.reveal(j)))
        elif j.status in (JobStatus.CONVERTING, JobStatus.QUEUED):
            specs.append(("x", "Cancel", lambda: m.cancel(j)))
        elif j.status in (JobStatus.READY, JobStatus.FAILED, JobStatus.CANCELLED):
            if j.readable:
                ready = j.status == JobStatus.READY
                specs.append(("play" if ready else "refresh", "Convert this file" if ready else "Try again",
                              lambda: m.enqueue(j)))
            specs.append(("trash", "Remove from the list", lambda: m.remove(j)))
        for icon, tip, fn in specs:
            b = IconButton(icon, tip)
            b.clicked.connect(fn)
            self.button_lay.addWidget(b)

    def show_menu(self, pos):
        menu = job_menu(self.model, self.job, self)
        if not menu.isEmpty():
            menu.exec(pos)


def job_menu(model, job, parent):
    m = QMenu(parent)
    if job.status == JobStatus.DONE:
        if job.result_files:
            m.addAction("Export Again…" if job.is_exported else "Export…", lambda: model.request_export([job]))
        if job.is_exported:
            m.addAction(system.REVEAL_LABEL, lambda: model.reveal(job))
            m.addAction("Open STEP File", lambda: model.open_output(job))
        m.addAction("Convert Again", lambda: model.reconvert(job))
    if job.can_convert:
        m.addAction("Convert" if job.status == JobStatus.READY else "Try Again", lambda: model.enqueue(job))
    if job.can_split and model.split_available:
        a = m.addAction("Split to Parts")
        a.setCheckable(True)
        a.setChecked(job.split)
        a.setEnabled(not (job.status.is_busy or job.status == JobStatus.QUEUED))
        a.toggled.connect(lambda on: model.set_split(job, on))
    if job.status in (JobStatus.CONVERTING, JobStatus.QUEUED):
        m.addAction("Cancel", lambda: model.cancel(job))
    m.addSeparator()
    m.addAction("Remove from List", lambda: model.remove(job))
    return m


class Sidebar(QWidget):
    add_clicked = Signal()

    def __init__(self, model, parent=None):
        super().__init__(parent)
        self.model = model
        self.rows = {}
        self.setAutoFillBackground(True)
        self.setStyleSheet(f"Sidebar {{ background: {theme.css(theme.PANEL)}; }}")
        self.header = BrandHeader()
        self.update_banner = UpdateBanner()
        self.drop = DropZone()
        self.drop.clicked.connect(self.add_clicked)
        self.count = Label("", 10, mono=True, color=theme.TEXT_FAINT)
        self.empty = QWidget()
        self.empty.setLayout(vbox(None, _centered(IconLabel("tray", theme.TEXT_FAINT, 22, weight=1.4)),
                                  _centered(Label("No files yet", 12, color=theme.TEXT_FAINT)), None, spacing=8))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list = QWidget()
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setContentsMargins(0, 0, 0, 8)
        self.list_lay.setSpacing(8)
        self.list_lay.addStretch(1)
        self.scroll.setWidget(self.list)
        self.add = GhostButton("Add", "plus")
        self.add.setToolTip(f"Add files or folders ({system.MOD}+O)")
        self.add.clicked.connect(self.add_clicked)
        self.clear = GhostButton("Clear finished")
        self.clear.clicked.connect(model.clear_finished)
        self.stop = GhostButton("Stop all", tint=theme.BAD)
        self.stop.clicked.connect(model.cancel_all)
        lay = vbox(self.header, self.update_banner, self.drop, hbox(SectionLabel("Queue"), None, self.count), self.empty,
                   self.scroll, hbox(self.add, None, self.clear, self.stop), spacing=14, margins=(16, 16, 16, 16))
        lay.setStretch(4, 1)
        lay.setStretch(5, 1)
        self.setLayout(lay)
        model.jobs_changed.connect(self.rebuild)
        model.state_changed.connect(self.refresh_footer)
        model.selection_changed.connect(self.refresh_selection)
        self.rebuild()

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), theme.PANEL)

    def rebuild(self):
        ids = {j.id for j in self.model.jobs}
        for jid in list(self.rows):
            if jid not in ids:
                row = self.rows.pop(jid)
                row.setParent(None)
                row.deleteLater()
        for i, j in enumerate(self.model.jobs):
            row = self.rows.get(j.id)
            if row is None:
                row = self.rows[j.id] = JobRow(self.model, j)
            self.list_lay.insertWidget(i, row)
        n = len(self.model.jobs)
        self.count.setText(f"{n} file{'' if n == 1 else 's'}" if n else "")
        self.empty.setVisible(n == 0)
        self.scroll.setVisible(n > 0)
        self.refresh_selection()
        self.refresh_footer()

    def refresh_selection(self):
        for jid, row in self.rows.items():
            row.set_selected(jid == self.model.selection)
        row = self.rows.get(self.model.selection)
        if row is not None:
            self.scroll.ensureWidgetVisible(row, 0, 8)

    def refresh_footer(self):
        jobs = self.model.jobs
        self.clear.setVisible(any(j.status == JobStatus.DONE for j in jobs))
        self.stop.setVisible(any(j.status in (JobStatus.CONVERTING, JobStatus.QUEUED) for j in jobs))

    def refresh_theme(self):
        for row in self.rows.values():
            row.refresh()
        self.update()


def _centered(w):
    return hbox(None, w, None)


