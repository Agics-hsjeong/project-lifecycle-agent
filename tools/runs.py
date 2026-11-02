"""Resumable host-executed work. Records receipts; never runs arbitrary commands."""
import json
import re
from tools.storage import commit, confined, consistent_read, digest, json_bytes, lock
from tools.intake import hash_file

STORE = "state/runs.json"
STATES = {"planned", "running", "blocked", "awaiting_decision", "succeeded", "failed", "skipped"}


def read(root):
    path = confined(root, STORE)
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": 1, "runs": []}
    if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("runs"), list):
        raise ValueError("실행 기록 구조가 잘못되었습니다.")
    return value


def hashes(root, refs):
    if not isinstance(refs, list) or any(not isinstance(ref, str) for ref in refs):
        raise ValueError("실제 입력·출력·근거 경로 목록이 필요합니다.")
    result = {}
    paths = set()
    for ref in refs:
        if ref in {"", ".", "./"}:
            raise ValueError("프로젝트 전체 대신 구체적인 입력·출력 경로를 지정하세요.")
        path = confined(root, ref)
        if path.relative_to(root).parts[0] == "state":
            raise ValueError("실행 제어 파일을 작업 입력·완료 근거로 사용하지 않습니다.")
        if path.is_file():
            paths.add(path)
        elif path.is_dir() and path.relative_to(root).parts[0] != "state":
            paths.update(p for p in path.rglob("*") if p.is_file() and not set(p.parts).intersection({"__pycache__", ".git", "node_modules", ".venv"}))
        else:
            raise ValueError(f"실제 파일·코드 폴더가 아닙니다: {ref}")
    if len(paths) > 10000 or sum(p.stat().st_size for p in paths) > 64 * 1024 * 1024:
        raise ValueError("작업 근거 크기 제한 초과")
    for path in sorted(paths):
        relative = path.relative_to(root).as_posix()
        if path.stat().st_size == 0:
            raise ValueError(f"빈 파일은 완료 근거로 쓸 수 없습니다: {relative}")
        result[relative] = hash_file(confined(root, relative))
    return result


def unchanged(root, step):
    if step["status"] not in {"succeeded", "skipped"}:
        return False
    try:
        return hashes(root, step["inputs"]) == step.get("input_hashes") and hashes(root, list(step["receipt_hashes"])) == step["receipt_hashes"]
    except (ValueError, OSError):
        return False


def valid_step(root, run, step, visited=None):
    visited = set() if visited is None else visited
    if step["id"] in visited or not unchanged(root, step):
        return False
    visited = visited | {step["id"]}
    parents = [s for s in run["steps"] if s["id"] in step["depends_on"]]
    return all(valid_step(root, run, parent, visited) for parent in parents)


def require_revision(root, payload):
    from tools import lifecycle
    events = lifecycle.events(root)
    if "expected_revision" not in payload or payload["expected_revision"] != (events[-1]["revision"] if events else None):
        raise ValueError("실행 기록이 변경되었습니다. inspect 후 다시 작성하세요.")


def save(root, store, action, actor, details):
    from tools import lifecycle
    changes = {STORE: json_bytes(store)}
    revision = lifecycle.add_event(root, changes, action, actor, details)
    commit(root, changes)
    return revision


def create(folder, payload, actor="agent"):
    from tools import starter, lifecycle
    root, _, tables = lifecycle.bundle(folder)
    if not isinstance(payload, dict) or set(payload) != {"expected_revision", "key", "title", "requirement_ids", "steps"}:
        raise ValueError("실행 계획의 필드가 잘못되었습니다.")
    if not all(isinstance(payload[k], str) and payload[k].strip() for k in ("key", "title")):
        raise ValueError("실행 key와 title이 필요합니다.")
    signature = digest(json_bytes({k: v for k, v in payload.items() if k != "expected_revision"}))
    with lock(root):
        store = read(root)
        prior = next((r for r in store["runs"] if r["key"] == payload["key"]), None)
        if prior:
            if prior["signature"] != signature:
                raise ValueError("같은 key로 다른 작업을 만들 수 없습니다.")
            return {"run_id": prior["id"], "unchanged": True}
        require_revision(root, payload)
        tables = lifecycle.bundle(root)[2]
        ids = payload["requirement_ids"]
        if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or not set(ids).issubset({r["requirement_id"] for r in tables["requirements"]}):
            raise ValueError("실행 범위의 실제 요구사항 ID가 필요합니다.")
        if not isinstance(payload["steps"], list) or not 0 < len(payload["steps"]) <= 1000:
            raise ValueError("실행 단계가 필요합니다.")
        seen, steps = set(), []
        for step in payload["steps"]:
            if not isinstance(step, dict) or not {"id", "title", "depends_on", "inputs"}.issubset(step) or set(step) - {"id", "title", "depends_on", "inputs", "expected_outputs"}:
                raise ValueError("단계는 id/title/depends_on/inputs가 필요합니다.")
            identifier = step["id"]
            if not isinstance(identifier, str) or not re.fullmatch(r"STEP-\d{3,}", identifier) or identifier in seen or not isinstance(step["title"], str) or not step["title"].strip():
                raise ValueError("잘못된 실행 단계 ID·제목")
            depends = step["depends_on"]
            if not isinstance(depends, list) or any(not isinstance(i, str) for i in depends) or not set(depends).issubset(seen):
                raise ValueError("의존 단계는 앞서 정의한 단계여야 합니다.")
            if not step["inputs"]:
                raise ValueError("단계별 실제 입력 경로가 필요합니다.")
            if not isinstance(step["inputs"], list) or any(not isinstance(ref, str) for ref in step["inputs"]):
                raise ValueError("단계 입력은 경로 목록이어야 합니다.")
            outputs = step.get("expected_outputs", [])
            if not isinstance(outputs, list) or any(not isinstance(ref, str) for ref in outputs):
                raise ValueError("예상 출력은 경로 목록이어야 합니다.")
            for ref in outputs:
                target = confined(root, ref)
                if not target.relative_to(root).parts or target.relative_to(root).parts[0] == "state":
                    raise ValueError("제어 기록을 작업 출력으로 지정하지 않습니다.")
            future = {ref for previous in steps if previous["id"] in depends for ref in previous.get("expected_outputs", [])}
            for ref in step["inputs"]:
                path = confined(root, ref)
                if path.exists() or ref not in future:
                    hashes(root, [ref])
            seen.add(identifier)
            steps.append({**step, "status": "planned", "attempts": 0, "results": [], "receipt_hashes": {}})
        identifier = f"RUN-{len(store['runs']) + 1:03d}"
        store["runs"].append({"id": identifier, "key": payload["key"], "signature": signature,
                             "title": payload["title"], "requirement_ids": ids, "steps": steps})
        revision = save(root, store, "run-create", actor, {"run_id": identifier})
        return {"run_id": identifier, "revision": revision, "unchanged": False}


