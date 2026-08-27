//! Translate winit events into HostEvent queue entries.

use std::sync::atomic::{AtomicI32, Ordering};
use std::sync::{Mutex, OnceLock};

use winit::event::{DeviceEvent, ElementState, MouseButton, MouseScrollDelta, WindowEvent};
use winit::keyboard::{Key, NamedKey};

use crate::event_queue::{types, EventValue, HostEvent, EVENT_QUEUE};

/// Last CursorMoved coordinates — attached to MouseInput button events so
/// clicks are not stuck at origin (Path M / inject_mouse parity).
static LAST_CURSOR_X: AtomicI32 = AtomicI32::new(0);
static LAST_CURSOR_Y: AtomicI32 = AtomicI32::new(0);

fn store_last_cursor(x: i32, y: i32) {
    LAST_CURSOR_X.store(x, Ordering::Relaxed);
    LAST_CURSOR_Y.store(y, Ordering::Relaxed);
}

fn last_cursor() -> (i32, i32) {
    (
        LAST_CURSOR_X.load(Ordering::Relaxed),
        LAST_CURSOR_Y.load(Ordering::Relaxed),
    )
}

// ---------------------------------------------------------------------------
// Gamepad state
// ---------------------------------------------------------------------------

/// M3 B3 T3 GamepadState — mirrors evdev / SDL_GameController layout.
///
/// * `axes` 6 floats in [-1,1] (SDL JOY axes 0..5, normalized)
/// * `buttons` bitmask for up to 16 buttons (bit N = button N pressed)
/// * `hats` two hats as (x,y) in -1..1 discrete (SDL hat positions)
#[derive(Debug, Clone, Copy)]
pub struct GamepadState {
    pub axes: [f32; 6],
    pub buttons: u16,
    pub hats: [(i8, i8); 2],
}

impl Default for GamepadState {
    fn default() -> Self {
        Self {
            axes: [0.0; 6],
            buttons: 0,
            hats: [(0, 0); 2],
        }
    }
}

static GAMEPADS: OnceLock<Mutex<Vec<GamepadState>>> = OnceLock::new();

fn gamepads_lock() -> &'static Mutex<Vec<GamepadState>> {
    GAMEPADS.get_or_init(|| Mutex::new(Vec::new()))
}

/// Ensure at least `id+1` entries exist (auto-grow on first inject).
fn ensure_gamepad(id: usize) -> usize {
    let lock = gamepads_lock();
    let mut g = lock.lock().unwrap();
    while g.len() <= id {
        g.push(GamepadState::default());
        // Emit JOYDEVICEADDED for new slot (id = len-1)
        let new_id = (g.len() - 1) as i64;
        drop(g);
        EVENT_QUEUE.push(HostEvent::with(
            types::JOYDEVICEADDED,
            vec![("which".into(), EventValue::Int(new_id))],
        ));
        // also controller added for compatibility
        EVENT_QUEUE.push(HostEvent::with(
            types::CONTROLLERDEVICEADDED,
            vec![("which".into(), EventValue::Int(new_id))],
        ));
        return ensure_gamepad(id);
    }
    g.len()
}

pub fn gamepad_count() -> usize {
    gamepads_lock().lock().map(|g| g.len()).unwrap_or(0)
}

pub fn gamepad_axis(id: usize, axis: usize) -> f32 {
    if axis >= 6 {
        return 0.0;
    }
    gamepads_lock()
        .lock()
        .ok()
        .and_then(|g| g.get(id).map(|s| s.axes[axis]))
        .unwrap_or(0.0)
}

pub fn gamepad_button(id: usize, button: usize) -> bool {
    if button >= 16 {
        return false;
    }
    gamepads_lock()
        .lock()
        .ok()
        .and_then(|g| g.get(id).map(|s| (s.buttons >> button) & 1 == 1))
        .unwrap_or(false)
}

pub fn gamepad_hat(id: usize, hat: usize) -> (i8, i8) {
    gamepads_lock()
        .lock()
        .ok()
        .and_then(|g| g.get(id).map(|s| s.hats.get(hat).copied().unwrap_or((0, 0))))
        .unwrap_or((0, 0))
}

