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
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal

APP_VERSION = "1.1.5"
REPO = "PsiRunner/FPS-overlay"
API_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "FpsOverlay.exe"
DOWNLOAD_TIMEOUT = 15   # seconds of socket inactivity before we call it stalled
MAX_RETRIES = 5         # stalled downloads resume up to this many times


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

    'status' fires sparingly: only at 0/25/50/75% milestones - never per
    percent - so the tray doesn't spam notifications.
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

    def _headers(self, resume_from: int = 0) -> dict:
        h = {"User-Agent": f"FPSOverlay/{APP_VERSION}"}
        if resume_from:
            h["Range"] = f"bytes={resume_from}-"
        return h

    def _download(self, url: str, dest: Path, expected: int, tag: str):
        """Download with resume-on-retry. A stalled/dropped connection
        continues from where it left off instead of failing outright.
        The socket timeout doubles as stall detection: if no bytes
        arrive for DOWNLOAD_TIMEOUT seconds the read raises, we tell
        the user we're resuming, and reconnect with a Range header."""
        last_milestone = -1
        for attempt in range(1, MAX_RETRIES + 1):
            if self._stop:
                return
            done = dest.stat().st_size if dest.exists() else 0
            try:
                req = urllib.request.Request(url, headers=self._headers(done))
                mode = "ab" if done else "wb"
                with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT) as r:
                    if done and getattr(r, "status", 206) != 206:
                        mode, done = "wb", 0     # server ignored Range -> restart
                    with open(dest, mode) as f:
                        while True:
                            if self._stop:
                                return
                            chunk = r.read(262144)
                            if not chunk:
                                break
                            f.write(chunk)
                            done += len(chunk)
                            if expected:
                                pct = min(100, done * 100 // expected)
                                if pct >= last_milestone + 25:
                                    last_milestone = 25 * (pct // 25)
                                    self.status.emit(f"Downloading {tag}... "
                                                     f"{last_milestone}%")
                if not expected or done >= expected:
                    return                       # complete
            except Exception:
                if attempt >= MAX_RETRIES:
                    raise
                if done:                         # tell the user what happened
                    self.status.emit(f"Connection stalled - resuming "
                                     f"{tag} at {done * 100 // max(expected, 1)}%...")
                time.sleep(min(1.5 * attempt, 5))  # brief back off, then resume
        raise RuntimeError("download did not complete")

    def run(self):
        tmp = None
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
            self._download(url, tmp, expected, tag)

            actual = tmp.stat().st_size
            if expected and actual != expected:
                tmp.unlink(missing_ok=True)
                self.failed.emit(f"Download incomplete ({actual}/{expected} bytes).")
                return

            self.update_ready.emit(str(tmp))
        except Exception as e:  # network, JSON, disk - report anything
            if tmp is not None:
                tmp.unlink(missing_ok=True)
            self.failed.emit(f"{type(e).__name__}: {e}")


def apply_update(new_exe: str) -> None:
    """Swap the freshly downloaded exe into place and restart cleanly.

    The running exe can't be deleted or overwritten on Windows, but it
    CAN be renamed - so: current -> .exe.old, download -> current.

    The restart itself is handed to a DETACHED PowerShell helper that
    runs after we're gone:
        1. wait for THIS process to fully exit
        2. force-remove our _MEI temp dir and the .exe.old backup
        3. start the new exe (inherits our elevation - no UAC prompt)

    Launching the new exe directly from here (os.startfile) raced with
    our own teardown: the new instance started while the old one was
    still alive, and any lingering handle (usually the PresentMon child
    running from _MEI) made the bootloader's cleanup fail with the modal
    "Failed to remove temporary directory" warning. Sequencing through
    the helper removes the race entirely.
    """
    exe = Path(sys.executable).resolve()
    old = exe.with_suffix(".exe.old")
    old.unlink(missing_ok=True)
    exe.rename(old)
    Path(new_exe).replace(exe)

    # sanity check before we kill ourselves: AV sometimes quarantines a
    # freshly written unsigned exe milliseconds after it lands. If the
    # new file is gone/tiny, roll back instead of bricking the install.
    if not exe.exists() or exe.stat().st_size < 1_000_000:
        old.replace(exe)                 # put the old build back
        raise RuntimeError("new executable disappeared after swap (antivirus?)")

    mei = getattr(sys, "_MEIPASS", "")
    old_pid = os.getpid()

    def ps(s: str) -> str:
        return "'" + s.replace("'", "''") + "'"

    script = (
        f"Wait-Process -Id {old_pid} -Timeout 30 -ErrorAction SilentlyContinue; "
        "Start-Sleep -Milliseconds 300; "
        f"Remove-Item -LiteralPath {ps(mei)} -Recurse -Force -ErrorAction SilentlyContinue; "
        f"Remove-Item -LiteralPath {ps(str(old))} -Force -ErrorAction SilentlyContinue; "
        f"Start-Process -FilePath {ps(str(exe))}"
    )
    # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP: invisible and fully
    # detached - it outlives us and does the launch once we're gone.
    subprocess.Popen(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-Command", script],
        creationflags=0x08000000 | 0x00000200,
        close_fds=True,
    )
