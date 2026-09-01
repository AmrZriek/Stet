"""CI gate regression guard.

Verifies the Windows CI job is NOT configured with continue-on-error, so
Windows regressions fail the build instead of being silently ignored.
"""

from pathlib import Path


def _ci_workflow_text() -> str:
    root = Path(__file__).resolve().parent.parent
    path = root / ".github" / "workflows" / "ci.yml"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def test_windows_ci_not_continue_on_error():
    """The Windows test-win job must fail on regression.

    continue-on-error: true is forbidden because it lets Windows regressions
    (e.g. clipboard, hotkey, or Qt GC failures) pass silently.
    """
    text = _ci_workflow_text()
    assert "continue-on-error: true" not in text, (
        "Windows CI must fail on regression; "
        "do not use continue-on-error: true in ci.yml."
    )


def test_ci_workflow_exists():
    """The CI workflow file must exist and define the Windows test job."""
    text = _ci_workflow_text()
    assert text, "ci.yml not found"
    assert "test-win" in text, "test-win job missing from ci.yml"
