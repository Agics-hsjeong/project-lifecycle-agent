"""Observed native events and protected QA repair contracts; synthetic CLIs only."""
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

from tools import host_providers as providers, starter, lifecycle, workspace, workspace_runner
from tools.storage import digest


CLI = r'''import json,sys,time,os
from pathlib import Path
args=sys.argv[1:];mode=args[0]
if '-C' in args:os.chdir(args[args.index('-C')+1])
root=Path.cwd();prompt=sys.stdin.read()
if mode=='auto':
 if '"id": "qa"' in prompt:mode='qa-fail'
 elif 'qa-feedback/' in prompt:mode='repair'
 else:mode='backend-bug'
if mode=='stream':
 print(json.dumps({'type':'item.started','item':{'id':'cmd1','type':'command_execution','command':'echo secret-token-do-not-expose'}}),flush=True)
 time.sleep(.4)
 print(json.dumps({'type':'item.completed','item':{'id':'cmd1','type':'command_execution','status':'completed','exit_code':0}}),flush=True)
 time.sleep(.4)
 (root.parent/'finished.flag').write_text('done')
 sys.exit(0)
if mode=='qa-fail':
 (root/'tests').mkdir(exist_ok=True);(root/'reports').mkdir(exist_ok=True)
 (root/'tests/test_edge.py').write_text('import unittest\nfrom src.policy import VALUE\nclass Edge(unittest.TestCase):\n def test_counterexample(self): self.assertEqual(VALUE,2)\n')
 (root/'reports/qa.md').write_text('Independent QA counterexample; supervisor executes it.')
 refs=['tests/test_edge.py','reports/qa.md']
else:
 (root/'src').mkdir(exist_ok=True);(root/'src/policy.py').write_text('VALUE = 1\n' if mode=='backend-bug' else 'VALUE = 2\n')
 if mode=='tamper':(root/'tests/test_edge.py').write_text('import unittest\nclass Fake(unittest.TestCase):\n def test_fake(self): self.assertTrue(True)\n')
 refs=['src/policy.py']
if '-o' in args:Path(args[args.index('-o')+1]).write_text(json.dumps({'summary':'Synthetic scoped fixture','evidence_refs':refs}))
print(json.dumps({'type':'thread.started','thread_id':'synthetic-session'}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':4,'output_tokens':2}}),flush=True)
'''


