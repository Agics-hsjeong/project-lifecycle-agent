"""The host entrypoint must return usable UTF-8 on Korean Windows too."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from tools import starter


class WorkspaceCliTests(unittest.TestCase):
    def test_snapshot_json_survives_non_utf8_host_console(self):
        test_root = starter.ROOT / ".test-output"
        test_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_root) as folder:
            project = starter.init_project(Path(folder), "utf8-workspace", "한글 관제 검증", "product", profile="api")
            env = {**os.environ, "PYTHONIOENCODING": "cp949", "PYTHONUTF8": "0"}
            for command in ([sys.executable, str(starter.ROOT / "tools/starter.py"), "workspace-snapshot", str(project), "--json"],
                            [sys.executable, "-m", "tools.workspace", "snapshot", str(project)]):
                with self.subTest(command=command[1:3]):
                    result = subprocess.run(command, cwd=starter.ROOT, env=env, capture_output=True, timeout=20)
                    self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
                    value = json.loads(result.stdout.decode("utf-8"))
                    self.assertEqual(value["project"]["name"], "한글 관제 검증")
                    self.assertIsNone(value["usage"]["tokens"])
