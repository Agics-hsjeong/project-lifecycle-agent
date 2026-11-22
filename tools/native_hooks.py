"""Content-minimising native-host telemetry adapter; never controls model decisions.

Only provider lifecycle envelopes and delegation tickets are retained. User text,
commands, model messages and tool results are deliberately never forwarded.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

MAX_INPUT = 1024 * 1024
SOURCE_ROOT = Path(__file__).resolve().parents[1]
_ID = re.compile(r"[A-Za-z0-9_./:@+\-]{1,200}\Z")
_TOKEN = re.compile(r"(?:IEUM_BIND:|ieum[-_])([0-9a-f]{24})(?![0-9a-f])")
_ROLE = {"po", "spec", "ux", "ia", "design-system", "designer", "ui", "ux-writer", "architect", "database", "backend", "frontend", "devops", "qa", "security", "writer", "technical-writer", "orchestrator", "reviewer", "proposal", "proposal-producer", "proposal-strategist"}
_TOOLS = {"Read", "Write", "Edit", "Glob", "Grep", "Bash", "PowerShell", "Agent", "Task", "spawn_agent", "send_message", "followup_task", "exec_command", "write_stdin", "apply_patch", "update_plan"}


def identifier(value):
    return value if isinstance(value, str) and _ID.fullmatch(value) else None


def find_project(cwd):
    """Resolve only nearest valid project, stopping at the session VCS boundary."""
    if not isinstance(cwd, str) or not cwd:
        return None
    try:
        start = Path(cwd).resolve(strict=True)
        if not start.is_dir():
            return None
        for folder in (start, *start.parents):
            # A template or installed plugin must never become a customer project.
            if folder == SOURCE_ROOT or any(part in {".codex-plugin", ".claude-plugin"} for part in folder.parts):
                return None
            if (folder / ".codex-plugin/plugin.json").is_file() or (folder / ".claude-plugin/plugin.json").is_file():
                return None
            config = folder / "project.json"
            if config.is_file() and not config.is_symlink() and config.stat().st_size <= MAX_INPUT:
                data = json.loads(config.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict) and data.get("schema_version") == 1 and identifier(data.get("project_id")) and data["project_id"] != "TEMPLATE":
                    return folder
                return None
            if (folder / ".git").exists():
                break
    except (OSError, ValueError, TypeError):
        return None
    return None


def role_for(agent_type):
    if not isinstance(agent_type, str):
        return "unassigned"
    candidate = agent_type.rsplit(":", 1)[-1]
    if candidate.startswith("ieum-"):
        candidate = candidate[5:]
    return candidate if candidate in _ROLE else "unassigned"


def delegation_token(tool_input):
    """A token is a lookup capability, never a TASK ID inferred from prose."""
    if not isinstance(tool_input, dict):
        return None
    values = []
    for key in ("description", "task_name"):
        value = tool_input.get(key)
        if isinstance(value, str) and len(value) <= 4096:
            values.extend(_TOKEN.findall(value))
    unique = set(values)
    return next(iter(unique)) if len(unique) == 1 else None


def normalize(payload, provider, *, now=None):
    """Return a minimal event from a trusted host hook envelope, never its text."""
    if provider not in {"codex", "claude"} or not isinstance(payload, dict):
        return None
    session = identifier(payload.get("session_id"))
    event_name = payload.get("hook_event_name")
    if not session or not isinstance(event_name, str):
        return None
    at = now or datetime.now(timezone.utc).isoformat()
    native_type = identifier(payload.get("agent_type"))
    agent_id = identifier(payload.get("agent_id"))
    event = {"session_id": session, "source": "host_hook", "at": at,
             "role": role_for(native_type), "parent_id": session}
    if native_type:
        event["native_type"] = native_type
    if agent_id:
        event["native_agent_id"] = agent_id
    call_id = identifier(payload.get("tool_use_id") or payload.get("call_id"))
    if call_id:
        event["call_id"] = call_id
    if event_name in {"SessionStart", "UserPromptSubmit"}:
        event.update(kind="session_started", summary="호스트 작업 준비")
    elif event_name in {"Stop", "SessionEnd", "Interrupt", "StopFailure"}:
        event.update(kind="session_ended" if event_name == "SessionEnd" else "session_idle", summary="호스트 응답 종료" if event_name == "Stop" else "호스트 실행 오류" if event_name == "StopFailure" else "호스트 실행 종료")
    elif event_name in {"SubagentStart", "SubagentStop"}:
        if not agent_id:
            return None
        event.update(kind="agent_started" if event_name == "SubagentStart" else "agent_completed",
                     summary="실제 역할 작업자 실행" if event_name == "SubagentStart" else "역할 작업자 실행 종료 · 산출물 검증 별도")
    elif event_name in {"PreToolUse", "PostToolUse", "PostToolUseFailure"}:
        tool = identifier(payload.get("tool_name"))
        if not tool:
            return None
        # A tool name is host metadata, but arbitrary plugin names can include
        # user-controlled identifiers. Keep known names or generic MCP/local.
        event["tool_name"] = tool if tool in _TOOLS else ("MCP" if tool.startswith("mcp__") else "local_tool")
        before = event_name == "PreToolUse"
        event.update(kind="tool_started" if before else "tool_completed", summary="도구 실행" if before else "도구 실행 종료")
        if tool in {"Agent", "Task", "spawn_agent"}:
            if not call_id:
                return None
            event.update(kind="delegation_started" if before else "delegation_failed" if event_name == "PostToolUseFailure" else "delegation_bound", summary="역할 위임 요청" if before else "역할 위임 실패" if event_name == "PostToolUseFailure" else "역할 위임 응답")
            tool_input = payload.get("tool_input")
            token = delegation_token(tool_input)
            if token:
                event["delegation_token"] = token
            if isinstance(tool_input, dict):
                requested_type = identifier(tool_input.get("subagent_type") or tool_input.get("agent_type"))
                if requested_type:
                    event.update(native_type=requested_type, role=role_for(requested_type))
            if not before:
                response = payload.get("tool_response")
                if isinstance(response, dict):
                    actual_id = identifier(response.get("agentId") or response.get("agent_id") or response.get("task_name"))
                    if actual_id:
                        event["native_agent_id"] = actual_id
                # JSON-looking strings and result text are intentionally ignored.
        if event_name == "PostToolUseFailure":
            event["summary"] = "도구 실행 실패 · 작업 결과 검증 별도"
    else:
        return None
    # Include host turn identity in the dedupe key, but never raw user content.
    identity = {**event, "turn_id": identifier(payload.get("turn_id")), "hook": event_name}
    event["event_id"] = "hook-" + hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:40]
    return event


def process(payload, provider, *, ingest=None, attach=None, enqueue=None):
    root = find_project(payload.get("cwd")) if isinstance(payload, dict) else None
    if root is None:
        return False
    event = normalize(payload, provider)
    if event is None:
        return False
    sys.path.insert(0, str(SOURCE_ROOT))
    from tools import native_inbox
    # Canonicalize once for BOTH deliveries. A crash after immediate ingest but
    # before acknowledgement must replay the identical immutable signature.
    event = native_inbox._normalise(provider, event)["event"]
    enqueue = enqueue or native_inbox.enqueue
    pending = Path(enqueue(root, provider, event))
    if ingest is None:
        from tools.native_observation import ingest
    deadline = time.monotonic() + 2
    delivered = False
    while True:
        try:
            ingest(root, provider, event)
            delivered = True
            break
        except Exception as exc:
            # Read-only snapshots and real engine writes share this lock. Retry
            # only its exact busy condition; never recover or remove another lock.
            busy = isinstance(exc, ValueError) and str(exc) == "다른 쓰기 작업의 잠금이 있습니다. 종료 여부를 확인한 뒤 recover를 사용하세요."
            if not busy or time.monotonic() >= deadline:
                break
            time.sleep(0.02)
    if delivered:
        try:
            pending.unlink(missing_ok=True)
        except OSError:
            # It is safe to redeliver this exact event after an ack I/O failure.
            delivered = False
    if not delivered:
        print("프로젝트이음: 관측 이벤트를 저장했습니다. 관제 연결 시 자동으로 다시 수신합니다.", file=sys.stderr)
    if provider == "codex" and payload.get("hook_event_name") in {"SessionStart", "UserPromptSubmit"}:
        try:
            if attach is None:
                from tools.native_bridge import attach
            # The bridge validates the exact native session and project envelope;
            # this string is never executed or used to search unrelated sessions.
            transcript = payload.get("transcript_path")
            attach(root, event["session_id"], transcript_path=transcript if isinstance(transcript, str) else None)
        except Exception:
            # A missing/unsupported rollout does not undo official hook evidence.
            print("프로젝트이음: 현재 Codex 세션의 보조 관측 연결을 확인하세요.", file=sys.stderr)
    return True


def main(argv=None, *, stream=None):
    parser = argparse.ArgumentParser(description="Minimal native-host observer hook")
    parser.add_argument("--provider", choices=("codex", "claude"), required=True)
    try:
        args = parser.parse_args(argv)
        raw = (stream or sys.stdin.buffer).read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("hook payload too large")
        payload = json.loads(raw)
        process(payload, args.provider)
    except (Exception, SystemExit):
        # Telemetry cannot grant permission or block real work. Never print the
        # exception because it could contain command arguments or credentials.
        print("프로젝트이음: 실행 관측을 기록하지 못했습니다. 실제 작업 권한과 결과는 변경하지 않습니다.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
