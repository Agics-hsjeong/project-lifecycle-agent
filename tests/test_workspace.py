"""Engine-level workbench safety, lineage and execution evidence tests."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from tools import lifecycle, runs, starter, workspace, workspace_server
from tools.storage import lock


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="workspace-engine-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temp.name), "synthetic", "Synthetic engine workspace", "product"))
        (self.root / "src").mkdir()
        (self.root / "src/policy.py").write_text("POLICY = 'owner'\n", encoding="utf-8")
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "backend", "owner": "Synthetic backend", "status": "done", "next_action": "API policy", "evidence_refs": "src/policy.py"},
            {"role": "qa", "owner": "Synthetic QA", "status": "todo", "next_action": "Validate policy"}]})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": "TASK-001", "title": "API policy", "stage": "implementation"},
            {"id": "TASK-002", "title": "Validate policy", "stage": "verification", "depends_on": ["TASK-001"]}],
            "gates": [{"id": "API", "task_id": "TASK-001", "paths": ["src/policy.py"]}],
            "capture_baselines": ["src/policy.py"]})

    def rev(self):
        history = lifecycle.events(self.root)
        return history[-1]["revision"] if history else None

    def decision(self, key="synthetic-approval", **fields):
        snap = workspace.snapshot(self.root)
        gate = snap["gates"][0]
        return {"action_id": key, "expected_revision": snap["revision"], "gate_id": "API", "decision": "approve",
                "actor": "Synthetic human fixture", "feedback": "Synthetic test decision", "target_hashes": gate["target_hashes"],
                "context_hash": gate["context_hash"], **fields}

    def host(self, kind, **fields):
        return workspace.host_event(self.root, {"expected_revision": self.rev(), "kind": kind,
            "agent_id": "synthetic-host", "provider": "codex", "role": "qa", **fields})

    def start_consumer(self):
        workspace.decide(self.root, self.decision())
        command = self.host("claim")["command"]
        self.assertIsNotNone(command)
        self.host("started", command_id=command["id"])
        return command

    def test_diff_uses_preserved_baseline_and_observes_external_file_edits(self):
        (self.root / "src/policy.py").write_text("POLICY = 'owner-and-state'\n", encoding="utf-8")
        view = workspace.diff(self.root, "src/policy.py")
        self.assertTrue(view["baseline_available"])
        self.assertIn("-POLICY = 'owner'", view["diff"])
        self.assertIn("+POLICY = 'owner-and-state'", view["diff"])
        self.assertEqual(view["sha256"], workspace.snapshot(self.root)["gates"][0]["target_hashes"]["src/policy.py"])

    def test_configuration_cycle_or_unknown_gate_rolls_back_every_file(self):
        previous = self.rev()
        raw = (self.root / workspace.STORE).read_bytes()
        for bad in ({"tasks": [{"id": "TASK-001", "depends_on": ["TASK-002"]}]},
                    {"gates": [{"id": "BAD", "task_id": "TASK-001", "paths": ["../escape.txt"]}]}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                workspace.configure(self.root, {"expected_revision": previous, **bad})
            self.assertEqual(previous, self.rev())
            self.assertEqual(raw, (self.root / workspace.STORE).read_bytes())

    def test_concurrent_different_decisions_have_one_winner(self):
        barrier = threading.Barrier(2)
        payload = self.decision()
        outcomes = []

        def act(key):
            barrier.wait()
            try:
                workspace.decide(self.root, {**payload, "action_id": key})
                outcomes.append("accepted")
            except ValueError:
                outcomes.append("rejected")

        threads = [threading.Thread(target=act, args=(str(i),)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(sorted(outcomes), ["accepted", "rejected"])
        self.assertEqual(len(workspace.read(self.root)["decisions"]), 1)

    def test_claim_matches_role_and_failed_task_can_be_explicitly_retried(self):
        workspace.decide(self.root, self.decision())
        self.assertIsNone(self.host("claim", role="frontend")["command"])
        command = self.host("claim")["command"]
        self.host("started", command_id=command["id"])
        self.host("failed", command_id=command["id"], notes="Synthetic test process failed")
        task = next(t for t in workspace.snapshot(self.root)["tasks"] if t["id"] == "TASK-002")
        self.assertEqual(task["status"], "blocked")
        self.host("retry", command_id=command["id"], notes="Synthetic failure resolved; explicit retry")
        self.assertEqual(self.host("claim")["command"]["id"], command["id"])

    def test_changed_approved_input_cannot_complete_even_after_fresh_approval(self):
        command = self.start_consumer()
        (self.root / "src/policy.py").write_text("POLICY = 'changed-after-start'\n", encoding="utf-8")
        (self.root / "src/test-result.txt").write_text("Synthetic real test receipt", encoding="utf-8")
        workspace.decide(self.root, self.decision("synthetic-fresh-version"))
        before = self.rev()
        with self.assertRaises(workspace.Conflict):
            self.host("completed", command_id=command["id"], evidence_refs=["src/test-result.txt"])
        self.assertEqual(before, self.rev())
        self.assertEqual(starter.read_table(self.root, "tasks")[1]["status"], "doing")

    def test_nonfinite_or_fractional_usage_rolls_back_completion(self):
        command = self.start_consumer()
        (self.root / "src/test-result.txt").write_text("Synthetic receipt", encoding="utf-8")
        before = self.rev()
        for usage in ({"tokens": 2.5}, {"cost_usd": float("nan")}, {"cost_usd": float("inf")}, {"tokens": True}):
            with self.subTest(usage=usage), self.assertRaises(ValueError):
                self.host("completed", command_id=command["id"], evidence_refs=["src/test-result.txt"], usage={"source": "synthetic-test", **usage})
            self.assertEqual(before, self.rev())
        finished = self.host("completed", command_id=command["id"], evidence_refs=["src/test-result.txt"], usage={"source": "synthetic-test", "tokens": 123, "cost_usd": 0.001})
        self.assertEqual(finished["snapshot"]["usage"]["tokens"], 123)
        task = next(t for t in finished["snapshot"]["tasks"] if t["id"] == "TASK-002")
        self.assertTrue(task["actual_start"])
        self.assertTrue(task["actual_end"])

    def test_large_binary_gate_is_hash_reviewed_without_fake_text_diff(self):
        relative = "src/synthetic-deck.pptx"
        (self.root / relative).write_bytes(b"PK\x00" + b"x" * (workspace.MAX_FILE + 1))
        workspace.configure(self.root, {"expected_revision": self.rev(), "gates": [{"id": "DECK", "task_id": "TASK-001", "paths": [relative]}]})
        preview = workspace.diff(self.root, relative)
        self.assertTrue(preview["binary"])
        self.assertIsNone(preview["diff"])
        gate = next(g for g in workspace.snapshot(self.root)["gates"] if g["id"] == "DECK")
        result = workspace.decide(self.root, {**self.decision("synthetic-deck-review"), "gate_id": "DECK", "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]})
        self.assertEqual(next(g for g in result["snapshot"]["gates"] if g["id"] == "DECK")["status"], "approved")

    def test_scope_change_invalidates_approval_without_changing_output_file(self):
        workspace.decide(self.root, self.decision())
        before_hash = workspace.snapshot(self.root)["gates"][0]["target_hashes"]
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "project": {"scope_baseline": "Synthetic expanded business scope"}})
        gate = workspace.snapshot(self.root)["gates"][0]
        self.assertEqual(gate["target_hashes"], before_hash)
        self.assertEqual(gate["status"], "stale")

    def test_cli_actor_mismatch_does_not_create_decision(self):
        before = self.rev()
        with self.assertRaises(ValueError):
            workspace.decide(self.root, self.decision(), actor="Different actor")
        self.assertEqual(before, self.rev())
        self.assertEqual(workspace.read(self.root)["decisions"], [])

    def add_baseline(self):
        source = Path(self.temp.name) / "synthetic-rfp.md"
        source.write_text("Synthetic original requirement: owner-only access.", encoding="utf-8")
        return lifecycle.ingest(self.root, source, actor="synthetic-fixture")["input"]

    def test_original_input_bytes_change_invalidates_raw_task_approval(self):
        record = self.add_baseline()
        workspace.decide(self.root, self.decision())
        manifest = (self.root / "inputs/manifest.json").read_bytes()
        target = workspace.snapshot(self.root)["gates"][0]["target_hashes"]
        (self.root / record["path"]).write_text("Synthetic modified original: different access policy.", encoding="utf-8")
        gate = workspace.snapshot(self.root)["gates"][0]
        self.assertEqual(manifest, (self.root / "inputs/manifest.json").read_bytes())
        self.assertEqual(target, gate["target_hashes"])
        self.assertEqual(gate["status"], "stale")

    def test_extracted_input_bytes_change_invalidates_raw_task_approval(self):
        record = self.add_baseline()
        workspace.decide(self.root, self.decision())
        manifest = (self.root / "inputs/manifest.json").read_bytes()
        (self.root / record["extraction"]["path"]).write_text("Synthetic corrected extraction changes meaning.", encoding="utf-8")
        self.assertEqual(manifest, (self.root / "inputs/manifest.json").read_bytes())
        self.assertEqual(workspace.snapshot(self.root)["gates"][0]["status"], "stale")

    def test_operating_note_ingest_does_not_invalidate_baseline_approval(self):
        self.add_baseline()
        workspace.decide(self.root, self.decision())
        note = Path(self.temp.name) / "synthetic-operation-note.md"
        note.write_text("Synthetic operating note: daily standup moved, scope unchanged.", encoding="utf-8")
        result = lifecycle.ingest(self.root, note, actor="synthetic-fixture", purpose="operating_note")
        self.assertEqual(result["input"]["purpose"], "operating_note")
        self.assertEqual(workspace.snapshot(self.root)["gates"][0]["status"], "approved")
        (self.root / result["input"]["path"]).write_text("Synthetic operating update; still no scope change.", encoding="utf-8")
        self.assertEqual(workspace.snapshot(self.root)["gates"][0]["status"], "approved")

    def test_upstream_output_change_invalidates_downstream_raw_task_approval(self):
        (self.root / "src/synthetic-qa.txt").write_text("Synthetic unchanged QA artefact.", encoding="utf-8")
        workspace.configure(self.root, {"expected_revision": self.rev(), "gates": [{"id": "QA", "task_id": "TASK-002", "paths": ["src/synthetic-qa.txt"]}]})
        gate = next(g for g in workspace.snapshot(self.root)["gates"] if g["id"] == "QA")
        workspace.decide(self.root, {**self.decision("synthetic-downstream-review"), "gate_id": "QA", "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]})
        (self.root / "src/policy.py").write_text("POLICY = 'upstream-output-changed'\n", encoding="utf-8")
        changed = next(g for g in workspace.snapshot(self.root)["gates"] if g["id"] == "QA")
        self.assertEqual(changed["target_hashes"], gate["target_hashes"])
        self.assertEqual(changed["status"], "stale")

    def test_dependency_link_change_invalidates_existing_approval(self):
        (self.root / "src/synthetic-qa.txt").write_text("Synthetic unchanged QA artefact.", encoding="utf-8")
        patch = lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "backend", "owner": "Synthetic alternate", "status": "done", "next_action": "Alternate source", "evidence_refs": "src/policy.py"}]})
        alternate = patch["assigned"]["tasks"][0]
        workspace.configure(self.root, {"expected_revision": self.rev(), "gates": [{"id": "QA", "task_id": "TASK-002", "paths": ["src/synthetic-qa.txt"]}]})
        gate = next(g for g in workspace.snapshot(self.root)["gates"] if g["id"] == "QA")
        workspace.decide(self.root, {**self.decision("synthetic-link-review"), "gate_id": "QA", "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": "TASK-002", "depends_on": [alternate]}]})
        self.assertEqual(next(g for g in workspace.snapshot(self.root)["gates"] if g["id"] == "QA")["status"], "stale")

    def test_task_and_run_without_stage_metadata_are_explicitly_unassigned(self):
        result = lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "requirements": [
            {"source": "Synthetic fixture", "description": "Synthetic dependency", "acceptance_criteria": "Recorded", "priority": "must", "status": "proposed"}],
            "tasks": [{"role": "backend", "owner": "Synthetic", "status": "todo", "next_action": "No explicit stage"}]})
        new_task = result["assigned"]["tasks"][0]
        run = runs.create(self.root, {"expected_revision": self.rev(), "key": "synthetic-unassigned", "title": "Synthetic unstaged execution",
            "requirement_ids": result["assigned"]["requirements"], "steps": [{"id": "STEP-001", "title": "No stage", "depends_on": [], "inputs": ["src/policy.py"], "expected_outputs": ["src/later.txt"]}]})
        tasks = workspace.snapshot(self.root)["tasks"]
        self.assertTrue(next(t for t in tasks if t["id"] == new_task)["stage_unassigned"])
        run_task = next(t for t in tasks if t.get("run_id") == run["run_id"])
        self.assertTrue(run_task["stage_unassigned"])
        self.assertFalse(next(t for t in tasks if t["id"] == "TASK-001")["stage_unassigned"])
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": new_task, "stage": "planning"}, {"id": run_task["id"], "stage": "implementation"}]})
        tasks = workspace.snapshot(self.root)["tasks"]
        self.assertFalse(next(t for t in tasks if t["id"] == new_task)["stage_unassigned"])
        self.assertFalse(next(t for t in tasks if t["id"] == run_task["id"])["stage_unassigned"])

    def test_original_bytes_changed_after_snapshot_cannot_be_approved(self):
        source = self.add_baseline()
        stale_request = self.decision("synthetic-stale-source-context")
        before_revision = self.rev()
        before_state = (self.root / workspace.STORE).read_bytes()
        (self.root / source["path"]).write_text("Synthetic source changed after the user saw the review.", encoding="utf-8")
        self.assertEqual(self.rev(), before_revision)
        view = workspace.snapshot(self.root)
        self.assertEqual(view["gates"][0]["target_hashes"], stale_request["target_hashes"])
        self.assertNotEqual(view["gates"][0]["context_hash"], stale_request["context_hash"])
        with self.assertRaises(workspace.Conflict):
            workspace.decide(self.root, stale_request)
        self.assertEqual(before_revision, self.rev())
        self.assertEqual(before_state, (self.root / workspace.STORE).read_bytes())
        workspace.decide(self.root, self.decision("synthetic-refreshed-source-context"))
        self.assertEqual(workspace.snapshot(self.root)["gates"][0]["status"], "approved")

    def test_upstream_bytes_changed_after_snapshot_cannot_be_revised_or_approved(self):
        (self.root / "src/synthetic-qa.txt").write_text("Synthetic stable review target.", encoding="utf-8")
        workspace.configure(self.root, {"expected_revision": self.rev(), "gates": [{"id": "QA", "task_id": "TASK-002", "paths": ["src/synthetic-qa.txt"]}]})
        gate = next(g for g in workspace.snapshot(self.root)["gates"] if g["id"] == "QA")
        payload = {**self.decision("synthetic-stale-ancestor-context"), "gate_id": "QA", "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]}
        before_revision = self.rev()
        before_state = (self.root / workspace.STORE).read_bytes()
        (self.root / "src/policy.py").write_text("POLICY = 'updated-after-review-snapshot'\n", encoding="utf-8")
        for decision in ("approve", "request_revision"):
            with self.subTest(decision=decision), self.assertRaises(workspace.Conflict):
                workspace.decide(self.root, {**payload, "decision": decision})
        self.assertEqual(before_revision, self.rev())
        self.assertEqual(before_state, (self.root / workspace.STORE).read_bytes())


    def test_current_approval_has_explicit_validation_evidence_edge(self):
        workspace.decide(self.root, self.decision())
        view = workspace.snapshot(self.root)
        edges = [e for e in view["relations"] if e["type"] == "validates"]
        self.assertEqual(len(edges), 1)
        self.assertEqual(edges[0]["target"], "file:src/policy.py")
        self.assertTrue(edges[0]["evidence"].startswith("reports/workspace-decisions/DEC-"))
        report = self.root / edges[0]["source"].removeprefix("file:")
        report.write_text("{}\n", encoding="utf-8")
        self.assertFalse([e for e in workspace.snapshot(self.root)["relations"] if e["type"] == "validates"])
        (self.root / "src/policy.py").write_text("POLICY = 'changed'\n", encoding="utf-8")
        self.assertFalse([e for e in workspace.snapshot(self.root)["relations"] if e["type"] == "validates"])

    def test_requirement_source_full_path_is_traced_without_fuzzy_matches(self):
        original = Path(self.temp.name) / "synthetic-rfp.md"
        original.write_text("# Synthetic RFP\nOwner policy\n", encoding="utf-8")
        lifecycle.ingest(self.root, original, actor="synthetic-fixture")
        manifest = json.loads((self.root / "inputs/manifest.json").read_text(encoding="utf-8"))
        source = manifest["inputs"][0]
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "requirements": [
            {"source": source["path"] + "#owner-policy", "description": "Explicit file reference", "status": "proposed"},
            {"source": source["input_id"] + "0", "description": "Different source identifier", "status": "proposed"}]})
        links = [e for e in workspace.snapshot(self.root)["relations"]
                 if e["source"] == "file:" + source["path"] and e["type"] == "references"]
        self.assertEqual([e["target"] for e in links], ["REQ-001"])

    def test_direct_task_modified_receipt_is_stale_and_blocks_downstream(self):
        patch = lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "technical-writer", "owner": "Synthetic downstream", "status": "todo", "next_action": "Publish validated result"}]})
        downstream = patch["assigned"]["tasks"][0]
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": downstream, "stage": "handover", "depends_on": ["TASK-002"]}]})
        command = self.start_consumer()
        relative = "src/synthetic-direct-result.txt"
        evidence = self.root / relative
        evidence.write_text("Synthetic output actually produced by the direct worker.", encoding="utf-8")
        finished = self.host("completed", command_id=command["id"], evidence_refs=[relative])
        direct = next(t for t in finished["snapshot"]["tasks"] if t["id"] == "TASK-002")
        self.assertEqual(direct["status"], "done")
        self.assertEqual(direct["progress"], 100)
        self.assertTrue(direct["receipt_valid"])
        self.assertTrue(any(c["task_id"] == downstream and c["status"] == "queued" for c in finished["snapshot"]["commands"]))
        for mode in ("modified", "deleted"):
            with self.subTest(mode=mode):
                if mode == "modified":
                    evidence.write_text("Synthetic output changed after host completion.", encoding="utf-8")
                else:
                    evidence.unlink()
                view = workspace.snapshot(self.root)
                stale = next(t for t in view["tasks"] if t["id"] == "TASK-002")
                self.assertEqual(stale["status"], "stale")
                self.assertIsNone(stale["progress"])
                self.assertFalse(stale["receipt_valid"])
                self.assertIn(relative, stale["blocked_reason"])
                child = next(t for t in view["tasks"] if t["id"] == downstream)
                self.assertEqual(child["status"], "blocked")
                self.assertFalse(child["ready"])
                self.assertIsNone(self.host("claim", role="technical-writer", task_id=downstream)["command"])
        # A read projection must not silently rewrite the historical CSV receipt.
        self.assertEqual(next(r for r in starter.read_table(self.root, "tasks") if r["task_id"] == "TASK-002")["status"], "done")

    def test_legacy_done_task_without_host_receipts_retains_its_state(self):
        relative = "src/synthetic-legacy.txt"
        (self.root / relative).write_text("Synthetic legacy evidence.", encoding="utf-8")
        patch = lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "backend", "owner": "Synthetic legacy", "status": "done", "next_action": "Legacy completed task", "evidence_refs": relative}]})
        identifier = patch["assigned"]["tasks"][0]
        (self.root / relative).write_text("Synthetic edited legacy evidence.", encoding="utf-8")
        legacy = next(t for t in workspace.snapshot(self.root)["tasks"] if t["id"] == identifier)
        self.assertEqual(legacy["status"], "done")
        self.assertEqual(legacy["progress"], 100)
        self.assertNotIn("receipt_valid", legacy)

    def test_read_grace_waits_for_real_engine_lock_without_removing_it(self):
        acquired, release = threading.Event(), threading.Event()
        intact = []

        def hold():
            with lock(self.root):
                path = self.root / "state/write.lock"
                before = path.read_bytes()
                acquired.set()
                release.wait(3)
                intact.append(path.read_bytes() == before)

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(acquired.wait(2))
        timer = threading.Timer(0.12, release.set)
        timer.start()
        try:
            view = workspace_server._read_with_lock_grace(self.root, lambda: workspace.diff(self.root, "src/policy.py"))
            self.assertEqual(view["content"].splitlines(), ["POLICY = 'owner'"])
        finally:
            release.set()
            holder.join(3)
            timer.cancel()
            timer.join(3)
        self.assertEqual(intact, [True])

    def test_read_grace_timeout_keeps_other_owners_lock_intact(self):
        acquired, release = threading.Event(), threading.Event()

        def hold():
            with lock(self.root):
                acquired.set()
                release.wait(3)

        holder = threading.Thread(target=hold)
        holder.start()
        self.assertTrue(acquired.wait(2))
        path = self.root / "state/write.lock"
        before = path.read_bytes()
        try:
            started = time.monotonic()
            with self.assertRaises(ValueError):
                workspace_server._read_with_lock_grace(self.root, lambda: workspace.snapshot(self.root), timeout=0.08)
            self.assertLess(time.monotonic() - started, 0.5)
            self.assertEqual(path.read_bytes(), before)
        finally:
            release.set()
            holder.join(3)

    def test_read_grace_never_retries_pending_recovery_or_context_conflict(self):
        calls = []

        def stale():
            calls.append("stale")
            raise workspace.Conflict("Synthetic stale revision")

        with self.assertRaises(workspace.Conflict):
            workspace_server._read_with_lock_grace(self.root, stale)
        self.assertEqual(calls, ["stale"])
        pending = self.root / "state/pending.json"
        pending.write_text('{"schema_version":1,"entries":[]}', encoding="utf-8")

        def recovering():
            calls.append("pending")
            return workspace.snapshot(self.root)

        try:
            with self.assertRaises(ValueError):
                workspace_server._read_with_lock_grace(self.root, recovering)
            self.assertEqual(calls, ["stale", "pending"])
            self.assertTrue(pending.exists())
        finally:
            pending.unlink()


if __name__ == "__main__":
    unittest.main()
