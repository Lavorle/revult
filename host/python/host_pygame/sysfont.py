"""Minimal sysfont stub for host (no OS font enumeration required)."""

Sysfonts = {}
Sysalias = {}


def initsysfonts():
    return None


def SysFont(name, size, bold=False, italic=False):
    return None
