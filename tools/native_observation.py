"""Host-sourced runtime projection, separate from deliverable verification and history."""
import hashlib
import json
import os
import re
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

from tools.storage import atomic_write, confined, consistent_read, ensure_project_directory, json_bytes, lock
from tools.sensitive_data import redact_text

STORE = "state/workspace-native.json"
MAX_STATE = 8 * 1024 * 1024
MAX_EVENTS = 1200
MAX_BINDINGS = 500
KINDS = {"session_started", "session_idle", "session_ended", "agent_started", "agent_completed", "agent_failed", "tool_started", "tool_completed", "delegation_started", "delegation_bound", "delegation_failed"}
SOURCES = {"host_hook", "codex_rollout"}
TOOL_NAMES = {"Read", "Write", "Edit", "Glob", "Grep", "Bash", "PowerShell", "Agent", "Task", "spawn_agent", "send_message", "followup_task", "exec_command", "write_stdin", "apply_patch", "update_plan", "exec", "MCP", "local_tool"}
ID = re.compile(r"[A-Za-z0-9_./:@-]{1,240}\Z")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _root(project):
    from tools.starter import load_project
    return load_project(project)[0]


@consistent_read
def _read(root):
    path = confined(root, STORE)
    if not path.exists():
        return {"schema_version": 1, "seq": 0, "sessions": {}, "agents": {}, "events": [], "bindings": {}, "calls": {}, "focus": {}, "cursors": {}}
    if path.stat().st_size > MAX_STATE:
        raise ValueError("네이티브 관측 기록 크기 제한을 초과했습니다.")
    with path.open("rb") as stream:
        raw = stream.read(MAX_STATE + 1)
    if len(raw) > MAX_STATE:
        raise ValueError("네이티브 관측 기록 크기 제한을 초과했습니다.")
    data = json.loads(raw)
    if data.get("schema_version") != 1:
        raise ValueError("네이티브 관측 기록 형식이 다릅니다.")
    for key, value in {"sessions": {}, "agents": {}, "events": [], "bindings": {}, "calls": {}, "focus": {}, "cursors": {}, "seq": 0}.items():
        data.setdefault(key, value)
    return data


def _save(root, data):
    data["events"] = data["events"][-MAX_EVENTS:]
    raw = json_bytes(data)
    if len(raw) > MAX_STATE:
        raise ValueError("네이티브 관측 기록 한도에 도달했습니다. 원본 이력은 유지합니다.")
    # This replayable projection changes one file while the caller holds lock.
    # Keep it outside the business rollback journal: a reader-held Windows file
    # must not leave pending.json blocking snapshots and unrelated transactions.
    path = confined(root, STORE)
    deadline = time.monotonic() + 0.25
    while True:
        try:
            atomic_write(path, raw, security_anchor=root)
            return
        except PermissionError as exc:
            # Only retry replacement of this exact native file. Denials during
            # temp creation/flush, other paths and non-Windows errors stay fatal.
            destination = getattr(exc, "filename2", None)
            remaining = deadline - time.monotonic()
            if (os.name != "nt" or getattr(exc, "winerror", None) not in {5, 32}
                    or destination is None or Path(destination).absolute() != path.absolute()
                    or remaining <= 0):
                raise
            time.sleep(min(0.01, remaining))


def read(project):
    data = _read(_root(project))
    return {"seq": data["seq"], "sessions": list(data["sessions"].values()), "agents": list(data["agents"].values()), "events": list(data["events"])}


def _identifier(value, label):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValueError(label + " 식별자가 잘못되었습니다.")
    return value


def _session_key(provider, session):
    if provider not in {"codex", "claude"}:
        raise ValueError("지원하지 않는 실행 호스트입니다.")
    _identifier(session, "세션")
    return provider + ":" + session


def _agent_key(provider, session, native):
    return _session_key(provider, session) + ":" + _identifier(native, "작업자")


def _task_ids(root):
    from tools import starter, runs, workspace
    data = json.loads((root / "project.json").read_text(encoding="utf-8"))
    ids = {r["task_id"] for r in starter.read_table(root, "tasks")}
    meta = workspace.read(root)
    ids.update(meta["tasks"])
    for run in runs.read(root)["runs"]:
        ids.update(data["project_id"] + "/" + run["id"] + "/" + s["id"] for s in run["steps"])
    return ids


