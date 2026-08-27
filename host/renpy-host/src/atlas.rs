//! Dynamic glyph atlas — isolated from `arena.rs` (>3200 line guard, M3 B1 T1).
//!
//! Owns `AtlasTexture {id, size, glyph_slots: HashMap<GlyphKey, UvRect>, lru}` and
//! shelf-packing allocation for SDF glyphs. GPU texture itself lives in
//! `GpuArena::textures` (LruSlotMap<TextureSlot>) so LRU/FIFO + deferred-destroy
//! stay unified; this module owns the packing + slot map. Exposed via
//! `GpuArena::create_atlas_rgba / destroy_atlas / write_atlas_subrect` which
//! forward to the wgpu queue. Held by `GpuArena` as `Option<AtlasTexture>`.
//!
//! Constants mirror `renpy/wgpu/constants.py` provenance:
//! ATLAS_SIZE=2048  src: atlas.rs:12 — dynamic glyph atlas width/height
//! ATLAS_MAX_GLYPHS=4096 src: text_atlas.py:18 — cap per atlas
//! SDF_RADIUS=8     src: text_sdf.py:12 — separated from PIL_PADDING

use std::collections::{HashMap, VecDeque};

pub const ATLAS_SIZE: u32 = 2048;
pub const ATLAS_MAX_GLYPHS: usize = 4096;
pub const ATLAS_MAX_ATLASES: usize = 2;
pub const SDF_RADIUS: u32 = 8;
pub const SDF_THRESHOLD: f32 = 0.5;
pub const SDF_AA: f32 = 0.02;

/// Opaque glyph identity — Python side uses (char,size,font) tuple hashed to u64.
/// Rust side never interprets it beyond Hash/Eq.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub struct GlyphKey(pub u64);

impl From<u64> for GlyphKey {
    fn from(v: u64) -> Self {
        Self(v)
    }
}

/// UV rectangle for a glyph inside the atlas (pixels + normalized).
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct UvRect {
    pub x: u32,
    pub y: u32,
    pub w: u32,
    pub h: u32,
    pub u0: f32,
    pub v0: f32,
    pub u1: f32,
    pub v1: f32,
}

impl UvRect {
    pub fn new(x: u32, y: u32, w: u32, h: u32, atlas_size: u32) -> Self {
        let s = atlas_size as f32;
        Self {
            x,
            y,
            w,
            h,
            u0: x as f32 / s,
            v0: y as f32 / s,
            u1: (x + w) as f32 / s,
            v1: (y + h) as f32 / s,
        }
    }
}

/// Single atlas texture owner — shelf packing + LRU (mirrors Python AtlasManager).
/// `id == 0` means not yet allocated (no GPU texture).
pub struct AtlasTexture {
    pub id: u64,
    pub size: u32,
    pub glyph_slots: HashMap<GlyphKey, UvRect>,
    pub lru: VecDeque<GlyphKey>,
    cursor_x: u32,
    cursor_y: u32,
    row_h: u32,
    /// Padding per side (mirrors PIL_PADDING=4, separate from SDF_RADIUS).
    pub padding: u32,
}

impl AtlasTexture {
    pub fn new(size: u32) -> Self {
        Self {
            id: 0,
            size,
            glyph_slots: HashMap::new(),
            lru: VecDeque::new(),
            cursor_x: 0,
            cursor_y: 0,
            row_h: 0,
            padding: 4,
        }
    }

    pub fn with_padding(mut self, pad: u32) -> Self {
        self.padding = pad;
        self
    }

    pub fn is_allocated(&self) -> bool {
        self.id != 0
    }

    /// Mark `key` as MRU.
    fn touch_lru(&mut self, key: GlyphKey) {
        if let Some(pos) = self.lru.iter().position(|&k| k == key) {
            self.lru.remove(pos);
        }
        self.lru.push_back(key);
    }

    /// Evict least-recently-used slot. Returns evicted key if any.
    pub fn evict_lru(&mut self) -> Option<GlyphKey> {
        let key = self.lru.pop_front()?;
        self.glyph_slots.remove(&key);
        Some(key)
    }

    /// Clear all slots and packing cursors (keeps `id`).
    pub fn clear_slots(&mut self) {
        self.glyph_slots.clear();
        self.lru.clear();
        self.cursor_x = 0;
        self.cursor_y = 0;
        self.row_h = 0;
    }

    /// Full clear including id (forces reallocation).
    pub fn clear_all(&mut self) {
        self.clear_slots();
        self.id = 0;
    }

