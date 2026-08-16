//! Phase 0 pump smoke helper (still used by RENPY_HOST_PHASE0_SMOKE=1).

use std::sync::Arc;
use std::time::{Duration, Instant};

use log::{error, info};
use winit::application::ApplicationHandler;
use winit::event::WindowEvent;
use winit::event_loop::{ActiveEventLoop, EventLoop};
use winit::platform::pump_events::{EventLoopExtPumpEvents, PumpStatus};
use winit::window::{Window, WindowId};

use crate::gpu::GpuState;
use crate::python::PythonRuntime;

pub struct Phase0Handler {
    pub window: Option<Arc<Window>>,
    pub gpu: Option<GpuState>,
    pub python: PythonRuntime,
    pub started: Instant,
    pub frames: u64,
    pub should_exit: bool,
    pub auto_exit: Option<Duration>,
    smoked: bool,
}

impl Phase0Handler {
    pub fn new(python: PythonRuntime) -> Self {
        Self {
            window: None,
            gpu: None,
            python,
            started: Instant::now(),
            frames: 0,
            should_exit: false,
            auto_exit: None,
            smoked: false,
        }
    }
}

impl ApplicationHandler for Phase0Handler {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let attrs = Window::default_attributes()
            .with_title("renpy-host (Phase 0)")
            .with_inner_size(winit::dpi::LogicalSize::new(1280.0, 720.0));
        match event_loop.create_window(attrs) {
            Ok(window) => {
                let window = Arc::new(window);
                match pollster::block_on(GpuState::new(window.clone())) {
                    Ok(gpu) => {
                        info!(
                            "[renpy-host] wgpu adapter backend={} name=\"{}\"",
                            gpu.backend_label(),
                            gpu.adapter_name
                        );
                        self.gpu = Some(gpu);
                    }
                    Err(e) => {
                        error!("GPU init failed: {e}");
                        self.should_exit = true;
                    }
                }
                self.window = Some(window);
            }
            Err(e) => {
                error!("create_window failed: {e}");
                self.should_exit = true;
            }
        }
    }

    fn window_event(&mut self, _event_loop: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => {
                // Cooperative quit: set flag only; about_to_wait calls event_loop.exit().
                // Avoid tearing the loop down mid-handler (mirrors ProductApp Q-A).
                self.should_exit = true;
            }
            WindowEvent::Resized(size) => {
                if let Some(gpu) = self.gpu.as_mut() {
                    gpu.resize(size);
                }
            }
            WindowEvent::RedrawRequested => {
                if let Some(gpu) = self.gpu.as_mut() {
                    if gpu.render_clear().is_ok() {
                        self.frames = self.frames.saturating_add(1);
                    }
                }
            }
            _ => {}
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        if !self.smoked && self.gpu.is_some() {
            self.smoked = true;
            if let Err(e) = self.python.smoke() {
                error!("Python smoke failed: {e}");
                self.should_exit = true;
            }
        }
        if let Some(limit) = self.auto_exit {
            if self.started.elapsed() >= limit {
                self.should_exit = true;
            }
        }
        if let Some(w) = self.window.as_ref() {
            w.request_redraw();
        }
        if self.should_exit {
            event_loop.exit();
        }
    }
}

pub fn run_pump_smoke(python: PythonRuntime, total_ms: u64) -> Result<(), String> {
    let mut event_loop = EventLoop::new().map_err(|e| format!("EventLoop: {e}"))?;
    event_loop.set_control_flow(winit::event_loop::ControlFlow::Poll);
    let mut handler = Phase0Handler::new(python);
    handler.auto_exit = Some(Duration::from_millis(total_ms));

    let start = Instant::now();
    let deadline = start + Duration::from_millis(total_ms);
    let mut pumps = 0u32;

    info!("pump smoke: nested EventLoopExtPumpEvents for {total_ms} ms");

    loop {
        let now = Instant::now();
        if now >= deadline || handler.should_exit {
            break;
        }
        let remaining = deadline - now;
        let timeout = Some(remaining.min(Duration::from_millis(16)));
        match event_loop.pump_app_events(timeout, &mut handler) {
            PumpStatus::Continue => pumps += 1,
            PumpStatus::Exit(code) => {
                info!("pump smoke exit code={code} after {pumps} pumps");
                break;
            }
        }
    }

    info!(
        "pump smoke done: pumps={pumps} frames={} elapsed={:?}",
        handler.frames,
        start.elapsed()
    );
    Ok(())
}
