# Stet Pipeline Architecture & Engineering Reference

> Canonical Reference: `docs/PROJECT_PIPELINE.md`  
> Version: `v1.4.0` | Last Updated: 2026-09-03

---

## 1. End-to-End Processing Flow

```
[1. User Action]
     │ Global Hotkey Trigger (F9 / Shift+F9) or Tray Menu Action
     ▼
[2. Target & Selection Capture]
     │ Window Handle, Thread/Process ID & Selection Capture
     │ (Direct UIA / Win32 SendInput / macOS AXUIElement / Native Core IPC)
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
- **`crates/stet-core`**: 156 verified Rust unit tests. Manages physical modifier release tracking, clipboard restore gates, and frame codecs.
- **`crates/stet-win32`**: Direct Win32 FFI bindings linked against real MSVC libraries.
- **`stet/core/ipc_client.py`**: Framed JSON-RPC 2.0 client communicating over `\\.\pipe\stet_ipc_v2` with length-prefixed 4 MiB headers and HMAC-SHA-256 handshake.

### 2.2 Correction Engine (`stet/core/` & `stet/llm/`)
- **`ContextPlanner`**: Formulates token budget boundaries, monolithic vs sliding window passes with 2-sentence overlap and continuous disjoint intervals.
- **`PromptCompiler`**: Generates fresh 128-bit cryptographic nonces (`CONTENT_BEGIN_<nonce>`) to eliminate prompt injection and ambiguous boundary cuts.
- **`DocumentProtector`**: Builds an immutable atom table masking sensitive items before inference and restoring them verbatim afterward.
- **`UnitValidator` & `DocumentValidator`**: Enforces strict structural preservation, zero-refusal guarantees, and atom survival checks.

### 2.3 User Experience & UI Layer (`stet/ui/`)
- **Tray Actions**: Direct actions for Correct (F9), Rewrite (Shift+F9), dynamic Saved Actions submenu, and RAM-only Undo.
- **Review Window (`main_window.py`)**: Two-state redline diff view (strikethrough red / addition blue & green typo pairs) with full keyboard navigation (Enter accept, Ctrl+Enter chat, Escape close).
- **Onboarding (`welcome_window.py`)**: 3-step guided flow with private AI setup, mode selection, and instant demo diff.
