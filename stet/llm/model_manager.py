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

import requests
from PyQt6.QtCore import QObject, pyqtSignal

from stet.constants import DEFAULT_CONFIG, LOG_FILE, WINDOWS
from stet.core.config import ConfigManager
from stet.core.text_utils import (
    _HALLUCINATION_THRESHOLD_AGGRESSIVE,
    _HALLUCINATION_THRESHOLD_CONSERVATIVE,
    _HALLUCINATION_THRESHOLD_SMARTFIX,
    _apply_post_fixes,
    _chunk_text_by_sentences,
    _extract_content_from_response,
    _extract_rewritten_sentence,
    _hallucination_ratio,
    _is_corrupt_output,
    _is_fewshot_echo,
    _is_refusal_or_empty,
    _loses_meaningful_repetition,
    _normalize_chunk_newlines,
    _post_splice_sanity,
    _wrap_correction_prompt,
)
from stet.core.utils import friendly_name, log
from stet.llm.utils import (
    _MIN_RELIABLE_MODEL_B,
    _find_shipped_llama_server,
    _model_size_billions,
    has_nvidia,
)
from stet.llm.worker import StreamWorker

_HALLUCINATION_THRESHOLDS_BY_STRENGTH = {
    "conservative": _HALLUCINATION_THRESHOLD_CONSERVATIVE,
    "spelling_only": _HALLUCINATION_THRESHOLD_CONSERVATIVE,
    "smart_fix": _HALLUCINATION_THRESHOLD_SMARTFIX,
    "full_correction": 0.6,
    "aggressive": _HALLUCINATION_THRESHOLD_AGGRESSIVE,
    "rewrite_polish": 0.99,
}

_STRENGTH_TO_MODE_INDEX = {
    "conservative": 0,
    "spelling_only": 0,
    "smart_fix": 1,
    "full_correction": 1,
    "aggressive": 2,
    "rewrite_polish": 2,
}

