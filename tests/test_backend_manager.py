"""Tests for manifest-driven backend selection without native binaries."""

import subprocess
import sys
from types import SimpleNamespace

import pytest


from stet.llm.backend_manager import (
    BackendError,
    BackendManager,
    backend_release_number,
    classify_backend_failure,
    detect_host,
    verify_bundled_backend,
)
from stet.llm.backend_manifest import (
    BACKEND_MANIFEST,
    HostDescription,
    manifests_for_host,
    normalize_architecture,
)


def test_manifest_normalizes_architectures_and_filters_hosts():
    assert normalize_architecture("AMD64") == "x86_64"
    assert normalize_architecture("aarch64") == "arm64"
    assert normalize_architecture("") == "unknown"

    host = HostDescription("darwin", "arm64", "arm64")
    entries = manifests_for_host(host)
    assert [entry.name for entry in entries] == ["macos-arm64-metal"]
    assert manifests_for_host(HostDescription("linux", "x86_64", "x86_64"))[0].platform == "linux"


def test_rosetta_detection_prefers_native_arm64_bundle():
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="1", stderr="", returncode=0)

    host = detect_host("darwin", "x86-64", sysctl_runner=runner)

    assert host.process_architecture == "x86_64"
    assert host.hardware_architecture == "arm64"
    assert host.rosetta
    assert host.architecture == "rosetta"
    assert host.bundle_architecture == "arm64"
    assert calls[0][0] == ["sysctl", "-in", "sysctl.proc_translated"]


def test_detect_host_treats_failed_rosetta_probe_as_intel():
    def runner(*_args, **_kwargs):
        raise OSError("sysctl unavailable")

    host = detect_host("darwin", "x86_64", sysctl_runner=runner)
    assert host.hardware_architecture == "x86_64"
    assert not host.rosetta


@pytest.mark.parametrize(
    "error, expected",
    [
        (subprocess.TimeoutExpired("server", 1), "timeout"),
        (FileNotFoundError("missing"), "missing_binary"),
        (PermissionError("permission denied"), "permission_denied"),
        ("bad CPU type", "unsupported_architecture"),
        ("Metal device unavailable", "capability_unavailable"),
    ],
)
def test_classify_backend_failure(error, expected):
    assert classify_backend_failure(error) == expected


def test_verify_backend_rejects_missing_non_executable_and_foreign_binary(tmp_path):
    candidate = tmp_path / "llama-server"
    candidate.touch()

    if sys.platform != "win32":
        no_execute = verify_bundled_backend(
            candidate,
            "arm64",
            platform_name="darwin",
            runner=lambda *_args, **_kwargs: SimpleNamespace(stdout="arm64", returncode=0),
        )
        assert no_execute is False

    candidate.chmod(candidate.stat().st_mode | 0o111)

    foreign = verify_bundled_backend(
        candidate,
        "arm64",
        platform_name="darwin",
        runner=lambda *_args, **_kwargs: SimpleNamespace(stdout="PE32+ Windows", returncode=0),
    )
    assert foreign is False
    assert not verify_bundled_backend(tmp_path / "missing", "arm64", platform_name="darwin")


def test_manager_resolves_verified_macos_bundle(tmp_path):
    bundle = tmp_path / "llama-macos-arm64-metal"
    bundle.mkdir()
    server = bundle / "llama-server"
    server.touch()
    server.chmod(server.stat().st_mode | 0o111)

    def runner(command, **_kwargs):
        if command[0] == "file":
            return SimpleNamespace(stdout="Mach-O 64-bit executable arm64", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    manager = BackendManager(
        root=tmp_path,
        platform_name="darwin",
        machine="arm64",
        command_runner=runner,
    )

    assert manager.is_macos
    assert manager.resolve_bundled_backend() == str(server)


def test_manager_prefers_newest_known_backend_release_numerically(tmp_path):
    """b10068 must win over b10016; a lexical sort gets this wrong."""

    servers = []
    for release in ("b9000", "b10016", "b10068"):
        bundle = tmp_path / f"llama-{release}-bin-macos-arm64"
        bundle.mkdir()
        server = bundle / "llama-server"
        server.touch()
        server.chmod(server.stat().st_mode | 0o111)
        servers.append(server)

    def runner(command, **_kwargs):
        if command[0] == "file":
            return SimpleNamespace(stdout="Mach-O 64-bit executable arm64", returncode=0)
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    manager = BackendManager(
        root=tmp_path,
        platform_name="darwin",
        machine="arm64",
        command_runner=runner,
    )

    assert backend_release_number(servers[1]) == 10016
    assert manager.resolve_bundled_backend() == str(servers[2])


def test_manager_probe_and_select_policy(tmp_path):
    server = tmp_path / "llama-server"
    server.touch()
    server.chmod(server.stat().st_mode | 0o111)

    def runner(command, **_kwargs):
        if command[0] == "file":
            return SimpleNamespace(stdout="arm64", returncode=0)
        return SimpleNamespace(stdout="Metal available", stderr="", returncode=0)

    manager = BackendManager(
        root=tmp_path,
        platform_name="darwin",
        machine="arm64",
        command_runner=runner,
    )

    assert manager.probe_capability(server).supported is True
    assert manager.select_backend("auto", 32, server_path=server).mode == "metal"
    assert manager.select_backend("cpu", 32).gpu_layers == 0
    assert manager.select_backend("metal", 32).gpu_layers == 32

    with pytest.raises(BackendError, match="Unsupported backend_mode"):
        manager.select_backend("vulkan", 32)


def test_manager_recognizes_current_llama_cpp_metal_device_format(tmp_path):
    server = tmp_path / "llama-server"
    server.touch()
    server.chmod(server.stat().st_mode | 0o111)

    def runner(command, **_kwargs):
        if command[0] == "file":
            return SimpleNamespace(stdout="Mach-O 64-bit executable arm64", returncode=0)
        return SimpleNamespace(
            stdout="Available devices:\n  MTL0: Apple M2 (5461 MiB, 5460 MiB free)\n",
            stderr="",
            returncode=0,
        )

    manager = BackendManager(
        root=tmp_path,
        platform_name="darwin",
        machine="arm64",
        command_runner=runner,
    )

    assert manager.probe_capability(server).supported is True
    assert manager.select_backend("metal", 99, server_path=server).mode == "metal"


def test_manager_honors_negative_metal_probe_and_non_macos_policy(tmp_path):
    server = tmp_path / "llama-server"
    server.touch()

    def _negative_runner(*_args, **_kwargs):
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    manager = BackendManager(
        root=tmp_path,
        platform_name="darwin",
        machine="arm64",
        command_runner=_negative_runner,
    )
    selection = manager.select_backend("auto", 32, server_path=server)
    assert (selection.mode, selection.gpu_layers) == ("cpu", 0)

    linux_manager = BackendManager(
        root=tmp_path,
        platform_name="linux",
        machine="x86_64",
        command_runner=_negative_runner,
    )
    selection = linux_manager.select_backend("auto", 17)
    assert (selection.mode, selection.gpu_layers) == ("native", 17)


def test_manager_intel_macos_is_cpu_only():
    manager = BackendManager(platform_name="darwin", machine="x86_64")
    selection = manager.select_backend("auto", 99)
    assert (selection.mode, selection.gpu_layers) == ("cpu", 0)


def test_manifest_entries_are_immutable_and_have_expected_capabilities():
    metal = next(entry for entry in BACKEND_MANIFEST if entry.name == "macos-arm64-metal")
    assert metal.backend == "metal"
    assert "metal" in metal.capabilities
    with pytest.raises(AttributeError):
        metal.name = "changed"
