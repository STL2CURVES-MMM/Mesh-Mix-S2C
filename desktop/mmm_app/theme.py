"""Visual language shared with the Mac app: calm surfaces, hairline borders,
one accent. Four themes (Dark, the default; Light; Original, 1.6.0's look; and
Match system, which follows the computer), all with the same layout and corners."""
import atexit
import os
import shutil
import tempfile

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette

from . import icons


def rgbf(r, g, b, a=1.0):
    return QColor.fromRgbF(r, g, b, a)


def white(a):
    return QColor.fromRgbF(1, 1, 1, a)


def hexc(h, a=1.0):
    c = QColor(h)
    c.setAlphaF(a)
    return c


ACCENT_COLORS = {
    "matte": hexc("#7FB4D9"),
    "cyan": rgbf(0.13, 0.83, 0.93),
    "mint": rgbf(0.20, 0.93, 0.68),
    "violet": rgbf(0.62, 0.45, 1.00),
    "orange": rgbf(1.00, 0.55, 0.18),
    "pink": rgbf(1.00, 0.33, 0.62),
    "lime": rgbf(0.70, 0.95, 0.20),
}
# accents for text, outlines and thin lines on a light background, where the colours
# above are too pale (matte blue's own darker shade; the others with about a third black)
LIGHT_ACCENTS = {"matte": hexc("#3F7FB3")}

# Every colour of a theme. Widgets read these as theme.NAME when they paint, so
# apply_theme() changing them here changes the whole app (with a repaint).
THEMES = {
    "dark": dict(
        light=False, BG=hexc("#0E1116"), PANEL=hexc("#141920"), CARD=hexc("#1A2029"),
        CARD_HOVER=hexc("#212936"), STROKE=white(0.08), STROKE_STRONG=white(0.15),
        TEXT=hexc("#E8EDF3"), TEXT_DIM=hexc("#9AA6B4"), TEXT_FAINT=hexc("#66717F"),
        GOOD=hexc("#5CC98A"), WARN=hexc("#E3B04F"), BAD=hexc("#E06B6E"),
        VIEWPORT_TOP=hexc("#1A2029"), VIEWPORT_BOTTOM=hexc("#0B0E12")),
    "light": dict(
        light=True, BG=hexc("#EEF1F5"), PANEL=hexc("#F6F8FA"), CARD=hexc("#FFFFFF"),
        CARD_HOVER=hexc("#F1F4F8"), STROKE=rgbf(0, 0, 0, 0.08), STROKE_STRONG=rgbf(0, 0, 0, 0.15),
        TEXT=hexc("#1A1F26"), TEXT_DIM=hexc("#5A6573"), TEXT_FAINT=hexc("#8B95A2"),
        GOOD=hexc("#1E9E57"), WARN=hexc("#B7791F"), BAD=hexc("#D64545"),
        VIEWPORT_TOP=hexc("#F4F6F9"), VIEWPORT_BOTTOM=hexc("#DCE2E9")),
    # Manifold Mesh Mix 1.6.0's look, colour for colour
    "original": dict(
        light=False, BG=rgbf(0.039, 0.043, 0.055), PANEL=rgbf(0.063, 0.070, 0.086),
        CARD=rgbf(0.090, 0.098, 0.118), CARD_HOVER=rgbf(0.115, 0.125, 0.150), STROKE=white(0.07),
        STROKE_STRONG=white(0.14), TEXT=white(0.93), TEXT_DIM=white(0.58), TEXT_FAINT=white(0.36),
        GOOD=rgbf(0.30, 0.88, 0.55), WARN=rgbf(1.00, 0.75, 0.25), BAD=rgbf(1.00, 0.38, 0.40),
        VIEWPORT_TOP=rgbf(0.085, 0.092, 0.112), VIEWPORT_BOTTOM=rgbf(0.035, 0.038, 0.048)),
}
THEME_NAMES = ("dark", "light", "original", "system")

