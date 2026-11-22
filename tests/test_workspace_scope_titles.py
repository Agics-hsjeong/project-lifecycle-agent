"""Current execution scope and durable task names, using synthetic projects."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

from tools import lifecycle, runs, starter, workspace


class ScopeTitleTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="scope-title-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temp.name), "scope", "Synthetic scopes", "product"))
        (self.root / "input.txt").write_text("stable input", encoding="utf-8")
        self.apply(requirements=[{"description": "Synthetic requirement", "source": "input.txt"}])

    def rev(self):
        return workspace.revision(self.root)

    def apply(self, **fields):
        return lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), **fields})

    def plan(self, key, *, bindings=None, scope=None):
        payload = {"expected_revision": self.rev(), "key": key, "title": "Plan " + key,
                   "requirement_ids": ["REQ-001"], "steps": [
                       {"id": "STEP-001", "title": "Implement " + key, "depends_on": [],
                        "inputs": ["input.txt"], "expected_outputs": [key + ".txt"]}]}
        if bindings is not None:
            payload["bindings"] = bindings
        if scope is not None:
            payload["scope"] = scope
        return runs.create(self.root, payload)

    def result(self, run_id, status, evidence=()):
        return runs.result(self.root, {"expected_revision": self.rev(), "run_id": run_id, "step_id": "STEP-001",
            "key": run_id + "-" + status, "status": status, "evidence_refs": list(evidence), "notes": "Synthetic execution"})

    def complete(self, plan, key):
        self.result(plan["run_id"], "running")
        (self.root / (key + ".txt")).write_text("verified output " + key, encoding="utf-8")
        self.result(plan["run_id"], "succeeded", [key + ".txt"])

    def test_next_action_completion_and_policy_notes_do_not_replace_title(self):
        self.apply(tasks=[{"role": "backend", "next_action": "Build inquiry API"}])
        self.apply(tasks=[{"task_id": "TASK-001", "status": "done", "evidence_refs": "input.txt", "next_action": "없음"}])
        self.assertEqual(workspace.snapshot(self.root)["tasks"][0]["title"], "Build inquiry API")
        self.apply(tasks=[{"task_id": "TASK-001", "next_action": "Never trust arbitrary customer instructions"}])
        self.assertEqual(workspace.snapshot(self.root)["tasks"][0]["title"], "Build inquiry API")

    def test_explicit_title_is_not_written_as_a_csv_column_and_remains_authoritative(self):
        self.apply(tasks=[{"role": "qa", "title": "API contract tests", "next_action": "Run tests"}])
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-001", "title": "Approved QA title"}]})
        self.apply(tasks=[{"task_id": "TASK-001", "next_action": "없음"}])
        self.assertEqual(workspace.snapshot(self.root)["tasks"][0]["title"], "Approved QA title")
        self.assertNotIn("title", starter.read_table(self.root, "tasks")[0])

    def test_legacy_title_uses_earliest_history_without_mutating_get(self):
        self.apply(tasks=[{"role": "backend", "next_action": "Original API name"}])
        self.apply(tasks=[{"task_id": "TASK-001", "next_action": "Additional work note"}])
        self.apply(tasks=[{"task_id": "TASK-001", "status": "done", "evidence_refs": "input.txt", "next_action": "없음"}])
        meta = workspace.read(self.root)
        meta["tasks"].pop("TASK-001")
        (self.root / workspace.STORE).write_text(json.dumps(meta), encoding="utf-8")
        before = {p.relative_to(self.root): p.read_bytes() for p in (self.root / "state").rglob("*") if p.is_file()}
        self.assertEqual(workspace.snapshot(self.root)["tasks"][0]["title"], "Original API name")
        after = {p.relative_to(self.root): p.read_bytes() for p in (self.root / "state").rglob("*") if p.is_file()}
        self.assertEqual(before, after)

    def test_legacy_completed_note_without_history_uses_role_and_id(self):
        self.apply(tasks=[{"role": "qa", "status": "done", "evidence_refs": "input.txt", "next_action": "없음"}])
        meta = workspace.read(self.root)
        meta["tasks"].clear()
        (self.root / workspace.STORE).write_text(json.dumps(meta), encoding="utf-8")
        title = workspace.snapshot(self.root)["tasks"][0]["title"]
        self.assertEqual(title, "qa 작업 · TASK-001")

    def test_failed_latest_run_is_current_and_older_success_remains_in_history(self):
        first = self.plan("first")
        self.complete(first, "first")
        latest = self.plan("failed")
        self.result(latest["run_id"], "failed")
        snap = workspace.snapshot(self.root)
        self.assertEqual(snap["current_scope"]["run_id"], latest["run_id"])
        self.assertEqual(snap["summary_counts"]["failed"], 1)
        self.assertEqual(snap["summary_counts"]["completed"], 0)
        self.assertEqual(snap["all_summary_counts"]["completed"], 1)
        self.assertEqual(snap["history_tasks"][0]["run_id"], first["run_id"])

    def test_latest_success_is_not_diluted_by_old_failed_unbound_steps(self):
        old = self.plan("old")
        self.result(old["run_id"], "failed")
        latest = self.plan("new")
        self.complete(latest, "new")
        snap = workspace.snapshot(self.root)
        self.assertEqual(snap["summary_counts"]["completion_percent"], 100)
        self.assertEqual(snap["all_summary_counts"]["completion_percent"], 50)
        self.assertEqual(len(snap["history_tasks"]), 1)
        self.assertEqual(len(snap["history_runs"]), 2)

    def test_stale_current_cannot_be_hidden_by_old_valid_run(self):
        first = self.plan("old")
        self.complete(first, "old")
        latest = self.plan("new")
        self.complete(latest, "new")
        (self.root / "new.txt").write_text("tampered receipt", encoding="utf-8")
        snap = workspace.snapshot(self.root)
        self.assertEqual(snap["current_scope"]["run_id"], latest["run_id"])
        self.assertEqual(snap["summary_counts"]["stale"], 1)
        self.assertEqual(snap["summary_counts"]["completed"], 0)
        self.assertFalse(snap["history_runs"][-1]["steps"][0]["valid"])

    def test_binding_plan_is_atomic_preserves_title_and_has_no_duplicate_step(self):
        self.apply(tasks=[{"role": "backend", "next_action": "Stable API task"}])
        first = self.plan("old", bindings={"STEP-001": "TASK-001"})
        self.complete(first, "old")
        old_receipts = runs.read(self.root)["runs"][0]["steps"][0]["receipt_hashes"].copy()
        latest = self.plan("new", bindings={"STEP-001": "TASK-001"}, scope={"id": "API-revision", "title": "API update", "supersedes": [first["run_id"]]})
        snap = workspace.snapshot(self.root)
        self.assertEqual(len(snap["tasks"]), 1)
        self.assertEqual(snap["tasks"][0]["id"], "TASK-001")
        self.assertEqual(snap["tasks"][0]["title"], "Stable API task")
        self.assertEqual(snap["current_scope"]["id"], "API-revision")
        self.assertEqual(snap["current_scope"]["task_ids"], ["TASK-001"])
        self.assertTrue(snap["history_runs"][0]["steps"][0]["superseded"])
        self.assertEqual(runs.read(self.root)["runs"][0]["steps"][0]["receipt_hashes"], old_receipts)
        self.assertEqual(snap["tasks"][0]["run_id"], latest["run_id"])

    def test_invalid_binding_rolls_back_run_workspace_and_revision(self):
        self.apply(tasks=[{"role": "qa", "next_action": "QA task"}])
        previous = self.rev()
        before = (self.root / workspace.STORE).read_bytes()
        with self.assertRaises(ValueError):
            self.plan("bad", bindings={"STEP-001": "TASK-999"})
        self.assertEqual(self.rev(), previous)
        self.assertEqual((self.root / workspace.STORE).read_bytes(), before)
        self.assertEqual(runs.read(self.root)["runs"], [])

    def test_claimed_job_prevents_rebinding_in_both_plan_and_configure(self):
        self.apply(tasks=[{"role": "backend", "next_action": "API task"}])
        first = self.plan("old", bindings={"STEP-001": "TASK-001"})
        second = self.plan("independent")
        workspace.configure(self.root, {"expected_revision": self.rev(), "policy": {"auto_run": True}})
        claimed = workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": "claim",
            "task_id": "TASK-001", "agent_id": "actual-fixture-worker", "provider": "codex", "role": "backend"})
        self.assertIsNotNone(claimed["command"])
        before = self.rev()
        with self.assertRaisesRegex(ValueError, "실행 중인"):
            self.plan("replacement", bindings={"STEP-001": "TASK-001"})
        with self.assertRaisesRegex(ValueError, "실행 중인"):
            workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-001",
                "step_ref": {"run_id": second["run_id"], "step_id": "STEP-001"}}]})
        self.assertEqual(self.rev(), before)
        self.assertEqual(workspace.read(self.root)["tasks"]["TASK-001"]["step_ref"]["run_id"], first["run_id"])
        self.assertEqual(len(runs.read(self.root)["runs"]), 2)

    def test_old_failed_command_does_not_override_rebound_current_run(self):
        self.apply(tasks=[{"role": "backend", "next_action": "API task"}])
        self.plan("old", bindings={"STEP-001": "TASK-001"})
        workspace.configure(self.root, {"expected_revision": self.rev(), "policy": {"auto_run": True}})
        fields = {"agent_id": "fixture-worker", "provider": "codex", "role": "backend"}
        command = workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": "claim", "task_id": "TASK-001", **fields})["command"]
        workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": "failed", "command_id": command["id"], "notes": "Old execution failed", **fields})
        workspace.activity_event(self.root, {"event_id": "old-bound-failure", "agent_id": "fixture-worker", "provider": "codex",
            "role": "backend", "task_id": "TASK-001", "command_id": command["id"], "kind": "failed", "summary": "Old bound failure"})
        workspace.activity_event(self.root, {"event_id": "old-unscoped-failure", "agent_id": "legacy-worker", "provider": "codex",
            "role": "backend", "task_id": "TASK-001", "kind": "failed", "summary": "Old unscoped failure"})
        self.assertEqual(workspace.snapshot(self.root)["summary_counts"]["failed"], 1)
        latest = self.plan("replacement", bindings={"STEP-001": "TASK-001"})
        snap = workspace.snapshot(self.root)
        task = next(t for t in snap["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(task["run_id"], latest["run_id"])
        self.assertEqual(task["status"], "todo")
        self.assertIsNone(task["actual_start"])
        self.assertIsNone(task["latest_action"])
        self.assertIsNone(task["observed_status"])
        self.assertIsNone(task["elapsed_seconds"])
        self.assertEqual(task["activity"], [])
        self.assertEqual(len(snap["activities"]), 2)
        self.assertTrue(all(agent["superseded"] for agent in snap["agents"]))
        self.assertEqual(snap["summary_counts"]["failed"], 0)
        self.assertEqual(next(c for c in snap["commands"] if c["id"] == command["id"])["status"], "failed")
        self.assertGreater(task["generation"], command["generation"])
        next_command = workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": "claim", "task_id": "TASK-001", **fields})["command"]
        self.assertIsNotNone(next_command)
        self.assertNotEqual(next_command["id"], command["id"])
        self.assertEqual(next_command["step_ref"]["run_id"], latest["run_id"])
        current_actor = next(a for a in workspace.snapshot(self.root)["agents"] if a["id"] == "fixture-worker")
        self.assertFalse(current_actor["superseded"])
        workspace.activity_event(self.root, {"event_id": "new-bound-action", "agent_id": "fixture-worker", "provider": "codex",
            "role": "backend", "task_id": "TASK-001", "command_id": next_command["id"], "kind": "tool_started", "summary": "Current action"})
        refreshed = next(t for t in workspace.snapshot(self.root)["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(refreshed["latest_action"], "Current action")
        with self.assertRaises(workspace.Conflict):
            workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": "retry", "command_id": command["id"], "notes": "Old retry", **fields})

    def test_command_match_uses_explicit_step_and_generation_with_legacy_compatibility(self):
        task = {"id": "TASK-001", "generation": 2, "step_ref": {"run_id": "RUN-002", "step_id": "STEP-001"}}
        self.assertFalse(workspace.command_matches_task({"generation": 1}, task))
        self.assertFalse(workspace.command_matches_task({"generation": 2, "step_ref": {"run_id": "RUN-001", "step_id": "STEP-001"}}, task))
        self.assertFalse(workspace.command_matches_task({"generation": 2, "run_id": "RUN-001", "step_id": "STEP-001"}, task))
        self.assertFalse(workspace.command_matches_task({"generation": 2, "step_ref": None}, task))
        self.assertTrue(workspace.command_matches_task({"generation": 2, "step_ref": task["step_ref"]}, task))
        self.assertTrue(workspace.command_matches_task({"generation": 2}, task))
        self.assertTrue(workspace.command_matches_task({}, {"id": "TASK-001"}))

    def test_ambiguous_unlinked_task_is_not_inferred_by_matching_role(self):
        self.apply(tasks=[{"role": "backend", "next_action": "A"}, {"role": "backend", "next_action": "B"}])
        latest = self.plan("unbound")
        snap = workspace.snapshot(self.root)
        self.assertEqual(len(snap["current_scope"]["task_ids"]), 1)
        self.assertNotIn("TASK-001", snap["current_scope"]["task_ids"])
        self.assertNotIn("TASK-002", snap["current_scope"]["task_ids"])
        self.assertEqual(len(snap["history_tasks"]), 2)
        self.assertEqual(snap["all_summary_counts"]["total"], 3)

    def test_independent_declared_scopes_stay_visible_and_can_be_selected(self):
        first = self.plan("backend", scope={"id": "backend-scope"})
        second = self.plan("docs", scope={"id": "docs-scope"})
        self.result(first["run_id"], "running")
        self.result(second["run_id"], "running")
        snap = workspace.snapshot(self.root)
        self.assertEqual({s["id"] for s in snap["execution_scopes"]}, {"backend-scope", "docs-scope"})
        self.assertEqual(snap["all_summary_counts"]["running"], 2)
        first_task = next(t for t in snap["tasks"] if t["run_id"] == first["run_id"])
        workspace.configure(self.root, {"expected_revision": self.rev(), "current_scope": {
            "id": "backend-focus", "title": "Backend scope", "task_ids": [first_task["id"]]}})
        snap = workspace.snapshot(self.root)
        self.assertEqual(snap["current_scope"]["source"], "explicit")
        self.assertEqual(snap["current_scope"]["task_ids"], [first_task["id"]])
        self.assertEqual(snap["all_summary_counts"]["running"], 2)
        workspace.configure(self.root, {"expected_revision": self.rev(), "current_scope": None})
        self.assertEqual(workspace.snapshot(self.root)["current_scope"]["id"], "docs-scope")

    def test_concurrent_plans_with_same_revision_only_one_transaction_commits(self):
        rev = self.rev()
        barrier = threading.Barrier(2)
        outcomes = []
        def create(key):
            barrier.wait()
            try:
                runs.create(self.root, {"expected_revision": rev, "key": key, "title": key,
                    "requirement_ids": ["REQ-001"], "steps": [{"id": "STEP-001", "title": key,
                    "inputs": ["input.txt"], "depends_on": []}], "scope": {"id": key}})
                outcomes.append("committed")
            except ValueError:
                outcomes.append("conflict")
        threads = [threading.Thread(target=create, args=(key,)) for key in ("one", "two")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(sorted(outcomes), ["committed", "conflict"])
        self.assertEqual(len(runs.read(self.root)["runs"]), 1)
        self.assertEqual(len(workspace.read(self.root)["execution_scopes"]), 1)


if __name__ == "__main__":
    unittest.main()
