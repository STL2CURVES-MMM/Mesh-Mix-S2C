"""3D preview: decodes the engine's MMMP preview files and draws them with
OpenGL (turntable orbit, zoom and pan; Z is up, like in the Mac app)."""
import math
import struct

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QMatrix4x4, QSurfaceFormat, QVector3D, QVector4D
from PySide6.QtOpenGL import QOpenGLBuffer, QOpenGLShader, QOpenGLShaderProgram
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QLabel

from . import theme

GL_TRIANGLES = 0x0004
GL_LINES = 0x0001
GL_TRIANGLE_STRIP = 0x0005
GL_FLOAT = 0x1406
GL_UNSIGNED_BYTE = 0x1401
GL_UNSIGNED_INT = 0x1405
GL_DEPTH_TEST = 0x0B71
GL_BLEND = 0x0BE2
GL_POLYGON_OFFSET_FILL = 0x8037
GL_COLOR_BUFFER_BIT = 0x4000
GL_DEPTH_BUFFER_BIT = 0x0100
GL_LEQUAL = 0x0203
GL_SRC_ALPHA = 0x0302
GL_ONE_MINUS_SRC_ALPHA = 0x0303
GL_MULTISAMPLE = 0x809D
GL_CULL_FACE = 0x0B44

FOV = 35.0
VIEW_DIR = (0.9, -1.25, 0.85)


class PreviewMesh:
    """Geometry decoded from the engine's preview format:

        b"MMMP", uint32 version, n_verts, n_tris, n_line_pts,
        positions f32[n*3], normals f32[n*3], colors u8[n*4],
        indices u32[t*3], line points f32[l*3] (pairs = segments)
    """

    def __init__(self, P, N, C, I, L):
        self.positions = P
        self.normals = N
        self.colors = C
        self.indices = I
        self.lines = L
        self.vertex_count = len(P)
        self.triangle_count = len(I)
        self.line_count = len(L)
        if len(P):
            self.bbox_min = P.min(axis=0).astype(np.float64)
            self.bbox_max = P.max(axis=0).astype(np.float64)
        else:
            self.bbox_min = self.bbox_max = np.zeros(3)
        self.nbytes = P.nbytes + N.nbytes + C.nbytes + I.nbytes + L.nbytes

    @classmethod
    def load(cls, path):
        try:
            with open(path, "rb") as fh:
                d = fh.read()
        except OSError:
            return None
        if len(d) < 20 or d[:4] != b"MMMP":
            return None
        _ver, nv, nt, nl = struct.unpack_from("<IIII", d, 4)
        need = 20 + nv * 12 * 2 + nv * 4 + nt * 12 + nl * 12
        if len(d) < need:
            return None
        o = 20
        P = np.frombuffer(d, np.float32, nv * 3, o).reshape(nv, 3)
        o += nv * 12
        N = np.frombuffer(d, np.float32, nv * 3, o).reshape(nv, 3)
        o += nv * 12
        C = np.frombuffer(d, np.uint8, nv * 4, o).reshape(nv, 4)
        o += nv * 4
        I = np.frombuffer(d, np.uint32, nt * 3, o).reshape(nt, 3)
        o += nt * 12
        L = np.frombuffer(d, np.float32, nl * 3, o).reshape(nl, 3)
        if nt and int(I.max()) >= nv:
            return None  # damaged file: indices outside the vertex list
        if nv and not np.isfinite(P).all():
            P = np.nan_to_num(P)
        return cls(P, N, C, I, L)


def _shader_src(es, kind, body):
    if es:
        head = "#version 100\nprecision highp float;\n" if kind == "frag" else "#version 100\n"
    else:
        head = "#version 120\n"
    return head + body


MESH_VS = """
attribute vec3 a_pos;
attribute vec3 a_nrm;
attribute vec4 a_col;
uniform mat4 u_mvp;
uniform mat4 u_view;
varying vec3 v_nrm;
varying vec3 v_pos;
varying vec3 v_col;
void main() {
    gl_Position = u_mvp * vec4(a_pos, 1.0);
    v_nrm = (u_view * vec4(a_nrm, 0.0)).xyz;
    v_pos = (u_view * vec4(a_pos, 1.0)).xyz;
    v_col = a_col.rgb;
}
"""

