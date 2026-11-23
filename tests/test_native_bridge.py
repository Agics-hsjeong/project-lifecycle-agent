"""Incremental Codex transcript adapter safety and native identity regressions."""
import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import lifecycle, native_bridge as bridge, native_observation as observation, starter, storage


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


    def monitor_thread(self):
        class History(dict):
            def update(self, *args, **kwargs):
                super().update(*args, **kwargs)
                seen.append(dict(self))
        seen = []
        stop, health = threading.Event(), History()
        thread = threading.Thread(target=bridge.monitor, args=(self.root, stop, health))
        thread.start()
        self.addCleanup(lambda: (stop.set(), thread.join(5)))
        return stop, health, thread, seen

    def until(self, predicate, timeout=4):
        end = time.monotonic() + timeout
        while not predicate():
            if time.monotonic() >= end:
                self.fail("monitor did not reach the expected bounded state")
            time.sleep(.01)

    def held_project_lock(self):
        held, release = threading.Event(), threading.Event()
        def hold():
            with storage.lock(self.root):
                held.set()
                release.wait(5)
        thread = threading.Thread(target=hold)
        thread.start()
        self.addCleanup(lambda: (release.set(), thread.join(5)))
        self.assertTrue(held.wait(3))
        return release, thread

    def test_monitor_short_real_reader_lock_waits_then_delivers_without_warning(self):
        bridge.attach(self.root, self.SESSION, str(self.source))
        self.append({'type': 'event_msg', 'timestamp': self.AT, 'payload': {'type': 'task_complete'}})
        release, holder = self.held_project_lock()
        stop, health, thread, seen = self.monitor_thread()
        time.sleep(.1)
        self.assertFalse(any(x.get('status') == 'unavailable' for x in seen))
        release.set(); holder.join(3)
        self.until(lambda: health.get('status') == 'ready')
        stop.set(); thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertFalse(any(x.get('status') in {'waiting', 'unavailable'} for x in seen))
        self.assertTrue(any(x.get('last_success_at') for x in seen))
        self.assertEqual(self.events()[-1]['kind'], 'session_idle')
        self.assertFalse((self.root / 'state/write.lock').exists())

    def test_monitor_persistent_lock_is_waiting_then_warns_and_recovers_without_cursor_loss(self):
        bridge.attach(self.root, self.SESSION, str(self.source))
        original = (self.root / observation.STORE).read_bytes()
        self.append({'type': 'event_msg', 'timestamp': self.AT, 'payload': {'type': 'task_complete'}})
        release, holder = self.held_project_lock()
        with patch.object(bridge, 'MONITOR_LOCK_GRACE', .04), patch.object(bridge, 'MONITOR_POLL_INTERVAL', .02), patch.object(bridge, 'MONITOR_BUSY_WARNING', .1):
            stop, health, thread, seen = self.monitor_thread()
            self.until(lambda: any(x.get('status') == 'waiting' for x in seen))
            self.assertIsNone(next(x for x in seen if x['status'] == 'waiting')['last_error'])
            self.until(lambda: health.get('error_code') == 'lock_timeout')
            self.assertEqual(health['status'], 'unavailable')
            self.assertEqual((self.root / observation.STORE).read_bytes(), original)
            release.set(); holder.join(3)
            self.until(lambda: health.get('status') == 'ready')
            self.assertIsNone(health['waiting_since'])
            self.assertIsNone(health['last_error'])
            stop.set(); thread.join(3)
        self.assertEqual([x['kind'] for x in self.events()].count('session_idle'), 1)

    def test_monitor_stop_interrupts_project_lock_grace_without_recovering_anything(self):
        bridge.attach(self.root, self.SESSION, str(self.source))
        release, holder = self.held_project_lock()
        with patch.object(bridge, 'MONITOR_LOCK_GRACE', 10):
            stop, health, thread, seen = self.monitor_thread()
            time.sleep(.05); start = time.monotonic(); stop.set(); thread.join(1)
        self.assertFalse(thread.is_alive())
        self.assertLess(time.monotonic() - start, .5)
        self.assertTrue((self.root / 'state/write.lock').is_file())
        release.set(); holder.join(3)
        self.assertEqual(health['status'], 'stopped')

    def test_monitor_pending_transaction_is_real_error_and_is_never_deleted(self):
        bridge.attach(self.root, self.SESSION, str(self.source))
        pending = self.root / 'state/pending.json'; pending.write_bytes(b'preserve this journal')
        stop, health, thread, seen = self.monitor_thread()
        self.until(lambda: health.get('status') == 'unavailable')
        self.assertEqual(health['error_code'], 'state_error')
        self.assertIsNone(health['waiting_since'])
        stop.set(); thread.join(3)
        self.assertEqual(pending.read_bytes(), b'preserve this journal')

    def test_damaged_old_source_does_not_starve_healthy_current_source(self):
        bridge.attach(self.root, self.SESSION, str(self.source))
        other = self.make_source(self.OTHER)
        bridge.attach(self.root, self.OTHER, str(other))
        original_offset = observation.cursor(self.root, 'codex:' + self.SESSION)['offset']
        with self.source.open('a', encoding='utf-8') as stream:
            stream.write('PRIVATE MALFORMED SOURCE CONTENT\n')
        self.append({'type': 'event_msg', 'timestamp': self.AT, 'payload': {'type': 'task_complete'}}, source=other)
        stop, health, thread, seen = self.monitor_thread()
        self.until(lambda: health.get('status') == 'unavailable')
        self.assertEqual(health['error_code'], 'source_invalid')
        stop.set(); thread.join(3)
        self.assertEqual(observation.cursor(self.root, 'codex:' + self.SESSION)['offset'], original_offset)
        self.assertEqual(self.events(self.OTHER)[-1]['kind'], 'session_idle')
        self.assertNotIn('PRIVATE', json.dumps(health))
        self.assertNotIn(str(self.source), json.dumps(health))

    def test_monitor_corrupt_projection_shapes_report_error_instead_of_killing_thread(self):
        bridge.attach(self.root, self.SESSION, str(self.source))
        path = self.root / observation.STORE
        original = path.read_bytes(); valid = json.loads(original)
        bad = [[], {**valid, 'seq': True}, {**valid, 'cursors': {'codex:bad': []}}, {**valid, 'cursors': {'codex:bad': {'pending': []}}}]
        for value in bad:
            with self.subTest(value=value):
                raw = json.dumps(value).encode(); path.write_bytes(raw)
                stop, health, thread, seen = self.monitor_thread()
                self.until(lambda: health.get('status') == 'unavailable')
                self.assertTrue(thread.is_alive())
                self.assertEqual(health['error_code'], 'state_error')
                stop.set(); thread.join(3)
                self.assertEqual(path.read_bytes(), raw)
                path.write_bytes(original)

    @unittest.skipUnless(os.name == 'nt', 'Windows denied file sharing')
    def test_monitor_actual_source_access_denial_warns_and_recovers(self):
        bridge.attach(self.root, self.SESSION, str(self.source))
        self.append({'type': 'event_msg', 'timestamp': self.AT, 'payload': {'type': 'task_complete'}})
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
        kernel.CreateFileW.restype = ctypes.c_void_p
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.CreateFileW(str(self.source), 0x80000000, 0, None, 3, 0, None)
        self.assertNotEqual(handle, ctypes.c_void_p(-1).value)
        try:
            stop, health, thread, seen = self.monitor_thread()
            self.until(lambda: health.get('status') == 'unavailable')
            self.assertEqual(health['error_code'], 'access_denied')
            self.assertIsNone(health['waiting_since'])
        finally:
            kernel.CloseHandle(handle)
        self.until(lambda: health.get('status') == 'ready')
        stop.set(); thread.join(3)
        self.assertEqual(self.events()[-1]['kind'], 'session_idle')

    def test_monitor_never_retries_untyped_or_wrong_path_lock_errors(self):
        for error in [ValueError('private invalid source'), storage.ProjectBusyError('wrong lock')]:
            if isinstance(error, storage.ProjectBusyError):
                error.__cause__ = FileExistsError(17, 'exists', str(self.base / 'unrelated.lock'))
            self.assertFalse(bridge._monitor_busy(self.root, error))
        with patch('tools.native_bridge.Path.resolve', side_effect=PermissionError('private denied root')):
            self.assertFalse(bridge._monitor_busy(self.root, PermissionError('private denied source')))
            self.assertFalse(bridge._monitor_busy(self.root, storage.ProjectBusyError('unverifiable lock')))


if __name__=='__main__':unittest.main()
