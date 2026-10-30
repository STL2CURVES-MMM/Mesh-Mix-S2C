"""The main window: queue | 3D preview | settings, plus menus and shortcuts."""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit,
                               QAbstractSpinBox, QWidget)

from . import APP_NAME, REPO_URL, SUPPORT_URL, __version__, bugreport, desktop_entry, system, theme
from .export_dialog import ExportDialog
from .model import ACCEPTED_EXTENSIONS, ENGINE_MISSING_MESSAGE, AppModel, JobStatus
from .preferences import PreferencesDialog
from .preview import PreviewPanel
from .settings import PRESETS
from .settings_panel import SettingsPanel
from .sidebar import Sidebar
from .update_ui import UpdateController, UpdateDialog, schedule_launch_check
from .widgets import Card, Divider, IconLabel, Label


def short_name_list(groups, limit=8):
    """Groups of part names as one line ("A, B; C, D"), the first ``limit``
    names only and "and N more" after them: a 3MF of 334 touching parts
    listed them all, and the question's buttons were off the screen."""
    out, shown = [], 0
    for g in groups:
        take = [str(x) for x in g][:max(0, limit - shown)]
        if take:
            out.append(", ".join(take))
        shown += len(take)
    rest = sum(len(g) for g in groups) - shown
    text = "; ".join(out)
    return f"{text} and {rest} more" if rest > 0 else text

class EngineMissingBanner(Card):
    def __init__(self, parent=None):
        super().__init__(parent, padding=12, radius=theme.R_MED, vertical=False, spacing=10)
        self.border = theme.with_alpha(theme.WARN, 0.5)
        self.lay.addWidget(IconLabel("warning", theme.WARN, 14))
        self.lay.addWidget(Label(ENGINE_MISSING_MESSAGE, 12))
        self.adjustSize()


