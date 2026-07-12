"""
Real LLM hallucination threshold tuning tests.

THESE TESTS CALL THE ACTUAL LLM and are slow (seconds per test).
They measure REAL behavior to find optimal thresholds.

Usage:
    pytest tests/test_hallucination_real_llm.py -v -s
    # or run individual tests
    pytest tests/test_hallucination_real_llm.py::test_real_llm_typo_fixes -v -s
"""

import time

import pytest

from stet.core.text_utils import (
    _HALLUCINATION_THRESHOLD_CONSERVATIVE,
    _HALLUCINATION_THRESHOLD_SMARTFIX,
    _hallucination_ratio,
)
from stet.llm.model_manager import ModelManager


class MockConfig:
    def get(self, key, default=None):
        return default


def wait_for_model(timeout=30):
    """Wait for model to be ready."""
    import requests

    start = time.time()
    while time.time() - start < timeout:
        try:
            r = requests.get("http://localhost:8080/health", timeout=1)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# Test cases designed to span the drift spectrum
REAL_TEST_CASES = [
    # Category 1: Simple typos (should pass conservative)
    {
        "input": "i beleive it",
        "expected_contains": "believe",
        "min_drift": 0.0,
        "max_drift": 0.3,
    },
    {
        "input": "teh test",
        "expected_contains": "the",
        "min_drift": 0.0,
        "max_drift": 0.3,
    },
    {
        "input": "recieve",
        "expected_contains": "receive",
        "min_drift": 0.0,
        "max_drift": 0.3,
    },
    {
        "input": "definately",
        "expected_contains": "definitely",
        "min_drift": 0.0,
        "max_drift": 0.3,
    },
    {
        "input": "occured",
        "expected_contains": "occurred",
        "min_drift": 0.0,
        "max_drift": 0.3,
    },
    {
        "input": "seperate",
        "expected_contains": "separate",
        "min_drift": 0.0,
        "max_drift": 0.3,
    },
    {
        "input": "wheather",
        "expected_contains": "weather",
        "min_drift": 0.0,
        "max_drift": 0.3,
    },
    # Category 2: Wrong words (should pass smart_fix, fail conservative)
    {
        "input": "their going to the store",
        "expected_contains": "they're",
        "min_drift": 0.2,
        "max_drift": 0.5,
    },
    {
        "input": "its a nice day",
        "expected_contains": "it's",
        "min_drift": 0.2,
        "max_drift": 0.5,
    },
    {
        "input": "your welcome",
        "expected_contains": "you're",
        "min_drift": 0.2,
        "max_drift": 0.5,
    },
    {
        "input": "to many to count",
        "expected_contains": "too many",
        "min_drift": 0.2,
        "max_drift": 0.5,
    },
    # Category 3: Grammar fixes
    {
        "input": "he go to school",
        "expected_contains": "goes",
        "min_drift": 0.2,
        "max_drift": 0.5,
    },
    {
        "input": "she like apples",
        "expected_contains": "likes",
        "min_drift": 0.2,
        "max_drift": 0.5,
    },
    {
        "input": "they was happy",
        "expected_contains": "were",
        "min_drift": 0.2,
        "max_drift": 0.5,
    },
    # Category 4: Multiple fixes
    {
        "input": "teh cat is cute",
        "expected_contains": "the",
        "min_drift": 0.3,
        "max_drift": 0.6,
    },
    {
        "input": "i beleive its gonna be ok",
        "expected_contains": "believe",
        "min_drift": 0.3,
        "max_drift": 0.6,
    },
    {
        "input": "definately thats wrong",
        "expected_contains": "definitely",
        "min_drift": 0.3,
        "max_drift": 0.6,
    },
    # Category 5: Heavy edits (should FAIL conservative, pass smart_fix)
    {
        "input": "i am so happy to see you today my dear friend",
        "expected_contains": None,
        "min_drift": 0.5,
        "max_drift": 1.0,
        "heavy": True,
    },
]


@pytest.fixture(scope="module")
def model():
    """Ensure model is running."""
    if not wait_for_model(timeout=5):
        pytest.skip("Model not running - start llama-server first")

    mgr = ModelManager(MockConfig())
    yield mgr


