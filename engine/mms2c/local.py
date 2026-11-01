"""Local changes to a big shape, at the cost of the faces changed rather than the whole
shape.

OCC's BRepTools_ReShape walks the whole shape down to its vertices looking for what to
replace (0.3 s on a 77,000-face solid), and a map of edges to faces is built over every
face. A change confined to a few faces needs neither: requests at edge or vertex level
are applied to the faces they can touch only, and the shape gets face-for-face
replacements, whose walk stops at the faces (0.05 s). The technique is from refit.py
(boromyr/STL2STEP, MIT licence); written here afresh for our needs.
"""
from OCP.BRep import BRep_Builder, BRep_Tool
from OCP.BRepTools import BRepTools_ReShape
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_FORWARD
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS, TopoDS_Compound
from OCP.collections import (IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher,
                             IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher)


def compound(shapes):
    comp = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(comp)
    for s in shapes:
        builder.Add(comp, s)
    return comp


def faces_of(shape):
    out, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        out.append(TopoDS.Face(ex.Current()))
        ex.Next()
    return out


def edge_faces(shape):
    """Edge -> the faces on it, over a shape (or a compound of a few faces)."""
    m = IndexedDataMap_TopoDS_Shape_List_TopoDS_Shape_TopTools_ShapeMapHasher()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, m)
    return m


def apply_local(shape, reshape, faces):
    """reshape applied to shape, where its requests (on edges, vertices or faces) can
    only touch these faces: each face is reshaped on its own, and the shape gets the
    face-for-face result. Returns (new shape, the face-level ReShape, whose Value()
    answers for the old faces)."""
    by_face = BRepTools_ReShape()
    for face in faces:
        key = TopoDS.Face(face.Oriented(TopAbs_FORWARD))
        new = reshape.Apply(key)
        if new.IsNull():
            by_face.Remove(key)
        elif not (new.IsSame(key) and new.Orientation() == key.Orientation()):
            by_face.Replace(key, new)
    return by_face.Apply(shape, TopAbs_FACE), by_face


def replace_faces(shape, old, new):
    """The shape with the faces old taken out and new put in their place (one ReShape
    walk stopping at the faces). new: a face or compound, or None to remove only."""
    reshape = BRepTools_ReShape()
    old = list(old)
    if not old:
        return shape
    first = TopoDS.Face(old[0].Oriented(TopAbs_FORWARD))
    if new is None:
        reshape.Remove(first)
    else:
        reshape.Replace(first, new if first.Orientation() == old[0].Orientation() else new.Reversed())
    for face in old[1:]:
        reshape.Remove(TopoDS.Face(face.Oriented(TopAbs_FORWARD)))
    return reshape.Apply(shape, TopAbs_FACE)


def boundary(faces, around):
    """The edges of a region (faces) it shares with faces outside it, from a map of the
    edges round it (edge_faces of the region and its neighbours): [(edge, outside face)]."""
    inside = IndexedMap_TopoDS_Shape_TopTools_ShapeMapHasher()
    for f in faces:
        inside.Add(f)
    out = []
    for i in range(1, around.Extent() + 1):
        on = [TopoDS.Face(f) for f in around.FindFromIndex(i)]
        ins = [f for f in on if inside.Contains(f)]
        outs = [f for f in on if not inside.Contains(f)]
        if ins and outs:
            out.append((TopoDS.Edge(around.FindKey(i)), outs[0]))
    return out


def open_edges(m):
    """Edges with a face on one side only, in an edge -> faces map (seams of closed
    surfaces and degenerated edges aside)."""
    out = []
    for i in range(1, m.Extent() + 1):
        faces = m.FindFromIndex(i)
        if faces.Size() == 1:
            e = TopoDS.Edge(m.FindKey(i))
            if not BRep_Tool.Degenerated_s(e) and not BRep_Tool.IsClosed_s(e, TopoDS.Face(faces.First())):
                out.append(e)
    return out
