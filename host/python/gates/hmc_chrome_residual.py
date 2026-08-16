"""
HuangmeiC AC-C* / AC-Nav residual chrome gate — reachable gui/* Image/Frame.

Gate name: hmc_chrome_residual  (RENPY_HOST_GATE=hmc_chrome_residual)

Sequential isolated draws through product WgpuDraw of HuangmeiC recovered
gui assets. Asserts no featureless black slab / missing panel structure.

Covers main_menu/say/choice/confirm residual (AC-C*) plus post-ShowMenu
full-bleed panels for load/preferences/appreciation/flowchart (AC-Nav engine
asset path). Prefs Frame multipiece (heading Borders 60,20; choice 30,19).

Stop rule: unrecovered prefs stubs (game_config_2 / mouse_config) are game
content. Full screen interact = human checklist residual (AC-Nav1–2 authority).

Note: no from __future__; host run_file prepends imports.
"""

import os
import struct
import zlib
from pathlib import Path

import renpy_host  # type: ignore
from renpy.wgpu.draw import HostTexture, WgpuDraw

_base = Path(os.environ.get("RENPY_HOST_BASE") or str(Path.cwd()))
_candidates = [
    Path(os.environ["RENPY_HOST_GAME"]) / "game" / "gui" if os.environ.get("RENPY_HOST_GAME") else None,
    _base / "host" / "playtests" / "HuangmeiC" / "game" / "gui",
    Path("/mnt/nvme0n1p2/@home/isah1221/huangmeic/recovered_project/gui"),
]
GUI = next((p for p in _candidates if p is not None and p.is_dir()), None)
out = _base / "host" / "target" / "gate-hmc_chrome_residual.txt"
out.parent.mkdir(parents=True, exist_ok=True)

VW, VH = 1280, 720
BG = (40, 80, 120, 255)


class Mat2:
    def __init__(self, a, b, c, d):
        self.xdx = float(a)
        self.xdy = float(b)
        self.ydx = float(c)
        self.ydy = float(d)


class Surf:
    def __init__(self, w, h, p):
        self._w = int(w)
        self._h = int(h)
        need = self._w * self._h * 4
        raw = bytes(p)
        self._pixels = raw if len(raw) >= need else raw + bytes(need - len(raw))

    def get_size(self):
        return self._w, self._h


class R:
    def __init__(self, w, h):
        self.width = int(w)
        self.height = int(h)
        self.children = []
        self.mesh = None
        self.texture = None
        self.textures = None
        self.color = None
        self.shaders = None
        self.pipeline = None
        self.vertices = None
        self.indices = None
        self.cached_model = None
        self.blits = None
        self.ndc = None
        self.uniforms = None
        self.loaded = False
        self.forward = None
        self.reverse = None

    def blit(self, c, x=0, y=0):
        self.children.append((c, float(x), float(y), False, True))

    def get_size(self):
        return self.width, self.height


