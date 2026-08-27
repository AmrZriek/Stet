"""Doc anchor lint — Phase 5 guard (docs/ANCHORING_PLAN.md §5).

Verifies that every comma-pair code anchor in the pipeline docs

    (`symbol`, `path/to/file.py`)

references a symbol that actually exists in the referenced file, and bans
line-number anchors (``file.py:N``) outright.

Scope is pinned to exactly three docs (ANCHORING_PLAN §5.1). Narrative
history docs (SESSION_LOG.md etc.) intentionally keep frozen-in-time line
refs and are NOT scanned.

Checks:
1. Pair existence (prose only; fenced blocks stripped): every pair's symbol
   must exist in the target file's AST universe — functions, methods,
   classes, module/class-level Name assignments (incl. pyqtSignal class
   attrs) at ANY depth, and attribute-assignment names (``self.x = ...``).
2. Inverse hygiene (FULL text incl. fences): no ``file.py:N(-N)`` patterns
   may remain anywhere in the scoped docs.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCOPED_DOCS = [
    ROOT / "docs" / "PROJECT_PIPELINE.md",
    ROOT / "docs" / "PROJECT_PIPELINE_FRONTEND.md",
    ROOT / "docs" / "architecture.md",
]

PAIR_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`,[ \t]*`([\w/\\]+\.py)`")
LINE_ANCHOR_RE = re.compile(r"[\w/\\]+\.py:\d+(?:-\d+)?")
FENCE_RE = re.compile(r"```.*?```", re.S)


def strip_fences(text: str) -> str:
    return FENCE_RE.sub(lambda m: "`" * len(m.group()), text)


_BASENAME_INDEX: dict[str, list] = {}


def _basename_candidates(basename: str) -> list:
    if not _BasenameIndex_built():
        for p in (ROOT / "stet").rglob("*.py"):
            _BASENAME_INDEX.setdefault(p.name, []).append(p)
    return _BASENAME_INDEX.get(basename, [])


def _BasenameIndex_built() -> bool:
    return bool(_BASENAME_INDEX) or not any((ROOT / "stet").glob("*.py"))


def resolve_file(ref: str):
    cand = ROOT / ref
    if cand.exists():
        return cand
    cand = ROOT / "stet" / ref
    if cand.exists():
        return cand
    if "/" not in ref and "\\" not in ref:
        # bare filename used by docs for readability — resolve uniquely
        hits = _basename_candidates(ref)
        if len(hits) == 1:
            return hits[0]
    return None


_UNIVERSE_CACHE: dict[Path, set] = {}


def symbol_universe(path: Path) -> set:
    cached = _UNIVERSE_CACHE.get(path)
    if cached is not None:
        return cached
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
                elif isinstance(t, ast.Attribute):
                    names.add(t.attr)
    _UNIVERSE_CACHE[path] = names
    return names


def test_no_line_anchors_remain():
    """Inverse hygiene: line anchors are banned across FULL text (incl. fences)."""
    offenders = []
    for doc in SCOPED_DOCS:
        text = doc.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            for m in LINE_ANCHOR_RE.finditer(line):
                offenders.append(f"{doc.name}:{i}: {m.group(0)}")
    assert not offenders, (
        "Line-number anchors are banned (docs/ANCHORING_PLAN.md §3). "
        "Use (`symbol`, `file.py`) pairs instead:\n  " + "\n  ".join(offenders)
    )


def test_pair_symbols_exist():
    """Every comma-pair anchor resolves to a real symbol in its file."""
    failures = []
    checked = 0
    for doc in SCOPED_DOCS:
        prose = strip_fences(doc.read_text(encoding="utf-8"))
        for m in PAIR_RE.finditer(prose):
            sym, fileref = m.group(1), m.group(2)
            checked += 1
            path = resolve_file(fileref)
            if path is None:
                failures.append(f"{doc.name}: unresolved file '{fileref}' (pair '{sym}')")
                continue
            try:
                universe = symbol_universe(path)
            except SyntaxError as e:
                failures.append(f"{doc.name}: cannot parse {path}: {e}")
                continue
            parts = sym.split(".")
            base = parts[-1]
            ok = base in universe or sym in universe
            if not ok and len(parts) == 2:
                # dotted Class.attr: accept when both halves exist somewhere
                ok = parts[0] in universe and parts[1] in universe
            if not ok:
                failures.append(
                    f"{doc.name}: symbol '{sym}' NOT FOUND in {path.relative_to(ROOT)}"
                )
    assert checked > 100, f"Pair scan suspiciously small ({checked}) — regex drift?"
    assert not failures, (
        f"{len(failures)} broken pair anchor(s):\n  " + "\n  ".join(failures)
    )
