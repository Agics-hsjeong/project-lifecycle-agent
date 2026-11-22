"""Confined, serialized project writes with rollback and crash recovery."""
import base64
import errno
from contextlib import contextmanager
import hashlib
import json
import os
import stat
import sys
from pathlib import Path, PureWindowsPath
import tempfile
import threading
import time
from functools import wraps

_OWNED = threading.local()


def consistent_read(function):
    """Serialize a complete managed-state read with engine writers."""
    @wraps(function)
    def wrapped(folder, *args, **kwargs):
        root = Path(folder).resolve()
        if not (root / "project.json").is_file():
            raise ValueError("프로젝트 파일이 없습니다.")
        if root in getattr(_OWNED, "roots", set()):
            return function(folder, *args, **kwargs)
        with lock(root):
            return function(folder, *args, **kwargs)
    return wrapped


def digest(value):
    return hashlib.sha256(value).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _containment_path(path):
    """Compare equivalent resolved Windows DOS/UNC names without changing I/O.

    Windows realpath may retain its extended prefix if a file is atomically
    replaced between final-name queries. Both results still name the same file.
    Only documented DOS-drive and UNC forms are normalized, never device paths.
    """
    if isinstance(path, PureWindowsPath):
        value = str(path)
        if value[:8].casefold() == "\\\\?\\unc\\":
            value = "\\\\" + value[8:]
        elif value.startswith("\\\\?\\") and len(value) >= 7 and value[4].isalpha() and value[5:7] == ":\\":
            value = value[4:]
        return PureWindowsPath(value)
    return path


def confined(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("프로젝트 내부의 '/' 상대 경로가 필요합니다.")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("프로젝트 외부 경로는 허용하지 않습니다.")
    path = root / rel
    for attempt in range(5):
        resolved = _containment_path(path.resolve())
        if resolved.is_relative_to(_containment_path(root)):
            break
        # A handle opened just before unlink can resolve to NTFS's private
        # delete-pending tombstone. Never accept that path: re-query the original
        # name and still require its resolved destination to be inside root.
        deleted = (os.name == 'nt' and isinstance(resolved, PureWindowsPath)
                   and tuple(part.casefold() for part in resolved.parts[1:3]) == ('$extend', '$deleted'))
        if not deleted or attempt == 4:
            raise ValueError("경로가 프로젝트 밖을 가리킵니다.")
        time.sleep(0.002)
    # Do not mutate through links, including links back into the same project.
    current = path
    while current != root:
        try:
            attributes = getattr(current.lstat(), "st_file_attributes", 0)
        except FileNotFoundError:
            attributes = 0  # The exact file may disappear during an atomic replace.
        if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ValueError("연결된 경로에는 기록할 수 없습니다.")
        current = current.parent
    return path


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary = Path(temporary)
        if temporary.exists():
            temporary.unlink()


@contextmanager
def lock(root, *, recovering=False):
    state = confined(root, "state")
    state.mkdir(exist_ok=True)
    path = confined(root, "state/write.lock")
    try:
        # Windows may report EACCES while a prior lock deletion is pending.
        # Wait only on this exact managed lock; never delete it or relax ACLs.
        # A persistent access denial remains the original permission failure.
        deadline = time.monotonic() + 0.25
        while True:
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except PermissionError as exc:
                winerror = getattr(exc, "winerror", None)
                transient_code = winerror in {5, 32} or (winerror is None and exc.errno == errno.EACCES)
                exact_lock = exc.filename is not None and Path(exc.filename).absolute() == path.absolute()
                if os.name != "nt" or not transient_code or not exact_lock or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)
    except FileExistsError as exc:
        raise ValueError("다른 쓰기 작업의 잠금이 있습니다. 종료 여부를 확인한 뒤 recover를 사용하세요.") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid()}, handle)
    try:
        if not recovering and confined(root, "state/pending.json").exists():
            raise ValueError("미완료 변경 기록이 있습니다. recover로 먼저 복구하세요.")
        roots = getattr(_OWNED, "roots", set())
        _OWNED.roots = roots
        roots.add(Path(root).resolve())
        yield
    finally:
        getattr(_OWNED, "roots", set()).discard(Path(root).resolve())
        path.unlink(missing_ok=True)


