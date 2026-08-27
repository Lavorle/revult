//! GpuArena: textures, meshes, pipelines + high-level draw_model (Phase 2/5).

use std::collections::{HashMap, HashSet, VecDeque};
use std::ops::{Deref, DerefMut};
use std::sync::atomic::{AtomicU64, Ordering};

use log::{info, warn};
use wgpu::util::DeviceExt;

use crate::gpu::{GpuState, SWAPCHAIN_FORMAT};

pub const BG_CACHE_SOFT_CAP: usize = 4096;
pub const RING_INIT: usize = 256;
pub const MAX_RTTS_PER_SIZE: usize = 16;
pub const QUERY_RESOLVE_SIZE: usize = 16;

#[derive(Copy, Clone, Hash, PartialEq, Eq, Debug)]
#[allow(dead_code)]
pub struct TextureHandle(pub u64);
#[derive(Copy, Clone, Hash, PartialEq, Eq, Debug)]
#[allow(dead_code)]
pub struct MeshHandle(pub u64);
#[derive(Copy, Clone, Hash, PartialEq, Eq, Debug)]
#[allow(dead_code)]
pub struct PipelineHandle(pub u64);

impl From<u64> for TextureHandle {
    fn from(v: u64) -> Self { Self(v) }
}
impl From<u64> for MeshHandle {
    fn from(v: u64) -> Self { Self(v) }
}
impl From<u64> for PipelineHandle {
    fn from(v: u64) -> Self { Self(v) }
}

static NEXT_HANDLE: AtomicU64 = AtomicU64::new(1);

fn next_handle() -> u64 {
    NEXT_HANDLE.fetch_add(1, Ordering::Relaxed)
}

#[inline]
fn needs_bgra_swizzle(format: wgpu::TextureFormat) -> bool {
    matches!(
        format,
        wgpu::TextureFormat::Bgra8Unorm | wgpu::TextureFormat::Bgra8UnormSrgb
    )
}

fn maybe_swizzle_rgba<'a>(
    src: &'a [u8],
    expected: usize,
    format: wgpu::TextureFormat,
) -> std::borrow::Cow<'a, [u8]> {
    if needs_bgra_swizzle(format) {
        let mut bgra = Vec::with_capacity(expected);
        for chunk in src[..expected].chunks_exact(4) {
            bgra.push(chunk[2]);
            bgra.push(chunk[1]);
            bgra.push(chunk[0]);
            bgra.push(chunk[3]);
        }
        std::borrow::Cow::Owned(bgra)
    } else {
        std::borrow::Cow::Borrowed(&src[..expected])
    }
}

pub struct TextureSlot {
    pub texture: wgpu::Texture,
    pub view: wgpu::TextureView,
    pub width: u32,
    pub height: u32,
    /// True when texture may be used as a color render target (RTT).
    pub renderable: bool,
}

pub struct MeshSlot {
    pub vertex: wgpu::Buffer,
    pub index: Option<wgpu::Buffer>,
    pub vertex_count: u32,
    pub index_count: u32,
}

pub struct PipelineSlot {
    pub pipeline: wgpu::RenderPipeline,
    pub bind_group_layout: wgpu::BindGroupLayout,
    pub parts_key: String,
    /// 0 = solid, 1 = single texture, 2 = dual texture, 3 = triple (imagedissolve).
    pub tex_count: u8,
    pub has_uniforms: bool,
}

#[derive(Clone)]
pub struct DrawCmd {
    pub pipeline: u64,
    pub mesh: u64,
    pub texture: Option<u64>,
    pub texture1: Option<u64>,
    /// Third sample texture (ImageDissolve control/bottom/top uses tex0/1/2).
    pub texture2: Option<u64>,
    /// 16 f32 blob: blur_log2 / mask mult+offset / mat4 columns / imagedissolve.
    pub uniforms: [f32; 16],
}

/// Bind-group cache key for draws (pipeline + textures [+ uniform ring slot]).
/// Continuous UI is dominated by solid/textured cmds; create_bind_group per
/// DrawCmd was the main host encode CPU cost on dense prefs (~120 presents/s).
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct BgCacheKey {
    pipeline: u64,
    texture: u64,
    texture1: u64,
    texture2: u64,
    /// Uniform ring slot index; u32::MAX means no uniforms / unused slot.
    ubuf_slot: u32,
}
const UNIFORM_BYTES: u64 = 64; // 16 f32
const UNIFORM_RING_INITIAL: usize = RING_INIT;

/// Generic LRU slot map: HashMap + insertion-order queue + capacity.
/// Used for sample textures and meshes to deduplicate FIFO logic.
pub struct LruSlotMap<T> {
    map: HashMap<u64, T>,
    order: VecDeque<u64>,
    capacity: usize,
}

impl<T> LruSlotMap<T> {
    pub fn new(capacity: usize) -> Self {
        Self {
            map: HashMap::new(),
            order: VecDeque::new(),
            capacity,
        }
    }
    pub fn capacity(&self) -> usize {
        self.capacity
    }
    /// True if `id` is present.
    pub fn alive(&self, id: u64) -> bool {
        self.map.contains_key(&id)
    }
    pub fn contains_key(&self, k: &u64) -> bool {
        self.map.contains_key(k)
    }
    pub fn get(&self, k: &u64) -> Option<&T> {
        self.map.get(k)
    }
    pub fn len(&self) -> usize {
        self.map.len()
    }
    pub fn order_len(&self) -> usize {
        self.order.len()
    }
    pub fn order(&self) -> &VecDeque<u64> {
        &self.order
    }
    pub fn order_mut(&mut self) -> &mut VecDeque<u64> {
        &mut self.order
    }
    /// Mark `id` as most-recently-used (move to back). No-op if missing.
    pub fn touch(&mut self, id: u64) {
        if id == 0 || !self.map.contains_key(&id) {
            return;
        }
        if self.order.back().copied() == Some(id) {
            return;
        }
        if let Some(pos) = self.order.iter().position(|&x| x == id) {
            self.order.remove(pos);
        }
        self.order.push_back(id);
    }
    /// Insert and track order. If key existed, old order entry is replaced.
    pub fn insert(&mut self, id: u64, value: T) {
        if self.map.contains_key(&id) {
            if let Some(pos) = self.order.iter().position(|&x| x == id) {
                self.order.remove(pos);
            }
        }
        self.map.insert(id, value);
        self.order.push_back(id);
    }
    /// Remove and drop from order.
    pub fn remove(&mut self, k: &u64) -> Option<T> {
        if let Some(pos) = self.order.iter().position(|&x| x == *k) {
            self.order.remove(pos);
        }
        self.map.remove(k)
    }
}

impl<T> Deref for LruSlotMap<T> {
    type Target = HashMap<u64, T>;
    fn deref(&self) -> &Self::Target {
        &self.map
    }
}
impl<T> DerefMut for LruSlotMap<T> {
    fn deref_mut(&mut self) -> &mut Self::Target {
        &mut self.map
    }
}

pub struct GpuArena {
    pub textures: LruSlotMap<TextureSlot>,
    pub meshes: LruSlotMap<MeshSlot>,
    pub pipelines: HashMap<u64, PipelineSlot>,
    pub pipeline_by_key: HashMap<String, u64>,
    pub sampler: Option<wgpu::Sampler>,
    pub nearest_sampler: Option<wgpu::Sampler>,
    pub game_rt: Option<wgpu::Texture>,
    pub game_rt_view: Option<wgpu::TextureView>,
    pub game_rt_size: (u32, u32),
    pub frame_cmds: Vec<DrawCmd>,
    pub in_frame: bool,
    /// Nested begin_frame stack so mid-draw RTT bake can push/pop parent cmds.
    /// Outer product draw_screen remains intact while children bake into RTT.
    pub frame_cmd_stack: Vec<Vec<DrawCmd>>,
    /// When set, end_frame_present draws only into that render texture handle.
    pub active_target: Option<u64>,
    /// Nested begin_target stack. Without this, an inner RTT bake that calls
    /// `end_target()` clears the outer product/parent target to None and the
    /// next `draw_model` either encodes to the wrong surface or, after a bad
    /// recovery, lands "outside begin_frame" (arena-clear product RT).
    /// Flowchart custom mesh + confirm overlay is the product residual path.
    active_target_stack: Vec<Option<u64>>,
    pub solid_pipeline: Option<u64>,
    pub textured_pipeline: Option<u64>,
    pub dissolve_pipeline: Option<u64>,
    pub imagedissolve_pipeline: Option<u64>,
    pub blur_pipeline: Option<u64>,
    pub matrixcolor_pipeline: Option<u64>,
    pub alpha_mask_pipeline: Option<u64>,
    pub mask_pipeline: Option<u64>,
    /// Phase 7 Live2D parts (synthetic Cubism-like mesh path).
    pub live2d_mask_pipeline: Option<u64>,
    pub live2d_inverted_mask_pipeline: Option<u64>,
    pub live2d_colors_pipeline: Option<u64>,
    pub live2d_flip_pipeline: Option<u64>,
    pub clear_color: wgpu::Color,
    /// Mesh ids that would have been destroyed while still referenced by the
    /// open product frame. Flushed after end_frame_present drains frame_cmds.
    mesh_deferred_destroy: Vec<u64>,
    /// Sample texture ids deferred while still referenced by open frame_cmds
    /// (or last-presented product cmds). Flushed after present drains pins.
    texture_deferred_destroy: Vec<u64>,
    /// Sample texture ids used by the last successfully encoded product frame.
    /// Pinned across prepare of the *next* frame so dense prefs walks
    /// (dialog_config ~700 HT) cannot FIFO-kill chrome between presents.
    last_frame_sample_textures: Vec<u64>,
    /// Mesh ids used by the last successfully encoded product frame.
    /// Same cross-frame pin as last_frame_sample_textures: without this,
    /// post-present FIFO thrash can kill chrome meshes still held in Python
    /// `_mesh_cache` before mesh_alive recreates them (text_config residual).
    last_frame_meshes: Vec<u64>,
    /// Draw-cmd count of the last successfully encoded product frame.
    /// Used to skip sparse thrash presents that would Clear→partial chrome
    /// (text_config residual ~1 flaky frame under dense hover).
    last_frame_cmd_count: usize,
    /// Full last product DrawCmd list for Movie-only re-present (WP3 residual).
    last_product_cmds: Vec<DrawCmd>,
    /// Sample textures created or touched since the last product present.
    /// Protects early prepare uploads (bg first in tree) from FIFO kill before
    /// draw_model queues them into frame_cmds. Cleared/replaced on present.
    epoch_sample_textures: HashSet<u64>,
    /// Meshes created or touched since the last product present (same role as
    /// epoch_sample_textures for geometry / mesh-cache thrash).
    epoch_meshes: HashSet<u64>,
    /// Free RTT handles keyed by (w, h).
    rtt_free: HashMap<(u32, u32), Vec<u64>>,
    /// Live RTT handles keyed by (w, h) (still may be referenced by Python).
    rtt_live: HashMap<(u32, u32), Vec<u64>>,
    /// Hard cap of RTTs per size. Further requests reuse the oldest live handle.
    max_rtts_per_size: usize,
    /// Color format for sample textures, RTTs, game RT, and pipelines.
    /// Synced from GpuState.surface_format on first pipeline ensure.
    color_format: wgpu::TextureFormat,
    /// True when game RT content is undefined (just created / resized).
    /// Product encode_pass Clears once, then Loads on subsequent presents so
    /// skipped draw cmds (missing mesh/tex) do not flash arena-clear holes.
    game_rt_needs_clear: bool,
    /// Bind group cache (pipeline + textures [+ ubuf slot]).
    /// Avoids create_bind_group per DrawCmd — dominant CPU cost on dense prefs.
    bg_cache: HashMap<BgCacheKey, wgpu::BindGroup>,
    /// Ring of reusable 64-byte uniform buffers (write_buffer, not create each draw).
    uniform_ring: Vec<wgpu::Buffer>,
    /// Next free slot in uniform_ring for the current encode_pass.
    uniform_ring_next: usize,
}

