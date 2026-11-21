"""Resident, scoped work-queue supervisor for authenticated native host CLIs.

The supervisor owns project records. Native workers receive a role contract and
an explicit execution policy, and cannot turn their result into human approval.
"""
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid

from tools import lifecycle, role_agents, runs, starter, workspace
from tools.storage import atomic_write, confined, consistent_read, json_bytes, lock, process_alive

LEASE = "state/workspace-runner.lock"
PROVIDERS = {"codex", "claude"}


def _contention(error):
    return isinstance(error, ValueError) and isinstance(error.__cause__, FileExistsError)


def _retry(function, seconds=3.0):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return function()
        except ValueError as error:
            revision_changed = isinstance(error, workspace.Conflict) and "진행 기록이 변경" in str(error)
            if not (_contention(error) or revision_changed) or time.monotonic() >= deadline:
                raise
            time.sleep(0.04)


def _snapshot(root):
    return _retry(lambda: workspace.snapshot(root))


@consistent_read
def _task_blockers(root, tasks, identifier):
    """Read gate/context dependencies under one managed-state read lock."""
    return workspace.blockers(root, workspace.read(root), tasks, identifier)


def _host(root, identity, kind, **fields):
    def action():
        payload = {**identity, "kind": kind, "expected_revision": _snapshot(root)["revision"], **fields}
        return workspace.host_event(root, payload)
    return _retry(action)


def _telemetry(root, runner, status, **fields):
    def action():
        return workspace.runner_event(root, {"expected_revision": _snapshot(root)["revision"],
            "runner_id": runner["runner_id"], "provider": runner["provider"], "pid": os.getpid(),
            "status": status, **fields})
    return _retry(action)


def _activity(root, identity, task, command, kind, summary, **fields):
    payload = {**identity, 'task_id': task.get('id'), 'command_id': command.get('id'),
        'kind': kind, 'summary': summary, **fields}
    payload['role'] = role_agents.resolve(identity['role'])['id']
    if payload.get('peer_role'):
        payload['peer_role'] = role_agents.resolve(payload['peer_role'])['id']
    if 'event_id' in payload:
        payload['event_id'] = str(task.get('_attempt_id', 'runner')) + ':' + str(payload['event_id'])
    return _retry(lambda: workspace.activity_event(root, payload))


class RunnerAlreadyRunning(ValueError):
    pass


class StopRequested(RuntimeError):
    pass


class RunnerLease:
    """Single writer process ownership; never removes a live owner's lock."""
    def __init__(self, root, provider):
        self.root = root
        self.path = confined(root, LEASE)
        self.previous = None
        self.record = {"runner_id": "RUNNER-" + uuid.uuid4().hex, "provider": provider,
            "pid": os.getpid(), "child_pid": None, "created_at": starter.now()}

    def __enter__(self):
        def acquire():
            with lock(self.root):
                if self.path.exists():
                    previous = json.loads(self.path.read_text(encoding="utf-8"))
                    if not isinstance(previous, dict) or type(previous.get("pid")) is not int:
                        raise ValueError("실행기 소유권 파일을 확인해야 합니다. 자동 삭제하지 않습니다.")
                    child = previous.get("child_pid")
                    if process_alive(previous["pid"]) or (type(child) is int and child > 0 and process_alive(child)):
                        raise RunnerAlreadyRunning("이 프로젝트에는 이미 실행 중인 관제 실행기가 있습니다.")
                    self.previous = previous
                    self.path.unlink()
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(json_bytes(self.record))
        _retry(acquire)
        return self

    def update(self, **fields):
        current = json.loads(self.path.read_text(encoding="utf-8"))
        if current.get("runner_id") != self.record["runner_id"]:
            raise RuntimeError("실행기 소유권이 변경되었습니다.")
        self.record.update(fields, updated_at=starter.now())
        atomic_write(self.path, json_bytes(self.record))

    def __exit__(self, exc_type, exc_value, traceback):
        if not self.path.exists():
            return
        current = json.loads(self.path.read_text(encoding="utf-8"))
        if current.get("runner_id") != self.record["runner_id"]:
            return
        if exc_type is not None and current.get("command_id"):
            # Preserve interrupted ownership if its terminal record could not be
            # written. Recovery must inspect it before starting a replacement.
            return
        child = current.get("child_pid")
        if type(child) is int and child > 0 and child != os.getpid() and process_alive(child):
            # A surviving native process must not overlap with a new supervisor.
            return
        self.path.unlink()