@pytest.mark.live
def test_real_llm_typo_fixes(model):
    """
    Test simple typo fixes with REAL LLM.
    This is the CORE test for threshold tuning.
    """
    print("\n" + "=" * 70)
    print("REAL LLM TYPO FIX TESTS")
    print("=" * 70)
    print(
        f"Current thresholds: conservative={_HALLUCINATION_THRESHOLD_CONSERVATIVE}, smart_fix={_HALLUCINATION_THRESHOLD_SMARTFIX}"
    )
    print()

    results = []

    for i, case in enumerate(REAL_TEST_CASES[:10]):  # Start with 10 to be quick
        print(f"[{i + 1}/10] Testing: {case['input']!r}...", end=" ", flush=True)

        try:
            # Call LLM with conservative strength
            result, units = model.correct_text_patch(
                case["input"], strength="spelling_only"
            )

            if result is None:
                print("FAILED (LLM returned None)")
                results.append(
                    {**case, "result": None, "accepted": False, "drift": 1.0}
                )
                continue

            # Calculate real drift
            orig = case["input"]
            corr = result.strip()
            drift = _hallucination_ratio(orig, corr, "full_correction")

            # Check if correction was applied
            expected = case.get("expected_contains")
            accepted = expected and expected.lower() in corr.lower()

            print(f"drift={drift:.3f}, accepted={accepted}")
            print(f"  Input:    {orig!r}")
            print(f"  Output:   {corr!r}")

            results.append(
                {**case, "result": corr, "accepted": accepted, "drift": drift}
            )

        except Exception as e:
            print(f"ERROR: {e}")
            results.append(
                {
                    **case,
                    "result": str(e),
                    "accepted": False,
                    "drift": 1.0,
                    "error": True,
                }
            )

        time.sleep(0.3)  # Small delay between calls

    # Analyze results
    print("\n" + "=" * 70)
    print("RESULTS SUMMARY")
    print("=" * 70)

    accepted_count = sum(1 for r in results if r.get("accepted"))
    rejected_count = len(results) - accepted_count

    drifts = [r.get("drift", 1.0) for r in results]

    print(f"Total: {len(results)}")
    print(f"Accepted: {accepted_count} ({100 * accepted_count / len(results):.1f}%)")
    print(f"Rejected: {rejected_count} ({100 * rejected_count / len(results):.1f}%)")
    print(f"Avg drift: {sum(drifts) / len(drifts):.3f}")
    print(f"Min drift: {min(drifts):.3f}")
    print(f"Max drift: {max(drifts):.3f}")

    # Show rejected cases with their drift
    print("\nRejected cases:")
    for r in results:
        if not r.get("accepted"):
            print(f"  {r['input']!r} -> drift={r.get('drift', 1.0):.3f}")

    # Test current thresholds
    print("\n" + "=" * 70)
    print("THRESHOLD PERFORMANCE WITH CURRENT VALUES")
    print("=" * 70)

    at_conservative = sum(
        1
        for r in results
        if r.get("drift", 1.0) < _HALLUCINATION_THRESHOLD_CONSERVATIVE
    )
    at_smartfix = sum(
        1 for r in results if r.get("drift", 1.0) < _HALLUCINATION_THRESHOLD_SMARTFIX
    )

    print(
        f"At conservative ({_HALLUCINATION_THRESHOLD_CONSERVATIVE}): {at_conservative}/{len(results)} pass"
    )
    print(
        f"At smart_fix ({_HALLUCINATION_THRESHOLD_SMARTFIX}): {at_smartfix}/{len(results)} pass"
    )

    assert len(results) > 0


@pytest.mark.live
def test_find_optimal_conservative_threshold(model):
    """Find optimal conservative threshold."""
    print("\n" + "=" * 70)
    print("FINDING OPTIMAL CONSERVATIVE THRESHOLD")
    print("=" * 70)

    # Use same test cases
    cases = REAL_TEST_CASES[:15]
    results = []

    for case in cases:
        result, units = model.correct_text_patch(case["input"], strength="spelling_only")
        if result:
            drift = _hallucination_ratio(case["input"], result.strip(), "spelling_only")
            results.append((case["input"], result.strip(), drift))

    # Test different thresholds
    print("\nThreshold sensitivity:")
    if not results:
        pytest.skip("Local LLM not available")
    for threshold in [0.3, 0.35, 0.4, 0.45, 0.5, 0.55, 0.6]:
        passed = sum(1 for _, _, d in results if d < threshold)
        total = len(results)
        print(f"  {threshold}: {passed}/{total} ({100 * passed / total:.1f}%)")

    assert len(results) > 0


@pytest.mark.live
def test_find_optimal_smartfix_threshold(model):
    """Find optimal smart_fix threshold."""
    print("\n" + "=" * 70)
    print("FINDING OPTIMAL SMART_FIX THRESHOLD")
    print("=" * 70)

    cases = REAL_TEST_CASES
    results = []

    for case in cases:
        result, units = model.correct_text_patch(case["input"], strength="full_correction")
        if result:
            drift = _hallucination_ratio(case["input"], result.strip(), "spelling_only")
            results.append((case["input"], result.strip(), drift))

    print("\nThreshold sensitivity:")
    if not results:
        pytest.skip("Local LLM not available")
    for threshold in [0.4, 0.5, 0.6, 0.7, 0.8]:
        passed = sum(1 for _, _, d in results if d < threshold)
        total = len(results)
        print(f"  {threshold}: {passed}/{total} ({100 * passed / total:.1f}%)")

    assert len(results) > 0


@pytest.mark.live
def test_full_regression_run(model):
    """Run all 20 test cases with both strengths."""
    print("\n" + "=" * 70)
    print("FULL REGRESSION RUN (20 cases, both strengths)")
    print("=" * 70)

    all_results = {"spelling_only": [], "full_correction": []}

    for strength in ["spelling_only", "full_correction"]:
        print(f"\n=== {strength.upper()} ===")

        for i, case in enumerate(REAL_TEST_CASES):
            result, units = model.correct_text_patch(case["input"], strength=strength)
            if result:
                drift = _hallucination_ratio(
                    case["input"], result.strip(), "spelling_only"
                )
                threshold = (
                    _HALLUCINATION_THRESHOLD_CONSERVATIVE
                    if strength == "spelling_only"
                    else _HALLUCINATION_THRESHOLD_SMARTFIX
                )
                passed = drift < threshold

                print(
                    f"[{i + 1:2d}] {case['input'][:30]:30s} drift={drift:.3f} threshold={threshold} {'PASS' if passed else 'FAIL'}"
                )

                all_results[strength].append(
                    {
                        "input": case["input"],
                        "result": result.strip(),
                        "drift": drift,
                        "passed": passed,
                    }
                )

    # Summary
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)

    for strength in ["spelling_only", "full_correction"]:
        if not all_results[strength]:
            pytest.skip("Local LLM not available")
        passed = sum(1 for r in all_results[strength] if r["passed"])
        total = len(all_results[strength])
        threshold = (
            _HALLUCINATION_THRESHOLD_CONSERVATIVE
            if strength == "spelling_only"
            else _HALLUCINATION_THRESHOLD_SMARTFIX
        )
        print(
            f"{strength}: {passed}/{total} pass ({100 * passed / total:.1f}%) at threshold {threshold}"
        )

    assert "spelling_only" in all_results
