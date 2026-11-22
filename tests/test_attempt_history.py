"""Attempt projection privacy, provenance and strict-ledger regression checks."""
import contextlib
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import attempt_history as history, lifecycle, runs, starter, workspace

SHA = "a" * 64
AT = "2026-11-23T10:00:00+00:00"


def record(status, notes="Actual recorded note", evidence=None):
    return {"status": status, "notes": notes, "evidence_hashes": evidence or {},
            "key": "PRIVATE_RESULT_KEY", "actor": "PRIVATE_ACTOR", "signature": "PRIVATE_SIGNATURE"}


def step(records, identifier="STEP-001", **extra):
    return {"id": identifier, "results": records, "attempts": 2, **extra}


def run(steps, identifier="RUN-001"):
    return {"id": identifier, "steps": steps}


def event(status, run_id="RUN-001", step_id="STEP-001", at=AT):
    return {"action": "run-result", "at": at, "details": {"run_id": run_id, "step_id": step_id, "status": status}}


def task(identifier="TASK-001", run_id="RUN-001", step_id="STEP-001", role="qa", **extra):
    return {"id": identifier, "run_id": run_id, "step_id": step_id, "role": role, **extra}


class AttemptProjectionTests(unittest.TestCase):
    def project(self, records, *, events=None, tasks=None, **extra):
        return history.project_steps([run([step(records, **extra)])], tasks or [], events or [])[("RUN-001", "STEP-001")]

    def test_failure_developer_fix_and_retry_have_true_cross_step_ledger_order(self):
        qa = [record("running", "QA started"), record("failed", "Boundary case fails", {"tests/counterexample.py": SHA}),
              record("running", "QA rerun"), record("succeeded", "Counterexample now passes")]
        dev = [record("running", "Fix started"), record("succeeded", "Range check corrected", {"tests/counterexample.py": SHA})]
        events = [{"action": "run-create", "at": AT}, event("running"), event("failed"),
                  event("running", step_id="STEP-002"), event("succeeded", step_id="STEP-002"),
                  event("running"), event("succeeded")]
        projected = history.project_steps([run([step(qa), step(dev, "STEP-002")])],
            [task(), task("TASK-002", step_id="STEP-002", role="backend")], events)
        rows = [row for value in projected.values() for row in value["result_history"]]
        ordered = sorted(rows, key=lambda row: row["sequence"])
        self.assertEqual([r["sequence"] for r in ordered], list(range(2, 8)))
        self.assertEqual([r["summary"] for r in ordered], ["QA started", "Boundary case fails", "Fix started", "Range check corrected", "QA rerun", "Counterexample now passes"])
        self.assertEqual([r["attempt"] for r in projected[("RUN-001", "STEP-001")]["result_history"]], [1, 1, 2, 2])
        self.assertEqual(ordered[1]["evidence_hashes"], {"tests/counterexample.py": SHA})
        self.assertEqual(ordered[3]["evidence_hashes"], ordered[1]["evidence_hashes"])
        self.assertEqual(ordered[3]["role"], "backend")
        self.assertNotIn("PRIVATE", json.dumps(rows))

    def test_missing_starts_and_counter_do_not_invent_attempt_numbers(self):
        projected = self.project([record("failed"), record("skipped")], attempts=7)
        self.assertEqual(projected["attempts"], 7)
        self.assertEqual([r["attempt"] for r in projected["result_history"]], [None, None])
        self.assertTrue(all(r["at"] is None and r["sequence"] is None for r in projected["result_history"]))

    def test_legacy_missing_fields_remain_unreported(self):
        projected = history.project_steps([run([{"id": "STEP-001", "results": [{"status": "failed"}]}])], [], [])[("RUN-001", "STEP-001")]
        self.assertIsNone(projected["attempts"])
        row = projected["result_history"][0]
        self.assertEqual(row["summary"], "")
        self.assertIsNone(row["task_id"])
        self.assertEqual(row["role"], "")
        self.assertEqual(row["evidence_hashes"], {})

    def test_time_match_requires_complete_exact_per_step_status_sequence(self):
        records = [record("running"), record("failed"), record("running"), record("succeeded")]
        variants = [[event("running"), event("failed")],
                    [event("running"), event("running"), event("failed"), event("succeeded")],
                    [event(r["status"]) for r in records] + [event("failed")]]
        for events in variants:
            with self.subTest(statuses=[e["details"]["status"] for e in events]):
                rows = self.project(records, events=events)["result_history"]
                self.assertTrue(all(r["at"] is None and r["sequence"] is None for r in rows))

    def test_missing_or_invalid_timestamp_does_not_fabricate_time_or_lose_event_order(self):
        statuses = ["running", "failed", "running", "succeeded"]
        events = [event(status, at=at) for status, at in zip(statuses, [AT, None, "2026-11-23T10:00:00", "invalid"])]
        rows = self.project([record(s) for s in statuses], events=events)["result_history"]
        self.assertEqual([r["sequence"] for r in rows], [1, 2, 3, 4])
        self.assertEqual([r["at"] for r in rows], [AT, None, None, None])

    def test_one_steps_mismatch_does_not_assign_other_steps_timestamps(self):
        projected = history.project_steps([run([step([record("failed")]), step([record("running"), record("failed")], "STEP-002")])], [],
            [event("failed"), event("failed", step_id="STEP-002")])
        self.assertEqual(projected[("RUN-001", "STEP-001")]["result_history"][0]["sequence"], 1)
        self.assertTrue(all(r["sequence"] is None for r in projected[("RUN-001", "STEP-002")]["result_history"]))

    def test_bound_preserves_full_source_attempt_number_and_event_position(self):
        records = [record(status) for _ in range(40) for status in ("running", "failed")]
        projected = self.project(records, attempts=40, events=[event(r["status"]) for r in records])
        rows = projected["result_history"]
        self.assertEqual(len(rows), 64)
        self.assertEqual(projected["history_omitted"], 16)
        self.assertEqual(rows[0]["id"], "RUN-001:STEP-001:17")
        self.assertEqual(rows[0]["attempt"], 9)
        self.assertEqual(rows[0]["sequence"], 17)
        self.assertEqual(rows[-1]["attempt"], 40)
        self.assertEqual(len({r["id"] for r in rows}), 64)

    def test_notes_are_redacted_including_json_keys_before_truncation(self):
        provider = "sk-proj-" + "CanarySecretValue" * 4
        notes = ["Range check failed " + "x" * 550 + " api_key=" + "CANARY_CREDENTIAL_" * 60,
                 json.dumps({provider: "plain", "nested": {"password": "CANARY_PASSWORD"}}),
                 'escaped {\"api_key\":\"CANARY_ESCAPED\"}',
                 "-----BEGIN PRIVATE KEY-----\nCANARY_PRIVATE\n-----END PRIVATE KEY-----"]
        for note in notes:
            with self.subTest(note_type=note[:16]):
                summary = self.project([record("failed", note)])["result_history"][0]["summary"]
                self.assertLessEqual(len(summary), 600)
                for secret in (provider, "CANARY_CREDENTIAL_", "CANARY_PASSWORD", "CANARY_ESCAPED", "CANARY_PRIVATE"):
                    self.assertNotIn(secret, summary)

    def test_evidence_is_only_safe_path_and_digest_metadata_including_code(self):
        unsafe = ["../escape.md", "/absolute.md", "C:/absolute.md", "state/events.jsonl", "STATE/a.json",
                  "reports/../escape.md", "reports//alias.md", "reports/a./alias.md", ".git/config", ".codex/auth.json",
                  "secrets/token.txt", ".env", "reports/credentials.json", "reports/api_key=CANARY_PATH.md", "reports\\escape.py"]
        refs = {path: SHA for path in unsafe}
        refs.update({"src/quote.py": "B" * 64, "src/view.ts": SHA, "db/schema.sql": SHA,
                     "reports/page.html": SHA, "reports/red.log": SHA, "reports/bad.md": "not-a-hash"})
        projected = history.evidence_hashes(refs)
        self.assertEqual(projected, {"src/quote.py": "b" * 64, "src/view.ts": SHA, "db/schema.sql": SHA,
                                     "reports/page.html": SHA, "reports/red.log": SHA})
        self.assertNotIn("CANARY", json.dumps(projected))

    def test_provider_secret_in_evidence_dictionary_key_is_not_published(self):
        secret = "ghp_" + "CanaryProviderToken" * 3
        projected = history.evidence_hashes({"reports/" + secret + ".log": SHA, "reports/public.log": SHA})
        self.assertEqual(projected, {"reports/public.log": SHA})

    def test_evidence_bound_and_projection_never_read_files_or_mutate_sources(self):
        records = [record("running"), record("failed", evidence={f"reports/{i}.log": SHA for i in range(80)})]
        sources = [run([step(records)])], [task(native_agent_id="PRIVATE_CURRENT_NATIVE")], [event("running"), event("failed")]
        before = deepcopy(sources)
        with patch.object(Path, "open", side_effect=AssertionError("artifact I/O forbidden")), patch.object(Path, "stat", side_effect=AssertionError("artifact stat forbidden")), patch.object(Path, "resolve", side_effect=AssertionError("path resolution forbidden")):
            output = history.project_steps(*sources)
        self.assertEqual(sources, before)
        self.assertEqual(len(output[("RUN-001", "STEP-001")]["result_history"][-1]["evidence_hashes"]), 64)
        self.assertNotIn("PRIVATE_CURRENT_NATIVE", json.dumps(output[("RUN-001", "STEP-001")]))

    def test_mapping_is_exact_and_ambiguous_or_superseded_role_is_not_reused(self):
        old = run([step([record("failed")])])
        current = run([step([record("running")])], "RUN-002")
        mapped = history.project_steps([old, current], [task(run_id="RUN-002", role="backend", native_agent_id="CURRENT_WORKER")], [])
        self.assertIsNone(mapped[("RUN-001", "STEP-001")]["task_id"])
        self.assertEqual(mapped[("RUN-001", "STEP-001")]["role"], "")
        self.assertEqual(mapped[("RUN-002", "STEP-001")]["task_id"], "TASK-001")
        self.assertEqual(mapped[("RUN-002", "STEP-001")]["role"], "backend")
        ambiguous = history.project_steps([old], [task(), task("TASK-002")], [])[("RUN-001", "STEP-001")]
        self.assertIsNone(ambiguous["task_id"])
        self.assertEqual(ambiguous["role"], "")


    def test_conflicting_or_partial_nested_binding_is_excluded_without_flat_fallback(self):
        original = [run([step([record("failed")])])]
        conflicts = [{"run_id": "RUN-002", "step_id": "STEP-001"},
                     {"run_id": "RUN-001", "step_id": "STEP-002"},
                     {"run_id": "RUN-001"}, "malformed"]
        for declared in conflicts:
            with self.subTest(declared=declared):
                value = history.project_steps(original, [task(step_ref=declared)], [])[("RUN-001", "STEP-001")]
                self.assertIsNone(value["task_id"])
                self.assertEqual(value["role"], "")
                self.assertIsNone(value["result_history"][0]["task_id"])
        for declared in (None, {"run_id": "RUN-001", "step_id": "STEP-001"}):
            with self.subTest(valid=declared):
                value = history.project_steps(original, [task(step_ref=declared)], [])[("RUN-001", "STEP-001")]
                self.assertEqual(value["task_id"], "TASK-001")
                self.assertEqual(value["role"], "qa")



class AttemptSnapshotIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ieum-attempt-history-")
        self.addCleanup(self.temporary.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temporary.name), "attempts", "Synthetic attempts", "product"))
        (self.root / "input.txt").write_text("Stable source", encoding="utf-8")
        lifecycle.apply_patch(self.root, {"expected_revision": workspace.revision(self.root),
            "requirements": [{"description": "Reject invalid percentage", "source": "input.txt"}],
            "tasks": [{"role": "qa", "next_action": "Check percentage"}]})
        self.run_id = runs.create(self.root, {"expected_revision": workspace.revision(self.root), "key": "attempt-test", "title": "QA retry",
            "requirement_ids": ["REQ-001"], "bindings": {"STEP-001": "TASK-001"},
            "steps": [{"id": "STEP-001", "title": "Validate quote", "inputs": ["input.txt"], "depends_on": [], "expected_outputs": ["report.md"]}]})["run_id"]

    def result(self, status, key, notes, evidence=()):
        return runs.result(self.root, {"expected_revision": workspace.revision(self.root), "run_id": self.run_id,
            "step_id": "STEP-001", "key": key, "status": status, "evidence_refs": list(evidence), "notes": notes})

    def managed_bytes(self):
        paths = [self.root / "project.json", *(self.root / "registers").glob("*.csv"),
                 *(p for p in (self.root / "state").rglob("*") if p.is_file() and p.name != "write.lock")]
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in paths}

    def test_failed_attempt_then_success_is_visible_without_rewriting_ledgers_or_verification(self):
        self.result("running", "first-start", "QA started")
        (self.root / "report.md").write_text("Counterexample reproduced", encoding="utf-8")
        self.result("failed", "first-failed", "Boundary condition fails", ["report.md"])
        self.result("running", "second-start", "Recheck corrected boundary")
        (self.root / "report.md").write_text("Counterexample passes", encoding="utf-8")
        self.result("succeeded", "second-success", "Corrected boundary verified", ["report.md"])
        before = self.managed_bytes()
        snapshot = workspace.snapshot(self.root)
        projected = snapshot["history_runs"][0]["steps"][0]
        self.assertEqual([r["status"] for r in projected["result_history"]], ["running", "failed", "running", "succeeded"])
        self.assertEqual([r["attempt"] for r in projected["result_history"]], [1, 1, 2, 2])
        self.assertEqual(projected["attempts"], 2)
        self.assertEqual(projected["task_id"], "TASK-001")
        self.assertEqual(projected["role"], "qa")
        self.assertTrue(projected["valid"])
        self.assertTrue(all(r["at"] and r["sequence"] for r in projected["result_history"]))
        self.assertNotEqual(projected["result_history"][1]["evidence_hashes"], projected["result_history"][-1]["evidence_hashes"])
        self.assertEqual(self.managed_bytes(), before)
        (self.root / "report.md").write_text("Changed after verification", encoding="utf-8")
        changed = workspace.snapshot(self.root)["history_runs"][0]["steps"][0]
        self.assertFalse(changed["valid"], "projection must never bypass receipt validation")
        self.assertEqual(changed["result_history"][-1]["status"], "succeeded", "recorded outcome is distinct from current validity")

    def test_every_historical_run_is_projected_and_current_binding_never_relabels_superseded_step(self):
        self.result("failed", "legacy-failed", "Older attempt failed")
        runs.create(self.root, {"expected_revision": workspace.revision(self.root), "key": "current", "title": "Current validation",
            "requirement_ids": ["REQ-001"], "bindings": {"STEP-001": "TASK-001"},
            "steps": [{"id": "STEP-001", "title": "New validation", "inputs": ["input.txt"], "depends_on": [], "expected_outputs": ["current.md"]}]})
        snapshot = workspace.snapshot(self.root)
        old, current = snapshot["history_runs"]
        self.assertEqual(old["steps"][0]["result_history"][0]["summary"], "Older attempt failed")
        self.assertIsNone(old["steps"][0]["task_id"])
        self.assertEqual(old["steps"][0]["role"], "")
        self.assertEqual(current["steps"][0]["result_history"], [])
        self.assertEqual(current["steps"][0]["task_id"], "TASK-001")


if __name__ == "__main__":
    unittest.main()
