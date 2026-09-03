pub mod protocol;

use protocol::{BrokerOutcome, BrokerRequest, BrokerResponse, BrokerState};

pub const MAX_UIA_TEXT_LENGTH: i32 = 100_000;

#[cfg(windows)]
mod win_uia {
    use super::*;

    #[repr(C)]
    struct GUID {
        data1: u32,
        data2: u16,
        data3: u16,
        data4: [u8; 8],
    }

    const CLSID_CUIAutomation: GUID = GUID {
        data1: 0xff48dba4,
        data2: 0xbf32,
        data3: 0x4f70,
        data4: [0xa6, 0x1e, 0xf3, 0xb2, 0xf1, 0x66, 0xa5, 0x37],
    };

    const IID_IUIAutomation: GUID = GUID {
        data1: 0x30cbe57d,
        data2: 0xd9d0,
        data3: 0x4a2a,
        data4: [0xab, 0x13, 0x7a, 0xc5, 0xac, 0x48, 0x25, 0xee],
    };

    #[link(name = "ole32")]
    extern "system" {
        fn CoInitializeEx(pvReserved: *mut core::ffi::c_void, dwCoInit: u32) -> i32;
        fn CoUninitialize();
        fn CoCreateInstance(
            rclsid: *const GUID,
            pUnkOuter: *mut core::ffi::c_void,
            dwClsContext: u32,
            riid: *const GUID,
            ppv: *mut *mut core::ffi::c_void,
        ) -> i32;
    }

    #[link(name = "oleaut32")]
    extern "system" {
        fn SysFreeString(bstrString: *const u16);
        fn SysStringLen(pbstr: *const u16) -> u32;
    }

    unsafe fn call_v0(ptr: *mut core::ffi::c_void, slot: usize) -> i32 {
        let vtable = *(ptr as *mut *mut usize);
        let func: extern "system" fn(*mut core::ffi::c_void) -> i32 =
            core::mem::transmute(*vtable.add(slot));
        func(ptr)
    }

    unsafe fn call_v1(ptr: *mut core::ffi::c_void, slot: usize, a1: *mut core::ffi::c_void) -> i32 {
        let vtable = *(ptr as *mut *mut usize);
        let func: extern "system" fn(*mut core::ffi::c_void, *mut core::ffi::c_void) -> i32 =
            core::mem::transmute(*vtable.add(slot));
        func(ptr, a1)
    }

    unsafe fn call_v1_hwnd(ptr: *mut core::ffi::c_void, slot: usize, hwnd: usize, a1: *mut core::ffi::c_void) -> i32 {
        let vtable = *(ptr as *mut *mut usize);
        let func: extern "system" fn(*mut core::ffi::c_void, usize, *mut core::ffi::c_void) -> i32 =
            core::mem::transmute(*vtable.add(slot));
        func(ptr, hwnd, a1)
    }

    unsafe fn call_v2_pattern(ptr: *mut core::ffi::c_void, slot: usize, pattern_id: i32, a1: *mut core::ffi::c_void) -> i32 {
        let vtable = *(ptr as *mut *mut usize);
        let func: extern "system" fn(*mut core::ffi::c_void, i32, *mut core::ffi::c_void) -> i32 =
            core::mem::transmute(*vtable.add(slot));
        func(ptr, pattern_id, a1)
    }

    unsafe fn call_v2_text(ptr: *mut core::ffi::c_void, slot: usize, max_len: i32, a1: *mut *mut u16) -> i32 {
        let vtable = *(ptr as *mut *mut usize);
        let func: extern "system" fn(*mut core::ffi::c_void, i32, *mut *mut u16) -> i32 =
            core::mem::transmute(*vtable.add(slot));
        func(ptr, max_len, a1)
    }

    unsafe fn release(ptr: *mut core::ffi::c_void) {
        if !ptr.is_null() {
            call_v0(ptr, 2); // IUnknown::Release
        }
    }

