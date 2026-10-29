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

THEMES = [
    Choice("dark", "Dark"), Choice("light", "Light"), Choice("original", "Original"),
    Choice("system", "Match system"),
]

ACCENTS = [
    Choice("matte", "Matte blue"), Choice("cyan", "Cyan"), Choice("mint", "Mint"), Choice("violet", "Violet"),
    Choice("orange", "Orange"), Choice("pink", "Pink"), Choice("lime", "Lime"),
]


# Auto quality (with the Auto preset): each file gets a quality from its
# triangle count. Small meshes are quick even near the top of the slider;
# very big ones get a faster setting, so they don't take hours or make huge
# STEP files. (triangles below, quality)
AUTO_QUALITY_STEPS = ((50_000, 0.9), (300_000, 0.8), (1_000_000, 0.7), (3_000_000, 0.6))
AUTO_QUALITY_BIGGEST = 0.5


def auto_quality(triangles):
    """The quality Auto picks for a mesh with this many triangles."""
    for limit, q in AUTO_QUALITY_STEPS:
        if triangles < limit:
            return q
    return AUTO_QUALITY_BIGGEST


def choice(options, value):
    for c in options:
        if c.value == value:
            return c
    return options[0]


# what the engine saved when neither CAD faces nor smooth surfaces followed a
# mesh closely enough: its own flat triangles (not a preset anyone picks)
FACETED_TITLE = "Flat faces"
FACETED_TIP = ("Saved as flat faces, exactly like the mesh: neither CAD faces nor smooth surfaces followed it "
               "closely enough, so Auto kept the mesh's own triangles as a valid solid. This isn't a preset you "
               "pick; a higher quality may give real surfaces.")


# result kinds that are another preset's faces: stl2curves' own sewn faces are CAD
KIND_ALIASES = {"stl2curves": "cad"}


def used_kinds(value, kinds=()):
    """The methods behind a reported preset: the engine's list when it sends
    one, else CAD and Smooth for "mixed" (what older engines meant by it)."""
    if kinds:
        return {KIND_ALIASES.get(k, k) for k in kinds}
    if value == "mixed":
        return {"cad", "smooth"}
    return {KIND_ALIASES.get(value, value)} if value else set()


def preset_title(value, kinds=()):
    """Display name of a preset the engine reports ("mixed" when the parts of
    a file used different methods; `kinds` names them when known)."""
    if value == "mixed":
        names = [preset_title(k) for k in ("cad", "smooth", "precise", "faceted") if k in used_kinds(value, kinds)]
        return " + ".join(names) or "Mixed"
    if value == "faceted":
        return FACETED_TITLE
    value = KIND_ALIASES.get(value, value)
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
    # with the Auto preset, pick the quality for each file (moving the
    # slider turns it off: then every file uses the slider's quality)
    autoQuality = Field(True)
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
    # Dark (the default), Light, Original (1.6.0's look) or Match system
    theme = Field("dark", _values(THEMES))
    # (Matte blue since 2.0; settings store only what someone changed, so an accent
    # chosen in 1.x is kept, and one never chosen becomes Matte blue)
    accent = Field("matte", _values(ACCENTS))
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

    @property
    def auto_quality_on(self):
        return self.preset == "auto" and self.autoQuality

    def quality_for(self, triangles):
        """The quality a file with this many triangles is converted at."""
        if self.auto_quality_on and triangles:
            return auto_quality(triangles)
        return self.quality

    @classmethod
    def fields(cls):
        return {k: v for k, v in vars(cls).items() if isinstance(v, Field)}

    def sync(self):
        self._store.sync()
