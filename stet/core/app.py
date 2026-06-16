import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

from PyQt6.QtCore import QObject, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QCursor
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from stet.constants import APP_VERSION, GITHUB_RELEASES_API, SCRIPT_DIR, WINDOWS
from stet.core.clipboard import (
    VK_C,
    VK_V,
    _clipboard_read_text,
    _clipboard_write_text,
    _send_ctrl_chord,
    _send_ctrl_shift_chord,
)
from stet.core.config import ConfigManager
from stet.core.utils import _release_zip_asset, friendly_name, log
from stet.llm.model_manager import ModelManager
from stet.llm.utils import _find_shipped_llama_server

if WINDOWS:
    try:
        import winreg
    except ImportError:
        winreg = None
else:
    winreg = None

from stet.ui.main_window import CorrectionWindow
from stet.ui.osd import SilentCorrectionOSD
from stet.ui.settings import SettingsDialog
from stet.ui.tray import make_tray_icon, make_left_arrow_icon


def _quote_cmd(args: list[str]) -> str:
    return subprocess.list2cmdline(args)


def _source_startup_python() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        pythonw = exe.with_name("pythonw.exe")
        if pythonw.exists():
            return str(pythonw)
    pythonw = shutil.which("pythonw.exe") or shutil.which("pythonw")
    return pythonw or str(exe)


def _startup_command() -> str:
    # Detect compiled builds: PyInstaller sets sys.frozen, but Nuitka does
    # not.  A reliable cross-packager check is whether the running
    # executable is a Python interpreter or the app itself.
    import builtins
    _is_compiled = (
        getattr(sys, "frozen", False)
        or hasattr(builtins, "__nuitka_binary_exe")
        or not Path(sys.executable).stem.lower().startswith("python")
    )

    if _is_compiled:
        # Frozen / Nuitka standalone — the exe IS the launcher.
        # This shows as "Stet.exe" in Task Manager → Startup.
        # Inside Nuitka standalone, sys.executable points to python.exe (which doesn't exist).
        # We resolve the actual executable from SCRIPT_DIR.
        if sys.platform == "win32":
            exe_path = SCRIPT_DIR / "Stet.exe"
        elif sys.platform == "darwin":
            app_bundle = SCRIPT_DIR / "Stet.app"
            exe_path = app_bundle if app_bundle.exists() else Path(sys.executable)
        else:
            exe_path = SCRIPT_DIR / "Stet"

        if not exe_path.exists():
            exe_path = Path(sys.executable)

        return _quote_cmd([str(exe_path)])

    # Source build: use a VBScript wrapper for startup — it sets CWD to
    # the project root before launching pythonw.exe.  Registry startup
    # entries run with CWD = C:\Windows\System32, and some libraries
    # (keyboard hook, config file resolution) behave differently without
    # the correct CWD.
    vbs = SCRIPT_DIR / "startup.vbs"
    if vbs.exists():
        return _quote_cmd(["wscript.exe", str(vbs)])

    # Fallback: direct pythonw.exe invocation
    main_py = SCRIPT_DIR / "stet" / "main.py"
    if main_py.exists():
        return _quote_cmd([_source_startup_python(), str(main_py)])

    return _quote_cmd([_source_startup_python(), "-m", "stet.main"])


import ctypes
import ctypes.wintypes

from PyQt6.QtCore import QAbstractNativeEventFilter

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_VK_MAP = {
    "f1": 0x70,
    "f2": 0x71,
    "f3": 0x72,
    "f4": 0x73,
    "f5": 0x74,
    "f6": 0x75,
    "f7": 0x76,
    "f8": 0x77,
    "f9": 0x78,
    "f10": 0x79,
    "f11": 0x7A,
    "f12": 0x7B,
    "space": 0x20,
    "enter": 0x0D,
    "tab": 0x09,
    "escape": 0x1B,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "pause": 0x13,
    "scroll lock": 0x91,
    "print screen": 0x2C,
    "numlock": 0x90,
    "capslock": 0x14,
    "period": 0xBE,
    ".": 0xBE,
    "slash": 0xBF,
    "/": 0xBF,
    "comma": 0xBC,
    ",": 0xBC,
    "semicolon": 0xBA,
    ";": 0xBA,
    "equal": 0xBB,
    "=": 0xBB,
    "minus": 0xBD,
    "-": 0xBD,
    "tilde": 0xC0,
    "`": 0xC0,
    "bracketleft": 0xDB,
    "[": 0xDB,
    "bracketright": 0xDD,
    "]": 0xDD,
    "backslash": 0xDC,
    "\\": 0xDC,
    "quote": 0xDE,
    "'": 0xDE,
}
for c in range(26):
    _VK_MAP[chr(ord("a") + c)] = 0x41 + c
for c in range(10):
    _VK_MAP[str(c)] = 0x30 + c

_MOD_MAP = {
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "super": MOD_WIN,
}


def parse_hotkey_string(combo: str) -> tuple[int, int]:
    """Parse 'ctrl+shift+f9' -> (MOD_CONTROL|MOD_SHIFT, VK_F9).

    Returns (modifiers, vk_code). Returns (0, 0) on parse failure.
    """
    parts = [p.strip().lower() for p in combo.split("+")]
    mods = MOD_NOREPEAT
    vk = 0
    for part in parts:
        if part in _MOD_MAP:
            mods |= _MOD_MAP[part]
        elif part in _VK_MAP:
            vk = _VK_MAP[part]
        else:
            return 0, 0
    return mods, vk


class WinHotkeyFilter(QAbstractNativeEventFilter):
    """Intercept WM_HOTKEY messages from RegisterHotKey."""

    def __init__(self):
        super().__init__()
        self._callbacks: dict[int, callable] = {}

    def register_callback(self, hotkey_id: int, callback: callable):
        self._callbacks[hotkey_id] = callback

    def clear_callbacks(self):
        self._callbacks.clear()

    def nativeEventFilter(self, event_type, message):
        if (
            event_type == b"windows_generic_MSG"
            or event_type == b"windows_dispatcher_MSG"
        ):
            try:
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == WM_HOTKEY:
                    hotkey_id = msg.wParam
                    callback = self._callbacks.get(hotkey_id)
                    if callback:
                        callback()
                        return True, 0
            except Exception as e:
                log(f"[HotkeyFilter] Error processing native event: {e}")
        return False, 0