def _generation(root, task_id):
    if not task_id:
        return None
    from tools import workspace
    meta = workspace.read(root)["tasks"].get(task_id, {})
    ref = meta.get("step_ref")
    base = (ref["run_id"] + "/" + ref["step_id"]) if ref else "task:" + task_id
    return base + ":g" + str(meta.get("generation", 1))


def _role(value):
    from tools.role_agents import resolve
    text = str(value or "").split(":")[-1].removeprefix("ieum-")
    try:
        return resolve(text)["id"]
    except (ValueError, KeyError):
        return "unassigned"


def prepare_delegation(project, task_id, role, provider, session_id):
    root = _root(project)
    _session_key(provider, session_id)
    role = _role(role)
    if role == "unassigned":
        raise ValueError("등록된 역할 계약이 필요합니다.")
    with lock(root):
        if task_id not in _task_ids(root):
            raise ValueError("실제 작업 ID가 필요합니다.")
        from tools import workspace
        execution = workspace.read(root)["tasks"].get(task_id, {}).get("execution")
        if execution is not None:
            execution = workspace.validate_execution(root, execution)
            for relative in execution["expected_outputs"]:
                ensure_project_directory(workspace.execution_path(root, relative).parent, root)
        data = _read(root)
        if len(data["bindings"]) >= MAX_BINDINGS:
            raise ValueError("이 프로젝트의 위임 연결 한도에 도달했습니다.")
        token = secrets.token_hex(12)
        ticket = {"token": token, "task_id": task_id, "role": role, "provider": provider, "session_id": session_id, "created_at": _now(),
                  "execution_ref": _generation(root, task_id), "task_name": "ieum_" + token, "description": "IEUM_BIND:" + token, "native_agent_id": None}
        data["bindings"][token] = ticket
        _save(root, data)
        return dict(ticket)


def resolve_delegation(project, provider, session_id, token):
    _session_key(provider, session_id)
    if not isinstance(token, str) or not re.fullmatch(r"[0-9a-f]{24}", token):
        return None
    value = _read(_root(project))["bindings"].get(token)
    return dict(value) if value and value["provider"] == provider and value["session_id"] == session_id else None


def focus(project, task_id, role, provider, session_id):
    root = _root(project)
    key = _session_key(provider, session_id)
    role = _role(role)
    if role == "unassigned":
        raise ValueError("등록된 역할 계약이 필요합니다.")
    with lock(root):
        if task_id not in _task_ids(root):
            raise ValueError("현재 작업 ID를 확인하세요.")
        data = _read(root)
        data["focus"][key] = {"task_id": task_id, "role": role, "execution_ref": _generation(root, task_id)}
        _save(root, data)
    return {"task_id": task_id, "role": role, "session_id": session_id}


def _at(value):
    if not isinstance(value, str):
        return _now()
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).isoformat()
    except ValueError:
        raise ValueError("관측 시각에는 시간대가 필요합니다.")


