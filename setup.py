#!/usr/bin/env python

# Copyright 2004-2025 Tom Rothamel <pytom@bishoujo.us>
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

import sys
import os

# Change to the directory containing this file.
BASE = os.path.abspath(os.path.dirname(sys.argv[0]))
os.chdir(BASE)

SCRIPTS = os.path.join(BASE, "scripts")
sys.path.insert(0, SCRIPTS)

import setuplib
from setuplib import windows, cython as _cython, find_unnecessary_gen, generate_all_cython, env

import generate_styles

# Host is default (Phase 9 strip): SDL-free Class A only. Set RENPY_HOST_BUILD=0 to force SDL reference (if sources still present).
# See .omc/research/host-cython-inventory.md. Never packages=sdl* on this path.
HOST_BUILD = os.environ.get("RENPY_HOST_BUILD", "1") in ("1", "true", "yes")

# Exact allowlist for host (plus renpy.styledata.style_*functions via prefix match).
HOST_ALLOW = {
    "renpy.astsupport",
    "renpy.cslots",
    "renpy.lexersupport",
    "renpy.pydict",
    "renpy.style",
    "renpy.encryption",
    "renpy.tfd",
    "renpy.ecsign",
    "renpy.audio.filter",
    "renpy.styledata.styleclass",
    "renpy.styledata.stylesets",
    "renpy.display.matrix",
    "renpy.display.render",
    "renpy.display.quaternion",
    "renpy.gl2.gl2mesh",
    "renpy.gl2.gl2mesh2",
    "renpy.gl2.gl2mesh3",
    "renpy.gl2.gl2polygon",
    "renpy.text.textsupport",
    "renpy.text.texwrap",
    "renpy.text.bidi",
}


def _host_allowed(name: str) -> bool:
    if name in HOST_ALLOW:
        return True
    # Generated style function modules: renpy.styledata.style_<prefix>functions
    if name.startswith("renpy.styledata.style_") and name.endswith("functions"):
        return True
    return False


def cython(name, source=[], pyx=None, language="c", compile_args=[], define_macros=[], packages=""):
    """
    Wrapper around setuplib.cython. On RENPY_HOST_BUILD=1, only Class A modules
    are registered, and any packages=*sdl* is a hard error (must not dlopen libSDL).
    """
    if HOST_BUILD:
        if not _host_allowed(name):
            print("host build: skip", name)
            return
        for pkg in packages.split():
            if "sdl" in pkg.lower():
                raise SystemExit(
                    "host build refused packages containing sdl for {}: {!r}".format(name, packages)
                )
    _cython(
        name,
        source=source,
        pyx=pyx,
        language=language,
        compile_args=compile_args,
        define_macros=define_macros,
        packages=packages,
    )


