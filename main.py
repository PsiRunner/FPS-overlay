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


def main():
    global LOG_PATH
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) \
        else Path(__file__).resolve().parent
    LOG_PATH = base / "fps_overlay.log"

    app = QApplication(sys.argv)

    overlay = FpsOverlay()
    overlay.show()

    worker = FpsWorker()
    worker.fps_updated.connect(lambda fps: overlay.set_text(str(fps)))
    worker.status_message.connect(lambda msg: _on_status(overlay, msg))
    worker.start()

    exit_code = app.exec()
    worker.stop()
    worker.wait(2000)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
