"""Observation reports use host capability; viewers never dispatch work."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request,urlopen
from tools import starter,lifecycle,workspace,workspace_server

class ObservationHttpTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=starter.ROOT/'.test-output')
        self.addCleanup(self.temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.project=Path(starter.init_project(Path(self.temp.name),'observe','Observation fixture','product',profile='api'))
        lifecycle.apply_patch(self.project,{'expected_revision':self.revision(),'tasks':[{'role':'backend','owner':'synthetic host','status':'todo','next_action':'Implement fixture'}]})
        self.server=workspace_server.make_server(self.project)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.close)
    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(3)
    def revision(self):
        events=lifecycle.events(self.project);return events[-1]['revision'] if events else None
    def payload(self):
        return {'task_id':'TASK-001','agent_id':'synthetic-native-worker','provider':'codex','role':'backend','kind':'action','summary':'Read the registered API contract','event_id':'synthetic-event-1'}
    def request(self,payload,host):
        token=self.server.host_token if host else self.server.human_token
        r=Request(self.server.origin+'/api/host/activity',data=json.dumps(payload).encode(),headers={'Authorization':'Bearer '+token,'Content-Type':'application/json','Origin':self.server.origin})
        try:
            with urlopen(r,timeout=5) as response:return response.status,json.load(response)
        except HTTPError as error:return error.code,json.load(error)
    def test_host_reports_are_visible_without_business_revision_change(self):
        original=self.revision();code,result=self.request(self.payload(),True)
        self.assertEqual(code,200,result);self.assertEqual(self.revision(),original)
        code,duplicate=self.request(self.payload(),True);self.assertEqual(code,200,duplicate)
        snapshot=workspace.snapshot(self.project)
        self.assertEqual(len(snapshot['activity']),1);self.assertEqual(snapshot['activity'][0]['summary'],self.payload()['summary'])
        self.assertEqual(snapshot['view_mode'],'observer')
    def test_viewer_cannot_inject_activity_or_commands(self):
        code,result=self.request(self.payload(),False);self.assertEqual(code,403,result)
        code,result=self.request({**self.payload(),'command':['echo','unsafe']},True);self.assertEqual(code,400,result)
        self.assertEqual(workspace.snapshot(self.project)['activity'],[])
    def test_native_host_cli_records_utf8_activity_without_shell_dispatch(self):
        f=Path(self.temp.name)/'activity.json';f.write_text(json.dumps(self.payload()),encoding='utf-8')
        r=subprocess.run([sys.executable,str(starter.ROOT/'tools/starter.py'),'workspace-activity',str(self.project),'--file',str(f),'--json'],capture_output=True,timeout=20)
        self.assertEqual(r.returncode,0,r.stderr.decode('utf-8',errors='replace'))
        result=json.loads(r.stdout.decode('utf-8'));self.assertIn('event',result)
        self.assertEqual(len(workspace.snapshot(self.project)['activity']),1)
