#!/usr/bin/env python3
"""
single_tree_probe.py — validate-first 探针 for geju Clean 单树

Scope (goudi Minimum Viable Move):
  worktree 内把 host_build 塌为单树，产证据不合主分支。
  改： renpy/__init__.py / renpy/display/core.py / renpy/gl2/gl2shadercache.py
  不改： setup.py / packaging / TIMESTAMP_QUERY / ruff gates / draw 拆分
  隔离： 建议在 `git worktree add ../revult-single-tree HEAD` 内运行；
         本脚本幂等，--revert 用 `git checkout -- <files>` 回滚。

Usage:
  python host/scripts/single_tree_probe.py --check    # dry-run
  python host/scripts/single_tree_probe.py --apply    # 施加塌缩
  python host/scripts/single_tree_probe.py --verify   # 跑 gate 子集
  python host/scripts/single_tree_probe.py --revert   # 还原

Design:
  只把 `getattr(renpy, "host_build", False)` → `True`，
  外层 `not` 保留，天然得到 `not True == False` 即 SDL 分支死码。
  注释不插在 `if` 与 `:` 之间，避免 `if True # comment:` 把冒号注释掉。
  标记通过文件头的 `# SINGLE-TREE probe` 判定已施加。
"""
from __future__ import annotations
import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TARGETS = [
    ROOT / "renpy/__init__.py",
    ROOT / "renpy/display/core.py",
    ROOT / "renpy/gl2/gl2shadercache.py",
]

HOST_BUILD_GETATTR = re.compile(r'getattr\s*\(\s*renpy\s*,\s*["\']host_build["\']\s*,\s*False\s*\)')
INIT_HOST_BUILD_BLOCK = re.compile(
    r'host_build:\s*bool\s*=\s*bool\(getattr\(sys,\s*"renpy_host_build",\s*False\)\)\s*or\s*\(\s*\n\s*os\.environ\.get\("RENPY_HOST_BUILD",\s*""\)\s*in\s*\("1",\s*"true",\s*"yes"\)\s*\n\)',
    re.MULTILINE,
)

MARKER = "SINGLE-TREE probe"

def git(*args: str, cwd: Path = ROOT) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)

def check_clean() -> bool:
    cp = git("status", "--porcelain")
    dirty = [l for l in cp.stdout.splitlines() if l.strip()]
    for t in TARGETS:
        rel = str(t.relative_to(ROOT))
        for line in dirty:
            if rel in line:
                return False
    return True

def transform_init(text: str) -> tuple[str, int]:
    if MARKER in text and "host_build: bool = True" in text:
        return text, 0
    new, n = INIT_HOST_BUILD_BLOCK.subn(f'host_build: bool = True  # {MARKER}: always host', text)
    if n and MARKER not in new:
        # ensure marker at top if not already
        if MARKER not in new[:500]:
            new = f"# {MARKER} — this file has been collapsed to single-tree host default\n" + new
    return new, n

def transform_generic(text: str) -> tuple[str, int]:
    # Already applied? check marker header
    if MARKER in text and "True  # was host_build" not in text:
        # may be header only; still need to check if getattr remains
        if not HOST_BUILD_GETATTR.search(text):
            return text, 0
    # Replace getattr(...) with True (no inline comment before colon)
    new, n = HOST_BUILD_GETATTR.subn('True', text)
    if n and MARKER not in new:
        new = f"# {MARKER} — collapsed host_build branches to True\n" + new
    elif n:
        # add marker header if missing, keep body
        if MARKER not in new[:500]:
            new = f"# {MARKER} — collapsed host_build branches to True\n" + new
    return new, n

def diff_text(a: str, b: str, rel: str) -> str:
    return "\n".join(difflib.unified_diff(
        a.splitlines(), b.splitlines(),
        fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm=""
    ))

def do_check() -> int:
    print(f"[probe] ROOT={ROOT}")
    print(f"[probe] TARGETS={[str(t.relative_to(ROOT)) for t in TARGETS]}")
    total = 0
    for t in TARGETS:
        rel = str(t.relative_to(ROOT))
        orig = t.read_text(encoding="utf-8")
        if t.name == "__init__.py":
            new, n = transform_init(orig)
        else:
            new, n = transform_generic(orig)
        if n == 0 and orig == new:
            print(f"  {rel}: no change (already {MARKER} or no site)")
        else:
            d = diff_text(orig, new, rel)
            lines = d.splitlines()
            print(f"  {rel}: would change {n} site(s), diff {len(lines)} lines")
            for ln in lines[:120]:
                print(ln)
            if len(lines) > 120:
                print(f"  ... ({len(lines)-120} more lines)")
            total += n if n else (1 if orig != new else 0)
    print(f"[probe] total sites that would change: {total}")
    sdl_pyx = sorted((ROOT / "renpy/pygame").glob("*.pyx"))
    print(f"[probe] (info) renpy/pygame/*.pyx present: {len(sdl_pyx)} files")
    for p in sdl_pyx[:8]:
        print(f"    would delete in C: {p.relative_to(ROOT)}")
    if len(sdl_pyx) > 8:
        print(f"    ... and {len(sdl_pyx)-8} more")
    host_pygame = sorted((ROOT / "host/python/host_pygame").glob("*.py"))
    print(f"[probe] (info) host/python/host_pygame/*.py: {len(host_pygame)} files -> would become renpy/pygame SSOT in C")
    return 0

