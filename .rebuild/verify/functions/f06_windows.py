"""Windows-only code, run here with a fake kernel32/msvcrt (isolated functions of lt_module):
the binary std streams taken from the OS handles (a --noconsole exe may have sys.stdin None) and
the lower process priority. The calls made to the fake OS are the result.

The functions are found by name; when the rebuild names them differently, FUNCTIONS[VERSION]
says where they are (the recorded results are the original's)."""
import ctypes  # imported before os.name is faked: it checks it at import
import logging
import sys
import types

from _common import VERSION, WORK, emit

FUNCTIONS = {"original": ("binary_streams", "lower_priority")}

calls = []


class Fn:
    def __init__(self, name, result):
        self.name, self.result = name, result
        self.restype, self.argtypes = None, None

    def __call__(self, *args):
        calls.append([self.name, [getattr(a, "value", a) for a in args]])
        r = self.result(*args) if callable(self.result) else self.result
        return r


def fake_os(handles, priority_ok=True, raise_on_priority=False):
    import ctypes
    k32 = types.SimpleNamespace(
        GetStdHandle=Fn("GetStdHandle", lambda num: handles.get(num.value)),
        GetCurrentProcess=Fn("GetCurrentProcess", 0xFFFF),
        SetPriorityClass=Fn("SetPriorityClass", priority_ok))
    if raise_on_priority:
        def boom(*a):
            raise OSError("access denied")
        k32.SetPriorityClass = boom
    ctypes.windll = types.SimpleNamespace(kernel32=k32)
    ctypes.GetLastError = lambda: 5
    msvcrt = types.ModuleType("msvcrt")
    msvcrt.open_osfhandle = Fn("open_osfhandle", lambda h, flags: 100 + h)
    msvcrt.setmode = Fn("setmode", None)
    sys.modules["msvcrt"] = msvcrt


def main():
    import lt_module as M
    streams_name, priority_name = FUNCTIONS.get(VERSION, FUNCTIONS["original"])
    fdopen = []
    M.os.fdopen = lambda fd, mode, buffering=-1: fdopen.append([fd, mode, buffering]) or f"file{fd}"
    log_lines = []
    logging.getLogger("lt").addHandler(type("H", (logging.Handler,), {
        "emit": lambda self, r: log_lines.append([r.levelname, r.getMessage()])})())
    logging.getLogger("lt").setLevel(logging.DEBUG)
    out = {}
    real_name = M.os.name
    M.os.name = "nt"
    try:
        for case, handles in (("all", {0xFFFFFFF6: 1, 0xFFFFFFF5: 2, 0xFFFFFFF4: 3}),
                              ("no_stdin", {0xFFFFFFF5: 2, 0xFFFFFFF4: 3}),
                              ("invalid", {0xFFFFFFF6: 1, 0xFFFFFFF5: (1 << 64) - 1, 0xFFFFFFF4: 0})):
            calls.clear(), fdopen.clear()
            fake_os(handles)
            result = getattr(M, streams_name)()
            out[f"streams_{case}"] = {"result": list(result), "calls": list(calls), "fdopen": list(fdopen),
                                      "O_RDONLY": M.os.O_RDONLY, "O_BINARY": getattr(M.os, "O_BINARY", None)}
        for case, kw in (("ok", {}), ("refused", {"priority_ok": False}), ("raises", {"raise_on_priority": True})):
            calls.clear(), log_lines.clear()
            fake_os({}, **kw)
            getattr(M, priority_name)()
            out[f"priority_{case}"] = {"calls": list(calls), "log": list(log_lines)}
    finally:
        M.os.name = real_name
    calls.clear(), log_lines.clear()
    getattr(M, priority_name)()
    out["priority_not_windows"] = {"calls": list(calls), "log": list(log_lines)}
    # the whole start on Windows without a stdout handle: no handshake, exit code 2
    import os as real_os
    proxy = types.SimpleNamespace(**{k: getattr(real_os, k) for k in dir(real_os) if not k.startswith("__")})
    proxy.name = "nt"
    proxy.fdopen = M.os.fdopen
    M.os = proxy  # only the module sees Windows (pathlib must stay POSIX here)
    calls.clear()
    fake_os({0xFFFFFFF6: 1, 0xFFFFFFF5: 0, 0xFFFFFFF4: 3})
    sys.argv = ["lt_module.py", "--fake", "--lang-dir", str(WORK / "IT"), "--cache-db", str(WORK / "c.db")]
    out["main_without_stdout"] = {"exit": M.main(), "calls": list(calls),
                                  "files": sorted(p.name for p in WORK.iterdir())}
    return out


if not hasattr(__import__("os"), "O_BINARY"):
    __import__("os").O_BINARY = 0x8000  # Windows value, missing on Linux
emit(main())
