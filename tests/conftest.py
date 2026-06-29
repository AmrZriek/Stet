"""Shared fixtures and mocks for Stet tests.

The autouse mock_llm_post fixture intercepts LLM API calls and returns
strength-appropriate responses, allowing tests to verify strength routing
without a real model.
"""

import json
import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


STRENGTH_KEYWORDS = {
    "conservative": ["spelling mistakes", "spelling-only", "Spelling Only"],
    "aggressive": ["clearly and smoothly", "expert editor", "Improve clarity"],
}

MOCK_STRENGTH_RESPONSES = {
    "conservative": "<<<START>>>Teh project recieved the update.<<<END>>>",
    "smart_fix": "<<<START>>>The project received the update.<<<END>>>",
    "aggressive": "<<<START>>>Project update received successfully.<<<END>>>",
}


def _detect_strength_from_messages(messages: list) -> str:
    """Detect correction strength from the system message in a chat payload."""
    for msg in messages:
        if msg.get("role") == "system":
            content = msg.get("content", "")
            for strength, keywords in STRENGTH_KEYWORDS.items():
                for kw in keywords:
                    if kw in content:
                        return strength
            return "smart_fix"
    return "smart_fix"


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
                strength, "<<<START>>>Mocked correction<<<END>>>"
            )
            return MockResponse({"choices": [{"message": {"content": content}}]})
        return original_post(self, url, *args, **kwargs)

    monkeypatch.setattr(requests.Session, "post", mock_post)


@pytest.fixture(autouse=True)
def block_model_load(monkeypatch, request):
    """Prevent ModelManager.load_model from spawning llama-server in tests."""
    if "test_gpu_" in request.node.name:
        return
    monkeypatch.setattr(
        "stet.llm.model_manager.ModelManager.load_model",
        lambda *args, **kwargs: None,
    )


@pytest.fixture(autouse=True)
def suppress_first_run_and_update(monkeypatch):
    """Block the 'Welcome to Stet' dialog and auto-update checker.

    StetApp.__init__ fires QTimer.singleShot(800, _show_first_run) when no
    model is configured and QTimer.singleShot(5000, _check_app_update).
    Both can pop up blocking modal dialogs during test runs.
    """
    monkeypatch.setattr("stet.core.app.StetApp._show_first_run", lambda self: None)
    monkeypatch.setattr("stet.core.app.StetApp._check_app_update", lambda self: None)


@pytest.fixture(autouse=True)
def isolate_debug_log(tmp_path, monkeypatch):
    """Redirect debug log to a temp file so tests never pollute app_debug.log."""
    monkeypatch.setattr("stet.core.utils.DEBUG_LOG", tmp_path / "test_debug.log")


@pytest.fixture(autouse=True)
def isolate_config(request, tmp_path, monkeypatch):
    """Redirect config file to a temp file so tests never pollute config.json."""
    if "test_frozen_compat" in request.module.__name__:
        return
    temp_config = tmp_path / "config.json"
    monkeypatch.setattr("stet.constants.CONFIG_FILE", temp_config)
    monkeypatch.setattr("stet.core.config.CONFIG_FILE", temp_config)



@pytest.fixture(autouse=True)
def mock_osd_show(monkeypatch):
    """Stub show_animated on SilentCorrectionOSD to prevent PyQt6 aborts in headless tests."""
    monkeypatch.setattr("stet.ui.osd.SilentCorrectionOSD.show_animated", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def mock_llm_get(monkeypatch):
    """Intercept HTTP GET calls to health endpoints in tests to prevent 180s hangs."""
    original_get = requests.get

    def mock_get(url, *args, **kwargs):
        if any(k in str(url) for k in ("health", "localhost", "127.0.0.1", "MagicMock")):
            return MockResponse({"status": "ok"}, 200)
        return original_get(url, *args, **kwargs)

    monkeypatch.setattr(requests, "get", mock_get)


# TODO: Windows mock GC access violation in tests (Medium)
# Root Cause: PyQt6 C++ destructor ordering during Python GC when StetApp instances are garbage-collected.
# MagicMock objects that mock Qt widgets (e.g., QSystemTrayIcon, QTimer, signal connections) can be destroyed
# in an order that triggers Qt's C++ destructor chain to access already-freed memory.
# Proposed Fix: Add addFinalizer/addCleanup in test fixtures to explicitly call app.deleteLater() + QApplication.processEvents()
# before GC runs to ensure clean C++ widget teardown before Python objects are garbage-collected.


