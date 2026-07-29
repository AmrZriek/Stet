"""Unit tests for the platform-neutral input contracts and macOS adapters.

Native AppKit, Foundation, Quartz, and TCC objects are represented by small
test doubles so these tests run on macOS, Windows, and Linux CI alike.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import stet.core.clipboard_macos as clipboard_macos
import stet.core.macos_paths as macos_paths
import stet.core.macos_permissions as macos_permissions
from stet.core.input import (
    AppIdentity,
    ClipboardSnapshot,
    HotkeySpec,
    InputCode,
    NullInputBackend,
    PermissionState,
    PermissionStatus,
    PasteboardItemSnapshot,
    PasteboardRepresentation,
    SelectionResult,
    SelectionSource,
    normalize_shortcut,
)


def _pretend_macos(monkeypatch):
    monkeypatch.setattr(clipboard_macos.sys, "platform", "darwin")
    monkeypatch.setattr(macos_paths.sys, "platform", "darwin")
    monkeypatch.setattr(macos_permissions.sys, "platform", "darwin")


class FakePasteboardItem:
    def __init__(self, representations=None):
        self.representations = dict(representations or {})

    def types(self):
        return list(self.representations)

    def dataForType_(self, type_identifier):
        return self.representations[type_identifier]

    @classmethod
    def alloc(cls):
        return cls()

    def init(self):
        return self

    def setData_forType_(self, data, type_identifier):
        self.representations[type_identifier] = data


class FakePasteboard:
    def __init__(self, items=None, change_count=4, text=None):
        self.items = list(items or [])
        self.change_count = change_count
        self.text = text

    def pasteboardItems(self):
        return self.items

    def changeCount(self):
        return self.change_count

    def stringForType_(self, _type_identifier):
        return self.text

    def clearContents(self):
        self.items = []
        self.text = None
        self.change_count += 1

    def setString_forType_(self, text, _type_identifier):
        self.text = text
        self.change_count += 1
        return True

    def writeObjects_(self, objects):
        self.items = list(objects)
        self.change_count += 1
        return True


def test_input_contracts_normalize_shortcuts_and_report_null_backend():
    assert normalize_shortcut(" Shift + CMD + F9 ") == "command+shift+f9"
    assert HotkeySpec("ctrl+alt+k", identifier="correct").parts == (
        "control",
        "option",
        "k",
    )
    with pytest.raises(ValueError, match="end with a non-modifier"):
        HotkeySpec("command+shift")

    backend = NullInputBackend()
    assert backend.register_hotkeys([], lambda _spec: None).code is InputCode.UNSUPPORTED_PLATFORM
    assert backend.frontmost_app().code is InputCode.UNSUPPORTED_PLATFORM
    assert backend.capture_selection().code is InputCode.UNSUPPORTED_PLATFORM
    assert backend.paste_text("text", AppIdentity(42)).code is InputCode.UNSUPPORTED_PLATFORM
    assert backend.permission_state() == PermissionState(
        supported=False,
        accessibility=PermissionStatus.NOT_APPLICABLE,
        post_events=PermissionStatus.NOT_APPLICABLE,
        input_monitoring=PermissionStatus.NOT_APPLICABLE,
        clipboard=PermissionStatus.NOT_APPLICABLE,
        detail="Native macOS permissions do not apply on this platform",
    )


def test_input_contracts_model_selection_and_app_identity():
    target = AppIdentity(42, bundle_id="com.example.Editor")
    assert target.matches(AppIdentity(42, name="Editor"))
    assert not target.matches(AppIdentity(43, bundle_id=target.bundle_id))
    assert not target.matches(None)

    result = SelectionResult(
        InputCode.OK,
        text="selected",
        source=SelectionSource.ACCESSIBILITY,
        target=target,
    )
    assert result.ok
    assert not SelectionResult(InputCode.OK).ok


def test_clipboard_snapshot_reads_all_representations(monkeypatch):
    _pretend_macos(monkeypatch)
    board = FakePasteboard(
        [
            FakePasteboardItem(
                {
                    "public.utf8-plain-text": bytearray(b"hello"),
                    "public.rtf": None,
                }
            )
        ],
        change_count=9,
    )

    result = clipboard_macos.snapshot_pasteboard(pasteboard=board)

    assert result.ok
    assert result.value == ClipboardSnapshot(
        change_count=9,
        items=(
            PasteboardItemSnapshot(
                (PasteboardRepresentation("public.utf8-plain-text", b"hello"),)
            ),
        ),
    )


def test_clipboard_text_write_and_restore_are_ownership_aware(monkeypatch):
    _pretend_macos(monkeypatch)
    appkit = SimpleNamespace(
        NSPasteboardTypeString="public.utf8-plain-text",
        NSPasteboardItem=FakePasteboardItem,
    )
    board = FakePasteboard(text="before", change_count=2)

    written = clipboard_macos.write_text("after", appkit=appkit, pasteboard=board)
    assert written.value == 4
    assert clipboard_macos.read_text(appkit=appkit, pasteboard=board).value == "after"

    snapshot = ClipboardSnapshot(
        change_count=1,
        items=(
            PasteboardItemSnapshot(
                (PasteboardRepresentation("public.utf8-plain-text", b"original"),)
            ),
        ),
    )
    restored = clipboard_macos.restore_snapshot(
        snapshot, expected_change_count=board.change_count, appkit=appkit, pasteboard=board
    )
    assert restored.ok
    assert board.items[0].representations == {"public.utf8-plain-text": b"original"}

    board.change_count += 1
    rejected = clipboard_macos.restore_snapshot(
        snapshot, expected_change_count=restored.value, appkit=appkit, pasteboard=board
    )
    assert rejected.code is InputCode.PASTEBOARD_CHANGED_EXTERNALLY


def test_clipboard_without_text_returns_not_available(monkeypatch):
    _pretend_macos(monkeypatch)
    appkit = SimpleNamespace(NSPasteboardTypeString="public.utf8-plain-text")
    result = clipboard_macos.read_text(appkit=appkit, pasteboard=FakePasteboard())
    assert result.code is InputCode.NOT_AVAILABLE


class FakeFoundation:
    NSUserDomainMask = 1
    NSApplicationSupportDirectory = 14
    NSCachesDirectory = 13

    def __init__(self):
        self.calls = []

    def NSSearchPathForDirectoriesInDomains(self, directory, domain, expand):
        self.calls.append((directory, domain, expand))
        if directory == self.NSApplicationSupportDirectory:
            return ["/Users/test/Library/Application Support"]
        return "/Users/test/Library/Caches"


def test_macos_paths_use_foundation_search_paths(monkeypatch):
    _pretend_macos(monkeypatch)
    foundation = FakeFoundation()

    paths = macos_paths.resolve_paths("Example", foundation=foundation)

    assert paths.supported
    assert paths.application_support == Path("/Users/test/Library/Application Support/Example")
    assert paths.models == paths.application_support / "Models"
    assert paths.caches == Path("/Users/test/Library/Caches/Example")
    assert paths.downloads == paths.caches / "downloads"
    assert foundation.calls == [(14, 1, True), (13, 1, True)]


def test_macos_paths_create_only_supported_directories(monkeypatch, tmp_path):
    _pretend_macos(monkeypatch)
    paths = macos_paths.MacOSPaths(
        supported=True,
        application_support=tmp_path / "Support",
        models=tmp_path / "Support" / "Models",
        downloads=tmp_path / "Cache" / "downloads",
        temporary=tmp_path / "Cache" / "temporary",
        logs=tmp_path / "Logs",
        crash=tmp_path / "Logs" / "crash",
    )

    assert macos_paths.ensure_directories(paths)
    for directory in (
        paths.application_support,
        paths.models,
        paths.downloads,
        paths.temporary,
        paths.logs,
        paths.crash,
    ):
        assert directory.is_dir()

    unsupported = macos_paths.MacOSPaths(False)
    assert not macos_paths.ensure_directories(unsupported)


class FakeApplicationServices:
    kAXTrustedCheckOptionPrompt = "prompt"

    def __init__(self, trusted=True):
        self.trusted = trusted
        self.prompt_options = None

    def AXIsProcessTrusted(self):
        return self.trusted

    def AXIsProcessTrustedWithOptions(self, options):
        self.prompt_options = options
        return self.trusted


class FakeQuartz:
    def __init__(self, post_events=True, input_monitoring=False):
        self.post_events = post_events
        self.input_monitoring = input_monitoring
        self.post_request_count = 0
        self.listen_request_count = 0

    def CGPreflightPostEventAccess(self):
        return self.post_events

    def CGPreflightListenEventAccess(self):
        return self.input_monitoring

    def CGRequestPostEventAccess(self):
        self.post_request_count += 1

    def CGRequestListenEventAccess(self):
        self.listen_request_count += 1


def test_macos_permissions_check_and_request_with_fake_services(monkeypatch):
    _pretend_macos(monkeypatch)
    services = FakeApplicationServices(trusted=True)
    quartz = FakeQuartz(post_events=True, input_monitoring=False)

    assert macos_permissions.accessibility_status(services) is PermissionStatus.GRANTED
    assert macos_permissions.post_events_status(quartz) is PermissionStatus.GRANTED
    assert macos_permissions.input_monitoring_status(quartz) is PermissionStatus.DENIED
    assert macos_permissions.request_accessibility(services) is PermissionStatus.GRANTED
    assert services.prompt_options == {"prompt": True}
    assert macos_permissions.request_post_events(quartz) is PermissionStatus.GRANTED
    assert quartz.post_request_count == 1
    assert macos_permissions.request_input_monitoring(quartz) is PermissionStatus.DENIED
    assert quartz.listen_request_count == 1

    state = macos_permissions.permission_state(
        services, quartz, clipboard_status=PermissionStatus.GRANTED
    )
    assert state.supported
    assert state.all_required_granted
    assert state.input_monitoring is PermissionStatus.DENIED


def test_macos_permissions_convert_native_errors_to_unknown(monkeypatch):
    _pretend_macos(monkeypatch)
    services = SimpleNamespace(AXIsProcessTrusted=lambda: (_ for _ in ()).throw(OSError("TCC")))
    quartz = SimpleNamespace(
        CGPreflightPostEventAccess=lambda: (_ for _ in ()).throw(OSError("TCC")),
        CGPreflightListenEventAccess=lambda: (_ for _ in ()).throw(AttributeError("missing")),
    )

    assert macos_permissions.accessibility_status(services) is PermissionStatus.UNKNOWN
    assert macos_permissions.post_events_status(quartz) is PermissionStatus.UNKNOWN
    assert macos_permissions.input_monitoring_status(quartz) is PermissionStatus.UNKNOWN
