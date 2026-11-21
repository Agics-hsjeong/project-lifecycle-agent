"""Disposable-fixture retention tests; never purge a real user's runtime."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

from tools import runtime_retention as retention, starter
from tools.storage import digest, json_bytes


class RetentionTests(unittest.TestCase):
    def setUp(self):
        base = starter.ROOT / ".test-output"
        base.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.parent = Path(self.temp.name)
        self.root = starter.init_project(self.parent, "retention-fixture", "Synthetic retention test", "product")
        self.runtime = self.parent / ".ieum-runtime" / self.root.name
        self.attempt = "attempt-" + uuid.uuid4().hex
        self.command = "CMD-" + uuid.uuid4().hex
        self.job = self.runtime / "jobs" / self.attempt / self.command
        (self.job / "workspace/src").mkdir(parents=True)
        (self.job / "workspace/src/main.py").write_text("Synthetic copy", encoding="utf-8")
        (self.job / "stdout.jsonl").write_text("Synthetic raw log", encoding="utf-8")
        (self.job / "stderr.log").write_text("Synthetic stderr", encoding="utf-8")
        (self.job / "verification-0.stdout").write_text("Synthetic check output", encoding="utf-8")
        self.receipt = {"command_id": self.command, "attempt_id": self.attempt, "status": "completed"}
        (self.job / "supervisor-receipt.json").write_bytes(json_bytes(self.receipt))
        for name in ("result.json", "final.json", "receipt.schema.json"):
            (self.job / name).write_text("{}", encoding="utf-8")
        (self.runtime / "runtimes").mkdir()
        (self.runtime / "runtimes/cache.txt").write_text("Keep dependency cache", encoding="utf-8")
        self.queue("completed")
        (self.root / "docs/keep.md").write_text("Keep real output", encoding="utf-8")
        self.original = (self.root / "project.json").read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def queue(self, status):
        (self.root / "state").mkdir(exist_ok=True)
        (self.root / "state/workspace.json").write_bytes(json_bytes({"queue": [{"id": self.command, "status": status}]}))

    def plan(self, age=0):
        return retention.preview(self.root, older_than_seconds=age, min_free_bytes=0)

    def reseal(self, plan):
        plan["plan_sha256"] = digest(json_bytes({key: value for key, value in plan.items() if key != "plan_sha256"}))
        return plan

    def test_preview_is_read_only_and_apply_retains_originals_receipts_and_cache(self):
        plan = self.plan()
        self.assertEqual(4, len(plan["targets"]))
        self.assertTrue((self.job / "stdout.jsonl").exists())
        self.assertFalse((self.runtime / retention.JOURNAL).exists())
        result = retention.apply(self.root, plan)
        self.assertEqual(4, len(result["deleted"]))
        self.assertFalse((self.job / "workspace").exists())
        self.assertEqual(self.original, (self.root / "project.json").read_bytes())
        for path in (self.root / "docs/keep.md", self.runtime / "runtimes/cache.txt", self.job / "supervisor-receipt.json", self.job / "final.json", self.job / "result.json"):
            self.assertTrue(path.is_file(), str(path))
        events = [json.loads(line) for line in (self.runtime / retention.JOURNAL).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(4, sum(event["status"] == "deleted" for event in events))
        self.assertEqual([], retention._pending(self.runtime))

    def test_active_queue_blocks_preview_and_saved_plan_application(self):
        plan = self.plan()
        for status in ("queued", "claimed", "running", "unknown"):
            self.queue(status)
            with self.subTest(status=status), self.assertRaises(ValueError):
                retention.preview(self.root, older_than_seconds=0, min_free_bytes=0)
            with self.assertRaises(ValueError):
                retention.apply(self.root, plan)
        self.assertTrue((self.job / "workspace").is_dir())

    def test_lease_intent_child_or_live_pid_blocks_cleanup(self):
        plan = self.plan()
        for lease in ({"pid": 999999999, "command_id": self.command}, {"pid": 999999999, "child_pid": 999999999}, {"pid": os.getpid()}):
            (self.root / "state/workspace-runner.lock").write_bytes(json_bytes(lease))
            with self.subTest(lease=lease), self.assertRaises(ValueError):
                retention.apply(self.root, plan)

    def test_same_size_edit_and_mtime_only_change_each_reject_stale_plan(self):
        plan = self.plan()
        path = self.job / "stdout.jsonl"
        before = path.stat()
        original = path.read_bytes()
        path.write_bytes(b"x" * len(original))
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, "변경"):
            retention.apply(self.root, plan)
        path.write_bytes(original)
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        with self.assertRaisesRegex(ValueError, "변경"):
            retention.apply(self.root, plan)
        self.assertFalse((self.runtime / retention.JOURNAL).exists())

    def test_live_observed_child_or_runner_blocks_even_without_lease_file(self):
        plan = self.plan()
        for runner in ({"pid": os.getpid(), "status": "running"}, {"pid": 999999999, "child_pid": os.getpid(), "status": "stopped"}):
            (self.root / "state/workspace-runtime.json").write_bytes(json_bytes({"runners": {"synthetic": runner}}))
            with self.subTest(runner=runner), self.assertRaisesRegex(ValueError, "프로세스 관측"):
                retention.apply(self.root, plan)
        self.assertTrue((self.job / "workspace").is_dir())

    def test_receipt_change_or_missing_terminal_queue_is_rejected(self):
        plan = self.plan()
        (self.job / "supervisor-receipt.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(ValueError):
            retention.apply(self.root, plan)

    def test_custom_job_root_and_nonstandard_attempt_are_never_candidates(self):
        external = self.parent / "custom-jobs"
        external.mkdir()
        (external / "keep.log").write_text("Keep custom runtime", encoding="utf-8")
        (self.runtime / "jobs/attempt-not-uuid").mkdir()
        plan = self.plan()
        plan["runtime_root"] = str(external)
        self.reseal(plan)
        with self.assertRaisesRegex(ValueError, "경계"):
            retention.apply(self.root, plan)
        self.assertTrue((external / "keep.log").exists())

    def test_plan_tampering_and_outside_target_are_rejected(self):
        plan = self.plan()
        plan["targets"][0]["name"] = "../../project.json"
        with self.assertRaisesRegex(ValueError, "무결성"):
            retention.apply(self.root, plan)
        self.reseal(plan)
        with self.assertRaisesRegex(ValueError, "외에는"):
            retention.apply(self.root, plan)

    def test_age_and_disk_thresholds_are_explicit_and_respected(self):
        self.assertEqual([], self.plan(age=3600)["targets"])
        self.assertEqual([], retention.preview(self.root, older_than_seconds=0, min_free_bytes=1)["targets"])
        for age, free in ((True, 0), (float("nan"), 0), (-1, 0), (0, True), (0, -1)):
            with self.subTest(age=age, free=free), self.assertRaises(ValueError):
                retention.preview(self.root, older_than_seconds=age, min_free_bytes=free)

    def test_predelete_audit_failure_preserves_all_targets(self):
        plan = self.plan()
        with patch.object(retention, "_journal", side_effect=OSError("Synthetic full disk")), self.assertRaises(OSError):
            retention.apply(self.root, plan)
        self.assertTrue((self.job / "workspace").exists())
        self.assertTrue((self.job / "stdout.jsonl").exists())

    def test_completion_audit_failure_reports_deleted_path_and_blocks_future_apply(self):
        plan = self.plan()
        original = retention._journal
        def fail_completed(runtime, event):
            if event["status"] == "deleted":
                raise OSError("Synthetic completion write failure")
            original(runtime, event)
        with patch.object(retention, "_journal", side_effect=fail_completed), self.assertRaises(retention.RetentionError) as caught:
            retention.apply(self.root, plan)
        self.assertTrue(caught.exception.result["audit_incomplete"])
        deleted = caught.exception.result["deleted"]
        self.assertEqual(1, len(deleted))
        self.assertFalse((self.runtime / "jobs" / deleted[0]).exists())
        with self.assertRaisesRegex(retention.RetentionError, "감사"):
            self.plan()

    def test_partial_delete_failure_is_reported_and_not_claimed_as_completed(self):
        plan = self.plan()
        plan["targets"] = [item for item in plan["targets"] if item["name"] == "workspace"]
        self.reseal(plan)
        def partial(path):
            (path / "src/main.py").unlink()
            raise OSError("Synthetic partial removal")
        with patch.object(retention.shutil, "rmtree", side_effect=partial), self.assertRaises(retention.RetentionError) as caught:
            retention.apply(self.root, plan)
        self.assertEqual([], caught.exception.result["deleted"])
        self.assertTrue(caught.exception.result["failed"][0]["may_be_partial"])
        self.assertTrue(caught.exception.result["failed"][0]["still_exists"])

    def test_outside_symlink_never_deletes_target(self):
        outside = self.parent / "outside-directory"
        outside.mkdir()
        (outside / "keep.txt").write_text("Never delete", encoding="utf-8")
        link = self.job / "workspace/src/link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            if os.name != "nt":
                self.skipTest("Host does not permit symlink creation")
            quote = lambda value: "'" + str(value).replace("'", "''") + "'"
            command = "$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path " + quote(link) + " -Target " + quote(outside) + " | Out-Null"
            created = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], capture_output=True, timeout=15)
            if created.returncode:
                self.skipTest("Host does not permit directory junction creation")
        try:
            with self.assertRaisesRegex(ValueError, "연결|밖"):
                self.plan()
            self.assertEqual("Never delete", (outside / "keep.txt").read_text(encoding="utf-8"))
        finally:
            if link.is_symlink():
                link.unlink()
            else:
                link.rmdir()

    def test_later_start_journal_failure_preserves_accurate_partial_result(self):
        plan = self.plan()
        original = retention._journal
        starts = []
        def fail_second_start(runtime, event):
            if event["status"] == "delete_started":
                starts.append(event)
                if len(starts) == 2:
                    raise OSError("Synthetic second intent write failure")
            original(runtime, event)
        with patch.object(retention, "_journal", side_effect=fail_second_start), self.assertRaises(retention.RetentionError) as caught:
            retention.apply(self.root, plan)
        self.assertEqual(1, len(caught.exception.result["deleted"]))
        self.assertTrue((self.runtime / "jobs" / starts[1]["path"]).exists())

    def test_module_cli_defaults_to_preview_and_requires_apply_file(self):
        result = subprocess.run([sys.executable, "-m", "tools.runtime_retention", "preview", str(self.root), "--older-than-hours", "0", "--min-free-bytes", "0"], cwd=starter.ROOT, capture_output=True, encoding="utf-8", timeout=20)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(4, len(json.loads(result.stdout)["targets"]))
        self.assertTrue((self.job / "workspace").exists())


if __name__ == "__main__":
    unittest.main()
