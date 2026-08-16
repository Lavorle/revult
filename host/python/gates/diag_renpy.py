import sys, traceback, os
out = open("/tmp/diag_renpy.txt", "w")
try:
    out.write(f"path0={sys.path[:6]!r}\n")
    out.write(f"renpy in modules before={ 'renpy' in sys.modules }\n")
    if 'renpy' in sys.modules:
        r = sys.modules['renpy']
        out.write(f"renpy={r!r} file={getattr(r,'__file__',None)!r}\n")
        out.write(f"has config={hasattr(r,'config')}\n")
        out.write(f"dir conf={[x for x in dir(r) if 'conf' in x.lower() or x=='host_build']}\n")
        try:
            out.write(f"config={r.config!r}\n")
        except Exception as e:
            out.write(f"access config FAIL {type(e).__name__}: {e}\n")
            out.write(traceback.format_exc())
    # try import
    import renpy
    out.write(f"import renpy ok host_build={getattr(renpy,'host_build',None)} has_config={hasattr(renpy,'config')}\n")
    out.write(f"renpy file={renpy.__file__}\n")
    # import config submodule
    import renpy.config
    out.write(f"import renpy.config ok module={renpy.config}\n")
    out.write(f"after import has attr={hasattr(renpy,'config')}\n")
except Exception:
    out.write("OUTER\n"+traceback.format_exc())
finally:
    out.close()
try:
    import renpy_host
    renpy_host.request_quit()
except Exception:
    pass
