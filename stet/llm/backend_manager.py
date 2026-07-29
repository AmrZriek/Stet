"""Runtime selection and safe resolution of llama.cpp backends."""

from __future__ import annotations

import os
import platform as platform_module
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from stet.llm.backend_manifest import (
    BACKEND_MANIFEST,
    BackendManifest,
    HostDescription,
    manifests_for_host,
    normalize_architecture,
)

BACKEND_MODES = frozenset({"auto", "metal", "cpu"})
LLAMA_CPP_LATEST_RELEASE_API = "https://api.github.com/repos/ggml-org/llama.cpp/releases/latest"
_BACKEND_RELEASE_RE = re.compile(r"(?:^|[-_])b(\d+)(?:[-_]|$)", re.IGNORECASE)
# The current upstream macOS artifacts are built on macOS 26.  This digest-
# pinned release is verified on macOS 14 and remains the compatibility channel
# for Sonoma/Sequoia-era machines until upstream lowers its deployment
# target again.  Intel is intentionally CPU-only in the runtime policy.
_MACOS_COMPATIBLE_ASSETS = {
    "arm64": (
        "llama-b9000-bin-macos-arm64.tar.gz",
        "https://github.com/ggml-org/llama.cpp/releases/download/b9000/llama-b9000-bin-macos-arm64.tar.gz",
        "e4531e819dd9fe4add199db998df55cf8bd20e18a67cbd1449b49409dc01c642",
    ),
    "x86_64": (
        "llama-b9000-bin-macos-x64.tar.gz",
        "https://github.com/ggml-org/llama.cpp/releases/download/b9000/llama-b9000-bin-macos-x64.tar.gz",
        "82b81368266b6290509c221484df073624c5325239d6f375d60589fa760519bc",
    ),
}


class BackendError(RuntimeError):
    """A backend could not be selected or safely started."""

    def __init__(self, message: str, failure_class: str = "unknown"):
        super().__init__(message)
        self.failure_class = failure_class


@dataclass(frozen=True)
class CapabilityProbe:
    """Result of a side-effect-free backend capability query."""

    capability: str
    supported: bool | None
    failure_class: str | None = None
    detail: str = ""


@dataclass(frozen=True)
class BackendSelection:
    requested_mode: str
    mode: str
    gpu_layers: int
    reason: str
    host: HostDescription


@dataclass(frozen=True)
class BackendDownload:
    """A verified GitHub release asset suitable for the current macOS host."""

    url: str
    filename: str
    sha256: str
    destination: Path
    label: str


def classify_backend_failure(error: BaseException | str) -> str:
    """Map a backend failure to a stable, user-actionable category."""

    if isinstance(error, subprocess.TimeoutExpired):
        return "timeout"
    if isinstance(error, FileNotFoundError):
        return "missing_binary"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, BackendError):
        return error.failure_class

    text = str(error).lower()
    if any(term in text for term in ("no such file", "not found", "missing")):
        return "missing_binary"
    if any(term in text for term in ("permission denied", "operation not permitted")):
        return "permission_denied"
    if any(term in text for term in ("incompatible architecture", "bad cpu type", "exec format")):
        return "unsupported_architecture"
    if any(
        term in text
        for term in (
            "built for macos",
            "built for mac os",
            "newer than running os",
            "symbol not found",
        )
    ):
        return "unsupported_macos_version"
    if any(term in text for term in ("out of memory", "oom", "metal heap")):
        return "out_of_memory"
    if any(term in text for term in ("metal", "gpu", "backend")):
        return "capability_unavailable"
    return "launch_failure"


def detect_host(
    platform_name: str | None = None,
    machine: str | None = None,
    sysctl_runner: Callable[..., subprocess.CompletedProcess] | None = None,
) -> HostDescription:
    """Detect native architecture and Rosetta without importing PyObjC.

    ``sysctl.proc_translated`` is queried only for an x86_64 process on
    Darwin.  A missing or failing probe is treated as Intel x86_64, never as
    Apple Silicon, which is the safe choice for backend execution.
    """

    platform_name = platform_name or sys.platform
    process_architecture = normalize_architecture(machine or platform_module.machine())
    hardware_architecture = process_architecture
    rosetta = False

    if platform_name == "darwin" and process_architecture == "x86_64":
        runner = sysctl_runner or subprocess.run
        try:
            result = runner(
                ["sysctl", "-in", "sysctl.proc_translated"],
                capture_output=True,
                text=True,
                timeout=1,
                check=False,
            )
            translated = (getattr(result, "stdout", "") or "").strip()
            rosetta = translated == "1"
            if rosetta:
                hardware_architecture = "arm64"
        except (OSError, subprocess.SubprocessError, TypeError):
            pass

    return HostDescription(
        platform=platform_name,
        process_architecture=process_architecture,
        hardware_architecture=hardware_architecture,
        rosetta=rosetta,
    )


