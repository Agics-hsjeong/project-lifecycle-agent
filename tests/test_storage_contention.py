"""Windows concurrency edge cases without relaxing path or permission guards."""
import base64
import contextlib
import errno
import json
import os
from pathlib import Path, PureWindowsPath
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools import starter, storage


class StorageContentionTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="storage-contention-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()

    def windows_os(self, open_function):
        proxy = SimpleNamespace(**vars(os))
        proxy.name, proxy.open = "nt", open_function
        return proxy

    def test_transient_windows_lock_delete_denial_is_retried_without_deletion(self):
        original = os.open
        for winerror in (None, 5, 32):
            with self.subTest(winerror=winerror):
                attempts = []
                def opening(path, flags, mode=0o777, **kwargs):
                    if Path(path) == self.root / "state/write.lock":
                        attempts.append(1)
                        if len(attempts) < 3:
                            error = PermissionError(errno.EACCES, "Synthetic pending delete", str(path))
                            if winerror is not None:
                                error.winerror = winerror
                            raise error
                    return original(path, flags, mode, **kwargs)
                with patch.object(storage, "os", self.windows_os(opening)):
                    with storage.lock(self.root):
                        self.assertTrue((self.root / "state/write.lock").is_file())
                self.assertEqual(len(attempts), 3)
                self.assertFalse((self.root / "state/write.lock").exists())

    def test_persistent_lock_acl_denial_remains_a_permission_failure(self):
        calls, sleeps = [], []
        clock = iter([0.0, .1, .3])
        def denied(path, flags, mode=0o777, **kwargs):
            calls.append(str(path))
            error = PermissionError(errno.EACCES, "Synthetic persistent ACL denial", str(path))
            error.winerror = 5
            raise error
        with patch.object(storage, "os", self.windows_os(denied)), patch.object(storage, "time",
                SimpleNamespace(monotonic=lambda: next(clock), sleep=sleeps.append)):
            with self.assertRaises(PermissionError):
                with storage.lock(self.root):
                    self.fail("An ACL denial must never acquire a lock")
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [.01])
        self.assertFalse((self.root / "state/write.lock").exists())

    def test_unrelated_permission_errors_are_never_retried(self):
        calls = []
        def denied(path, flags, mode=0o777, **kwargs):
            calls.append(1)
            error = PermissionError(errno.EACCES, "Synthetic unrelated file", str(self.root / "another.lock"))
            error.winerror = 5
            raise error
        with patch.object(storage, "os", self.windows_os(denied)):
            with self.assertRaises(PermissionError):
                with storage.lock(self.root):
                    pass
        self.assertEqual(calls, [1])

    def test_equivalent_windows_extended_names_are_only_normalized_for_comparison(self):
        normalize = storage._containment_path
        root = PureWindowsPath(r"D:\work\project")
        self.assertTrue(normalize(PureWindowsPath(r"\\?\D:\work\project\src\a.py")).is_relative_to(normalize(root)))
        self.assertTrue(normalize(PureWindowsPath(r"\\?\UNC\server\share\project\src\a.py")).is_relative_to(
            normalize(PureWindowsPath(r"\\server\share\project"))))
        for outside in (r"\\?\E:\work\project\a.py", r"\\?\D:\outside\a.py",
                        r"\\?\GLOBALROOT\Device\HarddiskVolume1\work\project\a.py", r"\\.\D:\work\project\a.py"):
            with self.subTest(outside=outside):
                self.assertFalse(normalize(PureWindowsPath(outside)).is_relative_to(normalize(root)))
        self.assertFalse(normalize(PureWindowsPath(r"\\?\UNC\server\different-share\project\a.py")).is_relative_to(
            normalize(PureWindowsPath(r"\\server\share\project"))))

    def test_path_traversal_ads_device_and_link_guards_are_preserved(self):
        for relative in ("../escape", "file:stream", "//server/share/a", "\\\\.\\NUL", "C:/outside"):
            with self.subTest(relative=relative), self.assertRaises(ValueError):
                storage.confined(self.root, relative)
        target = self.root / "linked.txt"
        original = Path.is_symlink
        with patch.object(Path, "is_symlink", lambda path: path == target or original(path)):
            with self.assertRaisesRegex(ValueError, "연결된 경로"):
                storage.confined(self.root, "linked.txt")
        resolve = Path.resolve
        with patch.object(Path, "resolve", lambda path, *args, **kwargs:
                self.root.parent / "outside.txt" if path == target else resolve(path, *args, **kwargs)):
            with self.assertRaisesRegex(ValueError, "프로젝트 밖"):
                storage.confined(self.root, "linked.txt")

    @unittest.skipUnless(os.name == "nt", "Windows final-name prefix race")
    def test_atomic_replace_between_windows_final_name_queries_keeps_inside_path_valid(self):
        import ntpath
        target = self.root / "inside.txt"
        target.write_text("Synthetic file", encoding="utf-8")
        original = ntpath._getfinalpathname
        calls = []
        def replacing(path):
            value = str(path)
            if value.casefold() == str(target).casefold():
                calls.append(1)
                if len(calls) == 2:
                    error = FileNotFoundError(2, "Synthetic atomic replacement", value)
                    error.winerror = 2
                    raise error
            return original(path)
        with patch.object(ntpath, "_getfinalpathname", side_effect=replacing):
            result = storage.confined(self.root, "inside.txt")
        self.assertEqual(result, target)
        self.assertTrue(result.samefile(target))
        self.assertEqual(len(calls), 2)

    @unittest.skipUnless(os.name == "nt", "Windows NTFS delete-pending final name")
    def test_deleted_ntfs_final_name_is_requeried_and_never_accepted(self):
        target = self.root / 'inside.txt'
        target.write_text('Synthetic file', encoding='utf-8')
        resolve = Path.resolve
        tombstone = Path(self.root.anchor) / '$Extend/$Deleted/synthetic-tombstone'
        calls = []
        def deleted(path, *args, **kwargs):
            if path == target:
                calls.append(1)
                if len(calls) == 1:
                    return tombstone
            return resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', deleted):
            self.assertEqual(storage.confined(self.root, 'inside.txt'), target)
        self.assertEqual(len(calls), 2)
        with patch.object(Path, 'resolve', lambda path,*a,**kw: tombstone if path == target else resolve(path,*a,**kw)):
            with self.assertRaisesRegex(ValueError, '프로젝트 밖'):
                storage.confined(self.root, 'inside.txt')
        outside = self.root.parent / 'outside.txt'
        with patch.object(Path, 'resolve', lambda path,*a,**kw: outside if path == target else resolve(path,*a,**kw)):
            with self.assertRaisesRegex(ValueError, '프로젝트 밖'):
                storage.confined(self.root, 'inside.txt')

    def test_disappearing_file_during_link_query_keeps_parent_checks(self):
        target = self.root / 'inside.txt'
        lstat = Path.lstat
        def removed(path, *args, **kwargs):
            if path == target:
                raise FileNotFoundError(2, 'Synthetic atomic replacement', str(path))
            return lstat(path, *args, **kwargs)
        with patch.object(Path, 'lstat', removed):
            self.assertEqual(storage.confined(self.root, 'inside.txt'), target)