impl GpuArena {
    pub fn new() -> Self {
        Self {
            textures: LruSlotMap::new(8192),
            meshes: LruSlotMap::new(8192),
            pipelines: HashMap::new(),
            pipeline_by_key: HashMap::new(),
            sampler: None,
            nearest_sampler: None,
            game_rt: None,
            game_rt_view: None,
            game_rt_size: (0, 0),
            frame_cmds: Vec::new(),
            in_frame: false,
            frame_cmd_stack: Vec::new(),
            active_target: None,
            active_target_stack: Vec::new(),
            solid_pipeline: None,
            textured_pipeline: None,
            dissolve_pipeline: None,
            imagedissolve_pipeline: None,
            blur_pipeline: None,
            matrixcolor_pipeline: None,
            alpha_mask_pipeline: None,
            mask_pipeline: None,
            live2d_mask_pipeline: None,
            live2d_inverted_mask_pipeline: None,
            live2d_colors_pipeline: None,
            live2d_flip_pipeline: None,
            clear_color: wgpu::Color {
                r: 0.05,
                g: 0.05,
                b: 0.08,
                a: 1.0,
            },
            // Product splash/dissolve thrash can allocate thousands of meshes
            // and sample textures per second without Python-side reuse. Hard
            // caps keep VRAM bounded; least-recently-used sample handles are
            // destroyed first (touch_texture on reuse / draw). 8192 leaves
            // headroom for main_menu chrome + Movie + text atlas thrash without
            // silently killing dock HostTextures still held on the surftree
            // (encode_pass skips missing textures → permanent arena_rt_clear).
            mesh_deferred_destroy: Vec::new(),
            texture_deferred_destroy: Vec::new(),
            last_frame_sample_textures: Vec::new(),
            last_frame_meshes: Vec::new(),
            last_frame_cmd_count: 0,
            last_product_cmds: Vec::new(),
            epoch_sample_textures: HashSet::new(),
            epoch_meshes: HashSet::new(),
            rtt_free: HashMap::new(),
            rtt_live: HashMap::new(),
            // Full-screen RTT thrash (mesh bake / dissolve) without recycle
            // OOMs the process; reuse oldest once this many live per size.
            max_rtts_per_size: MAX_RTTS_PER_SIZE,
            color_format: SWAPCHAIN_FORMAT,
            game_rt_needs_clear: true,
            bg_cache: HashMap::new(),
            uniform_ring: Vec::new(),
            uniform_ring_next: 0,
        }
    }

    /// Sync sample/RTT/pipeline color format from the live surface config.
    /// Safe to call repeatedly; no-ops when already matching.
    pub fn set_color_format(&mut self, format: wgpu::TextureFormat) {
        if self.color_format != format {
            // Format change after pipelines exist would desync; only set early.
            if self.pipelines.is_empty() {
                self.color_format = format;
            }
        }
    }

    /// True when `id` is still referenced as a sample texture by the open
    /// product frame_cmds, any nested RTT bake stack, the last presented
    /// product frame, or the current prepare epoch (created/touched since
    /// last product present). Epoch pin stops dense prepare walks from
    /// FIFO-killing the background uploaded first in the tree.
    fn texture_pinned(&self, id: u64) -> bool {
        let uses =
            |c: &DrawCmd| c.texture == Some(id) || c.texture1 == Some(id) || c.texture2 == Some(id);
        if self.frame_cmds.iter().any(uses) {
            return true;
        }
        for cmds in &self.frame_cmd_stack {
            if cmds.iter().any(uses) {
                return true;
            }
        }
        if self.last_frame_sample_textures.iter().any(|&x| x == id) {
            return true;
        }
        if self.epoch_sample_textures.contains(&id) {
            return true;
        }
        false
    }

    /// Mark a sample texture as part of the current prepare/present epoch so
    /// FIFO eviction cannot destroy it before the next product present.
    fn epoch_pin_texture(&mut self, id: u64) {
        if id == 0 {
            return;
        }
        self.epoch_sample_textures.insert(id);
    }

    fn evict_sample_textures_if_needed(&mut self) {
        // Cap is a thrash guard, not a hard VRAM budget. Prefer dropping the
        // least-recently-used sample texture (front of order). RTTs that
        // slip into the order list are skipped (not destroyed) so we only free
        // sample uploads. Callers that reuse a handle MUST touch_texture so
        // dock chrome is not treated as "oldest" while still live on the surftree.
        // Never destroy sample textures still referenced by open frame_cmds —
        // encode_pass skips missing textures after Clear → prefs chrome holes.
        // Generic LRU: capacity via textures.capacity(), order via textures.order().
        let mut guard = 0usize;
        while self.textures.order_len() > self.textures.capacity() {
            guard = guard.saturating_add(1);
            if guard > self.textures.order_len().saturating_add(8) {
                // All remaining entries are renderable / pinned / missing — stop.
                break;
            }
            let Some(old) = self.textures.order().front().copied() else {
                break;
            };
            // Only destroy non-renderable sample textures from the order list.
            if let Some(slot) = self.textures.get(&old) {
                if slot.renderable {
                    // RTT slipped into order list — skip destroy, keep going.
                    self.textures.order_mut().pop_front();
                    continue;
                }
            }
            if self.texture_pinned(old) {
                // Rotate pinned sample to end; try other eviction candidates.
                self.textures.order_mut().pop_front();
                self.textures.order_mut().push_back(old);
                continue;
            }
            self.textures.order_mut().pop_front();
            self.textures.remove(&old);
        }
    }

    /// True if `id` is still a live arena texture (sample or RTT).
    /// Used by Python load_texture / draw recovery when FIFO eviction may have
    /// destroyed a handle still referenced by HostTexture / texture_cache.
    pub fn texture_alive(&self, id: u64) -> bool {
        self.textures.alive(id)
    }

    /// Sample (non-renderable) texture count currently in the arena map.
    pub fn sample_texture_count(&self) -> u32 {
        self.textures.values().filter(|s| !s.renderable).count() as u32
    }

    /// Total texture map size (sample + RTT).
    pub fn texture_map_len(&self) -> u32 {
        self.textures.len() as u32
    }
    /// Length of the sample-texture LRU order list (eviction basis).
    pub fn texture_order_len(&self) -> u32 {
        self.textures.order_len() as u32
    }

    /// Mark a sample texture as most-recently-used so thrash eviction prefers
    /// truly idle uploads over dock chrome / logo still referenced every frame.
    /// No-op for unknown / RTT-only handles (still safe to call).
    pub fn touch_texture(&mut self, id: u64) {
        if id == 0 || !self.textures.contains_key(&id) {
            return;
        }
        // Prepare-epoch pin: dense prefs walks touch hundreds of chrome
        // HostTextures before draw_model; keep them out of FIFO destroy.
        if let Some(slot) = self.textures.get(&id) {
            if !slot.renderable {
                self.epoch_pin_texture(id);
            }
        }
        // Only track non-renderable sample textures in the order list.
        if let Some(slot) = self.textures.get(&id) {
            if slot.renderable {
                return;
            }
        }
        self.textures.touch(id);
    }
    /// True if `id` is still a live arena mesh.
    /// Used by Python mesh-cache recovery when host FIFO eviction may have
    /// destroyed a handle still referenced by `_mesh_cache` / frame_cmds.
    pub fn mesh_alive(&self, id: u64) -> bool {
        self.meshes.alive(id)
    }

    /// Live mesh map size.
    pub fn mesh_map_len(&self) -> u32 {
        self.meshes.len() as u32
    }

    /// Length of the mesh LRU order list (eviction basis).
    pub fn mesh_order_len(&self) -> u32 {
        self.meshes.order_len() as u32
    }

    /// Mark a mesh as most-recently-used so thrash eviction prefers truly idle
    /// geometry over chrome quads still referenced by the open product frame.
    /// Mirrors `touch_texture` for sample textures.
    pub fn touch_mesh(&mut self, id: u64) {
        if id == 0 || !self.meshes.contains_key(&id) {
            return;
        }
        self.epoch_pin_mesh(id);
        self.meshes.touch(id);
    }

    fn epoch_pin_mesh(&mut self, id: u64) {
        if id == 0 {
            return;
        }
        self.epoch_meshes.insert(id);
    }

    /// True when `id` is still referenced by the open product frame_cmds, any
    /// nested RTT bake stack, or the current prepare epoch. Those ids must not
    /// be FIFO-destroyed until after encode_pass drains them — otherwise encode
    /// silently skips chrome draws and presents arena clear holes.
    fn mesh_pinned(&self, id: u64) -> bool {
        if self.frame_cmds.iter().any(|c| c.mesh == id) {
            return true;
        }
        for cmds in &self.frame_cmd_stack {
            if cmds.iter().any(|c| c.mesh == id) {
                return true;
            }
        }
        if self.epoch_meshes.contains(&id) {
            return true;
        }
        if self.last_frame_meshes.iter().any(|&x| x == id) {
            return true;
        }
        false
    }

