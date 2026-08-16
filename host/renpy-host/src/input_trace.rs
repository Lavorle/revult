//! Env-gated input reachability counters for bare-product dead-input diagnose.
//!
//! Enabled only when `RENPY_HOST_INPUT_TRACE=1`. Pure Rust atomics — safe while
//! the GIL is released inside nested pump / wait_until.
//!
//! Counter map (Slice 0 / D2):
//!   (a) handle_window_event invocation count
//!   (b) try_nested_pump invocation count (not nested_pump_once — single site)
//!   (c) wait_until enter count
//!   (d) poll_event non-empty return count
//!   (e) wait_until early-exit because EVENT_QUEUE non-empty (Phase 0 dual-signal)

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::OnceLock;

static ENABLED: OnceLock<bool> = OnceLock::new();
static DUMPED: AtomicBool = AtomicBool::new(false);
static SIGNAL_HOOKED: AtomicBool = AtomicBool::new(false);

static A_HANDLE_WINDOW_EVENT: AtomicU64 = AtomicU64::new(0);
static B_TRY_NESTED_PUMP: AtomicU64 = AtomicU64::new(0);
static C_WAIT_UNTIL: AtomicU64 = AtomicU64::new(0);
static D_POLL_EVENT_NONEMPTY: AtomicU64 = AtomicU64::new(0);
static E_WAIT_UNTIL_EARLY_EXIT: AtomicU64 = AtomicU64::new(0);

// Minimal libc signal bits so timeout SIGTERM can flush without a crate dep.
#[cfg(unix)]
mod raw_signal {
    pub const SIGINT: i32 = 2;
    pub const SIGTERM: i32 = 15;
    pub const SIG_DFL: usize = 0;

    extern "C" {
        pub fn signal(sig: i32, handler: usize) -> usize;
        pub fn raise(sig: i32) -> i32;
    }
}

fn enabled() -> bool {
    *ENABLED.get_or_init(|| {
        matches!(
            std::env::var("RENPY_HOST_INPUT_TRACE").ok().as_deref(),
            Some("1")
        )
    })
}

#[inline]
pub fn count_handle_window_event() {
    if enabled() {
        A_HANDLE_WINDOW_EVENT.fetch_add(1, Ordering::Relaxed);
    }
}

/// Count (b) at `try_nested_pump` only — do not also count nested_pump_once.
#[inline]
pub fn count_try_nested_pump() {
    if enabled() {
        B_TRY_NESTED_PUMP.fetch_add(1, Ordering::Relaxed);
    }
}

#[inline]
pub fn count_wait_until() {
    if enabled() {
        C_WAIT_UNTIL.fetch_add(1, Ordering::Relaxed);
    }
}

#[inline]
pub fn count_poll_event_nonempty() {
    if enabled() {
        D_POLL_EVENT_NONEMPTY.fetch_add(1, Ordering::Relaxed);
    }
}

/// Count (e): wait_until broke out because EVENT_QUEUE was non-empty.
#[inline]
pub fn count_wait_until_early_exit() {
    if enabled() {
        E_WAIT_UNTIL_EARLY_EXIT.fetch_add(1, Ordering::Relaxed);
    }
}

/// Dump once on product exit / should_exit / Drop / request_quit / SIGTERM.
/// Safe to call from multiple sites; prints at most once.
pub fn dump_if_enabled() {
    if !enabled() {
        return;
    }
    if DUMPED.swap(true, Ordering::SeqCst) {
        return;
    }
    let a = A_HANDLE_WINDOW_EVENT.load(Ordering::Relaxed);
    let b = B_TRY_NESTED_PUMP.load(Ordering::Relaxed);
    let c = C_WAIT_UNTIL.load(Ordering::Relaxed);
    let d = D_POLL_EVENT_NONEMPTY.load(Ordering::Relaxed);
    let e = E_WAIT_UNTIL_EARLY_EXIT.load(Ordering::Relaxed);
    // eprintln so it survives log-level filters and lands in tee'd stderr+stdout.
    eprintln!("INPUT_TRACE a={a} b={b} c={c} d={d} e={e}");
    // Also log via env_logger for non-tee captures.
    log::info!("INPUT_TRACE a={a} b={b} c={c} d={d} e={e}");
}

/// RAII dump on Drop (covers normal unwind / main return).
pub struct DumpOnDrop;

impl Drop for DumpOnDrop {
    fn drop(&mut self) {
        dump_if_enabled();
    }
}

/// Install SIGTERM/SIGINT → dump so `timeout` SIGTERM can flush counters.
/// Idempotent; no-op when trace disabled.
pub fn install_exit_hooks() {
    if !enabled() {
        return;
    }
    if SIGNAL_HOOKED.swap(true, Ordering::SeqCst) {
        return;
    }
    #[cfg(unix)]
    {
        // SAFETY: handler only touches atomics + eprintln; reentrant dump is once-gated.
        unsafe {
            raw_signal::signal(raw_signal::SIGTERM, signal_dump as *const () as usize);
            raw_signal::signal(raw_signal::SIGINT, signal_dump as *const () as usize);
        }
    }
}

#[cfg(unix)]
extern "C" fn signal_dump(sig: i32) {
    dump_if_enabled();
    // Re-raise default termination so timeout/SIGINT still kill the process.
    unsafe {
        raw_signal::signal(raw_signal::SIGTERM, raw_signal::SIG_DFL);
        raw_signal::signal(raw_signal::SIGINT, raw_signal::SIG_DFL);
        let _ = raw_signal::raise(sig);
    }
}
