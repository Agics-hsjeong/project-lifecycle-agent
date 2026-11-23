"""Exact producer publication boundaries, with real Windows staging coverage."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import output_publication as publication, starter, windows_security as security


class OutputPublicationTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="output-publication-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temp.name), "synthetic", "Synthetic output", "product"))
        (self.root / "state").mkdir(exist_ok=True)
        self.relative = "reports/qa.md"
        self.path = self.root / self.relative
        self.path.parent.mkdir(exist_ok=True)
        self.raw = b"actual QA evidence\r\n\x00 exact bytes"
        self.path.write_bytes(self.raw)
        self.step = {"id": "STEP-001", "inputs": [], "expected_outputs": [self.relative], "results": []}
        self.run = {"id": "RUN-001", "steps": [self.step]}
        self.owner, self.writer = "S-1-5-21-1-2-3-1001", "S-1-5-21-1-2-3-1002"
        self.policy = {"owner": self.writer, "protected": False,
                       "aces": [{"type": 0, "flags": 16, "mask": 0x1f01ff, "sid": security.OWNER_RIGHTS}]}

    @contextlib.contextmanager
    def model(self):
        def read(path):
            return {**self.policy, "owner": self.owner} if Path(path) == self.root else self.policy
        with patch.object(publication, "os", SimpleNamespace(name="nt")), \
                patch.object(security, "read_security", side_effect=read), \
                patch.object(security, "effective_user_sid", return_value=self.writer), \
                patch.object(publication, "atomic_write") as write:
            yield write

    def test_exact_producer_output_published_with_same_bytes_and_hash(self):
        with self.model() as write:
            result = publication.publish_outputs(self.root, self.run, self.step, [self.relative])
        write.assert_called_once_with(self.path, self.raw, security_anchor=self.root, repair_owner=True)
        self.assertEqual(result["files"][0]["sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertTrue(result["files"][0]["published"])

    def test_other_role_reference_is_never_republished(self):
        other = "reports/other-role.md"
        (self.root / other).write_bytes(b"other role")
        with self.model() as write:
            result = publication.publish_outputs(self.root, self.run, self.step, [other])
        write.assert_not_called()
        self.assertEqual(result["files"], [])

    def test_automatic_directory_evidence_is_skipped_without_expanding_acl_scope(self):
        self.step["expected_outputs"] = ["reports"]
        with self.model() as write, patch.object(security, "read_security", side_effect=AssertionError("no directory policy access")):
            result = publication.publish_outputs(self.root, self.run, self.step, ["reports"])
            self.assertEqual(result["files"], [])
            write.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_explicit_recovery_never_publishes_directory(self):
        self.step["expected_outputs"] = ["reports"]
        self.step["results"] = [{"evidence_hashes": {"reports": "0" * 64}}]
        (self.root / "state/runs.json").write_text(json.dumps({"schema_version": 1, "runs": [self.run]}), encoding="utf-8")
        with self.model() as write:
            with self.assertRaises(ValueError):
                publication.publish_recorded_outputs(self.root, "RUN-001", "STEP-001", ["reports"])
            write.assert_not_called()

    def test_actual_run_result_preserves_directory_evidence_receipts(self):
        from tools import lifecycle, runs, workspace
        input_path = self.root / "input.txt"
        input_path.write_bytes(b"synthetic scope input")
        lifecycle.apply_patch(self.root, {"expected_revision": workspace.revision(self.root),
            "requirements": [{"description": "Synthetic directory evidence", "source": "input.txt"}]})
        plan = runs.create(self.root, {"expected_revision": workspace.revision(self.root), "key": "directory-output",
            "title": "Synthetic directory evidence", "requirement_ids": ["REQ-001"], "bindings": {},
            "steps": [{"id": "STEP-001", "title": "Directory decision evidence", "depends_on": [],
                       "inputs": ["input.txt"], "expected_outputs": ["reports"]}]})
        before = security.read_security(self.path) if os.name == "nt" else None
        with patch.object(publication, "atomic_write", side_effect=AssertionError("no recursive ACL publication")):
            result = runs.result(self.root, {"expected_revision": workspace.revision(self.root), "run_id": plan["run_id"],
                "step_id": "STEP-001", "key": "directory-decision", "status": "skipped", "evidence_refs": ["reports"],
                "notes": "Synthetic exclusion decision supported by exact directory contents"})
        self.assertEqual(result["status"], "skipped")
        step = runs.inspect(self.root, plan["run_id"])["runs"][0]["steps"][0]
        self.assertTrue(step["valid"])
        self.assertEqual(step["receipt_hashes"], {self.relative: hashlib.sha256(self.raw).hexdigest()})
        self.assertEqual(self.path.read_bytes(), self.raw)
        if before is not None:
            self.assertEqual(security.read_security(self.path), before)

    def test_normal_directory_input_suffix_does_not_block_unrelated_output(self):
        self.step["inputs"] = ["src/"]
        with self.model() as write:
            result = publication.publish_outputs(self.root, self.run, self.step, [self.relative])
        self.assertTrue(result["files"][0]["content_preserved"])
        self.assertEqual(write.call_count, 1)
        self.step["inputs"] = ["REPORTS/"]
        with self.model() as write:
            with self.assertRaises(ValueError):
                publication.publish_outputs(self.root, self.run, self.step, [self.relative])
            write.assert_not_called()

    def test_normal_directory_output_suffix_preserves_skip_and_explicit_rejection(self):
        self.step["expected_outputs"] = ["reports/"]
        with self.model() as write, patch.object(security, "read_security", side_effect=AssertionError("no directory ACL access")):
            result = publication.publish_outputs(self.root, self.run, self.step, ["reports/"])
            self.assertEqual(result["files"], [])
            write.assert_not_called()
        self.step["results"] = [{"evidence_hashes": {"reports/": "0" * 64}}]
        (self.root / "state/runs.json").write_text(json.dumps({"schema_version": 1, "runs": [self.run]}), encoding="utf-8")
        with self.model() as write:
            with self.assertRaises(ValueError):
                publication.publish_recorded_outputs(self.root, "RUN-001", "STEP-001", ["reports/"])
            write.assert_not_called()

    def test_regular_file_with_directory_suffix_is_rejected_after_type_check(self):
        alias = self.relative + "/"
        self.step["expected_outputs"] = [alias]
        with self.model() as write:
            with self.assertRaises(ValueError):
                publication.publish_outputs(self.root, self.run, self.step, [alias])
            write.assert_not_called()

    def test_original_inputs_and_input_directory_are_rejected(self):
        for input_ref in [self.relative, "reports"]:
            with self.subTest(input_ref=input_ref), self.model() as write:
                self.step["inputs"] = [input_ref]
                with self.assertRaises(ValueError):
                    publication.publish_outputs(self.root, self.run, self.step, [self.relative])
                write.assert_not_called()

    def test_private_control_and_escape_declarations_are_rejected(self):
        for ref in ["project.json", "state/runs.json", "inputs/private.md", "reports/.env", "../outside", "/outside", "reports/../qa.md"]:
            with self.subTest(ref=ref), self.model() as write:
                self.step["expected_outputs"] = [ref]
                with self.assertRaises(ValueError):
                    publication.publish_outputs(self.root, self.run, self.step, [ref])
                write.assert_not_called()

    def test_nonwriter_protected_and_explicit_policies_are_not_repaired(self):
        cases = [{"owner": "S-1-5-21-1-2-3-9999"}, {"protected": True},
                 {"aces": [{"type": 1, "flags": 0, "mask": 1, "sid": security.OWNER_RIGHTS}]}]
        original = self.policy
        for changes in cases:
            with self.subTest(changes=changes):
                self.policy = {**original, **changes}
                with self.model() as write:
                    with self.assertRaises(ValueError):
                        publication.publish_outputs(self.root, self.run, self.step, [self.relative])
                    write.assert_not_called()
        self.assertEqual(self.path.read_bytes(), self.raw)

    def test_normalized_owner_policy_is_byte_acl_preserving_noop(self):
        self.policy = {**self.policy, "owner": self.owner}
        with self.model() as write:
            result = publication.publish_outputs(self.root, self.run, self.step, [self.relative])
        write.assert_not_called()
        self.assertFalse(result["files"][0]["published"])

    def test_recorded_recovery_requires_latest_producer_hash(self):
        self.step["results"] = [{"evidence_hashes": {self.relative: hashlib.sha256(self.raw).hexdigest()}}]
        (self.root / "state/runs.json").write_text(json.dumps({"schema_version": 1, "runs": [self.run]}), encoding="utf-8")
        with self.model() as write:
            result = publication.publish_recorded_outputs(self.root, "RUN-001", "STEP-001", [self.relative])
        self.assertTrue(result["content_preserved"])
        self.assertEqual(write.call_count, 1)
        self.step["results"].append({"evidence_hashes": {self.relative: "0" * 64}})
        (self.root / "state/runs.json").write_text(json.dumps({"schema_version": 1, "runs": [self.run]}), encoding="utf-8")
        with self.model() as write:
            with self.assertRaises(ValueError):
                publication.publish_recorded_outputs(self.root, "RUN-001", "STEP-001", [self.relative])
            write.assert_not_called()

    def test_recorded_recovery_rejects_unreceipted_or_undeclared_path(self):
        (self.root / "state/runs.json").write_text(json.dumps({"schema_version": 1, "runs": [self.run]}), encoding="utf-8")
        with self.model() as write:
            for refs in [[self.relative], ["reports/other.md"], [["bad"]]]:
                with self.subTest(refs=refs), self.assertRaises(ValueError):
                    publication.publish_recorded_outputs(self.root, "RUN-001", "STEP-001", refs)
            write.assert_not_called()

    def test_changed_after_preflight_is_not_overwritten(self):
        from tools import storage
        original = storage.digest
        def changed(raw):
            self.path.write_bytes(b"concurrent change")
            return original(raw)
        with self.model() as write, patch.object(publication, "digest", side_effect=changed):
            with self.assertRaises(ValueError):
                publication.publish_outputs(self.root, self.run, self.step, [self.relative])
            write.assert_not_called()

    def test_windows_file_and_directory_input_aliases_rejected_before_io(self):
        from tools import workspace
        for input_ref, output_ref in [("reports/qa.md", "reports/QA.md"),
                                      ("REPORTS", "reports/qa.md")]:
            with self.subTest(input_ref=input_ref, output_ref=output_ref), self.model() as write, \
                    patch.object(workspace, "execution_path", side_effect=AssertionError("guard must precede I/O")):
                self.step["inputs"] = [input_ref]
                self.step["expected_outputs"] = [output_ref]
                with self.assertRaises(ValueError):
                    publication.publish_outputs(self.root, self.run, self.step, [output_ref])
                write.assert_not_called()

    def test_control_case_and_ambiguous_suffix_aliases_rejected_before_io(self):
        from tools import workspace
        refs = ["STATE/runs.json", "INPUTS/source.md", "Project.json", "reports/WORKSPACE-DECISIONS/x.md",
                "reports/qa.md.", "reports/qa.md ", "reports./qa.md", "reports /qa.md", "reports//qa.md",
                "./reports/qa.md", "reports/./qa.md", "reports/qa\x00.md", "reports/qa\n.md", "reports/CON.txt",
                "reports/.ENV", "REPORTS/.AWS/token.json", "reports/qa.md:stream"]
        for ref in refs:
            with self.subTest(ref=ref), self.model() as write, \
                    patch.object(workspace, "execution_path", side_effect=AssertionError("guard must precede I/O")):
                self.step["expected_outputs"] = [ref]
                with self.assertRaises(ValueError):
                    publication.publish_outputs(self.root, self.run, self.step, [ref])
                write.assert_not_called()

    def test_canonical_duplicate_output_declarations_are_ambiguous(self):
        self.step["expected_outputs"] = [self.relative, "reports/QA.md"]
        with self.model() as write:
            with self.assertRaises(ValueError):
                publication.publish_outputs(self.root, self.run, self.step, [self.relative])
            write.assert_not_called()

    def test_recovery_never_uses_old_hash_under_different_case_alias(self):
        self.step["results"] = [
            {"evidence_hashes": {self.relative: hashlib.sha256(self.raw).hexdigest()}},
            {"evidence_hashes": {"REPORTS/QA.md": "0" * 64}},
        ]
        (self.root / "state/runs.json").write_text(json.dumps({"schema_version": 1, "runs": [self.run]}), encoding="utf-8")
        with self.model() as write:
            with self.assertRaises(ValueError):
                publication.publish_recorded_outputs(self.root, "RUN-001", "STEP-001", [self.relative])
            write.assert_not_called()
        self.step["results"][-1]["evidence_hashes"]["REPORTS/QA.md"] = hashlib.sha256(self.raw).hexdigest()
        (self.root / "state/runs.json").write_text(json.dumps({"schema_version": 1, "runs": [self.run]}), encoding="utf-8")
        with self.model() as write:
            self.assertTrue(publication.publish_recorded_outputs(self.root, "RUN-001", "STEP-001", [self.relative])["content_preserved"])
            self.assertEqual(write.call_count, 1)

    def test_conflicting_alias_hashes_in_same_result_fail_closed(self):
        self.step["results"] = [{"evidence_hashes": {self.relative: hashlib.sha256(self.raw).hexdigest(),
                                                     "REPORTS/QA.md": "0" * 64}}]
        (self.root / "state/runs.json").write_text(json.dumps({"schema_version": 1, "runs": [self.run]}), encoding="utf-8")
        with self.model() as write:
            with self.assertRaises(ValueError):
                publication.publish_recorded_outputs(self.root, "RUN-001", "STEP-001", [self.relative])
            write.assert_not_called()

    def test_growth_before_or_after_replacement_uses_only_bounded_reads(self):
        for growth_at in [2, 3]:
            requests, reads = [], [0]
            original_raw = self.raw
            class BoundedStream(io.BytesIO):
                def read(self, size=-1):
                    requests.append(size)
                    if size != 65:
                        raise AssertionError("unbounded read")
                    return super().read(size)
            def open_stream(*args, **kwargs):
                reads[0] += 1
                return BoundedStream(b"x" * 65 if reads[0] == growth_at else original_raw)
            with self.subTest(growth_at=growth_at), self.model() as write, \
                    patch.object(publication, "MAX_FILE", 64), \
                    patch.object(Path, "open", side_effect=open_stream), \
                    patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded path read")):
                with self.assertRaises(ValueError):
                    publication.publish_outputs(self.root, self.run, self.step, [self.relative])
                self.assertEqual(write.call_count, 0 if growth_at == 2 else 1)
                self.assertEqual(requests, [65] * growth_at)

    @unittest.skipUnless(os.name == "nt", "requires real Windows file security")
    def test_real_windows_staging_preserves_bytes_and_pins_only_inherited_owner_rights(self):
        # Only this disposable parent receives a synthetic inherited policy.
        advapi, kernel = security._api()
        security._apply(self.path.parent, [{"type": 0, "flags": 3, "mask": 0x1f01ff,
                                         "sid": security.OWNER_RIGHTS}], True, advapi, kernel)
        self.path.unlink()
        self.path.write_bytes(self.raw)
        original = security.read_security
        anchor = original(self.root)["owner"]
        inode = self.path.stat().st_ino
        def legacy(path):
            policy = original(path)
            if Path(path) == self.path and self.path.stat().st_ino == inode:
                return {**policy, "owner": self.writer}
            return policy
        with patch.object(security, "read_security", side_effect=legacy), \
                patch.object(security, "effective_user_sid", return_value=self.writer):
            result = publication.publish_outputs(self.root, self.run, self.step, [self.relative])
        self.assertTrue(result["files"][0]["published"])
        self.assertEqual(self.path.read_bytes(), self.raw)
        policy = original(self.path)
        self.assertIn(anchor, [a["sid"] for a in policy["aces"]])
        self.assertFalse(any(a["sid"] == security.OWNER_RIGHTS and a["mask"] for a in policy["aces"]))
        self.assertEqual(policy["aces"][-1], {"type": 0, "flags": 0, "mask": 0, "sid": security.OWNER_RIGHTS})

    @unittest.skipUnless(os.name == "nt", "requires actual effective Windows token")
    def test_real_effective_writer_token_is_concrete_sid(self):
        self.assertRegex(security.effective_user_sid(), r"^S-1-5-\d+(?:-\d+)+$")
