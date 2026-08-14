from stet.core.text_utils import contains_meta_commentary


def test_legitimate_questions_accepted():
    # Normal corrected questions should NOT be flagged as meta commentary
    assert not contains_meta_commentary("What time is it?")
    assert not contains_meta_commentary("Did you go to the store?")
    assert not contains_meta_commentary("How are you today?")
    assert not contains_meta_commentary("Is this sentence grammatically correct?")


def test_conversational_meta_questions_rejected():
    # Assistant clarification meta questions SHOULD be flagged as meta commentary
    assert contains_meta_commentary("Is there anything else I can help you with?")
    assert contains_meta_commentary("Does this look correct?")
    assert contains_meta_commentary("Let me know if you need anything else?")
    assert contains_meta_commentary("Would you like me to make any other changes?")
    assert contains_meta_commentary("Is this what you meant?")

def test_other_conversational_patterns_rejected():
    # Standard conversational preambles and structures
    assert contains_meta_commentary("Sure! Here is the corrected text:")
    assert contains_meta_commentary("I have corrected the spelling mistakes.")
    assert contains_meta_commentary("In my opinion, this version is better.")


def test_chunk_text_by_sentences_list_markers():
    from stet.core.text_utils import _chunk_text_by_sentences

    text = "a. leave whatsapp dormant\nb. proposal seems fine\nc. ok. Let us see."
    # Max words is small to force chunks to split at sentence boundaries
    chunks = _chunk_text_by_sentences(text, max_words=4)

    # The regex splits on ". " and "\n".  The post-merge step re-attaches
    # orphaned list markers ("a.", "b.", "c.") to their content.
    # Expected chunks after merge:
    # 1. "a. leave whatsapp dormant" (split on \n)
    # 2. "b. proposal seems fine"   (split on \n)
    # 3. "c. ok."                    (split on ". " between ok. and Let)
    # 4. "Let us see."              (end)
    assert len(chunks) == 4
    assert chunks[0] == ("a. leave whatsapp dormant", "\n")
    assert chunks[1] == ("b. proposal seems fine", "\n")
    assert chunks[2] == ("c. ok.", " ")
    assert chunks[3] == ("Let us see.", "")


def test_chunk_normal_sentences_still_split():
    """Normal sentences ending with periods must still be split."""
    from stet.core.text_utils import _chunk_text_by_sentences

    text = "The cat sat on the mat. The dog barked loudly."
    chunks = _chunk_text_by_sentences(text, max_words=5)
    assert len(chunks) == 2, f"Expected 2 chunks, got {len(chunks)}: {chunks}"
    assert "cat sat" in chunks[0][0]
    assert "dog barked" in chunks[1][0]


def test_looks_like_prose():
    from stet.core.text_utils import looks_like_prose
    import re

    assert looks_like_prose("This is a normal paragraph of English text that should be accepted as prose.")
    assert looks_like_prose("We will meet tomorrow at 10 AM. Please bring the document.")
    assert looks_like_prose("(Note) this is true.")
    assert looks_like_prose("I came; I saw; I conquered.")
    assert looks_like_prose("[Citation Needed] this is true.")

    # 2. No words
    assert not looks_like_prose("123456 !!! ???")
    assert not looks_like_prose("")

    # 3. Indented bullets / lines
    # Unindented markdown list should return True
    assert looks_like_prose("- item 1\n- item 2\n- item 3")
    # Indented lines (2+ indented) should return False (indented >= 2)
    assert not looks_like_prose("  - indented item 1\n  - indented item 2")

    # 4. Math equations / code characters (sym > 0.04)
    # Inline equation with high symbol ratio
    assert not looks_like_prose("Let x = y + z; if (x > 10) return;")
    # Pure equation E = mc^2 has special symbols: '=' count = 1, len = 8 -> 0.125 > 0.04
    assert not looks_like_prose("E = mc^2")

    # 5. CamelCase / code tokens (avg_caps_mid > 0.05)
    assert not looks_like_prose("Check the value of myVariable and run getUserId method.")

    # 6. Code keywords regex
    assert not looks_like_prose("def my_func():\n    pass")
    assert not looks_like_prose("class StetApp(QMainWindow):\n    pass")
    assert not looks_like_prose("const value = 42;")
    assert not looks_like_prose("import sys\nprint(sys.argv)")

    # 7. Log files / Hex patterns
    assert not looks_like_prose("12:34:56 [INFO] Started server process")
    assert not looks_like_prose("Error occurred at address 0x7fffb88c")

    # 8. Double-call sentinel check pattern simulation
    # Simulate a chunk that contains masked URL sentinels
    chunk_text = "visit ⟦U1⟧ for details on ⟦U2⟧"
    # Replacing sentinels with a space (like in model_manager.py:995)
    editable_text = re.sub(r"⟦U\d+⟧", " ", chunk_text)

    # Both must return True for the pipeline to accept it as prose
    assert looks_like_prose(chunk_text)
    assert looks_like_prose(editable_text)


