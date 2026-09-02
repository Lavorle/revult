"""
Adversarial Stress Gate: Dynamic Uniform Ring Buffer Growth, Alignment, Overwrites.
Gate name: adversarial_uniform_ring (RENPY_HOST_GATE=adversarial_uniform_ring)
"""
import sys
import pathlib
import renpy_host

print("[adversarial_uniform_ring] Starting Dynamic Uniform Ring Stress Gate...")

# Create test texture & mesh
pix = [255, 128, 64, 255] * (16 * 16)
tex = renpy_host.create_texture_rgba(16, 16, bytes(pix))
verts = [
    -1.0, -1.0, 0.0, 1.0, 1, 1, 1, 1,
     1.0, -1.0, 1.0, 1.0, 1, 1, 1, 1,
     1.0,  1.0, 1.0, 0.0, 1, 1, 1, 1,
    -1.0,  1.0, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])

pipe_matrix = renpy_host.matrixcolor_pipeline()
pipe_blur = renpy_host.blur_pipeline()

# Tiered draw call counts to force dynamic uniform ring buffer growth:
# Initial capacity: 256
# Step 1: 200 (within 256)
# Step 2: 500 (grows to 512)
# Step 3: 1500 (grows to 2048)
# Step 4: 3500 (grows to 4096)
# Step 5: 6000 (grows to 8192)
draw_counts = [200, 500, 1500, 3500, 6000]

for frame_idx, count in enumerate(draw_counts):
    renpy_host.begin_frame()
    batch = []
    for i in range(count):
        # Alternate between matrixcolor and blur with unique uniform values
        if i % 2 == 0:
            scale = (i % 100) / 100.0
            u = [scale, 0.0, 0.0, 0.0,
                 0.0, scale, 0.0, 0.0,
                 0.0, 0.0, scale, 0.0,
                 0.0, 0.0, 0.0, 1.0]
            batch.append((pipe_matrix, mesh, tex, None, u, None))
        else:
            blur_val = float(i % 8)
            u = [blur_val] + [0.0] * 15
            batch.append((pipe_blur, mesh, tex, None, u, None))
    
    renpy_host.draw_models(batch)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)
    print(f"[adversarial_uniform_ring] Frame {frame_idx + 1}: {count} uniform draws submitted successfully.")

# Rapid overwrite stress: 5 successive frames with 4000 draws each
print("[adversarial_uniform_ring] Starting rapid overwrite stress (5x4000 draws)...")
for frame_idx in range(5):
    renpy_host.begin_frame()
    batch = []
    for i in range(4000):
        val = ((frame_idx * 4000 + i) % 255) / 255.0
        u = [val, 0.0, 0.0, 0.0,
             0.0, val, 0.0, 0.0,
             0.0, 0.0, val, 0.0,
             0.0, 0.0, 0.0, 1.0]
        batch.append((pipe_matrix, mesh, tex, None, u, None))
    renpy_host.draw_models(batch)
    renpy_host.end_frame_present()
    renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, f"Invalid readback: ({w}, {h}, len={len(rgba)})"
nonzero = any(b != 0 for b in rgba[:1000])
assert nonzero, "Render target is unexpectedly completely black"

result_msg = f"[adversarial_uniform_ring] ok=True max_draws=6000 rt={w}x{h} frames=10"
out_path = pathlib.Path("target/gate-adversarial_uniform_ring.txt")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(result_msg + "\n", encoding="utf-8")
print(result_msg)

renpy_host.request_quit()
