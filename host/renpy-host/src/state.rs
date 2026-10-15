//! Shared host runtime state (window, GPU, timers, arena, audio, video clocks).

use std::cell::Cell;
use std::collections::HashMap;
use std::sync::atomic::Ordering;
use std::sync::{Arc, Mutex, OnceLock};
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

/// Hard cap on sub-callback interpolation advance (one buffer period worth of
/// headroom). Guardrail: the estimate can never run more than this far ahead
/// of the last *observed* callback counter, so a paused/starved callback
/// cannot push the video clock ahead of real audio output.
const AUDIO_INTERP_CAP_FRAMES: u64 = 4410; // 100 ms at 44100; independent of rate

/// Per-channel A/V presentation clock (Phase 6).
/// Video upload is marshaled on the main thread; this only tracks presentation time.
#[derive(Debug)]
pub struct VideoClock {
    pub start_ms: u64,
    /// Audio frames captured when the video clock armed (audio master).
    /// GLOBAL_SAMPLE_CLOCK is a **device-level** counter since cpal start; the
    /// raw value is the device time, not the video time, so the anchor keeps
    /// pos==0 when the clock starts regardless of when the audio device was
    /// opened. Previously the unanchored raw counter offset the presented
    /// index by the whole device age (observed drift_ms ≈ -9490 for the
    /// HuangmeiC menu, which opens the audio device ~9 s before the video).
    pub anchor_frames: u64,
    /// Interpolated position while paused (audio master freezes here).
    pub pause_frozen_frames: Option<u64>,
    pub paused: bool,
    pub pause_started_ms: Option<u64>,
    pub pause_accum_ms: u64,
    pub master: ClockMaster,
    pub drift_ms: f32,
    pub dropped: u32,
    pub repeated: u32,
    /// Sub-callback interpolation state (audio master). GLOBAL_SAMPLE_CLOCK
    /// only advances when the cpal callback runs (~one device buffer per
    /// call, ~40 ms) — without interpolation, index changes latch to that
    /// cadence (observed p50 42.7 ms / eff 23.5 fps on a 30 fps source).
    /// True once `video_clock_start` created this clock (vs a default/default
    /// referenced entry). Guards the wall-across continuity below: a clock
    /// with start_ms==0 that was never started must bind anchor=frames_now,
    /// not re-derive a bogus wall position.
    pub started: bool,
    /// Sub-callback interpolation state (audio master). GLOBAL_SAMPLE_CLOCK
    /// only advances when the cpal callback runs (~one device buffer per
    /// call, ~40 ms) — without interpolation, index changes latch to that
    /// cadence (observed p50 42.7 ms / eff 23.5 fps on a 30 fps source).
    pub audio_last_frames: Cell<u64>,
    pub audio_last_ms: Cell<u64>,
}

impl Clone for VideoClock {
    fn clone(&self) -> Self {
        Self {
            start_ms: self.start_ms,
            anchor_frames: self.anchor_frames,
            pause_frozen_frames: self.pause_frozen_frames,
            paused: self.paused,
            pause_started_ms: self.pause_started_ms,
            pause_accum_ms: self.pause_accum_ms,
            master: self.master,
            drift_ms: self.drift_ms,
            dropped: self.dropped,
            repeated: self.repeated,
            started: self.started,
            audio_last_frames: Cell::new(self.audio_last_frames.get()),
            audio_last_ms: Cell::new(self.audio_last_ms.get()),
        }
    }
}

impl Default for VideoClock {
    fn default() -> Self {
        Self {
            start_ms: 0,
            anchor_frames: 0,
            pause_frozen_frames: None,
            paused: false,
            pause_started_ms: None,
            pause_accum_ms: 0,
            master: ClockMaster::Wall,
            drift_ms: 0.0,
            dropped: 0,
            repeated: 0,
            started: false,
            audio_last_frames: Cell::new(0),
            audio_last_ms: Cell::new(0),
        }
    }
}

