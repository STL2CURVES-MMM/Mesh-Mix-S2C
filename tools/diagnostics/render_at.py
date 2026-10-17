"""render_at.py in.mmmp out.png x,y,z dist [colors] - close-up views of one spot from four sides"""
import math, sys
sys.path.insert(0, "/home/user/Manifold-Mesh-Mix/desktop")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage, QPainter, QVector3D
from mmm_app.viewport import Viewport, PreviewMesh
src, out = sys.argv[1], sys.argv[2]
x, y, z = map(float, sys.argv[3].split(",")); dist = float(sys.argv[4])
colors = len(sys.argv) > 5 and sys.argv[5] == "1"
app = QApplication([])
v = Viewport(); v.resize(600, 600); v.show()
def go():
    m = PreviewMesh.load(src)
    v.set_mesh(m, "x"); v.set_style(colors, True, (0.75, 0.75, 0.75))
    v.frame()
    shots = []
    for az, el in ((0.3, 0.5), (1.9, 0.5), (3.5, 0.5), (5.0, -0.3)):
        v.target = QVector3D(x, y, z); v.dist = dist; v.az = az; v.el = el; v.update()
        shots.append(v.grabFramebuffer())
    W = sum(s.width() for s in shots); H = max(s.height() for s in shots)
    img = QImage(W, H, QImage.Format.Format_RGB32); p = QPainter(img); xo = 0
    for s in shots:
        p.drawImage(xo, 0, s); xo += s.width()
    p.end(); img.save(out); app.quit()
QTimer.singleShot(300, go); app.exec()