def _clear_pending(root):
    """Caller holds lock; retain the journal if bounded Windows cleanup fails."""
    pending = confined(root, "state/pending.json")
    deadline = time.monotonic() + 0.25
    while True:
        try:
            pending.unlink()
            return
        except PermissionError as exc:
            # A scanner can hold this completed journal briefly without sharing
            # delete. Retry only its exact name, never remove any other file or
            # hide a persistent denial that still requires normal recovery.
            filename = exc.filename
            remaining = deadline - time.monotonic()
            if (os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32}
                    or filename is None or Path(filename).absolute() != pending.absolute()
                    or remaining <= 0):
                raise
            time.sleep(min(0.01, remaining))


def commit(root, changes):
    """Caller holds lock. Keep durable preimages until every replacement succeeds."""
    pending = confined(root, "state/pending.json")
    entries = []
    for relative, content in changes.items():
        if relative in {"state/pending.json", "state/write.lock"}:
            raise ValueError("내부 제어 파일을 변경할 수 없습니다.")
        path = confined(root, relative)
        before = path.read_bytes() if path.exists() else None
        entries.append({"path": relative, "before": base64.b64encode(before).decode() if before is not None else None,
                        "after": digest(content) if content is not None else None})
    atomic_write(pending, json_bytes({"schema_version": 1, "entries": entries}))
    try:
        for relative, content in changes.items():
            path = confined(root, relative)
            if content is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, content)
    except BaseException:
        _restore(root, entries)
        _clear_pending(root)
        raise
    _clear_pending(root)


def _restore(root, entries):
    for entry in entries:
        path = confined(root, entry["path"])
        before = base64.b64decode(entry["before"], validate=True) if entry["before"] is not None else None
        if before is None:
            path.unlink(missing_ok=True)
        else:
            atomic_write(path, before)


def process_alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        raise ValueError("잘못된 잠금 소유자입니다.")
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        kernel.GetExitCodeProcess.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87
        try:
            status = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(status)):
                return True  # A process we cannot query is conservatively alive.
            return status.value == 259  # STILL_ACTIVE; an exited handle may remain open.
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        if sys.platform.startswith("linux"):
            try:
                # kill(0) also succeeds for an exited child awaiting its reaper.
                # comm can contain spaces and parentheses; fields follow its last ')'.
                fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
                if fields and fields[0] in {"Z", "X", "x"}:
                    return False
            except FileNotFoundError:
                os.kill(pid, 0)  # Recheck disappearance; /proc itself may be unavailable.
            except (OSError, UnicodeError):
                pass  # Unknown/query-denied owners remain conservatively alive.
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def recover(root, *, abandoned=False):
    """Recover only known before/after bytes; stop if somebody edited a pending file."""
    lock_path = confined(root, "state/write.lock")
    if abandoned and lock_path.exists():
        owner = json.loads(lock_path.read_text(encoding="utf-8"))
        if process_alive(owner.get("pid")):
            raise ValueError("잠금 소유 프로세스가 살아 있어 복구할 수 없습니다.")
        lock_path.unlink()
    with lock(root, recovering=True):
        pending = confined(root, "state/pending.json")
        if not pending.exists():
            return {"recovered": False, "message": "미완료 변경 없음"}
        journal = json.loads(pending.read_text(encoding="utf-8"))
        if not isinstance(journal, dict) or journal.get("schema_version") != 1 or not isinstance(journal.get("entries"), list):
            raise ValueError("잘못된 복구 기록입니다.")
        for entry in journal["entries"]:
            if entry.get("path") in {"state/pending.json", "state/write.lock"}:
                raise ValueError("잘못된 복구 대상입니다.")
            path = confined(root, entry["path"])
            before = base64.b64decode(entry["before"], validate=True) if entry["before"] is not None else None
            current = digest(path.read_bytes()) if path.is_file() else None
            if current not in {digest(before) if before is not None else None, entry["after"]}:
                raise ValueError(f"복구 중단: {entry['path']}가 기록 이후 변경되었습니다.")
        _restore(root, journal["entries"])
        _clear_pending(root)
        return {"recovered": True, "files": [entry["path"] for entry in journal["entries"]]}