impl VideoClock {
    /// Current interpolated audio-device frame estimate (audio master).
    /// Established on the last observed callback counter; extrapolates at
    /// exactly `rate` frames between callbacks, capped hard.
    fn audio_est_frames(&self, now_ms: u64, rate: u32) -> u64 {
        let frames = crate::audio::GLOBAL_SAMPLE_CLOCK.load(Ordering::Relaxed);
        if rate == 0 {
            return frames;
        }
        let last_f = self.audio_last_frames.get();
        let last_ms = self.audio_last_ms.get();
        if frames != last_f {
            // A new callback step was observed: resample.
            self.audio_last_frames.set(frames);
            self.audio_last_ms.set(now_ms);
            return frames;
        }
        // Between callbacks: device drains at `rate` frames/s.
        let dt_ms = now_ms.saturating_sub(last_ms);
        let mut adv = (dt_ms as u128 * rate as u128 / 1000) as u64;
        adv = adv.min(AUDIO_INTERP_CAP_FRAMES);
        frames + adv
    }

    /// Audio-master presentation position in seconds (anchored, interpolated).
    pub fn audio_pos_s(&self, now_ms: u64, rate: u32) -> f64 {
        if rate == 0 {
            return 0.0;
        }
        if self.paused {
            if let Some(f) = self.pause_frozen_frames {
                return (f.saturating_sub(self.anchor_frames)) as f64 / rate as f64;
            }
        }
        let est = self.audio_est_frames(now_ms, rate);
        (est.saturating_sub(self.anchor_frames)) as f64 / rate as f64
    }

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
            ClockMaster::AudioSample { rate } => self.audio_pos_s(now_ms, rate),
        }
    }

    /// Anchor the clock to the audio-stream head at `now_ms` so a master
    /// switch keeps presentation continuity: pos just before and after the
    /// bind must agree (observed bug: bind re-based the index by the whole
    /// device age and `drift` never converged).
    pub fn bind_audio(&mut self, rate: u32, now_ms: u64) {
        let frames = crate::audio::GLOBAL_SAMPLE_CLOCK.load(Ordering::Relaxed);
        if self.master == ClockMaster::Wall && self.started {
            // Master switch with a real start: keep presentation continuity
            // so the index does not jump when the master changes.
            let wall_pos_s = match self.pos_ms(now_ms) {
                p if p.is_finite() => p.max(0.0),
                _ => 0.0,
            };
            // pos_ms is seconds; frames_behind = seconds * rate.
            self.anchor_frames = frames.saturating_sub((wall_pos_s * rate as f64).round() as u64);
        } else {
            // Never-started clock (or re-bind): anchor the audio counter so
            // pos is 0 at the bind instant.
            self.anchor_frames = frames;
        }
        // Interp pours on top of the anchored counter; resample from the bind
        // instant so the wall→audio master handoff holds.
        self.audio_last_frames.set(frames);
        self.audio_last_ms.set(now_ms);
        // If we were already on the AudioSample master (re-bind with a new
        // rate), keep the anchor: rate changes alone re-scale pos.
        self.master = ClockMaster::AudioSample { rate };
        log::info!(
            "[renpy-host] video_clock bind_audio rate={} master=AudioSample anchor_frames={} drift_ms={}",
            rate,
            self.anchor_frames,
            self.drift_ms
        );
    }

    /// Re-anchor the audio counter so that pos reads `pos_s` right now.
    /// This is what makes `video_clock_set_pos` meaningful under the audio
    /// master (previously it rewrote `start_ms`, which AudioSample pos ignores,
    /// so starved-presenter re-anchoring was a silent no-op).
    pub fn set_pos_audio(&mut self, pos_s: f64, rate: u32, now_ms: u64) -> bool {
        if rate == 0 {
            return false;
        }
        let frames = crate::audio::GLOBAL_SAMPLE_CLOCK.load(Ordering::Relaxed);
        let pos_frames = (pos_s.max(0.0) * rate as f64).round() as u64;
        self.anchor_frames = frames.saturating_sub(pos_frames);
        // Resample interpolation from this call so a later callback step
        // advances from the re-anchored base.
        self.audio_last_frames.set(frames);
        self.audio_last_ms.set(now_ms);
        self.pause_frozen_frames = None;
        true
    }
}

