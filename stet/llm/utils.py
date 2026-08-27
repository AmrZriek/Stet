import os
import re
import subprocess

from stet.constants import LLAMA_CPP_DIR, MACOS, SCRIPT_DIR, SERVER_EXE, WINDOWS


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


def _find_mtp_draft_model(model_path: str) -> str | None:
    """Find a companion MTP draft model in the same directory as model_path.

    Detects separated MTP draft heads (e.g. ``mtp-gemma-4-E2B-it.gguf``,
    ``qwen-draft.gguf``, ``*-assistant*.gguf``) shipped alongside the base model.
    """
    if not model_path or not os.path.exists(model_path):
        return None
    try:
        model_dir = os.path.dirname(os.path.abspath(model_path))
        model_name = os.path.basename(model_path).lower()
        candidates = []
        for entry in os.scandir(model_dir):
            if not entry.is_file() or not entry.name.lower().endswith(".gguf"):
                continue
            fn_lower = entry.name.lower()
            if fn_lower == model_name:
                continue
            # Match MTP draft patterns: mtp-*, *-mtp*, *-assistant*, *-draft*, draft-*
            if (
                fn_lower.startswith("mtp-")
                or "-mtp" in fn_lower
                or "_mtp" in fn_lower
                or "assistant" in fn_lower
                or "draft" in fn_lower
            ):
                candidates.append(entry.path)
        if candidates:
            # Prioritize files explicitly containing "mtp"
            for c in candidates:
                if "mtp" in os.path.basename(c).lower():
                    return c
            return candidates[0]
    except Exception:
        pass
    return None


def _supports_mtp(model_path: str) -> bool:
    """Check whether a GGUF model supports Multi-Token Prediction.

    Detection strategy (in priority order):
    1. Companion MTP draft model present in the same directory (e.g.
       ``mtp-*.gguf`` or ``*-assistant*.gguf``).
    2. GGUF metadata key ``{arch}.nextn_predict_layers`` — written by
       llama.cpp's converter for models with built-in MTP draft heads.
    3. Tensor name containing ``nextn_proj`` or ``nextn.`` — present in
       MTP weight tensors even when the metadata key is stripped.
    4. Filename heuristic — models shipped with ``-mtp`` or ``_mtp``
       in the name (e.g. ``qwen3-4b-mtp-q4_k_m.gguf``).
    """
    if not model_path or not os.path.exists(model_path):
        return False
    try:
        if os.path.getsize(model_path) < 1024:
            return False
        # Sibling draft model detection
        if _find_mtp_draft_model(model_path) is not None:
            return True
        name_lower = os.path.basename(model_path).lower()
        if "-mtp" in name_lower or "_mtp" in name_lower:
            return True
        with open(model_path, "rb") as f:
            chunk = f.read(1024 * 1024 * 8)
            chunk_lower = chunk.lower()
            return (
                b"nextn_predict_layers" in chunk_lower
                or b"nextn_proj" in chunk_lower
                or b"nextn." in chunk_lower
            )
    except Exception:
        return False


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
    # macOS bundles are selected through the architecture/backend manifest so
    # an Intel or translated process cannot accidentally pick another build.
    # Windows and the historical non-macOS scan below intentionally retain
    # their existing behavior.
    if MACOS:
        try:
            from stet.llm.backend_manager import BackendManager

            resolved = BackendManager().resolve_bundled_backend(
                legacy_dir=LLAMA_CPP_DIR,
                executable_name=SERVER_EXE,
            )
            if resolved:
                return resolved
        except Exception:
            pass

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
    re.compile(r"<think(?!ing)[^>]*>.*?</think>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<thinking[^>]*>.*?</thinking>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<reasoning[^>]*>.*?</reasoning>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<thought[^>]*>.*?</thought>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\|thought\|>.*?</\|thought\|>", re.DOTALL),
    re.compile(r"<\|start_of_thought\|>.*?</\|end_of_thought\|>", re.DOTALL),
    re.compile(r"\[(?:THINK|THOUGHT|REASONING)\].*?\[/(?:THINK|THOUGHT|REASONING)\]", re.DOTALL | re.IGNORECASE),
]

_COMPILED_UNCLOSED_PATTERNS = [
    re.compile(r"<think(?!ing)[^>]*>.*", re.DOTALL | re.IGNORECASE),
    re.compile(r"<thinking[^>]*>.*", re.DOTALL | re.IGNORECASE),
    re.compile(r"<reasoning[^>]*>.*", re.DOTALL | re.IGNORECASE),
    re.compile(r"<thought[^>]*>.*", re.DOTALL | re.IGNORECASE),
    re.compile(r"<\|thought\|>.*", re.DOTALL),
    re.compile(r"<\|start_of_thought\|>.*", re.DOTALL),
    re.compile(r"\[(?:THINK|THOUGHT|REASONING)\].*", re.DOTALL | re.IGNORECASE),
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

    # Method 2: CIM / WMI query (works even when nvidia-smi is not in PATH, and on Win11 24H2+ where wmic is removed)
    if WINDOWS:
        try:
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | Select-Object -ExpandProperty Name",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=0x08000000,
            )
            if r.returncode == 0:
                output = r.stdout.lower()
                if any(kw in output for kw in ("nvidia", "geforce", "rtx", "gtx")):
                    return True
        except Exception:
            pass

        # Fallback for older Windows where wmic is available
        try:
            r = subprocess.run(
                ["wmic", "path", "win32_videocontroller", "get", "name"],
                capture_output=True,
                text=True,
                timeout=5,
                creationflags=0x08000000,
            )
            if r.returncode == 0:
                output = r.stdout.lower()
                if any(kw in output for kw in ("nvidia", "geforce", "rtx", "gtx")):
                    return True
        except Exception:
            pass

    return False


def query_free_vram_mb() -> int | None:
    """Query available free VRAM across NVIDIA GPUs in megabytes."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=5,
            **({"creationflags": 0x08000000} if WINDOWS else {}),
        )
        if r.returncode == 0 and r.stdout.strip():
            lines = [line.strip() for line in r.stdout.strip().splitlines() if line.strip()]
            if lines:
                # Return maximum free VRAM among available GPUs
                values = [int(line.split()[0]) for line in lines if line.split()[0].isdigit()]
                if values:
                    return max(values)
    except Exception:
        pass
    return None


def estimate_model_vram_mb(model_path: str, ctx_size: int = 4096) -> int | None:
    """Estimate required VRAM in megabytes for a given GGUF model and context size."""
    if not model_path or not os.path.exists(model_path):
        return None
    try:
        file_size_bytes = os.path.getsize(model_path)
        file_size_mb = file_size_bytes / (1024 * 1024)
        # Model weights + ~15% overhead for KV cache, activations, CUDA context
        kv_overhead_mb = (ctx_size * 0.1) if ctx_size > 0 else 256
        return int(file_size_mb * 1.15 + kv_overhead_mb)
    except Exception:
        return None


def _is_valid_gguf(path) -> bool:
    """Verify that a path is a valid GGUF file.

    Checks if the file exists, is at least 10MB (to avoid matching empty/incomplete files),
    and starts with the 'GGUF' magic bytes.
    """
    try:
        from pathlib import Path
        p = Path(path) if isinstance(path, (str, Path)) else path
        if not p.is_file():
            return False
        if p.stat().st_size < 10 * 1024 * 1024:
            return False
        with open(p, "rb") as f:
            magic = f.read(4)
            return magic == b"GGUF"
    except Exception:
        return False

