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

APP_VERSION = "1.1.0"

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
LLAMA_BACKEND_VERSION = "b9940"
_LLAMA_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BACKEND_VERSION}"
LLAMA_BACKEND_URLS = {
    "llama": f"{_LLAMA_BASE}/llama-{LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64.zip",
    "cuda": f"{_LLAMA_BASE}/cudart-llama-bin-win-cuda-12.4-x64.zip",
}
LLAMA_BACKEND_HASHES = {
    "llama": "1EB3AFEC18662B69A8E6716978E61263C8B9F4829A6E929B8FCDCC142BE51893",
    "cuda": "8C79A9B226DE4B3CACFD1F83D24F962D0773BE79F1E7B75C6AF4DED7E32AE1D6",
}
LLAMA_BACKEND_DIR = f"llama-{LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64"

# Recommended Model Configuration
RECOMMENDED_MODEL_URL = "https://huggingface.co/unsloth/gemma-4-E2B-it-GGUF/resolve/main/gemma-4-E2B-it-UD-Q4_K_XL.gguf"
RECOMMENDED_MODEL_FILE = "gemma-4-E2B-it-UD-Q4_K_XL.gguf"
RECOMMENDED_MODEL_HASH = "b8906b8c5e05e57b657646bbc657bd35814a269b2c20f0a2579047fafa1a67dd"

WELCOME_SAMPLE_TEXT = (
    "Stet is running! Select any text in any app, and press the F9 hotkey "
    "to correct spelling, grammar, and phrasing instantly.\n\n"
    "Test correction with this sentence:\n"
    "\"him and me was late becuase the traffic was super bad.\""
)


