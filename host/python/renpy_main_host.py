"""
Host-side renpy.__main__ path helpers.

Under renpy-host, renpy.py is not the process __main__. renpy.main.main() and
renpy.bootstrap.bootstrap() still call renpy.__main__.path_to_common /
path_to_gamedir / path_to_saves / predefined_searchpath / path_to_logdir.

This module mirrors the helpers from the repo-root renpy.py. The host embed
preamble (python.rs) installs it as:

    sys.modules["renpy.__main__"] = renpy_main_host
    renpy.__main__ = renpy_main_host

Do not import renpy at module level for the pure path helpers that do not need
it; path_to_saves / predefined_searchpath / path_to_logdir import renpy lazily.
"""

from __future__ import annotations

import os
import sys


def path_to_gamedir(basedir, name):
    """
    Returns the absolute path to the directory containing the game scripts
    and assets. (This becomes config.gamedir.)

    `basedir`
        The base directory (config.basedir)
    `name`
        The basename of the executable, with the extension removed.
    """

    candidates = [name]

    game_name = name
    while game_name:
        prefix = game_name[0]
        game_name = game_name[1:]
        if prefix == " " or prefix == "_":
            candidates.append(game_name)

    candidates.extend(["game", "data", "launcher/game"])

    for i in candidates:
        if i == "renpy":
            continue
        gamedir = os.path.join(basedir, i)
        if os.path.isdir(gamedir):
            break
    else:
        gamedir = basedir

    return gamedir


def path_to_common(renpy_base):
    """
    Returns the absolute path to the Ren'Py common directory, or None.

    `renpy_base`
        The absolute path to the Ren'Py base directory.
    """
    path = renpy_base + "/renpy/common"
    if os.path.isdir(path):
        return path
    return None


def path_to_saves(gamedir, save_directory=None):  # type: (str, str|None) -> str
    """
    Given the path to a Ren'Py game directory, and the value of
    config.save_directory, returns absolute path to the directory where
    save files will be placed.
    """
    import renpy  # @UnresolvedImport

    if save_directory is None:
        save_directory = renpy.config.save_directory

    def test_writable(d):
        try:
            fn = os.path.join(d, "test.txt")
            open(fn, "w").close()
            open(fn, "r").close()
            os.unlink(fn)
            return True
        except Exception:
            return False

    if renpy.android:
        paths = [
            os.path.join(os.environ["ANDROID_OLD_PUBLIC"], "game/saves"),
            os.path.join(os.environ["ANDROID_PRIVATE"], "saves"),
            os.path.join(os.environ["ANDROID_PUBLIC"], "saves"),
        ]
        for rv in paths:
            if os.path.isdir(rv) and test_writable(rv):
                break
        else:
            rv = paths[-1]
        print("Saving to", rv)
        return rv

    if renpy.ios:
        from pyobjus import autoclass  # type: ignore
        from pyobjus.objc_py_types import enum  # type: ignore

        NSSearchPathDirectory = enum("NSSearchPathDirectory", NSDocumentDirectory=9)
        NSSearchPathDomainMask = enum("NSSearchPathDomainMask", NSUserDomainMask=1)

        NSFileManager = autoclass("NSFileManager")
        manager = NSFileManager.defaultManager()
        url = manager.URLsForDirectory_inDomains_(
            NSSearchPathDirectory.NSDocumentDirectory,
            NSSearchPathDomainMask.NSUserDomainMask,
        ).lastObject()

        try:
            rv = url.path().UTF8String()
        except Exception:
            rv = url.path.UTF8String()

        if isinstance(rv, bytes):
            rv = rv.decode("utf-8")

        print("Saving to", rv)
        return rv

    if not save_directory:
        return os.path.join(gamedir, "saves")

    if "RENPY_PATH_TO_SAVES" in os.environ:
        return os.environ["RENPY_PATH_TO_SAVES"] + "/" + save_directory

    path = renpy.config.renpy_base
    while True:
        if os.path.isdir(path + "/Ren'Py Data"):
            return path + "/Ren'Py Data/" + save_directory
        newpath = os.path.dirname(path)
        if path == newpath:
            break
        path = newpath

    if renpy.macintosh:
        rv = "~/Library/RenPy/" + save_directory
        return os.path.expanduser(rv)
    elif renpy.windows:
        if "APPDATA" in os.environ:
            return os.environ["APPDATA"] + "/RenPy/" + save_directory
        else:
            rv = "~/RenPy/" + renpy.config.save_directory  # type: ignore
            return os.path.expanduser(rv)
    else:
        rv = "~/.renpy/" + save_directory
        return os.path.expanduser(rv)


def path_to_renpy_base():
    """
    Returns the absolute path to the Ren'Py base directory under host embed.

    Prefer RENPY_HOST_BASE; fall back to walking from this file (host/python →
    repo root) or cwd.
    """
    env = os.environ.get("RENPY_HOST_BASE")
    if env:
        return os.path.abspath(env)

    # host/python/renpy_main_host.py → repo root is two levels up.
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.abspath(os.path.join(here, "..", ".."))
    if os.path.isdir(os.path.join(candidate, "renpy")):
        return candidate

    return os.path.abspath(os.getcwd())


def path_to_logdir(basedir):
    """
    Returns the absolute path to the log directory.
    `basedir`
        The base directory (config.basedir)
    """
    import renpy  # @UnresolvedImport

    if renpy.android:
        return os.environ["ANDROID_PUBLIC"]
    return basedir


def predefined_searchpath(commondir):
    import renpy  # @UnresolvedImport

    searchpath = [renpy.config.gamedir]

    if renpy.android:
        if "ANDROID_PUBLIC" in os.environ:
            android_game = os.path.join(os.environ["ANDROID_PUBLIC"], "game")
            if os.path.exists(android_game):
                searchpath.insert(0, android_game)

        packs = [
            "ANDROID_PACK_FF1",
            "ANDROID_PACK_FF2",
            "ANDROID_PACK_FF3",
            "ANDROID_PACK_FF4",
        ]
        for i in packs:
            if i not in os.environ:
                continue
            assets = os.environ[i]
            for j in ["renpy/common", "game"]:
                dn = os.path.join(assets, j)
                if os.path.isdir(dn):
                    searchpath.append(dn)
    else:
        if "RENPY_SEARCHPATH" in os.environ:
            searchpath.extend(os.environ["RENPY_SEARCHPATH"].split("::"))

    if commondir and os.path.isdir(commondir):
        searchpath.append(commondir)

    if renpy.android or renpy.ios:
        print("Mobile search paths:", " ".join(searchpath))

    return searchpath


def install(renpy_module=None):
    """
    Install this module as renpy.__main__ and sys.modules['renpy.__main__'].

    Safe to call more than once. Returns this module.
    """
    mod = sys.modules[__name__]
    sys.modules["renpy.__main__"] = mod

    if renpy_module is None:
        renpy_module = sys.modules.get("renpy")
    if renpy_module is not None:
        renpy_module.__main__ = mod  # type: ignore[attr-defined]

    return mod


# Also expose as a module attribute for getattr(renpy.__main__, ...) probes.
__all__ = [
    "path_to_gamedir",
    "path_to_common",
    "path_to_saves",
    "path_to_renpy_base",
    "path_to_logdir",
    "predefined_searchpath",
    "install",
]
