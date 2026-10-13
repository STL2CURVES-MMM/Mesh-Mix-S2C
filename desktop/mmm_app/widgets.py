"""Small custom-drawn controls that give the app the same look as the Mac version."""
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel, QLayout, QPushButton, QSizePolicy,
                               QVBoxLayout, QWidget)

from . import icons, theme
from .theme import MEDIUM, SEMIBOLD


def _dpr(w):
    return max(1.0, w.devicePixelRatioF())


def rounded(rect, r):
    p = QPainterPath()
    p.addRoundedRect(QRectF(rect), r, r)
    return p


class Card(QFrame):
    """Rounded card with a hairline border; highlighted cards get an accent border."""

    def __init__(self, parent=None, padding=12, radius=12, highlighted=False, accent=None, spacing=8, vertical=True):
        super().__init__(parent)
        self.radius = radius
        self.highlighted = highlighted
        self.accent = accent  # None: the theme accent
        self.fill = None
        self.border = None
        lay = QVBoxLayout(self) if vertical else QHBoxLayout(self)
        lay.setContentsMargins(padding, padding, padding, padding)
        lay.setSpacing(spacing)
        self.lay = lay

    def set_highlighted(self, on, accent=None):
        if on != self.highlighted or accent != self.accent:
            self.highlighted = on
            self.accent = accent
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        fill = self.fill or (theme.CARD_HOVER if self.highlighted else theme.CARD)
        if self.border is not None:
            border, width = self.border, 1.0
        elif self.highlighted:
            border, width = theme.with_alpha(self.accent or theme.accent(), 0.85), 1.2
        else:
            border, width = theme.STROKE, 1.0
        p.setPen(QPen(border, width))
        p.setBrush(fill)
        p.drawRoundedRect(r, self.radius, self.radius)


class SectionLabel(QLabel):
    """Small uppercase section label."""

    def __init__(self, text, parent=None):
        super().__init__(text.upper(), parent)
        f = theme.font(10, SEMIBOLD)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 1.2)
        self.setFont(f)
        self.setStyleSheet(f"color: {theme.css(theme.TEXT_FAINT)}; background: transparent;")


class Label(QLabel):
    def __init__(self, text="", size=12, weight=QFont.Weight.Normal, color=None, mono=False, wrap=False, parent=None):
        super().__init__(text, parent)
        self.setFont(theme.font(size, weight, mono))
        self.set_color(color or theme.TEXT)
        if wrap:
            self.setWordWrap(True)

    def set_color(self, c):
        self.setStyleSheet(f"color: {theme.css(c)}; background: transparent;")


class ElidedLabel(QWidget):
    """One line of text, cut off with "…" (at the end, middle or start) and
    with the whole text as a tooltip when it doesn't fit."""

    clicked = Signal()

    def __init__(self, text="", size=12, weight=QFont.Weight.Normal, color=None, mono=False,
                 mode=Qt.TextElideMode.ElideRight, parent=None, tooltip=None):
        super().__init__(parent)
        self._text = text
        self.color = color or theme.TEXT
        self.mode = mode
        self.extra_tooltip = tooltip
        self.setFont(theme.font(size, weight, mono))
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def text(self):
        return self._text

    def setText(self, t):
        if t != self._text:
            self._text = t
            self.updateGeometry()
            self.update()

    def set_color(self, c):
        self.color = c
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        # +3: room for underscores and descenders below the line
        return QSize(fm.horizontalAdvance(self._text) + 4, fm.height() + 3)

    def minimumSizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(min(40, fm.horizontalAdvance(self._text)), fm.height() + 3)

    def is_truncated(self):
        return QFontMetrics(self.font()).horizontalAdvance(self._text) > self.width() + 0.5

    def event(self, e):
        if e.type() == QEvent.Type.ToolTip:
            tip = self._text if self.is_truncated() else (self.extra_tooltip or "")
            self.setToolTip(tip)
        return super().event(e)

    def mousePressEvent(self, e):
        self.clicked.emit()
        super().mousePressEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setFont(self.font())
        p.setPen(self.color)
        fm = QFontMetrics(self.font())
        t = fm.elidedText(self._text, self.mode, self.width())
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, t)


