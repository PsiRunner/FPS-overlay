"""Self-update support: checks GitHub releases, downloads the new
FpsOverlay.exe asset and swaps it in.

Windows won't let a running .exe be deleted or overwritten - but it CAN
be renamed. So the swap is: running exe -> FpsOverlay.exe.old, downloaded
file -> FpsOverlay.exe, relaunch, quit. The stale .old file is deleted
on the next startup (see cleanup_old_exe in main.py).

Only meaningful when frozen: from source there is no single exe to
replace (sys.executable is python.exe), so callers should hide update
UI unless getattr(sys, "frozen", False).
"""
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

APP_VERSION = "1.1.0"
REPO = "PsiRunner/FPS-overlay"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "FpsOverlay.exe"
CREATE_NO_WINDOW = 0x08000000


def version_tuple(s: str):
    """'v1.2.3' -> (1, 2, 3). Non-numeric parts are ignored."""
    parts = []
    for x in s.lstrip("vV ").split("."):
        digits = "".join(ch for ch in x if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) if parts else (0,)


def fetch_latest_release() -> dict:
    """Returns the 'latest' release JSON from the GitHub API.
    Raises urllib.error.URLError / HTTPError on network problems."""
    req = urllib.request.Request(
        API_URL,
        headers={
            "User-Agent": f"FPSOverlay/{APP_VERSION}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


class UpdaterWorker(QThread):
    """Runs the check + download off the GUI thread.

    Signals (exactly one of these terminates a run):
      up_to_date(str)   - already on the newest version
      failed(str)       - network/parse/disk problem, message explains
      update_ready(str) - download finished; str is path to the new exe
    """
    status = pyqtSignal(str)
    up_to_date = pyqtSignal(str)
    failed = pyqtSignal(str)
    update_ready = pyqtSignal(str)

    def __init__(self, exe_path: Path):
        super().__init__()
        self._exe_path = exe_path
        self._stop = False

    def stop(self):
        self._stop = True

    def run(self):
        try:
            self.status.emit("Checking for updates...")
            rel = fetch_latest_release()
            tag = rel.get("tag_name", "")
            if version_tuple(tag) <= version_tuple(APP_VERSION):
                self.up_to_date.emit(f"Already on the latest version (v{APP_VERSION}).")
                return

            asset = next((a for a in rel.get("assets", [])
                          if a.get("name") == ASSET_NAME), None)
            if asset is None:
                self.failed.emit(f"Release {tag} has no {ASSET_NAME} asset.")
                return

            url = asset["browser_download_url"]
            expected = int(asset.get("size", 0))
            tmp = self._exe_path.with_suffix(".exe.new")
            self.status.emit(f"Downloading {tag}...")

            req = urllib.request.Request(url, headers={"User-Agent": f"FPSOverlay/{APP_VERSION}"})
            done = 0
            with urllib.request.urlopen(req, timeout=30) as r, open(tmp, "wb") as f:
                while True:
                    if self._stop:
                        tmp.unlink(missing_ok=True)
                        return
                    chunk = r.read(262144)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    if expected:
                        self.status.emit(f"Downloading {tag}... {done * 100 // expected}%")

            if expected and done != expected:
                tmp.unlink(missing_ok=True)
                self.failed.emit(f"Download incomplete ({done}/{expected} bytes).")
                return

            self.update_ready.emit(str(tmp))
        except Exception as e:  # network, JSON, disk - report anything
            self.failed.emit(f"{type(e).__name__}: {e}")


def apply_update(new_exe: str) -> bool:
    """Swap the freshly downloaded exe into place and relaunch.
    Returns True when the new instance was started."""
    exe = Path(sys.executable).resolve()
    old = exe.with_suffix(".exe.old")
    old.unlink(missing_ok=True)
    exe.rename(old)                      # allowed even while we're running
    Path(new_exe).replace(exe)           # move download into place
    subprocess.Popen(
        [str(exe)],
        creationflags=CREATE_NO_WINDOW,
        cwd=str(exe.parent),
    )
    return True