    fn evict_meshes_if_needed(&mut self) {
        // Cap is a thrash guard. Never destroy meshes still referenced by the
        // open frame (frame_cmds / nested stacks): encode_pass skips missing
        // mesh ids after LoadOp::Clear → partial prefs chrome holes on hover.
        // Budget is live MeshSlot count (includes deferred-destroy slots that
        // left order but still occupy VRAM), not only order.len().
        let mut guard = 0usize;
        while self.meshes.len() > self.meshes.capacity() {
            guard = guard.saturating_add(1);
            let bound = self
                .meshes
                .order_len()
                .saturating_add(self.mesh_deferred_destroy.len())
                .saturating_add(8);
            if guard > bound {
                // Remaining entries are all pinned / missing — stop. Temporary
                // over-cap while the product frame is open is preferred to
                // killing chrome still in frame_cmds.
                break;
            }
            if self.meshes.order_len() == 0 {
                // Deferred-only over-cap: try flush (no-op if still pinned).
                self.flush_deferred_meshes();
                if self.meshes.len() <= self.meshes.capacity() || self.meshes.order_len() == 0 {
                    break;
                }
                continue;
            }
            let Some(old) = self.meshes.order().front().copied() else {
                break;
            };
            if self.mesh_pinned(old) {
                // Rotate pinned mesh to end; try other eviction candidates.
                self.meshes.order_mut().pop_front();
                self.meshes.order_mut().push_back(old);
                continue;
            }
            self.meshes.order_mut().pop_front();
            self.meshes.remove(&old);
            self.mesh_deferred_destroy.retain(|&x| x != old);
        }
    }

    fn ensure_sampler(&mut self, device: &wgpu::Device) {
        if self.sampler.is_none() {
            self.sampler = Some(device.create_sampler(&wgpu::SamplerDescriptor {
                label: Some("renpy-linear"),
                address_mode_u: wgpu::AddressMode::ClampToEdge,
                address_mode_v: wgpu::AddressMode::ClampToEdge,
                address_mode_w: wgpu::AddressMode::ClampToEdge,
                mag_filter: wgpu::FilterMode::Linear,
                min_filter: wgpu::FilterMode::Linear,
                mipmap_filter: wgpu::FilterMode::Linear,
                ..Default::default()
            }));
        }
        if self.nearest_sampler.is_none() {
            self.nearest_sampler = Some(device.create_sampler(&wgpu::SamplerDescriptor {
                label: Some("renpy-nearest"),
                address_mode_u: wgpu::AddressMode::ClampToEdge,
                address_mode_v: wgpu::AddressMode::ClampToEdge,
                address_mode_w: wgpu::AddressMode::ClampToEdge,
                mag_filter: wgpu::FilterMode::Nearest,
                min_filter: wgpu::FilterMode::Nearest,
                mipmap_filter: wgpu::FilterMode::Nearest,
                ..Default::default()
            }));
        }
    }

