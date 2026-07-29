"""Native macOS global-input adapter.

The adapter keeps all Objective-C imports lazy, so importing Stet on Windows
or Linux never requires PyObjC.  It uses a listen-only Quartz event tap for
global shortcuts, Accessibility for selected text when available, and a
snapshot-aware pasteboard fallback where Accessibility cannot expose a
selection (notably in browsers and Electron applications).
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from stet.core.clipboard_macos import (
    read_text,
    restore_snapshot,
    snapshot_pasteboard,
    write_text,
)
from stet.core.input import (
    AppIdentity,
    HotkeyResult,
    HotkeySpec,
    InputCode,
    Outcome,
    PermissionState,
    PermissionStatus,
    SelectionResult,
    SelectionSource,
)
from stet.core.macos_permissions import permission_state


# ANSI virtual-key values used by Quartz.  They cover the common keys exposed
# by the shortcut editor without relying on layout-specific character
# translation at event-tap time.
_KEY_CODES = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6,
    "x": 7, "c": 8, "v": 9, "b": 11, "q": 12, "w": 13, "e": 14,
    "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20,
    "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26,
    "-": 27, "8": 28, "0": 29, "]": 30, "o": 31, "u": 32,
    "[": 33, "i": 34, "p": 35, "enter": 36, "return": 36,
    "l": 37, "j": 38, "'": 39, "k": 40, ";": 41, "\\": 42,
    ",": 43, "/": 44, "n": 45, "m": 46, ".": 47, "tab": 48,
    "space": 49, "`": 50, "tilde": 50, "backspace": 51,
    "delete": 117, "forwarddelete": 117,
    "escape": 53, "f1": 122, "f2": 120, "f3": 99, "f4": 118,
    "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101,
    "f10": 109, "f11": 103, "f12": 111, "left": 123, "right": 124,
    "down": 125, "up": 126, "home": 115, "end": 119,
    "pageup": 116, "pagedown": 121, "insert": 114,
    "capslock": 57, "numlock": 71,
}

# The shortcut editor accepts both punctuation names and literal forms.
# Quartz keycodes are physical ANSI positions, so they remain layout-safe.
_KEY_CODES.update(
    {
        "period": _KEY_CODES["."],
        "comma": _KEY_CODES[","],
        "slash": _KEY_CODES["/"],
        "semicolon": _KEY_CODES[";"],
        "equal": _KEY_CODES["="],
        "minus": _KEY_CODES["-"],
        "bracketleft": _KEY_CODES["["],
        "bracketright": _KEY_CODES["]"],
        "backslash": _KEY_CODES["\\"],
        "quote": _KEY_CODES["'"],
    }
)


def _load_appkit() -> Any:
    import AppKit  # type: ignore[import-not-found]

    return AppKit


def _load_quartz() -> Any:
    import Quartz  # type: ignore[import-not-found]

    return Quartz


def _load_application_services() -> Any:
    import ApplicationServices  # type: ignore[import-not-found]

    return ApplicationServices


def _attribute_value(services: Any, element: Any, attribute: Any) -> Any:
    """Normalize PyObjC's tuple return convention for AX copy operations."""

    result = services.AXUIElementCopyAttributeValue(element, attribute, None)
    if isinstance(result, tuple):
        if len(result) < 2 or int(result[0]) != 0:
            return None
        return result[1]
    return result


