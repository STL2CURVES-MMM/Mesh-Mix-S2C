"""Linux: adding the AppImage to the applications menu."""
import os

import pytest

from mmm_app import desktop_entry

pytestmark = pytest.mark.skipif(not desktop_entry.system.IS_LINUX, reason="Linux only")


@pytest.fixture
def appimage(tmp_path, monkeypatch):
    a = tmp_path / "My Apps" / "Manifold Mesh Mix.AppImage"
    a.parent.mkdir()
    a.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(a))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    return str(a)


def test_not_available_without_appimage(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert not desktop_entry.available()
    assert desktop_entry.install() is not None


def test_install_and_remove(appimage):
    assert desktop_entry.available() and not desktop_entry.is_installed()
    assert desktop_entry.install() is None
    assert desktop_entry.is_installed()
    with open(desktop_entry.entry_path(), encoding="utf-8") as fh:
        text = fh.read()
    assert f'Exec="{appimage}" %F' in text
    assert "MimeType=model/stl;" in text and "model/3mf" in text
    assert os.path.exists(desktop_entry.icon_path())
    desktop_entry.uninstall()
    assert not desktop_entry.is_installed()
    assert not os.path.exists(desktop_entry.entry_path()) and not os.path.exists(desktop_entry.icon_path())


def test_moved_appimage_is_not_installed(appimage, monkeypatch, tmp_path):
    desktop_entry.install()
    moved = tmp_path / "other.AppImage"
    moved.write_bytes(b"x")
    monkeypatch.setenv("APPIMAGE", str(moved))
    assert not desktop_entry.is_installed()


def test_quoting():
    assert desktop_entry._quote('/a b/c"d$e') == '"/a b/c\\"d\\$e"'
