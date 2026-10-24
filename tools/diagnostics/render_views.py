"""Render an MMMP preview from a few angles into one PNG: render_views.py in.mmmp out.png [face_colors] [edges]"""
import math, os, sys
sys.path.insert(0, "desktop")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QPainter
from mmm_app.viewport import Viewport, PreviewMesh
src, out = sys.argv[1], sys.argv[2]
colors = len(sys.argv) > 3 and sys.argv[3] == "1"
edges = len(sys.argv) > 4 and sys.argv[4] == "1"
crop = sys.argv[5] if len(sys.argv) > 5 else None   # "zoom" = closer view
app = QApplication([])
v = Viewport(); v.resize(700, 900); v.show()
def go():
    m = PreviewMesh.load(src)
    v.set_mesh(m, "x"); v.set_style(colors, edges, (0.75, 0.75, 0.75))
    shots = []
    for az in (-0.95, 0.6, 2.2):
        v.frame(); v.az = az
        if crop == "zoom":
            v.dist *= 0.4
        shots.append(v.grabFramebuffer())
    W = sum(s.width() for s in shots); H = max(s.height() for s in shots)
    img = QImage(W, H, QImage.Format.Format_RGB32); p = QPainter(img); x = 0
    for s in shots:
        p.drawImage(x, 0, s); x += s.width()
    p.end(); img.save(out); app.quit()
QTimer.singleShot(300, go); app.exec()