def _is_terminal_or_ide(hwnd) -> bool:
    """Check if the foreground window is a terminal/console where Ctrl+C would
    send SIGINT and kill a running process.

    IMPORTANT: This function only blocks actual terminal emulators and shells.
    IDEs, code editors, and runtimes (VS Code, JetBrains, Sublime, Notepad,
    Antigravity, Python, Node, etc.) are NOT blocked — Ctrl+C is a safe copy
    operation in those apps.
    """
    if not hwnd:
        return False

    # 1. Check window class name
    class_name = ctypes.create_unicode_buffer(256)
    ctypes.windll.user32.GetClassNameW(hwnd, class_name, 256)
    class_str = class_name.value.lower()

    # Terminal / console UI class names (NOT generic "ide"/"editor"/"shell")
    terminal_class_keywords = {
        "consolewindowclass",   # Windows Console Host
        "cascadiahostingwindowclass",  # Windows Terminal
        "cascadia",             # Windows Terminal (short form)
        "vte",                  # VTE widget (GTK terminals)
        "mintty",               # Mintty (Cygwin/MSYS2)
        "conhost",              # Console Host (legacy)
        "putty",                # PuTTY
    }
    if any(kw in class_str for kw in terminal_class_keywords):
        log(f"[Capture] Terminal detected by class name: {class_str!r}")
        return True

    # 2. Check process name — only actual terminal emulators and shells.
    # Ctrl+C in these processes sends SIGINT and kills foreground jobs.
    # IDEs (code, jetbrains, sublime, notepad, opencode, antigravity, etc.)
    # and runtimes (python, node, npm, npx) are intentionally NOT listed
    # because Ctrl+C is a safe text-copy operation in those apps.
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h_process = ctypes.windll.kernel32.OpenProcess(
        0x1000, False, pid
    )  # PROCESS_QUERY_LIMITED_INFORMATION
    if h_process:
        try:
            buf = ctypes.create_unicode_buffer(260)
            size = ctypes.c_ulong(260)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                h_process, 0, buf, ctypes.byref(size)
            ):
                proc_name = Path(buf.value).name.lower()

                # Only terminal emulators and shells — NOT IDEs, editors, or runtimes
                terminal_process_keywords = {
                    # Windows
                    "cmd.exe", "powershell.exe", "pwsh.exe", "conhost.exe",
                    "wt.exe",            # Windows Terminal
                    "windowsterminal.exe",
                    "wsl.exe",           # Windows Subsystem for Linux
                    # Unix shells (via MSYS2, Cygwin, Git Bash, WSL, etc.)
                    "bash.exe", "sh.exe", "zsh.exe", "fish.exe",
                    "git-bash.exe",
                    # Terminal emulators
                    "wezterm.exe", "wezterm-gui.exe",
                    "alacritty.exe",
                    "hyper.exe",
                    "tabby.exe",
                    "cmder.exe",
                    "conemu.exe", "conemu64.exe",
                    "kitty.exe",
                    "mintty.exe",
                }

                if any(kw in proc_name for kw in terminal_process_keywords):
                    log(f"[Capture] Terminal detected by process name: {proc_name!r}")
                    return True

                log(
                    f"[Capture] Not a terminal — class={class_str!r} proc={proc_name!r}"
                )
            else:
                log(f"[Capture] QueryFullProcessImageNameW failed for hwnd={hwnd}")
        except Exception as e:
            log(f"[Capture] Exception checking process name: {e}")
        finally:
            ctypes.windll.kernel32.CloseHandle(h_process)
    else:
        log(f"[Capture] OpenProcess failed for pid={pid.value}")

    return False


