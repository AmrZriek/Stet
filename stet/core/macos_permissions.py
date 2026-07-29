"""Small, re-checkable macOS TCC permission helpers.

All PyObjC imports are lazy.  Prompting is explicit and separate from checking,
so callers do not accidentally loop a privacy prompt.
"""

from __future__ import annotations

import sys
from typing import Any

from stet.core.input import PermissionState, PermissionStatus


def _load_application_services() -> Any:
    import ApplicationServices  # type: ignore[import-not-found]

    return ApplicationServices


def _load_quartz() -> Any:
    import Quartz  # type: ignore[import-not-found]

    return Quartz


def accessibility_status(application_services: Any = None) -> PermissionStatus:
    if sys.platform != "darwin":
        return PermissionStatus.NOT_APPLICABLE
    try:
        services = application_services or _load_application_services()
        trusted = services.AXIsProcessTrusted()
        return PermissionStatus.GRANTED if bool(trusted) else PermissionStatus.DENIED
    except (AttributeError, ImportError, OSError):
        return PermissionStatus.UNKNOWN


def post_events_status(quartz: Any = None) -> PermissionStatus:
    if sys.platform != "darwin":
        return PermissionStatus.NOT_APPLICABLE
    try:
        module = quartz or _load_quartz()
        trusted = module.CGPreflightPostEventAccess()
        return PermissionStatus.GRANTED if bool(trusted) else PermissionStatus.DENIED
    except (AttributeError, ImportError, OSError):
        return PermissionStatus.UNKNOWN


def input_monitoring_status(quartz: Any = None) -> PermissionStatus:
    if sys.platform != "darwin":
        return PermissionStatus.NOT_APPLICABLE
    try:
        module = quartz or _load_quartz()
        trusted = module.CGPreflightListenEventAccess()
        return PermissionStatus.GRANTED if bool(trusted) else PermissionStatus.DENIED
    except (AttributeError, ImportError, OSError):
        return PermissionStatus.UNKNOWN


def request_accessibility(application_services: Any = None) -> PermissionStatus:
    """Ask once, then return the current state; approval may require app return."""

    if sys.platform != "darwin":
        return PermissionStatus.NOT_APPLICABLE
    try:
        services = application_services or _load_application_services()
        option = getattr(services, "kAXTrustedCheckOptionPrompt", "AXTrustedCheckOptionPrompt")
        services.AXIsProcessTrustedWithOptions({option: True})
    except (AttributeError, ImportError, OSError):
        return PermissionStatus.UNKNOWN
    return accessibility_status(services)


def request_post_events(quartz: Any = None) -> PermissionStatus:
    if sys.platform != "darwin":
        return PermissionStatus.NOT_APPLICABLE
    try:
        module = quartz or _load_quartz()
        request = getattr(module, "CGRequestPostEventAccess", None)
        if request is not None:
            request()
    except (AttributeError, ImportError, OSError):
        return PermissionStatus.UNKNOWN
    return post_events_status(module)


def request_input_monitoring(quartz: Any = None) -> PermissionStatus:
    """Ask macOS for Input Monitoring access for global shortcut listening.

    The call is intentionally separate from :func:`input_monitoring_status`.
    It is safe to invoke once from an explicit shortcut-registration attempt;
    macOS owns the prompt and retains the user's decision.
    """

    if sys.platform != "darwin":
        return PermissionStatus.NOT_APPLICABLE
    try:
        module = quartz or _load_quartz()
        request = getattr(module, "CGRequestListenEventAccess", None)
        if request is not None:
            request()
    except (AttributeError, ImportError, OSError):
        return PermissionStatus.UNKNOWN
    return input_monitoring_status(module)


def permission_state(
    application_services: Any = None,
    quartz: Any = None,
    clipboard_status: PermissionStatus = PermissionStatus.UNKNOWN,
) -> PermissionState:
    """Return current state without prompting or caching stale TCC decisions."""

    if sys.platform != "darwin":
        return PermissionState(
            supported=False,
            accessibility=PermissionStatus.NOT_APPLICABLE,
            post_events=PermissionStatus.NOT_APPLICABLE,
            input_monitoring=PermissionStatus.NOT_APPLICABLE,
            clipboard=PermissionStatus.NOT_APPLICABLE,
            detail="macOS privacy permissions are unavailable on this platform",
        )
    return PermissionState(
        supported=True,
        accessibility=accessibility_status(application_services),
        post_events=post_events_status(quartz),
        input_monitoring=input_monitoring_status(quartz),
        clipboard=clipboard_status,
    )
