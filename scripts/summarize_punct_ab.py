"""Aggregate the eval_punct_ab.py results into a verdict table.

Usage:
    python scripts/summarize_punct_ab.py scripts/eval_punct_ab_results.jsonl
"""
import json
import sys
from collections import Counter, defaultdict


def main(jsonl_path: str) -> int:
    rows = []
    for line in open(jsonl_path, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    by_strength = defaultdict(list)
    for r in rows:
        if "error" in r and "classification" not in r:
            print(f"ERROR {r.get('sample_id')} {r.get('strength')}: {r.get('error')}")
            continue
        by_strength[r["strength"]].append(r)

    print("=" * 78)
    print(f"Total rows: {len(rows)} across {len(by_strength)} strengths")
    print("=" * 78)
    for strength, group in by_strength.items():
        n = len(group)
        n_fired = sum(1 for r in group if r.get("fired"))
        counts = Counter(r.get("classification", "?") for r in group)
        n_needed = counts.get("needed", 0)
        n_harmful = counts.get("harmful", 0)
        n_redundant = counts.get("redundant", 0)
        n_indet = counts.get("indeterminate", 0)
        n_errors = sum(1 for r in group if "error" in r)
        print()
        print(f"[{strength}]  total={n}  fired={n_fired}")
        print(f"  needed       = {n_needed}")
        print(f"  harmful      = {n_harmful}")
        print(f"  redundant    = {n_redundant}")
        print(f"  indeterminate= {n_indet}")
        if n_errors:
            print(f"  errors       = {n_errors}")
        if n_fired:
            verdict = "KEEP" if n_needed > n_harmful else (
                "REMOVE" if n_harmful > n_needed else "AMBIGUOUS"
            )
            ratio = n_needed / max(n_harmful, 1)
            print(f"  >>> VERDICT  = {verdict}  (needed/harmful = {n_needed}/{n_harmful} = {ratio:.2f})")

    # Show every fired case for inspection
    print()
    print("=" * 78)
    print("FIRED CASES (with != without):")
    print("=" * 78)
    for r in rows:
        if not r.get("fired"):
            continue
        print(
            f"  {r['strength']:12} {r['sample_id']:36} "
            f"verdict={r['verdict']:18} class={r['classification']:14}"
        )
        print(f"    in    = {r['input']!r}")
        print(f"    with  = {r['with_restore']!r}")
        print(f"    w/o   = {r['without_restore']!r}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/summarize_punct_ab.py <results.jsonl>")
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
