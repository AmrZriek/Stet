import concurrent.futures
import ctypes
import ctypes.wintypes
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests
from PyQt6.QtCore import QObject, pyqtSignal

from stet.constants import DEFAULT_CONFIG, LOG_FILE, WINDOWS
from stet.core.config import ConfigManager
from stet.core.text_utils import (
    _INLINE_HAZARD_RE,
    _INLINE_SENTINEL_CAPTURE_RE,
    _INLINE_SENTINEL_RE,
    PROFILES,
    CorrectionOutcome,
    CorrectionProfile,
    CorrectionResult,
    _apply_post_fixes,
    _chunk_text_by_sentences,
    _dict_prepass,
    _extract_content_from_response,
    _extract_rewritten_sentence,
    _hallucination_ratio,
    _is_corrupt_output,
    _is_fewshot_echo,
    _is_no_change_declaration,
    _is_refusal_or_empty,
    _loses_meaningful_repetition,
    _normalize_chunk_newlines,
    _post_splice_sanity,
    _wrap_correction_prompt,
    recover_sentinels,
)
from stet.core.utils import friendly_name, log
from stet.llm.backend_manager import (
    BackendManager,
)
from stet.llm.gguf_info import get_gguf_info_cached
from stet.llm.template_sanitizer import (
    sanitize_template,
    write_sanitized_template,
)
from stet.llm.utils import (
    _MIN_RELIABLE_MODEL_B,
    _find_mtp_draft_model,
    _find_shipped_llama_server,
    _model_size_billions,
    estimate_model_vram_mb,
    has_nvidia,
    query_free_vram_mb,
)
from stet.llm.worker import StreamWorker

_STRENGTH_TO_MODE_INDEX = {
    "spelling_only": 0,
    "full_correction": 1,
    "rewrite_polish": 2,
}

# (open-tag regex, close-tag regex) pairs for detecting an UNBALANCED think
# open-tag (an open with no matching close) in a rendered prompt. <think is
# matched with a negative lookahead so <thinking isn't counted twice.
_UNBALANCED_TAG_PAIRS = [
    (re.compile(r"<think(?!ing)"), re.compile(r"</think>")),
    (re.compile(r"<thinking"), re.compile(r"</thinking>")),
    (re.compile(r"<reasoning"), re.compile(r"</reasoning>")),
]


def _has_unbalanced_think_open(text: str) -> bool:
    """True when *text* contains a think open-tag with no matching close."""
    if not text:
        return False
    for open_re, close_re in _UNBALANCED_TAG_PAIRS:
        if len(open_re.findall(text)) > len(close_re.findall(text)):
            return True
    return False


def _detect_loaded_backend(log_content: str, *, metal_verified: bool = False) -> str:
    """Classify execution backend from llama.cpp server logs.
    Returns: 'cuda', 'metal', 'cpu', or 'unknown'
    """
    if not log_content:
        return "metal" if metal_verified else "unknown"

    content_lower = log_content.lower()

    if any(
        kw in content_lower
        for kw in (
            "ggml_cuda",
            "cuda0",
            "using cuda",
            "cublas",
            "device 0: nvidia",
        )
    ):
        return "cuda"

    if (
        metal_verified
        or "ggml_metal" in content_lower
        or "using metal" in content_lower
        or bool(re.search(r"mtl\d*:", content_lower))
        or bool(re.search(r"\bmetal\b", content_lower))
    ):
        return "metal"

    if any(
        kw in content_lower
        for kw in (
            "blast",
            "openblas",
            "accelerate",
            "cpu",
            "ggml_cpu",
            "model loaded",
        )
    ) or not metal_verified:
        return "cpu"

    return "metal" if metal_verified else "unknown"




