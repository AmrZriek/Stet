"""Shared fixtures and mocks for Stet tests.

The autouse mock_llm_post fixture intercepts LLM API calls and returns
strength-appropriate responses, allowing tests to verify strength routing
without a real model.
"""

import json
import os
import shutil
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

# The macOS Cocoa platform plug-in can terminate a headless pytest process
# when a unit test shows a transient window.  UI behavior is still exercised
# through Qt, just with the deterministic offscreen platform instead.
if sys.platform == "darwin":
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


STRENGTH_KEYWORDS = {
    "spelling_only": ["Correct every clear spelling", "spelling or typing error", "Spelling Only"],
    "full_correction": ["Correct the text completely", "full correction", "Full Correction"],
    "rewrite_polish": ["Rewrite and polish", "rewrite and polish", "Rewrite & Polish"],
}

MOCK_STRENGTH_RESPONSES = {
    "spelling_only": "Teh project recieved the update.",
    "full_correction": "The project received the update.",
    "rewrite_polish": "Project update received successfully.",
}


def _detect_strength_from_messages(messages: list) -> str:
    """Detect correction strength from the system message in a chat payload."""
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "").lower()
            for strength, keywords in STRENGTH_KEYWORDS.items():
                for kw in keywords:
                    if kw.lower() in content:
                        return strength
            return "full_correction"
    return "full_correction"


class MockResponse:
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code
        self.ok = status_code == 200
        self.text = (
            json.dumps(json_data) if isinstance(json_data, dict) else str(json_data)
        )

    def json(self):
        return self.json_data

    def raise_for_status(self):
        if self.status_code != 200:
            raise requests.exceptions.HTTPError(f"Status: {self.status_code}")


@pytest.fixture(autouse=True)
def mock_llm_post(monkeypatch):
    """Intercept HTTP calls and return strength-appropriate mock responses.

    Inspects the request payload to detect correction strength from the
    system prompt, then returns a mock output that reflects that strength.
    Tests that need specific mock behavior can patch requests.Session.post
    locally within their function scope; the autouse fixture's mock is
    restored outside the local patch context.
    """
    original_post = requests.Session.post

    def mock_post(self, url, *args, **kwargs):
        if "v1/chat/completions" in url or "localhost" in url:
            json_data = kwargs.get("json", {})
            messages = json_data.get("messages", [])
            strength = _detect_strength_from_messages(messages)
            content = MOCK_STRENGTH_RESPONSES.get(
                strength, "Mocked correction"
            )
            return MockResponse({"choices": [{"message": {"content": content}}]})
        if "/apply-template" in url:
            # Benign default: a clean rendered prompt with no think open-tag.
            # Keeps post-load sanitized-template validation from ever hitting
            # a real socket; tests that assert on the flag patch
            # ModelManager._get_session locally instead.
            return MockResponse({"prompt": "<|im_start|>assistant\n"})
        return original_post(self, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "post", mock_post)


@pytest.fixture(autouse=True)
def block_model_load(monkeypatch, request):
    """Prevent ModelManager.load_model from spawning llama-server in tests."""
    # Bypass for launch-command tests: the GPU-launch-command tests and the
    # TestServerLaunchCommand class exercise the real load_model builder with
    # subprocess.Popen patched out. nodeid carries the class, so check it
    # (node.name alone is just the function name).
    if (
        "test_gpu_" in request.node.name
        or "TestServerLaunchCommand" in request.node.nodeid
        or "TestMtpLoadingAndFallback" in request.node.nodeid
        or "TestAuditEnhancements" in request.node.nodeid
        or "TestGpuOomFallback" in request.node.nodeid
    ):
        return
    monkeypatch.setattr(
        "stet.llm.model_manager.ModelManager.load_model",
        lambda *args, **kwargs: None,
    )


@pytest.fixture(autouse=True)
def suppress_first_run_and_update(monkeypatch):
    """Block the 'Welcome to Stet' dialog, auto-update checker, and first-run downloads check.

    StetApp.__init__ fires QTimer.singleShot(800, _show_first_run) when no
    model is configured, QTimer.singleShot(5000, _check_app_update), and
    QTimer.singleShot(100, _check_first_run_downloads).
    All can pop up blocking modal dialogs during test runs.
    """
    monkeypatch.setattr("stet.core.app.StetApp._show_first_run", lambda self: None)
    monkeypatch.setattr("stet.core.app.StetApp._check_app_update", lambda self: None)
    monkeypatch.setattr("stet.core.app.StetApp._check_first_run_downloads", lambda self: None)


@pytest.fixture(autouse=True)
def suppress_background_welcome_poll(request, monkeypatch):
    """Prevent unrelated StetApp test instances from opening welcome UI later.

    The production app polls for an external welcome flag every two seconds.
    A unit-test instance can outlive its test through Qt ownership, so its
    timer otherwise opens a real AppKit window during a later test.  The
    dedicated welcome-window module exercises the poll explicitly.
    """
    if request.module.__name__.endswith("test_welcome_window"):
        return
    monkeypatch.setattr("stet.core.app.StetApp._check_welcome_flag", lambda self: None)


