"""The export window: where the STEP files go, their names and what happens
when a file with the same name is already there."""
import os

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QCheckBox, QDialog, QLineEdit, QScrollArea, QVBoxLayout, QWidget

from . import system, theme
from .model import ExportItem, safe_file_name
from .settings_panel import OutputModePicker, set_checked
from .theme import BOLD, SEMIBOLD
from .widgets import (AccentButton, Card, ElidedLabel, GhostButton, IconLabel, Label, Pill, SectionLabel, ThinProgress,
                      clear_layout, hbox, vbox)


def display_path(path):
    if not system.IS_WINDOWS:
        home = os.path.expanduser("~")
        if path == home or path.startswith(home + os.sep):
            return "~" + path[len(home):]
    return path


def _key(path):
    return os.path.normcase(os.path.abspath(path))


class _Header(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(36, 36)

    def paintEvent(self, _):
        from . import icons

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = theme.accent()
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.with_alpha(a, 0.16))
        p.drawRoundedRect(QRectF(self.rect()), theme.R_MED, theme.R_MED)
        p.drawPixmap(10, 10, icons.pixmap("upload", a, 16, max(1.0, self.devicePixelRatioF())))


class FileRow(Card):
    def __init__(self, dialog, item):
        super().__init__(padding=8, spacing=4)
        self.dialog = dialog
        self.item = item
        self.edit = QLineEdit(item.name)
        self.edit.setPlaceholderText("File name")
        self.edit.textEdited.connect(self._edited)
        self.pill = Pill()
        self.dest = ElidedLabel("", 10, mono=True, color=theme.TEXT_FAINT, mode=Qt.TextElideMode.ElideLeft)
        top = hbox(IconLabel("doc", theme.TEXT_DIM, 12), self.edit, Label(".step", 11, mono=True, color=theme.TEXT_DIM),
                   self.pill, spacing=6)
        top.setStretch(1, 1)
        self.lay.addLayout(top)
        self.lay.addLayout(hbox(18, self.dest, spacing=0))
        self.refresh()

    def _edited(self, t):
        self.item.name = t
        self.refresh()
        self.dialog.refresh_footer()

    def refresh(self):
        m = self.dialog.model
        dest = m.export_path(self.item)
        exists = os.path.exists(dest)
        replace = self.dialog.settings.replaceExisting
        self.pill.setVisible(exists)
        if exists:
            self.pill.set("Will be replaced" if replace else "Name taken", theme.WARN, "exclamation")
            self.pill.setToolTip("A file with this name is already in the folder. It will be replaced." if replace else
                                 "A file with this name is already in the folder. You'll be asked whether to replace it or use another name.")
        folder = os.path.dirname(dest)
        self.dest.setText("→ " + display_path(folder))
        self.dest.extra_tooltip = folder
        if self.edit.text() != self.item.name:
            self.edit.setText(self.item.name)


