"""Native hook telemetry must identify execution without retaining user content."""
import contextlib
import io
import json
import os
import shutil
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import native_hooks


class NativeHookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'project.json').write_text(json.dumps({'schema_version':1,'project_id':'hook-test','business_type':'product','methodology':'agile','current_stage':'discovery'}),encoding='utf-8')
        self.base = {'session_id':'session-real-123','cwd':str(self.root)}

    def event(self, name, **fields):
        return native_hooks.normalize({**self.base,'hook_event_name':name,**fields},'claude',now='2026-11-22T00:00:00+00:00')

    def test_nearest_project_and_vcs_boundary(self):
        child=self.root/'src'/'nested';child.mkdir(parents=True)
        self.assertEqual(native_hooks.find_project(str(child)),self.root.resolve())
        (self.root/'src'/'.git').mkdir()
        self.assertIsNone(native_hooks.find_project(str(child)))

    def test_template_and_plugin_roots_are_never_customer_projects(self):
        (self.root/'project.json').write_text(json.dumps({'schema_version':1,'project_id':'TEMPLATE'}),encoding='utf-8')
        self.assertIsNone(native_hooks.find_project(str(self.root)))
        (self.root/'project.json').write_text(json.dumps({'schema_version':1,'project_id':'fake-customer'}),encoding='utf-8')
        (self.root/'.codex-plugin').mkdir();(self.root/'.codex-plugin/plugin.json').write_text('{}')
        self.assertIsNone(native_hooks.find_project(str(self.root)))

    def test_ordinary_directory_is_noop(self):
        (self.root/'project.json').unlink()
        records=[]
        self.assertFalse(native_hooks.process({**self.base,'hook_event_name':'SessionStart'},'claude',ingest=lambda *a:records.append(a)))
        self.assertEqual(records,[])

    def test_missing_schema_and_malformed_project_noop(self):
        for data in ('[]','{','{"schema_version":2,"project_id":"x"}'):
            (self.root/'project.json').write_text(data)
            self.assertIsNone(native_hooks.find_project(str(self.root)))

    def test_session_preparing_then_idle(self):
        for name in ('SessionStart','UserPromptSubmit'):
            self.assertEqual(self.event(name)['kind'],'session_started')
        self.assertEqual(self.event('Stop')['kind'],'session_idle')
        self.assertEqual(self.event('SessionEnd')['kind'],'session_ended')
        self.assertEqual(self.event('Interrupt')['kind'],'session_idle')
        self.assertEqual(self.event('StopFailure')['kind'],'session_idle')
        self.assertNotIn('status',self.event('Stop'))

    def test_native_subagent_id_preserved_and_completion_is_not_verified_done(self):
        event=self.event('SubagentStart',agent_id='ac687bc969b23419d',agent_type='project-lifecycle-agent:ieum-reviewer')
        self.assertEqual(event['native_agent_id'],'ac687bc969b23419d')
        self.assertEqual(event['role'],'reviewer')
        self.assertEqual(event['parent_id'],self.base['session_id'])
        stop=self.event('SubagentStop',agent_id='ac687bc969b23419d',agent_type='project-lifecycle-agent:ieum-reviewer',last_assistant_message='secret body')
        self.assertEqual(stop['kind'],'agent_completed')
        self.assertNotIn('status',stop)
        self.assertNotIn('secret',json.dumps(stop))

    def test_missing_real_id_cannot_create_a_subagent(self):
        self.assertIsNone(self.event('SubagentStart',agent_type='ieum-reviewer'))

    def test_no_raw_prompt_tool_arguments_results_reasoning_or_secrets(self):
        event=self.event('PostToolUse',tool_name='Bash',tool_use_id='call-123',prompt='sk-secret',tool_input={'command':'curl password=private'},tool_response={'stdout':'hidden result'},reasoning='private CoT')
        encoded=json.dumps(event)
        for secret in ('sk-secret','password','curl','hidden result','private CoT','tool_response','tool_input'):
            self.assertNotIn(secret,encoded)
        self.assertEqual(event['tool_name'],'Bash')

    def test_unknown_tool_names_are_reduced_to_fixed_categories(self):
        self.assertEqual(self.event('PreToolUse',tool_name='mcp__private_client__password')['tool_name'],'MCP')
        self.assertEqual(self.event('PreToolUse',tool_name='customer_secret_function')['tool_name'],'local_tool')

    def test_pre_agent_passes_only_single_ticket_not_task_guess(self):
        event=self.event('PreToolUse',tool_name='Agent',tool_use_id='call-1',tool_input={'description':'IEUM_BIND:abcdef123456abcdef123456 verify TASK-001','subagent_type':'project-lifecycle-agent:ieum-qa','prompt':'do not retain'})
        self.assertEqual(event['kind'],'delegation_started')
        self.assertEqual(event['delegation_token'],'abcdef123456abcdef123456')
        self.assertEqual(event['role'],'qa')
        self.assertEqual(native_hooks.delegation_token({'task_name':'ieum-abcdef123456abcdef123456'}),'abcdef123456abcdef123456')
        self.assertEqual(native_hooks.delegation_token({'task_name':'ieum_abcdef123456abcdef123456'}),'abcdef123456abcdef123456')
        self.assertNotIn('task_id',event)
        self.assertNotIn('TASK-001',json.dumps(event))

    def test_ambiguous_tickets_are_not_bound(self):
        event=self.event('PreToolUse',tool_name='Agent',tool_use_id='call-1',tool_input={'description':'IEUM_BIND:abcdef123456abcdef123456 IEUM_BIND:123456abcdef123456abcdef'})
        self.assertNotIn('delegation_token',event)

    def test_structured_agent_result_is_bound_by_call_id(self):
        event=self.event('PostToolUse',tool_name='Agent',tool_use_id='toolu_01ABC',tool_response={'agentId':'real-agent-123','content':'secret'})
        self.assertEqual(event['kind'],'delegation_bound')
        self.assertEqual(event['call_id'],'toolu_01ABC')
        self.assertEqual(event['native_agent_id'],'real-agent-123')
        self.assertNotIn('secret',json.dumps(event))

    def test_agent_id_in_result_text_is_not_trusted(self):
        for response in ('{"agentId":"fake"}',{'content':[{'type':'text','text':'agentId: fake'}]}):
            event=self.event('PostToolUse',tool_name='Agent',tool_use_id='toolu_01ABC',tool_response=response)
            self.assertNotIn('native_agent_id',event)


    def test_failed_agent_dispatch_cancels_pending_without_fabricating_worker(self):
        event=self.event('PostToolUseFailure',tool_name='Agent',tool_use_id='toolu_failed',tool_input={'description':'IEUM_BIND:abcdef123456abcdef123456','subagent_type':'ieum-qa'},error='private dispatch failure')
        self.assertEqual(event['kind'],'delegation_failed')
        self.assertEqual(event['call_id'],'toolu_failed')
        self.assertNotIn('native_agent_id',event)
        self.assertNotIn('private',json.dumps(event))

    def test_agent_call_without_correlation_id_is_not_bound(self):
        self.assertIsNone(self.event('PreToolUse',tool_name='Agent',tool_input={'description':'IEUM_BIND:abcdef123456abcdef123456'}))

    def test_invalid_envelopes_are_noop(self):
        for payload in (None,[],{}, {'session_id':'bad\nsecret','hook_event_name':'SessionStart'}, {'session_id':'valid','hook_event_name':'Unknown'}):
            self.assertIsNone(native_hooks.normalize(payload,'claude'))
        self.assertIsNone(native_hooks.normalize(self.base,'unknown'))

    def test_telemetry_failure_returns_zero_without_raw_exception(self):
        payload={**self.base,'hook_event_name':'SessionStart'}
        err=io.StringIO()
        with patch.object(native_hooks,'process',side_effect=RuntimeError('password=hidden')),contextlib.redirect_stderr(err):
            self.assertEqual(native_hooks.main(['--provider','claude'],stream=io.BytesIO(json.dumps(payload).encode())),0)
        self.assertNotIn('password',err.getvalue())
        self.assertIn('관측',err.getvalue())

    def test_input_limit_and_malformed_json_do_not_block_host(self):
        for data in (b'x'*(native_hooks.MAX_INPUT+1),b'{'):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(native_hooks.main(['--provider','claude'],stream=io.BytesIO(data)),0)

    def test_process_calls_ingest_for_selected_project_only(self):
        records=[]
        self.assertTrue(native_hooks.process({**self.base,'hook_event_name':'SessionStart'},'claude',ingest=lambda *a:records.append(a)))
        self.assertEqual(records[0][:2],(self.root.resolve(),'claude'))
        self.assertEqual(records[0][2]['source'],'host_hook')


    def test_codex_attach_failure_keeps_successful_hook_observation(self):
        records=[]
        attach_calls=[]
        def broken_attach(*args,**kwargs):
            attach_calls.append((args,kwargs))
            raise ValueError('private path and token')
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertTrue(native_hooks.process({**self.base,'hook_event_name':'SessionStart','transcript_path':'private-session.jsonl'},'codex',ingest=lambda *a:records.append(a),attach=broken_attach))
        self.assertEqual(len(records),1)
        self.assertEqual(records[0][2]['kind'],'session_started')
        self.assertEqual(attach_calls[0][0],(self.root.resolve(),self.base['session_id']))
        self.assertEqual(attach_calls[0][1],{'transcript_path':'private-session.jsonl'})
        self.assertNotIn('private',stderr.getvalue())

    def test_codex_attach_only_uses_session_lifecycle(self):
        calls=[]
        native_hooks.process({**self.base,'hook_event_name':'UserPromptSubmit'},'codex',ingest=lambda *a:None,attach=lambda *a,**k:calls.append((a,k)))
        native_hooks.process({**self.base,'hook_event_name':'PreToolUse','tool_name':'Read'},'codex',ingest=lambda *a:None,attach=lambda *a,**k:calls.append((a,k)))
        self.assertEqual(len(calls),1)


    def test_busy_engine_lock_is_retried_with_identical_event_identity(self):
        attempts=[]
        def busy_once(*args):
            attempts.append(args)
            if len(attempts)==1:
                raise ValueError('다른 쓰기 작업의 잠금이 있습니다. 종료 여부를 확인한 뒤 recover를 사용하세요.')
        with patch('tools.native_hooks.time.sleep'):
            self.assertTrue(native_hooks.process({**self.base,'hook_event_name':'SessionStart'},'claude',ingest=busy_once))
        self.assertEqual(len(attempts),2)
        self.assertEqual(attempts[0],attempts[1])

    def test_non_lock_validation_failure_is_never_retried(self):
        attempts=[]
        def invalid(*args):
            attempts.append(args)
            raise ValueError('invalid event')
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(native_hooks.process({**self.base,'hook_event_name':'SessionStart'},'claude',ingest=invalid))
        self.assertEqual(len(attempts),1)
        self.assertEqual(len(list((self.root/'state/native-inbox').glob('*.json'))),1)


    def test_durable_event_exists_before_ingest_and_ack_deletes_only_after_success(self):
        def inspect_queue(root,provider,event):
            files=list((root/'state/native-inbox').glob('*.json'))
            self.assertEqual(len(files),1)
            self.assertEqual(json.loads(files[0].read_text(encoding='utf-8'))['event'],event)
        self.assertTrue(native_hooks.process({**self.base,'hook_event_name':'SubagentStop','agent_id':'actual-agent'},'claude',ingest=inspect_queue))
        self.assertEqual(list((self.root/'state/native-inbox').glob('*.json')),[])

    def test_lock_timeout_retains_terminal_event_for_later_delivery(self):
        def busy(*args):raise ValueError('다른 쓰기 작업의 잠금이 있습니다. 종료 여부를 확인한 뒤 recover를 사용하세요.')
        def durable(root,provider,event):
            folder=root/'state/native-inbox';folder.mkdir(parents=True,exist_ok=True)
            path=folder/'queued.json';path.write_text(json.dumps({'event':event}),encoding='utf-8');return path
        with patch('tools.native_hooks.time.monotonic',side_effect=[0,3]),contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertTrue(native_hooks.process({**self.base,'hook_event_name':'SubagentStop','agent_id':'actual-agent'},'claude',ingest=busy,enqueue=durable))
        pending=list((self.root/'state/native-inbox').glob('*.json'))
        self.assertEqual(len(pending),1)
        self.assertEqual(json.loads(pending[0].read_text(encoding='utf-8'))['event']['kind'],'agent_completed')
        self.assertIn('자동으로 다시 수신',stderr.getvalue())


    def test_terminal_event_survives_real_business_lock_and_drains_after_release(self):
        from tools import native_inbox, native_observation
        from tools.storage import lock
        payload={**self.base,'hook_event_name':'SubagentStart','agent_id':'real-lock-worker'}
        native_observation.ingest(self.root,'claude',native_hooks.normalize(payload,'claude'))
        payload['hook_event_name']='SubagentStop'
        with lock(self.root):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertTrue(native_hooks.process(payload,'claude'))
            self.assertTrue((self.root/'state/write.lock').exists())
            self.assertEqual(len(list((self.root/'state/native-inbox').glob('*.json'))),1)
        self.assertEqual(native_inbox.drain(self.root),1)
        worker=next(a for a in native_observation.read(self.root)['agents'] if a['native_agent_id']=='real-lock-worker')
        self.assertEqual(worker['status'],'completed')
        self.assertEqual(list((self.root/'state/native-inbox').glob('*.json')),[])

    def test_optional_none_is_canonicalized_for_immediate_and_replay_signatures(self):
        event=self.event('SessionStart');event['native_agent_id']=None;seen=[]
        with patch('tools.native_hooks.normalize',return_value=event):
            native_hooks.process({**self.base,'hook_event_name':'SessionStart'},'claude',ingest=lambda *args:seen.append(args[2]))
        self.assertNotIn('native_agent_id',seen[0])
        self.assertEqual(list((self.root/'state/native-inbox').glob('*.json')),[])

    def test_hook_configurations_use_native_safe_launchers(self):
        root=Path(native_hooks.__file__).resolve().parents[1]
        claude=json.loads((root/'hooks/hooks.json').read_text(encoding='utf-8'))
        codex=json.loads((root/'hooks/codex.json').read_text(encoding='utf-8'))
        for event in ('SessionStart','UserPromptSubmit','SubagentStart','SubagentStop','PreToolUse','PostToolUse','Stop'):
            c=claude['hooks'][event][0]['hooks'][0]
            self.assertEqual(c['command'],'python')
            self.assertEqual(c['args'],['-I','-X','utf8','${CLAUDE_PLUGIN_ROOT}/tools/native_hooks.py','--provider','claude'])
            x=codex['hooks'][event][0]['hooks'][0]
            self.assertIn("os.environ['CLAUDE_PLUGIN_ROOT']",x['commandWindows'])
            self.assertNotIn('${',x['commandWindows'])
            self.assertNotIn('$env:',x['commandWindows'])
            self.assertNotIn('args',x)
            self.assertIn(' -I -X utf8 -c ',x['command'])
            self.assertIn(' -I -X utf8 -c ',x['commandWindows'])



    def test_isolated_launcher_never_imports_customer_json_module(self):
        marker=self.root/'shadow-imported.txt'
        (self.root/'json.py').write_text("from pathlib import Path\nPath('shadow-imported.txt').write_text('untrusted customer import')\nraise RuntimeError('safe import shadow probe')\n",encoding='utf-8')
        source=Path(native_hooks.__file__).resolve().parents[1]
        command=json.loads((source/'hooks/codex.json').read_text(encoding='utf-8'))['hooks']['SessionStart'][0]['hooks'][0]['commandWindows']
        argv=shlex.split(command)
        result=subprocess.run([sys.executable,*argv[1:]],cwd=self.root,input=json.dumps({**self.base,'hook_event_name':'SessionStart'}).encode(),capture_output=True,env={**os.environ,'CLAUDE_PLUGIN_ROOT':str(source)},timeout=15)
        self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
        self.assertFalse(marker.exists())
        self.assertTrue((self.root/'state/workspace-native.json').exists())
        self.assertEqual(list((self.root/'state/native-inbox').glob('*.json')),[])

    @unittest.skipUnless(sys.platform == 'win32' and shutil.which('powershell'), 'Windows hook launcher')
    def test_windows_launcher_does_not_interpret_plugin_path_as_shell(self):
        plugin=self.root/"plugin $() & quote'"
        (plugin/'tools').mkdir(parents=True)
        shutil.copy2(Path(native_hooks.__file__),plugin/'tools/native_hooks.py')
        (plugin/'tools/__init__.py').write_text('',encoding='utf-8')
        (plugin/'tools/native_observation.py').write_text("from pathlib import Path\ndef ingest(root,provider,event):\n    (Path(root)/'hook-received.txt').write_text(event['session_id'],encoding='utf-8')\n",encoding='utf-8')
        (plugin/'tools/native_inbox.py').write_text("from pathlib import Path\ndef _normalise(provider,event):return {'event':event}\ndef enqueue(root,provider,event):\n    path=Path(root)/'queued.json'\n    path.write_text('{}')\n    return path\n",encoding='utf-8')
        source=Path(native_hooks.__file__).resolve().parents[1]
        config=json.loads((source/'hooks/codex.json').read_text(encoding='utf-8'))
        command=config['hooks']['SessionStart'][0]['hooks'][0]['commandWindows']
        env={**os.environ,'CLAUDE_PLUGIN_ROOT':str(plugin),'PYTHONUTF8':'1'}
        result=subprocess.run(['powershell','-NoProfile','-Command',command],input=json.dumps({**self.base,'hook_event_name':'SessionStart'}),text=True,capture_output=True,encoding='utf-8',errors='replace',env=env,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual((self.root/'hook-received.txt').read_text(),self.base['session_id'])
        self.assertEqual(result.stdout,'')


if __name__=='__main__':unittest.main()
