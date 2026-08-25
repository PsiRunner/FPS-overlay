"""Background worker that:

1. Watches which window is currently in the foreground.
2. Launches PresentMon (Microsoft/Intel's open-source frame-capture tool)
   targeted at that process, so we get real Present() timings straight
   from the OS instead of guessing.
3. Turns those timings into a rolling-average FPS number a few times a
   second and emits it to the overlay widget.

PresentMon uses Event Tracing for Windows (ETW) to observe frames -
it does not inject any code into the game, which is why it needs to be
run elevated (Administrator) but doesn't touch the game process itself.
"""
import ctypes
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import psutil
from PyQt6.QtCore import QThread, pyqtSignal

# Windows/system processes we should never mistake for "the game"
IGNORED_PROCESSES = {
    "explorer.exe", "dwm.exe", "searchhost.exe", "shellexperiencehost.exe",
    "textinputhost.exe", "applicationframehost.exe", "systemsettings.exe",
    "python.exe", "pythonw.exe", "cmd.exe", "powershell.exe",
    "windowsterminal.exe", "conhost.exe", "taskmgr.exe",
}

FOREGROUND_CHECK_INTERVAL = 1.0   # how often we check "did the user alt-tab?"
FPS_EMIT_INTERVAL = 0.5           # how often we compute a fresh average
EMA_ALPHA = 0.5                   # smoothing factor: lower = steadier, higher = snappier


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def get_foreground_process_name(self_pid: int) -> Optional[str]:
    """Returns the exe name of whatever window currently has focus, or
    None if it's this overlay itself / a system window."""
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if not pid.value or pid.value == self_pid:
        return None
    try:
        name = psutil.Process(pid.value).name()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    lname = name.lower()
    if lname in IGNORED_PROCESSES or lname.startswith("presentmon"):
        return None
    return name


def _app_dirs():
    """Places to look for PresentMon: the PyInstaller temp extraction dir
    (when bundled inside FpsOverlay.exe) and the folder containing the
    script / the built .exe itself."""
    dirs = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)
    dirs.append(Path(__file__).resolve().parent)
    return dirs


def find_presentmon_exe() -> Optional[Path]:
    for d in _app_dirs():
        matches = sorted(d.glob("PresentMon*.exe"))
        if matches:
            return matches[0]
    return None


class _PresentMonSession:
    """Owns one PresentMon subprocess targeting a single process name, plus
    a background thread that parses its CSV stdout and pushes each frame's
    MsBetweenPresents value (float, milliseconds) onto a queue. Puts None
    on the queue once PresentMon exits (e.g. the game closed)."""

    def __init__(self, exe_path: Path, process_name: str):
        self.process_name = process_name
        self.samples: "queue.Queue" = queue.Queue()
        cmd = [
            str(exe_path),
            "--process_name", process_name,
            "--output_stdout",
            "--no_csv",
            "--stop_existing_session",
            "--terminate_on_proc_exit",
        ]
        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        header = None
        mbp_idx = None
        stdout = self.proc.stdout
        for line in iter(stdout.readline, ""):
            row = line.rstrip("\r\n").split(",")
            if header is None:
                header = row
                if "MsBetweenPresents" not in header:
                    break
                mbp_idx = header.index("MsBetweenPresents")
                continue
            try:
                ms = float(row[mbp_idx])
            except (ValueError, IndexError):
                continue
            if ms > 0:
                self.samples.put(ms)
        self.samples.put(None)

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=2)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass
        # PresentMon runs FROM our PyInstaller _MEI temp dir; give the OS
        # a moment to release its file handles so the on-exit cleanup of
        # that directory doesn't fail with "Failed to remove temporary
        # directory" (seen right after a self-update restart).
        time.sleep(0.3)


class FpsWorker(QThread):
    fps_updated = pyqtSignal(int)      # smoothed, whole-number FPS
    status_message = pyqtSignal(str)   # e.g. "Waiting for a game..."

    def __init__(self):
        super().__init__()
        self._running = True
        self._presentmon_path = find_presentmon_exe()
        self._smoothed_fps = None      # EMA of the raw fps readings
        self._last_shown = None        # last integer value we actually emitted
        self._session = None           # active _PresentMonSession (for external kill)

    def stop(self):
        self._running = False

    def kill_presentmon(self):
        """Force-kill the PresentMon child from OUTSIDE the worker thread.

        Last-resort cleanup for the update restart: if this thread is
        stuck in a blocking pipe read, stop()/wait() can time out without
        close() ever running - and a surviving PresentMon keeps our _MEI
        temp dir locked, which is what produced the 'Failed to remove
        temporary directory' warning after a self-update."""
        s = self._session
        if s is None:
            return
        try:
            p = psutil.Process(s.proc.pid)
            if p.is_running():
                p.kill()
                p.wait(timeout=3)
        except psutil.Error:
            pass
        except Exception:
            pass
        time.sleep(0.2)                # let the OS release the file handles

    def run(self):
        if sys.platform != "win32":
            self.status_message.emit("Windows only")
            return
        if self._presentmon_path is None:
            self.status_message.emit("PresentMon .exe missing")
            return
        if not is_admin():
            self.status_message.emit("Run as Administrator")
            return

        self_pid = ctypes.windll.kernel32.GetCurrentProcessId()
        session: Optional[_PresentMonSession] = None
        buffer = []
        last_fg_check = 0.0
        last_emit = 0.0

        while self._running:
            now = time.time()

            if now - last_fg_check >= FOREGROUND_CHECK_INTERVAL:
                last_fg_check = now
                target = get_foreground_process_name(self_pid)
                current = session.process_name if session else None
                if target != current:
                    if session:
                        session.close()
                        session = None
                        self._session = None
                    buffer.clear()
                    self._smoothed_fps = None
                    self._last_shown = None
                    if target:
                        session = _PresentMonSession(self._presentmon_path, target)
                        self._session = session
                        self.status_message.emit(f"Tracking {target}")
                    else:
                        self.status_message.emit("Waiting for a game...")

            if session is None:
                time.sleep(0.1)
                continue

            try:
                item = session.samples.get(timeout=0.2)
                got_item = True
            except queue.Empty:
                item = None
                got_item = False

            if got_item and item is None:
                # PresentMon exited on its own -> game likely closed
                session.close()
                session = None
                self._session = None
                buffer.clear()
                self._smoothed_fps = None
                self._last_shown = None
                self.status_message.emit("Waiting for a game...")
                continue
            if got_item:
                buffer.append(item)

            now = time.time()
            if buffer and now - last_emit >= FPS_EMIT_INTERVAL:
                last_emit = now
                avg_ms = sum(buffer) / len(buffer)
                buffer.clear()
                if avg_ms <= 0:
                    continue

                raw_fps = 1000.0 / avg_ms
                if self._smoothed_fps is None:
                    self._smoothed_fps = raw_fps
                else:
                    self._smoothed_fps += EMA_ALPHA * (raw_fps - self._smoothed_fps)

                shown = round(self._smoothed_fps)
                if shown != self._last_shown:
                    self._last_shown = shown
                    self.fps_updated.emit(shown)

        if session:
            session.close()
            self._session = None
