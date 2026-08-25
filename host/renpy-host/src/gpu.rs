//! wgpu Vulkan surface + clear-color present (Phase 0).

use std::sync::Arc;
use std::time::Duration;

use log::info;
use wgpu::util::DeviceExt;
use winit::dpi::PhysicalSize;
use winit::window::Window;

/// Preferred internal + swapchain format (ADR §4.3.1): Rgba8Unorm (non-sRGB).
/// Some Wayland/X11 surfaces only expose Bgra8Unorm{,Srgb}; GpuState falls back
/// and stores the actual `surface_format` so arena pipelines/textures match.
pub const SWAPCHAIN_FORMAT: wgpu::TextureFormat = wgpu::TextureFormat::Rgba8Unorm;
pub struct GpuState {
    pub surface: wgpu::Surface<'static>,
    pub device: wgpu::Device,
    pub queue: wgpu::Queue,
    pub config: wgpu::SurfaceConfiguration,
    /// Retained so resize can re-query surface caps and re-run PresentMode policy.
    pub adapter: wgpu::Adapter,
    pub adapter_name: String,
    pub backend: wgpu::Backend,
    /// Solid clear color (premultiplied-ish dark teal for visibility).
    pub clear: wgpu::Color,
    /// Actual surface / RT / sample format (Rgba8Unorm preferred, Bgra8Unorm fallback).
    pub surface_format: wgpu::TextureFormat,
    /// TIMESTAMP_QUERY support probing (D debt).
    pub timestamp_supported: bool,
    pub query_set: Option<wgpu::QuerySet>,
    pub query_resolve_buffer: Option<wgpu::Buffer>,
    pub query_readback_buffer: Option<wgpu::Buffer>,
    pub timestamp_period: f32,
}

fn present_mode_name(mode: wgpu::PresentMode) -> &'static str {
    match mode {
        wgpu::PresentMode::Fifo => "fifo",
        wgpu::PresentMode::FifoRelaxed => "fifo_relaxed",
        wgpu::PresentMode::Mailbox => "mailbox",
        wgpu::PresentMode::Immediate => "immediate",
        wgpu::PresentMode::AutoVsync => "auto_vsync",
        wgpu::PresentMode::AutoNoVsync => "auto_no_vsync",
    }
}

fn parse_present_mode_override(raw: &str) -> Option<wgpu::PresentMode> {
    match raw.trim().to_ascii_lowercase().as_str() {
        "fifo" => Some(wgpu::PresentMode::Fifo),
        "fifo_relaxed" | "fiforelaxed" | "relaxed" => Some(wgpu::PresentMode::FifoRelaxed),
        "mailbox" => Some(wgpu::PresentMode::Mailbox),
        "immediate" => Some(wgpu::PresentMode::Immediate),
        "auto_vsync" | "autovsync" => Some(wgpu::PresentMode::AutoVsync),
        "auto_no_vsync" | "autonovsync" => Some(wgpu::PresentMode::AutoNoVsync),
        "" => None,
        other => {
            log::warn!("RENPY_HOST_PRESENT_MODE unknown value {other:?}; ignoring override");
            None
        }
    }
}

/// Select PresentMode for pacing: Mailbox → FifoRelaxed → Immediate → Fifo.
/// Env RENPY_HOST_PRESENT_MODE can force a mode when supported.
fn select_present_mode(supported: &[wgpu::PresentMode]) -> (wgpu::PresentMode, u32) {
    let override_mode = std::env::var("RENPY_HOST_PRESENT_MODE")
        .ok()
        .and_then(|s| parse_present_mode_override(&s));
    if let Some(want) = override_mode {
        if supported.contains(&want) {
            let latency = if matches!(want, wgpu::PresentMode::Fifo) {
                2
            } else {
                1
            };
            info!(
                "PresentMode override={} (supported={:?}) desired_maximum_frame_latency={}",
                present_mode_name(want),
                supported
                    .iter()
                    .map(|m| present_mode_name(*m))
                    .collect::<Vec<_>>(),
                latency
            );
            return (want, latency);
        }
        log::warn!(
            "RENPY_HOST_PRESENT_MODE={} not in supported {:?}; falling back to policy chain",
            present_mode_name(want),
            supported
                .iter()
                .map(|m| present_mode_name(*m))
                .collect::<Vec<_>>()
        );
    }

    // Policy: Mailbox → FifoRelaxed → Immediate → Fifo
    let preference = [
        wgpu::PresentMode::Mailbox,
        wgpu::PresentMode::FifoRelaxed,
        wgpu::PresentMode::Immediate,
        wgpu::PresentMode::Fifo,
    ];
    let chosen = preference
        .into_iter()
        .find(|m| supported.contains(m))
        .unwrap_or(wgpu::PresentMode::Fifo);
    // Non-Fifo: try latency 1 for tighter pacing; Fifo keeps 2 for stability.
    let latency = if matches!(chosen, wgpu::PresentMode::Fifo) {
        2
    } else {
        1
    };
    info!(
        "PresentMode selected={} supported={:?} desired_maximum_frame_latency={}",
        present_mode_name(chosen),
        supported
            .iter()
            .map(|m| present_mode_name(*m))
            .collect::<Vec<_>>(),
        latency
    );
    (chosen, latency)
}

