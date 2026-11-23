"""Durable delivery during real business-lock contention without raw content."""
import contextlib
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import native_inbox as inbox, native_observation as native, starter
from tools.storage import ProjectBusyError, confined, json_bytes, lock


class NativeInboxTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="native-inbox-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temp.name), "inbox", "Synthetic inbox", "product"))

    def event(self, identifier="event-1", kind="agent_started", **fields):
        return {"event_id": identifier, "kind": kind, "session_id": "session-a", "native_agent_id": "worker",
                "source": "host_hook", "at": "2026-11-22T00:00:01+00:00", "role": "backend", **fields}

    def test_real_lock_contention_retains_terminal_event_then_drains_after_release(self):
        native.ingest(self.root, "claude", self.event())
        ready = threading.Event()
        def hold():
            with lock(self.root):
                ready.set()
                time.sleep(2.3)
        thread = threading.Thread(target=hold)
        thread.start()
        self.addCleanup(thread.join)
        ready.wait(5)
        end = self.event("event-2", "agent_completed", at="2026-11-22T00:00:02+00:00")
        path = inbox.enqueue(self.root, "claude", end)
        self.assertTrue(path.is_file())
        with self.assertRaises(ProjectBusyError):
            inbox.drain(self.root)
        self.assertTrue(path.is_file())
        thread.join(5)
        self.assertEqual(inbox.drain(self.root), 1)
        self.assertFalse(path.exists())
        self.assertEqual(native.read(self.root)["agents"][0]["status"], "completed")

    def test_identical_events_share_one_durable_file_and_ingest_once(self):
        first = inbox.enqueue(self.root, "claude", self.event())
        second = inbox.enqueue(self.root, "claude", self.event())
        self.assertEqual(first, second)
        self.assertEqual(len(list(first.parent.glob("*.json"))), 1)
        self.assertEqual(inbox.drain(self.root), 1)
        self.assertEqual(native.read(self.root)["seq"], 1)
        # A crash after ingest but before acknowledgement is also safe.
        inbox.enqueue(self.root, "claude", self.event())
        self.assertEqual(inbox.drain(self.root), 1)
        self.assertEqual(native.read(self.root)["seq"], 1)

    def test_concurrent_identical_enqueue_is_atomic_and_has_no_stale_owner_lock(self):
        barrier = threading.Barrier(6)
        paths, errors = [], []
        def write():
            barrier.wait()
            try:
                paths.append(inbox.enqueue(self.root, "claude", self.event()))
            except Exception as exc:
                errors.append(type(exc).__name__)
        threads = [threading.Thread(target=write) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)
        self.assertEqual(errors, [])
        self.assertEqual(len(paths), 6)
        self.assertEqual(len(set(paths)), 1)
        self.assertEqual(inbox.drain(self.root), 1)

    def test_secret_and_raw_fields_are_rejected_before_any_inbox_write(self):
        for key in ("prompt", "arguments", "result", "reasoning", "transcript_path", "encrypted_content"):
            with self.subTest(field=key), self.assertRaises(ValueError):
                inbox.enqueue(self.root, "claude", self.event(**{key: "private-value"}))
        self.assertFalse((self.root / inbox.DIRECTORY).exists())
        self.assertFalse((self.root / inbox.LOCK_FILE).exists())

    def test_summary_credentials_are_masked_and_unrecognised_tool_name_is_generic(self):
        path = inbox.enqueue(self.root, "claude", self.event(summary='password="private-value"', tool_name="private_customer_tool"))
        raw = path.read_text(encoding="utf-8")
        self.assertNotIn("private-value", raw)
        self.assertNotIn("private_customer_tool", raw)
        self.assertIn("[redacted]", raw)
        self.assertEqual(json.loads(raw)["event"]["tool_name"], "local_tool")
        self.assertEqual(path.name, hashlib.sha256(path.read_bytes()).hexdigest() + ".json")

    def test_corrupt_record_is_preserved_and_does_not_starve_valid_event_budget_one(self):
        valid = inbox.enqueue(self.root, "claude", self.event())
        corrupt = valid.parent / ("0" * 64 + ".json")
        corrupt.write_bytes(b'{"partial":')
        with self.assertRaisesRegex(ValueError, "원본은 보존"):
            inbox.drain(self.root, max_events=1)
        self.assertTrue(corrupt.is_file())
        self.assertEqual(corrupt.read_bytes(), b'{"partial":')
        self.assertFalse(valid.exists())
        self.assertEqual(native.read(self.root)["seq"], 1)

    def test_conflicting_event_id_is_retained_and_later_good_record_still_drains(self):
        native.ingest(self.root, "claude", self.event())
        conflict = inbox.enqueue(self.root, "claude", self.event(kind="agent_failed"))
        good = inbox.enqueue(self.root, "claude", self.event("event-2", "agent_completed", at="2026-11-22T00:00:02+00:00"))
        with self.assertRaises(ValueError):
            inbox.drain(self.root, max_events=1)
        self.assertTrue(conflict.exists())
        self.assertFalse(good.exists())
        self.assertEqual(native.read(self.root)["agents"][0]["status"], "completed")

    def test_queued_events_are_applied_in_host_time_order(self):
        inbox.enqueue(self.root, "claude", self.event("end", "agent_completed", at="2026-11-22T00:00:02+00:00"))
        inbox.enqueue(self.root, "claude", self.event("start"))
        self.assertEqual(inbox.drain(self.root), 2)
        self.assertEqual([event["kind"] for event in native.read(self.root)["events"]], ["agent_started", "agent_completed"])
        self.assertEqual(native.read(self.root)["agents"][0]["status"], "completed")

    def test_queue_count_total_size_and_event_limits_fail_without_overwrite(self):
        existing = inbox.enqueue(self.root, "claude", self.event())
        before = existing.read_bytes()
        with patch.object(inbox, "MAX_QUEUED", 1):
            self.assertEqual(inbox.enqueue(self.root, "claude", self.event()), existing)
            with self.assertRaises(ValueError):
                inbox.enqueue(self.root, "claude", self.event("event-2"))
        with patch.object(inbox, "MAX_TOTAL", len(before)):
            with self.assertRaises(ValueError):
                inbox.enqueue(self.root, "claude", self.event("event-2"))
        with patch.object(inbox, "MAX_EVENT", len(before) - 1):
            with self.assertRaises(ValueError):
                inbox.enqueue(self.root, "claude", self.event("event-2"))
        self.assertEqual(existing.read_bytes(), before)

    def test_invalid_metadata_and_batch_limits_do_not_write(self):
        invalid = [self.event(at="not-a-time"), self.event(at="2026-11-22T00:00:00"), self.event(event_id="../invalid id"),
                   self.event(summary="a" * 501), self.event(source="user_prompt"), self.event(kind=[])]
        for event in invalid:
            with self.subTest(event=event), self.assertRaises(ValueError):
                inbox.enqueue(self.root, "claude", event)
        for amount in (0, 513, True, "1"):
            with self.subTest(amount=amount), self.assertRaises(ValueError):
                inbox.drain(self.root, max_events=amount)
        self.assertFalse((self.root / inbox.DIRECTORY).exists())

    def test_immediate_ack_race_during_another_enqueue_preserves_new_event(self):
        first = inbox.enqueue(self.root, "claude", self.event())
        native.ingest(self.root, "claude", self.event())
        original = inbox._files
        def acknowledged(root, folder):
            paths = original(root, folder)
            first.unlink(missing_ok=True)
            return paths
        with patch.object(inbox, "_files", side_effect=acknowledged):
            second = inbox.enqueue(self.root, "claude", self.event("event-2", "agent_completed"))
        self.assertTrue(second.is_file())
        self.assertEqual(inbox.drain(self.root), 1)
        self.assertEqual(native.read(self.root)["seq"], 2)

    @unittest.skipUnless(os.name == "nt", "Windows directory junction boundary")
    def test_windows_directory_junction_is_rejected_without_admin_symlink_privilege(self):
        outside = Path(self.temp.name) / "outside-junction"
        outside.mkdir()
        directory = self.root / inbox.DIRECTORY
        directory.parent.mkdir(exist_ok=True)
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(directory), str(outside)], capture_output=True)
        if result.returncode:
            self.skipTest("Host does not permit directory junction creation")
        try:
            with self.assertRaises(ValueError):
                inbox.enqueue(self.root, "claude", self.event())
            with self.assertRaises(ValueError):
                inbox.drain(self.root)
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            directory.rmdir()

    def test_symlink_directory_and_file_never_write_or_read_outside_project(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        directory = self.root / inbox.DIRECTORY
        directory.parent.mkdir(exist_ok=True)
        try:
            directory.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not grant symlink creation")
        with self.assertRaises(ValueError):
            inbox.enqueue(self.root, "claude", self.event())
        self.assertEqual(list(outside.iterdir()), [])
        directory.unlink()
        valid = inbox.enqueue(self.root, "claude", self.event())
        external = outside / "private.json"
        external.write_text("private sentinel", encoding="utf-8")
        link = valid.parent / ("f" * 64 + ".json")
        link.symlink_to(external)
        with self.assertRaises(ValueError):
            inbox.drain(self.root)
        self.assertEqual(external.read_text(encoding="utf-8"), "private sentinel")
        self.assertTrue(valid.exists())


if __name__ == "__main__":
    unittest.main()
