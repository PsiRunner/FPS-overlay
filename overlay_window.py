"""The overlay itself: a frameless, fully transparent, always-on-top label
that sits over your game. Shows nothing but the FPS number - no status
text, no background box, ever. When there's no game to measure it shows
"0", so the counter is always on screen and always adjustable.

- Left-click + drag: move it anywhere on screen.
- Mouse wheel over it: grow/shrink the font one point at a time.
- Right-click: menu -> text color, font family, font size, exit.
Everything is saved automatically to config.json.
"""
import ctypes
import sys

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QApplication, QColorDialog, QMenu, QWidget

from autostart import disable as autostart_disable, enable as autostart_enable, is_enabled as autostart_enabled
from config import load_config, save_config

# Fonts that suit an FPS counter. The first group is the classic
# esports/sci-fi look (free Google Fonts - they only appear in the menu
# if you've actually installed them); the second group ships with Windows.
GAMING_FONTS = [
    # esports / sci-fi (install the TTFs to unlock these)
    "Orbitron", "Rajdhani", "Chakra Petch", "Audiowide",
    "Exo 2", "Teko", "Bebas Neue", "Press Start 2P",
    # built into Windows, all read well as a counter
    "Bahnschrift", "Consolas", "Segoe UI", "Arial Black",
    "Impact", "Franklin Gothic Medium", "Verdana", "Tahoma",
]
FONT_SIZES = [12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 64]
MIN_FONT_SIZE, MAX_FONT_SIZE = 8, 200

# --- Windows-only constants for two native tweaks Qt doesn't expose ---
# 1) Windows 11 auto-applies a "Mica" backdrop + rounded corners to plain
#    frameless windows, which is exactly the lavender rounded box you saw
#    instead of true transparency. These DWM attributes turn that off.
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWCP_DONOTROUND = 1
_DWMWA_SYSTEMBACKDROP_TYPE = 38
_DWMSBT_NONE = 1

# 2) Some games (especially borderless/windowed ones) can push an
#    always-on-top window behind themselves during play. Re-asserting
#    HWND_TOPMOST periodically keeps the overlay pinned above them.
_HWND_TOPMOST = -1
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010


class FpsOverlay(QWidget):
    def __init__(self):
        super().__init__()
        self.cfg = load_config()
        self._text = "0"   # visible from launch so it can be dragged/styled
        self._drag_offset = None

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet("background: transparent;")

        self._apply_font()
        self.move(self.cfg["pos_x"], self.cfg["pos_y"])

        # keep re-pinning ourselves above the game every couple seconds
        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._reassert_topmost)
        self._topmost_timer.start(2000)

    def _apply_font(self):
        """(Re)build the font + metrics from config and refit the window."""
        self._font = QFont(
            self.cfg["font_family"], self.cfg["font_size"], QFont.Weight.Bold
        )
        self._metrics = QFontMetrics(self._font)
        self.set_text(self._text)

    def set_text(self, text: str):
        """Only ever called with a bare number ('' shows nothing at all)."""
        self._text = text
        pad_x, pad_y = 16, 10
        w = self._metrics.horizontalAdvance(text) + pad_x if text else 60
        h = self._metrics.height() + pad_y
        self.resize(max(w, 60), h)
        self.update()

    def paintEvent(self, event):
        if not self._text:
            return  # fully blank - nothing painted, nothing visible
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self._font)
        rect = self.rect()
        align = Qt.AlignmentFlag.AlignCenter

        # thin black outline behind the text so it stays readable no
        # matter what's happening in the game behind it
        painter.setPen(QPen(QColor(0, 0, 0, 220)))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            painter.drawText(rect.translated(dx, dy), align, self._text)

        painter.setPen(QColor(self.cfg["color"]))
        painter.drawText(rect, align, self._text)

    def showEvent(self, event):
        super().showEvent(event)
        self._disable_windows_backdrop()
        self._reassert_topmost()

    # --- Windows-only native tweaks ---
    def _disable_windows_backdrop(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            dwmapi = ctypes.windll.dwmapi
            backdrop = ctypes.c_int(_DWMSBT_NONE)
            dwmapi.DwmSetWindowAttribute(
                hwnd, _DWMWA_SYSTEMBACKDROP_TYPE,
                ctypes.byref(backdrop), ctypes.sizeof(backdrop),
            )
            corner = ctypes.c_int(_DWMWCP_DONOTROUND)
            dwmapi.DwmSetWindowAttribute(
                hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE,
                ctypes.byref(corner), ctypes.sizeof(corner),
            )
        except Exception:
            pass

    def _reassert_topmost(self):
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowPos(
                hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE,
            )
        except Exception:
            pass

    # --- dragging + right-click settings menu ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
        elif event.button() == Qt.MouseButton.RightButton:
            self._open_menu()

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self._drag_offset is not None:
            self._drag_offset = None
            self.cfg["pos_x"] = self.x()
            self.cfg["pos_y"] = self.y()
            save_config(self.cfg)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        step = 1 if delta > 0 else -1
        new_size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, self.cfg["font_size"] + step))
        if new_size != self.cfg["font_size"]:
            self.cfg["font_size"] = new_size
            save_config(self.cfg)
            self._apply_font()

    def _open_menu(self):
        menu = QMenu(self)

        color_act = menu.addAction("Text color...")

        font_menu = menu.addMenu("Font")
        installed = set(QFontDatabase.families())
        current_family = self.cfg["font_family"]
        families = [f for f in GAMING_FONTS if f in installed]
        if current_family not in families:
            families.insert(0, current_family)  # keep its checkmark visible
        for family in families:
            act = font_menu.addAction(family)
            act.setCheckable(True)
            act.setChecked(family == current_family)
            act.triggered.connect(lambda _, f=family: self._set_font_family(f))
        if not families:
            font_menu.addAction("(no fonts found)").setEnabled(False)

        size_menu = menu.addMenu("Font size")
        current_size = self.cfg["font_size"]
        for size in sorted(set(FONT_SIZES + [current_size])):
            act = size_menu.addAction(str(size))
            act.setCheckable(True)
            act.setChecked(size == current_size)
            act.triggered.connect(lambda _, s=size: self._set_font_size(s))

        menu.addSeparator()
        auto_act = menu.addAction("Start with Windows")
        auto_act.setCheckable(True)
        auto_act.setChecked(autostart_enabled())
        auto_act.triggered.connect(self._toggle_autostart)
        exit_act = menu.addAction("Exit")

        color_act.triggered.connect(self._pick_color)
        exit_act.triggered.connect(QApplication.instance().quit)
        menu.exec(self.cursor().pos())

    def _toggle_autostart(self, checked: bool):
        ok = autostart_enable() if checked else autostart_disable()
        if not ok:
            # revert the checkbox so it never lies about the real state
            sender = self.sender()
            if sender:
                sender.setChecked(not checked)
        print(f"[fps overlay] start with windows: "
              f"{'enabled' if checked and ok else 'disabled'}")

    def _set_font_family(self, family: str):
        self.cfg["font_family"] = family
        save_config(self.cfg)
        self._apply_font()

    def _set_font_size(self, size: int):
        self.cfg["font_size"] = int(size)
        save_config(self.cfg)
        self._apply_font()

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self.cfg["color"]), self, "Pick FPS text color")
        if color.isValid():
            self.cfg["color"] = color.name()
            save_config(self.cfg)
            self.update()
