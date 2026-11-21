"""Owned process-tree lifetime; Windows children join a kill-on-close job before running."""
import os
import json
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

    def command(self, command):
        if not sys.platform.startswith("linux"):
            return command
        # A separate helper owns subreaper state; never change the threaded
        # supervisor's process-global adoption or signal configuration.
        identity = _linux_record(os.getpid())
        if identity is None:
            raise RuntimeError("Cannot identify Linux process owner")
        payload = {"argv": list(command), "parent_pid": os.getpid(), "parent_start": identity["start"]}
        return [sys.executable, "-I", str(Path(__file__).resolve()), "--owned-linux-broker",
                json.dumps(payload, ensure_ascii=False)]

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


def _linux_record(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        if len(fields) < 20:
            return None
        return {"pid": int(pid), "parent": int(fields[1]), "start": int(fields[19]), "state": fields[0]}
    except (OSError, UnicodeError, ValueError):
        return None


def _linux_descendants(owner):
    records = {}
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            record = _linux_record(int(entry.name))
            if record:
                records[record["pid"]] = record
    owned, frontier = {}, {owner}
    while frontier:
        following = {}
        for pid, record in records.items():
            if pid != owner and pid not in owned and record["parent"] in frontier:
                following[pid] = record
        owned.update(following)
        frontier = set(following)
    return owned


def _linux_signal_owned(record, signum):
    # Open a stable kernel process handle, then recheck its birth identity.
    # Never fall back to an unguarded kill of a potentially recycled PID.
    current = _linux_record(record["pid"])
    if not current or current["start"] != record["start"] or current["state"] in {"Z", "X", "x"}:
        return
    try:
        descriptor = os.pidfd_open(record["pid"], 0)
    except ProcessLookupError:
        return
    try:
        current = _linux_record(record["pid"])
        if current and current["start"] == record["start"]:
            try:
                signal.pidfd_send_signal(descriptor, signum)
            except ProcessLookupError:
                pass
    finally:
        os.close(descriptor)


def _linux_reap_children():
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return True
        if pid == 0:
            return False


def _linux_cleanup_children(owner):
    deadline = time.monotonic() + 2.0
    while True:
        records = _linux_descendants(owner)
        # Freeze before kill so a worker cannot fork a fresh escaping session
        # while its parent is being removed. Orphans are adopted by this helper.
        for record in records.values():
            _linux_signal_owned(record, signal.SIGSTOP)
        records.update(_linux_descendants(owner))
        for record in records.values():
            _linux_signal_owned(record, signal.SIGKILL)
        reaped = _linux_reap_children()
        if reaped and not _linux_descendants(owner):
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Owned Linux descendants did not stop")
        time.sleep(.01)


def _linux_broker(payload):
    if not isinstance(payload, dict) or set(payload) != {"argv", "parent_pid", "parent_start"}:
        raise ValueError("Invalid owned process broker request")
    command = payload["argv"]
    if not isinstance(command, list) or not command or any(not isinstance(item, str) or "\0" in item for item in command):
        raise ValueError("Owned process command must be an argv list")
    if not command[0]:
        raise ValueError("Owned process executable must not be empty")
    if type(payload["parent_pid"]) is not int or payload["parent_pid"] <= 0 or type(payload["parent_start"]) is not int:
        raise ValueError("Invalid owned process parent identity")
    if not callable(getattr(os, "pidfd_open", None)) or not callable(getattr(signal, "pidfd_send_signal", None)):
        raise RuntimeError("Linux process ownership requires pidfd support")
    # Prove kernel/syscall availability before any workload can start.
    descriptor = os.pidfd_open(os.getpid(), 0)
    try:
        signal.pidfd_send_signal(descriptor, 0)
    finally:
        os.close(descriptor)
    stop = [False]
    def request_stop(signum, frame):
        stop[0] = True
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, request_stop)
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    libc.prctl.restype = ctypes.c_int
    # Only this newly spawned broker is a subreaper and watches its owner's death.
    for operation, value in ((36, 1), (1, signal.SIGTERM)):
        if libc.prctl(operation, value, 0, 0, 0) != 0:
            raise OSError(ctypes.get_errno(), "Cannot establish owned Linux process lifetime")
    parent = _linux_record(payload["parent_pid"])
    if os.getppid() != payload["parent_pid"] or not parent or parent["start"] != payload["parent_start"] or stop[0]:
        return 143
    worker = None
    try:
        worker = subprocess.Popen(command, stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
        while worker.poll() is None and not stop[0]:
            time.sleep(.01)
        # poll captures the real worker exit before generic orphan reaping.
        result = worker.poll()
    finally:
        # Also owns children created immediately before launch/callback failures.
        _linux_cleanup_children(os.getpid())
    if stop[0]:
        return 143
    if result is None:
        raise RuntimeError("Owned Linux worker ended without a result")
    return result if result >= 0 else 128 - result


if __name__ == "__main__":
    if not sys.platform.startswith("linux") or len(sys.argv) != 3 or sys.argv[1] != "--owned-linux-broker":
        raise SystemExit("This helper is only an internal Linux process owner")
    try:
        status = _linux_broker(json.loads(sys.argv[2]))
    except (OSError, ValueError, RuntimeError) as error:
        print("Owned Linux process cleanup failed: " + str(error), file=sys.stderr)
        status = 125
    raise SystemExit(status)
