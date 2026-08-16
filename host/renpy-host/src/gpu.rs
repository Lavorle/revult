//! wgpu Vulkan surface + clear-color present (Phase 0).

use std::sync::Arc;

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
            let latency = if matches!(want, wgpu::PresentMode::Fifo) { 2 } else { 1 };
            info!(
                "PresentMode override={} (supported={:?}) desired_maximum_frame_latency={}",
                present_mode_name(want),
                supported.iter().map(|m| present_mode_name(*m)).collect::<Vec<_>>(),
                latency
            );
            return (want, latency);
        }
        log::warn!(
            "RENPY_HOST_PRESENT_MODE={} not in supported {:?}; falling back to policy chain",
            present_mode_name(want),
            supported.iter().map(|m| present_mode_name(*m)).collect::<Vec<_>>()
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
    let latency = if matches!(chosen, wgpu::PresentMode::Fifo) { 2 } else { 1 };
    info!(
        "PresentMode selected={} supported={:?} desired_maximum_frame_latency={}",
        present_mode_name(chosen),
        supported.iter().map(|m| present_mode_name(*m)).collect::<Vec<_>>(),
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

        let (device, queue) = adapter
            .request_device(
                &wgpu::DeviceDescriptor {
                    label: Some("renpy-host-device"),
                    required_features: wgpu::Features::empty(),
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

    pub fn render_clear(&mut self) -> Result<(), wgpu::SurfaceError> {
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
                timestamp_writes: None,
                occlusion_query_set: None,
            });
        }
        self.queue.submit(Some(encoder.finish()));
        frame.present();
        Ok(())
    }
}
