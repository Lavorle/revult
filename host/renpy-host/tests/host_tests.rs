use renpy_host::calculate_avg_duration;
use renpy_host::event_queue::{types, EventQueue, EventValue, HostEvent};
use renpy_host::shader::{
    NativeShaderComposer, ShaderHook, ShaderPart, DEFAULT_SOLID, DEFAULT_TEXTURE,
    UNIFORM_MATRIXCOLOR16, UNIFORM_NONE,
};
use renpy_host::state::HostState;
use renpy_host::timer::{TimerKind, TimerWheel};
use std::time::Duration;

#[test]
fn test_benchmark_duration_div_safety() {
    // Zero count returns Duration::ZERO safely without panicking or dividing by zero
    assert_eq!(
        calculate_avg_duration(Duration::from_millis(500), 0),
        Duration::ZERO
    );
    assert_eq!(
        calculate_avg_duration(Duration::from_nanos(0), 0),
        Duration::ZERO
    );
    assert_eq!(
        calculate_avg_duration(Duration::from_secs(100), 0),
        Duration::ZERO
    );

    // Single iteration preserves exact duration
    assert_eq!(
        calculate_avg_duration(Duration::from_nanos(1234567), 1),
        Duration::from_nanos(1234567)
    );

    // Non-zero count calculates average with nanosecond precision
    let total = Duration::from_millis(500); // 500_000_000 ns
    let count = 5;
    assert_eq!(
        calculate_avg_duration(total, count),
        Duration::from_millis(100)
    );

    let total_odd = Duration::from_nanos(10);
    let count_odd = 3;
    assert_eq!(
        calculate_avg_duration(total_odd, count_odd),
        Duration::from_nanos(3)
    );

    // Large u128 duration avoids overflow
    let total_large = Duration::from_secs(1_000_000);
    let count_large = 10_000;
    assert_eq!(
        calculate_avg_duration(total_large, count_large),
        Duration::from_secs(100)
    );
}

#[test]
fn test_host_state_defaults_and_allocation() {
    let state = HostState::new();
    assert_eq!(state.frames, 0);
    assert!(!state.should_exit);
    assert!(!state.text_input_active);
    assert_eq!(state.width, 1280);
    assert_eq!(state.height, 720);
    assert_eq!(state.title, "renpy-host");
    assert_eq!(state.product_presents, 0);
    assert!(state.window.is_none());
    assert!(state.gpu.is_none());
    assert!(state.last_product_present_at.is_none());
    assert_eq!(state.idle_clears_after_present, 0);
    assert!(state.forced_drawable.is_none());
    assert!(state.forced_from_chrome.is_none());
    assert_eq!(state.next_custom_type, 0x8000);
}

#[test]
fn test_zero_sdl_static_dependency_check() {
    // Validate that Cargo.toml and Cargo.lock in host/ do not contain SDL dependencies
    let cargo_toml = include_str!("../Cargo.toml");
    assert!(
        !cargo_toml.to_lowercase().contains("sdl2"),
        "host Cargo.toml must not depend on sdl2"
    );
    assert!(
        !cargo_toml.to_lowercase().contains("sdl3"),
        "host Cargo.toml must not depend on sdl3"
    );
    assert!(
        !cargo_toml.to_lowercase().contains("pysdl2"),
        "host Cargo.toml must not depend on pysdl2"
    );
}
#[test]
fn test_event_queue_push_poll_clear() {
    let q = EventQueue::new();
    assert_eq!(q.len(), 0);
    assert_eq!(q.poll().is_none(), true);

    q.push(HostEvent::simple(types::KEYDOWN));
    assert_eq!(q.len(), 1);
    assert_eq!(q.peek_type(), Some(types::KEYDOWN));

    q.push(HostEvent::with(
        types::TEXTINPUT,
        vec![("text".to_string(), EventValue::Str("hello".to_string()))],
    ));
    assert_eq!(q.len(), 2);

    let ev1 = q.poll().expect("event 1");
    assert_eq!(ev1.type_id, types::KEYDOWN);

    let ev2 = q.poll().expect("event 2");
    assert_eq!(ev2.type_id, types::TEXTINPUT);
    assert_eq!(ev2.dict.len(), 1);

    assert_eq!(q.len(), 0);
    q.push(HostEvent::simple(types::QUIT));
    assert_eq!(q.len(), 1);
    q.clear();
    assert_eq!(q.len(), 0);
}

