"""Live LLM behavior matrix — OPT-IN ONLY, never runs with the normal suite.

Runs the real correction pipeline against real GGUF(s) across a settings axis
and a behavioral corpus, with the model itself judging output quality.

Usage:
    pytest tests/test_live_matrix.py --run-live            # smoke: deterministic cell, all models
    pytest tests/test_live_matrix.py --run-live=full       # full sweep: all settings x all cases
    STET_LIVE_MODELS="D:/models/a.gguf,D:/models/b.gguf" pytest tests/test_live_matrix.py --run-live=full

When to run: after a llama.cpp backend bump, when evaluating a new model, or
when sampling/pipeline behavior is in doubt. After every normal `pytest` run:
never — this file skips without --run-live.

Env:
    STET_LIVE_MODELS     comma-separated GGUF paths. Default: model_path from
                         your real user config (single-model mode).
    STET_LIVE_JUDGE_URL  optional separate chat-completions endpoint used for
                         judging (e.g. a bigger model). Default: self-judge on
                         the same server (flagged in the report).

Artifact: tests/.artifacts/live_matrix_<UTC-timestamp>.json with per-case
outputs, judge scores, and a model x setting pass-rate grid, plus a
before/after diff against live_matrix_baseline.json when present.
"""

import difflib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

pytestmark = [pytest.mark.live, pytest.mark.slow]

ARTIFACT_DIR = Path(__file__).resolve().parent / ".artifacts"

# ── setting axis ────────────────────────────────────────────────────────────
# correction_* keys are read live per request; server_kwargs force a relaunch.
SETTING_CELLS = {
    "deterministic": {
        "overrides": {
            "correction_temperature": 0.0,
            "correction_top_k": 1,
            "correction_top_p": 0.95,
            "correction_min_p": 0.0,
        },
        "server_kwargs": {},
    },
    "chatty": {
        "overrides": {
            "correction_temperature": 0.7,
            "correction_top_k": 40,
            "correction_top_p": 0.95,
            "correction_min_p": 0.05,
        },
        "server_kwargs": {},
    },
    "min_p_strict": {
        "overrides": {"correction_min_p": 0.3},
        "server_kwargs": {},
    },
    "typical": {
        "overrides": {"typical_p": 0.7},
        "server_kwargs": {},
    },
    "mirostat2": {
        "overrides": {"mirostat": 2, "mirostat_tau": 5.0, "mirostat_eta": 0.1},
        "server_kwargs": {},
    },
    "rep_penalty": {
        "overrides": {
            "repeat_penalty": 1.15,
            "frequency_penalty": 0.5,
            "presence_penalty": 0.5,
        },
        "server_kwargs": {},
    },
    "seeded": {
        "overrides": {"seed": 42},
        "server_kwargs": {},
    },
    "mtp_off": {"overrides": {}, "server_kwargs": {"disable_mtp": True}},
    "flash_off": {"overrides": {}, "server_kwargs": {"disable_flash_attn": True}},
    "kv_f16": {
        "overrides": {"kv_cache_type_k": "f16", "kv_cache_type_v": "f16"},
        "server_kwargs": {},
    },
}

