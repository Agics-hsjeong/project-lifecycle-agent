"""Synthetic regression cases for the v2 foundation; no actual business approvals."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import struct
import zlib
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

import test_lifecycle as scenario
from revision_fixtures import apply_current
from tools import artifacts, dashboard, history, inputs, intake, lifecycle, package_plugin, proposal, runs, starter, workflow


class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.fixture = scenario.LifecycleTests()
        self.fixture.setUp()
        self.root = self.fixture.project
        self.fixture.prepare()

    def tearDown(self):
        self.fixture.tearDown()

    def rev(self):
        return lifecycle.events(self.root)[-1]["revision"]

    def test_missing_and_stale_revision_never_write(self):
        before = (self.root / "project.json").read_bytes()
        count = len(lifecycle.events(self.root))
        for payload in ({"project": {"objective": "bad"}}, {"expected_revision": None, "project": {"objective": "bad"}}):
            with self.assertRaises(ValueError):
                lifecycle.apply_patch(self.root, payload)
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        self.assertEqual(len(lifecycle.events(self.root)), count)

    def test_reader_serializes_a_writer_and_returns_matching_revision(self):
        original, attempted = starter.read_table, []
        revision = self.rev()
        fired = False
        def interleave(folder, name):
            nonlocal fired
            if not fired:
                fired = True
                def write():
                    try:
                        lifecycle.apply_patch(self.root, {"expected_revision": revision, "project": {"objective": "interleaved"}})
                    except ValueError:
                        attempted.append("blocked")
                worker = threading.Thread(target=write)
                worker.start()
                worker.join(3)
                self.assertFalse(worker.is_alive())
            return original(folder, name)
        with patch.object(starter, "read_table", side_effect=interleave):
            view = lifecycle.inspect_project(self.root)
        self.assertEqual(attempted, ["blocked"])
        self.assertEqual(view["revision"], revision)
        self.assertNotEqual(view["project"]["objective"], "interleaved")

    def test_custom_deliverable_is_visible_approved_and_fingerprinted(self):
        self.fixture.write("docs/custom.md", "Synthetic custom contract deliverable")
        apply_current(self.root, {"deliverables": [{"deliverable_id": "DEL-999", "stage": "discovery",
            "title": "Custom", "owner": "Synthetic", "version": "1", "path": "docs/custom.md", "requirement_ids": "REQ-001"}]})
        self.fixture.approve()
        row = next(r for r in lifecycle.inspect_project(self.root)["deliverables"] if r["deliverable_id"] == "DEL-999")
        self.assertEqual(row["status"], "approved")
        self.fixture.write("docs/custom.md", "Changed custom deliverable")
        self.assertTrue(lifecycle.stale_reviews(self.root, "discovery", strict=True))

    def test_all_supported_unicode_xml_declarations_rejected(self):
        for encoding in ("utf-8", "utf-16", "utf-32"):
            source = self.fixture.parent / (encoding + ".docx")
            xml = f'<?xml version="1.0" encoding="{encoding}"?><!DOCTYPE document [<!ENTITY fixture "MARKER">]><document><p><t>&fixture;</t></p></document>'
            with ZipFile(source, "w") as archive:
                archive.writestr("word/document.xml", xml.encode(encoding))
            record, text = intake.make_record(source, "INPUT-001", "inputs/original.docx", "synthetic")
            self.assertEqual(record["extraction"]["status"], "needs_reader")
            self.assertIsNone(text)

    def test_header_only_pdf_never_passes_export_check(self):
        path = self.fixture.write("invalid.pdf", "%PDF-1.7\nnot a document\n")
        with self.assertRaises(ValueError):
            proposal.check_export(path, "qualitative")

    def test_mixed_pdf_records_each_unread_page_and_partial_status(self):
        source = self.fixture.write("mixed.pdf", "Synthetic mocked PDF reader input")
        reader = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: "Known text"), SimpleNamespace(extract_text=lambda: "")])
        with patch.object(intake.importlib.util, "find_spec", return_value=True), patch.object(intake, "pdf_reader", return_value=reader):
            record, text = intake.make_record(source, "INPUT-001", "inputs/mixed.pdf", "synthetic")
        self.assertEqual(record["extraction"]["status"], "partial")
        self.assertEqual(record["extraction"]["page_count"], 2)
        self.assertEqual(record["extraction"]["pages"][1], {"page": 2, "status": "needs_ocr"})
        self.assertIn("[page 2]", text)

    def test_reader_receipt_resolves_unread_source_and_detects_changed_proof(self):
        source = self.fixture.parent / "unsupported.hwp"
        source.write_bytes(b"Synthetic unparsed input")
        lifecycle.ingest(self.root, source)
        self.assertTrue(inputs.gate_issues(self.root))
        self.fixture.write("reviews/read.md", "Synthetic host reader result with actual source notes")
        inputs.review(self.root, {"expected_revision": self.rev(), "input_id": "INPUT-001",
            "evidence_ref": "reviews/read.md", "page_count": None, "pages": []})
        self.assertEqual(inputs.gate_issues(self.root), [])
        self.fixture.write("reviews/read.md", "Changed reader result")
        self.assertTrue(inputs.gate_issues(self.root))

    def test_page_review_requires_complete_inventory(self):
        with self.assertRaises(ValueError):
            inputs.review(self.root, {"expected_revision": self.rev(), "input_id": "INPUT-001",
                "evidence_ref": "review.md", "page_count": 2, "pages": [1]})

    def test_operating_note_preserves_business_review_but_baseline_does_not(self):
        self.fixture.approve()
        note = self.fixture.parent / "note.txt"
        note.write_text("Synthetic operating note", encoding="utf-8")
        lifecycle.ingest(self.root, note, purpose="operating_note")
        self.assertEqual(lifecycle.stale_reviews(self.root, "discovery", strict=True), [])
        note.write_text("Synthetic changed baseline", encoding="utf-8")
        lifecycle.ingest(self.root, note)
        self.assertTrue(lifecycle.stale_reviews(self.root, "discovery", strict=True))

    def test_source_dependency_and_technical_environment_change_fingerprint(self):
        self.fixture.write("src/dependency.py", "value = 1\n")
        before = lifecycle.fingerprint(self.root, "verification", "docs/design.md")[0]
        self.fixture.write("src/dependency.py", "value = 2\n")
        self.assertNotEqual(before, lifecycle.fingerprint(self.root, "verification", "docs/design.md")[0])
        before = lifecycle.fingerprint(self.root, "release", "docs/design.md")[0]
        apply_current(self.root, {"project": {"technical": {"deployment_target": "synthetic changed environment"}}})
        self.assertNotEqual(before, lifecycle.fingerprint(self.root, "release", "docs/design.md")[0])

    def test_source_roots_cannot_hash_control_state(self):
        for roots in (["state"], ["."], ["../outside"], []):
            with self.assertRaises(ValueError):
                apply_current(self.root, {"project": {"technical": {"source_roots": roots}}})

    def test_child_of_plugin_source_rejected_before_project_creation(self):
        target = starter.ROOT / "docs" / "synthetic-customer"
        with contextlib.redirect_stderr(io.StringIO()):
            result = starter.main(["init", "--workspace", str(target), "--name", "Synthetic", "--slug", "synthetic"])
        self.assertEqual(result, 2)
        self.assertFalse(target.exists())

    def test_unlisted_environment_and_customer_files_excluded_from_zip(self):
        mirror = self.fixture.parent / "mirror"
        inventory = json.loads((starter.ROOT / "tools/package-files.json").read_text(encoding="utf-8"))
        for name in inventory:
            target = mirror / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(starter.ROOT / name, target)
        for name in ("docs/.env", "docs/private-note.txt", "docs/workspace/projects/client/rfp.txt"):
            target = mirror / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("Synthetic never-public fixture", encoding="utf-8")
        with patch.object(package_plugin, "ROOT", mirror), contextlib.redirect_stdout(io.StringIO()):
            output = package_plugin.package(self.fixture.parent / "safe.zip")
        with ZipFile(output) as archive:
            names = archive.namelist()
        self.assertFalse(any(".env" in name or "private-note" in name or "/projects/" in name for name in names))

    def test_undo_restores_previous_content_and_preserves_audit(self):
        before = starter.load_project(self.root)[1]["objective"]
        changed = apply_current(self.root, {"project": {"objective": "New synthetic objective"}})
        plan = history.preview(self.root, changed["revision"])
        restored = history.undo(self.root, plan)
        self.assertEqual(starter.load_project(self.root)[1]["objective"], before)
        events = lifecycle.events(self.root)
        self.assertIn(changed["revision"], [e["revision"] for e in events])
        self.assertEqual(events[-1]["action"], "undo")
        self.assertNotEqual(restored["revision"], changed["revision"])

    def test_undo_refuses_later_edits_and_non_edit_actions(self):
        changed = apply_current(self.root, {"project": {"objective": "First edit"}})
        plan = history.preview(self.root, changed["revision"])
        apply_current(self.root, {"project": {"objective": "User newer edit"}})
        with self.assertRaises(ValueError):
            history.undo(self.root, plan)
        self.assertEqual(starter.load_project(self.root)[1]["objective"], "User newer edit")
        approval = self.fixture.approve()
        with self.assertRaises(ValueError):
            history.preview(self.root, approval["revision"])

    def test_profiles_change_execution_and_keep_legacy_order(self):
        data = starter.load_project(self.root)[1]
        self.assertEqual(len(workflow.stages(data)), 11)
        for profile, count in (("product", 6), ("api", 9), ("si", 11)):
            apply_current(self.root, {"project": {"workflow": {"profile": profile}}})
            self.assertEqual(len(lifecycle.inspect_project(self.root)["workflow"]["stages"]), count)

    def test_product_release_finishes_with_future_backlog_unfinished(self):
        apply_current(self.root, {"requirements": [
            {"requirement_id": "REQ-001", "status": "accepted", "design_refs": "docs/design.md", "implementation_refs": "src/example.py", "test_refs": "tests/result.md", "deliverable_ids": "DEL-011"},
            {"source": "Synthetic later idea", "description": "Future backlog", "priority": "later", "acceptance_criteria": "Future acceptance"}],
            "project": {"workflow": {"profile": "product", "release": {"id": "REL-001", "requirement_ids": ["REQ-001"]}}}})
        for stage in workflow.PROFILES["product"]:
            self.fixture.approve(stage)
            if stage != "maintenance":
                lifecycle.transition(self.root)
        self.assertEqual(starter.load_project(self.root)[1]["current_stage"], "maintenance")
        self.assertEqual(starter.read_table(self.root, "requirements")[1]["status"], "proposed")

    def test_declared_blocking_tasks_and_risks_require_real_closure(self):
        apply_current(self.root, {"tasks": [{"status": "blocked", "next_action": "Synthetic prerequisite"}],
            "risks": [{"description": "Synthetic risk", "status": "open"}],
            "project": {"workflow": {"profile": "si", "blockers": {"discovery": {"tasks": ["TASK-001"], "risks": ["RISK-001"]}}}}})
        with self.assertRaises(lifecycle.GateError):
            self.fixture.approve()
        self.fixture.write("reviews/closed.md", "Synthetic closure evidence")
        apply_current(self.root, {"tasks": [{"task_id": "TASK-001", "status": "done", "evidence_refs": "reviews/closed.md"}],
            "risks": [{"risk_id": "RISK-001", "status": "closed", "response": "reviews/closed.md"}]})
        self.fixture.approve()

    def plan(self, steps=1):
        payload = {"expected_revision": self.rev(), "key": "synthetic-run", "title": "Synthetic host work", "requirement_ids": ["REQ-001"], "steps": [
            {"id": f"STEP-{i:03d}", "title": f"Step {i}", "depends_on": [f"STEP-{i-1:03d}"] if i > 1 else [], "inputs": ["docs/design.md"]} for i in range(1, steps+1)]}
        return runs.create(self.root, payload), payload

    def record(self, step, status, key, evidence=()):
        payload = {"expected_revision": self.rev(), "run_id": "RUN-001", "step_id": step,
            "key": key, "status": status, "evidence_refs": list(evidence), "notes": "Synthetic host receipt"}
        return runs.result(self.root, payload), payload

    def test_runs_are_idempotent_and_need_actual_execution_and_receipts(self):
        created, plan = self.plan()
        self.assertTrue(runs.create(self.root, plan)["unchanged"])
        with self.assertRaises(ValueError):
            self.record("STEP-001", "succeeded", "no-start")
        self.record("STEP-001", "running", "start")
        with self.assertRaises(ValueError):
            self.record("STEP-001", "succeeded", "no-proof")
        self.fixture.write("results/output.md", "Synthetic actual output fixture")
        _, receipt = self.record("STEP-001", "succeeded", "done", ["results/output.md"])
        revision = self.rev()
        self.assertTrue(runs.result(self.root, receipt)["unchanged"])
        self.assertEqual(self.rev(), revision)
        self.assertTrue(runs.inspect(self.root)["runs"][0]["complete"])

    def test_run_resume_detects_changed_receipt_and_transitive_dependencies(self):
        self.plan(3)
        for i in (1, 2):
            self.record(f"STEP-{i:03d}", "running", f"start-{i}")
            self.fixture.write(f"results/{i}.md", f"Synthetic step {i}")
            self.record(f"STEP-{i:03d}", "succeeded", f"done-{i}", [f"results/{i}.md"])
        self.fixture.write("results/1.md", "Changed upstream output")
        view = runs.inspect(self.root)["runs"][0]
        self.assertEqual(view["steps"][1]["effective_status"], "stale")
        self.assertFalse(view["steps"][2]["ready_to_start"])
        with self.assertRaises(ValueError):
            self.record("STEP-003", "running", "premature")

    def test_running_work_is_not_automatically_restarted(self):
        self.plan()
        self.record("STEP-001", "running", "start")
        view = runs.inspect(self.root)["runs"][0]["steps"][0]
        self.assertFalse(view["ready_to_start"])
        self.assertEqual(view["attempts"], 1)
        self.fixture.write("docs/design.md", "Input changed during work")
        self.fixture.write("results/output.md", "Old input output")
        with self.assertRaises(ValueError):
            self.record("STEP-001", "succeeded", "bad-done", ["results/output.md"])
        self.record("STEP-001", "failed", "failed")
        self.record("STEP-001", "running", "retry")
        self.assertEqual(runs.inspect(self.root)["runs"][0]["steps"][0]["attempts"], 2)

    def test_run_rejects_invalid_dag_and_skip_without_decision(self):
        _, plan = self.plan()
        plan["key"] = "other"
        plan["expected_revision"] = self.rev()
        plan["steps"][0]["depends_on"] = ["STEP-001"]
        with self.assertRaises(ValueError):
            runs.create(self.root, plan)
        with self.assertRaises(ValueError):
            self.record("STEP-001", "skipped", "skip")

    def test_pipeline_can_plan_future_dependency_outputs(self):
        payload = {"expected_revision": self.rev(), "key": "pipeline", "title": "Synthetic artifact pipeline", "requirement_ids": ["REQ-001"], "steps": [
            {"id": "STEP-001", "title": "Draft", "depends_on": [], "inputs": ["docs/design.md"], "expected_outputs": ["results/draft.md"]},
            {"id": "STEP-002", "title": "Review", "depends_on": ["STEP-001"], "inputs": ["results/draft.md"], "expected_outputs": ["results/review.md"]}]}
        runs.create(self.root, payload)
        self.record("STEP-001", "running", "draft-start")
        self.fixture.write("results/draft.md", "Synthetic actual draft")
        with self.assertRaises(ValueError):
            self.record("STEP-001", "succeeded", "missing-output", ["docs/01-discovery.md"])
        self.record("STEP-001", "succeeded", "draft-done", ["results/draft.md"])
        self.record("STEP-002", "running", "review-start")
        self.fixture.write("results/review.md", "Synthetic actual review fixture")
        self.record("STEP-002", "succeeded", "review-done", ["results/review.md"])
        self.assertTrue(runs.inspect(self.root)["runs"][0]["complete"])

    def test_customer_cwd_cli_create_resume_and_undo(self):
        # This workspace is a sibling of the plugin, never inside its source.
        base = starter.ROOT.parent.resolve()
        with tempfile.TemporaryDirectory(dir=base, prefix="ieum-cli-") as temporary:
            space = Path(temporary).resolve()
            self.assertTrue(space.is_relative_to(base))
            def cli(*args):
                result = subprocess.run([sys.executable, "-X", "utf8", str(starter.ROOT / "tools/starter.py"), *map(str, args)],
                    cwd=space, capture_output=True, text=True, encoding="utf-8")
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            created = cli("init", "--workspace", space, "--name", "Synthetic CLI", "--slug", "cli-project", "--type", "product", "--profile", "product", "--json")
            project = Path(created["path"])
            self.assertEqual(len(cli("inspect", project)["workflow"]["stages"]), 6)
            request = space / "request.json"
            request.write_text(json.dumps({"expected_revision": None, "project": {"objective": "Synthetic original"}}), encoding="utf-8")
            first = cli("apply", project, "--file", request)
            request.write_text(json.dumps({"expected_revision": first["revision"], "project": {"objective": "Synthetic changed"}}), encoding="utf-8")
            changed = cli("apply", project, "--file", request)
            plan = cli("undo-preview", project, "--revision", changed["revision"])
            request.write_text(json.dumps(plan), encoding="utf-8")
            cli("undo", project, "--file", request)
            self.assertEqual(cli("inspect", project)["project"]["objective"], "Synthetic original")
            self.assertEqual(cli("run-resume", project)["runs"], [])
            self.assertEqual(cli("history", project)["events"][-1]["action"], "undo")

    def png(self, name):
        def chunk(kind, data):
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind+data) & 0xffffffff)
        raw = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        raw += chunk(b"IDAT", zlib.compress(b"\x00\xff\xff\xff")) + chunk(b"IEND", b"")
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_png_requires_real_chunks_and_pixel_data(self):
        good = self.png("qa/good.png")
        self.assertEqual(artifacts.png(good), {"width": 1, "height": 1})
        good.write_bytes(good.read_bytes()[:-5])
        with self.assertRaises(ValueError):
            artifacts.png(good)
        good.write_bytes(b"\x89PNG\r\n\x1a\n")
        with self.assertRaises(ValueError):
            artifacts.png(good)

    def test_output_review_covers_every_page_and_invalidates_on_change(self):
        # PDF reader is stubbed only in this inventory/receipt test.
        self.fixture.write("exports/render.pdf", "Synthetic mocked PDF container")
        self.fixture.write("reviews/visual.md", "Synthetic content/style comparison fixture")
        self.png("qa/page-1.png")
        self.png("qa/page-2.png")
        payload = {"expected_revision": self.rev(), "role": "qualitative", "source_file": "exports/qualitative.docx",
            "pdf_file": "exports/render.pdf", "pages": [{"page": 1, "image_ref": "qa/page-1.png"}], "review_ref": "reviews/visual.md"}
        with patch.object(artifacts, "pdf_reader", return_value=SimpleNamespace(pages=[None, None])):
            with self.assertRaises(ValueError):
                artifacts.review(self.root, payload)
            payload["pages"].append({"page": 2, "image_ref": "qa/page-2.png"})
            artifacts.review(self.root, payload)
        profile = proposal.read(self.root)
        issues = artifacts.check(self.root, starter.load_project(self.root)[1], profile)
        self.assertFalse(any("qualitative" in item for item in issues))
        self.fixture.write("reviews/visual.md", "Changed visual comparison")
        self.assertTrue(any("qualitative" in item for item in artifacts.check(self.root, starter.load_project(self.root)[1], profile)))

    def test_explicit_si_profile_requires_output_reviews(self):
        project = starter.load_project(self.root)[1]
        self.assertEqual(artifacts.check(self.root, project, proposal.read(self.root)), [])
        project["workflow"] = {"profile": "si"}
        self.assertEqual(len(artifacts.check(self.root, project, proposal.read(self.root))), 2)

    def test_dashboard_escapes_data_and_preserves_user_edits(self):
        apply_current(self.root, {"project": {"name": "<script>Synthetic</script>"}})
        result = dashboard.write(self.root)
        page = Path(result["path"])
        html = page.read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertFalse(result["published"])
        dashboard.write(self.root)
        page.write_text(html+"User edit", encoding="utf-8")
        with self.assertRaises(ValueError):
            dashboard.write(self.root)
        self.assertTrue(page.read_text(encoding="utf-8").endswith("User edit"))
        with self.assertRaises(ValueError):
            dashboard.write(self.root, "../outside.html")


if __name__ == "__main__":
    unittest.main()