DEFAULT_CONFIG: dict = {
    # Presets
    "show_welcome_on_startup": True,
    "chat_thinking_enabled": True,
    "startup_on_login": False,

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
    "patch_chunk_size": 60,
    "cache_prompt": True,
    "recent_models": [],
    # Chat model
    "chat_model_path": "",
    "chat_use_separate_model": False,
    "chat_keep_loaded": False,
    "chat_idle_timeout_seconds": 60,
    "chat_context_size": 12800,
    "chat_gpu_layers": 99,
    "chat_threads": -1,
    "chat_batch_size": 1024,
    "chat_ubatch_size": 512,
    "chat_flash_attn": True,
    "chat_kv_cache_type_k": "q8_0",
    "chat_kv_cache_type_v": "q8_0",
    "chat_mtp_enabled": False,
    "chat_mtp_max_draft": 2,
    "chat_mtp_min_draft": 0,
    "chat_temperature": 0.8,
    "chat_top_k": 40,
    "chat_top_p": 0.95,
    "chat_min_p": 0.05,
    "chat_seed": -1,
    "chat_typical_p": 1.0,
    "chat_tfs_z": 1.0,
    "chat_mirostat": 0,
    "chat_mirostat_tau": 5.0,
    "chat_mirostat_eta": 0.1,
    "chat_repeat_penalty": 1.1,
    "chat_frequency_penalty": 0.0,
    "chat_presence_penalty": 0.0,
    "chat_rope_freq_base": 0.0,
    "chat_rope_freq_scale": 0.0,
    "chat_parallel": 1,
    "chat_threads_batch": -1,
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
        {
            "name": "Academic & Scholarly",
            "prompt": (
                "Rewrite this text in an objective, formal, and academic tone suitable "
                "for research papers, essays, or scholarly publications:\n"
                "- Use precise, scholarly vocabulary and formal sentence structures.\n"
                "- Remove first-person pronouns (I, we, my) where possible, adopting "
                "an objective, third-person perspective.\n"
                "- Eliminate colloquialisms, contractions, slang, and conversational phrasing.\n"
                "- Ensure arguments flow logically and transitions are smooth.\n"
                "- Preserve the exact meaning, factual claims, and technical concepts "
                "of the original text.\n"
                "Output ONLY the academic version without preamble or explanation."
            ),
        },
        {
            "name": "Notes Assistant",
            "prompt": (
                "Format and structure the text cleanly as readable notes:\n"
                "1. If input is already a list/notes layout, keep it but fix typos, grammar, and alignment.\n"
                "2. If input is prose/dictation, convert it into structured bulleted notes.\n"
                "3. Use headers (#, ##) for sections, bullet points (-) for items, and bolding (**) for key terms.\n"
                "4. Keep all facts, names, code, and technical terms exactly as given. Do not summarize or omit.\n"
                "5. Output ONLY the notes. No preamble, no explanation."
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
            "hallucination_threshold": 0.7,
            "builtin": True,
        },
        {
            "name": "Full Correction",
            "prompt": "Fix spelling, grammar, punctuation, and capitalization. Keep the author's words.\n\nOUTPUT: the corrected text between <<<START>>> and <<<END>>>. No other words. No explanations.\n\nRULES:\n1. Fix typos, spelling, grammar, punctuation, and capitalization.\n2. Add missing terminal punctuation (periods or question marks) at the end of sentences that lack it.\n3. Never remove existing terminal punctuation (. ? !) from the end of a sentence — only add missing punctuation or fix incorrect punctuation.\n4. Keep the author's wording, tone, and meaning. Do not rewrite for style.\n5. If the text is already correct, copy it exactly as given.\n6. Keep line breaks and spacing exactly as given.\n7. Keep ALL-CAPS words, acronyms (NASA, USA), Title Case, repeated words, and repeated sentences exactly as given.\n8. Copy numbers, names, code, URLs, and symbols exactly as given. Never fix them.\n9. Add nothing else. Remove nothing. Reorder nothing beyond the minimum a fix requires.\n10. Fix ALL instances of a repeated error, not just the first one.\n11. NEVER change the case of well-known protocol prefixes (https, http, www) — they are case-sensitive in URLs.\n\nEXAMPLE\nInput: <<<START>>>him and me was late becuase the traffic.<<<END>>>\nOutput: <<<START>>>He and I were late because of the traffic.<<<END>>>\n\nEXAMPLE\nInput: <<<START>>>The CFO approved the Q3 budget.<<<END>>>\nOutput: <<<START>>>The CFO approved the Q3 budget.<<<END>>>",
            "hallucination_threshold": 1.0,
            "builtin": True,
        },
        {
            "name": "Rewrite & Polish",
            "prompt": "Edit the text so it reads clearly and smoothly. Keep the author's voice.\n\nOUTPUT: the edited text between <<<START>>> and <<<END>>>. No other words. No explanations.\n\nRULES:\n1. Fix all spelling, grammar, punctuation, and capitalization.\n2. Preserve existing terminal punctuation (. ? !) at the end of sentences. Do not drop sentence-ending punctuation.\n3. Improve clarity, flow, and word choice. Cut filler words (um, uh, like, basically, you know, I mean, so yeah, kind of, sort of).\n4. Keep the author's tone: casual stays casual, formal stays formal. Keep slang, contractions, humor, and emphasis.\n5. Keep every fact, claim, name, and number exactly as given. Invent nothing.\n6. Add no greetings, sign-offs, examples, or commentary.\n7. Keep line breaks and paragraph structure as given.\n8. Copy code, URLs, and symbols exactly as given.\n9. NEVER change the case of well-known protocol prefixes (https, http, www) — they are case-sensitive in URLs.\n\nEXAMPLE\nInput: <<<START>>>basically the velvet sofa thing is, it kinda just dont fit in the hallway at all tbh.<<<END>>>\nOutput: <<<START>>>tbh the velvet sofa just doesn't fit in the hallway.<<<END>>>\n\nEXAMPLE\nInput: <<<START>>>Our pilot program reduced onboarding time by 40%.<<<END>>>\nOutput: <<<START>>>Our pilot program reduced onboarding time by 40%.<<<END>>>",
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
    {
        "name": "Academic & Scholarly",
        "prompt": (
            "Rewrite this text in an objective, formal, and academic tone suitable "
            "for research papers, essays, or scholarly publications:\n"
            "- Use precise, scholarly vocabulary and formal sentence structures.\n"
            "- Remove first-person pronouns (I, we, my) where possible, adopting "
            "an objective, third-person perspective.\n"
            "- Eliminate colloquialisms, contractions, slang, and conversational phrasing.\n"
            "- Ensure arguments flow logically and transitions are smooth.\n"
            "- Preserve the exact meaning, factual claims, and technical concepts "
            "of the original text.\n"
            "Output ONLY the academic version without preamble or explanation."
        ),
    },
    {
        "name": "Notes Assistant",
        "prompt": (
            "Format and structure the text cleanly as readable notes:\n"
            "1. If input is already a list/notes layout, keep it but fix typos, grammar, and alignment.\n"
            "2. If input is prose/dictation, convert it into structured bulleted notes.\n"
            "3. Use headers (#, ##) for sections, bullet points (-) for items, and bolding (**) for key terms.\n"
            "4. Keep all facts, names, code, and technical terms exactly as given. Do not summarize or omit.\n"
            "5. Output ONLY the notes. No preamble, no explanation."
        ),
    },
]