# ── behavior corpus ─────────────────────────────────────────────────────────
# must_contain / must_not_contain are deterministic gates; the judge scores
# meaning preservation + introduced errors. smoke=True runs in base mode.
CORPUS = [
    # spelling-only x4
    {"id": "spell_1", "category": "spelling", "strength": "spelling_only",
     "input": "I beleive we should recieve the package seperateley.",
     "must_contain": ["believe", "receive"], "must_not_contain": [],
     "judge": True, "smoke": True},
    {"id": "spell_2", "category": "spelling", "strength": "spelling_only",
     "input": "Teh project definately occured on wheather delay.",
     "must_contain": ["The", "definitely", "occurred"], "must_not_contain": [],
     "judge": True, "smoke": True},
    {"id": "spell_3", "category": "spelling", "strength": "spelling_only",
     "input": "Its a seperate occassion, not teh first one.",
     "must_contain": ["separate"], "must_not_contain": ["teh"],
     "judge": True, "smoke": False},
    {"id": "spell_4", "category": "spelling", "strength": "spelling_only",
     "input": "The accomodation was excelent and the staff were freindly.",
     "must_contain": ["accommodation", "excellent", "friendly"], "must_not_contain": [],
     "judge": True, "smoke": False},
    # grammar x4
    {"id": "gram_1", "category": "grammar", "strength": "full_correction",
     "input": "Their going to the store with there friends.",
     "must_contain": ["They're", "their"], "must_not_contain": [],
     "judge": True, "smoke": True},
    {"id": "gram_2", "category": "grammar", "strength": "full_correction",
     "input": "Its a nice day and your welcome to join us.",
     "must_contain": ["It's", "you're"], "must_not_contain": [],
     "judge": True, "smoke": True},
    {"id": "gram_3", "category": "grammar", "strength": "full_correction",
     "input": "She don't like apples, but he do like oranges.",
     "must_contain": ["doesn't"], "must_not_contain": ["She don't"],
     "judge": True, "smoke": False},
    {"id": "gram_4", "category": "grammar", "strength": "full_correction",
     "input": "The team are winning, and the data shows it.",
     "must_contain": ["is winning"], "must_not_contain": [],
     "judge": True, "smoke": False},
    # rewrite-polish x2
    {"id": "rw_1", "category": "rewrite", "strength": "rewrite_polish",
     "input": "Due to the fact that the meeting was cancelled, we will need to, at a later point in time, reschedule it.",
     "must_contain": ["reschedule"], "must_not_contain": ["Due to the fact that"],
     "judge": True, "smoke": True},
    {"id": "rw_2", "category": "rewrite", "strength": "rewrite_polish",
     "input": "I am writing this email in order to inform you that your request has been received and is currently being processed.",
     "must_contain": ["request"], "must_not_contain": [],
     "judge": True, "smoke": False},
    # url / email / path preservation x3 (byte-identical anchors)
    {"id": "url_1", "category": "preservation", "strength": "full_correction",
     "input": "Please visit https://example.com/some-typo-path?q=beleive for details.",
     "must_contain": ["https://example.com/some-typo-path?q=beleive"], "must_not_contain": [],
     "judge": False, "smoke": True},
    {"id": "url_2", "category": "preservation", "strength": "full_correction",
     "input": "Contact me at jane.doe@example.org, she will confirm teh meeting.",
     "must_contain": ["jane.doe@example.org", "the meeting"], "must_not_contain": [],
     "judge": True, "smoke": False},
    {"id": "url_3", "category": "preservation", "strength": "full_correction",
     "input": "The file lives at C:\\Users\\jane\\Documents\\recieve_report.docx and needs review.",
     "must_contain": ["C:\\Users\\jane\\Documents\\recieve_report.docx"], "must_not_contain": [],
     "judge": False, "smoke": False},
    # protected terms x2
    {"id": "prot_1", "category": "protected_terms", "strength": "full_correction",
     "input": "The StetEngine recieve pipeline uses Qdrant for retrival.",
     "must_contain": ["StetEngine", "Qdrant"], "must_not_contain": ["Stetengine", "stetengine"],
     "protected_terms": ["StetEngine", "Qdrant"],
     "judge": True, "smoke": True},
    {"id": "prot_2", "category": "protected_terms", "strength": "full_correction",
     "input": "Deploy the FooBarBaz service; teh config is ready.",
     "must_contain": ["FooBarBaz", "the config"], "must_not_contain": ["Foobarbaz"],
     "protected_terms": ["FooBarBaz"],
     "judge": True, "smoke": False},
    # code blocks x2
    {"id": "code_1", "category": "code", "strength": "full_correction",
     "input": "Run `pip install reqeusts` then call requests.get(url); teh docs explain it.",
     "must_contain": ["requests.get(url)"], "must_not_contain": [],
     "judge": True, "smoke": True},
    {"id": "code_2", "category": "code", "strength": "full_correction",
     "input": "```python\ndef recieve(data):\n    return data\n```\nTeh function is simple.",
     "must_contain": ["def recieve(data):", "The function"], "must_not_contain": ["def receive(data):"],
     "judge": True, "smoke": False},
    # proper names x2 (must NOT be "fixed")
    {"id": "name_1", "category": "names", "strength": "full_correction",
     "input": "Nguyen and Kowalski met in Reykjavik to discuss teh proposal.",
     "must_contain": ["Nguyen", "Kowalski", "Reykjavik", "the proposal"], "must_not_contain": [],
     "judge": True, "smoke": True},
    {"id": "name_2", "category": "names", "strength": "full_correction",
     "input": "Saoirse Ronan starred with Timothee Chalamet; teh film was great.",
     "must_contain": ["Saoirse Ronan", "the film"], "must_not_contain": [],
     "judge": True, "smoke": False},
    # repetition x2
    {"id": "rep_1", "category": "repetition", "strength": "full_correction",
     "input": "Very, very, very important: teh deadline is Friday, Friday, Friday.",
     "must_contain": ["the deadline"], "must_not_contain": [],
     "judge": True, "smoke": True},
    {"id": "rep_2", "category": "repetition", "strength": "full_correction",
     "input": "ok ok ok ok ok ok teh thing works works works",
     "must_contain": ["The thing"], "must_not_contain": ["<think>"],
     "judge": True, "smoke": False},
    # long input / chunking x2 (deterministic gates only — too big to judge)
    {"id": "long_1", "category": "long_input", "strength": "full_correction",
     "input": ("Teh quick brown fox jumps over teh lazy dog near teh riverbank. " * 120).strip(),
     "must_contain": ["The quick brown fox"], "must_not_contain": ["Teh quick"],
     "judge": False, "smoke": False},
    {"id": "long_2", "category": "long_input", "strength": "spelling_only",
     "input": ("I beleive this sentance has a typo and so does this one as well. " * 80).strip(),
     "must_contain": ["believe"], "must_not_contain": ["beleive"],
     "judge": False, "smoke": True},
    # thinking leak x1 (judge decides; vacuous pass on non-reasoning models)
    {"id": "think_1", "category": "thinking", "strength": "full_correction",
     "input": "Explain briefly why teh sky is blue, then correct: teh wether is nice.",
     "must_contain": [], "must_not_contain": ["<think>", "< /think>", "</think>"],
     "judge": True, "smoke": False},
]