pub fn set_gamepad_axis(id: usize, axis: usize, value: f32) {
    if axis >= 6 {
        return;
    }
    let v = value.clamp(-1.0, 1.0);
    ensure_gamepad(id);
    if let Ok(mut g) = gamepads_lock().lock() {
        if let Some(s) = g.get_mut(id) {
            s.axes[axis] = v;
        }
    }
    // push JOYAXISMOTION + CONTROLLERAXISMOTION for dual consumers
    let ival = (v * 32767.0) as i64;
    EVENT_QUEUE.push(HostEvent::with(
        types::JOYAXISMOTION,
        vec![
            ("which".into(), EventValue::Int(id as i64)),
            ("joy".into(), EventValue::Int(id as i64)),
            ("instance_id".into(), EventValue::Int(id as i64)),
            ("axis".into(), EventValue::Int(axis as i64)),
            ("value".into(), EventValue::Int(ival)),
        ],
    ));
    EVENT_QUEUE.push(HostEvent::with(
        types::CONTROLLERAXISMOTION,
        vec![
            ("which".into(), EventValue::Int(id as i64)),
            ("axis".into(), EventValue::Int(axis as i64)),
            ("value".into(), EventValue::Int(ival)),
        ],
    ));
}

pub fn set_gamepad_button(id: usize, button: usize, pressed: bool) {
    if button >= 16 {
        return;
    }
    ensure_gamepad(id);
    if let Ok(mut g) = gamepads_lock().lock() {
        if let Some(s) = g.get_mut(id) {
            if pressed {
                s.buttons |= 1 << button;
            } else {
                s.buttons &= !(1 << button);
            }
        }
    }
    let type_id = if pressed {
        types::JOYBUTTONDOWN
    } else {
        types::JOYBUTTONUP
    };
    EVENT_QUEUE.push(HostEvent::with(
        type_id,
        vec![
            ("which".into(), EventValue::Int(id as i64)),
            ("joy".into(), EventValue::Int(id as i64)),
            ("instance_id".into(), EventValue::Int(id as i64)),
            ("button".into(), EventValue::Int(button as i64)),
        ],
    ));
    let ctype = if pressed {
        types::CONTROLLERBUTTONDOWN
    } else {
        types::CONTROLLERBUTTONUP
    };
    EVENT_QUEUE.push(HostEvent::with(
        ctype,
        vec![
            ("which".into(), EventValue::Int(id as i64)),
            ("button".into(), EventValue::Int(button as i64)),
        ],
    ));
}

pub fn set_gamepad_hat(id: usize, hat: usize, x: i8, y: i8) {
    if hat >= 2 {
        return;
    }
    ensure_gamepad(id);
    if let Ok(mut g) = gamepads_lock().lock() {
        if let Some(s) = g.get_mut(id) {
            s.hats[hat] = (x.clamp(-1, 1), y.clamp(-1, 1));
        }
    }
    EVENT_QUEUE.push(HostEvent::with(
        types::JOYHATMOTION,
        vec![
            ("which".into(), EventValue::Int(id as i64)),
            ("joy".into(), EventValue::Int(id as i64)),
            ("hat".into(), EventValue::Int(hat as i64)),
            ("value".into(), EventValue::Str(format!("{},{}", x, y))),
            ("value_x".into(), EventValue::Int(x as i64)),
            ("value_y".into(), EventValue::Int(y as i64)),
        ],
    ));
}

/// Legacy poll_gamepad compat — returns copy of slot 0 if present.
#[allow(dead_code)]
pub fn poll_gamepad() -> Option<GamepadState> {
    gamepads_lock().lock().ok().and_then(|g| g.first().copied())
}

// ---------------------------------------------------------------------------
// a11y probe stub (AT-SPI2 deferred)
// ---------------------------------------------------------------------------
pub mod a11y {
    /// Probe Orca / AT-SPI2 screen-reader presence.
    ///
    /// Deferred: real D-Bus AT-SPI2 connection requires at-spi2 + session bus;
    /// for now return JSON-serializable stub so probe gate never KeyErrors.
    pub fn probe_orca() -> String {
        // Minimal JSON so python gate can parse without KeyError
        r#"{"screen_reader_active":false,"backend":"stub","detail":"deferred AT-SPI2"}"#.to_string()
    }

