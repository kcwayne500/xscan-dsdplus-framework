from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from ctypes import wintypes
from typing import Iterator


CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
WINDOW_CREATION_FLAGS = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
_DLL_SEARCH_LOCK = threading.RLock()


def _set_dll_directory(path: str | None) -> None:
    kernel32 = ctypes.windll.kernel32
    kernel32.SetDllDirectoryW.argtypes = [wintypes.LPCWSTR]
    kernel32.SetDllDirectoryW.restype = wintypes.BOOL
    if not kernel32.SetDllDirectoryW(path):
        raise ctypes.WinError(ctypes.get_last_error())


@contextmanager
def external_program_dll_search() -> Iterator[None]:
    """Give non-bundled child programs the normal Windows DLL search path."""
    bundle_directory = getattr(sys, "_MEIPASS", None)
    if os.name != "nt" or not bundle_directory:
        yield
        return
    with _DLL_SEARCH_LOCK:
        _set_dll_directory(None)
        try:
            yield
        finally:
            _set_dll_directory(str(bundle_directory))


def set_process_windows_visible(pid: int, visible: bool) -> int:
    if os.name != "nt" or not pid:
        return 0
    user32 = ctypes.windll.user32
    shown = 0
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _lparam):
        nonlocal shown
        process_id = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        if process_id.value == pid:
            user32.ShowWindow(hwnd, 5 if visible else 0)
            shown += 1
        return True

    user32.EnumWindows(callback_type(callback), 0)
    return shown


def terminate_process(process: subprocess.Popen | None, timeout: float = 3.0) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
            process.wait(timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            pass
