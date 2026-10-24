"""Side-by-side pictures of the same part from the same camera, with labels:
compare_render.py out.png az el zoom label1=a.mmmp[:colors] label2=b.mmmp[:colors] ...
(the camera is framed on the first file; zoom < 1 moves closer; ":colors" gives
each face its own color)"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "desktop"))
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, Qt, QRect
from PySide6.QtGui import QImage, QPainter, QFont, QColor
from mmm_app.viewport import Viewport, PreviewMesh

out, az, el, zoom = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
panels = []
for arg in sys.argv[5:]:
    label, rest = arg.split("=", 1)
    colors = rest.endswith(":colors")
    panels.append((label, rest[:-7] if colors else rest, colors))
app = QApplication([])
v = Viewport(); v.resize(640, 560); v.show()


def go():
    shots, cam = [], None
    for label, path, colors in panels:
        v.set_mesh(PreviewMesh.load(path), "x")
        v.set_style(colors, False, (0.75, 0.75, 0.75))
        if cam is None:
            v.frame(); v.az, v.el = az, el; v.dist *= zoom
            cam = (v.target, v.dist)
        v.target, v.dist = cam; v.az, v.el = az, el
        v.update()
        shots.append((label, v.grabFramebuffer()))
    w, h = shots[0][1].width(), shots[0][1].height()
    band = 46
    img = QImage(w * len(shots), h + band, QImage.Format.Format_RGB32)
    img.fill(QColor(18, 20, 26))
    p = QPainter(img)
    f = QFont(); f.setPixelSize(22); f.setBold(True); p.setFont(f)
    for i, (label, s) in enumerate(shots):
        p.drawImage(i * w, band, s)
        p.setPen(QColor(235, 238, 245))
        p.drawText(QRect(i * w, 0, w, band), Qt.AlignmentFlag.AlignCenter, label)
    p.end(); img.save(out); app.quit()


QTimer.singleShot(300, go); app.exec()
