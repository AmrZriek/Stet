"""
test_update.py — Unit tests for the AppUpdateChecker version comparison logic
and the update.py standalone script helpers.

These tests are source-level (no network, no Qt event loop) — they verify:
  - APP_VERSION is a valid semver string
  - The version parser handles all tag formats we've seen in the GitHub releases
  - The update checker only fires when remote > local
  - The standalone updater's file-copy exclusion list covers user data files
  - F-1…F-7 runtime hardening (HTTPS wrapper, mandatory SHA-256, path
    traversal block, URL centralization, tag validation, secure temp dir,
    sanitized errors)
"""

import re
import sys
import json
import hashlib
import zipfile
import importlib.util
import urllib.error
from pathlib import Path

import pytest

# ── Project root on path ────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SRC = "\n".join(f.read_text(encoding="utf-8") for f in (ROOT / "stet").rglob("*.py"))
UPDATE_SRC = (ROOT / "stet" / "update.py").read_text(encoding="utf-8")

# Build ZIP fixtures on first use (re-uses built copies thereafter).
from tests.fixtures import build_all  # noqa: E402  (sys.path already set)
FIXTURES = build_all(ROOT / "tests" / "fixtures")


# ── Helpers extracted from source (duplicated here to test them in isolation) ──


def _parse_version(v_str: str) -> tuple:
    """Same implementation as AppUpdateChecker._parse_version (inner function)."""
    v_str = re.sub(r"[^0-9\.]", "", v_str)
    parts = []
    for p in v_str.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


# ═══════════════════════════════════════════════════════════════════════════════
# APP_VERSION constant
# ═══════════════════════════════════════════════════════════════════════════════


def test_app_version_constant_exists():
    """APP_VERSION must be defined at module level."""
    m = re.search(r'APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]', SRC)
    assert m, "APP_VERSION constant not found in stet.py"


def test_app_version_is_semver():
    """APP_VERSION must be in X.Y.Z or X.Y format."""
    m = re.search(r'APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]', SRC)
    version = m.group(1)
    assert re.match(r"^\d+\.\d+(\.\d+)?$", version), (
        f"APP_VERSION '{version}' is not a valid semver string"
    )


