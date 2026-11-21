"""Windows concurrency edge cases without relaxing path or permission guards."""
import errno
import os
from pathlib import Path, PureWindowsPath
import tempfile
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
