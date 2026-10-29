"""Update check, download and install (against a local test server)."""
import hashlib
import http.server
import json
import os
import threading

import pytest

from mmm_app import updater


@pytest.mark.parametrize("s, want", [("v1.2.3", (1, 2, 3)), ("1.2", (1, 2, 0)), ("v2.0.0-beta", (2, 0, 0)),
                                     ("V10", (10, 0, 0)), ("latest", None), (None, None)])
def test_parse_version(s, want):
    assert updater.parse_version(s) == want


def test_is_newer():
    assert updater.is_newer("v1.0.1", "1.0.0")
    assert updater.is_newer("1.10.0", "1.9.9")
    assert not updater.is_newer("v1.0.0", "1.0.0")
    assert not updater.is_newer("v0.9", "1.0.0")
    assert not updater.is_newer("nightly", "1.0.0")


def test_test_builds_come_before_their_release():
    assert updater.is_newer("v2.0.0", "2.0.0-test.3")
    assert updater.is_newer("2.0.0-test.2", "2.0.0-test.1")
    assert updater.is_newer("2.0.0-test.1", "1.6.0")
    assert not updater.is_newer("2.0.0-test.1", "2.0.0")
    assert not updater.is_newer("v2.0.0-test.1", "2.0.0-test.1")


class Server:
    """Serves a fake GitHub "latest release" plus its files."""

    def __init__(self, tmp_path):
        self.files = {}
        self.release = {}
        self.status = 200
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                if self.path == "/latest":
                    if outer.status != 200:
                        self.send_response(outer.status)
                        self.end_headers()
                        return
                    body = json.dumps(outer.release).encode()
                elif self.path.startswith("/files/") and self.path[7:] in outer.files:
                    body = outer.files[self.path[7:]]
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.httpd.handle_error = lambda *a: None  # a cancelled download closes the connection early
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.httpd.server_port}"

    def publish(self, tag, files, sums=True, sums_text=None):
        self.files = dict(files)
        assets = [{"name": n, "browser_download_url": f"{self.base}/files/{n}", "size": len(b)} for n, b in files.items()]
        if sums:
            text = sums_text if sums_text is not None else "".join(
                f"{hashlib.sha256(b).hexdigest()}  {n}\n" for n, b in files.items())
            self.files["SHA256SUMS.txt"] = text.encode()
            assets.append({"name": "SHA256SUMS.txt", "browser_download_url": f"{self.base}/files/SHA256SUMS.txt"})
        self.release = {"tag_name": tag, "body": "Fixes things.", "html_url": f"{self.base}/page", "assets": assets}

    def close(self):
        self.httpd.shutdown()


@pytest.fixture
def server(tmp_path, monkeypatch):
    s = Server(tmp_path)
    monkeypatch.setenv("MMM_UPDATE_URL", s.base + "/latest")
    yield s
    s.close()


def asset(ver="9.9.9"):
    return f"MeshMix-S2C-{ver}{updater.asset_suffix()}"


def test_check_finds_newer_version(server):
    server.publish("v9.9.9", {asset(): b"new app", "MeshMix-S2C-9.9.9-Other-OS.bin": b"x"})
    info = updater.check()
    assert info.version == "9.9.9" and info.asset_name == asset() and info.notes == "Fixes things."


def test_check_up_to_date(server):
    server.publish("v0.0.1", {asset("0.0.1"): b"old"})
    assert updater.check() is None


def test_check_errors(server):
    server.publish("v9.9.9", {"MeshMix-S2C-9.9.9-Other-OS.bin": b"x"})
    with pytest.raises(updater.UpdateError, match="no download for"):
        updater.check()
    server.publish("v9.9.9", {asset(): b"x"}, sums=False)
    with pytest.raises(updater.UpdateError, match="checksum file is missing"):
        updater.check()
    server.status = 404
    with pytest.raises(updater.UpdateError, match="No release"):
        updater.check()
    server.status = 403
    with pytest.raises(updater.UpdateError, match="limiting"):
        updater.check()


