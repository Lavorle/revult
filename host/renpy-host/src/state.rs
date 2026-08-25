//! Shared host runtime state (window, GPU, timers, arena, audio, video clocks).

use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

use crate::arena::GpuArena;
use crate::audio::AudioEngine;
use crate::gpu::GpuState;
use crate::shader::NativeShaderComposer;
use crate::timer::TimerWheel;
use winit::window::Window;

/// Per-channel A/V presentation clock (Phase 6).
/// Video upload is marshaled on the main thread; this only tracks presentation time.
#[derive(Debug, Clone)]
pub struct VideoClock {
    pub start_ms: u64,
    pub paused: bool,
    pub pause_started_ms: Option<u64>,
    pub pause_accum_ms: u64,
}

impl VideoClock {
    pub fn pos_ms(&self, now_ms: u64) -> f64 {
        let mut elapsed = now_ms.saturating_sub(self.start_ms);
        elapsed = elapsed.saturating_sub(self.pause_accum_ms);
        if let Some(ps) = self.pause_started_ms {
            elapsed = elapsed.saturating_sub(now_ms.saturating_sub(ps));
        }
        elapsed as f64 / 1000.0
    }
}

pub struct HostState {
    pub window: Option<Arc<Window>>,
    pub gpu: Option<GpuState>,
    pub timers: TimerWheel,
    pub frames: u64,
    pub should_exit: bool,
    pub text_input_active: bool,
    pub custom_types: HashMap<String, u32>,
    pub next_custom_type: u32,
    pub width: u32,
    pub height: u32,
    pub title: String,
    pub arena: GpuArena,
    #[allow(dead_code)]
    /// Native WGSL shader composer (single source of truth for shader-part
    /// composition). Held on `HostState` for future real compose paths; the
    /// bin crate does not read it yet, so it is retained for API stability.
     pub composer: NativeShaderComposer,
    pub audio: AudioEngine,
    /// Channel number → A/V clock (movie / video channels).
    pub video_clocks: HashMap<i32, VideoClock>,
    /// True after a product frame was presented to the swapchain.
    /// Idle RedrawRequested skips render_clear while this is set so product content
    /// is not overwritten by the dark-teal clear between presents.
    pub last_product_present: bool,
    /// Count of successful swapchain product presents (not RTT-only paths).
    pub product_presents: u64,
    /// Wall time of last successful product swapchain present (feel AC-F SSOT).
    pub last_product_present_at: Option<Instant>,
    /// Recent inter-product-present gaps in milliseconds (ring-capped).
    pub inter_present_gaps_ms: Vec<f32>,
    /// Idle clears that ran while last_product_present was true (should stay 0
    /// when the present-ownership skip is working; used by capture-cycle gates).
    pub idle_clears_after_present: u64,
    /// Programmatic drawable size when the WM ignores `request_inner_size`
    /// (common on some Wayland compositors). `window_size()` returns this until a
    /// real live size change (maximize / user drag, or WM accepting the request).
    /// Keeps hermetic resize probes from false-green at the create size while
    /// live maximize remains the SSOT via the Resized path.
    pub forced_drawable: Option<(u32, u32)>,
    /// Live `inner_size` chrome when `forced_drawable` was set. Resized that still
    /// reports this chrome must not clear the force; a different live size means
    /// the user/WM moved the window (maximize) and wins.
    pub forced_from_chrome: Option<(u32, u32)>,
}

// Future wiring: `crate::config::HostConfig::from_env()` is the single
// `RENPY_HOST_*` read site. `HostState` stays env-free this pass to avoid
// wide init churn; later `host_state()` / `PythonRuntime::bootstrap()` can
// be seeded from `HostConfig` instead of ad-hoc `std::env::var` calls.
impl HostState {
    pub fn new() -> Self {
        Self {
            window: None,
            gpu: None,
            timers: TimerWheel::new(),
            frames: 0,
            should_exit: false,
            text_input_active: false,
            custom_types: HashMap::new(),
            next_custom_type: 0x8000,
            width: 1280,
            height: 720,
            title: "renpy-host".into(),
            arena: GpuArena::new(),
            composer: NativeShaderComposer::new(),
            audio: AudioEngine::new(),
            video_clocks: HashMap::new(),
            last_product_present: false,
            product_presents: 0,
            last_product_present_at: None,
            inter_present_gaps_ms: Vec::new(),
            idle_clears_after_present: 0,
            forced_drawable: None,
            forced_from_chrome: None,
        }
    }
}

pub fn host_state() -> &'static Mutex<HostState> {
    static STATE: OnceLock<Mutex<HostState>> = OnceLock::new();
    STATE.get_or_init(|| Mutex::new(HostState::new()))
}
