"""Finds, downloads and installs a newer version from the project's GitHub
releases.

Every release carries one file per system and a SHA256SUMS.txt:

    MeshMix-S2C-<version>-Windows-x64-Setup.exe
    MeshMix-S2C-<version>-Linux-x86_64.AppImage
    MeshMix-S2C-<version>-macOS-arm64.zip

Windows runs the new installer silently (it installs for the current user
only, so no administrator password is needed) and starts the app again.
The Linux AppImage replaces itself and starts again.
"""
import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

from . import REPO, REPO_URL, __version__, system

API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"{REPO_URL}/releases/latest"
SUMS_NAME = "SHA256SUMS.txt"
WINDOWS_MUTEX = "ManifoldMeshMixRunning"


def asset_suffix():
    if system.IS_WINDOWS:
        return "-Windows-x64-Setup.exe"
    if system.IS_MAC:
        return "-macOS-arm64.zip"
    return "-Linux-x86_64.AppImage"


def parse_version(s):
    """'v1.2.3' / '1.2' / '1.2.3-beta' -> (1, 2, 3); None when it isn't a version."""
    if not isinstance(s, str):
        return None
    m = re.match(r"^\s*[vV]?(\d+(?:\.\d+)*)", s)
    if not m:
        return None
    parts = [int(x) for x in m.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def version_key(s):
    """parse_version plus test builds: '2.0.0-test.2' comes after
    '2.0.0-test.1' and before '2.0.0', so a tester is offered the release."""
    base = parse_version(s)
    if base is None:
        return None
    rest = re.sub(r"^\s*[vV]?\d+(?:\.\d+)*", "", s).strip()
    if not rest.startswith("-"):
        return base + (1, 0)
    n = re.findall(r"\d+", rest)
    return base + (0, int(n[-1]) if n else 0)


def is_newer(latest, current=__version__):
    a, b = version_key(latest), version_key(current)
    return a is not None and b is not None and a > b


class UpdateError(Exception):
    """A problem in plain words for the user."""


class UpdateInfo:
    def __init__(self, version, notes, asset_name, asset_url, size, sums_url, page_url):
        self.version = version
        self.notes = notes
        self.asset_name = asset_name
        self.asset_url = asset_url
        self.size = size
        self.sums_url = sums_url
        self.page_url = page_url


def _is_local(url):
    return (urllib.parse.urlsplit(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


def _allowed(url):
    """Updates only come over HTTPS (plain HTTP only from a test server on
    this computer)."""
    return url.startswith("https://") or (url.startswith("http://") and _is_local(url))


def _api_url():
    # MMM_UPDATE_URL is for tests against a server on this computer only
    url = os.environ.get("MMM_UPDATE_URL")
    return url if url and _is_local(url) else API_LATEST


def _ssl_context():
    ctx = ssl.create_default_context()
    try:  # a packaged app may not find the system's certificates on every Linux
        import certifi

        ctx.load_verify_locations(certifi.where())
    except Exception:
        pass
    return ctx


def _open(url, timeout=30):
    if not _allowed(url):
        raise UpdateError("The update's download address isn't secure, so it wasn't used.")
    req = urllib.request.Request(url, headers={
        "User-Agent": f"ManifoldMeshMix/{__version__} ({system.OS_NAME})",
        "Accept": "application/vnd.github+json" if "api.github.com" in url else "*/*",
    })
    try:
        if url.startswith("https:"):
            return urllib.request.urlopen(req, timeout=timeout, context=_ssl_context())
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("No release has been published yet.") from e
        if e.code in (403, 429):
            raise UpdateError("GitHub is limiting update checks right now. Try again in an hour.") from e
        raise UpdateError(f"GitHub answered with an error ({e.code}). Try again later.") from e
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError("Couldn't reach GitHub. Check your internet connection and try again.") from e


def check():
    """Returns UpdateInfo for a newer release for this system, or None when
    this version is the newest."""
    with _open(_api_url()) as r:
        try:
            data = json.loads(r.read().decode("utf-8"))
        except ValueError as e:
            raise UpdateError("GitHub sent an answer the app doesn't understand. Try again later.") from e
    tag = data.get("tag_name") or ""
    if parse_version(tag) is None:
        raise UpdateError("The newest release has no version number the app understands.")
    # test builds are GitHub pre-releases, which /releases/latest already skips;
    # this guard keeps them from ever being offered even if one is mislabelled
    if data.get("prerelease") or "-" in tag:
        return None
    if not is_newer(tag):
        return None
    suffix = asset_suffix().lower()
    asset = sums = None
    for a in data.get("assets") or []:
        name = a.get("name") or ""
        if name.lower().endswith(suffix):
            asset = a
        elif name == SUMS_NAME:
            sums = a
    version = tag.lstrip("vV")
    page = data.get("html_url") or RELEASES_PAGE
    if asset is None:
        raise UpdateError(f"Version {version} is out, but it has no download for {system.OS_NAME} yet.")
    if sums is None:
        raise UpdateError(f"Version {version} is out, but its checksum file is missing, so it can't be installed safely.")
    return UpdateInfo(version, data.get("body") or "", asset["name"], asset["browser_download_url"],
                      int(asset.get("size") or 0), sums["browser_download_url"], page)


def download(info, progress=None, cancelled=lambda: False, folder=None):
    """Downloads the release file, checks it against SHA256SUMS.txt and
    returns its path."""
    with _open(info.sums_url) as r:
        sums_text = r.read().decode("utf-8", "replace")
    expected = None
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == info.asset_name:
            expected = parts[0].lower()
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise UpdateError("The update's checksum is missing, so it can't be installed safely.")
    # a new private folder (only this user can write to it): a shared,
    # predictable one would let someone else swap the file
    folder = folder or tempfile.mkdtemp(prefix="ManifoldMeshMix-update-")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, os.path.basename(info.asset_name))
    part = dest + ".download"
    h = hashlib.sha256()
    got = 0
    try:
        with _open(info.asset_url, timeout=60) as r, open(part, "wb") as fh:
            total = int(r.headers.get("Content-Length") or info.size or 0)
            while True:
                if cancelled():
                    raise UpdateError("The download was cancelled.")
                chunk = r.read(256 * 1024)
                if not chunk:
                    break
                fh.write(chunk)
                h.update(chunk)
                got += len(chunk)
                if progress and total:
                    progress(min(1.0, got / total))
    except OSError as e:
        _remove(part)
        raise UpdateError("The download stopped. Check your internet connection and free disk space, then try again.") from e
    except BaseException:
        _remove(part)
        raise
    if h.hexdigest() != expected:
        _remove(part)
        raise UpdateError("The downloaded file doesn't match its checksum, so it wasn't installed. Try again.")
    os.replace(part, dest)
    _verified[os.path.abspath(dest)] = expected
    return dest


# checksums of the downloads this run verified (checked again right before
# installing, in case the file changed since)
_verified = {}


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _check_again(path):
    expected = _verified.get(os.path.abspath(path))
    try:
        ok = expected is not None and _sha256(path) == expected
    except OSError:
        ok = False
    if not ok:
        raise UpdateError("The downloaded update changed or disappeared since it was checked, so it wasn't installed. "
                          "Download it again.")


def _remove(p):
    try:
        os.remove(p)
    except OSError:
        pass


def install_target():
    """Where this copy of the app is installed, and whether it can update
    itself: (can_update, reason_when_not)."""
    if system.IS_WINDOWS:
        if not system.FROZEN:
            return False, "This copy runs from the source code, so it can't update itself."
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if not any(n.lower().startswith("unins") and n.lower().endswith(".exe") for n in os.listdir(exe_dir)):
            return False, "This copy wasn't installed with the installer, so it can't update itself."
        return True, ""
    if system.IS_LINUX:
        appimage = os.environ.get("APPIMAGE")
        if not appimage or not os.path.isfile(appimage):
            return False, "Only the AppImage version can update itself."
        if not os.access(os.path.dirname(appimage), os.W_OK) or not os.access(appimage, os.W_OK):
            return False, f"The folder with the AppImage ({os.path.dirname(appimage)}) can't be written to."
        return True, ""
    return False, "Updates for this system are installed by the Mac app."


def relaunch_env():
    """Environment for the installer and the restarted app: the user's own,
    without this run's test settings."""
    # the new copy waits for this one to quit (instead of handing its files
    # to it as a second window would)
    env = system.clean_env({"MMM_JUST_UPDATED": "1", "MMM_WAIT_FOR_PID": str(os.getpid())})
    for k in list(env):
        if k.startswith("MMM_SELFTEST") or k in ("MMM_UPDATE_URL", "MMM_SETTINGS_FILE"):
            env.pop(k)
    return env


def install(path):
    """Starts installing the downloaded update. The caller quits the app
    right after; the new version starts by itself."""
    _check_again(path)
    if system.IS_WINDOWS:
        # The installer waits until this app has quit (it watches the
        # app's mutex), installs silently and starts the app again.
        args = [path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/relaunch=1"]
        if os.environ.get("MMM_INSTALLER_LOG"):  # for tests and troubleshooting
            args.append("/LOG=" + os.environ["MMM_INSTALLER_LOG"])
        system.popen_detached(args, cwd=os.path.dirname(path), env=relaunch_env())
        return
    if system.IS_LINUX:
        target = os.environ["APPIMAGE"]
        folder = os.path.dirname(target)
        tmp = os.path.join(folder, f".{os.path.basename(target)}.update")
        try:
            with open(path, "rb") as src, open(tmp, "wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    dst.write(chunk)
            os.chmod(tmp, 0o755)
            # the running copy keeps working: Linux keeps the old file alive
            # until it is closed
            os.replace(tmp, target)
        except OSError as e:
            _remove(tmp)
            raise UpdateError(f"The AppImage couldn't be replaced ({e.strerror or e}).") from e
        _remove(path)
        try:
            os.rmdir(os.path.dirname(path))
        except OSError:
            pass
        system.popen_detached([target], cwd=os.path.expanduser("~"), env=relaunch_env())
        return
    raise UpdateError("Updates for this system are installed by the Mac app.")


def hold_windows_mutex():
    """Windows: a named mutex that tells the installer the app is still
    running (it waits for it to disappear before replacing files)."""
    if not system.IS_WINDOWS:
        return None
    import ctypes

    k32 = ctypes.WinDLL("kernel32")
    k32.CreateMutexW.restype = ctypes.c_void_p
    return k32.CreateMutexW(None, False, WINDOWS_MUTEX)