# Corner radii, the same in every theme: softened squares rather than pills
R_BIG = 8       # cards, panels, the sidebar, dialogs
R_MED = 6       # banners, pills, list rows, overlays
R_SMALL = 4     # buttons, combo boxes, inputs, menus, checkboxes
R_TINY = 2      # scroll bars, sliders, progress bars

BG = PANEL = CARD = CARD_HOVER = STROKE = STROKE_STRONG = None
TEXT = TEXT_DIM = TEXT_FAINT = GOOD = WARN = BAD = VIEWPORT_TOP = VIEWPORT_BOTTOM = None
_light = False
_theme = "dark"


def system_dark():
    """Whether the computer is set to dark mode (Qt 6.5+'s colour scheme; when the
    system doesn't say, the default palette's window colour decides)."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance()
    if app is None:
        return True
    scheme = app.styleHints().colorScheme()
    if scheme == Qt.ColorScheme.Unknown:
        return app.style().standardPalette().color(QPalette.ColorRole.Window).lightnessF() < 0.5
    return scheme == Qt.ColorScheme.Dark


def install(app, settings):
    """Apply the settings' theme and accent to the whole app: colours, palette, style
    sheet. Called at start and whenever either changes (or the system's dark mode
    flips while Match system is chosen); the caller repaints."""
    apply_theme(settings.theme, system_dark() if settings.theme == "system" else True)
    set_accent(settings.accent)
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())


def resolve(name, system_dark=True):
    """The theme a setting stands for: "system" follows the computer's dark or light
    setting; anything unknown falls back to Dark."""
    if name == "system":
        return "dark" if system_dark else "light"
    return name if name in THEMES else "dark"


def apply_theme(name, system_dark=True):
    """Make theme ``name`` the current one (its colours become theme.BG, theme.TEXT...).
    Callers then rebuild the palette and style sheet and repaint (see app.py)."""
    global _light, _theme
    _theme = resolve(name, system_dark)
    for k, v in THEMES[_theme].items():
        if k == "light":
            _light = v
        else:
            globals()[k] = QColor(v)
    icons.KNOCKOUT = "#ffffff" if _light else "#0b0c0e"


def current():
    return _theme


def is_light():
    return _light


# the colours a widget can name instead of copying (see token_of)
TOKENS = ("TEXT", "TEXT_DIM", "TEXT_FAINT", "GOOD", "WARN", "BAD", "STROKE", "STROKE_STRONG", "BG",
          "PANEL", "CARD", "CARD_HOVER")


def token_of(c):
    """Which theme colour c is ("TEXT", "accent"...; None for any other colour), so a
    widget that was given it can take the new theme's one when the theme changes."""
    if c is None:
        return None
    for n in TOKENS:
        if globals()[n] == c:
            return n
    return "accent" if c == accent() else None


def color_of(token, fallback):
    """The current theme's colour for a token from token_of (fallback when there is none)."""
    if token == "accent":
        return accent()
    return QColor(globals()[token]) if token else fallback


def ink(a):
    """A hairline or wash of the text's own colour: white on dark themes, black on Light
    (the dark themes' white overlays would vanish on a light background)."""
    return rgbf(0, 0, 0, a) if _light else white(a)


apply_theme("dark")

_accent_name = "matte"


def _for_theme(name):
    c = QColor(ACCENT_COLORS[name])
    if _light:
        c = QColor(LIGHT_ACCENTS.get(name) or QColor.fromRgbF(c.redF() * 0.66, c.greenF() * 0.66, c.blueF() * 0.66))
    return c


def accent():
    return _for_theme(_accent_name)


def set_accent(name):
    global _accent_name
    _accent_name = name if name in ACCENT_COLORS else "matte"


def on_accent():
    """Text and check marks drawn on an accent fill: near-black on the dark themes'
    light accents, white on Light's deeper ones."""
    return QColor(255, 255, 255) if _light else QColor(10, 11, 14)


def auto_pick():
    """The color that marks the preset Auto chose: one that stands apart
    from the accent color."""
    return _for_theme("mint" if _accent_name in ("violet", "pink") else "violet")


