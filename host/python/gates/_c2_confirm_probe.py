"""C2 thrash confirm present probe — count HT/dead and frame pairing."""
import os, sys, threading, time
from pathlib import Path

def _base():
    return Path(os.environ.get("RENPY_HOST_BASE") or "/mnt/nvme1n1p2/revult")

def _log(m):
    try:
        sys.__stdout__.write("[c2_probe] %s\n" % m); sys.__stdout__.flush()
    except Exception:
        pass
    open("/tmp/c2_confirm_probe.log","a").write(m+"\n")

def _quit():
    try:
        import renpy_host; renpy_host.request_quit()
    except Exception:
        pass

def _clear_falsey(n):
    v=os.environ.get(n)
    if v is not None and str(v).strip().lower() in ("","0","false","no","off","n"):
        os.environ.pop(n, None)

def _pre():
    import types
    try:
        import renpy.audio.renpysound_host as h, renpy.audio as a
        sys.modules["renpy.audio.renpysound"]=h; a.renpysound=h
    except Exception as e: _log("sound %s"%e)
    try:
        import host_pygame, host_pygame.locals as loc, host_pygame.scrap as scrap
        if not hasattr(host_pygame,"constants"): host_pygame.constants=loc
        sys.modules.setdefault("renpy.pygame.constants", host_pygame.constants)
        sys.modules["renpy.pygame.scrap"]=scrap; sys.modules["pygame.scrap"]=scrap
        import renpy.pygame as rpg
        if not hasattr(rpg,"constants"): rpg.constants=host_pygame.constants
        try: rpg.scrap=scrap
        except Exception: pass
        try: rpg.import_as_pygame()
        except Exception: pass
    except Exception as e: _log("pygame %s"%e)
    try:
        import renpy_uguu_host as u
        sys.modules["renpy.uguu.uguu"]=u; sys.modules["renpy.uguu.gl"]=u
        pkg=sys.modules.get("renpy.uguu") or types.ModuleType("renpy.uguu"); pkg.__path__=[]
        sys.modules["renpy.uguu"]=pkg
        for n in dir(u):
            if n.startswith("GL_") or n in ("clear_errors","get_error"): setattr(pkg,n,getattr(u,n))
        pkg.uguu=u; pkg.gl=u
        import renpy; renpy.uguu=pkg
    except Exception as e: _log("uguu %s"%e)
    try:
        import renpy_ecsign_host as e
        sys.modules["renpy.ecsign"]=e
        import renpy; renpy.ecsign=e
    except Exception as e: _log("ecsign %s"%e)

