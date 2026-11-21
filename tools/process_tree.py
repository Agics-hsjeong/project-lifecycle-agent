"""Owned process-tree lifetime; Windows children join a kill-on-close job before running."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


class ProcessTree:
    def __init__(self):
        self.job = None
        self.kernel = None
        if os.name == "nt":
            import ctypes
            from ctypes import wintypes as w
            self.ctypes = ctypes
            k = ctypes.WinDLL("kernel32", use_last_error=True)
            k.CreateJobObjectW.argtypes = [w.LPVOID, w.LPCWSTR]
            k.CreateJobObjectW.restype = w.HANDLE
            k.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
            k.SetInformationJobObject.restype = w.BOOL
            k.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
            k.AssignProcessToJobObject.restype = w.BOOL
            k.TerminateJobObject.argtypes = [w.HANDLE, w.UINT]
            k.TerminateJobObject.restype = w.BOOL
            k.CloseHandle.argtypes = [w.HANDLE]
            k.CloseHandle.restype = w.BOOL
            class Basic(ctypes.Structure):
                _fields_ = [("per_process", ctypes.c_int64), ("per_job", ctypes.c_int64),
                            ("flags", w.DWORD), ("min_ws", ctypes.c_size_t), ("max_ws", ctypes.c_size_t),
                            ("active", w.DWORD), ("affinity", ctypes.c_size_t),
                            ("priority", w.DWORD), ("scheduling", w.DWORD)]
            class IO(ctypes.Structure):
                _fields_ = [(name, ctypes.c_uint64) for name in ("ro", "wo", "oo", "rt", "wt", "ot")]
            class Extended(ctypes.Structure):
                _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                            ("job_memory", ctypes.c_size_t), ("peak_process", ctypes.c_size_t),
                            ("peak_job", ctypes.c_size_t)]
            self.kernel = k
            self.job = k.CreateJobObjectW(None, None)
            if not self.job:
                raise ctypes.WinError(ctypes.get_last_error())
            limits = Extended()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE; no breakaway.
            if not k.SetInformationJobObject(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                error = ctypes.WinError(ctypes.get_last_error())
                self.close()
                raise error

    @property
    def popen_options(self):
        if os.name == "nt":
            return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW | 0x4}
        return {"start_new_session": True}

    def attach(self, process):
        if os.name == "nt":
            if not self.kernel.AssignProcessToJobObject(self.job, int(process._handle)):
                raise self.ctypes.WinError(self.ctypes.get_last_error())

    def resume(self, process):
        if os.name == "nt":
            from ctypes import wintypes as w
            nt = self.ctypes.WinDLL("ntdll")
            nt.NtResumeProcess.argtypes = [w.HANDLE]
            nt.NtResumeProcess.restype = w.LONG
            status = nt.NtResumeProcess(int(process._handle))
            if status != 0:
                raise OSError("Cannot resume owned suspended process: " + str(status))

    def terminate(self, process):
        # A dead leader does not imply a dead process tree.
        if os.name == "nt" and self.job:
            if not self.kernel.TerminateJobObject(self.job, 1):
                raise self.ctypes.WinError(self.ctypes.get_last_error())
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            if sys.platform.startswith("linux"):
                # Delivery of SIGKILL is asynchronous. Wait for owned group members
                # to exit, ignoring zombies that can no longer execute or hold files.
                deadline = time.monotonic() + 3
                while self._linux_group_active(process.pid):
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Owned process group did not stop")
                    time.sleep(0.01)

    @staticmethod
    def _linux_group_active(group):
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rpartition(")")[2].split()
                if len(fields) > 2 and int(fields[2]) == group and fields[0] not in {"Z", "X", "x"}:
                    return True
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (OSError, UnicodeError, ValueError):
                # /proc entries belonging to unrelated users may be unreadable.
                continue
        return False

    def close(self):
        if self.job:
            self.kernel.CloseHandle(self.job)
            self.job = None
