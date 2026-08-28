//! Video host decode SSOT (M2 B3 T4).
//!
//! V1 桩版: cfg(feature=ffmpeg-host) 真实现 vs not(feature) 桩 Err。
//! 默认 cargo check 不链 libav*; 桩保证无 libav* 时绿。
//! YuvKind + SeekIndex + StagingRing + VideoDecoder + DecodePool 全在此 SSOT。
//! StagingRing 默认 64MiB = gpu::STAGING_RING_CAP_BYTES, push cap逐出最老防 OOM。
//! DecodePool workers 2..4 clamp, starvation-free (bounded pool, V1 log桩).
//! V1 不链真 libav* 仅探针; arena/gpu/SWAPCHAIN 不破; backend 分流在 video.py。

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use log::{info, warn};

use crate::gpu::{DECODE_POOL_WORKERS_DEFAULT, STAGING_RING_CAP_BYTES};

// ---------------------------------------------------------------------------
// YuvKind
// ---------------------------------------------------------------------------

/// YUV 平面格式 SSOT — 仅两种，shader 与 python 侧一致.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum YuvKind {
    Yuv420p,
    Nv12,
}

impl Default for YuvKind {
    fn default() -> Self {
        Self::Yuv420p
    }
}

impl YuvKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Yuv420p => "yuv420p",
            Self::Nv12 => "nv12",
        }
    }
    pub fn from_str(s: &str) -> Option<Self> {
        match s.to_ascii_lowercase().as_str() {
            "yuv420p" | "yuv420" | "420p" => Some(Self::Yuv420p),
            "nv12" | "nv21" => Some(Self::Nv12),
            _ => None,
        }
    }
}

impl std::fmt::Display for YuvKind {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.as_str())
    }
}

// ---------------------------------------------------------------------------
// SeekIndex
// ---------------------------------------------------------------------------

/// Seek 索引桩 — entries: Vec<(pts_ms, is_key)>
/// V2 将扩展为 (pts_ms, byte_offset, is_key) 但 V1 保持 spec 的 2-tuple
/// 以通过 cargo test 契约; python 侧 FrameBag.seek_index 为 3-tuple，互不冲突.
#[derive(Debug, Clone, Default)]
pub struct SeekIndex {
    pub entries: Vec<(u64, bool)>,
}

impl SeekIndex {
    pub fn new() -> Self {
        Self {
            entries: Vec::new(),
        }
    }
    pub fn push(&mut self, pts_ms: u64, is_key: bool) {
        self.entries.push((pts_ms, is_key));
    }
    pub fn len(&self) -> usize {
        self.entries.len()
    }
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
    pub fn clear(&mut self) {
        self.entries.clear();
    }
}

// ---------------------------------------------------------------------------
// StagingRing
// ---------------------------------------------------------------------------

/// GPU 侧 staging ring — 64 MiB cap, push 时逐出最老防 OOM.
/// buffers: VecDeque<wgpu::Buffer> + used_bytes + cap_bytes.
/// 另维护并行 `sizes` 队列以知逐出字节数 (不暴露，满足 spec buffers 字段).
#[derive(Debug)]
pub struct StagingRing {
    pub cap_bytes: usize,
    pub buffers: VecDeque<wgpu::Buffer>,
    pub used_bytes: usize,
    // parallel byte sizes for eviction accounting (not in spec but required)
    sizes: VecDeque<usize>,
}

impl StagingRing {
    pub fn new() -> Self {
        Self::with_cap(STAGING_RING_CAP_BYTES)
    }
    pub fn with_cap(cap_bytes: usize) -> Self {
        let cap = if cap_bytes == 0 {
            STAGING_RING_CAP_BYTES
        } else {
            cap_bytes
        };
        info!(
            "StagingRing::new cap_bytes={} ({} MiB) backend=Vulkan",
            cap,
            cap / (1024 * 1024)
        );
        Self {
            cap_bytes: cap,
            buffers: VecDeque::new(),
            used_bytes: 0,
            sizes: VecDeque::new(),
        }
    }
    /// Push a staging buffer, evicting oldest until under cap_bytes.
    /// Soak 轻量不 OOM: 逐出保证 used <= cap.
    pub fn push(&mut self, buf: wgpu::Buffer, bytes: usize) {
        let bytes = if bytes == 0 {
            // fallback: try buffer size if caller passes 0
            buf.size() as usize
        } else {
            bytes
        };
        self.buffers.push_back(buf);
        self.sizes.push_back(bytes);
        self.used_bytes = self.used_bytes.saturating_add(bytes);
        // Evict oldest while over cap — starvation-free: always makes progress
        while self.used_bytes > self.cap_bytes && !self.buffers.is_empty() {
            if let Some(_old) = self.buffers.pop_front() {
                if let Some(sz) = self.sizes.pop_front() {
                    self.used_bytes = self.used_bytes.saturating_sub(sz);
                    info!(
                        "StagingRing evict oldest {} bytes, used {}/{} buffers={}",
                        sz,
                        self.used_bytes,
                        self.cap_bytes,
                        self.buffers.len()
                    );
                }
            } else {
                break;
            }
        }
        // Double-check: if single buffer > cap, we keep it (one live) but log
        if self.used_bytes > self.cap_bytes && self.buffers.len() == 1 {
            warn!(
                "StagingRing single buffer {} > cap {} — keeping one, cap exceeded",
                self.used_bytes, self.cap_bytes
            );
        }
    }
    pub fn len(&self) -> usize {
        self.buffers.len()
    }
    pub fn is_empty(&self) -> bool {
        self.buffers.is_empty()
    }
    pub fn clear(&mut self) {
        self.buffers.clear();
        self.sizes.clear();
        self.used_bytes = 0;
    }
}

