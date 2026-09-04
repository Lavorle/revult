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

# Single-tree (Phase 9 strip complete): only the SDL-free extension set below
# is registered. The SDL reference graph is retired — its .pyx sources are
# absent and no packages=*sdl* entry remains. See doc/strip-phase9-inventory.md.
def cython(name, source=[], pyx=None, language="c", compile_args=[], define_macros=[], packages=""):
    """
    Wrapper around setuplib.cython. Single-tree: every registered module must
    be SDL-free; any packages=*sdl* is a hard error.
    """
    for pkg in packages.split():
        if "sdl" in pkg.lower():
            raise SystemExit(
                "single-tree refused packages containing sdl for {}: {!r}".format(name, packages)
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

    # Single-tree: pygame SDL extensions retired (host uses host_pygame).
    # _renpy retired (host uses renpy.audio.renpysound_host / _renpy_host).

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
    # renpysound retired (SDL audio; host uses cpal via renpysound_host).

    cython("renpy.audio.filter")

    # renpy.styledata
    cython("renpy.styledata.styleclass")
    cython("renpy.styledata.stylesets")

    for p in generate_styles.prefixes:
        cython("renpy.styledata.style_{}functions".format(p), pyx=setuplib.gen + "/style_{}functions.pyx".format(p))

    # renpy.display
    cython("renpy.display.matrix")
    cython("renpy.display.render")
    # accelerator retired (SDL; .pyx cimports renpy.pygame.sdl).
    cython("renpy.display.quaternion")

    # renpy.uguu retired (SDL/GL; .pyx cimports renpy.pygame.sdl).

    # renpy.gl2
    cython("renpy.gl2.gl2mesh")
    cython("renpy.gl2.gl2mesh2")
    cython("renpy.gl2.gl2mesh3")
    cython("renpy.gl2.gl2polygon")
    # gl2model retired (cimports gl2texture, whose .pyx is SDL/retired — unimportable).
    # gl2draw/gl2texture retired (SDL). gl2uniform/gl2shader retired (.pyx absent).
    # live2dmodel retired (SDL/Cubism GL; host covers Live2D via WGSL parts).
    # assimp retired (SDL package; host mesh path via model.py blob upload).

    # renpy.text
    cython("renpy.text.textsupport")
    cython("renpy.text.texwrap")
    # ftfont/hbfont retired (SDL package; host text via Pillow bitmap upload).
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