JUDGE_SYSTEM = (
    "You are a strict text-correction evaluator. Compare ORIGINAL (user text with "
    "errors) and CORRECTED (model output). The correction strength is given. Score "
    "1-5: 5 = all errors fixed, meaning preserved, nothing spurious; 3 = usable "
    "with minor issues; 1 = meaning changed or output garbage. "
    "introduced_errors means NEW errors the correction ADDED that were NOT in "
    "ORIGINAL — never list ORIGINAL typos that got fixed. Reply with STRICT "
    "JSON only: {\"score\": N, \"preserved_meaning\": true/false, "
    "\"introduced_errors\": [...], \"notes\": \"...\"}"
)

_MANAGERS: dict = {}


def _mode(request) -> str | None:
    try:
        return request.config.getoption("run_live")
    except ValueError:
        return None


def _resolve_models(cfg_model: str) -> list[str]:
    env = os.environ.get("STET_LIVE_MODELS", "").strip()
    if env:
        return [p.strip() for p in env.split(",") if p.strip()]
    return [cfg_model] if cfg_model else []


def pytest_generate_tests(metafunc):
    if "live_cell" not in metafunc.fixturenames:
        return
    mode = metafunc.config.getoption("run_live")
    cells = ["deterministic"] if mode != "full" else list(SETTING_CELLS)
    env = os.environ.get("STET_LIVE_MODELS", "").strip()
    n_models = len([p for p in env.split(",") if p.strip()]) if env else 1
    # model paths resolve in-fixture (need the real user config); collection
    # stays server-independent so plain --collect-only never touches the backend.
    metafunc.parametrize("live_cell", cells, scope="session")
    metafunc.parametrize("live_model_idx", list(range(n_models)), scope="session")


def _build_cfg(model_path: str, monkeypatch, tmp_path):
    """Fresh ConfigManager seeded from the REAL user config + overrides.

    Never mutates the user's config file: overrides live in a temp copy.
    """
    import stet.core.config as config_mod
    from stet.core.config import ConfigManager

    real_file = Path(config_mod.CONFIG_FILE)
    data = json.loads(real_file.read_text(encoding="utf-8")) if real_file.exists() else {}
    data["model_path"] = model_path
    seed = tmp_path / "live_matrix_config.json"
    seed.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(config_mod, "CONFIG_FILE", seed)
    monkeypatch.setattr("stet.constants.CONFIG_FILE", seed)
    return ConfigManager()


