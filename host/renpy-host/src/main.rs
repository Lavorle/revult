//! renpy-host entry — winit outermost, Python pumped (plan §4.1.1).

mod app;
mod arena;
mod audio;
mod event_queue;
mod gpu;
mod input;
mod input_trace;
mod pump;
mod python;
mod shader;
mod state;
mod timer;

use std::cell::Cell;
use std::sync::Arc;
use std::time::{Duration, Instant};

use log::{error, info};
use winit::application::ApplicationHandler;
use winit::event::WindowEvent;
use winit::event_loop::{ActiveEventLoop, EventLoop};
use winit::platform::pump_events::{EventLoopExtPumpEvents, PumpStatus};
use winit::window::{Window, WindowId};

use crate::event_queue::{HostEvent, EVENT_QUEUE};
use crate::gpu::GpuState;
use crate::input::handle_window_event;
use crate::python::PythonRuntime;
use crate::state::host_state;

thread_local! {
    static NESTED_CTX: Cell<Option<(*mut EventLoop<()>, *mut ProductApp)>> =
        const { Cell::new(None) };
}

fn main() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();
    info!("renpy-host Phase 2 starting");
    // Slice 0: dump INPUT_TRACE on exit / SIGTERM when RENPY_HOST_INPUT_TRACE=1.
    crate::input_trace::install_exit_hooks();
    let _input_trace_dump = crate::input_trace::DumpOnDrop;

    // Parse benchmark flags and set env vars so run_product_pump can see them.
    let args: Vec<String> = std::env::args().collect();
    let mut benchmark_frames: Option<u64> = None;
    let mut benchmark_output: Option<String> = None;
    let mut i = 1;
    while i < args.len() {
        match args[i].as_str() {
            "--benchmark" => {
                benchmark_frames = Some(1800);
                i += 1;
            }
            "--benchmark-frames" => {
                if i + 1 < args.len() {
                    if let Ok(f) = args[i + 1].parse() {
                        benchmark_frames = Some(f);
                    } else {
                        eprintln!("invalid --benchmark-frames value: {}", args[i + 1]);
                        std::process::exit(1);
                    }
                }
                i += 2;
            }
            "--output" => {
                if i + 1 < args.len() {
                    benchmark_output = Some(args[i + 1].clone());
                }
                i += 2;
            }
            _ => {
                i += 1;
            }
        }
    }
    // Pass to product pump via env vars if not already set.
    if let Some(frames) = benchmark_frames {
        if std::env::var("RENPY_HOST_BENCHMARK_FRAMES").is_err() {
            std::env::set_var("RENPY_HOST_BENCHMARK_FRAMES", frames.to_string());
        }
    }
    if let Some(path) = benchmark_output {
        if std::env::var("RENPY_HOST_BENCHMARK_OUTPUT").is_err() {
            std::env::set_var("RENPY_HOST_BENCHMARK_OUTPUT", path);
        }
    }

    let python = match PythonRuntime::bootstrap() {
        Ok(p) => p,
        Err(e) => {
            error!("Python bootstrap failed: {e}");
            std::process::exit(1);
        }
    };

    if std::env::var("RENPY_HOST_PHASE0_SMOKE").ok().as_deref() == Some("1") {
        let secs = std::env::var("RENPY_HOST_SMOKE_SECS")
            .ok()
            .and_then(|s| s.parse::<u64>().ok())
            .unwrap_or(3);
        if let Err(e) = app::run_pump_smoke(python, secs.saturating_mul(1000)) {
            error!("phase0 smoke failed: {e}");
            std::process::exit(1);
        }
        let code = host_state().lock().map(|s| s.exit_code).unwrap_or(0);
        if code != 0 {
            std::process::exit(code);
        }
        return;
    }

    let exit_code = match run_product_pump(python) {
        Ok(code) => code,
        Err(e) => {
            error!("product pump failed: {e}");
            1
        }
    };
    if exit_code != 0 {
        std::process::exit(exit_code);
    }
}

