"""Execution-plan metadata and truthful local-runner lifecycle contracts."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import lifecycle, runs, starter, workspace, workspace_server


class ExecutionMetadataTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="workspace-execution-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(starter.init_project(Path(self.temp.name), "project", "Execution fixture", "product"))
        (self.root / "src").mkdir()
        (self.root / "src/policy.py").write_text("LIMIT = 200\n", encoding="utf-8")
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "architect", "owner": "Synthetic author", "status": "done", "next_action": "API contract", "evidence_refs": "src/policy.py"},
            {"role": "backend", "owner": "Synthetic worker", "status": "todo", "next_action": "Implement contract"}]})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": "TASK-001", "stage": "planning"},
            {"id": "TASK-002", "stage": "implementation", "depends_on": ["TASK-001"]}],
            "gates": [{"id": "API", "task_id": "TASK-001", "paths": ["src/policy.py"]}]})

    def rev(self):
        events = lifecycle.events(self.root)
        return events[-1]["revision"] if events else None

    def plan(self):
        return {"instruction": "Implement the accepted owner policy and tests.", "allowed_paths": ["src", "tests", "reports"],
                "expected_outputs": ["src/handler.py", "tests/test_handler.py"],
                "verification_commands": [["python", "-m", "unittest", "discover", "-s", "tests", "-v"]]}

    def decide(self, decision="approve"):
        view = workspace.snapshot(self.root)
        gate = view["gates"][0]
        return workspace.decide(self.root, {"action_id": "synthetic-" + decision + "-" + os.urandom(6).hex(), "expected_revision": view["revision"],
            "gate_id": gate["id"], "decision": decision, "actor": "Synthetic human fixture", "feedback": "Reduce LIMIT to 100 and verify.",
            "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]})

    def runner(self, status="waiting", **fields):
        return workspace.runner_event(self.root, {"expected_revision": self.rev(), "runner_id": "RUNNER-fixture", "provider": "codex",
            "pid": os.getpid(), "status": status, **fields})

    def test_supervisor_reports_project_lock_wait_then_actual_start(self):
        import threading
        from types import SimpleNamespace
        from tools import workspace_server
        server = SimpleNamespace(project=self.root, supervisor_lock=threading.Lock(), supervisor_state={})
        received = []
        options = {"job_timeout": 12}
        def worker(project, provider, stop_event, provider_options):
            callback = provider_options["on_startup_wait"]
            callback({"status": "starting", "reason": "project_busy"})
            received.append(dict(server.supervisor_state))
            callback({"status": "running", "reason": "ready"})
            received.append(dict(server.supervisor_state))
        workspace_server._supervise(server, "codex", worker, threading.Event(), options)
        self.assertEqual(received[0]["status"], "starting")
        self.assertEqual(received[0]["last_error"], "프로젝트 기록 작업을 기다립니다.")
        self.assertEqual(received[1]["status"], "running")
        self.assertIsNone(received[1]["last_error"])
        self.assertEqual(server.supervisor_state["status"], "stopped")
        self.assertEqual(options, {"job_timeout": 12})

    def test_execution_plan_is_explicit_and_exported_without_csv_rewrite(self):
        csv_before = (self.root / "registers/tasks.csv").read_bytes()
        plan = self.plan()
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-002", "execution": plan}]})
        task = next(t for t in workspace.snapshot(self.root)["tasks"] if t["id"] == "TASK-002")
        self.assertEqual(task["execution"], plan)
        self.assertEqual(task["expected_outputs"], plan["expected_outputs"])
        self.assertEqual((self.root / "registers/tasks.csv").read_bytes(), csv_before)

    def test_rework_preserves_configured_policy_and_carries_human_instruction(self):
        plan = {"instruction": "Maintain the policy source", "allowed_paths": ["src"],
                "expected_outputs": ["src/policy.py"], "verification_commands": []}
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-001", "execution": plan}]})
        view = self.decide("request_revision")["snapshot"]
        task = next(t for t in view["tasks"] if t.get("rework"))
        self.assertEqual(task["execution"]["expected_outputs"], ["src/policy.py"])
        self.assertEqual(task["execution"]["allowed_paths"], ["src"])
        self.assertIn("Reduce LIMIT to 100", task["execution"]["instruction"])
        self.assertEqual(workspace.read(self.root)["tasks"]["TASK-001"]["execution"], plan)

    def test_legacy_rework_is_narrowly_scoped_to_actual_gate_files(self):
        view = self.decide("request_revision")["snapshot"]
        task = next(t for t in view["tasks"] if t.get("rework"))
        self.assertEqual(task["execution"]["allowed_paths"], ["src/policy.py"])
        self.assertEqual(task["execution"]["verification_commands"], [])

    def test_root_control_and_external_execution_paths_are_rejected_atomically(self):
        original = self.rev()
        for path in (".", "../outside", "state/workspace.json", "registers", "project.json", "HANDOFF.md", "inputs/rfp", ".agents", "reports/workspace-decisions"):
            with self.subTest(path=path):
                plan = self.plan()
                plan["allowed_paths"] = [path]
                with self.assertRaises(ValueError):
                    workspace.configure(self.root, {"expected_revision": original, "tasks": [{"id": "TASK-002", "execution": plan}]})
                self.assertEqual(self.rev(), original)

    def test_output_outside_scope_or_shell_verification_is_rejected(self):
        cases = [dict(self.plan(), expected_outputs=["outside/result.py"]),
                 dict(self.plan(), verification_commands=["python -m unittest"]),
                 dict(self.plan(), verification_commands=[["powershell", "-Command", "whoami"]]),
                 dict(self.plan(), verification_commands=[["python", "../outside.py"]]),
                 dict(self.plan(), verification_commands=[["python", "state/workspace.json"]]),
                 dict(self.plan(), expected_outputs=[])]
        for plan in cases:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-002", "execution": plan}]})

    def test_runtime_heartbeat_keeps_business_revision_and_human_context_stable(self):
        before = self.rev()
        gate_context = workspace.snapshot(self.root)["gates"][0]["context_hash"]
        self.runner("starting")
        self.runner("waiting")
        view = workspace.snapshot(self.root)
        self.assertEqual(view["revision"], before)
        self.assertEqual(view["gates"][0]["context_hash"], gate_context)
        self.assertTrue(view["host"]["runner_connected"])
        self.assertEqual(view["agents"], [])
        self.assertIsNone(view["usage"]["tokens"])
        self.runner("stopped")
        self.assertFalse(workspace.snapshot(self.root)["host"]["connected"])

    def test_worker_heartbeat_does_not_duplicate_business_history(self):
        before = self.rev()
        workspace.host_event(self.root, {"expected_revision": before, "kind": "heartbeat", "agent_id": "manual-fixture",
            "provider": "codex", "role": "backend"})
        self.assertEqual(self.rev(), before)
        self.assertEqual(workspace.snapshot(self.root)["agents"][0]["id"], "manual-fixture")

    def test_runner_identity_and_live_pid_cannot_be_replaced(self):
        self.runner()
        with self.assertRaises(ValueError):
            workspace.runner_event(self.root, {"expected_revision": self.rev(), "runner_id": "RUNNER-fixture", "provider": "claude",
                "pid": os.getpid(), "status": "waiting"})
        with self.assertRaises(ValueError):
            workspace.runner_event(self.root, {"expected_revision": self.rev(), "runner_id": "RUNNER-fixture", "provider": "codex",
                "pid": 999999999, "status": "waiting"})

    def test_failure_receipt_is_allowed_when_approved_input_changed_during_job(self):
        self.decide()
        base = {"agent_id": "fixture-worker", "provider": "codex", "role": "backend"}
        claim = workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "claim", "task_id": "TASK-002"})
        command_id = claim["command"]["id"]
        workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "started", "command_id": command_id})
        (self.root / "src/policy.py").write_text("LIMIT = 100\n", encoding="utf-8")
        result = workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "failed", "command_id": command_id,
            "notes": "Input changed; no completion was accepted"})
        self.assertEqual(result["command"]["status"], "failed")
        self.assertEqual(next(t for t in result["snapshot"]["tasks"] if t["id"] == "TASK-002")["status"], "failed")

    def test_local_server_stops_its_owned_runner_with_server_lifecycle(self):
        observed = {}
        entered = threading.Event()
        def run_forever(folder, provider, stop_event=None, provider_options=None):
            observed.update(folder=folder, provider=provider, options=provider_options)
            entered.set()
            observed["stopped"] = stop_event.wait(3)
        def loop():
            self.assertTrue(entered.wait(2))
            raise KeyboardInterrupt
        fake = SimpleNamespace(project=self.root, origin="http://127.0.0.1:12345", url="http://127.0.0.1:12345/#token=fixture-human",
                               host_token="fixture-host", serve_forever=loop, server_close=lambda:None)
        module = SimpleNamespace(run_forever=run_forever)
        with patch.dict(sys.modules, {"tools.workspace_runner": module}), patch.object(workspace_server, "make_server", return_value=fake), contextlib.redirect_stdout(io.StringIO()):
            workspace_server.serve(self.root, provider="codex", runner_options={"timeout": 30})
        self.assertEqual(observed["provider"], "codex")
        self.assertEqual({key: value for key, value in observed["options"].items() if key != "on_startup_wait"}, {"timeout": 30})
        self.assertTrue(callable(observed["options"]["on_startup_wait"]))
        self.assertTrue(observed["stopped"])


    def test_role_alias_and_metadata_override_match_native_role_claim(self):
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-002", "role": "백엔드"}]})
        self.decide()
        result = workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": "claim", "agent_id": "alias-worker",
            "provider": "codex", "role": "backend", "task_id": "TASK-002"})
        self.assertEqual(result["command"]["task_id"], "TASK-002")
        self.assertEqual(result["task"]["role"], "백엔드")

    def test_changed_execution_policy_after_claim_prevents_start(self):
        self.decide()
        base = {"agent_id": "contract-worker", "provider": "codex", "role": "backend"}
        claimed = workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "claim", "task_id": "TASK-002"})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-002", "execution": self.plan()}]})
        with self.assertRaises(workspace.Conflict):
            workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "started", "command_id": claimed["command"]["id"]})
        self.assertEqual(workspace.read(self.root)["queue"][0]["status"], "claimed")

    def test_changed_execution_policy_after_start_prevents_completion(self):
        plan = {"instruction": "Create the handler", "allowed_paths": ["src"], "expected_outputs": ["src/handler.py"], "verification_commands": []}
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-002", "execution": plan}]})
        self.decide()
        base = {"agent_id": "contract-worker", "provider": "codex", "role": "backend"}
        claimed = workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "claim", "task_id": "TASK-002"})
        command = claimed["command"]["id"]
        workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "started", "command_id": command})
        (self.root / "src/handler.py").write_text("VALUE = 1\n", encoding="utf-8")
        plan["instruction"] = "A different revised contract"
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-002", "execution": plan}]})
        with self.assertRaises(workspace.Conflict):
            workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "completed", "command_id": command,
                "evidence_refs": ["src/handler.py"]})
        failed = workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "failed", "command_id": command,
            "notes": "Job contract changed during execution"})
        self.assertEqual(failed["command"]["status"], "failed")

    def test_runner_stop_does_not_disconnect_an_unrelated_manual_host(self):
        self.runner()
        workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": "heartbeat", "agent_id": "manual-fixture", "provider": "codex", "role": "backend"})
        self.runner("stopped")
        manual = next(a for a in workspace.snapshot(self.root)["agents"] if a["id"] == "manual-fixture")
        self.assertEqual(manual["status"], "idle")


    def pipeline(self, with_run=False):
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "requirements": [
            {"source": "Synthetic contract", "description": "Owner policy", "status": "proposed"}],
            "tasks": [{"task_id": "TASK-001", "requirement_ids": "REQ-001"},
                      {"task_id": "TASK-002", "requirement_ids": "REQ-001"},
                      {"role": "qa", "status": "todo", "next_action": "Verify handler", "requirement_ids": "REQ-001"}]})
        policies = {"TASK-001": {"instruction": "Maintain policy", "allowed_paths": ["src/policy.py"], "expected_outputs": ["src/policy.py"], "verification_commands": []},
                    "TASK-002": {"instruction": "Implement policy", "allowed_paths": ["src/handler.py"], "expected_outputs": ["src/handler.py"], "verification_commands": []},
                    "TASK-003": {"instruction": "Verify policy", "allowed_paths": ["reports/qa.md"], "expected_outputs": ["reports/qa.md"], "verification_commands": []}}
        metas = [{"id": identifier, "execution": policy} for identifier, policy in policies.items()]
        metas[-1]["depends_on"] = ["TASK-002"]
        metas[-1]["stage"] = "verification"
        if with_run:
            run = runs.create(self.root, {"expected_revision": self.rev(), "key": "generation-fixture", "title": "Original pipeline", "requirement_ids": ["REQ-001"],
                "steps": [{"id": "STEP-001", "title": "Policy", "depends_on": [], "inputs": ["docs/01-discovery.md"], "expected_outputs": ["src/policy.py"]},
                          {"id": "STEP-002", "title": "Handler", "depends_on": ["STEP-001"], "inputs": ["src/policy.py"], "expected_outputs": ["src/handler.py"]},
                          {"id": "STEP-003", "title": "QA", "depends_on": ["STEP-002"], "inputs": ["src/handler.py"], "expected_outputs": ["reports/qa.md"]}]})
            for index, meta in enumerate(metas, 1):
                meta["step_ref"] = {"run_id": run["run_id"], "step_id": f"STEP-{index:03d}"}
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": metas})
        if with_run:
            for state, evidence in (("running", []), ("succeeded", ["src/policy.py"])):
                runs.result(self.root, {"expected_revision": self.rev(), "run_id": run["run_id"], "step_id": "STEP-001", "key": "policy-" + state,
                    "status": state, "evidence_refs": evidence, "notes": "Synthetic original author execution"})
        return policies

    def finish_job(self, identifier, *, text=None, fail=None, usage=None):
        view = workspace.snapshot(self.root)
        task = next(t for t in view["tasks"] if t["id"] == identifier)
        base = {"agent_id": "generation-worker-" + identifier, "provider": "codex", "role": task["role"]}
        claimed = workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "claim", "task_id": identifier})
        self.assertIsNotNone(claimed["command"], identifier)
        task = claimed["task"]
        command = claimed["command"]["id"]
        if task.get("run_id"):
            runs.result(self.root, {"expected_revision": self.rev(), "run_id": task["run_id"], "step_id": task["step_id"], "key": command + "-start",
                "status": "running", "evidence_refs": [], "notes": "Synthetic process started"})
        workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "started", "command_id": command})
        if fail:
            if task.get("run_id"):
                runs.result(self.root, {"expected_revision": self.rev(), "run_id": task["run_id"], "step_id": task["step_id"], "key": command + "-failed",
                    "status": "failed", "evidence_refs": [], "notes": fail})
            return workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "failed", "command_id": command, "notes": fail,
                **({"usage": usage} if usage is not None else {})})
        outputs = task["execution"]["expected_outputs"]
        for relative in outputs:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text or f"Synthetic generation {task.get('generation', 1)} output for {identifier}\n", encoding="utf-8")
        if task.get("run_id"):
            runs.result(self.root, {"expected_revision": self.rev(), "run_id": task["run_id"], "step_id": task["step_id"], "key": command + "-complete",
                "status": "succeeded", "evidence_refs": outputs, "notes": "Synthetic verified output"})
        return workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "completed", "command_id": command,
            "notes": "Current successful generation", "evidence_refs": outputs, **({"usage": usage} if usage is not None else {})})

    def test_completed_raw_pipeline_replans_backend_and_qa_after_revision(self):
        self.pipeline()
        self.decide()
        first_backend = self.finish_job("TASK-002")["command"]["id"]
        first_qa = self.finish_job("TASK-003")["command"]["id"]
        before = workspace.snapshot(self.root)
        self.assertTrue(all(t["status"] == "done" for t in before["tasks"]))
        self.decide("request_revision")
        stale = workspace.snapshot(self.root)
        self.assertEqual(next(t for t in stale["tasks"] if t["id"] == "TASK-002")["status"], "stale")
        rework = next(t for t in stale["tasks"] if t.get("rework"))
        self.finish_job(rework["id"], text="LIMIT = 100\n")
        self.decide()
        queue = workspace.snapshot(self.root)["commands"]
        self.assertTrue(any(c["task_id"] == "TASK-002" and c["status"] == "queued" and c["id"] != first_backend for c in queue))
        next_backend = self.finish_job("TASK-002")["command"]["id"]
        next_qa = self.finish_job("TASK-003")["command"]["id"]
        self.assertNotEqual(first_backend, next_backend)
        self.assertNotEqual(first_qa, next_qa)
        final = workspace.snapshot(self.root)
        self.assertTrue(all(t["status"] == "done" for t in final["tasks"]))
        self.assertTrue(all(next(c for c in final["commands"] if c["id"] == old)["status"] == "completed" for old in (first_backend, first_qa)))
        self.assertEqual(next(t for t in final["tasks"] if t["id"] == "TASK-002")["generation"], 2)

    def test_completed_run_pipeline_uses_fresh_runs_and_keeps_original_receipts(self):
        self.pipeline(with_run=True)
        self.decide()
        self.finish_job("TASK-002")
        self.finish_job("TASK-003")
        original = json.loads(json.dumps(runs.read(self.root)["runs"][0]))
        self.decide("request_revision")
        rework = next(t for t in workspace.snapshot(self.root)["tasks"] if t.get("rework"))
        self.finish_job(rework["id"], text="LIMIT = 100\n")
        approved = self.decide()["snapshot"]
        owner = next(t for t in approved["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(owner["status"], "done")
        self.assertEqual(owner["artefact_revision"]["task_id"], rework["id"])
        self.assertNotIn("run_id", owner)
        self.finish_job("TASK-002")
        self.finish_job("TASK-003")
        final = workspace.snapshot(self.root)
        self.assertEqual(runs.read(self.root)["runs"][0], original)
        self.assertEqual(len(runs.read(self.root)["runs"]), 3)
        self.assertEqual(len(final["archived_steps"]), 3)
        self.assertEqual(len(final["tasks"]), 4)
        self.assertEqual(next(t for t in final["tasks"] if t["id"] == "TASK-002")["run_id"], "RUN-002")
        self.assertEqual(next(t for t in final["tasks"] if t["id"] == "TASK-003")["run_id"], "RUN-003")
        self.assertTrue(all(t["status"] == "done" for t in final["tasks"]))

    def test_failed_auth_job_is_not_retried_without_changed_human_revision(self):
        self.pipeline()
        self.decide()
        failed = self.finish_job("TASK-002", fail="Authentication unavailable")["command"]["id"]
        (self.root / "src/policy.py").write_text("LIMIT = 100\n", encoding="utf-8")
        self.decide()
        backend = [c for c in workspace.snapshot(self.root)["commands"] if c["task_id"] == "TASK-002"]
        self.assertEqual([(c["id"], c["status"]) for c in backend], [(failed, "failed")])

    def test_failed_usage_is_recorded_and_later_success_replaces_current_notes(self):
        self.pipeline()
        self.decide()
        usage = {"tokens": 12, "cost_usd": None, "source": "synthetic-provider", "attempt_id": "fixture-1", "provider_session_id": "fixture-session"}
        failed = self.finish_job("TASK-002", fail="Temporary verification failure", usage=usage)
        self.assertEqual(failed["snapshot"]["usage"]["tokens"], 12)
        self.assertEqual(failed["snapshot"]["usage"]["records"][0]["outcome"], "failed")
        self.assertIsNone(failed["snapshot"]["usage"]["cost_usd"])
        command = failed["command"]
        workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": "retry", "agent_id": command["agent_id"], "provider": "codex",
            "role": "backend", "command_id": command["id"], "notes": "Explicit synthetic retry"})
        # A retried command is reclaimed with the same stable ID, but this unit
        # helper gives each physical attempt a separate engine receipt key.
        snapshot = workspace.snapshot(self.root)
        task = next(t for t in snapshot["tasks"] if t["id"] == "TASK-002")
        base = {"agent_id": command["agent_id"], "provider": "codex", "role": "backend"}
        workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "claim", "task_id": task["id"]})
        workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "started", "command_id": command["id"]})
        (self.root / "src/handler.py").write_text("Synthetic retry output\n", encoding="utf-8")
        result = workspace.host_event(self.root, {**base, "expected_revision": self.rev(), "kind": "completed", "command_id": command["id"],
            "notes": "Current successful retry", "evidence_refs": ["src/handler.py"],
            "usage": {"tokens": 8, "cost_usd": None, "source": "synthetic-provider", "attempt_id": "fixture-2"}})
        self.assertEqual(result["command"]["notes"], "Current successful retry")
        self.assertEqual(result["snapshot"]["usage"]["tokens"], 20)
        self.assertEqual(len(result["snapshot"]["usage"]["records"]), 2)
        self.assertTrue(any("Temporary verification failure" in event["details"].get("notes", "") for event in lifecycle.events(self.root)))


    def test_run_gate_revision_before_first_implementation_defers_future_inputs(self):
        self.pipeline(with_run=True)
        original = json.loads(json.dumps(runs.read(self.root)["runs"][0]))
        self.assertFalse((self.root / "src/handler.py").exists())
        self.decide("request_revision")
        rework = next(t for t in workspace.snapshot(self.root)["tasks"] if t.get("rework"))
        self.finish_job(rework["id"], text="LIMIT = 100\n")
        self.decide()
        pending = workspace.snapshot(self.root)
        self.assertTrue(next(t for t in pending["tasks"] if t["id"] == "TASK-003")["run_generation_pending"])
        self.assertEqual(len(runs.read(self.root)["runs"]), 1)
        self.finish_job("TASK-002")
        self.finish_job("TASK-003")
        self.assertEqual(runs.read(self.root)["runs"][0], original)
        self.assertTrue(all(t["status"] == "done" for t in workspace.snapshot(self.root)["tasks"]))


    def test_failed_qa_gets_new_generation_only_after_explicit_human_revision(self):
        self.pipeline()
        self.decide()
        self.finish_job("TASK-002")
        failed = self.finish_job("TASK-003", fail="Verified edge case failed")["command"]["id"]
        self.decide("request_revision")
        rework = next(t for t in workspace.snapshot(self.root)["tasks"] if t.get("rework"))
        self.finish_job(rework["id"], text="LIMIT = 100\n")
        self.decide()
        self.finish_job("TASK-002")
        new_qa = self.finish_job("TASK-003")["command"]["id"]
        self.assertNotEqual(new_qa, failed)
        self.assertEqual(next(c for c in workspace.snapshot(self.root)["commands"] if c["id"] == failed)["status"], "failed")
        self.assertTrue(all(t["status"] == "done" for t in workspace.snapshot(self.root)["tasks"]))

    def test_failed_run_qa_gets_fresh_run_after_human_revision(self):
        self.pipeline(with_run=True)
        self.decide()
        self.finish_job("TASK-002")
        failed = self.finish_job("TASK-003", fail="Verified edge case failed")["command"]["id"]
        original = json.loads(json.dumps(runs.read(self.root)["runs"][0]))
        self.decide("request_revision")
        rework = next(t for t in workspace.snapshot(self.root)["tasks"] if t.get("rework"))
        self.finish_job(rework["id"], text="LIMIT = 100\n")
        self.decide()
        self.finish_job("TASK-002")
        new_qa = self.finish_job("TASK-003")["command"]["id"]
        final = workspace.snapshot(self.root)
        self.assertNotEqual(new_qa, failed)
        self.assertEqual(runs.read(self.root)["runs"][0], original)
        self.assertEqual(next(t for t in final["tasks"] if t["id"] == "TASK-003")["run_id"], "RUN-003")
        self.assertTrue(all(t["status"] == "done" for t in final["tasks"]))


class SupervisorHealthTests(unittest.TestCase):
    def server(self):
        return SimpleNamespace(project="synthetic-project", supervisor_lock=threading.Lock(),
            supervisor_state={"configured": True, "provider": "codex", "status": "starting", "attempts": 0,
                              "last_error": None, "next_retry_seconds": None})

    def test_transient_startup_conflict_retries_bounded_then_enters_worker(self):
        from tools.workspace_runner import RunnerAlreadyRunning
        server, calls = self.server(), []
        def worker(*args, **kwargs):
            calls.append(1)
            if len(calls) < 3:
                raise RunnerAlreadyRunning("Synthetic old owner stopping")
        workspace_server._supervise(server, "codex", worker, threading.Event(), {},
                                    (RunnerAlreadyRunning,), retry_delays=(0, 0))
        self.assertEqual(len(calls), 3)
        self.assertEqual(server.supervisor_state["status"], "stopped")
        self.assertEqual(server.supervisor_state["attempts"], 3)
        self.assertIsNone(server.supervisor_state["last_error"])

    def test_persistent_owner_conflict_stops_with_visible_failed_health(self):
        from tools.workspace_runner import RunnerAlreadyRunning
        server, calls = self.server(), []
        def worker(*args, **kwargs):
            calls.append(1)
            raise RunnerAlreadyRunning("Synthetic live owner; do not steal")
        with contextlib.redirect_stderr(io.StringIO()):
            workspace_server._supervise(server, "codex", worker, threading.Event(), {},
                                        (RunnerAlreadyRunning,), retry_delays=(0, 0))
        self.assertEqual(len(calls), 3)
        self.assertEqual(server.supervisor_state["status"], "failed")
        self.assertIsNone(server.supervisor_state["next_retry_seconds"])
        snap = workspace_server._snapshot_with_supervisor(server, {"host": {"runner_connected": False}})
        self.assertFalse(snap["host"]["runner_connected"])
        self.assertEqual(snap["host"]["supervisor"]["status"], "failed")

    def test_backoff_is_interruptible_and_does_not_restart_after_stop(self):
        from tools.workspace_runner import RunnerAlreadyRunning
        server, calls, stop = self.server(), [], threading.Event()
        def worker(*args, **kwargs):
            calls.append(1)
            raise RunnerAlreadyRunning("Synthetic owner stopping")
        thread = threading.Thread(target=workspace_server._supervise,
            args=(server, "codex", worker, stop, {}, (RunnerAlreadyRunning,), (30,)))
        thread.start()
        try:
            deadline = time.monotonic() + 2
            while server.supervisor_state["status"] != "retrying" and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertEqual(server.supervisor_state["status"], "retrying")
            stop.set(); thread.join(1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(calls, [1])
            self.assertEqual(server.supervisor_state["status"], "stopped")
        finally:
            stop.set(); thread.join(2)

    def test_unknown_startup_failure_is_not_retried_and_error_is_redacted(self):
        server, calls = self.server(), []
        def worker(*args, **kwargs):
            calls.append(1)
            raise RuntimeError("Synthetic failure token=do-not-disclose")
        with contextlib.redirect_stderr(io.StringIO()) as errors:
            workspace_server._supervise(server, "codex", worker, threading.Event(), {}, retry_delays=(0, 0))
        self.assertEqual(len(calls), 1)
        self.assertEqual(server.supervisor_state["status"], "failed")
        self.assertNotIn("do-not-disclose", server.supervisor_state["last_error"])
        self.assertNotIn("do-not-disclose", errors.getvalue())


    def test_viewer_only_snapshot_health_does_not_claim_a_runner_connection(self):
        server = self.server()
        server.supervisor_state.update(configured=False, provider=None, status="disabled")
        original = {"host": {"runner_connected": False}, "tasks": [{"id": "synthetic"}]}
        result = workspace_server._snapshot_with_supervisor(server, original)
        self.assertFalse(result["host"]["supervisor"]["configured"])
        self.assertEqual(result["host"]["supervisor"]["status"], "disabled")
        self.assertFalse(result["host"]["runner_connected"])
        self.assertNotIn("supervisor", original["host"])
        server.supervisor_state["status"] = "failed"
        self.assertEqual(result["host"]["supervisor"]["status"], "disabled")
