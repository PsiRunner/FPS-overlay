"""Start-with-Windows support via Task Scheduler.

Why not the usual HKCU\\...\\Run registry key? This app always runs
elevated (PresentMon needs ETW), and a Run-key launch would trigger a
UAC prompt on every login. A scheduled task created with /RL HIGHEST
starts silently at logon with full admin rights - no prompt.

Task name is fixed ("FpsOverlay") so enable/disable/query are trivial.
"""
import subprocess
import sys
from pathlib import Path

TASK_NAME = "FpsOverlay"
_CREATE_NO_WINDOW = 0x08000000


def _run(args) -> int:
    """Run a schtasks command, return its exit code (0 = success)."""
    try:
        done = subprocess.run(
            ["schtasks"] + args,
            capture_output=True, text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        return done.returncode
    except OSError:
        return 1


def _launch_target() -> str:
    """The command line the task should run: the built .exe when frozen,
    otherwise pythonw.exe + main.py so no console window appears."""
    if getattr(sys, "frozen", False):
        exe = sys.executable
    else:
        py = Path(sys.executable).with_name("pythonw.exe")
        if not py.exists():
            py = Path(sys.executable)
        script = Path(__file__).resolve().parent / "main.py"
        return f'"{py}" "{script}"'
    return f'"{exe}"'


def is_enabled() -> bool:
    return _run(["/Query", "/TN", TASK_NAME]) == 0


def enable() -> bool:
    # /F overwrites an existing task (also repairs a stale exe path)
    code = _run([
        "/Create", "/TN", TASK_NAME,
        "/TR", _launch_target(),
        "/SC", "ONLOGON",     # every login, this user only
        "/RL", "HIGHEST",     # elevated -> no UAC prompt at startup
        "/F",
    ])
    return code == 0 and is_enabled()


def disable() -> bool:
    if not is_enabled():
        return True
    return _run(["/Delete", "/TN", TASK_NAME, "/F"]) == 0
