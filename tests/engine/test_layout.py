"""The merged engine's two packages find each other in the repository's layout (engine/mmm
and engine/mms2c): the imports resolve, and the rival conversions start their child
processes in the folder holding both. Quick: nothing is converted here."""
import os

import pytest

pytest.importorskip("OCP")
pytest.importorskip("trimesh")

ENGINE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "engine")


def test_merged_engine_imports_the_top_level_mmm_package():
    from mms2c import merged, rivals

    import mmm.pipeline
    assert merged.mpipe is mmm.pipeline
    assert os.path.samefile(os.path.dirname(os.path.dirname(rivals.__file__)), ENGINE)


def test_child_processes_start_where_both_packages_are():
    from mms2c import rivals

    assert os.path.samefile(rivals.ROOT, ENGINE)
    for module in ("mmm/cli.py", "mms2c/judge.py", "mms2c/__main__.py", "mms2c/merged.py"):
        assert os.path.isfile(os.path.join(rivals.ROOT, module))


def test_no_import_of_mmm_goes_through_mms2c():
    """The fork's layout has MMM inside the package (stl2curves.mmm): nothing here may still say so."""
    folder = os.path.join(ENGINE, "mms2c")
    for name in sorted(os.listdir(folder)):
        if name.endswith(".py"):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                text = f.read()
            assert "mms2c.mmm" not in text and "stl2curves.mmm" not in text, name
            assert "from .mmm" not in text, name