def _get_manager(cfg, cell_name: str):
    """Return a loaded ModelManager for (model, server-flags), reusing when hot."""
    from stet.llm.model_manager import ModelManager

    model_path = cfg.get("model_path", "")
    sig = (model_path, json.dumps(SETTING_CELLS[cell_name]["server_kwargs"], sort_keys=True))
    mgr = _MANAGERS.get(sig)
    if mgr is not None and mgr.is_loaded():
        return mgr
    for old in list(_MANAGERS.values()):
        try:
            old.unload_model()
        except Exception:
            pass
    _MANAGERS.clear()
    mgr = ModelManager(cfg, label=f"live:{Path(model_path).name}:{cell_name}")
    _MANAGERS[sig] = mgr
    return mgr


def _judge(base_url: str, strength: str, original: str, corrected: str) -> dict:
    """Ask the model to score its own correction. Returns {} on any failure."""
    url = os.environ.get("STET_LIVE_JUDGE_URL", base_url).rstrip("/") + "/v1/chat/completions"
    for attempt in ("", " Reply with JSON only, no other text."):
        body = {"role": "user", "content": (
            f"STRENGTH: {strength}\nORIGINAL: {original[:4000]}\nCORRECTED: {corrected[:4000]}"
        ) + attempt}
        payload = {
            "messages": [{"role": "system", "content": JUDGE_SYSTEM}, body],
            "temperature": 0.0,
            "top_k": 1,
            "max_tokens": 384,
            "stream": False,
        }
        try:
            r = requests.post(url, json=payload, timeout=180)
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"].strip()
            if content.startswith("```"):
                content = content.strip("`").split("\n", 1)[-1].rsplit("```", 1)[0]
            data = json.loads(content[content.index("{"): content.rindex("}") + 1])
            return {
                "score": int(data.get("score", 0)),
                "preserved_meaning": bool(data.get("preserved_meaning", False)),
                "introduced_errors": list(data.get("introduced_errors", []))[:8],
                "notes": str(data.get("notes", ""))[:300],
            }
        except Exception as e:  # noqa: BLE001 — retry once, then record
            last_error = e
    return {"score": 0, "preserved_meaning": False,
            "introduced_errors": [f"judge-error: {type(last_error).__name__}"], "notes": ""}


@pytest.fixture(scope="session")
def live_report(request):
    store: dict = {"cells": []}
    yield store
    if not store["cells"]:
        return
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = {
        "meta": {
            "date_utc": stamp,
            "mode": request.config.getoption("run_live"),
            "self_judge": not os.environ.get("STET_LIVE_JUDGE_URL"),
        },
        "cells": store["cells"],
    }
    baseline = ARTIFACT_DIR / "live_matrix_baseline.json"
    if baseline.exists():
        try:
            out["diff_vs_baseline"] = _diff_against(json.loads(baseline.read_text(encoding="utf-8")), out)
        except Exception as e:
            out["diff_vs_baseline"] = {"error": f"{type(e).__name__}: {e}"}
    path = ARTIFACT_DIR / f"live_matrix_{stamp}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    # compact console grid
    print(f"\n[live-matrix] artifact: {path}")
    models = sorted({c["model"] for c in store["cells"]})
    for model in models:
        row = [f"  {Path(model).name}"]
        for c in [x for x in store["cells"] if x["model"] == model]:
            row.append(f"{c['setting']}={c['pass_rate']:.0%}({c['mean_score']:.1f})")
        print("[live-matrix] " + " | ".join(row))
    if out.get("diff_vs_baseline"):
        d = out["diff_vs_baseline"]
        print(f"[live-matrix] vs baseline: {len(d.get('regressions', []))} regressions, "
              f"{len(d.get('improvements', []))} improvements")
        for r in d.get("regressions", [])[:10]:
            print(f"[live-matrix]   REGRESSED {r}")
        for r in d.get("improvements", [])[:10]:
            print(f"[live-matrix]   improved  {r}")


def _diff_against(old: dict, new: dict) -> dict:
    def keyed(cells):
        return {(c["model"], c["setting"], r["id"]): r.get("passed") for c in cells for r in c["results"]}
    a, b = keyed(old.get("cells", [])), keyed(new.get("cells", []))
    regressions, improvements = [], []
    for k in set(a) & set(b):
        if a[k] and not b[k]:
            regressions.append(f"{Path(k[0]).name}/{k[1]}/{k[2]}")
        elif b[k] and not a[k]:
            improvements.append(f"{Path(k[0]).name}/{k[1]}/{k[2]}")
    return {"regressions": sorted(regressions), "improvements": sorted(improvements)}


