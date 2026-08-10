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

APP_VERSION = "1.2.2"

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
        # Keep the app bundle immutable. User state is resolved below through
        # Foundation's Application Support location, while bundled resources
        # remain discoverable from Contents/Resources.
        APP_BUNDLE_DIR = exe_path.parent.parent.parent.resolve()
        BUNDLED_RESOURCES_DIR = APP_BUNDLE_DIR / "Contents" / "Resources"
        SCRIPT_DIR = APP_BUNDLE_DIR
    else:
        APP_BUNDLE_DIR = None
        BUNDLED_RESOURCES_DIR = None
        SCRIPT_DIR = exe_path.parent.resolve()
else:
    APP_BUNDLE_DIR = None
    BUNDLED_RESOURCES_DIR = None
    SCRIPT_DIR = Path(__file__).parent.parent.resolve()

if MACOS and not _is_compiled:
    # Development runs and live checks are deliberately self-contained. Keep
    # the model and llama backend visibly at the checkout root instead of in
    # a hidden runtime directory or an unrelated Application Support install.
    # A packaged .app remains immutable and follows the branch below.
    PROJECT_RUNTIME_DIR = SCRIPT_DIR
    LEGACY_PROJECT_RUNTIME_DIR = SCRIPT_DIR / ".stet-runtime"
    APP_DATA_DIR = PROJECT_RUNTIME_DIR
    MODELS_DIR = SCRIPT_DIR / "Models"
    BACKENDS_DIR = SCRIPT_DIR / "backends"
    DOWNLOADS_DIR = SCRIPT_DIR / "downloads"
    CONFIG_FILE = SCRIPT_DIR / "config.json"
    LOG_FILE = SCRIPT_DIR / "logs" / "server_log.txt"
    DEBUG_LOG = SCRIPT_DIR / "logs" / "app_debug.log"
    for _directory in (MODELS_DIR, BACKENDS_DIR, DOWNLOADS_DIR, LOG_FILE.parent):
        _directory.mkdir(parents=True, exist_ok=True)
    LLAMA_CPP_DIR = BACKENDS_DIR / "llama_cpp"
    BUNDLED_BACKEND_DIR = BACKENDS_DIR
elif MACOS:
    # A signed packaged application is immutable. Foundation gives it the
    # proper sandbox-aware user Library locations for user-owned state.
    from stet.core.macos_paths import ensure_directories, resolve_paths

    _mac_paths = resolve_paths("Stet")
    if _mac_paths.supported and ensure_directories(_mac_paths):
        APP_DATA_DIR = _mac_paths.application_support
        MODELS_DIR = _mac_paths.models
        BACKENDS_DIR = APP_DATA_DIR / "backends"
        DOWNLOADS_DIR = _mac_paths.downloads
        CONFIG_FILE = APP_DATA_DIR / "config.json"
        LOG_FILE = _mac_paths.logs / "server_log.txt"
        DEBUG_LOG = _mac_paths.logs / "app_debug.log"
    else:
        # This only applies to constrained source/test environments without
        # Foundation. A normal packaged macOS app always uses Library paths.
        APP_DATA_DIR = SCRIPT_DIR
        MODELS_DIR = SCRIPT_DIR / "Models"
        BACKENDS_DIR = SCRIPT_DIR / "backends"
        DOWNLOADS_DIR = SCRIPT_DIR / "downloads"
        CONFIG_FILE = SCRIPT_DIR / "config.json"
        LOG_FILE = SCRIPT_DIR / "server_log.txt"
        DEBUG_LOG = SCRIPT_DIR / "app_debug.log"
    LLAMA_CPP_DIR = BACKENDS_DIR / "llama_cpp"
    BUNDLED_BACKEND_DIR = (BUNDLED_RESOURCES_DIR or SCRIPT_DIR) / "llama_cpp"
else:
    LEGACY_PROJECT_RUNTIME_DIR = None
    APP_DATA_DIR = SCRIPT_DIR
    MODELS_DIR = SCRIPT_DIR
    BACKENDS_DIR = SCRIPT_DIR
    DOWNLOADS_DIR = SCRIPT_DIR
    CONFIG_FILE = SCRIPT_DIR / "config.json"
    LLAMA_CPP_DIR = SCRIPT_DIR / "llama_cpp"
    BUNDLED_BACKEND_DIR = LLAMA_CPP_DIR
    LOG_FILE = SCRIPT_DIR / "server_log.txt"
    DEBUG_LOG = SCRIPT_DIR / "app_debug.log"


