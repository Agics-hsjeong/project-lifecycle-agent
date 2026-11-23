"""Durable, metadata-only hook inbox independent of the business-state lock."""
from contextlib import contextmanager
from datetime import datetime, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from tools import native_observation as observation
from tools.sensitive_data import redact_text
from tools.storage import ProjectBusyError, atomic_write, confined, json_bytes

DIRECTORY = "state/native-inbox"
LOCK_FILE = "state/native-inbox.lock"
MAX_EVENT = 64 * 1024
MAX_QUEUED = 512
MAX_TOTAL = 8 * 1024 * 1024
ALLOWED = {"event_id", "kind", "session_id", "native_agent_id", "role", "task_id", "at", "call_id", "parent_id", "source", "summary", "tool_name", "delegation_token", "native_type", "span_id"}
REQUIRED = {"event_id", "kind", "session_id", "source", "at"}
DIGEST_NAME = re.compile(r"[0-9a-f]{64}\.json\Z")
ERROR = "네이티브 관측 보관함에 처리하지 못한 이벤트가 있습니다. 원본은 보존했습니다."


class InboxBusyError(ValueError):
    """The exact inbox OS lock is busy; queued events remain durable."""

    def __init__(self, path):
        super().__init__("네이티브 관측 보관함이 사용 중입니다. 자동으로 다시 수신합니다.")
        self.lock_path = Path(path).absolute()


