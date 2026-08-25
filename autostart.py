"""Start-with-Windows support via Task Scheduler.

Why not the usual HKCU\\...\\Run registry key? This app always runs
elevated (PresentMon needs ETW), and a Run-key launch would trigger a
UAC prompt on every login. A scheduled task created with /RL HIGHEST
starts silently at logon with full admin rights - no prompt.

Task name is fixed ("FpsOverlay") so enable/disable/query are trivial.
"""
import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "FpsOverlay"
_CREATE_NO_WINDOW = 0x08000000
_POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
LAST_ERROR = ""    # stderr/exit detail of the last failed enable(), for logs


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


def get_task_path() -> str:
    """The command the task currently points at, or '' if unknown."""
    try:
        done = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME, "/V", "/FO", "LIST"],
            capture_output=True, text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        if done.returncode != 0:
            return ""
        for line in done.stdout.splitlines():
            if line.startswith("Task To Run"):
                return line.split(":", 1)[1].strip()
    except OSError:
        pass
    return ""


def is_current() -> bool:
    """True when the task exists AND launches this exact exe/script."""
    target = get_task_path().replace('"', '').strip()
    want = _launch_target().replace('"', '').strip()
    return bool(target) and target.lower() == want.lower()


def _ps(s: str) -> str:
    """Single-quote a string for PowerShell (doubled quotes inside)."""
    return "'" + s.replace("'", "''") + "'"


def enable() -> bool:
    """Create/overwrite the logon task via Register-ScheduledTask.

    NOT schtasks /Create: tasks made that way default to 'do not start
    on battery power' (the task silently queues forever on a laptop that
    is not plugged in) and to a 72-hour execution limit (the overlay
    gets killed mid-session). Register-ScheduledTask lets us set both
    explicitly. Needs elevation - which the frozen app always has.
    """
    global LAST_ERROR
    LAST_ERROR = ""
    if getattr(sys, "frozen", False):
        execute, argument = sys.executable, None
    else:
        py = Path(sys.executable).with_name("pythonw.exe")
        if not py.exists():
            py = Path(sys.executable)
        script = Path(__file__).resolve().parent / "main.py"
        execute, argument = str(py), str(script)

    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}"
    arg_part = f"-Argument {_ps(argument)} " if argument else ""
    script_ps = (
        f"$action = New-ScheduledTaskAction -Execute {_ps(execute)} {arg_part}; "
        f"$trigger = New-ScheduledTaskTrigger -AtLogOn -User {_ps(user)}; "
        "$settings = New-ScheduledTaskSettingsSet "
        "-AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; "
        "$settings.ExecutionTimeLimit = 'PT0S'; "   # PT0S = no time limit
        f"Register-ScheduledTask -TaskName {_ps(TASK_NAME)} -Action $action "
        "-Trigger $trigger -Settings $settings -RunLevel Highest -Force "
        "| Out-Null; "
        "if ($?) { exit 0 } else { exit 1 }"
    )
    try:
        done = subprocess.run(
            [_POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", script_ps],
            capture_output=True, text=True,
            creationflags=_CREATE_NO_WINDOW,
        )
        if done.returncode != 0:
            LAST_ERROR = (done.stderr or "").strip()[:300] or \
                f"powershell exit code {done.returncode}"
            return False
    except OSError as e:
        LAST_ERROR = f"failed to start powershell: {e}"
        return False
    # verify the settings actually stuck before claiming success
    ok = is_enabled()
    if not ok:
        LAST_ERROR = "task missing after Register-ScheduledTask"
    return ok


def ensure_current() -> None:
    """Called once at every startup (frozen only). Re-creates the task
    when autostart is ON: Register-ScheduledTask -Force is idempotent,
    so this silently repairs a stale exe path AND migrates tasks created
    by the old schtasks command (battery-blocked, 72h limit) to healthy
    settings. No-op when autostart is off."""
    if is_enabled():
        enable()


def disable() -> bool:
    if not is_enabled():
        return True
    return _run(["/Delete", "/TN", TASK_NAME, "/F"]) == 0