class ClashPrompt(QWidget):
    """"A file with this name exists": replace it, or rename the new one."""

    def __init__(self, dialog):
        super().__init__(dialog)
        self.dialog = dialog
        self.card = Card(padding=18, spacing=12)
        self.card.fill = theme.CARD
        self.card.border = theme.STROKE_STRONG
        self.card.setFixedWidth(440)
        self.card.radius = theme.R_BIG
        self.title = Label("", 14, BOLD, wrap=True)
        self.text = Label("", 11.5, color=theme.TEXT_DIM, wrap=True)
        self.card.lay.addLayout(hbox(IconLabel("doc-dup", theme.WARN, 20), vbox(self.title, self.text, spacing=4), spacing=10))
        self.name = QLineEdit()
        self.name.setPlaceholderText("New name")
        self.name.textEdited.connect(lambda _: self.refresh())
        self.taken = Label("That name is taken too.", 10.5, color=theme.WARN)
        self.card.lay.addLayout(vbox(Label("New name", 11, SEMIBOLD, color=theme.TEXT_DIM),
                                     hbox(self.name, Label(".step", 11, mono=True, color=theme.TEXT_DIM), spacing=6),
                                     self.taken, spacing=4))
        self.cancel = GhostButton("Cancel Export")
        self.cancel.clicked.connect(dialog.cancel_clash)
        self.replace = GhostButton("Replace", tint=theme.BAD)
        self.replace.setToolTip("Overwrite the file that is already there")
        self.replace.clicked.connect(dialog.replace_clash)
        self.save = AccentButton("Save as New Name", expand=False)
        self.save.clicked.connect(dialog.rename_clash)
        self.card.lay.addLayout(hbox(self.cancel, None, self.replace, self.save, spacing=8))
        lay = QVBoxLayout(self)
        lay.addStretch(1)
        lay.addLayout(hbox(None, self.card, None))
        lay.addStretch(1)
        self.hide()

    def paintEvent(self, _):
        QPainter(self).fillRect(self.rect(), theme.with_alpha(theme.BG, 0.6))

    def ask(self, item, new_name):
        dest = self.dialog.model.export_path(item)
        self.item = item
        self.title.setText(f"“{os.path.basename(dest)}” already exists")
        self.text.setText(f"There is already a file with this name in “{os.path.basename(os.path.dirname(dest))}”. "
                          "Replace it, or save the new file under another name?")
        self.name.setText(new_name)
        self.refresh()
        self.setGeometry(self.dialog.rect())
        self.show()
        self.raise_()
        self.name.setFocus()
        self.name.selectAll()

    def candidate(self):
        return ExportItem(job=self.item.job, part=self.item.part, source=self.item.source, name=self.name.text())

    def refresh(self):
        empty = not self.name.text().strip()
        taken = not empty and self.dialog.is_taken(self.candidate())
        self.taken.setVisible(taken)
        self.save.setEnabled(not taken and not empty)

    def _submit(self):
        if self.save.isEnabled():
            self.dialog.rename_clash()