class StetApp(QObject):
    _trigger = pyqtSignal(str, str)
    _notify = pyqtSignal(str, str)
    _hotkey_signal = pyqtSignal(dict)
    _silent_osd_signal = pyqtSignal(
        str, str
    )  # message, state ('loading'|'success'|'warning')
    _large_doc_warning_signal = pyqtSignal(str)  # text that is too large

    def __init__(self):
        super().__init__()

        qapp = QApplication.instance()
        if qapp:
            qapp.setQuitOnLastWindowClosed(False)
            qapp.setApplicationName("Stet")

        self.cfg = ConfigManager()
        _ac_path_boot = self.cfg.get("model_path", "")
        _chat_path_boot = self.cfg.get("chat_model_path", "")
        log(f"[APP] Boot — chat_use_separate_model: {self.cfg.get('chat_use_separate_model', False)}")
        log(f"[APP] Boot — Autocorrect model: {_ac_path_boot}")
        log(f"[APP] Boot — Chat model: {_chat_path_boot}")
        log(
            f"[APP] Boot — keep_model_loaded: {self.cfg.get('keep_model_loaded', True)}"
        )
        log(f"[APP] Boot — gpu_layers: {self.cfg.get('gpu_layers', 99)}")
        log("[APP] Boot — correction_method: patch (fixed)")
        log(
            f"[APP] Boot — default_strength: {self.cfg.get('streaming_strength', 'smart_fix')}"
        )
        self.ac_model = ModelManager(
            self.cfg,
            model_path_key="model_path",
            label="AC",
            keep_loaded_key="keep_model_loaded",
            idle_timeout_key="idle_timeout_seconds",
        )
        self.chat_model = ModelManager(
            self.cfg,
            model_path_key="chat_model_path",
            label="Chat",
            keep_loaded_key="chat_keep_loaded",
            idle_timeout_key="chat_idle_timeout_seconds",
        )
        self._window: CorrectionWindow | None = None
        self._old_clip = ""
        # Hotkey re-entrancy guard — holding the keys or rapid repeat presses
        # used to spawn overlapping _hotkey_fired threads, each firing its own
        # "no text selected" notification in a feedback loop. This lock ensures
        # only one hotkey flow runs at a time.
        self._hotkey_busy = threading.Lock()
        self._pending_panel_strength = "smart_fix"
        self._last_empty_notify_ts = 0.0
        # Debounce guard for _register_hotkey — prevents rapid re-registration
        # cycles from corrupting keyboard library internal state.
        self._last_register_ts = 0.0
        self._hotkey_handles: list = []

        self._trigger.connect(self._show_window)
        self._notify.connect(self._show_notify)
        self._hotkey_signal.connect(self._handle_hotkey_fired)
        self._silent_osd_signal.connect(self._show_silent_osd)
        self._large_doc_warning_signal.connect(self._on_large_doc_warning)
        self.ac_model.status_changed.connect(self._on_ac_status)
        self.chat_model.status_changed.connect(self._on_chat_status)
        self.ac_model.model_loaded.connect(lambda: self._set_tray_icon("#3b82f6"))
        self.chat_model.model_loaded.connect(lambda: self._set_tray_icon("#a78bfa"))
        self.chat_model.model_unloaded.connect(lambda: self._set_tray_icon("#475569"))
        # Surface tiny-model warnings to the user once, at load time
        self.ac_model.model_warning.connect(self._show_model_warning)
        self.chat_model.model_warning.connect(self._show_model_warning)

        self._hotkey_filter = WinHotkeyFilter()
        QApplication.instance().installNativeEventFilter(self._hotkey_filter)

        self._build_tray()
        self._register_hotkey()

        self._idle_timer = QTimer()
        self._idle_timer.timeout.connect(self._on_idle_timeout_check)
        self._idle_timer.start(60_000)


        # Load autocorrect model at boot
        ac_path = self.cfg.get("model_path", "")
        if ac_path:
            # Connect deferred retry BEFORE starting the thread so the first
            # 'not found' emission is captured even if load_model completes
            # before the event loop processes the signal.
            self._retry_scheduled = False
            self._retry_count = 0
            self._max_retries = 3
            self.ac_model.status_changed.connect(self._schedule_model_retry_if_needed)
            threading.Thread(
                target=lambda: self.ac_model.load_model(retry_missing_path=True),
                daemon=True,
            ).start()

        # Load chat model at boot if configured
        if self.cfg.get("chat_use_separate_model", False) and self.cfg.get("chat_keep_loaded", False):
            chat_path = self.cfg.get("chat_model_path", "")
            if chat_path:
                threading.Thread(
                    target=lambda: self.chat_model.load_model(retry_missing_path=True),
                    daemon=True,
                ).start()

        # Clean up legacy startup scheduled task if present to avoid dual launching
        self._cleanup_legacy_startup_task()

        # Check for Stet updates 5 s after boot (non-blocking)
        self._update_checker: AppUpdateChecker | None = None
        self._available_update: tuple[str, str] | None = None
        QTimer.singleShot(5000, self._check_app_update)

        if not self.cfg.get("model_path", ""):
            QTimer.singleShot(800, self._show_first_run)

    def __del__(self):
        try:
            qapp = QApplication.instance()
            if qapp and hasattr(self, "_hotkey_filter"):
                qapp.removeNativeEventFilter(self._hotkey_filter)
        except Exception:
            pass

    def _on_idle_timeout_check(self):
        if self._is_window_alive():
            log("[APP] CorrectionWindow is active — marking models as used")
            self.chat_model.mark_used()
            self.ac_model.mark_used()
        self.chat_model.check_idle()
        self.ac_model.check_idle()


    # ── deferred model retry ──────────────────────────────────────────────


    def _schedule_model_retry_if_needed(self, status_msg: str):
        """Slot connected to ac_model.status_changed at boot.

        Triggers a deferred retry when the model fails to load for any reason
        (missing file, server crash, startup timeout, CUDA error, etc.).
        Uses exponential backoff (45s → 90s → 180s) capped at _max_retries
        attempts.  The _retry_scheduled flag ensures only one timer is queued
        at a time.
        """
        FAILURE_KEYWORDS = (
            "not found",
            "load error",
            "server did not start",
            "server exited",
        )
        msg = status_msg.lower()
        is_failure = any(kw in msg for kw in FAILURE_KEYWORDS)

        if is_failure and not self.ac_model.is_loaded() and not self._retry_scheduled:
            if self._retry_count >= self._max_retries:
                log(
                    f"[APP] Model load retry limit reached "
                    f"({self._retry_count}/{self._max_retries}) — giving up"
                )
                return
            self._retry_scheduled = True
            delay_ms = min(45_000 * (2 ** self._retry_count), 180_000)
            self._retry_count += 1
            log(
                f"[APP] Model load failed (attempt {self._retry_count}/"
                f"{self._max_retries}) — scheduling retry in {delay_ms // 1000}s"
            )
            QTimer.singleShot(delay_ms, self._deferred_model_retry)

    def _deferred_model_retry(self):
        """Fire a single deferred load attempt.

        Runs in the Qt main thread (QTimer callback). Kicks off the real
        load_model in a daemon thread so the event loop stays responsive.
        If the path is still missing, the status signal will re-arm another
        one-shot retry instead of sleeping inside load_model().
        """
        if not self.ac_model.is_loaded():
            self._retry_scheduled = False
            log("[APP] Deferred model retry firing now")
            threading.Thread(
                target=lambda: self.ac_model.load_model(retry_missing_path=True),
                daemon=True,
            ).start()
        else:
            self._retry_scheduled = False
            self._retry_count = 0
            log("[APP] Deferred model retry skipped — model already loaded")

    def _build_tray(self):
        self.tray = QSystemTrayIcon(make_tray_icon("#475569"), self)
        self.tray.setToolTip("Stet")
        self.tray.activated.connect(self._tray_activated)

        menu = QMenu()
        menu.setStyleSheet(
            "QMenu{background:#121315;border:1px solid #28292c;border-radius:0px;"
            "padding:8px 0;color:#88898c;font-size:13px;font-family:'IBM Plex Mono', 'Consolas', monospace;}"
            "QMenu::item{padding:8px 32px 8px 32px;border-radius:0px;color:#88898c;}"
            "QMenu::item:selected{background:#090a0b;color:#ededee;}"
            "QMenu::indicator{left:10px;width:12px;height:12px;}"
            "QMenu::icon{left:10px;width:12px;height:12px;}"
            "QMenu::right-arrow{image:none;width:0px;height:0px;}"
            "QMenu::separator{height:1px;background:#28292c;margin:8px 0;}"
            "QMenu::item:disabled{color:#88898c;}"
        )

        header_widget = QWidget()
        header_widget.setStyleSheet("background:transparent;")
        header_lay = QVBoxLayout(header_widget)
        header_lay.setContentsMargins(32, 4, 16, 8)
        header_lay.setSpacing(4)

        title_lbl = QLabel("STREAM CORRECT")
        title_lbl.setStyleSheet(
            "font-size: 11px; font-weight: 500; color: #d4a373; text-transform: uppercase; letter-spacing: 0.05em; border: none;"
        )

        self._status_lbl = QLabel("● AC: Offline")
        self._status_lbl.setStyleSheet("font-size: 12px; color: #ededee; border: none;")

        self._chat_status_lbl = QLabel("● Chat: Offline")
        self._chat_status_lbl.setStyleSheet("font-size: 12px; color: #88898c; border: none;")

        header_lay.addWidget(title_lbl)
        header_lay.addWidget(self._status_lbl)
        header_lay.addWidget(self._chat_status_lbl)

        header_act = QWidgetAction(self)
        header_act.setDefaultWidget(header_widget)
        menu.addAction(header_act)
        menu.addSeparator()

        self._llm_menu = menu.addMenu("Model: Offline")
        self._llm_menu_action = self._llm_menu.menuAction()
        self._llm_menu_action.setIcon(make_left_arrow_icon())
        self._llm_menu.setStyleSheet(menu.styleSheet())
        self._llm_menu.aboutToShow.connect(self._rebuild_llm_menu)

        act_settings = QAction("Settings...", self)
        act_settings.triggered.connect(self._open_settings)
        menu.addAction(act_settings)


        self._update_action = QAction("Check for Updates", self)

        if WINDOWS:
            menu.addSeparator()
            self._act_startup = QAction("Run at Startup", self)
            self._act_startup.setCheckable(True)
            self._act_startup.triggered.connect(self._toggle_startup)
            menu.addAction(self._act_startup)
            menu.aboutToShow.connect(self._update_startup_action)

        act_quit = QAction("Quit", self)
        act_quit.triggered.connect(self._quit)
        menu.addAction(act_quit)

        if WINDOWS:
            self._tray_menu = menu
        else:
            self.tray.setContextMenu(menu)
        self._update_llm_menu_initial_text()
        self._show_tray_with_retry()

    def _show_tray_with_retry(self):
        """Show the tray icon, retrying if the system tray isn't available yet.

        At Windows boot, explorer.exe may not have created the notification
        area by the time this runs. tray.show() silently does nothing in
        that case, leaving the app as a zombie with no visible UI.
        Retry every 2 s for up to 60 s.
        """
        self._tray_retry_count = 0
        self._tray_retry_max = 30  # 30 × 2s = 60s

        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            if self.tray.isVisible():
                log("[Tray] Shown successfully")
                return

        log("[Tray] System tray not available yet — starting retry timer")
        self._tray_retry_timer = QTimer()
        self._tray_retry_timer.timeout.connect(self._retry_tray_show)
        self._tray_retry_timer.start(2000)

    def _retry_tray_show(self):
        self._tray_retry_count += 1
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray.show()
            if self.tray.isVisible():
                log(f"[Tray] Shown after {self._tray_retry_count} retries")
                self._tray_retry_timer.stop()
                return

        if self._tray_retry_count >= self._tray_retry_max:
            log("[Tray] FAILED — gave up after 60s, forcing show")
            self.tray.show()  # Last-ditch attempt
            self._tray_retry_timer.stop()

    def _set_tray_icon(self, color: str):
        self.tray.setIcon(make_tray_icon(color))

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._open_settings()
        elif WINDOWS and reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.Context,
        ):
            self._tray_menu.exec(QCursor.pos())

    def _rebuild_llm_menu(self):
        self._llm_menu.clear()
        act_llm_load = QAction("Load model", self)
        act_llm_unload = QAction("Unload model", self)
        act_llm_browse = QAction("Browse GGUF…", self)
        act_llm_load.triggered.connect(self._tray_load_model)
        act_llm_unload.triggered.connect(self._tray_unload_model)
        act_llm_browse.triggered.connect(self._browse_model)
        self._llm_menu.addAction(act_llm_load)
        self._llm_menu.addAction(act_llm_unload)
        self._llm_menu.addSeparator()
        self._llm_menu.addAction(act_llm_browse)
        self._llm_menu.addSeparator()
        recent_list = []
        for path in self.cfg.get("recent_models", []):
            if path and Path(path).exists():
                recent_list.append(path)
                if len(recent_list) >= 8:
                    break
        for path in recent_list:
            act = QAction(friendly_name(path), self)
            act.triggered.connect(lambda checked, p=path: self._select_model(p))
            self._llm_menu.addAction(act)

    def _tray_load_model(self):
        threading.Thread(target=self.ac_model.load_model, daemon=True).start()

    def _tray_unload_model(self):
        self.ac_model.unload_model()

    def _update_llm_menu_initial_text(self):
        if not hasattr(self, "_llm_menu_action"):
            return
        
        ac_name = friendly_name(self.cfg.get("model_path", ""))
        if self.ac_model.is_loaded():
            self._status_lbl.setText(f"● AC: Ready — {ac_name}")
            self._llm_menu_action.setText(f"Model: Ready — {ac_name}")
        else:
            self._status_lbl.setText("● AC: Offline")
            self._llm_menu_action.setText("Model: Offline")
            
        if self.cfg.get("chat_use_separate_model", False):
            self._chat_status_lbl.show()
            chat_name = friendly_name(self.cfg.get("chat_model_path", ""))
            if self.chat_model.is_loaded():
                self._chat_status_lbl.setText(f"● Chat: Ready — {chat_name}")
            else:
                self._chat_status_lbl.setText("● Chat: Offline")
        else:
            self._chat_status_lbl.hide()

    def _on_ac_status(self, msg: str):
        lbl_msg = msg
        if lbl_msg.startswith("●"):
            lbl_msg = lbl_msg.lstrip("●").strip()
        
        if hasattr(self, "_status_lbl"):
            self._status_lbl.setText(f"● AC: {lbl_msg}")
        elif hasattr(self, "_status_action"):
            self._status_action.setText(f"Autocorrect: {lbl_msg}")
        
        if hasattr(self, "_llm_menu_action"):
            self._llm_menu_action.setText(f"Model: {lbl_msg}")

    def _on_chat_status(self, msg: str):
        color = (
            "#a78bfa"
            if "ready" in msg.lower()
            else "#f59e0b"
            if "loading" in msg.lower() or "starting" in msg.lower()
            else "#ef4444"
            if "error" in msg.lower() or "failed" in msg.lower()
            else "#475569"
        )
        self._set_tray_icon(color)
        
        lbl_msg = msg
        if lbl_msg.startswith("●"):
            lbl_msg = lbl_msg.lstrip("●").strip()
            
        if self.cfg.get("chat_use_separate_model", False):
            self._chat_status_lbl.show()
            self._chat_status_lbl.setText(f"● Chat: {lbl_msg}")
        else:
            self._chat_status_lbl.hide()

    def _register_hotkey(self, force: bool = False):
        """Register global hotkeys using Windows RegisterHotKey API."""
        now = time.monotonic()
        if not force and now - self._last_register_ts < 0.5:
            log("[Hotkey] register debounced — too recent")
            return
        self._last_register_ts = now

        # ── Step 1: remove previous handles surgically ────────────────────
        for hotkey_id in self._hotkey_handles:
            try:
                ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
            except Exception as e:
                log(f"[Hotkey] Unregister failed: {e}")
        self._hotkey_handles.clear()
        if hasattr(self, "_hotkey_filter"):
            self._hotkey_filter.clear_callbacks()

        # ── Step 2: register new hotkeys ──────────────────────────────────
        hotkeys = self.cfg.get("hotkeys", [])

        for idx, hk_cfg in enumerate(hotkeys):
            shortcut = hk_cfg.get("shortcut", "").lower().strip()
            if not shortcut:
                continue
            try:
                mods, vk = parse_hotkey_string(shortcut)
                if vk == 0:
                    log(f"[Hotkey] parse failed for '{shortcut}'")
                    continue

                hotkey_id = 1000 + idx
                res = ctypes.windll.user32.RegisterHotKey(None, hotkey_id, mods, vk)
                if res == 0:
                    raise OSError(ctypes.FormatError(ctypes.GetLastError()))

                self._hotkey_handles.append(hotkey_id)
                if hasattr(self, "_hotkey_filter"):
                    self._hotkey_filter.register_callback(
                        hotkey_id, lambda h=hk_cfg: self._hotkey_signal.emit(h)
                    )
                log(f"[Hotkey] registered: {shortcut} (mode: {hk_cfg.get('mode')})")
            except Exception as e:
                log(f"[Hotkey] register failed for '{shortcut}': {e}")
                self.tray.showMessage(
                    "Stet",
                    f"Could not register hotkey '{shortcut}'. Try running as administrator or change it in settings.",
                    QSystemTrayIcon.MessageIcon.Warning,
                    4000,
                )

    def _safe_paste(self, retries=5, delay=0.03) -> str:
        for i in range(retries):
            try:
                return _clipboard_read_text()
            except Exception as e:
                if i == retries - 1:
                    log(f"[Clipboard] paste failed: {e}")
                    return ""
                time.sleep(delay)
        return ""

    def _safe_copy(self, text: str, retries=5, delay=0.03):
        for i in range(retries):
            try:
                _clipboard_write_text(text)
                return
            except Exception as e:
                if i == retries - 1:
                    log(f"[Clipboard] copy failed: {e}")
                    return
                time.sleep(delay)

    def _is_window_alive(self) -> bool:
        """Check if _window is alive without risking RuntimeError on deleted C++."""
        if self._window is None:
            return False
        try:
            # PyQt6 wraps a C++ QWidget; accessing any method on a deleted
            # wrapper raises RuntimeError. Try the cheapest check first.
            from PyQt6 import sip
            from PyQt6.QtCore import QObject

            if not isinstance(self._window, QObject):
                # Fake or mock in unit tests
                return bool(self._window.isVisible())
            if sip.isdeleted(self._window):
                self._window = None
                return False
            return self._window.isVisible()
        except RuntimeError:
            # Wrapped C/C++ object already deleted — clear stale ref.
            log("[Hotkey] CorrectionWindow C++ object deleted — clearing reference")
            self._window = None
            return False

    def _handle_hotkey_fired(self, hk_cfg: dict):
        """Called from main Qt thread via queue polling."""
        mode = hk_cfg.get("mode", "panel")
        strength = hk_cfg.get("strength", "smart_fix")
        custom_prompt = hk_cfg.get("custom_prompt", "")
        log(f"[Hotkey] fired mode={mode} strength={strength}")

        # Re-entrancy guard
        if not self._hotkey_busy.acquire(blocking=False):
            log("[Hotkey] Fired but already busy — ignoring")
            return

        if mode == "silent":
            # Immediate visual feedback
            self._silent_osd_signal.emit("Loading model…", "loading")
            threading.Thread(
                target=self._silent_hotkey_worker,
                args=(strength, custom_prompt),
                daemon=True,
            ).start()
        else:
            try:
                # Check window state on MAIN thread to avoid PyQt6 background thread crashes
                if self._is_window_alive():
                    log("[Hotkey] window already open — focusing")
                    try:
                        self._window.raise_()
                        self._window.activateWindow()
                    except Exception:
                        pass
                    self._hotkey_busy.release()
                    return
            except Exception as e:
                log(f"[Hotkey] window check failed: {e}")
                # Fall through to create a new window

            # Run actual work in background thread so Qt stays responsive
            self._pending_panel_strength = strength
            self._pending_panel_custom_prompt = custom_prompt
            threading.Thread(target=self._hotkey_worker, daemon=True).start()

    # Clipboard polling tunables. Modern Windows clipboard updates are fast,
    # so we can poll aggressively without paying meaningful CPU cost.
    # Worst-case wait: GRACE + MAX_POLLS * INTERVAL.
    _CLIPBOARD_POLL_INTERVAL = 0.015   # 15 ms between polls
    _CLIPBOARD_MAX_POLLS = 12          # 12 attempts
    _CLIPBOARD_INITIAL_GRACE = 0.05    # 50 ms grace before first poll
    # 50 ms + 12 * 15 ms = 230 ms worst case (was 680 ms; ~65% reduction)

    def _capture_selection(self) -> str:
        """Copy selected text from the active window via direct UIA or Ctrl+C.

        Returns the selected text, or empty string if nothing was selected.
        Saves the previous clipboard content to self._old_clip and restores it
        only when no selection is found.
        """
        # Try UIA direct text capture first (bypassing the clipboard)
        from stet.core.clipboard import _read_selection_uia

        uia_text = _read_selection_uia()
        if uia_text:
            log(f"[Capture] Direct UIA capture succeeded: {uia_text[:80]!r}")
            self._old_clip = self._safe_paste()
            return uia_text

        # Terminal handling runs only after UIA fails. UIA text extraction is
        # safe in editors and terminals alike; the fallback must switch to a
        # terminal-safe copy chord instead of sending plain Ctrl+C.
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        if _is_terminal_or_ide(hwnd):
            log("[Capture] Active window is a terminal. Using Ctrl+Shift+C fallback.")
            terminal_safe_copy = True
        else:
            terminal_safe_copy = False

        # Fallback: existing Ctrl+C + clipboard read
        # Longer settle: the hotkey fires on key-down; 200 ms is enough
        # for both the key-up event and the target app to regain input focus.
        time.sleep(0.20)

        self._old_clip = self._safe_paste()
        log(
            f"[Capture] old_clip={self._old_clip[:80]!r}"
            if self._old_clip
            else "[Capture] old_clip=(empty)"
        )

        if terminal_safe_copy:
            # Terminal path — never clear the clipboard beforehand.
            # Clearing the clipboard deselects text in terminal apps,
            # which means the subsequent Ctrl+Shift+C copy has nothing to
            # capture and the final paste inserts alongside the original
            # instead of replacing it (doubled text).
            #
            # Instead we send the copy chord immediately and detect success
            # by checking whether the clipboard content *changed* from
            # what we saved in _old_clip.
            _send_ctrl_shift_chord(VK_C)
            time.sleep(self._CLIPBOARD_INITIAL_GRACE)

            for attempt in range(self._CLIPBOARD_MAX_POLLS):
                time.sleep(self._CLIPBOARD_POLL_INTERVAL)
                clip = self._safe_paste()
                if clip and clip != self._old_clip:
                    log(
                        f"[Capture] got selection on poll {attempt + 1}: "
                        f"{clip[:80]!r}"
                    )
                    return clip

            # If the selected text happened to match the old clipboard
            # content, the change-detection loop won't catch it. Try the
            # standard non-empty check as a last resort.
            clip = self._safe_paste()
            if clip:
                log(f"[Capture] terminal fallback (same as old clip): {clip[:80]!r}")
                return clip

            log("[Capture] no selection detected (terminal)")
            if self._old_clip:
                self._safe_copy(self._old_clip)
            return ""

        # ── Non-terminal path ─────────────────────────────────────────
        # Clear clipboard and VERIFY it was actually cleared.  If another app
        # holds the clipboard lock (clipboard managers, password managers),
        # _safe_copy("") may silently fail, leaving stale content that the
        # polling loop would return as the "selected" text.
        self._safe_copy("")
        time.sleep(0.05)
        verify = self._safe_paste()
        if verify:
            # Clear failed — retry once with a longer delay
            log(f"[Capture] clipboard clear failed, retrying (got {verify[:40]!r})")
            time.sleep(0.05)
            self._safe_copy("")
            time.sleep(0.05)
            verify = self._safe_paste()
            if verify:
                log("[Capture] clipboard clear STILL failed — proceeding anyway")

        _send_ctrl_chord(VK_C)

        # Grace period: SendInput injects key events into the input queue, but
        # the target app's message loop needs time to dequeue WM_KEYDOWN,
        # process the copy command, and write to the clipboard. The grace
        # constant covers even heavier apps (browsers, IDEs).
        time.sleep(self._CLIPBOARD_INITIAL_GRACE)

        for attempt in range(self._CLIPBOARD_MAX_POLLS):
            time.sleep(self._CLIPBOARD_POLL_INTERVAL)
            clip = self._safe_paste()
            if clip:
                log(f"[Capture] got selection on poll {attempt + 1}: {clip[:80]!r}")
                return clip

        log(f"[Capture] no selection after {self._CLIPBOARD_MAX_POLLS} polls")
        if self._old_clip:
            self._safe_copy(self._old_clip)
        return ""

    def _hotkey_worker(self):
        try:
            strength = getattr(self, "_pending_panel_strength", "smart_fix")
            selected = self._capture_selection()

            if selected.strip():
                text = selected.strip()
                if len(text.split()) > 1000:
                    self._large_doc_warning_signal.emit(text)
                    return
                self._trigger.emit(text, strength)
            else:
                now = time.monotonic()
                if now - self._last_empty_notify_ts > 3.0:
                    self._last_empty_notify_ts = now
                    self._notify.emit(
                        "No text selected. Select text first, then press the hotkey.",
                        "info",
                    )
                else:
                    log("[Hotkey] empty selection — throttled")
        except Exception as e:
            log(f"[Hotkey] worker error: {e}")
        finally:
            if self._old_clip:
                try:
                    self._safe_copy(self._old_clip)
                except Exception:
                    pass
            self._hotkey_busy.release()

    def _silent_hotkey_worker(self, strength: str, custom_prompt: str = ""):
        """Background: capture selection → correct via patch → auto-paste.

        Does NOT open CorrectionWindow. The user sees only the OSD notification.
        Shares _hotkey_busy with _hotkey_worker so both can't run simultaneously.
        """
        try:
            selected = self._capture_selection()

            if not selected.strip():
                now = time.monotonic()
                if now - self._last_empty_notify_ts > 3.0:
                    self._last_empty_notify_ts = now
                    self._notify.emit(
                        "No text selected. Select text first, then press the hotkey.",
                        "info",
                    )
                self._silent_osd_signal.emit("No text selected", "warning")
                return

            text = selected.strip()
            log(f"[Silent] captured {len(text.split())} words: {text[:120]!r}")

            # Ensure model is loaded. If the boot load (started in __init__)
            # is still in progress, load_model() returns False because
            # self.loading == True. In that case we poll until it finishes
            # instead of giving up with "Model not ready".
            #
            # We use _is_model_ready() (checks /health endpoint) instead of
            # is_loaded() (only checks process liveness). The server process
            # can be alive for 10-60s before model weights finish loading
            # and it starts accepting requests.
            if not self._is_model_ready():
                loaded = self.ac_model.load_model()
                if not loaded and self.ac_model.loading:
                    # Boot load is in progress — wait up to 180 s for it
                    self._wait_for_model_ready()

            if not self._is_model_ready():
                if self.ac_model.should_retry_load():
                    log("[Silent] Model load failed but file exists — retrying after 5s")
                    self._silent_osd_signal.emit("Loading model…", "loading")
                    time.sleep(5)
                    self.ac_model.load_model()
                    if not self.ac_model.is_loaded() and self.ac_model.loading:
                        self._wait_for_model_ready()

            if not self._is_model_ready():
                self._safe_copy(self._old_clip)
                self._silent_osd_signal.emit("Model not ready", "warning")
                return

            custom_sys = self.cfg.get("system_prompt", "").strip()
            result, _units = self.ac_model.correct_text_patch(
                text,
                custom_sys=custom_sys or None,
                strength=strength,
                mode_prompt_override=custom_prompt or None,
            )

            if result is None:
                # patch failed — restore clipboard and show error
                self._safe_copy(self._old_clip)
                self._silent_osd_signal.emit("Correction failed, try again", "warning")
                return

            if result == text:
                # No changes — restore clipboard silently
                log(
                    f"[Silent] no changes needed (input={text[:80]!r}, result identical)"
                )
                self._safe_copy(self._old_clip)
                self._silent_osd_signal.emit("No changes needed", "success")
                return

            # Write corrected text and paste it
            self._safe_copy(result)
            time.sleep(0.12)
            _send_ctrl_chord(VK_V)
            time.sleep(0.08)

            # Restore original clipboard after paste settles.
            # We're already in a background thread — just sleep instead of
            # QTimer.singleShot, which would crash from a non-Qt thread.
            if self._old_clip and self._old_clip != result:
                time.sleep(0.5)
                self._safe_copy(self._old_clip)

            self._silent_osd_signal.emit("Silently corrected", "success")
            log("[Silent] done")

        except Exception as e:
            log(f"[Silent] worker error: {e}")
            self._silent_osd_signal.emit("Error during correction", "warning")
        finally:
            self._hotkey_busy.release()

    def _is_model_ready(self) -> bool:
        """True when the server process is alive AND /health returns 200.

        is_loaded() only checks process liveness — the server can be alive for
        10-60 s while the model loads weights, rejecting all requests.
        This method ensures we don't try to correct during that window.
        """
        if not self.ac_model.is_loaded():
            return False
        try:
            import requests

            r = requests.get(self.ac_model._health_url(), timeout=1)
            return r.status_code == 200
        except Exception:
            return False

    def _wait_for_model_ready(self, max_seconds: int = 180):
        """Block until model is truly ready (health check passes), or timeout."""
        for i in range(max_seconds):
            time.sleep(1)
            if self._is_model_ready():
                return
            if not self.ac_model.loading and not self.ac_model.is_loaded():
                # load_model() finished without success — don't waste time
                return
            if i > 0 and i % 15 == 0:
                self._silent_osd_signal.emit(f"Loading model… ({i}s)", "loading")

    def _show_silent_osd(self, message: str, state: str):
        """Called on main Qt thread via _silent_osd_signal. Creates and shows OSD."""
        prev = getattr(self, "_osd_widget", None)
        if prev is not None:
            try:
                prev.close()
            except Exception:
                pass
        osd = SilentCorrectionOSD(message, state=state)
        self._osd_widget = osd
        # Loading state stays visible until replaced; others auto-dismiss
        osd.show_animated(auto_dismiss=(state != "loading"))

    def _show_model_warning(self, msg: str):
        # Longer duration (6s) than standard notifications — this is a sticky
        # heads-up, not a quick confirmation, and users need time to read it
        self.tray.showMessage(
            "Stet — Model warning",
            msg,
            QSystemTrayIcon.MessageIcon.Warning,
            6000,
        )

    def _show_notify(self, msg: str, icon: str):
        ico = (
            QSystemTrayIcon.MessageIcon.Warning
            if icon == "warn"
            else QSystemTrayIcon.MessageIcon.Information
        )
        self.tray.showMessage("Stet", msg, ico, 2500)

    def _on_large_doc_warning(self, text: str):
        """Warn the user when selected text is too large for reliable correction."""
        word_count = len(text.split())
        self.tray.showMessage(
            "Stet — Document too large",
            f"Selected text is ~{word_count} words. Stet works best with smaller "
            "selections (1–3 paragraphs). Select a shorter passage and try again.",
            QSystemTrayIcon.MessageIcon.Warning,
            5000,
        )

    def _show_window(self, text: str, initial_strength: str = "smart_fix"):
        log(f"[Window] _show_window called, text length={len(text)}")
        try:
            if self._window:
                old = self._window
                self._window = None
                old.close()
                old.deleteLater()

            custom_prompt = getattr(self, "_pending_panel_custom_prompt", "")
            self._window = CorrectionWindow(
                text,
                self.ac_model,
                self.chat_model,
                self.cfg,
                re_register_cb=self._register_hotkey,
                initial_strength=initial_strength,
                mode_prompt_override=custom_prompt or None,
            )
            self._window.accepted.connect(self._paste_text)
            # Clear stale reference when the user closes the window,
            # preventing RuntimeError on next hotkey press.
            self._window.destroyed.connect(self._on_window_destroyed)
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
            log("[Window] Window shown successfully")
        except Exception as e:
            log(f"[Window] CRASH in _show_window: {e}\n{traceback.format_exc()}")

    def _on_window_destroyed(self):
        """Slot called when CorrectionWindow's C++ object is destroyed."""
        self._window = None
        log("[Window] CorrectionWindow destroyed — reference cleared")

    def _paste_text(self, text: str):
        self._safe_copy(text)
        time.sleep(0.15)
        _send_ctrl_chord(VK_V)
        time.sleep(0.1)
        if self._old_clip and self._old_clip != text:
            clip_to_restore = self._old_clip
            QTimer.singleShot(500, lambda: self._safe_copy(clip_to_restore))

    def _open_settings(self):
        dlg = SettingsDialog(
            self.cfg,
            re_register_cb=self._register_hotkey,
            app_update_cb=self._run_settings_update_action,
            app_update_label=self._settings_update_action_text(),
        )
        dlg.saved.connect(self._on_settings_saved)
        dlg.show()
        self._settings_dlg = dlg

    def _on_settings_saved(self):
        self._register_hotkey(force=True)
        # If autocorrect model changed, reload
        ac_path = self.cfg.get("model_path", "")
        if self.ac_model.is_loaded():
            self.ac_model.unload_model()
        if ac_path:
            threading.Thread(target=self.ac_model.load_model, daemon=True).start()

        if self.chat_model.is_loaded():
            self.chat_model.unload_model()
        if self.cfg.get("chat_use_separate_model", False) and self.cfg.get("chat_keep_loaded", False):
            chat_path = self.cfg.get("chat_model_path", "")
            if chat_path:
                threading.Thread(target=self.chat_model.load_model, daemon=True).start()

        self._update_llm_menu_initial_text()

    def _browse_model(self):
        path, _ = QFileDialog.getOpenFileName(
            None, "Select GGUF Model", "", "GGUF (*.gguf)"
        )
        if path:
            self._select_model(path)

    def _show_first_run(self):
        # Check for llama-server backend first — if missing, prompt to download
        if not _find_shipped_llama_server():
            self._show_backend_download_prompt()
            return

        # Bail if the user already chose a model while the timer was pending
        # (e.g. via settings or tray menu), or if they've dismissed this
        # welcome before and the flag was set.
        if self.cfg.get("model_path", ""):
            return

        box = QMessageBox()
        box.setWindowTitle("Welcome to Stet")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Stet needs a language model to work.")
        dl_name = "download_model.bat" if WINDOWS else "download_model.sh"
        box.setInformativeText(
            "You can:\n\n"
            f"  • Download the recommended model (~1.8 GB) — runs {dl_name} in a terminal\n"
            "  • Browse to an existing .gguf file you already have\n"
            "  • Skip for now and configure from Settings later"
        )
        dl_btn = box.addButton(
            "Download recommended", QMessageBox.ButtonRole.AcceptRole
        )
        br_btn = box.addButton("Browse existing…", QMessageBox.ButtonRole.ActionRole)
        box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(dl_btn)
        box.exec()

        clicked = box.clickedButton()
        if clicked is dl_btn:
            self._run_download_script()
        elif clicked is br_btn:
            self._browse_model()

    def _show_backend_download_prompt(self):
        """Prompt the user to download the llama.cpp backend if not found."""
        box = QMessageBox()
        box.setWindowTitle("Stet — Backend Required")
        box.setIcon(QMessageBox.Icon.Information)
        box.setText("Stet needs the llama.cpp backend to run AI models.")
        dl_name = "download_backend.bat" if WINDOWS else "download_backend.sh"
        box.setInformativeText(
            "The llama.cpp server + CUDA runtime (~652 MB) will be downloaded "
            "to the app directory.\n\n"
            "This is a one-time download.\n\n"
            f"  • Click 'Download' to run {dl_name}\n"
            "  • Click 'Skip' to configure manually from Settings later"
        )
        dl_btn = box.addButton("Download", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Skip", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(dl_btn)
        box.exec()

        if box.clickedButton() is dl_btn:
            self._run_download_backend_script()

    def _run_download_backend_script(self):
        """Launch the bundled download_backend script in a visible terminal."""
        script = SCRIPT_DIR / ("download_backend.bat" if WINDOWS else "download_backend.sh")
        if not script.exists():
            self.tray.showMessage(
                "Stet",
                f"Backend download script not found at {script.name}. "
                "Please download llama.cpp manually.",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
            return
        try:
            if WINDOWS:
                subprocess.Popen(
                    ["cmd", "/c", "start", "", str(script)],
                    cwd=str(SCRIPT_DIR),
                )
            else:
                subprocess.Popen(["bash", str(script)], cwd=str(SCRIPT_DIR))
        except Exception as e:
            log(f"[FirstRun] Failed to launch backend download script: {e}")
            self.tray.showMessage(
                "Stet",
                f"Could not launch {script.name}: {e}",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )

    def _run_download_script(self):
        """Launch the bundled download_model script in a visible terminal so
        the user can watch progress. Falls back to opening the release folder
        if the script is missing (e.g. dev launch)."""
        script = SCRIPT_DIR / ("download_model.bat" if WINDOWS else "download_model.sh")
        if not script.exists():
            # Dev mode or corrupted unzip — just reveal the folder so the user
            # can grab the model manually.
            self.tray.showMessage(
                "Stet",
                f"Download script not found at {script.name}. Please download a GGUF model manually.",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )
            return
        try:
            if WINDOWS:
                # start "" opens the .bat in its own console window so the user
                # sees curl's progress bar instead of a silent background fetch
                subprocess.Popen(
                    ["cmd", "/c", "start", "", str(script)],
                    cwd=str(SCRIPT_DIR),
                )
            else:
                subprocess.Popen(["bash", str(script)], cwd=str(SCRIPT_DIR))
        except Exception as e:
            log(f"[FirstRun] Failed to launch download script: {e}")
            self.tray.showMessage(
                "Stet",
                f"Could not launch {script.name}: {e}",
                QSystemTrayIcon.MessageIcon.Warning,
                5000,
            )

    def _select_model(self, path: str):
        self.cfg.set("model_path", path)
        self.cfg.add_recent(path)
        
        if not self.cfg.get("chat_use_separate_model", False):
            self.cfg.set("chat_model_path", path)
            self.chat_model.unload_model()

        if self.ac_model.is_loaded():
            self.ac_model.unload_model()
        threading.Thread(target=self.ac_model.load_model, daemon=True).start()

        self.tray.showMessage(
            "Stet",
            f"Model selected: {os.path.basename(path)}",
            QSystemTrayIcon.MessageIcon.Information,
            2500,
        )

    def _cleanup_legacy_startup_task(self):
        if WINDOWS:
            try:
                subprocess.run(
                    ["schtasks", "/delete", "/tn", "Stet Startup", "/f"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            except Exception as e:
                log(f"[Startup] Failed to delete legacy scheduled task: {e}")

    def _update_startup_action(self):
        if WINDOWS and winreg:
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0,
                    winreg.KEY_READ,
                )
                try:
                    winreg.QueryValueEx(key, "Stet")
                    self._act_startup.setChecked(True)
                except FileNotFoundError:
                    self._act_startup.setChecked(False)
                finally:
                    winreg.CloseKey(key)
            except Exception as e:
                log(f"[Startup] Failed to check registry key: {e}")
                self._act_startup.setChecked(False)
        else:
            self._act_startup.setChecked(False)

    def _toggle_startup(self, checked: bool):
        # 1. Clean up legacy scheduled task
        self._cleanup_legacy_startup_task()

        # 2. Write or delete registry value
        try:
            if WINDOWS and winreg:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run",
                    0,
                    winreg.KEY_SET_VALUE,
                )
                if checked:
                    cmd = _startup_command()
                    winreg.SetValueEx(key, "Stet", 0, winreg.REG_SZ, cmd)
                    log(f"[Startup] Registered registry startup: {cmd}")
                    msg = "Added to Windows startup (via registry)."
                else:
                    try:
                        winreg.DeleteValue(key, "Stet")
                        log("[Startup] Removed registry startup")
                    except FileNotFoundError:
                        pass
                    msg = "Removed from Windows startup."
                winreg.CloseKey(key)
            else:
                msg = "Startup configuration not supported on this OS."

            self.tray.showMessage(
                "Stet", msg, QSystemTrayIcon.MessageIcon.Information, 2500
            )
        except Exception as e:
            log(f"[Startup] Toggle failed: {e}")
            self.tray.showMessage(
                "Stet", f"Startup error: {e}", QSystemTrayIcon.MessageIcon.Warning, 3000
            )

    def _check_app_update(self):
        """Start background update check. Safe to call multiple times."""
        if self._update_checker and self._update_checker.isRunning():
            return
        self._set_settings_update_action_text("Checking...")
        self._update_checker = AppUpdateChecker()
        self._update_checker.finished.connect(self._update_checker.deleteLater)
        self._update_checker.finished.connect(self._on_update_check_finished)
        self._update_checker.update_available.connect(self._on_update_available)
        self._update_checker.start()

    def _on_update_available(self, tag: str, url: str, notes: str):
        log(f"[Update] New Stet available: {tag}")
        self._available_update = (tag, url)
        self._update_action.setText(f"Install Stet {tag}")
        self._set_settings_update_action_text(f"Install Stet {tag}")

        self.tray.showMessage(
            "Stet - Update available",
            f"Version {tag} is out.\nOpen Settings to install it.",
            QSystemTrayIcon.MessageIcon.Information,
            8000,
        )

    def _settings_update_action_text(self) -> str:
        if self._available_update:
            tag, _url = self._available_update
            return f"Install Stet {tag}"
        return "Check for Updates"

    def _set_settings_update_action_text(self, text: str):
        dlg = getattr(self, "_settings_dlg", None)
        if dlg is not None:
            try:
                dlg.set_update_action_text(text)
            except RuntimeError:
                self._settings_dlg = None

    def _on_update_check_finished(self):
        if not self._available_update:
            self._set_settings_update_action_text("Check for Updates")

    def _run_settings_update_action(self):
        if self._available_update:
            tag, url = self._available_update
            self._start_app_update(url, tag)
            return
        self._check_app_update()

    def _updater_command(self) -> list[str]:
        """Return a command that runs the external updater outside this process."""
        if getattr(sys, "frozen", False):
            updater = SCRIPT_DIR / ("StetUpdater.exe" if WINDOWS else "StetUpdater")
            if not updater.exists():
                raise FileNotFoundError(f"Updater not found: {updater}")

            # The updater is one-file/self-contained. Running a temp copy means
            # the installed updater EXE is not locked while files are replaced.
            temp_dir = Path(tempfile.gettempdir()) / "StetUpdate"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_updater = temp_dir / updater.name
            shutil.copy2(updater, temp_updater)
            return [
                str(temp_updater),
                "--app",
                "--install-dir",
                str(SCRIPT_DIR),
                "--wait-pid",
                str(os.getpid()),
                "--restart",
            ]

        return [
            sys.executable,
            str(SCRIPT_DIR / "stet" / "update.py"),
            "--app",
            "--install-dir",
            str(SCRIPT_DIR),
            "--restart",
        ]

    def _start_app_update(self, url: str, tag: str):
        """Launch the packaged updater, then exit so Windows can replace files."""
        reply = QMessageBox.question(
            None,
            "Update Stet",
            f"Install Stet {tag} now?\n\nThe app will close, update, and restart.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        try:
            cmd = self._updater_command()
            log(f"[Update] Starting updater for {tag}: {' '.join(cmd)}")
            kwargs = {"cwd": str(SCRIPT_DIR), "shell": False}
            if WINDOWS:
                kwargs["creationflags"] = subprocess.CREATE_NEW_CONSOLE
            subprocess.Popen(cmd, **kwargs)
        except Exception as e:
            log(f"[Update] Failed to start updater: {e}")
            QMessageBox.warning(
                None,
                "Update Stet",
                f"Could not start the updater.\n\n{e}\n\nRelease ZIP:\n{url}",
            )
            return

        self._quit()

    def _quit(self):
        for hotkey_id in self._hotkey_handles:
            try:
                ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)
            except Exception as e:
                log(f"[Hotkey] Unregister failed on quit: {e}")
        self._hotkey_handles.clear()
        qapp = QApplication.instance()
        if hasattr(self, "_hotkey_filter"):
            self._hotkey_filter.clear_callbacks()
            if qapp:
                try:
                    qapp.removeNativeEventFilter(self._hotkey_filter)
                except Exception:
                    pass
        self.ac_model.unload_model()
        self.chat_model.unload_model()
        if qapp:
            qapp.quit()


class AppUpdateChecker(QThread):
    """Background thread — checks GitHub for a newer Stet release."""

    update_available = pyqtSignal(str, str, str)  # (new_tag, asset_url, release_notes)
    check_done = pyqtSignal()

    def run(self):
        try:
            import urllib.request

            req = urllib.request.Request(
                GITHUB_RELEASES_API,
                headers={"User-Agent": "Stet-update-checker"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())

            tag = data.get("tag_name", "")  # e.g. "v3.2.0"
            notes = data.get("body", "")
            asset = _release_zip_asset(data)
            asset_url = asset.get("browser_download_url", "") if asset else ""

            if not tag or not asset_url:
                return

            # Clean "v" from versions for comparison (e.g. "v3.1.1" -> "3.1.1")
            remote_ver = tag.lstrip("vV")
            local_ver = APP_VERSION.lstrip("vV")

            log(
                f"[Update] local version={local_ver}  remote version={remote_ver}  tag={tag}"
            )

            # Basic semantic version comparison
            def _parse_version(v_str):
                # Handle cases like "3.1.0" or "Release_v3.1.0" gracefully
                v_str = re.sub(r"[^0-9\.]", "", v_str)
                parts = []
                for p in v_str.split("."):
                    try:
                        parts.append(int(p))
                    except ValueError:
                        parts.append(0)
                # Pad to at least 3 parts (major.minor.patch)
                while len(parts) < 3:
                    parts.append(0)
                return tuple(parts)

            remote_tuple = _parse_version(remote_ver)
            local_tuple = _parse_version(local_ver)

            # If the remote version is newer, alert the user
            if remote_tuple > local_tuple:
                self.update_available.emit(tag, asset_url, notes)

        except Exception as e:
            log(f"[Update] Check failed: {e}")
        finally:
            self.check_done.emit()