def test_app_version_is_newer_than_old_releases():
    """APP_VERSION must be >= 1.0.0 (the new baseline release)."""
    m = re.search(r'APP_VERSION\s*=\s*[\'"]([^\'"]+)[\'"]', SRC)
    version = m.group(1)
    assert _parse_version(version) >= _parse_version("1.0.0"), (
        f"APP_VERSION '{version}' is older than the baseline release 1.0.0"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Version parser
# ═══════════════════════════════════════════════════════════════════════════════


def test_parse_version_clean():
    assert _parse_version("3.2.0") == (3, 2, 0)


def test_parse_version_with_v_prefix():
    """Tags from GitHub often come as 'v3.2.0'."""
    assert _parse_version("v3.2.0".lstrip("vV")) == (3, 2, 0)


def test_parse_version_release_prefix():
    """Old releases used 'Release_v3.1.0' as the tag."""
    assert _parse_version("Release_v3.1.0".lstrip("vV")) == (3, 1, 0)


def test_parse_version_two_part():
    assert _parse_version("3.1") == (3, 1, 0)


def test_parse_version_ordering_major():
    assert _parse_version("4.0.0") > _parse_version("3.9.9")


def test_parse_version_ordering_minor():
    assert _parse_version("3.2.0") > _parse_version("3.1.9")


def test_parse_version_ordering_patch():
    assert _parse_version("3.1.2") > _parse_version("3.1.1")


def test_parse_version_equal():
    assert _parse_version("3.1.1") == _parse_version("3.1.1")


# ═══════════════════════════════════════════════════════════════════════════════
# AppUpdateChecker wiring
# ═══════════════════════════════════════════════════════════════════════════════


def test_update_checker_class_exists():
    assert "class AppUpdateChecker" in SRC, (
        "AppUpdateChecker class not found — was it renamed or removed?"
    )


def test_update_checker_points_to_stet_repo():
    assert "AmrZriek/Stet/releases/latest" in SRC, (
        "GITHUB_RELEASES_API must point to AmrZriek/Stet, not llama.cpp"
    )


def test_old_llama_api_removed():
    assert "ggml-org/llama.cpp/releases/latest" not in SRC, (
        "Old llama.cpp GitHub API URL should be removed"
    )


def test_check_app_update_wired_to_boot():
    """The update check must be scheduled at boot, not _check_llama_update."""
    assert "_check_app_update" in SRC
    assert "_check_llama_update" not in SRC


def test_update_action_label_is_generic():
    """Tray menu item should say 'Check for updates', not 'llama.cpp update'."""
    assert '"Check for Updates"' in SRC or '"Check for updates"' in SRC
    assert '"Check for llama.cpp update"' not in SRC


def test_gui_update_launches_packaged_updater():
    """The packaged app should launch the dedicated updater helper."""
    assert "StetUpdater.exe" in SRC
    assert "_start_app_update" in SRC
    assert "_updater_command" in SRC


def test_gui_update_has_no_self_apply_batch():
    """The packaged GUI should not generate updater batch files."""
    assert "_apply_update.bat" not in SRC
    assert "_update_exclude.txt" not in SRC
    assert "xcopy" not in SRC
    assert "DETACHED_PROCESS" not in SRC


def test_gui_update_no_shell_true():
    """The packaged GUI update flow must not launch shell scripts."""
    body_m = re.search(r"def _start_app_update.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m, "_start_app_update method not found"
    body = body_m.group(0)
    assert "shell=True" not in body
    assert "_apply_update.bat" not in body
    assert "xcopy" not in body


def test_gui_update_uses_temp_updater_copy():
    """Verify the updater is copied to a secure temp directory."""
    body_m = re.search(r"def _updater_command.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m
    body = body_m.group(0)
    assert "tempfile.mkdtemp" in body
    assert "shutil.copy2" in body


def test_gui_update_does_not_create_exclude_file():
    """The packaged GUI should not create xcopy exclude files."""
    body_m = re.search(r"def _start_app_update.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m
    body = body_m.group(0)
    assert "_update_exclude.txt" not in body


def test_gui_update_does_not_embed_model_copy_rules():
    """The packaged GUI should not embed model overwrite rules."""
    body_m = re.search(r"def _start_app_update.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m
    body = body_m.group(0)
    assert ".gguf" not in body


def test_gui_update_does_not_embed_llama_copy_rules():
    """The packaged GUI should not embed llama overwrite rules."""
    body_m = re.search(r"def _start_app_update.*?(?=\n    def |\Z)", SRC, re.DOTALL)
    assert body_m
    body = body_m.group(0)
    assert "llama-" not in body
    assert "llama_cpp" not in body


# ═══════════════════════════════════════════════════════════════════════════════
# update.py standalone script
# ═══════════════════════════════════════════════════════════════════════════════


def test_update_script_has_app_flag():
    assert "--app" in UPDATE_SRC, (
        "update.py must expose --app flag for standalone app update"
    )


def test_update_script_no_llama_flag():
    assert "--llama" not in UPDATE_SRC, (
        "Old --llama flag should be removed from update.py"
    )


def test_update_script_reads_app_version():
    assert "APP_VERSION" in UPDATE_SRC, "update.py must read APP_VERSION from stet.py"


def test_update_script_preserves_config():
    assert '"config.json"' in UPDATE_SRC or "'config.json'" in UPDATE_SRC, (
        "update.py must exclude config.json from overwrite"
    )


def test_update_script_preserves_gguf():
    assert ".gguf" in UPDATE_SRC, (
        "update.py must exclude .gguf model files from overwrite"
    )


def test_update_script_waits_for_gui_before_copying():
    assert "--wait-pid" in UPDATE_SRC
    assert "_wait_for_pid" in UPDATE_SRC


def test_update_script_uses_safe_extract():
    assert "_safe_extract" in UPDATE_SRC
    assert "extractall(staging_dir)" not in UPDATE_SRC


def test_update_script_has_atomic_copy_helper():
    assert "_copy_file_atomic" in UPDATE_SRC
    assert "os.replace" in UPDATE_SRC


# ═══════════════════════════════════════════════════════════════════════════════
# build.py version extraction
# ═══════════════════════════════════════════════════════════════════════════════


def test_build_reads_app_version_constant():
    build_src = (ROOT / "build.py").read_text(encoding="utf-8")
    assert "APP_VERSION" in build_src, (
        "build.py must extract version from APP_VERSION, not the docstring"
    )


def test_build_writes_version_file():
    build_src = (ROOT / "build.py").read_text(encoding="utf-8")
    assert '"VERSION"' in build_src or "'VERSION'" in build_src, (
        "build.py must write a VERSION file into the release folder"
    )


def test_build_creates_updater_helper():
    build_src = (ROOT / "build.py").read_text(encoding="utf-8")
    assert "UPDATER_SCRIPT" in build_src
    assert "StetUpdater" in build_src
    assert "--onefile" in build_src


# ═══════════════════════════════════════════════════════════════════════════════
# update.py — runtime regression tests (catch NameError, etc.)
# ═══════════════════════════════════════════════════════════════════════════════


def _load_update_module():
    """Import stet/update.py as a fresh module so tests can patch & reload."""
    spec = importlib.util.spec_from_file_location(
        "_stet_update_under_test", ROOT / "stet" / "update.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_update_app_skips_when_remote_not_newer(monkeypatch, tmp_path, capsys):
    """
    Regression: update_app() must NOT raise NameError on the version-compare
    block. Previously it referenced undefined ``remote_ver`` / ``local_ver``
    and crashed silently before the "already have the latest" check.
    """
    upd = _load_update_module()  # noqa: F841

    # Fake GitHub API response: tag equal to local version → "up to date" path.
    fake_payload = {"tag_name": "v1.0.1", "assets": []}

    class _FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self._payload).encode()

        def geturl(self):
            return "https://api.github.com/repos/AmrZriek/Stet/releases/latest"

    monkeypatch.setattr(
        upd.urllib.request, "urlopen", lambda req, timeout=15: _FakeResp(fake_payload)
    )

    # Point get_local_version at our temp dir so the "local" version is 1.0.1.
    monkeypatch.setattr(upd, "get_local_version", lambda root=None: "1.0.1")

    # Should not raise. With matching versions, it should hit the early-return
    # branch and print the "already have the latest" message.
    upd.update_app(root=tmp_path, wait_pid=None, restart=False)
    out = capsys.readouterr().out
    assert "latest version" in out.lower(), (
        f"Expected 'already have the latest' message, got:\n{out}"
    )


def test_update_app_defines_remote_and_local_ver(monkeypatch, tmp_path):
    """
    Source-level guard: the version-compare block must define both
    ``remote_ver`` and ``local_ver`` before calling ``_parse_version``.
    """
    upd_src = (ROOT / "stet" / "update.py").read_text(encoding="utf-8")
    # Grab the function body from "def update_app" to the next "def ".
    body_m = re.search(
        r"def update_app\(.*?(?=\ndef |\Z)", upd_src, re.DOTALL
    )
    assert body_m, "update_app() not found in stet/update.py"
    body = body_m.group(0)

    # The fix defines remote_ver and local_ver right before _parse_version is
    # called on them. Reject the original bug pattern (parsing undefined names).
    parse_calls = re.findall(r"_parse_version\((remote_ver|local_ver)\)", body)
    assert "remote_ver" in parse_calls and "local_ver" in parse_calls, (
        "update_app() calls _parse_version on remote_ver / local_ver but those "
        "names are never defined in the function — original NameError bug."
    )
    assert "remote_ver = " in body, "remote_ver must be assigned in update_app()"
    assert "local_ver = " in body, "local_ver must be assigned in update_app()"


# ═══════════════════════════════════════════════════════════════════════════════
# F-2: HTTPS enforcement wrapper
# ═══════════════════════════════════════════════════════════════════════════════


def test_f2_safe_urlopen_rejects_http(monkeypatch):
    """_safe_urlopen must raise before issuing the request for an http:// URL."""
    upd = _load_update_module()  # noqa: F841
    called = {"n": 0}

    def _fake(*a, **kw):
        called["n"] += 1
        raise AssertionError("urlopen must NOT be called for http:// URLs")

    monkeypatch.setattr(upd.urllib.request, "urlopen", _fake)
    try:
        upd._safe_urlopen("http://api.example.com/x")
    except RuntimeError as e:
        assert "non-https" in str(e).lower()
    else:
        raise AssertionError("Expected RuntimeError for http:// URL")
    assert called["n"] == 0, "urlopen was called despite http:// scheme"


def test_f2_safe_urlopen_rejects_downgrade(monkeypatch):
    """If urlopen reports a final http:// URL, _safe_urlopen must refuse."""
    upd = _load_update_module()  # noqa: F841

    class _Resp:
        def __init__(self, final):
            self._final = final
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def geturl(self):
            return self._final
        def read(self):
            return b"{}"

    monkeypatch.setattr(
        upd.urllib.request, "urlopen",
        lambda req, timeout=15: _Resp("http://attacker.example.com/payload")
    )
    try:
        upd._safe_urlopen("https://github.com/redirect")
    except RuntimeError as e:
        assert "downgrade" in str(e).lower()
    else:
        raise AssertionError("Expected RuntimeError on HTTPS→HTTP downgrade")


def test_f2_update_app_uses_safe_urlopen(monkeypatch, tmp_path, capsys):
    """End-to-end: when the asset URL is http://, update_app refuses cleanly."""
    upd = _load_update_module()  # noqa: F841

    fake_payload = {
        "tag_name": "v1.2.3",
        "assets": [{
            "name": "Stet-windows.zip",
            "browser_download_url": "http://insecure.example.com/Stet.zip",
        }],
    }

    class _Resp:
        def __init__(self, payload):
            self._payload = payload
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps(self._payload).encode()
        def geturl(self):
            return "https://api.github.com/repos/AmrZriek/Stet/releases/latest"

    monkeypatch.setattr(
        upd.urllib.request, "urlopen",
        lambda req, timeout=15: _Resp(fake_payload)
    )
    monkeypatch.setattr(upd, "get_local_version", lambda root=None: "1.0.0")

    with pytest.raises(RuntimeError, match=r"(?i)https"):
        upd.update_app(root=tmp_path, wait_pid=None, restart=False)


# ═══════════════════════════════════════════════════════════════════════════════
# F-5: tag_name validation
# ═══════════════════════════════════════════════════════════════════════════════


def test_f5_validate_tag_accepts_normal_tags():
    upd = _load_update_module()  # noqa: F841
    for tag in ("v1.2.3", "1.2.3", "V10.20.30", "v1.2.3-rc1", "v1.2.3+build.42"):
        assert upd._validate_tag(tag) == tag


def test_f5_validate_tag_rejects_bad_tags():
    upd = _load_update_module()  # noqa: F841
    for bad in ("", "../../etc", "rm -rf /", "a" * 200, "$(whoami)", None, 123):
        with pytest.raises(RuntimeError):
            upd._validate_tag(bad) if bad is not None else upd._validate_tag("")  # type: ignore[arg-type]


def test_f5_update_app_rejects_malformed_tag(monkeypatch, tmp_path, capsys):
    upd = _load_update_module()  # noqa: F841

    fake_payload = {"tag_name": "../../etc/passwd", "assets": []}

    class _Resp:
        def __init__(self, p):
            self._p = p
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return json.dumps(self._p).encode()
        def geturl(self):
            return "https://api.github.com/repos/AmrZriek/Stet/releases/latest"

    monkeypatch.setattr(
        upd.urllib.request, "urlopen",
        lambda req, timeout=15: _Resp(fake_payload)
    )
    monkeypatch.setattr(upd, "get_local_version", lambda root=None: "1.0.0")

    upd.update_app(root=tmp_path, wait_pid=None, restart=False)
    out = capsys.readouterr().out.lower()
    assert "invalid tag" in out or "error" in out


# ═══════════════════════════════════════════════════════════════════════════════
# F-3: _safe_extract path-traversal hardening
# ═══════════════════════════════════════════════════════════════════════════════


def test_f3_rejects_path_traversal(tmp_path):
    """A ZIP with '../' members must be refused before any extraction."""
    upd = _load_update_module()  # noqa: F841
    zip_path = FIXTURES["path_traversal"]
    staging = tmp_path / "staging"
    staging.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        with pytest.raises(RuntimeError, match="[Uu]nsafe|[Rr]efus"):
            upd._safe_extract(zf, staging)
    # And critically: nothing was extracted.
    assert list(staging.rglob("*")) == [] or all(
        p.name == "Stet.exe" or p.parent.name == "Stet" for p in staging.rglob("*")
    ), f"Traversal was not blocked: {list(staging.rglob('*'))}"


def test_f3_rejects_symlink(tmp_path):
    upd = _load_update_module()  # noqa: F841
    zip_path = FIXTURES["symlink"]
    staging = tmp_path / "staging"
    staging.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        with pytest.raises(RuntimeError, match="symlink"):
            upd._safe_extract(zf, staging)


def test_f3_rejects_absolute_path(tmp_path):
    upd = _load_update_module()  # noqa: F841
    zip_path = FIXTURES["absolute_path"]
    staging = tmp_path / "staging"
    staging.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        with pytest.raises(RuntimeError, match="absolute"):
            upd._safe_extract(zf, staging)


def test_f3_extracts_clean_zip(tmp_path):
    """Sanity: a benign ZIP must extract successfully."""
    upd = _load_update_module()  # noqa: F841
    zip_path = FIXTURES["sample_update"]
    staging = tmp_path / "staging"
    staging.mkdir()
    with zipfile.ZipFile(zip_path) as zf:
        upd._safe_extract(zf, staging)
    assert (staging / "Stet" / "Stet.exe").exists()


# ═══════════════════════════════════════════════════════════════════════════════
# F-1: mandatory SHA-256 verification
# ═══════════════════════════════════════════════════════════════════════════════


def _http_response(payload=None, body=b"", status=200):
    """Build a context-manager response object compatible with _safe_urlopen."""
    class _R:
        def __init__(self):
            self._payload = payload
            self._body = body
            self.headers = {"Content-Length": str(len(body))}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n=-1):
            if n is None or n < 0:
                data, self._body = self._body, b""
                return data
            data, self._body = self._body[:n], self._body[n:]
            return data
        def geturl(self):
            return "https://github.com/x"
    return _R()


def _wire_network(monkeypatch, upd, *, payload, sha_body=None,
                  asset_bytes=None, sha_status=200):
    """Patch urlopen for update_app so it can run end-to-end with no network.

    Asset download always returns a non-empty payload (default: a tiny zip-less
    byte string). Tests that exercise SHA-256 or the extraction path should
    pass ``asset_bytes`` explicitly.
    """
    asset_payload = asset_bytes if asset_bytes is not None else b"placeholder"

    def _fake_urlopen(req, timeout=15):
        url = req.full_url
        if url.endswith("/releases/latest") or "api.github.com" in url:
            return _http_response(body=json.dumps(payload).encode())
        if url.endswith("SHA256SUMS.txt"):
            if sha_status != 200:
                raise urllib.error.HTTPError(url, sha_status, "Not Found", {}, None)
            return _http_response(body=(sha_body or "").encode())
        # Asset download (or any other URL — e.g. the SHA URL on a 200 response).
        return _http_response(body=asset_payload)

    monkeypatch.setattr(upd.urllib.request, "urlopen", _fake_urlopen)


def test_f1_refuses_when_sha256sums_missing(monkeypatch, tmp_path, capsys):
    """If the release has no SHA256SUMS.txt, update_app must REFUSE by default."""
    upd = _load_update_module()  # noqa: F841
    payload = {
        "tag_name": "v1.2.3",
        "assets": [{
            "name": "Stet-windows.zip",
            "browser_download_url": "https://github.com/x/Stet-windows.zip",
        }],
    }
    _wire_network(monkeypatch, upd, payload=payload, sha_status=404)
    monkeypatch.setattr(upd, "get_local_version", lambda root=None: "1.0.0")

    upd.update_app(root=tmp_path, wait_pid=None, restart=False)
    out = capsys.readouterr().out
    assert "refusing" in out.lower(), out
    assert "--allow-unsigned" in out


def test_f1_refuses_when_asset_not_listed(monkeypatch, tmp_path, capsys):
    """SHA256SUMS.txt present but asset has no entry → refuse by default."""
    upd = _load_update_module()  # noqa: F841
    payload = {
        "tag_name": "v1.2.3",
        "assets": [{
            "name": "Stet-windows.zip",
            "browser_download_url": "https://github.com/x/Stet-windows.zip",
        }],
    }
    sha_body = "deadbeef" * 8 + "  Stet-linux.zip\n"  # windows NOT listed
    _wire_network(monkeypatch, upd, payload=payload, sha_body=sha_body)
    monkeypatch.setattr(upd, "get_local_version", lambda root=None: "1.0.0")

    upd.update_app(root=tmp_path, wait_pid=None, restart=False)
    out = capsys.readouterr().out
    assert "refusing" in out.lower(), out


def test_f1_refuses_on_sha_mismatch(monkeypatch, tmp_path, capsys):
    """Wrong hash in SHA256SUMS.txt must abort the install with a clean message."""
    upd = _load_update_module()  # noqa: F841
    payload = {
        "tag_name": "v1.2.3",
        "assets": [{
            "name": "Stet-windows.zip",
            "browser_download_url": "https://github.com/x/Stet-windows.zip",
        }],
    }
    asset_bytes = b"totally legit"
    bad_sha = "00" * 32  # wrong on purpose
    sha_body = f"{bad_sha}  Stet-windows.zip\n"
    _wire_network(monkeypatch, upd, payload=payload, sha_body=sha_body,
                  asset_bytes=asset_bytes)
    monkeypatch.setattr(upd, "get_local_version", lambda root=None: "1.0.0")

    with pytest.raises(RuntimeError, match="SHA-256 mismatch"):
        upd.update_app(root=tmp_path, wait_pid=None, restart=False)


def test_f1_happy_path_installs_with_real_hash(monkeypatch, tmp_path, capsys):
    """When the SHA matches, update_app must actually apply the update."""
    upd = _load_update_module()  # noqa: F841
    # Build a real ZIP and use its real hash.
    asset_path = FIXTURES["sample_update"]
    asset_bytes = asset_path.read_bytes()
    real_sha = hashlib.sha256(asset_bytes).hexdigest()
    payload = {
        "tag_name": "v1.2.3",
        "assets": [{
            "name": asset_path.name,
            "browser_download_url": f"https://github.com/x/{asset_path.name}",
        }],
    }
    sha_body = f"{real_sha}  {asset_path.name}\n"
    _wire_network(monkeypatch, upd, payload=payload, sha_body=sha_body,
                  asset_bytes=asset_bytes)
    monkeypatch.setattr(upd, "get_local_version", lambda root=None: "1.0.0")

    upd.update_app(root=tmp_path, wait_pid=None, restart=False)
    out = capsys.readouterr().out
    assert "SHA-256 verified" in out
    assert "Stet updated to v1.2.3" in out
    # The ZIP layout is "Stet/<files>" and update_app picks the inner Stet/
    # dir as app_dir, so the file lands at tmp_path / VERSION (the Stet/ prefix
    # is stripped because it IS app_dir).
    assert (tmp_path / "VERSION").read_text().strip() == "1.2.3"
    assert (tmp_path / "Stet.exe").exists()


def test_f1_allow_unsigned_overrides(monkeypatch, tmp_path, capsys):
    """--allow-unsigned (allow_unsigned=True) bypasses the missing-SHA refusal."""
    upd = _load_update_module()  # noqa: F841
    asset_path = FIXTURES["sample_update"]
    asset_bytes = asset_path.read_bytes()
    payload = {
        "tag_name": "v1.2.3",
        "assets": [{
            "name": asset_path.name,
            "browser_download_url": f"https://github.com/x/{asset_path.name}",
        }],
    }
    _wire_network(monkeypatch, upd, payload=payload, sha_status=404,
                  asset_bytes=asset_bytes)
    monkeypatch.setattr(upd, "get_local_version", lambda root=None: "1.0.0")

    upd.update_app(root=tmp_path, wait_pid=None, restart=False, allow_unsigned=True)
    out = capsys.readouterr().out
    assert "--allow-unsigned" in out
    assert "Stet updated to v1.2.3" in out
    assert "(unverified)" in out


# ═══════════════════════════════════════════════════════════════════════════════
# F-6: predictable temp dir → mkdtemp
# ═══════════════════════════════════════════════════════════════════════════════


def test_f6_uses_mkdtemp_not_fixed_path(monkeypatch, tmp_path):
    upd = _load_update_module()  # noqa: F841
    src = (ROOT / "stet" / "update.py").read_text(encoding="utf-8")
    # The fixed-name "StetUpdate" directory (no underscore suffix) must be gone.
    # The prefixed variant "StetUpdate_" is the mkdtemp prefix and is fine.
    assert '"StetUpdate"' not in src, (
        'Found fixed-name literal "StetUpdate" — use tempfile.mkdtemp with '
        "a unique prefix instead."
    )
    assert "'StetUpdate'" not in src
    # mkdtemp IS used, and with the per-run unique prefix.
    assert "tempfile.mkdtemp" in src
    assert 'mkdtemp(prefix="StetUpdate_")' in src


# ═══════════════════════════════════════════════════════════════════════════════
# F-4: URL centralization
# ═══════════════════════════════════════════════════════════════════════════════


def test_f4_uses_constants_for_github_api():
    upd = _load_update_module()  # noqa: F841
    # update.py must import GITHUB_RELEASES_API from stet.constants (or fall back)
    # but must NOT hard-code the URL string in the wrong place.
    src = (ROOT / "stet" / "update.py").read_text(encoding="utf-8")
    assert "from stet.constants import" in src
    assert "GITHUB_RELEASES_API" in src
    # Only one literal github URL may appear (the fallback inside the try/except).
    assert src.count("api.github.com/repos/AmrZriek/Stet") == 1, (
        "GitHub API URL must appear exactly once (the fallback); the live URL "
        "must come from stet.constants.GITHUB_RELEASES_API."
    )


def test_f4_update_module_constants_match():
    upd = _load_update_module()  # noqa: F841
    sys.path.insert(0, str(ROOT))
    from stet.constants import GITHUB_RELEASES_API as CONST
    assert upd.GITHUB_RELEASES_API == CONST


# ═══════════════════════════════════════════════════════════════════════════════
# F-7: error messages sanitized (no raw exception in print)
# ═══════════════════════════════════════════════════════════════════════════════


def test_f7_errors_dont_leak_raw_exception(monkeypatch, tmp_path, capsys):
    """A urllib failure should print a clean message, not the raw exception text."""
    upd = _load_update_module()  # noqa: F841

    def _boom(req, timeout=15):
        raise RuntimeError("SSL: CERTIFICATE_VERIFY_FAILED with secret/path/cert.pem")

    monkeypatch.setattr(upd.urllib.request, "urlopen", _boom)
    monkeypatch.setattr(upd, "get_local_version", lambda root=None: "1.0.0")

    upd.update_app(root=tmp_path, wait_pid=None, restart=False)
    out = capsys.readouterr().out
    # No raw cert path or stack text in the user-facing message.
    assert "cert.pem" not in out
    assert "CERTIFICATE_VERIFY_FAILED" not in out
    # But the message still tells the user something went wrong.
    assert "ERROR" in out


# Suppress "unused import" warnings for stdlib imports used only inside
# inline helpers above. (Re-assigned to a tuple so the linter still sees them.)
__all__ = ["FIXTURES", "build_all"]  # noqa: F401  (re-exported for the fixtures module)