#[test]
fn test_timer_wheel_operations() {
    let mut wheel = TimerWheel::new();
    assert_eq!(wheel.poll_due().is_empty(), true);

    let id = wheel.set_timer(100, 50, TimerKind::Periodic, true);
    assert!(id > 0);

    // Cancel timer with interval 0
    let id_cancel = wheel.set_timer(100, 0, TimerKind::Periodic, true);
    assert_eq!(id_cancel, 0);

    let id2 = wheel.set_timer(200, 1000, TimerKind::Custom(200), false);
    assert!(id2 > 0);
    wheel.clear_event_type(200);
}

#[test]
fn test_native_shader_composer_basic() {
    let composer = NativeShaderComposer::new();

    // Empty part list with texture -> renpy.texture
    let (parts, tex_count, layout, has_uniforms, key, wgsl) = composer
        .compose_wgsl(&vec![], true)
        .expect("compose basic texture");
    assert_eq!(parts, vec![DEFAULT_TEXTURE]);
    assert_eq!(tex_count, 1);
    assert_eq!(layout, UNIFORM_NONE);
    assert_eq!(has_uniforms, false);
    assert!(key.starts_with("composed:"));
    assert!(wgsl.contains("textureSample(t_color, s_color, v.uv)"));

    // Empty part list with no texture -> renpy.solid
    let (parts_solid, tex_count_solid, layout_solid, has_uniforms_solid, key_solid, wgsl_solid) =
        composer
            .compose_wgsl(&vec![], false)
            .expect("compose basic solid");
    assert_eq!(parts_solid, vec![DEFAULT_SOLID]);
    assert_eq!(tex_count_solid, 0);
    assert_eq!(layout_solid, UNIFORM_NONE);
    assert_eq!(has_uniforms_solid, false);
    assert!(key_solid.starts_with("composed:"));
    assert!(wgsl_solid.contains("color = v.color;"));
}

#[test]
fn test_native_shader_composer_matrixcolor() {
    let composer = NativeShaderComposer::new();
    let parts_input = vec![
        "renpy.matrixcolor".to_string(),
        "renpy.geometry".to_string(),
    ];
    let (parts, tex_count, layout, has_uniforms, _key, wgsl) = composer
        .compose_wgsl(&parts_input, true)
        .expect("compose matrixcolor");
    // renpy.geometry is composition_only and should be stripped
    assert_eq!(parts, vec!["renpy.matrixcolor"]);
    assert_eq!(tex_count, 1);
    assert_eq!(layout, UNIFORM_MATRIXCOLOR16);
    assert_eq!(has_uniforms, true);
    assert!(wgsl.contains("struct Params"));
    assert!(wgsl.contains("mat4x4<f32>(u.col0, u.col1, u.col2, u.col3)"));
}

#[test]
fn test_native_shader_composer_conflicts_and_atomics() {
    let composer = NativeShaderComposer::new();

    // Atomic parts (dissolve, imagedissolve) must be rejected from compose_wgsl
    let err_dissolve = composer.compose_wgsl(&vec!["renpy.dissolve".to_string()], true);
    assert!(err_dissolve.is_err());
    assert!(err_dissolve.unwrap_err().contains("atomic"));

    // Conflicting uniform layouts (matrixcolor16 + blur params16) must error
    let err_conflict = composer.compose_wgsl(
        &vec!["renpy.matrixcolor".to_string(), "renpy.blur".to_string()],
        true,
    );
    assert!(err_conflict.is_err());
    assert!(err_conflict
        .unwrap_err()
        .contains("conflicting uniform layouts"));
}

#[test]
fn test_shader_part_registry_custom_part() {
    let mut composer = NativeShaderComposer::new();
    let custom = ShaderPart::new(
        "custom.invert",
        1,
        UNIFORM_NONE,
        vec![],
        vec![ShaderHook {
            priority: 300,
            body: "color = vec4<f32>(1.0 - color.rgb, color.a);".to_string(),
        }],
        false,
        false,
    );
    composer.registry.register_part(custom);
    assert!(composer.registry.get_part("custom.invert").is_some());

    let (parts, tex_count, layout, _, _, wgsl) = composer
        .compose_wgsl(&vec!["custom.invert".to_string()], true)
        .expect("compose custom part");
    assert_eq!(parts, vec!["custom.invert"]);
    assert_eq!(tex_count, 1);
    assert_eq!(layout, UNIFORM_NONE);
    assert!(wgsl.contains("1.0 - color.rgb"));
}
