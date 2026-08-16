//! Timer wheel: PERIODIC / REDRAW / TIMEEVENT (plan §4.1.2).

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};

use crate::pump::get_ticks_ms;

static NEXT_TIMER_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimerKind {
    /// Ren'Py PERIODIC — default 50 ms repeating.
    Periodic,
    /// REDRAW one-shot or repeating.
    Redraw,
    /// TIMEEVENT one-shot or repeating.
    TimeEvent,
    /// Custom pygame event type id.
    Custom(u32),
}

#[derive(Debug, Clone)]
struct TimerEntry {
    id: u64,
    kind: TimerKind,
    event_type: u32,
    interval_ms: u64,
    next_due_ms: u64,
    repeating: bool,
}

/// Absolute-ms deadline wheel. Main-thread only.
#[derive(Default)]
pub struct TimerWheel {
    entries: BTreeMap<u64, TimerEntry>,
}

impl TimerWheel {
    pub fn new() -> Self {
        Self::default()
    }

    /// Register a timer. `interval_ms == 0` clears any timer for this event_type
    /// (pygame.time.set_timer semantics).
    ///
    /// `repeating=false` matches pygame.time.set_timer(..., once=True) used by
    /// Ren'Py for TIMEEVENT/REDRAW one-shots (transitions, PauseBehavior).
    pub fn set_timer(
        &mut self,
        event_type: u32,
        interval_ms: u64,
        kind: TimerKind,
        repeating: bool,
    ) -> u64 {
        self.entries.retain(|_, e| e.event_type != event_type);
        if interval_ms == 0 {
            return 0;
        }
        let id = NEXT_TIMER_ID.fetch_add(1, Ordering::Relaxed);
        let now = get_ticks_ms();
        self.entries.insert(
            id,
            TimerEntry {
                id,
                kind,
                event_type,
                interval_ms,
                next_due_ms: now.saturating_add(interval_ms),
                repeating,
            },
        );
        id
    }

    pub fn clear_timer_id(&mut self, id: u64) {
        self.entries.remove(&id);
    }

    pub fn clear_event_type(&mut self, event_type: u32) {
        self.entries.retain(|_, e| e.event_type != event_type);
    }

    pub fn next_deadline_ms(&self) -> Option<u64> {
        self.entries.values().map(|e| e.next_due_ms).min()
    }

    /// Fire all due timers; returns event type codes to inject.
    pub fn poll_due(&mut self) -> Vec<u32> {
        let now = get_ticks_ms();
        let mut fired = Vec::new();
        let mut remove = Vec::new();
        let mut reinsert = Vec::new();

        for (id, entry) in self.entries.iter() {
            if entry.next_due_ms <= now {
                fired.push(entry.event_type);
                if entry.repeating {
                    let mut e = entry.clone();
                    while e.next_due_ms <= now {
                        e.next_due_ms = e.next_due_ms.saturating_add(e.interval_ms);
                    }
                    reinsert.push(e);
                } else {
                    remove.push(*id);
                }
            }
        }
        for id in remove {
            self.entries.remove(&id);
        }
        for e in reinsert {
            self.entries.insert(e.id, e);
        }
        fired
    }
}
