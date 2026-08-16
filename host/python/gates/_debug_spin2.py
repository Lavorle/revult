import faulthandler, os, sys, threading, time, runpy
f=open("/tmp/fault2.log","w")
faulthandler.enable(file=f, all_threads=False)
def dump_loop():
    while True:
        time.sleep(3)
        with open("/tmp/fault2.log","a") as out:
            out.write("\n===== dump =====\n"); out.flush()
            faulthandler.dump_traceback(file=out, all_threads=False)
threading.Thread(target=dump_loop, daemon=True).start()
runpy.run_path(os.path.join(os.environ.get("RENPY_HOST_BASE","/mnt/nvme1n1p2/revult"),"host/python/gates/product.py"), run_name="__main__")
