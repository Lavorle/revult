"""
Adversarial Stress Gate: Shader Arithmetic Edge Cases (Zero Blur, Extreme Matrix, Boundary Dissolve, Mask & Live2D).
Gate name: adversarial_shader_arithmetic (RENPY_HOST_GATE=adversarial_shader_arithmetic)
"""
import math
import pathlib
import renpy_host

print("[adversarial_shader_arithmetic] Starting Shader Arithmetic Stress Gate...")

# Setup test textures (control, bottom, top)
pix_white = [255, 255, 255, 255] * (16 * 16)
pix_red = [255, 0, 0, 255] * (16 * 16)
pix_blue = [0, 0, 255, 255] * (16 * 16)
pix_gradient = []
for y in range(16):
    for x in range(16):
        v = (x * 16 + y * 16) // 2
        pix_gradient.extend([v, v, v, 255])

tex_white = renpy_host.create_texture_rgba(16, 16, bytes(pix_white))
tex_red = renpy_host.create_texture_rgba(16, 16, bytes(pix_red))
tex_blue = renpy_host.create_texture_rgba(16, 16, bytes(pix_blue))
tex_grad = renpy_host.create_texture_rgba(16, 16, bytes(pix_gradient))

verts = [
    -1.0, -1.0, 0.0, 1.0, 1, 1, 1, 1,
     1.0, -1.0, 1.0, 1.0, 1, 1, 1, 1,
     1.0,  1.0, 1.0, 0.0, 1, 1, 1, 1,
    -1.0,  1.0, 0.0, 0.0, 1, 1, 1, 1,
]
mesh = renpy_host.create_mesh(verts, [0, 1, 2, 0, 2, 3])

pipe_blur = renpy_host.blur_pipeline()
pipe_matrix = renpy_host.matrixcolor_pipeline()
pipe_dissolve = renpy_host.dissolve_pipeline()
pipe_imagedissolve = renpy_host.imagedissolve_pipeline()
pipe_mask = renpy_host.mask_pipeline()
pipe_l2d_mask = renpy_host.live2d_mask_pipeline()
pipe_l2d_colors = renpy_host.live2d_colors_pipeline()

# 1. Blur Edge Cases
print("[adversarial_shader_arithmetic] Testing Blur arithmetic edge cases...")
blur_cases = [
    0.0,        # 0.0 blur radius
    -100.0,     # sub-texel clamp (radius -> 0.5)
    -1.0,
    1.0,
    10.0,
    25.0,       # extreme radius
    -0.0,
]

renpy_host.begin_frame()
for blur_log2 in blur_cases:
    u = [blur_log2] + [0.0] * 15
    renpy_host.draw_model(pipe_blur, mesh, tex_white, None, u)
renpy_host.end_frame_present()
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

# 2. MatrixColor Edge Cases
print("[adversarial_shader_arithmetic] Testing MatrixColor 4x4 arithmetic edge cases...")
matrix_cases = [
    # Identity
    [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,1],
    # Zero matrix
    [0,0,0,0, 0,0,0,0, 0,0,0,0, 0,0,0,0],
    # Inverted color matrix
    [-1,0,0,0, 0,-1,0,0, 0,0,-1,0, 1,1,1,1],
    # Extreme positive values (1e5)
    [100000.0, 0, 0, 0, 0, 100000.0, 0, 0, 0, 0, 100000.0, 0, 0, 0, 0, 1],
    # Extreme negative values (-1e5)
    [-100000.0, 0, 0, 0, 0, -100000.0, 0, 0, 0, 0, -100000.0, 0, 0, 0, 0, 1],
    # High alpha modulation
    [1,0,0,0, 0,1,0,0, 0,0,1,0, 0,0,0,100.0],
    # Subnormal / small float
    [1e-20, 0, 0, 0, 0, 1e-20, 0, 0, 0, 0, 1e-20, 0, 0, 0, 0, 1e-20],
]

renpy_host.begin_frame()
for mat in matrix_cases:
    u = [float(x) for x in mat]
    renpy_host.draw_model(pipe_matrix, mesh, tex_white, None, u)
renpy_host.end_frame_present()
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

# 3. Dissolve & ImageDissolve Boundary Thresholds
print("[adversarial_shader_arithmetic] Testing Dissolve & ImageDissolve boundaries...")
dissolve_cases = [
    0.0,    # exact 0.0 threshold
    1.0,    # exact 1.0 threshold
    0.5,    # midpoint
    -10.0,  # negative out-of-bounds
    10.0,   # positive out-of-bounds
]

renpy_host.begin_frame()
for amt in dissolve_cases:
    u = [amt] + [0.0] * 15
    renpy_host.draw_model(pipe_dissolve, mesh, tex_red, tex_blue, u)
renpy_host.end_frame_present()
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

# ImageDissolve combinations
imagedissolve_cases = [
    (0.0, 0.0, 0.0),       # zero ramp & offset
    (0.0, 1.0, 0.0),       # normal
    (0.0, 1.0, 1.0),       # red channel select
    (-1.0, 1000.0, 0.0),   # very steep ramp
    (10.0, -100.0, 0.5),   # negative slope ramp
    (-0.5, 2.0, 0.0),      # standard transition
]

renpy_host.begin_frame()
for off, mult, ch in imagedissolve_cases:
    u = [off, mult, ch] + [0.0] * 13
    renpy_host.draw_model(pipe_imagedissolve, mesh, tex_grad, tex_red, u, tex_blue)
renpy_host.end_frame_present()
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

# 4. Mask & Live2D Edge Cases
print("[adversarial_shader_arithmetic] Testing Mask & Live2D edge cases...")
renpy_host.begin_frame()
# Mask: zero mult/offset, extreme mult
renpy_host.draw_model(pipe_mask, mesh, tex_white, tex_grad, [0.0, 0.0] + [0.0] * 14)
renpy_host.draw_model(pipe_mask, mesh, tex_white, tex_grad, [10000.0, -5000.0] + [0.0] * 14)

# Live2D Mask: zero model_size (protected by max(1.0, 1.0)), negative offset
renpy_host.draw_model(pipe_l2d_mask, mesh, tex_white, tex_grad, [0.0, 0.0, 0.0, 0.0, 0.0] + [0.0] * 11)
renpy_host.draw_model(pipe_l2d_mask, mesh, tex_white, tex_grad, [-100.0, -100.0, 1.0, -50.0, -50.0] + [0.0] * 11)

# Live2D Colors: zero mult/screen, overflow mult/screen
renpy_host.draw_model(pipe_l2d_colors, mesh, tex_white, None, [0.0,0.0,0.0,0.0, 0.0,0.0,0.0,0.0] + [0.0] * 8)
renpy_host.draw_model(pipe_l2d_colors, mesh, tex_white, None, [5.0,5.0,5.0,1.0, 2.0,2.0,2.0,1.0] + [0.0] * 8)

renpy_host.end_frame_present()
renpy_host.wait_until(renpy_host.get_ticks_ms() + 16)

# Final RT Readback & Assertions
w, h, rgba = renpy_host.read_game_rt_rgba()
assert w > 0 and h > 0 and len(rgba) == w * h * 4, f"Readback failed ({w}x{h})"

result_msg = "[adversarial_shader_arithmetic] ok=True blur_tested=True matrix_tested=True dissolve_tested=True live2d_tested=True"
out_path = pathlib.Path("target/gate-adversarial_shader_arithmetic.txt")
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(result_msg + "\n", encoding="utf-8")
print(result_msg)

renpy_host.request_quit()