def test_extract_rewritten_content_candidate_length_ceiling():
    from stet.core.text_utils import _extract_rewritten_sentence, _MAX_REWRITTEN_CANDIDATE_CHARS

    # Candidate <= 3000 chars should be accepted
    text_2500 = "a" * 2500
    assert _extract_rewritten_sentence(text_2500) == text_2500

    text_3000 = "b" * _MAX_REWRITTEN_CANDIDATE_CHARS
    assert _extract_rewritten_sentence(text_3000) == text_3000

    # Candidate > 3000 chars should be rejected (returns None)
    text_3001 = "c" * (_MAX_REWRITTEN_CANDIDATE_CHARS + 1)
    assert _extract_rewritten_sentence(text_3001) is None


# ── strip_meta_commentary original-awareness (2026-08-11 bug) ──────────────
# Root cause: preamble patterns are ^-anchored, so a chunk whose FIRST
# sentence legitimately starts with "The corrected version …" had that real
# content stripped. Fix: when *original* is provided, a pattern that the
# original itself starts with is skipped (it is echoed content, not commentary).


def test_strip_meta_commentary_strips_preamble_without_original():
    """Without an original, preamble-like lead-ins must still be stripped."""
    from stet.core.text_utils import strip_meta_commentary

    assert strip_meta_commentary("The corrected text: Hello world.") == "Hello world."
    assert (
        strip_meta_commentary("Here is the corrected version: Hello world.")
        == "Hello world."
    )
    assert strip_meta_commentary("---\nHello world.") == "Hello world."


def test_strip_meta_commentary_preserves_preamble_when_original_matches():
    """A real lead-in echoed from the original must be preserved."""
    from stet.core.text_utils import strip_meta_commentary

    orig = "The corrected version was released on Friday."
    assert strip_meta_commentary(orig, original=orig) == orig


def test_strip_meta_commentary_still_strips_when_original_differs():
    """Preamble strip must still fire when the original does NOT itself start
    with the phrase (genuine model commentary, not echoed content)."""
    from stet.core.text_utils import strip_meta_commentary

    assert strip_meta_commentary(
        "The corrected version was released on Friday.",
        original="Please fix this sentence.",
    ) == "was released on Friday."


def test_strip_meta_commentary_here_is_corrected_version_matching_original():
    """'Here is the corrected version …' is echoed real content when the
    original starts with it — must be preserved."""
    from stet.core.text_utils import strip_meta_commentary

    orig = (
        "Here is the corrected version of the final quarterly report that the "
        "board reviewed and approved yesterday."
    )
    assert strip_meta_commentary(orig, original=orig) == orig


def test_strip_meta_commentary_hrule_preserved_when_original_matches():
    """A leading '---' divider in the original (real content) must survive."""
    from stet.core.text_utils import strip_meta_commentary

    orig = "---\nActual body text."
    assert strip_meta_commentary(orig, original=orig) == orig


def test_chunk_text_by_sentences_preserves_version_numbers():
    """Gemini's (disproven) claim: chunking truncates 1.2.2 -> 1.2.
    The chunker only splits after [.!?] followed by whitespace, which never
    happens inside a version number — pin that the full value survives."""
    from stet.core.text_utils import _chunk_text_by_sentences

    text = "This will be 1.2.2. Also, there's a tiny commit for the release."
    chunks = _chunk_text_by_sentences(text, max_words=60)
    joined = "".join(c + s for c, s in chunks)
    assert "1.2.2" in joined


def test_is_no_change_declaration_detects_already_correct_declarations():
    """LFM 2.5-style 'the text is fine' meta-declarations must be flagged."""
    from stet.core.text_utils import _is_no_change_declaration

    assert _is_no_change_declaration(
        "The text appears to be already correct. I will not change anything."
    )
    assert _is_no_change_declaration("This text is grammatically correct.")
    assert _is_no_change_declaration("The text is already correct and needs no edits.")
    assert _is_no_change_declaration("No errors found in the provided text.")


def test_is_no_change_declaration_detects_analysis_commentary():
    """Task restatements and analysis lists that are not edits must be flagged."""
    from stet.core.text_utils import _is_no_change_declaration

    assert _is_no_change_declaration(
        "I need to fix errors, improve flow, and make the text clearer."
    )
    assert _is_no_change_declaration(
        "Original text issues: 1. Grammar 2. Word flow. The text reads fine."
    )
    assert _is_no_change_declaration("Let me review it carefully before editing.")


def test_is_no_change_declaration_ignores_real_corrections():
    """Real corrections — even ones mentioning correctness later — must pass."""
    from stet.core.text_utils import _is_no_change_declaration

    assert not _is_no_change_declaration("I will receive the package tomorrow.")
    assert not _is_no_change_declaration("She goes to school every day.")
    assert not _is_no_change_declaration(
        "The report was submitted on time and the team reviewed it."
    )
    assert not _is_no_change_declaration(
        "This is a corrected version of the text. The text is already correct anyway."
    )
    assert not _is_no_change_declaration("")