_INLINE_HAZARD_RE = re.compile(
    r'\b(?:https?://|www\.)\S+\b'  # URLs
    r'|\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b'  # Emails
    r'|\b[a-zA-Z]:\\[\w.-]+(?:\\[\w.-]+)*(?:\.\w+)?\b'  # Windows absolute backslash paths
    r'|\b[a-zA-Z]:/[\w.-]+(?:/[\w.-]+)*(?:\.\w+)?\b'  # Windows absolute slash paths
    r'|(?<=[\s"\'(])/[/\w.-]+/[/\w.-]+\b'  # Unix absolute paths
    r'|(?<=[\s"\'(])\.\.?/[/\w.-]+/[/\w.-]+\b'  # Unix relative paths
    r'|(?<=[\s"\'(])\.\.?\\[\\\w.-]+\\[\\\w.-]+\b'  # Windows relative backslash paths
)
_INLINE_SENTINEL_RE = re.compile(r"⟦U\d+⟧")


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
    ):
        super().__init__()
        self.cfg = cfg
        self.model_path_key = model_path_key
        self.label = label
        self.keep_loaded_key = keep_loaded_key
        self.idle_timeout_key = idle_timeout_key
        self.server_process = None
        self.log_file = None
        self.last_used = None
        self.loading = False
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

    # ── internal helpers ──────────────────────────────────────────────────
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
            parallel_slots = self.cfg.get("parallel", 4)
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
            system = _wrap_correction_prompt(mode_prompt_override, mode_index)
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
        user-folded system prompt and assistant prefill.
        """
        system = self._correction_system_prompt(
            custom_sys,
            strength,
            mode_prompt_override,
        )
        wrapped = f"<<<START>>>\n{chunk_text}\n<<<END>>>"
        model_name = Path(self.cfg.get(self.model_path_key, "")).name.lower()
        if "gemma" in model_name:
            return [
                {"role": "user", "content": f"{system}\n\n{wrapped}"},
                {"role": "assistant", "content": "<<<START>>>\n"},
            ]
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": wrapped},
            {"role": "assistant", "content": "<<<START>>>\n"},
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
            parallel_slots = self.cfg.get("parallel", 4)
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
        gpu_layers = 0 if force_cpu else self.cfg.get("gpu_layers", 99)
        if force_cpu:
            log(f"[{self.label}] force_cpu=True — overriding gpu_layers to 0")
        elif not gpu_detected and gpu_layers > 0:
            log(
                f"[{self.label}] nvidia-smi not found but gpu_layers={gpu_layers} from config — attempting GPU (error recovery will retry CPU on failure)"
            )
        log(f"[{self.label}] Using gpu_layers={gpu_layers}")
        ctx = self.cfg.get("context_size", 12800)
        host = self.cfg.get("server_host", "127.0.0.1")
        port = self.cfg.get("server_port", 8080) + self.port_offset
        threads = self.cfg.get("threads", -1)
        batch_size = self.cfg.get("batch_size", 2048)
        ubatch_size = self.cfg.get("ubatch_size", 512)
        flash_attn = self.cfg.get("flash_attn", False)
        mtp_enabled = self.cfg.get("mtp_enabled", False)
        mtp_max_draft = self.cfg.get("mtp_max_draft", 2)
        mtp_min_draft = self.cfg.get("mtp_min_draft", 0)

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
            str(self.cfg.get("threads_batch", -1)),
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
            str(self.cfg.get("parallel", 4)),
            "--reasoning",
            "off",
            "--no-warmup",
            "--cache-reuse",
            "64",
            "--temp",
            str(self.cfg.get("temperature", 0.1)),
            "--top-k",
            str(self.cfg.get("top_k", 40)),
            "--top-p",
            str(self.cfg.get("top_p", 0.95)),
            "--min-p",
            str(self.cfg.get("min_p", 0.05)),
            "--repeat-penalty",
            str(self.cfg.get("repeat_penalty", 1.0)),
        ]

        if self.cfg.get("seed", -1) != -1:
            cmd.extend(["--seed", str(self.cfg.get("seed", -1))])
        
        rope_base = self.cfg.get("rope_freq_base", 0.0)
        if rope_base > 0.0:
            cmd.extend(["--rope-freq-base", str(rope_base)])
            
        rope_scale = self.cfg.get("rope_freq_scale", 0.0)
        if rope_scale > 0.0:
            cmd.extend(["--rope-freq-scale", str(rope_scale)])

        cache_k = self.cfg.get("kv_cache_type_k", "")
        if cache_k:
            cmd.extend(["--cache-type-k", cache_k])
            
        cache_v = self.cfg.get("kv_cache_type_v", "")
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

            self.log_file = open(LOG_FILE, "w", encoding="utf-8")
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
                if self.server_process.poll() is not None:
                    # Dump server log into app_debug.log for easier diagnosis
                    try:
                        tail = LOG_FILE.read_text(encoding="utf-8", errors="replace")[
                            -2000:
                        ]
                        log(f"[{self.label}] server_log.txt tail:\n{tail}")
                    except Exception:
                        pass
                    raise RuntimeError("Server exited immediately — see server_log.txt")
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

            self.mark_used()
            with self._lock:
                self.loading = False
            name = friendly_name(model_path)
            self.status_changed.emit(f"Ready — {name}")
            self._warmup_prompt_cache()
            self.model_loaded.emit()
            log(f"[{self.label}] Model ready: {name}")

            # Determine actual loaded backend type and offloading status
            self.actual_backend_type = "cpu"
            if gpu_layers > 0:
                try:
                    log_content = LOG_FILE.read_text(encoding="utf-8", errors="replace")
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
        strength: str = "smart_fix",
        cancel_event: threading.Event | None = None,
        mode_prompt_override: str | None = None,
    ) -> tuple[str | None, int]:
        """Three-phase correction: dict pre-pass, parallel sentence rewrite, hallucination guard.

        Returns (corrected_text_or_None, units_processed).
        - Returns (None, 0) on total failure -> caller falls back to streaming.
        - Returns (text, 0) when text is empty.
        - Returns (text, 0) when dict pre-pass is sufficient (fast path, no LLM call).
        - Returns (final, N) where N = sentence-units sent to the LLM.

        The return-tuple shape is preserved so existing call sites in _do_correction
        don't need to change. The second element was "passes_run" and is now
        "units_processed" — semantically different but used only for the method
        badge ("Patch (Smart Fix, 3x)" reads fine either way).
        """
        if not self.is_loaded():
            if not self.load_model():
                return None, 0
        self.mark_used()
        self.status_changed.emit("Correcting…")
        if not text.strip():
            return text, 0

        if cancel_event is not None and cancel_event.is_set():
            return None, 0

        has_windows_newlines = "\r\n" in text
        text = _normalize_newlines(text, use_windows_newlines=False)

        # ── Phase 0: deterministic dict pre-pass (disabled) ────────────────
        # Disabled: the LLM already corrects all typos in a single pass, making
        # the ~4300-entry static typo dictionary redundant. The pre-pass also
        # lacked a reliable [OK] confirmation mechanism, so it could never
        # short-circuit the LLM call — every correction went to the model
        # regardless. Keeping the code available but inactive for reference.
        # pre_corrected, dict_fixes = _dict_prepass(text)
        pre_corrected, dict_fixes = text, 0
        total_words = len(pre_corrected.split())



        # ── Phase 1: split into sentence units and rewrite in parallel ────
        # 200-word cap produces paragraph-scale units. With --parallel 4 slots,
        # up to 4 units run concurrently. Larger chunks reduce the number of
        # redundant system-prompt evaluations (each chunk re-sends the full
        # ~400-token prompt). Separator preserves inter-unit whitespace/newlines
        # so reassembly is lossless.
        
        # Inline masking for URLs, emails, and file paths.
        masked_entities = []
        def mask_repl(match):
            idx = len(masked_entities) + 1
            masked_entities.append(match.group(0))
            return f"⟦U{idx}⟧"

        pre_corrected = _INLINE_HAZARD_RE.sub(mask_repl, pre_corrected)
        
        is_rewrite_polish = (strength in {"rewrite_polish", "aggressive"})
        if is_rewrite_polish:
            chunks = []
            parts = re.split(r"(\r?\n)", pre_corrected)
            for i in range(0, len(parts), 2):
                chunk = parts[i]
                sep = parts[i+1] if i+1 < len(parts) else ""
                if len(chunk.split()) > 150:
                    sub_chunks = _chunk_text_by_sentences(chunk, 150)
                    if sub_chunks:
                        last_c, _ = sub_chunks[-1]
                        sub_chunks[-1] = (last_c, sep)
                        chunks.extend(sub_chunks)
                else:
                    chunks.append((chunk, sep))
        else:
            chunk_size = self.cfg.get("patch_chunk_size", 60)
            chunks = _chunk_text_by_sentences(pre_corrected, chunk_size)
        # if dict_fixes > 0:
        #     log(f"[{self.label}] Dict prepass applied {dict_fixes} fixes before LLM")
        if len(chunks) > 1:
            log(
                f"[{self.label}] Patch: {len(chunks)} sentence units "
                f"({total_words} words)"
            )

        corrected_parts: list[tuple[str, str]] = [("", "")] * len(chunks)
        any_success = False
        any_preserved = False

        from stet.core.text_utils import looks_like_prose
        
        modes = self.cfg.get("correction_modes", [])
        mode_index = _resolve_mode_index(strength, modes)

        # Config-driven threshold (single source of truth) for the
        # rewrite-mode guards (raw + hunk). Falls back to the legacy dict so
        # older configs still work.
        threshold = (
            modes[mode_index].get("hallucination_threshold")
            if mode_index < len(modes) and isinstance(modes[mode_index], dict)
            else None
        )
        if threshold is None:
            threshold = _HALLUCINATION_THRESHOLDS_BY_STRENGTH.get(strength, 1.0)

        max_workers = min(len(chunks), self.cfg.get("parallel", 4)) if chunks else 1

        shared_session = self._get_session()
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
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
                        continue

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
                    )] = (idx, chunk_text, sep)

                remaining = list(futures.keys())
                while remaining:
                    if cancel_event is not None and cancel_event.is_set():
                        log(f"[{self.label}] Patch: cancelled mid-correction")
                        return None, 0

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
                            log(f"[{self.label}] Patch: unit {idx + 1} exception: {e}")
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

                        # Sentinel survival: every ⟦Ui⟧ in the original chunk
                        # must survive verbatim in the corrected output. If a
                        # small model drops or mangles a sentinel the chunk is
                        # unusable because hazard unmasking will fail.
                        _chunk_sentinels = _INLINE_SENTINEL_RE.findall(chunk_text)
                        if _chunk_sentinels and not all(
                            s in corrected for s in _chunk_sentinels
                        ):
                            log(f"[{self.label}] Patch unit {idx + 1} rejected: sentinel(s) lost in correction")
                            corrected_parts[idx] = (chunk_text, sep)
                            any_preserved = True
                            continue

                        # Reject if raw output exceeds the (config-driven) hallucination threshold
                        if _hallucination_ratio(chunk_text, corrected, strength) > threshold:
                            log(f"[{self.label}] Patch unit {idx + 1} rejected: raw hallucination ratio exceeds threshold")
                            corrected = None
                            corrected_parts[idx] = (chunk_text, sep)
                            continue

                        # Reject if the model refused or returned an empty edit
                        # (marker-wrapped "Please provide text" type output). The
                        # divergence guard cannot separate refusals from legit
                        # rewrites — distributions overlap (refusal 0.58–0.87 vs
                        # legit max ~0.687) — so use a dedicated detector.
                        if _is_refusal_or_empty(corrected, chunk_text):
                            log(f"[{self.label}] Patch unit {idx + 1} rejected: refusal/empty output from model")
                            corrected_parts[idx] = (chunk_text, sep)
                            continue

                        # Phase 2: hunk-level hallucination guard
                        from stet.core.text_utils import apply_hunk_guard

                        guarded_corrected = apply_hunk_guard(chunk_text, corrected, mode_index, threshold=threshold)
                        if guarded_corrected != corrected:
                            log(
                                f"[{self.label}] Patch unit {idx + 1}: hunk-level "
                                "hallucination guard reverted parts of the correction"
                            )
                        corrected = guarded_corrected

                        if not _post_splice_sanity(chunk_text, corrected):
                            log(
                                f"[{self.label}] Patch unit {idx + 1} rejected: "
                                "post-splice sanity check failed"
                            )
                            corrected_parts[idx] = (chunk_text, sep)
                            continue

                        if strength in {
                            "rewrite_polish",
                            "aggressive",
                        } and _loses_meaningful_repetition(
                            chunk_text,
                            corrected,
                        ):
                            log(
                                f"[{self.label}] Patch unit {idx + 1}: repetition-loss "
                                "in aggressive mode — log only, accepting rewrite"
                            )

                        corrected_parts[idx] = (corrected, sep)
                        any_success = True

        # NOTE: shared_session is the persistent session — intentionally not
        # closed here. close_session() handles lifecycle on shutdown.

        reassembled = "".join(part + sep for part, sep in corrected_parts)
        
        for i, entity in enumerate(masked_entities):
            reassembled = reassembled.replace(f"⟦U{i+1}⟧", entity)

        # If dict pre-pass changed nothing AND no unit ever succeeded, report
        # total failure so the caller falls back to streaming. Otherwise we
        # accept partial success (kept-original units are not a failure).
        if not any_success and dict_fixes == 0 and reassembled == text:
            if any_preserved or masked_entities:
                log(
                    f"[{self.label}] Patch: protected/non-prose unit(s) "
                    "preserved original — skipping streaming fallback"
                )
            else:
                log(f"[{self.label}] Patch: no unit succeeded — streaming fallback")
                return None, len(chunks)

        final = reassembled
        if final != text:
            final = _apply_post_fixes(final, original=text, strength=strength)

        # Restore Windows/original newlines
        final = _normalize_newlines(final, has_windows_newlines)

        self.mark_used()
        self.status_changed.emit("Ready")
        return final, len(chunks)

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
    ) -> str | None:
        """Rewrite one sentence unit end-to-end. Returns corrected text or None on failure.

        Uses the same blocking `requests.post` pattern as the old patch path so
        the outer orchestrator can wait on ThreadPoolExecutor futures without
        needing Qt event-loop integration. The server's --parallel 4 slots
        allow up to 4 of these to run concurrently.
        """
        if not chunk_text.strip():
            return chunk_text

        messages = self._build_correction_messages(
            chunk_text,
            custom_sys,
            strength,
            mode_prompt_override,
        )

        # Output budget: input tokens + 96 headroom. Per-slot ctx is
        # ~3200 tokens (ctx_size / parallel); paragraph units are ~200 words
        # (~260 tokens) in, so the budget leaves plenty of room.
        word_count = len(chunk_text.split())
        est_input_tokens = _estimate_tokens(chunk_text)
        ctx = self.cfg.get("context_size", 12800)
        slot_limit = (self.actual_ctx_size or ctx) // self.cfg.get("parallel", 4)
        max_tokens = min(int(est_input_tokens * 1.4) + 16, 2048)
        # Prevent slot overflow by capping max_tokens to the remaining slot budget
        if est_input_tokens + max_tokens > slot_limit:
            max_tokens = max(128, slot_limit - est_input_tokens - 64)

        payload = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "top_k": 1,
            "top_p": 0.95,
            "min_p": 0.05,
            "seed": self.cfg.get("seed", -1),
            "typical_p": self.cfg.get("typical_p", 1.0),
            "tfs_z": self.cfg.get("tfs_z", 1.0),
            "mirostat": self.cfg.get("mirostat", 0),
            "mirostat_tau": self.cfg.get("mirostat_tau", 5.0),
            "mirostat_eta": self.cfg.get("mirostat_eta", 0.1),
            "repeat_penalty": 1.0,
            "frequency_penalty": 0.0,
            "presence_penalty": 0.0,
            "stream": False,
            "think": False,
            "cache_prompt": self.cfg.get("cache_prompt", True),
            "stop": ["<<<END>>>"],
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

        if finish_reason == "length" and "<<<END>>>" not in raw:
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
            if finish_reason == "length" and "<<<END>>>" not in raw:
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

        corrected = _extract_rewritten_sentence(raw)
        if corrected is None:
            log(f"[{self.label}] no marker pair in rewrite unit {unit_idx}")
            return None

        # Guard against LLM-introduced extra newlines (common with small models
        # that insert blank lines between lines that were single-spaced).
        corrected = _normalize_chunk_newlines(chunk_text, corrected)

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
            "temperature": self.cfg.get("temperature", 0.3),
            "top_k": self.cfg.get("top_k", 40),
            "top_p": self.cfg.get("top_p", 0.95),
            "min_p": self.cfg.get("min_p", 0.05),
            "seed": self.cfg.get("seed", -1),
            "typical_p": self.cfg.get("typical_p", 1.0),
            "tfs_z": self.cfg.get("tfs_z", 1.0),
            "mirostat": self.cfg.get("mirostat", 0),
            "mirostat_tau": self.cfg.get("mirostat_tau", 5.0),
            "mirostat_eta": self.cfg.get("mirostat_eta", 0.1),
            "repeat_penalty": self.cfg.get("repeat_penalty", 1.0),
            "frequency_penalty": self.cfg.get("frequency_penalty", 0.0),
            "presence_penalty": self.cfg.get("presence_penalty", 0.0),
            "think": False,
            "cache_prompt": self.cfg.get("cache_prompt", True),
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