class Pill(QWidget):
    """Rounded "pill" used for status and stats."""

    def __init__(self, text="", color=None, icon=None, filled=False, parent=None):
        super().__init__(parent)
        self._text = text
        self.color = color or theme.TEXT_DIM
        self.icon = icon
        self.filled = filled
        self.setFont(theme.font(10.5, SEMIBOLD))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set(self, text, color=None, icon=None):
        self._text = text
        if color is not None:
            self.color = color
        self.icon = icon
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = fm.horizontalAdvance(self._text) + 14 + (13 if self.icon else 0)
        return QSize(w, fm.height() + 6)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        rad = r.height() / 2
        if self.filled:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(self.color)
        else:
            p.setPen(QPen(theme.with_alpha(self.color, 0.25), 1))
            p.setBrush(theme.with_alpha(self.color, 0.12))
        p.drawRoundedRect(r, rad, rad)
        fg = QColor(10, 11, 14, 217) if self.filled else self.color
        x = 7
        if self.icon:
            pm = icons.pixmap(self.icon, fg, 9, _dpr(self), 3.0)
            p.drawPixmap(QPointF(x, (self.height() - 9) / 2), pm)
            x += 13
        p.setFont(self.font())
        p.setPen(fg)
        p.drawText(QRectF(x, 0, self.width() - x - 6, self.height()), Qt.AlignmentFlag.AlignVCenter, self._text)


class _Button(QPushButton):
    def __init__(self, text="", icon=None, parent=None):
        super().__init__(text, parent)
        self.icon_name = icon
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def set_icon_name(self, name):
        self.icon_name = name
        self.updateGeometry()
        self.update()

    def _content_width(self, fm, icon_size):
        w = fm.horizontalAdvance(self.text()) if self.text() else 0
        if self.icon_name:
            w += icon_size + (6 if self.text() else 0)
        return w

    def _draw_content(self, p, color, icon_size):
        fm = QFontMetrics(self.font())
        w = self._content_width(fm, icon_size)
        x = (self.width() - w) / 2
        if self.icon_name:
            pm = icons.pixmap(self.icon_name, color, icon_size, _dpr(self))
            p.drawPixmap(QPointF(x, (self.height() - icon_size) / 2), pm)
            x += icon_size + 6
        if self.text():
            p.setFont(self.font())
            p.setPen(color)
            avail = self.width() - x - 4
            p.drawText(QRectF(x, 0, max(0, avail), self.height()), Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(self.text(), Qt.TextElideMode.ElideRight, int(max(0, avail))))


class GhostButton(_Button):
    """Subtle button: faint fill, hairline border, dim text."""

    def __init__(self, text="", icon=None, tint=None, parent=None, checkable=False):
        super().__init__(text, icon, parent)
        self.tint = tint  # None: dim text color; "accent": the theme accent
        self.setFont(theme.font(12, MEDIUM))
        self.setCheckable(checkable)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def color(self):
        if self.tint == "accent" or (self.isCheckable() and self.isChecked()):
            c = theme.accent()
        else:
            c = self.tint or theme.TEXT_DIM
        if not self.isEnabled():
            c = theme.with_alpha(c, 0.35)
        return c

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(self._content_width(fm, 13) + 18, max(fm.height(), 13) + 10)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self.isCheckable() and self.isChecked():
            fill = theme.with_alpha(theme.accent(), 0.2 if self.isDown() else 0.14)
            border = theme.with_alpha(theme.accent(), 0.5)
        else:
            a = 0.12 if self.isDown() else (0.09 if self.underMouse() and self.isEnabled() else 0.05)
            fill, border = theme.white(a), theme.STROKE
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, 7, 7)
        self._draw_content(p, self.color(), 13)


