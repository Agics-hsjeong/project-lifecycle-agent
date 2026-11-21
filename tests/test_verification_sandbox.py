"""Verification boundary regressions; real CLI tests are opt-in and never call a model."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import host_providers, lifecycle, starter, verification_sandbox, workspace


class SandboxFixture:
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="verification-boundary-", dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.copy = self.base / "copy"
        self.copy.mkdir()
        self.project = self.base / "original-project"
        self.project.mkdir()

    def make_job(self, *, malicious=False):
        with contextlib.redirect_stdout(io.StringIO()):
            self.project = Path(starter.init_project(self.base / "projects", "synthetic", "Synthetic verification", "product", profile="api"))
        (self.project / "src").mkdir()
        (self.project / "src/policy.py").write_text("POLICY = 'before'\n", encoding="utf-8")
        generated = ("import unittest\nfrom pathlib import Path\nclass Generated(unittest.TestCase):\n"
                     + (" def test_generated(self):\n  Path(" + repr(str(self.project / "src/policy.py"))
                        + ").write_text('UNSAFE_EXTERNAL_WRITE',encoding='utf-8')\n  self.assertTrue(True)\n" if malicious else
                        " def test_generated(self):\n  from src.policy import POLICY\n  self.assertEqual(POLICY,'owner')\n"))
        self.fake_cli = self.base / "synthetic-producer.py"
        self.fake_cli.write_text(
            "import json,os,sys\nfrom pathlib import Path\nargs=sys.argv[1:]\n"
            "root=Path(args[args.index('-C')+1]);os.chdir(root)\nsys.stdin.read()\n"
            "(root/'src/policy.py').write_text(\"POLICY = 'owner'\\n\",encoding='utf-8')\n"
            "(root/'tests').mkdir(exist_ok=True)\n"
            + "(root/'tests/test_generated.py').write_text(" + repr(generated) + ",encoding='utf-8')\n"
            + "Path(args[args.index('-o')+1]).write_text(json.dumps({'summary':'Synthetic produced code, no model call',"
              "'evidence_refs':['src/policy.py','tests/test_generated.py']}),encoding='utf-8')\n"
              "print(json.dumps({'type':'thread.started','thread_id':'synthetic-no-model'}))\n", encoding="utf-8")
        execution = {"instruction": "Produce only synthetic owner code and a test.",
                     "allowed_paths": ["src/policy.py", "tests/test_generated.py"],
                     "expected_outputs": ["src/policy.py", "tests/test_generated.py"],
                     "verification_commands": [[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]]}
        lifecycle.apply_patch(self.project, {"expected_revision": None, "tasks": [
            {"role": "backend", "owner": "Synthetic producer", "status": "todo", "next_action": "Owner policy"}]})
        workspace.configure(self.project, {"expected_revision": lifecycle.events(self.project)[-1]["revision"],
            "tasks": [{"id": "TASK-001", "stage": "implementation", "execution": execution}]})
        self.task = workspace.snapshot(self.project)["tasks"][0]

    def run_job(self, executable=None):
        return host_providers.run_job(self.project, self.task, {"id": "backend", "mission": "Synthetic verification test"},
            self.base / "job", provider="codex", executable=[sys.executable, str(self.fake_cli)],
            verification_executable=executable, timeout=12)


class VerificationSandboxTests(SandboxFixture, unittest.TestCase):
    def test_prepare_requires_managed_strict_profile_and_only_copy_writes(self):
        def portable(runtime, root):
            return runtime / "python" / Path(sys.executable).name
        with patch.object(host_providers, "executable_argv", return_value=["trusted-codex"]), \
                patch.object(verification_sandbox, "portable_python", side_effect=portable):
            command, environment, info = verification_sandbox.prepare(
                [sys.executable, "-m", "unittest"], self.copy, self.project)
        self.addCleanup(verification_sandbox.cleanup, info)
        profile = next(value for value in command if value.startswith("permissions.ieum-verification="))
        required = [json.dumps(":root")+'="deny"',
                    json.dumps(self.copy.resolve().as_posix(), ensure_ascii=False)+'="write"']
        private_rules = [json.dumps(path.as_posix(), ensure_ascii=False)+'="deny"'
                         for path in (Path.home(), self.project.resolve())]
        if not sys.platform.startswith('linux'):
            required += private_rules
        else:
            for rule in private_rules:
                self.assertNotIn(rule, profile, "Default-denied private paths must not add redundant bwrap mounts")
        for rule in required:
            self.assertIn(rule, profile)
        self.assertIn("network={enabled=false}", profile)
        self.assertIn("--include-managed-config", command)
        self.assertEqual(command[command.index("--permission-profile")+1], "ieum-verification")
        self.assertEqual(Path(command[command.index("-C")+1]), self.copy.resolve())
        runtime = Path(info["runtime_root"])
        self.assertEqual(runtime.parent, Path(tempfile.gettempdir()).resolve())
        self.assertTrue(runtime.name.startswith("ieum-v-"))
        self.assertNotIn(json.dumps(runtime.parent.as_posix(), ensure_ascii=False)+'="write"', profile)
        self.assertIn(json.dumps(runtime.as_posix(), ensure_ascii=False)+'="read"', profile)
        self.assertFalse(runtime.is_relative_to(self.copy))
        if os.name == "nt":
            self.assertIn('windows.sandbox="mxc"', command)
            self.assertIn("windows.require_mxc=true", command)
            self.assertTrue(Path(command[command.index("--")+1]).is_relative_to(runtime))
        self.assertFalse(info["network"])
        marker = Path(info["started_marker"])
        self.assertTrue(marker.is_relative_to(self.copy / verification_sandbox.INTERNAL))
        self.assertFalse(marker.exists(), "Only the actual sandbox entrypoint may issue its start receipt")
        wrapped = command[command.index("--")+1:]
        self.assertTrue(Path(wrapped[1]).is_relative_to(runtime))
        self.assertEqual(Path(wrapped[2]), marker)
        self.assertEqual(json.loads(wrapped[3])[1:], ["-m", "unittest"])
        self.assertEqual(info["write_root"], str(self.copy.resolve()))
        self.assertEqual(Path(environment["TEMP"]), self.copy / verification_sandbox.INTERNAL / "tmp")

    def test_linux_default_deny_has_no_nested_private_masks_or_host_write_grants(self):
        runtime = self.base / 'trusted-runtime'
        selected = Path(sys.executable).resolve()
        rules = verification_sandbox._filesystem_rules(self.copy.resolve(), self.project.resolve(), runtime.resolve(), selected, platform='linux')
        self.assertEqual(rules[':root'], 'deny')
        self.assertEqual(rules[':minimal'], 'read')
        self.assertNotIn(Path.home().as_posix(), rules)
        self.assertNotIn(self.project.resolve().as_posix(), rules)
        self.assertEqual([path for path, access in rules.items() if access == 'write'], [self.copy.resolve().as_posix()])
        self.assertEqual(rules[runtime.resolve().as_posix()], 'read')
        self.assertFalse(any(path == runtime.parent.as_posix() and access == 'write' for path, access in rules.items()))

    def test_linux_private_project_inside_a_runtime_read_grant_fails_closed(self):
        with patch.object(verification_sandbox.sys, 'base_prefix', str(self.project.parent)):
            with self.assertRaises(verification_sandbox.SandboxUnavailable):
                verification_sandbox._filesystem_rules(self.copy.resolve(), self.project.resolve(), self.base/'trusted-runtime', Path(sys.executable).resolve(), platform='linux')

    def test_linux_broker_resolves_nested_npm_binary_without_directory_grants(self):
        package = self.base / 'node_modules/@openai/codex'
        entry = package / 'bin/codex.js'
        entry.parent.mkdir(parents=True);entry.write_text('// synthetic entry')
        native = package / 'node_modules/@openai/codex-linux-x64'
        native.mkdir(parents=True);(native/'package.json').write_text('{}')
        binary = native / 'vendor/x86_64-unknown-linux-musl/bin/codex'
        binary.parent.mkdir(parents=True);binary.write_text('synthetic binary')
        self.assertEqual(verification_sandbox._linux_sandbox_executable(['node',str(entry)],machine='x86_64'),binary.resolve())
        with patch.object(host_providers, 'executable_argv', return_value=['node',str(entry)]), \
                patch.object(verification_sandbox.sys, 'platform', 'linux'), \
                patch.object(verification_sandbox.platform, 'machine', return_value='x86_64'), \
                patch.object(verification_sandbox, 'portable_python', side_effect=lambda runtime,root: runtime/'python'):
            command, _, info = verification_sandbox.prepare([sys.executable,'-m','unittest'],self.copy,self.project)
        self.addCleanup(verification_sandbox.cleanup,info)
        profile = next(arg for arg in command if arg.startswith('permissions.ieum-verification='))
        self.assertIn(json.dumps(binary.resolve().as_posix(), ensure_ascii=False)+'="read"',profile)
        self.assertNotIn(json.dumps(native.as_posix(), ensure_ascii=False)+'=',profile)
        self.assertNotIn(json.dumps(self.base.as_posix(), ensure_ascii=False)+'=',profile)

    def test_linux_broker_resolves_hoisted_or_bundled_arm_package(self):
        package = self.base / 'node_modules/@openai/codex'
        entry = package / 'bin/codex.js'
        entry.parent.mkdir(parents=True);entry.write_text('// synthetic entry')
        native = self.base / 'node_modules/@openai/codex-linux-arm64'
        native.mkdir(parents=True);(native/'package.json').write_text('{}')
        binary = native / 'vendor/aarch64-unknown-linux-musl/bin/codex'
        binary.parent.mkdir(parents=True);binary.write_text('synthetic binary')
        self.assertEqual(verification_sandbox._linux_sandbox_executable(['node',str(entry)],machine='aarch64'),binary.resolve())
        (native/'package.json').unlink()
        bundled = package / 'vendor/aarch64-unknown-linux-musl/bin/codex'
        bundled.parent.mkdir(parents=True);bundled.write_text('synthetic bundled binary')
        self.assertEqual(verification_sandbox._linux_sandbox_executable(['node',str(entry)],machine='arm64'),bundled.resolve())

    def test_linux_broker_native_file_and_missing_package_fail_closed(self):
        binary = self.base / 'codex'
        binary.write_text('synthetic native binary')
        self.assertEqual(verification_sandbox._linux_sandbox_executable([str(binary)]),binary.resolve())
        entry = self.base/'missing/bin/codex.js'
        with self.assertRaises(verification_sandbox.SandboxUnavailable):
            verification_sandbox._linux_sandbox_executable(['node',str(entry)],machine='x86_64')

    def test_environment_strips_credentials_interpreters_and_app_hints(self):
        injected = {"OPENAI_API_KEY": "synthetic-secret", "ANTHROPIC_API_KEY": "synthetic-secret",
                    "AWS_SECRET_ACCESS_KEY": "synthetic-secret", "GH_TOKEN": "synthetic-secret",
                    "IEUM_SYNTHETIC_SECRET": "synthetic-secret", "PYTHONPATH": "untrusted-python",
                    "NODE_OPTIONS": "untrusted-node", "BASH_ENV": "untrusted-shell",
                    "CODEX_WINDOWS_REGISTERED_CORE": "untrusted-hint", "CODEX_HOME": "untrusted-home",
                    "SYSTEMROOT": "C:/Windows", "SYSTEMDRIVE": "C:", "PATH": "trusted-path"}
        with patch.dict(os.environ, injected, clear=True):
            result = verification_sandbox.safe_environment(self.copy)
        for name in set(injected)-{"SYSTEMROOT", "SYSTEMDRIVE", "PATH"}:
            self.assertNotIn(name, result)
        self.assertEqual(result["PATH"], "trusted-path")
        for name in ("TEMP", "TMP", "TMPDIR"):
            self.assertTrue(Path(result[name]).is_relative_to(self.copy))

    def test_missing_sandbox_is_unavailable_without_unsafe_fallback(self):
        with patch.object(host_providers, "executable_argv", side_effect=ValueError("CLI unavailable")), \
                patch.object(host_providers.subprocess, "Popen") as launch:
            with self.assertRaises(verification_sandbox.SandboxUnavailable):
                verification_sandbox.prepare([sys.executable, "-m", "unittest"], self.copy, self.project)
        launch.assert_not_called()

    def test_original_project_cannot_be_used_as_verification_copy(self):
        with patch.object(host_providers, "executable_argv") as launcher:
            for copy in (self.project, self.project / "nested-copy"):
                with self.subTest(copy=copy), self.assertRaises(verification_sandbox.SandboxUnavailable):
                    verification_sandbox.prepare([sys.executable, "-m", "unittest"], copy, self.project)
        launcher.assert_not_called()

    def test_each_verification_uses_new_supervisor_runtime(self):
        model_path = self.copy / verification_sandbox.INTERNAL / "python"
        model_path.mkdir(parents=True)
        (model_path / Path(sys.executable).name).write_bytes(b"SYNTHETIC_UNTRUSTED_RUNTIME")
        with patch.object(host_providers, "executable_argv", return_value=["trusted-codex"]), \
                patch.object(verification_sandbox, "portable_python", side_effect=lambda runtime, root: runtime / "python" / Path(sys.executable).name):
            first, _, first_info = verification_sandbox.prepare([sys.executable], self.copy, self.project)
            self.addCleanup(verification_sandbox.cleanup, first_info)
            second, _, second_info = verification_sandbox.prepare([sys.executable], self.copy, self.project)
            self.addCleanup(verification_sandbox.cleanup, second_info)
        runtimes = [Path(first_info["runtime_root"]), Path(second_info["runtime_root"])]
        self.assertEqual(len(runtimes), 2)
        self.assertNotEqual(runtimes[0], runtimes[1])
        self.assertTrue(all(not runtime.is_relative_to(self.copy) for runtime in runtimes))
        if os.name == "nt":
            self.assertNotEqual(first[first.index("--")+1], second[second.index("--")+1])
            self.assertNotEqual(Path(first[first.index("--")+1]).parent, model_path)

    def test_runtime_cleanup_rejects_unowned_targets(self):
        sentinel = self.copy / "retained.txt"
        sentinel.write_text("keep", encoding="utf-8")
        for target in (self.copy, Path(tempfile.gettempdir()), self.base / "ieum-v-fake"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                verification_sandbox.cleanup({"runtime_root": str(target)})
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_unavailable_verification_does_not_publish_producer_outputs(self):
        self.make_job()
        before = (self.project / "src/policy.py").read_bytes()
        with patch.object(verification_sandbox, "prepare", side_effect=verification_sandbox.SandboxUnavailable("synthetic unavailable")):
            result = self.run_job()
        self.assertFalse(result["success"], result)
        self.assertEqual(result["failure_kind"], "sandbox_unavailable")
        self.assertEqual((self.project / "src/policy.py").read_bytes(), before)
        self.assertFalse((self.project / "tests/test_generated.py").exists())
        self.assertFalse(result.get("published_paths"))


    def test_bootstrap_denial_is_unavailable_without_code_repair_evidence(self):
        self.make_job()
        before = (self.project / "src/policy.py").read_bytes()
        def denied(argv, root, project, **options):
            marker = Path(root) / ".ieum-verification" / "unissued-start-receipt"
            return ([sys.executable, "-c", "raise SystemExit(2)"],
                    verification_sandbox.safe_environment(root),
                    {"backend": "synthetic-bootstrap-denial", "started_marker": str(marker)})
        with patch.object(verification_sandbox, "prepare", side_effect=denied):
            result = self.run_job()
        self.assertFalse(result["success"], result)
        self.assertEqual(result["failure_kind"], "sandbox_unavailable")
        self.assertEqual(result["verification"], [])
        self.assertFalse(result.get("feedback_refs"))
        self.assertFalse(result.get("check_hashes"))
        self.assertFalse(result.get("published_paths"))
        self.assertEqual((self.project / "src/policy.py").read_bytes(), before)


@unittest.skipUnless(os.environ.get("IEUM_SANDBOX_CLI"), "Set IEUM_SANDBOX_CLI to verify the actual OS sandbox; no model calls")
class RealVerificationSandboxTests(SandboxFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.cli = os.environ["IEUM_SANDBOX_CLI"]

    def execute(self, argv, *, stop_event=None):
        command, environment, info = verification_sandbox.prepare(argv, self.copy, self.project, executable=self.cli)
        self.addCleanup(verification_sandbox.cleanup, info)
        code, _ = host_providers._process(command, self.copy, None, self.base/"actual.stdout", self.base/"actual.stderr", 12,
                                          stop_event=stop_event, environment=environment)
        return code, info

    def test_real_os_blocks_home_project_writes_network_and_runtime_tamper(self):
        (self.project / "synthetic.txt").write_text("synthetic original input", encoding="utf-8")
        outside = self.base / 'outside.txt'
        outside.write_text('synthetic private host sentinel', encoding='utf-8')
        outside_before = outside.read_bytes()
        with tempfile.TemporaryDirectory(prefix="ieum-home-boundary-") as home_temp:
            home_file = Path(home_temp) / "synthetic.txt"
            home_file.write_text("synthetic home input", encoding="utf-8")
            tests = self.copy / "tests"
            tests.mkdir()
            source = "import errno,socket,sys,unittest\nfrom pathlib import Path\nclass Boundaries(unittest.TestCase):\n"
            source += " def test_copy_write(self):\n  p=Path('inside.txt');p.write_text('inside');self.assertEqual(p.read_text(),'inside')\n"
            source += " def test_original_read(self):\n  with self.assertRaises(OSError) as denied: Path("+repr(str(self.project/'synthetic.txt'))+").read_bytes()\n  self.assertIn(denied.exception.errno,{errno.EACCES,errno.EPERM,errno.ENOENT})\n"
            source += " def test_home_read(self):\n  with self.assertRaises(OSError) as denied: Path("+repr(str(home_file))+").read_bytes()\n  self.assertIn(denied.exception.errno,{errno.EACCES,errno.EPERM,errno.ENOENT})\n"
            # Linux may create private tmpfs ancestor directories to mount the
            # candidate. A same-named shadow file there cannot modify the host.
            # Verify both that host contents are unreadable and that the actual
            # pre-existing host sentinel remains unchanged after execution.
            source += " def test_outside_host_write(self):\n  p=Path("+repr(str(outside))+")\n  with self.assertRaises(OSError) as denied: p.read_bytes()\n  self.assertIn(denied.exception.errno,{errno.EACCES,errno.EPERM,errno.ENOENT})\n  try: p.write_text('outside')\n  except OSError as blocked: self.assertIn(blocked.errno,{errno.EACCES,errno.EPERM,errno.EROFS,errno.ENOENT})\n  else: self.assertEqual(p.read_text(),'outside')\n"
            source += " def test_network(self):\n  with self.assertRaises(OSError) as denied: socket.create_connection(('127.0.0.1',9),timeout=.5)\n  self.assertNotIn(denied.exception.errno,{errno.ECONNREFUSED})\n"
            source += " def test_runtime_readonly(self):\n  with self.assertRaises(OSError) as denied: Path(sys.executable).parent.joinpath('tamper.txt').write_text('tamper')\n  self.assertIn(denied.exception.errno,{errno.EACCES,errno.EPERM,errno.EROFS})\n"
            (tests/"test_boundaries.py").write_text(source, encoding="utf-8")
            code, info = self.execute([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        self.assertEqual(code, 0, (self.base/"actual.stderr").read_text(encoding="utf-8"))
        self.assertTrue((self.copy/"inside.txt").is_file())
        self.assertEqual(outside.read_bytes(), outside_before)
        self.assertEqual(info["backend"], "codex-sandbox-mxc" if os.name == "nt" else "codex-sandbox")

    def test_real_generated_escape_test_fails_and_nothing_is_published(self):
        self.make_job(malicious=True)
        before = (self.project/"src/policy.py").read_bytes()
        control_before = (self.project/"project.json").read_bytes()
        result = self.run_job(self.cli)
        self.assertFalse(result["success"], result)
        self.assertEqual(result["failure_kind"], "verification_failed", result)
        self.assertNotEqual(result["verification"][0]["exit_code"], 0)
        self.assertEqual((self.project/"src/policy.py").read_bytes(), before)
        self.assertEqual((self.project/"project.json").read_bytes(), control_before)
        self.assertFalse((self.project/"tests/test_generated.py").exists())
        self.assertFalse(result.get("published_paths"))
        self.assertFalse(Path(result["verification_sandbox"]["runtime_root"]).exists())
        log = (self.base/"job"/"verification-0.stderr").read_text(encoding="utf-8")
        self.assertTrue(any(name in log for name in ("PermissionError", "FileNotFoundError", "Read-only file system")), log)

    def test_real_safe_registered_test_can_publish_after_verification(self):
        self.make_job()
        result = self.run_job(self.cli)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["verification"][0]["exit_code"], 0)
        self.assertEqual((self.project/"src/policy.py").read_text(encoding="utf-8"), "POLICY = 'owner'\n")
        self.assertEqual(set(result["published_paths"]), {"src/policy.py", "tests/test_generated.py"})
        self.assertEqual(starter.read_table(self.project, "tasks")[0]["status"], "todo")
        self.assertEqual(starter.read_table(self.project, "reviews"), [])
        self.assertFalse(Path(result["verification_sandbox"]["runtime_root"]).exists())

    def test_real_deep_copy_path_keeps_executable_short_and_runs(self):
        # A normal attempt/job path can approach CreateProcessW's executable limit.
        deep = self.base
        while len(str(deep)) < 175:
            deep = deep / ("attempt-" + "x" * 22)
        width = max(1, 210 - len(str(deep)) - len("candidate-") - 1)
        self.copy = deep / ("candidate-" + "x" * width)
        self.copy.mkdir(parents=True)
        self.assertGreaterEqual(len(str(self.copy)), 200)
        script = "from pathlib import Path;import unittest;Path('deep-result.txt').write_text('verified')"
        command, environment, info = verification_sandbox.prepare(
            [sys.executable, "-c", script], self.copy, self.project, executable=self.cli)
        self.addCleanup(verification_sandbox.cleanup, info)
        executable = command[command.index("--")+1]
        if os.name == "nt":
            self.assertLess(len(executable), 260)
        self.assertFalse(Path(info["runtime_root"]).is_relative_to(self.copy))
        code, _ = host_providers._process(command,self.copy,None,self.base/"deep.stdout",self.base/"deep.stderr",12,
                                         environment=environment)
        self.assertEqual(code,0,(self.base/"deep.stderr").read_text(encoding="utf-8"))
        self.assertTrue(Path(info["started_marker"]).is_file())
        self.assertEqual((self.copy/"deep-result.txt").read_text(encoding="utf-8"),"verified")
        verification_sandbox.cleanup(info)
        self.assertFalse(Path(info["runtime_root"]).exists())

    def test_real_stop_reaps_sandbox_child_before_late_write(self):
        started = self.copy/"started.txt"
        late = self.copy/"late.txt"
        script = "import os,time;from pathlib import Path;Path('started.txt').write_text(str(os.getpid()));time.sleep(2);Path('late.txt').write_text('late')"
        command, environment, info = verification_sandbox.prepare([sys.executable, "-c", script], self.copy, self.project, executable=self.cli)
        self.addCleanup(verification_sandbox.cleanup, info)
        stop, cancel = threading.Event(), threading.Event()
        def watch():
            deadline=time.monotonic()+8
            while not cancel.wait(.025) and time.monotonic()<deadline:
                if started.exists():
                    stop.set()
                    return
        watcher = threading.Thread(target=watch, daemon=True)
        watcher.start()
        try:
            with self.assertRaisesRegex(host_providers.ProviderError, "중지"):
                host_providers._process(command,self.copy,None,self.base/"stop.stdout",self.base/"stop.stderr",12,
                                         stop_event=stop,environment=environment)
        finally:
            cancel.set()
            watcher.join(2)
        self.assertTrue(started.exists(), (self.base/"stop.stderr").read_text(encoding="utf-8"))
        time.sleep(2.3)
        self.assertFalse(late.exists(), "Sandbox child survived its owned CLI process tree")


if __name__ == "__main__":
    unittest.main()
