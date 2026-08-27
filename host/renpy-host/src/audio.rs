//! cpal audio callback + PCM ring (plan §4.1 / Phase 4).
//! Callback thread: read ring only — **no Python**.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use log::{info, warn};

/// Global sample clock (frames, not samples) — SSOT for VideoClock AudioSample master.
/// Incremented in PcmRing::fill_output; read by VideoClock::pos_ms without HostState lock.
pub static GLOBAL_SAMPLE_CLOCK: AtomicU64 = AtomicU64::new(0);
pub static GLOBAL_CHANNELS: AtomicU32 = AtomicU32::new(2);
pub static GLOBAL_DROPPED: AtomicU32 = AtomicU32::new(0);
pub static GLOBAL_REPEATED: AtomicU32 = AtomicU32::new(0);

/// Stereo f32 interleaved ring buffer shared with the cpal callback.
pub struct PcmRing {
    buf: Mutex<VecDeque<f32>>,
    capacity: usize,
}

impl PcmRing {
    pub fn new(capacity_samples: usize) -> Self {
        Self {
            buf: Mutex::new(VecDeque::with_capacity(capacity_samples)),
            capacity: capacity_samples,
        }
    }

    pub fn push_interleaved(&self, samples: &[f32]) {
        let mut q = self.buf.lock().unwrap();
        for &s in samples {
            if q.len() >= self.capacity {
                q.pop_front();
            }
            q.push_back(s);
        }
    }

    pub fn fill_output(&self, out: &mut [f32]) {
        let mut q = self.buf.lock().unwrap();
        let ch = GLOBAL_CHANNELS.load(Ordering::Relaxed).max(1) as usize;
        // Multi-channel expansion: per-frame interleaved fill.
        // ch==2 fast-path stays zero-change vs legacy sample-wise loop;
        // ch==1 (mono) / ch==6 (5.1) share the same chunked path so 5.1
        // preparation needs no extra allocation — underrun tails zero-fill.
        if ch == 1 || ch == 2 || ch == 6 {
            for frame in out.chunks_mut(ch) {
                for s in frame.iter_mut() {
                    *s = q.pop_front().unwrap_or(0.0);
                }
            }
        } else {
            // Unexpected ch (should not happen after MixerConfig clamp); fallback.
            for s in out.iter_mut() {
                *s = q.pop_front().unwrap_or(0.0);
            }
        }
        let frames = out.len() / ch.max(1);
        if frames > 0 {
            GLOBAL_SAMPLE_CLOCK.fetch_add(frames as u64, Ordering::Relaxed);
        }
    }

    pub fn len(&self) -> usize {
        self.buf.lock().unwrap().len()
    }

    pub fn clear(&self) {
        self.buf.lock().unwrap().clear();
    }
}

