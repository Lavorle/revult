//! Host-fed event queue (winit + TimerWheel → Python).

use std::collections::VecDeque;
use std::sync::Mutex;

/// Serializable event for Python (mirrors pygame.event.Event fields we need).
#[derive(Debug, Clone)]
pub struct HostEvent {
    pub type_id: u32,
    pub dict: Vec<(String, EventValue)>,
}

#[derive(Debug, Clone)]
pub enum EventValue {
    Int(i64),
    #[allow(dead_code)]
    Float(f64),
    Bool(bool),
    Str(String),
    #[allow(dead_code)]
    None,
}

impl HostEvent {
    pub fn simple(type_id: u32) -> Self {
        Self {
            type_id,
            dict: vec![],
        }
    }

    pub fn with(type_id: u32, dict: Vec<(String, EventValue)>) -> Self {
        Self { type_id, dict }
    }
}

/// Process-global queue. Main thread produces; Python consumes under GIL.
pub struct EventQueue {
    inner: Mutex<VecDeque<HostEvent>>,
}

impl EventQueue {
    pub const fn new() -> Self {
        Self {
            inner: Mutex::new(VecDeque::new()),
        }
    }

    pub fn push(&self, ev: HostEvent) {
        if let Ok(mut q) = self.inner.lock() {
            q.push_back(ev);
        }
    }

    pub fn poll(&self) -> Option<HostEvent> {
        self.inner.lock().ok().and_then(|mut q| q.pop_front())
    }

    #[allow(dead_code)]
    pub fn peek_type(&self) -> Option<u32> {
        self.inner
            .lock()
            .ok()
            .and_then(|q| q.front().map(|e| e.type_id))
    }

    pub fn len(&self) -> usize {
        self.inner.lock().map(|q| q.len()).unwrap_or(0)
    }

    #[allow(dead_code)]
    pub fn clear(&self) {
        if let Ok(mut q) = self.inner.lock() {
            q.clear();
        }
    }
}

pub static EVENT_QUEUE: EventQueue = EventQueue::new();

// --- Well-known event type ids (must match renpy.pygame.locals / host shims) ---
// pygame SDL event numbers used by Ren'Py; host shims register the same integers.
pub mod types {
    pub const NOEVENT: u32 = 0;
    pub const QUIT: u32 = 256;
    pub const KEYDOWN: u32 = 768;
    pub const KEYUP: u32 = 769;
    pub const TEXTEDITING: u32 = 770;
    pub const TEXTINPUT: u32 = 771;
    pub const MOUSEMOTION: u32 = 1024;
    pub const MOUSEBUTTONDOWN: u32 = 1025;
    pub const MOUSEBUTTONUP: u32 = 1026;
    pub const MOUSEWHEEL: u32 = 1027;
    /// Legacy SDL2-style umbrella (kept for any consumer still matching 512).
    pub const WINDOWEVENT: u32 = 512;
    /// SDL3 / host_pygame.locals.WINDOWRESIZED = 0x206.
    /// Ren'Py `core.py` matches this exact type to set force_redraw after resize.
    pub const WINDOWRESIZED: u32 = 0x206;
    /// SDL3 WINDOWEXPOSED = 0x204 (SWDraw full_redraw path).
    pub const WINDOWEXPOSED: u32 = 0x204;
    // Custom Ren'Py types start after user events; host registers via Python.
    // Placeholders filled at runtime once Python registers names.
}