def test_check_unreachable(monkeypatch):
    monkeypatch.setenv("MMM_UPDATE_URL", "http://127.0.0.1:9/latest")
    with pytest.raises(updater.UpdateError, match="Couldn't reach GitHub"):
        updater.check()


def test_download_verifies_checksum(server, tmp_path):
    data = os.urandom(300_000)
    server.publish("v9.9.9", {asset(): data})
    info = updater.check()
    seen = []
    path = updater.download(info, seen.append, folder=str(tmp_path))
    with open(path, "rb") as fh:
        assert fh.read() == data
    assert seen and seen[-1] == 1.0
    server.publish("v9.9.9", {asset(): data}, sums_text=f"{'0' * 64}  {asset()}\n")
    with pytest.raises(updater.UpdateError, match="doesn't match"):
        updater.download(updater.check(), folder=str(tmp_path / "b"))
    assert not os.listdir(tmp_path / "b")
    server.publish("v9.9.9", {asset(): data}, sums_text="abc  something-else\n")
    with pytest.raises(updater.UpdateError, match="checksum is missing"):
        updater.download(updater.check(), folder=str(tmp_path / "c"))


def test_download_can_be_cancelled(server, tmp_path):
    server.publish("v9.9.9", {asset(): os.urandom(2_000_000)})
    with pytest.raises(updater.UpdateError, match="cancelled"):
        updater.download(updater.check(), cancelled=lambda: True, folder=str(tmp_path))
    assert os.listdir(tmp_path) == []


@pytest.mark.skipif(not updater.system.IS_LINUX, reason="AppImage updates are Linux only")
def test_appimage_install_replaces_file(tmp_path, monkeypatch):
    old = tmp_path / "ManifoldMeshMix.AppImage"
    old.write_bytes(b"old")
    old.chmod(0o755)
    new = tmp_path / "download.AppImage"
    new.write_bytes(b"new")
    monkeypatch.setenv("APPIMAGE", str(old))
    assert updater.install_target() == (True, "")
    started = []
    monkeypatch.setattr(updater.system, "popen_detached", lambda args, **kw: started.append((args, kw)))
    # only a file this run downloaded and checked is installed, and only if it hasn't changed since
    with pytest.raises(updater.UpdateError):
        updater.install(str(new))
    monkeypatch.setitem(updater._verified, os.path.abspath(str(new)), updater._sha256(str(new)))
    new.write_bytes(b"evil")
    with pytest.raises(updater.UpdateError):
        updater.install(str(new))
    assert old.read_bytes() == b"old" and not started
    new.write_bytes(b"new")
    updater.install(str(new))
    assert old.read_bytes() == b"new" and os.access(old, os.X_OK)
    assert started[0][0] == [str(old)] and not new.exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ManifoldMeshMix.AppImage"]


def test_install_target_without_installer(monkeypatch):
    monkeypatch.delenv("APPIMAGE", raising=False)
    ok, why = updater.install_target()
    assert not ok and why


def test_update_addresses_are_restricted(monkeypatch):
    """The test override only works for a server on this computer, and
    downloads must use HTTPS (plain HTTP only from such a test server)."""
    monkeypatch.setenv("MMM_UPDATE_URL", "http://example.com/latest")
    assert updater._api_url() == updater.API_LATEST
    monkeypatch.setenv("MMM_UPDATE_URL", "http://127.0.0.1:8000/latest")
    assert updater._api_url() == "http://127.0.0.1:8000/latest"
    assert updater._allowed("https://github.com/x/y.exe")
    assert updater._allowed("http://localhost:9/a")
    assert not updater._allowed("http://github.com/x/y.exe")
    assert not updater._allowed("ftp://127.0.0.1/a")
    with pytest.raises(updater.UpdateError):
        updater._open("http://github.com/x/y.exe")
