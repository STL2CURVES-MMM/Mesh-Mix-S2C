"""The judge reads a 3MF project with the engine's own reader (judge.load_mesh): every
object its build prints, placed and scaled as printed. trimesh's 3MF reader needs
networkx (or lxml), which the pinned environment lacks, so every 3MF part used to get an
error scorecard (2026-10-30). The file is made here; no mesh is stored."""
import zipfile

import numpy as np
import pytest


from mms2c.judge import load_mesh  # noqa: E402

CORE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
CUBE_V = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)]
CUBE_T = [(0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7), (0, 1, 5), (0, 5, 4),
          (1, 2, 6), (1, 6, 5), (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7)]


def _cube_object(oid, size):
    v = "".join(f'<vertex x="{x * size}" y="{y * size}" z="{z * size}"/>' for x, y, z in CUBE_V)
    t = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in CUBE_T)
    return f'<object id="{oid}" type="model"><mesh><vertices>{v}</vertices><triangles>{t}</triangles></mesh></object>'


def _write(path):
    model = (f'<?xml version="1.0" encoding="UTF-8"?><model unit="millimeter" xmlns="{CORE}">'
             f'<resources>{_cube_object(1, 10)}{_cube_object(2, 20)}</resources><build>'
             '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 0 0 0"/>'
             '<item objectid="1" transform="1 0 0 0 1 0 0 0 1 50 0 0"/>'
             '<item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 50 0"/>'
             '</build></model>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", model)


def test_a_3mf_project_is_read_as_printed(tmp_path):
    path = tmp_path / "plate.3mf"
    _write(path)
    m = load_mesh(str(path))
    assert len(m.faces) == 3 * 12
    # two 10 mm cubes and one 20 mm cube
    assert abs(m.volume) == pytest.approx(2 * 1000 + 8000, rel=1e-9)
    assert np.allclose(m.bounds, [[0, 0, 0], [60, 70, 20]])


def test_a_3mf_with_nothing_printed_is_refused(tmp_path):
    path = tmp_path / "empty.3mf"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("3D/3dmodel.model", f'<model unit="millimeter" xmlns="{CORE}"><resources/><build/></model>')
    with pytest.raises(ValueError):
        load_mesh(str(path))
