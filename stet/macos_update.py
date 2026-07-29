"""Safe macOS update policy and external-installer handoff helpers.

macOS application bundles are signed units.  This module intentionally does
not copy, extract, rename, or remove anything in an ``.app`` bundle.  It only
validates external paths, checks an installer handoff, and starts an external
installer that owns whole-bundle replacement.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable


class UpdatePolicyError(ValueError):
    """Base error for an invalid or unsafe macOS update request."""


class UnsafeUpdatePath(UpdatePolicyError):
    """Raised when an update input is inside an application bundle."""


class InstallerUnavailable(UpdatePolicyError):
    """Raised when the requested external installer cannot be handed off."""


@dataclass(frozen=True)
class InstallerCheck:
    available: bool
    path: Path
    detail: str = ""


@dataclass(frozen=True)
class UpdateCheck:
    ready: bool
    archive: Path
    installer: Path
    target_app: Path
    detail: str = ""


@dataclass(frozen=True)
class InstallerHandoff:
    command: tuple[str, ...]
    process: Any
    target_app: Path


def _resolved(path: os.PathLike[str] | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def current_app_bundle(executable: os.PathLike[str] | str | None = None) -> Path | None:
    """Find the containing ``.app`` for a macOS executable, if any."""

    candidate = _resolved(executable or sys.executable)
    for parent in (candidate, *candidate.parents):
        if parent.name.lower().endswith(".app"):
            return parent
    return None


def is_inside_app_bundle(
    path: os.PathLike[str] | str,
    app_bundle: os.PathLike[str] | str | None = None,
) -> bool:
    """Return whether ``path`` is below an app bundle (the bundle root is allowed)."""

    candidate = _resolved(path)
    bundles = []
    if app_bundle is not None:
        bundles.append(_resolved(app_bundle))
    discovered = current_app_bundle(candidate)
    if discovered is not None and discovered not in bundles:
        bundles.append(discovered)
    return any(candidate != bundle and _is_descendant(candidate, bundle) for bundle in bundles)


def _is_descendant(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def assert_external_path(
    path: os.PathLike[str] | str,
    *,
    app_bundle: os.PathLike[str] | str | None = None,
    label: str = "update path",
) -> Path:
    """Validate a path that the update flow may stage/read externally.

    The returned path is normalized but no directory or file is created.
    """

    candidate = _resolved(path)
    if is_inside_app_bundle(candidate, app_bundle):
        bundle = _resolved(app_bundle) if app_bundle is not None else current_app_bundle(candidate)
        raise UnsafeUpdatePath(f"{label} must be outside app bundle {bundle}: {candidate}")
    return candidate


def update_staging_directory(
    app_name: str = "Stet",
    *,
    temporary_root: os.PathLike[str] | str | None = None,
    app_bundle: os.PathLike[str] | str | None = None,
) -> Path:
    """Return a non-bundle staging location without creating it."""

    root = _resolved(temporary_root or tempfile.gettempdir())
    return assert_external_path(
        root / f"{app_name}-updates",
        app_bundle=app_bundle,
        label="staging directory",
    )


def check_installer(
    installer: os.PathLike[str] | str,
    *,
    app_bundle: os.PathLike[str] | str | None = None,
) -> InstallerCheck:
    """Check that an external installer exists and is not embedded in Stet.app."""

    path = assert_external_path(installer, app_bundle=app_bundle, label="installer")
    if app_bundle is not None and path == _resolved(app_bundle):
        return InstallerCheck(False, path, "installer must be external to the running app")
    if not path.exists():
        return InstallerCheck(False, path, "external installer does not exist")
    if not (path.is_file() or path.suffix.lower() == ".app"):
        return InstallerCheck(False, path, "external installer is not a file or app bundle")
    if path.is_file() and not os.access(path, os.X_OK):
        return InstallerCheck(False, path, "external installer is not executable")
    return InstallerCheck(True, path)


def check_update(
    archive: os.PathLike[str] | str,
    installer: os.PathLike[str] | str,
    target_app: os.PathLike[str] | str,
    *,
    app_bundle: os.PathLike[str] | str | None = None,
) -> UpdateCheck:
    """Validate an external archive/installer handoff without changing state."""

    archive_path = assert_external_path(archive, app_bundle=app_bundle, label="update archive")
    installer_path = assert_external_path(installer, app_bundle=app_bundle, label="installer")
    target_path = _resolved(target_app)
    # The target bundle root is the one path intentionally handed to the
    # external installer.  A nested bundle path would still be an in-bundle
    # write and is rejected before the installer is launched.
    assert_external_path(target_path, app_bundle=app_bundle, label="target app")
    if target_path.suffix.lower() != ".app":
        return UpdateCheck(
            False,
            archive_path,
            installer_path,
            target_path,
            "target must be an .app bundle",
        )
    if not archive_path.is_file():
        return UpdateCheck(
            False,
            archive_path,
            installer_path,
            target_path,
            "update archive does not exist",
        )
    installer_state = check_installer(installer_path, app_bundle=app_bundle)
    if not installer_state.available:
        return UpdateCheck(False, archive_path, installer_path, target_path, installer_state.detail)
    return UpdateCheck(True, archive_path, installer_path, target_path)


def handoff_to_installer(
    archive: os.PathLike[str] | str,
    installer: os.PathLike[str] | str,
    target_app: os.PathLike[str] | str,
    *,
    app_bundle: os.PathLike[str] | str | None = None,
    runner: Callable[..., Any] | None = None,
    extra_args: Iterable[str] = (),
) -> InstallerHandoff:
    """Start the external installer responsible for replacing the whole app.

    The installer receives ``--install <archive> --target <app>``.  A caller
    can inject ``runner`` (normally ``subprocess.Popen``) for unit tests.
    No shell is used and this function never writes to the target bundle.
    """

    checked = check_update(archive, installer, target_app, app_bundle=app_bundle)
    if not checked.ready:
        raise InstallerUnavailable(checked.detail)

    command = (
        str(checked.installer),
        "--install",
        str(checked.archive),
        "--target",
        str(checked.target_app),
        *(str(arg) for arg in extra_args),
    )
    launch = runner or subprocess.Popen
    process = launch(list(command), shell=False, close_fds=True)
    return InstallerHandoff(command, process, checked.target_app)


__all__ = [
    "InstallerCheck",
    "InstallerHandoff",
    "InstallerUnavailable",
    "UnsafeUpdatePath",
    "UpdateCheck",
    "UpdatePolicyError",
    "assert_external_path",
    "check_installer",
    "check_update",
    "current_app_bundle",
    "handoff_to_installer",
    "is_inside_app_bundle",
    "update_staging_directory",
]