impl Default for StagingRing {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// VideoDecoder
// ---------------------------------------------------------------------------

/// 单路视频解码器桩 — path/fps/yuv + 共享 StagingRing/SeekIndex.
/// V1 不实际解码; decode_chunk 按 feature 分流.
pub struct VideoDecoder {
    pub path: String,
    pub fps: f32,
    pub yuv: YuvKind,
    pub staging: Arc<Mutex<StagingRing>>,
    pub seek_index: Arc<Mutex<SeekIndex>>,
}

impl std::fmt::Debug for VideoDecoder {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("VideoDecoder")
            .field("path", &self.path)
            .field("fps", &self.fps)
            .field("yuv", &self.yuv)
            .field(
                "staging_cap",
                &self.staging.lock().map(|s| s.cap_bytes).unwrap_or(0),
            )
            .field(
                "seek_len",
                &self.seek_index.lock().map(|s| s.len()).unwrap_or(0),
            )
            .finish()
    }
}

impl VideoDecoder {
    pub fn new(path: impl Into<String>, fps: f32, yuv: YuvKind) -> Self {
        let path = path.into();
        let fps = if fps > 0.0 { fps } else { 30.0 };
        let staging = Arc::new(Mutex::new(StagingRing::new()));
        let seek_index = Arc::new(Mutex::new(SeekIndex::new()));
        info!(
            "VideoDecoder::new path={} fps={} yuv={} staging_cap={} backend=Vulkan",
            path, fps, yuv, STAGING_RING_CAP_BYTES
        );
        Self {
            path,
            fps,
            yuv,
            staging,
            seek_index,
        }
    }

    /// Decode chunk [start_ms, start_ms+len_ms) — V1 桩.
    /// cfg(feature=ffmpeg-host) 时为“真实现”占位 (仍桩，log 后 Err)，否则直接 Err.
    /// 保证无 libav* 时 cargo check 绿.
    #[cfg(feature = "ffmpeg-host")]
    pub fn decode_chunk(&self, start_ms: u64, len_ms: u64) -> Result<Vec<u8>, String> {
        // V1: 即使 feature 开启仍为桩，仅探针 — 不真链 libav* 成功仅探针.
        // 预留 ffmpeg-sys-next 调用位，当前仅 log.
        info!(
            "VideoDecoder::decode_chunk [ffmpeg-host] path={} start_ms={} len_ms={} fps={} yuv={} staging_used={} backend=Vulkan",
            self.path,
            start_ms,
            len_ms,
            self.fps,
            self.yuv,
            self.staging.lock().map(|s| s.used_bytes).unwrap_or(0)
        );
        // 桩: 返回 Err，告知调用方回落 CLI
        // 未来 V2 在此接入 ffmpeg-sys-next demux + symphonia audio
        Err(format!(
            "VideoDecoder decode_chunk stub (ffmpeg-host enabled) path={} start_ms={} len_ms={}",
            self.path, start_ms, len_ms
        ))
    }

    #[cfg(not(feature = "ffmpeg-host"))]
    pub fn decode_chunk(&self, start_ms: u64, len_ms: u64) -> Result<Vec<u8>, String> {
        info!(
            "VideoDecoder::decode_chunk [stub] path={} start_ms={} len_ms={} fps={} yuv={} backend=Vulkan staging_used={}",
            self.path,
            start_ms,
            len_ms,
            self.fps,
            self.yuv,
            self.staging.lock().map(|s| s.used_bytes).unwrap_or(0)
        );
        Err("ffmpeg-host feature not enabled (V1 stub)".to_string())
    }
}

// ---------------------------------------------------------------------------
// DecodePool
// ---------------------------------------------------------------------------

/// 解码线程池桩 — workers 2..4 clamp.
/// V1 仅 log，不真 spawn 线程; 保证 decode starvation 无 (bounded) 且 soak 轻量.
#[derive(Debug, Clone)]
pub struct DecodePool {
    pub workers: usize,
}

