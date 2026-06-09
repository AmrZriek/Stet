"""
Stet v1.0
==================
Instant AI-powered text correction with a premium dark UI.

Architecture
------------
- Autocorrect      : lightweight LLM via llama.cpp (loaded at boot, instant)
- Chat / rewrite   : separate LLM via llama.cpp (lazy-load, unloads after idle)
- GUI              : PyQt6, frameless dark-navy theme
- Hotkey           : global keyboard hook → clipboard copy → correction popup

Cross-platform: Windows / macOS / Linux.
Single-file deployment (plus llama_cpp/ binary folder and LLM model .gguf).
"""

APP_VERSION = "1.0.0"

# ── stdlib ─────────────────────────────────────────────────────────────────
import os
import sys
from pathlib import Path

# ── Qt HiDPI env vars must be set before importing PyQt6 ───────────────────
os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "1")
os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")

# ── third-party ─────────────────────────────────────────────────────────────


# ── Platform detection ───────────────────────────────────────────────────────
WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"
if WINDOWS:
    pass

# ── Portable base directory ──────────────────────────────────────────────────
# When frozen (PyInstaller) or compiled (Nuitka), exe lives at project root.
# When running from source, constants.py is inside stet/ — go up one
# level so SCRIPT_DIR always points to the project root.
# NOTE: Nuitka does NOT set sys.frozen (that's PyInstaller).  A reliable
# cross-packager check is whether the running executable is a Python
# interpreter or the app itself.
import builtins
_exe_stem = Path(sys.executable).stem.lower()
_is_compiled = (
    getattr(sys, "frozen", False)
    or hasattr(builtins, "__nuitka_binary_exe")
    or not _exe_stem.startswith("python")
)
if _is_compiled:
    exe_path = Path(sys.executable).resolve()
    if MACOS and ".app/Contents/MacOS" in exe_path.as_posix():
        SCRIPT_DIR = exe_path.parent.parent.parent.parent.resolve()
    else:
        SCRIPT_DIR = exe_path.parent.resolve()
else:
    SCRIPT_DIR = Path(__file__).parent.parent.resolve()

CONFIG_FILE = SCRIPT_DIR / "config.json"
LLAMA_CPP_DIR = SCRIPT_DIR / "llama_cpp"
LOG_FILE = SCRIPT_DIR / "server_log.txt"
DEBUG_LOG = SCRIPT_DIR / "app_debug.log"

SERVER_EXE = "llama-server.exe" if WINDOWS else "llama-server"

GITHUB_RELEASES_API = "https://api.github.com/repos/AmrZriek/Stet/releases/latest"

# ── llama.cpp backend auto-download ──────────────────────────────────────────
# The llama-server binaries + CUDA runtime are downloaded on first run instead
# of bundled in the installer (keeps installer under 120 MB to avoid AV flags).
LLAMA_BACKEND_VERSION = "b9577"
_LLAMA_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BACKEND_VERSION}"
LLAMA_BACKEND_URLS = {
    "llama": f"{_LLAMA_BASE}/llama-{LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64.zip",
    "cuda": f"{_LLAMA_BASE}/cudart-llama-bin-win-cuda-12.4-x64.zip",
}
LLAMA_BACKEND_HASHES = {
    "llama": "49A7FFB9E68A6306A2CB0A7284D1565049CD978C3B130EAA1D2197E471F4F5D2",
    "cuda": "8C79A9B226DE4B3CACFD1F83D24F962D0773BE79F1E7B75C6AF4DED7E32AE1D6",
}
LLAMA_BACKEND_DIR = f"llama-{LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64"


