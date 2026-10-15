"""Confined, serialized project writes with rollback and crash recovery."""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
import stat
from pathlib import Path
import tempfile


def digest(value):
    return hashlib.sha256(value).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def confined(root, relative):
    root = Path(root).resolve()
    if not isinstance(relative, str) or not relative or "\\" in relative or ":" in relative:
        raise ValueError("프로젝트 내부의 '/' 상대 경로가 필요합니다.")
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts:
        raise ValueError("프로젝트 외부 경로는 허용하지 않습니다.")
    path = root / rel
    if not path.resolve().is_relative_to(root):
        raise ValueError("경로가 프로젝트 밖을 가리킵니다.")
    # Do not mutate through links, including links back into the same project.
    current = path
    while current != root:
        attributes = getattr(current.lstat(), "st_file_attributes", 0) if current.exists() else 0
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
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("다른 쓰기 작업의 잠금이 있습니다. 종료 여부를 확인한 뒤 recover를 사용하세요.") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump({"pid": os.getpid()}, handle)
    try:
        if not recovering and confined(root, "state/pending.json").exists():
            raise ValueError("미완료 변경 기록이 있습니다. recover로 먼저 복구하세요.")
        yield
    finally:
        path.unlink(missing_ok=True)


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
                        "after": digest(content)})
    atomic_write(pending, json_bytes({"schema_version": 1, "entries": entries}))
    try:
        for relative, content in changes.items():
            atomic_write(confined(root, relative), content)
    except BaseException:
        _restore(root, entries)
        pending.unlink()
        raise
    pending.unlink()


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
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return ctypes.get_last_error() != 87
        kernel.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
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
        pending.unlink()
        return {"recovered": True, "files": [entry["path"] for entry in journal["entries"]]}
