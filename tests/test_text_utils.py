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
