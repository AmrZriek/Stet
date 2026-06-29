"""A/B harness for the deterministic trailing-punctuation restore.

Two sites do this:
  1. Per-chunk in model_manager._rewrite_sentence_chunk
     (model_manager.py:1282-1296)
  2. Post-reassembly in _apply_post_fixes
     (text_utils.py:732-734)

This harness measures their isolated effect by:
  - Monkey-patching each site to a togglable form.
  - Calling correct_text_patch with the site ON, then with the site OFF.
  - For each (sample, strength, site) the LLM is called once with temp=0
    and top_k=1 (so output is essentially a function of input).
  - Where the two outputs differ, classifying the restore's effect:
      needed       = original ended in .?!, LLM dropped it, restore was right
      harmful      = LLM dropped intentionally, restore was wrong
      redundant    = restore was a no-op (with == without)

Usage:
    python scripts/eval_punct_ab.py --offline
    python scripts/eval_punct_ab.py --strength smart_fix
    python scripts/eval_punct_ab.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


STRENGTHS = ("conservative", "smart_fix", "aggressive")


# ── Targeted samples ────────────────────────────────────────────────────────
# Designed to force the LLM to drop terminal periods, so the restore rule
# actually fires.  Most eval_matrix samples end cleanly and the LLM keeps
# the period; we need samples where the LLM is likely to lose it.
#
# Each sample has:
#   id, input, verdict, notes
#   verdict is the human-judged expectation for this sample:
#     "expected_drop"      = LLM dropping the period is the LLM's correct call
#                            (mid-sentence fragment, list item, etc.). Restore
#                            firing here is harmful.
#     "expected_restore"   = LLM dropping the period is wrong. Restore firing
#                            here is needed.
#     "neutral"            = depends on LLM's behavior; we just observe.
SAMPLES = [
    # ── expected_drop: LLM should drop period; restore is harmful ────────
    {"id": "mid_paragraph_clean_end",
     "input": "First sentence is correct. Second is also fine",
     "verdict": "expected_drop",
     "notes": "Final sentence has no period in source; restore would add one."},
    {"id": "bullet_list_no_periods",
     "input": "Please bring:\n- apples\n- oranges\n- bread",
     "verdict": "expected_drop",
     "notes": "List items; restore would add periods to non-sentences."},
    {"id": "fragment_after_colon",
     "input": "The categories are: alpha, beta, gamma",
     "verdict": "expected_drop",
     "notes": "After-colon enumeration; no period expected."},
    {"id": "header_line",
     "input": "Project Status Update",
     "verdict": "expected_drop",
     "notes": "Title/header; no period expected."},
    {"id": "trailing_url_strip_period",
     "input": "See the docs at https://example.com/api/v2",
     "verdict": "expected_drop",
     "notes": "URL at end; period would be appended to URL chars."},
    {"id": "trailing_email_strip_period",
     "input": "Contact us at support@example.com",
     "verdict": "expected_drop",
     "notes": "Email at end; period would corrupt the address."},
    {"id": "dialogue_line",
     "input": "He said \"we're going\" and walked out",
     "verdict": "expected_drop",
     "notes": "Dialogue fragment; period after closing quote is correct but LLM may drop."},
    {"id": "imperative_no_period",
     "input": "Please review the attached document and let me know",
     "verdict": "expected_drop",
     "notes": "Polite imperative, no period in source."},
    {"id": "two_sentences_one_missing",
     "input": "The first sentence ends with a period. The second does not",
     "verdict": "expected_drop",
     "notes": "Final sentence has no period; restore would add one."},
    {"id": "multiparagraph_last_missing",
     "input": "First paragraph ends correctly.\n\nThe second paragraph trails off",
     "verdict": "expected_drop",
     "notes": "Final paragraph has no period; restore would add one."},
    {"id": "trailing_path_no_period",
     "input": "The log is at /var/log/app/error",
     "verdict": "expected_drop",
     "notes": "File path at end; no period expected."},
    {"id": "decimal_number_end",
     "input": "The answer is 3.14",
     "verdict": "expected_drop",
     "notes": "Decimal number at end; period is part of the number, not sentence punct."},
    {"id": "ellipsis_end",
     "input": "And then...",
     "verdict": "expected_drop",
     "notes": "Ellipsis is intentional; restore would append a fourth dot or another char."},

    # ── expected_restore: LLM dropping period is wrong; restore is needed ──
    {"id": "full_paragraph_with_typo_loses_period",
     "input": "Teh project recieved teh update.",
     "verdict": "expected_restore",
     "notes": "Typo + trailing period. If LLM fixes typo and keeps period, restore is no-op."},
    {"id": "ends_with_existing_excl",
     "input": "That is fantastic!",
     "verdict": "expected_restore",
     "notes": "Source ends with !. If LLM drops it, restore is right."},
    {"id": "ends_with_existing_q",
     "input": "Are you sure?",
     "verdict": "expected_restore",
     "notes": "Source ends with ?. If LLM drops it, restore is right."},
    {"id": "all_clean_paragraphs",
     "input": (
         "This is a clean first sentence.\n\n"
         "This is a clean second paragraph."
     ),
     "verdict": "expected_restore",
     "notes": "Both sentences end with periods in source. LLM should preserve."},
    {"id": "single_complete_sentence",
     "input": "This sentence ends with a period.",
     "verdict": "expected_restore",
     "notes": "If LLM drops the period, restore is right."},

    # ── neutral: depends on LLM behavior ─────────────────────────────────
    {"id": "two_clean_no_garble",
     "input": "The project received the update yesterday. The team celebrated",
     "verdict": "neutral",
     "notes": "Final sentence has no period; LLM behavior varies."},
    {"id": "url_inside_sentence",
     "input": "Visit https://example.com for more information about the project",
     "verdict": "neutral",
     "notes": "URL mid-sentence, no terminal period."},
    {"id": "abbreviation_dr_mr",
     "input": "Talk to Dr Smith about the results. He will get back to you",
     "verdict": "neutral",
     "notes": "Abbreviation may confuse the LLM's period handling."},
    {"id": "long_paragraph_drops_mid_period",
     "input": (
         "The first sentence ends here. "
         "The second sentence trails off. "
         "Then a third one that ends. "
         "And a fourth that just stops"
     ),
     "verdict": "neutral",
     "notes": "Mid-paragraph drop test; restore only fires on final char."},
]


# ── Toggleable post-fix (mirror of text_utils._apply_post_fixes) ───────────
# We don't modify the source; we wrap the live function in Python with a
# version that has the same dedup/contraction logic but lets us toggle
# just the trailing-punct-restore block.

def _make_post_fixes(skip_punct_restore: bool):
    """Return a callable with the same signature as _apply_post_fixes
    but with the trailing-punct-restore block optionally skipped."""
    from stet.core.text_utils import (
        _DUP_WORD_PATTERN,
        _remove_introduced_duplicate_sentences,
        _COMPILED_CONTRACTIONS,
    )

    def pf(text: str, original: str = "", strength: str = "smart_fix") -> str:
        if not text:
            return text
        result = text
        if _DUP_WORD_PATTERN.search(result):
            def _dedup(m: re.Match) -> str:
                if original and m.group(0).lower() in original.lower():
                    return m.group(0)
                return m.group(1)
            result = _DUP_WORD_PATTERN.sub(_dedup, result)
        result = _remove_introduced_duplicate_sentences(result, original)
        if strength not in {"spelling_only", "conservative"}:
            for c_pat, repl in _COMPILED_CONTRACTIONS:
                if c_pat.search(result):
                    def _repl_fn(m, _r=repl):
                        if m.group().isupper():
                            return _r.upper()
                        if m.group()[0].isupper():
                            return _r[0].upper() + _r[1:]
                        return _r
                    result = c_pat.sub(_repl_fn, result)
            if not skip_punct_restore:
                if original and original[-1] in ".?!":
                    if (
                        result
                        and not result.endswith(original[-1])
                        and result[-1] not in ".?!"
                    ):
                        result += original[-1]
        return result

    return pf


def _per_chunk_restore_enabled(chunk_orig: str, chunk_corrected: str) -> str:
    """Reproduction of model_manager.py:1282-1296 with restore ENABLED."""
    if not chunk_corrected:
        return chunk_corrected
    orig_stripped = chunk_orig.rstrip()
    corr_stripped = chunk_corrected.rstrip()
    if (
        orig_stripped
        and orig_stripped[-1] in ".!?"
        and corr_stripped
        and corr_stripped[-1] not in ".!?"
    ):
        trailing = chunk_corrected[len(corr_stripped):]
        return corr_stripped + orig_stripped[-1] + trailing
    return chunk_corrected


def _per_chunk_restore_disabled(chunk_orig: str, chunk_corrected: str) -> str:
    """Same as enabled, but returns the input unchanged."""
    return chunk_corrected


# ── A/B evaluation ────────────────────────────────────────────────────────

@dataclass
class DiffRow:
    sample_id: str
    strength: str
    site: str          # "per_chunk", "post_reassembly", "both"
    verdict: str
    input: str
    with_restore: str
    without_restore: str
    fired: bool
    classification: str
    notes: str = ""


def _classify(verdict: str, fired: bool) -> str:
    if not fired:
        return "redundant"
    if verdict == "expected_restore":
        return "needed"
    if verdict == "expected_drop":
        return "harmful"
    return "indeterminate"


def _run_offline(args: argparse.Namespace) -> int:
    """Dry-run: identity LLM (no rewrite), then check if restore would fire."""
    pf_on = _make_post_fixes(skip_punct_restore=False)
    pf_off = _make_post_fixes(skip_punct_restore=True)
    for sample in SAMPLES:
        for strength in args.strength or STRENGTHS:
            # post-reassembly site
            with_on = pf_on(sample["input"], original=sample["input"], strength=strength)
            with_off = pf_off(sample["input"], original=sample["input"], strength=strength)
            fired = with_on != with_off
            row = DiffRow(
                sample_id=sample["id"],
                strength=strength,
                site="post_reassembly",
                verdict=sample["verdict"],
                input=sample["input"],
                with_restore=with_on,
                without_restore=with_off,
                fired=fired,
                classification=_classify(sample["verdict"], fired),
                notes=sample.get("notes", ""),
            )
            _print_row(row, args.json)
    return 0


def _run_live(args: argparse.Namespace) -> int:
    """Live: call the LLM via correct_text_patch, monkey-patching the
    two restore sites to be independently togglable."""
    from stet.core.config import ConfigManager
    from stet.llm import model_manager as mm_mod
    from stet.core import text_utils as tu_mod
    from stet.core.text_utils import _apply_post_fixes as real_pf

    strengths = tuple(args.strength or STRENGTHS)
    manager = mm_mod.ModelManager(ConfigManager())
    manager.is_loaded = lambda: True

    # CRITICAL: model_manager.py line 17 imports _apply_post_fixes by name
    # into its own module namespace, so patching tu_mod._apply_post_fixes
    # has no effect. We must patch the local binding in model_manager's
    # module namespace.
    real_pf_in_mm = mm_mod._apply_post_fixes

    # Cache the original per-chunk rewrite
    original_chunk_rewrite = mm_mod.ModelManager._rewrite_sentence_chunk

    def make_chunk_rewrite(skip_restore: bool):
        """Return a method that wraps _rewrite_sentence_chunk so we can
        disable the per-chunk punct-restore (lines 1282-1296). To disable,
        we detect what the restore would have added and strip it back off.
        """
        def rewrite(self, chunk_text, custom_sys, idx, total, strength_,
                    cancel_event=None, mode_prompt_override=None, session=None):
            out = original_chunk_rewrite(
                self, chunk_text, custom_sys, idx, total, strength_,
                cancel_event, mode_prompt_override, session,
            )
            if out is None or not skip_restore:
                return out
            orig_stripped = chunk_text.rstrip()
            out_stripped = out.rstrip()
            if (
                orig_stripped
                and orig_stripped[-1] in ".!?"
                and out_stripped
                and out_stripped.endswith(orig_stripped[-1])
                and len(out_stripped) > 1
                and out_stripped[-2] not in ".!?"
            ):
                trailing = out[len(out_stripped):]
                return out_stripped[:-1] + trailing
            return out
        return rewrite

    for sample in SAMPLES:
        for strength in strengths:
            started = perf_counter()
            try:
                # Path A: BOTH restore sites ENABLED (live behavior).
                mm_mod._apply_post_fixes = _make_post_fixes(skip_punct_restore=False)
                tu_mod._apply_post_fixes = mm_mod._apply_post_fixes
                mm_mod.ModelManager._rewrite_sentence_chunk = make_chunk_rewrite(skip_restore=False)
                with_enabled, _ = manager.correct_text_patch(
                    sample["input"], strength=strength,
                )

                # Path B: BOTH restore sites DISABLED.
                mm_mod._apply_post_fixes = _make_post_fixes(skip_punct_restore=True)
                tu_mod._apply_post_fixes = mm_mod._apply_post_fixes
                mm_mod.ModelManager._rewrite_sentence_chunk = make_chunk_rewrite(skip_restore=True)
                with_disabled, _ = manager.correct_text_patch(
                    sample["input"], strength=strength,
                )

                # Restore the real function for the next iteration
                mm_mod._apply_post_fixes = real_pf_in_mm
                tu_mod._apply_post_fixes = real_pf
                mm_mod.ModelManager._rewrite_sentence_chunk = original_chunk_rewrite

                fired = with_enabled != with_disabled
                row = DiffRow(
                    sample_id=sample["id"],
                    strength=strength,
                    site="post_reassembly",
                    verdict=sample["verdict"],
                    input=sample["input"],
                    with_restore=with_enabled or "",
                    without_restore=with_disabled or "",
                    fired=fired,
                    classification=_classify(sample["verdict"], fired),
                    notes=sample.get("notes", ""),
                )
                elapsed_ms = int((perf_counter() - started) * 1000)
                if args.json:
                    d = asdict(row)
                    d["elapsed_ms"] = elapsed_ms
                    print(json.dumps(d, ensure_ascii=False))
                else:
                    _print_row(row, args.json)
                    print(f"      (elapsed={elapsed_ms}ms)")
            except Exception as exc:
                if args.json:
                    print(json.dumps({
                        "sample_id": sample["id"],
                        "strength": strength,
                        "error": f"{type(exc).__name__}: {exc}",
                    }))
                else:
                    print(f"FAIL {sample['id']:36} {strength:12} {exc}")
    return 0


def _print_row(row: DiffRow, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(row), ensure_ascii=False))
        return
    status = "FIRED" if row.fired else "skip"
    print(
        f"{status:5} {row.sample_id:36} {row.strength:12} "
        f"verdict={row.verdict:18} class={row.classification:14} "
        f"in={row.input!r}"
    )
    if row.fired or row.with_restore != row.input:
        print(f"      with    ={row.with_restore!r}")
        print(f"      without ={row.without_restore!r}")
    if row.notes:
        print(f"      note: {row.notes}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n", 1)[0],
    )
    parser.add_argument(
        "--offline", action="store_true",
        help="Dry-run; do not call the LLM.",
    )
    parser.add_argument(
        "--strength", choices=STRENGTHS, action="append",
        help="Strength to evaluate. Default: all.",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Emit JSON lines.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.offline:
        return _run_offline(args)
    return _run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