def _policy(task):
    value = task.get("execution")
    if not isinstance(value, dict):
        raise ValueError("이 작업의 실행 범위와 검증 정책이 등록되지 않았습니다.")
    if not isinstance(value.get("instruction"), str) or not value["instruction"].strip():
        raise ValueError("실제 작업 지시가 필요합니다.")
    for key in ("allowed_paths", "expected_outputs"):
        refs = value.get(key)
        if not isinstance(refs, list) or not refs or any(not isinstance(p, str) or not p for p in refs):
            raise ValueError("허용 경로와 예상 산출물을 명시해야 합니다.")
    commands = value.get("verification_commands", [])
    if not isinstance(commands, list) or any(not isinstance(argv, list) or not argv or
            any(not isinstance(arg, str) or not arg for arg in argv) for argv in commands):
        raise ValueError("검증 명령은 명시한 argv 배열이어야 합니다.")
    return value


def _role(task):
    # Empty legacy roles enter the explicit orchestration contract, not a made-up
    # specialist. Actual planned roles use the existing catalog's aliases.
    return role_agents.resolve(task.get("role") or "orchestrator")


def _run_result(root, task, command_id, status, evidence_refs, notes):
    if not task.get("run_id"):
        return None
    result_key = "runner:" + command_id + ":" + task.get("_attempt_id", "recovery-" + uuid.uuid4().hex) + ":" + status
    def action():
        return runs.result(root, {"expected_revision": _snapshot(root)["revision"],
            "run_id": task["run_id"], "step_id": task["step_id"],
            "key": result_key, "status": status,
            "evidence_refs": evidence_refs, "notes": notes}, actor="workspace-runner")
    return _retry(action)


def _identity(provider, runner_id, command_id, role):
    return {"provider": provider, "agent_id": f"runner:{runner_id}:{command_id}", "role": role}


def _mark_failed(root, task, command, identity, notes, usage=None):
    notes = str(notes)[:9000] or "실제 실행 결과를 확인하지 못했습니다."
    if task.get("run_id"):
        current = _retry(lambda: runs.inspect(root, task["run_id"]))
        observed = next((step for run in current["runs"] for step in run["steps"]
                         if step["id"] == task["step_id"]), {})
        if observed.get("status") not in {"succeeded", "skipped", "failed"}:
            _run_result(root, task, command["id"], "failed", [], notes)
    fields = {"command_id": command["id"], "notes": notes}
    if usage is not None:
        fields["usage"] = usage
    return _host(root, identity, "failed", **fields)


def _recover_previous(root, lease):
    previous = lease.previous
    if not previous or not isinstance(previous.get("runner_id"), str):
        return []
    recovered = []
    prefix = "runner:" + previous["runner_id"] + ":"
    snapshot = _snapshot(root)
    agents = {a["id"]: a for a in snapshot.get("agents", [])}
    tasks = {t["id"]: t for t in snapshot["tasks"]}
    for command in snapshot.get("commands", []):
        agent_id = command.get("agent_id", "")
        if command.get("status") not in {"running", "claimed"} or not agent_id.startswith(prefix):
            continue
        agent = agents.get(agent_id, {})
        task = tasks.get(command["task_id"])
        if not task:
            raise ValueError("이전 실행기의 작업 연결을 확인해야 합니다.")
        identity = {"provider": agent.get("provider", previous["provider"]),
            "agent_id": agent_id, "role": agent.get("role") or task.get("role") or "orchestrator"}
        _mark_failed(root, task, command, identity,
            "이전 실행기가 중단되었습니다. 종료를 확인한 작업은 실패로 보존하며 자동 중복 실행하지 않습니다. 명시 재시도가 필요합니다.")
        recovered.append(command["id"])
    return recovered