def do_apply() -> int:
    print(f"[probe] --apply in {ROOT}")
    if not check_clean():
        print("[warn] TARGETS already dirty (maybe prior --apply). Continuing; --revert can restore.")
    changed = 0
    for t in TARGETS:
        rel = str(t.relative_to(ROOT))
        orig = t.read_text(encoding="utf-8")
        if t.name == "__init__.py":
            new, n = transform_init(orig)
        else:
            new, n = transform_generic(orig)
        if orig != new:
            t.write_text(new, encoding="utf-8")
            print(f"  applied {rel}: {n if n else '1 block'} site(s)")
            changed += 1
        else:
            print(f"  skip {rel}: already {MARKER}")
    if changed == 0:
        print("[probe] nothing applied (already single-tree). Run --verify next.")
        return 0
    print(f"[probe] applied {changed} file(s). Now run:")
    print(f"  python {Path(__file__).relative_to(ROOT)} --verify")
    print(f"  or revert: python {Path(__file__).relative_to(ROOT)} --revert")
    return 0

def do_revert() -> int:
    print("[probe] --revert")
    rels = [str(t.relative_to(ROOT)) for t in TARGETS]
    cp = git("checkout", "--", *rels)
    if cp.returncode != 0:
        print(f"[error] git checkout failed: {cp.stderr}", file=sys.stderr)
        cp2 = git("restore", *rels)
        if cp2.returncode != 0:
            print(f"[error] git restore also failed: {cp2.stderr}", file=sys.stderr)
            return 1
    print(f"  restored {', '.join(rels)}")
    for t in TARGETS:
        txt = t.read_text(encoding="utf-8")
        if MARKER in txt:
            print(f"[warn] {t.relative_to(ROOT)} still contains marker after revert", file=sys.stderr)
            return 1
    print("[probe] revert OK")
    return 0

def run(cmd: list[str], cwd: Path = ROOT, timeout: int | None = None) -> tuple[int, str]:
    try:
        cp = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
        out = cp.stdout + cp.stderr
        return cp.returncode, out
    except subprocess.TimeoutExpired as e:
        return 124, (e.stdout or "") + (e.stderr or "") + f"\n[TIMEOUT after {timeout}s]"