    pub fn ensure_game_rt(&mut self, device: &wgpu::Device, w: u32, h: u32) {
        let w = w.max(1);
        let h = h.max(1);
        if self.game_rt_size == (w, h) && self.game_rt.is_some() {
            return;
        }
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("game-rt"),
            size: wgpu::Extent3d {
                width: w,
                height: h,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: self.color_format,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT
                | wgpu::TextureUsages::TEXTURE_BINDING
                | wgpu::TextureUsages::COPY_SRC,
            view_formats: &[],
        });
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        self.game_rt = Some(texture);
        self.game_rt_view = Some(view);
        self.game_rt_size = (w, h);
        self.game_rt_needs_clear = true;
    }

    pub fn create_texture_rgba(
        &mut self,
        device: &wgpu::Device,
        queue: &wgpu::Queue,
        width: u32,
        height: u32,
        rgba: &[u8],
    ) -> Result<u64, String> {
        let expected = (width as usize)
            .saturating_mul(height as usize)
            .saturating_mul(4);
        if rgba.len() < expected {
            return Err(format!(
                "texture rgba too short: {} < {}",
                rgba.len(),
                expected
            ));
        }
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("tex-rgba"),
            size: wgpu::Extent3d {
                width: width.max(1),
                height: height.max(1),
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: self.color_format,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        let upload = maybe_swizzle_rgba(rgba, expected, self.color_format);
        queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &upload,
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(4 * width.max(1)),
                rows_per_image: Some(height.max(1)),
            },
            wgpu::Extent3d {
                width: width.max(1),
                height: height.max(1),
                depth_or_array_layers: 1,
            },
        );
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        let id = next_handle();
        self.textures.insert(
            id,
            TextureSlot {
                texture,
                view,
                width,
                height,
                renderable: false,
            },
        );
        // New sample upload is part of the current prepare epoch — pin until
        // the next product present so dense walks cannot FIFO-kill bg first.
        self.epoch_pin_texture(id);
        self.evict_sample_textures_if_needed();
        Ok(id)
    }

    /// Update an existing texture's pixels in place (video frame upload path).
    /// Uses `queue.write_texture` — same format contract as `create_texture_rgba`.
    pub fn write_texture_rgba(
        &mut self,
        queue: &wgpu::Queue,
        id: u64,
        rgba: &[u8],
    ) -> Result<(), String> {
        let slot = self
            .textures
            .get(&id)
            .ok_or_else(|| format!("write_texture: unknown handle {id}"))?;
        let expected = (slot.width as usize)
            .saturating_mul(slot.height as usize)
            .saturating_mul(4);
        if rgba.len() < expected {
            return Err(format!(
                "write_texture rgba too short: {} < {} ({}x{})",
                rgba.len(),
                expected,
                slot.width,
                slot.height
            ));
        }
        let w = slot.width.max(1);
        let h = slot.height.max(1);
        let upload = maybe_swizzle_rgba(rgba, expected, self.color_format);
        queue.write_texture(
            wgpu::TexelCopyTextureInfo {
                texture: &slot.texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            &upload,
            wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(4 * w),
                rows_per_image: Some(h),
            },
            wgpu::Extent3d {
                width: w,
                height: h,
                depth_or_array_layers: 1,
            },
        );
        // Successful rewrite = still in active use (Movie / dirty image).
        // Bump LRU so thrash eviction does not treat this as oldest.
        self.touch_texture(id);
        Ok(())
    }

    /// Offscreen RTT (transitions). Handle is also a sampleable texture.
    ///
    /// Uses a size-keyed freelist and hard-caps live RTTs per size so product
    /// mesh-bake thrash cannot allocate unbounded 1080p targets.
    pub fn create_render_texture(
        &mut self,
        device: &wgpu::Device,
        width: u32,
        height: u32,
    ) -> Result<u64, String> {
        let mut w = width.max(1);
        let mut h = height.max(1);
        // Feel residual H3 RTT cap: reverse-inflated sizes allocate
        // multi-megapixel targets during prefs page switches. Cap strictly
        // to the live game RT / drawable, never inflate with 1920/1080 floors.
        let (cw, ch) = self.game_rt_size;
        if cw > 0 && ch > 0 {
            if w > cw {
                w = cw;
            }
            if h > ch {
                h = ch;
            }
        } else {
            w = w.min(1920);
            h = h.min(1080);
        }
        let key = (w, h);

        // 1) Freelist hit.
        if let Some(free) = self.rtt_free.get_mut(&key) {
            if let Some(id) = free.pop() {
                if self.textures.contains_key(&id) {
                    self.rtt_live.entry(key).or_default().push(id);
                    return Ok(id);
                }
                // Stale free id (destroyed elsewhere) — fall through.
            }
        }

        // 2) Hard cap: reuse oldest live of this size (overwrite content).
        let live = self.rtt_live.entry(key).or_default();
        // Drop dead handles from live list.
        live.retain(|id| self.textures.contains_key(id));
        if live.len() >= self.max_rtts_per_size {
            let id = live.remove(0);
            live.push(id);
            return Ok(id);
        }

        // 3) Allocate new.
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("rtt"),
            size: wgpu::Extent3d {
                width: w,
                height: h,
                depth_or_array_layers: 1,
            },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: self.color_format,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT
                | wgpu::TextureUsages::TEXTURE_BINDING
                | wgpu::TextureUsages::COPY_SRC
                | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        let id = next_handle();
        self.textures.insert(
            id,
            TextureSlot {
                texture,
                view,
                width: w,
                height: h,
                renderable: true,
            },
        );
        self.rtt_live.entry(key).or_default().push(id);
        // Log only when the pool for this size grows (not every thrash call).
        info!(
            "created render texture id={id} {w}x{h} (live={})",
            self.rtt_live.get(&key).map(|v| v.len()).unwrap_or(0)
        );
        Ok(id)
    }

    /// Return an RTT handle to the freelist (preferred over destroy for thrash).
    #[allow(dead_code)]
    pub fn release_render_texture(&mut self, id: u64) {
        let Some(slot) = self.textures.get(&id) else {
            return;
        };
        if !slot.renderable {
            return;
        }
        let key = (slot.width, slot.height);
        if let Some(live) = self.rtt_live.get_mut(&key) {
            live.retain(|&x| x != id);
        }
        let free = self.rtt_free.entry(key).or_default();
        if free.len() < self.max_rtts_per_size && !free.contains(&id) {
            free.push(id);
        } else {
            // Pool full — destroy.
            self._scrub_target_id(id);
            self.textures.remove(&id);
        }
    }

    pub fn destroy_texture(&mut self, id: u64) {
        self.invalidate_bg_cache_for_texture(id);
        self._scrub_target_id(id);
        // Prefer freelisting RTTs so create_render_texture can reuse VRAM.
        // Sample textures are truly destroyed — but not while still referenced
        // by open frame_cmds / last-presented product frame (prefs hover residual).
        if let Some(slot) = self.textures.get(&id) {
            if slot.renderable {
                let key = (slot.width, slot.height);
                if let Some(live) = self.rtt_live.get_mut(&key) {
                    live.retain(|&x| x != id);
                }
                let free = self.rtt_free.entry(key).or_default();
                if free.len() < self.max_rtts_per_size && !free.contains(&id) {
                    free.push(id);
                    return;
                }
                // Pool full — fall through to real destroy.
                if let Some(free) = self.rtt_free.get_mut(&key) {
                    free.retain(|&x| x != id);
                }
            } else {
                // Sample texture still needed by open / last product frame.
                if self.texture_pinned(id) {
                    if !self.texture_deferred_destroy.contains(&id) {
                        self.texture_deferred_destroy.push(id);
                    }
                    // Leave slot alive; drop from LRU so it is not re-selected.
                    self.textures.order_mut().retain(|&x| x != id);
                    return;
                }
            }
        }
        self.textures.remove(&id);
        self.texture_deferred_destroy.retain(|&x| x != id);
        self.last_frame_sample_textures.retain(|&x| x != id);
        self.epoch_sample_textures.retain(|&x| x != id);
    }

    /// Destroy sample textures queued while pinned by an open / last product frame.
    /// Call only after end_frame_present has drained frame_cmds and refreshed
    /// last_frame_sample_textures.
    pub fn flush_deferred_textures(&mut self) {
        if self.texture_deferred_destroy.is_empty() {
            return;
        }
        let pending: Vec<u64> = self.texture_deferred_destroy.drain(..).collect();
        for id in pending {
            if self.texture_pinned(id) {
                if !self.texture_deferred_destroy.contains(&id) {
                    self.texture_deferred_destroy.push(id);
                }
                continue;
            }
            // Only destroy sample (non-renderable) textures via this path.
            if let Some(slot) = self.textures.get(&id) {
                if slot.renderable {
                    continue;
                }
            }
            self.textures.remove(&id);
            self.last_frame_sample_textures.retain(|&x| x != id);
            self.epoch_sample_textures.retain(|&x| x != id);
        }
    }

    pub fn begin_target(&mut self, handle: u64) -> Result<(), String> {
        let tex = self
            .textures
            .get(&handle)
            .ok_or_else(|| format!("begin_target: unknown texture {handle}"))?;
        if !tex.renderable {
            return Err(format!(
                "begin_target: texture {handle} is not a render texture"
            ));
        }
        // Push parent target so nested RTT bake restores it on end_target.
        // Product residual: flowchart mesh bake nested under product present
        // used to clobber active_target; end_target then left None and the
        // next screen (confirm) drew outside a valid product frame.
        self.active_target_stack.push(self.active_target);
        self.active_target = Some(handle);
        Ok(())
    }

    pub fn end_target(&mut self) {
        // Pop parent target if nested; otherwise clear to None (top-level).
        self.active_target = self.active_target_stack.pop().unwrap_or(None);
    }

    /// Drop a destroyed texture from active_target and the nest stack.
    fn _scrub_target_id(&mut self, id: u64) {
        if self.active_target == Some(id) {
            self.active_target = None;
        }
        for slot in self.active_target_stack.iter_mut() {
            if *slot == Some(id) {
                *slot = None;
            }
        }
    }
    /// Blit the current game RT to the swapchain.
    /// Merges three previous `copy_texture_to_texture` sites (product, present_last, re-present).
    #[inline]
    fn blit_game_rt_to_swapchain(
        &self,
        encoder: &mut wgpu::CommandEncoder,
        gpu: &GpuState,
        frame: &wgpu::SurfaceTexture,
    ) {
        let Some(game_tex) = self.game_rt.as_ref() else {
            return;
        };
        let (rw, rh) = self.game_rt_size;
        if rw == 0 || rh == 0 {
            return;
        }
        encoder.copy_texture_to_texture(
            wgpu::TexelCopyTextureInfo {
                texture: game_tex,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            wgpu::TexelCopyTextureInfo {
                texture: &frame.texture,
                mip_level: 0,
                origin: wgpu::Origin3d::ZERO,
                aspect: wgpu::TextureAspect::All,
            },
            wgpu::Extent3d {
                width: rw.max(1).min(gpu.config.width.max(1)),
                height: rh.max(1).min(gpu.config.height.max(1)),
                depth_or_array_layers: 1,
            },
        );
    }


    /// Vertex layout: pos.xy, uv.xy, color.rgba  (8 f32 = 32 bytes)
    pub fn create_mesh(
        &mut self,
        device: &wgpu::Device,
        vertices: &[f32],
        indices: Option<&[u32]>,
    ) -> Result<u64, String> {
        if vertices.len() % 8 != 0 {
            return Err("vertices must be stride-8 f32 (pos2,uv2,color4)".into());
        }
        let vertex = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("mesh-vbo"),
            contents: cast_f32(vertices),
            usage: wgpu::BufferUsages::VERTEX,
        });
        let vertex_count = (vertices.len() / 8) as u32;
        let (index, index_count) = if let Some(idx) = indices {
            let buf = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
                label: Some("mesh-ibo"),
                contents: cast_u32(idx),
                usage: wgpu::BufferUsages::INDEX,
            });
            (Some(buf), idx.len() as u32)
        } else {
            (None, 0)
        };
        let id = next_handle();
        self.meshes.insert(
            id,
            MeshSlot {
                vertex,
                index,
                vertex_count,
                index_count,
            },
        );
        self.epoch_pin_mesh(id);
        self.evict_meshes_if_needed();
        Ok(id)
    }

    pub fn destroy_mesh(&mut self, id: u64) {
        // If the mesh is still referenced by the open product frame, defer
        // destruction until after encode (prefs hover / dense dialog_config).
        if self.mesh_pinned(id) {
            if !self.mesh_deferred_destroy.contains(&id) {
                self.mesh_deferred_destroy.push(id);
            }
            // Drop from LRU order so it is not re-selected as "live cache" growth,
            // but keep the MeshSlot until flush.
            self.meshes.order_mut().retain(|&x| x != id);
            return;
        }
        self.meshes.remove(&id);
        self.mesh_deferred_destroy.retain(|&x| x != id);
        self.epoch_meshes.retain(|&x| x != id);
        self.last_frame_meshes.retain(|&x| x != id);
    }

    /// Destroy meshes queued while pinned by an open product frame.
    /// Call only after end_frame_present has drained frame_cmds.
    pub fn flush_deferred_meshes(&mut self) {
        if self.mesh_deferred_destroy.is_empty() {
            return;
        }
        let pending: Vec<u64> = self.mesh_deferred_destroy.drain(..).collect();
        for id in pending {
            // Still pinned (nested / next frame already re-queued) → re-defer.
            if self.mesh_pinned(id) {
                if !self.mesh_deferred_destroy.contains(&id) {
                    self.mesh_deferred_destroy.push(id);
                }
                continue;
            }
            self.meshes.remove(&id);
            self.epoch_meshes.retain(|&x| x != id);
            self.last_frame_meshes.retain(|&x| x != id);
        }
    }

    pub fn ensure_builtin_pipelines(&mut self, device: &wgpu::Device) {
        self.ensure_sampler(device);
        if self.solid_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "solid", SOLID_WGSL, 0, false)
                .expect("solid pipeline");
            self.solid_pipeline = Some(id);
        }
        if self.textured_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "textured", TEXTURED_WGSL, 1, false)
                .expect("textured pipeline");
            self.textured_pipeline = Some(id);
        }
        if self.dissolve_pipeline.is_none() {
            // 2-tex mix + uniforms (amount in data0.x) — GL2 renpy.dissolve parity.
            let id = self
                .create_pipeline(device, "dissolve", DISSOLVE_WGSL, 2, true)
                .expect("dissolve pipeline");
            self.dissolve_pipeline = Some(id);
        }
        if self.imagedissolve_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "imagedissolve", IMAGEDISSOLVE_WGSL, 3, true)
                .expect("imagedissolve pipeline");
            self.imagedissolve_pipeline = Some(id);
        }
        if self.blur_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "blur", BLUR_WGSL, 1, true)
                .expect("blur pipeline");
            self.blur_pipeline = Some(id);
        }
        if self.matrixcolor_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "matrixcolor", MATRIXCOLOR_WGSL, 1, true)
                .expect("matrixcolor pipeline");
            self.matrixcolor_pipeline = Some(id);
        }
        if self.alpha_mask_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "alpha_mask", ALPHA_MASK_WGSL, 2, false)
                .expect("alpha_mask pipeline");
            self.alpha_mask_pipeline = Some(id);
        }
        if self.mask_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "mask", MASK_WGSL, 2, true)
                .expect("mask pipeline");
            self.mask_pipeline = Some(id);
        }
        // Phase 7 Live2D parts (pre-baked; real path still Mesh2+Render via Cubism Core).
        if self.live2d_mask_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "live2d_mask", LIVE2D_MASK_WGSL, 2, true)
                .expect("live2d.mask pipeline");
            self.live2d_mask_pipeline = Some(id);
        }
        if self.live2d_inverted_mask_pipeline.is_none() {
            let id = self
                .create_pipeline(
                    device,
                    "live2d_inverted_mask",
                    LIVE2D_INVERTED_MASK_WGSL,
                    2,
                    true,
                )
                .expect("live2d.inverted_mask pipeline");
            self.live2d_inverted_mask_pipeline = Some(id);
        }
        if self.live2d_colors_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "live2d_colors", LIVE2D_COLORS_WGSL, 1, true)
                .expect("live2d.colors pipeline");
            self.live2d_colors_pipeline = Some(id);
        }
        if self.live2d_flip_pipeline.is_none() {
            let id = self
                .create_pipeline(device, "live2d_flip", LIVE2D_FLIP_WGSL, 1, false)
                .expect("live2d.flip_texture pipeline");
            self.live2d_flip_pipeline = Some(id);
        }
    }

    /// Public thin wrap around [`Self::create_pipeline`] for composed WGSL from Python.
    /// Rejects `tex_count > 3` (solid/1/2/3-tex layouts only). Reuses pipeline_by_key cache.
    pub fn create_pipeline_from_wgsl(
        &mut self,
        device: &wgpu::Device,
        key: &str,
        wgsl: &str,
        tex_count: u8,
        has_uniforms: bool,
    ) -> Result<u64, String> {
        if tex_count > 3 {
            return Err(format!(
                "create_pipeline_from_wgsl: tex_count {tex_count} > 3 (max solid/1/2/3-tex)"
            ));
        }
        if let Some(id) = self.pipeline_by_key_lookup(key) {
            return Ok(id);
        }
        self.create_pipeline(device, key, wgsl, tex_count, has_uniforms)
    }

    /// Lookup a previously created pipeline by its cache key (e.g. "composed:abc123").
    pub fn pipeline_by_key_lookup(&self, key: &str) -> Option<u64> {
        self.pipeline_by_key.get(key).copied()
    }

    fn create_pipeline(
        &mut self,
        device: &wgpu::Device,
        key: &str,
        wgsl: &str,
        tex_count: u8,
        has_uniforms: bool,
    ) -> Result<u64, String> {
        if let Some(id) = self.pipeline_by_key.get(key) {
            return Ok(*id);
        }
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: Some(key),
            source: wgpu::ShaderSource::Wgsl(wgsl.into()),
        });

        let bind_group_layout = build_bind_group_layout(device, key, tex_count, has_uniforms);

        let pipeline_layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: Some(key),
            bind_group_layouts: &[&bind_group_layout],
            push_constant_ranges: &[],
        });

        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: Some(key),
            layout: Some(&pipeline_layout),
            vertex: wgpu::VertexState {
                module: &shader,
                entry_point: Some("vs_main"),
                buffers: &[wgpu::VertexBufferLayout {
                    array_stride: 32,
                    step_mode: wgpu::VertexStepMode::Vertex,
                    attributes: &[
                        wgpu::VertexAttribute {
                            offset: 0,
                            shader_location: 0,
                            format: wgpu::VertexFormat::Float32x2,
                        },
                        wgpu::VertexAttribute {
                            offset: 8,
                            shader_location: 1,
                            format: wgpu::VertexFormat::Float32x2,
                        },
                        wgpu::VertexAttribute {
                            offset: 16,
                            shader_location: 2,
                            format: wgpu::VertexFormat::Float32x4,
                        },
                    ],
                }],
                compilation_options: Default::default(),
            },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs_main"),
                targets: &[Some(wgpu::ColorTargetState {
                    format: self.color_format,
                    blend: Some(wgpu::BlendState {
                        color: wgpu::BlendComponent {
                            src_factor: wgpu::BlendFactor::One,
                            dst_factor: wgpu::BlendFactor::OneMinusSrcAlpha,
                            operation: wgpu::BlendOperation::Add,
                        },
                        alpha: wgpu::BlendComponent {
                            src_factor: wgpu::BlendFactor::One,
                            dst_factor: wgpu::BlendFactor::OneMinusSrcAlpha,
                            operation: wgpu::BlendOperation::Add,
                        },
                    }),
                    write_mask: wgpu::ColorWrites::ALL,
                })],
                compilation_options: Default::default(),
            }),
            primitive: wgpu::PrimitiveState {
                topology: wgpu::PrimitiveTopology::TriangleList,
                ..Default::default()
            },
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview: None,
            cache: None,
        });

        let id = next_handle();
        self.pipelines.insert(
            id,
            PipelineSlot {
                pipeline,
                bind_group_layout,
                parts_key: key.to_string(),
                tex_count,
                has_uniforms,
            },
        );
        self.pipeline_by_key.insert(key.to_string(), id);
        // Keep parts_key live (read, not just write) to avoid dead_code warning.
        let _ = self.pipelines.get(&id).map(|s| s.parts_key.len());
        info!("created pipeline {key} id={id} tex_count={tex_count} uniforms={has_uniforms}");
        Ok(id)
    }

    pub fn begin_frame(&mut self) {
        // Nested RTT bake: push current cmds so parent draw_screen is preserved.
        if self.in_frame {
            let parent = std::mem::take(&mut self.frame_cmds);
            self.frame_cmd_stack.push(parent);
        } else {
            self.frame_cmds.clear();
        }
        self.in_frame = true;
    }

    /// Nested begin_frame depth: 0 = idle, 1 = top-level in_frame, >1 = nested.
    /// Used by product draw_screen recovery so a stuck nest cannot discard cmds.
    pub fn frame_depth(&self) -> u32 {
        if !self.in_frame {
            0
        } else {
            (self.frame_cmd_stack.len() as u32).saturating_add(1)
        }
    }

    /// Drop stuck nested frames / active_target without encoding or presenting.
    /// Safe no-op when already clean. Product `_recover_frame_state` calls this
    /// so a mid-draw RTT exception cannot leave the next present nested.
    pub fn reset_frame_state(&mut self) {
        self.frame_cmds.clear();
        self.frame_cmd_stack.clear();
        self.in_frame = false;
        self.active_target = None;
        self.active_target_stack.clear();
        // Pins are gone with the cleared cmds; free anything deferred while
        // those cmds were open so recovery cannot leak MeshSlots forever.
        // Keep last_frame_sample_textures — product chrome may still be live on
        // the surftree across a recover between presents.
        self.flush_deferred_meshes();
        self.flush_deferred_textures();
    }

    pub fn draw_model(
        &mut self,
        pipeline: u64,
        mesh: u64,
        texture: Option<u64>,
        texture1: Option<u64>,
        texture2: Option<u64>,
        uniforms: Option<[f32; 16]>,
    ) {
        if !self.in_frame {
            warn!("draw_model outside begin_frame");
            return;
        }
        // Drawn this frame → still live. Bump LRU so thrash eviction does not
        // destroy dock chrome / logo while the product present path still holds
        // HostTexture ids to them (encode_pass silently skips missing textures).
        // Same for meshes: prefs hover thrash creates many unique quads; without
        // touch_mesh, FIFO would kill early chrome meshes already in frame_cmds.
        self.touch_mesh(mesh);
        if let Some(t) = texture {
            self.touch_texture(t);
        }
        if let Some(t) = texture1 {
            self.touch_texture(t);
        }
        if let Some(t) = texture2 {
            self.touch_texture(t);
        }
        self.frame_cmds.push(DrawCmd {
            pipeline,
            mesh,
            texture,
            texture1,
            texture2,
            uniforms: uniforms.unwrap_or([0.0; 16]),
        });
    }

    /// Draw into game RT (for readback) and present the same content to swapchain.
    /// When `active_target` is set, draws only into that RTT (no swapchain present).
    ///
    /// Nested begin_frame/end_frame_present (mesh bake / render_to_texture) pops
    /// the parent command list so the outer product draw_screen can continue.
    ///
    /// Returns `Ok(true)` when the swapchain was presented, `Ok(false)` for RTT-only
    /// or for a nested frame that restored the parent stack.
    ///
    /// Idle / double-end: if called while **not** in a frame and with no active
    /// target / nest, this is a no-op. Encoding an empty pass would wipe the last
    /// good game RT to arena clear — the confirm_alone_2 residual after flowchart
    /// thrash when a concurrent force-redraw and product interact interleave
    /// `end_frame_present` (class b / dead_present thrash companion).
    pub fn end_frame_present(&mut self, gpu: &mut GpuState) -> Result<bool, String> {
        // Idle end: never clear the product RT. Concurrent force-redraw paths
        // and prepare recovery can double-call end_frame_present after a good
        // present; wiping to (0.05,0.05,0.08) looks like permanent black chrome.
        if !self.in_frame
            && self.frame_cmd_stack.is_empty()
            && self.active_target.is_none()
            && self.active_target_stack.is_empty()
        {
            self.frame_cmds.clear();
            return Ok(false);
        }

        // Match pipelines/textures to the live surface format (Rgba preferred,
        // Bgra fallback on some Wayland/RADV surfaces).
        self.set_color_format(gpu.surface_format);
        self.ensure_builtin_pipelines(&gpu.device);

        let cmds: Vec<DrawCmd> = self.frame_cmds.drain(..).collect();
        let nested = !self.frame_cmd_stack.is_empty();

        if let Some(tid) = self.active_target {
            let view = self
                .textures
                .get(&tid)
                .ok_or_else(|| format!("active_target {tid} missing"))?
                .view
                .clone();
            self.encode_pass(gpu, &view, &cmds, false)?;
            if nested {
                // Restore parent frame cmds; keep in_frame so outer draw continues.
                self.frame_cmds = self.frame_cmd_stack.pop().unwrap_or_default();
                self.in_frame = true;
            } else {
                self.in_frame = false;
            }
            // Unpinned meshes/textures from this RTT bake can die; parent-pinned stay.
            self.flush_deferred_meshes();
            self.flush_deferred_textures();
            return Ok(false);
        }

        // Nested non-target present should not touch the swapchain/game RT —
        // only encode into whatever active path remains (treated as no-op cmds).
        if nested {
            // Defensive: without active_target a nested end is a programming error
            // for mesh bake (always RTT). Drop cmds and restore parent.
            self.frame_cmds = self.frame_cmd_stack.pop().unwrap_or_default();
            self.in_frame = true;
            self.flush_deferred_meshes();
            self.flush_deferred_textures();
            return Ok(false);
        }

        // Product present with zero draw cmds: keep the last good game RT.
        // encode_pass always clears first; an empty product present would wipe
        // confirm/preferences chrome to pure arena clear (confirm_alone_2 /
        // thrash residual) when force-redraw races an empty rebuild.
        // Still close the frame so in_frame cannot stick.
        if cmds.is_empty() {
            info!(
                "suppress product present: empty cmds (prev_cmds={})",
                self.last_frame_cmd_count
            );
            self.in_frame = false;
            self.flush_deferred_meshes();
            self.flush_deferred_textures();
            // Keep last good chrome on swapchain (do not freeze/hitch).
            return self.present_last_game_rt(gpu);
        }

        // Sparse product present guard: under dense hover thrash a force-redraw
        // can race with a half-built surftree and emit far fewer cmds than the
        // last good prefs frame. Encoding that with Clear flashes arena-clear
        // holes (text_config residual). Keep last good RT and close the frame.
        //
        // Feel residual H3: prefs page switches legitimately drop cmd count
        // (dialog_config_1 is much denser than sound_config). Suppressing those
        // presents made first_interactive wait 400ms+ for a later full frame.
        // Only suppress *tiny* rebuilds (absolute floor), not half-of-previous.
        let prev_n = self.last_frame_cmd_count;
        if prev_n >= 64 && cmds.len() < 8 {
            info!(
                "suppress product present: sparse cmds={} prev={}",
                cmds.len(),
                prev_n
            );
            self.in_frame = false;
            self.flush_deferred_meshes();
            self.flush_deferred_textures();
            return self.present_last_game_rt(gpu);
        }

        // Incomplete product present guard (prefs residual H-I4):
        // encode_pass_into with Load-preserve silently skips DrawCmds whose mesh
        // or sample textures are missing. That leaves last-frame pixels for some
        // layers while others redraw -> intermittent wrong/misaligned chrome
        // (SOUND CONFIG texture disorder). Empty/sparse guards only catch empty
        // or tiny cmd lists, not dense lists with dead handles. Suppress the
        // half-built present and keep last good game RT.
        {
            let mut missing = 0usize;
            for c in &cmds {
                if !self.meshes.contains_key(&c.mesh) {
                    missing = missing.saturating_add(1);
                    continue;
                }
                let tex_count = self
                    .pipelines
                    .get(&c.pipeline)
                    .map(|p| p.tex_count)
                    .unwrap_or(0);
                if tex_count >= 1 {
                    match c.texture {
                        Some(t) if self.textures.contains_key(&t) => {}
                        _ => missing = missing.saturating_add(1),
                    }
                }
                if tex_count >= 2 {
                    match c.texture1 {
                        Some(t) if self.textures.contains_key(&t) => {}
                        _ => missing = missing.saturating_add(1),
                    }
                }
                if tex_count >= 3 {
                    match c.texture2 {
                        Some(t) if self.textures.contains_key(&t) => {}
                        _ => missing = missing.saturating_add(1),
                    }
                }
            }
            if missing > 0 {
                info!(
                    "suppress product present: incomplete missing_slots={} cmds={} prev={}",
                    missing,
                    cmds.len(),
                    prev_n
                );
                self.in_frame = false;
                self.flush_deferred_meshes();
                self.flush_deferred_textures();
                // Blit last complete game RT — reject half-built cmd list without
                // dropping the swapchain present (p99 hitch residual on prefs_idle).
                return self.present_last_game_rt(gpu);
            }
        }

        self.in_frame = false;

        let w = gpu.config.width.max(1);
        let h = gpu.config.height.max(1);
        self.ensure_game_rt(&gpu.device, w, h);

        // Product present: single command buffer = encode game RT + blit to swapchain.
        // WP3 residual: merging two submits cuts host queue overhead on dense prefs.
        // Load-preserve on game RT is unchanged (thrash AC-R).
        let frame = gpu
            .surface
            .get_current_texture()
            .map_err(|e| format!("swapchain: {e}"))?;

        let preserve = !self.game_rt_needs_clear;
        if let Some(view) = self.game_rt_view.clone() {
            let mut encoder = gpu
                .device
                .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                    label: Some("product-frame"),
                });
            self.encode_pass_into(gpu, &mut encoder, &view, &cmds, preserve)?;
            self.game_rt_needs_clear = false;

            self.blit_game_rt_to_swapchain(&mut encoder, gpu, &frame);
            gpu.queue.submit(Some(encoder.finish()));
        } else {
            // Fallback: no game RT - encode directly to swapchain.
            let swap_view = frame
                .texture
                .create_view(&wgpu::TextureViewDescriptor::default());
            self.encode_pass(gpu, &swap_view, &cmds, false)?;
        }
        frame.present();
        // Remember sample textures used by this product frame so the next
        // prepare walk cannot FIFO-kill them before draw_model re-touches
        // (dense dialog_config after image_config thrash residual).
        let mut seen = HashSet::with_capacity(cmds.len().saturating_mul(3));
        let mut last = Vec::with_capacity(cmds.len().saturating_mul(2));
        for c in &cmds {
            if let Some(t) = c.texture {
                if seen.insert(t) {
                    last.push(t);
                }
            }
            if let Some(t) = c.texture1 {
                if seen.insert(t) {
                    last.push(t);
                }
            }
            if let Some(t) = c.texture2 {
                if seen.insert(t) {
                    last.push(t);
                }
            }
        }
        self.last_frame_sample_textures = last;
        // Also pin meshes used by this product present across the next prepare.
        let mut seen_meshes = HashSet::with_capacity(cmds.len());
        let mut last_meshes = Vec::with_capacity(cmds.len());
        for c in &cmds {
            if c.mesh != 0 && seen_meshes.insert(c.mesh) {
                last_meshes.push(c.mesh);
            }
        }
        self.last_frame_meshes = last_meshes;
        self.last_frame_cmd_count = cmds.len();
        self.last_product_cmds = cmds;
        // Epoch ends at product present: only last-frame pins remain until the
        // next prepare starts filling epoch_* again. This bounds pin lifetime
        // so long-lived thrash can still reclaim idle samples/meshes.
        self.epoch_sample_textures.clear();
        self.epoch_meshes.clear();
        // Meshes / textures deferred while pinned by this product frame are free
        // now unless re-pinned by last_frame_* sets.
        self.flush_deferred_meshes();
        self.flush_deferred_textures();
        Ok(true)
    }

    /// Blit the last good game RT to the swapchain without re-encoding cmds.
    ///
    /// Used by empty/sparse/incomplete product-present suppress paths: the game RT
    /// already holds the last complete frame, but returning Ok(false) without a
    /// swapchain present freezes the display and inflates inter-present gaps
    /// (prefs_idle p99 hitch residual). Presenting the preserved RT keeps cadence
    /// and zero-glitch chrome while rejecting half-built DrawCmd lists.
    fn present_last_game_rt(&mut self, gpu: &mut GpuState) -> Result<bool, String> {
        if self.game_rt.is_none() || self.game_rt_needs_clear {
            return Ok(false);
        }
        let w = gpu.config.width.max(1);
        let h = gpu.config.height.max(1);
        self.ensure_game_rt(&gpu.device, w, h);
        let frame = gpu
            .surface
            .get_current_texture()
            .map_err(|e| format!("swapchain: {e}"))?;
        let mut encoder = gpu
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("present-last-game-rt"),
            });
        self.blit_game_rt_to_swapchain(&mut encoder, gpu, &frame);
        gpu.queue.submit(Some(encoder.finish()));
        frame.present();
        Ok(true)
    }

    /// Re-encode the last successful product DrawCmd list to the swapchain.
    ///
    /// Used by the host Movie-only / cadence fast path: when Python can keep the
    /// last frame's sample textures alive (Movie rewrite in place) and UI chrome
    /// has not changed, skip full render_screen + prepare + walk and only re-present.
    ///
    /// Returns Ok(true) on swapchain present, Ok(false) when no last cmds / busy.
    pub fn re_present_last_product(&mut self, gpu: &mut GpuState) -> Result<bool, String> {
        // Never interleave with an open product/RTT frame.
        if self.in_frame
            || !self.frame_cmd_stack.is_empty()
            || self.active_target.is_some()
            || !self.active_target_stack.is_empty()
        {
            return Ok(false);
        }
        if self.last_product_cmds.is_empty() {
            return Ok(false);
        }

        self.set_color_format(gpu.surface_format);
        self.ensure_builtin_pipelines(&gpu.device);

        let cmds = self.last_product_cmds.clone();

        let w = gpu.config.width.max(1);
        let h = gpu.config.height.max(1);
        self.ensure_game_rt(&gpu.device, w, h);

        let frame = gpu
            .surface
            .get_current_texture()
            .map_err(|e| format!("swapchain: {e}"))?;

        // Always Load-preserve on re-present: we re-draw the same chrome + movie.
        let preserve = !self.game_rt_needs_clear;
        if let Some(view) = self.game_rt_view.clone() {
            let mut encoder = gpu
                .device
                .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                    label: Some("re-present-product"),
                });
            self.encode_pass_into(gpu, &mut encoder, &view, &cmds, preserve)?;
            self.game_rt_needs_clear = false;

            self.blit_game_rt_to_swapchain(&mut encoder, gpu, &frame);
            gpu.queue.submit(Some(encoder.finish()));
        } else {
            let swap_view = frame
                .texture
                .create_view(&wgpu::TextureViewDescriptor::default());
            self.encode_pass(gpu, &swap_view, &cmds, false)?;
        }
        frame.present();
        // Pins already match last_product_cmds; refresh epoch clear only.
        self.epoch_sample_textures.clear();
        self.epoch_meshes.clear();
        self.flush_deferred_meshes();
        self.flush_deferred_textures();
        Ok(true)
    }

    /// True when a product DrawCmd list is available for re-present.
    pub fn has_last_product_cmds(&self) -> bool {
        !self.last_product_cmds.is_empty()
    }

    /// Read pre-present game RT as tightly packed RGBA8 bytes.
    pub fn read_game_rt_rgba(&self, gpu: &GpuState) -> Result<(u32, u32, Vec<u8>), String> {
        let texture = self
            .game_rt
            .as_ref()
            .ok_or_else(|| "game RT not created; call end_frame_present first".to_string())?;
        let (w, h) = self.game_rt_size;
        let (rw, rh, mut bytes) = read_texture_rgba(gpu, texture, w, h)?;
        // BGRA surface fallback: convert to RGBA for Python sample gates.
        if matches!(
            self.color_format,
            wgpu::TextureFormat::Bgra8Unorm | wgpu::TextureFormat::Bgra8UnormSrgb
        ) {
            for chunk in bytes.chunks_exact_mut(4) {
                chunk.swap(0, 2);
            }
        }
        Ok((rw, rh, bytes))
    }

    /// Read an arena texture (including RTT) as tightly packed RGBA8.
    pub fn read_texture_rgba(
        &self,
        gpu: &GpuState,
        handle: u64,
    ) -> Result<(u32, u32, Vec<u8>), String> {
        let slot = self
            .textures
            .get(&handle)
            .ok_or_else(|| format!("read_texture_rgba: unknown {handle}"))?;
        if !slot.renderable {
            // Only guarantee COPY_SRC on render textures + game RT; sample textures
            // may lack COPY_SRC. Allow if created as RTT.
            return Err(format!(
                "read_texture_rgba: texture {handle} has no COPY_SRC (use RTT or game RT)"
            ));
        }
        let (rw, rh, mut bytes) = read_texture_rgba(gpu, &slot.texture, slot.width, slot.height)?;
        if matches!(
            self.color_format,
            wgpu::TextureFormat::Bgra8Unorm | wgpu::TextureFormat::Bgra8UnormSrgb
        ) {
            for chunk in bytes.chunks_exact_mut(4) {
                chunk.swap(0, 2);
            }
        }
        Ok((rw, rh, bytes))
    }

    /// Drop cached bind groups that reference a destroyed sample/RTT texture.
    fn invalidate_bg_cache_for_texture(&mut self, id: u64) {
        if self.bg_cache.is_empty() {
            return;
        }
        self.bg_cache
            .retain(|k, _| k.texture != id && k.texture1 != id && k.texture2 != id);
    }

    fn ensure_uniform_ring(&mut self, device: &wgpu::Device, need: usize) {
        while self.uniform_ring.len() < need {
            let buf = device.create_buffer(&wgpu::BufferDescriptor {
                label: Some("draw-uniforms-ring"),
                size: UNIFORM_BYTES,
                usage: wgpu::BufferUsages::UNIFORM | wgpu::BufferUsages::COPY_DST,
                mapped_at_creation: false,
            });
            self.uniform_ring.push(buf);
        }
    }

    fn encode_pass(
        &mut self,
        gpu: &GpuState,
        view: &wgpu::TextureView,
        cmds: &[DrawCmd],
        preserve_previous: bool,
    ) -> Result<(), String> {
        let mut encoder = gpu
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("frame-pass"),
            });
        self.encode_pass_into(gpu, &mut encoder, view, cmds, preserve_previous)?;
        gpu.queue.submit(Some(encoder.finish()));
        Ok(())
    }

    /// Encode draw cmds into encoder without submitting.
    /// Uses bind-group cache + uniform buffer ring to avoid per-draw GPU object creation.
    fn encode_pass_into(
        &mut self,
        gpu: &GpuState,
        encoder: &mut wgpu::CommandEncoder,
        view: &wgpu::TextureView,
        cmds: &[DrawCmd],
        preserve_previous: bool,
    ) -> Result<(), String> {
        // Pass-local load op by target (A1 + prefs flicker residual):
        // - active_target Some (RTT) -> transparent Clear so RTT can composite.
        // - product game RT after first contentful present -> Load so thrash
        //   frames that skip missing mesh/texture cmds keep last good chrome
        //   instead of flashing arena clear (text_config hover residual).
        // - first product present / after resize -> Clear to clear_color.
        // - swapchain: always Clear then draw (swapchain has no retained RT).
        // Do NOT mutate self.clear_color per frame.
        //
        // Caller selects load via preserve_previous for product game RT after
        // the first successful present (game_rt_needs_clear == false).
        let load = if self.active_target.is_some() {
            wgpu::LoadOp::Clear(wgpu::Color {
                r: 0.0,
                g: 0.0,
                b: 0.0,
                a: 0.0,
            })
        } else if preserve_previous {
            wgpu::LoadOp::Load
        } else {
            wgpu::LoadOp::Clear(self.clear_color)
        };

        // Pre-count uniform slots so the ring is large enough before the pass.
        let uniform_need = cmds
            .iter()
            .filter(|c| {
                self.pipelines
                    .get(&c.pipeline)
                    .map(|p| p.has_uniforms)
                    .unwrap_or(false)
            })
            .count();
        self.ensure_uniform_ring(&gpu.device, uniform_need.max(UNIFORM_RING_INITIAL));
        self.uniform_ring_next = 0;

        // Cap cache growth: dense thrash can create many unique (pipe,tex) pairs.
        // Keep a soft ceiling; overflow clears (safe: rebuild next draws).
        if self.bg_cache.len() > BG_CACHE_SOFT_CAP {
            self.bg_cache.clear();
        }

        let timestamp_writes = gpu
            .query_set
            .as_ref()
            .map(|qs| wgpu::RenderPassTimestampWrites {
                query_set: qs,
                beginning_of_pass_write_index: Some(0),
                end_of_pass_write_index: Some(1),
            });
        {
            let mut pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("draw"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load,
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes,
                occlusion_query_set: None,
            });

            for cmd in cmds {
                let Some(pipe) = self.pipelines.get(&cmd.pipeline) else {
                    continue;
                };
                let Some(_mesh) = self.meshes.get(&cmd.mesh) else {
                    continue;
                };

                let tex0_id = if pipe.tex_count >= 1 {
                    let Some(tex_id) = cmd.texture else { continue };
                    if !self.textures.contains_key(&tex_id) {
                        continue;
                    }
                    Some(tex_id)
                } else {
                    None
                };
                let tex1_id = if pipe.tex_count >= 2 {
                    let Some(tex1_id) = cmd.texture1 else {
                        continue;
                    };
                    if !self.textures.contains_key(&tex1_id) {
                        continue;
                    }
                    Some(tex1_id)
                } else {
                    None
                };
                let tex2_id = if pipe.tex_count >= 3 {
                    let Some(tex2_id) = cmd.texture2 else {
                        continue;
                    };
                    if !self.textures.contains_key(&tex2_id) {
                        continue;
                    }
                    Some(tex2_id)
                } else {
                    None
                };
                if pipe.tex_count >= 1 && self.sampler.is_none() {
                    continue;
                }

                // Uniform slot: write into ring buffer; key includes slot so
                // distinct uniform values do not incorrectly share a bind group.
                // For non-uniform pipes, ubuf_slot = MAX and BG is fully reusable.
                let ubuf_slot: u32 = if pipe.has_uniforms {
                    let slot = self.uniform_ring_next;
                    if slot >= self.uniform_ring.len() {
                        continue;
                    }
                    gpu.queue
                        .write_buffer(&self.uniform_ring[slot], 0, cast_f32(&cmd.uniforms));
                    self.uniform_ring_next = slot + 1;
                    slot as u32
                } else {
                    u32::MAX
                };

                let key = BgCacheKey {
                    pipeline: cmd.pipeline,
                    texture: tex0_id.unwrap_or(0),
                    texture1: tex1_id.unwrap_or(0),
                    texture2: tex2_id.unwrap_or(0),
                    ubuf_slot,
                };

                if !self.bg_cache.contains_key(&key) {
                    let mut entries: Vec<wgpu::BindGroupEntry<'_>> = Vec::new();
                    if pipe.tex_count >= 1 {
                        let t0 = &self.textures.get(&tex0_id.unwrap()).unwrap().view;
                        let use_nearest = pipe.parts_key.contains("live2d_mask")
                            || pipe.parts_key.contains("live2d_inverted");
                        let samp = if use_nearest {
                            self.nearest_sampler
                                .as_ref()
                                .unwrap_or(self.sampler.as_ref().unwrap())
                        } else {
                            self.sampler.as_ref().unwrap()
                        };
                        entries.push(wgpu::BindGroupEntry {
                            binding: 0,
                            resource: wgpu::BindingResource::TextureView(t0),
                        });
                        entries.push(wgpu::BindGroupEntry {
                            binding: 1,
                            resource: wgpu::BindingResource::Sampler(samp),
                        });
                    }
                    if let Some(id1) = tex1_id {
                        let t1 = &self.textures.get(&id1).unwrap().view;
                        entries.push(wgpu::BindGroupEntry {
                            binding: 2,
                            resource: wgpu::BindingResource::TextureView(t1),
                        });
                    }
                    if let Some(id2) = tex2_id {
                        let t2 = &self.textures.get(&id2).unwrap().view;
                        entries.push(wgpu::BindGroupEntry {
                            binding: 3,
                            resource: wgpu::BindingResource::TextureView(t2),
                        });
                    }
                    if pipe.has_uniforms {
                        let binding = match pipe.tex_count {
                            0 => 0u32,
                            1 => 2u32,
                            2 => 3u32,
                            _ => 4u32,
                        };
                        entries.push(wgpu::BindGroupEntry {
                            binding,
                            resource: self.uniform_ring[ubuf_slot as usize].as_entire_binding(),
                        });
                    }
                    let layout = &self.pipelines.get(&cmd.pipeline).unwrap().bind_group_layout;
                    let bg = gpu.device.create_bind_group(&wgpu::BindGroupDescriptor {
                        label: Some("draw-bg"),
                        layout,
                        entries: &entries,
                    });
                    self.bg_cache.insert(key, bg);
                }

                let pipe = self.pipelines.get(&cmd.pipeline).unwrap();
                let mesh = self.meshes.get(&cmd.mesh).unwrap();
                let bg = self.bg_cache.get(&key).unwrap();
                pass.set_pipeline(&pipe.pipeline);
                pass.set_vertex_buffer(0, mesh.vertex.slice(..));
                pass.set_bind_group(0, bg, &[]);

                if let Some(ref ibo) = mesh.index {
                    pass.set_index_buffer(ibo.slice(..), wgpu::IndexFormat::Uint32);
                    pass.draw_indexed(0..mesh.index_count, 0, 0..1);
                } else {
                    pass.draw(0..mesh.vertex_count, 0..1);
                }
            }
        }
        if let (Some(qs), Some(resolve_buf), Some(readback_buf)) = (
            &gpu.query_set,
            &gpu.query_resolve_buffer,
            &gpu.query_readback_buffer,
        ) {
            encoder.resolve_query_set(qs, 0..2, resolve_buf, 0);
            encoder.copy_buffer_to_buffer(resolve_buf, 0, readback_buf, 0, QUERY_RESOLVE_SIZE as u64);
        }
        Ok(())
    }
}



