import concurrent.futures
import ctypes
import ctypes.wintypes
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
from stet.llm.utils import (
    _MIN_RELIABLE_MODEL_B,
    _find_shipped_llama_server,
    _model_size_billions,
    has_nvidia,
)
from stet.llm.backend_manager import (
    BackendManager,
)
from stet.llm.worker import StreamWorker

_STRENGTH_TO_MODE_INDEX = {
    "spelling_only": 0,
    "full_correction": 1,
    "rewrite_polish": 2,
}

_INLINE_SENTINEL_RE = re.compile(r"__STET_PROTECTED_\d+__")


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




def _resolve_mode_index(strength: str, modes: list) -> int:
    """Map a strength string to a correction_modes list index.

    Built-in strengths resolve via the static map. Custom mode names are
    matched by scanning modes[3:] by name. Falls back to 1 (full_correction).
    """
    builtin = _STRENGTH_TO_MODE_INDEX.get(strength)
    if builtin is not None:
        return builtin
    for i, m in enumerate(modes[3:], start=3):
        if m.get("name") == strength:
            return i
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
        # Set to True when load_model() fails because the file path is
        # configured but the file doesn't exist (e.g. drive not mounted yet).
        # Reset to False at the start of each load_model() call and on success.
        # Checked by StetApp to schedule a deferred retry.
        self._last_load_failed_not_found: bool = False
        self.last_load_error: str = ""
        self.last_patch_error: str | None = None
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
        model_name = Path(self.cfg.get(self.model_path_key, "")).name.lower()
        if "gemma" in model_name:
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

        Sends ``parallel`` concurrent /v1/chat/completions requests using the
        *actual* default correction system prompt (smart_fix / mode index 1)
        with cache_prompt=True.  llama-server's prompt cache is prefix-based
        and per-slot, so we must warm every slot for the first real correction
        to hit the cache regardless of which slot handles it.

        Best-effort: failures are logged but never raised, so a broken warmup
        can't prevent model load from completing.
        """
        try:
            parallel_slots = self._get_param("parallel", 4)
            strength = self.cfg.get("streaming_strength", "full_correction")
            for hotkey in self.cfg.get("hotkeys", []):
                if isinstance(hotkey, dict) and hotkey.get("strength"):
                    strength = hotkey["strength"]
                    break
            log(
                f"[{self.label}] KV cache warmup — pre-filling {parallel_slots} "
                f"slot(s) with real system prompt"
            )
            payload = {
                "messages": self._build_correction_messages(
                    "warmup",
                    None,
                    strength,
                ),
                "max_tokens": 1,
                "cache_prompt": True,
            }
            url = self._chat_url()

            session = self._get_session()
            def _warm_one(_slot_idx: int) -> None:
                session.post(url, json=payload, timeout=10)

            with concurrent.futures.ThreadPoolExecutor(
                max_workers=parallel_slots
            ) as ex:
                list(ex.map(_warm_one, range(parallel_slots)))
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
        self, force_cpu: bool = False, retry_missing_path: bool = False
    ) -> bool:
        # Reset the not-found flag at the start of each attempt
        self._last_load_failed_not_found = False

        with self._lock:
            if self.loading:
                return False
            self.loading = True

        # If already loaded, nothing to do
        if self.is_loaded():
            with self._lock:
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
                self.loading = False
            self.status_changed.emit("No model file configured")
            return False

        if not Path(model_path).exists():
            with self._lock:
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
        gpu_layers = 0 if force_cpu else self._get_param("gpu_layers", 99)
        if force_cpu:
            log(f"[{self.label}] force_cpu=True — overriding gpu_layers to 0")
        elif not gpu_detected and gpu_layers > 0:
            log(
                f"[{self.label}] nvidia-smi not found but gpu_layers={gpu_layers} from config — attempting GPU (error recovery will retry CPU on failure)"
            )
        log(f"[{self.label}] Using gpu_layers={gpu_layers}")
        ctx = self._get_param("context_size", 12800)
        host = self.cfg.get("server_host", "127.0.0.1")
        port = self.cfg.get("server_port", 8080) + self.port_offset
        threads = self._get_param("threads", -1)
        batch_size = self._get_param("batch_size", 2048)
        ubatch_size = self._get_param("ubatch_size", 512)
        flash_attn = self._get_param("flash_attn", False)
        mtp_enabled = self._get_param("mtp_enabled", False)
        mtp_max_draft = self._get_param("mtp_max_draft", 2)
        mtp_min_draft = self._get_param("mtp_min_draft", 0)

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
            # Some thinking templates (e.g. Liquid LFM 2.5) hard-prime <think>
            # in the generation prompt and ignore --reasoning off / payload
            # "think" flags, so every request burns all max_tokens on reasoning
            # with empty content. --reasoning-budget arms the budget sampler:
            # once N reasoning tokens are spent it forces the </think> end tag,
            # guaranteeing real content follows. No-op for models without
            # <think> tags, so it is safe to pass unconditionally. Budget is
            # deliberately small (50): with a 4k GGUF at parallel=4 the per-slot
            # budget is ~1024 tokens, and a 256-token reasoning budget left only
            # ~150 tokens of answer room — enough to starve a rewrite and hit
            # finish=length. 50 tokens is ample for the forced </think> close.
            "--reasoning-budget",
            "50",
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

        # Liquid LFM 2.5 is a pure reasoning model: its chat template ALWAYS
        # primes <think> at the generation prompt and ignores --reasoning off
        # and payload think flags, so reasoning spills into content. Point
        # llama-server at a byte-exact copy of the official template with the
        # <think> priming removed (stet/llm/chat_templates/lfm25_no_think.jinja).
        # The model then answers directly and the --reasoning-budget sampler
        # above disarms automatically (no think tags to budget). The path
        # resolves relative to this module so it works in both source runs and
        # PyInstaller bundles (build.py ships the template beside the module,
        # mirroring how stet.qss is bundled).
        if "lfm-2.5" in model_path.lower() or "lfm2.5" in model_path.lower():
            template_path = (
                Path(__file__).resolve().parent / "chat_templates" / "lfm25_no_think.jinja"
            )
            if template_path.exists():
                cmd.extend(["--chat-template", str(template_path)])
                log(
                    f"[{self.label}] LFM 2.5 detected — using no-think chat template: "
                    f"{template_path}"
                )
            else:
                log(
                    f"[{self.label}] LFM 2.5 detected but no-think chat template "
                    f"missing: {template_path}"
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
            cmd.extend([
                "--spec-type", "draft-mtp",
                "--spec-draft-n-max", str(mtp_max_draft),
                "--spec-draft-n-min", str(mtp_min_draft),
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
                _proc = self.server_process
                if _proc is not None and _proc.poll() is not None:
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
                    self.status_changed.emit(f"Loading… ({elapsed}s)")
                    next_status_at += 15.0
                time.sleep(0.15)
            else:
                raise RuntimeError("Server did not start within 180 s")

            name = friendly_name(model_path)
            self._warmup_prompt_cache()
            self._server_ready = True
            self.mark_used()
            with self._lock:
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

                    if (
                        self.actual_backend_type == "cpu"
                        and ("loaded CPU backend" in log_content or "CPU :" in log_content)
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
                        cuda_log_tail = (
                            "\n".join(cuda_lines[-10:])
                            if cuda_lines
                            else "(no GPU-related lines found in server log)"
                        )
                        warn_msg = (
                            f"GPU offloading requested (gpu_layers={gpu_layers}) but llama-server "
                            f"loaded CPU backend. Check your CUDA installation.\n"
                            f"Server log GPU lines:\n{cuda_log_tail}"
                        )
                        log(f"[{self.label}] WARNING: {warn_msg}")
                        self.model_warning.emit(
                            "GPU requested but CPU loaded. Check llama-server binary has CUDA support. "
                            "See server_log.txt for details."
                        )
                    elif (
                        self.actual_backend_type == "cpu"
                        and gpu_layers > 0
                        and log_content.strip()
                    ):
                        warn_msg = (
                            f"GPU offloading requested (gpu_layers={gpu_layers}) but no GPU backend "
                            f"was detected in server log. The llama-server binary may not have GPU support."
                        )
                        log(f"[{self.label}] WARNING: {warn_msg}")
                        self.model_warning.emit(
                            "GPU requested but no GPU backend found. Your llama-server binary may lack GPU support."
                        )
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
            except Exception as e:
                log(f"[{self.label}] /props fetch failed (non-fatal): {e}")

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
            import traceback

            tb = traceback.format_exc()
            log(f"[{self.label}] load_model failed: {e}\n{tb}")
            with self._lock:
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
            self.status_changed.emit(f"Load error: {str(e)[:70]}")
            return False

    def unload_model(self):
        with self._lock:
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
            self.actual_ctx_size or self._get_param("context_size", 12800)
        ) // self._get_param("parallel", 4)
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
                    any_preserved = True
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
                                    for m in re.finditer(r"__STET_PROTECTED_(\d+)__", chunk_text)
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
            if any_preserved or masked_entities:
                _reason = self.last_patch_error or "Protected/non-prose unit(s) preserved original"
                log(
                    f"[{self.label}] Patch: protected/non-prose unit(s) "
                    f"preserved original — outcome=UNCHANGED_PROTECTED "
                    f"atoms={len(masked_entities)} elapsed={_elapsed:.2f}s"
                )
                return CorrectionResult(
                    text=text, outcome=CorrectionOutcome.UNCHANGED_PROTECTED,
                    units_processed=len(chunks), units_corrected=0,
                    protected_atom_count=len(masked_entities),
                    reason=_reason, elapsed_s=_elapsed,
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
        if not changed and self.last_patch_error:
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
            for m in re.finditer(r"__STET_PROTECTED_(\d+)__", chunk_text)
        ]
        if _sentinel_indices:
            llm_text = re.sub(
                r"__STET_PROTECTED_(\d+)__", r"[REF\1]", chunk_text,
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
        # ~3200 tokens (ctx_size / parallel); paragraph units are ~200 words
        # (~260 tokens) in, so the budget leaves plenty of room.
        word_count = len(chunk_text.split())
        est_input_tokens = _estimate_tokens(chunk_text)
        ctx = self._get_param("context_size", 12800)
        slot_limit = (self.actual_ctx_size or ctx) // self._get_param("parallel", 4)
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
                r = session.post(self._chat_url(), json=payload, timeout=60)
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
                    r = session.post(self._chat_url(), json=payload, timeout=60)
                    if not r.ok:
                        log(f"[{self.label}] HTTP {r.status_code} (retry): {r.text[:200]}")
                    r.raise_for_status()
                    raw, finish_reason = _extract_content_from_response(r.json())
                    log(f"[{self.label}] rewrite unit {unit_idx} retry (finish={finish_reason}): {raw[:200]!r}")
                except requests.exceptions.ConnectionError:
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
            for _sidx in _sentinel_indices:
                corrected = corrected.replace(
                    f"[REF{_sidx}]", f"__STET_PROTECTED_{_sidx}__",
                )
            # Fuzzy recovery for mangled aliases (spaces, case, brackets)
            for _sidx in _sentinel_indices:
                _sentinel = f"__STET_PROTECTED_{_sidx}__"
                if _sentinel in corrected:
                    continue
                for _var in (
                    f"[REF {_sidx}]",
                    f"[ref{_sidx}]",
                    f"[Ref{_sidx}]",
                    f"REF{_sidx}",
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
            "think": self.cfg.get("chat_thinking_enabled", False),
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