def with_alpha(c, a):
    c = QColor(c)
    c.setAlphaF(max(0.0, min(1.0, a)))
    return c


def css(c):
    """QColor -> CSS rgba() for style sheets."""
    return f"rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})"


def font(size=12.0, weight=QFont.Weight.Normal, mono=False):
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont) if mono else QFont()
    f.setPixelSize(max(6, round(size)))
    f.setWeight(weight)
    return f


SEMIBOLD = QFont.Weight.DemiBold
BOLD = QFont.Weight.Bold
MEDIUM = QFont.Weight.Medium


def palette():
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: PANEL, QPalette.ColorRole.WindowText: TEXT,
        QPalette.ColorRole.Base: CARD, QPalette.ColorRole.AlternateBase: CARD_HOVER,
        QPalette.ColorRole.Text: TEXT, QPalette.ColorRole.Button: CARD,
        QPalette.ColorRole.ButtonText: TEXT, QPalette.ColorRole.ToolTipBase: CARD_HOVER,
        QPalette.ColorRole.ToolTipText: TEXT, QPalette.ColorRole.PlaceholderText: TEXT_FAINT,
        QPalette.ColorRole.BrightText: TEXT, QPalette.ColorRole.Mid: STROKE_STRONG,
    }
    for role, c in roles.items():
        p.setColor(role, c)
    p.setColor(QPalette.ColorRole.Highlight, accent())
    p.setColor(QPalette.ColorRole.HighlightedText, on_accent())
    p.setColor(QPalette.ColorRole.Link, accent())
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, TEXT_FAINT)
    return p


_asset_dir = None


def _assets():
    """Check marks and arrows for the style sheet (it needs image files)."""
    global _asset_dir
    if _asset_dir is None:
        _asset_dir = tempfile.mkdtemp(prefix="ManifoldMeshMix-ui-")
        atexit.register(shutil.rmtree, _asset_dir, True)
    return _asset_dir


def _asset(name, color, weight=2.0):
    key = f"{name}-{color.lstrip('#')}-{weight:g}.svg"
    path = os.path.join(_assets(), key)
    if not os.path.exists(path):
        icons.write_svg(path, name, color, weight)
    return path.replace("\\", "/")


