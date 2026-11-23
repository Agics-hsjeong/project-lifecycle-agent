"""Recorded completion and current evidence access are separate facts."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen

from tools import lifecycle, native_observation as native, runs, starter, workspace, workspace_server


class VerificationAccessTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / '.test-output'
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='verification-access-', dir=parent)
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root = Path(starter.init_project(Path(self.temp.name), 'access', 'Synthetic evidence access', 'product'))
        (self.root / 'input.txt').write_text('stable input', encoding='utf-8')
        lifecycle.apply_patch(self.root, {'expected_revision': self.rev(), 'requirements': [{'description': 'Check access', 'source': 'input.txt'}]})

    def rev(self):
        return workspace.revision(self.root)

    def complete(self, key, status='succeeded', bindings=None):
        plan = runs.create(self.root, {'expected_revision': self.rev(), 'key': key, 'title': key,
            'requirement_ids': ['REQ-001'], 'bindings': bindings or {}, 'steps': [{'id': 'STEP-001', 'title': key, 'depends_on': [],
                'inputs': ['input.txt'], 'expected_outputs': [key + '.txt']}]})
        for state in ('running', status):
            if state != 'running':
                (self.root / (key + '.txt')).write_text('receipt ' + key, encoding='utf-8')
            runs.result(self.root, {'expected_revision': self.rev(), 'run_id': plan['run_id'], 'step_id': 'STEP-001',
                'key': key + state, 'status': state, 'evidence_refs': [] if state == 'running' else [key + '.txt'], 'notes': 'Synthetic result'})
        return plan['run_id']

    def deny(self, *names):
        original = Path.open
        def guarded(path, *args, **kwargs):
            if path.parent == self.root and path.name in names:
                raise PermissionError('PRIVATE-ERROR absolute-path credentials-must-not-leak')
            return original(path, *args, **kwargs)
        return patch.object(Path, 'open', guarded)

    def test_permission_error_is_unavailable_and_read_only_then_current_hash_recovers(self):
        self.complete('output')
        before = {ref: (self.root / ref).read_bytes() for ref in (runs.STORE, workspace.STORE)}
        with self.deny('output.txt'):
            snap = workspace.snapshot(self.root)
            task = snap['tasks'][0]
            self.assertEqual(task['status'], 'verification_unavailable')
            self.assertEqual(task['verification_state'], 'unavailable')
            self.assertEqual(task['recorded_status'], 'succeeded')
            self.assertFalse(task['receipt_valid'])
            self.assertIsNone(task['progress'])
            self.assertEqual(snap['summary_counts']['completed'], 0)
            self.assertEqual(snap['summary_counts']['stale'], 0)
            self.assertEqual(snap['summary_counts']['recorded_completed'], 1)
            self.assertEqual(snap['summary_counts']['verification_unavailable'], 1)
            inspection = runs.inspect(self.root)['runs'][0]['steps'][0]
            self.assertFalse(inspection['valid'])
            self.assertFalse(inspection['ready_to_start'])
            self.assertEqual(inspection['effective_status'], 'verification_unavailable')
            self.assertNotIn('PRIVATE-ERROR', json.dumps(snap))
        self.assertEqual(before, {ref: (self.root / ref).read_bytes() for ref in before})
        self.assertEqual(workspace.snapshot(self.root)['tasks'][0]['verification_state'], 'valid')
        (self.root / 'output.txt').write_text('tampered', encoding='utf-8')
        snap = workspace.snapshot(self.root)
        self.assertEqual(snap['tasks'][0]['verification_state'], 'changed')
        self.assertEqual(snap['summary_counts']['stale'], 1)
        self.assertEqual(snap['summary_counts']['recorded_completed'], 1)

    def test_dependency_unavailable_blocks_gate_and_known_tamper_wins(self):
        self.complete('first')
        run = runs.read(self.root)['runs'][0]
        step = run['steps'][0]
        step['receipt_hashes']['other.txt'] = 'wrong'
        (self.root / 'other.txt').write_text('actual', encoding='utf-8')
        with self.deny('first.txt'):
            self.assertEqual(runs.verification_state(self.root, run, step), 'changed')
            self.assertFalse(runs.valid_step(self.root, run, step))
        step['receipt_hashes'].pop('other.txt')
        child = {**step, 'id': 'STEP-002', 'depends_on': ['STEP-001']}
        run['steps'].append(child)
        with self.deny('first.txt'):
            self.assertEqual(runs.verification_state(self.root, run, child), 'unavailable')
            self.assertFalse(runs.valid_step(self.root, run, child))
        step['depends_on'] = ['STEP-002']
        self.assertEqual(runs.verification_state(self.root, run, step), 'changed')

    def test_exact_multi_run_scope_counts_and_failed_attempt_are_preserved(self):
        first = self.complete('first')
        second = self.complete('second')
        failed = self.complete('failed', 'failed')
        snap = workspace.snapshot(self.root)
        selected = [task['id'] for task in snap['tasks'] if task.get('run_id') in {first, second}]
        workspace.configure(self.root, {'expected_revision': self.rev(), 'current_scope': {'id': 'both', 'title': 'Two runs', 'task_ids': selected}})
        with self.deny('first.txt', 'second.txt'):
            snap = workspace.snapshot(self.root)
        counts = snap['summary_counts']
        self.assertEqual(counts['total'], 2)
        self.assertEqual(counts['recorded_completed'], 2)
        self.assertEqual(counts['recorded_completion_percent'], 100)
        self.assertEqual(counts['completed'], 0)
        self.assertEqual(counts['completion_percent'], 0)
        self.assertEqual(counts['verification_unavailable'], 2)
        self.assertEqual(snap['all_summary_counts']['failed'], 1)
        failed_step = next(run for run in snap['history_runs'] if run['id'] == failed)['steps'][0]
        self.assertEqual(failed_step['status'], 'failed')
        self.assertEqual(failed_step['attempts'], 1)
        summary = {'status': 'failed', 'checks': 2, 'mode': 'execute', 'source': 'worker'}
        self.assertEqual(workspace._verification_for_task({'status': 'verification_unavailable', 'verification_state': 'unavailable'}, [{'verification_summary': summary}]), summary)

    def test_direct_receipt_access_and_invalid_paths_are_not_verified(self):
        self.assertEqual(workspace.receipt_verification(self.root, {'../outside.txt': 'hash'}), 'changed')
        self.assertEqual(workspace.receipt_verification(self.root, {'missing.txt': 'hash'}), 'changed')
        checksum = workspace.file_hash(self.root, 'input.txt')
        with self.deny('input.txt'):
            self.assertEqual(workspace.receipt_verification(self.root, {'input.txt': checksum}), 'unavailable')

    def test_public_api_does_not_leak_permission_error_details(self):
        self.complete('output')
        server = workspace_server.make_server(self.root)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = Request(server.origin + '/api/snapshot', headers={'Authorization': 'Bearer ' + server.human_token})
            with self.deny('output.txt'), urlopen(request, timeout=5) as response:
                value = json.load(response)
            self.assertEqual(value['tasks'][0]['verification_state'], 'unavailable')
            self.assertNotIn('PRIVATE-ERROR', json.dumps(value))
        finally:
            server.shutdown(); server.server_close(); thread.join(3)

    def test_native_terminal_observations_do_not_rewrite_unavailable_receipt(self):
        lifecycle.apply_patch(self.root, {'expected_revision': self.rev(), 'tasks': [{'role': 'backend', 'next_action': 'Publish receipt'}]})
        self.complete('output', bindings={'STEP-001': 'TASK-001'})
        for index, status in enumerate(('completed', 'failed')):
            with self.subTest(status=status):
                session = 'session-' + str(index)
                ticket = native.prepare_delegation(self.root, 'TASK-001', 'backend', 'claude', session)
                base = {'session_id': session, 'source': 'host_hook', 'native_agent_id': 'worker-' + str(index)}
                native.ingest(self.root, 'claude', {**base, 'event_id': 'start', 'kind': 'agent_started', 'delegation_token': ticket['token']})
                native.ingest(self.root, 'claude', {**base, 'event_id': 'end', 'kind': 'agent_' + status})
                with self.deny('output.txt'):
                    snap = workspace.snapshot(self.root)
                task = next(task for task in snap['tasks'] if task['id'] == 'TASK-001')
                self.assertEqual(task['status'], 'verification_unavailable')
                self.assertEqual(task['ledger_status'], 'verification_unavailable')
                self.assertEqual(task['execution_state'], status)
                self.assertEqual(snap['summary_counts']['recorded_completed'], 1)
                self.assertEqual(snap['summary_counts']['completed'], 0)

    @unittest.skipUnless(os.name == 'nt', 'Windows sharing violation')
    def test_real_windows_read_denial_is_unavailable_then_rehashes_tamper(self):
        import ctypes
        from ctypes import wintypes
        self.complete('output')
        path = self.root / 'output.txt'
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
        self.assertNotEqual(handle, wintypes.HANDLE(-1).value)
        try:
            with self.assertRaises(PermissionError):
                path.read_bytes()
            snap = workspace.snapshot(self.root)
            self.assertEqual(snap['tasks'][0]['verification_state'], 'unavailable')
            self.assertFalse(snap['history_runs'][0]['steps'][0]['valid'])
            self.assertEqual(snap['summary_counts']['recorded_completed'], 1)
            self.assertEqual(snap['summary_counts']['completed'], 0)
            self.assertEqual(snap['summary_counts']['stale'], 0)
        finally:
            kernel.CloseHandle(handle)
        self.assertEqual(workspace.snapshot(self.root)['tasks'][0]['verification_state'], 'valid')
        path.write_text('changed after lock released', encoding='utf-8')
        self.assertEqual(workspace.snapshot(self.root)['tasks'][0]['verification_state'], 'changed')
