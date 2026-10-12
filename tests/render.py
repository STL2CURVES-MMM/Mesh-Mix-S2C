"""Render an MMMP preview file (or STEP/STL) to PNG with VTK, offscreen.

    python render.py preview.mmmp out.png [--title text]
"""
import struct
import sys

import numpy as np


def read_preview(path):
    with open(path, "rb") as fh:
        data = fh.read()
    assert data[:4] == b"MMMP", "not a preview file"
    ver, nv, nt, nl = struct.unpack("<IIII", data[4:20])
    o = 20
    P = np.frombuffer(data, np.float32, nv * 3, o).reshape(-1, 3); o += nv * 12
    N = np.frombuffer(data, np.float32, nv * 3, o).reshape(-1, 3); o += nv * 12
    C = np.frombuffer(data, np.uint8, nv * 4, o).reshape(-1, 4); o += nv * 4
    I = np.frombuffer(data, np.uint32, nt * 3, o).reshape(-1, 3); o += nt * 12
    L = np.frombuffer(data, np.float32, nl * 3, o).reshape(-1, 3)
    return P, N, C, I, L


def render(P, N, C, I, L, out, title="", size=(900, 700), views=("iso",), view_dir=(1.0, -1.3, 0.9)):
    import vtk
    from vtk.util import numpy_support as ns

    pts = vtk.vtkPoints()
    pts.SetData(ns.numpy_to_vtk(np.ascontiguousarray(P, dtype=np.float32), deep=True))
    cells = vtk.vtkCellArray()
    conn = np.hstack([np.full((len(I), 1), 3, dtype=np.int64), I.astype(np.int64)]).ravel()
    cells.SetCells(len(I), ns.numpy_to_vtkIdTypeArray(conn, deep=True))
    poly = vtk.vtkPolyData()
    poly.SetPoints(pts)
    poly.SetPolys(cells)
    col = ns.numpy_to_vtk(np.ascontiguousarray(C[:, :3]), deep=True, array_type=vtk.VTK_UNSIGNED_CHAR)
    col.SetName("colors")
    poly.GetPointData().SetScalars(col)
    nrm = ns.numpy_to_vtk(np.ascontiguousarray(N, dtype=np.float32), deep=True)
    poly.GetPointData().SetNormals(nrm)

    mapper = vtk.vtkPolyDataMapper()
    mapper.SetInputData(poly)
    mapper.SetColorModeToDirectScalars()
    actor = vtk.vtkActor()
    actor.SetMapper(mapper)
    actor.GetProperty().SetInterpolationToPhong()
    actor.GetProperty().SetSpecular(0.2)

    ren = vtk.vtkRenderer()
    ren.AddActor(actor)
    if len(L):
        lp = vtk.vtkPoints()
        lp.SetData(ns.numpy_to_vtk(np.ascontiguousarray(L, dtype=np.float32), deep=True))
        nseg = len(L) // 2
        lc = vtk.vtkCellArray()
        conn = np.hstack([np.full((nseg, 1), 2, dtype=np.int64), np.arange(nseg * 2, dtype=np.int64).reshape(-1, 2)]).ravel()
        lc.SetCells(nseg, ns.numpy_to_vtkIdTypeArray(conn, deep=True))
        lpoly = vtk.vtkPolyData()
        lpoly.SetPoints(lp)
        lpoly.SetLines(lc)
        lm = vtk.vtkPolyDataMapper()
        lm.SetInputData(lpoly)
        la = vtk.vtkActor()
        la.SetMapper(lm)
        la.GetProperty().SetColor(0.05, 0.05, 0.08)
        la.GetProperty().SetLineWidth(1.5)
        lm.SetResolveCoincidentTopologyToPolygonOffset()
        ren.AddActor(la)
    ren.SetBackground(0.08, 0.09, 0.11)
    if title:
        t = vtk.vtkTextActor()
        t.SetInput(title)
        t.GetTextProperty().SetFontSize(18)
        t.GetTextProperty().SetColor(0.9, 0.9, 0.9)
        t.SetPosition(10, 10)
        ren.AddActor2D(t)
    win = vtk.vtkRenderWindow()
    win.SetOffScreenRendering(1)
    win.AddRenderer(ren)
    win.SetSize(*size)
    cam = ren.GetActiveCamera()
    c = P.mean(0) if len(P) else np.zeros(3)
    cam.SetFocalPoint(*c)
    cam.SetPosition(*(c + np.array(view_dir) * 10))
    cam.SetViewUp(0, 0, 1)
    ren.ResetCamera()
    cam.Zoom(1.2)
    win.Render()
    w2i = vtk.vtkWindowToImageFilter()
    w2i.SetInput(win)
    w2i.Update()
    wr = vtk.vtkPNGWriter()
    wr.SetFileName(out)
    wr.SetInputConnection(w2i.GetOutputPort())
    wr.Write()


if __name__ == "__main__":
    src, out = sys.argv[1], sys.argv[2]
    title = sys.argv[3] if len(sys.argv) > 3 else ""
    render(*read_preview(src), out, title)
