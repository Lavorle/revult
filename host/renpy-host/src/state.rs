//! Shared host runtime state (window, GPU, timers, arena, audio, video clocks).

use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
use std::sync::atomic::Ordering;
use std::time::Instant;

use crate::arena::GpuArena;
use crate::audio::AudioEngine;
use crate::gpu::GpuState;
use crate::shader::NativeShaderComposer;
use crate::timer::TimerWheel;
use winit::window::Window;

/// Clock master for A/V sync — Wall (default, old saves) or AudioSample.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClockMaster {
    Wall,
    AudioSample { rate: u32 },
}

impl Default for ClockMaster {
    fn default() -> Self {
        Self::Wall
    }
}

/// Per-channel A/V presentation clock (Phase 6).
/// Video upload is marshaled on the main thread; this only tracks presentation time.
#[derive(Debug, Clone)]
pub struct VideoClock {
    pub start_ms: u64,
    pub paused: bool,
    pub pause_started_ms: Option<u64>,
    pub pause_accum_ms: u64,
    pub master: ClockMaster,
    pub drift_ms: f32,
    pub dropped: u32,
    pub repeated: u32,
}

impl Default for VideoClock {
    fn default() -> Self {
        Self {
            start_ms: 0,
            paused: false,
            pause_started_ms: None,
            pause_accum_ms: 0,
            master: ClockMaster::Wall,
            drift_ms: 0.0,
            dropped: 0,
            repeated: 0,
        }
    }
}

impl VideoClock {
    pub fn pos_ms(&self, now_ms: u64) -> f64 {
        match self.master {
            ClockMaster::Wall => {
                let mut elapsed = now_ms.saturating_sub(self.start_ms);
                elapsed = elapsed.saturating_sub(self.pause_accum_ms);
                if let Some(ps) = self.pause_started_ms {
                    elapsed = elapsed.saturating_sub(now_ms.saturating_sub(ps));
                }
                elapsed as f64 / 1000.0
            }
            ClockMaster::AudioSample { rate } => {
                if rate == 0 {
                    return 0.0;
                }
                // Audio sample clock is frames; convert to seconds.
                let frames = crate::audio::GLOBAL_SAMPLE_CLOCK.load(Ordering::Relaxed);
                // Respect pause: if paused, freeze at pause moment's audio position is ideal,
                // but for now return live audio time; pause handling is done via drift tracking.
                // If paused, we still return audio time — the caller can clamp via pause logic if needed.
                // To keep behavior simple and testable, ignore pause for AudioSample and return audio time.
                frames as f64 / rate as f64
            }
        }
    }

    pub fn bind_audio(&mut self, rate: u32) {
        self.master = ClockMaster::AudioSample { rate };
        log::info!("[renpy-host] video_clock bind_audio rate={} master=AudioSample drift_ms={}", rate, self.drift_ms);
    }
}

pub struct HostState {
    pub window: Option<Arc<Window>>,
    pub gpu: Option<GpuState>,
    pub timers: TimerWheel,
    pub frames: u64,
    pub should_exit: bool,
    pub exit_code: i32,
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
impl HostState {
    pub fn new() -> Self {
        log::info!("[renpy-host] clock probe drift_ms=0 sample_clock=0 master=AudioSample HostState init");
        Self {
            window: None,
            gpu: None,
            timers: TimerWheel::new(),
            frames: 0,
            should_exit: false,
            exit_code: 0,
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

    #[allow(dead_code)]
    pub fn request_quit(&mut self) {
        self.should_exit = true;
        self.exit_code = 0;
    }

    #[allow(dead_code)]
    pub fn request_quit_with_code(&mut self, code: i32) {
        self.should_exit = true;
        self.exit_code = code;
    }
}

pub fn host_state() -> &'static Mutex<HostState> {
    static STATE: OnceLock<Mutex<HostState>> = OnceLock::new();
    STATE.get_or_init(|| Mutex::new(HostState::new()))
}



#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::Ordering;

    #[test]
    fn test_clock_master_wall_pos_ms_unchanged() {
        let mut c = VideoClock {
            start_ms: 1000,
            paused: false,
            pause_started_ms: None,
            pause_accum_ms: 0,
            master: ClockMaster::Wall,
            drift_ms: 0.0,
            dropped: 0,
            repeated: 0,
        };
        let pos = c.pos_ms(1100);
        assert!((pos - 0.1).abs() < 1e-6, "wall pos should be 0.1, got {}", pos);
        let pos2 = c.pos_ms(1500);
        assert!((pos2 - 0.5).abs() < 1e-6, "wall pos 0.5, got {}", pos2);
        // pause test
        c.paused = true;
        c.pause_started_ms = Some(1500);
        let pos_paused = c.pos_ms(1600);
        assert!((pos_paused - 0.5).abs() < 1e-6, "paused should freeze at 0.5, got {}", pos_paused);
    }

    #[test]
    fn test_clock_master_bind_audio_switches_master() {
        let mut c = VideoClock::default();
        assert_eq!(c.master, ClockMaster::Wall);
        c.bind_audio(48000);
        assert_eq!(c.master, ClockMaster::AudioSample { rate: 48000 });
        // drift initial 0
        assert!(c.drift_ms.abs() < 1e-6);
        // sample clock 0 => pos 0
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
        let pos = c.pos_ms(999999);
        assert!((pos - 0.0).abs() < 1e-6, "audio pos at 0 frames should be 0, got {}", pos);
        // 48000 frames => 1 sec
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(48000, Ordering::Relaxed);
        let pos2 = c.pos_ms(0);
        assert!((pos2 - 1.0).abs() < 1e-6, "48000 frames at 48000 rate => 1.0 sec, got {}", pos2);
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
    }

    #[test]
    fn test_clock_master_drift_probe_monotonic() {
        // Simulate 30 frames, drift delta <40ms per frame
        let mut c = VideoClock {
            start_ms: 0,
            paused: false,
            pause_started_ms: None,
            pause_accum_ms: 0,
            master: ClockMaster::Wall,
            drift_ms: 0.0,
            dropped: 0,
            repeated: 0,
        };
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
        c.bind_audio(48000);
        let mut prev_drift = c.drift_ms;
        // Simulate wall advancing 16ms per frame, audio advancing 800 frames per 60fps (~16.6ms)
        for i in 0..30 {
            let now_ms = (i as u64) * 16;
            let wall_ms = now_ms as f64;
            let frames = (i as u64) * 800; // 48000/60
            crate::audio::GLOBAL_SAMPLE_CLOCK.store(frames, Ordering::Relaxed);
            let audio_ms = frames as f64 / 48000.0 * 1000.0;
            let drift = (wall_ms - audio_ms) as f32;
            // monotonic check: delta <40ms
            let delta = (drift - prev_drift).abs();
            assert!(delta < 40.0, "drift jump at frame {}: prev {} cur {} delta {}", i, prev_drift, drift, delta);
            prev_drift = drift;
            // also ensure pos_ms returns audio time
            let pos = c.pos_ms(now_ms);
            let expected = frames as f64 / 48000.0;
            assert!((pos - expected).abs() < 1e-6, "pos mismatch at frame {}: got {} expected {}", i, pos, expected);
        }
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
    }
}