def _inspect_binary_architecture(
    path: Path,
    runner: Callable[..., subprocess.CompletedProcess],
) -> str | None:
    """Best-effort architecture inspection using the system ``file`` tool."""

    try:
        result = runner(
            ["file", "-b", str(path)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, TypeError):
        return None
    description = (getattr(result, "stdout", "") or "").lower()
    if "arm64" in description or "aarch64" in description:
        return "arm64"
    if "x86_64" in description or "x86-64" in description:
        return "x86_64"
    if "pe32" in description or "windows" in description:
        return "windows"
    return None


def _version_tuple(value: str) -> tuple[int, ...]:
    """Parse an OS version without making a malformed value fatal."""

    parts = []
    for piece in str(value).split("."):
        if piece.isdigit():
            parts.append(int(piece))
        else:
            break
    return tuple(parts)


def backend_release_number(path: str | Path) -> int | None:
    """Return a llama.cpp ``bNNNNN`` release number when it is knowable.

    Downloaded upstream archives name their directory ``llama-bNNNNN``. A
    packaged backend instead lives under ``Resources/backend/<arch>``, so its
    release is read from Stet's adjacent backend manifest. Unknown/custom
    executables deliberately return ``None`` and are never treated as newer
    than a known official release.
    """

    candidate = Path(path)
    for part in (candidate.name, *(parent.name for parent in candidate.parents)):
        match = _BACKEND_RELEASE_RE.search(part)
        if match:
            return int(match.group(1))

    for parent in candidate.parents:
        manifest = parent / "backend-manifest.json"
        if not manifest.is_file():
            continue
        try:
            value = json.loads(manifest.read_text(encoding="utf-8")).get("release")
            return int(value) if str(value).isdigit() else None
        except (OSError, ValueError, json.JSONDecodeError):
            return None
    return None


def _minimum_macos_version(path: Path, runner: Callable[..., subprocess.CompletedProcess]) -> tuple[int, ...] | None:
    """Read the Mach-O deployment target from the Metal library when present."""

    library = next(iter(sorted(path.parent.glob("libggml-metal*.dylib"))), None)
    if library is None:
        return None
    try:
        result = runner(
            ["otool", "-l", str(library)],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError, TypeError):
        return None
    match = re.search(r"\bminos\s+([0-9.]+)", getattr(result, "stdout", "") or "")
    return _version_tuple(match.group(1)) if match else None


def verify_bundled_backend(
    path: str | Path,
    expected_architecture: str,
    *,
    platform_name: str,
    runner: Callable[..., subprocess.CompletedProcess] | None = None,
    macos_version: str | None = None,
) -> bool:
    """Verify a candidate bundled server before returning it to the runtime."""

    candidate = Path(path)
    if not candidate.is_file():
        return False
    # Existing Windows behavior accepts the executable path based on suffix
    # and existence.  Do not impose POSIX executable-bit rules on Windows hosts
    # where the filesystem does not store POSIX permission bits.
    if sys.platform != "win32" and not os.access(candidate, os.X_OK):
        return False



    command_runner = runner or subprocess.run
    observed = _inspect_binary_architecture(candidate, command_runner)
    if observed is None:
        # A development test double or a platform without `file` can still be
        # verified by the checks above.  Reject explicitly identified foreign
        # binaries, but do not make resolution depend on an optional utility.
        return True
    if platform_name == "darwin" and observed == "windows":
        return False
    if platform_name == "darwin":
        minimum = _minimum_macos_version(candidate, command_runner)
        current = _version_tuple(macos_version if macos_version is not None else platform_module.mac_ver()[0])
        if minimum and current and minimum > current:
            return False
    return observed == expected_architecture


class BackendManager:
    """Resolve bundled servers and apply platform-specific backend policy."""

    def __init__(
        self,
        *,
        root: str | Path | None = None,
        platform_name: str | None = None,
        machine: str | None = None,
        macos_version: str | None = None,
        manifests: Sequence[BackendManifest] = BACKEND_MANIFEST,
        command_runner: Callable[..., subprocess.CompletedProcess] | None = None,
    ):
        self.root = Path(root) if root is not None else None
        self.host = detect_host(platform_name, machine, sysctl_runner=command_runner)
        self.macos_version = macos_version if macos_version is not None else platform_module.mac_ver()[0]
        self.manifests = tuple(manifests)
        self.command_runner = command_runner or subprocess.run

    @property
    def is_macos(self) -> bool:
        return self.host.platform == "darwin"

    def _root(self) -> Path:
        if self.root is not None:
            return self.root
        from stet.constants import SCRIPT_DIR

        return SCRIPT_DIR

    def _roots(self) -> tuple[Path, ...]:
        """Search immutable bundled resources before per-user downloaded state."""

        if self.root is not None:
            return (self.root,)
        from stet.constants import BACKENDS_DIR, BUNDLED_BACKEND_DIR, BUNDLED_RESOURCES_DIR, SCRIPT_DIR

        bundled_native = (
            BUNDLED_RESOURCES_DIR / "backend" / self.host.bundle_architecture
            if BUNDLED_RESOURCES_DIR is not None
            else BUNDLED_BACKEND_DIR
        )
        roots = (bundled_native, BUNDLED_BACKEND_DIR, BACKENDS_DIR, SCRIPT_DIR)
        unique: list[Path] = []
        for root in roots:
            if root not in unique:
                unique.append(root)
        return tuple(unique)

    def resolve_bundled_backend(
        self,
        *,
        legacy_dir: str | Path | None = None,
        executable_name: str | None = None,
    ) -> str:
        """Return a verified server from a manifest-described bundle."""

        entries = manifests_for_host(self.host, self.manifests)
        if not entries:
            return ""
        expected_architecture = self.host.bundle_architecture
        seen: set[Path] = set()
        candidates: list[tuple[int | None, int, Path]] = []
        for entry in entries:
            directories: list[tuple[int, Path]] = []
            for root_index, root in enumerate(self._roots()):
                # Current upstream archives sometimes contain llama-server at
                # their root rather than inside a versioned directory.
                if any((root / name).is_file() for name in entry.executable_names):
                    directories.append((root_index, root))
                for pattern in entry.directory_globs:
                    if pattern == "llama_cpp" and legacy_dir is not None:
                        directory = Path(legacy_dir)
                        if not directory.is_absolute():
                            directory = root / directory
                        directories.append((root_index, directory))
                        continue
                    directories.extend(
                        (root_index, directory)
                        for directory in sorted(root.glob(pattern))
                    )
            for root_index, directory in directories:
                if not directory.is_dir() or directory in seen:
                    continue
                seen.add(directory)
                names = (executable_name,) if executable_name else entry.executable_names
                for name in names:
                    candidate = directory / name
                    if verify_bundled_backend(
                        candidate,
                        expected_architecture,
                        platform_name=self.host.platform,
                        runner=self.command_runner,
                        macos_version=self.macos_version,
                    ):
                        candidates.append((backend_release_number(candidate), root_index, candidate))

        if not candidates:
            return ""
        # A known official release always ranks above an unknown/custom
        # directory; ties prefer immutable bundled resources over user state.
        candidates.sort(
            key=lambda item: (
                item[0] is not None,
                item[0] if item[0] is not None else -1,
                -item[1],
            ),
            reverse=True,
        )
        return str(candidates[0][2])

    def _release_asset(self, payload: dict) -> tuple[dict, str] | None:
        """Select the official macOS binary ZIP and its GitHub SHA-256 digest."""

        if self.host.platform != "darwin":
            return None
        wanted = "macos-arm64" if self.host.bundle_architecture == "arm64" else "macos-x64"
        alternatives = (
            ("macos-arm64", "macos-aarch64", "darwin-arm64")
            if wanted == "macos-arm64"
            else ("macos-x64", "macos-x86_64", "darwin-x64", "darwin-x86_64")
        )
        for asset in payload.get("assets", []):
            name = str(asset.get("name", "")).lower()
            if not name.endswith((".zip", ".tar.gz", ".tgz")) or not any(marker in name for marker in alternatives):
                continue
            digest = str(asset.get("digest", ""))
            if digest.startswith("sha256:") and len(digest) == 71:
                return asset, digest.split(":", 1)[1].lower()
        return None

    def latest_download(
        self,
        *,
        opener: Callable[..., Any] | None = None,
        timeout: float = 15.0,
    ) -> BackendDownload:
        """Resolve a host-appropriate, digest-pinned official release asset.

        GitHub exposes the asset digest in its release API.  Refusing assets
        without that digest is intentional: Stet must not download executable
        code without verifying its integrity.
        """

        if self.host.platform != "darwin":
            raise BackendError("Automatic backend downloads are only configured for macOS", "unsupported_platform")
        current_version = _version_tuple(self.macos_version)
        if not current_version or current_version < (26,):
            filename, url, sha256 = _MACOS_COMPATIBLE_ASSETS[self.host.bundle_architecture]
            from stet.constants import DOWNLOADS_DIR

            return BackendDownload(
                url=url,
                filename=filename,
                sha256=sha256,
                destination=Path(DOWNLOADS_DIR) / filename,
                label=f"llama.cpp ({self.host.bundle_architecture}, macOS compatibility build)",
            )
        request = urllib.request.Request(
            LLAMA_CPP_LATEST_RELEASE_API,
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Stet/1.1"},
        )
        try:
            open_request = opener or urllib.request.urlopen
            with open_request(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise BackendError(f"Could not retrieve the llama.cpp release manifest: {exc}", classify_backend_failure(exc)) from exc
        selected = self._release_asset(payload)
        if selected is None:
            raise BackendError(
                "The latest llama.cpp release has no verified binary for this Mac architecture",
                "missing_release_asset",
            )
        asset, digest = selected
        from stet.constants import DOWNLOADS_DIR

        filename = str(asset["name"])
        return BackendDownload(
            url=str(asset["browser_download_url"]),
            filename=filename,
            sha256=digest,
            destination=Path(DOWNLOADS_DIR) / filename,
            label=f"llama.cpp ({self.host.bundle_architecture})",
        )

    @staticmethod
    def _safe_extract_archive(archive: Path, destination: Path) -> None:
        """Extract a ZIP/tarball without traversal, preserving executable bits."""

        destination.mkdir(parents=True, exist_ok=True)
        base = destination.resolve()
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive, "r") as bundle:
                members = bundle.infolist()
                for member in members:
                    target = (destination / member.filename).resolve()
                    if target != base and base not in target.parents:
                        raise BackendError("Backend archive contains an unsafe path", "unsafe_archive")
                for member in members:
                    bundle.extract(member, destination)
                    mode = member.external_attr >> 16
                    if mode & 0o111:
                        try:
                            (destination / member.filename).chmod(mode & 0o777)
                        except OSError:
                            pass
            return
        with tarfile.open(archive, "r:*") as bundle:
            members = bundle.getmembers()
            for member in members:
                target = (destination / member.name).resolve()
                link_target = (
                    (destination / member.name).parent / member.linkname
                ).resolve() if (member.issym() or member.islnk()) else None
                unsafe_target = target != base and base not in target.parents
                unsafe_link = (
                    link_target is not None
                    and link_target != base
                    and base not in link_target.parents
                )
                if unsafe_target or unsafe_link or member.isdev():
                    raise BackendError("Backend archive contains an unsafe path", "unsafe_archive")
            for member in members:
                bundle.extract(member, destination)
                if member.mode & 0o111:
                    try:
                        (destination / member.name).chmod(member.mode & 0o777)
                    except OSError:
                        pass

    def install_download(
        self,
        download: BackendDownload,
        *,
        opener: Callable[..., Any] | None = None,
        progress: Callable[[int, int], None] | None = None,
    ) -> str:
        """Download, digest-verify, extract and resolve a user backend atomically."""

        from stet.constants import BACKENDS_DIR

        destination = Path(download.destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        temp_archive = destination.with_suffix(destination.suffix + ".part")
        digest = hashlib.sha256()
        try:
            request = urllib.request.Request(download.url, headers={"User-Agent": "Stet/1.1"})
            open_request = opener or urllib.request.urlopen
            with open_request(request, timeout=30) as response, open(temp_archive, "wb") as handle:
                total = int(response.headers.get("Content-Length", 0) or 0)
                written = 0
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if progress:
                        progress(written, total)
            if digest.hexdigest().lower() != download.sha256.lower():
                raise BackendError("Downloaded backend failed SHA-256 verification", "integrity_failure")
            temp_destination = Path(tempfile.mkdtemp(prefix=".stet-backend-", dir=str(BACKENDS_DIR.parent)))
            try:
                self._safe_extract_archive(temp_archive, temp_destination)
                BACKENDS_DIR.mkdir(parents=True, exist_ok=True)
                for child in temp_destination.iterdir():
                    target = BACKENDS_DIR / child.name
                    if target.exists():
                        if target.is_dir():
                            shutil.rmtree(target)
                        else:
                            target.unlink()
                    child.replace(target)
            finally:
                shutil.rmtree(temp_destination, ignore_errors=True)
            destination.unlink(missing_ok=True)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(f"Could not install llama.cpp backend: {exc}", classify_backend_failure(exc)) from exc
        finally:
            temp_archive.unlink(missing_ok=True)
        server = self.resolve_bundled_backend()
        if not server:
            raise BackendError("Downloaded backend did not contain a compatible llama-server", "missing_binary")
        return server

    def probe_capability(self, server_path: str | Path, capability: str = "metal") -> CapabilityProbe:
        """Probe a server with ``--list-devices`` and never raise to callers."""

        path = Path(server_path)
        if not path.is_file():
            return CapabilityProbe(capability, None, "missing_binary", "server is missing")
        try:
            result = self.command_runner(
                [str(path), "--list-devices"],
                capture_output=True,
                text=True,
                # Cold Metal initialization on supported Apple Silicon can
                # take about 16 seconds while its shader library is loaded.
                # A shorter probe would incorrectly force the CPU fallback.
                timeout=20,
                check=False,
            )
        except BaseException as error:  # capability checks must not break startup
            return CapabilityProbe(
                capability,
                None,
                classify_backend_failure(error),
                str(error),
            )
        output = "\n".join(
            value for value in (getattr(result, "stdout", ""), getattr(result, "stderr", "")) if value
        ).lower()
        if getattr(result, "returncode", 1) != 0:
            return CapabilityProbe(
                capability,
                None,
                classify_backend_failure(output or f"exit {getattr(result, 'returncode', 1)}"),
                output.strip(),
            )
        marker = capability.lower()
        # Current llama.cpp macOS builds identify Metal devices as ``MTL0:``,
        # not necessarily with the word "Metal".  Treat that stable device
        # label as a successful Metal probe while keeping the text marker for
        # older releases and diagnostic output.
        metal_device = capability.lower() == "metal" and bool(
            re.search(r"(?m)^\s*mtl\d+\s*:", output)
        )
        if marker in output or metal_device:
            return CapabilityProbe(capability, True, detail=output.strip())
        return CapabilityProbe(capability, False, "capability_unavailable", output.strip())

    def select_backend(
        self,
        mode: str | None,
        configured_gpu_layers: int,
        *,
        force_cpu: bool = False,
        server_path: str | Path | None = None,
    ) -> BackendSelection:
        """Apply ``auto|metal|cpu`` policy and return the CLI GPU layer count."""

        requested = (mode or "auto").strip().lower()
        if requested not in BACKEND_MODES:
            raise BackendError(
                f"Unsupported backend_mode {mode!r}; expected auto, metal, or cpu",
                "invalid_mode",
            )
        if force_cpu or requested == "cpu":
            return BackendSelection(requested, "cpu", 0, "CPU was requested", self.host)
        if not self.is_macos:
            return BackendSelection(
                requested,
                "native",
                configured_gpu_layers,
                "non-macOS policy is unchanged",
                self.host,
            )
        if self.host.architecture == "x86_64":
            return BackendSelection(
                requested,
                "cpu",
                0,
                "Intel macOS is CPU-only",
                self.host,
            )

        if requested == "metal":
            if server_path:
                probe = self.probe_capability(server_path, "metal")
                if probe.supported is False:
                    raise BackendError(
                        "The bundled llama-server does not provide Metal",
                        "capability_unavailable",
                    )
            return BackendSelection(requested, "metal", configured_gpu_layers, "Metal was requested", self.host)

        # On Apple Silicon, auto prefers Metal.  A definitive negative probe
        # is honored; an inconclusive probe remains eligible and lets llama.cpp
        # provide the detailed launch error for failure classification.
        if server_path:
            probe = self.probe_capability(server_path, "metal")
            if probe.supported is False:
                return BackendSelection(requested, "cpu", 0, "Metal capability was not reported", self.host)
        return BackendSelection(requested, "metal", configured_gpu_layers, "Apple Silicon prefers Metal", self.host)