    pub fn screen_reader_active() -> bool {
        false
    }
}

pub fn handle_window_event(event: &WindowEvent) {
    crate::input_trace::count_handle_window_event();
    match event {
        WindowEvent::CloseRequested => {
            EVENT_QUEUE.push(HostEvent::simple(types::QUIT));
        }
        WindowEvent::KeyboardInput { event, .. } => {
            let pressed = event.state == ElementState::Pressed;
            let type_id = if pressed {
                types::KEYDOWN
            } else {
                types::KEYUP
            };
            let keycode = key_to_code(&event.logical_key);
            let unicode = match &event.logical_key {
                Key::Character(s) => s.to_string(),
                _ => String::new(),
            };
            // Always emit unicode (empty for NamedKey / KEYUP) so Python Event
            // consumers never hit AttributeError on map_event char matching.
            let dict = vec![
                ("key".into(), EventValue::Int(keycode as i64)),
                ("scancode".into(), EventValue::Int(0)),
                ("mod".into(), EventValue::Int(0)),
                ("repeat".into(), EventValue::Bool(event.repeat)),
                ("unicode".into(), EventValue::Str(unicode)),
            ];
            EVENT_QUEUE.push(HostEvent::with(type_id, dict));
        }
        WindowEvent::CursorMoved { position, .. } => {
            let x = position.x as i32;
            let y = position.y as i32;
            store_last_cursor(x, y);
            EVENT_QUEUE.push(HostEvent::with(
                types::MOUSEMOTION,
                vec![
                    ("pos".into(), EventValue::Str(format!("{x},{y}"))),
                    ("x".into(), EventValue::Int(x as i64)),
                    ("y".into(), EventValue::Int(y as i64)),
                    ("rel".into(), EventValue::Str("0,0".into())),
                    ("buttons".into(), EventValue::Int(0)),
                    // map_event may read ev.mod on mouse keysyms; always present.
                    ("mod".into(), EventValue::Int(0)),
                ],
            ));
        }
        WindowEvent::MouseInput { state, button, .. } => {
            let type_id = if *state == ElementState::Pressed {
                types::MOUSEBUTTONDOWN
            } else {
                types::MOUSEBUTTONUP
            };
            let btn = match button {
                MouseButton::Left => 1,
                MouseButton::Middle => 2,
                MouseButton::Right => 3,
                MouseButton::Back => 4,
                MouseButton::Forward => 5,
                MouseButton::Other(n) => *n as i64,
            };
            let (x, y) = last_cursor();
            EVENT_QUEUE.push(HostEvent::with(
                type_id,
                vec![
                    ("button".into(), EventValue::Int(btn)),
                    ("pos".into(), EventValue::Str(format!("{x},{y}"))),
                    ("x".into(), EventValue::Int(x as i64)),
                    ("y".into(), EventValue::Int(y as i64)),
                    // map_event may read ev.mod on mouse keysyms; always present.
                    ("mod".into(), EventValue::Int(0)),
                ],
            ));
        }
        WindowEvent::MouseWheel { delta, .. } => {
            let (dx, dy) = match delta {
                MouseScrollDelta::LineDelta(x, y) => (*x as i64, *y as i64),
                MouseScrollDelta::PixelDelta(p) => (p.x as i64, p.y as i64),
            };
            EVENT_QUEUE.push(HostEvent::with(
                types::MOUSEWHEEL,
                vec![
                    ("x".into(), EventValue::Int(dx)),
                    ("y".into(), EventValue::Int(dy)),
                    ("flipped".into(), EventValue::Bool(false)),
                ],
            ));
        }
        WindowEvent::Ime(ime) => {
            use winit::event::Ime;
            match ime {
                Ime::Commit(text) => {
                    EVENT_QUEUE.push(HostEvent::with(
                        types::TEXTINPUT,
                        vec![("text".into(), EventValue::Str(text.clone()))],
                    ));
                }
                Ime::Preedit(text, _) => {
                    // Truncate compositions longer than 64 chars at Rust side too
                    // (Python side also truncates, double-guard).
                    let truncated = if text.chars().count() > 64 {
                        text.chars().take(64).collect::<String>()
                    } else {
                        text.clone()
                    };
                    EVENT_QUEUE.push(HostEvent::with(
                        types::TEXTEDITING,
                        vec![
                            ("text".into(), EventValue::Str(truncated.clone())),
                            ("start".into(), EventValue::Int(0)),
                            ("length".into(), EventValue::Int(truncated.chars().count() as i64)),
                        ],
                    ));
                }
                _ => {}
            }
        }
        WindowEvent::AxisMotion { axis, value, .. } => {
            // winit Wayland/X11 axis motion → gamepad axis 0..5 normalized
            // `value` is f64 in [-1,1] on most backends (evdev via winit)
            let ax = *axis as usize;
            if ax < 6 {
                let v = (*value as f32).clamp(-1.0, 1.0);
                set_gamepad_axis(0, ax, v);
            }
        }
        WindowEvent::Resized(size) => {
            // Emit SDL3 WINDOWRESIZED (0x206), not legacy WINDOWEVENT (512).
            // renpy.display.core only force_redraws on pygame.WINDOWRESIZED;
            // without it product never re-presents after swapchain reconfigure
            // and idle RedrawRequested paints solid deep-teal clear forever (S1).
            //
            // Skip no-op / force-chrome echoes so compositor spam does not thrash
            // force_redraw / before_resize on the main menu.
            let w = size.width.max(1);
            let h = size.height.max(1);
            {
                let st = crate::state::host_state().lock().unwrap();
                let cur_w = st.width.max(1);
                let cur_h = st.height.max(1);
                if w == cur_w && h == cur_h {
                    return;
                }
                if let Some((fw, fh)) = st.forced_drawable {
                    // Live chrome still pre-request or already at force — main.rs
                    // keeps force; do not queue a size event for the echo.
                    if (w == fw && h == fh)
                        || st
                            .forced_from_chrome
                            .map(|(cw, ch)| w == cw && h == ch)
                            .unwrap_or(false)
                    {
                        return;
                    }
                }
            }
            EVENT_QUEUE.push(HostEvent::with(
                types::WINDOWRESIZED,
                vec![
                    ("x".into(), EventValue::Int(w as i64)),
                    ("y".into(), EventValue::Int(h as i64)),
                    // Also expose pos-like size for any consumer expecting w/h.
                    ("w".into(), EventValue::Int(w as i64)),
                    ("h".into(), EventValue::Int(h as i64)),
                ],
            ));
        }
        _ => {}
    }
}

