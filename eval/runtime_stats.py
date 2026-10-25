"""Small platform-aware process memory measurement without extra dependencies."""
import ctypes
import os
import sys
from pathlib import Path


def memory_bytes():
    if sys.platform != "win32":
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return {"peak_rss": peak * (1 if sys.platform == "darwin" else 1024)}
    from ctypes import wintypes
    class Counters(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
            (name, ctypes.c_size_t) for name in ("peak_rss", "rss", "peak_paged", "paged",
                "peak_nonpaged", "nonpaged", "pagefile", "peak_pagefile", "private")]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess
    handle.restype = wintypes.HANDLE
    get_info = ctypes.windll.psapi.GetProcessMemoryInfo
    get_info.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
    get_info.restype = wintypes.BOOL
    ok = get_info(handle(), ctypes.byref(counters), counters.cb)
    if not ok:
        raise ctypes.WinError()
    return {"pid": os.getpid(), "rss": counters.rss, "peak_rss": counters.peak_rss,
            "private_bytes": counters.private}


def local_revision(model):
    root = Path(os.environ.get("HF_HOME", str(Path.home() / ".cache/huggingface"))) / "hub"
    return (root / ("models--" + model.replace("/", "--")) / "refs/main").read_text().strip()

