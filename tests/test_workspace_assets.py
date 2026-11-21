"""Asset isolation and corruption checks for the distributable local UI."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools import build_workspace


class WorkspaceAssetTests(unittest.TestCase):
    def test_bundle_copies_only_entry_dependencies_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, output = root / "website/dist", root / "tools/ui"
            (source / "workspace").mkdir(parents=True)
            (source / "_astro").mkdir()
            (source / "fonts").mkdir()
            (source / "downloads").mkdir()
            (source / "workspace/index.html").write_text('<script type="module" src="/project-lifecycle-agent/_astro/entry.js"></script>', encoding="utf-8")
            (source / "_astro/entry.js").write_text('import "./shared.js"; import "./style.css";', encoding="utf-8")
            (source / "_astro/shared.js").write_text('export const version=1;', encoding="utf-8")
            (source / "_astro/style.css").write_text('@font-face {src:url("/project-lifecycle-agent/fonts/test.woff2")}', encoding="utf-8")
            (source / "fonts/test.woff2").write_bytes(b"font fixture")
            (source / "downloads/private.txt").write_text("must not ship", encoding="utf-8")
            with patch.object(build_workspace, "ROOT", root):
                result = build_workspace.bundle(source, output)
                self.assertEqual(result["files"], 5)
                self.assertFalse((output / "downloads").exists())
                self.assertTrue(build_workspace.verify(output)["ready"])
                (output / "_astro/shared.js").write_bytes(b"changed")
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    build_workspace.verify(output)

    def test_manifest_traversal_is_rejected_before_read(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            (folder / "manifest.json").write_text(json.dumps({"schema_version": 1, "base": build_workspace.BASE,
                "entry": build_workspace.ENTRY, "sha256": {build_workspace.ENTRY: "x", "../outside": "y"}}), encoding="utf-8")
            # The entry must be valid so the traversal field is the failing one.
            (folder / "workspace").mkdir()
            data = b"fixture"
            (folder / build_workspace.ENTRY).write_bytes(data)
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
            manifest["sha256"][build_workspace.ENTRY] = build_workspace.checksum(data)
            (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unsafe asset path"):
                build_workspace.verify(folder)

    def test_source_fingerprint_is_independent_of_checkout_line_endings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "website/package.json"
            target.parent.mkdir()
            target.write_bytes(b'{\r\n  "name": "fixture"\r\n}\r\n')
            with patch.object(build_workspace, "ROOT", root):
                before = build_workspace.source_hashes()
                target.write_bytes(target.read_bytes().replace(b"\r\n", b"\n"))
                self.assertEqual(before, build_workspace.source_hashes())

    def test_stale_website_build_cannot_be_relabelled_as_current(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entry = root / "website/dist/workspace/index.html"
            source = root / "website/src/workspace/main.ts"
            entry.parent.mkdir(parents=True)
            source.parent.mkdir(parents=True)
            entry.write_text("<html></html>", encoding="utf-8")
            source.write_text("export const revised = true;", encoding="utf-8")
            stamp = entry.stat().st_mtime + 10
            os.utime(source, (stamp, stamp))
            with patch.object(build_workspace, "ROOT", root), self.assertRaisesRegex(ValueError, "newer than the build"):
                build_workspace.bundle(root / "website/dist", root / "tools/ui")

    def test_missing_or_external_entry_is_not_bundled(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "dist"
            source.mkdir()
            with patch.object(build_workspace, "ROOT", root):
                with self.assertRaisesRegex(ValueError, "Missing or unsafe"):
                    build_workspace.bundle(source, root / "tools/ui")
