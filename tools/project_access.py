"""Explicit byte-preserving access repair for exact engine-managed metadata.

No recursive ACL operations, business edits, customer documents or code repairs.
"""
import base64
import hashlib
import json
import os
import re

from tools import history, lifecycle, native_observation, runs, starter, workspace
from tools.storage import atomic_write, confined, lock

MAX_FILES = 256
MAX_FILE = 8 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
STATE_READERS = {
    "state/workspace.json": workspace.read,
    "state/workspace-runtime.json": workspace.runtime_read,
    "state/workspace-activity.json": workspace.activity_read,
    "state/workspace-native.json": native_observation._read,
    "state/runs.json": runs.read,
}
HISTORY = re.compile(r"state/history/([0-9a-f]{32})\.json\Z")


def _read_raw(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE + 1)
    if len(raw) > MAX_FILE:
        raise ValueError("관리 기록 접근 복원 크기 제한을 초과했습니다.")
    return raw


def _validate(root, relative, raw, revisions):
    table = next((name for name in starter.HEADERS if relative == "registers/" + name + ".csv"), None)
    if table is not None:
        starter.read_table(root, table)
        return
    if relative == "state/events.jsonl":
        lifecycle.events(root)
        return
    value = json.loads(raw)
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("생성된 schema 1 관리 기록만 접근 복원할 수 있습니다.")
    if relative == "project.json":
        starter.load_project(root)
    elif relative in STATE_READERS:
        if relative == native_observation.STORE:
            fields = {"schema_version", "seq", "sessions", "agents", "events", "bindings", "calls", "focus", "cursors"}
            if (set(value) != fields or type(value.get("seq")) is not int or value["seq"] < 0
                    or not isinstance(value.get("events"), list)
                    or any(not isinstance(value.get(key), dict) for key in fields - {"schema_version", "seq", "events"})):
                raise ValueError("생성된 네이티브 관측 구조만 접근 복원할 수 있습니다.")
        STATE_READERS[relative](root)
    else:
        matched = HISTORY.fullmatch(relative)
        if not matched or matched[1] not in revisions:
            raise ValueError("실제 진행 이력에 등록된 변경 보관 파일만 접근 복원할 수 있습니다.")
        _, record = history.read(root, matched[1])
        for entry in record["entries"]:
            if not isinstance(entry, dict) or set(entry) != {"path", "before", "after"} or not isinstance(entry["path"], str):
                raise ValueError("변경 보관 항목 형식이 잘못되었습니다.")
            confined(root, entry["path"])
            if entry["before"] is not None:
                if not isinstance(entry["before"], str):
                    raise ValueError("변경 보관 원본 형식이 잘못되었습니다.")
                base64.b64decode(entry["before"], validate=True)
            if entry["after"] is not None and (not isinstance(entry["after"], str) or not re.fullmatch(r"[0-9a-f]{64}", entry["after"])):
                raise ValueError("변경 보관 해시 형식이 잘못되었습니다.")


def repair_project_access(project, paths):
    """Explicit paths only; preserve raw bytes and business history exactly."""
    if os.name != "nt":
        raise OSError("프로젝트 관리 기록 보안 복원은 Windows에서만 사용할 수 있습니다.")
    if not isinstance(paths, list) or not paths or len(paths) > MAX_FILES or any(not isinstance(p, str) for p in paths) or len(set(paths)) != len(paths):
        raise ValueError("고유한 관리 기록 상대 경로를 명시하세요.")
    allowed = {"project.json", "state/events.jsonl", *STATE_READERS,
               *("registers/" + name + ".csv" for name in starter.HEADERS)}
    if any(p not in allowed and not HISTORY.fullmatch(p) for p in paths):
        raise ValueError("정확한 엔진 관리 기록만 접근 복원할 수 있습니다.")
    from tools.windows_security import OWNER_RIGHTS, read_security
    root, _ = starter.load_project(project)
    with lock(root):  # Includes the normal pending-journal refusal.
        events = lifecycle.events(root)
        revisions = {record["revision"] for record in events}
        if len(revisions) != len(events) or any(not re.fullmatch(r"[0-9a-f]{32}", r) for r in revisions):
            raise ValueError("진행 이력 revision 형식이 잘못되었습니다.")
        original, total = {}, 0
        # Validate every requested path and its schema before any replacement.
        for relative in paths:
            path = confined(root, relative)
            raw = _read_raw(path)
            total += len(raw)
            if len(raw) > MAX_FILE or total > MAX_TOTAL:
                raise ValueError("관리 기록 접근 복원 크기 제한을 초과했습니다.")
            _validate(root, relative, raw, revisions)
            original[relative] = raw
        owner = read_security(root)["owner"]
        results = []
        for relative, raw in original.items():
            path = confined(root, relative)
            if _read_raw(path) != raw:
                raise ValueError("사전 검증 이후 관리 기록이 변경되어 중단합니다.")
            security = read_security(path)
            dynamic = any(a["sid"] == OWNER_RIGHTS and a["mask"] != 0 for a in security["aces"])
            repaired = security["owner"] != owner and dynamic
            if repaired:
                # Same strict inherited-policy/capability checks as native repair.
                atomic_write(path, raw, security_anchor=root, repair_owner=True)
            if _read_raw(path) != raw:
                raise ValueError("접근 복원 후 원본 바이트 검증이 일치하지 않습니다.")
            results.append({"path": relative, "sha256": hashlib.sha256(raw).hexdigest(),
                            "bytes": len(raw), "content_preserved": True, "repaired": repaired})
        return {"content_preserved": True, "files": results}
