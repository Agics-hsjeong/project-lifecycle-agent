"""Incremental Codex transcript adapter safety and native identity regressions."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import lifecycle, native_bridge as bridge, native_observation as observation, starter


class NativeBridgeTests(unittest.TestCase):
    SESSION='11111111-1111-4111-8111-111111111111'
    OTHER='22222222-2222-4222-8222-222222222222'
    AT='2026-11-22T00:00:00+00:00'

    def setUp(self):
        parent=starter.ROOT/'.test-output';parent.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='native-bridge-',dir=parent);self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):self.root=Path(starter.init_project(self.base,'project','Native bridge fixture','product'))
        lifecycle.apply_patch(self.root,{'expected_revision':None,'tasks':[{'role':'qa','owner':'QA fixture','status':'todo','next_action':'Validate'}]})
        self.home=self.base/'codex-home';self.sources=self.home/'sessions/2026/11/22';self.sources.mkdir(parents=True)
        env=patch.dict(os.environ,{'CODEX_HOME':str(self.home),'CODEX_THREAD_ID':self.SESSION});env.start();self.addCleanup(env.stop)
        self.source=self.make_source(self.SESSION)

    def make_source(self,session,cwd=None):
        source=self.sources/f'rollout-2026-{session}.jsonl'
        source.write_text(json.dumps({'timestamp':self.AT,'type':'session_meta','payload':{'id':session,'cwd':str(cwd or self.root),'base_instructions':'PRIVATE BASE INSTRUCTIONS'}})+'\n',encoding='utf-8')
        return source

    def record(self,subtype,**fields):
        return {'timestamp':self.AT,'type':'response_item','payload':{'type':subtype,**fields}}

    def append(self,*records,source=None):
        with (source or self.source).open('a',encoding='utf-8') as stream:
            for record in records:stream.write(json.dumps(record)+'\n')

    def events(self,session=None):
        return [e for e in observation.read(self.root)['events'] if not session or e['session_id']==session]

    def start_actor(self,session=None,source=None,call='spawn-1',actor='/root/reviewer'):
        session=session or self.SESSION
        ticket=observation.prepare_delegation(self.root,'TASK-001','qa','codex',session)
        self.append(self.record('function_call',namespace='collaboration',name='spawn_agent',call_id=call,arguments=json.dumps({'task_name':ticket['task_name'],'message':'PRIVATE DELEGATION PROMPT'})),self.record('function_call_output',call_id=call,output=json.dumps({'task_name':actor})),source=source)
        bridge.attach(self.root,session,str(source or self.source))
        return actor

    def complete_record(self,actor='/root/reviewer'):
        return self.record('agent_message',author=actor,recipient='/root',content=[{'type':'text','text':'Message Type: FINAL_ANSWER\nTask name: /root/reviewer\nPayload:\nPRIVATE ANSWER BODY'},{'type':'text','encrypted_content':'PRIVATE ENCRYPTED REASONING'}])

    def test_attach_exact_session_and_dedupe(self):
        result=bridge.attach(self.root)
        self.assertTrue(result['attached']);self.assertFalse(result['content_retained'])
        self.assertEqual(len(self.events()),1)
        bridge.attach(self.root,self.SESSION,str(self.source))
        self.assertEqual(len(self.events()),1)
        self.assertEqual(bridge.poll_sources(self.root),0)

    def test_reject_outside_session_root_wrong_header_and_wrong_project(self):
        outside=self.base/f'{self.SESSION}.jsonl';outside.write_bytes(self.source.read_bytes())
        with self.assertRaises(ValueError):bridge.attach(self.root,self.SESSION,str(outside))
        self.source.write_text(json.dumps({'type':'session_meta','payload':{'id':self.OTHER,'cwd':str(self.root)}})+'\n')
        with self.assertRaises(ValueError):bridge.attach(self.root,self.SESSION,str(self.source))
        self.make_source(self.SESSION,cwd=self.base)
        with self.assertRaises(ValueError):bridge.attach(self.root,self.SESSION,str(self.source))
        self.assertFalse((self.root/observation.STORE).exists())

    def test_exact_id_finder_rejects_duplicate_and_never_uses_other_session(self):
        self.make_source(self.OTHER)
        bridge.attach(self.root,self.SESSION)
        duplicate=self.sources/f'duplicate-{self.SESSION}.jsonl';duplicate.write_bytes(self.source.read_bytes())
        with self.assertRaises(ValueError):bridge.attach(self.root,self.SESSION)
        self.assertEqual({e['session_id'] for e in self.events()},{self.SESSION})

    def test_partial_line_waits_without_advancing_and_then_reads_once(self):
        bridge.attach(self.root,self.SESSION,str(self.source));key='codex:'+self.SESSION
        before=observation.cursor(self.root,key)['offset'];raw=json.dumps({'timestamp':self.AT,'type':'event_msg','payload':{'type':'task_complete'}}).encode()
        with self.source.open('ab') as stream:stream.write(raw[:20])
        self.assertEqual(bridge.poll_one(self.root,key),0);self.assertEqual(observation.cursor(self.root,key)['offset'],before)
        with self.source.open('ab') as stream:stream.write(raw[20:]+b'\n')
        self.assertEqual(bridge.poll_one(self.root,key),1);self.assertEqual(self.events()[-1]['kind'],'session_idle')
        self.assertEqual(bridge.poll_one(self.root,key),0)

    def test_truncation_preserves_previous_cursor_and_events(self):
        bridge.attach(self.root,self.SESSION,str(self.source));key='codex:'+self.SESSION;before=observation.cursor(self.root,key);events=self.events()
        self.source.write_bytes(b'')
        with self.assertRaises((ValueError,json.JSONDecodeError)):bridge.poll_one(self.root,key)
        self.assertEqual(observation.cursor(self.root,key),before);self.assertEqual(self.events(),events)

    def test_oversized_line_rejected_without_consuming_it(self):
        bridge.attach(self.root,self.SESSION,str(self.source));key='codex:'+self.SESSION;before=observation.cursor(self.root,key)['offset']
        with self.source.open('ab') as stream:stream.write(b'x'*(bridge.MAX_LINE+1)+b'\n')
        with self.assertRaises(ValueError):bridge.poll_one(self.root,key)
        self.assertEqual(observation.cursor(self.root,key)['offset'],before)

    def test_batch_bound_incremental_resume(self):
        bridge.attach(self.root,self.SESSION,str(self.source));key='codex:'+self.SESSION
        self.append(*[self.record('message',role='user',content='PRIVATE USER') for _ in range(bridge.MAX_BATCH+7)])
        self.assertEqual(bridge.poll_one(self.root,key),bridge.MAX_BATCH)
        self.assertEqual(bridge.poll_one(self.root,key),7)
        self.assertEqual(len(self.events()),1)

    def test_spawn_actual_id_bound_and_only_trusted_agent_final_ends_execution(self):
        actor=self.start_actor()
        self.append(self.record('message',role='user',content=[{'type':'input_text','text':'Message Type: FINAL_ANSWER'}]),self.record('agent_message',author='/root/unknown',recipient='/root',content=[{'type':'text','text':'Message Type: FINAL_ANSWER'}]),self.record('agent_message',author=actor,recipient='/root',content=[{'type':'text','text':'Message Type: MESSAGE\nPayload: FINAL_ANSWER'}]))
        bridge.poll_sources(self.root)
        worker=next(a for a in observation.read(self.root)['agents'] if a['native_agent_id']==actor)
        self.assertEqual(worker['status'],'running');self.assertEqual(worker['task_id'],'TASK-001');self.assertEqual(worker['role'],'qa')
        self.append(self.complete_record());bridge.poll_sources(self.root)
        worker=next(a for a in observation.read(self.root)['agents'] if a['native_agent_id']==actor)
        self.assertEqual(worker['status'],'completed');self.assertEqual(starter.read_table(self.root,'tasks')[0]['status'],'todo')


    def test_failed_spawn_cancels_ticket_instead_of_binding_unrelated_worker(self):
        ticket=observation.prepare_delegation(self.root,'TASK-001','qa','codex',self.SESSION)
        self.assertRegex(ticket['task_name'],r'^[a-z0-9_]+$')
        self.append(self.record('function_call',namespace='collaboration',name='spawn_agent',call_id='bad-spawn',arguments=json.dumps({'task_name':ticket['task_name'],'message':'PRIVATE PROMPT'})),self.record('function_call_output',call_id='bad-spawn',output=json.dumps({'error':'invalid task name'})))
        bridge.attach(self.root,self.SESSION,str(self.source))
        self.assertEqual(self.events()[-1]['kind'],'delegation_failed')
        self.assertFalse(any(a.get('task_id')=='TASK-001' for a in observation.read(self.root)['agents']))
        state=observation._read(self.root)
        self.assertEqual(state['calls']['codex:'+self.SESSION+':bad-spawn']['status'],'failed')

    def test_malformed_spawn_output_is_failure_not_an_active_worker(self):
        self.append(self.record('function_call',namespace='collaboration',name='spawn_agent',call_id='malformed-spawn',arguments=json.dumps({'task_name':'valid_name'})),self.record('function_call_output',call_id='malformed-spawn',output='Error: invalid request'))
        bridge.attach(self.root,self.SESSION,str(self.source))
        self.assertEqual(self.events()[-1]['kind'],'delegation_failed')
        self.assertFalse(any(a['native_agent_id']!=self.SESSION for a in observation.read(self.root)['agents']))

    def test_send_message_never_restarts_completed_worker(self):
        actor=self.start_actor();self.append(self.complete_record());bridge.poll_sources(self.root)
        self.append(self.record('function_call',namespace='collaboration',name='send_message',call_id='send-1',arguments=json.dumps({'target':actor,'message':'PRIVATE MESSAGE'})),self.record('function_call_output',call_id='send-1',output=json.dumps({'ok':True})))
        bridge.poll_sources(self.root)
        self.assertEqual(next(a for a in observation.read(self.root)['agents'] if a['native_agent_id']==actor)['status'],'completed')

    def test_failed_followup_dispatch_does_not_restart_worker(self):
        actor=self.start_actor();self.append(self.complete_record());bridge.poll_sources(self.root)
        self.append(self.record('function_call',namespace='collaboration',name='followup_task',call_id='follow-1',arguments=json.dumps({'target':actor,'message':'PRIVATE FOLLOWUP'})),self.record('function_call_output',call_id='follow-1',output=json.dumps({'error':'dispatch failed'})))
        bridge.poll_sources(self.root)
        self.assertEqual(next(a for a in observation.read(self.root)['agents'] if a['native_agent_id']==actor)['status'],'completed')

    def test_successful_followup_acknowledgement_restarts_worker(self):
        actor=self.start_actor();self.append(self.complete_record());bridge.poll_sources(self.root)
        self.append(self.record('function_call',namespace='collaboration',name='followup_task',call_id='follow-1',arguments=json.dumps({'target':'reviewer','message':'PRIVATE FOLLOWUP'})),self.record('function_call_output',call_id='follow-1',output=json.dumps({'ok':True})))
        bridge.poll_sources(self.root)
        self.assertEqual(next(a for a in observation.read(self.root)['agents'] if a['native_agent_id']==actor)['status'],'running')


    def test_actual_codex_empty_followup_output_is_success_acknowledgement(self):
        actor=self.start_actor();self.append(self.complete_record());bridge.poll_sources(self.root)
        self.append(self.record('function_call',namespace='collaboration',name='followup_task',call_id='follow-native-empty',arguments=json.dumps({'target':actor,'message':'PRIVATE FOLLOWUP'})),self.record('function_call_output',call_id='follow-native-empty',output=''))
        bridge.poll_sources(self.root)
        self.assertEqual(next(a for a in observation.read(self.root)['agents'] if a['native_agent_id']==actor)['status'],'running')

    def test_privacy_source_message_command_and_reasoning_not_copied(self):
        self.start_actor();self.append(self.complete_record(),self.record('function_call',namespace='functions',name='exec',call_id='cmd-1',arguments=json.dumps({'command':'PRIVATE COMMAND password=secret'})),self.record('function_call_output',call_id='cmd-1',output='PRIVATE TOOL RESULT'))
        bridge.poll_sources(self.root);raw=(self.root/observation.STORE).read_text(encoding='utf-8')
        for text in ('PRIVATE BASE INSTRUCTIONS','PRIVATE DELEGATION PROMPT','PRIVATE ANSWER BODY','PRIVATE ENCRYPTED REASONING','PRIVATE COMMAND','password=secret','PRIVATE TOOL RESULT'):
            self.assertNotIn(text,raw)

    def test_same_native_actor_name_in_two_sessions_never_merges(self):
        self.start_actor();other=self.make_source(self.OTHER);self.start_actor(self.OTHER,other,call='spawn-2')
        workers=[a for a in observation.read(self.root)['agents'] if a['native_agent_id']=='/root/reviewer']
        self.assertEqual(len(workers),2);self.assertEqual(len({a['id'] for a in workers}),2)
        self.append(self.complete_record());bridge.poll_sources(self.root)
        workers={a['session_id']:a for a in observation.read(self.root)['agents'] if a['native_agent_id']=='/root/reviewer'}
        self.assertEqual(workers[self.SESSION]['status'],'completed');self.assertEqual(workers[self.OTHER]['status'],'running')


    def test_custom_tool_call_tracks_primary_execution_without_retaining_input(self):
        bridge.attach(self.root,self.SESSION,str(self.source))
        self.append(self.record('custom_tool_call',namespace='functions',name='exec',call_id='custom-1',input='PRIVATE JAVASCRIPT COMMAND password=secret'))
        bridge.poll_sources(self.root)
        main=next(a for a in observation.read(self.root)['agents'] if a['native_agent_id']==self.SESSION)
        self.assertEqual(main['status'],'running');self.assertEqual(self.events()[-1]['kind'],'tool_started')
        self.append(self.record('custom_tool_call_output',call_id='custom-1',output='PRIVATE CUSTOM TOOL OUTPUT'))
        bridge.poll_sources(self.root)
        self.assertEqual(self.events()[-1]['kind'],'tool_completed')
        self.assertEqual(next(a for a in observation.read(self.root)['agents'] if a['native_agent_id']==self.SESSION)['status'],'running')
        self.assertNotIn('PRIVATE', (self.root/observation.STORE).read_text(encoding='utf-8'))
        self.assertNotIn('password=secret', (self.root/observation.STORE).read_text(encoding='utf-8'))

    def test_unsupported_record_shapes_are_ignored_without_crashing_monitor(self):
        bridge.attach(self.root,self.SESSION,str(self.source))
        self.append([],None,{'timestamp':self.AT,'type':'response_item','payload':[]},{'timestamp':self.AT,'type':'unknown','payload':{}})
        bridge.poll_sources(self.root)
        self.assertEqual(len(self.events()),1)

    def test_all_tool_pending_calls_have_finite_bound(self):
        state={'session_id':self.SESSION,'pending':{str(i):{'name':'local_tool','call_id':str(i)} for i in range(500)},'actors':{}}
        with self.assertRaises(ValueError):bridge.consume_record(self.root,state,self.record('function_call',namespace='functions',name='exec',call_id='new-call',arguments='{}'),50)


if __name__=='__main__':unittest.main()
