"""Real host lifecycle metadata must not fabricate verified business completion."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from tools import lifecycle, native_observation as native, runs, starter, workspace


class NativeObservationTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="native-observation-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temp.name), "native", "Synthetic native metadata", "product"))
        (self.root / "input.txt").write_text("stable input", encoding="utf-8")
        lifecycle.apply_patch(self.root, {"expected_revision": workspace.revision(self.root),
            "tasks": [{"role": "backend", "next_action": "API task"}, {"role": "qa", "next_action": "Contract QA"}],
            "requirements": [{"description": "Native metadata requirement"}]})
        self.index = 0

    def event(self, kind, *, provider="claude", session="session-a", native_id=None, **fields):
        self.index += 1
        payload = {"event_id": "event-" + str(self.index), "kind": kind,
                   "session_id": session, "source": "host_hook", **fields}
        if native_id is not None:
            payload["native_agent_id"] = native_id
        return native.ingest(self.root, provider, payload)

    def ticket(self, task="TASK-001", role="backend", session="session-a", provider="claude"):
        return native.prepare_delegation(self.root, task, role, provider, session)

    def task(self, identifier="TASK-001"):
        return next(t for t in workspace.snapshot(self.root)["tasks"] if t["id"] == identifier)

    def test_ticket_pre_agent_start_stop_post_agent_keeps_terminal_worker_and_call(self):
        ticket = self.ticket()
        self.event("delegation_started", call_id="call-1", delegation_token=ticket["token"], native_type="ieum-backend")
        self.event("agent_started", native_id="actual-worker", native_type="ieum-backend")
        self.event("agent_completed", native_id="actual-worker", native_type="ieum-backend")
        self.event("delegation_bound", native_id="actual-worker", call_id="call-1", delegation_token=ticket["token"])
        worker = next(a for a in native.read(self.root)["agents"] if a["native_agent_id"] == "actual-worker")
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["task_id"], "TASK-001")
        self.assertEqual(worker["role"], "backend")
        self.assertTrue(worker["finished_at"])
        call = native._read(self.root)["calls"]["claude:session-a:call-1"]
        self.assertEqual(call["status"], "completed")
        self.assertEqual(self.task()["native_agent_id"], "actual-worker")

    def test_duplicate_event_is_idempotent_and_conflicting_payload_is_rejected(self):
        event = {"event_id": "duplicate", "kind": "session_started", "session_id": "session-a", "source": "host_hook"}
        first = native.ingest(self.root, "claude", event)
        second = native.ingest(self.root, "claude", event)
        self.assertFalse(first["unchanged"])
        self.assertTrue(second["unchanged"])
        self.assertEqual(native.read(self.root)["seq"], 1)
        with self.assertRaises(ValueError):
            native.ingest(self.root, "claude", {**event, "kind": "session_idle"})
        self.assertEqual(native.read(self.root)["seq"], 1)

    def test_actual_worker_ids_are_scoped_by_provider_and_session(self):
        for provider, session in (("claude", "session-a"), ("claude", "session-b"), ("codex", "session-a")):
            ticket = self.ticket(provider=provider, session=session)
            self.event("agent_started", provider=provider, session=session, native_id="same-worker", delegation_token=ticket["token"])
        workers = native.read(self.root)["agents"]
        self.assertEqual(len(workers), 3)
        self.assertEqual(len({w["id"] for w in workers}), 3)
        self.assertEqual({w["native_agent_id"] for w in workers}, {"same-worker"})

    def test_event_ids_from_distinct_sessions_do_not_collide(self):
        for provider, session in (("claude", "session-a"), ("claude", "session-b"), ("codex", "session-a")):
            native.ingest(self.root, provider, {"event_id": "host-event-1", "kind": "session_started", "session_id": session, "source": "host_hook"})
        self.assertEqual(len(native.read(self.root)["sessions"]), 3)

    def test_ticket_cannot_be_rebound_to_another_worker_or_resolved_by_another_session(self):
        ticket = self.ticket()
        self.event("agent_started", native_id="worker-a", delegation_token=ticket["token"])
        with self.assertRaises(ValueError):
            self.event("agent_started", native_id="worker-b", delegation_token=ticket["token"])
        self.assertIsNone(native.resolve_delegation(self.root, "claude", "session-b", ticket["token"]))
        self.assertIsNone(native.resolve_delegation(self.root, "codex", "session-a", ticket["token"]))
        self.assertEqual(native.resolve_delegation(self.root, "claude", "session-a", ticket["token"])["native_agent_id"], "worker-a")

    def test_unknown_role_has_no_task_guess_even_if_single_task_matches_native_type(self):
        self.event("agent_started", native_id="unknown-worker", native_type="unknown-role")
        worker = native.read(self.root)["agents"][0]
        self.assertEqual(worker["role"], "unassigned")
        self.assertIsNone(worker["task_id"])
        self.assertNotIn("native_agent_id", self.task())

    def test_two_pending_same_role_calls_are_not_guessed(self):
        one = self.ticket("TASK-001", "backend")
        two = self.ticket("TASK-002", "backend")
        for index, ticket in enumerate((one, two)):
            self.event("delegation_started", call_id="call-" + str(index), delegation_token=ticket["token"], native_type="ieum-backend")
        self.event("agent_started", native_id="ambiguous-worker", native_type="ieum-backend")
        worker = native.read(self.root)["agents"][0]
        self.assertIsNone(worker["task_id"])
        self.assertEqual(worker["native_agent_id"], "ambiguous-worker")

    def test_primary_focus_changes_without_assigning_the_old_task_to_new_activity(self):
        native.focus(self.root, "TASK-001", "backend", "codex", "primary-session")
        self.event("tool_started", provider="codex", session="primary-session", tool_name="exec_command")
        self.assertEqual(self.task()["execution_state"], "running")
        native.focus(self.root, "TASK-002", "qa", "codex", "primary-session")
        self.event("tool_started", provider="codex", session="primary-session", tool_name="exec_command")
        snap = workspace.snapshot(self.root)
        tasks = {t["id"]: t for t in snap["tasks"]}
        self.assertEqual(tasks["TASK-002"]["native_agent_id"], "primary-session")
        self.assertEqual(tasks["TASK-002"]["execution_state"], "running")
        self.assertEqual(tasks["TASK-001"]["active_native_agent_ids"], [])
        self.assertEqual(tasks["TASK-001"]["ledger_status"], "todo")
        self.assertEqual(tasks["TASK-001"]["status"], "todo")

    def test_native_end_is_result_pending_and_does_not_complete_a_task(self):
        ticket = self.ticket()
        revision = workspace.revision(self.root)
        self.event("agent_started", native_id="worker", delegation_token=ticket["token"])
        self.assertEqual(self.task()["status"], "running")
        self.event("agent_completed", native_id="worker")
        task = self.task()
        self.assertTrue(task["result_pending"])
        self.assertEqual(task["execution_state"], "completed")
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["ledger_status"], "todo")
        self.assertNotEqual(task["verification_status"], "passed")
        self.assertEqual(workspace.snapshot(self.root)["summary_counts"]["completed"], 0)
        self.assertEqual(workspace.revision(self.root), revision)
        self.assertEqual(starter.read_table(self.root, "tasks")[0]["status"], "todo")

    def test_tool_completion_does_not_mark_agent_or_task_complete(self):
        ticket = self.ticket()
        self.event("agent_started", native_id="worker", delegation_token=ticket["token"])
        self.event("tool_started", native_id="worker", tool_name="Edit")
        self.event("tool_completed", native_id="worker", tool_name="Edit")
        self.assertEqual(self.task()["execution_state"], "running")
        self.assertEqual(self.task()["status"], "running")
        self.assertEqual(starter.read_table(self.root, "tasks")[0]["status"], "todo")

    def test_failed_run_ledger_survives_native_success(self):
        plan = runs.create(self.root, {"expected_revision": workspace.revision(self.root), "key": "failed", "title": "Failed execution",
            "requirement_ids": ["REQ-001"], "bindings": {"STEP-001": "TASK-001"}, "steps": [
            {"id": "STEP-001", "title": "API task", "inputs": ["input.txt"], "depends_on": []}]})
        runs.result(self.root, {"expected_revision": workspace.revision(self.root), "run_id": plan["run_id"], "step_id": "STEP-001",
            "key": "failure", "status": "failed", "evidence_refs": [], "notes": "Synthetic failure"})
        ticket = self.ticket()
        self.event("agent_started", native_id="worker", delegation_token=ticket["token"])
        self.event("agent_completed", native_id="worker")
        task = self.task()
        self.assertEqual(task["ledger_status"], "failed")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(workspace.snapshot(self.root)["summary_counts"]["failed"], 1)

    def test_stale_receipts_survive_native_success(self):
        plan = runs.create(self.root, {"expected_revision": workspace.revision(self.root), "key": "stale", "title": "Stale execution",
            "requirement_ids": ["REQ-001"], "bindings": {"STEP-001": "TASK-001"}, "steps": [
            {"id": "STEP-001", "title": "API task", "inputs": ["input.txt"], "depends_on": [], "expected_outputs": ["output.txt"]}]})
        for status in ("running", "succeeded"):
            (self.root / "output.txt").write_text("verified output", encoding="utf-8")
            runs.result(self.root, {"expected_revision": workspace.revision(self.root), "run_id": plan["run_id"], "step_id": "STEP-001",
                "key": status, "status": status, "evidence_refs": ["output.txt"] if status == "succeeded" else [], "notes": "Synthetic execution"})
        (self.root / "output.txt").write_text("changed output", encoding="utf-8")
        ticket = self.ticket()
        self.event("agent_started", native_id="worker", delegation_token=ticket["token"])
        self.event("agent_completed", native_id="worker")
        task = self.task()
        self.assertEqual(task["ledger_status"], "stale")
        self.assertEqual(task["status"], "stale")
        self.assertEqual(workspace.snapshot(self.root)["summary_counts"]["completed"], 0)
        self.assertEqual(workspace.snapshot(self.root)["summary_counts"]["stale"], 1)

    def test_raw_prompt_result_reasoning_and_unknown_fields_are_rejected_without_writes(self):
        self.event("session_started")
        before = (self.root / native.STORE).read_bytes()
        for field in ("prompt", "arguments", "result", "transcript_path", "reasoning", "encrypted_content"):
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.event("tool_started", **{field: "Synthetic private text"})
            self.assertEqual((self.root / native.STORE).read_bytes(), before)

    def test_old_generation_native_end_does_not_overlay_rebound_task(self):
        def plan(key):
            return runs.create(self.root, {"expected_revision": workspace.revision(self.root), "key": key, "title": key,
                "requirement_ids": ["REQ-001"], "bindings": {"STEP-001": "TASK-001"}, "steps": [
                {"id": "STEP-001", "title": "API task", "inputs": ["input.txt"], "depends_on": []}]})
        old = plan("first")
        ticket = self.ticket()
        self.event("agent_started", native_id="old-worker", delegation_token=ticket["token"])
        latest = plan("second")
        self.event("agent_completed", native_id="old-worker")
        task = self.task()
        self.assertEqual(task["run_id"], latest["run_id"])
        self.assertEqual(task["status"], "todo")
        self.assertEqual(task["ledger_status"], "todo")
        self.assertFalse(task.get("result_pending", False))
        self.assertEqual(task.get("active_native_agent_ids"), [])
        self.assertNotIn("native_agent_id", task)
        actor = next(a for a in workspace.snapshot(self.root)["agents"] if a["native_agent_id"] == "old-worker")
        self.assertTrue(actor["superseded"])
        self.assertIn(old["run_id"] + "/STEP-001", actor["execution_ref"])

    def test_completed_or_idle_native_state_is_waiting_even_if_ledger_still_doing(self):
        lifecycle.apply_patch(self.root, {"expected_revision": workspace.revision(self.root), "tasks": [
            {"task_id": "TASK-001", "status": "doing"}]})
        ticket = self.ticket()
        self.event("agent_started", native_id="worker", delegation_token=ticket["token"])
        self.event("agent_completed", native_id="worker")
        snap = workspace.snapshot(self.root)
        task = next(t for t in snap["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(task["ledger_status"], "running")
        self.assertTrue(task["result_pending"])
        self.assertEqual(snap["summary_counts"]["running"], 0)
        self.assertEqual(snap["summary_counts"]["completed"], 0)
        self.assertEqual(snap["summary_counts"]["pending"], 2)
        counts = workspace._summary_counts([{"status": "running", "ledger_status": "running", "execution_state": "idle"}])
        self.assertEqual(counts["running"], 0)
        self.assertEqual(counts["pending"], 1)

    def test_older_rollout_start_cannot_reopen_worker_after_newer_hook_stop(self):
        ticket = self.ticket(provider="codex")
        later = "2026-01-01T00:00:02+00:00"
        self.event("agent_completed", provider="codex", native_id="worker", delegation_token=ticket["token"], at=later)
        self.event("agent_started", provider="codex", native_id="worker", delegation_token=ticket["token"],
                   source="codex_rollout", at="2026-01-01T00:00:01+00:00")
        worker = next(a for a in native.read(self.root)["agents"] if a["native_agent_id"] == "worker")
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(worker["last_seen"], later)
        self.assertEqual(worker["finished_at"], later)
        self.assertEqual(self.task()["execution_state"], "completed")
        self.assertTrue(self.task()["result_pending"])

    def test_direct_task_new_generation_isolates_old_worker_completion(self):
        from tools.storage import lock
        ticket = self.ticket()
        self.event("agent_started", native_id="old-worker", delegation_token=ticket["token"])
        # Synthetic fixture for the engine's direct-TASK generation increment.
        with lock(self.root):
            store = workspace.read(self.root)
            store["tasks"]["TASK-001"]["generation"] = 2
            workspace.save(self.root, store, "synthetic-generation", "test", {"task_id": "TASK-001"})
        self.event("agent_completed", native_id="old-worker")
        task = self.task()
        self.assertEqual(task["status"], "todo")
        self.assertFalse(task.get("result_pending", False))
        self.assertEqual(task["active_native_agent_ids"], [])
        actor = next(a for a in workspace.snapshot(self.root)["agents"] if a["native_agent_id"] == "old-worker")
        self.assertTrue(actor["superseded"])
        self.assertEqual(actor["execution_ref"], "task:TASK-001:g1")

    def test_host_uuid_sessions_remain_distinct_in_typed_snapshot_records(self):
        sessions = ["12345678-1234-4321-8321-123456789abc", "87654321-4321-4321-8321-abcdef123456"]
        for task, sid in zip(("TASK-001", "TASK-002"), sessions):
            ticket = self.ticket(task, "backend", session=sid)
            self.event("agent_started", session=sid, native_id="actual-worker", delegation_token=ticket["token"])
        workspace.configure(self.root, {"expected_revision": workspace.revision(self.root), "current_scope": {"title": "First task", "task_ids": ["TASK-001"]}})
        snap = workspace.snapshot(self.root)
        self.assertEqual({a["session_id"] for a in snap["agents"]}, set(sessions))
        self.assertEqual({a["session_id"] for a in snap["native_sessions"]}, set(sessions))
        self.assertEqual({t["session_id"] for t in snap["tasks"]}, set(sessions))
        self.assertEqual(snap["history_tasks"][0]["session_id"], sessions[1])
        self.assertEqual({a["session_id"] for a in snap["activities"]}, set(sessions))
        self.assertEqual({a["session_id"] for a in snap["activity"]}, set(sessions))

    def test_raw_log_and_nested_session_credentials_remain_redacted(self):
        sid = "12345678-1234-4321-8321-123456789abc"
        raw = {"logs": [{"source": "host_hook", "session_id": sid, "details": {"session_id": "private-cookie"},
                         "message": 'session_id="private-cookie"'}],
               "debug": {"session_id": sid}, "agents": [{"source": "host_hook", "session_id": sid,
               "details": {"session_id": "private-cookie"}}]}
        public = native.public_snapshot(raw)
        self.assertEqual(public["agents"][0]["session_id"], sid)
        self.assertEqual(public["logs"][0]["session_id"], "[redacted]")
        self.assertEqual(public["logs"][0]["details"]["session_id"], "[redacted]")
        self.assertEqual(public["debug"]["session_id"], "[redacted]")
        self.assertEqual(public["agents"][0]["details"]["session_id"], "[redacted]")
        self.assertNotIn("private-cookie", json.dumps(public))

    def test_non_uuid_or_untrusted_source_session_ids_are_not_restored(self):
        sid = "12345678-1234-4321-8321-123456789abc"
        public = native.public_snapshot({"agents": [
            {"source": "host_hook", "session_id": "private-cookie-value"},
            {"source": "host_report", "session_id": sid},
            {"source": "user_document", "session_id": sid}]})
        self.assertEqual([a["session_id"] for a in public["agents"]], ["[redacted]"] * 3)

    def test_native_start_end_counts_one_unreported_attempt_and_tools_do_not_add_cost(self):
        sid = "12345678-1234-4321-8321-123456789abc"
        ticket = self.ticket(session=sid)
        self.event("agent_started", session=sid, native_id="worker", delegation_token=ticket["token"])
        self.event("tool_started", session=sid, native_id="worker", tool_name="Read")
        self.event("tool_completed", session=sid, native_id="worker", tool_name="Read")
        self.event("agent_completed", session=sid, native_id="worker")
        usage = workspace.snapshot(self.root)["usage"]
        self.assertEqual(usage["total_attempts"], 1)
        self.assertEqual(usage["native_unreported_attempts"], 1)
        self.assertEqual(usage["unreported_attempts"], 1)
        self.assertEqual(usage["coverage"], "unknown")
        self.assertIsNone(usage["tokens"])
        self.assertIsNone(usage["cost_usd"])
        self.assertIsNone(usage["known_tokens"])

    def test_native_connected_tracks_active_execution_and_keeps_data_history_after_end(self):
        ticket = self.ticket()
        self.event("agent_started", native_id="worker", delegation_token=ticket["token"])
        self.assertTrue(workspace.snapshot(self.root)["host"]["native_connected"])
        self.event("agent_completed", native_id="worker")
        snap = workspace.snapshot(self.root)
        self.assertFalse(snap["host"]["native_connected"])
        self.assertTrue(snap["host"]["data_connected"])
        self.assertEqual(snap["host"]["execution_mode"], "native")
        self.assertEqual(snap["agents"][0]["status"], "completed")

    def test_legacy_activity_reporting_retains_active_connection_compatibility(self):
        fields = {"agent_id": "legacy-reporter", "provider": "codex", "role": "backend", "task_id": "TASK-001", "summary": "Synthetic activity"}
        workspace.activity_event(self.root, {**fields, "event_id": "legacy-start", "kind": "started"})
        self.assertTrue(workspace.snapshot(self.root)["host"]["native_connected"])
        workspace.activity_event(self.root, {**fields, "event_id": "legacy-end", "kind": "completed"})
        snap = workspace.snapshot(self.root)
        self.assertFalse(snap["host"]["native_connected"])
        self.assertTrue(snap["host"]["data_connected"])
        self.assertTrue(snap["activities"])

    def test_new_prompt_prepares_session_without_reopening_previous_completed_task(self):
        lifecycle.apply_patch(self.root, {"expected_revision": workspace.revision(self.root), "tasks": [
            {"task_id": "TASK-001", "status": "done", "evidence_refs": "input.txt", "next_action": "없음"}]})
        native.focus(self.root, "TASK-001", "backend", "claude", "session-a")
        self.event("tool_started", tool_name="Write")
        self.event("session_idle")
        self.assertEqual(self.task()["status"], "done")
        self.event("session_started")
        snap = workspace.snapshot(self.root)
        self.assertEqual(self.task()["status"], "done")
        self.assertEqual(snap["summary_counts"]["completed"], 1)
        self.assertEqual(snap["summary_counts"]["running"], 0)
        actor = next(a for a in snap["agents"] if a["native_agent_id"] == "session-a")
        self.assertEqual(actor["status"], "preparing")
        self.assertIsNone(actor["task_id"])
        self.assertNotIn("claude:session-a", native._read(self.root)["focus"])

    def test_failed_delegation_is_visible_without_fabricating_worker_or_approval(self):
        ticket = self.ticket()
        self.event("delegation_started", call_id="failed-call", delegation_token=ticket["token"], native_type="ieum-backend")
        self.event("delegation_failed", call_id="failed-call", delegation_token=ticket["token"])
        snap = workspace.snapshot(self.root)
        task = next(t for t in snap["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(task["ledger_status"], "todo")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["execution_state"], "failed")
        self.assertEqual(snap["summary_counts"]["failed"], 1)
        self.assertEqual(snap["agents"], [])
        self.assertEqual(snap["gates"], [])
        self.assertEqual(workspace.read(self.root)["decisions"], [])
        self.assertEqual(starter.read_table(self.root, "tasks")[0]["status"], "todo")
        self.assertNotIn("native_agent_id", task)
        retry = self.ticket()
        self.event("delegation_started", call_id="retry-call", delegation_token=retry["token"], native_type="ieum-backend")
        self.event("agent_started", native_id="retry-worker", call_id="retry-call", delegation_token=retry["token"])
        self.assertEqual(self.task()["status"], "running")
        self.assertEqual(self.task()["native_agent_id"], "retry-worker")

    def test_older_session_start_cannot_clear_current_primary_focus(self):
        native.focus(self.root, "TASK-002", "qa", "codex", "primary-session")
        self.event("tool_started", provider="codex", session="primary-session", tool_name="exec_command", at="2026-01-01T00:00:02+00:00")
        self.event("session_started", provider="codex", session="primary-session", source="codex_rollout", at="2026-01-01T00:00:01+00:00")
        state = native._read(self.root)
        self.assertEqual(state["focus"]["codex:primary-session"]["task_id"], "TASK-002")
        actor = next(a for a in state["agents"].values() if a["native_agent_id"] == "primary-session")
        self.assertEqual(actor["task_id"], "TASK-002")
        self.assertEqual(actor["status"], "running")
        self.assertEqual(self.task("TASK-002")["execution_state"], "running")

    def test_tool_name_metadata_is_allowlisted_without_retaining_arbitrary_name(self):
        self.event("tool_started", native_id="worker", tool_name="Write")
        first = native.read(self.root)["events"][-1]
        self.assertEqual(first["tool_name"], "Write")
        self.assertTrue(first["summary"].startswith("Write · "))
        self.event("tool_completed", native_id="worker", tool_name="private_customer_secret_tool")
        event = native.read(self.root)["events"][-1]
        self.assertEqual(event["tool_name"], "local_tool")
        self.assertNotIn("private_customer_secret_tool", json.dumps(native.read(self.root)))

    def test_verified_done_progress_survives_lingering_primary_and_child_execution(self):
        plan = runs.create(self.root, {"expected_revision": workspace.revision(self.root), "key": "verified-done", "title": "Verified API",
            "requirement_ids": ["REQ-001"], "bindings": {"STEP-001": "TASK-001"}, "steps": [
            {"id": "STEP-001", "title": "API task", "inputs": ["input.txt"], "depends_on": [], "expected_outputs": ["output.txt"]}]})
        for status in ("running", "succeeded"):
            if status == "succeeded":
                (self.root / "output.txt").write_text("actual verified receipt", encoding="utf-8")
            runs.result(self.root, {"expected_revision": workspace.revision(self.root), "run_id": plan["run_id"], "step_id": "STEP-001",
                "key": "verified-" + status, "status": status, "evidence_refs": ["output.txt"] if status == "succeeded" else [], "notes": "Actual fixture evidence"})
        self.assertTrue(runs.inspect(self.root, plan["run_id"])["runs"][0]["steps"][0]["valid"])
        native.focus(self.root, "TASK-001", "backend", "claude", "session-a")
        self.event("tool_started", tool_name="Read")
        ticket = self.ticket()
        self.event("agent_started", native_id="lingering-child", delegation_token=ticket["token"])
        snap = workspace.snapshot(self.root)
        task = next(t for t in snap["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(task["ledger_status"], "done")
        self.assertEqual(task["status"], "done")
        self.assertEqual(task["progress"], 100)
        self.assertEqual(task["execution_state"], "running")
        self.assertEqual(task["native_agent_id"], "lingering-child")
        self.assertEqual(len(task["active_native_agent_ids"]), 2)
        self.assertEqual(snap["summary_counts"]["completed"], 1)
        self.assertEqual(snap["summary_counts"]["completion_percent"], 100)
        self.assertEqual(snap["summary_counts"]["running"], 0)
        self.assertFalse(task.get("result_pending", False))
        self.assertTrue(runs.inspect(self.root, plan["run_id"])["runs"][0]["steps"][0]["valid"])

    def test_unknown_task_and_role_focus_are_rejected(self):
        for task, role in (("TASK-999", "backend"), ("TASK-001", "nonexistent-role")):
            with self.subTest(task=task, role=role), self.assertRaises(ValueError):
                self.ticket(task, role)
            with self.assertRaises(ValueError):
                native.focus(self.root, task, role, "claude", "session-a")


if __name__ == "__main__":
    unittest.main()
