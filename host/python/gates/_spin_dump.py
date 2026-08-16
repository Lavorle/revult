import faulthandler, os, threading, time, runpy
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