def _probe_gpu_devices(server_path, timeout=20.0):
    """Authoritatively probe a llama-server binary for GPU devices.

    Runs ``<server> --list-devices`` and inspects its output. Modern llama.cpp
    (b10xxx) prints the compiled-in backends and their devices, e.g.:

        Available devices:
          CUDA0: NVIDIA GeForce RTX 3060 Laptop GPU (6143 MiB, 5130 MiB free)

    The startup log and /props endpoint stopped carrying this information
    (verified on b10375/b10639), so this is the reliable GPU presence signal.
    Never raises: any failure simply reports no devices so the caller falls
    back to its existing (log + /props) evidence.

    Returns a dict: {cuda, vulkan, metal, devices, raw}
    """
    result = {"cuda": False, "vulkan": False, "metal": False, "devices": [], "raw": ""}
    try:
        path = Path(server_path)
        if not path.is_file():
            return result
        run_kwargs: dict = {}
        if WINDOWS:
            run_kwargs["creationflags"] = 0x08000000
        proc = subprocess.run(
            [str(path), "--list-devices"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            **run_kwargs,
        )
    except BaseException as exc:
        log(f"[GPU probe] --list-devices failed (non-fatal): {exc}")
        return result
    raw = "\n".join(
        value for value in (getattr(proc, "stdout", ""), getattr(proc, "stderr", "")) if value
    )
    result["raw"] = raw
    low = raw.lower()
    for line in raw.splitlines():
        if ":" in line and "(" in line:
            result["devices"].append(line.strip())
    result["cuda"] = any(
        token in low
        for token in (
            "cuda",
            "cublas",
            "nvidia",
            "rtx",
            "geforce",
            "quadro",
            "tesla",
            "a100",
            "h100",
        )
    )
    result["vulkan"] = "vulkan" in low
    result["metal"] = "metal" in low
    log(
        f"[GPU probe] --list-devices cuda={result['cuda']} vulkan={result['vulkan']} "
        f"metal={result['metal']} devices={result['devices']}"
    )
    return result


def _resolve_mode_index(strength: str, modes: list) -> int:
    """Map a strength string to a correction_modes list index.

    Built-in strengths resolve via the static map if valid. Custom mode names
    or renamed built-ins are matched by scanning all modes by name or id.
    Falls back to 1 (full_correction).
    """
    builtin = _STRENGTH_TO_MODE_INDEX.get(strength)
    if builtin is not None and builtin < len(modes):
        return builtin
    for i, m in enumerate(modes or []):
        if isinstance(m, dict) and (m.get("name") == strength or m.get("id") == strength):
            return i
    if builtin is not None:
        return builtin
    return 1


def _normalize_newlines(text: str, use_windows_newlines: bool) -> str:
    """Normalize mixed line endings, then restore the preferred style once."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if use_windows_newlines:
        return normalized.replace("\n", "\r\n")
    return normalized





def _create_job_object_for_subprocess(proc: subprocess.Popen):
    """Attach subprocess to a Windows Job Object so it dies when parent dies."""
    if not WINDOWS:
        return
    try:
        job = ctypes.windll.kernel32.CreateJobObjectW(None, None)
        if not job:
            return

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.POINTER(ctypes.c_ulong)),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JobObjectExtendedLimitInformation = 9
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

        ctypes.windll.kernel32.SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )

        proc_handle = ctypes.c_void_p(int(proc._handle))
        ctypes.windll.kernel32.AssignProcessToJobObject(job, proc_handle)
        log("[Server] Attached llama-server to Job Object (kill-on-close)")
        return job
    except Exception as e:
        log(f"[Server] Failed to attach to Job Object: {e}")
        return None


def _estimate_tokens(text: str) -> int:
    """Estimate token count. ~1.3 tokens per word for English, higher for CJK.

    Uses word count instead of character count for more accurate estimation.
    The 1.5 multiplier includes a safety margin for punctuation and special tokens.
    Falls back to character-based estimation for non-word content (e.g. CJK, code)
    when word count is suspiciously low relative to character count.
    """
    words = len(text.split())
    chars = len(text)
    # CJK/code text has very few spaces — fall back to char-based estimation
    # when average "word" length exceeds 10 characters.
    if words == 0 or (chars > 0 and words < chars / 10):
        return max(1, int(chars * 0.75))
    return max(1, int(words * 1.5))


class ModelManager(QObject):
    status_changed = pyqtSignal(str)
    model_loaded = pyqtSignal()
    model_unloaded = pyqtSignal()
    # Fires after load if the model is too small to reliably follow the patch
    # prompt format. Parent app surfaces this as a tray message so users don't
    # silently get garbage corrections.
    model_warning = pyqtSignal(str)

    def __init__(
        self,
        cfg: ConfigManager,
        model_path_key: str = "model_path",
        label: str = "LLM",
        keep_loaded_key: str = "keep_model_loaded",
        idle_timeout_key: str = "idle_timeout_seconds",
        server_log_path: str | Path | None = None,
    ):
        super().__init__()
        self.cfg = cfg
        self.model_path_key = model_path_key
        self.label = label
        self.keep_loaded_key = keep_loaded_key
        self.idle_timeout_key = idle_timeout_key
        self.server_log_path = Path(server_log_path) if server_log_path is not None else LOG_FILE
        self.server_process = None
        self.log_file = None
        self.last_used = None
        self.loading = False
        # A live server process is not necessarily a usable model.  llama.cpp
        # starts the HTTP listener before GGUF loading is complete on macOS.
        # This latch becomes true only after our complete startup sequence,
        # including the cache warmup, has finished.
        self._server_ready = False
        self._lock = threading.Lock()
        # Persistent HTTP session for LLM requests. Created lazily on first use
        # (so unit tests that never trigger a correction never open a socket)
        # and reused across all correction calls to keep TCP connections warm
        # and let HTTPAdapter's connection pool amortize the handshake cost
        # across the 4 parallel slots. Close via close_session() on shutdown.
        self._session: requests.Session | None = None
        # Actual context size as reported by llama-server's /props endpoint
        # after load. This may differ from cfg["context_size"] when the model's
        # metadata caps n_ctx lower than the user-requested value (common with
        # older GGUFs). None until the first successful load.
        self.actual_ctx_size: int | None = None
        self.actual_backend_type: str = "unknown"
        # True once the post-load /props probe returned OK. GPU warnings are
        # deferred until this probe, because llama.cpp b10375+ logs carry no
        # backend banners (see load_model GPU detection block).
        self._props_probed = False
        # Set to True when a sanitized chat-template override was passed at
        # launch (--chat-template-file). Cleared if post-load validation of
        # the sanitized template fails, falling back to base suppression.
        self._sanitized_template_used = False
        self._sanitized_template_str: str | None = None
        # Thinking support and the server's resolved chat template, captured
        # from /props after load (None until the first successful load).
        self.thinking_supported: bool = False
        self.model_chat_template: str | None = None
        # Set to True when load_model() fails because the file path is
        # configured but the file doesn't exist (e.g. drive not mounted yet).
        # Reset to False at the start of each load_model() call and on success.
        # Checked by StetApp to schedule a deferred retry.
        self._last_load_failed_not_found: bool = False
        # Lifecycle synchronization: generation counter and cancellation event
        self._load_generation: int = 0
        self._load_cancel_event = threading.Event()
        self._recycle_in_progress: bool = False
        self.backend_manager = BackendManager()

    def is_ready(self) -> bool:
        """True only once the live server has finished loading this model."""
        return self._server_ready and self.is_loaded()


    # ── internal helpers ──────────────────────────────────────────────────
    def _get_param(self, key: str, default=None):
        """Fetch config parameter, automatically resolving prefix (like 'chat_') based on self.model_path_key."""
        prefix = "chat_" if self.model_path_key == "chat_model_path" else ""
        if prefix and not key.startswith("chat_"):
            prefixed_key = f"{prefix}{key}"
            if prefixed_key in DEFAULT_CONFIG:
                return self.cfg.get(prefixed_key, default)
        return self.cfg.get(key, default)

    def _get_user_protection_re(self):
        """Compile (and cache) the protected-terms regex from config."""
        terms = tuple(self.cfg.get("protected_terms", []) or [])
        if terms != getattr(self, "_prot_terms_cache_key", None):
            from stet.core.text_utils import build_user_protection_re
            self._prot_terms_cache = build_user_protection_re(list(terms))
            self._prot_terms_cache_key = terms
        return self._prot_terms_cache

    def mark_used(self):
        self.last_used = datetime.now()

    def _get_session(self) -> requests.Session:
        """Return a persistent requests.Session, creating it on first access.

        The session is mounted with an HTTPAdapter sized for 4 parallel
        server slots (pool_maxsize=8 leaves headroom). requests.Session is
        thread-safe for sending concurrent requests, so this single session
        can be shared by the ThreadPoolExecutor in correct_text_patch and by
        any direct fallback callers without locking.
        """
        if self._session is None:
            from requests.adapters import HTTPAdapter

            session = requests.Session()
            parallel_slots = self._get_param("parallel", 4)
            adapter = HTTPAdapter(
                pool_connections=parallel_slots,
                pool_maxsize=parallel_slots * 2,
            )
            # mount() is part of the real requests.Session API; some tests
            # monkeypatch requests.Session with a stub that lacks it. In that
            # case the session is still functional for post() — it just won't
            # have the tuned pool, which is acceptable in test contexts.
            if hasattr(session, "mount"):
                session.mount("http://", adapter)
                session.mount("https://", adapter)
            self._session = session
        return self._session

    def close_session(self) -> None:
        """Close and discard the persistent session. Safe to call multiple times."""
        if self._session is not None:
            try:
                self._session.close()
            finally:
                self._session = None

    @property
    def port_offset(self) -> int:
        if self.model_path_key == "chat_model_path" and self.cfg.get("chat_use_separate_model", False):
            return 1
        return 0

    def _base_url(self) -> str:
        h = self.cfg.get("server_host", "127.0.0.1")
        p = self.cfg.get("server_port", 8080) + self.port_offset
        return f"http://{h}:{p}"

    def _health_url(self) -> str:
        return self._base_url() + "/health"

    def _is_server_alive(self, session: requests.Session | None = None) -> bool:
        """Check if llama-server's /health endpoint responds with 200 OK."""
        try:
            s = session or self._get_session()
            r = s.get(self._health_url(), timeout=2)
            return getattr(r, "status_code", None) == 200
        except Exception:
            return False

    def _chat_url(self) -> str:
        return self._base_url() + "/v1/chat/completions"

    def _correction_system_prompt(
        self,
        custom_sys: str | None,
        strength: str,
        mode_prompt_override: str | None = None,
    ) -> str:
        modes = self.cfg.get("correction_modes", [])
        mode_index = _resolve_mode_index(strength, modes)
        if mode_prompt_override:
            # Template prompts are self-contained — skip the structural wrapper.
            system = _wrap_correction_prompt(
                mode_prompt_override, mode_index, prompt_is_complete=True,
            )
        elif modes and mode_index < len(modes):
            system = _wrap_correction_prompt(modes[mode_index]["prompt"], mode_index)
        else:
            system = _wrap_correction_prompt(
                DEFAULT_CONFIG["correction_modes"][min(mode_index, 3)]["prompt"],
                min(mode_index, 3),
            )

        if custom_sys:
            system += f"\n\nAdditional instructions:\n{custom_sys}"
        return system

    def _build_correction_messages(
        self,
        chunk_text: str,
        custom_sys: str | None,
        strength: str,
        mode_prompt_override: str | None = None,
    ) -> list[dict[str, str]]:
        """Build the exact chat message shape used for patch correction.

        Warmup and real correction must share this path so llama-server prompt
        cache checkpoints match the first real request, including Gemma's
        user-folded system prompt.

        Uses input-only delimiters (CONTENT_BEGIN / CONTENT_END).  The model's
        response IS the corrected text — no output markers or assistant prefill
        required.
        """
        system = self._correction_system_prompt(
            custom_sys,
            strength,
            mode_prompt_override,
        )
        wrapped = f"CONTENT_BEGIN\n{chunk_text}\nCONTENT_END"
        model_path = self.cfg.get(self.model_path_key, "") or ""
        model_name = Path(model_path).name.lower()
        # Gemma folds the system prompt into the user turn. Detect via GGUF
        # architecture metadata first (name-agnostic), falling back to the
        # filename check when metadata is unavailable. The gguf_info cache
        # makes this cheap even when called repeatedly (warmup + corrections).
        is_gemma = "gemma" in model_name
        try:
            info = get_gguf_info_cached(model_path)
            if info is not None and (info.architecture or "").lower().startswith(
                "gemma"
            ):
                is_gemma = True
        except Exception:
            pass  # metadata unavailable — keep the filename-based guess
        if is_gemma:
            return [
                {"role": "user", "content": f"{system}\n\n{wrapped}"},
            ]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": wrapped},
        ]

    def _warmup_prompt_cache(self) -> None:
        """Pre-fill llama-server's KV cache so the first real correction doesn't
        pay full prompt-evaluation cost.

        Sends ``parallel`` concurrent /v1/chat/completions requests covering
        all distinct correction mode prompts configured across hotkeys
        with cache_prompt=True. llama-server's prompt cache is prefix-based
        and per-slot, so warming distinct prompts across slots ensures
        prompt cache hits regardless of which mode or slot is used first.

        Best-effort: failures are logged but never raised, so a broken warmup
        can't prevent model load from completing.
        """
        try:
            parallel_slots = self._get_param("parallel", 4)
            strengths = []
            for hotkey in self.cfg.get("hotkeys", []):
                if isinstance(hotkey, dict) and hotkey.get("strength"):
                    s = hotkey["strength"]
                    if s not in strengths:
                        strengths.append(s)
            fallback_strength = self.cfg.get("streaming_strength", "full_correction")
            if fallback_strength not in strengths:
                strengths.append(fallback_strength)
            if not strengths:
                strengths = ["full_correction"]

            # Assign each distinct strength to at least one parallel slot
            task_strengths = []
            for s in strengths:
                task_strengths.append(s)
            idx = 0
            while len(task_strengths) < parallel_slots:
                task_strengths.append(strengths[idx % len(strengths)])
                idx += 1
            task_strengths = task_strengths[:parallel_slots]

            log(
                f"[{self.label}] KV cache warmup — pre-filling {parallel_slots} "
                f"slot(s) with {len(strengths)} distinct mode prompt(s): {', '.join(strengths)}"
            )
            url = self._chat_url()
            session = self._get_session()

            def _warm_one(strength_for_slot: str) -> None:
                payload = {
                    "messages": self._build_correction_messages(
                        "warmup",
                        None,
                        strength_for_slot,
                    ),
                    "max_tokens": 1,
                    "cache_prompt": True,
                }
                session.post(url, json=payload, timeout=10)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=parallel_slots
            ) as ex:
                list(ex.map(_warm_one, task_strengths))
        except Exception as e:
            log(f"[{self.label}] KV cache warmup failed (non-fatal): {e}")

    def is_loaded(self) -> bool:
        proc = self.server_process
        return proc is not None and proc.poll() is None

    def should_retry_load(self) -> bool:
        """True when load failed but retrying makes sense.

        Returns True when the model file exists on disk but the server
        process is not running / not accepting requests. Returns False
        when already loaded, already loading, or when the configured
        model path is empty / missing — callers handle the missing-file
        case via separate deferred-retry timers.
        """
        if self.is_loaded() or self.loading:
            return False
        model_path = self.cfg.get(self.model_path_key, "")
        if not model_path or not Path(model_path).exists():
            return False
        return True

        # ── load ──────────────────────────────────────────────────────────────

    def load_model(
        self,
        force_cpu: bool = False,
        retry_missing_path: bool = False,
        disable_mtp: bool = False,
        disable_flash_attn: bool = False,
    ) -> bool:
        # Reset the not-found flag at the start of each attempt
        self._last_load_failed_not_found = False

        with self._lock:
            if self.loading:
                return False
            self.loading = True
            self._load_cancel_event = threading.Event()
            cancel_evt = self._load_cancel_event
            self._load_generation += 1
            current_gen = self._load_generation

        # If already loaded and ready with current server process, nothing to do
        if self.is_loaded() and not force_cpu:
            with self._lock:
                if self._load_generation == current_gen:
                    self.loading = False
            return True

        self.status_changed.emit("Loading…")

        # Close any previous log file before opening a new one
        if self.log_file:
            try:
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None

        model_path = self.cfg.get(self.model_path_key, "")
        if not model_path:
            with self._lock:
                if self._load_generation == current_gen:
                    self.loading = False
            self.status_changed.emit("No model file configured")
            return False

        if not Path(model_path).exists():
            with self._lock:
                if self._load_generation == current_gen:
                    self.loading = False
            self._last_load_failed_not_found = True
            msg = (
                "Model file not found — will retry"
                if retry_missing_path
                else "Model file not found"
            )
            self.status_changed.emit(msg)
            return False


        self.status_changed.emit("Starting server…")
        log(f"[{self.label}] Loading model: {model_path}")

        # Resolve llama-server path. The shipped build has `llama-server` inside
        # a sibling folder like `llama-b8728-bin-win-cuda-12.4-x64/`, not the
        # legacy `llama_cpp/` dir. Scan SCRIPT_DIR for any `llama*/llama-server`
        # so the app is plug-and-play for users who just unzipped the release.
        server_path = self.cfg.get("llama_server_path", "")
        if not server_path or not Path(server_path).exists():
            server_path = _find_shipped_llama_server()
            if server_path:
                log(f"[{self.label}] Auto-detected llama-server: {server_path}")
                # Persist so the auto-detect only happens once
                self.cfg.set("llama_server_path", server_path)
            else:
                self.loading = False
                self.status_changed.emit(
                    "llama-server not found — run download_backend to install it"
                )
                return False
        # Guard: reject non-executable paths (e.g. .py files from corrupted config)
        elif WINDOWS and not server_path.lower().endswith(".exe"):
            log(
                f"[{self.label}] Configured llama_server_path is not an executable: "
                f"{server_path} — falling back to auto-detection"
            )
            self.cfg.set("llama_server_path", "")
            server_path = _find_shipped_llama_server()
            if server_path:
                log(f"[{self.label}] Auto-detected llama-server: {server_path}")
                self.cfg.set("llama_server_path", server_path)
            else:
                self.loading = False
                self.status_changed.emit(
                    "llama-server not found — run download_backend to install it"
                )
                return False

        gpu_detected = has_nvidia()
        log(f"[{self.label}] GPU detection: has_nvidia()={gpu_detected}")
        gpu_layers = 0 if force_cpu else self._get_param("gpu_layers", 999)
        if force_cpu:
            log(f"[{self.label}] force_cpu=True — overriding gpu_layers to 0")
        elif not gpu_detected and gpu_layers > 0:
            log(
                f"[{self.label}] nvidia-smi not found but gpu_layers={gpu_layers} from config — attempting GPU (error recovery will retry CPU on failure)"
            )
        log(f"[{self.label}] Using gpu_layers={gpu_layers}")
        # Offline GGUF metadata (best-effort). Drives the generic hard-prime
        # template sanitizer and the default ctx-size derivation below. Any
        # failure just means "no metadata" — both features degrade to their
        # base behavior (no template override / configured ctx-size).
        info = None
        try:
            info = get_gguf_info_cached(model_path)
        except Exception as e:
            log(
                f"[{self.label}] GGUF metadata unavailable ({e}) — continuing "
                f"without template/ctx tuning"
            )

        ctx_auto = self._get_param("context_size_auto", True)
        ctx = self._get_param("context_size", 12800)
        # n_ctx policy: when context_size_auto is True, derive from model's
        # training context (capped at 12800, floored at 2048).
        if ctx_auto and info is not None and info.n_ctx_train:
            ctx = max(min(info.n_ctx_train, 12800), 2048)
            log(
                f"[{self.label}] Derived --ctx-size {ctx} from GGUF "
                f"n_ctx_train={info.n_ctx_train}"
            )

        # Proactive VRAM pre-check: estimate memory requirements and warn if tight
        if gpu_detected and gpu_layers > 0:
            free_vram = query_free_vram_mb()
            est_vram = estimate_model_vram_mb(model_path, ctx)
            if free_vram is not None and est_vram is not None:
                log(
                    f"[{self.label}] VRAM pre-check: free={free_vram} MB, "
                    f"estimated_required={est_vram} MB"
                )
                if est_vram > free_vram:
                    warn = (
                        f"Model estimated memory ({est_vram} MB) exceeds available free VRAM "
                        f"({free_vram} MB). GPU offload may be partial or spill to CPU."
                    )
                    log(f"[{self.label}] WARNING: {warn}")

        host = self.cfg.get("server_host", "127.0.0.1")
        port = self.cfg.get("server_port", 8080) + self.port_offset
        threads = self._get_param("threads", -1)
        batch_size = self._get_param("batch_size", DEFAULT_CONFIG["batch_size"])
        ubatch_size = self._get_param("ubatch_size", 512)
        flash_attn = False if disable_flash_attn else self._get_param("flash_attn", True)
        mtp_enabled = False if disable_mtp else self._get_param("mtp_enabled", DEFAULT_CONFIG["mtp_enabled"])
        mtp_max_draft = self._get_param("mtp_max_draft", DEFAULT_CONFIG["mtp_max_draft"])
        mtp_min_draft = self._get_param("mtp_min_draft", DEFAULT_CONFIG["mtp_min_draft"])
        mtp_p_min = self._get_param("mtp_p_min", DEFAULT_CONFIG["mtp_p_min"])

        # Pass all sampling defaults on the CLI too. llama-server uses these as
        # fallbacks when a request omits a given field, and some endpoints (e.g.
        # /completion from non-SDK callers) only honor CLI values. The per-request
        # payloads still override these when set — this just prevents hardcoded
        # server defaults from masking user settings.
        cmd = [
            server_path,
            "--model",
            model_path,
            "--ctx-size",
            str(ctx),
            "--n-gpu-layers",
            str(gpu_layers),
            "--threads",
            str(threads),
            "--threads-batch",
            str(self._get_param("threads_batch", -1)),
            "--batch-size",
            str(batch_size),
            "--ubatch-size",
            str(ubatch_size),
            "--flash-attn",
            "on" if flash_attn else "off",
            "--host",
            host,
            "--port",
            str(port),
            "--parallel",
            str(self._get_param("parallel", 4)),
            "--reasoning",
            "off",
            "--reasoning-budget",
            "0",
            "--no-warmup",
            "--cache-reuse",
            "64",
            "--temp",
            str(self._get_param("temperature", 0.1)),
            "--top-k",
            str(self._get_param("top_k", 40)),
            "--top-p",
            str(self._get_param("top_p", 0.95)),
            "--min-p",
            str(self._get_param("min_p", 0.05)),
            "--repeat-penalty",
            str(self._get_param("repeat_penalty", 1.0)),
        ]

        # Generic hard-prime sanitization (replaces the old LFM-2.5 path
        # special case): some chat templates ALWAYS prime <think> at the
        # generation prompt and ignore --reasoning off / payload think flags,
        # so every request burns its budget on reasoning with empty content.
        # When the embedded template hard-primes thinking, ship a sanitized
        # byte-exact copy (prime stripped) via --chat-template-file. Gated /
        # conditional thinking is left untouched — the base suppression
        # (--reasoning off + --reasoning-budget + strip regexes) handles it.
        # Any failure here degrades to "no override" + base suppression.
        # Single scan: sanitize_template both detects the hard prime
        # (sanitized != original) and produces the stripped copy.
        sanitized = None
        template_str = None
        if info is not None and info.chat_template and info.reasoning_capable:
            template_str = info.chat_template
            sanitized = sanitize_template(template_str)
        if sanitized is not None and sanitized != template_str:
            try:
                sanitized_path = write_sanitized_template(
                    sanitized, model_path
                )
                cmd.extend(["--chat-template-file", str(sanitized_path)])
                self._sanitized_template_used = True
                self._sanitized_template_str = sanitized
                log(
                    f"[{self.label}] Hard-primed thinking template "
                    f"detected — using sanitized chat template: "
                    f"{sanitized_path}"
                )
            except Exception as e:
                log(
                    f"[{self.label}] WARNING: failed to write sanitized "
                    f"chat template ({e}) — continuing with base "
                    f"suppression"
                )
        else:
            log(
                f"[{self.label}] No hard-primed thinking template — no "
                f"--chat-template-file override"
            )

        if self._get_param("seed", -1) != -1:
            cmd.extend(["--seed", str(self._get_param("seed", -1))])
        
        rope_base = self._get_param("rope_freq_base", 0.0)
        if rope_base > 0.0:
            cmd.extend(["--rope-freq-base", str(rope_base)])
            
        rope_scale = self._get_param("rope_freq_scale", 0.0)
        if rope_scale > 0.0:
            cmd.extend(["--rope-freq-scale", str(rope_scale)])

        cache_k = self._get_param("kv_cache_type_k", "")
        if cache_k:
            cmd.extend(["--cache-type-k", cache_k])
            
        cache_v = self._get_param("kv_cache_type_v", "")
        if cache_v:
            cmd.extend(["--cache-type-v", cache_v])
        
        if mtp_enabled:
            draft_model_path = _find_mtp_draft_model(model_path)
            if draft_model_path and Path(draft_model_path).exists():
                draft_ngl = "0" if force_cpu else str(gpu_layers)
                cmd.extend([
                    "--model-draft",
                    str(draft_model_path),
                    "--n-gpu-layers-draft",
                    draft_ngl,
                    "--spec-type",
                    "draft-mtp",
                    "--spec-draft-n-max",
                    str(mtp_max_draft),
                    "--spec-draft-n-min",
                    str(mtp_min_draft),
                    "--spec-draft-p-min",
                    str(mtp_p_min),
                ])
                log(f"[{self.label}] Sibling MTP draft model active: {draft_model_path}")
            else:
                cmd.extend([
                    "--spec-type",
                    "draft-mtp",
                    "--spec-draft-n-max",
                    str(mtp_max_draft),
                    "--spec-draft-n-min",
                    str(mtp_min_draft),
                    "--spec-draft-p-min",
                    str(mtp_p_min),
                ])

        # NOTE: frequency-penalty and presence-penalty are omitted from CLI
        # because not all llama-server builds support them. They are still
        # sent in every API payload (see make_stream_worker) so user
        # settings are honoured for all requests.

        log(f"[{self.label}] Server command: {' '.join(cmd)}")

        try:
            kwargs: dict = {}
            if WINDOWS:
                kwargs["creationflags"] = 0x08000000

            # Ensure CUDA runtime DLLs are on PATH for GPU acceleration
            if WINDOWS and gpu_layers > 0:
                env = os.environ.copy()
                server_dir = str(Path(server_path).parent)
                cuda_search = [
                    server_dir,
                    os.path.expandvars(
                        r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.4\bin"
                    ),
                    os.path.expandvars(
                        r"%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.6\bin"
                    ),
                ]
                # Search for cudart64_12.dll in common locations
                for d in Path(server_dir).parent.iterdir():
                    if d.is_dir() and "cuda" in d.name.lower():
                        cuda_search.append(str(d))
                # Also check Ollama bundled CUDA
                ollama_cuda = (
                    Path(os.path.expandvars(r"%LOCALAPPDATA%"))
                    / "Programs"
                    / "Ollama"
                    / "lib"
                    / "ollama"
                    / "cuda_v12"
                )
                if ollama_cuda.exists():
                    cuda_search.append(str(ollama_cuda))
                # Search broader Ollama / AnythingLLM locations
                anything_llm = (
                    Path(os.path.expandvars(r"%APPDATA%"))
                    / "AnythingLLM"
                    / "resources"
                    / "ollama"
                    / "lib"
                    / "ollama"
                    / "cuda_v12"
                )
                if anything_llm.exists():
                    cuda_search.append(str(anything_llm))
                extra = [
                    d
                    for d in cuda_search
                    if Path(d).exists() and d not in env.get("PATH", "")
                ]
                if extra:
                    env["PATH"] = ";".join(extra) + ";" + env.get("PATH", "")
                    log(f"[{self.label}] Added CUDA paths to PATH: {extra}")
                kwargs["env"] = env

            self.server_log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_file = open(self.server_log_path, "w", encoding="utf-8")
            self.server_process = subprocess.Popen(
                cmd, stdout=self.log_file, stderr=self.log_file, **kwargs
            )
            self._job_handle = _create_job_object_for_subprocess(self.server_process)

            # Close Python's write handle immediately to prevent sharing lock issues on Windows
            try:
                self.log_file.close()
            except Exception:
                pass
            self.log_file = None

            load_started_at = time.monotonic()
            load_deadline = load_started_at + 180.0
            next_status_at = load_started_at + 15.0
            while time.monotonic() < load_deadline:
                if cancel_evt.is_set() or self._load_generation != current_gen:
                    log(f"[{self.label}] load_model cancelled by subsequent request/unload")
                    try:
                        if self.server_process:
                            self.server_process.terminate()
                    except Exception:
                        pass
                    with self._lock:
                        if self._load_generation == current_gen:
                            self.loading = False
                    return False

                _proc = self.server_process
                if _proc is not None and _proc.poll() is not None:
                    if cancel_evt.is_set() or self._load_generation != current_gen:
                        with self._lock:
                            if self._load_generation == current_gen:
                                self.loading = False
                        return False
                    # Dump server log into app_debug.log for easier diagnosis
                    tail = ""
                    try:
                        tail = self.server_log_path.read_text(encoding="utf-8", errors="replace")[
                            -2000:
                        ]
                        log(f"[{self.label}] server_log.txt tail:\n{tail}")
                    except Exception:
                        pass
                    if tail:
                        raise RuntimeError(f"Server exited immediately — see {self.server_log_path}:\n{tail}")
                    raise RuntimeError(f"Server exited immediately — see {self.server_log_path}")
                try:
                    if requests.get(self._health_url(), timeout=1).status_code == 200:
                        break
                except requests.RequestException:
                    pass
                now = time.monotonic()
                if now >= next_status_at:
                    elapsed = int(now - load_started_at)
                    if not cancel_evt.is_set() and self._load_generation == current_gen:
                        self.status_changed.emit(f"Loading… ({elapsed}s)")
                    next_status_at += 15.0
                time.sleep(0.15)
            else:
                raise RuntimeError("Server did not start within 180 s")

            if cancel_evt.is_set() or self._load_generation != current_gen:
                try:
                    if self.server_process:
                        self.server_process.terminate()
                except Exception:
                    pass
                with self._lock:
                    if self._load_generation == current_gen:
                        self.loading = False
                return False

            name = friendly_name(model_path)
            self._warmup_prompt_cache()
            self._server_ready = True
            self.mark_used()
            with self._lock:
                if self._load_generation == current_gen:
                    self.loading = False
            self.status_changed.emit(f"Ready — {name}")
            self.model_loaded.emit()
            log(f"[{self.label}] Model ready: {name}")

            # Determine actual loaded backend type and offloading status
            self.actual_backend_type = "cpu"
            if gpu_layers > 0:
                try:
                    log_content = self.server_log_path.read_text(encoding="utf-8", errors="replace")

                    # Look for which backends were loaded.
                    # Patterns cover multiple llama.cpp log format generations:
                    #   - Old format:        "loaded CUDA backend"
                    #   - b9577+ device_info: "  - CUDA0   :" (via %-8s formatting)
                    #   - b9577+ system_info: "CUDA : CUDA0 = 1" (via llama_print_system_info)
                    #   - Debug/internal:     "ggml_cuda"
                    loaded_cuda = (
                        "loaded CUDA backend" in log_content
                        or "- CUDA" in log_content       # matches "- CUDA0   :" in device_info
                        or "ggml_cuda" in log_content
                        or "CUDA :" in log_content       # matches system_info (b9577+)
                    )
                    loaded_vulkan = (
                        "loaded Vulkan backend" in log_content
                        or "- Vulkan" in log_content     # matches "- Vulkan0 :" in device_info
                        or "ggml_vulkan" in log_content
                        or "VULKAN :" in log_content     # old system_info format
                        or "Vulkan :" in log_content     # b9577+ system_info (title case)
                    )
                    loaded_metal = (
                        "loaded Metal backend" in log_content
                        or "- Metal" in log_content      # matches "- Metal   :" in device_info
                        or "ggml_metal" in log_content
                        or "METAL :" in log_content      # old system_info format
                        or "Metal :" in log_content      # b9577+ system_info (title case)
                    )
                    loaded_rocm = (
                        "loaded ROCm backend" in log_content
                        or "- ROCm" in log_content       # matches "- ROCm0   :" in device_info
                        or "ggml_rocm" in log_content
                        or "ROCM :" in log_content       # old system_info format
                        or "ROCm :" in log_content       # b9577+ system_info (mixed case)
                        or "HIP :" in log_content        # alternative ROCm/HIP backend name
                    )
                    loaded_sycl = (
                        "loaded Sycl backend" in log_content
                        or "- Sycl" in log_content       # old device_info format
                        or "- SYCL" in log_content       # matches "- SYCL0   :" in device_info (b9577+)
                        or "ggml_sycl" in log_content
                        or "SYCL :" in log_content       # system_info format
                    )

                    if loaded_cuda:
                        self.actual_backend_type = "cuda"
                    elif loaded_vulkan:
                        self.actual_backend_type = "vulkan"
                    elif loaded_metal:
                        self.actual_backend_type = "metal"
                    elif loaded_rocm:
                        self.actual_backend_type = "rocm"
                    elif loaded_sycl:
                        self.actual_backend_type = "sycl"

                    # Also count actual offloaded layers
                    offloaded_count = 0
                    total_layers = 0
                    for line in log_content.splitlines():
                        if "offloaded" in line and "layers to GPU" in line:
                            # e.g., "load_tensors: offloaded 36/36 layers to GPU"
                            try:
                                parts = (
                                    line.split("offloaded")[1]
                                    .split("layers")[0]
                                    .strip()
                                    .split("/")
                                )
                                if len(parts) == 2:
                                    offloaded_count = int(parts[0])
                                    total_layers = int(parts[1])
                            except Exception:
                                pass

                    log(
                        f"[{self.label}] GPU detection after server start: gpu_layers requested={gpu_layers}, backend loaded={self.actual_backend_type}, layers offloaded={offloaded_count}/{total_layers}"
                    )

                    # Stash whether the log proves a CPU fallback. The actual
                    # warning is deferred until after the /props probe below:
                    # llama.cpp b10375 dropped the verbose backend/device_info
                    # banners (verified in real b10375 server logs), so a bare
                    # log scan alone can no longer prove GPU presence/absence.
                    # The /props response is authoritative — warn only when it
                    # also reports CPU while GPU offload was requested.
                    self._pending_gpu_warning: dict | None = None
                    if self.actual_backend_type == "cpu" and (
                        "loaded CPU backend" in log_content or "CPU :" in log_content
                    ):
                        # Extract CUDA-related lines from server log for diagnosis
                        cuda_lines = [
                            line
                            for line in log_content.splitlines()
                            if any(
                                kw in line.lower()
                                for kw in ("cuda", "gpu", "vulkan", "backend", "ggml")
                            )
                        ]
                        self._pending_gpu_warning = {
                            "loaded_cpu": True,
                            "log_tail": (
                                "\n".join(cuda_lines[-10:])
                                if cuda_lines
                                else "(no GPU-related lines found in server log)"
                            ),
                        }
                except Exception as e:
                    log(
                        f"[{self.label}] Failed to inspect backend loading logs (non-fatal): {e}"
                    )

            # Ask the server for the *actual* loaded context size. The user's
            # requested --ctx-size is a ceiling, not a guarantee — some GGUFs
            # cap n_ctx lower in their metadata. Chunking math must use the
            # real value or we'll overflow and the model drops tail tokens.
            try:
                pr = requests.get(self._base_url() + "/props", timeout=3)
                if pr.ok:
                    jp = pr.json()
                    # llama.cpp exposes n_ctx either at the top level or under
                    # default_generation_settings depending on server version
                    n_ctx = jp.get("default_generation_settings", {}).get(
                        "n_ctx"
                    ) or jp.get("n_ctx")
                    if isinstance(n_ctx, int) and n_ctx > 0:
                        self.actual_ctx_size = n_ctx
                        log(f"[{self.label}] /props reports n_ctx={n_ctx}")
                    # Stash thinking support + the server's resolved chat
                    # template for UI/feature decisions. chat_template_caps is
                    # a dict populated for nearly every model — read its
                    # support keys, not mere presence. Dual-key defensive read
                    # (the exact b10375 key name is unverifiable offline).
                    caps = jp.get("chat_template_caps") or {}
                    self.thinking_supported = bool(
                        caps.get("supports_thinking", caps.get("supports_reasoning", False))
                    )
                    self.model_chat_template = jp.get("chat_template")
                    log(
                        f"[{self.label}] /props thinking_supported="
                        f"{self.thinking_supported}"
                    )
                    # Inspect /props device/backend fields to augment GPU detection
                    # when log scraper did not find legacy startup strings
                    devices = jp.get("devices") or []
                    device_info = str(jp.get("device_info", "")).lower()
                    backend_name = str(jp.get("backend", "")).lower()
                    default_gen = jp.get("default_generation_settings", {})
                    props_str = json.dumps(jp).lower()
                    has_cuda_prop = (
                        any("cuda" in str(d).lower() for d in devices)
                        or "cuda" in device_info
                        or "cuda" in backend_name
                        or "cuda" in props_str
                        or int(jp.get("n_gpu_layers", default_gen.get("n_gpu_layers", 0)) or 0) > 0
                    )
                    if has_cuda_prop and self.actual_backend_type == "cpu" and gpu_layers > 0:
                        self.actual_backend_type = "cuda"
                        log(f"[{self.label}] GPU detection updated via /props: backend loaded=cuda")
                    self._props_probed = True
            except Exception as e:
                log(f"[{self.label}] /props fetch failed (non-fatal): {e}")

            # Authoritative GPU presence probe via `<server> --list-devices`:
            # Modern llama.cpp (b10375+) stopped printing backend banners in logs
            # and /props no longer returns device info. --list-devices is the reliable signal.
            if gpu_layers > 0 and self.actual_backend_type == "cpu":
                gpu_probe = _probe_gpu_devices(server_path)
                if gpu_probe["cuda"]:
                    self.actual_backend_type = "cuda"
                    log(f"[{self.label}] GPU detection updated via --list-devices: backend loaded=cuda")
                elif gpu_probe["vulkan"]:
                    self.actual_backend_type = "vulkan"
                    log(f"[{self.label}] GPU detection updated via --list-devices: backend loaded=vulkan")
                elif gpu_probe["metal"]:
                    self.actual_backend_type = "metal"
                    log(f"[{self.label}] GPU detection updated via --list-devices: backend loaded=metal")

            # Deferred GPU warning: emitted only now, after /props and --list-devices
            # had their chance to prove GPU presence.
            if gpu_layers > 0 and self.actual_backend_type == "cpu":
                pending = getattr(self, "_pending_gpu_warning", None)
                if pending and pending.get("loaded_cpu"):
                    warn_msg = (
                        f"GPU offloading requested (gpu_layers={gpu_layers}) but llama-server "
                        f"loaded CPU backend. Check your CUDA installation.\n"
                        f"Server log GPU lines:\n{pending.get('log_tail')}"
                    )
                    log(f"[{self.label}] WARNING: {warn_msg}")
                    self.model_warning.emit(
                        "GPU requested but CPU loaded. Check llama-server binary has CUDA support. "
                        "See server_log.txt for details."
                    )
                elif self._props_probed:
                    warn_msg = (
                        f"GPU offloading requested (gpu_layers={gpu_layers}) but no GPU backend "
                        f"was detected in server log, /props, or --list-devices. The llama-server binary "
                        f"may not have GPU support."
                    )
                    log(f"[{self.label}] WARNING: {warn_msg}")
                    self.model_warning.emit(
                        "GPU requested but no GPU backend found. Your llama-server binary may lack GPU support."
                    )
            # Post-load validation of a sanitized template override: ask the
            # server to render a probe prompt and require no residual
            # UNBALANCED think open-tag. On any failure fall back to base
            # suppression (never relaunch) and clear the override flag.
            if self._sanitized_template_used and self._sanitized_template_str:
                try:
                    vr = self._get_session().post(
                        self._base_url() + "/apply-template",
                        json={
                            "messages": [{"role": "user", "content": "hello"}],
                            "chat_template": self._sanitized_template_str,
                            "add_generation_prompt": True,
                        },
                        timeout=10,
                    )
                    rendered_prompt = ""
                    if vr.ok:
                        rendered_prompt = vr.json().get("prompt", "") or ""
                    if not vr.ok or _has_unbalanced_think_open(rendered_prompt):
                        log(
                            f"[{self.label}] WARNING: sanitized-template "
                            f"validation inconclusive; flag cleared — model "
                            f"keeps running the sanitized copy, base "
                            f"suppression still active"
                        )
                        self._sanitized_template_used = False
                except Exception as e:
                    log(
                        f"[{self.label}] WARNING: sanitized-template "
                        f"validation inconclusive ({e}); flag cleared — "
                        f"model keeps running the sanitized copy, base "
                        f"suppression still active"
                    )
                    self._sanitized_template_used = False

            # Warn if the model is too small for reliable patch-mode output.
            # Tiny models (<1B) produce tokenizer garbage or echo few-shot
            # examples verbatim — the echo-guard will catch it at correction
            # time, but a heads-up at load time is friendlier than a silent
            # "try a larger model" error after the user's first attempt.
            size_b = _model_size_billions(model_path)
            if size_b is not None and size_b < _MIN_RELIABLE_MODEL_B:
                warn = (
                    f"'{name}' is ~{size_b:g}B parameters. Models smaller than "
                    f"~1B may produce garbled or echoed output. Recommended: "
                    f"Gemma 4 E2B or larger."
                )
                log(f"[{self.label}] WARNING: {warn}")
                self.model_warning.emit(warn)
            return True

        except Exception as e:
            if cancel_evt.is_set() or self._load_generation != current_gen:
                log(f"[{self.label}] load_model aborted due to cancellation ({e})")
                with self._lock:
                    if self._load_generation == current_gen:
                        self.loading = False
                return False

            import traceback

            tb = traceback.format_exc()
            log(f"[{self.label}] load_model failed: {e}\n{tb}")
            with self._lock:
                if self._load_generation == current_gen:
                    self.loading = False
            self.unload_model()
            if gpu_layers > 0 and any(
                kw in str(e).lower()
                for kw in (
                    "cuda error",
                    "out of memory",
                    "gpu oom",
                    "cuda out of memory",
                )
            ):
                log(f"[{self.label}] CUDA error — retrying CPU-only")
                self.status_changed.emit("GPU error — retrying CPU…")
                return self.load_model(force_cpu=True)
            if mtp_enabled and not disable_mtp:
                log("[ModelManager] Speculative decoding failed on startup — falling back to non-speculative mode")
                self.status_changed.emit("MTP error — retrying non-speculative…")
                return self.load_model(
                    force_cpu=force_cpu,
                    retry_missing_path=retry_missing_path,
                    disable_mtp=True,
                    disable_flash_attn=disable_flash_attn,
                )
            if (
                flash_attn
                and not disable_flash_attn
                and any(
                    kw in str(e).lower()
                    for kw in (
                        "flash",
                        "flash-attn",
                        "flash_attn",
                        "flash_attention",
                        "flash attention",
                    )
                )
            ):
                log(f"[{self.label}] Server startup failed with flash-attn — retrying without flash-attn")
                self.status_changed.emit("Flash-attn error — retrying without…")
                return self.load_model(
                    force_cpu=force_cpu,
                    retry_missing_path=retry_missing_path,
                    disable_mtp=disable_mtp,
                    disable_flash_attn=True,
                )
            self.status_changed.emit(f"Load error: {str(e)[:70]}")
            return False

    def unload_model(self):
        with self._lock:
            self._load_cancel_event.set()
            self._load_generation += 1
            self.loading = False
            self._server_ready = False
            if self.server_process:
                try:
                    self.server_process.terminate()
                    self.server_process.wait(timeout=5)
                except Exception:
                    try:
                        self.server_process.kill()
                    except Exception:
                        pass
                self.server_process = None
            if hasattr(self, "_job_handle") and self._job_handle:
                try:
                    ctypes.windll.kernel32.CloseHandle(self._job_handle)
                except Exception:
                    pass
                self._job_handle = None
            if self.log_file:
                try:
                    self.log_file.close()
                except Exception:
                    pass
                self.log_file = None
            self.last_used = None
            # Drop any persistent HTTP session — server is gone, no point
            # holding the connection pool open.
            self.close_session()
        self.status_changed.emit("Model unloaded")
        self.model_unloaded.emit()

    def _recycle_server(self) -> bool:
        """Recycle (unload + reload) an unresponsive llama-server instance.

        Protected by a CAS flag so multiple parallel chunk workers don't
        trigger concurrent, thrashing reload cycles.
        """
        with self._lock:
            if self._recycle_in_progress or not self.is_loaded():
                return False
            self._recycle_in_progress = True

        try:
            log(f"[{self.label}] Freeze/timeout detected — recycling server...")
            self.unload_model()
            success = self.load_model()
            ready = bool(success and self.is_loaded())
            log(f"[{self.label}] Server recycle complete — ready={ready}")
            return ready
        except Exception as e:
            log(f"[{self.label}] Server recycle failed with exception: {e}")
            return False
        finally:
            with self._lock:
                self._recycle_in_progress = False

    # ── patch correction (dict pre-pass + parallel sentence rewrite) ──────
    def correct_text_patch(
        self,
        text: str,
        custom_sys: str | None = None,
        strength: str = "full_correction",
        cancel_event: threading.Event | None = None,
        mode_prompt_override: str | None = None,
        progress_cb: "Callable[[int, int], None] | None" = None,
    ) -> CorrectionResult:
        """Three-phase correction: dict pre-pass, parallel sentence rewrite, hallucination guard.

        Returns a CorrectionResult with structured outcome.  Backward-compatible
        tuple unpacking (``result, units = ...``) still works via __iter__.
        Use ``result.text_or_none`` for the old ``is None`` check to trigger
        streaming fallback.
        """
        _t0 = time.monotonic()
        self.last_patch_error = None
        if not self.is_loaded():
            if not self.load_model():
                self.last_patch_error = "Model failed to load"
                return CorrectionResult(
                    text=text, outcome=CorrectionOutcome.MODEL_UNAVAILABLE,
                    reason="Model failed to load",
                    elapsed_s=time.monotonic() - _t0,
                )
        self.mark_used()
        self.status_changed.emit("Correcting…")
        if not text.strip():
            return CorrectionResult(
                text=text, outcome=CorrectionOutcome.CORRECTED,
                elapsed_s=time.monotonic() - _t0,
            )

        if cancel_event is not None and cancel_event.is_set():
            self.last_patch_error = "Correction cancelled by user"
            return CorrectionResult(
                text=text, outcome=CorrectionOutcome.CANCELLED,
                reason="Correction cancelled by user",
                elapsed_s=time.monotonic() - _t0,
            )

        has_windows_newlines = "\r\n" in text
        text = _normalize_newlines(text, use_windows_newlines=False)

        # ── Pipeline profile ─────────────────────────────────────────────
        # Templates (mode_prompt_override) use the permissive transform
        # profile; built-in strengths use their named profile.
        profile_name = "template_transform" if mode_prompt_override else strength
        profile = PROFILES.get(profile_name, PROFILES["full_correction"])

        # ── Phase 0a: inline masking for URLs, emails, paths ──────────────
        # Mask BEFORE the dict prepass so the prepass can never rewrite a
        # protected atom (e.g. a URL path segment that matches a map word).
        masked_entities = []

        def mask_repl(match):
            idx = len(masked_entities) + 1
            masked_entities.append(match.group(0))
            return f"__STET_PROTECTED_{idx}__"

        working_text = _INLINE_HAZARD_RE.sub(mask_repl, text)

        user_re = self._get_user_protection_re()
        if user_re is not None:
            working_text = user_re.sub(mask_repl, working_text)

        # ── Phase 0: deterministic dict pre-pass ─────────────────────────
        # Applies ONLY in spelling_only mode. The ~4300-entry typo dictionary
        # is English-only and context-blind, so in full_correction / rewrite
        # modes it can mangle proper nouns and unusual-but-valid words the
        # LLM would have kept (Decision 5 / locked constraint #6). spelling_only
        # keeps the deterministic guarantee for common typos even when the
        # model is too small to catch them all; the LLM still runs afterwards
        # to catch unknown typos the dictionary missed. Skipped whenever the
        # user has taken control via a system prompt override or a custom
        # mode prompt. Rejection-fallback dict usage (_dict_prepass on failed
        # units) and typo-aware divergence normalization are unchanged.
        if strength == "spelling_only" and not custom_sys and not mode_prompt_override:
            pre_corrected, dict_fixes = _dict_prepass(working_text)
            if dict_fixes > 0:
                log(f"[{self.label}] Dict prepass applied {dict_fixes} fixes before LLM")
        else:
            pre_corrected, dict_fixes = working_text, 0
        total_words = len(pre_corrected.split())



        # ── Phase 1: split into sentence units and rewrite in parallel ────
        # Profile-driven chunking: each mode/template gets its own chunk size.
        # With --parallel 4 slots, up to 4 units run concurrently.  Separator
        # preserves inter-unit whitespace/newlines so reassembly is lossless.
        #
        # Adaptive cap: small-context models (e.g. 4k GGUFs at parallel=4)
        # can't fit a 250-word rewrite plus thinking budget plus answer in one
        # slot; sentence-boundary chunking already guarantees cohesion at
        # 120 words. Mirrors the per-unit slot_limit computation below.
        slot_tokens = (
            self.actual_ctx_size
            if self.actual_ctx_size is not None
            else self._get_param("context_size", 12800) // self._get_param("parallel", 4)
        )
        chunk_word_cap = (
            min(profile.chunk_words, 120) if slot_tokens < 2048 else profile.chunk_words
        )

        chunks = _chunk_text_by_sentences(pre_corrected, chunk_word_cap)
        if len(chunks) > 1:
            log(
                f"[{self.label}] Patch: {len(chunks)} sentence units "
                f"({total_words} words) profile={profile_name} "
                f"chunk_words={chunk_word_cap}"
            )
        elif slot_tokens < 2048 and chunk_word_cap < profile.chunk_words:
            log(
                f"[{self.label}] Patch: slot budget {slot_tokens} tokens — "
                f"rewrite chunk cap reduced {profile.chunk_words} -> "
                f"{chunk_word_cap} words"
            )

        corrected_parts: list[tuple[str, str]] = [("", "")] * len(chunks)
        any_success = False
        any_preserved = False
        _completed = 0
        units_corrected = 0

        from stet.core.text_utils import looks_like_prose

        # Config-driven threshold — single source of truth, mirroring the
        # streaming fallback in main_window.py. The correction_modes row for
        # this strength is authoritative; fall back to the profile value when
        # the config row/field is missing.
        _modes = self.cfg.get("correction_modes", [])
        _mi = _resolve_mode_index(strength, _modes)
        threshold = (
            _modes[_mi].get("hallucination_threshold")
            if 0 <= _mi < len(_modes) and isinstance(_modes[_mi], dict)
            else None
        )
        if threshold is None:
            threshold = profile.hallucination_threshold

        max_workers = min(len(chunks), self._get_param("parallel", 4)) if chunks else 1

        shared_session = self._get_session()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        # Track which chunks have protected atoms so we can attempt span-only
        # recovery on sentinel validation failure.
        _chunks_with_sentinels: dict[int, str] = {}  # idx -> original chunk text
        non_prose_bypassed = False
        try:
            futures = {}
            for idx, (chunk_text, sep) in enumerate(chunks):
                editable_text = _INLINE_SENTINEL_RE.sub(" ", chunk_text)
                if (
                    not chunk_text.strip()
                    or not editable_text.strip()
                    or not looks_like_prose(editable_text)
                    or not looks_like_prose(chunk_text)
                ):
                    corrected_parts[idx] = (chunk_text, sep)
                    if chunk_text.strip():
                        non_prose_bypassed = True
                    _completed += 1
                    if progress_cb is not None:
                        try:
                            progress_cb(_completed, len(chunks))
                        except Exception:
                            pass
                    continue

                # Record chunks that contain sentinels for recovery.
                if _INLINE_SENTINEL_RE.search(chunk_text):
                    _chunks_with_sentinels[idx] = chunk_text

                futures[executor.submit(
                    self._rewrite_sentence_chunk,
                    chunk_text,
                    custom_sys,
                    idx + 1,
                    len(chunks),
                    strength,
                    cancel_event,
                    mode_prompt_override,
                    shared_session,
                    profile,
                )] = (idx, chunk_text, sep)

            remaining = list(futures.keys())
            while remaining:
                if cancel_event is not None and cancel_event.is_set():
                    log(f"[{self.label}] Patch: cancelled mid-correction")
                    return CorrectionResult(
                        text=text, outcome=CorrectionOutcome.CANCELLED,
                        reason="Cancelled mid-correction",
                        elapsed_s=time.monotonic() - _t0,
                    )

                done, _pending = concurrent.futures.wait(
                    remaining,
                    timeout=0.2,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )

                for future in done:
                    remaining.remove(future)
                    idx, chunk_text, sep = futures[future]
                    try:
                        corrected = future.result()
                    except Exception as e:
                        err_msg = f"Unit {idx + 1} model generation error ({e})"
                        self.last_patch_error = err_msg
                        log(f"[{self.label}] Patch: {err_msg}")
                        corrected = None

                    if corrected is None:
                        # Unit failed — keep original text for this unit.
                        corrected_parts[idx] = (chunk_text, sep)
                        if _INLINE_SENTINEL_RE.search(chunk_text):
                            any_preserved = True
                        continue

                    corrected = _normalize_newlines(
                        corrected, use_windows_newlines=False
                    )

                    # Sentinel survival: every __STET_PROTECTED_N__ in the
                    # original chunk must survive in the same order with the
                    # same count.  A model that duplicates, reorders, or drops
                    # a sentinel would corrupt hazard unmasking.
                    _chunk_sentinels = _INLINE_SENTINEL_RE.findall(chunk_text)
                    if _chunk_sentinels:
                        _found = _INLINE_SENTINEL_RE.findall(corrected)
                        if _found != _chunk_sentinels:
                            # Try restoring mangled sentinels before rejecting.
                            _recovered = recover_sentinels(corrected, _chunk_sentinels)
                            _found_after = _INLINE_SENTINEL_RE.findall(_recovered)
                            if _found_after == _chunk_sentinels:
                                log(f"[{self.label}] Patch unit {idx + 1}: recovered mangled sentinel(s)")
                                corrected = _recovered
                            else:
                                # ── Span-only recovery ──────────────────
                                # The LLM mangled protected atoms.  Instead
                                # of rejecting the whole chunk, try sending
                                # only the editable prose spans to the LLM.
                                err_msg = f"Unit {idx + 1} rejected: protected placeholders/entities mangled"
                                self.last_patch_error = err_msg
                                log(f"[{self.label}] Patch {err_msg} — attempting span-only recovery")

                                # Build per-chunk entity list from sentinel indices.
                                # chunk_text has __STET_PROTECTED_N__ where N is
                                # 1-based into masked_entities.  Extract the
                                # indices present in this chunk and build a local
                                # entity list (0-based for the recovery method).
                                _chunk_sentinel_indices = [
                                    int(m.group(1))
                                    for m in _INLINE_SENTINEL_CAPTURE_RE.finditer(chunk_text)
                                ]
                                _chunk_entities = [
                                    masked_entities[si - 1]
                                    for si in _chunk_sentinel_indices
                                    if 0 < si <= len(masked_entities)
                                ]

                                span_result = self._correct_spans_around_atoms(
                                    chunk_text,
                                    _chunk_entities,
                                    custom_sys,
                                    idx + 1,
                                    len(chunks),
                                    strength,
                                    cancel_event,
                                    mode_prompt_override,
                                    shared_session,
                                    profile,
                                )
                                if span_result is not None:
                                    # Apply standard safety guards on the
                                    # reassembled result — span recovery
                                    # validates individual spans, but the
                                    # reassembled whole may still diverge.
                                    #
                                    # Compare against the UNMASKED chunk
                                    # (original text with real URLs/paths),
                                    # not the masked chunk_text which has
                                    # __STET_PROTECTED_n__ tokens.  Long
                                    # URLs cause false hallucination rejections
                                    # when compared against sentinel tokens.
                                    _unmasked_chunk = chunk_text
                                    for _si in _chunk_sentinel_indices:
                                        if 0 < _si <= len(masked_entities):
                                            _unmasked_chunk = _unmasked_chunk.replace(
                                                f"__STET_PROTECTED_{_si}__",
                                                masked_entities[_si - 1],
                                            )
                                    _span_ratio = _hallucination_ratio(_unmasked_chunk, span_result, strength)
                                    if _span_ratio > threshold:
                                        log(
                                            f"[{self.label}] Patch unit {idx + 1}: "
                                            f"span recovery result diverged ({_span_ratio:.2f} > {threshold}) — keeping original"
                                        )
                                        corrected_parts[idx] = (chunk_text, sep)
                                        any_preserved = True
                                        continue

                                    if not _post_splice_sanity(
                                        _unmasked_chunk, span_result,
                                        min_ratio=profile.min_word_ratio,
                                        max_ratio=profile.max_word_ratio,
                                    ):
                                        log(
                                            f"[{self.label}] Patch unit {idx + 1}: "
                                            "span recovery failed post-splice sanity — keeping original"
                                        )
                                        corrected_parts[idx] = (chunk_text, sep)
                                        any_preserved = True
                                        continue

                                    log(f"[{self.label}] Patch unit {idx + 1}: span-only recovery succeeded")
                                    corrected = span_result
                                    corrected_parts[idx] = (corrected, sep)
                                    any_success = True
                                    units_corrected += 1
                                    _completed += 1
                                    if progress_cb is not None:
                                        try:
                                            progress_cb(_completed, len(chunks))
                                        except Exception:
                                            pass
                                    continue
                                else:
                                    log(f"[{self.label}] Patch unit {idx + 1}: span-only recovery failed — keeping original")
                                    corrected_parts[idx] = (_dict_prepass(chunk_text)[0], sep)
                                    any_preserved = True
                                    continue

                    # Reject if raw output exceeds the (config-driven) hallucination
                    # threshold. Skipped for rewrite task types: char-level divergence
                    # is the point of a rewrite, so legitimate rewrites routinely
                    # diverge >97% — the raw ratio gate is miscalibrated for them and
                    # silently reverted whole units to the original text (partial
                    # corrections with no user warning). Rewrites remain protected by
                    # the refusal/empty detector below and the post-splice word-ratio
                    # sanity check (min/max_word_ratio from the profile).
                    if (
                        profile.task_type != "rewrite"
                        and _hallucination_ratio(chunk_text, corrected, strength) > threshold
                    ):
                        err_msg = f"Unit {idx + 1} rejected: raw hallucination ratio exceeded threshold ({threshold})"
                        self.last_patch_error = err_msg
                        log(f"[{self.label}] Patch {err_msg}")
                        corrected = None
                        corrected_parts[idx] = (_dict_prepass(chunk_text)[0], sep)
                        continue

                    # Reject if the model refused or returned an empty edit
                    # (marker-wrapped "Please provide text" type output). The
                    # divergence guard cannot separate refusals from legit
                    # rewrites — distributions overlap (refusal 0.58–0.87 vs
                    # legit max ~0.687) — so use a dedicated detector.
                    if _is_refusal_or_empty(corrected, chunk_text):
                        err_msg = f"Unit {idx + 1} rejected: model refused or returned empty edit"
                        self.last_patch_error = err_msg
                        log(f"[{self.label}] Patch {err_msg}")
                        corrected_parts[idx] = (_dict_prepass(chunk_text)[0], sep)
                        continue

                    # Reject no-change declarations and spilled reasoning
                    # commentary (e.g. LFM 2.5 "The text appears to be already
                    # correct...", "I need to fix errors, improve flow..."
                    # task restatements, "Original text issues:" analysis
                    # lists). These are not edits — treat them like a refusal
                    # and keep the original. Applied to all strengths: it also
                    # catches full_correction's spliced-commentary case.
                    if _is_no_change_declaration(corrected):
                        err_msg = f"Unit {idx + 1} rejected: no-change declaration/commentary"
                        self.last_patch_error = err_msg
                        log(f"[{self.label}] Patch {err_msg}")
                        corrected_parts[idx] = (_dict_prepass(chunk_text)[0], sep)
                        continue

                    # Reject agentic tool-call output: agentic finetunes (e.g.
                    # LFM 2.5) occasionally emit <|tool_call_start|>[edit_text(
                    # input='...'...)</...> instead of the corrected sentence.
                    # The tool-call envelope is not a correction and its inner
                    # content is not trusted — keep the original text like the
                    # other rejections. Only the <|tool_call_start|> marker is
                    # checked; nothing is extracted from inside the call.
                    if "<|tool_call_start|>" in corrected:
                        err_msg = f"Unit {idx + 1} rejected: agentic tool-call output"
                        self.last_patch_error = err_msg
                        log(f"[{self.label}] Patch {err_msg}")
                        corrected_parts[idx] = (_dict_prepass(chunk_text)[0], sep)
                        continue

                    # Phase 2: hunk-level hallucination guard
                    # Profile-driven: None disables the guard (for rewrite/template modes).
                    if profile.hunk_guard_mode is not None:
                        from stet.core.text_utils import apply_hunk_guard
                        guarded_corrected = apply_hunk_guard(chunk_text, corrected, profile.hunk_guard_mode, threshold=threshold)
                    else:
                        guarded_corrected = corrected
                    if guarded_corrected != corrected:
                        log(
                            f"[{self.label}] Patch unit {idx + 1}: hunk-level "
                            "hallucination guard reverted parts of the correction"
                        )
                    corrected = guarded_corrected

                    if not _post_splice_sanity(chunk_text, corrected, min_ratio=profile.min_word_ratio, max_ratio=profile.max_word_ratio):
                        _word_ratio = len(corrected.split()) / max(1, len(chunk_text.split()))
                        log(
                            f"[{self.label}] Patch unit {idx + 1} rejected: "
                            f"post-splice sanity check failed (word ratio "
                            f"{_word_ratio:.2f} outside "
                            f"[{profile.min_word_ratio}, {profile.max_word_ratio}])"
                        )
                        corrected_parts[idx] = (_dict_prepass(chunk_text)[0], sep)
                        continue

                    if strength in {
                        "rewrite_polish",
                    } and _loses_meaningful_repetition(
                        chunk_text,
                        corrected,
                    ):
                        log(
                            f"[{self.label}] Patch unit {idx + 1}: repetition-loss "
                            "in rewrite_polish mode — log only, accepting rewrite"
                        )

                    corrected_parts[idx] = (corrected, sep)
                    any_success = True
                    units_corrected += 1
                    _completed += 1
                    if progress_cb is not None:
                        try:
                            progress_cb(_completed, len(chunks))
                        except Exception:
                            pass

        finally:
            if "futures" in locals():
                for f in futures:
                    f.cancel()
            try:
                executor.shutdown(wait=False, cancel_futures=True)
            except TypeError:
                executor.shutdown(wait=False)
        # NOTE: shared_session is the persistent session — intentionally not
        # closed here. close_session() handles lifecycle on shutdown.

        reassembled = "".join(part + sep for part, sep in corrected_parts)
        
        for i, entity in enumerate(masked_entities):
            reassembled = reassembled.replace(f"__STET_PROTECTED_{i+1}__", entity)

        _elapsed = time.monotonic() - _t0

        # If dict pre-pass changed nothing AND no unit ever succeeded, report
        # total failure so the caller falls back to streaming. Otherwise we
        # accept partial success (kept-original units are not a failure).
        if not any_success and dict_fixes == 0 and reassembled == text:
            if masked_entities:
                _reason = self.last_patch_error or "Protected unit(s) preserved original"
                log(
                    f"[{self.label}] Patch: protected unit(s) "
                    f"preserved original — outcome=UNCHANGED_PROTECTED "
                    f"atoms={len(masked_entities)} elapsed={_elapsed:.2f}s"
                )
                return CorrectionResult(
                    text=text, outcome=CorrectionOutcome.UNCHANGED_PROTECTED,
                    units_processed=len(chunks), units_corrected=0,
                    protected_atom_count=len(masked_entities),
                    reason=_reason, elapsed_s=_elapsed,
                )
            elif non_prose_bypassed or any_preserved:
                log(
                    f"[{self.label}] Patch: non-prose unit(s) "
                    f"preserved original — outcome=UNCHANGED_NON_PROSE "
                    f"elapsed={_elapsed:.2f}s"
                )
                return CorrectionResult(
                    text=text, outcome=CorrectionOutcome.UNCHANGED_NON_PROSE,
                    units_processed=len(chunks), units_corrected=0,
                    protected_atom_count=0,
                    reason="Non-prose content preserved", elapsed_s=_elapsed,
                )
            else:
                if not self.last_patch_error:
                    self.last_patch_error = "All rewrite units failed validation"
                log(
                    f"[{self.label}] Patch failed ({self.last_patch_error}) "
                    f"— outcome=FAILED_ALL_UNITS elapsed={_elapsed:.2f}s"
                )
                return CorrectionResult(
                    text=text, outcome=CorrectionOutcome.FAILED_ALL_UNITS,
                    units_processed=len(chunks), units_corrected=0,
                    reason=self.last_patch_error,
                    elapsed_s=_elapsed,
                )

        final = reassembled
        changed = final != text
        if changed:
            final = _apply_post_fixes(final, original=text, strength=strength)

        # Restore Windows/original newlines
        final = _normalize_newlines(final, has_windows_newlines)

        self.mark_used()
        self.status_changed.emit("Ready")

        outcome = CorrectionOutcome.CORRECTED if changed else CorrectionOutcome.UNCHANGED_NO_ERRORS
        if not changed:
            if masked_entities:
                outcome = CorrectionOutcome.UNCHANGED_PROTECTED
            elif non_prose_bypassed:
                outcome = CorrectionOutcome.UNCHANGED_NON_PROSE
            elif self.last_patch_error:
                outcome = CorrectionOutcome.UNCHANGED_PROTECTED

        log(
            f"[{self.label}] Patch: outcome={outcome.value} "
            f"units={len(chunks)} corrected={units_corrected} "
            f"atoms={len(masked_entities)} elapsed={_elapsed:.2f}s"
        )
        return CorrectionResult(
            text=final, outcome=outcome,
            units_processed=len(chunks), units_corrected=units_corrected,
            protected_atom_count=len(masked_entities),
            reason=self.last_patch_error or "",
            elapsed_s=_elapsed,
        )

    # ── span-only recovery ────────────────────────────────────────────────
    def _correct_spans_around_atoms(
        self,
        masked_chunk: str,
        chunk_entities: list[str],
        custom_sys: str | None,
        unit_idx: int,
        total: int,
        strength: str,
        cancel_event: threading.Event | None = None,
        mode_prompt_override: str | None = None,
        session: requests.Session | None = None,
        profile: CorrectionProfile | None = None,
    ) -> str | None:
        """Recovery path when protected-atom sentinel validation fails.

        Instead of sending the whole chunk (with fragile sentinel aliases) to
        the LLM, this method:

        1. Splits *masked_chunk* into alternating editable/protected segments
           using the sentinel regex and the explicit *chunk_entities* mapping.
        2. Expands protected regions to cover complete Markdown link constructs
           (``[text](SENTINEL)``) so the LLM never sees broken syntax.
        3. Sends only the editable prose spans to the LLM with standard safety
           guards (hallucination, refusal, corruption).
        4. Reassembles with the original protected atoms verbatim.

        Args:
            masked_chunk: Chunk text with ``__STET_PROTECTED_N__`` sentinels.
            chunk_entities: Original entities for the sentinels in this chunk,
                indexed by sentinel position within the chunk (0-based).
            All other args forwarded to _rewrite_sentence_chunk.

        Returns the corrected chunk text, or None if recovery fails.
        """
        if not masked_chunk:
            return None

        sentinel_re = _INLINE_SENTINEL_RE
        sentinels_in_chunk = sentinel_re.findall(masked_chunk)
        if not sentinels_in_chunk or not chunk_entities:
            return None

        # Split masked chunk into editable parts (between sentinels).
        editable_parts = sentinel_re.split(masked_chunk)

        # Build segments: [(text, is_protected), ...]
        # Use the explicit chunk_entities mapping — no find() needed.
        segments: list[tuple[str, bool]] = []
        for i, editable in enumerate(editable_parts):
            if editable:
                segments.append((editable, False))
            if i < len(chunk_entities):
                segments.append((chunk_entities[i], True))

        if not segments:
            return None

        # ── Expand protected regions for Markdown link syntax ─────────
        # If an editable segment ends with "[label](" and the next segment is
        # protected, the link destination may or may not include the closing
        # ")".  The URL hazard regex (_INLINE_HAZARD_RE) sometimes captures
        # trailing ")" as part of the URL (e.g. Wikipedia links).  Handle
        # both cases:
        #   Case A: entity ends with ")" → merge [label]( + entity as one
        #           protected region (link fully enclosed).
        #   Case B: entity does NOT end with ")" → look for ")" in the next
        #           editable segment and merge [label]( + entity + ")".
        _MD_LINK_OPEN = re.compile(r'\[[^\]]*\]\(\s*$')
        _MD_LINK_CLOSE = re.compile(r'^\s*\)')
        merged: list[tuple[str, bool]] = []
        i = 0
        while i < len(segments):
            text, is_prot = segments[i]
            if (
                not is_prot
                and i + 1 < len(segments)
                and segments[i + 1][1]  # next is protected
                and _MD_LINK_OPEN.search(text)
            ):
                open_match = _MD_LINK_OPEN.search(text)
                pre_link = text[:open_match.start()]
                entity = segments[i + 1][0]

                if entity.endswith(')'):
                    # Case A: entity includes close paren → full link in entity.
                    protected_region = text[open_match.start():] + entity
                    if pre_link:
                        merged.append((pre_link, False))
                    merged.append((protected_region, True))
                    i += 2
                elif (
                    i + 2 < len(segments)
                    and not segments[i + 2][1]
                    and _MD_LINK_CLOSE.search(segments[i + 2][0])
                ):
                    # Case B: close paren in next editable segment.
                    link_suffix = segments[i + 2][0]
                    close_match = _MD_LINK_CLOSE.search(link_suffix)
                    post_link = link_suffix[close_match.end():]
                    protected_region = (
                        text[open_match.start():]
                        + entity
                        + link_suffix[: close_match.end()]
                    )
                    if pre_link:
                        merged.append((pre_link, False))
                    merged.append((protected_region, True))
                    if post_link:
                        merged.append((post_link, False))
                    i += 3
                else:
                    merged.append((text, is_prot))
                    i += 1
            else:
                merged.append((text, is_prot))
                i += 1
        segments = merged

        # Extract only the editable spans for LLM correction.
        editable_spans = [
            (i, text)
            for i, (text, is_prot) in enumerate(segments)
            if not is_prot and text.strip()
        ]
        if not editable_spans:
            return None

        # Send each editable span to the LLM with standard safety guards.
        corrected_segments = [text for text, _ in segments]
        session = session or self._get_session()
        profile = profile or PROFILES.get(strength, PROFILES["full_correction"])
        # Config-driven threshold — same resolution as the streaming path and
        # the main patch path (correct_text_patch): the correction_modes row
        # for this strength is authoritative, profile value is the fallback.
        _modes = self.cfg.get("correction_modes", [])
        _mi = _resolve_mode_index(strength, _modes)
        threshold = (
            _modes[_mi].get("hallucination_threshold")
            if 0 <= _mi < len(_modes) and isinstance(_modes[_mi], dict)
            else None
        )
        if threshold is None:
            threshold = profile.hallucination_threshold

        for seg_idx, span_text in editable_spans:
            if cancel_event is not None and cancel_event.is_set():
                return None

            corrected_span = self._rewrite_sentence_chunk(
                span_text,
                custom_sys,
                unit_idx,
                total,
                strength,
                cancel_event,
                mode_prompt_override,
                session,
                profile,
            )
            if corrected_span is None:
                # Span rewrite failed — keep original for this span.
                continue

            # ── Standard safety guards on the corrected span ──────────
            # The span was sent without sentinels, so no sentinel check needed.
            # But we still guard against hallucination, refusal, and corruption.

            if _is_corrupt_output(corrected_span):
                log(f"[{self.label}] span recovery unit {unit_idx}: corrupt output in span")
                continue

            if _is_fewshot_echo(corrected_span, span_text):
                log(f"[{self.label}] span recovery unit {unit_idx}: few-shot echo in span")
                continue

            if _is_refusal_or_empty(corrected_span, span_text):
                log(f"[{self.label}] span recovery unit {unit_idx}: refusal/empty in span")
                continue

            if _hallucination_ratio(span_text, corrected_span, strength) > threshold:
                log(
                    f"[{self.label}] span recovery unit {unit_idx}: "
                    f"span diverged beyond threshold ({threshold})"
                )
                continue

            if profile.hunk_guard_mode is not None:
                from stet.core.text_utils import apply_hunk_guard
                guarded = apply_hunk_guard(
                    span_text, corrected_span, profile.hunk_guard_mode, threshold=threshold,
                )
                corrected_span = guarded

            if not _post_splice_sanity(
                span_text, corrected_span,
                min_ratio=profile.min_word_ratio, max_ratio=profile.max_word_ratio,
            ):
                log(f"[{self.label}] span recovery unit {unit_idx}: post-splice sanity failed in span")
                continue

            corrected_segments[seg_idx] = corrected_span

        return "".join(corrected_segments)

    def _rewrite_sentence_chunk(
        self,
        chunk_text: str,
        custom_sys: str | None,
        unit_idx: int,
        total: int,
        strength: str,
        cancel_event: threading.Event | None = None,
        mode_prompt_override: str | None = None,
        session: requests.Session | None = None,
        profile: CorrectionProfile | None = None,
    ) -> str | None:
        """Rewrite one sentence unit end-to-end. Returns corrected text or None on failure.

        Uses the same blocking `requests.post` pattern as the old patch path so
        the outer orchestrator can wait on ThreadPoolExecutor futures without
        needing Qt event-loop integration. The server's --parallel 4 slots
        allow up to 4 of these to run concurrently.
        """
        if not chunk_text.strip():
            return chunk_text

        # ── Sentinel alias transformation ─────────────────────────────
        # Replace __STET_PROTECTED_N__ with LLM-friendly [REFN] aliases
        # before the model sees the text.  Short bracket-style references
        # survive BPE tokenisation far better than double-underscore
        # tokens that Gemma (and other small models) intermittently
        # mangle, drop, or "helpfully" unmask.
        _sentinel_indices = [
            m.group(1)
            for m in _INLINE_SENTINEL_CAPTURE_RE.finditer(chunk_text)
        ]
        if _sentinel_indices:
            llm_text = _INLINE_SENTINEL_CAPTURE_RE.sub(
                r"[REF\1]", chunk_text,
            )
        else:
            llm_text = chunk_text

        messages = self._build_correction_messages(
            llm_text,
            custom_sys,
            strength,
            mode_prompt_override,
        )

        # Output budget: input tokens + 96 headroom. Per-slot ctx is
        # (ctx_size / parallel) tokens; sentence units are up to
        # `chunk_words` words (60 correction / 250 rewrite profiles), so the
        # budget leaves plenty of room.
        word_count = len(chunk_text.split())
        est_input_tokens = _estimate_tokens(chunk_text)
        slot_limit = (
            self.actual_ctx_size
            if self.actual_ctx_size is not None
            else self._get_param("context_size", 12800) // self._get_param("parallel", 4)
        )
        max_tokens = min(max(int(est_input_tokens * 3.0) + 128, 512), 2048)
        # Prevent slot overflow by capping max_tokens to the remaining slot budget
        if est_input_tokens + max_tokens > slot_limit:
            max_tokens = max(128, slot_limit - est_input_tokens - 64)

        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            # Correction-specific sampling (deterministic).  _get_param prefixes
            # keys with "chat_" when self.model_path_key == "chat_model_path",
            # but the Chat instance's DEFAULT_CONFIG lacks chat_correction_*,
            # so fallback to the base keys is transparent.  If chat_correction_*
            # keys are ever added to the config, this will pick them up.
            "temperature": self._get_param(
                "correction_temperature",
                self._get_param("temperature", 0.0),
            ),
            "top_k": self._get_param(
                "correction_top_k",
                self._get_param("top_k", 1),
            ),
            "top_p": self._get_param(
                "correction_top_p",
                self._get_param("top_p", 0.95),
            ),
            "min_p": self._get_param(
                "correction_min_p",
                self._get_param("min_p", 0.0),
            ),
            "seed": self._get_param("seed", -1),
            "typical_p": self._get_param("typical_p", 1.0),
            "tfs_z": self._get_param("tfs_z", 1.0),
            "mirostat": self._get_param("mirostat", 0),
            "mirostat_tau": self._get_param("mirostat_tau", 5.0),
            "mirostat_eta": self._get_param("mirostat_eta", 0.1),
            "repeat_penalty": self._get_param("repeat_penalty", 1.0),
            "frequency_penalty": self._get_param("frequency_penalty", 0.0),
            "presence_penalty": self._get_param("presence_penalty", 0.0),
            "stream": False,
            "think": False,
            "reasoning_budget": 0,
            "chat_template_kwargs": {"enable_thinking": False},
            "extra_body": {
                "chat_template_kwargs": {"enable_thinking": False},
                "reasoning_budget": 0,
            },
            "cache_prompt": self._get_param("cache_prompt", True),
            "stop": [],
        }

        if session is None:
            # Fallback caller didn't pass a session — use the persistent one
            # so the per-chunk path also benefits from connection reuse.
            session = self._get_session()

        _RETRYABLE_STATUSES = {429, 502, 503, 504}
        _MAX_RETRIES = 2
        _BACKOFF_BASE = 1.0
        recycled_in_unit = False

        req_timeout = 120 if getattr(self, "actual_backend_type", "cpu") in ("cpu", "unknown") else 60

        try:
            if cancel_event is not None and cancel_event.is_set():
                return None
            log(
                f"[{self.label}] REWRITE unit {unit_idx}/{total} strength={strength} "
                f"words={word_count} max_tokens={max_tokens}"
            )
            for _attempt in range(_MAX_RETRIES + 1):
                if cancel_event is not None and cancel_event.is_set():
                    return None
                try:
                    r = session.post(self._chat_url(), json=payload, timeout=req_timeout)
                except requests.exceptions.ReadTimeout:
                    server_alive = self._is_server_alive(session)
                    if not server_alive and not recycled_in_unit and self._recycle_server():
                        recycled_in_unit = True
                        log(
                            f"[{self.label}] Freeze detected on unit {unit_idx} — "
                            f"recycled engine, retrying request"
                        )
                        session = self._get_session()
                        r = session.post(self._chat_url(), json=payload, timeout=req_timeout)
                    else:
                        raise
                if r.status_code in _RETRYABLE_STATUSES and _attempt < _MAX_RETRIES:
                    log(f"[{self.label}] HTTP {r.status_code} unit {unit_idx} — retrying in {_BACKOFF_BASE * (2 ** _attempt):.1f}s")
                    time.sleep(_BACKOFF_BASE * (2 ** _attempt))
                    continue
                break
            if not r.ok:
                log(f"[{self.label}] HTTP {r.status_code}: {r.text[:200]}")
            r.raise_for_status()
            raw, finish_reason = _extract_content_from_response(r.json())
            log(
                f"[{self.label}] rewrite unit {unit_idx} (finish={finish_reason}): "
                f"{raw[:200]!r}"
            )
        except requests.exceptions.ConnectionError:
            log(f"[{self.label}] chunk {unit_idx} connection closed (likely cancelled)")
            return None
        except requests.exceptions.ReadTimeout as e:
            log(f"[{self.label}] rewrite request timed out unit {unit_idx}: {e}")
            return None
        except Exception as e:
            log(f"[{self.label}] rewrite request failed unit {unit_idx}: {e}")
            return None
        # session is always caller-owned (persistent) — never closed here.
        # Lifecycle is managed by close_session() at shutdown.

        if finish_reason == "length":
            retry_tokens = min(int(max_tokens * 1.5), slot_limit - est_input_tokens - 64)
            if retry_tokens > max_tokens:
                log(f"[{self.label}] rewrite unit {unit_idx} truncated — retrying with max_tokens={retry_tokens}")
                payload["max_tokens"] = retry_tokens
                if cancel_event is not None and cancel_event.is_set():
                    return None
                try:
                    try:
                        r = session.post(self._chat_url(), json=payload, timeout=req_timeout)
                    except requests.exceptions.ReadTimeout:
                        server_alive = self._is_server_alive(session)
                        if not server_alive and not recycled_in_unit and self._recycle_server():
                            recycled_in_unit = True
                            log(
                                f"[{self.label}] Freeze detected on unit {unit_idx} (length retry) — "
                                f"recycled engine, retrying request"
                            )
                            session = self._get_session()
                            r = session.post(self._chat_url(), json=payload, timeout=req_timeout)
                        else:
                            raise
                    if not r.ok:
                        log(f"[{self.label}] HTTP {r.status_code} (retry): {r.text[:200]}")
                    r.raise_for_status()
                    raw, finish_reason = _extract_content_from_response(r.json())
                    log(f"[{self.label}] rewrite unit {unit_idx} retry (finish={finish_reason}): {raw[:200]!r}")
                except requests.exceptions.ConnectionError:
                    return None
                except requests.exceptions.ReadTimeout as e:
                    log(f"[{self.label}] retry timed out unit {unit_idx}: {e}")
                    return None
                except Exception as e:
                    log(f"[{self.label}] retry failed unit {unit_idx}: {e}")
                    return None
            if finish_reason == "length":
                log(f"[{self.label}] rewrite unit {unit_idx} still truncated after retry")
                return None

        if _is_corrupt_output(raw):
            log(f"[{self.label}] corrupt rewrite output unit {unit_idx}: {raw[:80]!r}")
            return None
        if _is_fewshot_echo(raw, chunk_text):
            log(
                f"[{self.label}] few-shot echo in rewrite unit {unit_idx}: {raw[:80]!r}"
            )
            return None

        corrected = _extract_rewritten_sentence(raw, original_text=chunk_text)
        if corrected is None:
            log(f"[{self.label}] no marker pair in rewrite unit {unit_idx}")
            return None

        # ── Restore sentinel aliases ──────────────────────────────────
        # Map [REFN] back to __STET_PROTECTED_N__ so the outer sentinel
        # validation check sees canonical tokens.
        if _sentinel_indices:
            sorted_indices = sorted(_sentinel_indices, key=int, reverse=True)
            for _sidx in sorted_indices:
                corrected = corrected.replace(
                    f"[REF{_sidx}]", f"__STET_PROTECTED_{_sidx}__",
                )
            # Fuzzy recovery for mangled aliases (spaces, case, brackets)
            for _sidx in sorted_indices:
                _sentinel = f"__STET_PROTECTED_{_sidx}__"
                if _sentinel in corrected:
                    continue
                for _var in (
                    f"[REF {_sidx}]",
                    f"[ref{_sidx}]",
                    f"[Ref{_sidx}]",
                    f"[REF{_sidx}",
                    f"REF{_sidx}]",
                    f"[ REF{_sidx} ]",
                    f"[REF {_sidx} ]",
                ):
                    if _var in corrected:
                        corrected = corrected.replace(_var, _sentinel, 1)
                        log(
                            f"[{self.label}] Recovered mangled alias "
                            f"{_var!r} -> {_sentinel}"
                        )
                        break
                if _sentinel not in corrected:
                    pat = re.compile(rf"(?<![A-Za-z0-9])REF{_sidx}(?!\d)", re.IGNORECASE)
                    if pat.search(corrected):
                        corrected = pat.sub(_sentinel, corrected, count=1)
                        log(
                            f"[{self.label}] Recovered mangled alias "
                            f"REF{_sidx} -> {_sentinel}"
                        )

        # Guard against LLM-introduced extra newlines (common with small models
        # that insert blank lines between lines that were single-spaced).
        # Template/transform profiles need to keep generated newlines so that
        # Notes Assistant bullets, headers, etc. survive post-processing.
        allow_nl = profile.allow_new_newlines if profile else (mode_prompt_override is not None)
        corrected = _normalize_chunk_newlines(
            chunk_text, corrected, allow_newlines=allow_nl,
        )

        # ── Per-chunk terminal-punctuation guard ──────────────────────
        # Only for single-chunk inputs: if the LLM drops trailing .!?,
        # restore it.  For multi-chunk inputs the per-chunk guard can
        # introduce spurious punctuation at interior chunk boundaries,
        # so we skip it and rely on _apply_post_fixes to guard the
        # final character of the reassembled text.
        if total == 1:
            orig_stripped = chunk_text.rstrip()
            corr_stripped = corrected.rstrip()
            if (
                orig_stripped
                and orig_stripped[-1] in ".!?"
                and corr_stripped
                and corr_stripped[-1] not in ".!?"
            ):
                # Preserve any trailing whitespace the corrected text may have
                trailing = corrected[len(corr_stripped):]
                corrected = corr_stripped + orig_stripped[-1] + trailing

        return corrected

    # ── streaming chat ─────────────────────────────────────────────────────
    def make_stream_worker(
        self, messages: list, max_tokens: int = 1024, grammar: str | None = None, json_schema: dict | None = None,
        **overrides,
    ) -> StreamWorker:
        think_enabled = overrides.pop("think", bool(self.cfg.get("chat_thinking_enabled", False)))
        reasoning_budget = overrides.pop("reasoning_budget", -1 if think_enabled else 0)
        chat_tmpl_kwargs = overrides.pop("chat_template_kwargs", {"enable_thinking": think_enabled})
        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": self._get_param("temperature", 0.3),
            "top_k": self._get_param("top_k", 40),
            "top_p": self._get_param("top_p", 0.95),
            "min_p": self._get_param("min_p", 0.05),
            "seed": self._get_param("seed", -1),
            "typical_p": self._get_param("typical_p", 1.0),
            "tfs_z": self._get_param("tfs_z", 1.0),
            "mirostat": self._get_param("mirostat", 0),
            "mirostat_tau": self._get_param("mirostat_tau", 5.0),
            "mirostat_eta": self._get_param("mirostat_eta", 0.1),
            "repeat_penalty": self._get_param("repeat_penalty", 1.0),
            "frequency_penalty": self._get_param("frequency_penalty", 0.0),
            "presence_penalty": self._get_param("presence_penalty", 0.0),
            "think": think_enabled,
            "reasoning_budget": reasoning_budget,
            "chat_template_kwargs": chat_tmpl_kwargs,
            "extra_body": {
                "chat_template_kwargs": chat_tmpl_kwargs,
                "reasoning_budget": reasoning_budget,
            },
            "cache_prompt": self._get_param("cache_prompt", True),
        }
        payload.update(overrides)
        if grammar:
            payload["grammar"] = grammar
        if json_schema:
            payload["json_schema"] = json_schema
        return StreamWorker(self._chat_url(), payload)

    # ── idle check ─────────────────────────────────────────────────────────
    def check_idle(self):
        if self.cfg.get(self.keep_loaded_key, True):
            log(f"[{self.label}] keep_model_loaded=True — skipping idle check")
            return
        if not self.is_loaded() or not self.last_used:
            return
        idle = (datetime.now() - self.last_used).total_seconds()
        timeout = max(60, self.cfg.get(self.idle_timeout_key, 300))
        if idle >= timeout:
            log(f"[{self.label}] Idle {idle:.0f}s — unloading")
            self.unload_model()
