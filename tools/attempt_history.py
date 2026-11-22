"""Read-only, bounded attempt metadata from recorded run results and events.

This module never reads artifacts, native identities, prompts or raw tool logs.
It does not infer a failure cause: summaries come only from recorded notes.
"""
from collections import deque
from datetime import datetime, timezone
import re

from tools.sensitive_data import is_sensitive_path, redact_text, redact_value

MAX_RESULTS = 64
MAX_EVIDENCE = 64
MAX_SUMMARY = 600
RESULT_STATUSES = {"running", "blocked", "awaiting_decision", "succeeded", "failed", "skipped"}
RUN_ID = re.compile(r"RUN-\d{3,}\Z")
STEP_ID = re.compile(r"STEP-\d{3,}\Z")
HASH = re.compile(r"[0-9a-fA-F]{64}\Z")


def _summary(note):
    if not isinstance(note, str):
        return ""
    # The second pass treats complete JSON as text as well: dictionary *keys*
    # can contain provider tokens and are not scrubbed by recursive JSON values.
    return redact_value(redact_text(note))[:MAX_SUMMARY]


def _public_path(value):
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return False
    parts = value.split("/")
    if any(not p or p in {".", ".."} or p != p.rstrip(" .") for p in parts):
        return False
    from tools.workspace import _artifact_reference
    try:
        _artifact_reference(value)
        _artifact_reference(value.casefold())  # Windows control aliases are not public.
    except (TypeError, ValueError):
        return False
    return not is_sensitive_path(value) and redact_value(value) == value


def evidence_hashes(value):
    """Expose path/hash metadata only, with no filesystem or delivery capability."""
    if not isinstance(value, dict):
        return {}
    safe = {}
    for path, checksum in value.items():
        if isinstance(checksum, str) and HASH.fullmatch(checksum) and _public_path(path):
            safe[path] = checksum.lower()
            if len(safe) == MAX_EVIDENCE:
                break
    return safe


def _at(value):
    if not isinstance(value, str) or len(value) > 80:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return moment.astimezone(timezone.utc).isoformat() if moment.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _task_mapping(tasks):
    mapping, ambiguous = {}, set()
    for task in tasks:
        if not isinstance(task, dict):
            continue
        ref = task.get("run_id"), task.get("step_id")
        if not all(isinstance(value, str) for value in ref):
            continue
        declared = task.get("step_ref")
        if declared is not None and (not isinstance(declared, dict)
                or (declared.get("run_id"), declared.get("step_id")) != ref):
            continue  # Conflicting identities must never select a task/role.
        if ref in mapping:
            ambiguous.add(ref)
        else:
            mapping[ref] = task
    return {ref: task for ref, task in mapping.items() if ref not in ambiguous}


def project_steps(run_list, tasks, events):
    """Project every step; timestamps require an exact full per-step status match.

    Event sequence is the one-based position in the complete lifecycle event
    ledger. A missing timestamp does not invalidate a known event position.
    """
    mapping = _task_mapping(tasks)
    projections, matching = {}, {}
    for run in run_list:
        for step in run["steps"]:
            ref = run["id"], step["id"]
            task = mapping.get(ref, {})
            task_id = task.get("id")
            if not isinstance(task_id, str) or len(task_id) > 240 or redact_value(task_id) != task_id:
                task_id = None
            role = _summary(task.get("role"))[:120]
            attempts = step.get("attempts")
            attempts = attempts if type(attempts) is int and attempts >= 0 else None
            results = step.get("results", [])
            results = results if isinstance(results, list) else []
            public, started, valid = [], 0, True
            canonical = isinstance(ref[0], str) and RUN_ID.fullmatch(ref[0]) and isinstance(ref[1], str) and STEP_ID.fullmatch(ref[1])
            for index, record in enumerate(results):
                status = record.get("status") if isinstance(record, dict) else None
                if not isinstance(status, str) or status not in RESULT_STATUSES:
                    valid = False
                    continue
                if status == "running":
                    started += 1
                if not canonical or index < len(results) - MAX_RESULTS:
                    continue
                public.append({"id": ref[0] + ":" + ref[1] + ":" + str(index + 1),
                    "attempt": started or None, "status": status, "summary": _summary(record.get("notes")),
                    "at": None, "sequence": None, "evidence_hashes": evidence_hashes(record.get("evidence_hashes")),
                    "task_id": task_id, "role": role})
            projections[ref] = {"task_id": task_id, "role": role, "attempts": attempts,
                "result_history": public, "history_omitted": len(results) - len(public)}
            matching[ref] = {"results": results, "position": 0, "valid": valid, "tail": deque(maxlen=MAX_RESULTS)}
    for sequence, event in enumerate(events, 1):
        if not isinstance(event, dict) or event.get("action") != "run-result" or not isinstance(event.get("details"), dict):
            continue
        details = event["details"]
        ref = details.get("run_id"), details.get("step_id")
        if not all(isinstance(value, str) for value in ref) or ref not in matching:
            continue
        state = matching[ref]
        index = state["position"]
        records = state["results"]
        expected = records[index].get("status") if index < len(records) and isinstance(records[index], dict) else None
        if index >= len(records) or details.get("status") != expected:
            state["valid"] = False
        state["tail"].append((index + 1, sequence, _at(event.get("at"))))
        state["position"] += 1
    for ref, state in matching.items():
        if not state["valid"] or state["position"] != len(state["results"]):
            continue
        timestamps = {index: (sequence, at) for index, sequence, at in state["tail"]}
        for row in projections[ref]["result_history"]:
            index = int(row["id"].rsplit(":", 1)[1])
            row["sequence"], row["at"] = timestamps.get(index, (None, None))
    return projections
