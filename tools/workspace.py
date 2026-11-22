"""Local project snapshots and version-bound human artefact gates."""
import argparse
import base64
import difflib
from contextvars import ContextVar
from functools import wraps
import json
from datetime import datetime, timedelta, timezone
import math
from pathlib import Path
import re
import uuid

from tools import lifecycle, runs, starter, workflow
from tools.storage import commit, confined, consistent_read, digest, json_bytes, lock
from tools.sensitive_data import is_sensitive_path, contains_sensitive_data, redact_text, redact_value

STORE = "state/workspace.json"
RUNTIME_STORE = "state/workspace-runtime.json"
ACTIVITY_STORE = "state/workspace-activity.json"
MAX_ACTIVITY = 1000
RUNNER_STALE_SECONDS = 30
PHASES = [
    {"id": "01", "title": "사업과 요구사항", "stages": ["discovery"]},
    {"id": "02", "title": "제안과 발표", "stages": ["proposal"]},
    {"id": "03", "title": "설계와 구현", "stages": ["kickoff", "planning", "design", "publishing", "implementation"]},
    {"id": "04", "title": "검증과 배포", "stages": ["verification", "release"]},
    {"id": "05", "title": "인수와 운영", "stages": ["handover", "maintenance"]},
]
MAX_FILE = 2 * 1024 * 1024
_HASH_SCOPE = ContextVar('workspace_snapshot_hashes', default=None)
_TITLE_SCOPE = ContextVar('workspace_snapshot_titles', default=None)


def snapshot_hash_scope(function):
    """Memoize unchanged files only during one consistent snapshot read.

    Never reuse across requests: external edits need no lifecycle revision.
    Context-local storage avoids sharing between HTTP threads or projects.
    """
    @wraps(function)
    def scoped(*args, **kwargs):
        token = _HASH_SCOPE.set({})
        title_token = _TITLE_SCOPE.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _HASH_SCOPE.reset(token)
            _TITLE_SCOPE.reset(title_token)
    return scoped


class Conflict(ValueError):
    """A revision or reviewed file version is no longer current."""


def read(root):
    path = confined(root, STORE)
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1}
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise ValueError("관제 메타데이터 형식이 잘못되었습니다.")
    for field, default in (("tasks", {}), ("gates", []), ("decisions", []), ("actions", {}),
                           ("agents", {}), ("queue", []), ("baselines", {}), ("feedback", []), ("usage", []), ("superseded_steps", {}), ("reviews", []), ("execution_scopes", []), ("policy", {"view_mode": "observer", "auto_run": False})):
        value.setdefault(field, default)
    return value


def revision(root):
    history = lifecycle.events(root)
    return history[-1]["revision"] if history else None


def require_revision(root, payload):
    if "expected_revision" not in payload or payload["expected_revision"] != revision(root):
        raise Conflict("진행 기록이 변경되었습니다. 새로고침 후 다시 검토하세요.")


def save(root, store, action, actor, details, extra=None):
    changes = {STORE: json_bytes(store), **(extra or {})}
    rev = lifecycle.add_event(root, changes, action, actor, details)
    commit(root, changes)
    return rev


def file_bytes(root, relative):
    path = confined(root, relative)
    if not path.is_file():
        return None
    if path.stat().st_size > MAX_FILE:
        raise ValueError("관제 파일 크기 제한(2MiB)을 초과했습니다.")
    with path.open('rb') as stream:
        value = stream.read(MAX_FILE + 1)
    if len(value) > MAX_FILE:
        raise ValueError("관제 파일 크기 제한(2MiB)을 초과했습니다.")
    return value


def file_hash(root, relative):
    path = confined(root, relative)
    if not path.is_file():
        return None
    info = path.stat()
    signature = (str(path), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    memo = _HASH_SCOPE.get()
    if memo is not None and signature in memo:
        return memo[signature]
    import hashlib
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            result.update(chunk)
    value = result.hexdigest()
    # Concurrent external writes cannot poison this request's memo.
    after = path.stat()
    if memo is not None and (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) == signature[1:]:
        memo[signature] = value
    return value


def version_hashes(root, gate):
    return {p: file_hash(root, p) for p in gate["paths"]}


def context_hash(root, store, gate, *, tasks=None):
    """Requirements/input changes invalidate an artefact approval as well."""
    root, data, tables = lifecycle.bundle(root)
    tasks = task_records(root, data, tables, store, check_context=False) if tasks is None else tasks
    task = tasks.get(gate["task_id"], {})
    upstream_ids = ancestors(tasks, gate["task_id"])
    ids = set(task.get("requirement_ids", []))
    for identifier in upstream_ids:
        ids.update(tasks.get(identifier, {}).get("requirement_ids", []))
    rows = [{k: row[k] for k in ("requirement_id", "source", "description", "acceptance_criteria", "priority", "deliverable_ids")}
            for row in tables["requirements"] if row["requirement_id"] in ids]
    inputs = {p: context_files(root, p) for p in sorted(task.get("inputs", []))}
    upstream = []
    for identifier in sorted(upstream_ids):
        parent = tasks.get(identifier, {})
        refs = set(parent.get("evidence_refs", []) + parent.get("expected_outputs", []))
        for parent_gate in store["gates"]:
            if parent_gate["task_id"] == identifier:
                refs.update(parent_gate["paths"])
        upstream.append({"id": identifier, "depends_on": sorted(parent.get("depends_on", [])),
                         "files": {ref: context_files(root, ref) for ref in sorted(refs)}})
    manifest_path = confined(root, "inputs/manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {"inputs": []}
    if not isinstance(manifest, dict) or not isinstance(manifest.get("inputs"), list):
        raise ValueError("입력 자료 목록이 잘못되었습니다.")
    baseline_inputs = []
    for record in manifest["inputs"]:
        if record.get("purpose") == "operating_note":
            continue
        extraction = record.get("extraction", {})
        refs = [record["path"]] + [extraction[k] for k in ("path", "review_ref") if extraction.get(k)]
        baseline_inputs.append({"input_id": record.get("input_id"), "path": record["path"],
            "sha256": record.get("sha256"), "purpose": record.get("purpose", "baseline"),
            "extraction": {k: extraction[k] for k in ("status", "path", "review_ref", "page_count", "pages") if k in extraction},
            "files": {ref: context_files(root, ref) for ref in sorted(refs)}})
    return digest(json_bytes({"requirements": sorted(rows, key=lambda row: row["requirement_id"]), "inputs": inputs,
        "depends_on": sorted(task.get("depends_on", [])), "upstream": upstream,
        "objective": data.get("objective"), "scope": data.get("scope_baseline"), "technical": data.get("technical"),
        "profile": data.get("workflow", {}).get("profile"),
        "baseline_inputs": sorted(baseline_inputs, key=lambda item: (item.get("input_id") or "", item["path"]))}))


def context_files(root, relative):
    """Hash actual referenced bytes, including empty files and missing paths."""
    path = confined(root, relative)
    if path.is_file():
        return {relative: file_hash(root, relative)}
    if not path.is_dir():
        return None
    paths = [p for p in path.rglob("*") if p.is_file() and not set(p.parts).intersection({"__pycache__", ".git", "node_modules", ".venv"})]
    if len(paths) > 10000 or sum(p.stat().st_size for p in paths) > 64 * 1024 * 1024:
        raise ValueError("입력 기준선 크기 제한을 초과했습니다.")
    return {p.relative_to(root).as_posix(): file_hash(root, p.relative_to(root).as_posix()) for p in sorted(paths)}


def gate_status(root, store, gate):
    decisions = [d for d in store["decisions"] if d["gate_id"] == gate["id"]]
    if not decisions:
        return "pending"
    last = decisions[-1]
    if last["decision"] == "approve":
        return "approved" if last["target_hashes"] == version_hashes(root, gate) and last.get("context_hash") == context_hash(root, store, gate) else "stale"
    revisions = [m for m in store["tasks"].values() if m.get("rework") and m.get("gate_id") == gate["id"]]
    if revisions and all(m.get("status") == "done" for m in revisions) and last["target_hashes"] != version_hashes(root, gate):
        return "pending"
    return "revision_requested"



def execution_path(root, relative):
    path = valid_artifact(root, relative)
    parts = path.relative_to(root).parts
    if parts[0] in {"registers", "inputs", "templates", "process", ".agents"} or relative in {"project.json", "HANDOFF.md", "AGENTS.md", "CLAUDE.md"} or parts[:2] == ("reports", "workspace-decisions"):
        raise ValueError("실행 작업자는 관리 기록·승인 근거·원본 입력을 수정할 수 없습니다.")
    return path


def validate_execution(root, value):
    allowed = {"instruction", "allowed_paths", "expected_outputs", "verification_commands", "input_paths", "verification_mode", "verification_reason", "runtime_ref"}
    if not isinstance(value, dict) or set(value) - allowed or not {"instruction", "allowed_paths", "expected_outputs"}.issubset(value):
        raise ValueError("실행 계획은 instruction/allowed_paths/expected_outputs가 필요합니다.")
    if not isinstance(value["instruction"], str) or not value["instruction"].strip() or len(value["instruction"]) > 50000:
        raise ValueError("실행 지시는 50,000자 이하의 비어 있지 않은 문자열이어야 합니다.")
    for key in ("allowed_paths", "expected_outputs"):
        refs = value[key]
        if not isinstance(refs, list) or not refs or len(refs) > 200 or any(not isinstance(ref, str) or not ref or ref in {".", "./"} for ref in refs) or len(refs) != len(set(refs)):
            raise ValueError("실행 범위와 출력은 고유한 프로젝트 상대 경로 목록이어야 합니다.")
        for ref in refs:
            execution_path(root, ref.rstrip("/"))
    outputs = value["expected_outputs"]
    for relative in outputs:
        path = execution_path(root, relative)
        if relative.endswith("/") or path.is_dir():
            raise ValueError("예상 출력은 구체적인 파일이어야 합니다.")
        if not any(relative == prefix.rstrip("/") or relative.startswith(prefix.rstrip("/") + "/") for prefix in value["allowed_paths"]):
            raise ValueError("예상 출력이 허용된 작업 경로 밖에 있습니다.")
    commands = value.get("verification_commands", [])
    if not isinstance(commands, list) or len(commands) > 30:
        raise ValueError("검증 명령은 argv 목록의 목록이어야 합니다.")
    for command in commands:
        if not isinstance(command, list) or not command or len(command) > 100 or any(not isinstance(arg, str) or not arg or len(arg) > 10000 or "\x00" in arg for arg in command):
            raise ValueError("검증 명령에는 셸 문자열 대신 문자열 argv 목록을 사용하세요.")
        if any(redact_text(arg) != arg or contains_sensitive_data(arg) for arg in command):
            raise ValueError("검증 명령에 인증 값·비밀값을 직접 넣지 마세요. 신뢰된 환경의 자격증명 경계를 사용하세요.")
        executable = command[0].replace("\\", "/").rsplit("/", 1)[-1].lower().removesuffix(".exe")
        if executable in {"cmd", "powershell", "pwsh", "sh", "bash", "zsh", "fish", "wscript", "cscript"}:
            raise ValueError("검증 명령에서 별도 셸을 실행하지 않습니다.")
        for argument in command[1:]:
            candidate = argument.split("=", 1)[-1]
            if candidate.startswith(("../", "..\\", "/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", candidate):
                raise ValueError("검증 명령은 프로젝트 밖 경로를 참조할 수 없습니다.")
            if candidate.startswith(("state/", "registers/", ".git/", ".codex/", ".claude/", ".agents/")) or candidate in {"project.json", "HANDOFF.md"}:
                raise ValueError("검증 명령이 관리 제어 파일을 대상으로 할 수 없습니다.")
    mode = value.get("verification_mode", "execute")
    if mode not in {"execute", "artifact_only"}:
        raise ValueError("검증 방식은 execute 또는 artifact_only입니다.")
    reason = value.get("verification_reason")
    if reason is not None and (not isinstance(reason, str) or not reason.strip() or len(reason) > 2000):
        raise ValueError("검증 정책 사유는 비어 있지 않은 2,000자 이하 문자열이어야 합니다.")
    if mode == "artifact_only" and (not reason or commands):
        raise ValueError("artifact_only에는 명시한 검수 사유와 빈 실행 검증 명령이 필요합니다.")
    if "input_paths" in value:
        refs = value["input_paths"]
        if not isinstance(refs, list) or len(refs) > 200 or any(not isinstance(ref, str) or not ref or ref in {".", "./"} for ref in refs) or len(refs) != len(set(refs)):
            raise ValueError("작업 입력은 고유한 프로젝트 상대 경로 목록이어야 합니다.")
        for ref in refs:
            valid_artifact(root, ref.rstrip("/"))
    if "runtime_ref" in value and (not isinstance(value["runtime_ref"], str) or not re.fullmatch(r"[0-9a-f]{64}", value["runtime_ref"])):
        raise ValueError("검증 환경은 등록된 64자리 SHA-256 식별자로만 참조합니다.")
    result = {"instruction": redact_text(value["instruction"].strip()), "allowed_paths": [ref.rstrip("/") for ref in value["allowed_paths"]],
              "expected_outputs": list(outputs), "verification_commands": commands}
    for key in ("input_paths", "verification_mode", "verification_reason", "runtime_ref"):
        if key in value:
            result[key] = redact_text(value[key]) if key == "verification_reason" else value[key]
    return result


def rework_execution(root, source, paths, feedback):
    original = source.get("execution") or {}
    instruction = original.get("instruction", source["title"])
    instruction += "\n\n사용자 수정 요청:\n" + feedback + "\n검토 대상 파일: " + "; ".join(paths)
    allowed = list(original.get("allowed_paths", []))
    for relative in paths:
        if not any(relative == prefix.rstrip("/") or relative.startswith(prefix.rstrip("/") + "/") for prefix in allowed):
            allowed.append(relative)
    return validate_execution(root, {**original, "instruction": instruction, "allowed_paths": allowed, "expected_outputs": list(paths),
                                     "verification_commands": original.get("verification_commands", [])})


def runtime_read(root):
    path = confined(root, RUNTIME_STORE)
    value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"schema_version": 1, "runners": {}}
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("runners"), dict):
        raise ValueError("실행기 상태 형식이 잘못되었습니다.")
    return value


def runner_records(root):
    from tools.storage import process_alive
    now = datetime.now(timezone.utc)
    result = []
    for stored in runtime_read(root)["runners"].values():
        item = dict(stored)
        age = (now - datetime.fromisoformat(item["last_seen"])).total_seconds()
        item["connected"] = (item["status"] in {"starting", "waiting", "running", "stopping"} and age <= RUNNER_STALE_SECONDS
                             and process_alive(item["pid"]))
        if item["status"] in {"starting", "waiting", "running", "stopping"} and not item["connected"]:
            item["status"] = "disconnected"
        result.append(item)
    return result


def runner_event(folder, payload):
    from tools.storage import process_alive
    allowed = {"expected_revision", "runner_id", "provider", "pid", "status", "child_pid", "task_id", "notes"}
    if not isinstance(payload, dict) or set(payload) - allowed or not {"expected_revision", "runner_id", "provider", "pid", "status"}.issubset(payload):
        raise ValueError("실행기 상태 필드가 잘못되었습니다.")
    if not isinstance(payload["runner_id"], str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,160}", payload["runner_id"]):
        raise ValueError("실제 실행기 ID가 필요합니다.")
    if payload["provider"] not in {"codex", "claude"} or payload["status"] not in {"starting", "waiting", "running", "stopping", "stopped", "failed"}:
        raise ValueError("실행 호스트 또는 실행기 상태가 잘못되었습니다.")
    if type(payload["pid"]) is not int or payload["pid"] <= 0 or (payload.get("child_pid") is not None and (type(payload["child_pid"]) is not int or payload["child_pid"] <= 0)):
        raise ValueError("실제 실행기·작업자 PID가 필요합니다.")
    if "notes" in payload and (not isinstance(payload["notes"], str) or len(payload["notes"]) > 10000):
        raise ValueError("실행기 상태 메모는 10,000자 이하 문자열이어야 합니다.")
    payload = {**payload, **({"notes": redact_text(payload["notes"])} if "notes" in payload else {})}
    root = starter.load_project(folder)[0]
    with lock(root):
        require_revision(root, payload)
        runtime = runtime_read(root)
        previous = runtime["runners"].get(payload["runner_id"])
        if previous and (previous["provider"] != payload["provider"] or previous["pid"] != payload["pid"]):
            raise Conflict("다른 실행기의 상태를 덮어쓸 수 없습니다.")
        tasks = task_records(root, starter.load_project(root)[1], lifecycle.bundle(root)[2], read(root))
        if payload.get("task_id") is not None and payload["task_id"] not in tasks:
            raise ValueError("실제 작업 ID가 필요합니다.")
        if payload["status"] in {"starting", "waiting", "running", "stopping"} and not process_alive(payload["pid"]):
            raise Conflict("실행기 프로세스가 이미 종료됐습니다.")
        item = {"id": payload["runner_id"], "runner_id": payload["runner_id"], "provider": payload["provider"],
                "pid": payload["pid"], "status": payload["status"], "task_id": payload.get("task_id"),
                "child_pid": payload.get("child_pid"), "notes": payload.get("notes", ""), "last_seen": starter.now()}
        runtime["runners"][payload["runner_id"]] = item
        commit(root, {RUNTIME_STORE: json_bytes(runtime)})
        return {"revision": revision(root), "runner": item, "snapshot": snapshot(root)}



def activity_read(root):
    path = confined(root, ACTIVITY_STORE)
    value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"schema_version": 1, "seq": 0, "events": []}
    if not isinstance(value, dict) or value.get("schema_version") != 1 or type(value.get("seq")) is not int or not isinstance(value.get("events"), list):
        raise ValueError("활동 기록 형식이 잘못되었습니다.")
    return value


