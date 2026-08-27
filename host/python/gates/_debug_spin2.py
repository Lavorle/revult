import faulthandler
import os
import runpy
import threading
import time

from host.python.gates._harness import gate_harness, parametrized_gate
f=open("/tmp/fault2.log","w")  # noqa: SIM115
faulthandler.enable(file=f, all_threads=False)
def dump_loop():
    while True:
        time.sleep(3)
        with open("/tmp/fault2.log","a") as out:
            out.write("\n===== dump =====\n"); out.flush()
            faulthandler.dump_traceback(file=out, all_threads=False)
threading.Thread(target=dump_loop, daemon=True).start()
runpy.run_path(os.path.join(os.environ.get("RENPY_HOST_BASE","/mnt/nvme1n1p2/revult"),"host/python/gates/product.py"), run_name="__main__")

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