def ingest(project, provider, event):
    root = _root(project)
    if not isinstance(event, dict):
        raise ValueError("호스트 관측 이벤트 객체가 필요합니다.")
    allowed = {"event_id", "kind", "session_id", "native_agent_id", "role", "task_id", "at", "call_id", "parent_id", "source", "summary", "tool_name", "delegation_token", "native_type", "span_id"}
    if set(event) - allowed or event.get("kind") not in KINDS or event.get("source") not in SOURCES:
        raise ValueError("지원하지 않는 네이티브 관측 필드입니다.")
    session = event.get("session_id")
    skey = _session_key(provider, session)
    event_id = _identifier(event.get("event_id"), "이벤트")
    native = event.get("native_agent_id")
    if native is not None:
        _identifier(native, "작업자")
    call_id = event.get("call_id")
    if call_id is not None:
        _identifier(call_id, "호출")
    kind, at = event["kind"], _at(event.get("at"))
    # Only normalized metadata reaches disk; no original prompt/result is retained.
    summary = redact_text(str(event.get("summary") or {
        "session_started": "프로젝트 작업 준비", "session_idle": "호스트 응답 완료", "session_ended": "호스트 세션 종료",
        "agent_started": "작업자 실행 시작", "agent_completed": "작업자 실행 종료 · 결과 확인", "agent_failed": "작업자 실행 실패",
        "delegation_failed": "작업자 실행 연결 실패", "delegation_started": "작업자 실행 연결 중", "delegation_bound": "실제 작업자 ID 연결", "tool_started": "도구 실행 중", "tool_completed": "도구 실행 종료"
    }[kind]))[:500]
    tool = event.get("tool_name")
    tool = tool if isinstance(tool, str) and tool in TOOL_NAMES else "local_tool"
    if kind in {"tool_started", "tool_completed"}:
        summary = tool + " · " + summary
    with lock(root):
        data = _read(root)
        signature = hashlib.sha256(json_bytes({"provider": provider, **event})).hexdigest()
        previous = next((e for e in data["events"] if e["id"] == event_id and e["provider"] == provider and e["session_id"] == session), None)
        if previous:
            if previous["signature"] != signature:
                raise ValueError("같은 관측 이벤트 ID의 내용이 바뀌었습니다.")
            return {"unchanged": True, "event": {k: v for k, v in previous.items() if k != "signature"}}
        token = event.get("delegation_token")
        ticket = data["bindings"].get(token) if isinstance(token, str) else None
        if ticket and (ticket["provider"] != provider or ticket["session_id"] != session):
            ticket = None
        call_key = skey + ":" + call_id if call_id else None
        if kind == "delegation_started" and call_key:
            if len(data["calls"]) >= 1500 and call_key not in data["calls"]:
                raise ValueError("관측 호출 연결 한도에 도달했습니다.")
            data["calls"][call_key] = {"call_id": call_id, "provider": provider, "session_id": session, "token": ticket["token"] if ticket else None,
                                       "native_type": str(event.get("native_type") or "")[:120], "status": "starting", "at": at, "native_agent_id": None}
        pending = data["calls"].get(call_key) if call_key else None
        if kind == "delegation_failed" and pending:
            pending["status"] = "failed"
        if not ticket and pending and pending.get("token"):
            ticket = data["bindings"].get(pending["token"])
        if kind == "agent_started" and not ticket:
            matching = [c for c in data["calls"].values() if c["provider"] == provider and c["session_id"] == session and c["status"] == "starting"
                        and c.get("token") and c["native_type"] == str(event.get("native_type") or "")]
            if len(matching) == 1:
                pending = matching[0]
                ticket = data["bindings"].get(pending["token"])
        if native and ticket:
            if ticket.get("native_agent_id") not in {None, native}:
                raise ValueError("위임 연결을 다른 작업자로 덮을 수 없습니다.")
            ticket["native_agent_id"] = native
            if pending:
                pending.update(native_agent_id=native, status=pending["status"] if pending["status"] in {"completed", "failed"} else "running")
        if kind == "delegation_bound" and native:
            for c in data["calls"].values():
                if c["provider"] == provider and c["session_id"] == session and c["native_agent_id"] == native:
                    if c.get("token") and not ticket:
                        ticket = data["bindings"].get(c["token"])
        actor_native = native or session
        actor_key = _agent_key(provider, session, actor_native)
        known = data["agents"].get(actor_key, {})
        task_id = ticket["task_id"] if ticket else event.get("task_id") or known.get("task_id")
        role = ticket["role"] if ticket else _role(event.get("role") or event.get("native_type"))
        reset_primary = actor_native == session and kind == "session_started" and at >= known.get("last_seen", "") and at >= data["sessions"].get(skey, {}).get("last_seen", "")
        if reset_primary:
            task_id = None
            data["focus"].pop(skey, None)
        execution_ref = ticket.get("execution_ref") if ticket else known.get("execution_ref")
        if reset_primary:
            execution_ref = None
        if actor_native == session and kind.startswith("tool_"):
            current = data["focus"].get(skey, {})
            task_id = current.get("task_id") or task_id
            execution_ref = current.get("execution_ref") or execution_ref
            role = current.get("role", role if role != "unassigned" else "orchestrator")
        if task_id is not None and task_id not in _task_ids(root):
            raise ValueError("존재하지 않는 작업에 실행을 연결할 수 없습니다.")
        if role == "unassigned" and known:
            role = known.get("role", role)
        if actor_native == session and role == "unassigned":
            role = "orchestrator"
        sess = data["sessions"].setdefault(skey, {"id": skey, "provider": provider, "session_id": session, "started_at": at, "status": "preparing"})
        newer_session = at >= sess.get("last_seen", "")
        sess.update(last_seen=max(at, sess.get("last_seen", "")), source=event["source"])
        if kind == "session_started" and newer_session:
            sess.update(status="preparing", started_at=at)
        elif kind in {"session_idle", "session_ended"} and newer_session:
            sess["status"] = "idle" if kind == "session_idle" else "ended"
            sess["finished_at"] = at
        elif newer_session and (kind.startswith("tool_") or kind == "delegation_started"):
            sess["status"] = "running"
        if kind not in {"delegation_started", "delegation_failed"} and not (kind == "delegation_bound" and not native):
            if len(data["agents"]) >= 500 and actor_key not in data["agents"]:
                raise ValueError("네이티브 작업자 관측 한도에 도달했습니다.")
            agent = data["agents"].setdefault(actor_key, {"id": actor_key, "native_agent_id": actor_native, "session_id": session,
                                                       "provider": provider, "role": role, "status": "idle", "source": event["source"]})
            newer_agent = at >= agent.get("last_seen", "")
            agent.update(role=role, task_id=task_id, execution_ref=execution_ref or _generation(root, task_id), last_seen=max(at, agent.get("last_seen", "")), last_activity_at=max(at, agent.get("last_activity_at", "")), current_action=summary if newer_agent else agent.get("current_action", summary), source=event["source"], observed=True,
                         native_type=str(event.get("native_type") or agent.get("native_type", ""))[:120], parent_id=event.get("parent_id") or (skey if actor_native != session else None))
            if newer_agent and kind in {"agent_started", "delegation_bound", "session_started", "tool_started"} and not (kind == "delegation_bound" and agent.get("finished_at")):
                if agent["status"] not in {"running", "preparing"}:
                    agent["started_at"] = at
                    agent.pop("finished_at", None)
                agent["status"] = "preparing" if kind == "session_started" else "running"
                agent["span_id"] = event.get("span_id") or call_id or agent.get("span_id")
            elif newer_agent and kind in {"agent_completed", "agent_failed", "session_idle", "session_ended"}:
                agent.update(status="failed" if kind == "agent_failed" else "completed" if kind == "agent_completed" else "idle", finished_at=at)
                if kind.startswith("agent_"):
                    for c in data["calls"].values():
                        if c["provider"] == provider and c["session_id"] == session and c["native_agent_id"] == actor_native:
                            c["status"] = "completed" if kind == "agent_completed" else "failed"
            # A tool completion does not imply completion of its owning task/agent.
        data["seq"] += 1
        record = {"id": event_id, "seq": data["seq"], "at": at, "observed_at": _now(), "provider": provider, "session_id": session,
                  "execution_ref": execution_ref or _generation(root, task_id), "native_agent_id": native, "agent_id": actor_key if kind not in {"delegation_started", "delegation_failed"} and not (kind == "delegation_bound" and not native) else None, "task_id": task_id,
                  "role": role, "kind": kind, "summary": summary, "source": event["source"], "signature": signature}
        if kind in {"tool_started", "tool_completed"}:
            record["tool_name"] = tool
        if call_id:
            record["call_id"] = call_id
        data["events"].append(record)
        _save(root, data)
        return {"unchanged": False, "event": {k: v for k, v in record.items() if k != "signature"}}


