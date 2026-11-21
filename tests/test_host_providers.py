"""Native adapter tests use a local synthetic CLI, never real model calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import host_providers, lifecycle, starter, workspace, workspace_runner


FAKE_CLI = r'''import json, os, sys, time
from pathlib import Path
args=sys.argv[1:]
mode=args[args.index('--fake-mode')+1]
if '-C' in args:os.chdir(args[args.index('-C')+1])
root=Path.cwd()
(root.parent/'invocation.json').write_text(json.dumps({'args':args,'cwd':str(root),'has_git':(root/'.git').exists(),'has_secret':(root/'.env').exists(),'has_state':(root/'state').exists(),'registered_core_hint':os.environ.get('CODEX_WINDOWS_REGISTERED_CORE'),'unrelated_env':os.environ.get('IEUM_SYNTHETIC_ENV_MARKER')}),encoding='utf-8')
prompt=sys.stdin.read()
if mode=='sleep':time.sleep(20)
if mode not in {'missing-output','tamper-decisions','tamper-runner'}:
 target=root/'src/policy.py';target.parent.mkdir(exist_ok=True);target.write_text("POLICY = 'owner'\n",encoding='utf-8')
if mode=='protected':(root/'project.json').write_text('{"compromised":true}',encoding='utf-8')
if mode=='undeclared':(root/'src/other.py').write_text('unexpected = True\n',encoding='utf-8')
if mode=='delete':(root/'docs/01-discovery.md').unlink()
if mode=='nested-state':
 (root/'src/state').mkdir(exist_ok=True)
 (root/'src/state/store.py').write_text('OWNER_STORE = True'+chr(10),encoding='utf-8')
if mode=='test-mutates-state':
 (root/'tests').mkdir(exist_ok=True)
 generated=chr(10).join(['import unittest', 'from pathlib import Path', 'class Generated(unittest.TestCase):', ' def test_owner(self):', "  Path('project.json').write_text('{}',encoding='utf-8')", '  self.assertTrue(True)'])+chr(10)
 compile(generated,'test_generated.py','exec')
 (root/'tests/test_generated.py').write_text(generated,encoding='utf-8')
if mode in {'tamper-decisions','tamper-runner'}:
 prefix='workspace-decisions' if mode=='tamper-decisions' else 'runner'
 (root/'reports'/prefix/'preserved.json').write_text('{"tampered":true}',encoding='utf-8')
 (root/'reports/qa.md').write_text('reviewed synthetic output'+chr(10),encoding='utf-8')
receipt={'summary':'Synthetic CLI says all tests passed; supervisor must verify.', 'evidence_refs':['../outside.py'] if mode=='traversal' else ['reports/qa.md'] if mode in {'tamper-decisions','tamper-runner'} else ['src/policy.py']}
if mode=='bad-json':value='{ invalid json'
else:value=json.dumps(receipt)
if '-o' in args:
 if mode!='missing-receipt':Path(args[args.index('-o')+1]).write_text(value,encoding='utf-8')
 print(json.dumps({'type':'thread.started','thread_id':'synthetic-cli-session'}))
else:
 print(json.dumps({'type':'result','subtype':'success','is_error':False,'session_id':'synthetic-cli-session','result':value,'structured_output':receipt if mode!='bad-json' else None}))
'''


class HostProviderTests(unittest.TestCase):
    def setUp(self):
        base = starter.ROOT / ".test-output"
        base.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix="provider-independent-", dir=base)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        # These unit fixtures test publication/receipts with a synthetic CLI.
        # Real OS denial is exercised separately by test_verification_sandbox.
        from tools.verification_sandbox import safe_environment
        fixture_sandbox = patch('tools.verification_sandbox.prepare',
            side_effect=lambda argv, root, project, **kw: (argv, safe_environment(root), {'backend': 'synthetic-unit-fixture'}))
        fixture_sandbox.start()
        self.addCleanup(fixture_sandbox.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(self.base / "projects", "project", "Synthetic provider", "product", profile="api"))
        (self.root / "src").mkdir()
        (self.root / "src/policy.py").write_text("POLICY = 'before'\n", encoding="utf-8")
        self.cli = self.base / "synthetic_cli.py"
        self.cli.write_text(FAKE_CLI, encoding="utf-8")
        self.execution = {"instruction": "Implement only the owner policy.", "allowed_paths": ["src/policy.py"],
            "expected_outputs": ["src/policy.py"], "verification_commands": [[sys.executable, "-c", "from pathlib import Path; assert 'owner' in Path('src/policy.py').read_text()"]]}
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "backend", "owner": "Synthetic worker", "status": "todo", "next_action": "Owner policy"}]})
        self.configure()
        self.job = self.base / "private-job"

    def rev(self):
        records = lifecycle.events(self.root)
        return records[-1]["revision"] if records else None

    def configure(self):
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": "TASK-001", "stage": "implementation", "execution": self.execution}]}, "synthetic-fixture")
        self.task = next(t for t in workspace.snapshot(self.root)["tasks"] if t["id"] == "TASK-001")

    def run_fake(self, mode="success", provider="codex", **kwargs):
        return host_providers.run_job(self.root, self.task, {"id": "backend", "mission": "Synthetic contract"}, self.job,
            provider=provider, executable=[sys.executable, str(self.cli), "--fake-mode", mode], **kwargs)

    def test_codex_worker_is_scoped_and_independently_verified(self):
        (self.root / ".env").write_text("SYNTHETIC_SECRET=not-real\n", encoding="utf-8")
        (self.root / ".git").write_text("gitdir: ../outside-worktree-metadata\n", encoding="utf-8")
        result = self.run_fake()
        self.assertTrue(result["success"], result)
        self.assertTrue(result["process_started"])
        self.assertEqual(result["verification"][0]["exit_code"], 0)
        self.assertEqual((self.root / "src/policy.py").read_text(encoding="utf-8").strip(), "POLICY = 'owner'")
        record = json.loads((self.job / "invocation.json").read_text(encoding="utf-8"))
        self.assertEqual(Path(record["cwd"]), self.job / "workspace")
        self.assertFalse(record["has_git"])
        self.assertFalse(record["has_secret"])
        self.assertFalse(record["has_state"])
        self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", record["args"])
        self.assertNotIn("--dangerously-bypass-hook-trust", record["args"])
        self.assertNotIn("--trust", record["args"])
        self.assertNotIn("--ignore-rules", record["args"])
        self.assertNotIn("danger-full-access", record["args"])
        self.assertTrue("--approve-for-me" in record["args"] or
                        ("--sandbox" in record["args"] and record["args"][record["args"].index("--sandbox") + 1] == "workspace-write"))
        self.assertEqual(result["published_paths"], ["src/policy.py"],
                         "Published outputs must not include supervisor state/history paths")
        self.assertEqual(starter.read_table(self.root, "tasks")[0]["status"], "todo")
        self.assertEqual(starter.read_table(self.root, "reviews"), [])

    def test_claude_file_tools_only_and_missing_usage_remains_unknown(self):
        result = self.run_fake(provider="claude")
        self.assertTrue(result["success"], result)
        record = json.loads((self.job / "invocation.json").read_text(encoding="utf-8"))
        self.assertNotIn("--dangerously-skip-permissions", record["args"])
        self.assertEqual(record["args"][record["args"].index("--permission-mode") + 1], "acceptEdits")
        self.assertNotIn("Bash", record["args"][record["args"].index("--tools") + 1])
        self.assertIsNone((result.get("usage") or {}).get("tokens"))
        self.assertEqual(result["provider_session_id"], "synthetic-cli-session")

    def test_candidate_protected_edit_never_reaches_actual_project(self):
        before = (self.root / "project.json").read_bytes()
        code = (self.root / "src/policy.py").read_bytes()
        result = self.run_fake("protected")
        self.assertFalse(result["success"])
        self.assertEqual((self.root / "project.json").read_bytes(), before)
        self.assertEqual((self.root / "src/policy.py").read_bytes(), code)

    def test_undeclared_output_and_deleted_input_are_rejected(self):
        for mode in ("undeclared", "delete"):
            with self.subTest(mode=mode):
                self.job = self.base / ("job-" + mode)
                before = (self.root / "src/policy.py").read_bytes()
                result = self.run_fake(mode)
                self.assertFalse(result["success"])
                self.assertEqual((self.root / "src/policy.py").read_bytes(), before)
                self.assertTrue((self.root / "docs/01-discovery.md").is_file())
                self.assertFalse((self.root / "src/other.py").exists())

    def test_provider_claimed_pass_cannot_override_failed_registered_verification(self):
        self.execution["verification_commands"] = [[sys.executable, "-c", "print('independent verification sentinel'); raise SystemExit(7)"]]
        self.configure()
        result = self.run_fake()
        self.assertFalse(result["success"])
        self.assertEqual(result["verification"][0]["exit_code"], 7)
        self.assertFalse(result["evidence_refs"])
        self.assertEqual(starter.read_table(self.root, "reviews"), [])

    def test_missing_or_malformed_receipt_and_traversal_evidence_are_rejected(self):
        for mode in ("missing-receipt", "bad-json", "traversal"):
            with self.subTest(mode=mode):
                self.job = self.base / ("job-" + mode)
                result = self.run_fake(mode)
                self.assertFalse(result["success"])
                self.assertFalse(result["evidence_refs"])

    def test_output_preimage_conflict_preserves_user_edit(self):
        def edit_after_spawn(pid):
            (self.root / "src/policy.py").write_text("POLICY = 'user edit'\n", encoding="utf-8")
        result = self.run_fake(on_started=edit_after_spawn)
        self.assertFalse(result["success"])
        self.assertEqual((self.root / "src/policy.py").read_text(encoding="utf-8").strip(), "POLICY = 'user edit'")

    def test_execution_contract_change_after_spawn_prevents_publication(self):
        def change_contract(pid):
            self.execution["instruction"] = "Changed scope after the worker started."
            self.configure()
        result = self.run_fake(on_started=change_contract)
        self.assertFalse(result["success"])
        self.assertEqual((self.root / "src/policy.py").read_text(encoding="utf-8").strip(), "POLICY = 'before'")

    def test_timeout_and_stop_reap_owned_native_process(self):
        for mode in ("timeout", "stop"):
            with self.subTest(mode=mode):
                self.job = self.base / ("job-" + mode)
                started = []
                stop = threading.Event()
                timer = threading.Timer(0.2, stop.set) if mode == "stop" else None
                if timer:
                    timer.start()
                began = time.monotonic()
                try:
                    result = self.run_fake("sleep", timeout=0.15 if mode == "timeout" else 5,
                        on_started=started.append, stop_event=stop)
                finally:
                    if timer:
                        timer.cancel()
                self.assertFalse(result["success"])
                self.assertLess(time.monotonic() - began, 4)
                self.assertEqual(len(started), 1)
                from tools.storage import process_alive
                self.assertFalse(process_alive(started[0]))

    def test_model_generated_verification_cannot_mutate_actual_control_records(self):
        self.execution = {"instruction": "Create owner code and tests within scope.",
            "allowed_paths": ["src/policy.py", "tests/test_generated.py"],
            "expected_outputs": ["src/policy.py", "tests/test_generated.py"],
            "verification_commands": [[sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]]}
        self.configure()
        before = (self.root / "project.json").read_bytes()
        result = self.run_fake("test-mutates-state")
        self.assertEqual(result["verification"][0]["exit_code"], 0, result)
        self.assertEqual((self.root / "project.json").read_bytes(), before,
                         "Generated tests must never receive authority to edit actual project controls")
        self.assertFalse(result["success"], result)

    def test_nested_state_source_directory_is_not_confused_with_engine_state(self):
        self.execution = {"instruction": "Create a nested application state source module.",
            "allowed_paths": ["src/policy.py", "src/state/store.py"],
            "expected_outputs": ["src/policy.py", "src/state/store.py"],
            "verification_commands": [[sys.executable, "-c", "from pathlib import Path; assert 'OWNER_STORE' in Path('src/state/store.py').read_text()"]]}
        self.configure()
        result = self.run_fake("nested-state")
        self.assertTrue(result["success"], result)
        self.assertTrue((self.root / "src/state/store.py").is_file())

    def test_registered_python_alias_survives_real_adapter_runner_receipt_validation(self):
        self.execution["verification_commands"] = [["python", "-c", "from pathlib import Path; assert 'owner' in Path('src/policy.py').read_text()"]]
        self.configure()
        (self.root / "docs/contract.md").write_text("Synthetic owner contract\n", encoding="utf-8")
        lifecycle.apply_patch(self.root, {"expected_revision": self.rev(), "tasks": [
            {"role": "architect", "owner": "Synthetic author", "status": "done", "next_action": "Contract", "evidence_refs": "docs/contract.md"}]})
        workspace.configure(self.root, {"expected_revision": self.rev(), "tasks": [
            {"id": "TASK-001", "depends_on": ["TASK-002"]}], "gates": [
            {"id": "GATE-NATIVE-CHAIN", "task_id": "TASK-002", "paths": ["docs/contract.md"]}]})
        snap = workspace.snapshot(self.root)
        gate = next(g for g in snap["gates"] if g["id"] == "GATE-NATIVE-CHAIN")
        workspace.decide(self.root, {"action_id": "synthetic-native-chain-approve", "expected_revision": snap["revision"],
            "gate_id": gate["id"], "decision": "approve", "feedback": "Synthetic only", "actor": "Synthetic human fixture",
            "target_hashes": gate["target_hashes"], "context_hash": gate["context_hash"]})
        result = workspace_runner.run_forever(self.root, "codex", poll_interval=0.1,
            provider_options={"executable": [sys.executable, str(self.cli), "--fake-mode", "success"],
                              "job_root": str(self.base / "runner-jobs"), "max_jobs": 1})
        self.assertEqual(result["completed"], 1, result)
        self.assertEqual(result["failed"], 0)
        task = next(t for t in workspace.snapshot(self.root)["tasks"] if t["id"] == "TASK-001")
        self.assertEqual(task["status"], "done")

    def test_desktop_core_transport_hint_is_scoped_to_windows_codex_child(self):
        sentinel = "synthetic-msix-runtime-hint"
        unrelated = "synthetic-environment-marker"
        self.execution["verification_commands"] = [[sys.executable, "-c",
            "import json,os; print(json.dumps({'core_hint':os.environ.get('CODEX_WINDOWS_REGISTERED_CORE'),'unrelated':os.environ.get('IEUM_SYNTHETIC_ENV_MARKER')}))"]]
        self.configure()
        with patch.dict(os.environ, {"CODEX_WINDOWS_REGISTERED_CORE": sentinel, "IEUM_SYNTHETIC_ENV_MARKER": unrelated}):
            for provider in ("codex", "claude"):
                with self.subTest(provider=provider):
                    self.job = self.base / ("environment-" + provider)
                    result = self.run_fake(provider=provider)
                    self.assertTrue(result["success"], result)
                    invocation = json.loads((self.job / "invocation.json").read_text(encoding="utf-8"))
                    expected_native = None if os.name == "nt" and provider == "codex" else sentinel
                    self.assertEqual(invocation["registered_core_hint"], expected_native)
                    self.assertEqual(invocation["unrelated_env"], unrelated)
                    verification = json.loads((self.job / "verification-0.stdout").read_text(encoding="utf-8"))
                    self.assertEqual(verification, {"core_hint": None, "unrelated": None})
                    self.assertEqual(os.environ["CODEX_WINDOWS_REGISTERED_CORE"], sentinel)
                    self.assertEqual(os.environ["IEUM_SYNTHETIC_ENV_MARKER"], unrelated)

    def test_broad_reports_scope_cannot_modify_supervisor_approval_or_verification_evidence(self):
        self.execution = {"instruction": "Write only a new review report; preserve prior evidence.",
            "allowed_paths": ["reports"], "expected_outputs": ["reports/qa.md"],
            "verification_commands": [[sys.executable, "-c", "from pathlib import Path; assert 'reviewed' in Path('reports/qa.md').read_text()"]]}
        self.configure()
        for mode, prefix in (("tamper-decisions", "workspace-decisions"), ("tamper-runner", "runner")):
            with self.subTest(prefix=prefix):
                evidence = self.root / "reports" / prefix / "preserved.json"
                evidence.parent.mkdir(parents=True, exist_ok=True)
                original = b'{"synthetic_preserved_evidence":true}\n'
                evidence.write_bytes(original)
                self.job = self.base / ("job-" + mode)
                result = self.run_fake(mode)
                self.assertFalse(result["success"], result)
                self.assertEqual(evidence.read_bytes(), original)
                self.assertFalse((self.root / "reports/qa.md").exists())