class MacOSInputBackend:
    """Global hotkey, selection, and safe paste implementation for macOS."""

    def __init__(
        self,
        *,
        appkit: Any = None,
        quartz: Any = None,
        application_services: Any = None,
        restore_delay: float = 0.55,
    ):
        self._appkit = appkit
        self._quartz = quartz
        self._application_services = application_services
        self._restore_delay = restore_delay
        self._callbacks: dict[tuple[int, int], Callable[[], None]] = {}
        self._tap: Any = None
        self._source: Any = None
        self._event_callback: Any = None
        self._loop: Any = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._tap_error = ""
        self._last_capture_change_count: int | None = None
        self._lock = threading.RLock()

    def _q(self) -> Any:
        return self._quartz or _load_quartz()

    def _a(self) -> Any:
        return self._appkit or _load_appkit()

    def _services(self) -> Any:
        return self._application_services or _load_application_services()

    def permission_state(self) -> PermissionState:
        return permission_state(self._application_services, self._quartz)

    @staticmethod
    def _shortcut_key(spec: HotkeySpec, quartz: Any) -> tuple[int, int] | None:
        parts = spec.parts
        keycode = _KEY_CODES.get(parts[-1])
        if keycode is None:
            return None
        masks = {
            "command": int(getattr(quartz, "kCGEventFlagMaskCommand", 1 << 20)),
            "control": int(getattr(quartz, "kCGEventFlagMaskControl", 1 << 18)),
            "option": int(getattr(quartz, "kCGEventFlagMaskAlternate", 1 << 19)),
            "shift": int(getattr(quartz, "kCGEventFlagMaskShift", 1 << 17)),
        }
        flags = 0
        for modifier in parts[:-1]:
            flags |= masks.get(modifier, 0)
        return keycode, flags

    def _tap_callback(self, _proxy: Any, event_type: int, event: Any, _refcon: Any) -> Any:
        quartz = self._q()
        disabled = {
            getattr(quartz, "kCGEventTapDisabledByTimeout", -1),
            getattr(quartz, "kCGEventTapDisabledByUserInput", -2),
        }
        if event_type in disabled:
            try:
                quartz.CGEventTapEnable(self._tap, True)
            except Exception:
                pass
            return event
        if event_type != getattr(quartz, "kCGEventKeyDown", 10):
            return event
        try:
            keycode = int(quartz.CGEventGetIntegerValueField(
                event, getattr(quartz, "kCGKeyboardEventKeycode", 9)
            ))
            if int(quartz.CGEventGetIntegerValueField(
                event, getattr(quartz, "kCGKeyboardEventAutorepeat", 8)
            )):
                return event
            flags = int(quartz.CGEventGetFlags(event))
            modifier_mask = (
                int(getattr(quartz, "kCGEventFlagMaskCommand", 1 << 20))
                | int(getattr(quartz, "kCGEventFlagMaskControl", 1 << 18))
                | int(getattr(quartz, "kCGEventFlagMaskAlternate", 1 << 19))
                | int(getattr(quartz, "kCGEventFlagMaskShift", 1 << 17))
            )
            callback = self._callbacks.get((keycode, flags & modifier_mask))
            if callback is not None:
                callback()
        except Exception:
            # A global input listener must never terminate the run loop because
            # an application delivered an unusual event.
            pass
        return event

    def _event_loop(self) -> None:
        try:
            quartz = self._q()
            mask = 1 << int(getattr(quartz, "kCGEventKeyDown", 10))
            self._event_callback = self._tap_callback
            self._tap = quartz.CGEventTapCreate(
                getattr(quartz, "kCGSessionEventTap", 1),
                getattr(quartz, "kCGHeadInsertEventTap", 0),
                getattr(quartz, "kCGEventTapOptionListenOnly", 1),
                mask,
                self._event_callback,
                None,
            )
            if self._tap is None:
                self._tap_error = "macOS did not permit a global keyboard listener"
                self._ready.set()
                return
            self._source = quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
            self._loop = quartz.CFRunLoopGetCurrent()
            quartz.CFRunLoopAddSource(
                self._loop,
                self._source,
                getattr(quartz, "kCFRunLoopCommonModes", "kCFRunLoopCommonModes"),
            )
            quartz.CGEventTapEnable(self._tap, True)
            self._ready.set()
            while not self._stop.is_set():
                quartz.CFRunLoopRunInMode(
                    getattr(quartz, "kCFRunLoopDefaultMode", "kCFRunLoopDefaultMode"),
                    0.25,
                    False,
                )
        except Exception as exc:
            self._tap_error = str(exc)
            self._ready.set()
        finally:
            if self._tap is not None:
                try:
                    self._q().CGEventTapEnable(self._tap, False)
                except Exception:
                    pass

    def _release_event_tap(self) -> None:
        """Detach and invalidate the Core Foundation event-tap resources."""

        quartz = self._q()
        loop, source, tap = self._loop, self._source, self._tap
        if loop is not None and source is not None:
            try:
                quartz.CFRunLoopRemoveSource(
                    loop,
                    source,
                    getattr(quartz, "kCFRunLoopCommonModes", "kCFRunLoopCommonModes"),
                )
            except Exception:
                pass
        if tap is not None:
            try:
                quartz.CGEventTapEnable(tap, False)
            except Exception:
                pass
            try:
                quartz.CFMachPortInvalidate(tap)
            except Exception:
                pass

    def register_hotkeys(
        self, specs: Sequence[HotkeySpec], callback: Callable[[HotkeySpec], None]
    ) -> HotkeyResult:
        if sys.platform != "darwin":
            return HotkeyResult(InputCode.UNSUPPORTED_PLATFORM, message="Global hotkeys require macOS")
        if not specs:
            self.unregister_hotkeys()
            return HotkeyResult(InputCode.OK)
        state = self.permission_state()
        if state.input_monitoring is not PermissionStatus.GRANTED:
            return HotkeyResult(
                InputCode.INPUT_MONITORING_REQUIRED,
                message="Allow Stet in Privacy & Security > Input Monitoring to use global shortcuts.",
            )
        try:
            quartz = self._q()
        except Exception as exc:
            return HotkeyResult(InputCode.NOT_AVAILABLE, message=str(exc))
        mappings: dict[tuple[int, int], Callable[[], None]] = {}
        for spec in specs:
            event_key = self._shortcut_key(spec, quartz)
            if event_key is None:
                return HotkeyResult(InputCode.ERROR, message=f"Unsupported macOS shortcut: {spec.shortcut}")
            if event_key in mappings:
                return HotkeyResult(InputCode.HOTKEY_CONFLICT, message=f"Duplicate shortcut: {spec.shortcut}")
            mappings[event_key] = lambda s=spec: callback(s)
        with self._lock:
            self.unregister_hotkeys()
            self._callbacks = mappings
            self._stop.clear()
            self._ready.clear()
            self._tap_error = ""
            self._thread = threading.Thread(target=self._event_loop, name="StetMacHotkeys", daemon=True)
            self._thread.start()
            self._ready.wait(1.0)
            if self._tap is None or self._tap_error:
                error = self._tap_error or "Could not start global shortcuts"
                self._callbacks.clear()
                self.unregister_hotkeys()
                return HotkeyResult(InputCode.PERMISSION_DENIED, message=error)
        return HotkeyResult(InputCode.OK, handles=tuple(spec.shortcut for spec in specs))

    def unregister_hotkeys(self) -> HotkeyResult:
        with self._lock:
            self._callbacks.clear()
            self._stop.set()
            if self._loop is not None:
                try:
                    self._q().CFRunLoopStop(self._loop)
                except Exception:
                    pass
            thread, self._thread = self._thread, None
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout=0.6)
            try:
                self._release_event_tap()
            except Exception:
                pass
            self._tap = self._source = self._loop = self._event_callback = None
        return HotkeyResult(InputCode.OK)

    def frontmost_app(self) -> Outcome[AppIdentity]:
        if sys.platform != "darwin":
            return Outcome.failure(InputCode.UNSUPPORTED_PLATFORM, "Foreground apps require macOS")
        try:
            app = self._a().NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return Outcome.failure(InputCode.NOT_AVAILABLE, "No foreground application")
            executable = app.executableURL()
            return Outcome.success(
                AppIdentity(
                    pid=int(app.processIdentifier()),
                    bundle_id=str(app.bundleIdentifier() or ""),
                    name=str(app.localizedName() or ""),
                    executable_path=str(executable.path()) if executable is not None else "",
                )
            )
        except Exception as exc:
            return Outcome.failure(InputCode.ERROR, f"Could not identify the foreground app: {exc}")

    def _selected_text_from_accessibility(self) -> Outcome[str]:
        state = self.permission_state()
        if state.accessibility is not PermissionStatus.GRANTED:
            return Outcome.failure(InputCode.ACCESSIBILITY_REQUIRED, "Accessibility permission is required")
        try:
            services = self._services()
            system = services.AXUIElementCreateSystemWide()
            focused = _attribute_value(services, system, services.kAXFocusedUIElementAttribute)
            if focused is None:
                return Outcome.failure(InputCode.NO_SELECTION, "No focused text element")
            selected = _attribute_value(services, focused, services.kAXSelectedTextAttribute)
            if selected is None:
                return Outcome.failure(InputCode.NO_SELECTION, "The focused app does not expose selected text")
            text = str(selected)
            return Outcome.success(text) if text else Outcome.failure(InputCode.NO_SELECTION, "No text selected")
        except Exception as exc:
            return Outcome.failure(InputCode.ERROR, f"Accessibility selection failed: {exc}")

    def _post_command_key(self, keycode: int) -> Outcome[None]:
        state = self.permission_state()
        if state.post_events is not PermissionStatus.GRANTED:
            return Outcome.failure(InputCode.POST_EVENT_REQUIRED, "Automation permission is required to send Command shortcuts")
        try:
            quartz = self._q()
            source = quartz.CGEventSourceCreate(getattr(quartz, "kCGEventSourceStateHIDSystemState", 1))
            down = quartz.CGEventCreateKeyboardEvent(source, keycode, True)
            up = quartz.CGEventCreateKeyboardEvent(source, keycode, False)
            flags = getattr(quartz, "kCGEventFlagMaskCommand", 1 << 20)
            quartz.CGEventSetFlags(down, flags)
            quartz.CGEventSetFlags(up, flags)
            tap = getattr(quartz, "kCGHIDEventTap", 0)
            quartz.CGEventPost(tap, down)
            quartz.CGEventPost(tap, up)
            return Outcome.success()
        except Exception as exc:
            return Outcome.failure(InputCode.ERROR, f"Could not send Command shortcut: {exc}")

    def capture_selection(self, timeout: float = 0.35) -> SelectionResult:
        self._last_capture_change_count = None
        target = self.frontmost_app()
        if not target.ok or target.value is None:
            return SelectionResult(target.code, message=target.message)
        ax = self._selected_text_from_accessibility()
        if ax.ok and ax.value is not None:
            return SelectionResult(InputCode.OK, ax.value, SelectionSource.ACCESSIBILITY, target=target.value)

        snapshot = snapshot_pasteboard(self._appkit)
        if not snapshot.ok or snapshot.value is None:
            return SelectionResult(snapshot.code, message=snapshot.message, target=target.value)
        copy_result = self._post_command_key(_KEY_CODES["c"])
        if not copy_result.ok:
            return SelectionResult(copy_result.code, message=copy_result.message, target=target.value)
        deadline = time.monotonic() + max(timeout, 0.05)
        copied_count: int | None = None
        while time.monotonic() < deadline:
            current = snapshot_pasteboard(self._appkit)
            if current.ok and current.value is not None and current.value.change_count != snapshot.value.change_count:
                text = read_text(self._appkit)
                copied_count = current.value.change_count
                if text.ok and text.value:
                    self._last_capture_change_count = copied_count
                    return SelectionResult(
                        InputCode.OK,
                        text.value,
                        SelectionSource.CLIPBOARD,
                        target=target.value,
                        original_clipboard=snapshot.value,
                        clipboard_change_count=copied_count,
                    )
                break
            time.sleep(0.015)
        if copied_count is not None:
            restore_snapshot(snapshot.value, copied_count, self._appkit)
        return SelectionResult(InputCode.NO_SELECTION, target=target.value, message=ax.message or "No text selected")

    def _activate_target(self, target: AppIdentity) -> Outcome[None]:
        try:
            appkit = self._a()
            running = appkit.NSRunningApplication.runningApplicationWithProcessIdentifier_(target.pid)
            if running is None:
                return Outcome.failure(InputCode.TARGET_APP_CHANGED, "The original application is no longer running")
            options = int(getattr(appkit, "NSApplicationActivateIgnoringOtherApps", 1 << 1))
            options |= int(getattr(appkit, "NSApplicationActivateAllWindows", 1 << 0))
            if not running.activateWithOptions_(options):
                return Outcome.failure(InputCode.TARGET_APP_CHANGED, "macOS did not reactivate the original application")
            time.sleep(0.08)
            current = self.frontmost_app()
            if not current.ok or not self._target_matches(target, current.value):
                return Outcome.failure(InputCode.TARGET_APP_CHANGED, "Focus changed before Stet could paste")
            return Outcome.success()
        except Exception as exc:
            return Outcome.failure(InputCode.ERROR, f"Could not reactivate the target app: {exc}")

    @staticmethod
    def _target_matches(expected: AppIdentity, actual: AppIdentity | None) -> bool:
        """Require the same process and, when available, the same executable."""

        if not expected.matches(actual):
            return False
        if actual is None or not expected.executable_path or not actual.executable_path:
            return True
        try:
            return Path(expected.executable_path).resolve() == Path(actual.executable_path).resolve()
        except (OSError, RuntimeError, TypeError, ValueError):
            return expected.executable_path == actual.executable_path

    def _restore_later(self, snapshot: Any, expected_change_count: int) -> None:
        time.sleep(self._restore_delay)
        restore_snapshot(snapshot, expected_change_count, self._appkit)

    def _restore_capture_if_owned(self, snapshot: Any) -> None:
        """Undo a fallback copy only while Stet still owns the pasteboard."""

        if snapshot is None or self._last_capture_change_count is None:
            return
        try:
            current = snapshot_pasteboard(self._appkit)
            if current.ok and current.value is not None:
                if current.value.change_count == self._last_capture_change_count:
                    restore_snapshot(snapshot, self._last_capture_change_count, self._appkit)
        except Exception:
            # Cleanup is best effort; never turn a failed paste into a crash.
            pass

    def paste_text(self, text: str, target: AppIdentity, original_clipboard: Any = None) -> Outcome[None]:
        if not text:
            return Outcome.failure(InputCode.ERROR, "Nothing to paste")
        activated = self._activate_target(target)
        if not activated.ok:
            self._restore_capture_if_owned(original_clipboard)
            return activated
        snapshot = snapshot_pasteboard(self._appkit)
        if not snapshot.ok or snapshot.value is None:
            return Outcome.failure(snapshot.code, snapshot.message)
        # If the clipboard changed after capture, that newer content belongs
        # to the user.  Restore the paste-time snapshot in that case; only
        # restore the pre-capture snapshot while Stet still owns the board.
        capture_owned = (
            original_clipboard is not None
            and (
                snapshot.value.change_count == original_clipboard.change_count
                or snapshot.value.change_count == self._last_capture_change_count
            )
        )
        restore_value = original_clipboard if capture_owned else snapshot.value
        written = write_text(text, self._appkit)
        if not written.ok or written.value is None:
            return Outcome.failure(written.code, written.message)
        posted = self._post_command_key(_KEY_CODES["v"])
        if not posted.ok:
            restore_snapshot(restore_value, written.value, self._appkit)
            return posted
        threading.Thread(
            target=self._restore_later,
            args=(restore_value, written.value),
            name="StetPasteboardRestore",
            daemon=True,
        ).start()
        return Outcome.success()

    def restore_clipboard(self, snapshot: Any, expected_change_count: int | None = None) -> Outcome[int]:
        """Restore a capture-time snapshot without overwriting newer clipboard data."""

        return restore_snapshot(snapshot, expected_change_count, self._appkit)

    def close(self) -> None:
        self.unregister_hotkeys()
        self._last_capture_change_count = None
