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