def result(folder, payload, actor="agent"):
    from tools import starter
    root, _ = starter.load_project(folder)
    fields = {"expected_revision", "run_id", "step_id", "key", "status", "evidence_refs", "notes"}
    if not isinstance(payload, dict) or set(payload) != fields or not isinstance(payload["status"], str) or payload["status"] not in STATES - {"planned"}:
        raise ValueError("실행 결과 필드·상태를 확인하세요.")
    if not all(isinstance(payload[k], str) and payload[k].strip() for k in ("key", "notes")):
        raise ValueError("재시도 key와 실제 결과·사유가 필요합니다.")
    signature = digest(json_bytes({k: v for k, v in payload.items() if k != "expected_revision"}))
    with lock(root):
        store = read(root)
        run = next((r for r in store["runs"] if r["id"] == payload["run_id"]), None)
        step = next((s for s in run["steps"] if s["id"] == payload["step_id"]), None) if run else None
        if step is None:
            raise ValueError("없는 실행·단계 ID입니다.")
        prior = next((r for r in step["results"] if r["key"] == payload["key"]), None)
        if prior:
            if prior["signature"] != signature:
                raise ValueError("같은 결과 key를 다른 결과로 덮을 수 없습니다.")
            return {"unchanged": True, "valid": valid_step(root, run, step), "status": step["status"]}
        require_revision(root, payload)
        status = payload["status"]
        dependencies = [s for s in run["steps"] if s["id"] in step["depends_on"]]
        if status in {"running", "succeeded"} and not all(valid_step(root, run, s) for s in dependencies):
            raise ValueError("선행 작업의 결과가 없거나 변경되었습니다.")
        if step["status"] in {"succeeded", "skipped"} and valid_step(root, run, step):
            raise ValueError("완료 작업을 중복 실행하지 않습니다. 새 변경은 새 실행으로 계획하세요.")
        receipts = hashes(root, payload["evidence_refs"])
        if status == "running":
            if step["status"] == "running":
                raise ValueError("이미 실행 중입니다. 완료·실패·차단 결과를 먼저 기록하세요.")
            step["input_hashes"] = hashes(root, step["inputs"])
            step["attempts"] += 1
        elif status == "succeeded":
            if step["status"] != "running" or not receipts:
                raise ValueError("실제 실행 시작과 비어 있지 않은 완료 근거가 필요합니다.")
            if hashes(root, step["inputs"]) != step.get("input_hashes"):
                raise ValueError("실행 중 입력이 변경되었습니다. 실패·차단을 기록하고 다시 검증하세요.")
            if set(receipts).intersection(step["input_hashes"]):
                raise ValueError("입력 자체를 완료 근거로 제출하지 않습니다.")
            if not set(step.get("expected_outputs", [])).issubset(receipts):
                raise ValueError("계획한 출력 파일의 실제 완료 근거가 누락됐습니다.")
            step["receipt_hashes"] = receipts
        elif status == "skipped":
            if not receipts:
                raise ValueError("적용 제외의 실제 결정 근거가 필요합니다.")
            step["input_hashes"] = hashes(root, step["inputs"])
            step["receipt_hashes"] = receipts
        step["status"] = status
        step["results"].append({"key": payload["key"], "signature": signature, "status": status,
                                "evidence_hashes": receipts, "notes": payload["notes"], "actor": actor})
        revision = save(root, store, "run-result", actor, {"run_id": run["id"], "step_id": step["id"], "status": status})
        return {"revision": revision, "status": status, "attempts": step["attempts"], "unchanged": False}


@consistent_read
def inspect(folder, run_id=None):
    from tools import starter
    root, _ = starter.load_project(folder)
    output = []
    for run in read(root)["runs"]:
        if run_id is not None and run["id"] != run_id:
            continue
        steps = []
        for step in run["steps"]:
            valid = valid_step(root, run, step)
            deps_valid = all(valid_step(root, run, s) for s in run["steps"] if s["id"] in step["depends_on"])
            steps.append({**step, "valid": valid, "ready_to_start": deps_valid and step["status"] != "running" and not valid,
                          "effective_status": "stale" if step["status"] in {"succeeded", "skipped"} and not valid else step["status"]})
        output.append({**run, "steps": steps, "complete": all(s["valid"] for s in steps)})
    if run_id is not None and not output:
        raise ValueError("없는 실행 ID입니다.")
    return {"runs": output, "execution_owner": "active_host_agent", "notice": "실행 중인 단계는 자동 재실행하지 않습니다. 실제 작업 종료·실패 여부를 확인하세요."}
