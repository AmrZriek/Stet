"""Platform-neutral contracts for global input and selection transactions.

The concrete platform adapters are intentionally not imported here. This module
is safe to import on every supported platform, including machines without
PyObjC installed.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Generic, Optional, Protocol, Sequence, Tuple, TypeVar


class InputCode(str, Enum):
    """Stable, user-actionable outcomes returned by input adapters."""

    OK = "ok"
    SUCCESS = "ok"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    ACCESSIBILITY_REQUIRED = "accessibility_required"
    INPUT_MONITORING_REQUIRED = "input_monitoring_required"
    POST_EVENT_REQUIRED = "post_event_required"
    HOTKEY_CONFLICT = "hotkey_conflict"
    NO_SELECTION = "no_selection"
    TARGET_APP_CHANGED = "target_app_changed"
    PASTEBOARD_CHANGED_EXTERNALLY = "pasteboard_changed_externally"
    PERMISSION_DENIED = "permission_denied"
    NOT_AVAILABLE = "not_available"
    ERROR = "error"

    # Wire error taxonomy (Phase 1a)
    ABORTED_WRONG_TARGET = "aborted_wrong_target"
    ABORTED_CLIPBOARD_CONFLICT = "aborted_clipboard_conflict"
    ABORTED_TRUNCATED = "aborted_truncated"
    GENERATION_TRUNCATED = "generation_truncated"
    UPGRADE_REQUIRED = "upgrade_required"
    BUSY_DROPPED = "busy_dropped"
    ABORTED_INTEGRITY = "aborted_integrity"
    ABORTED_SELECTION_CHANGED = "aborted_selection_changed"
    ABORTED_SELECTION_UNVERIFIABLE = "aborted_selection_unverifiable"
    SELECTION_CHANGED = "aborted_selection_changed"
    SELECTION_UNVERIFIABLE = "aborted_selection_unverifiable"
    PASTE_UNVERIFIED = "paste_unverified"


T = TypeVar("T")


@dataclass(frozen=True)
class Outcome(Generic[T]):
    """A typed operation result; callers never need to infer failure from ``None``."""

    code: InputCode
    value: Optional[T] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code is InputCode.OK or self.code is InputCode.SUCCESS

    @classmethod
    def success(cls, value: Optional[T] = None, message: str = "") -> "Outcome[T]":
        return cls(InputCode.OK, value, message)

    @classmethod
    def failure(cls, code: InputCode, message: str = "") -> "Outcome[T]":
        return cls(code, None, message)


class SelectionSource(str, Enum):
    ACCESSIBILITY = "accessibility"
    CLIPBOARD = "clipboard"
    UIA = "uia"


class PermissionStatus(str, Enum):
    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class PermissionState:
    """A point-in-time view of macOS privacy permissions."""

    supported: bool
    accessibility: PermissionStatus
    post_events: PermissionStatus
    input_monitoring: PermissionStatus
    clipboard: PermissionStatus
    detail: str = ""

    @property
    def all_required_granted(self) -> bool:
        return all(
            state is PermissionStatus.GRANTED
            for state in (self.accessibility, self.post_events)
        )


@dataclass(frozen=True)
class HotkeySpec:
    """A normalized, platform-neutral shortcut specification."""

    shortcut: str
    identifier: str = ""

    def __post_init__(self) -> None:
        normalized = normalize_shortcut(self.shortcut)
        if not normalized:
            raise ValueError("shortcut must contain a key")
        object.__setattr__(self, "shortcut", normalized)

    @property
    def parts(self) -> Tuple[str, ...]:
        return tuple(self.shortcut.split("+"))


_MODIFIER_ALIASES = {
    "cmd": "command",
    "command": "command",
    "meta": "command",
    "win": "command",
    "ctrl": "control",
    "control": "control",
    "alt": "option",
    "opt": "option",
    "option": "option",
    "shift": "shift",
}
_MODIFIER_ORDER = ("command", "control", "option", "shift")


def normalize_shortcut(shortcut: str) -> str:
    """Normalize common config spellings without making platform calls."""

    pieces = [piece.strip().lower() for piece in str(shortcut).split("+") if piece.strip()]
    if not pieces:
        return ""
    modifiers = {_MODIFIER_ALIASES[piece] for piece in pieces[:-1] if piece in _MODIFIER_ALIASES}
    key = pieces[-1]
    if key in _MODIFIER_ALIASES:
        raise ValueError("shortcut must end with a non-modifier key")
    ordered = [modifier for modifier in _MODIFIER_ORDER if modifier in modifiers]
    return "+".join(ordered + [key])


@dataclass(frozen=True)
class AppIdentity:
    pid: int
    bundle_id: str = ""
    name: str = ""
    executable_path: str = ""

    def matches(self, other: Optional["AppIdentity"]) -> bool:
        if other is None or self.pid != other.pid:
            return False
        return not self.bundle_id or not other.bundle_id or self.bundle_id == other.bundle_id


@dataclass(frozen=True)
class PasteboardRepresentation:
    type_identifier: str
    data: bytes


@dataclass(frozen=True)
class PasteboardItemSnapshot:
    representations: Tuple[PasteboardRepresentation, ...] = ()


@dataclass(frozen=True)
class ClipboardSnapshot:
    change_count: int
    items: Tuple[PasteboardItemSnapshot, ...] = ()


def sha256_fingerprint(text: str) -> str:
    """SHA-256 hex digest of a string's UTF-8 bytes.

    Used for both the raw (exact) and canonical selection fingerprints per the
    0b fingerprint contract. Deterministic; never mutates the input.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_selection(text: str) -> str:
    """Canonicalize a selection for cross-source verification.

    Changes ONLY CRLF/CR to LF. It never strips leading/trailing whitespace,
    never collapses internal whitespace, never applies Unicode normalization,
    and never mutates the text shown to the user.
    """
    return text.replace("\r\n", "\n").replace("\r", "\n")


