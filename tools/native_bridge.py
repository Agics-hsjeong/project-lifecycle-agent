"""Read only an explicitly attached Codex session; retain lifecycle metadata only.

The persisted rollout adapter complements official hooks for custom collaboration
namespaces. It never controls agents, reads unrelated sessions or copies messages.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import native_observation as observation
from tools.storage import confined

SESSION = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
TOKEN = re.compile(r"(?:ieum[-_]|IEUM_BIND:)([0-9a-f]{24})(?![0-9a-f])")
MAX_LINE = 1024 * 1024
MAX_BATCH = 256
_POLL_LOCK = threading.Lock()


def _sessions_root():
    return (Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions").resolve()


def _validate_source(project, session_id, source):
    if not isinstance(session_id, str) or not SESSION.fullmatch(session_id):
        raise ValueError("현재 Codex 세션 ID가 필요합니다.")
    root = _sessions_root()
    source = Path(source).resolve(strict=True)
    if not source.is_relative_to(root) or source.suffix != ".jsonl" or session_id not in source.name:
        raise ValueError("현재 세션의 Codex 원본 기록만 연결할 수 있습니다.")
    with source.open("rb") as stream:
        first = stream.readline(MAX_LINE + 1)
    if len(first) > MAX_LINE:
        raise ValueError("호스트 기록 헤더가 너무 큽니다.")
    record = json.loads(first)
    if not isinstance(record, dict) or not isinstance(record.get("payload"), dict):
        raise ValueError("호스트 기록 헤더 형식이 다릅니다.")
    meta = record["payload"]
    if record.get("type") != "session_meta" or (meta.get("id") or meta.get("session_id")) != session_id:
        raise ValueError("호스트 원본 세션 ID가 다릅니다.")
    if Path(meta.get("cwd", "")).resolve() != Path(project).resolve():
        raise ValueError("다른 프로젝트의 세션은 관측하지 않습니다.")
    return source


def attach(project, session_id=None, transcript_path=None):
    root = observation._root(project)
    session_id = session_id or os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")
    if not isinstance(session_id, str) or not SESSION.fullmatch(session_id):
        raise ValueError("현재 Codex 세션에서 연결하세요. 다른 세션은 자동 검색하지 않습니다.")
    if transcript_path:
        source = _validate_source(root, session_id, transcript_path)
    else:
        candidates = list(_sessions_root().glob("*/*/*/*" + session_id + ".jsonl"))
        if len(candidates) != 1:
            raise ValueError("현재 세션 원본이 아직 준비되지 않았거나 중복되었습니다.")
        source = _validate_source(root, session_id, candidates[0])
    key = "codex:" + session_id
    previous = observation.cursor(root, key)
    if previous.get("file_id") and previous["file_id"] != str(source):
        raise ValueError("연결된 세션의 원본 파일을 교체할 수 없습니다.")
    if not previous:
        observation.cursor(root, key, {"file_id": str(source), "session_id": session_id, "offset": 0, "pending": {}, "actors": {}})
    poll_one(root, key)
    return {"provider": "codex", "session_id": session_id, "attached": True,
            "source": "codex_rollout", "scope": "current_session_only", "content_retained": False}


def _token(value):
    if not isinstance(value, str) or len(value) > 4096:
        return None
    values = set(TOKEN.findall(value))
    return next(iter(values)) if len(values) == 1 else None


def _emit(project, session, offset, at, kind, **metadata):
    # Deterministic source offsets make a crash between ingest and cursor safe.
    event_id = "rollout-" + hashlib.sha256((session + ":" + str(offset) + ":" + kind).encode()).hexdigest()[:40]
    return observation.ingest(project, "codex", {"event_id": event_id, "kind": kind, "session_id": session,
        "source": "codex_rollout", "at": at, **metadata})


def consume_record(project, state, record, offset):
    """Consume trusted host envelopes, never search arbitrary text for status."""
    if not isinstance(record, dict):
        return
    session, payload = state["session_id"], record.get("payload", {})
    if not isinstance(payload, dict):
        return
    at = record.get("timestamp")
    kind = record.get("type")
    pending, actors = state.setdefault("pending", {}), state.setdefault("actors", {})
    if kind == "session_meta":
        _emit(project, session, offset, at, "session_started")
        return
    if kind == "event_msg":
        if payload.get("type") == "task_started":
            _emit(project, session, offset, at, "session_started")
        elif payload.get("type") == "task_complete":
            _emit(project, session, offset, at, "session_idle")
        return
    if kind != "response_item":
        return
    subtype = payload.get("type")
    if subtype in {"function_call", "custom_tool_call"}:
        name, call = payload.get("name"), payload.get("call_id")
        if not observation.ID.fullmatch(str(call or "")):
            return
        namespace = payload.get("namespace")
        if namespace == "collaboration" and name in {"spawn_agent", "followup_task", "send_message", "interrupt_agent"}:
            try:
                args = json.loads(payload.get("arguments", "{}"))
            except (ValueError, TypeError):
                return
            if not isinstance(args, dict):
                return
            token = _token(args.get("task_name")) if name == "spawn_agent" else None
            target = args.get("target")
            # Store IDs/token only; the full instructions never leave the source.
            item = {"name": name, "call_id": call}
            if token:
                item["token"] = token
            if isinstance(target, str) and observation.ID.fullmatch(target):
                item["target"] = target
            if len(pending) >= 500:
                raise ValueError("동시 관측 호출 한도를 초과했습니다.")
            pending[call] = item
            if name == "spawn_agent":
                _emit(project, session, offset, at, "delegation_started", call_id=call, delegation_token=token)
            return
        # Tool lifecycle without commands, results or arbitrary tool labels.
        if len(pending) >= 500:
            raise ValueError("동시 관측 호출 한도를 초과했습니다.")
        tool = name if name in observation.TOOL_NAMES else "local_tool"
        pending[call] = {"name": "local_tool", "call_id": call, "tool_name": tool}
        _emit(project, session, offset, at, "tool_started", call_id=call, tool_name=tool)
    elif subtype in {"function_call_output", "custom_tool_call_output"}:
        call = payload.get("call_id")
        item = pending.pop(call, None)
        if not item:
            return
        name = item["name"]
        if name == "spawn_agent":
            try:
                output = payload.get("output")
                result = output if isinstance(output, dict) else json.loads(output)
            except (ValueError, TypeError):
                _emit(project, session, offset, at, "delegation_failed", call_id=call)
                return
            native = result.get("task_name") or result.get("agent_id") if isinstance(result, dict) else None
            if not isinstance(native, str) or not observation.ID.fullmatch(native) or result.get("error"):
                _emit(project, session, offset, at, "delegation_failed", call_id=call)
                return
            if len(actors) >= 500 and native not in actors:
                raise ValueError("관측 작업자 한도를 초과했습니다.")
            actors[native] = {"native_agent_id": native}
            _emit(project, session, offset, at, "agent_started", native_agent_id=native,
                  call_id=call, delegation_token=item.get("token"))
        else:
            if name == "followup_task":
                try:
                    response = payload.get("output")
                    response = response if isinstance(response, dict) else json.loads(response)
                except (ValueError, TypeError):
                    response = {} if payload.get("output") == "" and not payload.get("error") and not payload.get("is_error") and payload.get("status") not in {"failed", "error"} else None
                target = item.get("target")
                native = next((i for i in actors if i == target or i.rsplit("/", 1)[-1] == target), None)
                if native and isinstance(response, dict) and not response.get("error"):
                    _emit(project, session, offset, at, "agent_started", native_agent_id=native, call_id=call)
                    return
            _emit(project, session, offset, at, "tool_completed", call_id=call, tool_name=item.get("tool_name", "local_tool"))
    elif subtype == "agent_message":
        native = payload.get("author")
        if native not in actors or payload.get("recipient") != "/root":
            return
        content = payload.get("content")
        if not isinstance(content, list):
            return
        text = next((i.get("text", "") for i in content if isinstance(i, dict) and i.get("type") in {"text", "input_text", "output_text"}), "")
        # Only the host-authored envelope header, not the answer body, is used.
        header = text.split("Payload:", 1)[0][:512] if isinstance(text, str) else ""
        if re.match(r"\s*Message Type: FINAL_ANSWER(?:\r?\n|$)", header):
            _emit(project, session, offset, at, "agent_completed", native_agent_id=native)


def poll_one(project, key):
    state = observation.cursor(project, key)
    if not state:
        return 0
    source = _validate_source(project, state["session_id"], state["file_id"])
    offset = state.get("offset", 0)
    if not isinstance(offset, int) or offset < 0 or offset > source.stat().st_size:
        raise ValueError("관측 원본이 잘렸습니다. 기존 기록을 덮지 않습니다.")
    count = 0
    with source.open("rb") as stream:
        stream.seek(offset)
        for _ in range(MAX_BATCH):
            begin = stream.tell()
            line = stream.readline(MAX_LINE + 1)
            if not line or not line.endswith(b"\n"):
                if len(line) > MAX_LINE:
                    raise ValueError("호스트 관측 행 크기 제한을 초과했습니다.")
                break
            if len(line) > MAX_LINE:
                raise ValueError("호스트 관측 행 크기 제한을 초과했습니다.")
            record = json.loads(line)
            consume_record(project, state, record, begin)
            state["offset"] = stream.tell()
            count += 1
    if count:
        observation.cursor(project, key, state)
    return count


def poll_sources(project):
    if not _POLL_LOCK.acquire(blocking=False):
        return 0
    try:
        data = observation._read(observation._root(project))
        return sum(poll_one(project, key) for key in data["cursors"] if key.startswith("codex:") and data["cursors"][key].get("file_id"))
    finally:
        _POLL_LOCK.release()


def monitor(project, stop, health=None):
    from tools.native_inbox import drain
    while not stop.is_set():
        count, unavailable = 0, False
        for operation in (drain, poll_sources):
            try:
                count += operation(project)
            except (ValueError, OSError, TypeError, KeyError, RecursionError):
                # One damaged queued event must not stop another source.
                unavailable = True
        if health is not None:
            health.update(status="unavailable" if unavailable else "ready",
                last_error="네이티브 실행 기록 연결을 확인하세요. 보관된 이벤트는 유지합니다." if unavailable else None,
                events_read=count)
        stop.wait(0.75)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("attach", "focus", "delegate", "poll"))
    parser.add_argument("project")
    parser.add_argument("--session")
    parser.add_argument("--provider", choices=("codex", "claude"), default="codex")
    parser.add_argument("--task")
    parser.add_argument("--role")
    args = parser.parse_args(argv)
    session = args.session or ((os.environ.get("CODEX_THREAD_ID") or os.environ.get("CODEX_SESSION_ID")) if args.provider == "codex" else None)
    if not session and args.provider == "claude":
        active = [s["session_id"] for s in observation.read(args.project)["sessions"] if s["provider"] == "claude" and s["status"] in {"preparing", "running"}]
        if len(active) == 1:
            session = active[0]
    try:
        if args.action == "attach":
            result = attach(args.project, session)
        elif args.action == "poll":
            result = {"events_read": poll_sources(args.project)}
        else:
            function = observation.focus if args.action == "focus" else observation.prepare_delegation
            result = function(args.project, args.task, args.role, args.provider, session)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
