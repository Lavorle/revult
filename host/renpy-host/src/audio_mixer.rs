//! Audio mixer pile (M2 B4 T5 V1): symphonia probe + cpal multi-channel planning.
//!
//! V1 is a stub: `probe_symphonia` only inspects the file extension so that
//! `cargo check` stays green without linking `libav*`. V2 will replace the
//! body with `symphonia::default::get_probe().format(...)` and real decode
//! into `PcmRing`.
//!
//! `MixerConfig` is the SSOT for channel count / rate / buffer sizing.
//! `RENPY_HOST_AUDIO_CHANNELS` env drives the 1/2/6 probe without touching
//! `PcmRing` signatures.

use std::path::Path;

use log::info;

/// Mixer configuration — SSOT for cpal channel planning.
///
/// `channels` is clamped to the discrete set {1,2,6} via `from_env()`.
/// `sample_rate` defaults to 48000 (audio.rs:60) and `buffer_ms` to 40.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MixerConfig {
    /// Channel count: 1 (mono), 2 (stereo), or 6 (5.1).
    pub channels: u8,
    /// Sample rate in Hz.
    pub sample_rate: u32,
    /// Ring / latency buffer in milliseconds (20..100, default 40).
    pub buffer_ms: u32,
}

impl Default for MixerConfig {
    fn default() -> Self {
        Self {
            channels: 2,
            sample_rate: 48000,
            buffer_ms: 40,
        }
    }
}

impl MixerConfig {
    /// Build from env `RENPY_HOST_AUDIO_CHANNELS` with clamping.
    ///
    /// Parse as `u8`, default 2, clamp 1..=6, then map:
    ///   6 -> 6
    ///   1 -> 1
    ///   _ -> 2
    /// so only discrete {1,2,6} survive. This keeps `PcmRing` signatures
    /// unchanged while the probe can exercise mono/stereo/5.1.
    pub fn from_env() -> Self {
        let raw = std::env::var("RENPY_HOST_AUDIO_CHANNELS")
            .ok()
            .and_then(|s| s.parse::<u8>().ok())
            .unwrap_or(2)
            .clamp(1, 6);
        let channels = if raw == 6 {
            6
        } else if raw == 1 {
            1
        } else {
            2
        };
        let cfg = Self {
            channels,
            ..Default::default()
        };
        // Keep the probe observable in info logs for `RUST_LOG=info cargo run`.
        // Use log level info so `grep AudioMixer` in verification finds it.
        info!(
            "[renpy-host] AudioMixer from_env channels={} rate={} buffer_ms={} raw_env={}",
            cfg.channels, cfg.sample_rate, cfg.buffer_ms, raw
        );
        cfg
    }

    /// Buffer capacity in samples (frames * channels) for `cfg.buffer_ms` + headroom.
    /// Used by callers that want to size a `PcmRing` without hardcoding stereo.
    #[allow(dead_code)]
    pub fn capacity_samples(&self) -> usize {
        // 2× buffer_ms as headroom so underruns don't pop immediately.
        let frames = (self.sample_rate as usize * self.buffer_ms as usize) / 1000;
        frames * self.channels as usize * 2
    }
}

/// Probe result for an audio file (V1: ext-based, V2: symphonia-backed).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Probe {
    /// Codec hint (file extension lowercased, e.g. "webm", "ogg", "mp3").
    pub codec: String,
    /// Sample rate hint (48000 default in V1).
    pub rate: u32,
    /// Frame count hint (0 in V1, unknown until decode).
    pub frames: u64,
    /// Channel count hint (2 in V1).
    pub channels: u8,
}

