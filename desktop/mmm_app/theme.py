"""Visual language shared with the Mac app: near-black surfaces, hairline
borders, one vivid accent."""
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


ACCENT_COLORS = {
    "cyan": rgbf(0.13, 0.83, 0.93),
    "mint": rgbf(0.20, 0.93, 0.68),
    "violet": rgbf(0.62, 0.45, 1.00),
    "orange": rgbf(1.00, 0.55, 0.18),
    "pink": rgbf(1.00, 0.33, 0.62),
    "lime": rgbf(0.70, 0.95, 0.20),
}

BG = rgbf(0.039, 0.043, 0.055)
PANEL = rgbf(0.063, 0.070, 0.086)
CARD = rgbf(0.090, 0.098, 0.118)
CARD_HOVER = rgbf(0.115, 0.125, 0.150)
STROKE = white(0.07)
STROKE_STRONG = white(0.14)
TEXT = white(0.93)
TEXT_DIM = white(0.58)
TEXT_FAINT = white(0.36)
GOOD = rgbf(0.30, 0.88, 0.55)
WARN = rgbf(1.00, 0.75, 0.25)
BAD = rgbf(1.00, 0.38, 0.40)
VIEWPORT_TOP = rgbf(0.085, 0.092, 0.112)
VIEWPORT_BOTTOM = rgbf(0.035, 0.038, 0.048)

_accent = QColor(ACCENT_COLORS["cyan"])


def accent():
    return QColor(_accent)


def set_accent(name):
    global _accent
    _accent = QColor(ACCENT_COLORS.get(name, ACCENT_COLORS["cyan"]))


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
    p.setColor(QPalette.ColorRole.HighlightedText, QColor(10, 11, 14))
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
    dark = "#0b0c0e"
    check = _asset("check", dark, 3.2)
    dot = _asset("dot", dark)
    chevron = _asset("chevron-down", TEXT_DIM.name(), 2.4)
    up = _asset("chevron-up", TEXT_DIM.name(), 2.4)
    down = _asset("chevron-down", TEXT_DIM.name(), 2.4)
    return f"""
    QWidget {{ color: {css(TEXT)}; }}
    QToolTip {{ background: {css(CARD_HOVER)}; color: {css(TEXT)}; border: 1px solid {css(STROKE_STRONG)};
                padding: 4px 6px; border-radius: 6px; }}
    QMenuBar {{ background: {css(PANEL)}; color: {css(TEXT)}; border-bottom: 1px solid {css(STROKE)}; }}
    QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
    QMenuBar::item:selected {{ background: {css(white(0.08))}; border-radius: 5px; }}
    QMenu {{ background: {css(CARD)}; color: {css(TEXT)}; border: 1px solid {css(STROKE_STRONG)}; padding: 4px; }}
    QMenu::item {{ padding: 5px 22px 5px 22px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {css(with_alpha(a, 0.22))}; }}
    QMenu::item:disabled {{ color: {css(TEXT_FAINT)}; }}
    QMenu::separator {{ height: 1px; background: {css(STROKE_STRONG)}; margin: 4px 6px; }}
    QMenu::indicator {{ width: 12px; height: 12px; left: 5px; }}
    QMenu::indicator:checked {{ image: url("{_asset('check', a.name(), 3.0)}"); }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {css(white(0.14))}; border-radius: 3px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {css(white(0.14))}; border-radius: 3px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
    QLineEdit, QSpinBox, QDoubleSpinBox {{
        background: {css(white(0.06))}; border: 1px solid {css(STROKE_STRONG)}; border-radius: 6px;
        padding: 4px 6px; color: {css(TEXT)}; selection-background-color: {css(with_alpha(a, 0.45))};
        font-size: 12px; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {css(with_alpha(a, 0.8))}; }}
    QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right;
        width: 16px; border: none; }}
    QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right;
        width: 16px; border: none; }}
    QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url("{up}"); width: 9px; height: 9px; }}
    QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url("{down}"); width: 9px; height: 9px; }}
    QComboBox {{ background: {css(white(0.06))}; border: 1px solid {css(STROKE_STRONG)}; border-radius: 6px;
        padding: 3px 8px; color: {css(TEXT)}; font-size: 12px; min-height: 18px; }}
    QComboBox:hover {{ background: {css(white(0.09))}; }}
    QComboBox::drop-down {{ border: none; width: 18px; }}
    QComboBox::down-arrow {{ image: url("{chevron}"); width: 10px; height: 10px; }}
    QComboBox QAbstractItemView {{ background: {css(CARD)}; border: 1px solid {css(STROKE_STRONG)};
        selection-background-color: {css(with_alpha(a, 0.25))}; color: {css(TEXT)}; outline: none; padding: 2px; }}
    QCheckBox, QRadioButton {{ spacing: 7px; font-size: 12px; color: {css(TEXT)}; background: transparent; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {css(TEXT_FAINT)}; }}
    QCheckBox::indicator {{ width: 14px; height: 14px; border-radius: 4px; border: 1px solid {css(white(0.28))};
        background: {css(white(0.05))}; }}
    QCheckBox::indicator:hover {{ border: 1px solid {css(white(0.45))}; }}
    QCheckBox::indicator:checked {{ background: {css(a)}; border: 1px solid {css(a)}; image: url("{check}"); }}
    QCheckBox::indicator:disabled {{ border: 1px solid {css(white(0.12))}; }}
    QCheckBox::indicator:checked:disabled {{ background: {css(with_alpha(a, 0.35))}; }}
    QRadioButton::indicator {{ width: 14px; height: 14px; border-radius: 7px; border: 1px solid {css(white(0.28))};
        background: {css(white(0.05))}; }}
    QRadioButton::indicator:checked {{ background: {css(a)}; border: 1px solid {css(a)}; image: url("{dot}"); }}
    QSlider::groove:horizontal {{ height: 4px; background: {css(white(0.1))}; border-radius: 2px; }}
    QSlider::sub-page:horizontal {{ background: {css(a)}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ background: #f2f3f5; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
        border: 1px solid {css(white(0.3))}; }}
    QProgressBar {{ background: {css(white(0.07))}; border: none; border-radius: 3px; height: 6px; text-align: center;
        color: transparent; }}
    QProgressBar::chunk {{ background: {css(a)}; border-radius: 3px; }}
    QDialog {{ background: {css(PANEL)}; }}
    QMessageBox {{ background: {css(PANEL)}; }}
    QMessageBox QLabel {{ color: {css(TEXT)}; }}
    QMessageBox QPushButton, QDialogButtonBox QPushButton {{ background: {css(white(0.06))}; border: 1px solid {css(STROKE_STRONG)};
        border-radius: 7px; padding: 5px 14px; color: {css(TEXT)}; min-width: 70px; }}
    QMessageBox QPushButton:hover, QDialogButtonBox QPushButton:hover {{ background: {css(white(0.11))}; }}
    QMessageBox QPushButton:default {{ background: {css(a)}; color: {dark}; border: 1px solid {css(a)}; }}
    QTextBrowser, QPlainTextEdit {{ background: {css(CARD)}; border: 1px solid {css(STROKE)}; border-radius: 8px;
        color: {css(TEXT)}; padding: 6px; }}
    QGroupBox {{ border: 1px solid {css(STROKE)}; border-radius: 10px; margin-top: 18px; padding: 10px;
        background: {css(CARD)}; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 4px; top: 0px; color: {css(TEXT_FAINT)};
        font-size: 10px; font-weight: 600; }}
    """
