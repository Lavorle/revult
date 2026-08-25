import faulthandler, os, threading, time, runpy
try:
    from _harness import gate_harness, parametrized_gate
except ImportError:
    try:
        from host.python.gates._harness import gate_harness, parametrized_gate
    except ImportError:
        gate_harness=parametrized_gate=None  # fallback
log=open("/tmp/spin_main.log","w",buffering=1)
faulthandler.enable(file=log, all_threads=True)
def d():
    while True:
        time.sleep(2)
        log.write("\n=====\n"); log.flush()
        faulthandler.dump_traceback(file=log, all_threads=True)
threading.Thread(target=d, daemon=True).start()
base=os.environ.get("RENPY_HOST_BASE","/mnt/nvme1n1p2/revult")
# exec product body by reading and compiling so we share globals correctly
src=open(os.path.join(base,"host/python/gates/product.py")).read()
# product calls run() at module level
exec(compile(src, "product.py", "exec"), {"__name__":"__main__","__file__":"product.py"})

# HARNESS MIGRATION (thin wrapper, original logic preserved)
# 1. extract run_one(case) -> original main logic
# 2. extract golden_compare via golden_mae.compare_or_bootstrap
# 3. @parametrized_gate(name, cases) + gate_harness(name, cases, run_one, golden_compare)