impl GpuState {
    pub async fn new(window: Arc<Window>) -> Result<Self, String> {
        let size = window.inner_size();
        let width = size.width.max(1);
        let height = size.height.max(1);

        let instance = wgpu::Instance::new(&wgpu::InstanceDescriptor {
            backends: wgpu::Backends::VULKAN,
            flags: wgpu::InstanceFlags::default(),
            backend_options: Default::default(),
            // wgpu 24 field name
            ..Default::default()
        });

        let surface = instance
            .create_surface(window.clone())
            .map_err(|e| format!("create_surface: {e}"))?;

        let adapter = instance
            .request_adapter(&wgpu::RequestAdapterOptions {
                power_preference: wgpu::PowerPreference::HighPerformance,
                compatible_surface: Some(&surface),
                force_fallback_adapter: false,
            })
            .await
            .ok_or_else(|| "request_adapter: no suitable Vulkan adapter".to_string())?;

        let info = adapter.get_info();
        if info.backend != wgpu::Backend::Vulkan {
            return Err(format!(
                "expected Vulkan backend, got {:?} ({})",
                info.backend, info.name
            ));
        }
        info!(
            "adapter: name={} vendor={} device={} backend={:?}",
            info.name, info.vendor, info.device, info.backend
        );
        let ts_supported = adapter.features().contains(wgpu::Features::TIMESTAMP_QUERY);
        info!("timestamp_query supported={}", ts_supported);
        let required_features = if ts_supported {
            wgpu::Features::TIMESTAMP_QUERY
        } else {
            wgpu::Features::empty()
        };
        let (device, queue) = adapter
            .request_device(
                &wgpu::DeviceDescriptor {
                    label: Some("renpy-host-device"),
                    required_features,
                    required_limits: wgpu::Limits::default(),
                    memory_hints: Default::default(),
                },
                None,
            )
            .await
            .map_err(|e| format!("request_device: {e}"))?;

        // Prefer Rgba8Unorm; fall back to Bgra8Unorm when the surface only exposes
        // BGRA (common on Wayland/RADV). Arena pipelines + sample textures follow
        // `surface_format` so encode_pass can target both game RT and swapchain.
        let caps = surface.get_capabilities(&adapter);
        let format = if caps.formats.contains(&SWAPCHAIN_FORMAT) {
            SWAPCHAIN_FORMAT
        } else if caps.formats.contains(&wgpu::TextureFormat::Bgra8Unorm) {
            info!(
                "surface lacks Rgba8Unorm; falling back to Bgra8Unorm (available={:?})",
                caps.formats
            );
            wgpu::TextureFormat::Bgra8Unorm
        } else if caps.formats.contains(&wgpu::TextureFormat::Bgra8UnormSrgb) {
            // Prefer non-sRGB when possible; last resort accept sRGB BGRA.
            info!(
                "surface lacks Rgba8Unorm/Bgra8Unorm; falling back to Bgra8UnormSrgb (available={:?})",
                caps.formats
            );
            wgpu::TextureFormat::Bgra8UnormSrgb
        } else {
            return Err(format!(
                "surface does not support Rgba8Unorm or Bgra8Unorm; available={:?}",
                caps.formats
            ));
        };

        let (present_mode, frame_latency) = select_present_mode(&caps.present_modes);
        let config = wgpu::SurfaceConfiguration {
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_DST,
            format,
            width,
            height,
            present_mode,
            alpha_mode: caps.alpha_modes[0],
            view_formats: vec![],
            desired_maximum_frame_latency: frame_latency,
        };
        surface.configure(&device, &config);
        info!(
            "surface configured {}x{} format={:?} present_mode={} latency={}",
            width,
            height,
            format,
            present_mode_name(present_mode),
            frame_latency
        );

        // Touch DeviceExt so the util feature path stays linked for later uploads.
        let _ = device.create_buffer_init(&wgpu::util::BufferInitDescriptor {
            label: Some("phase0-dummy"),
            contents: &[0u8; 4],
            usage: wgpu::BufferUsages::UNIFORM,
        });
        let (query_set, query_resolve_buffer, query_readback_buffer, timestamp_period) =
            if ts_supported {
                let qs = device.create_query_set(&wgpu::QuerySetDescriptor {
                    label: Some("timestamp-query"),
                    ty: wgpu::QueryType::Timestamp,
                    count: 2,
                });
                let resolve_buf = device.create_buffer(&wgpu::BufferDescriptor {
                    label: Some("timestamp-resolve"),
                    size: 16,
                    usage: wgpu::BufferUsages::QUERY_RESOLVE | wgpu::BufferUsages::COPY_SRC,
                    mapped_at_creation: false,
                });
                let readback_buf = device.create_buffer(&wgpu::BufferDescriptor {
                    label: Some("timestamp-readback"),
                    size: 16,
                    usage: wgpu::BufferUsages::MAP_READ | wgpu::BufferUsages::COPY_DST,
                    mapped_at_creation: false,
                });
                let period = queue.get_timestamp_period();
                info!("timestamp period={}", period);
                (Some(qs), Some(resolve_buf), Some(readback_buf), period)
            } else {
                (None, None, None, 1.0)
            };
        Ok(Self {
            surface,
            device,
            queue,
            config,
            adapter,
            adapter_name: info.name,
            backend: info.backend,
            clear: wgpu::Color {
                r: 0.08,
                g: 0.18,
                b: 0.28,
                a: 1.0,
            },
            surface_format: format,
            timestamp_supported: ts_supported,
            query_set,
            query_resolve_buffer,
            query_readback_buffer,
            timestamp_period,
        })
    }