    pub fn capture_uia_selection(req: &BrokerRequest) -> BrokerResponse {
        let mut states = vec![BrokerState::RequestReceived];
        unsafe {
            let _ = CoInitializeEx(core::ptr::null_mut(), 0); // COINIT_MULTITHREADED = 0

            let mut p_uia: *mut core::ffi::c_void = core::ptr::null_mut();
            let hr = CoCreateInstance(
                &CLSID_CUIAutomation,
                core::ptr::null_mut(),
                1, // CLSCTX_INPROC_SERVER
                &IID_IUIAutomation,
                &mut p_uia,
            );
            if hr < 0 || p_uia.is_null() {
                return BrokerResponse {
                    request_id: req.request_id,
                    outcome: BrokerOutcome::InternalError,
                    text: None,
                    truncated: false,
                    states_seen: states,
                };
            }

            // Step 2: Get focused element or element from hwnd
            let mut p_elem: *mut core::ffi::c_void = core::ptr::null_mut();
            let hr = if req.window_handle != 0 {
                // IUIAutomation::ElementFromHandle is slot 6
                call_v1_hwnd(p_uia, 6, req.window_handle as usize, &mut p_elem as *mut _ as *mut core::ffi::c_void)
            } else {
                // IUIAutomation::GetFocusedElement is slot 8
                call_v1(p_uia, 8, &mut p_elem as *mut _ as *mut core::ffi::c_void)
            };

            if hr < 0 || p_elem.is_null() {
                release(p_uia);
                return BrokerResponse {
                    request_id: req.request_id,
                    outcome: BrokerOutcome::SelectionUnavailable,
                    text: None,
                    truncated: false,
                    states_seen: states,
                };
            }
            states.push(BrokerState::FocusedElementResolved);

            // Step 3: Get current pattern (UIA_TextPatternId = 10014). Slot 16 in IUIAutomationElement
            let mut p_pattern: *mut core::ffi::c_void = core::ptr::null_mut();
            let hr = call_v2_pattern(p_elem, 16, 10014, &mut p_pattern as *mut _ as *mut core::ffi::c_void);
            if hr < 0 || p_pattern.is_null() {
                release(p_elem);
                release(p_uia);
                return BrokerResponse {
                    request_id: req.request_id,
                    outcome: BrokerOutcome::SelectionUnavailable,
                    text: None,
                    truncated: false,
                    states_seen: states,
                };
            }
            states.push(BrokerState::TextPatternResolved);

            // Step 4: GetSelection (slot 3 in IUIAutomationTextPattern)
            states.push(BrokerState::BlockingGetSelection);
            let mut p_ranges: *mut core::ffi::c_void = core::ptr::null_mut();
            let hr = call_v1(p_pattern, 3, &mut p_ranges as *mut _ as *mut core::ffi::c_void);
            if hr < 0 || p_ranges.is_null() {
                release(p_pattern);
                release(p_elem);
                release(p_uia);
                return BrokerResponse {
                    request_id: req.request_id,
                    outcome: BrokerOutcome::SelectionUnavailable,
                    text: None,
                    truncated: false,
                    states_seen: states,
                };
            }

            // Step 5: get_Length (slot 3 in IUIAutomationTextRangeArray)
            let mut len: i32 = 0;
            let hr = call_v1(p_ranges, 3, &mut len as *mut _ as *mut core::ffi::c_void);
            if hr < 0 || len <= 0 {
                release(p_ranges);
                release(p_pattern);
                release(p_elem);
                release(p_uia);
                return BrokerResponse {
                    request_id: req.request_id,
                    outcome: BrokerOutcome::SelectionUnavailable,
                    text: None,
                    truncated: false,
                    states_seen: states,
                };
            }

            // Step 6: GetElement(0) (slot 4 in IUIAutomationTextRangeArray)
            let mut p_range: *mut core::ffi::c_void = core::ptr::null_mut();
            let hr = call_v2_pattern(p_ranges, 4, 0, &mut p_range as *mut _ as *mut core::ffi::c_void);
            if hr < 0 || p_range.is_null() {
                release(p_ranges);
                release(p_pattern);
                release(p_elem);
                release(p_uia);
                return BrokerResponse {
                    request_id: req.request_id,
                    outcome: BrokerOutcome::SelectionUnavailable,
                    text: None,
                    truncated: false,
                    states_seen: states,
                };
            }

            // Step 7: GetText(MAX_UIA_TEXT_LENGTH) (slot 12 in IUIAutomationTextRange)
            let mut bstr: *mut u16 = core::ptr::null_mut();
            let hr = call_v2_text(p_range, 12, MAX_UIA_TEXT_LENGTH, &mut bstr);
            let (text_opt, truncated) = if hr >= 0 && !bstr.is_null() {
                let slen = SysStringLen(bstr) as usize;
                let slice = core::slice::from_raw_parts(bstr, slen);
                let text = String::from_utf16_lossy(slice);
                SysFreeString(bstr);
                let is_trunc = slen >= MAX_UIA_TEXT_LENGTH as usize;
                (Some(text), is_trunc)
            } else {
                (None, false)
            };

            release(p_range);
            release(p_ranges);
            release(p_pattern);
            release(p_elem);
            release(p_uia);
            CoUninitialize();

            states.push(BrokerState::ResultSerialized);

            let outcome = match &text_opt {
                Some(t) if !t.is_empty() => BrokerOutcome::Ok,
                _ => BrokerOutcome::SelectionUnavailable,
            };

            BrokerResponse {
                request_id: req.request_id,
                outcome,
                text: text_opt,
                truncated,
                states_seen: states,
            }
        }
    }
}

pub fn handle_broker_request(req: &BrokerRequest) -> BrokerResponse {
    #[cfg(windows)]
    {
        win_uia::capture_uia_selection(req)
    }
    #[cfg(not(windows))]
    {
        BrokerResponse {
            request_id: req.request_id,
            outcome: BrokerOutcome::SelectionUnavailable,
            text: None,
            truncated: false,
            states_seen: vec![BrokerState::RequestReceived],
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn handle_request_returns_valid_response() {
        let req = BrokerRequest {
            pid: std::process::id(),
            window_handle: 0,
            request_id: 42,
            deadline_ms: 250,
        };
        let resp = handle_broker_request(&req);
        assert_eq!(resp.request_id, 42);
        assert!(!resp.states_seen.is_empty());
    }
}