def _normalise(provider, event):
    if provider not in {"codex", "claude"} or not isinstance(event, dict) or set(event) - ALLOWED or not REQUIRED.issubset(event):
        raise ValueError("정규화한 호스트 메타데이터만 보관할 수 있습니다.")
    if (not isinstance(event["kind"], str) or event["kind"] not in observation.KINDS
            or not isinstance(event["source"], str) or event["source"] not in observation.SOURCES):
        raise ValueError("지원하지 않는 호스트 관측 종류입니다.")
    clean = {}
    identifiers = {"event_id", "session_id", "native_agent_id", "task_id", "call_id", "parent_id", "span_id", "native_type", "role"}
    for key, value in event.items():
        if value is None and key not in REQUIRED:
            continue
        if not isinstance(value, str) or len(value) > (500 if key == "summary" else 240):
            raise ValueError("호스트 관측 메타데이터의 길이와 형식을 확인하세요.")
        if key in identifiers and not observation.ID.fullmatch(value):
            raise ValueError("호스트 관측 식별자 형식이 잘못되었습니다.")
        if key == "delegation_token" and not re.fullmatch(r"[0-9a-f]{24}", value):
            raise ValueError("호스트 위임 연결 형식이 잘못되었습니다.")
        if key == "tool_name":
            value = value if value in observation.TOOL_NAMES else "local_tool"
        if key == "summary":
            value = redact_text(value)
        clean[key] = value
    try:
        stamp = datetime.fromisoformat(clean["at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError()
    except (ValueError, OverflowError):
        raise ValueError("호스트 관측 시각에는 시간대가 필요합니다.") from None
    # Keep the original host timestamp spelling: ingest signatures are immutable.
    return {"schema_version": 1, "provider": provider, "event": clean}


def _directory(root, *, create=False):
    folder = confined(root, DIRECTORY)
    if create:
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    return folder


@contextmanager
def _inbox_lock(root):
    """Cross-process lock released by the OS on exit; never takes write.lock."""
    path = confined(root, LOCK_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        deadline = time.monotonic() + 2
        while True:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                if os.fstat(fd).st_size == 0:
                    os.lseek(fd, 0, os.SEEK_SET)
                    os.write(fd, b"0")
                break
            except OSError as exc:
                # This fd was already opened successfully. Retry only an OS
                # byte-range/flock conflict, never unrelated I/O or ACL errors.
                if exc.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} and getattr(exc, "winerror", None) not in {33}:
                    raise
                if time.monotonic() >= deadline:
                    raise InboxBusyError(path) from exc
                time.sleep(0.01)
        yield
    finally:
        if acquired:
            os.lseek(fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _files(root, folder):
    if not folder.exists():
        return []
    result = []
    for path in folder.iterdir():
        safe = confined(root, DIRECTORY + "/" + path.name)
        try:
            info = safe.stat()
        except FileNotFoundError:
            continue  # A hook acknowledged it after successful immediate ingest.
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(ERROR)
        result.append(safe)
        if len(result) > MAX_QUEUED:
            raise ValueError("네이티브 관측 보관함의 이벤트 수 제한을 초과했습니다.")
    if _total_size(result) > MAX_TOTAL:
        raise ValueError("네이티브 관측 보관함의 용량 제한을 초과했습니다.")
    return result


def _total_size(paths):
    total = 0
    for path in paths:
        try:
            total += path.stat().st_size
        except FileNotFoundError:
            pass  # Immediate acknowledgement may race the directory snapshot.
    return total


def enqueue(project, provider, event):
    """Persist before an engine write; return the private acknowledgement path."""
    record = _normalise(provider, event)
    raw = json_bytes(record)
    if len(raw) > MAX_EVENT:
        raise ValueError("네이티브 관측 이벤트 크기 제한을 초과했습니다.")
    root = observation._root(project)
    name = hashlib.sha256(raw).hexdigest() + ".json"
    with _inbox_lock(root):
        folder = _directory(root, create=True)
        target = confined(root, DIRECTORY + "/" + name)
        if target.is_file():
            if target.stat().st_size > MAX_EVENT or target.read_bytes() != raw:
                raise ValueError(ERROR)
            return target
        files = _files(root, folder)
        if len(files) >= MAX_QUEUED or _total_size(files) + len(raw) > MAX_TOTAL:
            raise ValueError("네이티브 관측 보관함이 가득 찼습니다. 기존 이벤트는 보존했습니다.")
        atomic_write(target, raw)
        return target


def drain(project, max_events=32):
    """Apply bounded valid events, retaining every failed/corrupt original.

    Failed records do not consume the success budget, so a bad first event
    cannot starve later valid events even with max_events=1. At most the bounded
    512-file inbox is inspected; errors are reported only after other work.
    """
    if type(max_events) is not int or not 1 <= max_events <= MAX_QUEUED:
        raise ValueError("관측 수신 묶음은 1~512개여야 합니다.")
    root = observation._root(project)
    with _inbox_lock(root):
        folder = _directory(root)
        paths = _files(root, folder)
    pending, failed = [], False
    busy = None
    for path in paths:
        try:
            if not DIGEST_NAME.fullmatch(path.name):
                raise ValueError(ERROR)
            with path.open("rb") as stream:
                raw = stream.read(MAX_EVENT + 1)
            if len(raw) > MAX_EVENT or hashlib.sha256(raw).hexdigest() + ".json" != path.name:
                raise ValueError(ERROR)
            record = json.loads(raw)
            if not isinstance(record, dict) or set(record) != {"schema_version", "provider", "event"} or record["schema_version"] != 1:
                raise ValueError(ERROR)
            normal = _normalise(record["provider"], record["event"])
            if normal != record:
                raise ValueError(ERROR)
            stamp = datetime.fromisoformat(record["event"]["at"].replace("Z", "+00:00")).astimezone(timezone.utc)
            pending.append((stamp, path.name, path, raw, record))
        except FileNotFoundError:
            continue  # An immediate hook acknowledgement already consumed it.
        except (ValueError, OSError, TypeError, KeyError, RecursionError):
            failed = True
    consumed = 0
    for _, _, path, raw, record in sorted(pending):
        if consumed >= max_events:
            break
        try:
            observation.ingest(root, record["provider"], record["event"])
            with _inbox_lock(root):
                safe = confined(root, DIRECTORY + "/" + path.name)
                if safe.exists():
                    with safe.open("rb") as stream:
                        current = stream.read(MAX_EVENT + 1)
                    if current != raw:
                        raise ValueError(ERROR)
                    safe.unlink()
            consumed += 1
        except (ProjectBusyError, InboxBusyError) as exc:
            # Preserve contention as contention. Do not acknowledge this record
            # or call it corrupt; a retry replays its immutable event ID safely.
            busy = exc
        except (ValueError, OSError, TypeError, KeyError, RecursionError):
            failed = True
    if failed:
        raise ValueError(ERROR)
    if busy is not None:
        raise busy
    return consumed
