"""Resident supervisor unit tests use explicit synthetic host adapters only."""
import contextlib
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import lifecycle, runs, starter, workspace, workspace_runner as runner
from tools.storage import json_bytes


class RunnerTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="resident-unit-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(self.base / "projects", "synthetic", "Synthetic resident", "product", profile="api"))
        (self.root / "docs/contract.md").write_text("# Owner policy draft\n", encoding="utf-8")
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "requirements": [
            {"source": "Synthetic source", "description": "Owner contract", "status": "proposed"}], "tasks": [
            {"role": "architect", "owner": "Synthetic architect", "status": "done", "next_action": "Contract", "evidence_refs": "docs/contract.md", "requirement_ids": "REQ-001"},
            {"role": "backend", "owner": "", "status": "todo", "next_action": "Implement", "requirement_ids": "REQ-001"},
            {"role": "qa", "owner": "", "status": "todo", "next_action": "Verify", "requirement_ids": "REQ-001"}]})
        self.policies = {
            "TASK-001": self.policy("docs/contract.md"),
            "TASK-002": self.policy("src/feature.py"),
            "TASK-003": self.policy("reports/qa.md"),
        }
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": "TASK-001", "stage": "planning", "execution": self.policies["TASK-001"]},
            {"id": "TASK-002", "stage": "implementation", "depends_on": ["TASK-001"], "execution": self.policies["TASK-002"]},
            {"id": "TASK-003", "stage": "verification", "depends_on": ["TASK-002"], "execution": self.policies["TASK-003"]}],
            "gates": [{"id": "API", "task_id": "TASK-001", "paths": ["docs/contract.md"]}]})
        self.calls = []

    def policy(self, path):
        return {"instruction": "Write a synthetic test output only", "allowed_paths": [path],
                "expected_outputs": [path], "verification_commands": [[sys.executable, "-c",
                    "from pathlib import Path; import ast; p=Path(" + repr(path) + "); assert p.is_file(); " +
                    ("ast.parse(p.read_text(encoding='utf-8'))" if path.endswith('.py') else "assert p.stat().st_size > 0")]]}

    def rev(self):
        history = lifecycle.events(self.root)
        return history[-1]["revision"] if history else None

    def decide(self, kind="approve"):
        action_id = "fixture-" + str(time.monotonic_ns())
        def action():
            snap = runner._snapshot(self.root)
            gate = snap["gates"][0]
            return workspace.decide(self.root, {"action_id": action_id, "expected_revision": snap["revision"],
                "gate_id": "API", "decision": kind, "feedback": "Change the contract; revised bytes are required", "actor": "Synthetic human fixture",
                "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]})
        return runner._retry(action)

    def fake(self, project, task, role, job_dir, **options):
        self.calls.append((task["id"], role["id"]))
        options["on_started"](os.getpid())
        outputs = task["execution"]["expected_outputs"]
        for relative in outputs:
            path = Path(project) / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(("VALUE = 2\n" if path.suffix == '.py' else "Synthetic actual output: " + role["id"] + "\n"), encoding="utf-8")
        # Explicit synthetic adapter seam: real checks and byte receipts, no OS isolation claim.
        checks = []
        for command in task["execution"]["verification_commands"]:
            checked = subprocess.run(command, cwd=project, capture_output=True, timeout=10)
            checks.append({"command": command, "exit_code": checked.returncode})
            if checked.returncode:
                raise AssertionError(checked.stderr.decode(errors="replace"))
        from tools.verification_contract import VerificationContract
        files = {name: (Path(project) / name).read_bytes() for name in outputs}
        contract = VerificationContract(files, task["execution"], task).receipt(files)
        return {"success": True, "summary": "Synthetic fixture output written", "evidence_refs": outputs,
                "process_started": True, "verification": checks, "verification_contract": contract, "exit_code": 0}

    def execute(self, function=None, max_jobs=2):
        return runner.run_forever(self.root, "codex", poll_interval=0.02,
            provider_options={"run_job": function or self.fake, "max_jobs": max_jobs, "job_root": self.base / "native"})

    def launch_waiting(self, function=None):
        stop = threading.Event()
        result, errors = {}, []
        self.background_errors = errors
        def work():
            try:
                result.update(runner.run_forever(self.root, "codex", stop_event=stop, poll_interval=0.02,
                    provider_options={"run_job": function or self.fake, "job_root": self.base / "native"}))
            except Exception as error:
                errors.append(error)
        thread = threading.Thread(target=work)
        thread.start()
        def close():
            stop.set()
            thread.join(5)
        self.addCleanup(close)
        return stop, thread, result, errors

    def wait_for(self, predicate, seconds=8):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if getattr(self, "background_errors", None):
                raise self.background_errors[0]
            try:
                if predicate():
                    return
            except ValueError as error:
                if not runner._contention(error):
                    raise
            time.sleep(0.02)
        self.fail("Supervisor state did not arrive before timeout")

    def test_human_gate_waits_then_automatically_runs_backend_and_qa(self):
        stop, thread, result, errors = self.launch_waiting()
        self.wait_for(lambda: workspace.snapshot(self.root).get("runners"))
        self.assertEqual(self.calls, [])
        self.decide()
        self.wait_for(lambda: len(self.calls) == 2)
        self.wait_for(lambda: all(q["status"] == "completed" for q in workspace.snapshot(self.root)["commands"]))
        stop.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(self.calls, [("TASK-002", "backend"), ("TASK-003", "qa")])
        self.assertEqual(result["completed"], 2)
        self.assertEqual(starter.read_table(self.root, "reviews"), [])
        self.assertEqual(starter.load_project(self.root)[1]["current_stage"], "discovery")
        self.assertIsNone(workspace.snapshot(self.root)["usage"]["tokens"])

    def test_startup_resumes_after_real_long_business_lock_and_finishes_two_jobs(self):
        from tools.storage import lock
        ready = threading.Event()
        def hold():
            with lock(self.root):
                ready.set()
                time.sleep(3.25)
        reader = threading.Thread(target=hold)
        reader.start()
        self.addCleanup(reader.join)
        self.assertTrue(ready.wait(5))
        stop, notices, result, errors = threading.Event(), [], {}, []
        waiting = threading.Event()
        def notice(payload):
            notices.append(payload)
            if payload["reason"] == "project_busy":
                waiting.set()
        def work():
            try:
                result.update(runner.run_forever(self.root, "codex", stop_event=stop, poll_interval=0.02,
                    provider_options={"run_job": self.fake, "max_jobs": 2, "job_root": self.base / "native", "on_startup_wait": notice}))
            except Exception as error:
                errors.append(error)
        worker = threading.Thread(target=work)
        worker.start()
        self.background_errors = errors
        def cleanup():
            stop.set()
            worker.join(5)
        self.addCleanup(cleanup)
        self.assertTrue(waiting.wait(1))
        self.wait_for(lambda: workspace.snapshot(self.root).get("runners"))
        self.wait_for(lambda: any(n["reason"] == "ready" for n in notices))
        self.assertEqual(self.calls, [])
        self.decide()
        self.wait_for(lambda: len(self.calls) == 2)
        self.wait_for(lambda: all(q["status"] == "completed" for q in workspace.snapshot(self.root)["commands"]))
        worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result["completed"], 2)
        self.assertEqual(self.calls, [("TASK-002", "backend"), ("TASK-003", "qa")])
        self.assertFalse((self.root / runner.LEASE).exists())

    def test_stop_during_startup_lock_wait_preserves_other_live_lease_and_lock(self):
        from tools.storage import lock
        with runner.RunnerLease(self.root, "codex") as owner:
            original_lease = owner.path.read_bytes()
            ready, release, waiting, stop = (threading.Event() for _ in range(4))
            result, errors = {}, []
            def hold():
                with lock(self.root):
                    ready.set()
                    release.wait(5)
            reader = threading.Thread(target=hold)
            reader.start()
            self.assertTrue(ready.wait(5))
            original_lock = (self.root / "state/write.lock").read_bytes()
            def work():
                try:
                    result.update(runner.run_forever(self.root, "codex", stop_event=stop,
                        provider_options={"run_job": self.fake, "job_root": self.base / "native",
                            "on_startup_wait": lambda event: waiting.set()}))
                except Exception as error:
                    errors.append(error)
            worker = threading.Thread(target=work)
            worker.start()
            try:
                self.assertTrue(waiting.wait(1))
                stop.set()
                worker.join(1)
                self.assertFalse(worker.is_alive())
                self.assertEqual(errors, [])
                self.assertTrue(result["stopped"])
                self.assertEqual(result["completed"], 0)
                self.assertEqual(owner.path.read_bytes(), original_lease)
                self.assertEqual((self.root / "state/write.lock").read_bytes(), original_lock)
                self.assertEqual(self.calls, [])
            finally:
                stop.set()
                release.set()
                worker.join(5)
                reader.join(5)

    def test_startup_does_not_retry_permission_errors(self):
        notices = []
        with patch.object(runner, "lock", side_effect=PermissionError("Synthetic permission denial")) as attempted:
            with self.assertRaises(PermissionError):
                runner.run_forever(self.root, "codex", provider_options={"run_job": self.fake,
                    "job_root": self.base / "native", "on_startup_wait": notices.append})
        self.assertEqual(attempted.call_count, 1)
        self.assertEqual(notices, [])
        self.assertFalse((self.root / runner.LEASE).exists())

    def test_startup_does_not_wait_for_another_live_runner(self):
        notices = []
        with runner.RunnerLease(self.root, "codex") as owner:
            previous = owner.path.read_bytes()
            with self.assertRaises(runner.RunnerAlreadyRunning):
                runner.run_forever(self.root, "codex", provider_options={"run_job": self.fake,
                    "job_root": self.base / "native", "on_startup_wait": notices.append})
            self.assertEqual(owner.path.read_bytes(), previous)
        self.assertEqual(notices, [])
        self.assertEqual(self.calls, [])

    def test_startup_does_not_retry_an_unfinished_journal(self):
        path = self.root / "state/pending.json"
        path.write_bytes(json_bytes({"schema_version": 1, "entries": []}))
        notices = []
        try:
            with self.assertRaisesRegex(ValueError, "미완료 변경"):
                runner.run_forever(self.root, "codex", provider_options={"run_job": self.fake,
                    "job_root": self.base / "native", "on_startup_wait": notices.append})
            self.assertEqual(notices, [])
            self.assertFalse((self.root / runner.LEASE).exists())
            self.assertTrue(path.is_file())
        finally:
            path.unlink()

    def test_already_stopped_startup_does_not_inspect_or_touch_other_lease(self):
        notices = []
        with runner.RunnerLease(self.root, "codex") as owner:
            previous = owner.path.read_bytes()
            stop = threading.Event()
            stop.set()
            result = runner.run_forever(self.root, "codex", stop_event=stop, provider_options={"run_job": self.fake,
                "job_root": self.base / "native", "on_startup_wait": notices.append})
            self.assertTrue(result["stopped"])
            self.assertEqual(result["completed"], 0)
            self.assertEqual(owner.path.read_bytes(), previous)
        self.assertEqual(notices, [])
        self.assertEqual(self.calls, [])

    def test_revision_is_written_then_pauses_for_new_human_approval(self):
        self.decide("request_revision")
        result = self.execute(max_jobs=1)
        self.assertEqual(result["completed"], 1)
        view = workspace.snapshot(self.root)
        self.assertEqual(view["gates"][0]["status"], "pending")
        self.assertEqual(view["gates"][0]["decisions"][-1]["decision"], "request_revision")
        self.assertEqual(self.calls[0][1], "architect")
        self.assertFalse(any(tid == "TASK-002" for tid, _ in self.calls))

    def test_failed_provider_does_not_auto_retry_or_start_qa(self):
        self.decide()
        def failing(project, task, role, job_dir, **options):
            self.calls.append((task["id"], role["id"]))
            options["on_started"](os.getpid())
            raise RuntimeError("Synthetic native failure")
        result = self.execute(failing, max_jobs=1)
        self.assertEqual(result["failed"], 1)
        view = workspace.snapshot(self.root)
        self.assertEqual(view["commands"][0]["status"], "failed")
        self.assertEqual(next(t for t in view["tasks"] if t["id"] == "TASK-003")["status"], "blocked")
        self.assertEqual(len(self.calls), 1)

    def test_success_without_start_or_receipts_cannot_complete(self):
        for mode in ("no-start", "no-evidence"):
            with self.subTest(mode=mode):
                # Each fixture has its own failed command; retry is explicit.
                if mode == "no-start":
                    self.decide()
                else:
                    view = workspace.snapshot(self.root)
                    command = view["commands"][0]
                    agent = next(a for a in view["agents"] if a["id"] == command["agent_id"])
                    workspace.host_event(self.root, {"expected_revision": view["revision"], "kind": "retry",
                        "agent_id": agent["id"], "provider": agent["provider"], "role": agent["role"],
                        "command_id": command["id"], "notes": "Synthetic explicit retry"})
                def invalid(project, task, role, job_dir, **options):
                    if mode == "no-evidence":
                        options["on_started"](os.getpid())
                    return {"success": True, "evidence_refs": [], "process_started": mode != "no-start"}
                self.assertEqual(self.execute(invalid, max_jobs=1)["failed"], 1)
                self.assertEqual(workspace.snapshot(self.root)["commands"][0]["status"], "failed")

    def test_live_supervisor_lock_is_never_replaced(self):
        with runner.RunnerLease(self.root, "codex") as owner:
            raw = owner.path.read_bytes()
            with self.assertRaises(runner.RunnerAlreadyRunning):
                with runner.RunnerLease(self.root, "claude"):
                    pass
            self.assertEqual(owner.path.read_bytes(), raw)
        self.assertFalse((self.root / runner.LEASE).exists())

    def test_dead_supervisor_running_job_is_failed_not_replayed(self):
        self.decide()
        q = workspace.snapshot(self.root)["commands"][0]
        identity = {"provider": "codex", "agent_id": "runner:RUNNER-dead:" + q["id"], "role": "backend"}
        command = runner._host(self.root, identity, "claim", task_id="TASK-002")["command"]
        runner._host(self.root, identity, "started", command_id=command["id"])
        (self.root / runner.LEASE).write_bytes(json_bytes({"pid": 2147483644, "child_pid": None,
            "runner_id": "RUNNER-dead", "provider": "codex"}))
        with patch.object(runner, "process_alive", side_effect=lambda pid: pid == os.getpid()):
            stop, thread, result, errors = self.launch_waiting()
            self.wait_for(lambda: workspace.snapshot(self.root)["commands"][0]["status"] == "failed")
            stop.set(); thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(self.calls, [])
        self.assertEqual(result["recovered"], [command["id"]])

    def test_surviving_child_keeps_dead_supervisor_lease(self):
        path = self.root / runner.LEASE
        path.write_bytes(json_bytes({"pid": 2147483644, "child_pid": os.getpid(),
            "runner_id": "RUNNER-dead", "provider": "codex"}))
        raw = path.read_bytes()
        with patch.object(runner, "process_alive", side_effect=lambda pid: pid == os.getpid()), self.assertRaises(runner.RunnerAlreadyRunning):
            with runner.RunnerLease(self.root, "claude"):
                pass
        self.assertEqual(path.read_bytes(), raw)

    def test_existing_run_engine_receipts_are_recorded_automatically(self):
        created = runs.create(self.root, {"expected_revision": self.rev(), "key": "synthetic-native-run", "title": "Synthetic native plan",
            "requirement_ids": ["REQ-001"], "steps": [
                {"id": "STEP-001", "title": "Contract", "depends_on": [], "inputs": ["docs/01-discovery.md"], "expected_outputs": ["docs/contract.md"]},
                {"id": "STEP-002", "title": "Implement", "depends_on": ["STEP-001"], "inputs": ["docs/contract.md"], "expected_outputs": ["src/feature.py"]},
                {"id": "STEP-003", "title": "Verify", "depends_on": ["STEP-002"], "inputs": ["src/feature.py"], "expected_outputs": ["reports/qa.md"]}]})
        run_id = created["run_id"]
        for status in ("running", "succeeded"):
            runs.result(self.root, {"expected_revision": self.rev(), "run_id": run_id, "step_id": "STEP-001", "key": "fixture-" + status,
                "status": status, "evidence_refs": ["docs/contract.md"] if status == "succeeded" else [], "notes": "Synthetic contract receipt"})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": "TASK-001", "step_ref": {"run_id": run_id, "step_id": "STEP-001"}},
            {"id": "TASK-002", "step_ref": {"run_id": run_id, "step_id": "STEP-002"}},
            {"id": "TASK-003", "step_ref": {"run_id": run_id, "step_id": "STEP-003"}}]})
        self.decide()
        result = self.execute()
        self.assertEqual(result["completed"], 2)
        observed = runs.inspect(self.root, run_id)["runs"][0]
        self.assertTrue(observed["complete"])
        self.assertEqual([step["attempts"] for step in observed["steps"]], [1, 1, 1])

    def test_job_logs_cannot_be_placed_inside_customer_project(self):
        with self.assertRaisesRegex(ValueError, "프로젝트 밖"):
            runner.run_forever(self.root, "codex", provider_options={"run_job": self.fake, "job_root": self.root / "native"})
        self.assertFalse((self.root / runner.LEASE).exists())


    def test_direct_process_interrupt_records_failure_before_stopping(self):
        self.decide()
        def interrupted(project, task, role, job_dir, **options):
            options["on_started"](os.getpid())
            raise KeyboardInterrupt("Synthetic explicit interruption")
        with self.assertRaises(KeyboardInterrupt):
            self.execute(interrupted, max_jobs=1)
        view = workspace.snapshot(self.root)
        self.assertEqual(view["commands"][0]["status"], "failed")
        self.assertFalse((self.root / runner.LEASE).exists())


    def test_existing_unowned_input_cannot_be_claimed_as_produced_evidence(self):
        self.decide()
        def unrelated(project, task, role, job_dir, **options):
            result = self.fake(project, task, role, job_dir, **options)
            result["evidence_refs"] = result["evidence_refs"] + ["docs/01-discovery.md"]
            return result
        result = self.execute(unrelated, max_jobs=1)
        self.assertEqual(result["failed"], 1)
        self.assertIn("담당 범위 밖", result["jobs"][0]["error"])

    def test_success_without_configured_verification_receipts_is_rejected(self):
        policy = {**self.policies["TASK-002"], "verification_commands": [[sys.executable, "-c", "print('synthetic validation')"]]}
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-002", "execution": policy}]})
        self.decide()
        def missing_checks(*args, **options):
            result = self.fake(*args, **options)
            result['verification'] = []
            return result
        result = self.execute(missing_checks, max_jobs=1)
        self.assertEqual(result["failed"], 1)
        self.assertIn("검증 명령", result["jobs"][0]["error"])

    def test_adapter_cannot_leave_a_live_process_and_start_the_next_job(self):
        self.decide()
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            def detached(project, task, role, job_dir, **options):
                self.calls.append((task["id"], role["id"]))
                options["on_started"](child.pid)
                output = Path(project) / task["execution"]["expected_outputs"][0]
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text("synthetic output\n", encoding="utf-8")
                return {"success": True, "process_started": True, "evidence_refs": [output.relative_to(project).as_posix()], "verification": []}
            with self.assertRaisesRegex(RuntimeError, "실행 중"):
                self.execute(detached)
            self.assertEqual(self.calls, [("TASK-002", "backend")])
            self.assertEqual(workspace.snapshot(self.root)["commands"][0]["status"], "failed")
            self.assertTrue((self.root / runner.LEASE).exists())
        finally:
            child.terminate()
            child.wait(timeout=5)


    def test_explicit_retry_retains_native_attempts_and_command_basename(self):
        self.decide()
        directories = []
        def staged(project, task, role, job_dir, **options):
            # Mirrors a native adapter refusing to overwrite an old clone.
            candidate = Path(job_dir) / "workspace"
            candidate.mkdir(exist_ok=False)
            (candidate / "native-attempt.txt").write_text("preserve this attempt", encoding="utf-8")
            directories.append(Path(job_dir))
            if len(directories) == 1:
                options["on_started"](os.getpid())
                raise RuntimeError("Synthetic first native attempt failed")
            return self.fake(project, task, role, job_dir, **options)
        first = self.execute(staged, max_jobs=1)
        self.assertEqual(first["failed"], 1)
        view = workspace.snapshot(self.root)
        command = view["commands"][0]
        agent = next(a for a in view["agents"] if a["id"] == command["agent_id"])
        workspace.host_event(self.root, {"expected_revision": view["revision"], "kind": "retry",
            "agent_id": agent["id"], "provider": agent["provider"], "role": agent["role"],
            "command_id": command["id"], "notes": "Synthetic native retry after fixing the actual cause"})
        second = self.execute(staged, max_jobs=1)
        self.assertEqual(second["completed"], 1)
        self.assertEqual(len(directories), 2)
        self.assertNotEqual(directories[0], directories[1])
        self.assertEqual(directories[0].name, command["id"])
        self.assertEqual(directories[1].name, command["id"])
        self.assertTrue((directories[0] / "workspace/native-attempt.txt").is_file())
        self.assertTrue((directories[1] / "workspace/native-attempt.txt").is_file())
        report = json.loads((self.root / f"reports/runner/{command['id']}/receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(report["attempt_id"], directories[1].parent.name)

    def test_run_result_keys_distinguish_repeated_native_attempts(self):
        created = runs.create(self.root, {"expected_revision": self.rev(), "key": "synthetic-repeat-run", "title": "Repeated attempt",
            "requirement_ids": ["REQ-001"], "steps": [
                {"id": "STEP-001", "title": "Contract", "depends_on": [], "inputs": ["docs/01-discovery.md"], "expected_outputs": ["docs/contract.md"]},
                {"id": "STEP-002", "title": "Implement", "depends_on": ["STEP-001"], "inputs": ["docs/contract.md"], "expected_outputs": ["src/feature.py"]}]})
        run_id = created["run_id"]
        for status in ("running", "succeeded"):
            runs.result(self.root, {"expected_revision": self.rev(), "run_id": run_id, "step_id": "STEP-001", "key": "fixture-" + status,
                "status": status, "evidence_refs": ["docs/contract.md"] if status == "succeeded" else [], "notes": "Synthetic contract receipt"})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": "TASK-001", "step_ref": {"run_id": run_id, "step_id": "STEP-001"}},
            {"id": "TASK-002", "step_ref": {"run_id": run_id, "step_id": "STEP-002"}}]})
        self.decide()
        def first_failure(project, task, role, job_dir, **options):
            options["on_started"](os.getpid())
            raise RuntimeError("Synthetic first run attempt failed")
        self.assertEqual(self.execute(first_failure, max_jobs=1)["failed"], 1)
        view = workspace.snapshot(self.root)
        command = view["commands"][0]
        agent = next(a for a in view["agents"] if a["id"] == command["agent_id"])
        workspace.host_event(self.root, {"expected_revision": view["revision"], "kind": "retry",
            "agent_id": agent["id"], "provider": agent["provider"], "role": agent["role"],
            "command_id": command["id"], "notes": "Synthetic explicit run retry"})
        self.assertEqual(self.execute(max_jobs=1)["completed"], 1)
        step = next(s for r in runs.inspect(self.root, run_id)["runs"] for s in r["steps"] if s["id"] == "STEP-002")
        self.assertEqual(step["attempts"], 2)
        self.assertTrue(step["valid"])
        starts = [entry for entry in step["results"] if entry["status"] == "running"]
        self.assertEqual(len(starts), 2)
        self.assertNotEqual(starts[0]["key"], starts[1]["key"])


    def test_failed_native_usage_and_explicit_retry_are_both_counted_once(self):
        self.decide()
        attempts = []
        def metered(project, task, role, job_dir, **options):
            attempts.append(Path(job_dir))
            if len(attempts) == 1:
                options["on_started"](os.getpid())
                return {"success": False, "process_started": True, "summary": "Synthetic verification failed",
                    "error": "Synthetic independent verification failed", "evidence_refs": [], "verification": [],
                    "provider_session_id": "synthetic-session-failed",
                    "usage": {"tokens": 17, "cost_usd": 0.01, "model": "synthetic-fixture", "source": "synthetic-meter"}}
            result = self.fake(project, task, role, job_dir, **options)
            result.update(provider_session_id="synthetic-session-success", usage={"tokens": 23, "cost_usd": 0.02,
                "model": "synthetic-fixture", "source": "synthetic-meter"})
            return result
        first = self.execute(metered, max_jobs=1)
        self.assertEqual(first["failed"], 1)
        view = workspace.snapshot(self.root)
        self.assertEqual(view["usage"]["tokens"], 17)
        self.assertAlmostEqual(view["usage"]["cost_usd"], 0.01)
        command = view["commands"][0]
        agent = next(a for a in view["agents"] if a["id"] == command["agent_id"])
        workspace.host_event(self.root, {"expected_revision": view["revision"], "kind": "retry",
            "agent_id": agent["id"], "provider": agent["provider"], "role": agent["role"],
            "command_id": command["id"], "notes": "Synthetic explicit retry after verification fix"})
        second = self.execute(metered, max_jobs=1)
        self.assertEqual(second["completed"], 1)
        view = workspace.snapshot(self.root)
        self.assertEqual(view["usage"]["tokens"], 40)
        self.assertAlmostEqual(view["usage"]["cost_usd"], 0.03)
        self.assertEqual(len(view["usage"]["records"]), 2)
        records = view["usage"]["records"]
        self.assertEqual([entry["outcome"] for entry in records], ["failed", "completed"])
        self.assertEqual([entry["provider_session_id"] for entry in records],
                         ["synthetic-session-failed", "synthetic-session-success"])
        self.assertNotEqual(records[0]["attempt_id"], records[1]["attempt_id"])
        failed = json.loads((attempts[0] / "supervisor-receipt.json").read_text(encoding="utf-8"))
        passed = json.loads((attempts[1] / "supervisor-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["usage"]["tokens"], 17)
        self.assertEqual(failed["provider_session_id"], "synthetic-session-failed")
        self.assertEqual(passed["status"], "completed")
        self.assertEqual(passed["usage"]["tokens"], 23)
        self.assertNotEqual(failed["attempt_id"], passed["attempt_id"])

    def test_receipt_failure_retains_real_usage_but_missing_cost_stays_unknown(self):
        self.decide()
        directory = []
        def bad_receipt(project, task, role, job_dir, **options):
            directory.append(Path(job_dir))
            result = self.fake(project, task, role, job_dir, **options)
            result["evidence_refs"] = ["docs/01-discovery.md"]
            result["usage"] = {"tokens": 9, "cost_usd": None, "source": "synthetic-native-meter"}
            return result
        result = self.execute(bad_receipt, max_jobs=1)
        self.assertEqual(result["failed"], 1)
        view = workspace.snapshot(self.root)
        self.assertEqual(view["usage"]["tokens"], 9)
        self.assertIsNone(view["usage"]["cost_usd"])
        report = json.loads((directory[0] / "supervisor-receipt.json").read_text(encoding="utf-8"))
        self.assertEqual(report["usage"]["tokens"], 9)
        self.assertIsNone(report["usage"]["cost_usd"])


    def test_setup_failure_closes_claim_and_explicit_retry_finishes_pipeline(self):
        self.decide()
        blocked = self.base / "native"
        blocked.write_text("Synthetic runtime path is a file", encoding="utf-8")
        first = self.execute(max_jobs=1)
        self.assertEqual(first["failed"], 1)
        self.assertEqual(self.calls, [])
        failed_view = workspace.snapshot(self.root)
        command = failed_view["commands"][0]
        self.assertEqual(command["status"], "failed")
        self.assertEqual(next(t for t in failed_view["tasks"] if t["id"] == "TASK-002")["status"], "failed")
        self.assertFalse((self.root / runner.LEASE).exists())
        self.assertTrue(any(a["kind"] == "failed" for a in failed_view["activities"]))
        blocked.unlink()
        agent = next(a for a in failed_view["agents"] if a["id"] == command["agent_id"])
        workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": "retry",
            "agent_id": agent["id"], "provider": agent["provider"], "role": agent["role"],
            "command_id": command["id"], "notes": "Synthetic runtime path repaired; explicit retry"})
        second = self.execute(max_jobs=2)
        self.assertEqual(second["completed"], 2)
        final = workspace.snapshot(self.root)
        self.assertTrue(all(t["status"] == "done" for t in final["tasks"]))
        self.assertTrue(all(c["status"] == "completed" for c in final["commands"]))
        self.assertEqual(self.calls, [("TASK-002", "backend"), ("TASK-003", "qa")])
        self.assertTrue(any(a["kind"] == "handoff" for a in final["activities"]))

    def test_crash_between_claim_and_execute_keeps_ownership_for_restart(self):
        self.decide()
        with patch.object(runner, "_execute", side_effect=RuntimeError("Synthetic supervisor interruption before execution")):
            with self.assertRaisesRegex(RuntimeError, "Synthetic supervisor interruption"):
                self.execute(max_jobs=1)
        saved = json.loads((self.root / runner.LEASE).read_text(encoding="utf-8"))
        command = workspace.snapshot(self.root)["commands"][0]
        self.assertEqual(command["status"], "claimed")
        self.assertEqual(saved["command_id"], command["id"])
        self.assertEqual(saved["task_id"], "TASK-002")
        saved["pid"] = 2147483644
        (self.root / runner.LEASE).write_bytes(json_bytes(saved))
        with patch.object(runner, "process_alive", side_effect=lambda pid: pid == os.getpid()):
            stop, thread, result, errors = self.launch_waiting()
            self.wait_for(lambda: workspace.snapshot(self.root)["commands"][0]["status"] == "failed")
            stop.set(); thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(self.calls, [])
        self.assertIn(command["id"], result["recovered"])

    def test_missing_lease_orphan_is_closed_only_with_dead_native_owner_evidence(self):
        self.decide()
        q = workspace.snapshot(self.root)["commands"][0]
        identity = {"provider": "codex", "agent_id": "runner:RUNNER-orphan:" + q["id"], "role": "backend"}
        command = runner._host(self.root, identity, "claim", task_id="TASK-002")["command"]
        workspace.runner_event(self.root, {"expected_revision": self.rev(), "runner_id": "RUNNER-orphan",
            "provider": "codex", "pid": 2147483644, "status": "failed", "task_id": "TASK-002"})
        self.assertFalse((self.root / runner.LEASE).exists())
        with patch.object(runner, "process_alive", side_effect=lambda pid: pid == os.getpid()):
            stop, thread, result, errors = self.launch_waiting()
            self.wait_for(lambda: workspace.snapshot(self.root)["commands"][0]["status"] == "failed")
            stop.set(); thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(self.calls, [])
        self.assertIn(command["id"], result["recovered"])

    def test_orphan_recovery_never_takes_a_live_or_unproven_owner(self):
        self.decide()
        q = workspace.snapshot(self.root)["commands"][0]
        identity = {"provider": "codex", "agent_id": "runner:RUNNER-active:" + q["id"], "role": "backend"}
        runner._host(self.root, identity, "claim", task_id="TASK-002")
        self.assertEqual(runner._recover_orphaned_claims(self.root), [])
        workspace.runner_event(self.root, {"expected_revision": self.rev(), "runner_id": "RUNNER-active",
            "provider": "codex", "pid": os.getpid(), "status": "waiting", "task_id": "TASK-002"})
        self.assertEqual(runner._recover_orphaned_claims(self.root), [])
        workspace.runner_event(self.root, {"expected_revision": self.rev(), "runner_id": "RUNNER-child",
            "provider": "codex", "pid": 2147483644, "status": "failed", "child_pid": os.getpid()})
        # Existing command still belongs to the live owner, never the new record.
        self.assertEqual(runner._recover_orphaned_claims(self.root), [])
        self.assertEqual(workspace.snapshot(self.root)["commands"][0]["status"], "claimed")


    def test_server_startup_handoff_recovers_without_stealing_live_owner(self):
        from types import SimpleNamespace
        from tools import workspace_server
        self.decide()
        server = SimpleNamespace(project=self.root, supervisor_lock=threading.Lock(),
            supervisor_state={"configured": True, "provider": "codex", "status": "starting", "attempts": 0,
                              "last_error": None, "next_retry_seconds": None})
        stop = threading.Event()
        options = {"run_job": self.fake, "job_root": self.base / "native", "max_jobs": 2}
        with runner.RunnerLease(self.root, "codex") as owner:
            original = owner.path.read_bytes()
            thread = threading.Thread(target=workspace_server._supervise,
                args=(server, "codex", runner.run_forever, stop, options,
                      (runner.RunnerAlreadyRunning,), (.2, .4, .8)))
            thread.start()
            self.addCleanup(lambda: (stop.set(), thread.join(5)))
            deadline = time.monotonic() + 3
            while server.supervisor_state["status"] != "retrying" and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(server.supervisor_state["status"], "retrying")
            self.assertEqual(owner.path.read_bytes(), original)
        try:
            thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(server.supervisor_state["status"], "stopped")
            self.assertGreaterEqual(server.supervisor_state["attempts"], 2)
            final = workspace.snapshot(self.root)
            self.assertTrue(all(t["status"] == "done" for t in final["tasks"]))
            self.assertEqual(self.calls, [("TASK-002", "backend"), ("TASK-003", "qa")])
            self.assertFalse((self.root / runner.LEASE).exists())
        finally:
            stop.set(); thread.join(5)

    def test_verified_contract_is_required_and_binds_task_plan_and_final_output(self):
        task = next(t for t in workspace.snapshot(self.root)['tasks'] if t['id']=='TASK-002')
        for mode in ('missing', 'task', 'plan', 'published', 'bytes'):
            with self.subTest(mode=mode):
                result = self.fake(self.root, task, {'id':'backend'}, self.base/'receipt-fixture', on_started=lambda pid: None)
                if mode=='missing':
                    result.pop('verification_contract')
                elif mode=='task':
                    result['verification_contract']['task_context_sha256']=None
                elif mode=='plan':
                    result['verification_contract']['execution_sha256']='0'*64
                elif mode=='published':
                    result['verification_contract']['published_output_sha256']='0'*64
                else:
                    (self.root/'src/feature.py').write_text('VALUE = 999\n')
                with self.assertRaises(ValueError):
                    runner._validate_receipts(self.root, task, result, 'CMD-synthetic')

    def test_final_output_mutation_between_validation_and_completion_is_rejected(self):
        self.decide()
        original = runner._validate_receipts
        def mutate(root, task, result, command_id):
            refs = original(root, task, result, command_id)
            (Path(root)/'src/feature.py').write_text('VALUE = 999\n')
            return refs
        with patch.object(runner, '_validate_receipts', side_effect=mutate):
            result=self.execute(max_jobs=1)
        self.assertEqual(result['failed'],1,result)
        self.assertIn('변경', result['jobs'][0]['error'])
        final=workspace.snapshot(self.root)
        self.assertEqual(final['commands'][0]['status'],'failed')
        self.assertEqual(final['commands'][0]['verification_summary']['status'],'failed')
        self.assertEqual(next(t for t in final['tasks'] if t['id']=='TASK-003')['status'],'blocked')

    def test_budget_stops_next_job_and_reports_waiting_until_explicit_stop(self):
        workspace.configure(self.root, {'expected_revision':self.rev(), 'policy':{'limits':{'max_tokens':5}}})
        self.decide()
        def metered(*args, **options):
            result=self.fake(*args, **options)
            result['usage']={'tokens':5, 'cost_usd':0.01, 'source':'synthetic-meter', 'coverage':'complete'}
            return result
        stop,thread,result,errors=self.launch_waiting(metered)
        self.wait_for(lambda: workspace.snapshot(self.root)['usage']['known_tokens']==5)
        self.wait_for(lambda: any('한도' in r.get('notes','') for r in workspace.snapshot(self.root)['runners']))
        self.assertEqual(self.calls,[('TASK-002','backend')])
        self.assertTrue(thread.is_alive())
        stop.set();thread.join(5)
        self.assertEqual(errors,[])
        self.assertEqual(result['completed'],1)

    def test_token_only_budget_does_not_wait_for_unreported_price(self):
        snapshot={'usage':{'known_tokens':20,'known_cost_usd':None,'unreported_attempts':1,
                           'tokens_unreported_attempts':0,'cost_unreported_attempts':1}}
        self.assertIsNone(runner._budget_reason(snapshot,{'max_tokens':100},0))
        self.assertIn('미집계',runner._budget_reason(snapshot,{'max_cost_usd':1},0))
        self.assertIn('토큰',runner._budget_reason(snapshot,{'max_tokens':20},0))

    def test_budget_reason_distinguishes_unknown_measurement_and_known_limits(self):
        self.assertIn('미집계',runner._budget_reason({'usage':{'unreported_attempts':1}}, {'max_tokens':100},0))
        self.assertIsNone(runner._budget_reason({'usage':{'unreported_attempts':1}}, {'max_tokens':100,'unknown_usage':'warn'},0))
        self.assertIn('시간',runner._budget_reason({}, {'max_seconds':1},1))
        self.assertIn('비용',runner._budget_reason({'usage':{'known_cost_usd':1}}, {'max_cost_usd':1},0))

    def test_qa_environment_failure_never_schedules_code_repair(self):
        self.decide()
        self.assertEqual(self.execute(max_jobs=1)['completed'],1)
        def unsupported(*args,**options):
            result=self.fake(*args,**options)
            result.update(success=False,failure_kind='environment_unavailable',error='Unsupported network-isolated runtime')
            return result
        with patch.object(workspace,'schedule_repair') as schedule:
            result=self.execute(unsupported,max_jobs=1)
        self.assertEqual(result['failed'],1,result)
        schedule.assert_not_called()
        final=workspace.snapshot(self.root)
        self.assertEqual(len(final['commands']),2)
        self.assertEqual(self.calls,[('TASK-002','backend'),('TASK-003','qa')])
        self.assertFalse(any(a['kind']=='repair_requested' for a in final['activities']))