def public_summary(value, limit=1000):
    """Public tool/status summaries only. Never store hidden model reasoning."""
    if not isinstance(value, str) or not value.strip() or len(value) > 10000:
        raise ValueError("활동 요약은 비어 있지 않은 10,000자 이하 문자열이어야 합니다.")
    return redact_text(value).strip()[:limit]



def activity_event(folder, payload):
    """Append bounded, revision-neutral observations from real host adapters.

    This endpoint does not complete tasks, approve gates, or claim model thoughts.
    A native host can report its actual tool agent ID without claiming runner jobs.
    """
    allowed = {"task_id", "agent_id", "role", "provider", "kind", "summary", "event_id", "command_id", "path", "paths", "peer_role", "status", "source", "pid", "parent_id"}
    kinds = {"started", "tool_started", "tool_finished", "file_changed", "verification_started", "verification_passed", "verification_failed", "completed", "failed", "handoff", "repair_requested", "waiting", "action", "test", "info", "error", "review", "heartbeat"}
    if not isinstance(payload, dict) or set(payload) - allowed or payload.get("kind") not in kinds:
        raise ValueError("공개 활동 이벤트 필드가 잘못되었습니다.")
    for key in ("agent_id", "role", "provider"):
        if not isinstance(payload.get(key), str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", payload[key]):
            raise ValueError("실제 작업자 ID·호스트·역할이 필요합니다.")
    if payload["provider"] not in {"codex", "claude"}:
        raise ValueError("활동 호스트는 codex 또는 claude입니다.")
    summary = public_summary(payload.get("summary"))
    if payload.get("status") is not None and payload["status"] not in {"queued", "waiting", "running", "started", "completed", "failed", "blocked", "idle", "done", "success", "info", "verifying", "attention"}:
        raise ValueError("활동 상태가 잘못되었습니다.")
    for key in ("event_id", "source", "peer_role", "parent_id"):
        if payload.get(key) is not None and (not isinstance(payload[key], str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", payload[key])):
            raise ValueError("활동 식별자 형식이 잘못되었습니다.")
    if payload.get("pid") is not None and (type(payload["pid"]) is not int or payload["pid"] <= 0):
        raise ValueError("작업자 PID는 실제 양수여야 합니다.")
    root, data, tables = lifecycle.bundle(folder)
    with lock(root):
        store = read(root)
        tasks = task_records(root, data, tables, store)
        if payload.get("task_id") is not None and payload["task_id"] not in tasks:
            raise ValueError("실제 작업 ID가 필요합니다.")
        if payload.get("command_id") is not None:
            command = next((c for c in store["queue"] if c["id"] == payload["command_id"]), None)
            if not command or command.get("agent_id") != payload["agent_id"] or command["task_id"] != payload.get("task_id"):
                raise Conflict("이 작업자의 실제 실행 요청과 활동이 일치하지 않습니다.")
        paths = payload.get("paths", []) + ([payload["path"]] if payload.get("path") else []) if isinstance(payload.get("paths", []), list) else None
        if paths is None or len(paths) > 20 or any(not isinstance(p, str) for p in paths):
            raise ValueError("공개 활동 파일 경로는 20개 이하 목록이어야 합니다.")
        for relative in paths:
            valid_artifact(root, relative)
        activity = activity_read(root)
        signature = digest(json_bytes(payload))
        event_id = payload.get("event_id") or "ACT-" + uuid.uuid4().hex
        previous = next((e for e in activity["events"] if e["id"] == event_id), None)
        if previous:
            if previous.get("signature") != signature:
                raise Conflict("같은 활동 ID를 다른 내용에 사용할 수 없습니다.")
            return {"event": {k: v for k, v in previous.items() if k != "signature"}, "duplicate": True, "revision": revision(root)}
        activity["seq"] += 1
        event = {"id": event_id, "seq": activity["seq"], "at": starter.now(), "agent_id": payload["agent_id"], "role": payload["role"],
                 "provider": payload["provider"], "kind": payload["kind"], "summary": summary, "source": payload.get("source", "host_report"),
                 "signature": signature, "paths": list(dict.fromkeys(paths))}
        for key in ("task_id", "command_id", "peer_role", "status", "pid", "parent_id"):
            if payload.get(key) is not None:
                event[key] = payload[key]
        activity["events"] = (activity["events"] + [event])[-MAX_ACTIVITY:]
        commit(root, {ACTIVITY_STORE: json_bytes(activity)})
        return {"event": {k: v for k, v in event.items() if k != "signature"}, "duplicate": False, "revision": revision(root)}


def enrich_activity(root, result):
    recorded = activity_read(root)
    events = [{k: v for k, v in e.items() if k != "signature"} for e in recorded["events"]]
    result["activities"] = events[-300:]
    result["activity"] = result["activities"]
    result["activity_seq"] = recorded["seq"]
    now = datetime.now(timezone.utc)
    for task in result["tasks"]:
        activity = [e for e in events if e.get("task_id") == task["id"] and activity_matches_task(e, task, result["commands"])]
        task["activity"] = activity[-30:]
        task["latest_activity"] = activity[-1] if activity else None
        task["latest_action"] = activity[-1]["summary"] if activity else None
        task["last_activity_at"] = activity[-1]["at"] if activity else None
        task["observed_status"] = activity[-1].get("status") if activity else None
        commands = [c for c in result["commands"] if c["task_id"] == task["id"] and c.get("started_at") and command_matches_task(c, task)]
        current = commands[-1] if commands else {}
        task["attempt_started_at"] = current.get("started_at")
        task["attempt_finished_at"] = current.get("completed_at") if current.get("status") == "completed" else None
        try:
            started_at = task["attempt_started_at"] or next((e["at"] for e in activity if e["kind"] == "started"), None)
            ended_at = task["attempt_finished_at"] or (activity[-1]["at"] if activity and activity[-1]["kind"] in {"completed", "failed"} else None)
            started = datetime.fromisoformat(started_at) if started_at else None
            ended = datetime.fromisoformat(ended_at) if ended_at else now
            task["elapsed_seconds"] = max(0, int((ended-started).total_seconds())) if started else None
        except (ValueError, TypeError):
            task["elapsed_seconds"] = None
    observed = {}
    for event in events:
        observed[event["agent_id"]] = event
    agents = {agent["id"]: agent for agent in result["agents"]}
    for identifier, event in observed.items():
        age = (now-datetime.fromisoformat(event["at"])).total_seconds()
        status = event.get("status")
        if status in {"completed", "done", "success"} or event["kind"] == "completed":
            status = "idle"
        elif status in {"failed", "blocked", "attention"} or event["kind"] in {"failed", "verification_failed", "error"}:
            status = "blocked"
        elif status in {"queued", "waiting", "idle"} or event["kind"] == "waiting":
            status = "idle"
        else:
            status = "running"
        if identifier not in agents:
            agents[identifier] = {"id": identifier, "name": identifier, "provider": event["provider"], "role": event["role"],
                                  "task_id": event.get("task_id"), "last_seen": event["at"], "status": status if age <= 90 else "disconnected",
                                  "source": event.get("source", "host_report"), "observed": True}
        agent = agents[identifier]
        current_task = next((task for task in result["tasks"] if task["id"] == event.get("task_id")), None)
        if current_task is not None:
            old_event = not activity_matches_task(event, current_task, result["commands"])
            current_command = any(c.get("agent_id") == identifier and c["task_id"] == current_task["id"]
                                  and c["status"] in {"claimed", "running"} and command_matches_task(c, current_task)
                                  for c in result["commands"])
            agent["superseded"] = old_event and not current_command
            if old_event and current_command:
                # A fresh host claim/start is stronger evidence than its old log.
                continue
        agent.update(current_action=event["summary"], last_activity_at=event["at"], latest_activity=event)
        for key in ("parent_id", "pid", "peer_role"):
            if key in event:
                agent[key] = event[key]
    result["agents"] = list(agents.values())
    result["host"]["connected"] = result["host"]["connected"] or any(a.get("observed") and a["status"] != "disconnected" for a in result["agents"])
    result["capabilities"]["host_connected"] = result["host"]["connected"]


def review_event(folder, payload):
    """Record an independent, version-bound AI review, never human approval."""
    fields = {"expected_revision", "review_id", "task_id", "agent_id", "provider", "role", "outcome", "summary", "evidence_refs", "target_hashes", "findings"}
    if not isinstance(payload, dict) or set(payload)-fields or not (fields-{"findings"}).issubset(payload):
        raise ValueError("AI 검토는 실제 작업·검토자·버전·근거가 필요합니다.")
    if not isinstance(payload["review_id"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,150}", payload["review_id"]):
        raise ValueError("검토 ID가 잘못되었습니다.")
    if payload["provider"] not in {"codex", "claude"} or payload["outcome"] not in {"passed", "changes_requested"}:
        raise ValueError("AI 검토 결과가 잘못되었습니다.")
    from tools import role_agents
    role = role_agents.resolve(payload["role"])["id"]
    if role not in {"qa", "reviewer", "security", "architect"}:
        raise ValueError("독립 검토 역할이 필요합니다.")
    if not isinstance(payload["agent_id"], str) or not re.fullmatch(r"[A-Za-z0-9_./:-]{1,200}", payload["agent_id"]):
        raise ValueError("실제 검토 작업자 ID가 필요합니다.")
    summary = public_summary(payload["summary"])
    if not isinstance(payload.get("findings", []), list) or len(payload.get("findings", [])) > 50:
        raise ValueError("검토 지적은 50개 이하 목록이어야 합니다.")
    findings = [public_summary(v) for v in payload.get("findings", [])]
    if payload["outcome"] == "changes_requested" and not findings:
        raise ValueError("수정 검토는 구체적인 지적이 필요합니다.")
    root, data, tables = lifecycle.bundle(folder)
    with lock(root):
        store = read(root)
        signature = digest(json_bytes({k: v for k, v in payload.items() if k != "expected_revision"}))
        prior = next((r for r in store["reviews"] if r["id"] == payload["review_id"]), None)
        if prior:
            if prior["signature"] != signature:
                raise Conflict("같은 검토 ID를 다른 검토에 사용할 수 없습니다.")
            return {"review": prior, "duplicate": True, "revision": revision(root)}
        require_revision(root, payload)
        tasks = task_records(root, data, tables, store)
        task = tasks.get(payload["task_id"])
        if not task or role_matches(task.get("role", ""), role):
            raise ValueError("산출물 작성자와 독립된 역할의 실제 작업 검토가 필요합니다.")
        hashes = payload["target_hashes"]
        if not isinstance(hashes, dict) or not hashes or any(file_hash(root, p) != h or h is None for p, h in hashes.items()):
            raise Conflict("검토 대상 버전이 변경되거나 없습니다.")
        targets = set(task.get("evidence_refs", []) + task.get("expected_outputs", []))
        if not set(hashes).issubset(targets):
            raise ValueError("해당 작업의 실제 산출물만 검토합니다.")
        refs = payload["evidence_refs"]
        if not isinstance(refs, list) or not refs:
            raise ValueError("실제 검토 보고서 근거가 필요합니다.")
        evidence = runs.hashes(root, refs)
        if any(q.get("agent_id") == payload["agent_id"] and q["task_id"] == task["id"] and q["status"] == "completed" for q in store["queue"]):
            raise ValueError("같은 작성 작업자가 스스로 독립 검토할 수 없습니다.")
        record = {"id": payload["review_id"], "task_id": task["id"], "agent_id": payload["agent_id"], "provider": payload["provider"],
                  "role": role, "outcome": payload["outcome"], "authority": "host-reported-ai-review", "at": starter.now(), "summary": summary,
                  "findings": findings, "target_hashes": hashes, "evidence_hashes": evidence, "signature": signature}
        store["reviews"].append(record)
        receipt = "reports/runner/reviews/" + record["id"] + ".json"
        save(root, store, "workspace-ai-review", record["agent_id"], {"review_id": record["id"], "task_id": task["id"], "outcome": record["outcome"]}, {receipt: json_bytes(record)})
    activity_event(root, {"event_id": "review:" + record["id"], "task_id": task["id"], "agent_id": record["agent_id"], "provider": record["provider"],
                              "role": role, "kind": "review", "summary": summary, "status": "completed", "paths": list(hashes), "source": "host_report"})
    return {"review": record, "duplicate": False, "revision": revision(root), "evidence_ref": receipt}


def schedule_repair(folder, payload):
    """A real failing independent check can request bounded producer repair.

    Retains the failed command, original write scope and immutable QA checks.
    Neither this handoff nor its success creates a human approval record.
    """
    fields = {"expected_revision", "failed_command_id", "feedback_refs", "check_hashes", "summary"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ValueError("자동 수정 연결 필드가 잘못되었습니다.")
    summary = public_summary(payload["summary"])
    root, data, tables = lifecycle.bundle(folder)
    with lock(root):
        root, data, tables = lifecycle.bundle(root)
        store = read(root)
        command = next((c for c in store["queue"] if c["id"] == payload["failed_command_id"]), None)
        if not command or command["status"] != "failed":
            raise ValueError("실제로 실패한 독립 검증 작업이 필요합니다.")
        old = next((r for r in store.get("repair_requests", []) if r["failed_command_id"] == command["id"]), None)
        if old:
            return {**old, "unchanged": True, "revision": revision(root)}
        require_revision(root, payload)
        tasks = task_records(root, data, tables, store)
        review_task = tasks[command["task_id"]]
        from tools import role_agents
        reviewer_role = role_agents.resolve(review_task["role"])["id"]
        if reviewer_role not in {"qa", "reviewer", "security"}:
            raise ValueError("독립 QA·리뷰·보안 검증의 실패만 자동 수정으로 전달합니다.")
        review_meta = store["tasks"].setdefault(review_task["id"], {})
        if review_meta.get("repair_attempts", 0) >= 2:
            raise ValueError("같은 검토의 자동 수정 2회 한도에 도달했습니다. 원인 판단이 필요합니다.")
        prefix = "reports/runner/" + command["id"] + "/"
        verification_ref = prefix + "verification.txt"
        refs = payload["feedback_refs"]
        if not isinstance(refs, list) or verification_ref not in refs or not refs or len(refs) > 50:
            raise ValueError("감독자의 실제 실패 검증 로그가 필요합니다.")
        for ref in refs:
            if not isinstance(ref, str) or not ref.startswith(prefix):
                raise ValueError("해당 실패 시도의 감독 근거만 참조할 수 있습니다.")
            valid_artifact(root, ref)
        evidence = runs.hashes(root, refs)
        log = confined(root, verification_ref).read_text(encoding="utf-8", errors="replace")
        if not re.search(r"(?m)^EXIT:\s*(?!0\s*$)-?\d+\s*$", log):
            raise ValueError("실제 독립 검증의 nonzero 종료 기록이 없습니다. 인증·시간초과를 자동 재시도하지 않습니다.")
        hashes = payload["check_hashes"]
        if not isinstance(hashes, dict) or not hashes or len(hashes) > 100:
            raise ValueError("감독자가 보존한 독립 QA 반례 해시가 필요합니다.")
        checks = []
        for source, checksum in hashes.items():
            if not isinstance(source, str) or not source.startswith(prefix+"qa-feedback/"):
                raise ValueError("QA 반례가 해당 감독 기록 밖에 있습니다.")
            target = source[len(prefix+"qa-feedback/"):]
            if not target.startswith(("tests/", "checks/")) or Path(target).suffix != ".py":
                raise ValueError("독립 반례는 실제 Python 테스트 경로여야 합니다.")
            execution_path(root, target)
            if file_hash(root, source) != checksum or not isinstance(checksum, str):
                raise Conflict("독립 QA 반례가 변경되거나 삭제되었습니다.")
            checks.append({"source": source, "target": target, "sha256": checksum})
        candidates = [tasks[t] for t in review_task["depends_on"] if t in tasks and tasks[t].get("execution")
                      and role_agents.resolve(tasks[t]["role"])["id"] in {"backend", "frontend", "database", "architect", "devops"}]
        if len(candidates) != 1:
            raise ValueError("수정할 담당 개발 작업을 하나로 특정할 수 없습니다. 범위를 조율해야 합니다.")
        producer = candidates[0]
        if producer["status"] != "done":
            raise ValueError("검토한 선행 개발의 실제 완료 근거가 필요합니다.")
        if any(g.get("required", True) and g["task_id"] == producer["id"] for g in store["gates"]):
            raise ValueError("명시적으로 사람 검토가 필요한 산출물은 자동 수정으로 게이트를 우회하지 않습니다.")
        if any(c["task_id"] in {producer["id"], review_task["id"]} and c["status"] in {"claimed", "running"} for c in store["queue"]):
            raise Conflict("같은 범위에서 실제 작업자가 실행 중입니다.")
        request_id = "REPAIR-" + uuid.uuid4().hex
        policy = dict(producer["execution"])
        merged_commands = list(policy.get("verification_commands", []))
        for argv in (review_task.get("execution") or {}).get("verification_commands", []):
            if argv not in merged_commands:
                merged_commands.append(argv)
        policy["verification_commands"] = merged_commands
        policy["instruction"] += "\n\n독립 QA 수정 요청:\n" + summary + "\n실패 검증 로그: " + "; ".join(refs) + "\n감독자가 보존한 QA 반례를 읽고 원인을 수정하세요. 기존 허용 경로만 수정하고 QA 반례는 변경하지 마세요."
        validate_execution(root, policy)
        for task in (producer, review_task):
            meta = store["tasks"].setdefault(task["id"], {})
            previous_ref = archive_step(store, task, request_id)
            if previous_ref:
                meta["replan_run"] = {"previous_step_ref": previous_ref, "inputs": list(task.get("inputs", [])),
                    "expected_outputs": list(task.get("expected_outputs", [])), "requirement_ids": list(task["requirement_ids"])}
                meta.pop("step_ref", None)
            meta.pop("artefact_revision", None)
            meta.pop("invalidated_by", None)
            meta.update(status="todo", generation=meta.get("generation", 1)+1, logical_snapshot=logical_snapshot(task),
                        depends_on=list(task["depends_on"]), provenance_inputs=list(task.get("inputs", [])),
                        requirement_ids=list(task["requirement_ids"]), replan_decision=request_id)
            for row in tables["tasks"]:
                if row["task_id"] == task["id"]:
                    row["status"] = "todo"
            for pending in store["queue"]:
                if pending["task_id"] == task["id"] and pending["status"] == "queued":
                    pending.update(status="superseded", superseded_by_repair=request_id)
        producer_meta = store["tasks"][producer["id"]]
        producer_meta["execution"] = policy
        producer_meta["repair_checks"] = checks
        review_meta["repair_attempts"] = review_meta.get("repair_attempts", 0)+1
        record = {"id": request_id, "failed_command_id": command["id"], "repair_task_id": producer["id"], "review_task_id": review_task["id"],
                  "producer_role": producer["role"], "attempt": review_meta["repair_attempts"], "at": starter.now(),
                  "summary": summary, "feedback_hashes": evidence, "check_hashes": hashes, "authority": "supervisor-verified-qa-failure"}
        store.setdefault("repair_requests", []).append(record)
        store["feedback"].append({"id": request_id, "source": review_task["id"], "target": producer["id"], "type": "ai_revision", "instruction": summary, "at": record["at"]})
        store["reviews"].append({"id": request_id, "task_id": producer["id"], "agent_id": command["agent_id"], "provider": store["agents"].get(command["agent_id"], {}).get("provider"),
                                 "role": reviewer_role, "outcome": "changes_requested", "authority": "supervisor-verified-qa-failure", "at": record["at"],
                                 "summary": summary, "findings": [summary], "target_hashes": {ref: file_hash(root, ref) for ref in producer.get("expected_outputs", [])},
                                 "evidence_hashes": {**evidence, **hashes}})
        enqueue_ready(root, data, tables, store)
        extra = {"registers/tasks.csv": lifecycle.table_bytes(root, "tasks", tables["tasks"]), "reports/runner/repairs/"+request_id+".json": json_bytes(record)}
        save(root, store, "workspace-ai-repair", command["agent_id"], {"repair_id": request_id, "producer_task": producer["id"], "review_task": review_task["id"], "attempt": record["attempt"]}, extra)
        return {**record, "unchanged": False, "revision": revision(root)}

def phase(stage):
    return next((p["id"] for p in PHASES if stage in p["stages"]), "01")


def _meaningful_title(value):
    return (isinstance(value, str) and bool(value.strip()) and
            value.strip().casefold().rstrip(".!。") not in
            {"없음", "완료", "종료", "대기", "none", "n/a", "na", "null", "done", "-", "해당 없음"})


def legacy_task_titles(root):
    """Recover earliest pre-completion names from managed before-images, read only.

    The historical next_action is usable only before completion. A closing note
    is never treated as a title. Customer files are not migrated on GET.
    """
    import csv
    import io
    memo = _TITLE_SCOPE.get()
    cache_key = str(root)
    if memo is not None and cache_key in memo:
        return memo[cache_key]
    titles = {}
    for event in lifecycle.events(root):
        path = confined(root, "state/history/" + event["revision"] + ".json")
        if not path.is_file():
            continue
        try:
            history = json.loads(path.read_text(encoding="utf-8"))
            for entry in history.get("entries", []):
                if entry.get("path") != "registers/tasks.csv" or not entry.get("before"):
                    continue
                raw = base64.b64decode(entry["before"], validate=True).decode("utf-8-sig")
                for row in csv.DictReader(io.StringIO(raw)):
                    title = row.get("next_action", "")
                    if row.get("status") != "done" and _meaningful_title(title):
                        titles.setdefault(row["task_id"], redact_text(title.strip()))
        except (ValueError, KeyError, UnicodeError):
            # Missing/legacy history cannot manufacture a task name.
            continue
    if memo is not None:
        memo[cache_key] = titles
    return titles


def preserve_task_titles(root, tables, store, *, historical=None):
    """Populate only absent titles; caller commits these with the task mutation.

    Runtime hooks may use this helper inside their existing storage lock and
    transaction. Explicit workspace titles are always authoritative.
    """
    if all(_meaningful_title(store["tasks"].get(row["task_id"], {}).get("title")) for row in tables["tasks"]):
        return False
    historical = legacy_task_titles(root) if historical is None else historical
    run_store = runs.read(root)
    changed = False
    for row in tables["tasks"]:
        identifier = row["task_id"]
        meta = store["tasks"].setdefault(identifier, {})
        if _meaningful_title(meta.get("title")):
            continue
        ref = meta.get("step_ref") or {}
        bound = next((step["title"] for run in run_store["runs"] if run["id"] == ref.get("run_id")
                      for step in run["steps"] if step["id"] == ref.get("step_id")), None)
        title = bound or historical.get(identifier)
        if not title and row.get("status") != "done" and _meaningful_title(row.get("next_action")):
            title = row["next_action"].strip()
        named = title is not None
        meta["title"] = redact_text(title or ((row.get("role") or "프로젝트") + " 작업 · " + identifier))
        meta["title_source"] = "run_step" if bound else "history" if identifier in historical else "task_creation" if named else "fallback"
        changed = True
    return changed


def _task_scope(root, data, store, tasks):
    """An explicit focus or latest declared run; validity never chooses scope."""
    selected = store.get("current_scope")
    if selected:
        return {**selected, "task_ids": [i for i in selected["task_ids"] if i in tasks],
                "source": "explicit", "label": "선택한 실행 범위"}
    run_store = runs.read(root)
    if run_store["runs"]:
        latest = run_store["runs"][-1]
        declared = next((scope for scope in reversed(store["execution_scopes"]) if scope.get("run_id") == latest["id"]), {})
        return {"id": declared.get("id", latest["id"]), "run_id": latest["id"], "title": declared.get("title", latest["title"]), "label": "최근 실행", "source": "latest_run",
                "task_ids": [t["id"] for t in tasks.values() if t.get("run_id") == latest["id"]]}
    return {"id": data["project_id"], "title": data["name"], "label": "전체 프로젝트", "source": "project",
            "task_ids": list(tasks)}


def task_records(root, data, tables, store, *, run_store=None, check_context=True):
    result = {}
    historical_titles = None
    for row in tables["tasks"]:
        meta = store["tasks"].get(row["task_id"], {})
        stage = meta.get("stage", data["current_stage"])
        title = meta.get("title")
        if not _meaningful_title(title):
            if historical_titles is None:
                historical_titles = legacy_task_titles(root)
            title = historical_titles.get(row["task_id"])
            if not title and row["status"] != "done" and _meaningful_title(row["next_action"]):
                title = row["next_action"]
            title = title or ((row.get("role") or "프로젝트") + " 작업 · " + row["task_id"])
        result[row["task_id"]] = {"id": row["task_id"], "title": title,
            "stage": stage, "phase": phase(stage), "stage_unassigned": "stage" not in meta, "role": meta.get("role", row["role"]), "owner": row["owner"],
            "status": {"done": "done", "doing": "running", "blocked": "blocked"}.get(row["status"], "todo"),
            "requirement_ids": list(dict.fromkeys(starter.split_refs(row["requirement_ids"]) + meta.get("requirement_ids", []))), "depends_on": meta.get("depends_on", []),
            "artefact_ids": [], "start": meta.get("start"), "end": meta.get("end") or row["due_date"] or None,
            "parent_id": meta.get("parent_id"), "progress": 100 if row["status"] == "done" else None,
            "evidence_refs": starter.split_refs(row["evidence_refs"]), "step_ref": meta.get("step_ref"),
            "execution": meta.get("execution"), "repair_checks": meta.get("repair_checks", []), "expected_outputs": meta.get("execution", {}).get("expected_outputs", []),
            "inputs": meta.get("provenance_inputs", []), "generation": meta.get("generation", 1),
            "run_generation_pending": bool(meta.get("replan_run"))}
        # A direct TASK has no run.valid_step receipt check. Preserve legacy
        # CSV semantics, but validate every actual host completion we possess.
        if row["status"] == "done" and not meta.get("step_ref"):
            receipts = [c for c in store["queue"] if c["task_id"] == row["task_id"]
                        and c["status"] == "completed" and c.get("receipt_hashes")]
            if receipts:
                latest = max(enumerate(receipts), key=lambda item: (item[1].get("completed_at", ""), item[0]))[1]
                changed = []
                for relative, expected in latest["receipt_hashes"].items():
                    try:
                        current = file_hash(root, relative)
                    except (ValueError, OSError):
                        current = None
                    if current != expected:
                        changed.append(relative)
                record = result[row["task_id"]]
                record["receipt_valid"] = not changed
                if changed:
                    record.update(status="stale", progress=None,
                                  blocked_reason="완료 근거가 변경되거나 삭제되었습니다: " + "; ".join(changed[:5]))
    active_runs = runs.read(root) if run_store is None else run_store
    for run in active_runs["runs"]:
        mapping = {s["id"]: next((k for k, v in store["tasks"].items() if v.get("step_ref") == {"run_id": run["id"], "step_id": s["id"]}),
                   store["superseded_steps"].get(run["id"] + "/" + s["id"], {}).get("task_id", f"{data['project_id']}/{run['id']}/{s['id']}")) for s in run["steps"]}
        for step in run["steps"]:
            if run["id"] + "/" + step["id"] in store["superseded_steps"]:
                continue
            identifier = mapping[step["id"]]
            meta = store["tasks"].get(identifier, {})
            stage = meta.get("stage", data["current_stage"])
            valid = runs.valid_step(root, run, step)
            status = {"succeeded": "done", "skipped": "skipped", "running": "running", "failed": "failed",
                      "blocked": "blocked", "awaiting_decision": "awaiting_approval"}.get(step["status"], "todo")
            if step["status"] in {"succeeded", "skipped"} and not valid:
                status = "stale"
            previous = result.get(identifier, {})
            result[identifier] = {**previous, "id": identifier, "title": meta["title"] if _meaningful_title(meta.get("title")) else step["title"],
                "stage": stage, "phase": phase(stage), "stage_unassigned": "stage" not in meta,
                "role": previous.get("role", meta.get("role", "")),
                "owner": previous.get("owner", ""), "status": status,
                "requirement_ids": list(dict.fromkeys(run["requirement_ids"] + previous.get("requirement_ids", []))),
                "depends_on": list(dict.fromkeys([mapping[s] for s in step["depends_on"]] + meta.get("depends_on", []))),
                "artefact_ids": [], "start": meta.get("start"), "end": meta.get("end"), "parent_id": meta.get("parent_id"),
                "progress": 100 if valid and step["status"] == "succeeded" else None, "run_id": run["id"], "step_id": step["id"],
                "attempts": step["attempts"], "evidence_refs": list(step.get("receipt_hashes", {})),
                "inputs": step["inputs"], "execution": meta.get("execution"), "repair_checks": meta.get("repair_checks", []),
                "expected_outputs": list(dict.fromkeys(step.get("expected_outputs", []) + meta.get("execution", {}).get("expected_outputs", []))),
                "step_ref": {"run_id": run["id"], "step_id": step["id"]}, "generation": meta.get("generation", 1)}
    for identifier, meta in store["tasks"].items():
        if identifier not in result and meta.get("logical_snapshot"):
            result[identifier] = {**meta["logical_snapshot"], "id": identifier, "status": meta.get("status", "todo"),
                "run_id": None, "step_id": None, "step_ref": None, "execution": meta.get("execution"), "repair_checks": meta.get("repair_checks", []),
                "generation": meta.get("generation", 1), "run_generation_pending": bool(meta.get("replan_run")),
                "progress": 100 if meta.get("status") == "done" else None}
    for identifier, meta in store["tasks"].items():
        if not meta.get("rework"):
            continue
        result[identifier] = {"id": identifier, "title": meta["title"], "stage": meta["stage"], "phase": phase(meta["stage"]),
            "role": meta.get("role", ""), "owner": "", "status": meta.get("status", "todo"),
            "requirement_ids": meta.get("requirement_ids", []), "depends_on": [],
            "artefact_ids": ["file:" + p for p in meta.get("outputs", [])], "start": meta.get("start"), "end": meta.get("end"),
            "progress": 100 if meta.get("status") == "done" else None, "rework": True, "gate_id": meta["gate_id"],
            "evidence_refs": meta.get("evidence_refs", []), "inputs": [], "expected_outputs": meta.get("outputs", []),
            "feedback": meta.get("feedback", ""), "execution": meta.get("execution")}
    for identifier, task in result.items():
        meta = store["tasks"].get(identifier, {})
        provenance = meta.get("artefact_revision")
        if provenance:
            receipts = provenance.get("receipt_hashes", {})
            valid = bool(receipts) and all(file_hash(root, ref) == checksum for ref, checksum in receipts.items())
            task.update(status="done" if valid else "stale", progress=100 if valid else None,
                        receipt_valid=valid, artefact_revision=provenance)
            task["evidence_refs"] = list(receipts)
        if meta.get("invalidated_by") and task["status"] in {"done", "stale"}:
            task.update(status="stale", progress=None, blocked_reason="승인 기준 수정에 따라 새 실행이 필요합니다.", receipt_valid=False)
        if check_context and task["status"] == "done":
            completed = [c for c in store["queue"] if c["task_id"] == identifier and c["status"] == "completed"
                         and c.get("generation", 1) == meta.get("generation", 1)]
            if completed and not provenance and not completion_context_valid(root, store, completed[-1], result):
                task.update(status="stale", progress=None, blocked_reason="완료 당시 승인 기준이 변경되어 다시 실행해야 합니다.", receipt_valid=False)
    return result


def ancestors(tasks, identifier):
    found, pending = set(), list(tasks.get(identifier, {}).get("depends_on", []))
    while pending:
        node = pending.pop()
        if node not in found:
            found.add(node)
            pending.extend(tasks.get(node, {}).get("depends_on", []))
    return found


def blockers(root, store, tasks, identifier):
    parents = ancestors(tasks, identifier)
    result = [f"선행 작업 {p} 미완료" for p in parents if tasks.get(p, {}).get("status") not in {"done", "skipped", "awaiting_approval"}]
    result += [f"승인 게이트 {g['id']} 대기" for g in store["gates"] if g["task_id"] in parents and g.get("required", True) and gate_status(root, store, g) != "approved"]
    for gate in store["gates"]:
        if gate["task_id"] in parents and not gate.get("required", True) and any(m.get("rework") and m.get("gate_id") == gate["id"] and m.get("status") != "done" for m in store["tasks"].values()):
            result.append("선행 수정 작업 진행 중: " + gate["task_id"])
    return result


def assert_step_allowed(root, run_id, step_id):
    """Run-engine guard; a gated ancestor cannot be bypassed by run-result."""
    root, data, tables = lifecycle.bundle(root)
    store = read(root)
    if not store["gates"]:
        return
    tasks = task_records(root, data, tables, store)
    identifier = next((t["id"] for t in tasks.values() if t.get("run_id") == run_id and t.get("step_id") == step_id), None)
    if identifier is None:
        raise ValueError("실행 단계 연결을 찾을 수 없습니다.")
    issues = blockers(root, store, tasks, identifier)
    if issues:
        raise Conflict("; ".join(issues))


def valid_artifact(root, relative):
    path = confined(root, relative)
    parts = path.relative_to(root).parts
    if is_sensitive_path(relative) or not parts or parts[0] == "state" or set(parts).intersection({".git", ".codex", ".claude", ".aws", ".ssh", ".gnupg", ".azure", ".netrc", ".npmrc", ".pypirc"}) or any(p.startswith(".env") for p in parts):
        raise ValueError("제어 기록·인증 자료는 관제 산출물로 열 수 없습니다.")
    return path



def validate_verification_summary(value):
    allowed = {"mode", "status", "checks", "manifest_sha", "source"}
    if not isinstance(value, dict) or set(value) - allowed or value.get("mode") not in {"execute", "artifact_only"} or value.get("status") not in {"passed", "failed", "not_run", "artifact_only", "unknown"}:
        raise ValueError("검증 요약은 실제 방식·상태·명령 수가 필요합니다.")
    checks = value.get("checks")
    if type(checks) is not int or checks < 0 or checks > 30:
        raise ValueError("검증한 명령 수는 0~30 정수여야 합니다.")
    if value.get("source") not in {"supervisor", "host-reported"}:
        raise ValueError("검증 요약의 실제 수집원이 필요합니다.")
    if value.get("manifest_sha") is not None and not re.fullmatch(r"[0-9a-f]{64}", str(value["manifest_sha"])):
        raise ValueError("검증 대상 manifest SHA-256이 잘못되었습니다.")
    if value["status"] == "passed" and (value["mode"] != "execute" or checks == 0 or not value.get("manifest_sha")):
        raise ValueError("검증 통과에는 실행 명령과 검증 대상 manifest가 필요합니다.")
    if value["mode"] == "artifact_only" and (checks or value["status"] not in {"artifact_only", "failed", "unknown"}):
        raise ValueError("문서 기본 검수를 실행 검증 통과로 보고할 수 없습니다.")
    return dict(value)


def _usage_summary(records, commands, activities=()):
    """Count every observed attempt; a numeric known subtotal is not a total."""
    grouped = {}
    for index, record in enumerate(records):
        command = record.get("command_id") or "unbound"
        attempt = record.get("attempt_id") or record.get("provider_session_id") or record.get("at") or str(index)
        grouped[(command, attempt)] = record
    records = list(grouped.values())
    total = len(records)
    for command in commands:
        matched = [r for r in records if r.get("command_id") == command["id"]]
        status = command.get("status")
        if status in {"completed", "failed", "cancelled"} and not matched:
            total += 1
        elif status == "running":
            current = command.get("attempt_id")
            if not current or not any(r.get("attempt_id") == current for r in matched):
                total += 1
    # Native host observations without a queue receipt are still unmetered
    # executions. Count only explicit lifecycle events, never tool messages.
    represented = {(c.get("agent_id"), c.get("task_id")) for c in commands}
    native = {}
    native_total = 0
    for event in activities:
        identity = (event.get("agent_id"), event.get("task_id"))
        if event.get("command_id") or identity in represented or str(event.get("agent_id", "")).startswith("runner:"):
            continue
        kind = event.get("kind")
        if kind in {"started", "agent_started"}:
            native_total += 1
            native[identity] = True
        elif kind in {"completed", "failed", "agent_completed", "agent_failed"}:
            if not native.get(identity):
                native_total += 1
            native[identity] = False
    total += native_total
    def known(field):
        return [r[field] for r in records if type(r.get(field)) in (int, float) and math.isfinite(r[field]) and r[field] >= 0]
    tokens, costs = known("tokens"), known("cost_usd")
    partial_records = sum(r.get("coverage") in {"partial", "unknown"} for r in records)
    covered = sum(r.get("tokens") is not None and r.get("cost_usd") is not None and r.get("coverage") not in {"partial", "unknown"} for r in records)
    complete = total > 0 and covered == total
    observed = sum(r.get("tokens") is not None or r.get("cost_usd") is not None for r in records)
    return {"tokens": sum(tokens) if total > 0 and len(tokens) == total and not partial_records else None,
            "cost_usd": sum(costs) if total > 0 and len(costs) == total and not partial_records else None,
            "known_tokens": sum(tokens) if tokens else None, "known_cost_usd": sum(costs) if costs else None,
            "coverage": "known" if complete else ("partial" if observed else "unknown"),
            "observed_attempts": observed, "total_attempts": total, "unreported_attempts": max(0, total-covered),
            "tokens_unreported_attempts": max(0, total-len(tokens)), "cost_unreported_attempts": max(0,total-len(costs)),
            "source": "host-reported" if records else None, "records": records,
            "scope": "recorded-attempts", "native_unreported_attempts": native_total}


def command_matches_task(command, task):
    """Old receipts remain history; only the same execution may affect status."""
    if command.get("generation", 1) != task.get("generation", 1):
        return False
    if "step_ref" in command:
        return command["step_ref"] == task.get("step_ref")
    if command.get("run_id") or command.get("step_id"):
        return {"run_id": command.get("run_id"), "step_id": command.get("step_id")} == task.get("step_ref")
    return True


def activity_matches_task(event, task, commands):
    if event.get("command_id"):
        command = next((c for c in commands if c["id"] == event["command_id"]), None)
        return bool(command and command["task_id"] == task["id"] and command_matches_task(command, task))
    if "generation" in event:
        return command_matches_task(event, task)
    # Unscoped legacy reports cannot prove which replay they describe.
    return task.get("generation", 1) == 1


def _summary_counts(tasks):
    values = list(tasks)
    counts = {"total": len(values), "completed": 0, "excluded": 0, "running": 0, "failed": 0,
              "blocked": 0, "pending": 0, "waiting": 0, "stale": 0}
    for task in values:
        status = task.get("status")
        if status in {"done", "succeeded"}: counts["completed"] += 1
        elif status == "skipped": counts["excluded"] += 1
        elif status in {"running", "doing", "verifying"}:
            if task.get("result_pending") or task.get("execution_state") in {"completed", "idle"}:
                counts["waiting"] += 1
                counts["pending"] += 1
            else:
                counts["running"] += 1
        elif status == "failed": counts["failed"] += 1
        elif status == "blocked": counts["blocked"] += 1
        elif status == "stale": counts["stale"] += 1
        else:
            counts["waiting"] += 1
            if status not in {"awaiting_approval", "awaiting_decision"}: counts["pending"] += 1
    counts["applicable"] = counts["total"]-counts["excluded"]
    counts["completion_percent"] = round(counts["completed"] / counts["applicable"] * 100) if counts["applicable"] else None
    return counts


def _verification_for_task(task, commands):
    if task.get("status") == "stale":
        return {"mode": (task.get("execution") or {}).get("verification_mode", "execute"), "status": "stale", "checks": 0, "source": None}
    latest = commands[-1] if commands else {}
    if latest.get("verification_summary"):
        return dict(latest["verification_summary"])
    return {"mode": (task.get("execution") or {}).get("verification_mode", "execute"),
            "status": "not_run" if not commands and task.get("status") in {"todo", "skipped"} else "unknown", "checks": 0, "source": None}


@consistent_read
@snapshot_hash_scope
def snapshot(folder):
    root, data, tables = lifecycle.bundle(folder)
    store = read(root)
    tasks = task_records(root, data, tables, store)
    artefacts, edges = {}, []

    def artifact(relative, task_id=None, requirement_id=None, title=None):
        path = valid_artifact(root, relative)
        identifier = "file:" + relative
        if identifier not in artefacts:
            artefacts[identifier] = {"id": identifier, "path": relative, "title": title or Path(relative).name,
                "exists": path.is_file(), "sha256": file_hash(root, relative), "task_ids": [], "requirement_ids": [],
                "size_bytes": path.stat().st_size if path.is_file() else None,
                "preview_kind": "pdf" if path.suffix.lower() == ".pdf" else ("image" if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ("text" if path.suffix.lower() in {".md", ".txt", ".py", ".ts", ".tsx", ".json", ".csv", ".sql"} else "none")),
                "download_allowed": path.is_file() and not is_sensitive_path(relative)}
        item = artefacts[identifier]
        if task_id and task_id not in item["task_ids"]:
            item["task_ids"].append(task_id)
        if requirement_id and requirement_id not in item["requirement_ids"]:
            item["requirement_ids"].append(requirement_id)
        return identifier

    def edge(source, target, kind, evidence):
        entry = {"id": f"{kind}:{source}:{target}", "source": source, "target": target, "type": kind, "evidence": evidence}
        if not any(e["id"] == entry["id"] for e in edges):
            edges.append(entry)

    for row in tables["deliverables"]:
        if row["path"]:
            aid = artifact(row["path"], title=row["title"])
            for req in starter.split_refs(row["requirement_ids"]):
                artifact(row["path"], requirement_id=req)
                edge(req, aid, "references", f"registers/deliverables.csv#{row['deliverable_id']}")
    manifest_path = confined(root, "inputs/manifest.json")
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for source in manifest.get("inputs", []):
            if source.get("path"):
                aid = artifact(source["path"], title=source.get("original_name"))
                for req in tables["requirements"]:
                    identifier = source.get("input_id", "")
                    id_ref = bool(identifier and re.search(r"(?<![A-Za-z0-9_-])" + re.escape(identifier) + r"(?![A-Za-z0-9_-])", req["source"]))
                    # A full confined path with an optional section is also an
                    # explicit source; a filename substring alone is ambiguous.
                    path_ref = any(ref.strip().partition("#")[0].strip() == source["path"]
                                   for ref in req["source"].split(";"))
                    if id_ref or path_ref:
                        edge(aid, req["requirement_id"], "references", f"registers/requirements.csv#{req['requirement_id']}/source")
    for row in tables["requirements"]:
        for field in ("design_refs", "implementation_refs", "test_refs"):
            for relative in starter.split_refs(row[field]):
                aid = artifact(relative, requirement_id=row["requirement_id"])
                edge(row["requirement_id"], aid, "references", f"registers/requirements.csv#{row['requirement_id']}/{field}")
    for task in tasks.values():
        for dep in task["depends_on"]:
            edge(dep, task["id"], "depends_on", "state/runs.json" if task.get("run_id") else STORE)
        for req in task["requirement_ids"]:
            edge(req, task["id"], "references", "registers/tasks.csv" if task["id"].startswith("TASK-") else "state/runs.json")
        for relative in set(task.get("evidence_refs", []) + task.get("expected_outputs", [])):
            aid = artifact(relative, task["id"])
            if aid not in task["artefact_ids"]:
                task["artefact_ids"].append(aid)
            edge(task["id"], aid, "produces" if relative in task.get("evidence_refs", []) else "references",
                 "state/runs.json" if task.get("run_id") else "registers/tasks.csv")
        for relative in task.get("inputs", []):
            if confined(root, relative).is_dir():
                continue
            edge(artifact(relative), task["id"], "references", "state/runs.json")
    gates = []
    for gate in store["gates"]:
        item = {**gate, "status": gate_status(root, store, gate), "target_hashes": {p: file_hash(root, p) for p in gate["paths"]},
            "context_hash": context_hash(root, store, gate), "required": gate.get("required", True),
            "artefact_ids": [artifact(p, gate["task_id"]) for p in gate["paths"]],
            "decisions": [d for d in store["decisions"] if d["gate_id"] == gate["id"]]}
        item["review_status"] = item["status"]
        if not item["required"]:
            item["status"] = "observed"
        if item["decisions"]:
            item["feedback"] = item["decisions"][-1].get("feedback", "")
        gates.append(item)
        if item["status"] == "approved" and item["decisions"]:
            decision = item["decisions"][-1]
            evidence_path = f"reports/workspace-decisions/{decision['id']}.json"
            if file_hash(root, evidence_path) == digest(json_bytes(decision)):
                review_id = artifact(evidence_path, title="승인 근거 · " + gate["id"])
                for relative in gate["paths"]:
                    edge(review_id, artifact(relative), "validates", evidence_path + "#" + relative)
        task = tasks.get(gate["task_id"])
        if task:
            task["artefact_ids"] = list(dict.fromkeys(task["artefact_ids"] + item["artefact_ids"]))
            if item["required"] and item["status"] != "approved":
                task["status"] = "awaiting_approval" if item["status"] == "pending" else ("stale" if item["status"] == "stale" else "blocked")
                task["blocked_reason"] = f"{gate['id']}: {item['status']}"
    raw_tasks = task_records(root, data, tables, store)
    for task in tasks.values():
        issues = blockers(root, store, raw_tasks, task["id"])
        task["ready"] = task["status"] == "todo" and not issues
        if issues and task["status"] in {"todo", "running", "done"}:
            task["status"], task["blocked_reason"], task["ready"] = "blocked", "; ".join(issues), False
        commands = [c for c in store["queue"] if c["task_id"] == task["id"] and command_matches_task(c, task)]
        task["actual_start"] = next((c["started_at"] for c in commands if c.get("started_at")), None)
        task["actual_end"] = next((c["completed_at"] for c in reversed(commands) if c.get("completed_at")), None)
        if commands and commands[-1].get("status") == "failed" and task["status"] in {"todo", "blocked"}:
            task["status"] = "failed"
            task["blocked_reason"] = commands[-1].get("notes") or "실제 실행이 실패했습니다."
            task["ready"] = False
        task["verification"] = _verification_for_task(task, commands)
        task["verification_status"] = task["verification"]["status"]
    now = datetime.now(timezone.utc)
    agents = []
    for item in store["agents"].values():
        agent = {**item}
        age = (now - datetime.fromisoformat(agent["last_seen"])).total_seconds()
        agent["status"] = agent.get("status", "idle") if age <= 90 else "disconnected"
        agents.append(agent)
        if agent.get("task_id") in tasks:
            edge("agent:" + agent["id"], agent["task_id"], "references", "state/workspace.json#agents")
    runners = runner_records(root)
    runner_connected = any(item["connected"] for item in runners)
    for agent in agents:
        associated = [item for item in runners if agent["id"].startswith("runner:" + item["id"] + ":")]
        if associated and not any(item["connected"] for item in associated):
            agent["status"] = "disconnected"
        elif agent["status"] == "running" and associated:
            from tools.storage import process_alive
            if all(item.get("child_pid") and not process_alive(item["child_pid"]) for item in associated):
                agent["status"] = "verifying"
    usage = store["usage"]
    result = {"schema_version": 1, "mode": "local", "revision": revision(root), "capturedAt": starter.now(),
        "project": {"id": data["project_id"], "name": data["name"], "current_stage": data["current_stage"],
                    "version": data.get("starter_version", ""), "profile": data.get("workflow", {}).get("profile", "legacy")},
        "phases": [{**p, "active": any(s in workflow.stages(data) for s in p["stages"])} for p in PHASES],
        "tasks": list(tasks.values()), "artefacts": list(artefacts.values()), "agents": agents,
        "requirements": [{"id": r["requirement_id"], "title": r["description"], "status": r["status"], "source": r["source"]} for r in tables["requirements"]],
        "edges": edges, "gates": gates, "reviews": store["reviews"], "policy": store["policy"], "view_mode": store["policy"].get("view_mode", "observer"), "events": lifecycle.events(root)[-100:], "feedback": store["feedback"],
        "commands": store["queue"], "runners": runners, "archived_steps": list(store["superseded_steps"].values()),
        "usage": _usage_summary(usage, store["queue"], activity_read(root)["events"]), "summary_counts": _summary_counts(tasks.values()),
        "capabilities": {"approve": True, "revision": True, "host_connected": runner_connected or any(a["status"] != "disconnected" for a in agents)},
        "host": {"connected": runner_connected or any(a["status"] != "disconnected" for a in agents), "runner_connected": runner_connected, "queued": sum(q["status"] == "queued" for q in store["queue"])}}
    for task in result["tasks"]:
        task["ledger_status"] = task["status"]
    enrich_activity(root, result)
    from tools.native_observation import enrich_snapshot
    enrich_snapshot(root, result)
    result["usage"] = _usage_summary(usage, store["queue"], result["activities"])
    scope = _task_scope(root, data, store, {t["id"]: t for t in result["tasks"]})
    current_ids = set(scope["task_ids"])
    result["current_scope"] = scope
    run_store = runs.read(root)
    declared_runs = {item.get("run_id") for item in store["execution_scopes"]}
    result["execution_scopes"] = [
        {"id": run["id"], "run_id": run["id"], "title": run["title"], "source": "legacy_run",
         "task_ids": [t["id"] for t in result["tasks"] if t.get("run_id") == run["id"]]}
        for run in run_store["runs"] if run["id"] not in declared_runs] + store["execution_scopes"]
    result["history_runs"] = [{"id": run["id"], "title": run["title"], "scope_id": run.get("scope_id", run["id"]),
        "steps": [{"id": step["id"], "title": step["title"], "status": step["status"],
                   "valid": runs.valid_step(root, run, step), "inputs": step["inputs"],
                   "expected_outputs": step.get("expected_outputs", []), "receipt_hashes": step.get("receipt_hashes", {}),
                   "superseded": run["id"] + "/" + step["id"] in store["superseded_steps"]}
                  for step in run["steps"]]} for run in run_store["runs"]]
    result["history_tasks"] = [t for t in result["tasks"] if t["id"] not in current_ids]
    result["summary_counts"] = _summary_counts(t for t in result["tasks"] if t["id"] in current_ids)
    result["all_summary_counts"] = _summary_counts(result["tasks"])
    native_reported = any(not a["id"].startswith("runner:") for a in result["agents"])
    native_connected = any(not a["id"].startswith("runner:") and a.get("status") in {"running", "preparing", "starting"} and not a.get("superseded") and not a.get("stale") for a in result["agents"])
    execution_mode = ("mixed" if native_connected and runner_connected else
                      "cli" if runner_connected else "native" if native_connected else
                      "native" if native_reported else "cli" if runners else "unconnected")
    result["host"].update(data_connected=True, execution_mode=execution_mode, native_connected=native_connected,
        native_stale_seconds=120 if result.get("native_sessions") else 90, runner_stale_seconds=RUNNER_STALE_SECONDS,
        latest_activity_at=result["activities"][-1]["at"] if result["activities"] else None)
    result["captured_at"] = result["capturedAt"]
    result["relations"] = result["edges"]
    result["logs"] = [{**e, "message": e["action"] + " · " + json.dumps(e.get("details", {}), ensure_ascii=False)} for e in result["events"]]
    result["usage"]["cost"] = result["usage"]["cost_usd"]
    from tools.native_observation import public_snapshot
    return public_snapshot(result)


def configure(folder, payload, actor="agent"):
    if not isinstance(payload, dict) or set(payload) - {"expected_revision", "tasks", "gates", "capture_baselines", "policy", "current_scope"}:
        raise ValueError("관제 설정 필드가 잘못되었습니다.")
    root, data, tables = lifecycle.bundle(folder)
    with lock(root):
        root, data, tables = lifecycle.bundle(root)
        require_revision(root, payload)
        store = read(root)
        if "policy" in payload:
            policy = payload["policy"]
            if not isinstance(policy, dict) or set(policy) - {"view_mode", "auto_run", "limits"}:
                raise ValueError("관제 정책 필드가 잘못되었습니다.")
            if policy.get("view_mode", "observer") not in {"observer", "observe", "review"} or ("auto_run" in policy and type(policy["auto_run"]) is not bool):
                raise ValueError("관제 정책은 observe/review와 auto_run 불리언을 사용합니다.")
            if "limits" in policy:
                limits = policy["limits"]
                if not isinstance(limits, dict) or set(limits) - {"max_tokens", "max_cost_usd", "max_seconds", "unknown_usage"}:
                    raise ValueError("실행 한도 필드가 잘못되었습니다.")
                for name in ("max_tokens", "max_cost_usd", "max_seconds"):
                    if name in limits and (type(limits[name]) not in (int, float) or not math.isfinite(limits[name]) or limits[name] <= 0 or (name == "max_tokens" and type(limits[name]) != int)):
                        raise ValueError("실행 한도는 양수여야 하며 토큰은 정수여야 합니다.")
                if limits.get("unknown_usage", "stop") not in {"stop", "warn"}:
                    raise ValueError("미집계 사용량 정책은 stop 또는 warn입니다.")
            store["policy"].update(policy)
            if store["policy"].get("view_mode") == "observe":
                store["policy"]["view_mode"] = "observer"
        known = task_records(root, data, tables, store)
        for item in payload.get("tasks", []):
            allowed = {"id", "title", "stage", "depends_on", "start", "end", "parent_id", "step_ref", "role", "execution"}
            if not isinstance(item, dict) or set(item) - allowed or item.get("id") not in known:
                raise ValueError("기존 TASK 또는 실행 STEP의 메타데이터만 설정할 수 있습니다.")
            meta = {**store["tasks"].get(item["id"], {}), **{k: v for k, v in item.items() if k != "id"}}
            if any(field in meta and (not isinstance(meta[field], str) or not meta[field].strip()) for field in ("title", "role")) or ("title" in meta and not _meaningful_title(meta["title"])):
                raise ValueError("작업 제목·역할은 비어 있지 않은 문자열이어야 하며 완료 안내를 제목으로 쓰지 않습니다.")
            if meta.get("stage", data["current_stage"]) not in starter.STAGES:
                raise ValueError("잘못된 업무 단계입니다.")
            if not isinstance(meta.get("depends_on", []), list) or not set(meta.get("depends_on", [])).issubset(known):
                raise ValueError("실제 선행 작업 ID가 필요합니다.")
            if meta.get("parent_id") is not None and (meta["parent_id"] not in known or meta["parent_id"] == item["id"]):
                raise ValueError("실제 상위 작업 ID가 필요합니다.")
            for field in ("start", "end"):
                if meta.get(field) is not None:
                    try:
                        datetime.fromisoformat(meta[field])
                    except (TypeError, ValueError):
                        raise ValueError("일정은 ISO 날짜/시간이어야 합니다.") from None
            if meta.get("start") and meta.get("end") and meta["start"] > meta["end"]:
                raise ValueError("종료 일정이 시작보다 빠릅니다.")
            if "title" in meta:
                meta["title"] = redact_text(meta["title"])
            if "execution" in meta:
                meta["execution"] = validate_execution(root, meta["execution"])
            if "step_ref" in meta:
                ref = meta["step_ref"]
                if not isinstance(ref, dict) or set(ref) != {"run_id", "step_id"} or not any(r["id"] == ref["run_id"] and any(s["id"] == ref["step_id"] for s in r["steps"]) for r in runs.read(root)["runs"]):
                    raise ValueError("실제 RUN/STEP 연결이 필요합니다.")
            previous_ref = store["tasks"].get(item["id"], {}).get("step_ref")
            if previous_ref != meta.get("step_ref") and meta.get("step_ref") and any(
                    command["task_id"] == item["id"] and command["status"] in {"claimed", "running"} for command in store["queue"]):
                raise ValueError("수신·실행 중인 작업은 종료 결과를 기록한 뒤 새 STEP에 연결하세요.")
            if previous_ref != meta.get("step_ref") and meta.get("step_ref") and (previous_ref or any(
                    command["task_id"] == item["id"] for command in store["queue"])):
                meta["generation"] = meta.get("generation", 1) + 1
            if previous_ref and previous_ref != meta.get("step_ref"):
                store["superseded_steps"].setdefault(previous_ref["run_id"] + "/" + previous_ref["step_id"],
                    {**previous_ref, "task_id": item["id"], "at": starter.now(), "superseded_by": meta.get("step_ref")})
            store["tasks"][item["id"]] = meta
        refs = [json.dumps(v["step_ref"], sort_keys=True) for v in store["tasks"].values() if "step_ref" in v]
        if len(refs) != len(set(refs)):
            raise ValueError("한 실행 STEP을 여러 TASK에 중복 연결하지 않습니다.")
        known = task_records(root, data, tables, store)
        if "current_scope" in payload:
            scope = payload["current_scope"]
            if scope is None:
                store.pop("current_scope", None)
            else:
                if (not isinstance(scope, dict) or set(scope) - {"id", "title", "task_ids"}
                        or not isinstance(scope.get("title"), str) or not scope["title"].strip()
                        or not isinstance(scope.get("task_ids"), list) or not scope["task_ids"]
                        or any(not isinstance(i, str) for i in scope["task_ids"])
                        or len(set(scope["task_ids"])) != len(scope["task_ids"])
                        or not set(scope["task_ids"]).issubset(known)
                        or ("id" in scope and (not isinstance(scope["id"], str) or not scope["id"].strip()))):
                    raise ValueError("현재 실행 범위에는 제목과 실제 고유 작업 ID 목록이 필요합니다.")
                store["current_scope"] = {"id": scope.get("id", "selected"), "title": redact_text(scope["title"]),
                                          "task_ids": scope["task_ids"]}
        for task in known.values():
            if task["id"] in ancestors(known, task["id"]):
                raise ValueError("작업 의존 관계는 순환할 수 없습니다.")
            seen, parent = {task["id"]}, task.get("parent_id")
            while parent:
                if parent in seen:
                    raise ValueError("상위 작업 계층은 순환할 수 없습니다.")
                seen.add(parent)
                parent = known.get(parent, {}).get("parent_id")
        for item in payload.get("gates", []):
            if not isinstance(item, dict) or not {"id", "task_id", "paths"}.issubset(item) or set(item) - {"id", "task_id", "paths", "required"} or ("required" in item and type(item["required"]) is not bool) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", item["id"]):
                raise ValueError("게이트는 id/task_id/paths가 필요합니다.")
            if item["task_id"] not in known or not isinstance(item["paths"], list) or not item["paths"] or len(set(item["paths"])) != len(item["paths"]):
                raise ValueError("실제 작업과 고유 산출물 경로가 필요합니다.")
            for relative in item["paths"]:
                valid_artifact(root, relative)
                if file_hash(root, relative) is None:
                    raise ValueError("실제 생성된 산출물만 승인 대상으로 등록합니다.")
            existing = next((g for g in store["gates"] if g["id"] == item["id"]), None)
            if existing and any(existing[k] != item[k] for k in ("id", "task_id", "paths")):
                raise ValueError("기존 게이트 대상을 바꾸지 않습니다. 새 ID로 등록하세요.")
            if existing and "required" in item:
                existing["required"] = item["required"]
            if not existing:
                store["gates"].append(dict(item))
        for relative in payload.get("capture_baselines", []):
            valid_artifact(root, relative)
            raw = file_bytes(root, relative)
            if raw is None or relative in store["baselines"]:
                raise ValueError("기준 파일이 없거나 기존 기준 버전이 있습니다.")
            store["baselines"][relative] = {"sha256": digest(raw), "content": base64.b64encode(raw).decode(), "at": starter.now(), "actor": actor}
        if store["policy"].get("auto_run"):
            enqueue_ready(root, data, tables, store)
        save(root, store, "workspace-configure", actor, {"tasks": list(store["tasks"]), "gates": [g["id"] for g in store["gates"]]})
        return snapshot(root)



def completion_context_valid(root, store, command, tasks):
    for identifier, expected in command.get("approved_gate_hashes", {}).items():
        gate = next((g for g in store["gates"] if g["id"] == identifier), None)
        if gate is None or version_hashes(root, gate) != expected or context_hash(root, store, gate, tasks=tasks) != command.get("approved_gate_contexts", {}).get(identifier):
            return False
    return all(file_hash(root, ref) == checksum for ref, checksum in command.get("input_hashes", {}).items())


def logical_snapshot(task):
    fields = ("title", "stage", "phase", "stage_unassigned", "role", "owner", "requirement_ids", "depends_on", "artefact_ids",
              "start", "end", "parent_id", "inputs", "expected_outputs", "evidence_refs")
    return {key: task.get(key, [] if key.endswith("_ids") or key in {"depends_on", "inputs", "expected_outputs", "evidence_refs"} else None) for key in fields}


def archive_step(store, task, decision_id):
    ref = task.get("step_ref")
    if ref:
        key = ref["run_id"] + "/" + ref["step_id"]
        store["superseded_steps"].setdefault(key, {**ref, "task_id": task["id"], "decision_id": decision_id, "at": starter.now()})
    return ref


def promote_reworked_source(root, data, tables, store, gate, decision):
    candidates = [identifier for identifier, meta in store["tasks"].items()
                  if meta.get("rework") and meta.get("gate_id") == gate["id"] and meta.get("status") == "done"]
    if not candidates:
        return None
    rework_id = candidates[-1]
    completed = [c for c in store["queue"] if c["task_id"] == rework_id and c["status"] == "completed"]
    if not completed:
        return None
    receipt = completed[-1]
    hashes = receipt.get("receipt_hashes", {})
    if not set(gate["paths"]).issubset(hashes) or any(file_hash(root, ref) != checksum for ref, checksum in hashes.items()):
        raise Conflict("재제출한 수정 산출물의 완료 근거가 변경됐습니다. 다시 검증하세요.")
    tasks = task_records(root, data, tables, store)
    source = tasks[gate["task_id"]]
    meta = store["tasks"].setdefault(source["id"], {})
    if meta.get("artefact_revision", {}).get("command_id") == receipt["id"]:
        return None
    # Preserve unchanged outputs of a multi-file source run; a single gate must
    # never silently certify other modified outputs.
    combined = {}
    if source.get("run_id"):
        original = next(s for r in runs.read(root)["runs"] if r["id"] == source["run_id"] for s in r["steps"] if s["id"] == source["step_id"])
        combined.update(original.get("receipt_hashes", {}))
    else:
        prior = [c for c in store["queue"] if c["task_id"] == source["id"] and c["status"] == "completed"]
        if prior:
            combined.update(prior[-1].get("receipt_hashes", {}))
    combined.update(hashes)
    if any(file_hash(root, ref) != checksum for ref, checksum in combined.items()):
        raise Conflict("원본 작업의 다른 출력도 변경됐습니다. 해당 산출물을 먼저 검토하세요.")
    previous_ref = archive_step(store, source, decision["id"])
    meta.pop("step_ref", None)
    meta.update(status="done", logical_snapshot=logical_snapshot(source), provenance_inputs=source.get("inputs", []),
                depends_on=source.get("depends_on", []), requirement_ids=source["requirement_ids"],
                generation=meta.get("generation", 1) + 1,
                artefact_revision={"source_kind": "completed-rework", "task_id": rework_id, "command_id": receipt["id"],
                    "decision_id": decision["id"], "previous_step_ref": previous_ref, "receipt_hashes": combined})
    for row in tables["tasks"]:
        if row["task_id"] == source["id"]:
            row.update(status="done", evidence_refs=";".join(combined))
    return source["id"]


def prepare_reapproved_work(root, data, tables, store, gate, decision):
    tasks = task_records(root, data, tables, store)
    prior_approvals = [d for d in store["decisions"] if d["gate_id"] == gate["id"] and d["decision"] == "approve" and d["id"] != decision["id"]]
    changed_approval = bool(prior_approvals and (prior_approvals[-1]["target_hashes"] != decision["target_hashes"] or prior_approvals[-1].get("context_hash") != decision["context_hash"]))
    authorized_revision = store["tasks"].get(gate["task_id"], {}).get("artefact_revision", {}).get("decision_id") == decision["id"]
    affected = []
    for task in list(tasks.values()):
        if task.get("rework") or gate["task_id"] not in ancestors(tasks, task["id"]):
            continue
        commands = [c for c in store["queue"] if c["task_id"] == task["id"]]
        latest = commands[-1] if commands else None
        if latest and latest["status"] in {"running", "claimed"}:
            continue
        if latest and latest["status"] == "failed" and not authorized_revision:
            continue  # A changed human revision is an explicit new work instruction.
        meta = store["tasks"].setdefault(task["id"], {})
        policy = task.get("execution")
        if not policy:
            continue
        needs_new_run = False
        if task.get("run_id"):
            run = next(r for r in runs.read(root)["runs"] if r["id"] == task["run_id"])
            step = next(s for s in run["steps"] if s["id"] == task["step_id"])
            if step["status"] == "running" or (step["status"] in {"failed", "blocked"} and not authorized_revision):
                continue
            needs_new_run = any(run["id"] + "/" + parent in store["superseded_steps"] for parent in step["depends_on"])
        invalidated = bool(meta.get("invalidated_by") or task["status"] == "stale" or (changed_approval and task["status"] == "done")
                           or (authorized_revision and latest and latest["status"] == "failed"))
        if not invalidated and not needs_new_run:
            continue
        generation = meta.get("generation", 1) + 1
        previous_ref = archive_step(store, task, decision["id"])
        if previous_ref:
            meta["replan_run"] = {"previous_step_ref": previous_ref, "inputs": list(task.get("inputs", [])),
                                  "expected_outputs": list(task.get("expected_outputs", [])), "requirement_ids": list(task["requirement_ids"])}
            meta.pop("step_ref", None)
        meta.pop("invalidated_by", None)
        meta.pop("artefact_revision", None)
        meta.update(status="todo", generation=generation, depends_on=list(task["depends_on"]),
                    logical_snapshot=logical_snapshot(task), provenance_inputs=list(task.get("inputs", [])),
                    requirement_ids=list(task["requirement_ids"]), replan_decision=decision["id"])
        for row in tables["tasks"]:
            if row["task_id"] == task["id"]:
                row["status"] = "todo"
        for pending in commands:
            if pending["status"] == "queued":
                pending.update(status="superseded", superseded_by_decision=decision["id"])
        affected.append(task["id"])
    return affected


def materialize_run_generation(root, data, tables, store, task):
    meta = store["tasks"].get(task["id"], {})
    template = meta.get("replan_run")
    if not template:
        return None, task
    inputs = template["inputs"]
    if not inputs:
        raise ValueError("새 실행 세대에 실제 입력 근거가 필요합니다.")
    runs.hashes(root, inputs)
    outputs = list(dict.fromkeys(template["expected_outputs"] + task["execution"]["expected_outputs"]))
    for relative in outputs:
        execution_path(root, relative)
    run_store = runs.read(root)
    identifier = f"RUN-{len(run_store['runs']) + 1:03d}"
    payload = {"key": "workspace-generation:" + task["id"] + ":" + str(meta["generation"]) + ":" + meta["replan_decision"],
               "title": task["title"] + " · 새 실행 세대 " + str(meta["generation"]), "requirement_ids": template["requirement_ids"],
               "steps": [{"id": "STEP-001", "title": task["title"], "depends_on": [], "inputs": inputs, "expected_outputs": outputs}]}
    step = {**payload["steps"][0], "status": "planned", "attempts": 0, "results": [], "receipt_hashes": {}}
    run_store["runs"].append({"id": identifier, **payload, "signature": digest(json_bytes(payload)), "steps": [step]})
    meta["step_ref"] = {"run_id": identifier, "step_id": "STEP-001"}
    meta.pop("replan_run")
    task = task_records(root, data, tables, store, run_store=run_store)[task["id"]]
    return run_store, task


def validate_usage(usage):
    allowed = {"tokens", "cost_usd", "model", "source", "attempt_id", "provider_session_id", "coverage"}
    if not isinstance(usage, dict) or set(usage) - allowed or not isinstance(usage.get("source"), str) or not usage["source"].strip():
        raise ValueError("사용량 수집원과 허용된 필드가 필요합니다.")
    for field in ("tokens", "cost_usd"):
        value = usage.get(field)
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (field == "tokens" and type(value) != int)):
            raise ValueError("사용량은 0 이상의 실제 수치여야 합니다.")
    for field in ("model", "attempt_id", "provider_session_id"):
        if usage.get(field) is not None and (not isinstance(usage[field], str) or len(usage[field]) > 1000):
            raise ValueError("사용량의 모델·실행 식별자는 문자열이어야 합니다.")
    if usage.get("coverage") not in {None, "complete", "known", "partial", "unknown"}:
        raise ValueError("사용량 수집 완전성 값이 잘못되었습니다.")
    return dict(usage)


def enqueue_ready(root, data, tables, store, *, run_store=None):
    tasks = task_records(root, data, tables, store, run_store=run_store)
    for task in tasks.values():
        # Revision work has its own explicit queue entry and does not replay old runs.
        if task["status"] == "todo" and not blockers(root, store, tasks, task["id"]):
            if not any(q["task_id"] == task["id"] and q.get("generation", 1) == store["tasks"].get(task["id"], {}).get("generation", 1)
                       and q["status"] in {"queued", "claimed", "running", "failed"} for q in store["queue"]):
                store["queue"].append({"id": "CMD-" + uuid.uuid4().hex, "task_id": task["id"], "status": "queued", "at": starter.now(),
                                       "generation": store["tasks"].get(task["id"], {}).get("generation", 1)})


def decide(folder, payload, actor=None):
    fields = {"action_id", "expected_revision", "gate_id", "decision", "feedback", "actor", "target_hashes", "context_hash"}
    if not isinstance(payload, dict) or set(payload) != fields or payload["decision"] not in {"approve", "request_revision"}:
        raise ValueError("승인/수정 요청 필드가 잘못되었습니다.")
    if any(not isinstance(payload[k], str) or not payload[k].strip() for k in ("action_id", "gate_id", "actor")):
        raise ValueError("결정 ID·대상·실제 검토자 이름이 필요합니다.")
    if actor is not None and actor != payload["actor"]:
        raise ValueError("명시한 검토자와 결정 payload의 actor가 일치해야 합니다.")
    if len(payload["action_id"]) > 150 or len(payload["actor"]) > 200 or not isinstance(payload["feedback"], str) or len(payload["feedback"]) > 10000:
        raise ValueError("결정 입력 길이 제한을 초과했습니다.")
    if payload["decision"] == "request_revision" and not payload["feedback"].strip():
        raise ValueError("수정 요청에는 대상·사유·수용 기준을 입력하세요.")
    root, data, tables = lifecycle.bundle(folder)
    signature = digest(json_bytes({k: v for k, v in payload.items() if k != "expected_revision"}))
    payload = {**payload, "feedback": redact_text(payload["feedback"])}
    with lock(root):
        root, data, tables = lifecycle.bundle(root)
        store = read(root)
        prior = store["actions"].get(payload["action_id"])
        if prior:
            if prior["signature"] != signature:
                raise Conflict("같은 결정 ID를 다른 요청에 사용할 수 없습니다.")
            return {"snapshot": snapshot(root), "unchanged": True}
        require_revision(root, payload)
        gate = next((g for g in store["gates"] if g["id"] == payload["gate_id"]), None)
        if not gate:
            raise ValueError("없는 승인 게이트입니다.")
        hashes = {p: file_hash(root, p) for p in gate["paths"]}
        if not isinstance(payload["target_hashes"], dict) or hashes != payload["target_hashes"] or any(h is None for h in hashes.values()):
            raise Conflict("검토한 산출물이 변경/삭제되었습니다. 새 Diff를 확인하세요.")
        reviewed_context = context_hash(root, store, gate)
        if not isinstance(payload["context_hash"], str) or payload["context_hash"] != reviewed_context:
            raise Conflict("검토한 요구사항·원본·선행 산출물이 변경되었습니다. 새 연결 정보를 확인하세요.")
        if payload["decision"] == "approve" and gate_status(root, store, gate) == "approved":
            raise Conflict("이미 승인된 버전입니다.")
        active = [m for m in store["tasks"].values() if m.get("gate_id") == gate["id"] and m.get("rework") and m.get("status") != "done"]
        if payload["decision"] == "approve" and active:
            raise Conflict("수정 작업의 실제 완료 근거를 먼저 제출하세요.")
        if payload["decision"] == "request_revision" and active:
            raise Conflict("진행 중인 수정 작업이 있습니다. 완료 후 다시 검토하세요.")
        decision = {"id": "DEC-" + uuid.uuid4().hex, "gate_id": gate["id"], "decision": payload["decision"],
            "actor": payload["actor"], "authority": "local-cli-evidence" if actor else "local-human-capability", "at": starter.now(),
            "context_hash": reviewed_context,
            "target_hashes": hashes, "feedback": payload["feedback"], "expected_revision": payload["expected_revision"]}
        store["decisions"].append(decision)
        # Review versions are evidence only; these bytes never overwrite project files.
        for relative in gate["paths"]:
            path = confined(root, relative)
            raw = file_bytes(root, relative) if path.stat().st_size <= MAX_FILE else None
            if raw is not None and digest(raw) != hashes[relative]:
                raise Conflict("검토 처리 중 파일이 변경됐습니다. 다시 검토하세요.")
            store["baselines"][relative] = {"sha256": hashes[relative], "content": base64.b64encode(raw).decode() if raw is not None else None,
                "at": decision["at"], "actor": payload["actor"], "preview_available": raw is not None}
        store["actions"][payload["action_id"]] = {"signature": signature, "decision_id": decision["id"]}
        tasks = task_records(root, data, tables, store)
        extra = {f"reports/workspace-decisions/{decision['id']}.json": json_bytes(decision)}
        replanned = []
        promoted = None
        if payload["decision"] == "request_revision":
            for task in tasks.values():
                if gate["task_id"] in ancestors(tasks, task["id"]) and task["status"] in {"done", "stale"}:
                    store["tasks"].setdefault(task["id"], {})["invalidated_by"] = decision["id"]
            number = sum(1 for d in store["decisions"] if d["gate_id"] == gate["id"] and d["decision"] == "request_revision")
            identifier = f"REWORK-{gate['id']}-{number}"
            source = tasks[gate["task_id"]]
            store["tasks"][identifier] = {"rework": True, "gate_id": gate["id"], "title": "수정 · " + source["title"],
                "stage": source["stage"], "role": source["role"], "status": "todo", "requirement_ids": source["requirement_ids"],
                "outputs": gate["paths"], "feedback": payload["feedback"], "rejected_hashes": hashes,
                "execution": rework_execution(root, source, gate["paths"], payload["feedback"])}
            store["feedback"].append({"id": decision["id"], "source": gate["task_id"], "target": identifier,
                "type": "revision", "instruction": payload["feedback"], "at": decision["at"]})
            store["queue"].append({"id": "CMD-" + uuid.uuid4().hex, "task_id": identifier, "status": "queued", "at": starter.now()})
        else:
            promoted = promote_reworked_source(root, data, tables, store, gate, decision)
            replanned = prepare_reapproved_work(root, data, tables, store, gate, decision)
            enqueue_ready(root, data, tables, store)
            if promoted or replanned:
                extra["registers/tasks.csv"] = lifecycle.table_bytes(root, "tasks", tables["tasks"])
        if version_hashes(root, gate) != hashes or context_hash(root, store, gate) != reviewed_context:
            raise Conflict("검토 처리 중 산출물 또는 입력 기준선이 변경되었습니다. 다시 검토하세요.")
        save(root, store, "workspace-decision", payload["actor"], {"decision_id": decision["id"], "gate_id": gate["id"], "decision": decision["decision"],
            "replanned_tasks": replanned, "promoted_source": promoted}, extra)
        return {"snapshot": snapshot(root), "unchanged": False}


@consistent_read
def diff(folder, relative):
    root, _ = starter.load_project(folder)
    valid_artifact(root, relative)
    snap = snapshot(root)
    if relative not in {a["path"] for a in snap["artefacts"]}:
        raise ValueError("등록된 산출물만 열 수 있습니다.")
    path = confined(root, relative)
    if path.is_file() and path.stat().st_size > MAX_FILE:
        return {"path": relative, "baseline_available": relative in read(root)["baselines"], "binary": True,
            "content": None, "diff": None, "sha256": file_hash(root, relative), "message": "미리보기 크기 제한을 초과했습니다. 원본 도구에서 실제 파일을 검토하세요."}
    raw = file_bytes(root, relative)
    if raw is None:
        raise ValueError("파일이 없습니다.")
    base = read(root)["baselines"].get(relative)
    try:
        text = raw.decode("utf-8-sig")
        if "\x00" in text:
            raise UnicodeError("binary")
        before = base64.b64decode(base["content"]).decode("utf-8-sig") if base and base.get("content") else ""
        value = "".join(difflib.unified_diff(before.splitlines(True), text.splitlines(True), fromfile="baseline/" + relative, tofile="current/" + relative))
        return {"path": relative, "baseline_available": bool(base and base.get("content")), "binary": False, "content": text, "diff": value, "sha256": digest(raw)}
    except UnicodeError:
        return {"path": relative, "baseline_available": bool(base), "binary": True, "content": None,
            "diff": None, "sha256": digest(raw), "message": "이진 산출물입니다. 렌더링·원본 도구에서 버전을 검토하세요."}


def role_matches(configured, selected):
    if not configured or configured == selected:
        return True
    from tools import role_agents
    try:
        return role_agents.resolve(configured)["id"] == role_agents.resolve(selected)["id"]
    except ValueError:
        return False


def job_contract_hash(task):
    return digest(json.dumps(task.get("execution"), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))


def host_event(folder, payload):
    """Host capability: receipts and telemetry, never human decisions.

    RUN/STEP hosts first record run-result through the normal engine, then
    report the observed execution here. Register-only tasks require actual
    output hashes and update the CSV in the same transaction as their receipt.
    """
    allowed = {"expected_revision", "kind", "agent_id", "provider", "role", "task_id", "command_id", "notes", "evidence_refs", "usage", "verification_summary", "verified_outputs"}
    if not isinstance(payload, dict) or set(payload) - allowed or payload.get("kind") not in {"heartbeat", "claim", "started", "completed", "failed", "retry"}:
        raise ValueError("호스트 이벤트 필드를 확인하세요.")
    for key in ("agent_id", "provider", "role"):
        if not isinstance(payload.get(key), str) or not payload[key].strip() or len(payload[key]) > 200:
            raise ValueError("실제 작업자 ID·호스트·역할이 필요합니다.")
    if "notes" in payload and (not isinstance(payload["notes"], str) or len(payload["notes"]) > 10000):
        raise ValueError("호스트 로그는 10,000자 이하의 문자열이어야 합니다.")
    payload = {**payload, **({"notes": redact_text(payload["notes"])} if "notes" in payload else {})}
    root, data, tables = lifecycle.bundle(folder)
    with lock(root):
        root, data, tables = lifecycle.bundle(root)
        require_revision(root, payload)
        store = read(root)
        now = starter.now()
        agent = store["agents"].get(payload["agent_id"], {})
        if agent and agent.get("provider") != payload["provider"]:
            raise ValueError("작업자의 호스트를 변경할 수 없습니다.")
        agent = {**agent, "id": payload["agent_id"], "name": payload["agent_id"], "provider": payload["provider"],
                 "role": payload["role"], "last_seen": now, "status": agent.get("status", "idle")}
        store["agents"][agent["id"]] = agent
        extra, command = {}, None
        tasks = task_records(root, data, tables, store)
        lease_end = (datetime.now(timezone.utc) + timedelta(seconds=90)).isoformat()
        if payload["kind"] == "heartbeat":
            for queued in store["queue"]:
                if queued.get("agent_id") == agent["id"] and queued["status"] == "claimed":
                    queued["lease_expires_at"] = lease_end
        if payload["kind"] == "claim":
            for queued in store["queue"]:
                if queued["status"] == "claimed" and queued.get("lease_expires_at", "") < now:
                    queued.update(status="queued", previous_agent_id=queued.pop("agent_id", None))
            command = next((q for q in store["queue"] if q["status"] == "queued" and command_matches_task(q, tasks[q["task_id"]]) and (not payload.get("task_id") or q["task_id"] == payload["task_id"])
                and role_matches(tasks[q["task_id"]].get("role", ""), payload["role"]) and not blockers(root, store, tasks, q["task_id"])), None)
            if command:
                generated, fresh_task = materialize_run_generation(root, data, tables, store, tasks[command["task_id"]])
                if generated is not None:
                    extra[runs.STORE] = json_bytes(generated)
                    tasks[command["task_id"]] = fresh_task
                command.update(status="claimed", agent_id=agent["id"], claimed_at=now, lease_expires_at=lease_end,
                               generation=tasks[command["task_id"]].get("generation", 1), step_ref=tasks[command["task_id"]].get("step_ref"),
                               execution_hash=job_contract_hash(tasks[command["task_id"]]))
                agent.update(status="idle", task_id=command["task_id"])
        elif payload["kind"] != "heartbeat":
            command = next((q for q in store["queue"] if q["id"] == payload.get("command_id")), None)
            allowed_status = {"failed"} if payload["kind"] == "retry" else {"claimed", "running"}
            if command is None or command.get("agent_id") != agent["id"] or command["status"] not in allowed_status:
                raise Conflict("이 작업자가 소유한 유효한 실행 요청이 아닙니다.")
            task = tasks[command["task_id"]]
            if not command_matches_task(command, task):
                raise Conflict("이전 실행 세대의 요청입니다. 현재 TASK/STEP에 기록할 수 없습니다.")
            if payload["kind"] != "failed" and blockers(root, store, tasks, task["id"]):
                raise Conflict("선행 작업/승인 상태가 변경되었습니다.")
            if payload["kind"] in {"started", "completed"} and command.get("execution_hash", job_contract_hash(task)) != job_contract_hash(task):
                raise Conflict("실행 계획이 작업 수신 이후 변경되었습니다. 새 계획으로 다시 실행하세요.")
            if payload["kind"] == "started":
                if command["status"] != "claimed":
                    raise Conflict("이미 실행 중입니다.")
                if command.get("lease_expires_at", "") < now:
                    raise Conflict("실행 요청의 임대가 만료됐습니다. 다시 claim하세요.")
                if task.get("run_id") and task["status"] != "running":
                    raise Conflict("기존 엔진의 run-result running 영수증을 먼저 기록하세요.")
                command.update(status="running", started_at=now, attempt_id="ATT-" + uuid.uuid4().hex,
                    attempt_number=command.get("attempt_number", 0) + 1,
                    approved_gate_hashes={g["id"]: version_hashes(root, g) for g in store["gates"] if g["task_id"] in ancestors(tasks, task["id"])},
                    approved_gate_contexts={g["id"]: context_hash(root, store, g) for g in store["gates"] if g["task_id"] in ancestors(tasks, task["id"])},
                    input_hashes={p: file_hash(root, p) for p in task.get("inputs", []) if confined(root, p).is_file()})
                agent["status"] = "running"
                if task.get("rework"):
                    store["tasks"][task["id"]]["status"] = "running"
                elif task["id"].startswith("TASK-") and not task.get("run_id"):
                    row = next(r for r in tables["tasks"] if r["task_id"] == task["id"])
                    row["status"] = "doing"
                    extra["registers/tasks.csv"] = lifecycle.table_bytes(root, "tasks", tables["tasks"])
            elif payload["kind"] == "completed":
                if command["status"] != "running":
                    raise Conflict("실제 시작 이벤트가 필요합니다.")
                supplied_verification = validate_verification_summary(payload["verification_summary"]) if payload.get("verification_summary") is not None else None
                verified_outputs = payload.get("verified_outputs")
                if supplied_verification and supplied_verification["source"] == "supervisor" and supplied_verification["status"] in {"passed", "artifact_only"} and not verified_outputs:
                    raise ValueError("감독자 검증 완료에는 실제 출력의 해시·크기 영수증이 필요합니다.")
                if verified_outputs is not None:
                    if not isinstance(verified_outputs, dict) or not verified_outputs or len(verified_outputs) > 10000:
                        raise ValueError("검증된 출력 목록의 형식이 잘못되었습니다.")
                    if not set(task.get('expected_outputs', [])).issubset(verified_outputs):
                        raise ValueError('계획한 산출물 전체가 검증된 출력 영수증에 필요합니다.')
                    owned_paths = (task.get('execution') or {}).get('allowed_paths', [])
                    for relative, record in verified_outputs.items():
                        if not any(relative == owned.rstrip('/') or relative.startswith(owned.rstrip('/') + '/') for owned in owned_paths):
                            raise ValueError('검증된 출력이 담당 범위 밖입니다.')
                        path = execution_path(root, relative)
                        if not isinstance(record, dict) or set(record) != {"sha256", "bytes"} or not path.is_file() or file_hash(root, relative) != record["sha256"] or path.stat().st_size != record["bytes"]:
                            raise Conflict("검증한 산출물이 완료 기록 전에 변경됐습니다.")
                receipts = runs.hashes(root, payload.get("evidence_refs", []))
                if not receipts or not set(task.get("expected_outputs", [])).issubset(receipts):
                    raise ValueError("계획한 출력의 실제 완료 근거가 필요합니다.")
                if any(file_hash(root, p) != h for p, h in command.get("input_hashes", {}).items()):
                    raise Conflict("실행 입력이 변경되었습니다.")
                for gate_id, hashes in command.get("approved_gate_hashes", {}).items():
                    gate = next(g for g in store["gates"] if g["id"] == gate_id)
                    if version_hashes(root, gate) != hashes or context_hash(root, store, gate) != command.get("approved_gate_contexts", {}).get(gate_id):
                        raise Conflict("실행 중 승인된 선행 산출물의 버전이 변경됐습니다.")
                if task.get("run_id") and task["status"] != "done":
                    raise Conflict("기존 엔진의 run-result succeeded 영수증이 필요합니다.")
                if task.get("rework"):
                    meta = store["tasks"][task["id"]]
                    if all(file_hash(root, p) == h for p, h in meta["rejected_hashes"].items()):
                        raise Conflict("수정 요청 이후 산출물 변경이 없습니다.")
                    meta.update(status="done", evidence_refs=list(receipts))
                elif task["id"].startswith("TASK-") and not task.get("run_id"):
                    row = next(r for r in tables["tasks"] if r["task_id"] == task["id"])
                    row.update(status="done", evidence_refs=";".join(receipts))
                    extra["registers/tasks.csv"] = lifecycle.table_bytes(root, "tasks", tables["tasks"])
                command.update(status="completed", receipt_hashes=receipts, completed_at=now, notes=payload.get("notes", "완료 근거를 확인했습니다."))
                agent["status"] = "idle"
                if task.get("rework"):
                    gate = next(g for g in store["gates"] if g["id"] == task["gate_id"])
                    if not gate.get("required", True):
                        observation = {"id": "AI-REWORK-" + command["id"], "gate_id": gate["id"],
                            "target_hashes": version_hashes(root, gate), "context_hash": context_hash(root, store, gate)}
                        promote_reworked_source(root, data, tables, store, gate, observation)
                        prepare_reapproved_work(root, data, tables, store, gate, observation)
                        extra["registers/tasks.csv"] = lifecycle.table_bytes(root, "tasks", tables["tasks"])
                enqueue_ready(root, data, tables, store)
            elif payload["kind"] == "retry":
                if not isinstance(payload.get("notes"), str) or not payload["notes"].strip():
                    raise ValueError("재시도 사유가 필요합니다.")
                command.update(status="queued", retry_reason=payload["notes"])
                if task.get("rework"):
                    store["tasks"][task["id"]]["status"] = "todo"
                elif task["id"].startswith("TASK-") and not task.get("run_id"):
                    row = next(r for r in tables["tasks"] if r["task_id"] == task["id"])
                    row["status"] = "todo"
                    extra["registers/tasks.csv"] = lifecycle.table_bytes(root, "tasks", tables["tasks"])
            else:
                if not isinstance(payload.get("notes"), str) or not payload["notes"].strip():
                    raise ValueError("실패 사유가 필요합니다.")
                command.update(status="failed", notes=payload["notes"])
                agent["status"] = "idle"
                if task.get("rework"):
                    store["tasks"][task["id"]]["status"] = "blocked"
                elif task["id"].startswith("TASK-") and not task.get("run_id"):
                    row = next(r for r in tables["tasks"] if r["task_id"] == task["id"])
                    row["status"] = "blocked"
                    extra["registers/tasks.csv"] = lifecycle.table_bytes(root, "tasks", tables["tasks"])
        if payload["kind"] in {"completed", "failed"}:
            usage = validate_usage(payload["usage"]) if payload.get("usage") is not None else {"tokens": None, "cost_usd": None, "source": "unreported", "coverage": "unknown"}
            store["usage"].append({**usage, "attempt_id": usage.get("attempt_id") or command.get("attempt_id") or "ATT-"+uuid.uuid4().hex,
                "agent_id": agent["id"], "command_id": command["id"], "outcome": payload["kind"], "at": now})
            if payload.get("verification_summary") is not None:
                command["verification_summary"] = validate_verification_summary(payload["verification_summary"])
        if payload["kind"] == "heartbeat" and not payload.get("notes"):
            # Runtime liveness is not a business edit and must not duplicate
            # large approval snapshots in history or stale a human's revision.
            commit(root, {STORE: json_bytes(store)})
            return {"revision": revision(root), "command": None, "task": None, "snapshot": snapshot(root)}
        rev = save(root, store, "workspace-host", agent["id"], {"kind": payload["kind"], "command_id": command["id"] if command else None,
            "task_id": command["task_id"] if command else agent.get("task_id"), "provider": payload["provider"],
            "role": payload["role"], "notes": payload.get("notes", "")}, extra)
        return {"revision": rev, "command": command, "task": tasks.get(command["task_id"]) if command else None, "snapshot": snapshot(root)}


def main(argv=None):
    import sys
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="프로젝트이음 로컬 관제 데이터")
    parser.add_argument("command", choices=("snapshot", "configure", "decide", "host"))
    parser.add_argument("project")
    parser.add_argument("--json", dest="payload")
    parser.add_argument("--actor")
    args = parser.parse_args(argv)
    if args.command == "snapshot":
        result = snapshot(args.project)
    else:
        if not args.payload:
            parser.error("--json이 필요합니다.")
        if args.command == "decide" and not args.actor:
            parser.error("결정에 대한 실제 검토자 --actor가 필요합니다.")
        payload = json.loads(Path(args.payload).read_text(encoding="utf-8-sig"))
        result = configure(args.project, payload, args.actor or "agent") if args.command == "configure" else decide(args.project, payload, args.actor) if args.command == "decide" else host_event(args.project, payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