def _recover_orphaned_claims(root):
    """Close legacy native claims only after both recorded owner PIDs are dead.

    Older versions could lose the lifetime lease between claim and process start.
    Runtime telemetry supplies ownership evidence; unknown/manual/live owners are
    deliberately left alone. Recovery records failure, never repeats model work.
    """
    view = _snapshot(root)
    owners = {item["runner_id"]: item for item in view.get("runners", [])}
    agents = {item["id"]: item for item in view.get("agents", [])}
    tasks = {item["id"]: item for item in view["tasks"]}
    recovered = []
    for command in view.get("commands", []):
        if command.get("status") != "claimed":
            continue
        agent_id = command.get("agent_id", "")
        if not agent_id.startswith("runner:RUNNER-"):
            continue
        runner_id = agent_id.split(":", 2)[1]
        owner = owners.get(runner_id)
        if not owner or type(owner.get("pid")) is not int or process_alive(owner["pid"]):
            continue
        child = owner.get("child_pid")
        if type(child) is int and child > 0 and process_alive(child):
            continue
        task = tasks.get(command["task_id"])
        if not task:
            raise ValueError("중단된 수신 작업의 연결을 확인해야 합니다.")
        agent = agents.get(agent_id, {})
        identity = {"provider": owner["provider"], "agent_id": agent_id,
                    "role": agent.get("role") or task.get("role") or "orchestrator"}
        _mark_failed(root, task, command, identity,
            "이전 실행기의 작업 준비가 중단됐습니다. 실제 소유 프로세스 종료를 확인했으며 자동 중복 실행하지 않습니다. 원인 해결 후 명시 재시도하세요.")
        recovered.append(command["id"])
    return recovered


def _validate_receipts(root, task, result, command_id):
    if result.get("success") is not True:
        raise RuntimeError(result.get("error") or result.get("summary") or "호스트 실행이 실패했습니다.")
    refs = result.get("evidence_refs")
    if not isinstance(refs, list) or not refs or any(not isinstance(p, str) for p in refs):
        raise ValueError("실제 완료 근거 경로가 필요합니다.")
    policy = _policy(task)
    expected = policy["expected_outputs"]
    if not set(expected).issubset(refs):
        raise ValueError("계획한 산출물의 완료 근거가 누락됐습니다.")
    verification_ref = f"reports/runner/{command_id}/verification.txt"
    for relative in refs:
        workspace.execution_path(root, relative)
        if relative != verification_ref and not any(relative == path.rstrip("/") or
                relative.startswith(path.rstrip("/") + "/") for path in policy["allowed_paths"]):
            raise ValueError("담당 범위 밖의 완료 근거입니다: " + relative)
    required = policy.get("verification_commands", [])
    results = result.get("verification", [])
    if not isinstance(results, list) or len(results) != len(required) or any(
            not isinstance(item, dict) or item.get("command") != argv or item.get("exit_code") != 0
            for item, argv in zip(results, required)):
        raise ValueError("명시한 독립 검증 명령의 성공 영수증이 필요합니다.")
    runs.hashes(root, refs)
    return list(dict.fromkeys(refs))


def _observed_usage(result, task):
    value = result.get("usage") if isinstance(result, dict) else None
    if value is None:
        return None
    if not isinstance(value, dict):
        return value  # The engine rejects malformed measurements, never guesses.
    return {**value, "attempt_id": task.get("_attempt_id"),
            "provider_session_id": result.get("provider_session_id")}