SERVER_EXE = "llama-server.exe" if WINDOWS else "llama-server"

GITHUB_RELEASES_API = "https://api.github.com/repos/AmrZriek/Stet/releases/latest"

# ── llama.cpp backend auto-download ──────────────────────────────────────────
# The llama-server binaries + CUDA runtime are downloaded on first run instead
# of bundled in the installer (keeps installer under 120 MB to avoid AV flags).
LLAMA_BACKEND_VERSION = "b10107"
_LLAMA_BASE = f"https://github.com/ggml-org/llama.cpp/releases/download/{LLAMA_BACKEND_VERSION}"
LLAMA_BACKEND_URLS = {
    "llama": f"{_LLAMA_BASE}/llama-{LLAMA_BACKEND_VERSION}-bin-win-cuda-12.4-x64.zip",
    "cuda": f"{_LLAMA_BASE}/cudart-llama-bin-win-cuda-12.4-x64.zip",
}
LLAMA_BACKEND_HASHES = {
    "llama": "1E43BBEC9691CD0BC636603C366769148FA6265FD261C5F7C67050B450BBC237",
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


DEFAULT_TEMPLATES: list[dict[str, str]] = [

    {
        "name": "Clean Up Dictation",
        "prompt": (
            "Turn the dictated content into clean written text.\n\n"
            "Remove speech fillers, stutters, false starts, accidental "
            "repetitions, and abandoned phrases. Fix transcription errors, "
            "spelling, grammar, capitalization, and punctuation. Split run-on "
            "speech into natural sentences and lightly reorganize wording "
            "when needed for clarity.\n\n"
            "Keep every meaningful idea, fact, name, and number. "
            "Do not summarize, invent details, or change the speaker's "
            "intended tone."
        ),
    },
    {
        "name": "Professional Tone",
        "prompt": (
            "Rewrite the content as clear, concise workplace communication.\n\n"
            "Use a natural professional tone that is confident and direct, "
            "not stiff or overly formal. Improve organization, sentence flow, "
            "word choice, grammar, and punctuation. Remove filler, slang "
            "that is unsuitable for work, and unnecessary repetition.\n\n"
            "Preserve the intended message, facts, names, numbers, requests, "
            "and commitments. Do not add claims, greetings, or sign-offs "
            "that were not present."
        ),
    },
    {
        "name": "Academic & Scholarly",
        "prompt": (
            "Rewrite the content in precise, formal academic prose.\n\n"
            "Improve logical flow, terminology, sentence structure, "
            "transitions, grammar, and punctuation. Replace conversational "
            "phrasing and unsupported emphasis with objective wording. "
            "Reduce first-person phrasing when doing so does not change "
            "the meaning.\n\n"
            "Preserve every factual claim, qualification, citation, "
            "technical term, name, and number. Do not invent evidence, "
            "citations, conclusions, or technical details."
        ),
    },
    {
        "name": "Notes Assistant",
        "prompt": (
            "Convert the content into clear Markdown notes.\n\n"
            "Use short section headings only when they help. Put each "
            "main idea on its own \"- \" bullet line. Use indented bullets "
            "for supporting details. Use bold text sparingly for important "
            "terms. If the content is already notes, improve its "
            "organization and consistency.\n\n"
            "Preserve all facts, names, numbers, code, links, paths, "
            "and technical details. Do not invent information. "
            "Do not write prose before or after the notes."
        ),
    },
]


DEFAULT_CONFIG: dict = {
    # Presets
    "show_welcome_on_startup": True,
    "chat_thinking_enabled": True,
    "startup_on_login": False,

    # llama.cpp
    "llama_server_path": str(LLAMA_CPP_DIR / SERVER_EXE),
    # macOS: automatic selection uses Metal on Apple Silicon and CPU on Intel.
    # Windows/Linux retain their historical native backend behavior.
    "backend_mode": "auto",
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
    "top_k": 1,
    "top_p": 0.95,
    "min_p": 0.0,
    # Correction-specific overrides for the patch pipeline.
    # These lock the model to its most-probable token during correction,
    # preventing spurious capitalization, punctuation, and word changes.
    "correction_temperature": 0.0,
    "correction_top_k": 1,
    "correction_top_p": 0.95,
    "correction_min_p": 0.0,
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
    # Correction history (local-only record for undo and review)
    "history_enabled": True,
    "history_limit": 200,
    # Protected terms: user-defined words/phrases masked from the LLM like URLs
    "protected_terms": [],
    # Fallback default strength when no per-hotkey strength is set.
    #   "spelling_only" — typos only;  "full_correction" — full grammar/capitalization/punctuation;
    #   "rewrite_polish" — rewrite for clarity, concision, and impact.
    "streaming_strength": "full_correction",
    # Custom templates: list of {"name": str, "prompt": str}
    "custom_templates": [t.copy() for t in DEFAULT_TEMPLATES],
    # Chat interaction mode: "single" (each message replaces diff view) or "conversation" (persistent chat history)
    "chat_mode": "conversation",
    # Correction modes: configurable prompt + hallucination threshold per strength.
    # Index 0 = conservative/spelling_only, 1 = smart_fix/full_correction, 2 = aggressive/rewrite_polish.
    "correction_modes": [
        {
            "name": "Spelling Only",
            "prompt": (
                "Correct every clear spelling or typing error.\n\n"
                "A valid edit replaces one mistaken word with its obvious intended word. "
                "This includes accidental forms such as \"teh\", \"blockkers\", \"postphone\", "
                "and contextually unmistakable typing errors such as \"advise\" when \"advice\" "
                "was clearly intended.\n\n"
                "Preserve every other character exactly, including capitalization, punctuation, "
                "grammar, wording, word order, repetition, spacing, and line breaks. "
                "Do not modernize, regionalize, or improve the writing.\n\n"
                "If no clear spelling or typing error exists, return the content unchanged."
            ),
            "hallucination_threshold": 0.45,
            "builtin": True,
        },
        {
            "name": "Full Correction",
            "prompt": (
                "Correct the text completely without stylistically rewriting it.\n\n"
                "Fix every spelling, grammar, capitalization, punctuation, agreement, "
                "and clearly incorrect word-use error. Make the smallest edits needed "
                "for correct, natural text.\n\n"
                "Preserve the author's meaning, tone, level of formality, sentence order, "
                "repetition, and overall phrasing. Do not add new ideas, remove ideas, "
                "summarize, or make optional style changes.\n\n"
                "If the text is already correct, return it unchanged."
            ),
            "hallucination_threshold": 0.75,
            "builtin": True,
        },
        {
            "name": "Rewrite & Polish",
            "prompt": (
                "Rewrite and polish the text into its strongest clear, natural version.\n\n"
                "Fix all errors. Improve sentence flow, word choice, word placement, "
                "transitions, clarity, rhythm, and concision. Remove filler, redundancy, "
                "repeated ideas, and unnecessary sentences. You may combine, split, reorder, "
                "shorten, or rewrite sentences whenever that improves the result.\n\n"
                "Preserve the author's intended meaning, factual claims, names, numbers, "
                "tone, and level of formality. Do not invent information or make the text "
                "sound generically formal unless the original calls for it."
            ),
            "hallucination_threshold": 0.97,
            "builtin": True,
        },
        {
            "name": "Custom Patch",
            "prompt": "You are a text-correction engine. The user will send text to correct.\n\n- Fix typos, spelling, grammar, punctuation, and capitalization.\n- Improve clarity, conciseness, and overall quality.\n- Preserve the author's core intent and meaning.",
            "hallucination_threshold": 1.0,
            "builtin": False,
            "enabled": False,
        },
    ],
}


# Bare function keys collide with Mission Control and hardware controls on a
# Mac.  Fresh macOS installs therefore start with explicit modifier shortcuts;
# existing user selections are never overwritten except by the config migration.
LEGACY_MACOS_DEFAULT_HOTKEYS = [
    {"shortcut": "ctrl+alt+f9", "mode": "panel", "strength": "full_correction"},
    {"shortcut": "ctrl+alt+f10", "mode": "silent", "strength": "spelling_only"},
    {"shortcut": "ctrl+alt+shift+f9", "mode": "panel", "strength": "rewrite_polish"},
]
MACOS_DEFAULT_HOTKEYS = [
    {"shortcut": "cmd+option+f9", "mode": "panel", "strength": "full_correction"},
    {"shortcut": "cmd+option+f10", "mode": "silent", "strength": "spelling_only"},
    {"shortcut": "cmd+option+shift+f9", "mode": "panel", "strength": "rewrite_polish"},
]
if MACOS:
    DEFAULT_CONFIG["hotkeys"] = [entry.copy() for entry in MACOS_DEFAULT_HOTKEYS]