class AccentButton(_Button):
    """Primary button in the accent color (prominent), or a tinted one."""

    def __init__(self, text="", icon=None, prominent=True, parent=None, expand=True):
        super().__init__(text, icon, parent)
        self.prominent = prominent
        self.setFont(theme.font(13, SEMIBOLD))
        self.setSizePolicy(QSizePolicy.Policy.Expanding if expand else QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_prominent(self, on):
        if on != self.prominent:
            self.prominent = on
            self.update()

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(self._content_width(fm, 14) + 30, fm.height() + 18)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        a = theme.accent()
        on = self.isEnabled()
        if self.prominent:
            alpha = (0.75 if self.isDown() else (0.9 if self.underMouse() else 1.0)) if on else 0.3
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.with_alpha(a, alpha))
            fg = QColor.fromRgbF(0, 0, 0, 0.88 if on else 0.5)
        else:
            p.setPen(QPen(theme.with_alpha(a, 0.35 if on else 0.15), 1))
            p.setBrush(theme.with_alpha(a, (0.22 if self.isDown() else (0.17 if self.underMouse() else 0.12)) if on else 0.05))
            fg = a if on else theme.with_alpha(a, 0.4)
        p.drawRoundedRect(r, 10, 10)
        self._draw_content(p, fg, 14)


class IconButton(QAbstractButton):
    """Small round icon button (row actions)."""

    def __init__(self, icon, tooltip, parent=None, size=22):
        super().__init__(parent)
        self.icon_name = icon
        self._size = size
        self.setToolTip(tooltip)
        self.setAccessibleName(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setFixedSize(size, size)
        self.color = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = 0.16 if self.isDown() else (0.12 if self.underMouse() else 0.07)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.white(a))
        p.drawEllipse(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5))
        s = round(self._size * 0.46)
        pm = icons.pixmap(self.icon_name, self.color or (theme.TEXT if self.underMouse() else theme.TEXT_DIM), s, _dpr(self), 2.6)
        p.drawPixmap(QPointF((self.width() - s) / 2, (self.height() - s) / 2), pm)


class IconLabel(QWidget):
    """Just an icon."""

    def __init__(self, icon, color, size=14, parent=None, weight=2.0):
        super().__init__(parent)
        self.icon_name = icon
        self.color = color
        self.size_ = size
        self.weight = weight
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    def set(self, icon=None, color=None):
        if icon:
            self.icon_name = icon
        if color is not None:
            self.color = color
        self.update()

    def paintEvent(self, _):
        if not self.icon_name:
            return
        p = QPainter(self)
        p.drawPixmap(0, 0, icons.pixmap(self.icon_name, self.color, self.size_, _dpr(self), self.weight))


