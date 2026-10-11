"""Give this sender/helper timely CPU service without real-time priority."""

import ctypes as c
import os


class SchedulingLease:
    def __init__(self):
        self.original = None
        if os.name != "nt":
            return
        from ctypes import wintypes as w

        kernel = c.WinDLL("kernel32", use_last_error=True)
        current = kernel.GetCurrentProcess
        current.argtypes = []
        current.restype = w.HANDLE
        self.handle = current()
        self.get = kernel.GetPriorityClass
        self.get.argtypes = [w.HANDLE]
        self.get.restype = w.DWORD
        self.set = kernel.SetPriorityClass
        self.set.argtypes = [w.HANDLE, w.DWORD]
        self.set.restype = w.BOOL
        priority = self.get(self.handle)
        if not priority:
            raise c.WinError(c.get_last_error())
        if priority in (0x20, 0x40, 0x4000):
            if not self.set(self.handle, 0x8000):
                raise c.WinError(c.get_last_error())
            self.original = priority

    def close(self):
        if self.original is not None:
            if self.get(self.handle) == 0x8000 and not self.set(self.handle, self.original):
                raise c.WinError(c.get_last_error())
            self.original = None