class PendingJournalContentionTests(unittest.TestCase):
    """Real reader handles must not strand a completed business journal."""

    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="beta7-pending-contention-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        (self.root / "state").mkdir()
        self.pending = self.root / "state/pending.json"
        self.relative = "state/synthetic-business.json"
        self.target = self.root / self.relative
        self.before, self.after = b'{"before": true}\n', b'{"after": true}\n'
        self.target.write_bytes(self.before)

    def seed_pending(self):
        self.target.write_bytes(self.after)
        storage.atomic_write(self.pending, storage.json_bytes({"schema_version": 1, "entries": [{
            "path": self.relative, "before": base64.b64encode(self.before).decode(),
            "after": storage.digest(self.after)}]}))

    @contextlib.contextmanager
    def held_pending(self, *, short, existing=False, write_failure=None):
        """Open a real builtin reader and release after an actual unlink denial."""
        denied = threading.Event()
        handles, threads, errors, codes = [], [], [], []
        original_write, original_unlink = storage.atomic_write, Path.unlink
        def open_reader():
            if handles:
                return
            handle = self.pending.open("rb")
            handles.append(handle)
            if short:
                def release():
                    try:
                        if not denied.wait(5):
                            raise RuntimeError("no real journal unlink denial observed")
                        time.sleep(0.025)
                    except BaseException as exc:
                        errors.append(exc)
                    finally:
                        handle.close()
                thread = threading.Thread(target=release)
                threads.append(thread)
                thread.start()
        def writing(path, raw, **kwargs):
            if write_failure is not None and Path(path) == self.target and raw == self.after:
                raise write_failure
            result = original_write(path, raw, **kwargs)
            if Path(path) == self.pending:
                open_reader()
            return result
        def unlinking(path, *args, **kwargs):
            try:
                return original_unlink(path, *args, **kwargs)
            except PermissionError as exc:
                if path == self.pending:
                    codes.append(exc.winerror)
                    denied.set()
                raise
        try:
            with patch.object(storage, "atomic_write", writing), patch.object(Path, "unlink", unlinking):
                if existing:
                    open_reader()
                yield codes
        finally:
            denied.set()
            for thread in threads:
                thread.join(5)
            for handle in handles:
                handle.close()
        self.assertTrue(handles, "a real journal reader must be opened")
        self.assertTrue(codes, "a real sharing violation must occur before retry")
        self.assertTrue(all(code in {5, 32} for code in codes))
        self.assertEqual(errors, [])
        self.assertTrue(all(not thread.is_alive() for thread in threads))

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_short_pending_reader_allows_commit_to_finish_without_a_journal(self):
        with self.held_pending(short=True):
            with storage.lock(self.root):
                storage.commit(self.root, {self.relative: self.after})
        self.assertEqual(self.target.read_bytes(), self.after)
        self.assertFalse(self.pending.exists())
        self.assertFalse((self.root / "state/write.lock").exists())

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_long_pending_reader_preserves_journal_then_recover_restores_preimage(self):
        with self.held_pending(short=False):
            started = time.monotonic()
            with self.assertRaises(PermissionError):
                with storage.lock(self.root):
                    storage.commit(self.root, {self.relative: self.after})
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.22)
            self.assertLess(elapsed, 1.25)
            self.assertEqual(self.target.read_bytes(), self.after)
            entry = json.loads(self.pending.read_bytes())["entries"][0]
            self.assertEqual(base64.b64decode(entry["before"]), self.before)
            self.assertEqual(entry["after"], storage.digest(self.after))
            self.assertFalse((self.root / "state/write.lock").exists())
        self.assertTrue(storage.recover(self.root)["recovered"])
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertFalse(self.pending.exists())

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_short_pending_reader_allows_rollback_cleanup_and_original_error(self):
        failure = OSError(errno.EIO, "Synthetic business write failure")
        with self.held_pending(short=True, write_failure=failure):
            with self.assertRaises(OSError) as caught:
                with storage.lock(self.root):
                    storage.commit(self.root, {self.relative: self.after})
        self.assertIs(caught.exception, failure)
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertFalse(self.pending.exists())
        self.assertFalse((self.root / "state/write.lock").exists())

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_long_pending_reader_keeps_rollback_journal_recoverable(self):
        failure = OSError(errno.EIO, "Synthetic business write failure")
        with self.held_pending(short=False, write_failure=failure):
            started = time.monotonic()
            with self.assertRaises(PermissionError):
                with storage.lock(self.root):
                    storage.commit(self.root, {self.relative: self.after})
            self.assertGreaterEqual(time.monotonic() - started, 0.22)
            self.assertLess(time.monotonic() - started, 1.25)
            self.assertEqual(self.target.read_bytes(), self.before)
            self.assertTrue(self.pending.exists())
            self.assertFalse((self.root / "state/write.lock").exists())
        self.assertTrue(storage.recover(self.root)["recovered"])
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertFalse(self.pending.exists())

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_short_pending_reader_allows_recovery_cleanup_to_finish(self):
        self.seed_pending()
        with self.held_pending(short=True, existing=True):
            result = storage.recover(self.root)
        self.assertTrue(result["recovered"])
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertFalse(self.pending.exists())
        self.assertFalse((self.root / "state/write.lock").exists())

    @unittest.skipUnless(os.name == "nt", "requires real Windows delete-sharing semantics")
    def test_long_pending_reader_keeps_failed_recovery_replayable(self):
        self.seed_pending()
        original_journal = self.pending.read_bytes()
        with self.held_pending(short=False, existing=True):
            started = time.monotonic()
            with self.assertRaises(PermissionError):
                storage.recover(self.root)
            self.assertGreaterEqual(time.monotonic() - started, 0.22)
            self.assertLess(time.monotonic() - started, 1.25)
            self.assertEqual(self.target.read_bytes(), self.before)
            self.assertEqual(self.pending.read_bytes(), original_journal)
            self.assertFalse((self.root / "state/write.lock").exists())
        self.assertTrue(storage.recover(self.root)["recovered"])
        self.assertEqual(self.target.read_bytes(), self.before)
        self.assertFalse(self.pending.exists())

    def test_pending_retry_is_limited_to_exact_windows_sharing_errors(self):
        self.seed_pending()
        original = self.pending.read_bytes()
        original_unlink = Path.unlink
        for host, code, filename, kind in (
                ("nt", 5, None, PermissionError),
                ("nt", 32, self.root / "unrelated.json", PermissionError),
                ("nt", 13, self.pending, PermissionError),
                ("posix", 5, self.pending, PermissionError),
                ("nt", 5, self.pending, OSError),
                ("nt", None, self.pending, FileNotFoundError)):
            with self.subTest(host=host, code=code, filename=filename, kind=kind):
                failure = kind(errno.EIO, "Synthetic unrelated cleanup failure", str(filename) if filename else None)
                if code is not None:
                    failure.winerror = code
                calls = []
                def unlinking(path, *args, **kwargs):
                    if path == self.pending:
                        calls.append(path)
                        raise failure
                    return original_unlink(path, *args, **kwargs)
                proxy = SimpleNamespace(**vars(os))
                proxy.name = host
                with storage.lock(self.root, recovering=True):
                    with patch.object(storage, "os", proxy), patch.object(Path, "unlink", unlinking):
                        with self.assertRaises(kind) as caught:
                            storage._clear_pending(self.root)
                self.assertIs(caught.exception, failure)
                self.assertEqual(calls, [self.pending])
                self.assertEqual(self.pending.read_bytes(), original)
        self.assertEqual(self.target.read_bytes(), self.after)

    def test_persistent_pending_denial_obeys_250ms_budget_and_preserves_bytes(self):
        self.seed_pending()
        original = self.pending.read_bytes()
        original_unlink = Path.unlink
        for code in (5, 32):
            with self.subTest(winerror=code):
                failure = PermissionError(errno.EACCES, "Synthetic persistent cleanup denial", str(self.pending))
                failure.winerror = code
                calls, sleeps = [], []
                clock = iter([0.0, 0.1, 0.25])
                def unlinking(path, *args, **kwargs):
                    if path == self.pending:
                        calls.append(path)
                        raise failure
                    return original_unlink(path, *args, **kwargs)
                proxy = SimpleNamespace(**vars(os))
                proxy.name = "nt"
                with storage.lock(self.root, recovering=True):
                    with patch.object(storage, "os", proxy), patch.object(Path, "unlink", unlinking), patch.object(
                            storage, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=sleeps.append)):
                        with self.assertRaises(PermissionError) as caught:
                            storage._clear_pending(self.root)
                self.assertIs(caught.exception, failure)
                self.assertEqual(calls, [self.pending, self.pending])
                self.assertEqual(sleeps, [0.01])
                self.assertEqual(self.pending.read_bytes(), original)
        self.assertFalse((self.root / "state/write.lock").exists())