def main():

    setuplib.init()
    setuplib.check_imports(SCRIPTS, "setuplib.py", "generate_styles.py")

    generate_styles.generate()

    # These control the level of optimization versus debugging.
    setuplib.extra_compile_args = ["-Wno-unused-function"]
    setuplib.extra_link_args = []

    cubism = os.environ.get("CUBISM", None)
    if cubism:
        setuplib.include_dirs.append("{}/Core/include".format(cubism))

    if HOST_BUILD:
        print("RENPY_HOST_BUILD=1: building SDL-free Class A Cython subset only")

    # src/ directory.
    cython("_renpy", ["src/IMG_savepng.c", "src/core.c"], packages="sdl3 libpng")

    # renpy.pygame
    cython("renpy.pygame.iostream", packages="sdl3")
    cython("renpy.pygame.locals", packages="sdl3")
    cython(
        "renpy.pygame.image",
        source=["src/pygame/write_png.c", "src/pygame/write_jpeg.c"],
        packages="sdl3-image libjpeg libpng sdl3",
    )
    cython("renpy.pygame.sdl_image", packages="sdl3")
    cython("renpy.pygame.controller", packages="sdl3")
    cython("renpy.pygame.joystick", packages="sdl3")
    cython("renpy.pygame.pygame_time", packages="sdl3")
    cython("renpy.pygame.power", packages="sdl3")
    cython("renpy.pygame.transform", source=["src/pygame/SDL3_rotozoom.c"], packages="sdl3")
    cython("renpy.pygame.scrap", packages="sdl3")
    cython("renpy.pygame.key", packages="sdl3")
    cython("renpy.pygame.mouse", packages="sdl3")
    cython("renpy.pygame.event", packages="sdl3")
    cython("renpy.pygame.display", packages="sdl3")
    cython("renpy.pygame.sdl", packages="sdl3")
    cython("renpy.pygame.color", packages="sdl3")
    cython("renpy.pygame.rect", packages="sdl3")
    cython("renpy.pygame.error", packages="sdl3")
    cython("renpy.pygame.surface", packages="sdl3")
    cython("renpy.pygame.draw", packages="sdl3")
    cython(
        "renpy.pygame.gfxdraw",
        source=["src/pygame/SDL3_gfxPrimitives.c", "src/pygame/SDL3_rotozoom.c"],
        packages="sdl3",
    )

    # renpy
    cython("renpy.astsupport")
    cython("renpy.cslots")
    cython("renpy.lexersupport")
    cython("renpy.pydict")
    cython("renpy.style")
    cython("renpy.encryption")
    cython("renpy.tfd", ["src/tinyfiledialogs/tinyfiledialogs.c"])
    cython("renpy.ecsign", ["src/ec_sign_core.c"], packages="openssl")

    # renpy.audio
    cython(
        "renpy.audio.renpysound",
        ["src/renpysound_core.c", "src/ffmedia.c"],
        compile_args=["-Wno-deprecated-declarations"]
        if ("RENPY_FFMPEG_NO_DEPRECATED_DECLARATIONS" in os.environ)
        else [],
        packages="libavformat libavcodec libavutil libswresample libswscale sdl3",
    )

    cython("renpy.audio.filter")

    # renpy.styledata
    cython("renpy.styledata.styleclass")
    cython("renpy.styledata.stylesets")

    for p in generate_styles.prefixes:
        cython("renpy.styledata.style_{}functions".format(p), pyx=setuplib.gen + "/style_{}functions.pyx".format(p))

    # renpy.display
    cython("renpy.display.matrix")
    cython("renpy.display.render")
    cython("renpy.display.accelerator", packages="sdl3")
    cython("renpy.display.quaternion")

    # renpy.uguu
    cython("renpy.uguu.gl", packages="sdl3")
    cython("renpy.uguu.uguu", packages="sdl3")

    # renpy.gl2
    cython("renpy.gl2.gl2mesh")
    cython("renpy.gl2.gl2mesh2")
    cython("renpy.gl2.gl2mesh3")
    cython("renpy.gl2.gl2polygon")
    cython("renpy.gl2.gl2model")
    cython("renpy.gl2.gl2draw", packages="sdl3")
    cython("renpy.gl2.gl2texture", packages="sdl3")
    cython("renpy.gl2.gl2uniform")
    cython("renpy.gl2.gl2shader")

    if cubism:
        cython("renpy.gl2.live2dmodel", ["src/live2dcsm.c"], packages="sdl3")

    cython("renpy.gl2.assimp", ["src/assimpio.cc"], language="c++", packages="assimp sdl3")

    # renpy.text
    cython("renpy.text.textsupport")
    cython("renpy.text.texwrap")
    cython("renpy.text.ftfont", ["src/ftsupport.c", "src/ttgsubtable.c"], packages="freetype2 harfbuzz sdl3")
    cython("renpy.text.hbfont", ["src/ftsupport.c"], packages="freetype2 harfbuzz sdl3")
    cython("renpy.text.bidi", ["src/renpybidicore.c"], packages="fribidi")

    generate_all_cython()
    find_unnecessary_gen()

    env("CC")
    env("LD")
    env("CXX")
    env("CFLAGS")
    env("LDFLAGS")

    setuplib.setup("renpy", "8.99.99")


if __name__ == "__main__":
    main()
