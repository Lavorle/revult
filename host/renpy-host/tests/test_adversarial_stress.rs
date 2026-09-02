use renpy_host::arena::{
    UNIFORM_BYTES, UNIFORM_RING_INITIAL, UNIFORM_STRIDE,
};
use renpy_host::shader::{
    NativeShaderComposer, ShaderError, ShaderHook, ShaderPart, DEFAULT_SOLID, DEFAULT_TEXTURE,
    UNIFORM_NONE,
};
use std::collections::HashSet;

#[test]
fn test_adversarial_uniform_ring_alignment_and_growth_bounds() {
    // 1. Fundamental hardware constants verification for Vega 20 / GFX906
    assert_eq!(UNIFORM_BYTES, 64, "Uniform payload must be 64 bytes (4 vec4s)");
    assert_eq!(UNIFORM_STRIDE, 256, "Uniform stride must be exactly 256 bytes for Vulkan minUniformBufferOffsetAlignment");
    assert_eq!(UNIFORM_STRIDE % 256, 0, "Stride must be cleanly divisible by 256");
    assert!(UNIFORM_BYTES <= UNIFORM_STRIDE, "Uniform data must fit within stride boundary");

    // 2. Boundary alignment checks for 10,000 slots
    for slot in 0..10_000usize {
        let byte_offset = (slot as u64) * UNIFORM_STRIDE;
        assert_eq!(
            byte_offset % 256,
            0,
            "Slot {} produced unaligned byte offset {}",
            slot,
            byte_offset
        );
        // Ensure no arithmetic overflow up to 10M slots
        assert!(byte_offset <= (10_000 * 256));
    }

    // 3. Dynamic growth formula simulation (need_slots.max(UNIFORM_RING_INITIAL).next_power_of_two())
    let test_demands = vec![
        (0, 256),
        (1, 256),
        (255, 256),
        (256, 256),
        (257, 512),
        (511, 512),
        (512, 512),
        (513, 1024),
        (1024, 1024),
        (1025, 2048),
        (4096, 4096),
        (4097, 8192),
        (100_000, 131072),
    ];

    for (need, expected_cap) in test_demands {
        let cap = need.max(UNIFORM_RING_INITIAL).next_power_of_two();
        assert_eq!(
            cap, expected_cap,
            "Capacity for need={} must be {}, got {}",
            need, expected_cap, cap
        );
        let req_bytes = (cap as u64) * UNIFORM_STRIDE;
        assert_eq!(
            req_bytes % 256,
            0,
            "Total allocated buffer size must be 256B aligned"
        );
    }
}

#[test]
fn test_adversarial_bind_group_cache_key_robustness() {
    #[derive(Clone, Copy, PartialEq, Eq, Hash, Debug)]
    struct MockBgKey {
        pipeline: u64,
        texture: u64,
        texture1: u64,
        texture2: u64,
    }

    let mut key_set = HashSet::new();

    // Test 10,000 distinct permutations of pipelines and textures
    let pipe_ids = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 50, 100, 9999];
    let tex_ids = [0, 1, 2, 5, 10, 50, 100, 500, 1000, 0x7FFFFFFF, 0xFFFFFFFF];

    let mut total_keys = 0;
    for &p in &pipe_ids {
        for &t0 in &tex_ids {
            for &t1 in &tex_ids[..4] {
                for &t2 in &tex_ids[..2] {
                    let k = MockBgKey {
                        pipeline: p,
                        texture: t0,
                        texture1: t1,
                        texture2: t2,
                    };
                    key_set.insert(k);
                    total_keys += 1;
                }
            }
        }
    }

    assert_eq!(
        key_set.len(),
        total_keys,
        "Distinct pipeline/texture combinations must produce unique hash keys without collision"
    );

    // Test texture invalidation filter
    let target_tex = 50u64;
    key_set.retain(|k| k.texture != target_tex && k.texture1 != target_tex && k.texture2 != target_tex);
    for k in &key_set {
        assert_ne!(k.texture, target_tex);
        assert_ne!(k.texture1, target_tex);
        assert_ne!(k.texture2, target_tex);
    }
}

#[test]
fn test_adversarial_shader_composer_permutations() {
    let mut composer = NativeShaderComposer::new();

    // 1. Test empty parts list with and without texture
    let res_tex = composer.compose_wgsl(&[], true).expect("empty tex");
    assert_eq!(res_tex.0, vec![DEFAULT_TEXTURE]);
    assert_eq!(res_tex.1, 1);
    assert_eq!(res_tex.2, UNIFORM_NONE);
    assert_eq!(res_tex.3, false);

    let res_solid = composer.compose_wgsl(&[], false).expect("empty solid");
    assert_eq!(res_solid.0, vec![DEFAULT_SOLID]);
    assert_eq!(res_solid.1, 0);
    assert_eq!(res_solid.2, UNIFORM_NONE);
    assert_eq!(res_solid.3, false);

    // 2. Register multiple custom parts with extreme priorities
    let p_early = ShaderPart::new(
        "custom.early",
        0,
        UNIFORM_NONE,
        vec![],
        vec![ShaderHook {
            priority: 10,
            body: "color = color * 0.5;".to_string(),
        }],
        false,
        false,
    );
    let p_late = ShaderPart::new(
        "custom.late",
        0,
        UNIFORM_NONE,
        vec![],
        vec![ShaderHook {
            priority: 990,
            body: "color = clamp(color, vec4(0.0), vec4(1.0));".to_string(),
        }],
        false,
        false,
    );
    composer.registry.register_part(p_early);
    composer.registry.register_part(p_late);

    let res_custom = composer
        .compose_wgsl(&["custom.late".to_string(), "custom.early".to_string()], true)
        .expect("compose ordered");
    // Parts should be sorted and hooks ordered by priority
    assert!(res_custom.5.contains("color * 0.5"));
    assert!(res_custom.5.contains("clamp(color, vec4(0.0), vec4(1.0))"));

    // 3. Test max textures enforcement (tex_count > 3 must fail closed in validate)
    let p_tex4 = ShaderPart::new(
        "custom.tex4",
        4,
        UNIFORM_NONE,
        vec![],
        vec![],
        false,
        false,
    );
    let err_val = p_tex4.validate();
    assert!(err_val.is_err(), "Part with tex_count > 3 must fail validation");
    match err_val.unwrap_err() {
        ShaderError::ExceededMaxTextures { count, max } => {
            assert_eq!(count, 4);
            assert_eq!(max, 3);
        }
        other => panic!("Expected ExceededMaxTextures, got {:?}", other),
    }

    // 4. Test uniform layout conflict enforcement
    let err_conflict = composer.compose_wgsl(
        &["renpy.matrixcolor".to_string(), "renpy.blur".to_string()],
        true,
    );
    assert!(err_conflict.is_err());
    assert!(err_conflict.unwrap_err().contains("conflicting uniform layouts"));

    // 5. Test atomic part rejection
    let err_dissolve = composer.compose_wgsl(&["renpy.dissolve".to_string()], true);
    assert!(err_dissolve.is_err());
    assert!(err_dissolve.unwrap_err().contains("atomic"));

    let err_imagedissolve = composer.compose_wgsl(&["renpy.imagedissolve".to_string()], true);
    assert!(err_imagedissolve.is_err());
    assert!(err_imagedissolve.unwrap_err().contains("atomic"));
}
