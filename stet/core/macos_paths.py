"""macOS user-state paths resolved through Foundation search-path APIs."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class MacOSPaths:
    supported: bool
    application_support: Optional[Path] = None
    models: Optional[Path] = None
    migrations: Optional[Path] = None
    caches: Optional[Path] = None
    downloads: Optional[Path] = None
    temporary: Optional[Path] = None
    logs: Optional[Path] = None
    crash: Optional[Path] = None
    detail: str = ""


def _foundation_paths(foundation: Any, directory: Any, domain: Any) -> list:
    values = foundation.NSSearchPathForDirectoriesInDomains(directory, domain, True)
    if isinstance(values, (str, bytes)):
        values = [values]
    return list(values or [])


def resolve_paths(app_name: str = "Stet", foundation: Any = None) -> MacOSPaths:
    """Resolve writable paths without importing Foundation on non-macOS."""

    if sys.platform != "darwin":
        return MacOSPaths(False, detail="macOS Library paths are unavailable on this platform")

    if foundation is None:
        try:
            import Foundation as foundation  # type: ignore[import-not-found]
        except ImportError:
            return MacOSPaths(False, detail="PyObjC Foundation is not installed")

    user_domain = getattr(foundation, "NSUserDomainMask", 1)
    support_dir = getattr(foundation, "NSApplicationSupportDirectory", 14)
    caches_dir = getattr(foundation, "NSCachesDirectory", 13)
    support_roots = _foundation_paths(foundation, support_dir, user_domain)
    cache_roots = _foundation_paths(foundation, caches_dir, user_domain)
    if not support_roots or not cache_roots:
        return MacOSPaths(False, detail="Foundation did not return user Library paths")

    support = Path(str(support_roots[0])) / app_name
    caches = Path(str(cache_roots[0])) / app_name
    # ``support`` is ``~/Library/Application Support/Stet``.  Keep logs in
    # the platform-standard sibling, not beneath Application Support.
    logs = support.parent.parent / "Logs" / app_name
    return MacOSPaths(
        supported=True,
        application_support=support,
        models=support / "Models",
        migrations=support / "migrations.json",
        caches=caches,
        downloads=caches / "downloads",
        temporary=caches / "temporary",
        logs=logs,
        crash=logs / "crash",
    )


def ensure_directories(paths: MacOSPaths) -> bool:
    """Create only known user-state directories; unsupported paths are a no-op."""

    if not paths.supported:
        return False
    directories = (paths.application_support, paths.models, paths.downloads, paths.temporary, paths.logs, paths.crash)
    try:
        for directory in directories:
            if directory is not None:
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                directory.chmod(0o700)
    except OSError:
        return False
    return True
