import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

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


if __name__ == "__main__":
    unittest.main()