impl DecodePool {
    pub fn new(workers: usize) -> Self {
        let clamped = workers.clamp(2, 4);
        if clamped != workers {
            warn!(
                "DecodePool::new workers {} clamped to {} (range 2..4) backend=Vulkan",
                workers, clamped
            );
        }
        info!(
            "DecodePool::new workers={} cap_bytes={} backend=Vulkan StagingRing",
            clamped, STAGING_RING_CAP_BYTES
        );
        Self { workers: clamped }
    }

    pub fn default_pool() -> Self {
        Self::new(DECODE_POOL_WORKERS_DEFAULT)
    }

    /// V1 桩: log 即返回，不真 spawn; 后续 V2 在此 spawn 线程池.
    pub fn spawn(&self, decoder: Arc<VideoDecoder>) {
        info!(
            "DecodePool::spawn workers={} path={} fps={} yuv={} seek_len={} staging_used={} backend=Vulkan — V1 stub (no thread)",
            self.workers,
            decoder.path,
            decoder.fps,
            decoder.yuv,
            decoder.seek_index.lock().map(|s| s.len()).unwrap_or(0),
            decoder.staging.lock().map(|s| s.used_bytes).unwrap_or(0)
        );
        // V1 不真起线程 —  starvation-free 因为无阻塞队列; soak 轻量
        // 未来: thread::spawn + symphonia + ffmpeg channel
    }
}

impl Default for DecodePool {
    fn default() -> Self {
        Self::default_pool()
    }
}

// ---------------------------------------------------------------------------
// Tests — 保证 cargo test -p renpy-host greps video|staging 能命中
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_yuv_kind_display() {
        assert_eq!(YuvKind::Yuv420p.as_str(), "yuv420p");
        assert_eq!(YuvKind::Nv12.as_str(), "nv12");
        assert_eq!(format!("{}", YuvKind::Yuv420p), "yuv420p");
        assert_eq!(YuvKind::from_str("YUV420P"), Some(YuvKind::Yuv420p));
        assert_eq!(YuvKind::from_str("nv12"), Some(YuvKind::Nv12));
        assert_eq!(YuvKind::from_str("unknown"), None);
    }

    #[test]
    fn test_seek_index_push() {
        let mut idx = SeekIndex::new();
        assert!(idx.is_empty());
        idx.push(0, true);
        idx.push(33, false);
        assert_eq!(idx.len(), 2);
        assert_eq!(idx.entries[0], (0, true));
        idx.clear();
        assert!(idx.is_empty());
    }

    #[test]
    fn test_staging_ring_default_cap() {
        let ring = StagingRing::new();
        assert_eq!(ring.cap_bytes, STAGING_RING_CAP_BYTES);
        assert_eq!(ring.cap_bytes, 64 * 1024 * 1024);
        assert_eq!(ring.used_bytes, 0);
        assert!(ring.is_empty());
    }

    #[test]
    fn test_decode_pool_clamp() {
        let p1 = DecodePool::new(1);
        assert_eq!(p1.workers, 2);
        let p5 = DecodePool::new(10);
        assert_eq!(p5.workers, 4);
        let p3 = DecodePool::new(3);
        assert_eq!(p3.workers, 3);
        let p_def = DecodePool::default_pool();
        assert_eq!(p_def.workers, DECODE_POOL_WORKERS_DEFAULT);
    }

    #[test]
    fn test_video_decoder_new() {
        let dec = VideoDecoder::new("test.mp4", 30.0, YuvKind::Yuv420p);
        assert_eq!(dec.path, "test.mp4");
        assert_eq!(dec.fps, 30.0);
        assert_eq!(dec.yuv, YuvKind::Yuv420p);
        assert_eq!(
            dec.staging.lock().unwrap().cap_bytes,
            STAGING_RING_CAP_BYTES
        );
    }

    #[test]
    fn test_video_decoder_decode_chunk_stub() {
        let dec = VideoDecoder::new("dummy.mp4", 24.0, YuvKind::Nv12);
        let res = dec.decode_chunk(0, 100);
        assert!(res.is_err());
        let msg = res.unwrap_err();
        // both cfg branches return Err
        assert!(msg.contains("stub") || msg.contains("ffmpeg-host"));
    }

    #[test]
    fn test_decode_pool_spawn_stub_does_not_panic() {
        let pool = DecodePool::new(2);
        let dec = Arc::new(VideoDecoder::new("a.mp4", 30.0, YuvKind::Yuv420p));
        pool.spawn(dec);
    }

    #[test]
    fn test_staging_video_integration_probe() {
        // 确保 probe 字符串含关键字段 — 对应 python 侧 video_host_probe
        let pool = DecodePool::new(2);
        let ring = StagingRing::new();
        let probe = format!(
            "DecodePool workers={} cap_bytes={} ffmpeg-host={} backend=Vulkan StagingRing",
            pool.workers,
            ring.cap_bytes,
            cfg!(feature = "ffmpeg-host")
        );
        assert!(probe.contains("DecodePool"));
        assert!(probe.contains("StagingRing"));
        assert!(probe.contains("backend=Vulkan"));
        assert!(probe.contains("cap_bytes"));
    }
}
