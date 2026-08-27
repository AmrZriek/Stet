"""Generic chat-template sanitizer for hard-primed thinking models.

Some chat templates (e.g. Liquid LFM 2.5) ALWAYS emit a ``<think>`` open-tag
at the generation prompt — even when ``--reasoning off`` and per-request
``think: false`` are set — so every request burns its budget on reasoning
with empty content. The generic strategy keeps the base suppression
(``--reasoning off`` + ``--reasoning-budget`` + strip regexes) and adds a
sanitized copy of the template *only* when the embedded template
hard-primes thinking.

A "hard prime" is a think open-tag literal (``<think``, ``<thinking``,
``<reasoning``) that appears OUTSIDE:

- ``{% if enable_thinking %}`` / ``{%- if enable_thinking -%}`` blocks
- ``message.thinking`` handling (data-driven, never present with thinking off)
- ``{%- generation -%}``/``{{ generation }}``-guarded emission
- Jinja comments ``{# ... #}``

The heuristic is deliberately conservative: any parse difficulty or unknown
conditional returns "not a hard prime" (False / unchanged template). The
base suppression already handles gated/conditional thinking, so failing
conservatively only means we skip the (optional) template override.
"""

import hashlib
import re
import tempfile
from pathlib import Path

from stet.core.utils import log

# Order matters: <thinking must be checked before <think (prefix overlap).
_THINK_OPEN = ("<think", "<thinking", "<reasoning", "<thought", "<|thought", "<|start_of_thought", "[think", "[thought")

# Matches a think open-tag including its closing '>' when present (the
# <thinking must be tried before <think due to prefix overlap).
_THINK_TAG_RE = re.compile(
    r"<(?:thinking|reasoning|think|thought)(?![a-z0-9])>?"
    r"|<\|(?:thought|start_of_thought|end_of_thought)\|>"
    r"|\[(?:THINK|THOUGHT|REASONING)\]",
    re.IGNORECASE,
)

_QUOTED_STRING_RE = re.compile(r"""("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')""")

# Jinja statements that open/close a gated block (body emission may be
# conditional or data-driven -> treat think tags inside as non-primes).
_GATED_OPENS = {
    "for": True,
    "macro": True,
    "block": True,
    "filter": True,
    "call": True,
    "with": True,
    "raw": True,
}
_GATED_ENDS = {
    "endfor": "for",
    "endmacro": "macro",
    "endblock": "block",
    "endfilter": "filter",
    "endcall": "call",
    "endwith": "with",
    "endraw": "raw",
}

_STATEMENT_RE = re.compile(
    r"^-\s*("
    r"if|elif|else|endif"
    r"|for|endfor|macro|endmacro|block|endblock|filter|endfilter"
    r"|call|endcall|with|endwith|raw|endraw"
    r"|generation|endgeneration"
    r")\b"
)

# Expression referencing these must be left alone (generation-guarded
# emission, message.thinking handling, runtime thinking logic).
_EXPR_SKIP_RE = re.compile(
    r"\bgeneration\b|message\s*[\.\[]|(?<!<)\bthinking\b|(?<!<)\breasoning\b"
)

_STATICALLY_FALSE = {"false", "0", "none", "null", "''", '""', "not true"}


def _stmt_body(stmt: str) -> str:
    """Extract the condition/expression of a Jinja statement, stripping the
    ``-`` trim markers that ``{%- ... -%}`` leaves behind."""
    body = stmt[m.end():] if (m := _STATEMENT_RE.match(stmt)) else stmt
    return body.strip().strip("-").strip()


def _is_protective_condition(cond: str) -> bool:
    """True when an if-condition gates/prevents thinking emission.

    Conservative: unknown conditions protect (i.e. the think tag inside is
    treated as a possible non-prime). Any condition indicating generation prompt
    emission (e.g. add_generation_prompt) without explicit thinking gating is
    treated as a generation prompt context.
    """
    c = cond.strip()
    if not c:
        return True  # cannot parse -> conservative
    cl = c.lower()
    if "enable_thinking" in cl or "thinking" in cl or "reasoning" in cl:
        return True
    if cl in _STATICALLY_FALSE:
        return True
    if "add_generation_prompt" in cl and not ("enable_thinking" in cl or "think" in cl):
        return False
    return True  # everything else -> uncertain -> conservative


def _strip_think_from_string_literals(expr: str) -> str | None:
    """Strip think open-tags from quoted string literals in an expression.

    Returns None when the expression cannot be safely rewritten (unbalanced
    quotes, or stripping would leave a dangling operator) so callers can bail
    conservatively.
    """
    parts = []
    pos = 0
    found_literals = False
    for m in _QUOTED_STRING_RE.finditer(expr):
        found_literals = True
        parts.append(expr[pos:m.start()])
        literal = m.group(0)
        inner = literal[1:-1]
        new_inner = _THINK_TAG_RE.sub("", inner)
        if new_inner != inner:
            if new_inner.strip():
                literal = literal[0] + new_inner + literal[-1]
            else:
                literal = ""  # quotes enclosed only the prime -> strip them
        parts.append(literal)
        pos = m.end()
    parts.append(expr[pos:])
    if not found_literals:
        return expr
    rewritten = "".join(parts)
    core = rewritten.strip(" -")
    if core == "":
        return ""  # whole expression was the prime -> drop the emission
    if core.endswith(("+", "*", "/", "|", "-")) or core.startswith(
        ("+", "*", "/", "|")
    ):
        return None  # stripping broke the expression -> conservative bail
    return rewritten


