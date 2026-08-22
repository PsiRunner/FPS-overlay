import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from fps_worker import FpsWorker
from overlay_window import FpsOverlay

LOG_PATH = None  # set in main(); console output is mirrored here when frozen


def _on_status(overlay, message: str):
    # status text (waiting for a game, needs admin, etc.) goes to the
    # console / log file for troubleshooting - on screen the counter just
    # falls back to "0" so it stays visible and adjustable between games
    line = f"[fps overlay] {message}"
    print(line)
    if LOG_PATH is not None:
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {line}\n")
        except OSError:
            pass
    overlay.set_text("0")


def _cleanup_old_exe():
    """After a self-update the previous version sits around as .exe.old
    (a running exe can be renamed but not deleted). Remove it now that
    nothing is holding it."""
    if not getattr(sys, "frozen", False):
        return
    old = Path(sys.executable).with_suffix(".exe.old")
    try:
        old.unlink(missing_ok=True)
    except OSError:
        pass  # still locked by a second instance - try again next launch


def main():
    global LOG_PATH
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent
    LOG_PATH = base / "fps_overlay.log"
    _cleanup_old_exe()

    app = QApplication(sys.argv)

    overlay = FpsOverlay()
    overlay.show()

    worker = FpsWorker()
    worker.fps_updated.connect(lambda fps: overlay.set_text(str(fps)))
    worker.status_message.connect(lambda msg: _on_status(overlay, msg))
    worker.start()
    overlay.maybe_auto_check_updates()

    def _before_restart():
        """Update swap is about to happen: stop PresentMon/ETW cleanly so
        the old instance's temp dir unlocks and the new instance starts
        into a fresh session."""
        print("[fps overlay] stopping frame tracking for restart...")
        worker.stop()
        worker.wait(6000)

    overlay.before_restart = _before_restart

    exit_code = app.exec()
    worker.stop()
    worker.wait(2000)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
