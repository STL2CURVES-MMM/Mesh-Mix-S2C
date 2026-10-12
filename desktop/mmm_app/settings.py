"""User settings and the choices behind them (same keys and defaults as the Mac app)."""
import os

from PySide6.QtCore import QObject, QSettings, Signal


class Choice:
    """One option of a setting: stored value, title and help text."""

    def __init__(self, value, title, subtitle="", hint="", icon=""):
        self.value = value
        self.title = title
        self.subtitle = subtitle
        self.hint = hint
        self.icon = icon


PRESETS = [
    Choice("auto", "Auto", "Chooses CAD or Smooth for each part automatically",
           "Higher quality fits surfaces more tightly and adds detail, but takes longer.", "wand"),
    Choice("cad", "CAD", "For mechanical parts: flat faces, holes, cylinders, cones and fillets you can select and edit",
           "Higher quality makes the faces follow the mesh more closely and adds detail to curved freeform areas, but takes longer.",
           "cube-transparent"),
    Choice("smooth", "Smooth", "For organic shapes, sculpts and scans: smooth surfaces that follow the shape",
           "Higher quality uses more surfaces, so finer details are kept, but takes longer and makes bigger files.", "drop"),
    Choice("precise", "Precise",
           "Follows the mesh most closely: tighter CAD faces for mechanical parts, more detailed smooth surfaces for organic ones (slower)",
           "Higher quality makes the fit even tighter, but takes longer.", "scope"),
]

OUTPUT_MODES = [
    Choice("subfolder", "Subfolder next to the original"),
    Choice("sameFolder", "Same folder as the original"),
    Choice("custom", "A folder I choose"),
]

PLATE_LAYOUTS = [
    Choice("parts", "One of each part", "Each different part once, in its original orientation (copies on the plate are skipped)."),
    Choice("plate", "Whole build plate", "Every object exactly where it sits on the slicer's build plate."),
]

REPAIR_LEVELS = [Choice("off", "Off"), Choice("auto", "Automatic"), Choice("aggressive", "Thorough")]

UNITS = [Choice("mm", "Millimeters"), Choice("cm", "Centimeters"), Choice("m", "Meters"), Choice("in", "Inches")]

MAX_FACES = [Choice(0, "None"), Choice(1000, "1,000"), Choice(4000, "4,000"), Choice(10000, "10,000"), Choice(25000, "25,000")]

ACCENTS = [
    Choice("cyan", "Cyan"), Choice("mint", "Mint"), Choice("violet", "Violet"),
    Choice("orange", "Orange"), Choice("pink", "Pink"), Choice("lime", "Lime"),
]


def choice(options, value):
    for c in options:
        if c.value == value:
            return c
    return options[0]


def preset_title(value):
    """Display name of a preset the engine reports ("mixed" when some parts
    of a file used CAD and others Smooth)."""
    if value == "mixed":
        return "CAD + Smooth"
    for c in PRESETS:
        if c.value == value:
            return c.title
    return (value or "").capitalize()


def _to_bool(v, default):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        if v.lower() in ("true", "1", "yes"):
            return True
        if v.lower() in ("false", "0", "no"):
            return False
    return default


def _to_number(kind, v, default):
    try:
        return kind(v)
    except (TypeError, ValueError):
        return default


class Field:
    """A setting stored in QSettings; assigning it saves it and emits
    Settings.changed(name)."""

    def __init__(self, default, allowed=None, lo=None, hi=None):
        self.default = default
        self.kind = type(default)
        self.allowed = allowed
        self.lo = lo
        self.hi = hi

    def __set_name__(self, owner, name):
        self.name = name

    def coerce(self, v):
        if self.kind is bool:
            v = _to_bool(v, self.default)
        elif self.kind in (int, float):
            v = _to_number(self.kind, v, self.default)
            if self.kind is float and v != v:  # NaN
                v = self.default
            if self.lo is not None:
                v = max(self.lo, v)
            if self.hi is not None:
                v = min(self.hi, v)
        else:
            v = "" if v is None else str(v)
        if self.allowed is not None and v not in self.allowed:
            v = self.default
        return v

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        return obj._values[self.name]

    def __set__(self, obj, value):
        value = self.coerce(value)
        if obj._values.get(self.name) == value:
            return
        obj._values[self.name] = value
        obj._store.setValue(self.name, value)
        obj.changed.emit(self.name)


def _values(options):
    return {c.value for c in options}


class Settings(QObject):
    """User settings, kept between launches (the registry on Windows, a
    config file on Linux). The scripted self-test gets its own throwaway
    store, so it never changes the user's settings."""

    changed = Signal(str)

    preset = Field("auto", _values(PRESETS))
    quality = Field(0.7, lo=0.0, hi=1.0)
    repair = Field("auto", _values(REPAIR_LEVELS))
    removeDebris = Field(True)
    unit = Field("mm", _values(UNITS))
    useCustomTolerance = Field(False)
    customTolerance = Field(0.05, lo=0.0)
    maxFaces = Field(0, lo=0)
    outputMode = Field("subfolder", _values(OUTPUT_MODES))
    subfolderName = Field("STEP Files")
    outputFolder = Field("")
    plateLayout = Field("parts", _values(PLATE_LAYOUTS))
    replaceExisting = Field(False)
    parallelJobs = Field(2, lo=1, hi=6)
    accent = Field("cyan", _values(ACCENTS))
    convertOnDrop = Field(False)
    revealWhenDone = Field(False)
    # 3MF projects: one STEP file per part (like "Split to parts" in a slicer)
    splitParts = Field(True)
    # STEP view: give every CAD face its own color (off = one color, like a CAD program)
    colorFaces = Field(False)
    # Look for a newer version once when the app starts (installing always needs a click)
    checkUpdates = Field(True)

    def __init__(self, store=None, parent=None):
        super().__init__(parent)
        if store is None:
            store = self.default_store()
        self._store = store
        self._values = {}
        for name, f in self.fields().items():
            self._values[name] = f.coerce(store.value(name, f.default))

    @staticmethod
    def default_store():
        test_dir = os.environ.get("MMM_SELFTEST") or os.environ.get("MMM_SETTINGS_FILE")
        if test_dir:
            path = os.environ.get("MMM_SETTINGS_FILE") or os.path.join(test_dir, "settings.ini")
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            s = QSettings(path, QSettings.Format.IniFormat)
            if os.environ.get("MMM_SELFTEST"):
                s.clear()
            return s
        return QSettings("ManifoldMeshMix", "Manifold Mesh Mix")

    @classmethod
    def fields(cls):
        return {k: v for k, v in vars(cls).items() if isinstance(v, Field)}

    def sync(self):
        self._store.sync()
