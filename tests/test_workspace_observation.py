"""Observation is revision-neutral; independent review/repair is real evidence."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from tools import lifecycle, starter, workspace


class WorkspaceObservationTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="observation-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(starter.init_project(Path(self.temp.name), "project", "Observation fixture", "product"))
        (self.root / "docs/spec.md").write_text("Original contract\n", encoding="utf-8")
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "architect", "owner": "Fixture", "status": "done", "next_action": "Contract", "evidence_refs": "docs/spec.md"},
            {"role": "backend", "owner": "Fixture", "status": "todo", "next_action": "Implement"},
            {"role": "qa", "owner": "Fixture", "status": "todo", "next_action": "Review"}]})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": "TASK-001", "stage": "planning"},
            {"id": "TASK-002", "stage": "implementation", "depends_on": ["TASK-001"], "execution": self.plan(["src/handler.py"])},
            {"id": "TASK-003", "stage": "verification", "depends_on": ["TASK-002"], "execution": self.plan(["tests/test_edge.py", "reports/qa.md"])}],
            "gates": [{"id": "SPEC", "task_id": "TASK-001", "paths": ["docs/spec.md"]}]})

    def rev(self):
        return workspace.revision(self.root)

    def plan(self, outputs):
        return {"instruction": "Implement scoped fixture", "allowed_paths": outputs, "expected_outputs": outputs,
                "verification_commands": [["python", "-m", "unittest", "discover", "-s", "tests", "-v"]]}

    def optional(self):
        return workspace.configure(self.root, {"expected_revision": self.rev(), "policy": {"view_mode": "observe", "auto_run": True},
            "gates": [{"id": "SPEC", "task_id": "TASK-001", "paths": ["docs/spec.md"], "required": False}]})

    def event(self, **fields):
        return workspace.activity_event(self.root, {"task_id": "TASK-002", "agent_id": "/root/native-worker", "role": "backend",
            "provider": "codex", "kind": "tool_started", "summary": "Reading src/handler.py", **fields})

    def host(self, identity, kind, **fields):
        return workspace.host_event(self.root, {"expected_revision": self.rev(), **identity, "kind": kind, **fields})

    def finish_backend(self):
        identity = {"provider": "codex", "agent_id": "fixture-backend", "role": "backend"}
        command = self.host(identity, "claim", task_id="TASK-002")["command"]
        self.host(identity, "started", command_id=command["id"])
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src/handler.py").write_text("LIMIT = 1\n", encoding="utf-8")
        self.host(identity, "completed", command_id=command["id"], evidence_refs=["src/handler.py"])

    def failing_qa(self):
        identity = {"provider": "claude", "agent_id": "fixture-qa-"+os.urandom(3).hex(), "role": "qa"}
        command = self.host(identity, "claim", task_id="TASK-003")["command"]
        self.host(identity, "started", command_id=command["id"])
        self.host(identity, "failed", command_id=command["id"], notes="Real independent verification failed")
        prefix = "reports/runner/"+command["id"]+"/"
        verification = self.root / prefix / "verification.txt"
        verification.parent.mkdir(parents=True, exist_ok=True)
        verification.write_text('COMMAND: ["python", "-m", "unittest"]\nEXIT: 1\nFAILED (failures=1)\n', encoding="utf-8")
        test_ref = prefix+"qa-feedback/tests/test_edge.py"
        path = self.root / test_ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import unittest\nclass Edge(unittest.TestCase):\n def test_fixture(self): self.assertTrue(True)\n", encoding="utf-8")
        return {"expected_revision": self.rev(), "failed_command_id": command["id"], "feedback_refs": [prefix+"verification.txt"],
                "check_hashes": {test_ref: workspace.file_hash(self.root, test_ref)}, "summary": "Fix observed owner edge case."}

    def test_activity_is_live_host_observation_not_business_completion(self):
        before = self.rev()
        event = self.event(status="running", pid=os.getpid(), parent_id="/root", source="host_report")
        view = workspace.snapshot(self.root)
        self.assertEqual(self.rev(), before)
        self.assertEqual(view["activities"][-1]["id"], event["event"]["id"])
        self.assertEqual(view["activity"], view["activities"])
        task = next(t for t in view["tasks"] if t["id"] == "TASK-002")
        self.assertNotEqual(task["status"], "done")
        self.assertEqual(task["latest_action"], "Reading src/handler.py")
        agent = next(a for a in view["agents"] if a["id"] == "/root/native-worker")
        self.assertTrue(agent["observed"])
        self.assertEqual(agent["status"], "running")
        self.assertTrue(view["host"]["connected"])
        self.assertEqual(workspace.read(self.root)["decisions"], [])
        self.event(kind="completed", status="completed", summary="Actual worker returned")
        self.assertEqual(next(a for a in workspace.snapshot(self.root)["agents"] if a["id"] == agent["id"])["status"], "idle")

    def test_activity_redacts_credentials_and_deduplicates_identical_events(self):
        fields = {"event_id": "fixture-1", "summary": "Authorization: Bearer abcDEF123 token=private password:unknown sk-1234567890123456 https://local/path#token=secret"}
        self.event(**fields)
        result = self.event(**fields)
        self.assertTrue(result["duplicate"])
        self.assertEqual(workspace.snapshot(self.root)["activity_seq"], 1)
        summary = result["event"]["summary"]
        for secret in ("abcDEF123", "private", "unknown", "sk-1234567890123456", "token=secret"):
            self.assertNotIn(secret, summary)
        with self.assertRaises(workspace.Conflict):
            self.event(event_id="fixture-1", summary="different")

    def test_activity_bounded_storage_monotonic_sequence_and_identity_checks(self):
        original = workspace.MAX_ACTIVITY
        workspace.MAX_ACTIVITY = 3
        self.addCleanup(setattr, workspace, "MAX_ACTIVITY", original)
        for i in range(5):
            self.event(event_id="fixture-"+str(i))
        view = workspace.snapshot(self.root)
        self.assertEqual(view["activity_seq"], 5)
        self.assertEqual([e["seq"] for e in view["activities"]], [3, 4, 5])
        for fields in ({"task_id": "missing"}, {"kind": "hidden_reasoning"}, {"paths": ["../secret"]}, {"command_id": "missing"}, {"pid": -1}):
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                self.event(**fields)

    def test_explicit_human_gate_is_preserved_until_optional_policy_is_requested(self):
        before = workspace.snapshot(self.root)
        self.assertEqual(before["gates"][0]["status"], "pending")
        self.assertTrue(before["gates"][0]["required"])
        self.assertFalse(next(t for t in before["tasks"] if t["id"] == "TASK-002")["ready"])
        view = self.optional()
        self.assertEqual(view["view_mode"], "observer")
        self.assertEqual(view["gates"][0]["status"], "observed")
        self.assertEqual(view["gates"][0]["review_status"], "pending")
        self.assertEqual(view["gates"][0]["decisions"], [])
        self.assertTrue(next(t for t in view["tasks"] if t["id"] == "TASK-002")["ready"])
        self.assertTrue(any(c["task_id"] == "TASK-002" and c["status"] == "queued" for c in view["commands"]))
        with self.assertRaises(ValueError):
            workspace.configure(self.root, {"expected_revision": self.rev(), "gates": [{"id": "SPEC", "task_id": "TASK-002", "paths": ["docs/spec.md"], "required": False}]})

    def test_independent_ai_review_has_real_hashes_and_no_human_decision(self):
        self.optional()
        self.finish_backend()
        (self.root / "reports").mkdir(exist_ok=True)
        (self.root / "reports/review.md").write_text("Independent static review\n", encoding="utf-8")
        payload = {"expected_revision": self.rev(), "review_id": "fixture-review", "task_id": "TASK-002", "agent_id": "actual-qa-role",
                   "provider": "claude", "role": "qa", "outcome": "passed", "summary": "Reviewed owner policy", "findings": [],
                   "evidence_refs": ["reports/review.md"], "target_hashes": {"src/handler.py": workspace.file_hash(self.root, "src/handler.py")}}
        result = workspace.review_event(self.root, payload)
        self.assertEqual(result["review"]["authority"], "host-reported-ai-review")
        self.assertTrue((self.root / result["evidence_ref"]).is_file())
        self.assertTrue(workspace.review_event(self.root, payload)["duplicate"])
        self.assertEqual(workspace.read(self.root)["decisions"], [])
        self.assertEqual(workspace.snapshot(self.root)["reviews"][0]["outcome"], "passed")
        payload["review_id"] = "self-review"
        payload["expected_revision"] = self.rev()
        payload["role"] = "backend"
        with self.assertRaises(ValueError):
            workspace.review_event(self.root, payload)

    def test_verified_qa_failure_creates_bounded_same_scope_repair_then_qa(self):
        self.optional()
        self.finish_backend()
        payload = self.failing_qa()
        original_scope = workspace.read(self.root)["tasks"]["TASK-002"]["execution"]["allowed_paths"]
        result = workspace.schedule_repair(self.root, payload)
        self.assertEqual(result["repair_task_id"], "TASK-002")
        self.assertEqual(result["review_task_id"], "TASK-003")
        self.assertEqual(result["attempt"], 1)
        self.assertTrue(workspace.schedule_repair(self.root, payload)["unchanged"])
        view = workspace.snapshot(self.root)
        backend = next(t for t in view["tasks"] if t["id"] == "TASK-002")
        qa = next(t for t in view["tasks"] if t["id"] == "TASK-003")
        self.assertEqual(backend["generation"], 2)
        self.assertEqual(backend["execution"]["allowed_paths"], original_scope)
        self.assertEqual(backend["repair_checks"][0]["target"], "tests/test_edge.py")
        self.assertEqual(qa["generation"], 2)
        self.assertFalse(qa["ready"])
        self.assertTrue(any(c["id"] == payload["failed_command_id"] and c["status"] == "failed" for c in view["commands"]))
        self.assertEqual(workspace.read(self.root)["decisions"], [])
        self.finish_backend()
        self.assertTrue(any(c["task_id"] == "TASK-003" and c["status"] == "queued" for c in workspace.snapshot(self.root)["commands"]))
        second = workspace.schedule_repair(self.root, self.failing_qa())
        self.assertEqual(second["attempt"], 2)
        self.finish_backend()
        third = self.failing_qa()
        with self.assertRaisesRegex(ValueError, "2회"):
            workspace.schedule_repair(self.root, third)

    def test_auth_failure_missing_or_changed_counterexample_does_not_repair(self):
        self.optional()
        self.finish_backend()
        payload = self.failing_qa()
        log = self.root / payload["feedback_refs"][0]
        original = log.read_bytes()
        log.write_text("Native authentication failed; no tests executed\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            workspace.schedule_repair(self.root, payload)
        log.write_bytes(original)
        source = next(iter(payload["check_hashes"]))
        (self.root / source).write_text("Changed check\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            workspace.schedule_repair(self.root, payload)
        self.assertEqual(workspace.read(self.root).get("repair_requests", []), [])
        self.assertEqual(next(t for t in workspace.snapshot(self.root)["tasks"] if t["id"] == "TASK-002")["generation"], 1)

    def test_optional_rework_waits_for_real_completion_then_continues_without_approval(self):
        view = workspace.snapshot(self.root)
        gate = view["gates"][0]
        workspace.decide(self.root, {"action_id": "actual-prior-revision", "expected_revision": view["revision"], "gate_id": gate["id"],
            "decision": "request_revision", "actor": "Synthetic prior human", "feedback": "Clarify inputs", "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]})
        self.optional()
        view = workspace.snapshot(self.root)
        self.assertFalse(next(t for t in view["tasks"] if t["id"] == "TASK-002")["ready"])
        rework = next(t for t in view["tasks"] if t.get("rework"))
        identity = {"provider": "codex", "agent_id": "actual-architect", "role": "architect"}
        command = self.host(identity, "claim", task_id=rework["id"])["command"]
        self.host(identity, "started", command_id=command["id"])
        (self.root / "docs/spec.md").write_text("Revised inputs contract\n", encoding="utf-8")
        self.host(identity, "completed", command_id=command["id"], evidence_refs=["docs/spec.md"])
        view = workspace.snapshot(self.root)
        self.assertTrue(next(t for t in view["tasks"] if t["id"] == "TASK-002")["ready"])
        self.assertTrue(any(c["task_id"] == "TASK-002" and c["status"] == "queued" for c in view["commands"]))
        self.assertEqual([d["decision"] for d in workspace.read(self.root)["decisions"]], ["request_revision"])