fn build_bind_group_layout(
    device: &wgpu::Device,
    key: &str,
    tex_count: u8,
    has_uniforms: bool,
) -> wgpu::BindGroupLayout {
    let mut entries = Vec::new();
    if tex_count >= 1 {
        entries.push(wgpu::BindGroupLayoutEntry {
            binding: 0,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Texture {
                sample_type: wgpu::TextureSampleType::Float { filterable: true },
                view_dimension: wgpu::TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        });
        entries.push(wgpu::BindGroupLayoutEntry {
            binding: 1,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
            count: None,
        });
    }
    if tex_count >= 2 {
        entries.push(wgpu::BindGroupLayoutEntry {
            binding: 2,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Texture {
                sample_type: wgpu::TextureSampleType::Float { filterable: true },
                view_dimension: wgpu::TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        });
    }
    if tex_count >= 3 {
        entries.push(wgpu::BindGroupLayoutEntry {
            binding: 3,
            visibility: wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Texture {
                sample_type: wgpu::TextureSampleType::Float { filterable: true },
                view_dimension: wgpu::TextureViewDimension::D2,
                multisampled: false,
            },
            count: None,
        });
    }
    if has_uniforms {
        // binding after last texture: 0→0, 1→2, 2→3, 3→4
        let binding = match tex_count {
            0 => 0u32,
            1 => 2u32,
            2 => 3u32,
            _ => 4u32,
        };
        entries.push(wgpu::BindGroupLayoutEntry {
            binding,
            visibility: wgpu::ShaderStages::VERTEX | wgpu::ShaderStages::FRAGMENT,
            ty: wgpu::BindingType::Buffer {
                ty: wgpu::BufferBindingType::Uniform,
                has_dynamic_offset: false,
                min_binding_size: std::num::NonZeroU64::new(64),
            },
            count: None,
        });
    }
    device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
        label: Some(key),
        entries: &entries,
    })
}

