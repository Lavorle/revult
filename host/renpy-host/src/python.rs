//! PyO3 embed + `renpy_host` module (Phase 1).

use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{Duration, Instant};

use log::info;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyModule};
use pyo3::Bound;

use crate::event_queue::{types, EventValue, HostEvent, EVENT_QUEUE};
use crate::pump::{get_ticks_ms, log_wait};
use crate::state::host_state;
use crate::timer::TimerKind;

/// Unify repeated host_state lock + arena delegation boilerplate.
macro_rules! register_host_fns {
    ($module:ident, $($func:ident),* $(,)?) => {
        $( $module.add_function(wrap_pyfunction!($func, &$module)?)?; )*
    };
}
macro_rules! delegate_host {
    ( $( $name:ident : $method:ident : $kind:tt ),* $(,)? ) => {
        $( delegate_host!(@one $name : $method : $kind); )*
    };
    (@one $name:ident : $method:ident : void) => {
        #[pyfunction]
        fn $name(id: u64) { host_state().lock().unwrap().arena.$method(id); }
    };
    (@one $name:ident : $method:ident : bool) => {
        #[pyfunction]
        fn $name(id: u64) -> bool { host_state().lock().map(|s| s.arena.$method(id)).unwrap_or(false) }
    };
    (@one $name:ident : $method:ident : u32) => {
        #[pyfunction]
        fn $name() -> u32 { host_state().lock().map(|s| s.arena.$method()).unwrap_or(0) }
    };
    (@one $name:ident : $method:ident : touch) => {
        #[pyfunction]
        fn $name(id: u64) { if let Ok(mut st) = host_state().lock() { st.arena.$method(id); } }
    };
}

/// Embedded interpreter handle.
pub struct PythonRuntime {
    pub base_dir: PathBuf,
}

impl PythonRuntime {
    pub fn bootstrap() -> Result<Self, String> {
        // Centralized env host lives in `crate::config::HostConfig::from_env()` (config.rs).
        // This pass keeps `discover_base_dir()` as-is to avoid wide refactoring;
        // future pass can replace this call with `HostConfig::from_env().base`.
        let base_dir = discover_base_dir();
        info!("host base dir: {}", base_dir.display());
        let base_str = base_dir
            .to_str()
            .ok_or_else(|| "base dir is not UTF-8".to_string())?
            .to_string();

        // host/python holds host_pygame package (does NOT shadow renpy/).
        let host_python = base_dir.join("host").join("python");
        let host_python_str = host_python.to_string_lossy().into_owned();

        Python::attach(|py| {
            let sys = py.import("sys").map_err(|e| format!("{e}"))?;
            let path = sys.getattr("path").map_err(|e| format!("{e}"))?;
            // Repo root first so `import renpy` is the real package; host/python for host_pygame.
            for p in [&base_str, &host_python_str] {
                let already: bool = path
                    .call_method1("__contains__", (p,))
                    .and_then(|v| v.extract())
                    .unwrap_or(false);
                if !already {
                    path.call_method1("insert", (0, p))
                        .map_err(|e| format!("{e}"))?;
                }
            }

            register_renpy_host(py).map_err(|e| format!("{e}"))?;
            sys.setattr("renpy_host_build", true)
                .map_err(|e| format!("{e}"))?;

            // Install pure-Python pygame shims as renpy.pygame before bootstrap imports it.
            let code = r#"
import sys
import host_pygame
import host_pygame.event
import host_pygame.display
import host_pygame.time
import host_pygame.key
import host_pygame.mouse
import host_pygame.surface
import host_pygame.color
import host_pygame.rect
import host_pygame.locals
import host_pygame.error
import host_pygame.joystick
import host_pygame.controller
import host_pygame.scrap
import host_pygame.power
import host_pygame.iostream
import host_pygame.transform
import host_pygame.draw
import host_pygame.image
import host_pygame.sysfont

# Alias host_pygame as renpy.pygame package tree (SDL-free).
sys.modules["renpy.pygame"] = host_pygame
for name in (
    "event", "display", "time", "key", "mouse", "surface", "color", "rect",
    "locals", "error", "joystick", "controller", "scrap", "power", "iostream",
    "transform", "draw", "image", "sysfont", "constants",
):
    mod = getattr(host_pygame, name, None)
    if mod is None and name == "constants":
        mod = host_pygame.locals
        host_pygame.constants = mod
    if mod is not None:
        sys.modules[f"renpy.pygame.{name}"] = mod

# pygame_time alias used by some imports
sys.modules["renpy.pygame.pygame_time"] = host_pygame.time

# Host renpysound adapter (Phase 4) — prefer over SDL-linked extension when present.
try:
    import renpy.audio.renpysound_host as _rs_host
    import sys as _sys
    _sys.modules["renpy.audio.renpysound"] = _rs_host
    # Also bind as package attribute so `renpy.audio.renpysound` attribute access works
    # even when renpy.audio was already imported before the host module was installed.
    try:
        import renpy.audio as _ra
        _ra.renpysound = _rs_host
    except Exception:
        pass
except Exception:
    pass

# Host pure-Python `_renpy` (software surface ops). SDL `_renpy` is Class B.
try:
    import _renpy_host as _rh
    import sys as _sys2
    _sys2.modules["_renpy"] = _rh
except Exception:
    pass

# Host stubs (install independently — one failure must not block the rest).
import sys as _sys3
try:
    import renpy_text_ftfont_host as _ft
    _sys3.modules["renpy.text.ftfont"] = _ft
except Exception:
    pass
try:
    import renpy_text_hbfont_host as _hb
    _sys3.modules["renpy.text.hbfont"] = _hb
except Exception:
    pass
try:
    import renpy_display_accelerator_host as _acc
    # Pre-seed only. Do NOT import renpy.display here: that pulls renpy.log
    # which installs StdioRedirector before renpy.config exists, so any later
    # print() dies with AttributeError: module 'renpy' has no attribute 'config'.
    # Package attribute bind happens after renpy.import_all() via
    # bootstrap._bind_host_display_accelerator / product gate stubs.
    _sys3.modules["renpy.display.accelerator"] = _acc
except Exception:
    pass
try:
    import renpy_gl2_assimp_host as _assimp
    # Pre-seed so `import renpy.gl2.assimp` / defaultstore resolve without Cython.
    _sys3.modules["renpy.gl2.assimp"] = _assimp
except Exception:
    pass
try:
    # Pure-Python ecsign: OpenSSL 3.x policy blocks ECDSA+SHA1 in stock .so
    # (EVP_DigestSignInit_ex invalid digest). cryptography still allows SHA1.
    # Wire format unchanged: P-256, SHA1, 64-byte raw R||S. Install before
    # renpy.import_all / savetoken.init so product uses this path.
    import renpy_ecsign_host as _ecsign
    _sys3.modules["renpy.ecsign"] = _ecsign
except Exception:
    pass

# Host renpy.__main__ path helpers (renpy.py is not process __main__ under embed).
# renpy.main.main() / bootstrap call renpy.__main__.path_to_common etc.
try:
    import renpy_main_host as _rmh
    _sys3.modules["renpy.__main__"] = _rmh
    # If renpy is already imported, rebind; otherwise renpy/__init__ still
    # sets renpy.__main__ = sys.modules['__main__'] — gates re-install after import.
    if "renpy" in _sys3.modules:
        _sys3.modules["renpy"].__main__ = _rmh
except Exception:
    pass

# NOTE: renpy.uguu host stub is installed AFTER renpy.import_all() (see
# host/python/gates/main.py stage_early_main). Pre-seeding renpy.uguu.uguu
# before import_all makes backup.backup() try to pickle it and can break
# import_all=full. Do not install renpy_uguu_host here.
"#;
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("install host_pygame: {e}"))?;

            info!(
                "CPython {} embedded; renpy_host + host_pygame registered",
                Python::version_str()
            );
            Ok::<(), String>(())
        })?;

