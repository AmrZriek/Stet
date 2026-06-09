import inspect

from stet.core.text_utils import _apply_post_fixes


def test_post_fixes_conservative_mode():
    # It takes "dont" and leaves it as "dont"
    assert _apply_post_fixes("dont", strength="conservative") == "dont"


def test_post_fixes_smart_fix_mode():
    # Capitalizes the first letter
    assert _apply_post_fixes("i am happy", strength="smart_fix") == "I am happy"
    # Converts standalone 'i' to 'I'
    assert _apply_post_fixes("you and i", strength="smart_fix") == "You and I"


def test_post_fixes_mode_awareness():
    sig = inspect.signature(_apply_post_fixes)
    params = list(sig.parameters.keys())
    assert "strength" in params
    assert params == ["text", "original", "strength"]
