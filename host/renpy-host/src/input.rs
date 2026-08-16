//! Translate winit events into HostEvent queue entries.

use std::sync::atomic::{AtomicI32, Ordering};

use winit::event::{ElementState, MouseButton, MouseScrollDelta, WindowEvent};
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
                    EVENT_QUEUE.push(HostEvent::with(
                        types::TEXTEDITING,
                        vec![
                            ("text".into(), EventValue::Str(text.clone())),
                            ("start".into(), EventValue::Int(0)),
                            ("length".into(), EventValue::Int(text.len() as i64)),
                        ],
                    ));
                }
                _ => {}
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

fn key_to_code(key: &Key) -> u32 {
    match key {
        Key::Named(n) => match n {
            NamedKey::Escape => 27,
            NamedKey::Enter => 13,
            NamedKey::Tab => 9,
            NamedKey::Backspace => 8,
            NamedKey::Space => 32,
            NamedKey::ArrowLeft => 1073741904,
            NamedKey::ArrowRight => 1073741903,
            NamedKey::ArrowUp => 1073741906,
            NamedKey::ArrowDown => 1073741905,
            NamedKey::Shift => 1073742049,
            NamedKey::Control => 1073742048,
            NamedKey::Alt => 1073742050,
            _ => 0,
        },
        Key::Character(s) => s.chars().next().map(|c| c as u32).unwrap_or(0),
        _ => 0,
    }
}
