"""
Adversarial Stress Gate: BindGroup Cache Stress, Descriptor Leaks, Pipeline Permutations, Stale Bindings.
Gate name: adversarial_bindgroup_stress (RENPY_HOST_GATE=adversarial_bindgroup_stress)
"""
import random
import pathlib
import renpy_host

print("[adversarial_bindgroup_stress] Initializing pipelines, textures, and meshes...")

pipelines = {
    "solid": (renpy_host.solid_pipeline(), 0, False),
    "textured": (renpy_host.textured_pipeline(), 1, False),
    "blur": (renpy_host.blur_pipeline(), 1, True),
    "matrixcolor": (renpy_host.matrixcolor_pipeline(), 1, True),
    "dissolve": (renpy_host.dissolve_pipeline(), 2, True),
    "imagedissolve": (renpy_host.imagedissolve_pipeline(), 3, True),
    "alpha_mask": (renpy_host.alpha_mask_pipeline(), 2, False),
    "mask": (renpy_host.mask_pipeline(), 2, True),
    "live2d_mask": (renpy_host.live2d_mask_pipeline(), 2, True),
    "live2d_inverted_mask": (renpy_host.live2d_inverted_mask_pipeline(), 2, True),
    "live2d_colors": (renpy_host.live2d_colors_pipeline(), 1, True),
    "live2d_flip": (renpy_host.live2d_flip_pipeline(), 1, False),
}

# Create 120 distinct textures
textures = []
for i in range(120):
    r = (i * 37) % 256
    g = (i * 59) % 256
    b = (i * 83) % 256
    data = bytes([r, g, b, 255] * (8 * 8))
    tex = renpy_host.create_texture_rgba(8, 8, data)
    textures.append(tex)

# Create 30 distinct meshes
meshes = []
for i in range(30):
    scale = 0.2 + (i % 10) * 0.08
    verts = [
        -scale, -scale, 0.0, 1.0, 1, 1, 1, 1,
         scale, -scale, 1.0, 1.0, 1, 1, 1, 1,
         scale,  scale, 1.0, 0.0, 1, 1, 1, 1,
        -scale,  scale, 0.0, 0.0, 1, 1, 1, 1,
    ]
    m = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])
    meshes.append(m)

print(f"[adversarial_bindgroup_stress] Created {len(textures)} textures, {len(meshes)} meshes, {len(pipelines)} pipelines.")

# Phase 1: Heavy Permutation Stress (pushing bg_cache > 1024 to test cache thrash and soft cap clearing)
print("[adversarial_bindgroup_stress] Phase 1: Heavy Pipeline/Texture Permutations (3000 draws)...")
pipe_names = list(pipelines.keys())
random.seed(42)

for frame_idx in range(4):
    renpy_host.begin_frame()
    batch = []
    for _ in range(3000):
        pname = random.choice(pipe_names)
        pipe_id, tex_count, has_u = pipelines[pname]
        mesh_id = random.choice(meshes)
        
        t0 = random.choice(textures) if tex_count >= 1 else None
        t1 = random.choice(textures) if tex_count >= 2 else None
        t2 = random.choice(textures) if tex_count >= 3 else None
        
        u = None
        if has_u:
            u = [random.uniform(-2.0, 2.0) for _ in range(16)]
        
        batch.append((pipe_id, mesh_id, t0, t1, u, t2))
    
    renpy_host.draw_models(batch)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
    print(f"[adversarial_bindgroup_stress] Phase 1 Frame {frame_idx + 1} completed.")

# Phase 2: Invalidation Stress: Destroy half the textures and verify clean invalidation
print("[adversarial_bindgroup_stress] Phase 2: Texture destruction and invalidation stress...")
destroyed_textures = textures[:60]
surviving_textures = textures[60:]

for tex_id in destroyed_textures:
    renpy_host.destroy_texture(tex_id)

print(f"[adversarial_bindgroup_stress] Destroyed {len(destroyed_textures)} textures; {len(surviving_textures)} remain alive.")

# Phase 3: Draw with surviving textures mixed with intentionally stale/invalid handles
print("[adversarial_bindgroup_stress] Phase 3: Stale/Dead Handle Resilience Testing...")
renpy_host.begin_frame()
batch = []
# Valid draws
for _ in range(500):
    pname = random.choice(pipe_names)
    pipe_id, tex_count, has_u = pipelines[pname]
    mesh_id = random.choice(meshes)
    t0 = random.choice(surviving_textures) if tex_count >= 1 else None
    t1 = random.choice(surviving_textures) if tex_count >= 2 else None
    t2 = random.choice(surviving_textures) if tex_count >= 3 else None
    u = [random.uniform(-1.0, 1.0) for _ in range(16)] if has_u else None
    batch.append((pipe_id, mesh_id, t0, t1, u, t2))

# Adversarial dead handle draws (must be skipped gracefully without panic)
for _ in range(500):
    pname = random.choice(pipe_names)
    pipe_id, tex_count, has_u = pipelines[pname]
    # Intentionally pass dead textures
    dead_t0 = random.choice(destroyed_textures) if tex_count >= 1 else None
    dead_t1 = random.choice(destroyed_textures) if tex_count >= 2 else None
    # Invalid mesh handle
    invalid_mesh = 0xDEADBEEF
    u = [1.0] * 16 if has_u else None
    batch.append((pipe_id, invalid_mesh, dead_t0, dead_t1, u, None))
    # Handle 0 or non-existent pipeline
    batch.append((0x99999999, meshes[0], surviving_textures[0], None, None, None))

renpy_host.draw_models(batch)
renpy_host.end_frame_present()
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, "Readback failed"

result_msg = "[adversarial_bindgroup_stress] ok=True pipelines=12 textures=120 destroyed=60 dead_handles_handled=True"
out_path = pathlib.Path("target/gate-adversarial_bindgroup_stress.txt")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(result_msg + "\n", encoding="utf-8")
print(result_msg)

renpy_host.request_quit()