fn run_product_pump(python: PythonRuntime) -> Result<i32, String> {
    let mut event_loop = EventLoop::new().map_err(|e| format!("EventLoop: {e}"))?;
    event_loop.set_control_flow(winit::event_loop::ControlFlow::Poll);

    // Optional argv path → RENPY_HOST_GAME (does not override an existing env).
    // Usage: renpy-host the_question   OR   RENPY_HOST_GAME=... renpy-host
    // Existing RENPY_HOST_GATE sample scripts are unaffected (they ignore game dir).
    if std::env::var_os("RENPY_HOST_GAME").is_none() {
        if let Some(game) = std::env::args().nth(1).filter(|a| !a.starts_with('-')) {
            // Ignore cargo noise / known non-game tokens.
            let skip = matches!(game.as_str(), "run" | "build" | "test" | "check" | "smoke");
            if !skip {
                let path = {
                    let p = std::path::PathBuf::from(&game);
                    if p.is_absolute() {
                        p
                    } else {
                        python.base_dir.join(&game)
                    }
                };
                if path.is_dir() {
                    info!("RENPY_HOST_GAME from argv: {}", path.display());
                    // SAFETY: single-threaded before Python gate starts; set_var is process-global.
                    std::env::set_var("RENPY_HOST_GAME", path);
                }
            }
        }
    }

    let mut app = ProductApp {
        python,
        window: None,
        started: Instant::now(),
        frames: 0,
        should_exit: false,
        python_started: false,
        smoke_deadline: std::env::var("RENPY_HOST_SMOKE_SECS")
            .ok()
            .and_then(|s| s.parse::<u64>().ok())
            .map(|s| Instant::now() + Duration::from_secs(s)),
        max_wall_deadline: std::env::var("RENPY_HOST_MAX_SECS")
            .ok()
            .and_then(|s| s.parse::<u64>().ok())
            .map(|s| Instant::now() + Duration::from_secs(s)),
        gate_mode: std::env::var("RENPY_HOST_GATE").ok(),
        // Benchmark fields
        benchmark_frames: std::env::var("RENPY_HOST_BENCHMARK_FRAMES")
            .ok()
            .and_then(|s| s.parse::<u64>().ok()),
        benchmark_output: std::env::var("RENPY_HOST_BENCHMARK_OUTPUT").ok(),
        benchmark_start: None,
        benchmark_total: Duration::ZERO,
        benchmark_min: Duration::from_secs(u64::MAX),
        benchmark_max: Duration::ZERO,
        benchmark_count: 0,
    };

    // Mechanism 1: wait_until re-enters this EventLoop via nested pump_app_events.
    python::install_nested_pump(nested_pump_once);

    let max_wall = app.max_wall_deadline;
    let mut pump_exit_code: Option<i32> = None;

    loop {
        if app.should_exit || host_state().lock().unwrap().should_exit {
            break;
        }
        if let Some(dl) = max_wall {
            if Instant::now() >= dl {
                info!("max wall clock reached; exiting");
                // Also set host flag so any nested Python still winding down stops.
                host_state().lock().unwrap().should_exit = true;
                break;
            }
        }
        if let Some(dl) = app.smoke_deadline {
            if Instant::now() >= dl {
                info!("smoke deadline reached ({} frames); exiting", app.frames);
                host_state().lock().unwrap().should_exit = true;
                break;
            }
        }

        inject_due_timers();

        let status = {
            NESTED_CTX.with(|c| {
                c.set(Some((
                    &mut event_loop as *mut EventLoop<()>,
                    &mut app as *mut ProductApp,
                )));
            });
            let st = event_loop.pump_app_events(Some(Duration::from_millis(16)), &mut app);
            NESTED_CTX.with(|c| c.set(None));
            st
        };

        match status {
            PumpStatus::Continue => {}
            PumpStatus::Exit(code) => {
                info!("event loop exit code={code}");
                pump_exit_code = Some(code);
                break;
            }
        }
    }

    // Dump input-trace counters on product exit so timeout SIGTERM still flushes
    // if we reach this path (also dumped from request_quit / should_exit sites).
    crate::input_trace::dump_if_enabled();
    info!(
        "product pump done: frames={} elapsed={:?}",
        app.frames,
        app.started.elapsed()
    );

    // Write benchmark JSON if enabled
    if app.benchmark_frames.is_some() {
        let avg = renpy_host::calculate_avg_duration(app.benchmark_total, app.benchmark_count);
        let json = format!(
            r#"{{
  "frames": {},
  "total_time_ms": {},
  "avg_frame_time_ms": {},
  "min_frame_time_ms": {},
  "max_frame_time_ms": {},
  "total_time_sec": {}
}}"#,
            app.benchmark_count,
            app.benchmark_total.as_millis(),
            avg.as_millis(),
            app.benchmark_min.as_millis(),
            app.benchmark_max.as_millis(),
            app.benchmark_total.as_secs_f64()
        );
        let path = app.benchmark_output.as_deref().unwrap_or("benchmark.json");
        match std::fs::write(path, json) {
            Ok(_) => info!("benchmark JSON written to {}", path),
            Err(e) => error!("failed to write benchmark JSON: {}", e),
        }
    }

    let host_code = host_state().lock().map(|s| s.exit_code).unwrap_or(0);
    let pump_code = pump_exit_code.unwrap_or(0);
    let final_code = if host_code != 0 { host_code } else { pump_code };
    Ok(final_code)
}

