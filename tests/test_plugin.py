import importlib.util
import json
import hashlib
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_plugin", ROOT / "tools/package_plugin.py")
packager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(packager)


class PluginTests(unittest.TestCase):
    def test_hosts_share_identity_and_skill(self):
        manifests = [json.loads((ROOT / file).read_text(encoding="utf-8")) for file in
                     ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json")]
        self.assertEqual(len({m["name"] for m in manifests}), 1)
        self.assertEqual(len({m["version"] for m in manifests}), 1)
        self.assertTrue((ROOT / "skills/project-lifecycle/SKILL.md").is_file())

    def test_archive_contains_runtime_and_excludes_customer_data(self):
        test_root = (ROOT / ".test-output").resolve()
        test_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_root) as directory:
            temporary = Path(directory).resolve()
            self.assertTrue(temporary.is_relative_to(test_root))
            archive_path = packager.package(temporary / "plugin.zip")
            with ZipFile(archive_path) as archive:
                names = archive.namelist()
                prefix = "project-lifecycle-agent/"
                for required in ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json",
                                 ".claude-plugin/marketplace.json", ".agents/plugins/marketplace.json",
                                 "skills/project-lifecycle/SKILL.md", "tools/starter.py", "blueprint/project.json"):
                    self.assertIn(prefix + required, names)
                self.assertFalse(any("/projects/" in name or "/.test-output/" in name or "/__pycache__/" in name for name in names))
                catalog = json.loads(archive.read(prefix + ".agents/plugins/marketplace.json"))
                self.assertEqual(catalog["plugins"][0]["source"]["path"], "./")
                self.assertIsNone(archive.testzip())

    def test_reproducible_archive_checksums_and_extracted_runtime(self):
        test_root = ROOT / ".test-output"
        test_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_root) as directory:
            temporary = Path(directory)
            first = packager.package(temporary / "first.zip")
            second = packager.package(temporary / "second.zip")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with ZipFile(first) as archive:
                prefix = "project-lifecycle-agent/"
                manifest = json.loads(archive.read(prefix + "PACKAGE.json"))
                for name, checksum in manifest["sha256"].items():
                    self.assertEqual(hashlib.sha256(archive.read(prefix + name)).hexdigest(), checksum)
                archive.extractall(temporary / "unpacked")
            result = subprocess.run([sys.executable, str(temporary / "unpacked/project-lifecycle-agent/tools/starter.py"), "doctor"],
                                    cwd=temporary, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONUTF8": "1"})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(json.loads(result.stdout)["ready"])

    def test_link_rejection_preserves_previous_artifact(self):
        test_root = ROOT / ".test-output"
        test_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_root) as directory:
            output = Path(directory) / "existing.zip"
            output.write_bytes(b"previous release")
            original = Path.is_symlink
            def linked(path):
                return path == ROOT / "tools/starter.py" or original(path)
            with patch.object(Path, "is_symlink", linked), self.assertRaisesRegex(ValueError, "연결된"):
                packager.package(output)
            self.assertEqual(output.read_bytes(), b"previous release")


if __name__ == "__main__":
    unittest.main()
