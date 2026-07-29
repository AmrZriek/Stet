"""Lazy AppKit pasteboard adapter with complete, ownership-aware snapshots."""

from __future__ import annotations

import sys
from typing import Any, Optional

from stet.core.input import (
    ClipboardSnapshot,
    InputCode,
    Outcome,
    PasteboardItemSnapshot,
    PasteboardRepresentation,
)


def _load_appkit() -> Any:
    import AppKit  # type: ignore[import-not-found]

    return AppKit


def _pasteboard(appkit: Any = None, pasteboard: Any = None) -> Any:
    if pasteboard is not None:
        return pasteboard
    module = appkit or _load_appkit()
    return module.NSPasteboard.generalPasteboard()


def _as_bytes(data: Any) -> bytes:
    if isinstance(data, bytes):
        return data
    if data is None:
        return b""
    try:
        return bytes(data)
    except (TypeError, ValueError):
        return str(data).encode("utf-8")


def snapshot_pasteboard(appkit: Any = None, pasteboard: Any = None) -> Outcome[ClipboardSnapshot]:
    """Capture every readable UTI/data representation and the change count."""

    if sys.platform != "darwin" and pasteboard is None:
        return Outcome.failure(InputCode.UNSUPPORTED_PLATFORM, "macOS pasteboard is unavailable")
    try:
        board = _pasteboard(appkit, pasteboard)
        items = []
        for item in list(board.pasteboardItems() or []):
            representations = []
            for type_identifier in list(item.types() or []):
                data = item.dataForType_(type_identifier)
                if data is not None:
                    representations.append(
                        PasteboardRepresentation(str(type_identifier), _as_bytes(data))
                    )
            items.append(PasteboardItemSnapshot(tuple(representations)))
        return Outcome.success(ClipboardSnapshot(int(board.changeCount()), tuple(items)))
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as exc:
        return Outcome.failure(InputCode.ERROR, "Could not read the macOS pasteboard: %s" % exc)


snapshot = snapshot_pasteboard


def read_text(appkit: Any = None, pasteboard: Any = None) -> Outcome[str]:
    if sys.platform != "darwin" and pasteboard is None:
        return Outcome.failure(InputCode.UNSUPPORTED_PLATFORM, "macOS pasteboard is unavailable")
    try:
        module = appkit or _load_appkit()
        board = _pasteboard(module, pasteboard)
        text_type = getattr(module, "NSPasteboardTypeString", "public.utf8-plain-text")
        value = board.stringForType_(text_type)
        if value is None:
            return Outcome.failure(InputCode.NOT_AVAILABLE, "The pasteboard has no text representation")
        return Outcome.success(str(value))
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as exc:
        return Outcome.failure(InputCode.ERROR, "Could not read pasteboard text: %s" % exc)


def write_text(text: str, appkit: Any = None, pasteboard: Any = None) -> Outcome[int]:
    """Write text and return the resulting change count as ownership evidence."""

    if sys.platform != "darwin" and pasteboard is None:
        return Outcome.failure(InputCode.UNSUPPORTED_PLATFORM, "macOS pasteboard is unavailable")
    try:
        module = appkit or _load_appkit()
        board = _pasteboard(module, pasteboard)
        text_type = getattr(module, "NSPasteboardTypeString", "public.utf8-plain-text")
        board.clearContents()
        if not board.setString_forType_(str(text), text_type):
            return Outcome.failure(InputCode.ERROR, "The pasteboard rejected text")
        return Outcome.success(int(board.changeCount()))
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as exc:
        return Outcome.failure(InputCode.ERROR, "Could not write pasteboard text: %s" % exc)


def restore_snapshot(
    snapshot_value: ClipboardSnapshot,
    expected_change_count: Optional[int] = None,
    appkit: Any = None,
    pasteboard: Any = None,
) -> Outcome[int]:
    """Restore only while the pasteboard still has the expected owner/change count."""

    if sys.platform != "darwin" and pasteboard is None:
        return Outcome.failure(InputCode.UNSUPPORTED_PLATFORM, "macOS pasteboard is unavailable")
    try:
        board = _pasteboard(appkit, pasteboard)
        current = int(board.changeCount())
        if expected_change_count is not None and current != expected_change_count:
            return Outcome.failure(
                InputCode.PASTEBOARD_CHANGED_EXTERNALLY,
                "The pasteboard changed after Stet wrote it; newer content was preserved",
            )
        module = appkit or _load_appkit()
        item_class = getattr(module, "NSPasteboardItem", None)
        if item_class is None or not hasattr(board, "writeObjects_"):
            return Outcome.failure(InputCode.NOT_AVAILABLE, "Pasteboard snapshot restore is unavailable")
        board.clearContents()
        objects = []
        for item_snapshot in snapshot_value.items:
            item = item_class.alloc().init()
            for representation in item_snapshot.representations:
                item.setData_forType_(representation.data, representation.type_identifier)
            objects.append(item)
        if objects and not board.writeObjects_(objects):
            return Outcome.failure(InputCode.ERROR, "The pasteboard rejected the snapshot")
        if not objects:
            board.clearContents()
        return Outcome.success(int(board.changeCount()))
    except (AttributeError, ImportError, OSError, TypeError, ValueError) as exc:
        return Outcome.failure(InputCode.ERROR, "Could not restore the pasteboard: %s" % exc)