/// Wall-relative ms used for drift bookkeeping (pause-aware).
impl VideoClock {
    pub fn wall_elapsed_ms(&self, now_ms: u64) -> f64 {
        let mut wall_ms = now_ms
            .saturating_sub(self.start_ms)
            .saturating_sub(self.pause_accum_ms);
        if let Some(ps) = self.pause_started_ms {
            wall_ms = wall_ms.saturating_sub(now_ms.saturating_sub(ps));
        }
        wall_ms as f64
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
    /// IME candidate rectangle (x,y,w,h) in window physical pixels.
    /// Set by `renpy_host.set_text_input_rect`, consumed by winit
    /// `Window::set_ime_cursor_area` on next frame.
    pub ime_rect: Option<(i32, i32, i32, i32)>,
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
        log::info!(
            "[renpy-host] clock probe drift_ms=0 sample_clock=0 master=AudioSample HostState init"
        );
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
            ime_rect: None,
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
    use std::sync::Mutex;

    /// Serializes tests that mutate the process-global sample clock
    /// (cargo may run test fns in parallel threads).
    static TEST_CLOCK_LOCK: Mutex<()> = Mutex::new(());

    #[test]
    fn test_clock_master_wall_pos_ms_unchanged() {
        let mut c = VideoClock {
            start_ms: 1000,
            master: ClockMaster::Wall,
            ..Default::default()
        };
        let pos = c.pos_ms(1100);
        assert!(
            (pos - 0.1).abs() < 1e-6,
            "wall pos should be 0.1, got {}",
            pos
        );
        let pos2 = c.pos_ms(1500);
        assert!((pos2 - 0.5).abs() < 1e-6, "wall pos 0.5, got {}", pos2);
        // pause test
        c.paused = true;
        c.pause_started_ms = Some(1500);
        let pos_paused = c.pos_ms(1600);
        assert!(
            (pos_paused - 0.5).abs() < 1e-6,
            "paused should freeze at 0.5, got {}",
            pos_paused
        );
    }

    #[test]
    fn test_clock_master_bind_audio_switches_master() {
        let _g = TEST_CLOCK_LOCK.lock().unwrap();
        let mut c = VideoClock::default();
        assert_eq!(c.master, ClockMaster::Wall);
        c.bind_audio(48000, 1000);
        assert_eq!(c.master, ClockMaster::AudioSample { rate: 48000 });
        // drift initial 0
        assert!(c.drift_ms.abs() < 1e-6);
        // Anchored: device-level clock is at 0 => pos 0
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
        c.audio_last_frames.set(0);
        c.audio_last_ms.set(1000);
        let pos = c.pos_ms(1000);
        assert!(
            (pos - 0.0).abs() < 1e-6,
            "audio pos at 0 frames should be 0, got {}",
            pos
        );
        // 48000 frames => 1 sec (no wall dependence; pass now==1000 so the
        // interp window does not bias the estimate)
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(48000, Ordering::Relaxed);
        c.audio_last_frames.set(48000);
        c.audio_last_ms.set(1000);
        let pos2 = c.pos_ms(1000);
        assert!(
            (pos2 - 1.0).abs() < 1e-6,
            "48000 frames at 48000 rate => 1.0 sec, got {}",
            pos2
        );
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
    }

    #[test]
    fn test_bind_audio_anchors_stale_device_clock() {
        let _g = TEST_CLOCK_LOCK.lock().unwrap();
        // Regression: the HuangmeiC menu opens the audio device ~9 s before
        // the video clock arms. The device counter must NOT become the video
        // position — pos must start at 0 at arm/bind time.
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(420_000, Ordering::Relaxed);
        let mut c = VideoClock::default();
        c.started = true;
        c.start_ms = 900;
        c.bind_audio(44100, 1000); // wall pos at bind ≈ 0.1 s
        let pos = c.pos_ms(1000);
        c.audio_last_frames.set(420_000);
        // anchor = 420000 - 0.1*44100 = 415590 → pos ≈ 0.1 s (continuity)
        assert!(
            (pos - 0.1).abs() < 0.01,
            "pos after bind should be ~0.1 s (continuity), got {}",
            pos
        );
        // And 1 s later, pos should be ~1.1 s, NOT device time (10 s).
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(420_000 + 44100, Ordering::Relaxed);
        c.audio_last_frames.set(464100);
        c.audio_last_ms.set(2000);
        let pos2 = c.pos_ms(2000);
        assert!(
            (pos2 - 1.1).abs() < 0.02,
            "pos 1 s after bind should be 1.1 s, got {}",
            pos2
        );
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
    }

    #[test]
    fn test_video_clock_set_pos_audio_anchors() {
        let _g = TEST_CLOCK_LOCK.lock().unwrap();
        // set_pos under the audio master must move the presentation clock
        // (previously a silent no-op because start_ms is ignored).
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(100_000, Ordering::Relaxed);
        let mut c = VideoClock::default();
        c.started = true;
        c.start_ms = 0;
        c.bind_audio(44100, 0);
        c.audio_last_frames.set(100_000);
        c.audio_last_ms.set(5000);
        let ok = c.set_pos_audio(2.0, 44100, 5000);
        assert!(ok);
        let pos = c.pos_ms(5000);
        assert!(
            (pos - 2.0).abs() < 0.01,
            "set_pos should anchor pos to 2.0 s, got {}",
            pos
        );
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
    }

    #[test]
    fn test_audio_master_interp_does_not_run_ahead() {
        let _g = TEST_CLOCK_LOCK.lock().unwrap();
        // Between callbacks the estimate extrapolates at most one buffer
        // period (cap frames) past the last observed counter.
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
        let mut c = VideoClock::default();
        c.started = true;
        c.start_ms = 0;
        c.bind_audio(44100, 0);
        c.audio_last_frames.set(0);
        c.audio_last_ms.set(0);
        // 10 s of wall without a callback — estimate must be capped at
        // 100 ms (4410 frames), not 10 s.
        let pos = c.pos_ms(10_000);
        assert!(
            pos <= 0.101,
            "interp cap exceeded: pos={} s (cap 100 ms + anchor)",
            pos
        );
        assert!(pos >= 0.09, "interp should still extrapolate, got {}", pos);
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
    }

    #[test]
    fn test_clock_master_drift_probe_monotonic() {
        let _g = TEST_CLOCK_LOCK.lock().unwrap();
        // Simulate 30 frames, drift delta <40ms per frame
        let mut c = VideoClock {
            start_ms: 0,
            master: ClockMaster::Wall,
            ..Default::default()
        };
        c.started = true;
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
        c.bind_audio(48000, 0);
        c.audio_last_frames.set(0);
        c.audio_last_ms.set(0);
        let mut prev_drift = c.drift_ms;
        // Simulate wall advancing 16ms per frame, audio advancing 800 frames per 60fps (~16.6ms)
        for i in 0..30 {
            let now_ms = (i as u64) * 16;
            let wall_ms = now_ms as f64;
            let frames = (i as u64) * 800; // 48000/60
            crate::audio::GLOBAL_SAMPLE_CLOCK.store(frames, Ordering::Relaxed);
            c.audio_last_frames.set(frames);
            c.audio_last_ms.set(now_ms);
            let audio_ms = frames as f64 / 48000.0 * 1000.0;
            let drift = (wall_ms - audio_ms) as f32;
            // monotonic check: delta <40ms
            let delta = (drift - prev_drift).abs();
            assert!(
                delta < 40.0,
                "drift jump at frame {}: prev {} cur {} delta {}",
                i,
                prev_drift,
                drift,
                delta
            );
            prev_drift = drift;
            // also ensure pos_ms returns audio time
            let pos = c.pos_ms(now_ms);
            let expected = frames as f64 / 48000.0;
            assert!(
                (pos - expected).abs() < 1e-6,
                "pos mismatch at frame {}: got {} expected {}",
                i,
                pos,
                expected
            );
        }
        crate::audio::GLOBAL_SAMPLE_CLOCK.store(0, Ordering::Relaxed);
    }
}