MESH_FS = """
varying vec3 v_nrm;
varying vec3 v_pos;
varying vec3 v_col;
uniform vec3 u_tint;
uniform float u_use_col;
uniform vec3 u_key;
uniform vec3 u_rim;
uniform vec3 u_env;
void main() {
    vec3 n = normalize(v_nrm);
    if (!gl_FrontFacing) n = -n;
    vec3 base = mix(u_tint, v_col, u_use_col);
    vec3 v = normalize(-v_pos);
    float key = max(dot(n, u_key), 0.0);
    float rim = max(dot(n, u_rim), 0.0);
    float env = max(dot(n, u_env), 0.0);
    float light = 0.24 + 0.78 * key + 0.30 * rim + 0.22 * env + 0.10 * max(n.z, 0.0);
    vec3 h = normalize(u_key + v);
    float spec = pow(max(dot(n, h), 0.0), 36.0) * 0.16;
    gl_FragColor = vec4(base * light + vec3(spec), 1.0);
}
"""

LINE_VS = """
attribute vec3 a_pos;
uniform mat4 u_mvp;
void main() { gl_Position = u_mvp * vec4(a_pos, 1.0); }
"""

LINE_FS = """
uniform vec4 u_color;
void main() { gl_FragColor = u_color; }
"""

BG_VS = """
attribute vec2 a_pos;
varying float v_t;
void main() { v_t = a_pos.y * 0.5 + 0.5; gl_Position = vec4(a_pos, 0.0, 1.0); }
"""

BG_FS = """
varying float v_t;
uniform vec3 u_top;
uniform vec3 u_bottom;
void main() { gl_FragColor = vec4(mix(u_bottom, u_top, v_t), 1.0); }
"""


def _vec(c):
    return QVector3D(c.redF(), c.greenF(), c.blueF())