class ThinProgress(QWidget):
    """Thin accent progress bar; indeterminate mode sweeps a highlight."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.value = 0.0
        self.indeterminate = False
        self.phase = 0.0
        self.setFixedHeight(4)
        self.timer = QTimer(self)
        self.timer.setInterval(30)
        self.timer.timeout.connect(self._tick)

    def set_value(self, v, indeterminate=False):
        self.value = max(0.0, min(1.0, v))
        if indeterminate != self.indeterminate:
            self.indeterminate = indeterminate
            if indeterminate:
                self.timer.start()
            else:
                self.timer.stop()
        self.update()

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def showEvent(self, e):
        if self.indeterminate:
            self.timer.start()
        super().showEvent(e)

    def _tick(self):
        self.phase = (self.phase + 0.025) % 1.0
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        clip = rounded(self.rect(), h / 2)
        p.setClipPath(clip)
        p.fillPath(clip, theme.white(0.07))
        a = theme.accent()
        if self.indeterminate:
            bw = w * 0.35
            x = (w * 1.35) * self.phase - bw
            g = QLinearGradient(x, 0, x + bw, 0)
            g.setColorAt(0, theme.with_alpha(a, 0))
            g.setColorAt(0.5, a)
            g.setColorAt(1, theme.with_alpha(a, 0))
            p.fillRect(QRectF(x, 0, bw, h), g)
        else:
            p.fillPath(rounded(QRectF(0, 0, max(4.0, w * self.value), h), h / 2), a)


class StatChip(QWidget):
    def __init__(self, label, value, accent=False, parent=None):
        super().__init__(parent)
        self.label = label.upper()
        self.value = value
        self.accent = accent
        self.lf = theme.font(8.5, SEMIBOLD)
        self.lf.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.8)
        self.vf = theme.font(11.5, SEMIBOLD, mono=True)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip(f"{label}: {value}")

    def sizeHint(self):
        a, b = QFontMetrics(self.lf), QFontMetrics(self.vf)
        return QSize(max(a.horizontalAdvance(self.label), b.horizontalAdvance(self.value)) + 18, a.height() + b.height() + 11)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(theme.STROKE, 1))
        p.setBrush(theme.with_alpha(theme.CARD, 0.92))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 8, 8)
        a = QFontMetrics(self.lf)
        p.setFont(self.lf)
        p.setPen(theme.TEXT_FAINT)
        p.drawText(QRectF(9, 5, self.width() - 18, a.height()), Qt.AlignmentFlag.AlignLeft, self.label)
        p.setFont(self.vf)
        p.setPen(theme.accent() if self.accent else theme.TEXT)
        p.drawText(QRectF(9, 6 + a.height(), self.width() - 18, self.height() - a.height() - 8),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, self.value)


class FeatureChip(QWidget):
    def __init__(self, icon, text, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self.text = text
        self.setFont(theme.font(11, MEDIUM))
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(fm.horizontalAdvance(self.text) + 36, fm.height() + 12)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.STROKE, 1))
        p.setBrush(theme.CARD)
        p.drawRoundedRect(r, r.height() / 2, r.height() / 2)
        p.drawPixmap(QPointF(10, (self.height() - 10) / 2), icons.pixmap(self.icon_name, theme.accent(), 10, _dpr(self)))
        p.setFont(self.font())
        p.setPen(theme.TEXT)
        p.drawText(QRectF(26, 0, self.width() - 30, self.height()), Qt.AlignmentFlag.AlignVCenter, self.text)


class FileTab(QAbstractButton):
    """A tab in the preview header: a file, or one part of a 3MF project."""

    def __init__(self, title, icon, icon_color=None, selected=False, badge=None, max_width=260, parent=None):
        super().__init__(parent)
        self.title = title
        self.icon_name = icon
        self.icon_color = icon_color or theme.TEXT_DIM
        self.selected = selected
        self.badge = badge
        self.max_w = max_width
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.bf = theme.font(9.5, SEMIBOLD, mono=True)
        self._fonts()

    def _fonts(self):
        self.setFont(theme.font(13, SEMIBOLD if self.selected else MEDIUM))

    def set_state(self, title=None, icon=None, color=None, selected=None, badge=None):
        if title is not None:
            self.title = title
        if icon is not None:
            self.icon_name = icon
        if color is not None:
            self.icon_color = color
        if selected is not None:
            self.selected = selected
            self._fonts()
        self.badge = badge
        self.updateGeometry()
        self.update()

    def _badge_w(self):
        return QFontMetrics(self.bf).horizontalAdvance(self.badge) + 10 if self.badge else 0

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        w = 20 + 11 + 6 + fm.horizontalAdvance(self.title) + 6 + (6 + self._badge_w() if self.badge else 0)
        return QSize(min(self.max_w, w), fm.height() + 12)

    def minimumSizeHint(self):
        return self.sizeHint()

    def is_truncated(self):
        return self.sizeHint().width() >= self.max_w or self.width() < self.sizeHint().width()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        a = theme.accent()
        if self.selected:
            fill, border = theme.with_alpha(a, 0.14), theme.with_alpha(a, 0.7)
        else:
            fill = theme.white(0.06) if self.underMouse() else theme.with_alpha(theme.CARD, 0.7)
            border = theme.STROKE
        p.setPen(QPen(border, 1))
        p.setBrush(fill)
        p.drawRoundedRect(r, 8, 8)
        x = 10
        p.drawPixmap(QPointF(x, (self.height() - 11) / 2), icons.pixmap(self.icon_name, self.icon_color, 11, _dpr(self), 2.4))
        x += 17
        fm = QFontMetrics(self.font())
        bw = self._badge_w()
        avail = self.width() - x - 10 - (bw + 6 if self.badge else 0)
        p.setFont(self.font())
        p.setPen(theme.TEXT if self.selected else theme.TEXT_DIM)
        t = fm.elidedText(self.title, Qt.TextElideMode.ElideMiddle, int(max(0, avail)))
        p.drawText(QRectF(x, 0, avail, self.height()), Qt.AlignmentFlag.AlignVCenter, t)
        if self.badge:
            bx = x + fm.horizontalAdvance(t) + 6
            bf = QFontMetrics(self.bf)
            br = QRectF(bx, (self.height() - bf.height() - 2) / 2, bw, bf.height() + 2)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.white(0.07))
            p.drawRoundedRect(br, br.height() / 2, br.height() / 2)
            p.setFont(self.bf)
            p.setPen(theme.TEXT_DIM)
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, self.badge)


class Segmented(QWidget):
    """Two-way switch (Mesh | STEP)."""

    changed = Signal(int)

    def __init__(self, options, parent=None):
        super().__init__(parent)
        self.options = options
        self.index = 0
        self.setFont(theme.font(12, MEDIUM))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(120, 26)

    def set_index(self, i, emit=False):
        if i != self.index:
            self.index = i
            self.update()
            if emit:
                self.changed.emit(i)

    def mousePressEvent(self, e):
        i = min(len(self.options) - 1, int(e.position().x() / (self.width() / len(self.options))))
        self.set_index(i, emit=True)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(theme.STROKE_STRONG, 1))
        p.setBrush(theme.with_alpha(theme.CARD, 0.92))
        p.drawRoundedRect(r, 7, 7)
        w = r.width() / len(self.options)
        sel = QRectF(r.x() + w * self.index + 2, r.y() + 2, w - 4, r.height() - 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.white(0.16))
        p.drawRoundedRect(sel, 5, 5)
        p.setFont(self.font())
        for i, o in enumerate(self.options):
            p.setPen(theme.TEXT if i == self.index else theme.TEXT_DIM)
            p.drawText(QRectF(r.x() + w * i, r.y(), w, r.height()), Qt.AlignmentFlag.AlignCenter, o)


class Clickable(Card):
    """A card that reacts to clicks and hover (preset cards, job rows)."""

    clicked = Signal()
    right_clicked = Signal(object)

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.hover = False

    def enterEvent(self, e):
        self.hover = True
        self.on_hover(True)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.hover = False
        self.on_hover(False)
        super().leaveEvent(e)

    def on_hover(self, on):
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        elif e.button() == Qt.MouseButton.RightButton:
            self.right_clicked.emit(e.globalPosition().toPoint())
        super().mousePressEvent(e)


class PresetCard(Clickable):
    def __init__(self, preset, parent=None):
        super().__init__(parent, padding=10, vertical=False, spacing=10)
        self.preset = preset
        self.selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(f"{preset.title} preset")
        self.icon_box = _IconBox(preset.icon, self)
        self.lay.addWidget(self.icon_box, 0, Qt.AlignmentFlag.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.setContentsMargins(0, 0, 0, 0)
        self.title = Label(preset.title, 13, SEMIBOLD)
        self.sub = Label(preset.subtitle, 10.5, color=theme.TEXT_DIM, wrap=True)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        self.lay.addLayout(col, 1)
        self.picked = Pill("Auto used", filled=True)
        self.picked.hide()
        col.addWidget(self.picked, 0, Qt.AlignmentFlag.AlignLeft)
        self.check = IconLabel("check-circle", theme.accent(), 16)
        self.lay.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)
        # marked when the Auto preset chose this one for the selected file
        self.auto_picked = False
        self.refresh()

    def set_selected(self, on):
        self.selected = on
        self.refresh()

    def set_auto_picked(self, on, tip=""):
        if on != self.auto_picked or tip != self.toolTip():
            self.auto_picked = on
            self.setToolTip(tip if on else "")
            self.refresh()

    def on_hover(self, on):
        self.refresh()

    def refresh(self):
        a = theme.accent()
        pick = theme.auto_pick()
        self.icon_box.selected = self.selected
        self.icon_box.color = pick if (self.auto_picked and not self.selected) else None
        self.icon_box.update()
        self.check.setVisible(self.selected)
        self.check.set(color=a)
        self.picked.color = pick
        self.picked.setVisible(self.auto_picked and not self.selected)
        if self.selected:
            self.set_highlighted(True, a)
        elif self.auto_picked:
            self.set_highlighted(True, pick)
        else:
            self.set_highlighted(self.hover, theme.STROKE_STRONG)


class _IconBox(QWidget):
    def __init__(self, icon, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self.selected = False
        self.color = None  # a color of its own (the preset Auto chose)
        self.setFixedSize(32, 32)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = self.color or theme.accent()
        on = self.selected or self.color is not None
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(theme.with_alpha(a, 0.18) if on else theme.white(0.05))
        p.drawRoundedRect(QRectF(self.rect()), 8, 8)
        p.drawPixmap(QPointF(9.5, 9.5), icons.pixmap(self.icon_name, a if on else theme.TEXT_DIM, 13, _dpr(self)))


class Divider(QFrame):
    def __init__(self, vertical=False, parent=None):
        super().__init__(parent)
        if vertical:
            self.setFixedWidth(1)
        else:
            self.setFixedHeight(1)
        self.setStyleSheet(f"background: {theme.css(theme.STROKE)}; border: none;")


def _fill(lay, items, spacing, margins):
    lay.setSpacing(spacing)
    lay.setContentsMargins(*margins)
    for w in items:
        if w is None:
            lay.addStretch(1)
        elif isinstance(w, int):
            lay.addSpacing(w)
        elif isinstance(w, QLayout):
            lay.addLayout(w)
        else:
            lay.addWidget(w)
    return lay


def hbox(*items, spacing=8, margins=(0, 0, 0, 0)):
    """Row of widgets / layouts; None = stretch, int = fixed space."""
    return _fill(QHBoxLayout(), items, spacing, margins)


def vbox(*items, spacing=8, margins=(0, 0, 0, 0)):
    """Column of widgets / layouts; None = stretch, int = fixed space."""
    return _fill(QVBoxLayout(), items, spacing, margins)


def clear_layout(lay):
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.setParent(None)
            w.deleteLater()
        elif item.layout() is not None:
            clear_layout(item.layout())


def brand_mark(parent=None):
    return _BrandMark(parent)


class _BrandMark(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = theme.accent()
        g = QLinearGradient(0, 0, 34, 34)
        g.setColorAt(0, theme.with_alpha(a, 0.9))
        g.setColorAt(1, theme.with_alpha(a, 0.45))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawRoundedRect(QRectF(0, 0, 34, 34), 9, 9)
        p.drawPixmap(QPointF(8.5, 8.5), icons.pixmap("logo", QColor.fromRgbF(0, 0, 0, 0.8), 17, _dpr(self)))

