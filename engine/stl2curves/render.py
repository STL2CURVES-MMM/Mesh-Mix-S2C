"""
render - save a PNG preview of a STEP file, coloured by surface type.

  stl2curves-render part.step [more.step ...] [--out FOLDER]

Grey = flat, blue = cylinder, orange = cone, green = torus (curved rounded edge),
purple = sphere. Black lines are the face edges: areas still made of many small flat
facets show up as a dense grid of lines; rebuilt curves are smooth with few edges.
"""
import argparse
import os
import sys
from pathlib import Path

import vtk
from OCP.BRep import BRep_Tool
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus
from OCP.STEPControl import STEPControl_Reader
from OCP.TopAbs import TopAbs_FACE, TopAbs_EDGE, TopAbs_REVERSED
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS

COLOURS = {GeomAbs_Plane: (0.78, 0.78, 0.80), GeomAbs_Cylinder: (0.30, 0.55, 0.90),
           GeomAbs_Cone: (0.95, 0.60, 0.20), GeomAbs_Torus: (0.35, 0.75, 0.40),
           GeomAbs_Sphere: (0.65, 0.40, 0.85)}


EDGE_ANGLE = 0.1    # rad: edges drawn in straight segments turning at most this ...
EDGE_SAG = 0.01     # ... and bowing at most this far (mm)


def edge_points(curve):
    """Points along an edge (a BRepAdaptor_Curve) close enough to draw it with straight
    segments: spaced by how much it bends. A fixed 24 drew a thread's crest edge, two
    or three turns round a bore, as chords cutting across it."""
    from OCP.GCPnts import GCPnts_TangentialDeflection
    a, b = curve.FirstParameter(), curve.LastParameter()
    if int(curve.GetType()) == 0:
        return [curve.Value(a), curve.Value(b)]
    sample = GCPnts_TangentialDeflection(curve, EDGE_ANGLE, EDGE_SAG, 2)
    if sample.NbPoints() < 2:
        return [curve.Value(a + (b - a) * i / 23) for i in range(24)]
    return [sample.Value(i) for i in range(1, sample.NbPoints() + 1)]


def read_step(path):
    saved, devnull = os.dup(1), os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, 1)
    try:
        r = STEPControl_Reader()
        r.ReadFile(str(path))
        r.TransferRoots()
        return r.OneShape()
    finally:
        os.dup2(saved, 1)
        os.close(devnull)
        os.close(saved)


def to_vtk(shape):
    BRepMesh_IncrementalMesh(shape, 0.05, False, 0.3, True)
    points, polys, colours = vtk.vtkPoints(), vtk.vtkCellArray(), vtk.vtkUnsignedCharArray()
    colours.SetNumberOfComponents(3)
    ex = TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        face = TopoDS.Face(ex.Current())
        ex.Next()
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation_s(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        base = points.GetNumberOfPoints()
        for i in range(1, tri.NbNodes() + 1):
            p = tri.Node(i).Transformed(trsf)
            points.InsertNextPoint(p.X(), p.Y(), p.Z())
        rgb = [int(255 * c) for c in COLOURS.get(BRepAdaptor_Surface(face).GetType(), (0.9, 0.3, 0.3))]
        flip = face.Orientation() == TopAbs_REVERSED
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            ids = (a, c, b) if flip else (a, b, c)
            polys.InsertNextCell(3, [base + k - 1 for k in ids])
            colours.InsertNextTuple3(*rgb)
    surf = vtk.vtkPolyData()
    surf.SetPoints(points)
    surf.SetPolys(polys)
    surf.GetCellData().SetScalars(colours)

    lp, lines = vtk.vtkPoints(), vtk.vtkCellArray()
    ex = TopExp_Explorer(shape, TopAbs_EDGE)
    while ex.More():
        edge = TopoDS.Edge(ex.Current())
        ex.Next()
        if BRep_Tool.Degenerated_s(edge):
            continue
        ids = [lp.InsertNextPoint(p.X(), p.Y(), p.Z()) for p in edge_points(BRepAdaptor_Curve(edge))]
        lines.InsertNextCell(len(ids), ids)
    edges = vtk.vtkPolyData()
    edges.SetPoints(lp)
    edges.SetLines(lines)
    return surf, edges


def render(path, out, size=(1600, 800)):
    shape = read_step(path)
    surf, edges = to_vtk(shape)
    window = vtk.vtkRenderWindow()
    window.SetOffScreenRendering(1)
    window.SetSize(*size)
    views = [((1, -1.2, 0.9), (0, 0, 1)), ((-1, 1.2, -0.7), (0, 0, 1))]   # front-top, back-bottom
    for k, (direction, up) in enumerate(views):
        ren = vtk.vtkRenderer()
        ren.SetViewport(k / 2, 0, (k + 1) / 2, 1)
        ren.SetBackground(1, 1, 1)
        for data, is_edges in ((surf, False), (edges, True)):
            mapper = vtk.vtkPolyDataMapper()
            mapper.SetInputData(data)
            if is_edges:
                mapper.ScalarVisibilityOff()
            actor = vtk.vtkActor()
            actor.SetMapper(mapper)
            if is_edges:
                actor.GetProperty().SetColor(0.05, 0.05, 0.05)
                actor.GetProperty().SetLineWidth(1.0)
            else:
                actor.GetProperty().SetInterpolationToFlat()
                actor.GetProperty().SetAmbient(0.25)
                actor.GetProperty().SetDiffuse(0.8)
            ren.AddActor(actor)
        cam = ren.GetActiveCamera()
        cx, cy, cz = surf.GetCenter()
        cam.SetFocalPoint(cx, cy, cz)
        cam.SetPosition(cx + direction[0], cy + direction[1], cz + direction[2])
        cam.SetViewUp(*up)
        ren.ResetCamera()
        cam.Zoom(0.95)
        window.AddRenderer(ren)
    window.Render()
    grab = vtk.vtkWindowToImageFilter()
    grab.SetInput(window)
    grab.Update()
    writer = vtk.vtkPNGWriter()
    writer.SetFileName(str(out))
    writer.SetInputConnection(grab.GetOutputPort())
    writer.Write()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+")
    ap.add_argument("--out")
    args = ap.parse_args()
    for p in map(Path, args.inputs):
        out_dir = Path(args.out) if args.out else p.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / (p.stem + ".png")
        render(p, out)
        print(f"{p.name} -> {out}")


if __name__ == "__main__":
    main()
