"""HTTP and engine integration tests; all human decisions are synthetic fixtures."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tools import lifecycle, runs, starter, workspace, workspace_server


class WorkspaceIntegrationTests(unittest.TestCase):
    def setUp(self):
        base = starter.ROOT / ".test-output"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="workspace-integration-", dir=base)
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = starter.init_project(Path(self.temp.name), "project", "Synthetic workspace", "product", profile="api")
        self.root = Path(self.root)
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src/contract.py").write_text("POLICY = 'draft'\n", encoding="utf-8")
        result = lifecycle.apply_patch(self.root, {"expected_revision": self.rev(),
            "requirements": [{"source": "Synthetic requirement", "description": "Own order access", "priority": "must", "acceptance_criteria": "Only owner may access", "status": "proposed"}],
            "tasks": [{"role": "backend", "owner": "Synthetic worker", "status": "todo", "next_action": "Review API contract"},
                      {"role": "qa", "owner": "Synthetic reviewer", "status": "todo", "next_action": "Validate API contract"}]})
        task_ids = result["assigned"]["tasks"]
        self.producer, self.consumer = task_ids
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": self.producer, "title": "API contract", "stage": "implementation"},
            {"id": self.consumer, "title": "Contract validation", "stage": "verification", "depends_on": [self.producer]}],
            "gates": [{"id": "GATE-001", "task_id": self.producer, "paths": ["src/contract.py"]}]})
        self.server = workspace_server.make_server(self.root, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = self.server.origin

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def rev(self):
        entries = lifecycle.events(self.root)
        return entries[-1]["revision"] if entries else None

    def request(self, path, payload=None, *, token="human", origin=True, extra_headers=None):
        headers = {}
        if token:
            headers["Authorization"] = "Bearer " + (self.server.human_token if token == "human" else self.server.host_token if token == "host" else token)
        if payload is not None:
            headers["Content-Type"] = "application/json"
            if origin:
                headers["Origin"] = self.origin if origin is True else origin
        headers.update(extra_headers or {})
        request = Request(self.origin + path, headers=headers, data=json.dumps(payload).encode() if payload is not None else None)
        try:
            with urlopen(request, timeout=5) as response:
                body = response.read()
                return response.status, json.loads(body) if body else None
        except HTTPError as error:
            body = error.read()
            try:
                body = json.loads(body)
            except ValueError:
                body = body.decode(errors="replace")
            return error.code, body

    def snapshot(self):
        status, data = self.request("/api/snapshot")
        self.assertEqual(status, 200, data)
        return data

    def decision(self, action_id="synthetic-approve", decision="approve", **overrides):
        view = self.snapshot()
        gate = next(g for g in view["gates"] if g["id"] == "GATE-001")
        payload = {"action_id": action_id, "expected_revision": view["revision"], "gate_id": gate["id"], "decision": decision,
                   "actor": "Synthetic human fixture", "feedback": "Synthetic decision only; no customer approval", "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]}
        payload.update(overrides)
        return payload

    def test_reviewed_upstream_context_cannot_change_without_revision(self):
        upstream = self.root / "src/upstream.md"
        upstream.write_text("original design\n", encoding="utf-8")
        result = lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "architect", "owner": "Synthetic architect", "status": "done", "next_action": "Source design", "evidence_refs": "src/upstream.md"}]})
        source_id = result["assigned"]["tasks"][0]
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": self.producer, "stage": "implementation", "depends_on": [source_id]}]})
        payload = self.decision(action_id="synthetic-context-race")
        original_revision = self.rev()
        original_file_hash = workspace.file_hash(self.root, "src/contract.py")
        upstream.write_text("changed design after human loaded the Diff\n", encoding="utf-8")
        self.assertEqual(self.rev(), original_revision)
        self.assertEqual(workspace.file_hash(self.root, "src/contract.py"), original_file_hash)
        status, body = self.request("/api/action", payload)
        self.assertEqual(status, 409, body)
        self.assertEqual(self.rev(), original_revision)
        self.assertEqual(workspace.read(self.root)["decisions"], [])
        fresh = self.decision(action_id="synthetic-context-fresh")
        status, body = self.request("/api/action", fresh)
        self.assertEqual(status, 200, body)

    def test_read_requests_wait_for_short_real_writer_lock(self):
        from tools.storage import lock
        for route in ("/api/snapshot", "/api/diff?path=src/contract.py"):
            with self.subTest(route=route):
                held, release = threading.Event(), threading.Event()
                def writer():
                    with lock(self.root):
                        held.set()
                        release.wait(3)
                owner = threading.Thread(target=writer)
                owner.start()
                self.assertTrue(held.wait(2))
                timer = threading.Timer(0.12, release.set)
                timer.start()
                try:
                    status, body = self.request(route)
                    self.assertEqual(status, 200, body)
                finally:
                    release.set()
                    timer.cancel()
                    owner.join(timeout=3)
                self.assertFalse((self.root / "state/write.lock").exists())

    def test_http_principal_and_origin_boundaries(self):
        for token in (None, "wrong-token", "host"):
            with self.subTest(token=token):
                status, _ = self.request("/api/snapshot", token=token)
                self.assertIn(status, (401, 403))
        payload = self.decision()
        for origin in (False, "https://evil.invalid", "null", self.origin + ".evil.invalid"):
            with self.subTest(origin=origin):
                status, _ = self.request("/api/action", payload, origin=origin)
                self.assertIn(status, (401, 403))
        status, _ = self.request("/api/action", payload, token="host")
        self.assertIn(status, (401, 403))
        self.assertEqual(self.rev(), payload["expected_revision"])

    def test_snapshot_never_leaks_session_credentials_or_fabricates_usage(self):
        view = self.snapshot()
        text = json.dumps(view)
        self.assertNotIn(self.server.human_token, text)
        self.assertNotIn(self.server.host_token, text)
        self.assertIsNone(view["usage"]["tokens"])
        self.assertIsNone(view["usage"]["cost"])
        active = [agent for agent in view["agents"] if agent.get("status") in {"running", "doing"}]
        self.assertEqual(active, [])

    def test_http_traversal_and_unregistered_files_are_rejected(self):
        for path in ("../project.json", "%2e%2e/project.json", "state/events.jsonl", "C:/Windows/win.ini", "registers/reviews.csv"):
            with self.subTest(path=path):
                status, data = self.request("/api/diff?path=" + path)
                self.assertGreaterEqual(status, 400, data)
        status, _ = self.request("/api/snapshot", extra_headers={"Host": "evil.invalid"})
        self.assertIn(status, (400, 403, 421))

    def test_approval_persists_without_stage_or_business_approval(self):
        before_stage = starter.load_project(self.root)[1]["current_stage"]
        before_reviews = starter.read_table(self.root, "reviews")
        before_deliverables = starter.read_table(self.root, "deliverables")
        payload = self.decision()
        status, data = self.request("/api/action", payload)
        self.assertEqual(status, 200, data)
        fresh = workspace.snapshot(self.root)
        self.assertEqual(next(g for g in fresh["gates"] if g["id"] == "GATE-001")["status"], "approved")
        self.assertEqual(starter.load_project(self.root)[1]["current_stage"], before_stage)
        self.assertEqual(starter.read_table(self.root, "reviews"), before_reviews)
        self.assertEqual(starter.read_table(self.root, "deliverables"), before_deliverables)
        self.assertFalse(any(t["status"] == "running" for t in fresh["tasks"]))
        self.assertEqual(fresh["mode"], "local")

    def test_duplicate_http_decision_is_idempotent_but_reused_key_is_not(self):
        payload = self.decision()
        status, data = self.request("/api/action", payload)
        self.assertEqual(status, 200, data)
        after_count = len(lifecycle.events(self.root))
        status, data = self.request("/api/action", payload)
        self.assertEqual(status, 200, data)
        self.assertTrue(data["unchanged"])
        self.assertEqual(len(lifecycle.events(self.root)), after_count)
        changed = {**payload, "decision": "request_revision"}
        status, _ = self.request("/api/action", changed)
        self.assertGreaterEqual(status, 400)
        self.assertEqual(len(lifecycle.events(self.root)), after_count)

    def test_stale_ui_revision_and_file_hash_cannot_approve(self):
        old_payload = self.decision()
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "handoff": "# Synthetic concurrent edit\n"})
        status, _ = self.request("/api/action", old_payload)
        self.assertGreaterEqual(status, 400)
        old_payload = self.decision(action_id="synthetic-hash-check")
        (self.root / "src/contract.py").write_text("POLICY = 'changed-after-display'\n", encoding="utf-8")
        status, _ = self.request("/api/action", old_payload)
        self.assertGreaterEqual(status, 400)
        self.assertNotEqual(next(g for g in workspace.snapshot(self.root)["gates"] if g["id"] == "GATE-001")["status"], "approved")

    def test_approved_file_change_requires_new_decision(self):
        status, data = self.request("/api/action", self.decision())
        self.assertEqual(status, 200, data)
        (self.root / "src/contract.py").write_text("POLICY = 'new-version'\n", encoding="utf-8")
        gate = next(g for g in self.snapshot()["gates"] if g["id"] == "GATE-001")
        self.assertEqual(gate["status"], "stale")
        status, data = self.request("/api/action", self.decision(action_id="synthetic-new-version"))
        self.assertEqual(status, 200, data)
        self.assertEqual(next(g for g in self.snapshot()["gates"] if g["id"] == "GATE-001")["status"], "approved")

    def test_request_revision_records_rework_and_keeps_downstream_blocked(self):
        payload = self.decision(action_id="synthetic-revision", decision="request_revision", feedback="Owner check is missing. Target: contract.py. Accept when unauthorized actors fail.")
        status, data = self.request("/api/action", payload)
        self.assertEqual(status, 200, data)
        view = workspace.snapshot(self.root)
        gate = next(g for g in view["gates"] if g["id"] == "GATE-001")
        self.assertNotEqual(gate["status"], "approved")
        consumer = next(t for t in view["tasks"] if t["id"] == self.consumer)
        self.assertEqual(consumer["status"], "blocked")
        self.assertEqual(starter.load_project(self.root)[1]["current_stage"], "discovery")
        self.assertEqual(starter.read_table(self.root, "reviews"), [])


    def test_existing_run_result_cannot_bypass_human_gate(self):
        run = runs.create(self.root, {"expected_revision": self.rev(), "key": "synthetic-gated-run", "title": "Synthetic gated DAG",
            "requirement_ids": ["REQ-001"], "steps": [
                {"id": "STEP-001", "title": "Build contract", "depends_on": [], "inputs": ["docs/01-discovery.md"], "expected_outputs": ["src/contract.py"]},
                {"id": "STEP-002", "title": "Validate contract", "depends_on": ["STEP-001"], "inputs": ["src/contract.py"], "expected_outputs": []}]})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": self.producer, "title": "API contract", "stage": "implementation", "step_ref": {"run_id": run["run_id"], "step_id": "STEP-001"}},
            {"id": self.consumer, "title": "Contract validation", "stage": "verification", "depends_on": [self.producer], "step_ref": {"run_id": run["run_id"], "step_id": "STEP-002"}}],
            "gates": [{"id": "GATE-001", "task_id": self.producer, "paths": ["src/contract.py"]}]})
        runs.result(self.root, {"expected_revision": self.rev(), "run_id": run["run_id"], "step_id": "STEP-001", "key": "synthetic-build-start",
            "status": "running", "evidence_refs": [], "notes": "Synthetic producer execution started"})
        runs.result(self.root, {"expected_revision": self.rev(), "run_id": run["run_id"], "step_id": "STEP-001", "key": "synthetic-build",
            "status": "succeeded", "evidence_refs": ["src/contract.py"], "notes": "Synthetic generation receipt"})
        for state in ("running", "succeeded"):
            before = self.rev()
            with self.subTest(state=state), self.assertRaises(ValueError):
                runs.result(self.root, {"expected_revision": before, "run_id": run["run_id"], "step_id": "STEP-002", "key": "synthetic-unapproved-" + state,
                    "status": state, "evidence_refs": ["src/contract.py"], "notes": "Synthetic bypass attempt must fail"})
            self.assertEqual(self.rev(), before)
        status, data = self.request("/api/action", self.decision())
        self.assertEqual(status, 200, data)
        result = runs.result(self.root, {"expected_revision": self.rev(), "run_id": run["run_id"], "step_id": "STEP-002", "key": "synthetic-approved-start",
            "status": "running", "evidence_refs": [], "notes": "Synthetic actual worker start"})
        self.assertEqual(result["status"], "running")


    def host_request(self, kind, **fields):
        payload = {"expected_revision": self.rev(), "agent_id": "synthetic-connected-host", "provider": "codex", "role": "qa", "kind": kind, **fields}
        status, data = self.request("/api/host/event", payload, token="host")
        self.assertEqual(status, 200, data)
        return data

    def test_revision_roundtrip_requires_actual_changed_output_and_new_review(self):
        status, data = self.request("/api/action", self.decision(action_id="synthetic-revise-loop", decision="request_revision", feedback="Replace draft policy with owner validation; accept when unauthorized access fails."))
        self.assertEqual(status, 200, data)
        status, _ = self.request("/api/action", self.decision(action_id="synthetic-premature-reapprove"))
        self.assertGreaterEqual(status, 400)
        claim = self.host_request("claim", role="backend")
        command = claim["command"]
        self.assertIsNotNone(command)
        self.assertTrue(command["task_id"].startswith("REWORK-"))
        self.host_request("started", command_id=command["id"], role="backend")
        before = self.rev()
        status, data = self.request("/api/host/event", {"expected_revision": before, "agent_id": "synthetic-connected-host", "provider": "codex", "role": "backend", "kind": "completed", "command_id": command["id"], "evidence_refs": ["src/contract.py"]}, token="host")
        self.assertGreaterEqual(status, 400, data)
        self.assertEqual(self.rev(), before)
        (self.root / "src/contract.py").write_text("POLICY = 'owner-validation-added'\n", encoding="utf-8")
        self.host_request("completed", command_id=command["id"], evidence_refs=["src/contract.py"], role="backend")
        status, data = self.request("/api/action", self.decision(action_id="synthetic-reapprove-after-output"))
        self.assertEqual(status, 200, data)
        self.assertEqual(next(g for g in self.snapshot()["gates"] if g["id"] == "GATE-001")["status"], "approved")
        self.assertEqual(starter.read_table(self.root, "reviews"), [])

    def test_host_completion_queues_next_dependency_level(self):
        patch = lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"task_id": self.producer, "status": "done", "evidence_refs": "src/contract.py"},
            {"role": "technical-writer", "owner": "Synthetic writer", "status": "todo", "next_action": "Publish validation evidence"}]})
        final_task = next(identifier for identifier in patch["assigned"]["tasks"] if identifier not in {self.producer, self.consumer})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [{"id": final_task, "title": "Publish validation evidence", "stage": "handover", "depends_on": [self.consumer]}]})
        status, data = self.request("/api/action", self.decision(action_id="synthetic-three-level-approve"))
        self.assertEqual(status, 200, data)
        claim = self.host_request("claim", task_id=self.consumer)
        command = claim["command"]
        self.assertIsNotNone(command)
        self.assertEqual(command["task_id"], self.consumer)
        self.host_request("started", command_id=command["id"])
        (self.root / "src/validation.py").write_text("VALIDATED = True\n", encoding="utf-8")
        completed = self.host_request("completed", command_id=command["id"], evidence_refs=["src/validation.py"])
        queued = [q for q in completed["snapshot"]["commands"] if q["status"] == "queued"]
        self.assertTrue(any(q["task_id"] == final_task for q in queued), "Finishing a ready task must schedule the next dependency level")


    def test_failed_real_host_task_is_blocked_and_retryable(self):
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"task_id": self.producer, "status": "done", "evidence_refs": "src/contract.py"}]})
        status, data = self.request("/api/action", self.decision(action_id="synthetic-failure-path"))
        self.assertEqual(status, 200, data)
        claim = self.host_request("claim", task_id=self.consumer)
        command = claim["command"]
        self.assertIsNotNone(command)
        self.host_request("started", command_id=command["id"])
        failed = self.host_request("failed", command_id=command["id"], notes="Synthetic execution failure: API unavailable")
        task = next(t for t in failed["snapshot"]["tasks"] if t["id"] == self.consumer)
        self.assertEqual(task["status"], "blocked")
        self.assertFalse(any(a["status"] == "running" for a in failed["snapshot"]["agents"]))
        self.host_request("retry", command_id=command["id"], notes="Synthetic retry after availability recovery")
        claim = self.host_request("claim", task_id=self.consumer)
        self.assertEqual(claim["command"]["task_id"], self.consumer)


    def test_malformed_bodies_are_rejected_without_mutating_project(self):
        before = self.rev()
        bodies = [b"[]", b"null", b"true", b"42", b"{", b"", b"{\"number\":NaN}", b"\xff"]
        for body in bodies:
            with self.subTest(body=body[:25]):
                request = Request(self.origin + "/api/action", method="POST", data=body,
                    headers={"Authorization": "Bearer " + self.server.human_token, "Origin": self.origin, "Content-Type": "application/json"})
                with self.assertRaises(HTTPError) as failure:
                    urlopen(request, timeout=5)
                self.assertEqual(failure.exception.code, 400)
                failure.exception.close()
                self.assertEqual(self.rev(), before)
        oversized = Request(self.origin + "/api/action", method="POST", data=b"{}",
            headers={"Authorization": "Bearer " + self.server.human_token, "Origin": self.origin,
                     "Content-Type": "application/json", "Content-Length": str(workspace_server.MAX_BODY + 1)})
        with self.assertRaises(HTTPError) as failure:
            urlopen(oversized, timeout=5)
        self.assertEqual(failure.exception.code, 400)
        failure.exception.close()
        status, data = self.request("/api/action", self.decision(), extra_headers={"Content-Type": "text/plain"})
        self.assertEqual(status, 400, data)
        self.assertEqual(self.rev(), before)
        self.snapshot()

    def test_control_and_credential_files_cannot_be_registered_or_diffed(self):
        relatives = ["state/host-session.json", ".aws/credentials", ".ssh/id_rsa", ".env.local", "src/.env", "src/.aws/credentials", ".netrc"]
        for index, relative in enumerate(relatives):
            with self.subTest(relative=relative):
                target = self.root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("SYNTHETIC_SECRET_MUST_NOT_LEAK", encoding="utf-8")
                before = self.rev()
                with self.assertRaises(ValueError):
                    workspace.configure(self.root, {"expected_revision": before, "gates": [{"id": "SENSITIVE-" + str(index), "task_id": self.producer, "paths": [relative]}]})
                self.assertEqual(self.rev(), before)
                status, data = self.request("/api/diff?path=" + relative)
                self.assertGreaterEqual(status, 400)
                self.assertNotIn("SYNTHETIC_SECRET_MUST_NOT_LEAK", json.dumps(data))

    def test_cli_actor_must_match_payload_actual_reviewer(self):
        payload = self.decision(action_id="synthetic-cli-principal-check")
        before = self.rev()
        with self.assertRaises(ValueError):
            workspace.decide(self.root, payload, actor="Different synthetic reviewer")
        self.assertEqual(self.rev(), before)
        result = workspace.decide(self.root, payload, actor=payload["actor"])
        self.assertFalse(result["unchanged"])

    def test_host_session_is_outside_project_and_removed_on_shutdown(self):
        output = io.StringIO()
        observed = {}
        def inspect_runtime():
            record = json.loads(output.getvalue())
            session_path = Path(record["host_session"])
            self.assertTrue(session_path.is_absolute())
            self.assertFalse(session_path.resolve().is_relative_to(self.root.resolve()))
            self.assertTrue(session_path.is_file())
            session = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertEqual(session["host_token"], "synthetic-host-secret")
            self.assertNotIn("synthetic-host-secret", output.getvalue())
            self.assertFalse((self.root / "state/workspace-host-session.json").exists())
            observed["path"] = session_path
            raise KeyboardInterrupt
        fake = SimpleNamespace(origin="http://127.0.0.1:12345", url="http://127.0.0.1:12345/?mode=local#token=synthetic-human", host_token="synthetic-host-secret", serve_forever=inspect_runtime, server_close=lambda:None)
        with patch.object(workspace_server, "make_server", return_value=fake), contextlib.redirect_stdout(output):
            workspace_server.serve(self.root)
        self.assertFalse(observed["path"].exists())


    def test_direct_module_cli_cannot_ignore_actor_argument(self):
        payload = self.decision(action_id="synthetic-direct-cli-principal")
        request_file = self.root / "reports/synthetic-human-decision.json"
        request_file.parent.mkdir(parents=True, exist_ok=True)
        request_file.write_text(json.dumps(payload), encoding="utf-8")
        before = self.rev()
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises((ValueError, SystemExit)):
            workspace.main(["decide", str(self.root), "--json", str(request_file), "--actor", "Different synthetic reviewer"])
        self.assertEqual(self.rev(), before)


if __name__ == "__main__":
    unittest.main()
