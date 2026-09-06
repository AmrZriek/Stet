# Stet Pipeline Architecture & Engineering Reference

> Canonical Reference: `docs/PROJECT_PIPELINE.md`  
> Version: `v1.4.0` | Last Updated: 2026-09-03

---

## 1. End-to-End Processing Flow

```
[1. User Action]
     │ Global Hotkey Trigger (F9 / Shift+F9) or Tray Menu Action
     ▼
[2. Target & Selection Capture & Instant Shell]
     │ Window Handle, Thread/Process ID & Selection Capture
     │ (Direct UIA / Win32 SendInput / macOS AXUIElement / Native Core IPC)
     │ Foreground handshake: AllowSetForegroundWindow -> target capture -> _activate_window_to_foreground
     ▼
[3. Atom Masking (DocumentProtector)]
     │ Protects sensitive tokens:
     │ - Multi-line code blocks (```...```) & Inline code (`...`)
     │ - URLs, File Paths, Emails, User Protected Terms
     │ -> Replaced with [REFn] placeholders
     ▼
[4. Sentence Chunking & Budget Planning (ContextPlanner)]
     │ Evaluates slot tokens vs budget equation: I(x) + G(I(x)) + S <= n_ctx_slot
     │ Splits into sentence units (default cap: 60-120 words per unit)
     ▼
[5. LLM Inference (llama-server Backend)]
     │ - Model: Gemma 4 E2B Q4_K_XL (or user GGUF)
     │ - MTP Speculative Draft: mtp-gemma-4-E2B-it.gguf (--spec-type draft-mtp)
     │ - Context: 12,800 tokens default; expand + reload only if input > 0.4 of window
     │ - Streaming tokens over SSE -> PromptCompiler nonces
     ▼
[6. Validation & Reassembly (Validators & Reassembler)]
     │ - UnitValidator: refusal filter, placeholder completeness check
     │ - Atom Unmasking: exact byte restoration of all code/URLs/paths
     ▼
[7. Presentation & Replacement]
     │ - Panel Mode: Two-state redline diff view (main_window.py)
     │ - Silent Mode: Verified target-matched clipboard paste
     │ - RAM Undo: Instant target-verified restore
```

---

## 2. Core Subsystems & Components

### 2.1 Native Core & IPC Protocol (`crates/` & `stet/core/ipc_client.py`)
- **`crates/stet-core`**: 232 verified Rust unit tests. Manages physical modifier release tracking, cloud clipboard privacy suppression (`set_privacy_suppression`), clipboard restore gates, frame codecs, semver-aware handshakes, and JSON-RPC command dispatching.
- **`crates/stet-win32`**: Direct Win32 FFI bindings linked against real MSVC libraries (window management, message pumps, named pipes, privacy formats, and input synthesis).
- **`crates/stet-uia-broker`**: Out-of-process COM UI Automation provider interacting with foreground apps without UIPI elevation.
- **Rust Hotkey Host (`hotkey_host.rs`)**: Dedicated background message pump thread owning a message-only `StetHotkeyHost` window. Registers OS hotkeys (`F9`, `F10`, `Shift+F9`, `Ctrl+F10`), survives user switching, and dispatches `event.hotkey_fired` frames over the pipe.
- **`stet/core/native_daemon.py`**: Daemon lifecycle supervisor attached to a Windows Job Object (`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) preventing orphan processes on crash. Automatic stale instance termination (`_terminate_stale_daemons`) prevents pipe collisions and hotkey conflicts. Runs completely un-elevated (no admin requirement).
- **`stet/core/ipc_client.py`**: Framed JSON-RPC 2.0 client communicating over `\\.\pipe\stet_ipc_v2` with length-prefixed 4 MiB headers and HMAC-SHA-256 handshake. Preserves typed error codes and handles both command requests (`command.capture_selection`, `command.paste_text`, `command.register_hotkeys`, `command.unregister_hotkeys`) and incoming async events.
### 2.2 Correction Engine (`stet/core/` & `stet/llm/`)
- **`ContextPlanner`**: Formulates token budget boundaries, monolithic vs sliding window passes with 2-sentence overlap and continuous disjoint intervals.
- **`PromptCompiler`**: Generates fresh 128-bit cryptographic nonces (`CONTENT_BEGIN_<nonce>`) to eliminate prompt injection and ambiguous boundary cuts.
- **`DocumentProtector`**: Builds an immutable atom table masking sensitive items before inference and restoring them verbatim afterward.
- **`UnitValidator` & `DocumentValidator`**: Enforces strict structural preservation, zero-refusal guarantees, and atom survival checks.

### 2.3 User Experience & UI Layer (`stet/ui/`)
- **Background UI Pre-warming (`_prewarm_ui`)**: Pre-warms `CorrectionWindow` offscreen during idle startup concurrently with model loading. Caches Qt stylesheets (`stet.qss`), Windows DirectWrite font tables, and Win32 window handles, dropping first-trigger latency from 1666ms to <15ms.
- **Foreground Focus Reclaim (`_activate_window_to_foreground`, `_restore_capture_focus`)**: Resolves Windows OS foreground lock-out during the instant capturing shell lifecycle. Before yielding foreground to the target application to read selected text, Stet authorizes itself via `AllowSetForegroundWindow(os.getpid())`. Once capture finishes, `_activate_window_to_foreground` synchronizes thread queues via `AttachThreadInput`, fires a zero-overhead `keybd_event(0,0,0,0)` input pump, and invokes `BringWindowToTop`, `SetForegroundWindow`, and `SetActiveWindow` to ensure Stet reliably takes the active foreground and keyboard focus so `Enter`, `Escape`, and `Tab` keystrokes target Stet instead of the background application.
- **Button Priority & Focus Hierarchy (`main_window.py`)**: `accept_btn` is marked as the primary default and auto-default action (`setDefault(True)`, `setAutoDefault(True)`), while secondary buttons (`cancel_btn`, `copy_btn`) explicitly disable `autoDefault` to avoid hijacking `Enter`. Upon correction completion (`_on_correction_ready`) and chat turns (`_on_chat_done`), `accept_btn` automatically acquires keyboard focus (unless the user is actively typing in the chat input or editing text). A logical focus navigation chain (`QWidget.setTabOrder`) connects `corr_edit -> chat_input -> accept_btn -> copy_btn -> cancel_btn -> edit_text_btn -> strength_combo -> help_btn -> settings_btn`.
- **Tray Actions**: Direct actions for Correct (F9), Rewrite (Shift+F9), dynamic Saved Actions submenu, and RAM-only Undo.
- **Review Window (`main_window.py`)**: Two-state redline diff view (strikethrough red / addition blue & green typo pairs) with full keyboard navigation (Enter accept, Ctrl+Enter chat, Escape close). Includes explicit loading state indicators (`⏳ Loading model (initializing)…`) when triggered while models are still booting or warming up KV cache.
- **Onboarding (`welcome_window.py`)**: 3-step guided flow with private AI setup, mode selection, and instant demo diff.
