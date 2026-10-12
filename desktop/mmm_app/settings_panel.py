"""Right column: preset, speed/quality, where to export, advanced options,
and the Convert / Export buttons."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDoubleSpinBox, QLineEdit, QRadioButton, QScrollArea,
                               QSlider, QSpinBox, QVBoxLayout, QWidget)

from . import detail, system, theme
from .model import JobStatus
from .settings import MAX_FACES, OUTPUT_MODES, PLATE_LAYOUTS, PRESETS, REPAIR_LEVELS, UNITS, choice
from .theme import SEMIBOLD
from .widgets import (AccentButton, Card, ElidedLabel, GhostButton, IconLabel, Label, PresetCard, SectionLabel, Divider,
                      hbox, vbox)


def quality_word(q):
    if q < 0.25:
        return "Fastest"
    if q < 0.5:
        return "Fast"
    if q < 0.62:
        return "Balanced"
    if q < 0.85:
        return "High quality"
    return "Best quality"


def combo(options, current, width=None):
    c = QComboBox()
    for o in options:
        c.addItem(o.title, o.value)
    i = c.findData(current)
    c.setCurrentIndex(max(0, i))
    if width:
        c.setFixedWidth(width)
    return c


def set_combo(c, value):
    i = c.findData(value)
    if i >= 0 and i != c.currentIndex():
        c.blockSignals(True)
        c.setCurrentIndex(i)
        c.blockSignals(False)


def set_checked(w, on):
    if w.isChecked() != on:
        w.blockSignals(True)
        w.setChecked(on)
        w.blockSignals(False)


class OutputModePicker(QWidget):
    """'Export to' radio group with the subfolder name or the chosen folder."""

    def __init__(self, model, settings, folder_suffix="", parent=None):
        super().__init__(parent)
        self.model = model
        self.settings = settings
        self.group = QButtonGroup(self)
        lay = vbox(spacing=6)
        self.radios = {}
        for o in OUTPUT_MODES:
            r = QRadioButton(o.title)
            self.group.addButton(r)
            self.radios[o.value] = r
            r.toggled.connect(lambda on, v=o.value: on and setattr(settings, "outputMode", v))
            lay.addWidget(r)
        self.sub_name = QLineEdit(settings.subfolderName)
        self.sub_name.setPlaceholderText("Folder name")
        self.sub_name.textEdited.connect(lambda t: setattr(settings, "subfolderName", t))
        self.sub_row = QWidget()
        sub = hbox(IconLabel("folder", theme.TEXT_DIM, 14), self.sub_name, spacing=6)
        if folder_suffix:
            sub.addWidget(Label(folder_suffix, 11, color=theme.TEXT_FAINT))
        self.sub_row.setLayout(sub)
        self.sub_help = Label("When you export, a folder with this name is made next to each original file.", 10.5,
                              color=theme.TEXT_FAINT, wrap=True)
        self.custom_label = ElidedLabel("", 10.5, mono=True, mode=Qt.TextElideMode.ElideLeft)
        self.choose = GhostButton("Choose…")
        self.choose.clicked.connect(model.choose_output_folder)
        self.custom_row = QWidget()
        cr = hbox(self.custom_label, self.choose, spacing=8)
        cr.setStretch(0, 1)
        self.custom_row.setLayout(cr)
        lay.addWidget(self.sub_row)
        lay.addWidget(self.sub_help)
        lay.addWidget(self.custom_row)
        self.setLayout(lay)
        self.show_help = not folder_suffix
        settings.changed.connect(self._changed)
        self.refresh()

    def _changed(self, key):
        if key in ("outputMode", "subfolderName", "outputFolder"):
            self.refresh()

    def refresh(self):
        s = self.settings
        for v, r in self.radios.items():
            set_checked(r, v == s.outputMode)
        if self.sub_name.text() != s.subfolderName:
            self.sub_name.setText(s.subfolderName)
        self.sub_row.setVisible(s.outputMode == "subfolder")
        self.sub_help.setVisible(s.outputMode == "subfolder" and self.show_help)
        self.custom_row.setVisible(s.outputMode == "custom")
        missing = not s.outputFolder
        self.custom_label.setText("No folder chosen yet" if missing else s.outputFolder)
        self.custom_label.set_color(theme.WARN if missing else theme.TEXT_DIM)


class SettingsPanel(QWidget):
    def __init__(self, model, settings, parent=None):
        super().__init__(parent)
        self.model = model
        self.settings = settings
        s = settings

        # presets
        self.preset_cards = []
        presets = vbox(spacing=8)
        for p in PRESETS:
            card = PresetCard(p)
            card.clicked.connect(lambda v=p.value: setattr(s, "preset", v))
            self.preset_cards.append(card)
            presets.addWidget(card)

        # speed / quality
        self.quality_word = Label("", 10.5, SEMIBOLD)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setAccessibleName("Speed versus quality")
        self.slider.valueChanged.connect(lambda v: setattr(s, "quality", v / 1000))
        ends = hbox(IconLabel("fast", theme.TEXT_FAINT, 11), Label("Faster", 10, color=theme.TEXT_FAINT), None,
                    IconLabel("sparkles", theme.TEXT_FAINT, 11), Label("Better", 10, color=theme.TEXT_FAINT), spacing=4)
        self.quality_hint = Label("", 10.5, color=theme.TEXT_DIM, wrap=True)
        quality = Card()
        quality.lay.addLayout(hbox(SectionLabel("Speed ↔ Quality"), None, self.quality_word))
        quality.lay.addWidget(self.slider)
        quality.lay.addLayout(ends)
        quality.lay.addWidget(self.quality_hint)
        # heads-up when best quality will make a huge file of a dense mesh
        self.detail_text = Label("", 10.5, color=theme.WARN, wrap=True)
        self.detail_text.setAccessibleName("Large file warning")
        self.detail_warning = QWidget()
        dl = hbox(IconLabel("warning", theme.WARN, 14), self.detail_text, spacing=8)
        dl.setAlignment(dl.itemAt(0).widget(), Qt.AlignmentFlag.AlignTop)
        dl.setStretch(1, 1)
        self.detail_warning.setLayout(dl)
        self.detail_warning.hide()
        quality.lay.addWidget(self.detail_warning)

        # export
        export = Card(spacing=10)
        export.lay.addWidget(SectionLabel("Export to"))
        self.output = OutputModePicker(model, settings)
        export.lay.addWidget(self.output)
        export.lay.addWidget(Divider())
        self.unit = combo(UNITS, s.unit, 130)
        self.unit.currentIndexChanged.connect(lambda: setattr(s, "unit", self.unit.currentData()))
        units_row = QWidget()
        units_row.setLayout(hbox(Label("Units", 12), None, self.unit))
        units_row.setToolTip("The unit your STL files were made in. 3D-printing files are almost always millimeters.")
        export.lay.addWidget(units_row)
        self.replace = QCheckBox("Replace existing files")
        self.replace.setToolTip("When off, the app asks before replacing a file that has the same name, and lets you rename the new one.")
        self.replace.toggled.connect(lambda on: setattr(s, "replaceExisting", on))
        self.split = QCheckBox("Split 3MF projects into parts")
        self.split.setToolTip("Each part of a slicer project becomes its own STEP file (Project - Part.step), like "
                              "“Split to parts” in the slicer. When off, all parts go into one STEP file.")
        self.split.toggled.connect(lambda on: setattr(s, "splitParts", on))
        export.lay.addWidget(self.replace)
        export.lay.addWidget(self.split)

        # advanced
        self.advanced = Card(spacing=12)
        self.adv_toggle = GhostButton("", "chevron-right")
        self.adv_toggle.setFixedSize(22, 22)
        self.adv_toggle.clicked.connect(self._toggle_advanced)
        adv_head = QWidget()
        adv_head.setLayout(hbox(SectionLabel("Advanced"), None, self.adv_toggle))
        adv_head.setCursor(Qt.CursorShape.PointingHandCursor)
        adv_head.mousePressEvent = lambda e: self._toggle_advanced()
        self.advanced.lay.addWidget(adv_head)
        self.adv_body = QWidget()
        body = vbox(spacing=12, margins=(0, 8, 0, 0))
        self.layout_combo = combo(PLATE_LAYOUTS, s.plateLayout, 150)
        self.layout_combo.currentIndexChanged.connect(lambda: setattr(s, "plateLayout", self.layout_combo.currentData()))
        self.layout_help = Label("", 10.5, color=theme.TEXT_FAINT, wrap=True)
        body.addLayout(vbox(hbox(Label("3MF projects", 12), None, self.layout_combo), self.layout_help, spacing=4))
        self.repair = combo(REPAIR_LEVELS, s.repair, 120)
        self.repair.currentIndexChanged.connect(lambda: setattr(s, "repair", self.repair.currentData()))
        repair_row = QWidget()
        repair_row.setLayout(hbox(Label("Mesh repair", 12), None, self.repair))
        repair_row.setToolTip("Closes holes and fixes broken triangles before converting.")
        body.addWidget(repair_row)
        self.debris = QCheckBox("Remove tiny floating pieces")
        self.debris.toggled.connect(lambda on: setattr(s, "removeDebris", on))
        body.addWidget(self.debris)
        self.custom_tol = QCheckBox("Custom fit tolerance")
        self.custom_tol.setToolTip("How far the CAD surfaces may deviate from the mesh. Normally chosen automatically "
                                   "from the part size and the quality slider.")
        self.custom_tol.toggled.connect(lambda on: setattr(s, "useCustomTolerance", on))
        body.addWidget(self.custom_tol)
        self.tol = QDoubleSpinBox()
        self.tol.setDecimals(4)
        self.tol.setRange(0.0, 1000.0)
        self.tol.setSingleStep(0.01)
        self.tol.setFixedWidth(100)
        self.tol.valueChanged.connect(lambda v: setattr(s, "customTolerance", v))
        self.tol_unit = Label("", 11, color=theme.TEXT_DIM)
        self.tol_row = QWidget()
        self.tol_row.setLayout(hbox(self.tol, self.tol_unit, None))
        body.addWidget(self.tol_row)
        self.max_faces = combo(MAX_FACES, s.maxFaces, 120)
        self.max_faces.currentIndexChanged.connect(lambda: setattr(s, "maxFaces", self.max_faces.currentData()))
        mf_row = QWidget()
        mf_row.setLayout(hbox(Label("Smooth surface limit", 12), None, self.max_faces))
        mf_row.setToolTip("The most surfaces the Smooth preset may create. Fewer means smaller files but less detail.")
        body.addWidget(mf_row)
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 6)
        self.parallel.setFixedWidth(64)
        self.parallel.valueChanged.connect(lambda v: setattr(s, "parallelJobs", v))
        par_row = QWidget()
        par_row.setLayout(hbox(Label("Files at once", 12), None, self.parallel))
        par_row.setToolTip("How many files convert at the same time. Big meshes need a lot of memory; 1–2 is safest.")
        body.addWidget(par_row)
        self.on_drop = QCheckBox("Convert as soon as files are added")
        self.on_drop.toggled.connect(lambda on: setattr(s, "convertOnDrop", on))
        body.addWidget(self.on_drop)
        self.reveal = QCheckBox(f"{system.REVEAL_LABEL} after exporting")
        self.reveal.toggled.connect(lambda on: setattr(s, "revealWhenDone", on))
        body.addWidget(self.reveal)
        self.adv_body.setLayout(body)
        self.adv_body.hide()
        self.advanced.lay.addWidget(self.adv_body)

        content = QWidget()
        content.setLayout(vbox(SectionLabel("Preset"), presets, quality, export, self.advanced, None, spacing=16,
                               margins=(16, 16, 16, 16)))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(content)
        self.scroll = scroll

        # bottom buttons
        self.convert = AccentButton("Convert", "bolt")
        self.convert.clicked.connect(model.convert_all)
        self.export = AccentButton("Export", "upload")
        self.export.clicked.connect(lambda: model.request_export(model.unexported_jobs))
        self.status = Label("", 10.5, color=theme.TEXT_FAINT)
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.again = AccentButton("Convert selected file again", "refresh", prominent=False)
        self.again.setFont(theme.font(12.5, SEMIBOLD))
        self.again.setToolTip("Convert the selected file again with the current preset and settings")
        self.again.clicked.connect(lambda: model.selected_job and model.reconvert(model.selected_job))
        holder = QWidget()
        hl = vbox(spacing=8)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.addWidget(Divider())
        inner = vbox(self.convert, self.export, self.status, self.again, spacing=8, margins=(16, 0, 16, 0))
        hl.addLayout(inner)
        holder.setLayout(hl)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 16)
        lay.setSpacing(8)
        lay.addWidget(scroll, 1)
        lay.addWidget(holder)

        settings.changed.connect(self._setting_changed)
        model.state_changed.connect(self.refresh_buttons)
        model.selection_changed.connect(self.refresh_buttons)
        model.jobs_changed.connect(self.refresh_buttons)
        model.state_changed.connect(self.refresh_detail_warning)
        model.jobs_changed.connect(self.refresh_detail_warning)
        self.refresh()

    def paintEvent(self, _):
        QPainter(self).fillRect(self.rect(), theme.PANEL)

    def _toggle_advanced(self):
        on = not self.adv_body.isVisible()
        self.adv_body.setVisible(on)
        self.adv_toggle.set_icon_name("chevron-down" if on else "chevron-right")

    def _setting_changed(self, key):
        if key in ("splitParts", "plateLayout"):
            self.model.apply_split_default()
        self.refresh()

    def refresh(self):
        s = self.settings
        for card in self.preset_cards:
            card.set_selected(card.preset.value == s.preset)
        if self.slider.value() != round(s.quality * 1000):
            self.slider.blockSignals(True)
            self.slider.setValue(round(s.quality * 1000))
            self.slider.blockSignals(False)
        self.quality_word.setText(quality_word(s.quality))
        self.quality_word.set_color(theme.accent())
        self.quality_hint.setText(choice(PRESETS, s.preset).hint)
        self.refresh_detail_warning()
        set_combo(self.unit, s.unit)
        set_checked(self.replace, s.replaceExisting)
        set_checked(self.split, s.splitParts)
        self.split.setEnabled(self.model.split_available)
        set_combo(self.layout_combo, s.plateLayout)
        self.layout_help.setText(choice(PLATE_LAYOUTS, s.plateLayout).subtitle)
        set_combo(self.repair, s.repair)
        set_checked(self.debris, s.removeDebris)
        set_checked(self.custom_tol, s.useCustomTolerance)
        self.tol_row.setVisible(s.useCustomTolerance)
        if abs(self.tol.value() - s.customTolerance) > 1e-9:
            self.tol.blockSignals(True)
            self.tol.setValue(s.customTolerance)
            self.tol.blockSignals(False)
        self.tol_unit.setText("inches" if s.unit == "in" else s.unit)
        set_combo(self.max_faces, s.maxFaces)
        if self.parallel.value() != s.parallelJobs:
            self.parallel.blockSignals(True)
            self.parallel.setValue(s.parallelJobs)
            self.parallel.blockSignals(False)
        set_checked(self.on_drop, s.convertOnDrop)
        set_checked(self.reveal, s.revealWhenDone)
        self.refresh_buttons()

    def refresh_detail_warning(self):
        s = self.settings
        text = detail.warning_text(self.model.jobs, s.quality, s.preset, s.maxFaces)
        self.detail_warning.setVisible(bool(text))
        if text and self.detail_text.text() != text:
            self.detail_text.setText(text)

    def refresh_buttons(self):
        m = self.model
        n = m.pending_count
        to_export = m.unexported_jobs
        files = sum(len(j.result_files) for j in to_export)
        self.convert.setText(f"Convert {n} file{'' if n == 1 else 's'}" if n else "Convert")
        self.convert.setEnabled(n > 0)
        self.convert.set_prominent(n > 0 or files == 0)
        self.convert.setToolTip("Add files to convert" if n == 0 else f"Convert all files that are ready ({system.MOD}+Enter)")
        self.export.setVisible(files > 0)
        self.export.setText(f"Export {files} STEP file{'' if files == 1 else 's'}…")
        self.export.set_prominent(n == 0)
        self.export.setToolTip(f"Choose where to save the converted files that haven't been exported yet ({system.MOD}+Shift+E)")
        busy = any(j.status in (JobStatus.INSPECTING, JobStatus.QUEUED) for j in m.jobs) or m.running_count > 0
        if n == 0 and m.jobs and not busy:
            unreadable = sum(1 for j in m.jobs if not j.readable)
            if unreadable:
                self.status.setText(f"Done · {unreadable} file{'' if unreadable == 1 else 's'} couldn't be read")
                self.status.set_color(theme.WARN)
            else:
                self.status.setText("Converted · look the results over, then export" if files else "Everything is converted")
                self.status.set_color(theme.TEXT_FAINT)
            self.status.show()
        else:
            self.status.hide()
        j = m.selected_job
        self.again.setVisible(j is not None and j.status == JobStatus.DONE)
        for w in (self.convert, self.export, self.again):
            w.update()

    def refresh_theme(self):
        for c in self.preset_cards:
            c.refresh()
        self.refresh()
        self.update()
