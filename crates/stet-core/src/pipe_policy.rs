//! Named-pipe security policy (Phase 2g, §3.1).
//!
//! Encodes the §3.1 Windows transport rules as pure, testable decisions. The FFI
//! bindings (CreateNamedPipeW, SetSecurityDescriptorDacl, etc.) call these predicates
//! and must obey them. This module is platform-independent and unit-tested.

/// The named-pipe endpoint name for the IPC transport (fixed per §3.1).
pub const PIPE_NAME: &str = r"\\.\pipe\stet_ipc_v2";

/// The launch secret size (256 bits / 32 bytes).
pub const LAUNCH_SECRET_LEN: usize = 32;

/// Options controlling pipe creation (mapped to Win32 flags in the FFI bindings).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PipeOptions {
    pub reject_remote_clients: bool,
    pub first_instance: bool,
    pub max_frame_bytes: usize,
}

impl PipeOptions {
    pub fn required() -> Self {
        PipeOptions { reject_remote_clients: true, first_instance: true, max_frame_bytes: 4 * 1024 * 1024 }
    }
}

/// Access-control SID choices for the restrictive DACL (§3.1 rule 3).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DaclPrinciple {
    AuthenticatedUser,
    System,
    Everyone,
    Anonymous,
}

/// Verify a DACL principle list is allowed: only the authenticated user and SYSTEM. Everyone and Anonymous forbidden.
pub fn dacl_allows(principles: &[DaclPrinciple]) -> bool {
    !principles.is_empty() && principles.iter().all(|p| matches!(p, DaclPrinciple::AuthenticatedUser | DaclPrinciple::System))
}

/// Peer image plausibility check. DIAGNOSTIC only (§3.1 rule 5).
pub fn peer_image_plausible(canonical_path: &str) -> bool {
    let lower = canonical_path.to_lowercase();
    !lower.is_empty() && !lower.contains(r"\stet-core") && !lower.contains(r"\stet-uia-broker")
}

/// One authenticated UI connection at a time; additional clients rejected (§3.1).
pub fn single_client_allowed(active_connections: usize) -> bool {
    active_connections == 0
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn required_pipe_options_enable_remote_reject_and_first_instance() {
        let o = PipeOptions::required();
        assert!(o.reject_remote_clients);
        assert!(o.first_instance);
        assert_eq!(o.max_frame_bytes, 4 * 1024 * 1024);
    }
    #[test]
    fn dacl_rejects_everyone_and_anonymous() {
        assert!(dacl_allows(&[DaclPrinciple::AuthenticatedUser]));
        assert!(dacl_allows(&[DaclPrinciple::AuthenticatedUser, DaclPrinciple::System]));
        assert!(!dacl_allows(&[DaclPrinciple::Everyone]));
        assert!(!dacl_allows(&[DaclPrinciple::Anonymous]));
        assert!(!dacl_allows(&[DaclPrinciple::AuthenticatedUser, DaclPrinciple::Everyone]));
    }
    #[test]
    fn empty_dacl_is_forbidden() {
        assert!(!dacl_allows(&[]));
    }
    #[test]
    fn single_client_allowed_only_when_zero_active() {
        assert!(single_client_allowed(0));
        assert!(!single_client_allowed(1));
        assert!(!single_client_allowed(2));
    }
    #[test]
    fn peer_image_rejects_core_and_broker_paths() {
        assert!(peer_image_plausible("C:\\Users\\me\\App\\stet-ui.exe"));
        assert!(!peer_image_plausible("C:\\Program Files\\stet-core.exe"));
        assert!(!peer_image_plausible("C:\\stet-uia-broker.exe"));
    }
    #[test]
    fn pipe_name_is_correct_endpoint() {
        assert_eq!(PIPE_NAME, r"\\.\pipe\stet_ipc_v2");
    }
}