        Ok(Self { base_dir })
    }

    pub fn smoke(&self) -> Result<(), String> {
        Python::attach(|py| -> Result<(), String> {
            let renpy_host = py.import("renpy_host").map_err(|e| format!("{e}"))?;
            let t0 = renpy_host
                .call_method0("get_ticks_ms")
                .map_err(|e| format!("{e}"))?
                .extract::<u64>()
                .map_err(|e| format!("{e}"))?;
            renpy_host
                .call_method1("wait_until", (t0,))
                .map_err(|e| format!("{e}"))?;
            let t1 = renpy_host
                .call_method0("get_ticks_ms")
                .map_err(|e| format!("{e}"))?
                .extract::<u64>()
                .map_err(|e| format!("{e}"))?;
            info!("[python-smoke] ticks {t0} -> {t1}; renpy_host OK");
            Ok(())
        })
    }

    /// Run a named Phase 1 gate script (host/python/gates/<name>.py) or built-in.
    pub fn run_gate(&self, name: &str) -> Result<(), String> {
        match name {
            "smoke" => self.smoke(),
            "nested" => self.run_nested_gate(),
            "periodic" => self.run_periodic_gate(),
            "input" => self.run_input_gate(),
            "waitban" => self.run_waitban_gate(),
            "hostimport" => self.run_hostimport_gate(),
            "static" => self.run_static_draw_gate(),
            "text" => self.run_text_gate(),
            "ime" => self.run_ime_gate(),
            "audio" => self.run_audio_gate(),
            "dissolve" => self.run_dissolve_gate(),
            "blur" => self.run_blur_gate(),
            "mask" => self.run_mask_gate(),
            "matrixcolor" => self.run_matrixcolor_gate(),
            "readback" => self.run_readback_gate(),
            "rtt" => self.run_rtt_gate(),
            other => {
                let path = self
                    .base_dir
                    .join("host")
                    .join("python")
                    .join("gates")
                    .join(format!("{other}.py"));
                if path.is_file() {
                    self.run_file(&path)
                } else {
                    info!("unknown gate {other}; running smoke");
                    self.smoke()
                }
            }
        }
    }

    fn run_file(&self, path: &std::path::Path) -> Result<(), String> {
        let src = std::fs::read_to_string(path).map_err(|e| format!("read {path:?}: {e}"))?;
        // Ensure host/python/gates is on sys.path so golden helpers import cleanly.
        // Build the preamble with format!, then append raw src (src may contain `{}`).
        let gates_dir = self
            .base_dir
            .join("host")
            .join("python")
            .join("gates")
            .to_string_lossy()
            .into_owned();
        let base_str = self.base_dir.to_string_lossy().into_owned();
        // Optional product game dir (AC5 bootstrap / the_question). Do not override
        // if the user already set RENPY_HOST_GAME; discovery falls back in gate code.
        let game_env = std::env::var("RENPY_HOST_GAME")
            .ok()
            .filter(|s| !s.is_empty());
        let mut wrapped = String::new();
        wrapped.push_str("import os, sys\n");
        wrapped.push_str(&format!(
            "os.environ.setdefault('RENPY_HOST_BASE', {base_str:?})\n"
        ));
        wrapped.push_str("os.environ.setdefault('RENPY_HOST_BUILD', '1')\n");
        if let Some(ref game) = game_env {
            wrapped.push_str(&format!(
                "os.environ.setdefault('RENPY_HOST_GAME', {game:?})\n"
            ));
        }
        wrapped.push_str(&format!("_gates = {gates_dir:?}\n"));
        wrapped.push_str("if _gates not in sys.path:\n");
        wrapped.push_str("    sys.path.insert(0, _gates)\n");
        wrapped.push_str(&src);
        Python::attach(|py| {
            py.run(
                &std::ffi::CString::new(wrapped).map_err(|e| format!("{e}"))?,
                None,
                None,
            )
            .map_err(|e| {
                // Surface full traceback for bare-product diagnose (Slice 0).
                let mut tb = String::new();
                if let Ok(sys) = py.import("sys") {
                    if let Ok(stderr) = sys.getattr("stderr") {
                        let _ = e.print(py);
                        let _ = stderr;
                    }
                }
                // Also format via traceback module when possible.
                if let Ok(traceback) = py.import("traceback") {
                    if let Ok(lines) = traceback.call_method1(
                        "format_exception",
                        (e.get_type(py), &e.value(py), e.traceback(py)),
                    ) {
                        if let Ok(joined) = lines.call_method0("__iter__") {
                            let _ = joined;
                        }
                        if let Ok(s) = traceback.call_method1(
                            "format_exception",
                            (e.get_type(py), &e.value(py), e.traceback(py)),
                        ) {
                            if let Ok(list) = s.extract::<Vec<String>>() {
                                tb = list.join("");
                            }
                        }
                    }
                }
                if tb.is_empty() {
                    format!("gate script: {e}")
                } else {
                    format!("gate script: {e}\n{tb}")
                }
            })
        })
    }

    /// 1000 nested wait cycles; stack depth reported via sys._getframe.
    fn run_nested_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-nested.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host
import sys

def depth():
    d = 0
    f = sys._getframe()
    while f is not None:
        d += 1
        f = f.f_back
    return d

depths = []
for i in range(1000):
    d0 = depth()
    deadline = renpy_host.get_ticks_ms() + 1
    renpy_host.wait_until(deadline)
    depths.append(depth() - d0)

