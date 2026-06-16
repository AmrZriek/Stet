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

APP_VERSION = "1.0.1"

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
    "threads": -1,
    "batch_size": 1024,
    "ubatch_size": 512,
    "flash_attn": True,
    "kv_cache_type_k": "q8_0",
    "kv_cache_type_v": "q8_0",
    "mtp_enabled": False,
    "mtp_max_draft": 2,
    "mtp_min_draft": 0,
    "temperature": 0.0,
    "top_k": 40,
    "top_p": 0.95,
    "min_p": 0.05,
    "seed": -1,
    "typical_p": 1.0,
    "tfs_z": 1.0,
    "mirostat": 0,
    "mirostat_tau": 5.0,
    "mirostat_eta": 0.1,
    "repeat_penalty": 1.0,
    "frequency_penalty": 0.0,
    "presence_penalty": 0.0,
    "rope_freq_base": 0.0,
    "rope_freq_scale": 0.0,
    "parallel": 4,
    "threads_batch": -1,
    "keep_model_loaded": True,
    "idle_timeout_seconds": 300,
    "patch_chunk_size": 40,
    "cache_prompt": True,
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
    "custom_templates": [
        {
            "name": "Clean Up Dictation",
            "prompt": (
                "This text was dictated using speech-to-text and contains spoken "
                "artifacts. Clean it up:\n"
                "- Remove filler words (um, uh, ah, like, basically, you know, "
                "I mean, so yeah, kind of, sort of, right, actually, literally, "
                "honestly, essentially, anyway, well, OK so).\n"
                "- Remove stuttered or repeated words and false starts.\n"
                "- Fix run-on sentences — add proper punctuation and split where "
                "natural pauses should be.\n"
                "- Fix obvious STT misspellings and missing punctuation.\n"
                "- Lightly adjust sentence structure ONLY when the original is "
                "genuinely incoherent. Otherwise keep the author's phrasing.\n"
                "- Preserve every meaningful idea. Do not summarize or omit.\n"
                "- Do NOT rewrite from scratch — clean and refine only.\n"
                "Output ONLY the cleaned text without preamble or explanation."
            ),
        },
        {
            "name": "Polish and Refine",
            "prompt": (
                "Refine this text so it reads as polished, natural prose:\n"
                "- Fix all spelling, grammar, and punctuation errors.\n"
                "- Smooth awkward phrasing and improve sentence flow.\n"
                "- Tighten wordy constructions without losing meaning.\n"
                "- Choose more precise words where the original is vague.\n"
                "- Keep the author's voice and tone — do not make casual "
                "text formal or vice versa.\n"
                "- Do not add new ideas or remove existing content.\n"
                "Output ONLY the refined text without preamble or explanation."
            ),
        },
        {
            "name": "Fix Grammar Only",
            "prompt": (
                "Fix ONLY spelling, punctuation, and grammar errors. Do not "
                "change wording, tone, sentence structure, or meaning in any "
                "way. If the text is already correct, return it unchanged. "
                "Output ONLY the corrected text without preamble or explanation."
            ),
        },
        {
            "name": "Simplify",
            "prompt": (
                "Rewrite this text so it is easy to understand:\n"
                "- Replace jargon and technical terms with plain language.\n"
                "- Break long, complex sentences into shorter ones.\n"
                "- Use active voice where possible.\n"
                "- Remove unnecessary qualifiers and hedging.\n"
                "- Preserve all key information and the author's intent.\n"
                "Output ONLY the simplified text without preamble or explanation."
            ),
        },
        {
            "name": "Professional Tone",
            "prompt": (
                "Rewrite this text in a clear, professional tone suitable "
                "for workplace communication:\n"
                "- Use a neutral professional register — not stiff or overly "
                "formal.\n"
                "- Fix all spelling, grammar, and punctuation errors.\n"
                "- Remove slang, filler, and overly casual language.\n"
                "- Keep sentences direct and well-structured.\n"
                "- Preserve the author's intent and all key information.\n"
                "Output ONLY the rewritten text without preamble or explanation."
            ),
        },
    ],
    # Chat interaction mode: "single" (each message replaces diff view) or "conversation" (persistent chat history)
    "chat_mode": "conversation",
    # Correction modes: configurable prompt + hallucination threshold per strength.
    # Index 0 = conservative/spelling_only, 1 = smart_fix/full_correction, 2 = aggressive/rewrite_polish.
    "correction_modes": [
        {
            "name": "Spelling Only",
            "prompt": "Fix spelling mistakes. Change nothing else.\n\nOUTPUT: the corrected text between <<<START>>> and <<<END>>>. No other words. No explanations.\n\nRULES:\n1. Fix only misspelled words: \"libary\" -> \"library\", \"teh\" -> \"the\".\n2. Copy punctuation, capitalization, grammar, word order, line breaks, and spacing exactly as given.\n3. If nothing is misspelled, copy the text exactly as given.\n4. Repeated words and repeated sentences stay exactly as given.\n5. Copy numbers, names, ALL-CAPS words, code, URLs, and symbols exactly as given. Never fix them.\n6. NEVER change the case of well-known protocol prefixes (https, http, www) — they are case-sensitive in URLs.\n\nEXAMPLE\nInput: <<<START>>>She borowed teh red kayak yesterday.<<<END>>>\nOutput: <<<START>>>She borrowed the red kayak yesterday.<<<END>>>\n\nEXAMPLE\nInput: <<<START>>>the quartz lamp works fine fine.<<<END>>>\nOutput: <<<START>>>the quartz lamp works fine fine.<<<END>>>",
            "hallucination_threshold": 0.4,
            "builtin": True,
        },
        {
            "name": "Full Correction",
            "prompt": "Fix spelling, grammar, punctuation, and capitalization. Keep the author's words.\n\nOUTPUT: the corrected text between <<<START>>> and <<<END>>>. No other words. No explanations.\n\nRULES:\n1. Fix typos, spelling, grammar, punctuation, and capitalization.\n2. Add missing terminal punctuation (periods or question marks) at the end of sentences that lack it.\n3. Keep the author's wording, tone, and meaning. Do not rewrite for style.\n4. If the text is already correct, copy it exactly as given.\n5. Keep line breaks and spacing exactly as given.\n6. Keep ALL-CAPS words, acronyms (NASA, USA), Title Case, repeated words, and repeated sentences exactly as given.\n7. Copy numbers, names, code, URLs, and symbols exactly as given. Never fix them.\n8. Add nothing else. Remove nothing. Reorder nothing beyond the minimum a fix requires.\n9. Fix ALL instances of a repeated error, not just the first one.\n10. NEVER change the case of well-known protocol prefixes (https, http, www) — they are case-sensitive in URLs.\n\nEXAMPLE\nInput: <<<START>>>him and me was late becuase the traffic.<<<END>>>\nOutput: <<<START>>>He and I were late because of the traffic.<<<END>>>\n\nEXAMPLE\nInput: <<<START>>>The CFO approved the Q3 budget.<<<END>>>\nOutput: <<<START>>>The CFO approved the Q3 budget.<<<END>>>",
            "hallucination_threshold": 1.0,
            "builtin": True,
        },
        {
            "name": "Rewrite & Polish",
            "prompt": "Edit the text so it reads clearly and smoothly. Keep the author's voice.\n\nOUTPUT: the edited text between <<<START>>> and <<<END>>>. No other words. No explanations.\n\nRULES:\n1. Fix all spelling, grammar, punctuation, and capitalization.\n2. Improve clarity, flow, and word choice. Cut filler words (um, uh, like, basically, you know, I mean, so yeah, kind of, sort of).\n3. Keep the author's tone: casual stays casual, formal stays formal. Keep slang, contractions, humor, and emphasis.\n4. Keep every fact, claim, name, and number exactly as given. Invent nothing.\n5. Add no greetings, sign-offs, examples, or commentary.\n6. Keep line breaks and paragraph structure as given.\n7. Copy code, URLs, and symbols exactly as given.\n8. NEVER change the case of well-known protocol prefixes (https, http, www) — they are case-sensitive in URLs.\n\nEXAMPLE\nInput: <<<START>>>basically the velvet sofa thing is, it kinda just dont fit in the hallway at all tbh.<<<END>>>\nOutput: <<<START>>>tbh the velvet sofa just doesn't fit in the hallway.<<<END>>>\n\nEXAMPLE\nInput: <<<START>>>Our pilot program reduced onboarding time by 40%.<<<END>>>\nOutput: <<<START>>>Our pilot program reduced onboarding time by 40%.<<<END>>>",
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
        "name": "Clean Up Dictation",
        "prompt": (
            "This text was dictated using speech-to-text and contains spoken "
            "artifacts. Clean it up:\n"
            "- Remove filler words (um, uh, ah, like, basically, you know, "
            "I mean, so yeah, kind of, sort of, right, actually, literally, "
            "honestly, essentially, anyway, well, OK so).\n"
            "- Remove stuttered or repeated words and false starts.\n"
            "- Fix run-on sentences — add proper punctuation and split where "
            "natural pauses should be.\n"
            "- Fix obvious STT misspellings and missing punctuation.\n"
            "- Lightly adjust sentence structure ONLY when the original is "
            "genuinely incoherent. Otherwise keep the author's phrasing.\n"
            "- Preserve every meaningful idea. Do not summarize or omit.\n"
            "- Do NOT rewrite from scratch — clean and refine only.\n"
            "Output ONLY the cleaned text without preamble or explanation."
        ),
    },
    {
        "name": "Polish and Refine",
        "prompt": (
            "Refine this text so it reads as polished, natural prose:\n"
            "- Fix all spelling, grammar, and punctuation errors.\n"
            "- Smooth awkward phrasing and improve sentence flow.\n"
            "- Tighten wordy constructions without losing meaning.\n"
            "- Choose more precise words where the original is vague.\n"
            "- Keep the author's voice and tone — do not make casual "
            "text formal or vice versa.\n"
            "- Do not add new ideas or remove existing content.\n"
            "Output ONLY the refined text without preamble or explanation."
        ),
    },
    {
        "name": "Fix Grammar Only",
        "prompt": (
            "Fix ONLY spelling, punctuation, and grammar errors. Do not "
            "change wording, tone, sentence structure, or meaning in any "
            "way. If the text is already correct, return it unchanged. "
            "Output ONLY the corrected text without preamble or explanation."
        ),
    },
    {
        "name": "Simplify",
        "prompt": (
            "Rewrite this text so it is easy to understand:\n"
            "- Replace jargon and technical terms with plain language.\n"
            "- Break long, complex sentences into shorter ones.\n"
            "- Use active voice where possible.\n"
            "- Remove unnecessary qualifiers and hedging.\n"
            "- Preserve all key information and the author's intent.\n"
            "Output ONLY the simplified text without preamble or explanation."
        ),
    },
    {
        "name": "Professional Tone",
        "prompt": (
            "Rewrite this text in a clear, professional tone suitable "
            "for workplace communication:\n"
            "- Use a neutral professional register — not stiff or overly "
            "formal.\n"
            "- Fix all spelling, grammar, and punctuation errors.\n"
            "- Remove slang, filler, and overly casual language.\n"
            "- Keep sentences direct and well-structured.\n"
            "- Preserve the author's intent and all key information.\n"
            "Output ONLY the rewritten text without preamble or explanation."
        ),
    },
]