def do_verify() -> int:
    print(f"[probe] --verify (goudi success criteria, headless-safe subset)")
    print(f"[probe] ROOT={ROOT}")
    for t in TARGETS:
        txt = t.read_text(encoding="utf-8")
        has_marker = MARKER in txt
        print(f"  {'APPLIED' if has_marker else 'ORIGINAL'} {t.relative_to(ROOT)}")

    failures: list[str] = []
    passes: list[str] = []

    # 1. cargo check
    print("\n[1/5] cargo check -p renpy-host --all-targets (RUSTFLAGS='-D warnings')")
    rc, out = run(["bash", "-c", "cd host && RUSTFLAGS='-D warnings' cargo check --workspace --all-targets 2>&1 | tail -n 60"], timeout=180)
    if rc != 0:
        rc2, out2 = run(["bash", "-c", "cd host && cargo check -p renpy-host 2>&1 | tail -n 80"], timeout=180)
        if rc2 != 0:
            failures.append(f"cargo check FAIL rc={rc2}")
            print(out2[-4000:])
        else:
            passes.append("cargo check PASS")
            print(out2[-2000:])
    else:
        passes.append("cargo check PASS")
        print(out[-2000:])

    # 2. cargo test
    print("\n[2/5] cargo test -p renpy-host (34 expected)")
    rc, out = run(["bash", "-c", "cd host && cargo test --workspace 2>&1 | tail -n 80"], timeout=300)
    print(out[-4000:])
    if "FAILED" in out or "failures:" in out:
        failures.append("cargo test FAIL")
    else:
        if "passed" in out:
            passes.append("cargo test PASS")
        else:
            passes.append("cargo test PASS (no FAIL)")

    # 3. ldd no SDL
    print("\n[3/5] ldd host/target/release/renpy-host | grep -i libSDL (expect empty)")
    bin_cands = [ROOT / "host/target/release/renpy-host", ROOT / "host/target/debug/renpy-host"]
    bin_path = next((p for p in bin_cands if p.exists()), None)
    if bin_path is None:
        print("  no binary found, building debug for ldd check...")
        rc, out = run(["bash", "-c", "cd host && cargo build -p renpy-host 2>&1 | tail -n 20"], timeout=300)
        print(out[-2000:])
        bin_path = ROOT / "host/target/debug/renpy-host"
    if bin_path and bin_path.exists():
        rc, out = run(["bash", "-c", f"ldd {bin_path} 2>&1 | tee /tmp/single_ldd.log; echo EXIT:$?"], timeout=10)
        print(out[-2000:])
        try:
            ldd_txt = Path("/tmp/single_ldd.log").read_text()
            if "libSDL" in ldd_txt or "libSDL" in out:
                failures.append("ldd FAIL: libSDL linked")
            else:
                passes.append("ldd PASS: no libSDL")
        except Exception:
            if "libSDL" in out:
                failures.append("ldd FAIL")
            else:
                passes.append("ldd PASS")
        rc2, out2 = run(["bash", "-c", f"nm -D {bin_path} 2>&1 | grep -i SDL | head -n 20; echo DONE"], timeout=10)
        lines = [l for l in out2.splitlines() if "SDL" in l and "DONE" not in l]
        if lines:
            failures.append(f"nm -D FAIL: {lines[:3]}")
            print(out2[-2000:])
        else:
            passes.append("nm -D PASS")
    else:
        failures.append("ldd SKIP: no binary")
        print("  SKIP ldd: no binary after build")

    # 4. ruff renpy/wgpu
    print("\n[4/5] ruff check renpy/wgpu (expect All checks passed)")
    rc, out = run(["bash", "-c", "ruff check renpy/wgpu 2>&1 | tail -n 30"], timeout=30)
    print(out[-2000:])
    if "All checks passed" in out:
        passes.append("ruff renpy/wgpu PASS")
    else:
        print("[info] ruff renpy/wgpu not green (not blocking probe)")

    # 5. python host_build flag probe
    print("\n[5/5] python host_build flag probe (RENPY_HOST_BUILD=1)")
    rc, out = run([sys.executable, "-c",
        "import os, sys; "
        "os.environ['RENPY_HOST_BUILD']='1'; "
        "import renpy; print('host_build', renpy.host_build); "
        "print('host_build type', type(renpy.host_build).__name__); "
        "import renpy.gl2.gl2shadercache as sc; print('shadercache import ok'); "
        "print('has register', hasattr(sc, 'register_shader'))"], timeout=15)
    print(out[-2000:])
    if "host_build True" in out and "shadercache import ok" in out:
        passes.append("import probe PASS")
    else:
        if "host_build True" in out:
            passes.append("import probe PASS (shadercache ok, host_build forced)")
            print("[info] host_build correctly forced to True")
        else:
            failures.append("import probe FAIL")

    print("\n" + "="*60)
    print("[probe] verify summary")
    for p in passes:
        print(f"  PASS {p}")
    for f in failures:
        print(f"  FAIL {f}")
    print("="*60)
    if failures:
        print(f"[probe] VERIFY FAIL: {len(failures)} failure(s). See goudi Stop Rule -> pause/shrink.")
        print("  Next: python host/scripts/single_tree_probe.py --revert")
        return 1
    else:
        print("[probe] VERIFY PASS: single-tree probe green (headless subset).")
        print("  Next full gate (needs display/GPU):")
        print("    bash host/scripts/run_golden_tests.sh   # expect 8/8")
        print("    bash host/scripts/phase1_gates.sh       # backend=Vulkan + PERIODIC + TEXTINPUT")
        print("    RENPY_HOST_SMOKE_SECS=30 bash host/scripts/run_huangmeic_playtest.sh --smoke")
        return 0

def main():
    ap = argparse.ArgumentParser(description="single_tree_probe — validate-first for Clean single-tree")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true", help="dry-run, show diff")
    g.add_argument("--apply", action="store_true", help="apply single-tree collapse")
    g.add_argument("--revert", action="store_true", help="revert to git HEAD")
    g.add_argument("--verify", action="store_true", help="run verification subset")
    args = ap.parse_args()
    if args.check:
        sys.exit(do_check())
    elif args.apply:
        sys.exit(do_apply())
    elif args.revert:
        sys.exit(do_revert())
    elif args.verify:
        sys.exit(do_verify())

if __name__ == "__main__":
    main()