/// cpal::Stream is !Send on some platforms; we only start/stop from the main
/// (winit) thread, so this wrapper is intentional.
struct SendStream(#[allow(dead_code)] cpal::Stream);
// SAFETY: AudioEngine methods that touch the stream are only called from the
// main host thread; the callback itself only touches the Arc ring/volume.
unsafe impl Send for SendStream {}

pub struct AudioEngine {
    pub ring: Arc<PcmRing>,
    pub sample_rate: AtomicU32,
    pub channels: AtomicU32,
    pub running: AtomicBool,
    /// Master volume 0..1 as fixed-point 1e6.
    pub volume: Arc<AtomicU32>,
    pub sample_clock: AtomicU64,
    pub dropped: AtomicU32,
    pub repeated: AtomicU32,
    stream: Mutex<Option<SendStream>>,
}

impl AudioEngine {
    pub fn new() -> Self {
        // MixerConfig is SSOT for initial channel/rate/buffer sizing (T5).
        // Consumes RENPY_HOST_AUDIO_CHANNELS env via from_env() which logs
        // AudioMixer — required for RUST_LOG=info verification greps.
        let cfg = crate::audio_mixer::MixerConfig::from_env();
        let cap_samples = (cfg.sample_rate as usize)
            .checked_mul(cfg.channels as usize)
            .and_then(|v| v.checked_mul(2))
            .unwrap_or(48000 * 2 * 2)
            .max(48000 * 2);
        GLOBAL_CHANNELS.store(cfg.channels as u32, Ordering::Relaxed);
        info!(
            "[renpy-host] AudioMixer init channels={} rate={} buffer_ms={} cap_samples={} sample_clock={}",
            cfg.channels,
            cfg.sample_rate,
            cfg.buffer_ms,
            cap_samples,
            GLOBAL_SAMPLE_CLOCK.load(Ordering::Relaxed)
        );
        Self {
            ring: Arc::new(PcmRing::new(cap_samples)),
            sample_rate: AtomicU32::new(cfg.sample_rate),
            channels: AtomicU32::new(cfg.channels as u32),
            running: AtomicBool::new(false),
            volume: Arc::new(AtomicU32::new(1_000_000)),
            sample_clock: AtomicU64::new(0),
            dropped: AtomicU32::new(0),
            repeated: AtomicU32::new(0),
            stream: Mutex::new(None),
        }
    }

    pub fn set_volume(&self, v: f32) {
        let v = v.clamp(0.0, 1.0);
        self.volume
            .store((v * 1_000_000.0) as u32, Ordering::Relaxed);
    }

    #[allow(dead_code)]
    pub fn volume_f32(&self) -> f32 {
        self.volume.load(Ordering::Relaxed) as f32 / 1_000_000.0
    }

    pub fn start(&self) -> Result<(), String> {
        if self.running.load(Ordering::Relaxed) {
            return Ok(());
        }
        use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};

        let host = cpal::default_host();
        let device = host
            .default_output_device()
            .ok_or_else(|| "no default audio output device".to_string())?;
        let config = device
            .default_output_config()
            .map_err(|e| format!("default_output_config: {e}"))?;
        let sample_rate = config.sample_rate().0;
        let channels = config.channels() as u32;
        self.sample_rate.store(sample_rate, Ordering::Relaxed);
        self.channels.store(channels, Ordering::Relaxed);
        GLOBAL_CHANNELS.store(channels, Ordering::Relaxed);

        let ring = self.ring.clone();
        let vol = self.volume.clone();
        let err_fn = |e| warn!("cpal stream error: {e}");

        let stream = match config.sample_format() {
            cpal::SampleFormat::F32 => {
                let stream_config: cpal::StreamConfig = config.into();
                device
                    .build_output_stream(
                        &stream_config,
                        move |data: &mut [f32], _| {
                            ring.fill_output(data);
                            let v = vol.load(Ordering::Relaxed) as f32 / 1_000_000.0;
                            if (v - 1.0).abs() > 0.001 {
                                for s in data.iter_mut() {
                                    *s *= v;
                                }
                            }
                        },
                        err_fn,
                        None,
                    )
                    .map_err(|e| format!("build_output_stream: {e}"))?
            }
            cpal::SampleFormat::I16 => {
                let stream_config: cpal::StreamConfig = config.into();
                device
                    .build_output_stream(
                        &stream_config,
                        move |data: &mut [i16], _| {
                            let mut tmp = vec![0.0f32; data.len()];
                            ring.fill_output(&mut tmp);
                            let v = vol.load(Ordering::Relaxed) as f32 / 1_000_000.0;
                            for (o, s) in data.iter_mut().zip(tmp.iter()) {
                                let x = (s * v).clamp(-1.0, 1.0);
                                *o = (x * i16::MAX as f32) as i16;
                            }
                        },
                        err_fn,
                        None,
                    )
                    .map_err(|e| format!("build_output_stream: {e}"))?
            }
            other => return Err(format!("unsupported sample format: {other:?}")),
        };

        stream.play().map_err(|e| format!("stream.play: {e}"))?;
        *self.stream.lock().unwrap() = Some(SendStream(stream));
        self.running.store(true, Ordering::Relaxed);
        info!(
            "[renpy-host] cpal audio started rate={} channels={} sample_clock={}",
            sample_rate, channels, GLOBAL_SAMPLE_CLOCK.load(Ordering::Relaxed)
        );
        Ok(())
    }

    pub fn stop(&self) {
        *self.stream.lock().unwrap() = None;
        self.running.store(false, Ordering::Relaxed);
        self.ring.clear();
    }

    pub fn queue_pcm_f32(&self, samples: &[f32]) {
        self.ring.push_interleaved(samples);
    }

    pub fn queue_beep(&self, freq_hz: f32, duration_ms: u32, amplitude: f32) {
        let rate = self.sample_rate.load(Ordering::Relaxed).max(1) as f32;
        let ch = self.channels.load(Ordering::Relaxed).max(1) as usize;
        let n = ((duration_ms as f32) * rate / 1000.0) as usize;
        let mut samples = Vec::with_capacity(n * ch);
        for i in 0..n {
            let t = i as f32 / rate;
            let s = (t * freq_hz * std::f32::consts::TAU).sin() * amplitude;
            for _ in 0..ch {
                samples.push(s);
            }
        }
        self.queue_pcm_f32(&samples);
    }
}