    /// Check if `key` is present and touch LRU.
    pub fn get(&mut self, key: GlyphKey) -> Option<UvRect> {
        let rect = self.glyph_slots.get(&key).copied()?;
        self.touch_lru(key);
        Some(rect)
    }

    /// Allocate a glyph rectangle (w,h) content size, padding expanded.
    /// Returns `None` for oversized glyph fallback (spec: 超大字形fallback).
    pub fn alloc_glyph(&mut self, key: GlyphKey, w: u32, h: u32) -> Option<UvRect> {
        let need_w = w.saturating_add(self.padding * 2);
        let need_h = h.saturating_add(self.padding * 2);
        if need_w > self.size || need_h > self.size {
            return None;
        }
        // Hit?
        if let Some(&rect) = self.glyph_slots.get(&key) {
            self.touch_lru(key);
            return Some(rect);
        }
        // Cap eviction
        while self.glyph_slots.len() >= ATLAS_MAX_GLYPHS {
            if self.evict_lru().is_none() {
                break;
            }
            if self.glyph_slots.is_empty() {
                self.cursor_x = 0;
                self.cursor_y = 0;
                self.row_h = 0;
            }
        }

        // Shelf packing with eviction spiral
        let mut attempts = 0usize;
        let max_attempts = ATLAS_MAX_GLYPHS + 8;
        while attempts < max_attempts {
            attempts += 1;
            if self.cursor_x + need_w > self.size {
                self.cursor_x = 0;
                self.cursor_y = self.cursor_y.saturating_add(self.row_h);
                self.row_h = 0;
            }
            if self.cursor_y + need_h > self.size {
                if self.glyph_slots.is_empty() {
                    self.cursor_x = 0;
                    self.cursor_y = 0;
                    self.row_h = 0;
                    continue;
                }
                // 2K满驱逐路径
                self.evict_lru();
                if self.cursor_y + need_h > self.size && self.glyph_slots.len() < ATLAS_MAX_GLYPHS / 2 {
                    self.cursor_x = 0;
                    self.cursor_y = 0;
                    self.row_h = 0;
                }
                continue;
            }
            let x = self.cursor_x;
            let y = self.cursor_y;
            let rect = UvRect::new(x, y, need_w, need_h, self.size);
            self.glyph_slots.insert(key, rect);
            self.touch_lru(key);
            self.cursor_x += need_w;
            self.row_h = self.row_h.max(need_h);
            return Some(rect);
        }
        None
    }

    /// Number of live glyph slots.
    pub fn len(&self) -> usize {
        self.glyph_slots.len()
    }

    pub fn is_empty(&self) -> bool {
        self.glyph_slots.is_empty()
    }

    /// Replace texture handle (after GPU create).
    pub fn set_id(&mut self, id: u64) {
        self.id = id;
    }

    /// Invalidate handle when texture was destroyed / dead-handle recovery.
    pub fn invalidate(&mut self) {
        self.clear_all();
    }
}

impl Default for AtlasTexture {
    fn default() -> Self {
        Self::new(ATLAS_SIZE)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_atlas_alloc_and_lru() {
        let mut atlas = AtlasTexture::new(64);
        let k1 = GlyphKey(1);
        let r1 = atlas.alloc_glyph(k1, 10, 10).expect("alloc 1");
        assert_eq!(r1.w, 10 + 8);
        assert_eq!(atlas.len(), 1);
        // Hit returns same rect and touches LRU
        let r1b = atlas.alloc_glyph(k1, 10, 10).expect("hit");
        assert_eq!(r1b.x, r1.x);
        assert_eq!(atlas.lru.back().copied(), Some(k1));
    }

    #[test]
    fn test_atlas_oversized_fallback() {
        let mut atlas = AtlasTexture::new(64);
        let k = GlyphKey(99);
        // need_w = 100+8 >64 => None
        assert!(atlas.alloc_glyph(k, 100, 10).is_none());
    }

    #[test]
    fn test_atlas_evict_lru() {
        let mut atlas = AtlasTexture::new(32);
        // Fill with tiny glyphs until full-ish
        for i in 0..10u64 {
            let _ = atlas.alloc_glyph(GlyphKey(i), 4, 4);
        }
        let before = atlas.len();
        let ev = atlas.evict_lru().expect("evict");
        assert_eq!(atlas.len(), before - 1);
        assert!(!atlas.glyph_slots.contains_key(&ev));
    }
}