def _execute(root, provider, task, command, role, lease, job_dir, run_job, stop_event, options, identity=None):
    raw_role = task.get("role") or role["id"]
    identity = identity or _identity(provider, lease.record["runner_id"], command["id"], raw_role)
    started = False
    terminal_recorded = False
    result = None
    last_heartbeat = 0.0
    review_target = None

    def on_started(pid):
        nonlocal started, last_heartbeat
        if type(pid) is not int or pid <= 0:
            raise ValueError("실제 호스트 프로세스 ID가 필요합니다.")
        lease.update(child_pid=pid, command_id=command["id"], task_id=task["id"])
        if stop_event.is_set():
            raise StopRequested("실행기 중지 요청으로 작업을 시작하지 않습니다.")
        _run_result(root, task, command["id"], "running", [],
            f"{provider} 실제 프로세스 {pid} 시작 · 역할 {role['id']}")
        _host(root, identity, "started", command_id=command["id"], notes=f"{provider} 실제 프로세스 {pid} 시작 · 역할 {role['id']}")
        _telemetry(root, lease.record, "running", child_pid=pid, task_id=task["id"], notes=role["label"])
        started = True
        last_heartbeat = time.monotonic()
        _activity(root, identity, task, command, 'started', role['label'] + ' 작업 시작', status='running')

    def on_activity(event):
        if not isinstance(event, dict):
            return
        _activity(root, identity, task, command, event.get('kind', 'action'), event.get('summary', '작업 활동'),
            **{key: value for key, value in event.items() if key not in {'kind', 'summary'}})

    def on_heartbeat():
        nonlocal last_heartbeat
        if time.monotonic() - last_heartbeat < 2:
            return
        _host(root, identity, "heartbeat")
        fields = {"task_id": task["id"], "notes": role["label"]}
        if lease.record.get("child_pid"):
            fields["child_pid"] = lease.record["child_pid"]
        _telemetry(root, lease.record, "running" if started else "waiting", **fields)
        last_heartbeat = time.monotonic()

    try:
        _policy(task)
        job_dir.mkdir(parents=True, exist_ok=False)
        if role['id'] in {'qa', 'reviewer', 'security'}:
            view = _snapshot(root)
            producers = [t for t in view['tasks'] if t['id'] in task.get('depends_on', [])
                and t.get('role') not in {'qa', 'reviewer', 'security'} and (t.get('execution') or {}).get('expected_outputs')]
            if len(producers) == 1:
                producer = producers[0]
                review_target = {'id':producer['id'], 'role':producer.get('role'),
                    'hashes':runs.hashes(root, producer['execution']['expected_outputs'])}
        result = run_job(root, task, role, job_dir, provider=provider,
            executable=options.get("executable"), timeout=options.get("timeout", 900),
            verification_executable=options.get("verification_executable"),
            on_started=on_started, on_heartbeat=on_heartbeat, stop_event=stop_event, on_activity=on_activity)
        if not isinstance(result, dict):
            raise ValueError("호스트 결과 형식이 잘못되었습니다.")
        if not started or result.get("process_started") is False:
            raise RuntimeError(result.get("error") or "실제 프로세스 시작 영수증이 없습니다.")
        if stop_event.is_set():
            raise StopRequested("실행기 중지 요청으로 완료 처리하지 않습니다.")
        pid = lease.record.get("child_pid")
        if type(pid) is int and pid != os.getpid() and process_alive(pid):
            raise RuntimeError("호스트 프로세스가 아직 종료되지 않았습니다. 중복 작업을 시작하지 않습니다.")
        refs = _validate_receipts(root, task, result, command["id"])
        summary = str(result.get("summary") or "실제 출력과 검증 영수증을 확인했습니다.")[:8000]
        report_relative = f"reports/runner/{command['id']}/receipt.json"
        report = {"schema_version": 1, "provider": provider, "provider_session_id": result.get("provider_session_id"),
            "command_id": command["id"], "task_id": task["id"], "role": role["id"], "at": starter.now(),
            "attempt_id": task.get("_attempt_id"),
            "summary": summary, "evidence_hashes": runs.hashes(root, refs), "verification": result.get("verification", []),
            "usage": _observed_usage(result, task)}
        atomic_write(confined(root, report_relative), json_bytes(report))
        atomic_write(job_dir / "supervisor-receipt.json", json_bytes({**report, "status": "completed"}))
        refs.append(report_relative)
        _run_result(root, task, command["id"], "succeeded", refs, summary)
        fields = {"command_id": command["id"], "notes": summary, "evidence_refs": refs}
        if result.get("usage") is not None:
            fields["usage"] = _observed_usage(result, task)
        _host(root, identity, "completed", **fields)
        terminal_recorded = True
        _activity(root, identity, task, command, 'completed', role['label'] + ' 산출물 및 독립 검증 완료', status='completed')
        if review_target:
            def record_review():
                return workspace.review_event(root, {'expected_revision':_snapshot(root)['revision'],
                    'review_id':'REVIEW-'+command['id'], 'task_id':review_target['id'], 'agent_id':identity['agent_id'],
                    'provider':provider, 'role':role['id'], 'outcome':'passed',
                    'summary':'독립 역할 검토와 등록된 검증이 완료되었습니다.', 'evidence_refs':refs,
                    'target_hashes':review_target['hashes']})
            try:
                _retry(record_review)
                _activity(root, identity, task, command, 'review', '독립 검토 및 검증 통과', peer_role=review_target.get('role') or 'backend', status='success')
            except ValueError as review_error:
                _activity(root, identity, task, command, 'error', '검토 근거 연결 확인 필요: ' + str(review_error)[:400], status='attention')
        return {"command_id": command["id"], "task_id": task["id"], "success": True,
                "provider_session_id": result.get("provider_session_id")}
    except BaseException as error:
        observed = result if isinstance(result, dict) else {}
        failed_report = {"schema_version": 1, "status": "failed", "provider": provider,
            "provider_session_id": observed.get("provider_session_id"), "command_id": command["id"],
            "task_id": task["id"], "role": role["id"], "attempt_id": task.get("_attempt_id"), "at": starter.now(),
            "error": str(error)[:9000], "process_started": observed.get("process_started", started),
            "reported_evidence_refs": observed.get("evidence_refs", []),
            "verification": observed.get("verification", []), "usage": _observed_usage(observed, task)}
        try:
            atomic_write(job_dir / "supervisor-receipt.json", json_bytes(failed_report))
        except OSError:
            # The terminal engine record still takes priority when diagnostics
            # are unavailable (for example a full separate log volume).
            pass
        try:
            _mark_failed(root, task, command, identity, str(error), usage=_observed_usage(observed, task))
            terminal_recorded = True
        except Exception as record_error:
            raise RuntimeError("작업 실패 기록을 저장하지 못했습니다: " + str(record_error)) from error
        _activity(root, identity, task, command, 'failed', str(error)[:500], status='failed' if started else 'attention')
        repair = None
        if observed.get('failure_kind') == 'verification_failed' and role['id'] in {'qa', 'reviewer', 'security'} and not stop_event.is_set():
            def schedule():
                return workspace.schedule_repair(root, {'expected_revision': _snapshot(root)['revision'],
                    'failed_command_id': command['id'], 'feedback_refs': observed.get('feedback_refs', []),
                    'check_hashes': observed.get('check_hashes', {}), 'summary': '독립 QA 검증 실패를 담당 개발자에게 전달합니다.'})
            try:
                repair = _retry(schedule)
                _activity(root, identity, task, command, 'repair_requested', 'QA 반례를 담당 개발자에게 전달했습니다.',
                    peer_role=repair.get('producer_role', 'backend'))
            except ValueError as repair_error:
                _activity(root, identity, task, command, 'error', '자동 수정 중단: ' + str(repair_error)[:400], status='attention')
        if not isinstance(error, Exception):
            raise
        return {"command_id": command["id"], "task_id": task["id"], "success": False, "error": str(error), 'repair':repair}
    finally:
        # The adapter must reap its process before returning; a still-live child
        # retains the lifetime lock on exit and therefore blocks another runner.
        pid = lease.record.get("child_pid")
        if terminal_recorded and (pid is None or pid == os.getpid() or not process_alive(pid)):
            lease.update(child_pid=None, command_id=None, task_id=None)