@dataclass(frozen=True)
class CompoundIdentity:
    """Compounded, platform-neutral target identity captured at hotkey trigger.

    Captured before capture begins so the paste can verify the same target
    still owns the foreground (tiered matching). On non-Windows platforms the
    HWND/session fields may be 0/unsupported — a valid no-op identity.
    """

    hwnd: int
    pid: int
    process_creation_time: int
    session_id: int
    window_class: str
    title_hash: str


@dataclass(frozen=True)
class TargetToken:
    """Single-use, expiry-bound paste token binding a correction to its target.

    Carries full compound identity and dual fingerprints (raw exact + canonical).
    """

    pid: int
    process_creation_time: int
    session_id: int
    window_handle: int
    control_identity: str
    title_hash: str
    capture_source: SelectionSource
    session_mode: str
    selection_fingerprint: str
    raw_selection_fingerprint: str
    fingerprint_policy: str = "line_endings_only_v1"
    newline_policy: str = "target_default"
    captured_at_monotonic: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class UndoToken:
    """RAM-only, target-verified undo token. Single use, expires with review."""

    target_token: TargetToken
    replacement_fingerprint: str
    created_at_monotonic: float = field(default_factory=time.monotonic)


@dataclass(frozen=True)
class SelectionCapture:
    """A captured selection with exact text, source metadata, and dual fingerprints."""

    text: str
    source: SelectionSource
    selection_range_count: int = 1
    document_range_match: Optional[bool] = None
    selection_fingerprint: str = ""
    raw_selection_fingerprint: str = ""
    fingerprint_policy: str = "line_endings_only_v1"
    truncated: bool = False

    def __post_init__(self) -> None:
        if not self.raw_selection_fingerprint and self.text is not None:
            object.__setattr__(self, "raw_selection_fingerprint", sha256_fingerprint(self.text))
        if not self.selection_fingerprint and self.text is not None:
            object.__setattr__(
                self, "selection_fingerprint", sha256_fingerprint(canonical_selection(self.text))
            )


@dataclass(frozen=True)
class SelectionResult:
    code: InputCode
    text: Optional[str] = None
    source: Optional[SelectionSource] = None
    target_token: Optional[TargetToken] = None
    capture: Optional[SelectionCapture] = None
    truncated: bool = False
    message: str = ""
    target: Optional[AppIdentity] = None
    original_clipboard: Optional[ClipboardSnapshot] = None
    clipboard_change_count: Optional[int] = None

    @property
    def ok(self) -> bool:
        return (self.code is InputCode.OK or self.code is InputCode.SUCCESS) and self.text is not None


