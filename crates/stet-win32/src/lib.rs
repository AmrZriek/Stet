//! Phase 2h raw Win32 FFI skeleton.
//!
//! This crate declares the literal Win32 function surface Stet needs for the native
//! core, using raw `extern "system"` bindings instead of the `windows` crate. The
//! `windows` crate v0.62.2 dead-ended on `ReadFile`/`WriteFile` (not exported under
//! any enabled feature); declaring the OS functions directly needs no feature gate.
//!
//! Everything here is a *provable* FFI surface: the MSVC linker resolves each extern
//! against the real kernel32/user32/advapi32/ole32 import libs exactly once (see
//! `linkprobe`), and the layout tests assert struct sizes on this x64 host.
//!
//! Behavior wiring is deliberately gated behind the `lab` feature. The crate compiles
//! and links everywhere but does not touch the clipboard, injection, hooks, or pipes
//! unless a desktop session exercises it.

#![allow(non_snake_case, non_camel_case_types, non_upper_case_globals, dead_code)]
#![allow(clippy::missing_safety_doc, clippy::useless_transmute)]

pub mod types;
pub mod pipe;
pub mod security;
pub mod input;
pub mod hook;
pub mod window;
pub mod clipboard;
pub mod launcher;

#[cfg(target_os = "windows")]
pub mod linkprobe;