def stylesheet():
    a = accent()
    dark = on_accent().name()
    check = _asset("check", dark, 3.2)
    dot = _asset("dot", dark)
    chevron = _asset("chevron-down", TEXT_DIM.name(), 2.4)
    up = _asset("chevron-up", TEXT_DIM.name(), 2.4)
    down = _asset("chevron-down", TEXT_DIM.name(), 2.4)
    return f"""
    QWidget {{ color: {css(TEXT)}; }}
    QToolTip {{ background: {css(CARD_HOVER)}; color: {css(TEXT)}; border: 1px solid {css(STROKE_STRONG)};
                padding: 4px 6px; border-radius: {R_SMALL}px; }}
    QMenuBar {{ background: {css(PANEL)}; color: {css(TEXT)}; border-bottom: 1px solid {css(STROKE)}; }}
    QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
    QMenuBar::item:selected {{ background: {css(ink(0.08))}; border-radius: {R_SMALL}px; }}
    QMenu {{ background: {css(CARD)}; color: {css(TEXT)}; border: 1px solid {css(STROKE_STRONG)}; padding: 4px; }}
    QMenu::item {{ padding: 5px 22px 5px 22px; border-radius: {R_SMALL}px; }}
    QMenu::item:selected {{ background: {css(with_alpha(a, 0.22))}; }}
    QMenu::item:disabled {{ color: {css(TEXT_FAINT)}; }}
    QMenu::separator {{ height: 1px; background: {css(STROKE_STRONG)}; margin: 4px 6px; }}
    QMenu::indicator {{ width: 12px; height: 12px; left: 5px; }}
    QMenu::indicator:checked {{ image: url("{_asset('check', a.name(), 3.0)}"); }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {css(ink(0.14))}; border-radius: {R_TINY}px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {css(ink(0.14))}; border-radius: {R_TINY}px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QLineEdit, QSpinBox, QDoubleSpinBox {{
        background: {css(ink(0.06))}; border: 1px solid {css(STROKE_STRONG)}; border-radius: {R_SMALL}px;
        padding: 4px 6px; color: {css(TEXT)}; selection-background-color: {css(with_alpha(a, 0.45))};
        font-size: 12px; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {css(with_alpha(a, 0.8))}; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
        width: 16px; border: none; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
        width: 16px; border: none; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 9px; height: 9px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{down}"); width: 9px; height: 9px; }}
    QComboBox {{ background: {css(ink(0.06))}; border: 1px solid {css(STROKE_STRONG)}; border-radius: {R_SMALL}px;
        padding: 3px 8px; color: {css(TEXT)}; font-size: 12px; min-height: 18px; }}
    QComboBox:hover {{ background: {css(ink(0.09))}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox::down-arrow {{ image: url("{chevron}"); width: 10px; height: 10px; }}
    QComboBox QAbstractItemView {{ background: {css(CARD)}; border: 1px solid {css(STROKE_STRONG)};
        selection-background-color: {css(with_alpha(a, 0.25))}; color: {css(TEXT)}; outline: none; padding: 2px; }}
    QCheckBox, QRadioButton {{ spacing: 7px; font-size: 12px; color: {css(TEXT)}; background: transparent; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {css(TEXT_FAINT)}; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: {R_TINY + 1}px; border: 1px solid {css(ink(0.28))};
        background: {css(ink(0.05))}; }}
    QCheckBox::indicator:hover {{ border: 1px solid {css(ink(0.45))}; }}
    QCheckBox::indicator:checked {{ background: {css(a)}; border: 1px solid {css(a)}; image: url("{check}"); }}
    QCheckBox::indicator:disabled {{ border: 1px solid {css(ink(0.12))}; }}
    QCheckBox::indicator:checked:disabled {{ background: {css(with_alpha(a, 0.35))}; }}
    QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px; border: 1px solid {css(ink(0.28))};
        background: {css(ink(0.05))}; }}
    QRadioButton::indicator:checked {{ background: {css(a)}; border: 1px solid {css(a)}; image: url("{dot}"); }}
    QSlider::groove:horizontal {{ height: 4px; background: {css(ink(0.1))}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {css(a)}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: {"#ffffff" if _light else "#f2f3f5"}; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
        border: 1px solid {css(ink(0.3))}; }}
    QProgressBar {{ background: {css(ink(0.07))}; border: none; border-radius: {R_TINY}px; height: 6px; text-align: center;
        color: transparent; }}
    QProgressBar::chunk {{ background: {css(a)}; border-radius: {R_TINY}px; }}
    QDialog {{ background: {css(PANEL)}; }}
    QMessageBox {{ background: {css(PANEL)}; }}
    QMessageBox QLabel {{ color: {css(TEXT)}; }}
    QMessageBox QPushButton, QDialogButtonBox QPushButton {{ background: {css(ink(0.06))}; border: 1px solid {css(STROKE_STRONG)};
        border-radius: {R_SMALL}px; padding: 5px 14px; color: {css(TEXT)}; min-width: 70px; }}
    QMessageBox QPushButton:hover, QDialogButtonBox QPushButton:hover {{ background: {css(ink(0.11))}; }}
    QMessageBox QPushButton:default {{ background: {css(a)}; color: {dark}; border: 1px solid {css(a)}; }}
    QTextBrowser, QPlainTextEdit {{ background: {css(CARD)}; border: 1px solid {css(STROKE)}; border-radius: {R_MED}px;
        color: {css(TEXT)}; padding: 6px; }}
    QGroupBox {{ border: 1px solid {css(STROKE)}; border-radius: {R_BIG}px; margin-top: 18px; padding: 10px;
        background: {css(CARD)}; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 4px; top: 0px; color: {css(TEXT_FAINT)};
        font-size: 10px; font-weight: 600; }}
    """
