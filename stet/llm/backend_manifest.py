"""Declarative llama.cpp backend metadata.

The manifest deliberately contains only platform facts and bundle layout.  The
runtime policy and probing code lives in :mod:`stet.llm.backend_manager`.
Keeping the two separate makes host detection testable without macOS or
PyObjC, and prevents a platform-specific path from becoming an implicit
backend choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


def normalize_architecture(machine: str | None) -> str:
    """Return the architecture spelling used by the backend manifest."""

    value = (machine or "").strip().lower().replace("-", "_")
    if value in {"arm64", "aarch64"}:
        return "arm64"
    if value in {"x86_64", "amd64", "x64", "x86-64"}:
        return "x86_64"
    return value or "unknown"


@dataclass(frozen=True)
class HostDescription:
    """The process and hardware identity relevant to backend selection."""

    platform: str
    process_architecture: str
    hardware_architecture: str
    rosetta: bool = False

    @property
    def architecture(self) -> str:
        """A stable host label: ``arm64``, ``x86_64``, or ``rosetta``."""

        if self.rosetta:
            return "rosetta"
        return self.hardware_architecture or self.process_architecture

    @property
    def bundle_architecture(self) -> str:
        """Architecture of the preferred bundled executable.

        Rosetta processes can launch a native arm64 child, so they use the
        native Apple Silicon bundle rather than an Intel-only bundle.
        """

        return self.hardware_architecture or self.process_architecture


@dataclass(frozen=True)
class BackendManifest:
    """One supported backend bundle layout."""

    name: str
    platform: str
    architecture: str
    backend: str
    directory_globs: tuple[str, ...]
    executable_names: tuple[str, ...]
    capabilities: frozenset[str] = frozenset()


# Keep the names broad enough for both release archives and development
# bundles, but still architecture-specific on macOS.  ``llama_cpp`` is the
# legacy in-place layout used by existing installations.
BACKEND_MANIFEST: tuple[BackendManifest, ...] = (
    BackendManifest(
        name="macos-arm64-metal",
        platform="darwin",
        architecture="arm64",
        backend="metal",
        directory_globs=(
            "llama_cpp",
            "llama-*macos-arm64*",
            "llama-*darwin*arm64*",
            "llama-*arm64*",
            # Current upstream tarballs unpack to a neutral llama-bNNNNN
            # directory, so architecture verification below is authoritative.
            "llama-b*",
        ),
        executable_names=("llama-server",),
        capabilities=frozenset({"metal"}),
    ),
    BackendManifest(
        name="macos-x86_64-cpu",
        platform="darwin",
        architecture="x86_64",
        backend="cpu",
        directory_globs=(
            "llama_cpp",
            "llama-*macos-x86_64*",
            "llama-*macos-x64*",
            "llama-*darwin*x86_64*",
            "llama-*darwin*x64*",
            "llama-b*",
        ),
        executable_names=("llama-server",),
    ),
    # Non-macOS entries document the existing portable layouts.  The Windows
    # resolver continues to use its historical path checks in utils.py.
    BackendManifest(
        name="windows-x86_64-cuda",
        platform="win32",
        architecture="x86_64",
        backend="native",
        directory_globs=("llama_cpp", "llama-*win*x64*", "llama-**"),
        executable_names=("llama-server.exe",),
        capabilities=frozenset({"cuda"}),
    ),
    BackendManifest(
        name="linux-x86_64-cpu",
        platform="linux",
        architecture="x86_64",
        backend="cpu",
        directory_globs=("llama_cpp", "llama-*linux*x64*"),
        executable_names=("llama-server",),
    ),
)


def manifests_for_host(
    host: HostDescription,
    manifests: Iterable[BackendManifest] = BACKEND_MANIFEST,
) -> tuple[BackendManifest, ...]:
    """Return manifest entries applicable to a host, in priority order."""

    architecture = host.bundle_architecture
    return tuple(
        item
        for item in manifests
        if item.platform == host.platform and item.architecture == architecture
    )