class Viewport(QOpenGLWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setDepthBufferSize(24)
        fmt.setSamples(4)
        self.setFormat(fmt)
        self.setMinimumSize(200, 150)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.mesh = None
        self.mesh_key = None
        self.face_colors = False
        self.show_edges = True
        self.tint = QVector3D(0.62, 0.66, 0.72)
        self.gl_ok = None
        self.gl_error = ""
        self._upload = False
        self._bufs = {}
        self._counts = (0, 0)
        self._last = None
        self._button = None
        self.target = QVector3D(0, 0, 0)
        self.size = 1.0
        self.dist = 4.0
        self.az = math.atan2(VIEW_DIR[1], VIEW_DIR[0])
        self.el = math.asin(VIEW_DIR[2] / math.sqrt(sum(v * v for v in VIEW_DIR)))
        self.message = QLabel(self)
        self.message.setWordWrap(True)
        self.message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.message.setStyleSheet(f"color: {theme.css(theme.TEXT_DIM)}; background: transparent; font-size: 12px;")
        self.message.hide()

    # ------------------------------------------------------------------ public

    def set_mesh(self, mesh, key):
        if key == self.mesh_key and mesh is self.mesh:
            return
        changed_geometry = mesh is not self.mesh
        self.mesh = mesh
        self.mesh_key = key
        if changed_geometry:
            self._upload = True
            if mesh is not None:
                self.frame()
        self.update()

    def set_style(self, face_colors, show_edges, tint):
        self.face_colors = face_colors
        self.show_edges = show_edges
        self.tint = QVector3D(*tint)
        self.update()

    def reset_view(self):
        if self.mesh is not None:
            self.frame()
            self.update()

    def frame(self):
        """Fits the part into the view, seen from the front right and above."""
        m = self.mesh
        mn, mx = m.bbox_min, m.bbox_max
        center = (mn + mx) / 2
        self.size = max(float(np.linalg.norm(mx - mn)), 1e-6)
        self.target = QVector3D(*[float(v) for v in center])
        d = np.array(VIEW_DIR) / np.linalg.norm(VIEW_DIR)
        self.az = math.atan2(d[1], d[0])
        self.el = math.asin(d[2])
        vfov = math.radians(FOV)
        aspect = max(self.width(), 1) / max(self.height(), 1)
        hfov = 2 * math.atan(math.tan(vfov / 2) * aspect)
        fov = min(vfov, hfov)
        self.dist = (self.size / 2) / math.sin(fov / 2) * 1.08

    # ------------------------------------------------------------------ camera

    def _eye(self):
        ce = math.cos(self.el)
        off = QVector3D(ce * math.cos(self.az), ce * math.sin(self.az), math.sin(self.el)) * self.dist
        return self.target + off

    def _matrices(self):
        view = QMatrix4x4()
        view.lookAt(self._eye(), self.target, QVector3D(0, 0, 1))
        proj = QMatrix4x4()
        near = max(self.size * 0.002, self.dist * 0.01)
        far = self.dist + self.size * 10
        proj.perspective(FOV, max(self.width(), 1) / max(self.height(), 1), near, far)
        return proj, view

    def mousePressEvent(self, e):
        self._last = e.position()
        self._button = e.button()

    def mouseReleaseEvent(self, e):
        self._last = None

    def mouseMoveEvent(self, e):
        if self._last is None:
            return
        pos = e.position()
        dx, dy = pos.x() - self._last.x(), pos.y() - self._last.y()
        self._last = QPointF(pos)
        pan = self._button in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton) or \
            bool(e.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        if pan:
            eye = self._eye()
            fwd = (self.target - eye).normalized()
            right = QVector3D.crossProduct(fwd, QVector3D(0, 0, 1)).normalized()
            up = QVector3D.crossProduct(right, fwd).normalized()
            s = 2 * self.dist * math.tan(math.radians(FOV) / 2) / max(self.height(), 1)
            self.target = self.target - right * (dx * s) + up * (dy * s)
        else:
            self.az -= dx * 0.008
            self.el = max(-1.55, min(1.55, self.el + dy * 0.008))
        self.update()

    def wheelEvent(self, e):
        steps = e.angleDelta().y() / 120.0 or e.pixelDelta().y() / 60.0
        if steps:
            self.dist = max(self.size * 0.05, min(self.size * 30, self.dist * (0.88 ** steps)))
            self.update()

    def mouseDoubleClickEvent(self, e):
        self.reset_view()

    # ------------------------------------------------------------------ GL

    def _program(self, vs, fs):
        es = self.context().isOpenGLES()
        p = QOpenGLShaderProgram(self)
        if not p.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Vertex, _shader_src(es, "vert", vs)):
            raise RuntimeError(p.log())
        if not p.addShaderFromSourceCode(QOpenGLShader.ShaderTypeBit.Fragment, _shader_src(es, "frag", fs)):
            raise RuntimeError(p.log())
        if not p.link():
            raise RuntimeError(p.log())
        return p

    def initializeGL(self):
        try:
            ctx = self.context()
            if ctx is None or not ctx.isValid():
                raise RuntimeError("no OpenGL context")
            self.f = ctx.functions()
            self.mesh_prog = self._program(MESH_VS, MESH_FS)
            self.line_prog = self._program(LINE_VS, LINE_FS)
            self.bg_prog = self._program(BG_VS, BG_FS)
            quad = np.array([-1, -1, 1, -1, -1, 1, 1, 1], dtype=np.float32)
            self.bg_buf = self._make_buffer(quad.tobytes())
            self.gl_ok = True
            self._upload = True
        except Exception as e:  # noqa: BLE001 - shown in the viewport
            self.gl_ok = False
            self.gl_error = str(e)
            self.message.setText("The 3D preview isn't available on this computer (it needs OpenGL 2.1).\n"
                                 "Converting and exporting still work.")
            self.message.show()
            self._place_message()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_message()

    def _place_message(self):
        self.message.setGeometry(20, 0, max(0, self.width() - 40), self.height())

    def _make_buffer(self, data, kind=QOpenGLBuffer.Type.VertexBuffer):
        b = QOpenGLBuffer(kind)
        b.create()
        b.bind()
        b.allocate(data, len(data))
        b.release()
        return b

    def _free(self):
        for b in self._bufs.values():
            b.destroy()
        self._bufs = {}
        self._counts = (0, 0)

    def _upload_mesh(self):
        self._free()
        m = self.mesh
        if m is None or m.vertex_count == 0:
            return
        if not m.triangle_count:
            return
        # Drawn as plain triangle lists (PySide's glDrawElements can't take
        # an index buffer offset). The engine's mesh previews already are
        # one; CAD previews are expanded here.
        flat = m.indices.reshape(-1)
        if len(flat) == m.vertex_count and np.array_equal(flat, np.arange(m.vertex_count, dtype=flat.dtype)):
            P, N, C = m.positions, m.normals, m.colors
        else:
            P, N, C = m.positions[flat], m.normals[flat], m.colors[flat]
        self._bufs["pos"] = self._make_buffer(np.ascontiguousarray(P).tobytes())
        self._bufs["nrm"] = self._make_buffer(np.ascontiguousarray(N).tobytes())
        self._bufs["col"] = self._make_buffer(np.ascontiguousarray(C).tobytes())
        if m.line_count >= 2:
            self._bufs["lines"] = self._make_buffer(np.ascontiguousarray(m.lines).tobytes())
        self._counts = (len(flat), m.line_count - (m.line_count % 2))

    def _attr(self, prog, name, buf, kind, size):
        loc = prog.attributeLocation(name)
        if loc < 0:
            return -1
        buf.bind()
        prog.enableAttributeArray(loc)
        prog.setAttributeBuffer(loc, kind, 0, size, 0)
        return loc

    def paintGL(self):
        if not self.gl_ok:
            return
        f = self.f
        if self._upload:
            self._upload = False
            self._upload_mesh()
        dpr = self.devicePixelRatioF()
        f.glViewport(0, 0, int(self.width() * dpr), int(self.height() * dpr))
        f.glClearColor(theme.VIEWPORT_BOTTOM.redF(), theme.VIEWPORT_BOTTOM.greenF(), theme.VIEWPORT_BOTTOM.blueF(), 1)
        f.glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        f.glDisable(GL_CULL_FACE)

        # background gradient
        f.glDisable(GL_DEPTH_TEST)
        self.bg_prog.bind()
        self.bg_prog.setUniformValue("u_top", _vec(theme.VIEWPORT_TOP))
        self.bg_prog.setUniformValue("u_bottom", _vec(theme.VIEWPORT_BOTTOM))
        loc = self._attr(self.bg_prog, "a_pos", self.bg_buf, GL_FLOAT, 2)
        f.glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)
        self.bg_prog.disableAttributeArray(loc)
        self.bg_buf.release()
        self.bg_prog.release()

        n_idx, n_lines = self._counts
        if not n_idx:
            return
        proj, view = self._matrices()
        mvp = proj * view
        f.glEnable(GL_DEPTH_TEST)
        f.glDepthFunc(GL_LEQUAL)
        edges = self.show_edges and n_lines >= 2
        if edges:
            f.glEnable(GL_POLYGON_OFFSET_FILL)
            f.glPolygonOffset(1.0, 1.0)
        p = self.mesh_prog
        p.bind()
        p.setUniformValue("u_mvp", mvp)
        p.setUniformValue("u_view", view)
        p.setUniformValue("u_tint", self.tint)
        p.setUniformValue1f("u_use_col", 1.0 if self.face_colors else 0.0)
        # key light follows the camera (from the upper right front); the
        # others are fixed in the scene
        p.setUniformValue("u_key", QVector3D(0.35, 0.78, 0.51).normalized())
        p.setUniformValue("u_rim", view.mapVector(QVector3D(-0.36, -0.72, -0.6)).normalized())
        p.setUniformValue("u_env", view.mapVector(QVector3D(0.0, 0.81, -0.59)).normalized())
        locs = [self._attr(p, "a_pos", self._bufs["pos"], GL_FLOAT, 3),
                self._attr(p, "a_nrm", self._bufs["nrm"], GL_FLOAT, 3),
                self._attr(p, "a_col", self._bufs["col"], GL_UNSIGNED_BYTE, 4)]
        f.glDrawArrays(GL_TRIANGLES, 0, n_idx)
        for loc in locs:
            if loc >= 0:
                p.disableAttributeArray(loc)
        p.release()
        if edges:
            f.glDisable(GL_POLYGON_OFFSET_FILL)
            f.glEnable(GL_BLEND)
            f.glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            lp = self.line_prog
            lp.bind()
            lp.setUniformValue("u_mvp", mvp)
            lp.setUniformValue("u_color", QVector4D(0.02, 0.02, 0.02, 0.85))
            loc = self._attr(lp, "a_pos", self._bufs["lines"], GL_FLOAT, 3)
            f.glDrawArrays(GL_LINES, 0, n_lines)
            lp.disableAttributeArray(loc)
            self._bufs["lines"].release()
            lp.release()
            f.glDisable(GL_BLEND)
        self._bufs["pos"].release()

    def cleanup(self):
        if self.gl_ok:
            self.makeCurrent()
            self._free()
            self.doneCurrent()
