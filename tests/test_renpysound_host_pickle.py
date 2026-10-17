"""
Tests for pickle compatibility of renpy.audio.renpysound_host module attributes.
Ensures that renpy.import_all() backup routine (pickle.dumps) never encounters
unpicklable _thread.lock objects.
"""
import pickle
import threading
import pytest
import renpy.compat.pickle as renpy_pickle


def test_lazy_path_cache_lock_picklable():
    from renpy.audio import renpysound_host as rsh

    # Lock object should be picklable with both standard pickle and renpy_pickle
    pickled = pickle.dumps(rsh._PATH_CACHE_LOCK, protocol=pickle.HIGHEST_PROTOCOL)
    unpickled = pickle.loads(pickled)
    assert unpickled is not None

    pickled_rp = renpy_pickle.dumps(rsh._PATH_CACHE_LOCK, highest=True)
    assert len(pickled_rp) > 0

    # Context manager and acquisition operations should work seamlessly
    with rsh._PATH_CACHE_LOCK:
        assert isinstance(rsh._get_path_cache_lock(), type(threading.Lock()))

    # After acquiring lock, it should still be picklable via __reduce__
    pickled_after = pickle.dumps(rsh._PATH_CACHE_LOCK, protocol=pickle.HIGHEST_PROTOCOL)
    assert len(pickled_after) > 0


def test_renpysound_host_all_module_vars_picklable():
    from renpy.audio import renpysound_host as rsh
    import renpy

    name = "renpy.audio.renpysound_host"
    for k, v in vars(rsh).items():
        if k.startswith("__") and k.endswith("__"):
            continue
        if name + "." + k in renpy.name_blacklist:
            continue
        if isinstance(v, renpy.type_blacklist):
            continue

        try:
            renpy_pickle.dumps(v, highest=True)
        except Exception as e:
            pytest.fail(f"Variable {name}.{k} = {repr(v)} failed to pickle: {e}")