fn inject_due_timers() {
    let mut state = host_state().lock().unwrap();
    for type_id in state.timers.poll_due() {
        EVENT_QUEUE.push(HostEvent::simple(type_id));
    }
}

fn nested_pump_once(timeout: Duration) {
    inject_due_timers();
    NESTED_CTX.with(|c| {
        if let Some((loop_ptr, app_ptr)) = c.get() {
            // SAFETY: pointers live only while run_product_pump stack frame is active
            // on this thread; wait_until is called from Python on the same thread.
            unsafe {
                let event_loop = &mut *loop_ptr;
                let app = &mut *app_ptr;
                let _ = event_loop.pump_app_events(Some(timeout), app);
                // Product runs nested inside about_to_wait → run_gate → main().
                // Outer-loop smoke/max checks never run until Python returns, so
                // honor deadlines here by setting host should_exit (cooperative
                // unwind — same path as window X / request_quit).
                let now = Instant::now();
                let timed_out = app.smoke_deadline.map(|dl| now >= dl).unwrap_or(false)
                    || app.max_wall_deadline.map(|dl| now >= dl).unwrap_or(false);
                if timed_out && !app.should_exit {
                    if app.smoke_deadline.map(|dl| now >= dl).unwrap_or(false) {
                        info!(
                            "smoke deadline reached during nested product ({} frames); should_exit",
                            app.frames
                        );
                    } else {
                        info!("max wall clock reached during nested product; should_exit");
                    }
                    host_state().lock().unwrap().should_exit = true;
                    app.should_exit = true;
                }
            }
        }
    });
    inject_due_timers();
}

struct ProductApp {
    python: PythonRuntime,
    window: Option<Arc<Window>>,
    started: Instant,
    frames: u64,
    should_exit: bool,
    python_started: bool,
    smoke_deadline: Option<Instant>,
    /// Absolute deadline from RENPY_HOST_MAX_SECS (checked in nested pump too).
    max_wall_deadline: Option<Instant>,
    gate_mode: Option<String>,
    // Benchmark fields
    benchmark_frames: Option<u64>,
    benchmark_output: Option<String>,
    benchmark_start: Option<Instant>,
    benchmark_total: Duration,
    benchmark_min: Duration,
    benchmark_max: Duration,
    benchmark_count: u64,
}

