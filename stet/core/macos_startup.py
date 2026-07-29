"""macOS login-item support backed by ``SMAppService``.

The framework is imported only when a caller asks for a macOS login-item
operation.  This keeps importing Stet safe on Windows/Linux and also makes
the adapter straightforward to test with a small injected framework double.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from enum import Enum
from typing import Any


class LoginItemStatus(str, Enum):
    """State reported by the macOS login-item service."""

    ENABLED = "enabled"
    DISABLED = "disabled"
    REQUIRES_APPROVAL = "requires_approval"
    DENIED = "denied"
    NOT_FOUND = "not_found"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class LoginItemState:
    """A non-throwing snapshot of login-item support and state."""

    supported: bool
    status: LoginItemStatus
    detail: str = ""

    @property
    def enabled(self) -> bool:
        return self.status is LoginItemStatus.ENABLED


def _load_service_management() -> Any:
    """Load PyObjC lazily so this module remains importable off macOS."""

    import ServiceManagement  # type: ignore[import-not-found]

    return ServiceManagement


def _main_app(service_management: Any) -> Any:
    service_type = getattr(service_management, "SMAppService")
    factory = getattr(service_type, "mainApp")
    return factory() if callable(factory) else factory


def _status_value(service: Any) -> Any:
    value = getattr(service, "status")
    return value() if callable(value) else value


def _constant(module: Any, name: str) -> Any:
    """Find an enum constant on either the module or its SMAppService type."""

    value = getattr(module, name, None)
    if value is not None:
        return value
    service_type = getattr(module, "SMAppService", None)
    return getattr(service_type, name, None)


def _status_from_value(value: Any, service_management: Any) -> LoginItemStatus:
    """Map PyObjC enum values and friendly test doubles to our stable enum."""

    names = (
        ("SMAppServiceStatusEnabled", LoginItemStatus.ENABLED),
        ("SMAppServiceStatusRequiresApproval", LoginItemStatus.REQUIRES_APPROVAL),
        ("SMAppServiceStatusNotRegistered", LoginItemStatus.DISABLED),
        ("SMAppServiceStatusNotFound", LoginItemStatus.NOT_FOUND),
        ("SMAppServiceStatusDenied", LoginItemStatus.DENIED),
    )
    for name, status in names:
        expected = _constant(service_management, name)
        if expected is not None and value == expected:
            return status

    # These are the documented SMAppServiceStatus values.  Keep this fallback
    # for PyObjC builds that bridge the enum as a plain integer without
    # exporting the symbolic constants on the module.
    if isinstance(value, int):
        return {
            0: LoginItemStatus.DISABLED,
            1: LoginItemStatus.ENABLED,
            2: LoginItemStatus.REQUIRES_APPROVAL,
            3: LoginItemStatus.NOT_FOUND,
        }.get(value, LoginItemStatus.UNKNOWN)

    text = str(getattr(value, "name", value)).lower().replace("-", "_")
    if "require" in text and "approv" in text:
        return LoginItemStatus.REQUIRES_APPROVAL
    if "enable" in text:
        return LoginItemStatus.ENABLED
    if "notregistered" in text or "not_registered" in text or "disabled" in text:
        return LoginItemStatus.DISABLED
    if "notfound" in text or "not_found" in text:
        return LoginItemStatus.NOT_FOUND
    if "denied" in text or "reject" in text:
        return LoginItemStatus.DENIED
    return LoginItemStatus.UNKNOWN


def _error_text(error: Any) -> str:
    if error is None:
        return ""
    description = getattr(error, "localizedDescription", None)
    if callable(description):
        description = description()
    return str(description or error)


def _operation_result(result: Any) -> tuple[bool, Any]:
    """Normalize PyObjC ``(success, NSError)`` and simple test-double results."""

    if isinstance(result, tuple):
        if not result:
            return False, None
        return bool(result[0]), result[1] if len(result) > 1 else None
    return bool(result), None


def query_login_item(service_management: Any = None) -> LoginItemState:
    """Return the current login-item state without prompting the user."""

    if sys.platform != "darwin":
        return LoginItemState(
            supported=False,
            status=LoginItemStatus.NOT_APPLICABLE,
            detail="macOS login items are unavailable on this platform",
        )

    try:
        framework = service_management or _load_service_management()
        service = _main_app(framework)
        return LoginItemState(True, _status_from_value(_status_value(service), framework))
    except ImportError:
        return LoginItemState(
            False, LoginItemStatus.UNKNOWN, "PyObjC ServiceManagement is not installed"
        )
    except (AttributeError, OSError) as exc:
        return LoginItemState(True, LoginItemStatus.UNKNOWN, str(exc))
    except Exception as exc:  # Framework errors must not break application startup.
        return LoginItemState(True, LoginItemStatus.UNKNOWN, str(exc))


def _set_login_item(enabled: bool, service_management: Any = None) -> LoginItemState:
    if sys.platform != "darwin":
        return query_login_item(service_management)

    try:
        framework = service_management or _load_service_management()
        service = _main_app(framework)
        method_name = "registerAndReturnError_" if enabled else "unregisterAndReturnError_"
        method = getattr(service, method_name)
        success, error = _operation_result(method())
        if not success:
            status = LoginItemStatus.DENIED if error is not None else LoginItemStatus.UNKNOWN
            return LoginItemState(
                True,
                status,
                _error_text(error) or "macOS rejected the login-item change",
            )
        return query_login_item(framework)
    except ImportError:
        return LoginItemState(
            False, LoginItemStatus.UNKNOWN, "PyObjC ServiceManagement is not installed"
        )
    except (AttributeError, OSError) as exc:
        return LoginItemState(True, LoginItemStatus.UNKNOWN, str(exc))
    except Exception as exc:  # Framework errors must not escape a settings toggle.
        return LoginItemState(True, LoginItemStatus.UNKNOWN, str(exc))


def enable_login_item(service_management: Any = None) -> LoginItemState:
    """Register the app as a login item and return the resulting state."""

    return _set_login_item(True, service_management)


def disable_login_item(service_management: Any = None) -> LoginItemState:
    """Unregister the app as a login item and return the resulting state."""

    return _set_login_item(False, service_management)


# Short aliases are useful to callers that already group startup operations.
query = query_login_item
enable = enable_login_item
disable = disable_login_item


__all__ = [
    "LoginItemState",
    "LoginItemStatus",
    "disable_login_item",
    "enable_login_item",
    "query_login_item",
    "disable",
    "enable",
    "query",
]
