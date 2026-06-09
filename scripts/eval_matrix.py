"""Run a small live correction matrix across correction strengths.

The default mode attempts to use the local backend. Use --offline to print the
matrix without loading a model, which keeps the CLI safe in environments where
the llama.cpp server or model files are unavailable.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


STRENGTHS = ("conservative", "smart_fix", "aggressive")

SAMPLES = (
    {
        "id": "repeated_word_intentional",
        "input": "This is very very important.",
        "notes": "Intentional repeated word should be preserved.",
        "expected": {
            "conservative": "This is very very important.",
            "smart_fix": "This is very very important.",
            "aggressive": "This is very very important.",
        },
    },
    {
        "id": "repeated_sentence_intentional",
        "input": "Stop. Stop.",
        "notes": "Intentional repeated sentence should be preserved.",
        "expected": {
            "conservative": "Stop. Stop.",
            "smart_fix": "Stop. Stop.",
            "aggressive": "Stop. Stop.",
        },
    },
    {
        "id": "simple_typos",
        "input": "Teh project recieved teh update.",
        "notes": "Basic typo correction.",
    },
    {
        "id": "noisy_lowercase",
        "input": "i dont know if its gonna work.",
        "notes": "Mode-specific capitalization and contraction behavior.",
    },
)


@dataclass
class EvalRow:
    sample_id: str
    strength: str
    pass_index: int
    ok: bool
    units: int | None
    elapsed_ms: int | None
    input: str
    output: str | None
    expected: str | None
    error: str | None = None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate Stet corrections across strengths."
    )
    parser.add_argument(
        "--passes",
        type=int,
        default=5,
        help="Passes per sample per strength. Default: 5.",
    )
    parser.add_argument(
        "--strength",
        choices=STRENGTHS,
        action="append",
        help="Strength to evaluate. Repeat to select multiple. Default: all.",
    )
    parser.add_argument(
        "--sample",
        action="append",
        help="Sample id to evaluate. Repeat to select multiple. Default: all.",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Dry-run the matrix without loading the model or backend.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON lines instead of a compact text table.",
    )
    return parser


def _selected_samples(sample_ids: list[str] | None) -> list[dict[str, str]]:
    if not sample_ids:
        return list(SAMPLES)

    wanted = set(sample_ids)
    selected = [sample for sample in SAMPLES if sample["id"] in wanted]
    missing = sorted(wanted - {sample["id"] for sample in selected})
    if missing:
        raise SystemExit(f"Unknown sample id(s): {', '.join(missing)}")
    return selected


def _print_row(row: EvalRow, as_json: bool) -> None:
    if as_json:
        print(json.dumps(asdict(row), ensure_ascii=False))
        return

    status = "ok" if row.ok else "fail"
    elapsed = "-" if row.elapsed_ms is None else f"{row.elapsed_ms}ms"
    output = row.output if row.output is not None else row.error
    expected = "" if row.expected is None else f" expected={row.expected!r}"
    print(
        f"{status:4} {row.sample_id:30} {row.strength:12} "
        f"pass={row.pass_index:<2} units={row.units!s:<3} "
        f"time={elapsed:<7} output={output!r}{expected}"
    )


def _expected_output(sample: dict[str, object], strength: str) -> str | None:
    expected = sample.get("expected")
    if expected is None:
        return None
    if isinstance(expected, dict):
        return expected.get(strength)  # type: ignore[return-value]
    return expected  # type: ignore[return-value]


def _run_offline(args: argparse.Namespace) -> int:
    strengths = tuple(args.strength or STRENGTHS)
    samples = _selected_samples(args.sample)
    for sample in samples:
        for strength in strengths:
            for pass_index in range(1, args.passes + 1):
                _print_row(
                    EvalRow(
                        sample_id=sample["id"],
                        strength=strength,
                        pass_index=pass_index,
                        ok=True,
                        units=None,
                        elapsed_ms=None,
                        input=sample["input"],
                        expected=_expected_output(sample, strength),
                        output="[offline] " + sample["input"],
                    ),
                    args.json,
                )
    return 0


def _run_live(args: argparse.Namespace) -> int:
    from stet.core.config import ConfigManager
    from stet.llm.model_manager import ModelManager

    strengths = tuple(args.strength or STRENGTHS)
    samples = _selected_samples(args.sample)
    manager = ModelManager(ConfigManager())
    failures = 0

    for sample in samples:
        for strength in strengths:
            for pass_index in range(1, args.passes + 1):
                started = perf_counter()
                try:
                    output, units = manager.correct_text_patch(
                        sample["input"],
                        strength=strength,
                    )
                    elapsed_ms = int((perf_counter() - started) * 1000)
                    expected = _expected_output(sample, strength)
                    ok = output is not None and (expected is None or output == expected)
                    if not ok:
                        failures += 1
                    _print_row(
                        EvalRow(
                            sample_id=sample["id"],
                            strength=strength,
                            pass_index=pass_index,
                            ok=ok,
                            units=units,
                            elapsed_ms=elapsed_ms,
                            input=sample["input"],
                            expected=expected,
                            output=output,
                            error=(
                                None
                                if ok
                                else "output mismatch"
                                if output is not None
                                else "backend returned no correction"
                            ),
                        ),
                        args.json,
                    )
                except Exception as exc:
                    failures += 1
                    elapsed_ms = int((perf_counter() - started) * 1000)
                    _print_row(
                        EvalRow(
                            sample_id=sample["id"],
                            strength=strength,
                            pass_index=pass_index,
                            ok=False,
                            units=None,
                            elapsed_ms=elapsed_ms,
                            input=sample["input"],
                            expected=_expected_output(sample, strength),
                            output=None,
                            error=f"{type(exc).__name__}: {exc}",
                        ),
                        args.json,
                    )

    return 1 if failures else 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.passes < 1:
        parser.error("--passes must be at least 1")

    if args.offline:
        return _run_offline(args)
    return _run_live(args)


if __name__ == "__main__":
    raise SystemExit(main())
