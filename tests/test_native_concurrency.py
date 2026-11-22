"""Synthetic real-filesystem regressions for native projection/read contention.

Fixtures live only in .test-output/beta7-native-concurrency-*; host events here
are synthetic and are never delivered to a customer or live demo project.
"""
import contextlib
import errno
import hashlib
import io
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import lifecycle, native_bridge as bridge, native_inbox as inbox
from tools import native_observation as native, starter, storage, workspace


def _event(identifier, kind="agent_started", **extra):
    return {"event_id": identifier, "kind": kind, "session_id": "synthetic-session",
            "native_agent_id": "synthetic-worker", "role": "backend",
            "source": "host_hook", "at": "2026-11-22T00:00:01+00:00", **extra}


def _retry_busy(operation, deadline):
    """Retry real active-owner lock contention, never a failed transaction."""
    while True:
        try:
            return operation()
        except ValueError as exc:
            if not isinstance(exc.__cause__, FileExistsError) or time.monotonic() >= deadline:
                raise
            time.sleep(0.002)


def _process_worker(folder, kind, start, output):
    """Spawn-safe workers exercising the actual serialized APIs and filesystem."""
    try:
        if not start.wait(10):
            raise RuntimeError("test start barrier was not released")
        root = Path(folder)
        deadline = time.monotonic() + 25
        if kind.startswith("writer-"):
            for index in range(16):
                item = _event(kind + "-" + str(index), "tool_started", tool_name="exec_command")
                _retry_busy(lambda: native.ingest(root, "codex", item), deadline)
        else:
            for _ in range(32):
                _retry_busy(lambda: native.read(root), deadline)
                _retry_busy(lambda: native.cursor(root, "synthetic-cursor"), deadline)
                _retry_busy(lambda: bridge.poll_sources(root), deadline)
                _retry_busy(lambda: workspace.snapshot(root), deadline)
        output.put((kind, "ok"))
    except BaseException as exc:
        output.put((kind, type(exc).__name__ + ": " + str(exc)))
        raise


class NativeConcurrencyTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="beta7-native-concurrency-", dir=parent)
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temp.name), "synthetic", "Synthetic contention fixture", "product"))
        lifecycle.apply_patch(self.root, {"expected_revision": workspace.revision(self.root),
            "tasks": [{"role": "backend", "next_action": "Synthetic API task"}]})
        native.ingest(self.root, "codex", _event("initial", "session_started"))
        self.path = self.root / native.STORE
        self.original = self.path.read_bytes()
        self.revision = workspace.revision(self.root)

    def assert_projection_unchanged(self):
        raw = self.path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), hashlib.sha256(self.original).hexdigest())
        self.assertEqual(json.loads(raw)["seq"], 1)
        self.assertFalse((self.root / "state/pending.json").exists())
        self.assertFalse((self.root / "state/write.lock").exists())
        self.assertEqual(list(self.path.parent.glob(".write-*")), [])

    def test_all_managed_native_read_entrypoints_hold_the_real_project_lock(self):
        ticket = native.prepare_delegation(self.root, "TASK-001", "backend", "codex", "synthetic-session")
        operations = {
            "direct projection": lambda: native._read(self.root),
            "public read": lambda: native.read(self.root),
            "cursor getter": lambda: native.cursor(self.root, "synthetic-cursor"),
            "ticket resolution": lambda: native.resolve_delegation(self.root, "codex", "synthetic-session", ticket["token"]),
            "bridge source poll": lambda: bridge.poll_sources(self.root),
        }
        original_open = Path.open
        for name, operation in operations.items():
            with self.subTest(entrypoint=name):
                opened, release = threading.Event(), threading.Event()
                errors = []
                def hold_open(path, *args, **kwargs):
                    stream = original_open(path, *args, **kwargs)
                    if path == self.path and threading.current_thread().name == "managed-native-reader":
                        opened.set()
                        if not release.wait(5):
                            stream.close()
                            raise RuntimeError("reader release timed out")
                    return stream
                def read():
                    try:
                        operation()
                    except BaseException as exc:
                        errors.append(exc)
                with patch.object(Path, "open", hold_open):
                    thread = threading.Thread(target=read, name="managed-native-reader")
                    thread.start()
                    try:
                        self.assertTrue(opened.wait(5), name)
                        lock_path = self.root / "state/write.lock"
                        self.assertTrue(lock_path.exists(), name)
                        owner = lock_path.read_bytes()
                        before = self.path.read_bytes()
                        with self.assertRaises(ValueError) as caught:
                            native.ingest(self.root, "codex", _event("must-wait"))
                        self.assertIsInstance(caught.exception.__cause__, FileExistsError)
                        self.assertEqual(lock_path.read_bytes(), owner, "an active reader lock must not be stolen")
                        self.assertEqual(self.path.read_bytes(), before)
                    finally:
                        release.set()
                        thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertFalse((self.root / "state/write.lock").exists())
        self.assertFalse((self.root / "state/pending.json").exists())

    def test_ingest_inside_project_lock_keeps_managed_read_reentrant(self):
        # ingest owns the writer lock and its decorated _read must reuse it.
        result = native.ingest(self.root, "codex", _event("reentrant"))
        self.assertFalse(result["unchanged"])
        self.assertEqual(native.read(self.root)["seq"], 2)
        self.assertEqual(workspace.revision(self.root), self.revision)
        self.assertFalse((self.root / "state/write.lock").exists())

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_long_external_reader_failure_never_poisons_snapshot_or_business_writes(self):
        with self.path.open("rb") as reader:
            start = time.monotonic()
            with self.assertRaises(PermissionError):
                native.ingest(self.root, "codex", _event("held-reader"))
            elapsed = time.monotonic() - start
            self.assertGreaterEqual(elapsed, 0.22, "exercise a reader surviving the entire retry budget")
            self.assertLess(elapsed, 1.25, "bounded native retry must return promptly")
            self.assertEqual(reader.read(), self.original)
            self.assert_projection_unchanged()
            self.assertEqual(workspace.snapshot(self.root)["tasks"][0]["id"], "TASK-001")
            lifecycle.apply_patch(self.root, {"expected_revision": self.revision,
                "requirements": [{"description": "Business changes remain writable while raw native reader is held"}]})
            self.assertNotEqual(workspace.revision(self.root), self.revision)
            self.assertEqual(self.path.read_bytes(), self.original)
            self.assertFalse((self.root / "state/pending.json").exists())
        native.ingest(self.root, "codex", _event("held-reader"))
        self.assertEqual(native.read(self.root)["seq"], 2)

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_hook_start_and_end_survive_external_reader_then_drain_once(self):
        start = _event("queued-start")
        end = _event("queued-end", "agent_completed", at="2026-11-22T00:00:02+00:00")
        first = inbox.enqueue(self.root, "claude", start)
        second = inbox.enqueue(self.root, "claude", end)
        with self.path.open("rb"):
            with self.assertRaises(ValueError):
                inbox.drain(self.root)
            self.assertTrue(first.exists())
            self.assertTrue(second.exists())
            self.assert_projection_unchanged()
            self.assertEqual(workspace.snapshot(self.root)["summary_counts"]["completed"], 0)
        self.assertEqual(inbox.drain(self.root), 2)
        self.assertEqual(inbox.drain(self.root), 0)
        view = native.read(self.root)
        self.assertEqual(view["seq"], 3)
        self.assertEqual([e["id"] for e in view["events"]].count("queued-start"), 1)
        self.assertEqual([e["id"] for e in view["events"]].count("queued-end"), 1)
        worker = next(a for a in view["agents"] if a["provider"] == "claude" and a["native_agent_id"] == "synthetic-worker")
        self.assertEqual(worker["status"], "completed")
        self.assertEqual(workspace.snapshot(self.root)["summary_counts"]["completed"], 0,
                         "host termination must not fabricate verified business completion")
        self.assertEqual(workspace.revision(self.root), self.revision)
        self.assertFalse(first.exists())
        self.assertFalse(second.exists())
        self.assertFalse((self.root / "state/pending.json").exists())

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_actual_short_lived_reader_retries_replace_and_keeps_native_event(self):
        opened, denied, released = threading.Event(), threading.Event(), threading.Event()
        errors, codes = [], []
        original_write = native.atomic_write
        def reader():
            try:
                with self.path.open("rb"):
                    opened.set()
                    if not denied.wait(5):
                        raise RuntimeError("no real sharing denial was observed")
                    time.sleep(0.025)
            except BaseException as exc:
                errors.append(exc)
            finally:
                released.set()
        def write(path, raw):
            try:
                return original_write(path, raw)
            except PermissionError as exc:
                codes.append(exc.winerror)
                denied.set()
                raise
        thread = threading.Thread(target=reader)
        thread.start()
        try:
            self.assertTrue(opened.wait(5))
            with patch.object(native, "atomic_write", write):
                native.ingest(self.root, "codex", _event("short-reader"))
            self.assertTrue(denied.is_set(), "the test must exercise an actual replacement failure")
            self.assertTrue(all(code in {5, 32} for code in codes))
            self.assertTrue(released.wait(5))
        finally:
            denied.set()
            thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(native.read(self.root)["seq"], 2)
        self.assertEqual(workspace.revision(self.root), self.revision)
        self.assertFalse((self.root / "state/pending.json").exists())

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_codex_source_cursor_does_not_advance_on_denial_and_replays_exactly_once(self):
        session = "11111111-1111-4111-8111-111111111111"
        home = Path(self.temp.name) / "synthetic-codex-home"
        sources = home / "sessions/2026/11/22"
        sources.mkdir(parents=True)
        source = sources / ("rollout-synthetic-" + session + ".jsonl")
        source.write_text(json.dumps({"timestamp": "2026-11-22T00:00:01+00:00", "type": "session_meta",
            "payload": {"id": session, "cwd": str(self.root)}}) + "\n", encoding="utf-8")
        key = "codex:" + session
        with patch.dict(os.environ, {"CODEX_HOME": str(home)}):
            bridge.attach(self.root, session, str(source))
            before = native.cursor(self.root, key)
            original = self.path.read_bytes()
            records = [
                {"timestamp": "2026-11-22T00:00:02+00:00", "type": "response_item", "payload": {
                    "type": "function_call", "call_id": "synthetic-tool-call", "name": "exec_command", "arguments": "{}"}},
                {"timestamp": "2026-11-22T00:00:03+00:00", "type": "response_item", "payload": {
                    "type": "function_call_output", "call_id": "synthetic-tool-call", "output": "synthetic output"}},
            ]
            with source.open("a", encoding="utf-8") as stream:
                for record in records:
                    stream.write(json.dumps(record) + "\n")
            with self.path.open("rb"):
                with self.assertRaises(PermissionError):
                    bridge.poll_sources(self.root)
                self.assertEqual(native.cursor(self.root, key), before)
                self.assertEqual(self.path.read_bytes(), original)
                self.assertFalse((self.root / "state/pending.json").exists())
                self.assertEqual(workspace.snapshot(self.root)["tasks"][0]["id"], "TASK-001")
            self.assertEqual(bridge.poll_sources(self.root), 2)
            self.assertEqual(bridge.poll_sources(self.root), 0)
            self.assertEqual(native.cursor(self.root, key)["offset"], source.stat().st_size)
            events = [e for e in native.read(self.root)["events"] if e["session_id"] == session]
            self.assertEqual([e["kind"] for e in events], ["session_started", "tool_started", "tool_completed"])
            self.assertEqual(len({e["id"] for e in events}), 3)
            self.assertNotIn("synthetic output", self.path.read_text(encoding="utf-8"))
        self.assertEqual(workspace.revision(self.root), self.revision)
        self.assertFalse((self.root / "state/pending.json").exists())

    @unittest.skipUnless(os.name == "nt", "requires Windows replacement error metadata")
    def test_transient_exact_native_replace_denial_retries_then_succeeds(self):
        original_write = native.atomic_write
        attempts = []
        def replace(path, raw):
            attempts.append(path)
            if len(attempts) < 3:
                error = PermissionError(errno.EACCES, "synthetic replacement sharing denial")
                error.winerror = 32
                error.filename2 = str(self.path)
                raise error
            return original_write(path, raw)
        with patch.object(native, "atomic_write", replace):
            native.ingest(self.root, "codex", _event("transient"))
        self.assertEqual(attempts, [self.path] * 3)
        self.assertEqual(native.read(self.root)["seq"], 2)
        self.assertFalse((self.root / "state/pending.json").exists())
        self.assertEqual(workspace.revision(self.root), self.revision)

    @unittest.skipUnless(os.name == "nt", "requires Windows replacement error metadata")
    def test_persistent_exact_native_permission_denial_is_bounded_and_fail_closed(self):
        for code in (5, 32):
            with self.subTest(winerror=code):
                error = PermissionError(errno.EACCES, "synthetic persistent denial")
                error.winerror = code
                error.filename2 = str(self.path)
                started = time.monotonic()
                with patch.object(native, "atomic_write", side_effect=error) as write:
                    with self.assertRaises(PermissionError) as caught:
                        native.ingest(self.root, "codex", _event("denied-" + str(code)))
                elapsed = time.monotonic() - started
                self.assertIs(caught.exception, error)
                self.assertGreater(write.call_count, 1)
                self.assertGreaterEqual(elapsed, 0.22)
                self.assertLess(elapsed, 1.25)
                self.assert_projection_unchanged()
                self.assertEqual(workspace.revision(self.root), self.revision)

    def test_unrelated_permission_errors_are_not_retried_or_journaled(self):
        for code, destination in ((5, None), (5, self.root / "state/other.json"), (13, self.path)):
            with self.subTest(winerror=code, destination=destination):
                error = PermissionError(errno.EACCES, "synthetic unrelated denial")
                error.winerror = code
                if destination is not None:
                    error.filename2 = str(destination)
                with patch.object(native, "atomic_write", side_effect=error) as write:
                    with self.assertRaises(PermissionError) as caught:
                        native.ingest(self.root, "codex", _event("unrelated"))
                self.assertIs(caught.exception, error)
                self.assertEqual(write.call_count, 1)
                self.assert_projection_unchanged()

    def test_other_oserror_propagates_immediately_without_business_journal(self):
        error = OSError(errno.EIO, "synthetic I/O failure")
        with patch.object(native, "atomic_write", side_effect=error) as write:
            with self.assertRaises(OSError) as caught:
                native.ingest(self.root, "codex", _event("io-failure"))
        self.assertIs(caught.exception, error)
        self.assertEqual(write.call_count, 1)
        self.assert_projection_unchanged()
        self.assertEqual(workspace.revision(self.root), self.revision)

    def test_multiprocess_readers_writers_and_source_poll_preserve_every_event(self):
        ctx = multiprocessing.get_context("spawn")
        start, output = ctx.Event(), ctx.Queue()
        processes = [ctx.Process(target=_process_worker, args=(str(self.root), kind, start, output))
                     for kind in ("writer-a", "writer-b", "reader")]
        try:
            for process in processes:
                process.start()
            start.set()
            for process in processes:
                process.join(35)
                self.assertFalse(process.is_alive(), "bounded synthetic contention worker must finish")
                self.assertEqual(process.exitcode, 0)
            results = [output.get(timeout=5) for _ in processes]
            self.assertEqual(sorted(results), [("reader", "ok"), ("writer-a", "ok"), ("writer-b", "ok")])
        finally:
            start.set()
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(5)
            output.close()
            output.join_thread()
        view = native.read(self.root)
        self.assertEqual(view["seq"], 33)
        self.assertEqual(len({e["id"] for e in view["events"]}), 33)
        self.assertEqual(json.loads(self.path.read_bytes())["seq"], 33)
        self.assertEqual(workspace.revision(self.root), self.revision)
        self.assertFalse((self.root / "state/pending.json").exists())
        self.assertFalse((self.root / "state/write.lock").exists())
        self.assertEqual(list(self.path.parent.glob(".write-*")), [])

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_business_transaction_rollback_journal_is_still_preserved_on_failure(self):
        relative = "state/synthetic-business.json"
        path = self.root / relative
        path.write_bytes(b'{"before": true}\n')
        original = path.read_bytes()
        with path.open("rb"):
            with self.assertRaises(PermissionError), storage.lock(self.root):
                storage.commit(self.root, {relative: b'{"after": true}\n'})
            pending = json.loads((self.root / "state/pending.json").read_bytes())
            self.assertEqual([entry["path"] for entry in pending["entries"]], [relative])
            self.assertEqual(path.read_bytes(), original)
        self.assertTrue(storage.recover(self.root)["recovered"])
        self.assertFalse((self.root / "state/pending.json").exists())
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.path.read_bytes(), self.original)


if __name__ == "__main__":
    unittest.main()