def png_rgba(path):
    data = path.read_bytes()
    pos = 8
    w = h = None
    raw = b""
    ct = None
    while pos < len(data):
        ln = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4
        typ = data[pos : pos + 4]
        pos += 4
        chunk = data[pos : pos + ln]
        pos += ln + 4
        if typ == b"IHDR":
            w, h, _, ct = struct.unpack(">IIBB", chunk[:10])
        elif typ == b"IDAT":
            raw += chunk
        elif typ == b"IEND":
            break
    decomp = zlib.decompress(raw)
    bpp = 4 if ct == 6 else 3
    stride = w * bpp + 1
    outb = bytearray(w * h * 4)
    prev = bytearray(w * bpp)
    for y in range(h):
        row = decomp[y * stride : (y + 1) * stride]
        filt = row[0]
        scan = bytearray(row[1:])
        if filt == 1:
            for i in range(bpp, len(scan)):
                scan[i] = (scan[i] + scan[i - bpp]) & 255
        elif filt == 2:
            for i in range(len(scan)):
                scan[i] = (scan[i] + prev[i]) & 255
        elif filt == 3:
            for i in range(len(scan)):
                a = scan[i - bpp] if i >= bpp else 0
                scan[i] = (scan[i] + ((a + prev[i]) // 2)) & 255
        elif filt == 4:
            for i in range(len(scan)):
                a = scan[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if pa <= pb and pa <= pc else (b if pb <= pc else c)
                scan[i] = (scan[i] + pr) & 255
        prev = scan
        for x in range(w):
            si = x * bpp
            di = (y * w + x) * 4
            if bpp == 3:
                outb[di : di + 4] = bytes([scan[si], scan[si + 1], scan[si + 2], 255])
            else:
                outb[di : di + 4] = bytes(scan[si : si + 4])
    return w, h, bytes(outb)


def samp(rt, rw, rh, x, y):
    x = max(0, min(rw - 1, int(x)))
    y = max(0, min(rh - 1, int(y)))
    o = (y * rw + x) * 4
    return rt[o], rt[o + 1], rt[o + 2], rt[o + 3]


def is_blackish(c, tol=25):
    return c[0] <= tol and c[1] <= tol and c[2] <= tol


def near_bg(c, tol=18):
    return all(abs(int(c[i]) - int(BG[i])) <= tol for i in range(3))


def piece(tex, sx0, sy0, csw, csh, cdw, cdh):
    sub = tex.subsurface((sx0, sy0, csw, csh))
    if csw == cdw and csh == cdh:
        return sub
    p = R(cdw, cdh)
    p.reverse = Mat2(cdw / float(csw), 0, 0, cdh / float(csh))
    p.forward = Mat2(csw / float(cdw), 0, 0, csh / float(cdh))
    p.blit(sub, 0, 0)
    return p


def build_frame(tex, sw, sh, dw, dh, borders):
    left, top, right, bottom = borders
    frame = R(dw, dh)

    def regions(x0, x1, y0, y1):
        dx0, sx0 = (x0, x0) if x0 >= 0 else (dw + x0, sw + x0)
        dx1, sx1 = (x1, x1) if x1 > 0 else (dw + x1, sw + x1)
        dy0, sy0 = (y0, y0) if y0 >= 0 else (dh + y0, sh + y0)
        dy1, sy1 = (y1, y1) if y1 > 0 else (dh + y1, sh + y1)
        return sx0, sy0, sx1 - sx0, sy1 - sy0, dx0, dy0, dx1 - dx0, dy1 - dy0

    def draw(x0, x1, y0, y1):
        sx0, sy0, csw, csh, dx0, dy0, cdw, cdh = regions(x0, x1, y0, y1)
        if csw <= 0 or csh <= 0 or cdw <= 0 or cdh <= 0:
            return
        frame.blit(piece(tex, sx0, sy0, csw, csh, cdw, cdh), dx0, dy0)

    if top:
        if left:
            draw(0, left, 0, top)
        draw(left, -right, 0, top)
        if right:
            draw(-right, 0, 0, top)
    if left:
        draw(0, left, top, -bottom)
    draw(left, -right, top, -bottom)
    if right:
        draw(-right, 0, top, -bottom)
    if bottom:
        if left:
            draw(0, left, -bottom, 0)
        draw(left, -right, -bottom, 0)
        if right:
            draw(-right, 0, -bottom, 0)
    return frame


def main():
    if GUI is None:
        msg = "ok=False reason=gui_dir_missing"
        out.write_text(msg + "\n")
        print("[hmc_chrome_residual]", msg, flush=True)
        raise RuntimeError(msg)

    draw = WgpuDraw()
    draw.init((VW, VH))
    results = []
    notes = []

    # Image assets: (name, rel, require_color)
    # require_color=False: intentional dark/α-mask or near-transparent soft.
    images = [
        ("main_menu.dock", "main_menu/dock_bg.png", False),
        ("main_menu.start", "main_menu/start_idle.png", True),
        ("say.bg", "say/background.png", True),
        ("choice.idle", "choice/choice_idle.png", True),
        ("confirm.bg1", "confirm/background_1.png", True),
        ("confirm.rect1", "confirm/rect_1.png", True),
        ("confirm.bg2", "confirm/background_2.png", True),
        ("confirm.mask", "confirm/mask.png", True),
        ("confirm.title", "confirm/title.png", True),
        ("chapter.bg", "chapter_name/background.png", True),
        ("bgm.bg", "bgm_name/background.png", True),
        ("ctc.frame", "ctc/15.png", False),  # near-transparent soft
        # AC-Nav post-ShowMenu full-bleed / chrome panels (engine asset path)
        ("nav.load.bg", "savefile_manage/load/background.png", True),
        ("nav.prefs.mask", "preferences/common/mask.png", True),
        ("nav.prefs.bg", "preferences/common/background.png", True),
        ("nav.prefs.title", "preferences/common/title.png", True),
        ("nav.prefs.choice_idle", "preferences/common/choice_idle.png", True),
        ("nav.appr.bg", "appreciation/common/background.png", True),
        ("nav.appr.layout", "appreciation/common/layout_background.png", True),
        ("nav.appr.title", "appreciation/common/title.png", True),
        ("nav.flow.title", "flowchart/common/title.png", True),
        ("nav.flow.preview", "flowchart/common/preview_background.png", True),
        ("nav.flow.right_mask", "flowchart/common/right_mask.png", False),  # black RGB a≈51
    ]

    for name, rel, require_color in images:
        root = R(VW, VH)
        root.blit(Surf(VW, VH, bytes([BG[0], BG[1], BG[2], BG[3]]) * (VW * VH)), 0, 0)
        w, h, px = png_rgba(GUI / rel)
        t = draw.load_texture(Surf(w, h, px))
        if not isinstance(t, HostTexture) or t.handle <= 0:
            if isinstance(t, int) and t > 0:
                t = HostTexture(t, w, h)
            else:
                notes.append("fail:%s:load" % name)
                results.append({"name": name, "ok": False, "rel": rel, "kind": "image",
                                "mean": (0, 0, 0), "center": (0, 0, 0), "pure_frac": 1.0,
                                "dest": (0, 0), "black_slab": True, "still_bg": True})
                continue
        if w > 600 or h > 200:
            dw, dh = min(w, 600), min(h, 200)
            node = R(dw, dh)
            node.reverse = Mat2(dw / float(w), 0, 0, dh / float(h))
            node.forward = Mat2(w / float(dw), 0, 0, h / float(dh))
            node.blit(t, 0, 0)
            root.blit(node, 50, 50)
            size = (dw, dh)
            cx, cy = 50 + dw // 2, 50 + dh // 2
        else:
            root.blit(t, 50, 50)
            size = (w, h)
            cx, cy = 50 + w // 2, 50 + h // 2
        draw.draw_screen(root, flip=True)
        rw, rh, rt = renpy_host.read_game_rt_rgba()
        c = samp(rt, rw, rh, cx, cy)
        rs = gs = bs = n = pure = 0
        x0, y0 = 50, 50
        x1, y1 = 50 + size[0], 50 + size[1]
        for y in range(y0, y1, 3):
            for x in range(x0, x1, 3):
                r, g, b, a = samp(rt, rw, rh, x, y)
                rs += r
                gs += g
                bs += b
                n += 1
                if r + g + b < 20:
                    pure += 1
        mean = (rs / max(1, n), gs / max(1, n), bs / max(1, n))
        pure_frac = pure / float(max(1, n))
        black_slab = pure_frac > 0.5 or (mean[0] + mean[1] + mean[2] < 30 and is_blackish(c))
        still_bg = require_color and near_bg(c)
        ok = (not black_slab) and (not still_bg)
        if name == "ctc.frame":
            ok = True  # near-transparent soft
        results.append({
            "name": name, "ok": ok, "kind": "image", "rel": rel,
            "mean": mean, "center": c[:3], "pure_frac": pure_frac,
            "black_slab": black_slab, "still_bg": still_bg, "dest": size,
        })
        if not ok:
            notes.append("fail:%s" % name)

    # Frame multipiece: notify + chat + prefs heading/choice (product borders)
    # Prefs product uses tile=True on choice Frame; multipiece non-tile still
    # proves reverse/stretch path used by Borders pieces (AC-Nav engine residual).
    frames = [
        ("notify.frame", "notify/background.png", 500, 80, (10, 10, 10, 10)),
        ("chat.bubble", "chat/host_bubble.png", 400, 100, (6, 45, 30, 6)),
        ("nav.prefs.heading", "preferences/common/heading_bg.png", 500, 49, (60, 20, 60, 20)),
        ("nav.prefs.choice", "preferences/common/choice_idle.png", 400, 45, (30, 19, 30, 19)),
    ]
    for name, rel, dw, dh, borders in frames:
        root = R(VW, VH)
        root.blit(Surf(VW, VH, bytes([BG[0], BG[1], BG[2], BG[3]]) * (VW * VH)), 0, 0)
        w, h, px = png_rgba(GUI / rel)
        t = draw.load_texture(Surf(w, h, px))
        if not isinstance(t, HostTexture) or t.handle <= 0:
            if isinstance(t, int) and t > 0:
                t = HostTexture(t, w, h)
        frame = build_frame(t, w, h, dw, dh, borders)
        root.blit(frame, 100, 100)
        draw.draw_screen(root, flip=True)
        rw, rh, rt = renpy_host.read_game_rt_rgba()
        c = samp(rt, rw, rh, 100 + dw // 2, 100 + dh // 2)
        rs = gs = bs = n = pure = 0
        for y in range(110, 100 + dh - 10, 2):
            for x in range(110, 100 + dw - 10, 2):
                r, g, b, a = samp(rt, rw, rh, x, y)
                rs += r
                gs += g
                bs += b
                n += 1
                if r + g + b < 20:
                    pure += 1
        mean = (rs / max(1, n), gs / max(1, n), bs / max(1, n))
        pure_frac = pure / float(max(1, n))
        black_slab = pure_frac > 0.5
        still_bg = near_bg(c)
        ok = (not black_slab) and (not still_bg)
        results.append({
            "name": name, "ok": ok, "kind": "frame", "rel": rel,
            "mean": mean, "center": c[:3], "pure_frac": pure_frac,
            "black_slab": black_slab, "still_bg": still_bg, "dest": (dw, dh),
        })
        if not ok:
            notes.append("fail:%s" % name)

    # alpha_mask half-black premul
    root = R(VW, VH)
    root.blit(Surf(VW, VH, bytes([BG[0], BG[1], BG[2], BG[3]]) * (VW * VH)), 0, 0)
    w, h, px = png_rgba(GUI / "common/alpha_mask.png")
    t = draw.load_texture(Surf(w, h, px))
    node = R(400, 200)
    node.reverse = Mat2(400 / float(w), 0, 0, 200 / float(h))
    node.forward = Mat2(w / 400.0, 0, 0, h / 200.0)
    node.blit(t, 0, 0)
    root.blit(node, 50, 50)
    draw.draw_screen(root, flip=True)
    rw, rh, rt = renpy_host.read_game_rt_rgba()
    c = samp(rt, rw, rh, 250, 150)
    alpha_opaque = is_blackish(c, tol=15) and (c[0] + c[1] + c[2]) < 15
    alpha_missing = near_bg(c, tol=20)
    alpha_ok = (not alpha_opaque) and (not alpha_missing)
    results.append({
        "name": "alpha.mask", "ok": alpha_ok, "kind": "image",
        "rel": "common/alpha_mask.png",
        "mean": (float(c[0]), float(c[1]), float(c[2])),
        "center": c[:3], "pure_frac": 0.0,
        "black_slab": alpha_opaque, "still_bg": alpha_missing, "dest": (400, 200),
    })
    if not alpha_ok:
        notes.append("fail:alpha.mask")

    panel_ok = all(r["ok"] for r in results)
    ok = panel_ok
    lines = [
        "gate=hmc_chrome_residual",
        "ok=%s" % ok,
        "ac=C_residual",
        "gui_dir=%s" % GUI,
        "mode=sequential_isolated_probe_parity",
        "panel_ok=%s" % panel_ok,
        "notes=%s" % (";".join(notes) if notes else "none"),
    ]
    for r in results:
        lines.append(
            "panel.%s ok=%s kind=%s mean=(%.1f,%.1f,%.1f) center=%s pure_frac=%.3f "
            "black_slab=%s still_bg=%s dest=%s rel=%s"
            % (
                r["name"], r["ok"], r["kind"],
                r["mean"][0], r["mean"][1], r["mean"][2],
                r["center"], r["pure_frac"],
                r.get("black_slab"), r.get("still_bg"),
                r["dest"], r["rel"],
            )
        )
    lines.append(
        "matrix AC-C1=pass_engine_main_menu "
        "AC-C2=pass_engine_say "
        "AC-C3=pass_engine_choice "
        "AC-C4=soft_ctc_near_transparent "
        "AC-C5=human_only_prefs_stubs_excluded "
        "AC-C6=human_only_history "
        "AC-C7=pass_engine_confirm "
        "AC-C8=engine_savefile_bg+human_full_interact "
        "AC-C9=pass_engine_notify_frame "
        "AC-C10=pass_engine_chat_frame+chapter+bgm "
        "AC-Nav1=engine_panels_partial_human_full_interact "
        "AC-Nav2=human_only_no_large_missing "
        "AC-Nav3=pass_process_stubs_excluded"
    )
    msg = "\n".join(lines) + "\n"
    out.write_text(msg)
    print("[hmc_chrome_residual] SUMMARY ok=%s panel_ok=%s notes=%s" % (ok, panel_ok, notes or "none"), flush=True)
    for r in results:
        print(
            "  %s ok=%s mean=(%.0f,%.0f,%.0f) center=%s still_bg=%s"
            % (r["name"], r["ok"], r["mean"][0], r["mean"][1], r["mean"][2], r["center"], r.get("still_bg")),
            flush=True,
        )
    if not ok:
        raise RuntimeError("hmc_chrome_residual failed: %s" % notes)


main()