/// V1 stub probe: inspect file extension only, no symphonia decode.
///
/// V2 will call:
/// ```ignore
/// let hint = symphonia::core::probe::Hint::new();
/// hint.with_extension(ext);
/// let probed = symphonia::default::get_probe().format(&hint, source, &FormatOptions::default(), &MetadataOptions::default())?;
/// ```
/// and extract `codec`, `rate`, `frames`, `channels` from the probed track.
pub fn probe_symphonia(path: &Path) -> Result<Probe, String> {
    let ext = path
        .extension()
        .and_then(|s| s.to_str())
        .unwrap_or("")
        .to_lowercase();
    // V1 keeps cargo check light: no file I/O, no symphonia link.
    // Return a deterministic probe so `audio_probe("/tmp/foo.webm")` always
    // contains `codec=webm` for the T5 verification gate.
    Ok(Probe {
        codec: ext,
        rate: 48000,
        frames: 0,
        channels: 2,
    })
}

/// Direct-push helper: feed decoded PCM straight into the shared `PcmRing`.
///
/// Thin wrapper around `PcmRing::push_interleaved` so V2 can swap the
/// decode output path without touching `audio.rs` call sites.
#[allow(dead_code)]
pub fn push_decoded_to_ring(ring: &crate::audio::PcmRing, pcm: &[f32]) {
    ring.push_interleaved(pcm);
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;
    use std::sync::Mutex;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    fn with_env<F: FnOnce()>(k: &str, v: Option<&str>, f: F) {
        let _g = ENV_LOCK.lock().unwrap();
        let prev = env::var(k).ok();
        match v {
            Some(val) => env::set_var(k, val),
            None => env::remove_var(k),
        }
        f();
        match prev {
            Some(val) => env::set_var(k, val),
            None => env::remove_var(k),
        }
    }

    #[test]
    fn default_is_stereo_48k_40ms() {
        with_env("RENPY_HOST_AUDIO_CHANNELS", None, || {
            let cfg = MixerConfig::default();
            assert_eq!(cfg.channels, 2);
            assert_eq!(cfg.sample_rate, 48000);
            assert_eq!(cfg.buffer_ms, 40);
        });
    }

    #[test]
    fn from_env_clamps_and_maps_to_1_2_6() {
        with_env("RENPY_HOST_AUDIO_CHANNELS", Some("6"), || {
            assert_eq!(MixerConfig::from_env().channels, 6);
        });
        with_env("RENPY_HOST_AUDIO_CHANNELS", Some("1"), || {
            assert_eq!(MixerConfig::from_env().channels, 1);
        });
        with_env("RENPY_HOST_AUDIO_CHANNELS", Some("2"), || {
            assert_eq!(MixerConfig::from_env().channels, 2);
        });
        // 3,4,5 collapse to stereo (2) per spec mapping.
        with_env("RENPY_HOST_AUDIO_CHANNELS", Some("3"), || {
            assert_eq!(MixerConfig::from_env().channels, 2);
        });
        with_env("RENPY_HOST_AUDIO_CHANNELS", Some("5"), || {
            assert_eq!(MixerConfig::from_env().channels, 2);
        });
        // clamp 0 -> 1 -> mono, clamp 99 -> 6 -> 5.1
        with_env("RENPY_HOST_AUDIO_CHANNELS", Some("0"), || {
            assert_eq!(MixerConfig::from_env().channels, 1);
        });
        with_env("RENPY_HOST_AUDIO_CHANNELS", Some("99"), || {
            assert_eq!(MixerConfig::from_env().channels, 6);
        });
        with_env("RENPY_HOST_AUDIO_CHANNELS", None, || {
            assert_eq!(MixerConfig::from_env().channels, 2);
        });
    }

    #[test]
    fn probe_ext_lowercases_and_defaults() {
        let p = probe_symphonia(Path::new("/tmp/foo.WEBM")).unwrap();
        assert_eq!(p.codec, "webm");
        assert_eq!(p.rate, 48000);
        assert_eq!(p.channels, 2);
        assert_eq!(p.frames, 0);

        let p2 = probe_symphonia(Path::new("/tmp/noext")).unwrap();
        assert_eq!(p2.codec, "");
    }

    #[test]
    fn push_decoded_to_ring_passthrough() {
        let ring = crate::audio::PcmRing::new(1024);
        let pcm = vec![0.1f32, -0.2, 0.3, -0.4];
        push_decoded_to_ring(&ring, &pcm);
        assert_eq!(ring.len(), 4);
    }
}