impl ApplicationHandler for ProductApp {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let (w, h, title) = {
            let st = host_state().lock().unwrap();
            (st.width, st.height, st.title.clone())
        };
        let attrs = Window::default_attributes()
            .with_title(title)
            .with_inner_size(winit::dpi::LogicalSize::new(w as f64, h as f64));
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
                        let mut st = host_state().lock().unwrap();
                        st.window = Some(window.clone());
                        st.gpu = Some(gpu);
                    }
                    Err(e) => {
                        error!("GPU init failed: {e}");
                        self.should_exit = true;
                        return;
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
        handle_window_event(&event);
        match event {
            WindowEvent::CloseRequested => {
                // Cooperative quit (Q-A): handle_window_event already queued QUIT.
                // Set host should_exit so wait_until / core event_wait hard-unwind
                // Python (QuitException). Do NOT call event_loop.exit() here — that
                // kills nested pump_app_events while renpy.main.main() is still
                // nested inside about_to_wait → freeze on last frame.
                // Outer loop + about_to_wait exit after run_gate returns.
                host_state().lock().unwrap().should_exit = true;
                self.should_exit = true;
            }
            WindowEvent::Resized(size) => {
                // Reconfigure swapchain. Surface content is invalid after configure,
                // so clear once. Drop product ownership only until the next product
                // end_frame_present — input.rs emits WINDOWRESIZED so interact_core
                // force_redraws and product re-presents (S1: not stuck solid deep blue).
                //
                // Prefer live inner_size when the event reports zero.
                // forced_drawable (programmatic enlarge when WM ignores / flops):
                //   keep force while live chrome is still pre-request OR equals the
                //   forced size (Wayland may briefly report applied then reject);
                //   clear only when live is a third size (maximize / user drag).
                let live = self
                    .window
                    .as_ref()
                    .map(|w| w.inner_size())
                    .filter(|s| s.width > 0 && s.height > 0)
                    .unwrap_or(size);
                let live_w = live.width.max(1);
                let live_h = live.height.max(1);

                let mut st = host_state().lock().unwrap();
                let drawable = match (st.forced_drawable, st.forced_from_chrome) {
                    (Some((fw, fh)), chrome) => {
                        let at_force = live_w == fw && live_h == fh;
                        let at_chrome = chrome
                            .map(|(cw, ch)| live_w == cw && live_h == ch)
                            .unwrap_or(false);
                        if at_force || at_chrome {
                            winit::dpi::PhysicalSize::new(fw, fh)
                        } else {
                            st.forced_drawable = None;
                            st.forced_from_chrome = None;
                            winit::dpi::PhysicalSize::new(live_w, live_h)
                        }
                    }
                    (None, _) => {
                        st.forced_from_chrome = None;
                        winit::dpi::PhysicalSize::new(live_w, live_h)
                    }
                };

                // No-op Resized (same drawable + st size): skip configure/clear/
                // ownership drop so the main menu is not force_redraw-thrashed
                // by compositor echo events at the create size.
                let same_as_st = drawable.width.max(1) == st.width.max(1)
                    && drawable.height.max(1) == st.height.max(1);
                let same_as_gpu = st
                    .gpu
                    .as_ref()
                    .map(|g| {
                        g.config.width == drawable.width.max(1)
                            && g.config.height == drawable.height.max(1)
                    })
                    .unwrap_or(false);
                if same_as_st && same_as_gpu {
                    return;
                }

                if let Some(gpu) = st.gpu.as_mut() {
                    gpu.resize(drawable);
                    if let Err(e) = gpu.render_clear() {
                        match e {
                            wgpu::SurfaceError::Lost | wgpu::SurfaceError::Outdated => {
                                if let Some(w) = self.window.as_ref() {
                                    gpu.resize(w.inner_size());
                                }
                            }
                            other => error!("resize clear error: {other}"),
                        }
                    }
                }
                st.width = drawable.width.max(1);
                st.height = drawable.height.max(1);
                // Idle RedrawRequested must not keep painting clear over a product
                // frame after re-present. Flag drops until product presents again.
                st.last_product_present = false;
                if let Some(w) = self.window.as_ref() {
                    w.request_redraw();
                }
            }
            WindowEvent::RedrawRequested => {
                // Present-ownership design A:
                //   last_product_present → skip idle clear (keep product frame on swapchain)
                //   else → dark-teal clear (gpu.clear 0.08/0.18/0.28)
                // Nested pump uses the same ProductApp, so this path covers both pumps.
                let mut st = host_state().lock().unwrap();
                if st.last_product_present {
                    // Product owns the swapchain; leave flag true. about_to_wait may still
                    // request_redraw — skip depends on the flag, not on stopping redraw.
                    return;
                }
                if let Some(gpu) = st.gpu.as_mut() {
                    // Benchmark: record start time before render
                    let start = std::time::Instant::now();
                    match gpu.render_clear() {
                        Ok(()) => {
                            let elapsed = start.elapsed();
                            self.frames = self.frames.saturating_add(1);
                            st.frames = self.frames;
                            // Defensive: if a clear ever runs while product-owned, count it.
                            // With the skip above this stays 0 in a correct capture cycle.
                            if st.last_product_present {
                                st.idle_clears_after_present =
                                    st.idle_clears_after_present.saturating_add(1);
                            }
                            // Update benchmark stats if enabled
                            if let Some(target_frames) = self.benchmark_frames {
                                if self.benchmark_start.is_none() {
                                    self.benchmark_start = Some(std::time::Instant::now());
                                }
                                self.benchmark_count += 1;
                                self.benchmark_total += elapsed;
                                if elapsed < self.benchmark_min {
                                    self.benchmark_min = elapsed;
                                }
                                if elapsed > self.benchmark_max {
                                    self.benchmark_max = elapsed;
                                }
                                if self.frames >= target_frames {
                                    info!("benchmark: reached target frames ({})", target_frames);
                                    self.should_exit = true;
                                }
                            }
                        }
                        Err(wgpu::SurfaceError::Lost | wgpu::SurfaceError::Outdated) => {
                            if let Some(w) = self.window.as_ref() {
                                gpu.resize(w.inner_size());
                            }
                        }
                        Err(e) => error!("render error: {e}"),
                    }
                }
            }
            _ => {}
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        let has_gpu = host_state().lock().unwrap().gpu.is_some();
        if !self.python_started && self.window.is_some() && has_gpu {
            // Benchmark mode: skip Python gate and just render clear frames.
            if self.benchmark_frames.is_some() {
                self.python_started = true;
                info!("benchmark mode: skipping Python gate, rendering clear frames");
            } else {
                self.python_started = true;
                // Closed product-entry contract (H1):
                //   explicit RENPY_HOST_GATE always wins (stored in gate_mode);
                //   else if RENPY_HOST_GAME is set (env or argv discovery above) → product;
                //   else → smoke. Unknown names still fall through to smoke in python.rs.
                let gate = self.gate_mode.clone().unwrap_or_else(|| {
                    if std::env::var_os("RENPY_HOST_GAME").is_some() {
                        "product".into()
                    } else {
                        "smoke".into()
                    }
                });
                info!("starting Python gate mode={gate}");
                if let Err(e) = self.python.run_gate(&gate) {
                    error!("Python gate failed: {e}");
                    {
                        let mut st = host_state().lock().unwrap();
                        st.should_exit = true;
                        st.exit_code = 1;
                    }
                    self.should_exit = true;
                    event_loop.exit();
                    return;
                }
                if host_state().lock().unwrap().should_exit {
                    self.should_exit = true;
                    event_loop.exit();
                }
            }
        }

        // Feel residual H1: do not busy-wake the swapchain every about_to_wait
        // turn after product already owns the surface. Product interact / movie
        // / renpy_host.request_redraw() still request redraws when needed.
        // Keep requesting while no product present yet so first frames and
        // post-resize re-present are not starved.
        {
            let need_redraw = host_state()
                .lock()
                .map(|s| !s.last_product_present)
                .unwrap_or(true);
            if need_redraw {
                if let Some(w) = self.window.as_ref() {
                    w.request_redraw();
                }
            }
        }
        if self.should_exit || host_state().lock().unwrap().should_exit {
            self.should_exit = true;
            event_loop.exit();
        }
    }
}