    pub fn backend_label(&self) -> &'static str {
        match self.backend {
            wgpu::Backend::Vulkan => "Vulkan",
            wgpu::Backend::Metal => "Metal",
            wgpu::Backend::Dx12 => "Dx12",
            wgpu::Backend::Gl => "Gl",
            wgpu::Backend::BrowserWebGpu => "BrowserWebGpu",
            wgpu::Backend::Empty => "Empty",
        }
    }
    pub fn resize(&mut self, size: PhysicalSize<u32>) {
        if size.width == 0 || size.height == 0 {
            return;
        }
        self.config.width = size.width;
        self.config.height = size.height;
        // Re-run PresentMode policy on fresh surface caps (plan must-merge: same chain on resize).
        let caps = self.surface.get_capabilities(&self.adapter);
        let (present_mode, frame_latency) = select_present_mode(&caps.present_modes);
        self.config.present_mode = present_mode;
        self.config.desired_maximum_frame_latency = frame_latency;
        self.surface.configure(&self.device, &self.config);
        info!(
            "surface resize {}x{} present_mode={} latency={}",
            self.config.width,
            self.config.height,
            present_mode_name(present_mode),
            frame_latency
        );
    }

    pub fn render_clear(&mut self) -> Result<Option<Duration>, wgpu::SurfaceError> {
        let frame = self.surface.get_current_texture()?;
        let view = frame
            .texture
            .create_view(&wgpu::TextureViewDescriptor::default());
        let mut encoder = self
            .device
            .create_command_encoder(&wgpu::CommandEncoderDescriptor {
                label: Some("phase0-clear"),
            });
        {
            let timestamp_writes =
                self.query_set
                    .as_ref()
                    .map(|qs| wgpu::RenderPassTimestampWrites {
                        query_set: qs,
                        beginning_of_pass_write_index: Some(0),
                        end_of_pass_write_index: Some(1),
                    });
            let _pass = encoder.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: Some("phase0-clear-pass"),
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: &view,
                    resolve_target: None,
                    ops: wgpu::Operations {
                        load: wgpu::LoadOp::Clear(self.clear),
                        store: wgpu::StoreOp::Store,
                    },
                })],
                depth_stencil_attachment: None,
                timestamp_writes,
                occlusion_query_set: None,
            });
        }
        if let (Some(qs), Some(resolve_buf), Some(readback_buf)) = (
            &self.query_set,
            &self.query_resolve_buffer,
            &self.query_readback_buffer,
        ) {
            encoder.resolve_query_set(qs, 0..2, resolve_buf, 0);
            encoder.copy_buffer_to_buffer(resolve_buf, 0, readback_buf, 0, 16);
        }
        self.queue.submit(Some(encoder.finish()));
        // Read back GPU timestamps if supported
        let gpu_duration = if self.timestamp_supported {
            if let Some(buf) = &self.query_readback_buffer {
                let slice = buf.slice(..);
                let (sender, receiver) = std::sync::mpsc::channel();
                slice.map_async(wgpu::MapMode::Read, move |r| {
                    let _ = sender.send(r);
                });
                // Block until mapping completes
                self.device.poll(wgpu::Maintain::Wait);
                match receiver.recv() {
                    Ok(Ok(())) => {
                        let data = slice.get_mapped_range();
                        let mut ns_opt: Option<Duration> = None;
                        if data.len() >= 16 {
                            let start = u64::from_le_bytes(data[0..8].try_into().unwrap());
                            let end = u64::from_le_bytes(data[8..16].try_into().unwrap());
                            let diff = end.wrapping_sub(start);
                            let ns = (diff as f64 * self.timestamp_period as f64) as u64;
                            ns_opt = Some(Duration::from_nanos(ns));
                        }
                        drop(data);
                        buf.unmap();
                        ns_opt
                    }
                    _ => {
                        // unmap if needed (no-op if not mapped)
                        let _ = buf.unmap();
                        None
                    }
                }
            } else {
                None
            }
        } else {
            None
        };
        frame.present();
        Ok(gpu_duration)
    }
}
