# Phase 2h — Interactive Windows Lab Matrix (the acceptance gate)

> This is the per-target interactive matrix from REWRITE_PLAN `§5` `Phase 2h` and the
> hardening cases in `§4.4` / `§2b` / `§2d` / `§2e`. The pure decision logic is already TDD-verified
> in `crates/stet-core` (156/156 tests) and the raw Win32 FFI surface is link-proven in
> `../ffi-skel`. What this matrix exercises is the **end-to-end behavior** on a real desktop.
>
> Each row is a **target scenario** with its expected pass criterion. A scenario is PASS only
> when the *behavior* holds (capture correct, paste verified, no clipboard clobber, no wrong
> window paste, correct chord, hard abort when the target changed/selection changed). A
> scenario FAIL or a test that relies on an unverified assumption is a session blocker: it
> means the strict gate does not hold for that target class and shadow-mode field data may
> not be accepted for it.

## How to use

Run the driver on a real Windows desktop (interactive session — the lab needs a keyboard,
a window station with a desktop, and the real input/clipboard):

    cd phase2h/lab
    .\run_lab.ps1            # runs the whole matrix, prints PASS/FAIL per scenario
    .\run_lab.ps1 -Focus "Edit"   # run only one target class

The driver uses the FFI skeleton's link-proven surface to call the real OS functions.

## Target classes (the matrix rows)

### 1. Win32 Edit (notepad, classic edit control)
| Id | Scenario | Expected |
|----|----------|----------|
| E1 | Selection capture via UIA TextPattern on a classic edit | Exact text, source=uia, truncated=false |
| E2 | Ctrl+C chord capture fallback (UIA unavailable) | Seq moved, alternate Ctrl+Insert on retry |
| E3 | Ctrl+V paste into the same edit | Verified consumption, clipboard restored to pre-paste |
| E4 | Foreground changed between capture and paste | Hard abort `aborted_wrong_target` |
| E5 | Selection changed between capture and paste | Hard abort `aborted_selection_changed` |

### 2. RichEdit / Word (rich text)
| Id | Scenario | Expected |
|----|----------|----------|
| R1 | Selection capture via TextPattern2 | Exact text + format metadata, source=uia |
| R2 | Paste preserves HTML/RTF (no CF_UNICODETEXT wipe) | `preserves_all_formats` guard passes |
| R3 | Slow Word/RichEdit consumer | `paste_unverified` after bounded observation, no restore |
| R4 | IME composition active during chord | Deferred (do not inject while composing) |

### 3. Chromium / Electron (VS Code, Chrome, Slack)
| Id | Scenario | Expected |
|----|----------|----------|
| C1 | Modifier-release tracker before Ctrl+C (the Shift+F9 fix) | No synthetic Shift-up; chord only after physical Shift released |
| C2 | Ctrl+V paste into the renderer | Consumption verified (queued input not enough) |
| C3 | Stet-own window (Stet PID / image) excluded | Never a paste target |
| C4 | `Chrome_RenderWidgetHostHWND` target class | `Editor` chord (Ctrl+V) |

### 4. Terminals (Windows Terminal, conhost)
| Id | Scenario | Expected |
|----|----------|----------|
| T1 | Capture chord Ctrl+Shift+C | Terminal chord per target metadata |
| T2 | Paste chord Ctrl+Shift+V | Terminal chord (NOT Ctrl+V) |
| T3 | RDP / remote_input session mode | `remote_input`; injected flags never treated as Stet-self |

### 5. Elevated / UIPI targets
| Id | Scenario | Expected |
|----|----------|----------|
| U1 | UIA capture into an elevated (admin) app | Broker isolation; circuit breaker on frozen PID |
| U2 | SendInput into an UIPI-blocked target | Hard abort `aborted_integrity`; never silent-fail |
| U3 | Elevated target abort | Never paste; never claim success |

### 6. Clipboard managers & RDP redirection
| Id | Scenario | Expected |
|----|----------|----------|
| CL1 | Third-party clipboard manager running | Restore-vs-paste race handled; external change = skip restore |
| CL2 | Cloud clipboard sync enabled | `CanUploadToCloudClipboard=0` set; no leak |
| CL3 | RDP clipboard redirection | Sequence-based restore; `aborted_clipboard_conflict` on change |

### 7. Accessibility (NVDA/JAWS, Sticky/Filter Keys)
| Id | Scenario | Expected |
|----|----------|----------|
| A1 | NVDA/JAWS active during capture | Capture unaffected; no dropped modifier |
| A2 | Sticky Keys latched Shift | Latched logical modifier never cleared by physical release |
| A3 | Filter Keys slow-input mode | Modifier-release tracker respects accessibility flags |

### 8. Slow paste targets
| Id | Scenario | Expected |
|----|----------|----------|
| S1 | Large Word/RichEdit slow consumer | `paste_unverified` within commit ceiling; no clobber |
| S2 | Core restart / broker crash | Recovery; no auto-restore of clipboard |
| S3 | Broker deadlock (COM hang) | Hard terminate + recreate; circuit breaker opens |

## Acceptance rule

A target class **passes** only if every row for it is PASS. Any row FAIL means the strict
gate (exact selection precondition, target binding, chord synthesis, clipboard restore) does
not hold for that class. Until every matrix row passes for a class, **shadow-mode field data
for that class is not evidence** and the native cutover for that class is NOT ready. The
matrix is re-run wholesale as Phase 6's release gate (REWRITE_PLAN §5/§6).

## What the FFI skeleton enables here

The `ffi-skel` crate in this folder is the *linkable surface* these scenarios call:
`create_pipe_server`/`connect_client`/`read_bytes`/`write_bytes` (§3.1 named pipe),
`build_acl_allow_only`/`apply_restrictive_dacl`/`token_user_sid` (§3.1 DACL),
`register_hotkey`/`unregister_hotkey` (§2a), `install_keyboard_hook` (§2b),
`install_winevent_hook` (§2f), `make_keyboard_input`/`send_events`/`chord_events`/`is_stet_self_event`
(§2e), `ole_get_clipboard`/`clipboard_sequence`/`set_clipboard_unicode`/`ole_flush_clipboard` (§2d),
`create_job_object`/`set_kill_on_close`/`assign_to_job`/`update_handle_list` (§3.1 rule 1, §4.1).