def _sample():
    import renpy_host
    rw,rh,rt=renpy_host.read_game_rt_rgba()
    if not rw: return {"ok":False,"err":"empty"}
    rs=gs=bs=n=0; step_x=max(1,rw//32); step_y=max(1,rh//18)
    for y in range(step_y//2,rh,step_y):
        for x in range(step_x//2,rw,step_x):
            o=(y*rw+x)*4; r,g,b=rt[o],rt[o+1],rt[o+2]
            rs+=r; gs+=g; bs+=b; n+=1
    mean=(rs/n,gs/n,bs/n)
    var=sum((rt[(y*rw+x)*4]-mean[0])**2+(rt[(y*rw+x)*4+1]-mean[1])**2+(rt[(y*rw+x)*4+2]-mean[2])**2
            for y in range(step_y//2,rh,step_y) for x in range(step_x//2,rw,step_x))/n
    clear=abs(mean[0]-13)<8 and abs(mean[1]-13)<8 and abs(mean[2]-20)<12 and var<5
    return {"ok":not clear,"mean":mean,"var":var,"w":rw,"h":rh,"clear":clear}

def _frame_state():
    import renpy_host
    d={}
    for name in ("in_frame","frame_depth","sample_texture_count","texture_order_len"):
        try:
            d[name]=getattr(renpy_host,name)()
        except Exception as e:
            d[name]="err:%s"%e
    return d

def _walk_ht(node, budget=None, acc=None):
    from renpy.wgpu.draw import HostTexture
    if acc is None: acc={"n_ht":0,"n_dead":0,"n_alive":0,"sizes":[],"handles":[]}
    if budget is None: budget=[400]
    if node is None or budget[0] <= 0: return acc
    budget[0]-=1
    if isinstance(node, HostTexture):
        acc["n_ht"]+=1
        h=int(getattr(node,"handle",0) or 0)
        acc["handles"].append(h)
        acc["sizes"].append((getattr(node,"w",0),getattr(node,"h",0)))
        try:
            import renpy_host
            alive=bool(renpy_host.texture_alive(h)) if h>0 else False
        except Exception:
            alive=False
        if alive: acc["n_alive"]+=1
        else: acc["n_dead"]+=1
        return acc
    kids=[]
    try:
        ch=getattr(node,"children",None)
        if ch:
            for e in ch:
                if isinstance(e,(list,tuple)) and e: kids.append(e[0])
                else: kids.append(e)
    except Exception: pass
    for attr in ("cached_texture","cached_model","texture"):
        try:
            v=getattr(node,attr,None)
            if v is not None: kids.append(v)
        except Exception: pass
    try:
        ts=getattr(node,"textures",None)
        if ts:
            for t in ts: kids.append(t)
    except Exception: pass
    for k in kids:
        _walk_ht(k, budget, acc)
    return acc

def _present_diag(tag):
    import renpy, renpy_host, interact_helpers as ih
    from renpy.wgpu.draw import HostTexture
    ready,why,iface=ih.interface_ready()
    if not ready: return {"err":why}
    root=ih._rebuild_product_root(iface)
    w=int(getattr(renpy.config,"screen_width",1920) or 1920)
    h=int(getattr(renpy.config,"screen_height",1080) or 1080)
    st=renpy.display.render.render_screen(root,w,h)
    draw=renpy.display.draw
    pre_walk=_walk_ht(st)
    stash=len(getattr(draw,"_handle_pixels",{}) or {})
    remap=len(getattr(draw,"_handle_remap",{}) or {})
    tcache=len(getattr(draw,"texture_cache",{}) or {})
    before=_frame_state()
    try:
        if hasattr(draw,"load_all_textures"): draw.load_all_textures(st)
    except Exception as e:
        _log("%s prepare_err %s"%(tag,e))
    mid=_frame_state()
    mid_walk=_walk_ht(st)
    try:
        draw.draw_screen(st, flip=True)
        iface.surftree=st
        path="rebuild"
    except Exception as e:
        _log("%s draw_err %s"%(tag,e))
        path="err"
    after=_frame_state()
    post_walk=_walk_ht(st)
    rt=_sample()
    return {
        "path":path,"before":before,"mid":mid,"after":after,"rt":rt,
        "pre":pre_walk,"midw":mid_walk,"post":post_walk,
        "stash":stash,"remap":remap,"tcache":tcache,
        "depth":getattr(draw,"_draw_screen_depth",None),
    }

def _show_confirm():
    import renpy
    renpy.display.screen.show_screen(
        "confirm",
        message="确认要退出游戏吗",
        yes_action=[renpy.store.Hide("confirm")],
        no_action=[renpy.store.Hide("confirm")],
        confirm_type="quit",
    )
    try: renpy.restart_interaction()
    except Exception: pass

def _show_flow():
    import renpy
    renpy.store.ShowMenu("flowchart")()
    try: renpy.restart_interaction()
    except Exception: pass

def _hide_all():
    import renpy
    for n in ("load","preferences","appreciation","flowchart","confirm","save"):
        try: renpy.display.screen.hide_screen(n)
        except Exception: pass
    try: renpy.restart_interaction()
    except Exception: pass

def run():
    base=_base()
    out=base/"host"/"target"/"gate-_c2_confirm_probe.txt"
    game=os.environ.get("RENPY_HOST_GAME") or str(base/"host"/"playtests"/"HuangmeiC")
    os.environ["RENPY_HOST_BASE"]=str(base)
    os.environ["RENPY_HOST_BUILD"]="1"
    os.environ["RENPY_HOST_GAME"]=game
    os.environ.setdefault("RENPY_PERFORMANCE_TEST","0")
    os.environ["RENPY_HOST_UI_TRACE"]="1"
    _clear_falsey("RENPY_SKIP_MAIN_MENU"); _clear_falsey("RENPY_SKIP_SPLASHSCREEN")
    for p in (str(base/"host"/"python"/"gates"), str(base/"host"/"python")):
        if p not in sys.path: sys.path.insert(0,p)
    import renpy_host, bootstrap as boot
    for name,call in (("import_renpy",boot.stage_import_renpy),("import_all",boot.stage_import_all),
                      ("set_game_dir",lambda: boot.stage_set_game_dir(base))):
        good,miss,err,extra=call(); _log("stage %s good=%s err=%r"%(name,good,err))
        if not good:
            out.write_text("ok=False\nerror=%s\n"%err); _quit(); return
    import renpy
    renpy.host_build=True
    try:
        import renpy_main_host; renpy_main_host.install(renpy)
    except Exception as e: _log("main_host %s"%e)
    try:
        import renpy.arguments
        basedir=getattr(renpy.config,"basedir",None) or game
        argv0=sys.argv[0] if sys.argv else "renpy-host"
        sys.argv=[argv0,basedir,"run"]
        if not getattr(renpy.arguments,"commands",None):
            try:
                renpy.arguments.register_command("run", renpy.arguments.run, True)
                renpy.arguments.register_command("quit", renpy.arguments.quit)
            except Exception: pass
        renpy.game.args=renpy.arguments.bootstrap()
    except Exception as e: _log("args %s"%e)
    _pre()
    results=[]
    def injector():
        for i in range(400):
            try:
                if bool(getattr(renpy.store,"main_menu",False)):
                    _log("main_menu tick=%d"%i); break
            except Exception: pass
            time.sleep(0.05)
        time.sleep(2.0)
        open("/tmp/c2_confirm_probe.log","w").write("")
        # Match hmc_nav_confirm_diag sequence
        _log("=== A confirm alone ===")
        _show_confirm(); time.sleep(0.5)
        p=_present_diag("A"); _log("A %s"%{k:v for k,v in p.items() if k not in ("pre","midw","post")})
        _log("A ht pre n=%s dead=%s alive=%s sizes=%s"%(p["pre"]["n_ht"],p["pre"]["n_dead"],p["pre"]["n_alive"],p["pre"]["sizes"][:12]))
        results.append(("A",p.get("rt"),p))
        _hide_all(); time.sleep(0.5)
        _log("=== B flowchart then confirm ===")
        _show_flow(); time.sleep(0.8)
        p=_present_diag("Bflow"); _log("Bflow rt %s ht n=%s dead=%s"%(p.get("rt"),p["pre"]["n_ht"],p["pre"]["n_dead"]))
        _hide_all(); time.sleep(0.4)
        _show_confirm(); time.sleep(0.5)
        p=_present_diag("Bconf"); _log("Bconf rt %s"%(p.get("rt")))
        _log("Bconf frame before=%s mid=%s after=%s"%(p.get("before"),p.get("mid"),p.get("after")))
        _log("Bconf ht pre n=%s dead=%s alive=%s sizes=%s handles=%s"%(p["pre"]["n_ht"],p["pre"]["n_dead"],p["pre"]["n_alive"],p["pre"]["sizes"][:12],p["pre"]["handles"][:12]))
        _log("Bconf ht mid n=%s dead=%s alive=%s"%(p["midw"]["n_ht"],p["midw"]["n_dead"],p["midw"]["n_alive"]))
        _log("Bconf stash=%s remap=%s tcache=%s"%(p["stash"],p["remap"],p["tcache"]))
        results.append(("Bconf",p.get("rt"),p))
        _hide_all(); time.sleep(0.4)
        _log("=== C confirm alone again ===")
        _show_confirm(); time.sleep(0.5)
        p=_present_diag("C"); _log("C rt %s"%(p.get("rt")))
        _log("C ht pre n=%s dead=%s alive=%s sizes=%s"%(p["pre"]["n_ht"],p["pre"]["n_dead"],p["pre"]["n_alive"],p["pre"]["sizes"][:12]))
        _log("C ht mid n=%s dead=%s alive=%s"%(p["midw"]["n_ht"],p["midw"]["n_dead"],p["midw"]["n_alive"]))
        # dump shallow tree types
        try:
            import renpy
            def dump(n, d=0, budget=[80]):
                if n is None or budget[0]<=0: return
                budget[0]-=1
                from renpy.wgpu.draw import HostTexture
                if isinstance(n, HostTexture):
                    _log("  "*d+"HT %sx%s h=%s"%(n.w,n.h,n.handle)); return
                nm=type(n).__name__
                ch=list(getattr(n,"children",[]) or [])
                ct=getattr(n,"cached_texture",None); cm=getattr(n,"cached_model",None)
                rev=getattr(n,"reverse",None); mesh=getattr(n,"mesh",None)
                _log("  "*d+"%s ch=%s ct=%s cm=%s rev=%s mesh=%s size=%sx%s"%(nm,len(ch),type(ct).__name__ if ct is not None else None, type(cm).__name__ if cm is not None else None, bool(rev), mesh, getattr(n,"width",None), getattr(n,"height",None)))
                for e in ch[:12]:
                    c=e[0] if isinstance(e,(list,tuple)) and e else e
                    dump(c,d+1,budget)
                if ct is not None: dump(ct,d+1,budget)
            # re-render for dump
            import interact_helpers as ih
            ready,why,iface=ih.interface_ready()
            root=ih._rebuild_product_root(iface)
            w=int(getattr(renpy.config,"screen_width",1920) or 1920)
            h=int(getattr(renpy.config,"screen_height",1080) or 1080)
            st=renpy.display.render.render_screen(root,w,h)
            dump(st)
            # im.cache size
            import renpy.display.im as im
            _log("im.cache n=%s"%(len(getattr(im.cache,"cache",{}))))
            draw=renpy.display.draw
            _log("draw.tcache=%s stash=%s"%(len(draw.texture_cache),len(draw._handle_pixels)))
        except Exception as e:
            _log("dump err %s"%e)
        results.append(("C",p.get("rt"),p))
        lines=["gate=_c2_confirm_probe"]
        ok=all(r[1] and r[1].get("ok") for r in results)
        lines.append("ok=%s"%ok)
        for name,rt,p in results:
            lines.append("case.%s ok=%s clear=%s mean=%s n_ht=%s n_dead=%s n_alive=%s stash=%s"%(
                name, rt.get("ok") if rt else None, rt.get("clear") if rt else None,
                rt.get("mean") if rt else None,
                p["post"]["n_ht"], p["post"]["n_dead"], p["post"]["n_alive"], p["stash"]))
        out.write_text("\n".join(lines)+"\n")
        _log("wrote %s ok=%s"%(out,ok))
        _quit()
    threading.Thread(target=injector,daemon=True).start()
    import renpy.main as m
    try: m.main()
    except BaseException as e: _log("main exit %s"%e)

run()
