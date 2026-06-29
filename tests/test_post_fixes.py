import inspect

from stet.core.text_utils import _apply_post_fixes


def test_post_fixes_conservative_mode():
    # It takes "dont" and leaves it as "dont"
    assert _apply_post_fixes("dont", strength="conservative") == "dont"


def test_post_fixes_smart_fix_mode():
    # Capitalization post-fix was REMOVED (2026-06-23): the LLM now decides
    # case, so _apply_post_fixes no longer force-capitalizes the first letter
    # or standalone 'i'. It must pass text through with case intact instead of
    # silently re-casing (which corrupted URLs/emails at sentence boundaries).
    assert _apply_post_fixes("i am happy", strength="smart_fix") == "i am happy"
    assert _apply_post_fixes("you and i", strength="smart_fix") == "you and i"
    # Contractions are still fixed (case is preserved on the rest of the word).
    assert _apply_post_fixes("dont", strength="smart_fix") == "don't"
    # A lowercase URL/email start must NOT be force-capitalized.
    assert _apply_post_fixes("https://example.com is here", strength="smart_fix") == "https://example.com is here"
    assert _apply_post_fixes("john.doe@company.com sent it", strength="smart_fix") == "john.doe@company.com sent it"


def test_post_fixes_mode_awareness():
    sig = inspect.signature(_apply_post_fixes)
    params = list(sig.parameters.keys())
    assert "strength" in params
    assert params == ["text", "original", "strength"]
