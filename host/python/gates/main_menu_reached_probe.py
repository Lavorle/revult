"""Assert main menu is entered even when SKIP_MAIN_MENU was '0' before product gate."""
import os
import sys
import threading
import time
from pathlib import Path

try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback

# Poison like the old run_the_question.sh
os.environ["RENPY_SKIP_MAIN_MENU"] = "0"
os.environ["RENPY_SKIP_SPLASHSCREEN"] = "0"

# product.py run() is module-level - we cannot import it. Replicate normalize then boot.
base = Path(os.environ.get("RENPY_HOST_BASE", "/mnt/nvme1n1p2/revult"))
sys.path.insert(0, str(base / "host" / "python" / "gates"))

def _clear_falsey_skip(name):
    val = os.environ.get(name)
    if val is None:
        return
    if str(val).strip().lower() in ("", "0", "false", "no", "off", "n"):
        os.environ.pop(name, None)

_clear_falsey_skip("RENPY_SKIP_MAIN_MENU")
_clear_falsey_skip("RENPY_SKIP_SPLASHSCREEN")
assert "RENPY_SKIP_MAIN_MENU" not in os.environ, "normalize failed"
assert "RENPY_SKIP_SPLASHSCREEN" not in os.environ, "splash normalize failed"

os.environ.setdefault("RENPY_HOST_BASE", str(base))
os.environ.setdefault("RENPY_HOST_BUILD", "1")
os.environ.setdefault("RENPY_HOST_GAME", str(base / "the_question"))
os.environ.setdefault("RENPY_PERFORMANCE_TEST", "0")

import bootstrap as boot
import renpy_host

boot.stage_import_renpy()
boot.stage_import_all()
boot.stage_set_game_dir(base)
import renpy

renpy.host_build = True
try:
    import renpy_main_host
    renpy_main_host.install(renpy)
except Exception:
    pass
try:
    import renpy.audio as _ra
    import renpy.audio.renpysound_host as _rs
    sys.modules["renpy.audio.renpysound"] = _rs
    _ra.renpysound = _rs
except Exception:
    pass
import renpy.arguments

sys.argv = [sys.argv[0] if sys.argv else "h", str(base/"the_question"), "run"]
try:
    renpy.arguments.register_command("run", renpy.arguments.run, True)
except Exception:
    pass
renpy.game.args = renpy.arguments.bootstrap()

seen = {"main_menu": False}
stop = threading.Event()
def watch():
    for _ in range(300):
        if stop.is_set():
            return
        try:
            if bool(getattr(renpy.store, "main_menu", False)):
                seen["main_menu"] = True
                renpy_host.request_quit()
                return
        except Exception:
            pass
        time.sleep(0.05)
    renpy_host.request_quit()

t = threading.Thread(target=watch, daemon=True)
t.start()
try:
    import renpy.main as m
    m.main()
except BaseException as e:
    print("exit", type(e).__name__, e)
stop.set()
ok = seen["main_menu"]
out = base/"host"/"target"/"gate-main_menu_reached_probe.txt"
body = f"ok={ok}\nSKIP_MAIN_MENU env after normalize={os.environ.get('RENPY_SKIP_MAIN_MENU')!r}\nmain_menu_seen={ok}\n"
out.write_text(body)
print(body)
if not ok:
    raise SystemExit(1)

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
