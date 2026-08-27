"""Empirical probe: where can the chunked correction path lose text?

Tests three hypotheses:
  H1: _remove_introduced_duplicate_sentences is NOT a no-op when corrected == original
      (pure bug: dedup fires on verbatim text).
  H2: _remove_introduced_duplicate_sentences deletes sentences that ARE present in the
      original (false-positive dedup after a faithful correction).
  H3: The per-chunk word-ratio sanity gate (min_word_ratio=0.75 after the 1.2.2 change)
      accepts chunks where a sentence was dropped, i.e. the pipeline silently emits
      a document missing real content.
"""
import re
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stet.core.text_utils import (
    _chunk_text_by_sentences,
    _remove_introduced_duplicate_sentences,
    _post_splice_sanity,
)

random.seed(1234)

# ── H1: dedup must be a no-op when corrected == original ───────────────────
def sentence_bodies(text):
    return re.findall(r"[^.!?\n]+[.!?]", text)

h1_fails = 0
for _ in range(20000):
    n = random.randint(2, 8)
    sents = [random.choice([
        "The cat sat on the mat.",
        "The dog ran fast.",
        "Birds fly south in winter.",
        "Prices rose again today.",
        "The meeting starts at noon.",
        "We need more coffee.",
        "It rained all morning.",
        "The plan is simple.",
    ]) for _ in range(n)]
    text = " ".join(sents)
    out = _remove_introduced_duplicate_sentences(text, text)
    if out != text:
        h1_fails += 1
        if h1_fails <= 3:
            print(f"[H1] NO-OP VIOLATION: {text!r}\n     -> {out!r}")

# H1b: with punctuation variants / trailing separators
for text in [
    "Stop. Stop that now.\n\nGo. Go now.\n",
    "First.\n\nFirst.\n\nSecond.",
    "One.  One.  Two.",
]:
    out = _remove_introduced_duplicate_sentences(text, text)
    if out != text:
        h1_fails += 1
        print(f"[H1b] NO-OP VIOLATION: {text!r}\n     -> {out!r}")

print(f"H1 failures (dedup on verbatim): {h1_fails}")

# ── H2: dedup deletes sentences faithfully present in the original ─────────
# Model faithfully fixes a typo in chunk; dedup then removes a sentence that
# exists in the original because segmentation/adjacency differs.
h2_fails = 0
orig = ("The cat sat on the mat. The dog ran fast. "
        "The cat sat on the mat. The dog ran fast.")
# original has two non-adjacent copies of each -> runs of 1 each, allowed=1
corrected = orig.replace("ran", "run")  # faithful-ish correction, order preserved
out = _remove_introduced_duplicate_sentences(corrected, orig)
if out != corrected:
    h2_fails += 1
    print(f"[H2] non-adjacent-dup deleted: orig={orig!r}\n     corrected={corrected!r}\n     out={out!r}")

# Realistic: chunk boundary re-orders nothing, but model normalizes casing.
orig2 = "Hello. hello. hello. World."
corrected2 = "Hello. hello. hello. World."
out2 = _remove_introduced_duplicate_sentences(corrected2, orig2)
print(f"[H2b] case-normalized adjacent runs: out2={out2!r} (orig={orig2!r})")
if out2 != corrected2:
    h2_fails += 1

print(f"H2 failures: {h2_fails}")

# ── H3: per-chunk word-ratio sanity lets a dropped sentence through ────────
# A realistic chunk: 4 sentences. Sloppy model drops the LAST sentence.
chunk = "The committee approved the budget yesterday. Funding will begin next month. "
chunk += "Staff were notified this morning. The rollout starts in January."
sloppy = chunk.split(". ")[0] + ". " + chunk.split(". ")[1] + ". " + chunk.split(". ")[2] + "."
for name, min_r in [("old 0.85", 0.85), ("new 0.75", 0.75), ("default 0.5", 0.5)]:
    passes = _post_splice_sanity(chunk, sloppy, min_ratio=min_r, max_ratio=1.25)
    lost = "the rollout starts in january." not in sloppy.lower()
    print(f"[H3] {name}: sanity={passes} sentence-dropped={lost} "
          f"ratio={len(sloppy.split())}/{len(chunk.split())}={len(sloppy.split())/len(chunk.split()):.2f}")

# ── End-to-end chunked simulation: model drops sentence in ONE chunk ────────
def sim_chunked(text, max_words, min_ratio):
    """Replicate pipeline: chunk -> per-chunk 'model' -> sanity -> join -> dedup.
    The fake model fixes a typo but DROPS the last sentence of chunk 2."""
    chunks = _chunk_text_by_sentences(text, max_words)
    parts = []
    for idx, (ct, sep) in enumerate(chunks):
        if idx == 1:  # model sloppily drops last sentence of this chunk
            sents = re.findall(r"[^.!?\n]+[.!?]", ct)
            if len(sents) >= 2:
                head = sents[0]
                rest = " ".join(sents[1:])
                corr = head + " " + rest if rest else head
            else:
                corr = ct
        else:
            corr = ct
        if not _post_splice_sanity(ct, corr, min_ratio=min_ratio, max_ratio=1.25):
            corr = ct  # reject -> keep original
        parts.append((corr, sep))
    joined = "".join(p + s for p, s in parts)
    return joined, chunks

long_text = (
    "The committee approved the budget yesterday. Funding will begin next month.\n\n"
    "Staff were notified this morning. The rollout starts in January. "
    "Customers will see the change in Q2.\n\n"
    "The board meets again in March. More details will follow."
)
for min_r in (0.85, 0.75):
    joined, chunks = sim_chunked(long_text, 15, min_r)
    missing = "Customers will see the change in Q2." not in joined
    print(f"[E2E] min_ratio={min_r}: chunks={len(chunks)} "
          f"original_len={len(long_text)} joined_len={len(joined)} missing_Q2_sentence={missing}")

# ── Show the actual chunk decomposition for the long text ──────────────────
print("\nChunk decomposition (max_words=15):")
for i, (ct, sep) in enumerate(_chunk_text_by_sentences(long_text, 15)):
    print(f"  chunk {i}: {ct!r}  sep={sep!r}")