def cursor(project, key, value=None):
    root = _root(project)
    _identifier(key, "관측 커서")
    if value is None:
        return dict(_read(root)["cursors"].get(key, {}))
    if not isinstance(value, dict) or set(value) - {"offset", "file_id", "pending", "actors", "session_id"}:
        raise ValueError("관측 커서 형식이 잘못되었습니다.")
    with lock(root):
        data = _read(root)
        data["cursors"][key] = value
        _save(root, data)


def enrich_snapshot(project, result):
    """Overlay runtime facts without changing stored TASK/RUN validation receipts."""
    root = _root(project)
    native = read(root)
    if not native["sessions"]:
        return
    now = datetime.now(timezone.utc)
    agents = []
    for original in native["agents"]:
        agent = dict(original)
        try:
            age = max(0, (now - datetime.fromisoformat(agent["last_seen"])).total_seconds())
        except (TypeError, ValueError):
            age = 1e9
        agent["stale"] = age > 120 and agent["status"] in {"running", "preparing"}
        # Silence during model reasoning is not evidence that execution stopped.
        # Keep the last lifecycle fact, expose its age as telemetry uncertainty.
        agent["name"] = agent.get("native_type") or agent["role"]
        agent["superseded"] = bool(agent.get("task_id") and agent.get("execution_ref") != _generation(root, agent["task_id"]))
        agents.append(agent)
    # Legacy reported aliases remain available as history, not duplicate live actors.
    result["reported_agents"] = result.get("agents", [])
    represented = {(a.get("provider"), a.get("task_id")) for a in agents if a.get("task_id")}
    result["agents"] = agents + [a for a in result.get("agents", []) if a.get("source") not in {"host_report", "native"} and (a.get("provider"), a.get("task_id")) not in represented]
    for task in result["tasks"]:
        failures = [e for e in native["events"] if e.get("task_id") == task["id"] and e["kind"] == "delegation_failed" and e.get("execution_ref") == _generation(root, task["id"])]
        matching = [a for a in agents if a.get("task_id") == task["id"] and not a.get("superseded")]
        task["ledger_status"] = task["status"]
        task["active_native_agent_ids"] = [a["id"] for a in matching if a["status"] in {"running", "preparing"}]
        if matching:
            latest = max(matching, key=lambda a: a.get("last_seen", ""))
            running = next((a for a in reversed(matching) if a["status"] in {"running", "preparing"}), None)
            observed = running or latest
            task.update(execution_state=observed["status"], native_agent_id=observed["native_agent_id"], session_id=observed["session_id"],
                        runtime_source=observed["source"], native_started_at=observed.get("started_at"), native_finished_at=observed.get("finished_at"))
            if running and task["ledger_status"] not in {"done", "skipped", "failed", "stale"}:
                task.update(status="running", observed_status="running", latest_action=running["current_action"], attempt_started_at=running.get("started_at"))
            elif latest["status"] == "completed" and task["ledger_status"] not in {"done", "skipped", "failed", "stale"}:
                task["result_pending"] = True
            elif latest["status"] == "failed" and task["ledger_status"] not in {"done", "skipped", "stale"}:
                task["status"] = "failed"
        failed = max(failures, key=lambda e: e["at"]) if failures else None
        if failed and (not matching or failed["at"] > max(a.get("last_seen", "") for a in matching)):
            task.update(execution_state="failed", runtime_source=failed["source"], session_id=failed["session_id"],
                        latest_action=failed["summary"], native_finished_at=failed["at"])
            if task["ledger_status"] not in {"done", "skipped", "stale"}:
                task["status"] = "failed"
    events = [{k: v for k, v in event.items() if k != "signature"} for event in native["events"]]
    result["activities"] = sorted(result.get("activities", []) + events, key=lambda e: (e.get("at", ""), e.get("seq", 0)))[-MAX_EVENTS:]
    result["activity"] = result["activities"]
    result["native_sessions"] = native["sessions"]
    result["native_observation_seq"] = native["seq"]
    recent = [a for a in agents if not a.get("superseded") and not a.get("stale") and a.get("status") not in {"disconnected", "completed", "failed", "idle"}]
    result["host"]["connected"] = bool(recent) or result["host"].get("connected", False)
    result["host"]["native_stale_seconds"] = 120
    result["host"]["native_observation"] = {"available": True, "session_count": len(native["sessions"]), "seq": native["seq"]}
    if recent:
        result["host"]["execution_mode"] = "mixed" if result["host"].get("runner_connected") else "native"
    result["capabilities"]["host_connected"] = result["host"]["connected"]


def public_snapshot(result):
    """Keep credential masking; restore only typed, host-sourced UUID metadata.

    Generic session_id fields remain secret everywhere else (including logs and
    user documents). These UUIDs are coding-host execution IDs, not login cookies.
    """
    from tools.sensitive_data import redact_value
    public = redact_value(result)
    uuid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
    for key in ("agents", "native_sessions", "tasks", "history_tasks", "activities", "activity"):
        for original, safe in zip(result.get(key, []), public.get(key, [])):
            sid = original.get("session_id")
            source = original.get("source") or original.get("runtime_source")
            if source in SOURCES and isinstance(sid, str) and uuid.fullmatch(sid):
                safe["session_id"] = sid
    return public
