"""Small line icons (24×24 SVG), drawn for this app, tinted on demand.

They stand in for the SF Symbols the Mac app uses."""
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_S = 'fill="none" stroke="{c}" stroke-width="{w}" stroke-linecap="round" stroke-linejoin="round"'
_F = 'fill="{c}" stroke="none"'
# the mark cut out of a filled icon (the check in a filled circle): near-black on the
# dark themes, white on Light, where the fills are deeper (set by theme.apply_theme)
KNOCKOUT = "#0b0c0e"
_DARK = "{k}"

PATHS = {
    "plus": f'<path {_S} d="M12 5v14M5 12h14"/>',
    "x": f'<path {_S} d="M6.5 6.5l11 11M17.5 6.5l-11 11"/>',
    "check": f'<path {_S} d="M5 12.5l4.5 4.5L19 7.5"/>',
    "exclamation": f'<path {_S} d="M12 5v9"/><circle {_F} cx="12" cy="19" r="1.6"/>',
    "check-circle": f'<circle {_F} cx="12" cy="12" r="10"/><path fill="none" stroke="{_DARK}" stroke-width="2.4" '
                    'stroke-linecap="round" stroke-linejoin="round" d="M7.5 12.5l3 3 6-6.5"/>',
    "exclamation-circle": f'<circle {_F} cx="12" cy="12" r="10"/><path fill="none" stroke="{_DARK}" stroke-width="2.4" '
                          f'stroke-linecap="round" d="M12 6.8v6.4"/><circle fill="{_DARK}" cx="12" cy="17" r="1.5"/>',
    "x-octagon": f'<path {_F} d="M8 2h8l6 6v8l-6 6H8l-6-6V8z"/><path fill="none" stroke="{_DARK}" stroke-width="2.4" '
                 'stroke-linecap="round" d="M8.5 8.5l7 7M15.5 8.5l-7 7"/>',
    "x-octagon-line": f'<path {_S} d="M8 2.5h8l5.5 5.5v8L16 21.5H8L2.5 16V8z"/><path {_S} d="M9 9l6 6M15 9l-6 6"/>',
    "gears": f'<circle {_S} cx="9" cy="10" r="2.6"/><path {_S} d="M9 3.5v2M9 14.5v2M2.5 10h2M13.5 10h2'
             'M4.4 5.4l1.4 1.4M12.2 13.2l1.4 1.4M4.4 14.6l1.4-1.4M12.2 6.8l1.4-1.4"/>'
             f'<circle {_S} cx="17" cy="17" r="1.8"/><path {_S} d="M17 12.8v1.4M17 19.8v1.4M12.8 17h1.4M19.8 17h1.4"/>',
    "doc-search": f'<path {_S} d="M13 3H6.5A1.5 1.5 0 0 0 5 4.5v15A1.5 1.5 0 0 0 6.5 21H10M13 3l5 5v2M13 3v5h5"/>'
                  f'<circle {_S} cx="15.5" cy="15.5" r="3.5"/><path {_S} d="M18 18l3 3"/>',
    "clock": f'<circle {_S} cx="12" cy="12" r="9"/><path {_S} d="M12 7v5l3 2"/>',
    "stop-circle": f'<circle {_S} cx="12" cy="12" r="9"/><rect {_F} x="8.6" y="8.6" width="6.8" height="6.8" rx="1"/>',
    "cube": f'<path {_S} d="M12 2.8l8.2 4.6v9.2L12 21.2l-8.2-4.6V7.4z"/><path {_S} d="M3.8 7.4L12 12l8.2-4.6M12 12v9.2"/>',
    "cube-transparent": f'<path {_S} d="M12 2.8l8.2 4.6v9.2L12 21.2l-8.2-4.6V7.4z"/><path {_S} d="M3.8 7.4L12 12l8.2-4.6M12 12v9.2"/>'
                        '<path fill="none" stroke="{c}" stroke-width="1.2" stroke-dasharray="1.6 1.8" '
                        'd="M12 2.8V12M3.8 16.6L12 12M20.2 16.6L12 12"/>',
    "drop": f'<path {_F} d="M12 2.5c3.8 4.7 6.5 8.2 6.5 11.5a6.5 6.5 0 0 1-13 0c0-3.3 2.7-6.8 6.5-11.5z"/>',
    "wand": f'<path {_S} d="M4 20L15 9"/><path {_S} d="M13.5 7.5l3 3"/>'
            f'<path {_F} d="M18 2.5l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>'
            f'<path {_F} d="M8 2l.6 1.4L10 4l-1.4.6L8 6l-.6-1.4L6 4l1.4-.6z"/>'
            f'<path {_F} d="M20 12l.6 1.4 1.4.6-1.4.6L20 16l-.6-1.4L18 14l1.4-.6z"/>',
    "scope": f'<circle {_S} cx="12" cy="12" r="7.5"/><circle {_F} cx="12" cy="12" r="2"/>'
             f'<path {_S} d="M12 1.8v3.4M12 18.8v3.4M1.8 12h3.4M18.8 12h3.4"/>',
    "tray": f'<path {_S} d="M3 13.5l2.6-7.2A1.5 1.5 0 0 1 7 5.3h10a1.5 1.5 0 0 1 1.4 1l2.6 7.2V18a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18z"/>'
            f'<path {_S} d="M3 13.5h5l1.3 2.2h5.4l1.3-2.2h5"/>',
    "folder": f'<path {_S} d="M3 7a1.5 1.5 0 0 1 1.5-1.5h4.6l2 2.2h8.4A1.5 1.5 0 0 1 21 9.2v8.8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18z"/>',
    "upload": f'<path {_S} d="M8.5 9.5H6.5A1.5 1.5 0 0 0 5 11v8a1.5 1.5 0 0 0 1.5 1.5h11A1.5 1.5 0 0 0 19 19v-8a1.5 1.5 0 0 0-1.5-1.5h-2"/>'
              f'<path {_S} d="M12 14.5V3M8.5 6.5L12 3l3.5 3.5"/>',
    "import": f'<path {_S} d="M8 8.5H6A1.5 1.5 0 0 0 4.5 10v9A1.5 1.5 0 0 0 6 20.5h12a1.5 1.5 0 0 0 1.5-1.5v-9A1.5 1.5 0 0 0 18 8.5h-2"/>'
              f'<path {_S} d="M12 3v11M8.5 10.5L12 14l3.5-3.5"/>',
    "arrow-down-circle": f'<circle {_F} cx="12" cy="12" r="10"/><path fill="none" stroke="{_DARK}" stroke-width="2.4" '
                         'stroke-linecap="round" stroke-linejoin="round" d="M12 6.5v10M7.8 12.5L12 16.7l4.2-4.2"/>',
    "update": f'<circle {_S} cx="12" cy="12" r="9"/><path {_S} d="M12 7v9M8 12.5l4 4 4-4"/>',
    "play": f'<path {_F} d="M7 4.5v15a1 1 0 0 0 1.5.9l12-7.5a1 1 0 0 0 0-1.8l-12-7.5A1 1 0 0 0 7 4.5z"/>',
    "refresh": f'<path {_S} d="M20 12a8 8 0 1 1-2.3-5.7"/><path {_S} d="M20 4v4.5h-4.5"/>',
    "trash": f'<path {_S} d="M4 6.5h16M9.5 6.5V4.5h5v2M6.5 6.5l.9 13a1.5 1.5 0 0 0 1.5 1.4h6.2a1.5 1.5 0 0 0 1.5-1.4l.9-13"/>'
             f'<path {_S} d="M10 10.5v6.5M14 10.5v6.5"/>',
    "palette": f'<path {_S} d="M12 3a9 9 0 1 0 0 18c1.3 0 2-.8 2-1.8 0-1.4-1.2-1.8-1.2-3 0-1 .8-1.7 1.8-1.7H17a4 4 0 0 0 4-4C21 6.4 17 3 12 3z"/>'
               f'<circle {_F} cx="7.5" cy="11" r="1.4"/><circle {_F} cx="10" cy="7" r="1.4"/><circle {_F} cx="14.5" cy="7" r="1.4"/>',
    "square-dashed": '<rect fill="none" stroke="{c}" stroke-width="{w}" stroke-dasharray="3 2.6" x="4" y="4" width="16" height="16" rx="2.5"/>',
    "text-left": f'<path {_S} d="M4 6h16M4 10h10M4 14h16M4 18h10"/>',
    "split": f'<rect {_S} x="3" y="5" width="18" height="14" rx="2"/><path {_S} d="M12 5v14"/>',
    "stack": f'<path {_S} d="M12 3l9 4.5-9 4.5-9-4.5z"/><path {_S} d="M3 12l9 4.5 9-4.5M3 16.5L12 21l9-4.5"/>',
    "fast": f'<path {_S} d="M4 7l5 5-5 5M12 7l5 5-5 5"/>',
    "sparkles": f'<path {_F} d="M10 3l1.6 4.4L16 9l-4.4 1.6L10 15l-1.6-4.4L4 9l4.4-1.6z"/>'
                f'<path {_F} d="M18 13l.9 2.1 2.1.9-2.1.9-.9 2.1-.9-2.1-2.1-.9 2.1-.9z"/>',
    "bolt": f'<path {_F} d="M13.5 2L4.5 13.5h6.5L9.5 22l10-12.5H13z"/>',
    "logo": '<path fill="none" stroke="{c}" stroke-width="1.6" stroke-dasharray="2 2" d="M12 4.5L4.5 18.5h15z"/>'
            f'<circle {_F} cx="12" cy="4.5" r="2.6"/><circle {_F} cx="4.5" cy="18.5" r="2.6"/><circle {_F} cx="19.5" cy="18.5" r="2.6"/>',
    "warning": f'<path {_F} d="M10.3 3.6a2 2 0 0 1 3.4 0l8 13.6A2 2 0 0 1 20 20.2H4a2 2 0 0 1-1.7-3z"/>'
               f'<path fill="none" stroke="{_DARK}" stroke-width="2.3" stroke-linecap="round" d="M12 8.8v5"/>'
               f'<circle fill="{_DARK}" cx="12" cy="17" r="1.3"/>',
    "warning-line": f'<path {_S} d="M10.3 3.9a2 2 0 0 1 3.4 0l7.8 13.3A2 2 0 0 1 19.8 20H4.2a2 2 0 0 1-1.7-2.8z"/>'
                    f'<path {_S} d="M12 9v4.5"/><circle {_F} cx="12" cy="16.6" r="1.2"/>',
    "seal-check": f'<path {_S} d="M12 2.5l2.2 1.6 2.7-.1.8 2.6 2.2 1.6-.9 2.6.9 2.6-2.2 1.6-.8 2.6-2.7-.1L12 21.5l-2.2-1.6-2.7.1-.8-2.6-2.2-1.6.9-2.6-.9-2.6 2.2-1.6.8-2.6 2.7.1z"/>'
                  f'<path {_S} d="M8.5 12.2l2.4 2.4 4.6-5"/>',
    "doc": f'<path {_S} d="M13.5 3H7a1.5 1.5 0 0 0-1.5 1.5v15A1.5 1.5 0 0 0 7 21h10a1.5 1.5 0 0 0 1.5-1.5V8z"/><path {_S} d="M13.5 3v5h5"/>',
    "doc-dup": f'<path {_F} d="M9 2h6.5L20 6.5V16a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 7 16V3.5A1.5 1.5 0 0 1 8.5 2z"/>'
               f'<path {_S} d="M5 7v12.5A1.5 1.5 0 0 0 6.5 21H15"/>',
    "chevron-down": f'<path {_S} d="M6 9l6 6 6-6"/>',
    "chevron-right": f'<path {_S} d="M9 6l6 6-6 6"/>',
    "chevron-up": f'<path {_S} d="M6 15l6-6 6 6"/>',
    "dot": f'<circle {_F} cx="12" cy="12" r="5"/>',
    "info": f'<circle {_S} cx="12" cy="12" r="9"/><path {_S} d="M12 11v5.5"/><circle {_F} cx="12" cy="7.6" r="1.3"/>',
}


def svg(name, color, weight=2.0):
    body = PATHS[name].replace("{c}", color).replace("{w}", f"{weight:g}").replace("{k}", KNOCKOUT)
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>'


_cache = {}


def pixmap(name, color, size, dpr=2.0, weight=2.0):
    """A tinted icon. `color` is a QColor or a CSS color string."""
    if isinstance(color, str):
        c, opacity = color, 1.0
    else:
        c, opacity = color.name(), color.alphaF()  # "#rrggbb" + painter opacity
    key = (name, c, round(size * dpr), round(opacity, 3), weight, KNOCKOUT)
    pm = _cache.get(key)
    if pm is None:
        px = max(1, round(size * dpr))
        pm = QPixmap(px, px)
        pm.fill(Qt.GlobalColor.transparent)
        r = QSvgRenderer(QByteArray(svg(name, c, weight).encode()))
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setOpacity(opacity)
        r.render(p, QRectF(0, 0, px, px))
        p.end()
        pm.setDevicePixelRatio(dpr)
        _cache[key] = pm
    return pm


def icon(name, color, size=16, dpr=2.0):
    return QIcon(pixmap(name, color, size, dpr))


def write_svg(path, name, color, weight=2.0):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg(name, color, weight))