fn read_texture_rgba(
    gpu: &GpuState,
    texture: &wgpu::Texture,
    width: u32,
    height: u32,
) -> Result<(u32, u32, Vec<u8>), String> {
    let w = width.max(1);
    let h = height.max(1);
    let unpadded_bytes_per_row = 4u32.saturating_mul(w);
    let align = wgpu::COPY_BYTES_PER_ROW_ALIGNMENT;
    let padded_bytes_per_row = (unpadded_bytes_per_row + align - 1) / align * align;
    let buffer_size = (padded_bytes_per_row as u64).saturating_mul(h as u64);

    let staging = gpu.device.create_buffer(&wgpu::BufferDescriptor {
        label: Some("rt-readback"),
        size: buffer_size,
        usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
        mapped_at_creation: false,
    });

    let mut encoder = gpu
        .device
        .create_command_encoder(&wgpu::CommandEncoderDescriptor {
            label: Some("rt-readback-enc"),
        });
    encoder.copy_texture_to_buffer(
        wgpu::TexelCopyTextureInfo {
            texture,
            mip_level: 0,
            origin: wgpu::Origin3d::ZERO,
            aspect: wgpu::TextureAspect::All,
        },
        wgpu::TexelCopyBufferInfo {
            buffer: &staging,
            layout: wgpu::TexelCopyBufferLayout {
                offset: 0,
                bytes_per_row: Some(padded_bytes_per_row),
                rows_per_image: Some(h),
            },
        },
        wgpu::Extent3d {
            width: w,
            height: h,
            depth_or_array_layers: 1,
        },
    );
    gpu.queue.submit(Some(encoder.finish()));

    let slice = staging.slice(..);
    let (tx, rx) = std::sync::mpsc::channel();
    slice.map_async(wgpu::MapMode::Read, move |r| {
        let _ = tx.send(r);
    });
    gpu.device.poll(wgpu::Maintain::Wait);
    rx.recv()
        .map_err(|e| format!("map_async channel: {e}"))?
        .map_err(|e| format!("map_async: {e}"))?;

    let mapped = slice.get_mapped_range();
    let mut out = Vec::with_capacity((unpadded_bytes_per_row as usize).saturating_mul(h as usize));
    for row in 0..h as usize {
        let start = row * padded_bytes_per_row as usize;
        let end = start + unpadded_bytes_per_row as usize;
        out.extend_from_slice(&mapped[start..end]);
    }
    drop(mapped);
    staging.unmap();
    Ok((w, h, out))
}

