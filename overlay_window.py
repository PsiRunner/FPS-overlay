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
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import (QColor, QCursor, QFont, QFontDatabase, QFontMetrics,
                         QIcon, QPainter, QPen, QPixmap)
from PyQt6.QtWidgets import (QApplication, QColorDialog, QMenu, QSystemTrayIcon,
                             QWidget)

from autostart import disable as autostart_disable, enable as autostart_enable, is_enabled as autostart_enabled
from config import load_config, save_config
from updater import APP_VERSION, UpdaterWorker, apply_update

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
_HWND_NOTOPMOST = -2
_HWND_TOPMOST = -1
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_GWL_EXSTYLE = -20
_WS_EX_TOPMOST = 0x00000008


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


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

        # keep re-pinning ourselves above the game every second
        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._reassert_topmost)
        self._topmost_timer.start(1000)

        # system tray: the app's real "window" - close/customize from there
        self._upd_worker = None
        self._setup_tray()

    @staticmethod
    def _resource_path(name: str) -> Path:
        """Locate bundled resources (icon.ico) in frozen or source mode."""
        if getattr(sys, "frozen", False):
            for base in (getattr(sys, "_MEIPASS", None), Path(sys.executable).parent):
                if base and (Path(base) / name).exists():
                    return Path(base) / name
        return Path(__file__).resolve().parent / name

    def _setup_tray(self):
        self._tray = QSystemTrayIcon(self)
        icon = QIcon(str(self._resource_path("icon.ico")))
        if icon.isNull():  # fallback: 32x32 green square, never a blank tray
            pm = QPixmap(32, 32)
            pm.fill(QColor("#39FF14"))
            icon = QIcon(pm)
        self._tray.setIcon(icon)
        self._tray.setToolTip(f"FPS Overlay v{APP_VERSION}")
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Context:
            menu = self._build_menu()
            menu.exec(self.tray_popup_pos())
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.setVisible(not self.isVisible())

    @staticmethod
    def tray_popup_pos():
        """Pop the menu near the mouse so it appears by the tray."""
        return QCursor.pos()

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
        """Windows sometimes lets other windows bury us: it can silently
        drop the WS_EX_TOPMOST style, and windows that are themselves
        topmost (some games/launchers) can sit above us inside the
        topmost band. Check both every tick and repair only when needed.

        The repair is the NOTOPMOST->TOPMOST dance, which forces Windows
        to re-insert the window at the very top of the topmost band.
        """
        if sys.platform != "win32":
            return
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32

            ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
            if not (ex_style & _WS_EX_TOPMOST):
                self._bump_topmost(hwnd)          # style was stripped
                return
            if self.isVisible() and self._is_covered(hwnd):
                self._bump_topmost(hwnd)          # another topmost is above us
        except Exception:
            pass

    def _bump_topmost(self, hwnd: int):
        flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE
        user32 = ctypes.windll.user32
        user32.SetWindowPos(hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0, flags)
        user32.SetWindowPos(hwnd, _HWND_TOPMOST, 0, 0, 0, 0, flags)

    def _is_covered(self, hwnd: int) -> bool:
        """True when no point over our text hits OUR window - i.e. some
        window is drawn on top of us right now."""
        try:
            user32 = ctypes.windll.user32
            g = self.mapToGlobal(self.rect()).toRect()
            y = g.center().y()
            for fx in (0.3, 0.4, 0.5, 0.6, 0.7):
                pt = _POINT(g.x() + int(g.width() * fx), y)
                if user32.WindowFromPoint(pt) == hwnd:
                    return False   # at least one spot still shows us on top
            return True
        except Exception:
            return False

    # --- dragging + settings menus (shared by overlay & tray) ---
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
        elif event.button() == Qt.MouseButton.RightButton:
            self._build_menu().exec(self.cursor().pos())

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

    def _build_menu(self) -> QMenu:
        """One menu for both the tray icon and right-clicking the counter.
        Built fresh on every open so checkmarks always reflect reality."""
        menu = QMenu(self)

        show_act = menu.addAction("Show counter")
        show_act.setCheckable(True)
        show_act.setChecked(self.isVisible())
        show_act.toggled.connect(self.setVisible)

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
        auto_win_act = menu.addAction("Start with Windows")
        auto_win_act.setCheckable(True)
        auto_win_act.setChecked(autostart_enabled())
        auto_win_act.triggered.connect(self._toggle_autostart)

        if getattr(sys, "frozen", False):
            upd_act = menu.addAction(f"Check for updates...  (v{APP_VERSION})")
            upd_act.triggered.connect(self._check_updates)
            auto_upd_act = menu.addAction("Auto-check updates at launch")
            auto_upd_act.setCheckable(True)
            auto_upd_act.setChecked(self.cfg.get("auto_update", False))
            auto_upd_act.toggled.connect(self._toggle_auto_update)

        menu.addSeparator()
        exit_act = menu.addAction("Exit")

        color_act.triggered.connect(self._pick_color)
        exit_act.triggered.connect(QApplication.instance().quit)
        return menu

    def _toggle_autostart(self, checked: bool):
        ok = autostart_enable() if checked else autostart_disable()
        if not ok:
            # revert the checkbox so it never lies about the real state
            sender = self.sender()
            if sender:
                sender.setChecked(not checked)
        print(f"[fps overlay] start with windows: "
              f"{'enabled' if checked and ok else 'disabled'}")

    # --- updates (frozen exe only) ---
    def _check_updates(self, silent: bool = False):
        if self._upd_worker is not None:
            return
        exe = Path(sys.executable)
        self._upd_worker = UpdaterWorker(exe)
        self._upd_silent = silent
        if not silent:
            self._upd_worker.status.connect(
                lambda m: self._tray.showMessage("FPS Overlay", m,
                                                 QSystemTrayIcon.MessageIcon.Information))
        self._upd_worker.up_to_date.connect(self._update_up_to_date)
        self._upd_worker.failed.connect(self._update_failed)
        self._upd_worker.update_ready.connect(self._apply_update)
        self._upd_worker.start()

    def _update_up_to_date(self, message: str):
        # silent (auto at launch): "already latest" stays quiet
        if not getattr(self, "_upd_silent", False):
            self._tray.showMessage("FPS Overlay", message,
                                   QSystemTrayIcon.MessageIcon.Information)
        self._upd_worker = None

    def _update_failed(self, message: str):
        icon = QSystemTrayIcon.MessageIcon.Critical if getattr(self, "_upd_silent", False) \
            else QSystemTrayIcon.MessageIcon.Information
        self._tray.showMessage("FPS Overlay", message, icon)
        self._upd_worker = None

    def _apply_update(self, new_exe_path: str):
        """Download complete: swap exes and relaunch. A UAC prompt for the
        new instance is normal - it starts elevated like we do."""
        try:
            self._tray.showMessage("FPS Overlay", "Update downloaded - restarting...",
                                   QSystemTrayIcon.MessageIcon.Information)
            apply_update(new_exe_path)
            QApplication.instance().quit()   # new instance takes over
        except Exception as e:
            self._upd_worker = None
            self._tray.showMessage("FPS Overlay", f"Update failed: {e}",
                                   QSystemTrayIcon.MessageIcon.Critical)

    def _toggle_auto_update(self, checked: bool):
        self.cfg["auto_update"] = bool(checked)
        save_config(self.cfg)
        print(f"[fps overlay] auto-check updates at launch: {checked}")

    def maybe_auto_check_updates(self):
        """Called once at startup by main() when auto_update is enabled."""
        if getattr(sys, "frozen", False) and self.cfg.get("auto_update", False):
            self._check_updates(silent=True)

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