pub fn handle_device_event(event: &DeviceEvent) {
    crate::input_trace::count_handle_window_event();
    match event {
        DeviceEvent::Motion { axis, value } => {
            let ax = *axis as usize;
            if ax < 6 {
                let v = (*value as f32).clamp(-1.0, 1.0);
                // Prefer device motion as gamepad axis; route to pad 0
                set_gamepad_axis(0, ax, v);
            }
        }
        DeviceEvent::Button { button, state } => {
            let btn = *button as usize;
            if btn < 16 {
                let pressed = *state == ElementState::Pressed;
                set_gamepad_button(0, btn, pressed);
            }
        }
        _ => {}
    }
}

fn key_to_code(key: &Key) -> u32 {
    match key {
        Key::Named(n) => match n {
            NamedKey::Enter => 0x0D,
            NamedKey::Escape => 0x1B,
            NamedKey::Backspace => 0x08,
            NamedKey::Tab => 0x09,
            NamedKey::Space => 0x20,
            NamedKey::ArrowLeft => 0x4B,
            NamedKey::ArrowRight => 0x4D,
            NamedKey::ArrowUp => 0x52,
            NamedKey::ArrowDown => 0x50,
            NamedKey::Delete => 0x7F,
            NamedKey::Home => 0x48,
            NamedKey::End => 0x4F,
            NamedKey::PageUp => 0x4D,
            NamedKey::PageDown => 0x4E,
            _ => 0,
        },
        Key::Character(s) => s.chars().next().map(|c| c as u32).unwrap_or(0),
        _ => 0,
    }
}