@pytest.fixture(autouse=True)
def isolate_debug_log(tmp_path, monkeypatch):
    """Redirect debug log to a temp file so tests never pollute app_debug.log."""
    monkeypatch.setattr("stet.core.utils.DEBUG_LOG", tmp_path / "test_debug.log")


@pytest.fixture(autouse=True)
def isolate_config(request, tmp_path, monkeypatch):
    """Redirect config file & APP_DATA_DIR to a temp file so tests never pollute root."""
    if "test_frozen_compat" in request.module.__name__:
        return
    temp_config = tmp_path / "config.json"
    temp_app_data = tmp_path / "app_data"
    temp_app_data.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("stet.constants.CONFIG_FILE", temp_config)
    monkeypatch.setattr("stet.core.config.CONFIG_FILE", temp_config)
    monkeypatch.setattr("stet.constants.APP_DATA_DIR", temp_app_data)
    monkeypatch.setattr("stet.core.history.APP_DATA_DIR", temp_app_data)
    monkeypatch.setattr("stet.core.config.MODELS_DIR", tmp_path / "Models")


@pytest.fixture(autouse=True)
def mock_osd_show(monkeypatch):
    """Stub show_animated on SilentCorrectionOSD to prevent PyQt6 aborts in headless tests."""
    monkeypatch.setattr("stet.ui.osd.SilentCorrectionOSD.show_animated", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def mock_macos_system_tray(monkeypatch):
    """Keep unit tests from invoking macOS notification-center services.

    Qt's real QSystemTrayIcon reaches Notification Center even when no user
    session is available (as in the test runner).  That can terminate Python
    without a pytest failure report.  Production builds still use the genuine
    system menu-bar icon; this is test-only isolation.
    """
    if sys.platform != "darwin":
        return
    from PyQt6.QtWidgets import QSystemTrayIcon as RealSystemTrayIcon
    import stet.core.app as app_module

    tray_class = MagicMock()
    tray_class.MessageIcon = RealSystemTrayIcon.MessageIcon
    tray_class.ActivationReason = RealSystemTrayIcon.ActivationReason
    tray_class.isSystemTrayAvailable.return_value = False
    monkeypatch.setattr(app_module, "QSystemTrayIcon", tray_class)


@pytest.fixture(autouse=True)
def mock_llm_get(monkeypatch):
    """Intercept HTTP GET calls to health endpoints in tests to prevent 180s hangs."""
    original_get = requests.get

    def mock_get(url, *args, **kwargs):
        if any(k in str(url) for k in ("health", "localhost", "127.0.0.1", "MagicMock")):
            return MockResponse({"status": "ok"}, 200)
        return original_get(url, *args, **kwargs)

    monkeypatch.setattr(requests, "get", mock_get)


@pytest.fixture(scope="session", autouse=True)
def artifact_hygiene(tmp_path_factory):
    """Single wiped scratch dir per run; repo root stays clean.

    tests/.artifacts/ is the ONLY shared scratch location tests may use.
    It is deleted and recreated at session start so stale files from a
    previous run can never be mistaken for fresh output. Also removes the
    legacy root litter dirs (artifacts/, out/, .pytest_cache/, .ruff_cache/)
    and data files (history.jsonl, server_log.txt) produced by older tests.
    """
    scratch = ROOT / "tests" / ".artifacts"
    for legacy in (
        ROOT / "artifacts",
        ROOT / "out",
        ROOT / ".pytest_cache",
        ROOT / ".ruff_cache",
        ROOT / "history.jsonl",
        ROOT / "server_log.txt",
        scratch,
    ):
        if legacy.exists():
            try:
                if legacy.is_dir():
                    shutil.rmtree(legacy, ignore_errors=True)
                else:
                    legacy.unlink(missing_ok=True)
            except OSError:
                pass

    scratch.mkdir(parents=True, exist_ok=True)
    yield scratch

    # Clean up stray root files created during run
    for legacy in (ROOT / ".pytest_cache", ROOT / ".ruff_cache", ROOT / "artifacts", ROOT / "out"):
        if legacy.exists():
            try:
                shutil.rmtree(legacy, ignore_errors=True)
            except OSError:
                pass
    for stray_file in (ROOT / "history.jsonl", ROOT / "server_log.txt"):
        if stray_file.exists():
            try:
                stray_file.unlink(missing_ok=True)
            except OSError:
                pass


@pytest.fixture(autouse=True)
def _cleanup_qt_events_and_gc():
    yield
    import gc
    gc.collect()
    try:
        from PyQt6.QtWidgets import QApplication
        qapp = QApplication.instance()
        if qapp is not None:
            qapp.processEvents()
    except Exception:
        pass