fn cast_f32(v: &[f32]) -> &[u8] {
    unsafe { std::slice::from_raw_parts(v.as_ptr() as *const u8, std::mem::size_of_val(v)) }
}

fn cast_u32(v: &[u32]) -> &[u8] {
    unsafe { std::slice::from_raw_parts(v.as_ptr() as *const u8, std::mem::size_of_val(v)) }
}

const SOLID_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) color: vec4<f32>,
};
@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let a = v.color.a;
    return vec4<f32>(v.color.rgb * a, a);
}
"#;

const TEXTURED_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};
@group(0) @binding(0) var t_color: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let tex = textureSample(t_color, s_color, v.uv);
    let c = tex * v.color;
    return vec4<f32>(c.rgb * c.a, c.a);
}
"#;

/// renpy.dissolve GL2 parity: mix(tex0/old, tex1/new, amount).
/// amount from uniforms data0.x (u_renpy_dissolve); vertex color multiplies result.
const DISSOLVE_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};
struct Params {
    data0: vec4<f32>,
    data1: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};
@group(0) @binding(0) var t_old: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;
@group(0) @binding(2) var t_new: texture_2d<f32>;
@group(0) @binding(3) var<uniform> u: Params;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let amt = clamp(u.data0.x, 0.0, 1.0);
    // uniform-level branch: same for all 32 lanes in a wave → S_CBRANCH_EXECZ-friendly.
    if (amt <= 0.0) {
        let c1 = textureSample(t_new, s_color, v.uv) * v.color;
        let a = clamp(c1.a, 0.0, 1.0);
        return vec4<f32>(c1.rgb * a, a);
    }
    if (amt >= 1.0) {
        let c0 = textureSample(t_old, s_color, v.uv) * v.color;
        let a = clamp(c0.a, 0.0, 1.0);
        return vec4<f32>(c0.rgb * a, a);
    }
    let c0 = textureSample(t_old, s_color, v.uv);
    let c1 = textureSample(t_new, s_color, v.uv);
    let c = mix(c0, c1, amt) * v.color;
    let a = clamp(c.a, 0.0, 1.0);
    return vec4<f32>(c.rgb * a, a);
}
"#;

