# Phase 2h — Win32 FFI Skeleton + Interactive Lab Harness

**Container scope.** Everything in this folder is the Phase 2h deliverable and is
*self-contained, comittable, and traceable* — it lives entirely under `phase2h/`,
never pollutes the verified `crates/` tree, and can be deleted with one `rmdir`.

## Purpose

Phase 2h is the **interactive Windows lab gate** (REWRITE_PLAN §5 "Phase 2h"). The pure
safety/logic layers are already implemented and TDD-verified in `crates/stet-core`
(156/156 tests). What remains and cannot be exercised headlessly is the **literal Win32
FFI wiring** and the **per-target interactive matrix**. This folder provides:

1. `ffi-skel/` — a standalone Rust crate of **raw `extern "system"` Win32 bindings**
   (no `windows` crate; explicit `#[link]` to the real MSVC import libs) plus typed
   safe wrappers and layout/round-trip tests. It **links and resolves every symbol here**,
   proving the FFI surface is real, and includes a few deterministic headless smoke tests.
2. `lab/` — the interactive Windows scenario matrix (Win32 Edit, RichEdit/Word,
   Chromium, terminals, UIPI, IME, RDP, NVDA/JAWS, Sticky/Filter keys, slow targets,
   core restart/crash) + a PowerShell driver that a real desktop session runs.

## Why raw FFI instead of the `windows` crate

The `windows` crate v0.62.2 surfaced a genuine dead-end: `ReadFile`/`WriteFile` are
NOT exported in any reachable module under the enabled features (searched the full crate
source). Declaring the OS functions directly via `extern "system"` + `#[link]` needs no
feature gate at all, and the MSVC linker resolves every import-lib symbol exactly once.

## Verified here (headless compile-only machine)

- `cargo test` builds the crate, **links every declared extern against the real
  `kernel32/user32/advapi32/ole32` import libs** (linkprobe), and asserts key struct
  layouts on `x86_64-pc-windows-msvc`.
- Deterministic smoke tests call a subset of OS functions safe without a GUI session.
- Behavior wiring (real pipe round-trip, SendInput into a live target, SetWinEventHook,
  OLE GetClipboard) is **lab-only**, gated behind `#[cfg(feature = "lab")]` (off by default).

## Layout

    phase2h/
      README.md
      Cargo.toml                <- tiny workspace (members = ffi-skel)
      .gitignore
      ffi-skel/
        src/lib.rs              <- module re-exports + crate docs
        src/types.rs            <- Win32 base types, constants, struct layouts
        src/pipe.rs             <- named-pipe server/client primitives (§3.1)
        src/security.rs         <- SID/DACL + pipe security descriptors
        src/input.rs            <- SendInput synthesis, key state, self-tag
        src/hook.rs             <- WH_KEYBOARD_LL + set-winevent-hook
        src/window.rs           <- message-only HWND + diff-based RegisterHotKey
        src/clipboard.rs        <- clipboard snapshot/restore (OleGetClipboard)
        src/launcher.rs         <- suspended-start + Job Object handle handoff (§3.1 r1)
        src/linkprobe.rs        <- references EVERY extern -> forces symbol resolution
        tests/layout.rs         <- size_of/align_of assertions for key structs
        tests/smoke.rs          <- deterministic headless OS-call tests
        tests/link_grid.rs      <- module-by-module link reference checklist
      lab/
        matrix.md               <- interactive scenario matrix (Phase 2h acceptance gate)
        run_lab.ps1             <- PowerShell driver for a real Windows desktop session

## How to use

    cd phase2h
    $env:CARGO_INCREMENTAL='0'   # Windows AV file-lock workaround
    cargo test -j 1              # link + layout proof (this machine)
    cargo test --features lab    # additionally enables interactive lab probes (desktop)

## Status / what is NOT done here

A **skeleton + harness**, deliberately. The FFI surface is provable but end-to-end
*behavior* requires an interactive Windows desktop and is exercised in `lab/`. Not yet
wired into `stet-core`; that cutover happens in the desktop lab after the matrix passes,
alongside the existing verified policy layer.
