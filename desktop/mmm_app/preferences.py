"""Preferences window (the Mac app's Settings window, plus updates)."""
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QGroupBox, QSpinBox, QVBoxLayout

from . import __version__, system, theme
from .settings import ACCENTS, PRESETS
from .settings_panel import set_checked, set_combo
from .widgets import GhostButton, Label, hbox, vbox


def _dot(color):
    pm = QPixmap(20, 20)
    pm.fill(QColor(0, 0, 0, 0))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(color)
    p.setPen(QColor(0, 0, 0, 0))
    p.drawEllipse(5, 5, 10, 10)
    p.end()
    return QIcon(pm)


class PreferencesDialog(QDialog):
    def __init__(self, settings, updates, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.updates = updates
        self.setWindowTitle("Preferences")
        self.setFixedWidth(440)
        s = settings

        self.accent = QComboBox()
        for a in ACCENTS:
            self.accent.addItem(_dot(theme.ACCENT_COLORS[a.value]), a.title, a.value)
        self.accent.currentIndexChanged.connect(lambda: setattr(s, "accent", self.accent.currentData()))
        appearance = self._group("Appearance", hbox(Label("Accent color", 12), None, self.accent))

        self.preset = QComboBox()
        for p in PRESETS:
            self.preset.addItem(p.title, p.value)
        self.preset.currentIndexChanged.connect(lambda: setattr(s, "preset", self.preset.currentData()))
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 6)
        self.parallel.valueChanged.connect(lambda v: setattr(s, "parallelJobs", v))
        self.on_drop = QCheckBox("Convert as soon as files are added")
        self.on_drop.toggled.connect(lambda on: setattr(s, "convertOnDrop", on))
        conversion = self._group("Conversion", hbox(Label("Default preset", 12), None, self.preset),
                                 hbox(Label("Files at once", 12), None, self.parallel), self.on_drop)

        self.replace = QCheckBox("Replace existing files without asking")
        self.replace.toggled.connect(lambda on: setattr(s, "replaceExisting", on))
        self.reveal = QCheckBox(f"{system.REVEAL_LABEL} after exporting")
        self.reveal.toggled.connect(lambda on: setattr(s, "revealWhenDone", on))
        self.split = QCheckBox("Split 3MF projects into parts")
        self.split.toggled.connect(lambda on: setattr(s, "splitParts", on))
        export = self._group("Export", self.replace, self.reveal, self.split)

        self.auto_check = QCheckBox("Check for updates when the app opens")
        self.auto_check.setToolTip("Only looks: an update is installed when you click Install.")
        self.auto_check.toggled.connect(lambda on: setattr(s, "checkUpdates", on))
        check_now = GhostButton("Check for Updates…", "update")
        check_now.clicked.connect(self._check_now)
        updates_box = self._group("Updates", hbox(Label(f"Version {__version__}", 12, color=theme.TEXT_DIM), None, check_now),
                                  self.auto_check)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(4)
        for g in (appearance, conversion, export, updates_box):
            lay.addWidget(g)
        settings.changed.connect(self.refresh)
        self.refresh()

    def _group(self, title, *rows):
        g = QGroupBox(title.upper())
        g.setLayout(vbox(*rows, spacing=10))
        return g

    def _check_now(self):
        parent = self.parent()
        self.accept()
        if parent is not None and hasattr(parent, "check_for_updates"):
            parent.check_for_updates()

    def done(self, r):
        try:
            self.settings.changed.disconnect(self.refresh)
        except (RuntimeError, TypeError):
            pass
        super().done(r)

    def refresh(self, *_):
        s = self.settings
        set_combo(self.accent, s.accent)
        set_combo(self.preset, s.preset)
        if self.parallel.value() != s.parallelJobs:
            self.parallel.blockSignals(True)
            self.parallel.setValue(s.parallelJobs)
            self.parallel.blockSignals(False)
        set_checked(self.on_drop, s.convertOnDrop)
        set_checked(self.replace, s.replaceExisting)
        set_checked(self.reveal, s.revealWhenDone)
        set_checked(self.split, s.splitParts)
        set_checked(self.auto_check, s.checkUpdates)
