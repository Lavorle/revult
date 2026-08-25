//! Nested pump helpers (plan §4.1.1 Mechanism 1).
//!
//! Product path: Python `event_wait` → `renpy_host.wait_until(deadline_ms)` →
//! `EventLoopExtPumpEvents::pump_app_events` → return to same stack frame.

use std::time::{Duration, Instant};

use log::debug;

/// Host monotonic clock origin (ms since process start for get_ticks).
static START: std::sync::OnceLock<Instant> = std::sync::OnceLock::new();

pub fn process_start() -> Instant {
    *START.get_or_init(Instant::now)
}

pub fn get_ticks_ms() -> u64 {
    process_start().elapsed().as_millis() as u64
}

/// Compute a pump timeout from an absolute host deadline (ms since start).
#[allow(dead_code)]
pub fn timeout_until(deadline_ms: u64) -> Option<Duration> {
    let now = get_ticks_ms();
    if deadline_ms <= now {
        Some(Duration::from_millis(0))
    } else {
        Some(Duration::from_millis(deadline_ms - now))
    }
}

/// Placeholder for GIL-released OS wait accounting (Phase 1 metrics).
pub fn log_wait(deadline_ms: u64, waited: Duration) {
    debug!(
        "wait_until deadline_ms={deadline_ms} waited={:?} ticks={}",
        waited,
        get_ticks_ms()
    );
}
