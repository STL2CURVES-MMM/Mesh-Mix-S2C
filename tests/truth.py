"""Ground-truth face type counts for the generated CAD test models."""
import sys, os, collections
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import make_models as mm
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.GeomAbs import GeomAbs_Plane, GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Sphere, GeomAbs_Torus

NAMES = {GeomAbs_Plane: "plane", GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone", GeomAbs_Sphere: "sphere", GeomAbs_Torus: "torus"}

captured = {}
def fake_save(shape, name, **kw):
    captured[name] = shape
mm.save_shape = fake_save
mm.cad_models()
for name, shape in captured.items():
    c = collections.Counter()
    e = TopExp_Explorer(shape, TopAbs_FACE)
    while e.More():
        t = BRepAdaptor_Surface(TopoDS.Face(e.Current())).GetType()
        c[NAMES.get(t, str(t).split("_")[-1])] += 1
        e.Next()
    print(f"{name:28s} {sum(c.values()):4d} {dict(c)}")
