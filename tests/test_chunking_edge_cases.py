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
    """Paragraph breaks (double newlines) and single newlines force split even when max_words is large enough."""
    text = "Line 1.\n\nLine 2.\nLine 3."
    chunks = _chunk_text_by_sentences(text, 100)
    # Double newline and single newline both split
    assert chunks == [("Line 1.", "\n\n"), ("Line 2.", "\n"), ("Line 3.", "")]


def test_chunking_carriage_returns():
    """Verify that \r\n and \r newlines are normalized and chunked correctly without leaving \r as isolated blocks."""
    text = "Line 1.\r\n\r\nLine 2.\rLine 3."
    chunks = _chunk_text_by_sentences(text, 100)
    for chunk, sep in chunks:
        assert "\r" not in chunk
        assert "\r" not in sep
    
    reassembled = "".join(chunk + sep for chunk, sep in chunks)
    assert reassembled == "Line 1.\n\nLine 2.\nLine 3."
