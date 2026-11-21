"""Candidate integrity regressions; synthetic workers, optional real OS verifier."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tools import host_providers, lifecycle, starter, verification_sandbox, workspace
from tools.storage import digest

PRODUCER = r"""import json,os,sys,time
from pathlib import Path
args=sys.argv[1:];mode=args[0];root=Path(args[args.index('-C')+1]);os.chdir(root);sys.stdin.read()
(root.parent/'invoked.flag').write_text('producer invoked')
Path('src/policy.py').write_text('VALUE = 1\n' if mode!='broken' else 'def broken(\n')
if mode=='secret':
 Path('config').mkdir(exist_ok=True);Path('config/key.pem').write_text('SYNTHETIC PRIVATE MATERIAL')
if mode in {'fail-usage','timeout-usage'}:
 print(json.dumps({'type':'thread.started','thread_id':'synthetic-usage-session'}),flush=True)
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':11,'output_tokens':6}}),flush=True)
 if mode=='timeout-usage':time.sleep(20)
 else:sys.exit(7)
Path(args[args.index('-o')+1]).write_text(json.dumps({'summary':'Synthetic scoped output','evidence_refs':['src/policy.py']}))
print(json.dumps({'type':'thread.started','thread_id':'synthetic-integrity'}))
"""


class IntegrityFixture:
    real_cli = None

    def setUp(self):
        # Synthetic producer only; actual provider boundaries have separate native tests.
        generation = patch('tools.worker_boundary.prepare', side_effect=lambda provider, cmd, root, project, job, **kw:
            {'argv': cmd, 'env': dict(os.environ), 'contract': {'backend': 'synthetic-native-fixture'}})
        generation.start()
        self.addCleanup(generation.stop)
        parent = starter.ROOT / '.test-output'
        parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='sealed-integrity-',dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.project = Path(starter.init_project(self.base/'projects','synthetic','Synthetic sealed verification','product',profile='api'))
        (self.project/'src').mkdir()
        (self.project/'src/policy.py').write_text('VALUE = 0\n',encoding='utf-8')
        (self.project/'checks').mkdir()
        self.check = self.project/'checks/check.py'
        self.check.write_text("from pathlib import Path\nassert Path('src/policy.py').read_text() == 'VALUE = 1\\n'\n",encoding='utf-8')
        self.producer = self.base/'producer.py'
        self.producer.write_text(PRODUCER,encoding='utf-8')
        self.execution = {'instruction':'Implement the bounded source and preserve inputs.',
            'allowed_paths':['src/policy.py'], 'expected_outputs':['src/policy.py'],
            'input_paths':['src/policy.py','checks/check.py'],
            'verification_commands':[[sys.executable,'checks/check.py']]}
        lifecycle.apply_patch(self.project,{'expected_revision':None,'tasks':[{'role':'backend','owner':'Synthetic worker','status':'todo','next_action':'Policy'}]})
        self.configure()
        if self.real_cli is None:
            mocked = patch.object(verification_sandbox,'prepare',side_effect=lambda argv,root,project,**kw:
                (argv,verification_sandbox.safe_environment(root),{'backend':'synthetic-unit-fixture'}))
            mocked.start();self.addCleanup(mocked.stop)
        self.job = self.base/'job'

    def configure(self):
        workspace.configure(self.project,{'expected_revision':lifecycle.events(self.project)[-1]['revision'],
            'tasks':[{'id':'TASK-001','stage':'implementation','execution':self.execution}]})
        self.task = workspace.snapshot(self.project)['tasks'][0]

    def run_worker(self,mode='good',**options):
        return host_providers.run_job(self.project,self.task,{'id':'backend'},self.job,
            provider='codex',executable=[sys.executable,str(self.producer),mode],
            verification_executable=self.real_cli,timeout=options.pop('timeout',15),**options)

    def assert_original(self):
        self.assertEqual((self.project/'src/policy.py').read_text(encoding='utf-8'),'VALUE = 0\n')


class VerificationIntegrityTests(IntegrityFixture,unittest.TestCase):
    def test_check_then_mutate_is_never_published_even_when_exit_zero(self):
        self.check.write_text("from pathlib import Path\np=Path('src/policy.py')\nassert p.read_text() == 'VALUE = 1\\n'\np.write_text('def broken(\\n')\n",encoding='utf-8')
        result=self.run_worker()
        self.assertFalse(result['success'],result)
        self.assertEqual(result['verification'][0]['exit_code'],0)
        self.assertEqual(result['failure_kind'],'verification_integrity')
        self.assert_original()

    def test_generation_boundary_failure_never_starts_producer(self):
        from tools.worker_boundary import BoundaryUnavailable
        with patch('tools.worker_boundary.prepare', side_effect=BoundaryUnavailable('Missing strict boundary')):
            result = self.run_worker()
        self.assertFalse(result['success'], result)
        self.assertEqual(result['failure_kind'], 'worker_boundary_unavailable')
        self.assertFalse((self.job/'invoked.flag').exists())
        self.assert_original()

    def test_model_leftover_scratch_is_removed_before_verification(self):
        def boundary(provider, cmd, root, project, job, **kw):
            for name in ('.ieum-worker-tmp', '.ieum-verification/tmp'):
                directory = root/name
                directory.mkdir(parents=True, exist_ok=True)
                (directory/'poison.txt').write_text('unverified hidden dependency')
            return {'argv': cmd, 'env': dict(os.environ), 'contract': {'backend': 'synthetic-native-fixture'}}
        self.check.write_text("import os\nfrom pathlib import Path\nassert not Path('.ieum-worker-tmp').exists()\nassert not Path(os.environ['IEUM_VERIFICATION_SCRATCH'],'poison.txt').exists()\nassert Path('src/policy.py').read_text() == 'VALUE = 1\\n'\n", encoding='utf-8')
        with patch('tools.worker_boundary.prepare', side_effect=boundary):
            result = self.run_worker()
        self.assertTrue(result['success'], result)
        self.assertFalse(any('poison' in path for path in result['verification_contract']['verified_outputs']))

    def test_unsupported_runtime_is_environment_failure_without_qa_counterexamples(self):
        self.check.write_text("raise PermissionError('IEUM_ENVIRONMENT_UNAVAILABLE: isolated runtime is unsupported')\n",encoding='utf-8')
        result=self.run_worker()
        self.assertFalse(result['success'],result)
        self.assertEqual(result['failure_kind'],'environment_unavailable')
        self.assertTrue(result['verification'][0]['environment_unavailable'])
        self.assertEqual(result['verification_status'],'failed')
        self.assertNotIn('check_hashes',result)
        self.assert_original()

    def test_renamed_source_invalidates_verification(self):
        self.check.write_text("from pathlib import Path\nPath('src/policy.py').rename('src/renamed.py')\n",encoding='utf-8')
        result=self.run_worker()
        self.assertFalse(result['success'],result)
        self.assertEqual(result['failure_kind'],'verification_integrity')
        self.assert_original()
        self.assertFalse((self.project/'src/renamed.py').exists())

    def test_checker_cannot_change_its_own_input_and_keep_success(self):
        self.check.write_text("from pathlib import Path\nPath(__file__).write_text('raise SystemExit(0)\\n')\n",encoding='utf-8')
        original=self.check.read_bytes();result=self.run_worker()
        self.assertFalse(result['success'],result)
        self.assertEqual(self.check.read_bytes(),original)
        self.assert_original()

    def test_scratch_output_can_succeed_and_manifest_binds_published_bytes(self):
        self.check.write_text("import os\nfrom pathlib import Path\nassert Path('src/policy.py').read_text() == 'VALUE = 1\\n'\nPath(os.environ['IEUM_VERIFICATION_SCRATCH'],'coverage.json').write_text('{}')\n",encoding='utf-8')
        result=self.run_worker()
        self.assertTrue(result['success'],result)
        receipt=result['verification_contract']
        self.assertEqual(receipt['verified_output_sha256'],receipt['published_output_sha256'])
        self.assertEqual(receipt['verified_outputs']['src/policy.py']['sha256'],digest((self.project/'src/policy.py').read_bytes()))
        self.assertFalse((self.project/'coverage.json').exists())

    def test_frozen_bytes_survive_path_swap_at_publication(self):
        original=host_providers._publish
        def swap(project,task,before,frozen,execution):
            (self.job/'workspace/src/policy.py').write_text('def broken(\n')
            return original(project,task,before,frozen,execution)
        with patch.object(host_providers,'_publish',side_effect=swap):
            result=self.run_worker()
        self.assertTrue(result['success'],result)
        self.assertEqual((self.project/'src/policy.py').read_text(),'VALUE = 1\n')

    def test_unchanged_expected_output_still_has_an_exact_byte_receipt(self):
        (self.project/'src/policy.py').write_text('VALUE = 1\n',encoding='utf-8')
        result=self.run_worker()
        self.assertTrue(result['success'],result)
        self.assertEqual(result['published_paths'],[])
        self.assertEqual(result['verification_contract']['verified_outputs']['src/policy.py']['sha256'],
                         digest((self.project/'src/policy.py').read_bytes()))

    def test_changed_task_context_during_work_does_not_publish(self):
        def change(pid):
            workspace.configure(self.project,{'expected_revision':lifecycle.events(self.project)[-1]['revision'],
                'tasks':[{'id':'TASK-001','title':'Changed scope while native worker was starting'}]})
        result=self.run_worker(on_started=change)
        self.assertFalse(result['success'],result)
        self.assert_original()

    def test_review_without_input_contract_fails_before_producer(self):
        self.task['role']='qa'
        self.task['execution'].pop('input_paths',None)
        self.task['inputs']=[]
        result=self.run_worker()
        self.assertFalse(result['success'],result)
        self.assertFalse(result['process_started'])
        self.assertIn('입력',result['summary'])
        self.assert_original()

    def test_noop_registered_check_cannot_hide_python_syntax_error(self):
        self.execution['verification_commands']=[[sys.executable,'-c','pass']]
        self.configure();result=self.run_worker('broken')
        self.assertFalse(result['success'],result)
        self.assertEqual(result['failure_kind'],'output_syntax')
        self.assertEqual(result['verification_status'],'failed')
        self.assert_original()

    def test_oversized_input_is_rejected_without_opening_it(self):
        target=self.project/'src/large.bin';target.write_bytes(b'x'*128)
        opening=Path.open
        def guard(path,*args,**kwargs):
            if path == target:
                raise AssertionError('Oversized bytes were read before enforcing the limit')
            return opening(path,*args,**kwargs)
        with patch.object(host_providers,'MAX_BYTES',16),patch.object(Path,'open',guard):
            with self.assertRaisesRegex(ValueError,'크기'):
                host_providers._files(self.project,['src/large.bin'])

    def test_bounded_native_tail_preserves_a_complete_boundary_record(self):
        record=json.dumps({'type':'turn.completed','usage':{'input_tokens':11,'output_tokens':6}}).encode()
        record=record+b' '*(127-len(record))+b'\n'
        path=self.base/'native-tail.jsonl';path.write_bytes(b'ignored\n'+record)
        with patch.object(host_providers,'MAX_NATIVE_BYTES',128):
            usage,_=host_providers._observed_usage('codex',path,complete=True)
        self.assertEqual(usage['tokens'],17)
        self.assertEqual(usage['coverage'],'partial')

    def test_missing_approved_environment_fails_before_model_starts(self):
        self.execution['runtime_ref']='0'*64
        self.configure();result=self.run_worker()
        self.assertFalse(result['success'],result)
        self.assertFalse(result['process_started'])
        self.assertEqual(result['failure_kind'],'environment_unavailable')
        self.assertFalse((self.job/'invoked.flag').exists())
        self.assert_original()

    def test_empty_code_verification_is_rejected_before_producer_starts(self):
        self.execution['verification_commands']=[];self.configure()
        result=self.run_worker('broken')
        self.assertFalse(result['success'],result)
        self.assertFalse(result['process_started'])
        self.assertFalse((self.job/'invoked.flag').exists())
        self.assert_original()

    def test_artifact_only_is_explicit_and_cannot_bypass_code_role(self):
        for role,path,allowed in [('technical-writer','docs/guide.md',True),('backend','docs/guide.md',False),('technical-writer','src/app.py',False),('technical-writer','src/app.ipynb',False),('technical-writer','package.json',False)]:
            task={'role':role,'execution':{'instruction':'Bounded artifact', 'allowed_paths':[path],'expected_outputs':[path],
                  'verification_commands':[],'verification_mode':'artifact_only','verification_reason':'Editorial draft only'}}
            if allowed:self.assertEqual(host_providers._execution(task,self.project)['verification_mode'],'artifact_only')
            else:
                with self.assertRaises(ValueError):host_providers._execution(task,self.project)

    def test_input_manifest_omits_unselected_and_sensitive_files(self):
        (self.project/'docs/private-note.md').write_text('unselected private project text')
        (self.project/'config').mkdir()
        (self.project/'config/credentials.json').write_text('{"password":"SYNTHETIC_CREDENTIAL"}')
        (self.project/'config/key.pem').write_text('SYNTHETIC_KEY')
        files,manifest=host_providers._input_files(self.project,self.task,self.execution)
        self.assertEqual(set(files),{'src/policy.py','checks/check.py'})
        self.assertFalse(manifest['inferred'])
        with self.assertRaises(ValueError):
            host_providers._input_files(self.project,self.task,{**self.execution,'input_paths':['config/key.pem']})

    def test_generated_secret_file_is_not_published(self):
        result=self.run_worker('secret')
        self.assertFalse(result['success'],result)
        self.assert_original()
        self.assertFalse((self.project/'config/key.pem').exists())

    def test_failed_cli_preserves_reported_usage_without_claiming_full_coverage(self):
        result=self.run_worker('fail-usage')
        self.assertFalse(result['success'],result)
        self.assertEqual(result['usage']['tokens'],17)
        self.assertEqual(result['usage_coverage'],'partial')
        self.assert_original()

    def test_timeout_preserves_earlier_usage(self):
        result=self.run_worker('timeout-usage',timeout=.4)
        self.assertFalse(result['success'],result)
        self.assertEqual(result['usage']['tokens'],17)
        self.assertEqual(result['usage_coverage'],'partial')
        self.assert_original()

    def test_checks_share_one_cumulative_deadline(self):
        self.execution['verification_commands']=[[sys.executable,'-c','import time;time.sleep(.3)']]*2
        self.configure();began=time.monotonic();result=self.run_worker(timeout=.5)
        self.assertFalse(result['success'],result)
        self.assertEqual(result['failure_kind'],'timeout')
        self.assertLess(time.monotonic()-began,4)
        self.assert_original()


@unittest.skipUnless(os.environ.get('IEUM_SANDBOX_CLI'),'Explicit CLI required for actual OS verification; no model calls')
class RealReadonlyVerificationTests(IntegrityFixture,unittest.TestCase):
    @property
    def real_cli(self):return os.environ['IEUM_SANDBOX_CLI']

    def test_actual_readonly_source_denies_check_then_mutation(self):
        self.check.write_text("from pathlib import Path\np=Path('src/policy.py')\nassert p.read_text() == 'VALUE = 1\\n'\np.write_text('def broken(\\n')\n",encoding='utf-8')
        result=self.run_worker()
        self.assertFalse(result['success'],result)
        self.assertEqual(result['failure_kind'],'verification_failed',result)
        self.assertNotEqual(result['verification'][0]['exit_code'],0)
        self.assertTrue(result['verification_sandbox']['source_readonly'])
        self.assert_original()

    def test_actual_rename_checker_write_and_new_source_file_are_denied(self):
        self.check.write_text("from pathlib import Path\nimport unittest\np=Path('src/policy.py')\nt=unittest.TestCase()\nwith t.assertRaises(OSError): p.rename('src/renamed.py')\nwith t.assertRaises(OSError): Path(__file__).write_text('pass')\nwith t.assertRaises(OSError): Path('src/new.py').write_text('pass')\nassert p.read_text() == 'VALUE = 1\\n'\n",encoding='utf-8')
        before=self.check.read_bytes();result=self.run_worker()
        self.assertTrue(result['success'],result)
        self.assertEqual(self.check.read_bytes(),before)
        self.assertFalse((self.project/'src/renamed.py').exists())
        self.assertFalse((self.project/'src/new.py').exists())

    def test_actual_readonly_source_with_writable_scratch_succeeds(self):
        self.check.write_text("import os\nfrom pathlib import Path\nassert Path('src/policy.py').read_text() == 'VALUE = 1\\n'\nPath(os.environ['IEUM_VERIFICATION_SCRATCH'],'coverage.json').write_text('{}')\n",encoding='utf-8')
        result=self.run_worker()
        self.assertTrue(result['success'],result)
        self.assertEqual(result['verification_contract']['verified_output_sha256'],result['verification_contract']['published_output_sha256'])
        self.assertTrue((self.job/'workspace/.ieum-verification/tmp/coverage.json').exists())