class ExportDialog(QDialog):
    def __init__(self, model, settings, jobs, parent=None):
        super().__init__(parent)
        self.model = model
        self.settings = settings
        self.items = model.export_items(jobs)
        self.exporting = False
        self.pending = []
        self.settled = []
        self.clash_item = None
        self.failures = []
        self.setWindowTitle("Export")
        self.setModal(True)
        self.setFixedWidth(580)

        self.title = Label("", 16, BOLD)
        header = hbox(_Header(), vbox(self.title, Label("Check the names and where the files go, then press Export.", 11.5,
                                                         color=theme.TEXT_DIM), spacing=2), None, spacing=12)
        self.list_label = SectionLabel("Files" if len(self.items) != 1 else "File")
        self.rows_holder = QWidget()
        self.rows_lay = QVBoxLayout(self.rows_holder)
        self.rows_lay.setContentsMargins(0, 0, 0, 0)
        self.rows_lay.setSpacing(6)
        self.rows = []
        for it in self.items:
            r = FileRow(self, it)
            self.rows.append(r)
            self.rows_lay.addWidget(r)
        self.rows_lay.addStretch(1)
        self.rows_scroll = QScrollArea()
        self.rows_scroll.setWidgetResizable(True)
        self.rows_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rows_scroll.setWidget(self.rows_holder)
        row_h = 66
        self.rows_scroll.setFixedHeight(min(230, row_h * len(self.items) + 6 * max(0, len(self.items) - 1) + 2))

        dest = Card(spacing=8)
        dest.lay.addWidget(SectionLabel("Save to"))
        self.output = OutputModePicker(model, settings, folder_suffix="next to each original file")
        dest.lay.addWidget(self.output)

        self.replace = QCheckBox("Replace files that already exist")
        self.replace.toggled.connect(lambda on: setattr(settings, "replaceExisting", on))
        self.replace_help = Label("", 10.5, color=theme.TEXT_FAINT, wrap=True)
        self.reveal = QCheckBox(f"{system.REVEAL_LABEL} after exporting")
        self.reveal.toggled.connect(lambda on: setattr(settings, "revealWhenDone", on))
        options = vbox(self.replace, hbox(20, self.replace_help, spacing=0), self.reveal, spacing=8)

        self.failure_card = Card(padding=10, spacing=8)
        self.failure_card.fill = theme.with_alpha(theme.BAD, 0.08)
        self.failure_card.border = theme.with_alpha(theme.BAD, 0.4)
        self.failure_card.radius = theme.R_MED
        self.failure_card.hide()

        self.busy = ThinProgress()
        self.busy.setFixedWidth(60)
        self.footer_text = Label("", 11.5, color=theme.TEXT_DIM)
        self.cancel_btn = GhostButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        self.export_btn = AccentButton("Export", expand=False)
        self.export_btn.setDefault(True)
        self.export_btn.clicked.connect(self.start_export)
        footer = hbox(self.busy, self.footer_text, None, self.cancel_btn, self.export_btn, spacing=8)

        self.body = QWidget()
        self.body.setLayout(vbox(header, vbox(self.list_label, self.rows_scroll, spacing=6), dest, options,
                                 self.failure_card, footer, spacing=16))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 20, 20, 20)
        lay.addWidget(self.body)
        self.clash = ClashPrompt(self)
        settings.changed.connect(self._setting_changed)
        self.refresh()

    # ------------------------------------------------------------------ view

    def _enter(self):
        if self.clash.isVisible():
            self.clash._submit()
        elif self.export_btn.isEnabled():
            self.start_export()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            if self.clash.isVisible():
                self.cancel_clash()
            elif not self.exporting:
                self.reject()
            return
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._enter()
            return
        super().keyPressEvent(e)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if self.clash.isVisible():
            self.clash.setGeometry(self.rect())

    def done(self, r):
        try:
            self.settings.changed.disconnect(self._setting_changed)
        except (RuntimeError, TypeError):
            pass
        super().done(r)

    def reject(self):
        if not self.exporting:
            super().reject()

    def _setting_changed(self, key):
        if key in ("outputMode", "subfolderName", "outputFolder", "replaceExisting", "revealWhenDone"):
            if key in ("outputMode", "outputFolder"):
                self.failures = []
            self.refresh()

    @property
    def missing_folder(self):
        return self.settings.outputMode == "custom" and not self.settings.outputFolder

    def refresh(self):
        n = len(self.items)
        self.title.setText("Export STEP File" if n == 1 else f"Export {n} STEP Files")
        for r in self.rows:
            r.refresh()
        s = self.settings
        set_checked(self.replace, s.replaceExisting)
        set_checked(self.reveal, s.revealWhenDone)
        self.replace_help.setText("A file with the same name in the folder is overwritten." if s.replaceExisting else
                                  "If a file with the same name is already there, you'll be asked whether to replace it "
                                  "or give the new file another name.")
        self._refresh_failures()
        self.refresh_footer()
        self.adjustSize()

    def refresh_footer(self):
        self.busy.setVisible(self.exporting)
        self.busy.set_value(0, indeterminate=self.exporting)
        if self.exporting:
            self.footer_text.setText("Exporting…")
            self.footer_text.set_color(theme.TEXT_DIM)
        elif self.missing_folder:
            self.footer_text.setText("Choose a folder first")
            self.footer_text.set_color(theme.WARN)
        else:
            self.footer_text.setText("")
        n = len(self.items)
        self.export_btn.setText("Export" if n == 1 else f"Export {n} Files")
        self.export_btn.updateGeometry()
        self.export_btn.setEnabled(not self.exporting and not self.missing_folder and bool(self.items)
                                   and all(it.name.strip() for it in self.items))
        self.cancel_btn.setEnabled(not self.exporting)

    def _refresh_failures(self):
        clear_layout(self.failure_card.lay)
        if not self.failures:
            self.failure_card.hide()
            return
        for f in self.failures:
            row = hbox(IconLabel("x-octagon", theme.BAD, 11), Label(f.message, 11.5, wrap=True), spacing=7)
            row.setStretch(1, 1)
            self.failure_card.lay.addLayout(row)
        if any(f.is_permission for f in self.failures):
            if system.IS_WINDOWS:
                help_text = ("If Controlled folder access is on in Windows Security, allow Mesh Mix-S2C under "
                             "Ransomware protection. Or choose another folder to export to.")
            else:
                help_text = "Choose another folder to export to, or check that you may write to this one."
            self.failure_card.lay.addWidget(Label(help_text, 11, color=theme.TEXT_DIM, wrap=True))
            choose = GhostButton("Choose Another Folder…", tint="accent")
            choose.clicked.connect(self._choose_folder)
            buttons = [choose]
            if system.IS_WINDOWS:
                sec = GhostButton("Open Ransomware Protection")
                sec.clicked.connect(lambda: system.open_url("windowsdefender://ransomwareprotection"))
                buttons.append(sec)
            self.failure_card.lay.addLayout(hbox(*buttons, None))
        self.failure_card.show()

    def _choose_folder(self):
        if self.model.choose_output_folder():
            self.failures = []
            self.refresh()

    # ------------------------------------------------------------------ name clashes

    def is_taken(self, item):
        """The file's destination is already used: by a file in the folder,
        or by a file earlier in this export."""
        dest = self.model.export_path(item)
        if any(_key(self.model.export_path(s)) == _key(dest) for s in self.settled):
            return True
        return os.path.exists(dest)

    def free_name(self, item):
        """"Part 2", "Part 3", … — the first name that's free."""
        n = 2
        while True:
            c = ExportItem(job=item.job, part=item.part, source=item.source, name=f"{item.name} {n}")
            if not self.is_taken(c):
                return c.name
            n += 1

    def start_export(self):
        if self.exporting or self.missing_folder or not self.export_btn.isEnabled():
            return
        self.failures = []
        self._refresh_failures()
        self.settled = []
        self.pending = list(self.items)
        self._next_clash()

    def _next_clash(self):
        """Goes through the files; stops to ask when a name is taken."""
        while self.pending:
            item = self.pending[0]
            dest = self.model.export_path(item)
            in_folder = os.path.exists(dest)
            in_export = any(_key(self.model.export_path(s)) == _key(dest) for s in self.settled)
            if in_export or (in_folder and not self.settings.replaceExisting):
                self.clash_item = item
                self.body.setEnabled(False)
                self.clash.ask(item, self.free_name(item))
                return
            self.settled.append(item)
            self.pending.pop(0)
        self._run_export(self.settled)

    def _close_clash(self):
        self.clash_item = None
        self.clash.hide()
        self.body.setEnabled(True)

    def replace_clash(self):
        item = self.clash_item
        if item is None:
            return
        # two files of this export with the same name: the later one wins
        k = _key(self.model.export_path(item))
        self.settled = [s for s in self.settled if _key(self.model.export_path(s)) != k]
        self.settled.append(item)
        self.pending.pop(0)
        self._close_clash()
        self._next_clash()

    def rename_clash(self):
        item = self.clash_item
        if item is None or not self.clash.save.isEnabled():
            return
        item.name = safe_file_name(self.clash.name.text())
        for r in self.rows:
            if r.item is item:
                r.refresh()
        self.pending[0] = item
        self._close_clash()
        self._next_clash()

    def cancel_clash(self):
        self._close_clash()
        self.pending = []
        self.settled = []

    def _run_export(self, items):
        if not items:
            return
        self.exporting = True
        self.refresh_footer()

        def finished(failed):
            self.exporting = False
            self.failures = failed
            if not failed:
                self.accept()
                return
            # the others are saved; only the failed files are left to export
            ids = {f.item.id for f in failed}
            self.items = [it for it in self.items if it.id in ids]
            for r in list(self.rows):
                if r.item.id not in ids:
                    self.rows.remove(r)
                    r.setParent(None)
                    r.deleteLater()
            self.refresh()

        self.model.export(items, finished)