class NativeActivityTests(unittest.TestCase):
    def setUp(self):
        parent=starter.ROOT/'.test-output';parent.mkdir(exist_ok=True)
        self.temporary=tempfile.TemporaryDirectory(prefix='observed-runtime-',dir=parent)
        self.addCleanup(self.temporary.cleanup);self.base=Path(self.temporary.name)
        # Synthetic native fixtures exercise repair/publication without a host CLI.
        # test_verification_sandbox independently exercises the real OS boundary.
        from tools.verification_sandbox import safe_environment
        fixture_sandbox = patch('tools.verification_sandbox.prepare',
            side_effect=lambda argv, root, project, **kw: (argv, safe_environment(root), {'backend': 'synthetic-unit-fixture'}))
        fixture_sandbox.start()
        self.addCleanup(fixture_sandbox.stop)
        # Synthetic CLI seam; actual native tool boundaries have separate live tests.
        worker = patch('tools.worker_boundary.prepare', side_effect=lambda provider, cmd, *args, **kw:
            {'argv': cmd, 'env': None, 'contract': {'backend': 'synthetic-unit-fixture'}})
        worker.start(); self.addCleanup(worker.stop)
        with contextlib.redirect_stdout(io.StringIO()):
            self.project=Path(starter.init_project(self.base/'projects','observed','Synthetic observed work','product',profile='api'))
        (self.project/'src').mkdir();(self.project/'src/policy.py').write_text('VALUE = 1\n',encoding='utf-8')
        self.cli=self.base/'fake_native.py';self.cli.write_text(CLI,encoding='utf-8')

    def task(self, role, outputs):
        return {'id':'synthetic-'+role,'role':role,'execution':{'instruction':'Scoped synthetic test fixture',
            'allowed_paths':outputs,'expected_outputs':outputs,'input_paths':['src','tests'],
            'verification_commands':[[sys.executable,'-m','unittest','discover','-s','tests','-v']]}}

    def job(self, mode, task, directory, **options):
        return providers.run_job(self.project,task,{'id':task['role']},self.base/directory,
            provider='codex',executable=[sys.executable,str(self.cli),mode],timeout=10,**options)

    def test_codex_public_tool_events_omit_command_arguments_and_reasoning(self):
        events=providers.native_activity('codex',{'type':'item.started','item':{'type':'command_execution','command':'secret-token-123','text':'private reasoning'}},self.project)
        self.assertEqual(events[0]['kind'],'tool_started')
        self.assertNotIn('secret',json.dumps(events));self.assertNotIn('reasoning',json.dumps(events))
        self.assertEqual(providers.native_activity('codex',{'type':'item.completed','item':{'type':'reasoning','text':'private'}},self.project),[])
        events=providers.native_activity('codex',{'type':'item.completed','item':{'type':'file_change','changes':[{'path':'src/policy.py'},{'path':'.env'},{'path':'../secret.txt'},{'path':'reports/runner/receipt.json'}]}},self.project)
        self.assertEqual([e['path'] for e in events],['src/policy.py'])

    def test_claude_only_exposes_safe_tool_and_relative_path(self):
        events=providers.native_activity('claude',{'type':'assistant','message':{'content':[
            {'type':'thinking','thinking':'private'}, {'type':'text','text':'secret'},
            {'type':'tool_use','name':'Edit','input':{'file_path':str(self.project/'src/policy.py'),'old_string':'secret-key','new_string':'another-secret'}}]}},self.project)
        self.assertEqual(len(events),1);self.assertEqual(events[0]['path'],'src/policy.py')
        self.assertNotIn('secret',json.dumps(events));self.assertNotIn('private',json.dumps(events))
        self.assertEqual(providers.native_activity('claude',{'type':'user','message':{'content':[{'type':'tool_result','content':'credential=private','is_error':True}]}},self.project)[0]['status'],'failed')

    def test_incremental_partial_lines_emit_once_without_raw_payload(self):
        path=self.base/'stream.jsonl';seen=[];tail=providers.NativeActivityTail(path,'codex',self.project,seen.append)
        value=json.dumps({'type':'item.started','item':{'type':'command_execution','command':'do not expose'}}).encode()
        path.write_bytes(value[:15]);tail.poll();self.assertEqual(seen,[])
        with path.open('ab') as out:out.write(value[15:]+b'\n')
        tail.poll();tail.poll();self.assertEqual(len(seen),1)
        self.assertNotIn('expose',json.dumps(seen))
        with path.open('ab') as out:out.write(json.dumps({'type':'item.completed','item':{'type':'command_execution','exit_code':0}}).encode())
        tail.poll();tail.poll(final=True);self.assertEqual(len(seen),2)

    def test_stream_actions_arrive_before_native_process_finishes(self):
        folder=self.base/'stream-process';folder.mkdir();seen=[]
        def observed(event):
            seen.append((event,(self.base/'finished.flag').exists()))
        code,_=providers._process([sys.executable,str(self.cli),'stream'],folder,'fixture',folder/'out.jsonl',folder/'err.log',10,native_provider='codex',on_activity=observed)
        self.assertEqual(code,0);self.assertEqual(len(seen),2)
        self.assertTrue(all(not was_finished for _,was_finished in seen))
        self.assertTrue((self.base/'finished.flag').is_file())

    def test_failed_qa_preserves_counterexample_and_marks_real_verification_failure(self):
        events=[];result=self.job('qa-fail',self.task('qa',['tests/test_edge.py','reports/qa.md']),'CMD-QA',on_activity=events.append)
        self.assertFalse(result['success']);self.assertEqual(result['failure_kind'],'verification_failed')
        self.assertEqual(len(result['check_hashes']),1)
        source=next(iter(result['check_hashes']))
        self.assertTrue(source.startswith('reports/runner/CMD-QA/qa-feedback/tests/'))
        self.assertEqual(digest((self.project/source).read_bytes()),result['check_hashes'][source])
        self.assertFalse((self.project/'tests/test_edge.py').exists())
        self.assertEqual((self.project/'src/policy.py').read_text(),'VALUE = 1\n')
        self.assertTrue(any(e['kind']=='verification_started' for e in events))
        self.assertTrue(any(e['kind']=='verification_failed' for e in events))

    def repair_task(self):
        failed=self.job('qa-fail',self.task('qa',['tests/test_edge.py','reports/qa.md']),'CMD-QA')
        task=self.task('backend',['src/policy.py'])
        task['repair_checks']=[{'source':source,'target':source.split('/qa-feedback/')[1],'sha256':sha} for source,sha in failed['check_hashes'].items()]
        return task

    def test_repair_runs_immutable_qa_tests_without_publishing_them(self):
        task=self.repair_task();result=self.job('repair',task,'CMD-REPAIR')
        self.assertTrue(result['success'],result)
        self.assertEqual((self.project/'src/policy.py').read_text(),'VALUE = 2\n')
        self.assertFalse((self.project/'tests/test_edge.py').exists())
        self.assertEqual(result['published_paths'],['src/policy.py'])
        self.assertEqual(result['verification'][0]['exit_code'],0)
        self.assertIn('test_counterexample',(self.base/'CMD-REPAIR/verification-0.stderr').read_text())

    def test_repair_cannot_tamper_with_counterexample_to_pass(self):
        task=self.repair_task();result=self.job('tamper',task,'CMD-TAMPER')
        self.assertFalse(result['success']);self.assertIn('QA 반례',result['error'])
        self.assertEqual((self.project/'src/policy.py').read_text(),'VALUE = 1\n')

    def test_missing_native_output_does_not_masquerade_as_import_test_failure(self):
        task=self.task('qa',['tests/test_edge.py','reports/qa.md','reports/missing.md'])
        result=self.job('qa-fail',task,'CMD-MISSING')
        self.assertFalse(result['success']);self.assertEqual(result['failure_kind'],'missing_output')
        self.assertEqual(result['verification'],[])

    def test_native_runner_qa_failure_repairs_and_rechecks_without_human_gate(self):
        events=lifecycle.events(self.project)
        rev=events[-1]['revision'] if events else None
        lifecycle.apply_patch(self.project,{'expected_revision':rev,
            'requirements':[{'source':'Synthetic requirement','description':'Value must equal two','status':'proposed'}],
            'tasks':[{'role':'backend','owner':'automatic','status':'todo','next_action':'Implement value','requirement_ids':'REQ-001'},
                {'role':'qa','owner':'automatic','status':'todo','next_action':'Check independently','requirement_ids':'REQ-001'}]},'synthetic-setup')
        snapshot=workspace.snapshot(self.project)
        backend=self.task('backend',['src/policy.py'])['execution'];backend['verification_commands']=[[sys.executable,'-c',"import ast; from pathlib import Path; ast.parse(Path('src/policy.py').read_text())"]]
        qa=self.task('qa',['tests/test_edge.py','reports/qa.md'])['execution']
        workspace.configure(self.project,{'expected_revision':snapshot['revision'],'policy':{'view_mode':'observer','auto_run':True},'tasks':[
            {'id':'TASK-001','execution':backend},
            {'id':'TASK-002','depends_on':['TASK-001'],'execution':qa}]},'synthetic-setup')
        stop=threading.Event();timer=threading.Timer(60,stop.set);timer.start();self.addCleanup(timer.cancel)
        result=workspace_runner.run_forever(self.project,'codex',poll_interval=.02,stop_event=stop,
            provider_options={'executable':[sys.executable,str(self.cli),'auto'],'max_jobs':4})
        self.assertEqual(result['completed'],3,result)
        self.assertEqual(result['failed'],1,result)
        final=workspace.snapshot(self.project)
        self.assertTrue(all(t['status']=='done' for t in final['tasks']),final['tasks'])
        self.assertEqual(final['gates'],[])
        self.assertEqual(starter.read_table(self.project,'reviews'),[])
        self.assertEqual(len(final['commands']),4)
        self.assertEqual(sum(c['status']=='failed' for c in final['commands']),1)
        self.assertEqual((self.project/'src/policy.py').read_text(),'VALUE = 2\n')
        self.assertTrue((self.project/'tests/test_edge.py').is_file())
        self.assertTrue(any(a['kind']=='repair_requested' for a in final['activities']))
        self.assertTrue(any(a['kind']=='review' for a in final['activities']))
        self.assertTrue((self.project.parent/'.ieum-runtime'/self.project.name/'jobs').is_dir())

    def test_missing_review_input_fails_one_job_without_crashing_resident(self):
        history=lifecycle.events(self.project);rev=history[-1]['revision'] if history else None
        lifecycle.apply_patch(self.project,{'expected_revision':rev,'requirements':[
            {'source':'Synthetic source','description':'Owner policy','status':'proposed'}],
            'tasks':[{'role':'backend','status':'done','owner':'fixture','next_action':'Prior implementation','requirement_ids':'REQ-001','evidence_refs':'src/policy.py'},
                {'role':'qa','status':'todo','owner':'automatic','next_action':'Review','requirement_ids':'REQ-001'}]},'synthetic-setup')
        snapshot=workspace.snapshot(self.project)
        backend=self.task('backend',['src/policy.py','tests/missing.py'])['execution']
        qa=self.task('qa',['tests/test_edge.py','reports/qa.md'])['execution']
        workspace.configure(self.project,{'expected_revision':snapshot['revision'],'policy':{'auto_run':True},'tasks':[
            {'id':'TASK-001','execution':backend},{'id':'TASK-002','depends_on':['TASK-001'],'execution':qa}]},'synthetic-setup')
        calls=[]
        def must_not_start(*args,**kwargs):
            calls.append('started');raise AssertionError('Native process must not start with missing review input')
        stop=threading.Event();timer=threading.Timer(15,stop.set);timer.start();self.addCleanup(timer.cancel)
        result=workspace_runner.run_forever(self.project,'codex',poll_interval=.02,stop_event=stop,
            provider_options={'run_job':must_not_start,'max_jobs':1})
        self.assertEqual(result['failed'],1,result);self.assertEqual(calls,[])
        final=workspace.snapshot(self.project)
        self.assertEqual(final['commands'][0]['status'],'failed')
        self.assertTrue(any(a['kind']=='failed' and a['status']=='attention' for a in final['activities']))
        self.assertFalse((self.project/'state/workspace-runner.lock').exists())

    def test_changed_protected_check_hash_blocks_repair(self):
        task=self.repair_task();(self.project/task['repair_checks'][0]['source']).write_text('tampered',encoding='utf-8')
        result=self.job('repair',task,'CMD-BAD-HASH')
        self.assertFalse(result['success']);self.assertIn('해시',result['error'])
        self.assertFalse(result['process_started'])

if __name__=='__main__':unittest.main()
