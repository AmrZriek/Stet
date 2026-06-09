import os
import re
import subprocess

from stet.constants import LLAMA_CPP_DIR, SCRIPT_DIR, SERVER_EXE, WINDOWS


def _model_size_billions(model_path: str) -> float | None:
    """Parse the parameter count in billions from a GGUF filename.

    Examples:
        'qwen2.5-3b-instruct-q4_k_m.gguf'     → 3.0
        'gemma-4-E2B-it-UD-Q4_K_XL.gguf'      → 2.0
        'gemma3-270m-grammar-q8_0.gguf'       → 0.27
        'Llama-3.2-1B-Instruct-Q4_K_M.gguf'   → 1.0
        'phi-mini-3.8b-Q4.gguf'               → 3.8

    Returns None if no size marker is found. Used for UI-side sanity warnings
    — a 270M model will produce tokenizer garbage in patch mode, and we want
    to warn the user upfront rather than after a bad correction.
    """
    if not model_path:
        return None
    name = os.path.basename(model_path).lower()
    # Match patterns like "3b", "2.5b", "E2B" (effective 2B), "270m", "1.5m"
    # E-prefix is used by Google's "effective" size branding (E2B = ~2B effective)
    m = re.search(r"(?:^|[^a-z0-9])e?(\d+(?:\.\d+)?)([bm])(?:[^a-z]|$)", name)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    return value if unit == "b" else value / 1000.0


_MIN_RELIABLE_MODEL_B = 1.0


def _find_shipped_llama_server() -> str:
    """Locate a llama-server binary shipped alongside the app.

    Release ZIPs extract to a folder containing Stet.exe plus a
    sibling directory like `llama-b8728-bin-win-cuda-12.4-x64/` that holds
    `llama-server.exe`. Users shouldn't have to point Settings at it manually —
    if we can find it next to the app, auto-use it. Searched locations, in
    priority order:
      1. Legacy `llama_cpp/` folder (previous release layout)
      2. Any sibling folder matching `llama*` containing the server binary
    Returns an empty string if nothing is found.
    """
    # Legacy location first — if someone upgrades in place, keep their setup
    legacy = LLAMA_CPP_DIR / SERVER_EXE
    if legacy.exists():
        return str(legacy)
    # Scan SCRIPT_DIR for any folder that looks like an unpacked llama.cpp build
    try:
        for entry in SCRIPT_DIR.iterdir():
            if entry.is_dir() and "llama" in entry.name.lower():
                candidate = entry / SERVER_EXE
                if candidate.exists():
                    return str(candidate)
    except Exception:
        pass
    return ""


_COMPILED_THINKING_PATTERNS = [
    re.compile(r"<think>.*?</think>", re.DOTALL),
    re.compile(r"<thinking>.*?</thinking>", re.DOTALL),
    re.compile(r"<reasoning>.*?</reasoning>", re.DOTALL),
]

_COMPILED_UNCLOSED_PATTERNS = [
    re.compile(r"<think>.*", re.DOTALL),
    re.compile(r"<thinking>.*", re.DOTALL),
    re.compile(r"<reasoning>.*", re.DOTALL),
]

_PREAMBLE_PATTERNS = [
    r"^(?:Here(?:\'s| is) the corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:Sure[,!]? [Hh]ere(?:\'s| is) the corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:Corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:The corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:I(?:\'ve| have) corrected the (?:text|text for you)[:\.]?\s*\n?)",
    r"^(?:Below is the corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:This is the corrected (?:text|version)[:\.]?\s*\n?)",
    r"^(?:I\'ve proofread and refined the text[:\.]?\s*\n?)",
    r"^(?:I\'ve made the following corrections[:\.]?\s*\n?)",
    r"^\*\*Corrected(?: text)?\*\*[:\.]?\s*\n?",
    r"^#+\s*Corrected(?: text)?[:\.]?\s*\n?",
    r"^[-*]{3,}\s*\n?",
    r"^(?:Here are the corrections?[:\.]?\s*\n?)",
    r"^(?:The refined (?:text|version)[:\.]?\s*\n?)",
    r"^(?:I\'ve reviewed and corrected[:\.]?\s*\n?)",
    r"^(?:I\'ve proofread (?:and refined )?your text[:\.]?\s*\n?)",
    r"^(?:Here is the refined (?:text|version)[:\.]?\s*\n?)",
    r"^(?:The text has been corrected[:\.]?\s*\n?)",
    r"^(?:Your text,? corrected[:\.]?\s*\n?)",
]

_COMPILED_PREAMBLES = [re.compile(p, re.IGNORECASE) for p in _PREAMBLE_PATTERNS]


def has_nvidia() -> bool:
    """Detect NVIDIA GPU. Tries nvidia-smi first, then falls back to WMI."""
    # Method 1: nvidia-smi (fast, works when drivers are installed)
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=5,
            **({"creationflags": 0x08000000} if WINDOWS else {}),
        )
        if r.returncode == 0 and bool(r.stdout.strip()):
            return True
    except Exception:
        pass

    # Method 2: WMI query (works even when nvidia-smi is not in PATH)
    if WINDOWS:
        try:
            r = subprocess.run(
                ["wmic", "path", "win32_videocontroller", "get", "name"],
                capture_output=True,
                text=True,
                timeout=10,
                creationflags=0x08000000,
            )
            if r.returncode == 0:
                output = r.stdout.lower()
                if any(kw in output for kw in ("nvidia", "geforce", "rtx", "gtx")):
                    return True
        except Exception:
            pass

    return False