class TestLiveMatrix:
    def test_cell(self, request, tmp_path, monkeypatch, live_report, live_cell, live_model_idx):
        from stet.core.text_utils import CorrectionOutcome

        mode = _mode(request)
        if mode is None:
            pytest.skip("live matrix is opt-in: re-run with --run-live (or --run-live=full)")
        # resolve model list from the REAL user config
        import stet.core.config as config_mod
        real_file = Path(config_mod.CONFIG_FILE)
        real_data = json.loads(real_file.read_text(encoding="utf-8")) if real_file.exists() else {}
        models = _resolve_models(real_data.get("model_path", ""))
        if live_model_idx >= len(models):
            pytest.skip("no model configured (set model_path or STET_LIVE_MODELS)")
        model_path = models[live_model_idx]
        if not Path(model_path).exists():
            pytest.skip(f"model file not found: {model_path}")

        cfg = _build_cfg(model_path, monkeypatch, tmp_path)
        cell = SETTING_CELLS[live_cell]
        for k, v in cell["overrides"].items():
            cfg.set(k, v)

        mgr = _get_manager(cfg, live_cell)
        if not mgr.is_loaded():
            ok = mgr.load_model(**cell["server_kwargs"])
            if not ok or not mgr.is_loaded():
                pytest.fail(f"server failed to load {model_path} [{live_cell}] — "
                            "backend regression or bad model path")
        # sampling overrides apply live per request even on a reused server
        for k, v in cell["overrides"].items():
            cfg.set(k, v)
        base_url = f"http://{cfg.get('server_host', '127.0.0.1')}:{cfg.get('server_port', 8080)}"

        cases = CORPUS if mode == "full" else [c for c in CORPUS if c.get("smoke")]
        results = []
        for case in cases:
            prev_terms = cfg.get("protected_terms", [])
            t0 = datetime.now(timezone.utc)
            try:
                if case.get("protected_terms") is not None:
                    cfg.set("protected_terms", case["protected_terms"])
                res = mgr.correct_text_patch(case["input"], strength=case["strength"])
                outcome = getattr(res.outcome, "name", str(res.outcome))
                if outcome == CorrectionOutcome.MODEL_UNAVAILABLE.name:
                    pytest.fail(f"model became unavailable mid-cell [{live_cell}]")
                out = res.text or ""
            finally:
                cfg.set("protected_terms", prev_terms)
            latency = (datetime.now(timezone.utc) - t0).total_seconds()

            checks = {
                "contains": [s for s in case["must_contain"] if s not in out],
                "leaked": [s for s in case["must_not_contain"] if s in out],
            }
            similarity = round(difflib.SequenceMatcher(None, case["input"], out).ratio(), 3)
            judge = _judge(base_url, case["strength"], case["input"], out) if case["judge"] else None
            if judge is not None:
                # Guard against judge hallucination (observed on small QAT models:
                # fixed ORIGINAL typos reported as "introduced"). A claimed new
                # error that is not even present in the output cannot be real.
                out_low = out.lower()
                verified = [e for e in judge["introduced_errors"]
                            if isinstance(e, str) and e.lower() in out_low
                            and "judge-error" not in e.lower()]
                judge["unverified_claims"] = [e for e in judge["introduced_errors"] if e not in verified]
                judge["introduced_errors"] = verified
                # Gate on the judge's verifiable signals (meaning flag +
                # grounded new errors), NOT the 1-5 number: small-model scores
                # are noisy (perfect outputs scored 1 and 3 in validation).
                # The score remains a ranking signal via mean_score.
                judge_ok = judge["preserved_meaning"] and not verified
            else:
                judge_ok = True
            passed = not checks["contains"] and not checks["leaked"] and judge_ok
            results.append({
                "id": case["id"], "category": case["category"], "strength": case["strength"],
                "output": out[:2000], "latency_s": round(latency, 1),
                "similarity": similarity, "checks": checks, "judge": judge, "passed": passed,
            })

        scores = [r["judge"]["score"] for r in results if r["judge"] and r["judge"]["score"] > 0]
        cell_rec = {
            "model": model_path, "setting": live_cell,
            "pass_rate": round(sum(1 for r in results if r["passed"]) / max(1, len(results)), 3),
            "mean_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
            "results": results,
        }
        live_report["cells"].append(cell_rec)
        failures = [r["id"] for r in results if not r["passed"]]
        assert not failures, f"[{Path(model_path).name}/{live_cell}] failed cases: {failures}"
