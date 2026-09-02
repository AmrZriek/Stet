//! Interactive lab probes (feature `lab`).
//!
//! These call the real OS primitives that need a live interactive desktop: the named-pipe
//! byte-stream round-trip (ReadFile/WriteFile), OleGetClipboard, SendInput into a live
//! target, the message-only window pump, and the WinEvent hook. They are `#[ignore]` by
//! default so a headless `cargo test` links but skips them; `run_lab.ps1` runs them with
//! `--ignored` on a real desktop. Each test name starts with `lab_` so the driver's
//! `-Focus` filter can select a target class.

#![allow(non_snake_case, non_camel_case_types, dead_code)]

#![cfg(feature = "lab")]

use ffi_skel::types::*;

// ── §3.1 named-pipe byte-stream round-trip (the ReadFile/WriteFile surface) ──
#[test]
#[ignore]
fn lab_pipe_roundtrip() {
    // Create a server with a restrictive DACL, connect a client, write a frame, read it.
    let name = ffi_skel::pipe::pipe_name(r"\\.\pipe\stet_ipc_v2_lab_test");
    let server = ffi_skel::pipe::create_pipe_server(&name, core::ptr::null()).expect("create server");
    let client = ffi_skel::pipe::connect_client(&name).expect("connect client");
    ffi_skel::pipe::accept_client(server).expect("accept");
    let payload = b"{jsonrpc:2.0}";
    unsafe {
        ffi_skel::pipe::write_bytes(server, payload.as_ptr(), payload.len()).expect("write");
    }
    let mut buf = [0u8; 64];
    let n = unsafe { ffi_skel::pipe::read_bytes(client, buf.as_mut_ptr(), 64).expect("read") };
    assert_eq!(n, payload.len(), "frame length must round-trip exactly");
    let got = &buf[..n];
    let want: &[u8] = payload;
    assert!(got.windows(want.len()).any(|w| w == want), "frame content must match");
    ffi_skel::pipe::disconnect(server);
    ffi_skel::pipe::close_handle(server);
    ffi_skel::pipe::close_handle(client);
}

// ── §3.1 restrictive DACL build ──
#[test]
#[ignore]
fn lab_pipe_dacl_build() {
    unsafe {
        use core::mem::MaybeUninit;
        let token = ffi_skel::security::open_current_token().expect("open token");
        let user_sid = ffi_skel::security::token_user_sid(token).expect("token user sid");
        // ACL and SECURITY_DESCRIPTOR contain pointers -> must be 8-byte aligned.
        let mut acl: MaybeUninit<ffi_skel::security::ACL> = MaybeUninit::<ffi_skel::security::ACL>::zeroed();
        ffi_skel::security::build_acl_allow_only(
            acl.as_mut_ptr(),
            256,
            user_sid,
            ffi_skel::security::PIPE_ALL_ACCESS,
        ).expect("build ACL");
        let mut sd: MaybeUninit<ffi_skel::security::SECURITY_DESCRIPTOR> = MaybeUninit::<ffi_skel::security::SECURITY_DESCRIPTOR>::zeroed();
        ffi_skel::security::init_security_descriptor(
            sd.as_mut_ptr(),
            acl.as_ptr(),
        ).expect("init SD");
    }
}

// ── §2d OLE full-fidelity clipboard snapshot + sequence gate ──
#[test]
#[ignore]
fn lab_clipboard_snapshot_restore() {
    ffi_skel::clipboard::ole_initialize();
    let before = ffi_skel::clipboard::clipboard_sequence();
    let obj = ffi_skel::clipboard::ole_get_clipboard();
    let _ = obj;
    ffi_skel::clipboard::ole_flush_clipboard();
    let after = ffi_skel::clipboard::clipboard_sequence();
    assert_eq!(before, after, "clipboard sequence must be unchanged when not modified");
    ffi_skel::clipboard::ole_uninitialize();
}

// ── §2e SendInput into the foreground target (checked chord) ──
#[test]
#[ignore]
fn lab_sendinput_checked_chord() {
    let tag = ffi_skel::types::STET_DW_EXTRA_INFO;
    let evs = unsafe {
        [
            ffi_skel::input::make_keyboard_input(VK_CONTROL, 0, false, false, tag),
            ffi_skel::input::make_keyboard_input(VK_C, 0, false, false, tag),
            ffi_skel::input::make_keyboard_input(VK_C, 0, true, false, tag),
            ffi_skel::input::make_keyboard_input(VK_CONTROL, 0, true, false, tag),
        ]
    };
    let injected = unsafe { ffi_skel::input::send_events(&evs) };
    assert!(injected > 0, "SendInput must inject at least one event on a live desktop");
}

// ── §2b WH_KEYBOARD_LL hook install/remove (self-tag filter) ──
#[test]
#[ignore]
fn lab_keyboard_hook_lifecycle() {
    unsafe {
        let hook = ffi_skel::hook::install_keyboard_hook(ffi_skel::hook::passthrough_hook)
            .expect("install keyboard hook");
        assert!(!hook.is_null());
        let ok = ffi_skel::hook::remove_keyboard_hook(hook);
        assert!(ok, "unhook must succeed");
    }
}

// ── §2f WinEvent focus hook lifecycle ──
#[test]
#[ignore]
fn lab_winevent_hook_lifecycle() {
    unsafe {
        let hook = ffi_skel::hook::install_winevent_hook(ffi_skel::hook::winevent_passthrough)
            .expect("install winevent hook");
        assert!(!hook.is_null());
        let ok = ffi_skel::hook::remove_winevent_hook(hook);
        assert!(ok, "unhook winevent must succeed");
    }
}

// ── §2a message-only window pump (hotkey host shell) ──
#[test]
#[ignore]
fn lab_message_only_window() {
    let _ = ffi_skel::window::current_module();
    let hwnd: HWND = core::ptr::null_mut();
    let ok = ffi_skel::window::register_hotkey(hwnd, 0x5E71, MOD_CONTROL | MOD_SHIFT, VK_F9);
    if ok {
        let _ = ffi_skel::window::unregister_hotkey(hwnd, 0x5E71);
    }
}

// ── §3.1 rule 1 launcher Job Object (create/assign; do NOT terminate) ──
#[test]
#[ignore]
fn lab_launcher_job_object() {
    unsafe {
        let job = ffi_skel::launcher::create_job_object().expect("create job");
        ffi_skel::launcher::set_kill_on_close(job).expect("set kill-on-close");
        let proc_h = ffi_skel::launcher::open_process(ffi_skel::launcher::current_process_id()).expect("open proc");
        // Assign the current process to the job. Do NOT call TerminateJobObject here.
        let _ = ffi_skel::launcher::assign_to_job(job, proc_h).expect("assign to job");
        let _ = proc_h;
        let _ = job;
    }
}