DEFAULT_CONFIG: dict = {
    # llama.cpp
    "llama_server_path": str(LLAMA_CPP_DIR / SERVER_EXE),
    "model_path": "",
    "server_host": "127.0.0.1",
    "server_port": 8080,
    "context_size": 12800,
    "gpu_layers": 99,
    "temperature": 0.1,
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
    "repeat_penalty": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "keep_model_loaded": True,
    "idle_timeout_seconds": 300,
    "recent_models": [],
    # Chat model
    "chat_model_path": "",
    "chat_use_separate_model": False,
    "chat_keep_loaded": False,
    "chat_idle_timeout_seconds": 60,
    # Hotkeys
    "hotkeys": [
        {"shortcut": "f9", "mode": "panel", "strength": "full_correction"},
        {"shortcut": "f10", "mode": "silent", "strength": "spelling_only"},
        {"shortcut": "shift+f9", "mode": "panel", "strength": "rewrite_polish"},
    ],
    # Misc
    "system_prompt": "",
    # Correction delivery: always "patch" (stream mode removed from settings UI)
    "correction_method": "patch",
    # Fallback default strength when no per-hotkey strength is set.
    #   "spelling_only" — typos only;  "full_correction" — full grammar/capitalization/punctuation;
    #   "rewrite_polish" — rewrite for clarity, concision, and impact.
    "streaming_strength": "full_correction",
    # Custom templates: list of {"name": str, "prompt": str}
    "custom_templates": [],
    # Chat interaction mode: "single" (each message replaces diff view) or "conversation" (persistent chat history)
    "chat_mode": "conversation",
    # Correction modes: configurable prompt + hallucination threshold per strength.
    # Index 0 = conservative/spelling_only, 1 = smart_fix/full_correction, 2 = aggressive/rewrite_polish.
    "correction_modes": [
        {
            "name": "Spelling Only",
            "prompt": "You are a precise, spelling-only text-correction engine. You receive one sentence (or short passage) between <<<START>>> and <<<END>>> markers.\n\n- Fix ONLY clear misspellings, typos, and accidental keyboard slips (e.g., \"libary\" -> \"library\").\n- Do NOT change capitalization, punctuation, grammar, word choice, or word ordering.\n- Repeated words and repeated sentences are user content; they must not be removed.",
            "hallucination_threshold": 0.4,
            "builtin": True,
        },
        {
            "name": "Full Correction",
            "prompt": "You are a precise grammar and spelling correction engine. You receive one sentence (or short passage) between <<<START>>> and <<<END>>> markers.\n\n- Fix typos, spelling, grammar, punctuation, and capitalization.\n- Improve awkward grammar only when needed for natural, clear writing.\n- Preserve the author's wording, tone, and intent; do NOT rewrite for style or polish.\n- Repeated words and repeated sentences are user content; they must not be removed unless clearly accidental.\n- NEVER alter intentional styling: preserve ALL CAPS words, initialisms (NASA, USA), and Title Case exactly.",
            "hallucination_threshold": 1.0,
            "builtin": True,
        },
        {
            "name": "Rewrite & Polish",
            "prompt": "You are an expert editor and text-correction engine. You receive one sentence (or short passage) between <<<START>>> and <<<END>>> markers.\n\n- Fix typos, spelling, grammar, punctuation, and capitalization.\n- Improve clarity, conciseness, flow, and overall impact without changing the author's voice.\n- Preserve the original tone, formality level, rhythm, and casual lingo; do not make casual text formal, and do not make professional text casual.\n- Keep contractions, slang, directness, humor, enthusiasm, and emphasis when they are part of the original voice.\n- Change word choice only when it makes the writing clearer or stronger while preserving the author's core intent, claims, and meaning.\n- Repeated words and repeated sentences are user content; they must not be removed unless clearly accidental.\n- NEVER add new facts, examples, explanations, greetings, sign-offs, or commentary.",
            "hallucination_threshold": 1.0,
            "builtin": True,
        },
        {
            "name": "Custom Patch",
            "prompt": "You are a text-correction engine. You receive one sentence (or short passage) between <<<START>>> and <<<END>>> markers.\n\n- Fix typos, spelling, grammar, punctuation, and capitalization.\n- Improve clarity, conciseness, and overall quality.\n- Preserve the author's core intent and meaning.",
            "hallucination_threshold": 1.0,
            "builtin": False,
            "enabled": False,
        },
    ],
}

DEFAULT_TEMPLATES: list[dict[str, str]] = [
    {
        "name": "Email Polish",
        "prompt": "Fix spelling and grammar. Ensure a professional, polite, and punchy tone suitable for business correspondence. Keep it concise. Do not add greetings, closings, or extra pleasantries. Output ONLY the corrected text without preamble or explanation.",
    },
    {
        "name": "Executive Briefing",
        "prompt": "Structure the text into a crisp, professional, and highly actionable executive briefing. Use concise bullet points where appropriate. Retain all key dates, values, and names. Output ONLY the final briefing without preamble or explanation.",
    },
    {
        "name": "Fix Grammar",
        "prompt": "Fix all spelling, punctuation, and grammar errors. Preserve the original phrasing, vocabulary, and meaning exactly. Do not rephrase or polish. Output ONLY the corrected text without preamble or explanation.",
    },
    {
        "name": "Rewrite & Polish",
        "prompt": "Rewrite the text to be clearer, smoother, and more impactful while preserving the author's original tone, formality level, and casual lingo. Do not make casual writing formal or professional writing casual. Improve flow, sentence structure, and word choice only where it strengthens the existing voice. Output ONLY the polished text without preamble or explanation.",
    },
]