@dataclass(frozen=True)
class PasteResult:
    """Wire status returned by native core and input adapters for paste operations."""

    code: InputCode
    status: str = "aborted"  # pasted | unverified | aborted
    transaction_id: str = ""
    undo_token: Optional[UndoToken] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return (self.code is InputCode.OK or self.code is InputCode.SUCCESS) and self.status == "pasted"


@dataclass(frozen=True)
class HotkeyResult:
    code: InputCode
    handles: Tuple[Any, ...] = ()
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code is InputCode.OK or self.code is InputCode.SUCCESS


class InputBackend(Protocol):
    """Narrow boundary consumed by an application orchestrator."""

    def register_hotkeys(
        self, specs: Sequence[HotkeySpec], callback: Callable[[HotkeySpec], None]
    ) -> HotkeyResult:
        ...

    def unregister_hotkeys(self) -> HotkeyResult:
        ...

    def frontmost_app(self) -> Outcome[AppIdentity]:
        ...

    def capture_selection(self, timeout: float = 0.35) -> SelectionResult:
        ...

    def paste_text(
        self, text: str, target: AppIdentity, original_clipboard: Optional[ClipboardSnapshot] = None
    ) -> Outcome[None]:
        ...

    def permission_state(self) -> PermissionState:
        ...

    def close(self) -> None:
        ...


class NullInputBackend:
    """Safe, deterministic adapter for unsupported platforms and unit tests."""

    def register_hotkeys(
        self, specs: Sequence[HotkeySpec], callback: Callable[[HotkeySpec], None]
    ) -> HotkeyResult:
        del specs, callback
        return HotkeyResult(InputCode.UNSUPPORTED_PLATFORM, message="Native input is unavailable")

    def unregister_hotkeys(self) -> HotkeyResult:
        return HotkeyResult(InputCode.OK)

    def frontmost_app(self) -> Outcome[AppIdentity]:
        return Outcome.failure(InputCode.UNSUPPORTED_PLATFORM, "No foreground-app provider")

    def capture_selection(self, timeout: float = 0.35) -> SelectionResult:
        del timeout
        return SelectionResult(InputCode.UNSUPPORTED_PLATFORM, message="Selection capture is unavailable")

    def paste_text(
        self, text: str, target: AppIdentity, original_clipboard: Optional[ClipboardSnapshot] = None
    ) -> Outcome[None]:
        del text, target, original_clipboard
        return Outcome.failure(InputCode.UNSUPPORTED_PLATFORM, "Paste is unavailable")

    def permission_state(self) -> PermissionState:
        return PermissionState(
            supported=False,
            accessibility=PermissionStatus.NOT_APPLICABLE,
            post_events=PermissionStatus.NOT_APPLICABLE,
            input_monitoring=PermissionStatus.NOT_APPLICABLE,
            clipboard=PermissionStatus.NOT_APPLICABLE,
            detail="Native macOS permissions do not apply on this platform",
        )

    def close(self) -> None:
        return None


def capture_compound_identity() -> CompoundIdentity:
    """Capture the foreground window's compound identity on Windows."""
    try:
        import sys as _sys
        if _sys.platform != "win32":
            return CompoundIdentity(0, 0, 0, 0, "", "")
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        hwnd = int(user32.GetForegroundWindow() or 0)
        if not hwnd:
            return CompoundIdentity(0, 0, 0, 0, "", "")

        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))

        klass = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(wintypes.HWND(hwnd), klass, 256)
        title = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(wintypes.HWND(hwnd), title, 512)
        title_hash = hashlib.sha256(title.value.encode("utf-8")).hexdigest()

        session_id = 0
        try:
            import _winapi
            session_id = int(_winapi.ProcessIdToSessionId(pid.value))
        except Exception:
            pass

        return CompoundIdentity(
            hwnd=hwnd,
            pid=int(pid.value),
            process_creation_time=0,
            session_id=session_id,
            window_class=klass.value,
            title_hash=title_hash,
        )
    except Exception:
        return CompoundIdentity(0, 0, 0, 0, "", "")
