"""
Host model / mesh helpers (Phase 8).

Uploads mesh buffers via renpy_host.create_mesh and draws with
draw_model (solid + textured). Procedural meshes are the Phase 8 MVP
path: real assimp.pyx stays on the SDL tree; a host ifdef / IO-bridge
assimp load is Phase 9 strip work.

Vertex layout (matches GpuArena::create_mesh):
  pos.xy, uv.xy, color.rgba  — 8 × f32 per vertex.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

# Tight mesh blob for iostream bridge demos / future file loads.
# Layout: magic "RPYM" + u32le n_verts + u32le n_idx
#         + n_verts * 8 f32 LE + n_idx * u32 LE
MESH_MAGIC = b"RPYM"


@dataclass
class MeshData:
    """CPU-side mesh ready for GpuArena upload."""

    vertices: list[float]
    indices: list[int] | None = None

    @property
    def vertex_count(self) -> int:
        return len(self.vertices) // 8

    @property
    def index_count(self) -> int:
        return 0 if not self.indices else len(self.indices)


def _v(
    x: float,
    y: float,
    u: float = 0.0,
    v: float = 0.0,
    r: float = 1.0,
    g: float = 1.0,
    b: float = 1.0,
    a: float = 1.0,
) -> list[float]:
    return [float(x), float(y), float(u), float(v), float(r), float(g), float(b), float(a)]


def procedural_triangle(
    cx: float = 0.0,
    cy: float = 0.15,
    size: float = 0.55,
    color: Sequence[float] = (0.2, 0.75, 1.0, 1.0),
) -> MeshData:
    """Single solid triangle in NDC (no indices)."""
    r, g, b, a = (list(color) + [1.0, 1.0, 1.0, 1.0])[:4]
    h = size * 0.866  # equilateral height factor
    verts = (
        _v(cx, cy + h * 0.55, 0.5, 0.0, r, g, b, a)
        + _v(cx - size * 0.5, cy - h * 0.45, 0.0, 1.0, r, g, b, a)
        + _v(cx + size * 0.5, cy - h * 0.45, 1.0, 1.0, r, g, b, a)
    )
    return MeshData(vertices=verts, indices=None)


def procedural_quad(
    x0: float = -0.5,
    y0: float = -0.5,
    x1: float = 0.5,
    y1: float = 0.5,
    color: Sequence[float] = (1.0, 1.0, 1.0, 1.0),
) -> MeshData:
    """Axis-aligned textured/solid quad with UVs."""
    r, g, b, a = (list(color) + [1.0, 1.0, 1.0, 1.0])[:4]
    verts = (
        _v(x0, y0, 0.0, 1.0, r, g, b, a)
        + _v(x1, y0, 1.0, 1.0, r, g, b, a)
        + _v(x1, y1, 1.0, 0.0, r, g, b, a)
        + _v(x0, y1, 0.0, 0.0, r, g, b, a)
    )
    return MeshData(vertices=verts, indices=[0, 1, 2, 0, 2, 3])


def procedural_cube_isometric(
    cx: float = 0.0,
    cy: float = 0.0,
    size: float = 0.35,
) -> MeshData:
    """
    Project a unit cube to 2D NDC with a simple isometric-ish skew.

    Three visible faces (top / left / right) as 6 triangles. Face colors
    differ so draw_model solid path proves multi-triangle index buffers.
    """
    s = float(size)
    # Isometric basis (hand-tuned for NDC aesthetics).
    ex, ey = s * 0.9, s * 0.45  # right
    zx, zy = -s * 0.9, s * 0.45  # left
    uy = s * 0.95  # up (y only)

    # 7 unique corners used by the three faces (center of front-bottom is origin).
    # Origin at cube center projected.
    def p(i: float, j: float, k: float) -> tuple[float, float]:
        # i: right axis, j: up, k: left axis
        x = cx + i * ex + k * zx
        y = cy + i * ey + j * uy + k * zy
        return x, y

    # Corners: 0=front-bottom-center-ish labels for faces
    # Top face: (0,1,0),(1,1,0),(1,1,1),(0,1,1) in (i,j,k)
    # Right face: (1,0,0),(1,1,0),(1,1,1),(1,0,1)
    # Left face:  (0,0,1),(0,1,1),(1,1,1),(1,0,1) wait — use front-left properly.
    # Use:
    #   top:   A B C D
    #   right: B F C E  (right side)
    #   left:  A D G H  (left side)
    A = p(0, 1, 0)
    B = p(1, 1, 0)
    C = p(1, 1, 1)
    D = p(0, 1, 1)
    E = p(1, 0, 1)
    F = p(1, 0, 0)
    G = p(0, 0, 0)
    H = p(0, 0, 1)

    verts: list[float] = []
    indices: list[int] = []

    def add_face(
        corners: Sequence[tuple[float, float]],
        color: Sequence[float],
        uvs: Sequence[tuple[float, float]] = ((0, 1), (1, 1), (1, 0), (0, 0)),
    ) -> None:
        base = len(verts) // 8
        r, g, b, a = (list(color) + [1, 1, 1, 1])[:4]
        for (x, y), (u, v) in zip(corners, uvs):
            verts.extend(_v(x, y, u, v, r, g, b, a))
        indices.extend([base, base + 1, base + 2, base, base + 2, base + 3])

    # Top (light), right (mid), left (dark) — classic isometric shading.
    add_face([A, B, C, D], (0.95, 0.85, 0.35, 1.0))  # top gold
    add_face([B, F, E, C], (0.85, 0.35, 0.30, 1.0))  # right red
    add_face([A, D, H, G], (0.30, 0.45, 0.85, 1.0))  # left blue

    return MeshData(vertices=verts, indices=indices)


def mesh_to_blob(mesh: MeshData) -> bytes:
    """Serialize MeshData to a portable blob (iostream-friendly)."""
    import struct

    n_v = mesh.vertex_count
    idx = mesh.indices or []
    n_i = len(idx)
    parts = [MESH_MAGIC, struct.pack("<II", n_v, n_i)]
    parts.append(struct.pack(f"<{n_v * 8}f", *mesh.vertices))
    if n_i:
        parts.append(struct.pack(f"<{n_i}I", *idx))
    return b"".join(parts)


def mesh_from_blob(data: bytes) -> MeshData:
    """Deserialize MeshData from mesh_to_blob output."""
    import struct

    if len(data) < 12 or data[:4] != MESH_MAGIC:
        raise ValueError("bad mesh blob magic")
    n_v, n_i = struct.unpack_from("<II", data, 4)
    off = 12
    n_f = n_v * 8
    verts = list(struct.unpack_from(f"<{n_f}f", data, off))
    off += n_f * 4
    indices: list[int] | None = None
    if n_i:
        indices = list(struct.unpack_from(f"<{n_i}I", data, off))
    return MeshData(vertices=verts, indices=indices)


def mesh_via_iostream(mesh: MeshData) -> MeshData:
    """
    Round-trip MeshData through host_pygame.iostream (SDL_IOStream stand-in).

    Proves the Phase 1–2 iostream bridge is usable for future assimp file
    loads without touching SDL_IOStream.
    """
    try:
        from renpy.pygame import iostream as io
    except Exception:
        from host_pygame import iostream as io  # type: ignore

    blob = mesh_to_blob(mesh)
    stream = io.from_memory(blob)
    raw = stream.read()
    stream.close()
    return mesh_from_blob(raw)


def mesh_via_iostream_file(mesh: MeshData, path: str) -> MeshData:
    """Write blob to path via open, reload with iostream.from_file."""
    try:
        from renpy.pygame import iostream as io
    except Exception:
        from host_pygame import iostream as io  # type: ignore

    blob = mesh_to_blob(mesh)
    with open(path, "wb") as f:
        f.write(blob)
    stream = io.from_file(path, "rb")
    raw = stream.read()
    stream.close()
    return mesh_from_blob(raw)


def upload_mesh(mesh: MeshData) -> int:
    """Upload MeshData to GpuArena; returns mesh handle (u64)."""
    import renpy_host  # type: ignore

    idx = mesh.indices
    return int(renpy_host.create_mesh(list(mesh.vertices), idx))


def destroy_mesh(handle: int) -> None:
    import renpy_host  # type: ignore

    renpy_host.destroy_mesh(int(handle))


def draw_solid_model(mesh: int, pipeline: int | None = None) -> None:
    """draw_model solid path (no texture)."""
    import renpy_host  # type: ignore

    pipe = pipeline if pipeline is not None else renpy_host.solid_pipeline()
    renpy_host.draw_model(pipe, int(mesh), None)


def draw_textured_model(
    mesh: int,
    texture: int,
    pipeline: int | None = None,
) -> None:
    """draw_model textured path."""
    import renpy_host  # type: ignore

    pipe = pipeline if pipeline is not None else renpy_host.textured_pipeline()
    renpy_host.draw_model(pipe, int(mesh), int(texture))


def make_checker_texture(size: int = 4) -> int:
    """Small checker RGBA texture for textured model smoke."""
    import renpy_host  # type: ignore

    s = max(2, int(size))
    px = bytearray()
    for y in range(s):
        for x in range(s):
            on = ((x ^ y) & 1) == 0
            if on:
                px.extend((240, 240, 240, 255))
            else:
                px.extend((40, 40, 90, 255))
    return int(renpy_host.create_texture_rgba(s, s, bytes(px)))