unique = sorted(set(depths))
msg = f"[nested-gate] depth_deltas={{unique[:10]}} samples={{len(depths)}} min={{min(depths)}} max={{max(depths)}}"
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
if max(depths) - min(depths) > 2:
    raise RuntimeError(f"stack depth unstable: {{unique}}")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("nested gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    fn run_periodic_gate(&self) -> Result<(), String> {
        // Short soak if RENPY_HOST_SMOKE_SECS set; else 60s. Count PERIODIC events.
        let secs: u64 = std::env::var("RENPY_HOST_SMOKE_SECS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(60);
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-periodic.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host

PERIODIC = renpy_host.register_event_type("PERIODIC")
renpy_host.set_timer(PERIODIC, 50)
start = renpy_host.get_ticks_ms()
end = start + {secs} * 1000
count = 0
while renpy_host.get_ticks_ms() < end:
    ev = renpy_host.poll_event()
    if ev is None:
        renpy_host.wait_until(min(end, renpy_host.get_ticks_ms() + 20))
        continue
    if ev["type"] == PERIODIC:
        count += 1

expected = {secs} * 20
lo = int(expected * 0.8)
hi = int(expected * 1.2)
msg = f"[periodic-gate] count={{count}} expected≈{{expected}} range=[{{lo}},{{hi}}]"
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
if not (lo <= count <= hi):
    raise RuntimeError(f"PERIODIC count out of range: {{count}}")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("periodic gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    fn run_input_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-input.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host

renpy_host.inject_key(ord("a"), True)
renpy_host.inject_mouse(100, 200, 1, True)
renpy_host.inject_text("hello")

seen = {{"key": False, "mouse": False, "text": False}}
deadline = renpy_host.get_ticks_ms() + 2000
while renpy_host.get_ticks_ms() < deadline and not all(seen.values()):
    ev = renpy_host.poll_event()
    if ev is None:
        renpy_host.wait_until(renpy_host.get_ticks_ms() + 10)
        continue
    t = ev["type"]
    if t == renpy_host.KEYDOWN:
        seen["key"] = True
    elif t == renpy_host.MOUSEBUTTONDOWN:
        seen["mouse"] = True
    elif t == renpy_host.TEXTINPUT:
        seen["text"] = True

msg = f"[input-gate] seen={{seen}}"
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
if not all(seen.values()):
    raise RuntimeError(f"missing events: {{seen}}")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("input gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Assert pygame.event.wait raises on host shims.
    fn run_waitban_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-waitban.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy.pygame as pygame
import renpy_host

raised = False
try:
    pygame.event.wait()
except RuntimeError as e:
    raised = "forbidden" in str(e) or "wait" in str(e).lower()
msg = f"[waitban-gate] raised={{raised}}"
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
if not raised:
    raise RuntimeError("pygame.event.wait did not raise")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("waitban gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Import host_build flag, WgpuDraw, and event_wait path symbols.
    fn run_hostimport_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-hostimport.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import sys
import renpy
import renpy_host

assert renpy.host_build, "renpy.host_build must be True under host"
from renpy.wgpu.draw import WgpuDraw
d = WgpuDraw()
assert d.init((1280, 720))
d.draw_screen(None, flip=True)
d.quit()

# event_wait host branch exists in source; exercise Mechanism 1 call shape
deadline = renpy_host.get_ticks_ms() + 5
renpy_host.wait_until(deadline)

msg = f"[hostimport-gate] host_build={{renpy.host_build}} wgpu={{d.info['renderer']}}"
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("hostimport gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Phase 2: solid + textured quad via draw_model primary path.
    fn run_static_draw_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-static.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host

# 2x2 RGBA texture (checker)
tex_pixels = bytes([
    255, 0, 0, 255,   0, 255, 0, 255,
    0, 0, 255, 255,   255, 255, 0, 255,
])
tex = renpy_host.create_texture_rgba(2, 2, tex_pixels)

# Fullscreen-ish quad in NDC: two triangles, pos/uv/color
# v0 (-0.5,-0.5)  v1 (0.5,-0.5)  v2 (0.5,0.5)  v3 (-0.5,0.5)
verts = [
    -0.5, -0.5,  0.0, 1.0,  1,1,1,1,
     0.5, -0.5,  1.0, 1.0,  1,1,1,1,
     0.5,  0.5,  1.0, 0.0,  1,1,1,1,
    -0.5,  0.5,  0.0, 0.0,  1,1,1,1,
]
idx = [0, 1, 2, 0, 2, 3]
mesh = renpy_host.create_mesh(verts, idx)
pipe = renpy_host.textured_pipeline()

renpy_host.begin_frame()
renpy_host.draw_model(pipe, mesh, tex)
renpy_host.end_frame_present()

# solid triangle too
sverts = [
    -0.9, -0.9, 0,0,  1,0,0,1,
    -0.6, -0.9, 0,0,  1,0,0,1,
    -0.75, -0.6, 0,0, 1,0,0,1,
]
smesh = renpy_host.create_mesh(sverts, None)
spipe = renpy_host.solid_pipeline()
renpy_host.begin_frame()
renpy_host.draw_model(spipe, smesh, None)
renpy_host.draw_model(pipe, mesh, tex)
renpy_host.end_frame_present()

# Present a few more frames for visual soak
for _ in range(30):
    renpy_host.begin_frame()
    renpy_host.draw_model(spipe, smesh, None)
    renpy_host.draw_model(pipe, mesh, tex)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

msg = f"[static-gate] tex={{tex}} mesh={{mesh}} pipe={{pipe}} solid_pipe={{spipe}} frames={{renpy_host.frame_count()}}"
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("static gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Phase 3: bitmap text via Pillow + textured draw_model.
    fn run_text_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-text.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host
from renpy.wgpu.text import draw_text_screen, render_text_rgba

w, h, rgba = render_text_rgba("Hello renpy-host", size=48)
assert w > 8 and h > 8 and len(rgba) == w * h * 4, (w, h, len(rgba))
info = draw_text_screen("Hello renpy-host", size=48)
for _ in range(20):
    draw_text_screen("Hello renpy-host", size=48)
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

# GLSL textshader must hard-error on host
raised = False
try:
    from renpy.text.shader import register_textshader
    register_textshader("demo", variables="uniform float u_x;")
except Exception as e:
    raised = "not supported" in str(e) or "WGSL" in str(e) or "wgpu" in str(e)

msg = f"[text-gate] size=({{w}},{{h}}) tex={{info['tex']}} textshader_raised={{raised}}"
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
if not raised:
    raise RuntimeError("register_textshader did not raise on host")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("text gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Phase 3: TEXTINPUT / start_text_input smoke (typeable field).
    fn run_ime_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-ime.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host
from renpy.wgpu.text import draw_text_screen

renpy_host.start_text_input()
buf = []
# Simulate typed input (IME commit / TEXTINPUT)
for ch in list("Hello"):
    renpy_host.inject_text(ch)

deadline = renpy_host.get_ticks_ms() + 2000
while renpy_host.get_ticks_ms() < deadline:
    ev = renpy_host.poll_event()
    if ev is None:
        renpy_host.wait_until(renpy_host.get_ticks_ms() + 10)
        continue
    if ev["type"] == renpy_host.TEXTINPUT:
        buf.append(ev.get("text", ""))
    if "".join(buf) == "Hello":
        break

text = "".join(buf)
draw_text_screen(text or "?", size=40)
renpy_host.stop_text_input()

ok = text == "Hello"
msg = "[ime-gate] text=%r ok=%s" % (text, ok)
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
if not ok:
    raise RuntimeError("IME/TEXTINPUT failed: %r" % (text,))
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("ime gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Phase 4: cpal start + beep into ring.
    fn run_audio_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-audio.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host
import time

renpy_host.audio_start()
renpy_host.audio_set_volume(0.3)
renpy_host.audio_beep(440.0, 200, 0.2)
# let callback drain some samples
deadline = renpy_host.get_ticks_ms() + 500
while renpy_host.get_ticks_ms() < deadline:
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

ring = renpy_host.audio_ring_len()
# queue another beep and check ring grew
before = renpy_host.audio_ring_len()
renpy_host.audio_beep(880.0, 100, 0.15)
after = renpy_host.audio_ring_len()
ok = after >= before

# renpysound adapter import smoke
from renpy.audio import renpysound_host as rs
rs.init(48000, True, 1024)
rs.play(0, None, "beep")
rs.set_volume(0, 0.5)
rs.stop(0)
rs.quit()

msg = "[audio-gate] ring=%s after_beep=%s ok=%s" % (ring, after, ok)
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
if not ok:
    raise RuntimeError("audio ring did not accept PCM")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("audio gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    fn run_dissolve_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-dissolve.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host

red = bytes([255, 0, 0, 255] * 4)
blue = bytes([0, 0, 255, 255] * 4)
tex0 = renpy_host.create_texture_rgba(2, 2, red)
tex1 = renpy_host.create_texture_rgba(2, 2, blue)
verts = [
    -0.5, -0.5, 0.0, 1.0, 1, 1, 1, 1,
     0.5, -0.5, 1.0, 1.0, 1, 1, 1, 1,
     0.5,  0.5, 1.0, 0.0, 1, 1, 1, 1,
    -0.5,  0.5, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.dissolve_pipeline()
# amount=0.5 → mix red/blue (GL2 renpy.dissolve)
u = [0.5] + [0.0] * 15
for _ in range(10):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, tex0, tex1, u)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
msg = "[dissolve-gate] tex0=%s tex1=%s pipe=%s amount=0.5 ok=True" % (tex0, tex1, pipe)
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("dissolve gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Phase 5: blur pipeline + uniform blur_log2.
    fn run_blur_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-blur.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host

# checkerboard-ish 4x4
pix = []
for y in range(4):
    for x in range(4):
        if (x + y) & 1:
            pix.extend([255, 255, 255, 255])
        else:
            pix.extend([0, 0, 0, 255])
tex = renpy_host.create_texture_rgba(4, 4, bytes(pix))
verts = [
    -0.8, -0.8, 0.0, 1.0, 1, 1, 1, 1,
     0.8, -0.8, 1.0, 1.0, 1, 1, 1, 1,
     0.8,  0.8, 1.0, 0.0, 1, 1, 1, 1,
    -0.8,  0.8, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.blur_pipeline()
# uniforms[0] = blur_log2
u = [2.0] + [0.0] * 15
for _ in range(6):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, tex, None, u)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
w, h, rgba = renpy_host.read_game_rt_rgba()
msg = "[blur-gate] tex=%s pipe=%s rt=%sx%s bytes=%s ok=True" % (tex, pipe, w, h, len(rgba))
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("blur gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Phase 5: dual-texture mask + alpha_mask.
    fn run_mask_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-mask.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host

src = renpy_host.create_texture_rgba(2, 2, bytes([0, 255, 0, 255] * 4))
# left half opaque white mask.a=1, right half transparent
mask_pix = bytes([255, 255, 255, 255, 0, 0, 0, 0] * 2)
mask = renpy_host.create_texture_rgba(2, 2, mask_pix)
verts = [
    -0.7, -0.7, 0.0, 1.0, 1, 1, 1, 1,
     0.7, -0.7, 1.0, 1.0, 1, 1, 1, 1,
     0.7,  0.7, 1.0, 0.0, 1, 1, 1, 1,
    -0.7,  0.7, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.mask_pipeline()
ap = renpy_host.alpha_mask_pipeline()
# mult=1, offset=0
u = [1.0, 0.0] + [0.0] * 14
for _ in range(4):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, src, mask, u)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
renpy_host.begin_frame()
renpy_host.draw_model(ap, mesh, src, mask)
renpy_host.end_frame_present()
w, h, rgba = renpy_host.read_game_rt_rgba()
msg = "[mask-gate] src=%s mask=%s pipe=%s alpha=%s rt=%sx%s ok=True" % (src, mask, pipe, ap, w, h)
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("mask gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Phase 5: matrixcolor 4x4 uniform.
    fn run_matrixcolor_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-matrixcolor.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host

# solid white source
tex = renpy_host.create_texture_rgba(2, 2, bytes([255, 255, 255, 255] * 4))
verts = [
    -0.5, -0.5, 0.0, 1.0, 1, 1, 1, 1,
     0.5, -0.5, 1.0, 1.0, 1, 1, 1, 1,
     0.5,  0.5, 1.0, 0.0, 1, 1, 1, 1,
    -0.5,  0.5, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.matrixcolor_pipeline()
# identity mat4 column-major, then tint red via scale on R only:
# col0=(1,0,0,0), col1=(0,0,0,0), col2=(0,0,0,0), col3=(0,0,0,1) -> keep R+A
u = [
    1.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
]
for _ in range(4):
    renpy_host.begin_frame()
    renpy_host.draw_model(pipe, mesh, tex, None, u)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
w, h, rgba = renpy_host.read_game_rt_rgba()
msg = "[matrixcolor-gate] tex=%s pipe=%s rt=%sx%s ok=True" % (tex, pipe, w, h)
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("matrixcolor gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Phase 5: read_game_rt_rgba after textured draw.
    fn run_readback_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-readback.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host

red = bytes([255, 0, 0, 255] * 4)
tex = renpy_host.create_texture_rgba(2, 2, red)
verts = [
    -1.0, -1.0, 0.0, 1.0, 1, 0, 0, 1,
     1.0, -1.0, 1.0, 1.0, 1, 0, 0, 1,
     1.0,  1.0, 1.0, 0.0, 1, 0, 0, 1,
    -1.0,  1.0, 0.0, 0.0, 1, 0, 0, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.textured_pipeline()
renpy_host.begin_frame()
renpy_host.draw_model(pipe, mesh, tex)
renpy_host.end_frame_present()
w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0
assert len(rgba) == w * h * 4
# sample center-ish pixel; should be reddish after textured draw + clear blend
cx = (w // 2) * 4
cy = (h // 2)
idx = cy * w * 4 + cx
r, g, b, a = rgba[idx], rgba[idx+1], rgba[idx+2], rgba[idx+3]
ok = r > 100 and a > 0
msg = "[readback-gate] %sx%s center_rgba=(%s,%s,%s,%s) ok=%s" % (w, h, r, g, b, a, ok)
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
if not ok:
    raise RuntimeError(msg)
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("readback gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }

    /// Phase 5: create_render_texture + begin_target / end_target.
    fn run_rtt_gate(&self) -> Result<(), String> {
        let result_path = self
            .base_dir
            .join("host")
            .join("target")
            .join("gate-rtt.txt");
        let result_path_str = result_path.to_string_lossy().into_owned();
        Python::attach(|py| -> Result<(), String> {
            let code = format!(
                r#"
import renpy_host

rtt = renpy_host.create_render_texture(64, 64)
blue = renpy_host.create_texture_rgba(2, 2, bytes([0, 0, 255, 255] * 4))
verts = [
    -1.0, -1.0, 0.0, 1.0, 1, 1, 1, 1,
     1.0, -1.0, 1.0, 1.0, 1, 1, 1, 1,
     1.0,  1.0, 1.0, 0.0, 1, 1, 1, 1,
    -1.0,  1.0, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
pipe = renpy_host.textured_pipeline()
renpy_host.begin_target(rtt)
renpy_host.begin_frame()
renpy_host.draw_model(pipe, mesh, blue)
renpy_host.end_frame_present()
renpy_host.end_target()
w, h, rgba = renpy_host.read_texture_rgba(rtt)
assert (w, h) == (64, 64)
assert len(rgba) == 64 * 64 * 4
# also composite RTT onto swapchain
renpy_host.begin_frame()
renpy_host.draw_model(pipe, mesh, rtt)
renpy_host.end_frame_present()
msg = "[rtt-gate] rtt=%s size=%sx%s bytes=%s ok=True" % (rtt, w, h, len(rgba))
open({result_path_str:?}, "w", encoding="utf-8").write(msg + "\n")
renpy_host.request_quit()
"#
            );
            py.run(&std::ffi::CString::new(code).unwrap(), None, None)
                .map_err(|e| format!("rtt gate: {e}"))?;
            Ok(())
        })?;
        let msg = std::fs::read_to_string(&result_path).unwrap_or_default();
        info!("{msg}");
        Ok(())
    }
}

// Centralized env: `crate::config::HostConfig::from_env()` is the single
// `RENPY_HOST_*` read site. `discover_base_dir()` stays for compatibility
// this pass; future refactor can delegate base/game resolution to HostConfig.
fn discover_base_dir() -> PathBuf {
    if let Ok(v) = std::env::var("RENPY_HOST_BASE") {
        return PathBuf::from(v);
    }
    let mut dir = std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|p| p.to_path_buf()))
        .unwrap_or_else(|| PathBuf::from("."));
    for _ in 0..6 {
        if dir.join("renpy").is_dir() && dir.join("host").join("README.md").is_file() {
            return dir;
        }
        if !dir.pop() {
            break;
        }
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn register_renpy_host(py: Python<'_>) -> PyResult<()> {
    let module = PyModule::new(py, "renpy_host")?;
    module.setattr("__doc__", "Ren'Py host FFI (winit/wgpu/cpal bridge)")?;
    module.add_function(wrap_pyfunction!(wait_until, &module)?)?;
    module.add_function(wrap_pyfunction!(pump_once, &module)?)?;
    module.add_function(wrap_pyfunction!(get_ticks_ms_py, &module)?)?;
    module.add_function(wrap_pyfunction!(poll_event, &module)?)?;
    module.add_function(wrap_pyfunction!(set_timer, &module)?)?;
    module.add_function(wrap_pyfunction!(clear_timer, &module)?)?;
    module.add_function(wrap_pyfunction!(register_event_type, &module)?)?;
    module.add_function(wrap_pyfunction!(request_quit, &module)?)?;
    module.add_function(wrap_pyfunction!(request_quit_with_code, &module)?)?;
    module.add_function(wrap_pyfunction!(should_exit, &module)?)?;
    module.add_function(wrap_pyfunction!(request_redraw, &module)?)?;
    module.add_function(wrap_pyfunction!(window_size, &module)?)?;
    module.add_function(wrap_pyfunction!(request_window_size, &module)?)?;
    module.add_function(wrap_pyfunction!(set_fullscreen, &module)?)?;
    module.add_function(wrap_pyfunction!(is_fullscreen, &module)?)?;
    module.add_function(wrap_pyfunction!(set_window_title, &module)?)?;
    module.add_function(wrap_pyfunction!(start_text_input, &module)?)?;
    module.add_function(wrap_pyfunction!(stop_text_input, &module)?)?;
    module.add_function(wrap_pyfunction!(inject_key, &module)?)?;
    module.add_function(wrap_pyfunction!(inject_mouse, &module)?)?;
    module.add_function(wrap_pyfunction!(inject_text, &module)?)?;
    module.add_function(wrap_pyfunction!(frame_count, &module)?)?;
    // Phase 2 GPU FFI (draw_model primary path)
    module.add_function(wrap_pyfunction!(create_texture_rgba, &module)?)?;
    module.add_function(wrap_pyfunction!(write_texture_rgba, &module)?)?;
    module.add_function(wrap_pyfunction!(create_mesh, &module)?)?;
    register_host_fns!(
        module,
        destroy_texture,
        texture_alive,
        touch_texture,
        sample_texture_count,
        texture_map_len,
        texture_order_len,
        destroy_mesh,
        mesh_alive,
        touch_mesh,
        mesh_map_len,
        mesh_order_len
    );
    module.add_function(wrap_pyfunction!(solid_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(textured_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(dissolve_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(imagedissolve_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(blur_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(matrixcolor_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(create_pipeline_wgsl, &module)?)?;
    module.add_function(wrap_pyfunction!(alpha_mask_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(mask_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(live2d_mask_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(live2d_inverted_mask_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(live2d_colors_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(live2d_flip_pipeline, &module)?)?;
    module.add_function(wrap_pyfunction!(begin_frame, &module)?)?;
    module.add_function(wrap_pyfunction!(draw_model, &module)?)?;
    module.add_function(wrap_pyfunction!(draw_models, &module)?)?;
    module.add_function(wrap_pyfunction!(end_frame_present, &module)?)?;
    module.add_function(wrap_pyfunction!(re_present_last_product, &module)?)?;
    module.add_function(wrap_pyfunction!(has_last_product_cmds, &module)?)?;
    module.add_function(wrap_pyfunction!(frame_depth, &module)?)?;
    module.add_function(wrap_pyfunction!(in_frame, &module)?)?;
    module.add_function(wrap_pyfunction!(reset_frame_state, &module)?)?;
    module.add_function(wrap_pyfunction!(last_product_present, &module)?)?;
    module.add_function(wrap_pyfunction!(product_presents, &module)?)?;
    module.add_function(wrap_pyfunction!(idle_clears_after_present, &module)?)?;
    module.add_function(wrap_pyfunction!(reset_present_stats, &module)?)?;
    module.add_function(wrap_pyfunction!(take_inter_present_gaps_ms, &module)?)?;
    module.add_function(wrap_pyfunction!(inter_present_gaps_ms, &module)?)?;
    module.add_function(wrap_pyfunction!(create_render_texture, &module)?)?;
    module.add_function(wrap_pyfunction!(begin_target, &module)?)?;
    module.add_function(wrap_pyfunction!(end_target, &module)?)?;
    module.add_function(wrap_pyfunction!(read_game_rt_rgba, &module)?)?;
    module.add_function(wrap_pyfunction!(read_texture_rgba, &module)?)?;
    // Phase 4 audio
    module.add_function(wrap_pyfunction!(audio_start, &module)?)?;
    module.add_function(wrap_pyfunction!(audio_stop, &module)?)?;
    module.add_function(wrap_pyfunction!(audio_set_volume, &module)?)?;
    module.add_function(wrap_pyfunction!(audio_queue_pcm_f32, &module)?)?;
    module.add_function(wrap_pyfunction!(audio_beep, &module)?)?;
    module.add_function(wrap_pyfunction!(audio_ring_len, &module)?)?;
    // Phase 6 video A/V clock
    module.add_function(wrap_pyfunction!(video_clock_start, &module)?)?;
    module.add_function(wrap_pyfunction!(video_clock_stop, &module)?)?;
    module.add_function(wrap_pyfunction!(video_clock_pos, &module)?)?;
    module.add_function(wrap_pyfunction!(video_clock_set_pos, &module)?)?;
    module.add_function(wrap_pyfunction!(video_clock_pause, &module)?)?;
    module.add_function(wrap_pyfunction!(video_clock_unpause, &module)?)?;
    module.add("PHASE", 6)?;
    module.add("KEYDOWN", types::KEYDOWN)?;
    module.add("KEYUP", types::KEYUP)?;
    module.add("MOUSEBUTTONDOWN", types::MOUSEBUTTONDOWN)?;
    module.add("MOUSEBUTTONUP", types::MOUSEBUTTONUP)?;
    module.add("MOUSEMOTION", types::MOUSEMOTION)?;
    module.add("TEXTINPUT", types::TEXTINPUT)?;
    module.add("TEXTEDITING", types::TEXTEDITING)?;
    module.add("QUIT", types::QUIT)?;
    module.add("NOEVENT", types::NOEVENT)?;
    module.add("WINDOWEVENT", types::WINDOWEVENT)?;
    module.add("WINDOWRESIZED", types::WINDOWRESIZED)?;
    module.add("WINDOWEXPOSED", types::WINDOWEXPOSED)?;

    let sys = py.import("sys")?;
    sys.getattr("modules")?.set_item("renpy_host", &module)?;
    Ok(())
}

#[pyfunction]
#[pyo3(name = "get_ticks_ms")]
fn get_ticks_ms_py() -> u64 {
    get_ticks_ms()
}

#[pyfunction]
fn frame_count() -> u64 {
    host_state().lock().unwrap().frames
}

#[pyfunction]
fn request_quit() {
    {
        let mut st = host_state().lock().unwrap();
        st.should_exit = true;
        st.exit_code = 0;
    }
    // Wake nested wait_until / product event_wait so HostStop/watchdog can unwind.
    EVENT_QUEUE.push(HostEvent::simple(types::QUIT));
    crate::input_trace::dump_if_enabled();
}

#[pyfunction]
fn request_quit_with_code(code: i32) {
    {
        let mut st = host_state().lock().unwrap();
        st.should_exit = true;
        st.exit_code = code;
    }
    EVENT_QUEUE.push(HostEvent::simple(types::QUIT));
    crate::input_trace::dump_if_enabled();
}

#[pyfunction]
fn should_exit() -> bool {
    host_state().lock().map(|s| s.should_exit).unwrap_or(false)
}

#[pyfunction]
fn request_redraw() {
    if let Some(w) = host_state().lock().unwrap().window.as_ref() {
        w.request_redraw();
    }
}

/// Physical drawable size SSOT for Python (`WgpuDraw.physical_size`, scale).
///
/// Order:
/// 1. `forced_drawable` if set and live chrome is still pre-request or at force
///    (programmatic resize; WM may ignore / flop under Wayland)
/// 2. live `window.inner_size()` (maximize / user drag) — clears force when live
///    is a third size
/// 3. cached `st.width/height`
#[pyfunction]
fn window_size() -> (u32, u32) {
    let mut st = host_state().lock().unwrap();
    if let Some(window) = st.window.as_ref() {
        let live = window.inner_size();
        let live_w = live.width.max(1);
        let live_h = live.height.max(1);
        if let Some((fw, fh)) = st.forced_drawable {
            let at_force = live_w == fw && live_h == fh;
            let at_chrome = st
                .forced_from_chrome
                .map(|(cw, ch)| live_w == cw && live_h == ch)
                .unwrap_or(false);
            if at_force || at_chrome {
                st.width = fw;
                st.height = fh;
                return (fw, fh);
            }
            // Live is a real third size (maximize / user drag) — clear force.
            st.forced_drawable = None;
            st.forced_from_chrome = None;
        }
        st.width = live_w;
        st.height = live_h;
        (live_w, live_h)
    } else if let Some((w, h)) = st.forced_drawable {
        st.width = w.max(1);
        st.height = h.max(1);
        (st.width, st.height)
    } else {
        (st.width.max(1), st.height.max(1))
    }
}

/// Apply Resized-equivalent host side-effects for a known physical size.
/// Used when Wayland returns `Some` from `request_inner_size` (no Resized event)
/// and when the WM ignores the request (forced drawable for hermetic probes).
fn apply_drawable_size(
    st: &mut crate::state::HostState,
    window: &std::sync::Arc<winit::window::Window>,
    size: winit::dpi::PhysicalSize<u32>,
    forced_from_chrome: Option<(u32, u32)>,
) {
    let size = winit::dpi::PhysicalSize::new(size.width.max(1), size.height.max(1));
    if let Some(gpu) = st.gpu.as_mut() {
        gpu.resize(size);
        if let Err(e) = gpu.render_clear() {
            match e {
                wgpu::SurfaceError::Lost | wgpu::SurfaceError::Outdated => {
                    gpu.resize(window.inner_size());
                }
                other => log::error!("apply_drawable_size clear error: {other}"),
            }
        }
    }
    st.width = size.width;
    st.height = size.height;
    match forced_from_chrome {
        Some(chrome) => {
            st.forced_drawable = Some((size.width, size.height));
            st.forced_from_chrome = Some((chrome.0.max(1), chrome.1.max(1)));
        }
        None => {
            st.forced_drawable = None;
            st.forced_from_chrome = None;
        }
    }
    st.last_product_present = false;
    EVENT_QUEUE.push(HostEvent::with(
        types::WINDOWRESIZED,
        vec![
            ("x".into(), EventValue::Int(size.width as i64)),
            ("y".into(), EventValue::Int(size.height as i64)),
            ("w".into(), EventValue::Int(size.width as i64)),
            ("h".into(), EventValue::Int(size.height as i64)),
        ],
    ));
    window.request_redraw();
}

/// Request a physical window resize (winit). Used by residual probes for S1/RE.
///
/// Platform notes (winit 0.30):
/// - **X11**: usually returns `None` and delivers `WindowEvent::Resized` later.
/// - **Wayland**: often returns `Some(applied)` after updating local state, but the
///   compositor may still reconfigure back to the old size. A bare apply without
///   force is wiped by that echo → probes stay false-green at 1280×720.
///
/// Strategy: always apply the **requested** size as host drawable SSOT, with
/// `forced_from_chrome` = pre-request live chrome. Resized clears the force only
/// when live chrome equals the target (WM accepted) or leaves the pre-request
/// chrome (maximize / user drag). Same-chrome compositor echoes keep the force.
#[pyfunction]
fn request_window_size(w: u32, h: u32) {
    use winit::dpi::PhysicalSize;

    let target = PhysicalSize::new(w.max(1), h.max(1));
    let mut st = host_state().lock().unwrap();
    let Some(window) = st.window.as_ref().cloned() else {
        st.width = target.width;
        st.height = target.height;
        st.forced_drawable = Some((target.width, target.height));
        st.forced_from_chrome = None;
        return;
    };

    // Client resize needs a floating, resizable surface on Wayland.
    window.set_resizable(true);
    window.set_maximized(false);

    // Capture chrome *before* request_inner_size — Wayland may update local
    // inner_size immediately even when the compositor later rejects.
    let before = window.inner_size();
    let chrome = (before.width.max(1), before.height.max(1));
    let _applied = window.request_inner_size(target);

    // Already at target (including prior force).
    if target.width == st.width && target.height == st.height {
        window.request_redraw();
        return;
    }

    // Apply requested drawable immediately (S1 side-effects + WINDOWRESIZED).
    // Always force against pre-request chrome so a compositor reject echo at
    // `chrome` cannot wipe the enlarge. Live maximize still wins via Resized.
    apply_drawable_size(&mut st, &window, target, Some(chrome));
}

/// Toggle borderless fullscreen via winit (GL2 ``WINDOW_FULLSCREEN_DESKTOP``).
///
/// Product preferences (HuangmeiC image_config) flip ``preferences.fullscreen``
/// and core.interact calls ``draw.resize()``. Without this, fullscreen is a no-op.
///
/// Borderless (not exclusive) keeps compositor multi-monitor behaviour and avoids
/// mode-switch blackouts. After the call, clear ``forced_drawable`` so the next
/// Resized / ``window_size`` reads live chrome.
#[pyfunction]
fn set_fullscreen(enabled: bool) {
    use winit::window::Fullscreen;

    let mut st = host_state().lock().unwrap();
    let Some(window) = st.window.as_ref().cloned() else {
        return;
    };

    if enabled {
        // Prefer the monitor the window is already on.
        let monitor = window
            .current_monitor()
            .or_else(|| window.primary_monitor())
            .or_else(|| window.available_monitors().next());
        window.set_fullscreen(Some(Fullscreen::Borderless(monitor)));
    } else {
        window.set_fullscreen(None);
    }

    // Live chrome after toggle is SSOT — drop any programmatic force so
    // window_size / update see the new size from Resized or inner_size.
    st.forced_drawable = None;
    st.forced_from_chrome = None;
    st.last_product_present = false;
    window.request_redraw();
}

/// True when winit reports a fullscreen state (borderless or exclusive).
#[pyfunction]
fn is_fullscreen() -> bool {
    let st = host_state().lock().unwrap();
    st.window
        .as_ref()
        .map(|w| w.fullscreen().is_some())
        .unwrap_or(false)
}

#[pyfunction]
fn set_window_title(title: String) {
    let mut st = host_state().lock().unwrap();
    st.title = title.clone();
    if let Some(w) = st.window.as_ref() {
        w.set_title(&title);
    }
}

#[pyfunction]
fn start_text_input() {
    host_state().lock().unwrap().text_input_active = true;
    // winit IME enable when window exists
    if let Some(w) = host_state().lock().unwrap().window.as_ref() {
        w.set_ime_allowed(true);
    }
}

#[pyfunction]
fn stop_text_input() {
    host_state().lock().unwrap().text_input_active = false;
    if let Some(w) = host_state().lock().unwrap().window.as_ref() {
        w.set_ime_allowed(false);
    }
}

#[pyfunction]
fn register_event_type(name: String) -> u32 {
    let mut st = host_state().lock().unwrap();
    if let Some(id) = st.custom_types.get(&name) {
        return *id;
    }
    let id = st.next_custom_type;
    st.next_custom_type = st.next_custom_type.saturating_add(1);
    st.custom_types.insert(name, id);
    id
}

#[pyfunction]
#[pyo3(signature = (event_type, interval_ms, once=false))]
fn set_timer(event_type: u32, interval_ms: u64, once: bool) {
    let kind = TimerKind::Custom(event_type);
    // pygame.time.set_timer(..., once=True) → one-shot (Ren'Py TIMEEVENT/REDRAW).
    let repeating = !once;
    host_state()
        .lock()
        .unwrap()
        .timers
        .set_timer(event_type, interval_ms, kind, repeating);
}

#[pyfunction]
fn clear_timer(event_type: u32) {
    host_state()
        .lock()
        .unwrap()
        .timers
        .clear_event_type(event_type);
}

#[pyfunction]
fn poll_event(py: Python<'_>) -> PyResult<Option<Bound<'_, PyDict>>> {
    match EVENT_QUEUE.poll() {
        None => Ok(None),
        Some(ev) => {
            crate::input_trace::count_poll_event_nonempty();
            Ok(Some(host_event_to_py(py, ev)?))
        }
    }
}

fn host_event_to_py(py: Python<'_>, ev: HostEvent) -> PyResult<Bound<'_, PyDict>> {
    let dict = PyDict::new(py);
    dict.set_item("type", ev.type_id)?;
    for (k, v) in ev.dict {
        match v {
            EventValue::Int(i) => dict.set_item(k, i)?,
            EventValue::Float(f) => dict.set_item(k, f)?,
            EventValue::Bool(b) => dict.set_item(k, b)?,
            EventValue::Str(s) => dict.set_item(k, s)?,
            EventValue::None => dict.set_item(k, py.None())?,
        }
    }
    Ok(dict)
}

/// Mechanism 1 nested wait.
#[pyfunction]
fn wait_until(py: Python<'_>, deadline_ms: u64) -> PyResult<()> {
    crate::input_trace::count_wait_until();
    let start = Instant::now();

    loop {
        // Watchdog / request_quit must interrupt nested product waits.
        if host_state().lock().map(|s| s.should_exit).unwrap_or(false) {
            break;
        }
        // Timer firings are injected by nested_pump_once / outer loop.
        // Phase 0 dual-signal: early-exit with non-empty queue (class B probe).
        if EVENT_QUEUE.len() > 0 {
            crate::input_trace::count_wait_until_early_exit();
            break;
        }
        let now = get_ticks_ms();
        if now >= deadline_ms {
            break;
        }
        let remaining = Duration::from_millis(deadline_ms - now);
        let slice = remaining.min(Duration::from_millis(16));

        // Nested pump with GIL released (OS wait must not hold GIL).
        py.detach(|| {
            let pumped = try_nested_pump(slice);
            if !pumped {
                // No nested context (e.g. unit test) — sleep.
                std::thread::sleep(slice);
            }
        });
    }

    log_wait(deadline_ms, start.elapsed());
    Ok(())
}

/// Force one nested `pump_app_events` turn regardless of EVENT_QUEUE occupancy.
///
/// Slice 1 (D2 H1): product interact busy-polls via `event_poll` while
/// PERIODIC/TIMEEVENT keep the queue non-empty, so `wait_until` early-exits
/// without pumping and live winit events starve. Busy-poll side-pumps call this
/// instead of `wait_until` so winit still gets a turn.
#[pyfunction]
#[pyo3(signature = (timeout_ms=16))]
fn pump_once(py: Python<'_>, timeout_ms: u64) -> PyResult<bool> {
    if host_state().lock().map(|s| s.should_exit).unwrap_or(false) {
        return Ok(false);
    }
    let slice = Duration::from_millis(timeout_ms.min(16));
    let pumped = py.detach(|| try_nested_pump(slice));
    Ok(pumped)
}

#[pyfunction]
#[pyo3(signature = (key, pressed, unicode=None))]
fn inject_key(key: u32, pressed: bool, unicode: Option<String>) {
    let type_id = if pressed {
        types::KEYDOWN
    } else {
        types::KEYUP
    };
    let unicode = unicode.unwrap_or_default();
    EVENT_QUEUE.push(HostEvent::with(
        type_id,
        vec![
            ("key".into(), EventValue::Int(key as i64)),
            ("scancode".into(), EventValue::Int(0)),
            ("mod".into(), EventValue::Int(0)),
            ("repeat".into(), EventValue::Bool(false)),
            ("unicode".into(), EventValue::Str(unicode)),
        ],
    ));
}

/// Inject mouse motion (and optional button) at physical window coords.
///
/// Defaults: `button=0`, `pressed=false` → **motion only** (no synthetic
/// MOUSEBUTTON*). Callers that need a click pass `button=1, pressed=true/false`.
/// Motion-only is required for hover probes (ColorizeMatrix / hover_color) so a
/// zero-button UP does not steal focus or activate actions.
#[pyfunction]
#[pyo3(signature = (x, y, button=0, pressed=false))]
fn inject_mouse(x: i64, y: i64, button: i64, pressed: bool) {
    // Motion first so product focus / hover tracks the click point.
    // Emit mod=0 for parity with live winit mouse path (map_event may read it).
    EVENT_QUEUE.push(HostEvent::with(
        types::MOUSEMOTION,
        vec![
            ("pos".into(), EventValue::Str(format!("{x},{y}"))),
            ("x".into(), EventValue::Int(x)),
            ("y".into(), EventValue::Int(y)),
            ("rel".into(), EventValue::Str("0,0".into())),
            (
                "buttons".into(),
                EventValue::Int(if pressed { 1 } else { 0 }),
            ),
            ("mod".into(), EventValue::Int(0)),
        ],
    ));
    // button==0: motion-only (hover / set_pos). Real buttons are 1..N.
    if button == 0 {
        return;
    }
    let type_id = if pressed {
        types::MOUSEBUTTONDOWN
    } else {
        types::MOUSEBUTTONUP
    };
    EVENT_QUEUE.push(HostEvent::with(
        type_id,
        vec![
            ("button".into(), EventValue::Int(button)),
            ("pos".into(), EventValue::Str(format!("{x},{y}"))),
            ("x".into(), EventValue::Int(x)),
            ("y".into(), EventValue::Int(y)),
            ("mod".into(), EventValue::Int(0)),
        ],
    ));
}

#[pyfunction]
fn inject_text(text: String) {
    EVENT_QUEUE.push(HostEvent::with(
        types::TEXTINPUT,
        vec![("text".into(), EventValue::Str(text))],
    ));
}

// --- Phase 2 GPU FFI -------------------------------------------------------

#[pyfunction]
fn create_texture_rgba(width: u32, height: u32, rgba: Vec<u8>) -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    // Align arena sample format with the live surface before first upload.
    st.arena.set_color_format(gpu.surface_format);
    let result = st
        .arena
        .create_texture_rgba(&gpu.device, &gpu.queue, width, height, &rgba);
    st.gpu = Some(gpu);
    result.map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

/// Update an existing texture in place (video frame path). `rgba` must be
/// width*height*4 tight RGBA matching the texture created via create_texture_rgba.
#[pyfunction]
fn write_texture_rgba(id: u64, rgba: Vec<u8>) -> PyResult<()> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    let result = st.arena.write_texture_rgba(&gpu.queue, id, &rgba);
    st.gpu = Some(gpu);
    result.map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

delegate_host! {
    destroy_texture: destroy_texture: void,
    texture_alive: texture_alive: bool,
    touch_texture: touch_texture: touch,
    sample_texture_count: sample_texture_count: u32,
    texture_map_len: texture_map_len: u32,
    texture_order_len: texture_order_len: u32,
}

#[pyfunction]
fn create_mesh(vertices: Vec<f32>, indices: Option<Vec<u32>>) -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    let result = st
        .arena
        .create_mesh(&gpu.device, &vertices, indices.as_deref());
    st.gpu = Some(gpu);
    result.map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

delegate_host! {
    destroy_mesh: destroy_mesh: void,
    mesh_alive: mesh_alive: bool,
    touch_mesh: touch_mesh: touch,
    mesh_map_len: mesh_map_len: u32,
    mesh_order_len: mesh_order_len: u32,
}

#[pyfunction]
fn solid_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .solid_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("solid pipeline missing"))
}

#[pyfunction]
fn textured_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .textured_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("textured pipeline missing"))
}

#[pyfunction]
fn dissolve_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .dissolve_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("dissolve pipeline missing"))
}

#[pyfunction]
fn imagedissolve_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .imagedissolve_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("imagedissolve pipeline missing"))
}

#[pyfunction]
fn blur_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .blur_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("blur pipeline missing"))
}

#[pyfunction]
fn matrixcolor_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .matrixcolor_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("matrixcolor pipeline missing"))
}

/// Create (or cache-hit) a render pipeline from composed WGSL.
/// `key` is the cache key (e.g. "composed:abc123"); premul blend and vs_main/fs_main stay frozen.
#[pyfunction]
fn create_pipeline_wgsl(
    key: String,
    wgsl: String,
    tex_count: u8,
    has_uniforms: bool,
) -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    let result = st
        .arena
        .create_pipeline_from_wgsl(&gpu.device, &key, &wgsl, tex_count, has_uniforms)
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e));
    st.gpu = Some(gpu);
    result
}

#[pyfunction]
fn alpha_mask_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .alpha_mask_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("alpha_mask pipeline missing"))
}

#[pyfunction]
fn mask_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .mask_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("mask pipeline missing"))
}

#[pyfunction]
fn live2d_mask_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .live2d_mask_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("live2d.mask pipeline missing"))
}

#[pyfunction]
fn live2d_inverted_mask_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena.live2d_inverted_mask_pipeline.ok_or_else(|| {
        pyo3::exceptions::PyRuntimeError::new_err("live2d.inverted_mask pipeline missing")
    })
}

#[pyfunction]
fn live2d_colors_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena
        .live2d_colors_pipeline
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("live2d.colors pipeline missing"))
}

#[pyfunction]
fn live2d_flip_pipeline() -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    st.arena.ensure_builtin_pipelines(&gpu.device);
    st.gpu = Some(gpu);
    st.arena.live2d_flip_pipeline.ok_or_else(|| {
        pyo3::exceptions::PyRuntimeError::new_err("live2d.flip_texture pipeline missing")
    })
}

#[pyfunction]
fn begin_frame() {
    host_state().lock().unwrap().arena.begin_frame();
}

/// Nested begin_frame depth (0 = idle). Product draw uses this to drain stuck nests.
#[pyfunction]
fn frame_depth() -> u32 {
    host_state()
        .lock()
        .map(|s| s.arena.frame_depth())
        .unwrap_or(0)
}

/// True while a begin_frame is open (including nested RTT bakes).
#[pyfunction]
fn in_frame() -> bool {
    host_state()
        .lock()
        .map(|s| s.arena.in_frame)
        .unwrap_or(false)
}

/// Drop stuck nested frames / active_target without presenting (no-op if clean).
#[pyfunction]
fn reset_frame_state() {
    if let Ok(mut st) = host_state().lock() {
        st.arena.reset_frame_state();
    }
}

#[pyfunction]
#[pyo3(signature = (pipeline, mesh, texture=None, texture1=None, uniforms=None, texture2=None))]
fn draw_model(
    pipeline: u64,
    mesh: u64,
    texture: Option<u64>,
    texture1: Option<u64>,
    uniforms: Option<Vec<f32>>,
    texture2: Option<u64>,
) {
    // Signature keeps uniforms before texture2 so existing 5-arg call sites
    // (pipeline, mesh, tex, tex1, uniforms) remain valid.
    let u = uniforms_to_arr(uniforms);
    host_state()
        .lock()
        .unwrap()
        .arena
        .draw_model(pipeline, mesh, texture, texture1, texture2, u);
}

fn uniforms_to_arr(uniforms: Option<Vec<f32>>) -> Option<[f32; 16]> {
    uniforms.map(|v| {
        let mut arr = [0.0f32; 16];
        for (i, x) in v.into_iter().take(16).enumerate() {
            arr[i] = x;
        }
        arr
    })
}

/// Batch draw_model calls under a single host_state lock.
/// Continuous prefs/main_menu can emit hundreds of draw_model FFI calls per
/// present; each took its own Mutex lock + PyO3 boundary. One batch per frame
/// is a WP3 residual lever for host inter-present p99.
///
/// Each item is (pipeline, mesh, texture, texture1, uniforms, texture2).
#[pyfunction]
fn draw_models(
    cmds: Vec<(
        u64,
        u64,
        Option<u64>,
        Option<u64>,
        Option<Vec<f32>>,
        Option<u64>,
    )>,
) -> PyResult<()> {
    let mut st = host_state().lock().unwrap();
    for (pipeline, mesh, texture, texture1, uniforms, texture2) in cmds {
        let u = uniforms_to_arr(uniforms);
        st.arena
            .draw_model(pipeline, mesh, texture, texture1, texture2, u);
    }
    Ok(())
}

#[pyfunction]
fn end_frame_present() -> PyResult<()> {
    let mut st = host_state().lock().unwrap();
    let mut gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    let result = st.arena.end_frame_present(&mut gpu);
    st.gpu = Some(gpu);
    st.frames = st.frames.saturating_add(1);
    match result {
        Ok(presented_swapchain) => {
            // Only swapchain presents take product ownership (not RTT-only active_target).
            if presented_swapchain {
                st.last_product_present = true;
                st.product_presents = st.product_presents.saturating_add(1);
                // Feel AC-F SSOT: record wall-clock inter-product-present gaps
                // on the host present path (not probe-thread sleep sampling).
                let now = Instant::now();
                if let Some(prev) = st.last_product_present_at {
                    let gap_ms = prev.elapsed().as_secs_f64() * 1000.0;
                    // Clamp pathological first gaps / long pauses out of ring
                    // only when > 5s so freeze stalls still remain visible.
                    if gap_ms.is_finite() && gap_ms >= 0.0 && gap_ms < 5000.0 {
                        st.inter_present_gaps_ms.push(gap_ms as f32);
                        const CAP: usize = 512;
                        if st.inter_present_gaps_ms.len() > CAP {
                            let drop = st.inter_present_gaps_ms.len() - CAP;
                            st.inter_present_gaps_ms.drain(0..drop);
                        }
                    }
                }
                st.last_product_present_at = Some(now);
            }
            Ok(())
        }
        Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
    }
}

/// Re-present last product cmds (Movie-only / cadence fast path).
/// Returns True when the swapchain was presented (gap accounting applied).
#[pyfunction]
fn re_present_last_product() -> PyResult<bool> {
    let mut st = host_state().lock().unwrap();
    let mut gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    let result = st.arena.re_present_last_product(&mut gpu);
    st.gpu = Some(gpu);
    st.frames = st.frames.saturating_add(1);
    match result {
        Ok(presented_swapchain) => {
            if presented_swapchain {
                st.last_product_present = true;
                st.product_presents = st.product_presents.saturating_add(1);
                let now = Instant::now();
                if let Some(prev) = st.last_product_present_at {
                    let gap_ms = prev.elapsed().as_secs_f64() * 1000.0;
                    if gap_ms.is_finite() && gap_ms >= 0.0 && gap_ms < 5000.0 {
                        st.inter_present_gaps_ms.push(gap_ms as f32);
                        const CAP: usize = 512;
                        if st.inter_present_gaps_ms.len() > CAP {
                            let drop = st.inter_present_gaps_ms.len() - CAP;
                            st.inter_present_gaps_ms.drain(0..drop);
                        }
                    }
                }
                st.last_product_present_at = Some(now);
            }
            Ok(presented_swapchain)
        }
        Err(e) => Err(pyo3::exceptions::PyRuntimeError::new_err(e)),
    }
}

/// True when last successful product DrawCmd list can be re-presented.
#[pyfunction]
fn has_last_product_cmds() -> bool {
    host_state()
        .lock()
        .map(|s| s.arena.has_last_product_cmds())
        .unwrap_or(false)
}

/// True if the last successful product frame was presented to the swapchain.
/// Capture-cycle gate: expect true after product present through readback.
#[pyfunction]
fn last_product_present() -> bool {
    host_state()
        .lock()
        .map(|s| s.last_product_present)
        .unwrap_or(false)
}

/// Count of successful swapchain product presents (not RTT-only).
#[pyfunction]
fn product_presents() -> u64 {
    host_state().lock().map(|s| s.product_presents).unwrap_or(0)
}

/// Idle clears that ran while last_product_present was true (should be 0 in a
/// correct capture cycle when present-ownership skip is working).
#[pyfunction]
fn idle_clears_after_present() -> u64 {
    host_state()
        .lock()
        .map(|s| s.idle_clears_after_present)
        .unwrap_or(0)
}

/// Zero present-ownership counters so a gate can start a clean capture cycle.
/// Does not clear last_product_present (ownership flag is independent of stats).
#[pyfunction]
fn reset_present_stats() {
    if let Ok(mut st) = host_state().lock() {
        st.product_presents = 0;
        st.idle_clears_after_present = 0;
        st.last_product_present_at = None;
        st.inter_present_gaps_ms.clear();
    }
}

/// Snapshot and clear recent inter-product-present gaps (milliseconds).
/// Feel AC-F SSOT for continuous p99; host records on each swapchain present.
/// Also clears last_product_present_at so the next present starts a clean
/// gap window without zeroing product_presents counters.
#[pyfunction]
fn take_inter_present_gaps_ms() -> Vec<f32> {
    host_state()
        .lock()
        .map(|mut s| {
            s.last_product_present_at = None;
            std::mem::take(&mut s.inter_present_gaps_ms)
        })
        .unwrap_or_default()
}

/// Peek recent inter-product-present gaps without clearing.
#[pyfunction]
fn inter_present_gaps_ms() -> Vec<f32> {
    host_state()
        .lock()
        .map(|s| s.inter_present_gaps_ms.clone())
        .unwrap_or_default()
}

#[pyfunction]
fn create_render_texture(width: u32, height: u32) -> PyResult<u64> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    st.arena.set_color_format(gpu.surface_format);
    let result = st.arena.create_render_texture(&gpu.device, width, height);
    st.gpu = Some(gpu);
    result.map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pyfunction]
fn begin_target(handle: u64) -> PyResult<()> {
    host_state()
        .lock()
        .unwrap()
        .arena
        .begin_target(handle)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pyfunction]
fn end_target() {
    host_state().lock().unwrap().arena.end_target();
}

#[pyfunction]
fn read_game_rt_rgba() -> PyResult<(u32, u32, Vec<u8>)> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    let result = st.arena.read_game_rt_rgba(&gpu);
    st.gpu = Some(gpu);
    result.map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pyfunction]
fn read_texture_rgba(handle: u64) -> PyResult<(u32, u32, Vec<u8>)> {
    let mut st = host_state().lock().unwrap();
    let gpu = st
        .gpu
        .take()
        .ok_or_else(|| pyo3::exceptions::PyRuntimeError::new_err("gpu not ready"))?;
    let result = st.arena.read_texture_rgba(&gpu, handle);
    st.gpu = Some(gpu);
    result.map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

// --- Phase 4 audio FFI -----------------------------------------------------

#[pyfunction]
fn audio_start() -> PyResult<()> {
    host_state()
        .lock()
        .unwrap()
        .audio
        .start()
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pyfunction]
fn audio_stop() {
    host_state().lock().unwrap().audio.stop();
}

#[pyfunction]
fn audio_set_volume(v: f32) {
    host_state().lock().unwrap().audio.set_volume(v);
}

#[pyfunction]
fn audio_queue_pcm_f32(samples: Vec<f32>) {
    host_state().lock().unwrap().audio.queue_pcm_f32(&samples);
}

#[pyfunction]
fn audio_beep(freq_hz: f32, duration_ms: u32, amplitude: f32) {
    host_state()
        .lock()
        .unwrap()
        .audio
        .queue_beep(freq_hz, duration_ms, amplitude);
}

#[pyfunction]
fn audio_ring_len() -> usize {
    host_state().lock().unwrap().audio.ring.len()
}

// --- Phase 6 video A/V clock FFI ---------------------------------------------

#[pyfunction]
fn video_clock_start(channel: i32) {
    use crate::state::VideoClock;
    let now = get_ticks_ms();
    let mut st = host_state().lock().unwrap();
    st.video_clocks.insert(
        channel,
        VideoClock {
            start_ms: now,
            paused: false,
            pause_started_ms: None,
            pause_accum_ms: 0,
        },
    );
}

#[pyfunction]
fn video_clock_stop(channel: i32) {
    host_state().lock().unwrap().video_clocks.remove(&channel);
}

#[pyfunction]
fn video_clock_pos(channel: i32) -> f64 {
    let now = get_ticks_ms();
    let st = host_state().lock().unwrap();
    st.video_clocks
        .get(&channel)
        .map(|c| c.pos_ms(now))
        .unwrap_or(0.0)
}

/// Force presentation clock to pos_s seconds (wall-relative, pause-aware).
/// Used by host Movie when the wall clock outruns the progressive decode
/// buffer so playback tracks available frames instead of freezing on the
/// ring tail until ready_full.
#[pyfunction]
fn video_clock_set_pos(channel: i32, pos_s: f64) {
    let now = get_ticks_ms();
    let mut st = host_state().lock().unwrap();
    if let Some(c) = st.video_clocks.get_mut(&channel) {
        let pos_ms = if pos_s.is_finite() && pos_s > 0.0 {
            (pos_s * 1000.0).round() as u64
        } else {
            0
        };
        let mut pause_extra = c.pause_accum_ms;
        if let Some(ps) = c.pause_started_ms {
            pause_extra = pause_extra.saturating_add(now.saturating_sub(ps));
        }
        // elapsed = now - start - pause_extra == pos_ms
        // start   = now - pause_extra - pos_ms
        c.start_ms = now.saturating_sub(pause_extra).saturating_sub(pos_ms);
    }
}

#[pyfunction]
fn video_clock_pause(channel: i32) {
    let now = get_ticks_ms();
    let mut st = host_state().lock().unwrap();
    if let Some(c) = st.video_clocks.get_mut(&channel) {
        if !c.paused {
            c.paused = true;
            c.pause_started_ms = Some(now);
        }
    }
}

#[pyfunction]
fn video_clock_unpause(channel: i32) {
    let now = get_ticks_ms();
    let mut st = host_state().lock().unwrap();
    if let Some(c) = st.video_clocks.get_mut(&channel) {
        if c.paused {
            if let Some(ps) = c.pause_started_ms.take() {
                c.pause_accum_ms = c.pause_accum_ms.saturating_add(now.saturating_sub(ps));
            }
            c.paused = false;
        }
    }
}

type NestedPumpFn = Box<dyn FnMut(Duration) + Send>;
static NESTED_PUMP: Mutex<Option<NestedPumpFn>> = Mutex::new(None);

pub fn install_nested_pump<F>(f: F)
where
    F: FnMut(Duration) + Send + 'static,
{
    if let Ok(mut guard) = NESTED_PUMP.lock() {
        *guard = Some(Box::new(f));
    }
}

fn try_nested_pump(timeout: Duration) -> bool {
    // Count (b) here only — not also in nested_pump_once (single site, no double-count).
    crate::input_trace::count_try_nested_pump();
    // Take the callback out so re-entrant wait_until (from about_to_wait Python)
    // does not deadlock on NESTED_PUMP.
    let mut cb = {
        let mut guard = match NESTED_PUMP.lock() {
            Ok(g) => g,
            Err(_) => return false,
        };
        guard.take()
    };
    let result = if let Some(ref mut f) = cb {
        f(timeout);
        true
    } else {
        false
    };
    if let Some(f) = cb {
        if let Ok(mut guard) = NESTED_PUMP.lock() {
            *guard = Some(f);
        }
    }
    result
}

// end of module