class MainWindow(QMainWindow):
    def __init__(self, settings):
        super().__init__()
        self.settings = settings
        self.model = AppModel(settings, ui=self)
        self.updates = UpdateController(self)
        self.updating = False
        self.export_dialog = None
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1080, 640)
        self.resize(1280, 780)
        self.setAcceptDrops(True)

        self.sidebar = Sidebar(self.model)
        self.sidebar.setFixedWidth(310)
        self.preview = PreviewPanel(self.model, settings)
        self.preview.setMinimumWidth(420)
        self.settings_panel = SettingsPanel(self.model, settings)
        self.settings_panel.setFixedWidth(300)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.sidebar)
        lay.addWidget(Divider(vertical=True))
        lay.addWidget(self.preview, 1)
        lay.addWidget(Divider(vertical=True))
        lay.addWidget(self.settings_panel)
        self.setCentralWidget(central)
        self.engine_banner = EngineMissingBanner(self.preview)
        self.engine_banner.setVisible(self.model.engine_missing)

        self.sidebar.add_clicked.connect(self.open_files)
        self.sidebar.header.update_clicked.connect(self.check_for_updates)
        self.sidebar.update_banner.clicked.connect(self.check_for_updates)
        self.model.export_requested.connect(self.show_export)
        self.model.all_finished.connect(lambda: QApplication.alert(self))
        self.model.engine_missing_changed.connect(lambda: (self.engine_banner.setVisible(self.model.engine_missing),
                                                           self._place_banner()))
        self.updates.changed.connect(self._updates_changed)
        self.updates.install_requested.connect(self._install_update)
        settings.changed.connect(self._setting_changed)
        # Match system follows the computer's dark/light setting as it changes
        QApplication.instance().styleHints().colorSchemeChanged.connect(self._system_scheme_changed)
        self._build_menus()
        if not os.environ.get("MMM_SELFTEST"):
            schedule_launch_check(self.updates, settings)

    # ------------------------------------------------------------------ menus

    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("&File")
        self._action(m, "Add Files…", self.open_files, QKeySequence.StandardKey.Open)
        self._action(m, "Add Folder…", self.open_folder)
        m.addSeparator()
        if desktop_entry.available():
            # Linux AppImage: the applications menu and "Open With"
            self.menu_entry = self._action(m, "Add to Applications Menu", self._toggle_menu_entry)
            m.aboutToShow.connect(self._refresh_menu_entry)
            self._refresh_menu_entry()
        self._action(m, "Preferences…", self.show_preferences, "Ctrl+,")
        m.addSeparator()
        self._action(m, "Quit", self.close, QKeySequence.StandardKey.Quit)

        c = mb.addMenu("&Convert")
        self._action(c, "Convert All", self.model.convert_all, "Ctrl+R")
        self.export_selected = self._action(c, "Export Selected File…", self._export_selected, "Ctrl+E")
        self.export_all = self._action(c, "Export All…", self._export_all, "Ctrl+Shift+E")
        self.stop_all = self._action(c, "Stop All", self.model.cancel_all, "Ctrl+.")
        c.addSeparator()
        self._action(c, "Clear Finished", self.model.clear_finished)
        c.addSeparator()
        pm = c.addMenu("Preset")
        self.preset_group = QActionGroup(self)
        self.preset_actions = {}
        for p in PRESETS:
            a = QAction(p.title, self, checkable=True)
            a.triggered.connect(lambda _=False, v=p.value: setattr(self.settings, "preset", v))
            self.preset_group.addAction(a)
            pm.addAction(a)
            self.preset_actions[p.value] = a
        c.aboutToShow.connect(self._refresh_menu)

        h = mb.addMenu("&Help")
        self._action(h, "Check for Updates…", self.check_for_updates)
        h.addSeparator()
        self._action(h, "Source Code", lambda: system.open_url(REPO_URL))
        self._action(h, "Support the Project ☕", lambda: system.open_url(SUPPORT_URL))
        self._action(h, "Licenses", self.show_licenses)
        self._action(h, f"About {APP_NAME}", self.show_about)

        for seq in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(seq), self, activated=self.model.convert_all)
        QShortcut(QKeySequence(Qt.Key.Key_Delete), self, activated=self._delete_selected)
        self._refresh_menu()

    def _action(self, menu, title, fn, shortcut=None):
        a = QAction(title, self)
        if shortcut is not None:
            a.setShortcut(QKeySequence(shortcut))
        a.triggered.connect(lambda _=False: fn())
        menu.addAction(a)
        return a

    def _refresh_menu(self):
        j = self.model.selected_job
        self.export_selected.setEnabled(bool(j and j.status == JobStatus.DONE and j.result_files))
        self.export_all.setEnabled(bool(self.model.exportable_jobs))
        self.stop_all.setEnabled(any(x.status in (JobStatus.QUEUED, JobStatus.CONVERTING) for x in self.model.jobs))
        a = self.preset_actions.get(self.settings.preset)
        if a:
            a.setChecked(True)

    def _refresh_menu_entry(self):
        self.menu_entry.setText("Remove from Applications Menu" if desktop_entry.is_installed()
                                else "Add to Applications Menu")

    def _toggle_menu_entry(self):
        if desktop_entry.is_installed():
            desktop_entry.uninstall()
            return
        err = desktop_entry.install()
        if err:
            QMessageBox.warning(self, APP_NAME, err)
        else:
            QMessageBox.information(self, APP_NAME, "Mesh Mix-S2C is now in your applications menu, and in "
                                    "“Open With” for STL, 3MF, OBJ and PLY files. If you move the AppImage, add it again.")

    def _export_selected(self):
        j = self.model.selected_job
        if j is not None:
            self.model.request_export([j])

    def _export_all(self):
        m = self.model
        m.request_export(m.unexported_jobs or m.exportable_jobs)

    def _delete_selected(self):
        w = QApplication.focusWidget()
        if isinstance(w, (QLineEdit, QPlainTextEdit, QAbstractSpinBox)):
            return
        j = self.model.selected_job
        if j is not None:
            self.model.remove(j)

    # ------------------------------------------------------------------ dialogs used by the model

    def confirm_discard(self, count, action):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(APP_NAME)
        box.setText("This file hasn't been exported yet" if count == 1 else f"{count} files haven't been exported yet")
        box.setInformativeText(f"If you continue, the converted STEP {'file is' if count == 1 else 'files are'} "
                               "deleted and you would have to convert again.")
        cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        go = box.addButton(f"{action} Anyway", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel)
        box.exec()
        return box.clickedButton() is go

    def choose_output_folder(self):
        start = self.settings.outputFolder or os.path.expanduser("~")
        d = QFileDialog.getExistingDirectory(self.export_dialog or self, "Choose the folder to export STEP files to", start)
        return os.path.abspath(d) if d else None

    def ask_touching(self, job):
        """Parts of a 3MF that touch each other: keep them as separate parts
        or merge them into one solid? Returns "split" or "merge"."""
        groups = short_name_list([g.get("names", []) for g in job.touching])
        separate = "one STEP file each" if self.model.split_available else "separate solids in one STEP file"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(APP_NAME)
        box.setText("Parts that touch")
        box.setInformativeText(f"In “{job.file_name}”, these parts touch each other: {groups}.\n\n"
                               f"Keep them as separate parts ({separate}), or merge them into one solid? "
                               "You can change this later in the Mesh view.")
        merge = box.addButton("Merge Into One", QMessageBox.ButtonRole.AcceptRole)
        split = box.addButton("Split to Parts", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(split)
        box.setEscapeButton(split)
        box.exec()
        return "merge" if box.clickedButton() is merge else "split"

    def save_fixed_mesh(self, job, done=None):
        """Asks where to save the checked and fixed copy of the mesh, then
        saves it (.stl, or .3mf with one object per part)."""
        multi = len(job.part_list) > 1
        ext = ".3mf" if multi else ".stl"
        start = os.path.join(os.path.dirname(job.path), f"{job.name} (fixed){ext}")
        filters = "3MF project (*.3mf);;STL mesh (*.stl)" if multi else "STL mesh (*.stl);;3MF project (*.3mf)"
        path, chosen = QFileDialog.getSaveFileName(self, "Save the fixed mesh", start, filters)
        if not path:
            return False
        if not path.lower().endswith((".stl", ".3mf")):
            path += ".3mf" if chosen.startswith("3MF") else ".stl"

        def finished(err):
            if done is not None:
                done(err, path)
            if err:
                QMessageBox.warning(self, APP_NAME, f"The fixed mesh couldn't be saved. {err}")
        self.model.save_fixed_mesh(job, os.path.abspath(path), finished)
        return True

    def save_report(self, job):
        """Saves a text report about this file to send with a bug report."""
        start = os.path.join(os.path.expanduser("~"), bugreport.default_name(job))
        path, _ = QFileDialog.getSaveFileName(self, "Save a report to send", start, "Text file (*.txt)")
        if not path:
            return
        if not path.lower().endswith(".txt"):
            path += ".txt"
        try:
            bugreport.save(job, self.settings, path)
        except OSError as e:
            QMessageBox.warning(self, APP_NAME, f"The report couldn't be saved ({e.strerror or e}).")
            return
        QMessageBox.information(
            self, APP_NAME,
            f"The report was saved as “{os.path.basename(path)}”.\n\nSend it with a short description of what "
            f"happened, by opening an issue at {REPO_URL}/issues (attach the file), or to whoever gave you the app. "
            "It has the file's name and its check results, but not its folder or your user name, and not the "
            "mesh itself.")
        system.reveal([path])

    def open_files(self):
        exts = " ".join(f"*.{e} *.{e.upper()}" for e in sorted(ACCEPTED_EXTENSIONS))
        files, _ = QFileDialog.getOpenFileNames(self, "Choose mesh files (STL, 3MF, OBJ, PLY) to convert",
                                                self._last_dir(), f"Meshes ({exts});;All files (*)")
        if files:
            self.model.add(files)

    def open_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Choose a folder with mesh files", self._last_dir())
        if d:
            self.model.add([d])

    def _last_dir(self):
        j = self.model.selected_job
        return os.path.dirname(j.path) if j else os.path.expanduser("~")

    def show_export(self, jobs):
        if self.export_dialog is not None:
            return
        d = ExportDialog(self.model, self.settings, jobs, self)
        self.export_dialog = d
        d.finished.connect(self._export_closed)
        d.open()

    def _export_closed(self, _):
        d, self.export_dialog = self.export_dialog, None
        if d is not None:
            d.deleteLater()

    def show_preferences(self):
        PreferencesDialog(self.settings, self.updates, self).exec()

    def show_about(self):
        QMessageBox.about(self, f"About {APP_NAME}",
                          f"<b>{APP_NAME}</b> {__version__}<br>STL / 3MF / OBJ / PLY → editable STEP<br><br>"
                          "Copyright © 2026 Tuna-The-First, Duvdivan and Claude.<br>"
                          "Free software under the GNU Affero General Public License v3, with no warranty.<br><br>"
                          f'Source code: <a href="{REPO_URL}">{REPO_URL}</a><br><br>'
                          f'Free to use. If it saves you time, you can <a href="{SUPPORT_URL}">buy me a coffee ☕</a>: '
                          "it helps pay for the AI credits that go into building and improving it.")

    def show_licenses(self):
        for name in ("THIRD_PARTY_NOTICES.md", "LICENSE"):
            p = system.bundled_doc(name)
            if p:
                system.open_path(p)
                return
        system.open_url(REPO_URL)

    # ------------------------------------------------------------------ updates

    def check_for_updates(self):
        dlg = UpdateDialog(self.updates, self)
        if self.updates.state not in ("available", "downloading", "ready"):
            self.updates.check()
        dlg.exec()

    def _updates_changed(self):
        v = self.updates.available_version
        self.sidebar.header.set_update_available(v)
        self.sidebar.update_banner.set_version(v)

    def _install_update(self):
        n = len(self.model.unexported_jobs)
        if n and not os.environ.get("MMM_SELFTEST"):
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(APP_NAME)
            box.setText("Install the update without exporting?" if n == 1 else f"Install the update without exporting {n} files?")
            box.setInformativeText(f"{'A converted file hasn' if n == 1 else f'{n} converted files haven'}'t been exported yet. "
                                   "The app restarts to install the update, so you'd have to convert "
                                   f"{'it' if n == 1 else 'them'} again.")
            cancel = box.addButton("Not Now", QMessageBox.ButtonRole.RejectRole)
            go = box.addButton("Install Anyway", QMessageBox.ButtonRole.DestructiveRole)
            box.setDefaultButton(cancel)
            box.exec()
            if box.clickedButton() is not go:
                return
        if self.updates.install():
            self.updating = True
            QTimer.singleShot(200, self._quit_for_update)

    def _quit_for_update(self):
        for w in QApplication.topLevelWidgets():
            if w is not self and w.isVisible():
                w.close()
        self.close()

    # ------------------------------------------------------------------ settings / theme

    def _setting_changed(self, key):
        if key in ("accent", "theme"):
            self.apply_theme()
        if key in ("plateLayout",):
            self.preview.preview.refresh()

    def apply_theme(self):
        """Apply the current theme and accent everywhere, live (no restart): widgets that
        were given a theme colour take the new theme's one (retheme)."""
        app = QApplication.instance()
        theme.install(app, self.settings)
        for w in app.allWidgets():
            if hasattr(w, "retheme"):
                w.retheme()
        self.sidebar.refresh_theme()
        self.settings_panel.refresh_theme()
        self.preview.preview.refresh()
        for w in app.allWidgets():
            w.update()

    def _system_scheme_changed(self, *_):
        if self.settings.theme == "system":
            self.apply_theme()

    # ------------------------------------------------------------------ drag and drop

    @staticmethod
    def _dropped_paths(e):
        md = e.mimeData()
        if not md.hasUrls():
            return []
        return [u.toLocalFile() for u in md.urls() if u.isLocalFile()]

    def dragEnterEvent(self, e):
        if self._dropped_paths(e):
            e.acceptProposedAction()
            self.sidebar.drop.set_targeted(True)

    def dragMoveEvent(self, e):
        if self._dropped_paths(e):
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.sidebar.drop.set_targeted(False)

    def dropEvent(self, e):
        self.sidebar.drop.set_targeted(False)
        paths = self._dropped_paths(e)
        if paths:
            e.acceptProposedAction()
            QTimer.singleShot(0, lambda: self.model.add(paths))

    # ------------------------------------------------------------------ window

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_banner()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_banner()

    def _place_banner(self):
        b = self.engine_banner
        b.adjustSize()
        b.move(max(0, (self.preview.width() - b.width()) // 2), 12)
        b.raise_()

    def add_from_other_instance(self, paths):
        if paths:
            self.model.add(paths)
        self.setWindowState((self.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive)
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e):
        if not self.updating and not os.environ.get("MMM_SELFTEST"):
            n = len(self.model.unexported_jobs)
            if n:
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Icon.Warning)
                box.setWindowTitle(APP_NAME)
                box.setText("Quit without exporting?" if n == 1 else f"Quit without exporting {n} files?")
                box.setInformativeText(f"{'A converted file hasn' if n == 1 else f'{n} converted files haven'}'t been "
                                       f"exported yet. If you quit now, you'll have to convert {'it' if n == 1 else 'them'} again.")
                cancel = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
                go = box.addButton("Quit Anyway", QMessageBox.ButtonRole.DestructiveRole)
                box.setDefaultButton(cancel)
                box.exec()
                if box.clickedButton() is not go:
                    e.ignore()
                    return
        self.preview.viewport.cleanup()
        self.model.shutdown()
        self.settings.sync()
        e.accept()
        QApplication.instance().quit()