def run_forever(folder, provider, stop_event=None, poll_interval=1.0, provider_options=None):
    """Consume approved work until stopped; wait at human gates automatically.

    ``provider_options.run_job`` is a dependency injection seam for isolated
    tests. Production calls the native host adapter, with no shell interpolation.
    """
    if provider not in PROVIDERS:
        raise ValueError("지원하는 실행기는 codex 또는 claude입니다.")
    if not isinstance(poll_interval, (int, float)) or not 0.02 <= poll_interval <= 60:
        raise ValueError("실행기 조회 간격은 0.02~60초여야 합니다.")
    root = starter.load_project(folder)[0]
    stop_event = stop_event or threading.Event()
    options = dict(provider_options or {})
    max_jobs = options.get("max_jobs")
    if max_jobs is not None and (type(max_jobs) is not int or max_jobs <= 0):
        raise ValueError("max_jobs는 양수여야 합니다.")
    run_job = options.get("run_job")
    if run_job is None:
        from tools.host_providers import run_job
    if not callable(run_job):
        raise ValueError("호스트 실행 어댑터가 필요합니다.")
    job_root = Path(options["job_root"]).resolve() if options.get("job_root") else confined(root.parent, ".ieum-runtime/" + root.name + "/jobs")
    if job_root.is_relative_to(root):
        raise ValueError("호스트 원시 로그는 고객 프로젝트 밖의 실행 폴더에 보관합니다.")
    output = {"provider": provider, "completed": 0, "failed": 0, "jobs": [], "recovered": []}
    with RunnerLease(root, provider) as lease:
        output["runner_id"] = lease.record["runner_id"]
        output["recovered"] = _recover_previous(root, lease)
        output["recovered"].extend(_recover_orphaned_claims(root))
        _telemetry(root, lease.record, "starting", notes="관제 실행기 시작")
        last_idle = 0.0
        try:
            while not stop_event.is_set():
                snapshot = _snapshot(root)
                tasks = {t["id"]: t for t in snapshot["tasks"]}
                queued = [q for q in snapshot.get("commands", []) if q["status"] == "queued"]
                selected = None
                waiting_note = "실행 가능한 다음 작업을 기다립니다."
                for command in queued:
                    task = tasks.get(command["task_id"])
                    if not task or _retry(lambda: _task_blockers(root, tasks, task["id"])):
                        continue
                    try:
                        _policy(task)
                        role = _role(task)
                    except ValueError as error:
                        waiting_note = f"{task['id']}: {error}"
                        continue
                    selected = (command, task, role)
                    break
                if selected is None:
                    if time.monotonic() - last_idle >= 10:
                        _telemetry(root, lease.record, "waiting", notes=waiting_note)
                        last_idle = time.monotonic()
                    stop_event.wait(poll_interval)
                    continue
                command, task, role = selected
                raw_role = task.get("role") or role["id"]
                identity = _identity(provider, lease.record["runner_id"], command["id"], raw_role)
                # Persist intent before claiming: a crash at any later point
                # retains enough ownership evidence for restart recovery.
                lease.update(child_pid=None, command_id=command["id"], task_id=task["id"])
                result = _host(root, identity, "claim", task_id=task["id"], notes="선행 조건을 충족한 작업 자동 수신")
                claimed = result.get("command")
                if not claimed:
                    lease.update(child_pid=None, command_id=None, task_id=None)
                    stop_event.wait(poll_interval)
                    continue
                command = claimed
                task = {**(result.get("task") or task), "_command_id": claimed["id"]}
                lease.update(child_pid=None, command_id=command["id"], task_id=task["id"])
                attempt_id = "attempt-" + uuid.uuid4().hex
                task = {**task, "_attempt_id": attempt_id}
                job_dir = job_root / attempt_id / command["id"]
                outcome = _execute(root, provider, task, claimed, role, lease, job_dir, run_job, stop_event, options, identity)
                output["jobs"].append(outcome)
                if outcome['success']:
                    view = _snapshot(root)
                    downstream = [t for t in view['tasks'] if task['id'] in t.get('depends_on', [])]
                    for next_task in downstream:
                        _activity(root, identity, task, command, 'handoff', role['label'] + ' 결과를 다음 역할에 전달', peer_role=next_task.get('role') or 'orchestrator')
                child = lease.record.get("child_pid")
                if type(child) is int and child != os.getpid() and process_alive(child):
                    raise RuntimeError("이전 호스트 프로세스가 실행 중이므로 다음 작업을 차단합니다.")
                output["completed" if outcome["success"] else "failed"] += 1
                last_idle = 0.0
                if max_jobs is not None and len(output["jobs"]) >= max_jobs:
                    break
            _telemetry(root, lease.record, "stopping", notes="관제 실행기 종료 요청 처리")
            _telemetry(root, lease.record, "stopped", notes="관제 실행기 종료")
        except BaseException as error:
            try:
                _telemetry(root, lease.record, "failed", notes=str(error)[:9000])
            except Exception:
                pass
            raise
    return output
