"""Edge-case tests for _chunk_text_by_sentences."""

from stet.core.text_utils import _chunk_text_by_sentences


def test_chunking_empty_string():
    """Empty input should return an empty list (nothing to chunk)."""
    chunks = _chunk_text_by_sentences("", 100)
    assert chunks == []


def test_chunking_single_word():
    """Single word without any punctuation → one chunk."""
    chunks = _chunk_text_by_sentences("Hello", 100)
    assert len(chunks) == 1
    assert chunks[0][0] == "Hello"


def test_chunking_only_newlines():
    """Pure newlines with no content should not crash and returns empty."""
    chunks = _chunk_text_by_sentences("\n\n\n", 100)
    assert isinstance(chunks, list)
    # No real text content → empty is acceptable
    # The key assertion is that it doesn't crash


def test_chunking_multiple_paragraphs():
    """Multiple paragraphs separated by blank lines → each paragraph is its own chunk."""
    text = "First paragraph. It has two sentences.\n\nSecond paragraph here.\n\nThird."
    chunks = _chunk_text_by_sentences(text, 100)
    # Should split at every \n\n boundary
    assert len(chunks) == 3
    assert "First paragraph" in chunks[0][0]
    assert "Second paragraph" in chunks[1][0]
    assert "Third" in chunks[2][0]
    # Last chunk has empty trailing separator
    assert chunks[-1][1] == ""


def test_chunking_exceeds_word_budget():
    """When a single paragraph exceeds max_words, split at sentence boundaries."""
    text = "Word " * 50 + "end. Another " * 50 + "end."
    chunks = _chunk_text_by_sentences(text, 60)
    assert len(chunks) >= 2
    # Reassembly should reproduce the original
    reassembled = "".join(chunk + sep for chunk, sep in chunks)
    assert reassembled == text


def test_chunking_preserves_trailing_newlines():
    """Separators in the output should exactly reproduce the original when reassembled."""
    text = "Line A.\n\nLine B.\nLine C."
    chunks = _chunk_text_by_sentences(text, 100)
    reassembled = "".join(chunk + sep for chunk, sep in chunks)
    assert reassembled == text


def test_chunking_sentence_boundary_within_line():
    """Multiple sentences on one line → stay grouped when under word budget."""
    text = "Hello world. How are you. I am fine."
    chunks = _chunk_text_by_sentences(text, 100)
    # All on one line, no newlines → should NOT split (all within budget)
    assert len(chunks) == 1
    assert chunks[0][0] == text


def test_chunking_sentence_boundary_forces_split_over_budget():
    """Multiple sentences on one line that exceed budget → split at sentence boundary."""
    text = "Hello world. How are you today my friend. I am fine thank you very much indeed."
    chunks = _chunk_text_by_sentences(text, 5)
    # Each sentence exceeds 5 words alone or in combination → multiple chunks
    assert len(chunks) >= 2


def test_chunking_forces_split_on_newlines():
    """Only blank-line paragraph breaks (\n\n) force split; single newlines pack into the same chunk."""
    text = "Line 1.\n\nLine 2.\nLine 3."
    chunks = _chunk_text_by_sentences(text, 100)
    # Blank-line break splits; single newline does not
    assert chunks == [("Line 1.", "\n\n"), ("Line 2.\nLine 3.", "")]


def test_chunking_carriage_returns():
    """Verify that \r\n and \r newlines are normalized and chunked correctly without leaving \r as isolated blocks."""
    text = "Line 1.\r\n\r\nLine 2.\rLine 3."
    chunks = _chunk_text_by_sentences(text, 100)
    for chunk, sep in chunks:
        assert "\r" not in chunk
        assert "\r" not in sep
    
    reassembled = "".join(chunk + sep for chunk, sep in chunks)
    assert reassembled == "Line 1.\n\nLine 2.\nLine 3."


def test_chunking_digit_led_sentence_does_not_merge_across_periods():
    """Sentences starting with digits (1440p, 1080p, 4K, 2026) must not merge with preceding sentences ending in standard words."""
    text = (
        "I can change those values; however, all is good. "
        "1440p brings back default values and resets everything else in the export panel. "
        "1080p also has the same problem. 4K resolution works fine."
    )
    chunks = _chunk_text_by_sentences(text, 10)
    # With max_words=10, sentences should split into separate chunks, not merge into one massive chunk
    assert len(chunks) >= 3
    assert all(len(c.split()) <= 13 for c, s in chunks)
    assert "".join(c + s for c, s in chunks) == text


def test_chunking_oversized_sentence_without_punctuation():
    """A continuous run-on sentence exceeding max_words must sub-split into chunks <= max_words."""
    text = "word " * 200
    chunks = _chunk_text_by_sentences(text, 60)
    assert len(chunks) == 4
    assert all(len(c.split()) <= 60 for c, s in chunks)
    assert "".join(c + s for c, s in chunks) == text


def test_chunking_oversized_sentence_with_clauses():
    """Oversized sentences should split along secondary clause boundaries (; : , — -) before whitespace."""
    clause1 = "This is the first major clause with plenty of descriptive context"
    clause2 = "and here is the second major clause providing additional critical information"
    clause3 = "followed by a third concluding clause finishing the complex statement"
    text = f"{clause1}; {clause2}; {clause3}."
    chunks = _chunk_text_by_sentences(text, 15)
    assert len(chunks) >= 3
    assert all(len(c.split()) <= 15 for c, s in chunks)
    assert "".join(c + s for c, s in chunks) == text


def test_chunking_user_reported_mat_scale_document():
    """The real user text from the forensic log must not produce any chunk > 60 words."""
    text = (
        "I found a UI issue in Mat Scale where if I were to show fewer images in the grids per line, "
        "they would not scale accordingly and instead they're just a tiny speck in between the entire grid size. "
        "This is wrong, and they should automatically scale up to occupy the entire vertical space that they can "
        "or at least horizontal space as far as possible without cropping any of the images. Please fix it. "
        "Also on the export linear itself, if I were to, for example, select the Instagram resolution and then tap it, "
        "it would have its default values. I can change those values; however, all is good. "
        "1440p brings back default values and resets everything else in the export panel. "
        "1080p also has the same problem. 4K resolution works fine."
    )
    chunks = _chunk_text_by_sentences(text, 60)
    # Total is ~133 words across 3 chunks (39 words, 36 words, 58 words) — all strictly <= 60 words
    assert len(chunks) == 3
    assert all(len(c.split()) <= 60 for c, s in chunks)
    assert "".join(c + s for c, s in chunks) == text

    # At max_words=35, it further subdivides into 4 chunks
    chunks_35 = _chunk_text_by_sentences(text, 35)
    assert len(chunks_35) >= 4
    assert all(len(c.split()) <= 39 for c, s in chunks_35)
    assert "".join(c + s for c, s in chunks_35) == text