/// renpy.imagedissolve (GL2 parity) + HuangmeiC image_dissolve alias:
/// tex0 = control image, tex1 = old/bottom, tex2 = new/top
/// a = clamp((ctrl + offset) * multiplier, 0, 1); mix(bottom, top, a)
/// uniforms: data0.x = offset, data0.y = multiplier,
///           data0.z = channel (0 = alpha / stock ImageDissolve after red→alpha
///                              matrixcolor bake; >0.5 = red / product alias
///                              dissolve_transform which samples rule.r)
const IMAGEDISSOLVE_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};
struct Params {
    data0: vec4<f32>,
    data1: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};
@group(0) @binding(0) var t_control: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;
@group(0) @binding(2) var t_bottom: texture_2d<f32>;
@group(0) @binding(3) var t_top: texture_2d<f32>;
@group(0) @binding(4) var<uniform> u: Params;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let off = u.data0.x;
    let mult = u.data0.y;
    // uniform-level select: same for all lanes in a wave.
    let use_red = u.data0.z > 0.5;
    let control = textureSample(t_control, s_color, v.uv);
    let ctrl = select(control.a, control.r, use_red);
    // scalar-only compute; ctrl VGPR dies before bottom/top samples.
    let a = clamp((ctrl + off) * mult, 0.0, 1.0);
    let bottom = textureSample(t_bottom, s_color, v.uv);
    let top = textureSample(t_top, s_color, v.uv);
    let c = mix(bottom, top, a) * v.color;
    let out_a = clamp(c.a, 0.0, 1.0);
    return vec4<f32>(c.rgb * out_a, out_a);
}
"#;

/// Approximate renpy.blur: multi-tap gaussian with radius from u.data0.x = blur_log2.
const BLUR_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};
struct Params {
    // data0.x = blur_log2; pad to 64 bytes for min_binding_size
    data0: vec4<f32>,
    data1: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};
@group(0) @binding(0) var t_color: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;
@group(0) @binding(2) var<uniform> u: Params;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let dims = vec2<f32>(textureDimensions(t_color));
    let texel = vec2<f32>(1.0 / max(dims.x, 1.0), 1.0 / max(dims.y, 1.0));
    let blur_log2 = u.data0.x;
    // radius in texels ≈ 2^blur_log2; vertex color.a as extra scale
    let radius = max(exp2(blur_log2), 0.5) * max(v.color.a, 0.01);
    var acc = vec4<f32>(0.0, 0.0, 0.0, 0.0);
    var norm = 0.0;
    // 5-tap cross gaussian approx (no array constructors — naga-safe)
    acc = acc + textureSample(t_color, s_color, v.uv) * 1.0;
    norm = norm + 1.0;
    acc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(radius, 0.0) * texel) * 0.6;
    norm = norm + 0.6;
    acc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(-radius, 0.0) * texel) * 0.6;
    norm = norm + 0.6;
    acc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(0.0, radius) * texel) * 0.6;
    norm = norm + 0.6;
    acc = acc + textureSample(t_color, s_color, v.uv + vec2<f32>(0.0, -radius) * texel) * 0.6;
    norm = norm + 0.6;
    let tex = acc / max(norm, 0.0001);
    let c = tex * vec4<f32>(v.color.r, v.color.g, v.color.b, 1.0);
    let a = c.a;
    return vec4<f32>(c.r * a, c.g * a, c.b * a, a);
}
"#;

/// renpy.matrixcolor: 4x4 color matrix from uniform (column-major as 4 vec4 columns).
const MATRIXCOLOR_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};
struct Params {
    col0: vec4<f32>,
    col1: vec4<f32>,
    col2: vec4<f32>,
    col3: vec4<f32>,
};
@group(0) @binding(0) var t_color: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;
@group(0) @binding(2) var<uniform> u: Params;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let tex = textureSample(t_color, s_color, v.uv) * v.color;
    let m = mat4x4<f32>(u.col0, u.col1, u.col2, u.col3);
    let c = m * tex;
    let a = clamp(c.a, 0.0, 1.0);
    return vec4<f32>(c.r * a, c.g * a, c.b * a, a);
}
"#;

/// renpy.alpha_mask: src * mask.r (dual texture).
const ALPHA_MASK_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};
@group(0) @binding(0) var t_src: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;
@group(0) @binding(2) var t_mask: texture_2d<f32>;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let src = textureSample(t_src, s_color, v.uv) * v.color;
    let ma = textureSample(t_mask, s_color, v.uv).r;
    let a = src.a * ma;
    return vec4<f32>(src.rgb * ma, a);
}
"#;

/// renpy.mask: src * (mask.a * mult + offset); uniforms data0.xy = mult, offset.
const MASK_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};
struct Params {
    data0: vec4<f32>,
    data1: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};
@group(0) @binding(0) var t_src: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;
@group(0) @binding(2) var t_mask: texture_2d<f32>;
@group(0) @binding(3) var<uniform> u: Params;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let mult = u.data0.x;
    let offset = u.data0.y;
    let src = textureSample(t_src, s_color, v.uv) * v.color;
    let ma = textureSample(t_mask, s_color, v.uv).a;
    let k = ma * mult + offset;
    let c = src * k;
    return vec4<f32>(c.r, c.g, c.b, c.a);
}
"#;

/// live2d.mask: dual-tex; mask UV from model pos * ppu + offset / model_size (y-flip).
/// uniforms: data0 = (model_size.x, model_size.y, ppu, offset.x); data1.x = offset.y
const LIVE2D_MASK_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) mask_uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct Params {
    data0: vec4<f32>,
    data1: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};
@group(0) @binding(0) var t_src: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;
@group(0) @binding(2) var t_mask: texture_2d<f32>;
@group(0) @binding(3) var<uniform> u: Params;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    let model_size = max(u.data0.xy, vec2<f32>(1.0, 1.0));
    let ppu = u.data0.z;
    let offset = vec2<f32>(u.data0.w, u.data1.x);
    var muv = (v.pos * ppu + offset) / model_size;
    muv.y = 1.0 - muv.y;
    o.mask_uv = muv;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let color = textureSample(t_src, s_color, v.uv) * v.color;
    let mask = textureSample(t_mask, s_color, v.mask_uv);
    let c = color * mask.a;
    return vec4<f32>(c.rgb * c.a, c.a);
}
"#;

/// live2d.inverted_mask: color * (1 - mask.a); same UV math as live2d.mask.
const LIVE2D_INVERTED_MASK_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) mask_uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct Params {
    data0: vec4<f32>,
    data1: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};
@group(0) @binding(0) var t_src: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;
@group(0) @binding(2) var t_mask: texture_2d<f32>;
@group(0) @binding(3) var<uniform> u: Params;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    let model_size = max(u.data0.xy, vec2<f32>(1.0, 1.0));
    let ppu = u.data0.z;
    let offset = vec2<f32>(u.data0.w, u.data1.x);
    var muv = (v.pos * ppu + offset) / model_size;
    muv.y = 1.0 - muv.y;
    o.mask_uv = muv;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let color = textureSample(t_src, s_color, v.uv) * v.color;
    let mask = textureSample(t_mask, s_color, v.mask_uv);
    let c = color * (1.0 - mask.a);
    return vec4<f32>(c.rgb * c.a, c.a);
}
"#;

/// live2d.colors: multiply then screen blend; data0=u_multiply, data1=u_screen.
const LIVE2D_COLORS_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};
struct Params {
    multiply: vec4<f32>,
    screen: vec4<f32>,
    data2: vec4<f32>,
    data3: vec4<f32>,
};
@group(0) @binding(0) var t_color: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;
@group(0) @binding(2) var<uniform> u: Params;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = v.uv;
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    var c = textureSample(t_color, s_color, v.uv) * v.color;
    // gl_FragColor.rgb = rgb * multiply.rgb
    c = vec4<f32>(c.rgb * u.multiply.rgb, c.a);
    // screen: (rgb + screen.rgb * a) - (rgb * screen.rgb)
    let rgb = (c.rgb + u.screen.rgb * c.a) - (c.rgb * u.screen.rgb);
    let a = clamp(c.a, 0.0, 1.0);
    return vec4<f32>(rgb * a, a);
}
"#;

/// live2d.flip_texture: flip V coordinate (Cubism texture orientation).
const LIVE2D_FLIP_WGSL: &str = r#"
struct VsIn {
    @location(0) pos: vec2<f32>,
    @location(1) uv: vec2<f32>,
    @location(2) color: vec4<f32>,
};
struct VsOut {
    @builtin(position) clip: vec4<f32>,
    @location(0) uv: vec2<f32>,
    @location(1) color: vec4<f32>,
};
@group(0) @binding(0) var t_color: texture_2d<f32>;
@group(0) @binding(1) var s_color: sampler;

@vertex
fn vs_main(v: VsIn) -> VsOut {
    var o: VsOut;
    o.clip = vec4<f32>(v.pos, 0.0, 1.0);
    o.uv = vec2<f32>(v.uv.x, 1.0 - v.uv.y);
    o.color = v.color;
    return o;
}
@fragment
fn fs_main(v: VsOut) -> @location(0) vec4<f32> {
    let tex = textureSample(t_color, s_color, v.uv);
    let c = tex * v.color;
    return vec4<f32>(c.rgb * c.a, c.a);
}
"#;