def _sanitize_template_text(template: str) -> str | None:
    """Return the template with hard-primed think tags stripped.

    Returns None (conservative) on any parse difficulty; otherwise returns
    the (possibly unchanged) template byte-for-byte.
    """
    out = []
    i = 0
    n = len(template)
    # Stack of open gated frames: (kind, protective).
    frames: list[tuple[str, bool]] = []

    while i < n:
        # Jinja comment: consume whole span, never inspect inside.
        if template.startswith("{#", i):
            end = template.find("#}", i + 2)
            if end == -1:
                return None
            out.append(template[i:end + 2])
            i = end + 2
            continue

        # Expression: only touch quoted string literals, and only when not
        # inside a protective frame or a generation/message-guarded emission.
        if template.startswith("{{", i):
            end = template.find("}}", i + 2)
            if end == -1:
                return None
            expr = template[i + 2:end]
            protected = any(p for _kind, p in frames)
            if not protected and not _EXPR_SKIP_RE.search(expr):
                expr_new = _strip_think_from_string_literals(expr)
                if expr_new is None:
                    return None
                if expr_new.strip(" -") == "":
                    i = end + 2  # emission was only the prime -> drop it
                    continue
                out.append("{{" + expr_new + "}}")
            else:
                out.append(template[i:end + 2])
            i = end + 2
            continue

        # Statement: update frame stack.
        if template.startswith("{%", i):
            end = template.find("%}", i + 2)
            if end == -1:
                return None
            stmt = template[i + 2:end].strip()
            out.append(template[i:end + 2])
            m = _STATEMENT_RE.match(stmt)
            if m:
                kind = m.group(1)
                if kind == "if":
                    frames.append(("if", _is_protective_condition(_stmt_body(stmt))))
                elif kind == "elif":
                    if frames and frames[-1][0] in ("if", "elif", "else"):
                        frames[-1] = (
                            "elif",
                            _is_protective_condition(_stmt_body(stmt)),
                        )
                    else:
                        return None  # malformed nesting -> conservative
                elif kind == "else":
                    if frames and frames[-1][0] in ("if", "elif"):
                        # Inherit the enclosing if's protection (conservative).
                        frames[-1] = ("else", frames[-1][1])
                    else:
                        return None
                elif kind == "endif":
                    if frames and frames[-1][0] in ("if", "elif", "else"):
                        frames.pop()
                    else:
                        return None  # malformed nesting -> conservative
                elif kind in _GATED_OPENS:
                    frames.append((kind, True))
                elif kind in _GATED_ENDS:
                    if frames and frames[-1][0] == _GATED_ENDS[kind]:
                        frames.pop()
                    else:
                        return None
                elif kind == "generation":
                    frames.append(("generation", True))
                elif kind == "endgeneration":
                    if frames and frames[-1][0] == "generation":
                        frames.pop()
                    else:
                        return None
            i = end + 2
            continue

        # Literal text: find the next template token, strip think tags only
        # when not inside a protective frame.
        next_token = n
        for marker in ("{{", "{%", "{#"):
            j = template.find(marker, i)
            if j != -1:
                next_token = min(next_token, j)
        text = template[i:next_token]
        if not any(p for _kind, p in frames):
            text = _THINK_TAG_RE.sub("", text)
        out.append(text)
        i = next_token

    if frames:
        return None  # unclosed gated block -> parse difficulty -> conservative
    return "".join(out)


def detect_hard_prime(template: str) -> bool:
    """Return True when *template* hard-primes thinking at the generation
    prompt (a think open-tag outside any gating construct).

    Conservative: returns False on any parse difficulty or unknown
    conditional. The base suppression still handles gated thinking, so a
    False negative only skips the optional template override.
    """
    if not isinstance(template, str) or not template:
        return False
    try:
        sanitized = _sanitize_template_text(template)
    except Exception:
        return False
    return sanitized is not None and sanitized != template


def sanitize_template(template: str) -> str | None:
    """Strip the hard-primed literal think open-tag (and its surrounding
    string-literal quotes) from the generation-prompt emission only.

    Everything gated or guarded is left byte-identical. Returns None on any
    exception or malformed input — never raises.
    """
    if not isinstance(template, str) or not template:
        return None
    try:
        return _sanitize_template_text(template)
    except Exception:
        return None


def write_sanitized_template(template: str, model_path: str) -> Path:
    """Write *template* to the tempdir and return its path.

    Location: ``<tempdir>/stet_templates/<sha256(model_path+template)[:16]>.jinja``.
    The deterministic digest means repeated calls for the same model/template
    reuse the same file.
    """
    digest = hashlib.sha256(
        f"{model_path}{template}".encode("utf-8")
    ).hexdigest()[:16]
    out_dir = Path(tempfile.gettempdir()) / "stet_templates"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{digest}.jinja"
    out_path.write_text(template, encoding="utf-8")
    log(f"[TEMPLATE] Wrote sanitized chat template: {out_path}")
    return out_path
