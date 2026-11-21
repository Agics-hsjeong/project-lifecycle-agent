"""Automatic runner integration with synthetic providers, not real model calls.

The native Codex/Claude end-to-end run is separately performed by the owning
agent. These tests use real HTTP decisions, real files, verification processes,
and a persistent runner to verify orchestration without model/network costs.
"""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools import lifecycle, runs, starter, workspace, workspace_server, workspace_runner


class ExecutorIntegrationTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="executor-independent-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temp.name), "project", "Synthetic executor integration", "product", profile="api"))
        (self.root / "docs/contract.md").write_text("Owner contract version one.\n", encoding="utf-8")
        (self.root / "src").mkdir()
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "requirements": [
            {"source": "Synthetic fixture", "description": "Owner policy", "acceptance_criteria": "Owner policy is verified", "priority": "must", "status": "proposed"}]})
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "architect", "owner": "Synthetic author", "status": "done", "next_action": "Review owner contract", "requirement_ids": "REQ-001", "evidence_refs": "docs/contract.md"},
            {"role": "backend", "owner": "Synthetic worker", "status": "todo", "next_action": "Implement owner policy", "requirement_ids": "REQ-001"},
            {"role": "qa", "owner": "Synthetic reviewer", "status": "todo", "next_action": "Verify owner policy", "requirement_ids": "REQ-001"}]})
        self.plan = [
            {"id": "TASK-001", "title": "Review contract", "stage": "planning", "execution": {
                "instruction": "Revise only the contract according to the human feedback.",
                "allowed_paths": ["docs/contract.md"], "expected_outputs": ["docs/contract.md"],
                "verification_commands": [[sys.executable, "-c", "from pathlib import Path; assert 'Owner' in Path('docs/contract.md').read_text()"]]}},
            {"id": "TASK-002", "title": "Implement owner policy", "stage": "implementation", "depends_on": ["TASK-001"], "execution": {
                "instruction": "Implement the owner policy in src/policy.py.", "allowed_paths": ["src/policy.py"], "expected_outputs": ["src/policy.py"],
                "verification_commands": [[sys.executable, "-c", "from pathlib import Path; assert 'owner' in Path('src/policy.py').read_text()"]]}},
            {"id": "TASK-003", "title": "Independent QA", "stage": "verification", "depends_on": ["TASK-002"], "execution": {
                "instruction": "Independently verify src/policy.py and write a report.", "allowed_paths": ["reports/qa.md"], "expected_outputs": ["reports/qa.md"],
                "verification_commands": [[sys.executable, "-c", "from pathlib import Path; assert 'owner' in Path('src/policy.py').read_text(); assert Path('reports/qa.md').is_file()"]]}}]
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": self.plan,
            "gates": [{"id": "GATE-OWNER", "task_id": "TASK-001", "paths": ["docs/contract.md"]}]}, "synthetic-fixture")
        self.server = workspace_server.make_server(self.root, port=0)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()
        self.addCleanup(self.close_server)
        self.runners = []
        self.calls = []
        self.calls_lock = threading.Lock()
        self.failure_role = None
        self.hold_role = None
        self.forge_role = None
        self.provider_started = threading.Event()
        self.provider_release = threading.Event()

    def close_server(self):
        self.provider_release.set()
        for stop, thread, errors in self.runners:
            stop.set()
        for stop, thread, errors in self.runners:
            thread.join(timeout=5)
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=3)

    def rev(self):
        entries = lifecycle.events(self.root)
        return entries[-1]["revision"] if entries else None

    def request(self, path, body=None, principal="human"):
        token = self.server.human_token if principal == "human" else self.server.host_token
        headers = {"Authorization": "Bearer " + token}
        if body is not None:
            headers.update({"Content-Type": "application/json", "Origin": self.server.origin})
        req = Request(self.server.origin + path, data=json.dumps(body).encode() if body is not None else None, headers=headers)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as error:
            return error.code, json.load(error)

    def view(self):
        # The engine deliberately rejects a busy writer instead of deleting its
        # lock. A UI/host reader may retry this bounded, read-only operation.
        for _ in range(5):
            status, body = self.request("/api/snapshot")
            if status == 200:
                return body
            if status != 409 or "잠금" not in body.get("error", ""):
                break
            time.sleep(0.04)
        self.assertEqual(status, 200, body)
        return body

    def wait_until(self, predicate, timeout=8):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            for _, _, errors in self.runners:
                for error in errors:
                    if not isinstance(error, workspace_runner.RunnerAlreadyRunning):
                        summary = workspace.public_summary(str(error) or type(error).__name__)
                        summary = summary.replace(str(self.root), '<synthetic-project>').replace(str(self.temp.name), '<synthetic-temp>')
                        self.fail('Automatic executor stopped: ' + type(error).__name__ + ': ' + summary +
                                  ' [' + ', '.join(getattr(error, 'fixture_frames', [])) + ']')
            if predicate():
                return
            time.sleep(0.025)
        self.fail("Automatic executor did not reach the expected state")

    def decision_payload(self, decision="approve", action_id=None):
        snap = self.view()
        gate = next(g for g in snap["gates"] if g["id"] == "GATE-OWNER")
        return {"action_id": action_id or ("synthetic-" + decision + "-" + str(time.monotonic_ns())),
            "expected_revision": snap["revision"], "gate_id": gate["id"], "decision": decision,
            "feedback": "Owner policy must be explicit. Synthetic decision only.", "actor": "Synthetic human fixture",
            "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]}

    def decide(self, decision="approve"):
        payload = self.decision_payload(decision)
        for _ in range(20):
            status, body = self.request("/api/action", payload)
            if status == 200:
                return body
            self.assertEqual(status, 409, body)
            fresh = self.decision_payload(decision, payload["action_id"])
            self.assertEqual(fresh["target_hashes"], payload["target_hashes"])
            self.assertEqual(fresh["context_hash"], payload["context_hash"])
            payload["expected_revision"] = fresh["expected_revision"]
            time.sleep(0.025)
        self.fail("Synthetic human decision could not be stored")

    def fake_provider(self, project, task, role_contract, job_dir, *, provider, executable=None, timeout=None,
                      on_started=None, on_heartbeat=None, stop_event=None, **extra):
        """An explicit local test double that runs real configured checks."""
        import os
        role = role_contract["id"]
        with self.calls_lock:
            self.calls.append({"task_id": task["id"], "role": role})
        if on_started:
            on_started(os.getpid())
        root = Path(project)
        if self.hold_role == role:
            self.provider_started.set()
            while not self.provider_release.wait(0.025):
                if stop_event is not None and stop_event.is_set():
                    return {"success": False, "process_started": True, "error": "Synthetic stop request"}
        if self.forge_role == role:
            return {"success": True, "process_started": True, "summary": "Synthetic forged receipt",
                    "evidence_refs": task["execution"]["expected_outputs"], "verification": []}
        if role == "architect":
            (root / "docs/contract.md").write_text("Owner contract revised from synthetic human feedback.\n", encoding="utf-8")
        elif role == "backend":
            (root / "src/policy.py").write_text("POLICY = 'incorrect'\n" if self.failure_role == role else "POLICY = 'owner'\n", encoding="utf-8")
        elif role == "qa":
            (root / "reports/qa.md").write_text("Synthetic independent QA: owner policy verified.\n", encoding="utf-8")
        else:
            raise AssertionError("Unexpected automatic role: " + role)
        results = []
        for command in task["execution"].get("verification_commands", []):
            result = subprocess.run(command, cwd=root, capture_output=True, text=True, encoding="utf-8", timeout=5)
            results.append({"command": command, "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr})
        success = all(r["exit_code"] == 0 for r in results)
        return {"success": success, "exit_code": 0 if success else 1,
            "summary": "Synthetic provider with actual local verification", "evidence_refs": task["execution"]["expected_outputs"],
            "verification": results, "process_started": True, "provider_session_id": "synthetic-fixture",
            "error": "Synthetic verification failed" if not success else None}

    def start_runner(self, provider="codex"):
        stop, errors = threading.Event(), []
        def target():
            try:
                workspace_runner.run_forever(self.root, provider, stop_event=stop, poll_interval=0.1,
                    provider_options={"run_job": self.fake_provider, "job_root": str(Path(self.temp.name) / "private-jobs")})
            except Exception as error:
                error.fixture_frames = [Path(frame.filename).name + ':' + str(frame.lineno) + ':' + frame.name
                                        for frame in traceback.extract_tb(error.__traceback__)]
                errors.append(error)
        thread = threading.Thread(target=target, daemon=True)
        self.runners.append((stop, thread, errors))
        thread.start()
        return stop, thread, errors

    def test_http_approval_automatically_runs_backend_then_independent_qa(self):
        stop, thread, errors = self.start_runner()
        time.sleep(0.12)
        self.assertEqual(self.calls, [])
        self.decide()
        self.wait_until(lambda: [call["role"] for call in self.calls] == ["backend", "qa"] and
                        all(t["status"] == "done" for t in self.view()["tasks"] if t["id"] in {"TASK-002", "TASK-003"}))
        self.assertEqual(errors, [])
        self.assertEqual(starter.load_project(self.root)[1]["current_stage"], "discovery")
        self.assertEqual(starter.read_table(self.root, "reviews"), [])
        self.assertEqual(len(workspace.read(self.root)["decisions"]), 1)
        self.assertIsNone(self.view()["usage"]["tokens"])
        stop.set()
        thread.join(timeout=4)
        self.assertFalse(thread.is_alive())

    def test_revision_is_automatically_executed_but_waits_for_new_human_approval(self):
        stop, thread, errors = self.start_runner()
        self.decide("request_revision")
        self.wait_until(lambda: self.view()["gates"][0]["status"] == "pending" and len(self.calls) == 1)
        self.assertEqual(self.calls[0]["role"], "architect")
        time.sleep(0.12)
        self.assertEqual(len(self.calls), 1)
        self.assertFalse((self.root / "src/policy.py").exists())
        self.assertEqual(len(workspace.read(self.root)["decisions"]), 1)
        self.decide()
        self.wait_until(lambda: [c["role"] for c in self.calls] == ["architect", "backend", "qa"])
        self.assertEqual(errors, [])

    def test_failed_verification_blocks_followup_without_automatic_duplicate(self):
        self.failure_role = "backend"
        stop, thread, errors = self.start_runner()
        self.decide()
        self.wait_until(lambda: any(c["status"] == "failed" for c in self.view()["commands"]))
        time.sleep(0.15)
        self.assertEqual([c["role"] for c in self.calls], ["backend"])
        self.assertNotEqual(next(t for t in self.view()["tasks"] if t["id"] == "TASK-002")["status"], "done")
        self.assertFalse((self.root / "reports/qa.md").exists())
        self.assertEqual(errors, [])

    def test_second_runner_cannot_launch_the_same_project_again(self):
        first_stop, first, first_errors = self.start_runner()
        time.sleep(0.12)
        second_stop, second, second_errors = self.start_runner("claude")
        second.join(timeout=2)
        self.assertFalse(second.is_alive(), "Duplicate project executor must exit without claiming work")
        self.assertTrue(first.is_alive())
        self.decide()
        self.wait_until(lambda: [c["role"] for c in self.calls] == ["backend", "qa"])
        self.assertEqual(first_errors, [])

    def test_human_action_cannot_supply_runner_commands_or_change_provider(self):
        stop, thread, errors = self.start_runner()
        for field, value in (("provider", "claude"), ("executable", "evil.exe"), ("verification_commands", [["python", "evil.py"]])):
            with self.subTest(field=field):
                payload = self.decision_payload()
                payload[field] = value
                status, body = self.request("/api/action", payload)
                self.assertEqual(status, 400, body)
        status, body = self.request("/api/action", self.decision_payload(), principal="host")
        self.assertEqual(status, 403, body)
        self.assertEqual(workspace.read(self.root)["decisions"], [])
        self.assertEqual(self.calls, [])

    def test_execution_plan_rejects_control_paths_outside_outputs_and_shell_strings(self):
        cases = [
            {"instruction": "Invalid state edit", "allowed_paths": ["state"], "expected_outputs": ["state/fake.json"], "verification_commands": []},
            {"instruction": "Invalid outside output", "allowed_paths": ["src"], "expected_outputs": ["../outside.py"], "verification_commands": []},
            {"instruction": "Invalid undeclared output", "allowed_paths": ["src"], "expected_outputs": ["reports/other.md"], "verification_commands": []},
            {"instruction": "Invalid shell command", "allowed_paths": ["src"], "expected_outputs": ["src/policy.py"], "verification_commands": ["python test.py && echo success"]},
        ]
        for execution in cases:
            with self.subTest(execution=execution):
                before = self.rev()
                with self.assertRaises(ValueError):
                    workspace.configure(self.root, {"expected_revision": before, "tasks": [{"id": "TASK-002", "execution": execution}]})
                self.assertEqual(self.rev(), before)

    def test_approval_change_while_working_is_failed_without_stuck_running(self):
        self.hold_role = "backend"
        stop, thread, errors = self.start_runner()
        self.decide()
        self.assertTrue(self.provider_started.wait(5))
        (self.root / "docs/contract.md").write_text("Owner contract changed during work.\n", encoding="utf-8")
        self.provider_release.set()
        self.wait_until(lambda: any(c["status"] == "failed" for c in self.view()["commands"]))
        view = self.view()
        self.assertFalse(any(c["status"] == "running" for c in view["commands"]))
        self.assertEqual([c["role"] for c in self.calls], ["backend"])
        self.assertEqual(errors, [])
        self.assertTrue(thread.is_alive())

    def test_missing_actual_output_cannot_become_completed(self):
        self.forge_role = "backend"
        stop, thread, errors = self.start_runner()
        self.decide()
        self.wait_until(lambda: any(c["status"] == "failed" for c in self.view()["commands"]))
        self.assertFalse(any(c["status"] == "completed" for c in self.view()["commands"]))
        self.assertFalse((self.root / "src/policy.py").exists())
        self.assertEqual([c["role"] for c in self.calls], ["backend"])
        self.assertEqual(errors, [])

    def test_stop_during_work_does_not_replay_failed_job_after_restart(self):
        self.hold_role = "backend"
        stop, thread, errors = self.start_runner()
        self.decide()
        self.assertTrue(self.provider_started.wait(5))
        stop.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(any(c["status"] == "failed" for c in self.view()["commands"]))
        other_stop, other, other_errors = self.start_runner()
        time.sleep(0.15)
        self.assertTrue(other.is_alive())
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(other_errors, [])

    def test_korean_role_alias_is_resolved_and_claimed_automatically(self):
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"task_id": "TASK-002", "role": "백엔드"}]})
        stop, thread, errors = self.start_runner()
        self.decide()
        self.wait_until(lambda: [call["role"] for call in self.calls] == ["backend", "qa"])
        self.assertEqual(errors, [])

    def test_gate_change_during_mapped_run_records_failed_run_receipt(self):
        result = runs.create(self.root, {"expected_revision": self.rev(), "key": "synthetic-mapped-run", "title": "Synthetic mapped pipeline",
            "requirement_ids": ["REQ-001"], "steps": [
                {"id": "STEP-001", "title": "Contract", "depends_on": [], "inputs": ["docs/01-discovery.md"], "expected_outputs": ["docs/contract.md"]},
                {"id": "STEP-002", "title": "Implement", "depends_on": ["STEP-001"], "inputs": ["docs/contract.md"], "expected_outputs": ["src/policy.py"]},
                {"id": "STEP-003", "title": "Verify", "depends_on": ["STEP-002"], "inputs": ["src/policy.py"], "expected_outputs": ["reports/qa.md"]}]})
        run_id = result["run_id"]
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": "TASK-001", "step_ref": {"run_id": run_id, "step_id": "STEP-001"}},
            {"id": "TASK-002", "step_ref": {"run_id": run_id, "step_id": "STEP-002"}},
            {"id": "TASK-003", "step_ref": {"run_id": run_id, "step_id": "STEP-003"}}]})
        for status in ("running", "succeeded"):
            runs.result(self.root, {"expected_revision": self.rev(), "run_id": run_id, "step_id": "STEP-001", "key": "synthetic-contract-" + status,
                "status": status, "evidence_refs": ["docs/contract.md"] if status == "succeeded" else [], "notes": "Synthetic contract generation receipt"})
        self.hold_role = "backend"
        stop, thread, errors = self.start_runner()
        self.decide()
        self.assertTrue(self.provider_started.wait(5))
        (self.root / "docs/contract.md").write_text("Owner contract changed during mapped run.\n", encoding="utf-8")
        self.provider_release.set()
        self.wait_until(lambda: any(c["status"] == "failed" for c in self.view()["commands"]))
        raw = next(r for r in runs.read(self.root)["runs"] if r["id"] == run_id)
        step = next(s for s in raw["steps"] if s["id"] == "STEP-002")
        self.assertEqual(step["status"], "failed")
        self.assertEqual([c["role"] for c in self.calls], ["backend"])
        self.assertEqual(errors, [])
