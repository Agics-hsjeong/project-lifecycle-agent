"""Request-scoped memoization must never conceal an external file edit."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import workspace


class SnapshotHashTests(unittest.TestCase):
    def test_repeated_file_hash_is_read_once_in_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); (root/'shared.txt').write_bytes(b'first')
            original = Path.open
            reads = []
            def observed(path, *args, **kwargs):
                if path.name == 'shared.txt' and args and args[0] == 'rb': reads.append(path)
                return original(path, *args, **kwargs)
            @workspace.snapshot_hash_scope
            def snapshot():
                return [workspace.file_hash(root, 'shared.txt') for _ in range(50)]
            with patch.object(Path, 'open', observed):
                values = snapshot()
            self.assertEqual(len(reads), 1)
            self.assertEqual(len(set(values)), 1)

    def test_external_edits_visible_without_revision_and_scope_is_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root/'shared.txt'; path.write_bytes(b'first')
            @workspace.snapshot_hash_scope
            def snapshot(): return workspace.file_hash(root, 'shared.txt')
            before = snapshot(); path.write_bytes(b'other')
            self.assertNotEqual(before, snapshot())
            self.assertIsNone(workspace._HASH_SCOPE.get())

    def test_exception_clears_cache_and_in_scope_stat_change_invalidates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path=root/'shared.txt'; path.write_bytes(b'first')
            @workspace.snapshot_hash_scope
            def snapshot():
                first=workspace.file_hash(root, 'shared.txt')
                path.write_bytes(b'longer changed bytes')
                self.assertNotEqual(first,workspace.file_hash(root, 'shared.txt'))
                raise ValueError('fixture')
            with self.assertRaises(ValueError): snapshot()
            self.assertIsNone(workspace._HASH_SCOPE.get())
