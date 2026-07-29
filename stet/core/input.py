"""Platform-neutral contracts for global input and selection transactions.

The concrete platform adapters are intentionally not imported here.  This module
is safe to import on every supported platform, including machines without
PyObjC installed.
"""

from __future__ import annotations

from dataclasses import dataclass
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


T = TypeVar("T")


@dataclass(frozen=True)
class Outcome(Generic[T]):
    """A typed operation result; callers never need to infer failure from ``None``."""

    code: InputCode
    value: Optional[T] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code is InputCode.OK

    @classmethod
    def success(cls, value: Optional[T] = None, message: str = "") -> "Outcome[T]":
        return cls(InputCode.OK, value, message)

    @classmethod
    def failure(cls, code: InputCode, message: str = "") -> "Outcome[T]":
        return cls(code, None, message)


class SelectionSource(str, Enum):
    ACCESSIBILITY = "accessibility"
    CLIPBOARD = "clipboard"


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


@dataclass(frozen=True)
class SelectionResult:
    code: InputCode
    text: Optional[str] = None
    source: Optional[SelectionSource] = None
    target: Optional[AppIdentity] = None
    original_clipboard: Optional[ClipboardSnapshot] = None
    clipboard_change_count: Optional[int] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code is InputCode.OK and self.text is not None


@dataclass(frozen=True)
class HotkeyResult:
    code: InputCode
    handles: Tuple[Any, ...] = ()
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code is InputCode.OK


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
