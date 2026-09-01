//! Link probe.
//!
//! This module references the public wrapper API of every subsystem. Each wrapper calls a
//! raw `extern "system"` function, so a clean link on `x86_64-pc-windows-msvc` requires
//! the linker to resolve every underlying symbol against the real kernel32/user32/advapi32/
//! ole32 import libs. A clean link therefore *proves* the declared FFI surface maps to
//! genuine exported OS entry points, without a live desktop.
//!
//! tests/link_grid.rs asserts the surface is non-empty and internally consistent; the
//! definitive proof is a clean `cargo test` link.

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]

use crate::*;

/// Number of public wrapper link points that transitively force an `extern` symbol.
/// Referencing each wrapper keeps it alive at link time so its extern dependency resolves.
#[allow(dead_code)]
pub fn link_point_count() -> usize {
    let mut n = 0usize;

    // pipe
    let _ = pipe::close_handle as *const () as usize; n += 1;
    let _ = pipe::connect_client as *const () as usize; n += 1;
    let _ = pipe::create_pipe_server as *const () as usize; n += 1;
    let _ = pipe::accept_client as *const () as usize; n += 1;
    let _ = pipe::client_pid as *const () as usize; n += 1;
    let _ = pipe::read_bytes as *const () as usize; n += 1;
    let _ = pipe::write_bytes as *const () as usize; n += 1;
    let _ = pipe::peek_available as *const () as usize; n += 1;
    let _ = pipe::disconnect as *const () as usize; n += 1;

    // security
    let _ = security::create_well_known_sid as *const () as usize; n += 1;
    let _ = security::token_user_sid as *const () as usize; n += 1;
    let _ = security::open_current_token as *const () as usize; n += 1;
    let _ = security::build_acl_allow_only as *const () as usize; n += 1;
    let _ = security::init_security_descriptor as *const () as usize; n += 1;
    let _ = security::apply_restrictive_dacl as *const () as usize; n += 1;

    // input
    let _ = input::make_keyboard_input as *const () as usize; n += 1;
    let _ = input::send_key_event as *const () as usize; n += 1;
    let _ = input::send_events as *const () as usize; n += 1;
    let _ = input::chord_events as *const () as usize; n += 1;
    let _ = input::is_key_down as *const () as usize; n += 1;
    let _ = input::is_async_key_down as *const () as usize; n += 1;
    let _ = input::vk_to_scan as *const () as usize; n += 1;
    let _ = input::current_message_extra_info as *const () as usize; n += 1;
    let _ = input::set_message_extra_info as *const () as usize; n += 1;
    let _ = input::is_stet_self_event as *const () as usize; n += 1;

    // hook
    let _ = hook::install_keyboard_hook as *const () as usize; n += 1;
    let _ = hook::remove_keyboard_hook as *const () as usize; n += 1;
    let _ = hook::install_winevent_hook as *const () as usize; n += 1;
    let _ = hook::remove_winevent_hook as *const () as usize; n += 1;
    let _ = hook::current_thread_id as *const () as usize; n += 1;
    let _ = hook::passthrough_hook as *const () as usize; n += 1;
    let _ = hook::winevent_passthrough as *const () as usize; n += 1;

    // window
    let _ = window::register_hotkey as *const () as usize; n += 1;
    let _ = window::unregister_hotkey as *const () as usize; n += 1;
    let _ = window::current_module as *const () as usize; n += 1;
    let _ = window::create_message_only_window as *const () as usize; n += 1;
    let _ = window::register_window_class as *const () as usize; n += 1;
    let _ = window::message_pump as *const () as usize; n += 1;

    // launcher
    let _ = launcher::create_job_object as *const () as usize; n += 1;
    let _ = launcher::set_kill_on_close as *const () as usize; n += 1;
    let _ = launcher::assign_to_job as *const () as usize; n += 1;
    let _ = launcher::terminate_job as *const () as usize; n += 1;
    let _ = launcher::open_process as *const () as usize; n += 1;
    let _ = launcher::init_attribute_list as *const () as usize; n += 1;
    let _ = launcher::update_handle_list as *const () as usize; n += 1;
    let _ = launcher::delete_attribute_list as *const () as usize; n += 1;
    let _ = launcher::resume_thread as *const () as usize; n += 1;
    let _ = launcher::suspend_thread as *const () as usize; n += 1;
    let _ = launcher::current_process_id as *const () as usize; n += 1;

    // clipboard
    let _ = clipboard::ole_initialize as *const () as usize; n += 1;
    let _ = clipboard::ole_uninitialize as *const () as usize; n += 1;
    let _ = clipboard::ole_get_clipboard as *const () as usize; n += 1;
    let _ = clipboard::ole_flush_clipboard as *const () as usize; n += 1;
    let _ = clipboard::clipboard_sequence as *const () as usize; n += 1;
    let _ = clipboard::open_clipboard as *const () as usize; n += 1;
    let _ = clipboard::close_clipboard as *const () as usize; n += 1;
    let _ = clipboard::empty_clipboard as *const () as usize; n += 1;
    let _ = clipboard::set_clipboard_unicode as *const () as usize; n += 1;
    let _ = clipboard::count_clipboard_formats as *const () as usize; n += 1;
    let _ = clipboard::suppression_flag_can_upload_cloud as *const () as usize; n += 1;

    n
}

/// The wrapper names referenced above, grouped by subsystem. Asserted non-empty in
/// tests/link_grid.rs.
pub const LINK_POINTS: &[&str] = &[
    "pipe: close_handle", "pipe: connect_client", "pipe: create_pipe_server", "pipe: accept_client",
    "pipe: client_pid", "pipe: read_bytes", "pipe: write_bytes", "pipe: peek_available", "pipe: disconnect",
    "security: create_well_known_sid", "security: token_user_sid", "security: open_current_token",
    "security: build_acl_allow_only", "security: init_security_descriptor", "security: apply_restrictive_dacl",
    "input: make_keyboard_input", "input: send_key_event", "input: send_events", "input: chord_events",
    "input: is_key_down", "input: is_async_key_down", "input: vk_to_scan", "input: current_message_extra_info",
    "input: set_message_extra_info", "input: is_stet_self_event",
    "hook: install_keyboard_hook", "hook: remove_keyboard_hook", "hook: install_winevent_hook",
    "hook: remove_winevent_hook", "hook: current_thread_id", "hook: passthrough_hook", "hook: winevent_passthrough",
    "window: register_hotkey", "window: unregister_hotkey", "window: current_module",
    "window: create_message_only_window", "window: register_window_class", "window: message_pump",
    "launcher: create_job_object", "launcher: set_kill_on_close", "launcher: assign_to_job",
    "launcher: terminate_job", "launcher: open_process", "launcher: init_attribute_list",
    "launcher: update_handle_list", "launcher: delete_attribute_list", "launcher: resume_thread",
    "launcher: suspend_thread", "launcher: current_process_id",
    "clipboard: ole_initialize", "clipboard: ole_uninitialize", "clipboard: ole_get_clipboard",
    "clipboard: ole_flush_clipboard", "clipboard: clipboard_sequence", "clipboard: open_clipboard",
    "clipboard: close_clipboard", "clipboard: empty_clipboard", "clipboard: set_clipboard_unicode",
    "clipboard: count_clipboard_formats", "clipboard: suppression_flag_can_upload_cloud",
];