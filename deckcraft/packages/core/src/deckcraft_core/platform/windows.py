"""Windows power-throttling (EcoQoS) opt-out via ctypes. Safe no-op on non-Windows."""
from __future__ import annotations
import ctypes
import os
import sys
from collections import deque

_PROCESS_POWER_THROTTLING = 4
_EXECUTION_SPEED = 0x1
_PROCESS_SET_INFORMATION = 0x0200
_NORMAL_PRIORITY_CLASS = 0x00000020
_TH32CS_SNAPPROCESS = 0x00000002


if sys.platform == "win32":
    class _POWER_THROTTLING_STATE(ctypes.Structure):
        _fields_ = [
            ("Version", ctypes.c_uint32),
            ("ControlMask", ctypes.c_uint32),
            ("StateMask", ctypes.c_uint32),
        ]

    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_uint32),
            ("cntUsage", ctypes.c_uint32),
            ("th32ProcessID", ctypes.c_uint32),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", ctypes.c_uint32),
            ("cntThreads", ctypes.c_uint32),
            ("th32ParentProcessID", ctypes.c_uint32),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", ctypes.c_uint32),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.GetCurrentProcess.restype = ctypes.c_void_p
    _k32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    _k32.OpenProcess.restype = ctypes.c_void_p
    _k32.CloseHandle.argtypes = [ctypes.c_void_p]
    _k32.CloseHandle.restype = ctypes.c_int
    _k32.SetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    _k32.SetProcessInformation.restype = ctypes.c_int
    _k32.SetPriorityClass.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    _k32.SetPriorityClass.restype = ctypes.c_int
    _k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
    _k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    _k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.Process32FirstW.restype = ctypes.c_int
    _k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_PROCESSENTRY32W)]
    _k32.Process32NextW.restype = ctypes.c_int


def unthrottle(pid: int | None = None) -> bool:
    """Disable Windows EcoQoS power throttling on a process. No-op off Windows."""
    if sys.platform != "win32":
        return False
    handle = None
    opened = False
    try:
        if pid is None or pid == os.getpid():
            handle = _k32.GetCurrentProcess()
        else:
            handle = _k32.OpenProcess(_PROCESS_SET_INFORMATION, False, int(pid))
            opened = bool(handle)
        if not handle:
            return False
        state = _POWER_THROTTLING_STATE(Version=1, ControlMask=_EXECUTION_SPEED, StateMask=0)
        ok = bool(_k32.SetProcessInformation(handle, _PROCESS_POWER_THROTTLING, ctypes.byref(state), ctypes.sizeof(state)))
        _k32.SetPriorityClass(handle, _NORMAL_PRIORITY_CLASS)
        return ok
    except Exception:  # noqa: BLE001
        return False
    finally:
        if opened and handle:
            try:
                _k32.CloseHandle(handle)
            except Exception:  # noqa: BLE001
                pass


def child_pids(root_pid: int) -> list[int]:
    """Return all descendant process IDs of root_pid via Toolhelp snapshot."""
    if sys.platform != "win32":
        return []
    snap = _k32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    invalid = ctypes.c_void_p(-1).value
    if not snap or snap == invalid:
        return []
    children: dict[int, list[int]] = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(_PROCESSENTRY32W)
        ok = _k32.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            children.setdefault(entry.th32ParentProcessID, []).append(entry.th32ProcessID)
            ok = _k32.Process32NextW(snap, ctypes.byref(entry))
    except Exception:  # noqa: BLE001
        return []
    finally:
        try:
            _k32.CloseHandle(snap)
        except Exception:  # noqa: BLE001
            pass

    out: list[int] = []
    seen = {root_pid}
    q = deque([root_pid])
    while q:
        cur = q.popleft()
        for cid in children.get(cur, ()):
            if cid not in seen:
                seen.add(cid)
                out.append(cid)
                q.append(cid)
    return out


def unthrottle_tree(root_pid: int | None = None) -> int:
    """Disable EcoQoS on root_pid and all its descendant processes. Returns count succeeded."""
    if sys.platform != "win32":
        return 0
    pid = os.getpid() if root_pid is None else int(root_pid)
    targets = [pid] + child_pids(pid)
    return sum(1 for p in targets if unthrottle(p))
