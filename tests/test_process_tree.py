"""Real local process regressions; no native model or network is called."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from tools import host_providers, starter
from tools.storage import process_alive

class ProcessTreeTests(unittest.TestCase):
    def setUp(self):
        base=starter.ROOT/'.test-output'
        base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='process-tree-',dir=base)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def process(self,args,stdin=None,**options):
        return host_providers._process(args,self.root,stdin,self.root/'out.log',self.root/'err.log',**options)

    def test_normal_input_is_delivered_completely(self):
        text='입력'*50000
        code,_=self.process([sys.executable,'-c',"import sys; print(len(sys.stdin.read()))"],text,timeout=5)
        self.assertEqual(code,0)
        self.assertEqual(int((self.root/'out.log').read_text()),len(text))

    def test_leader_exit_reaps_live_descendants_before_return(self):
        sentinel=self.root/'late.txt';pidfile=self.root/'child.pid'
        child="import time;from pathlib import Path;time.sleep(1);Path("+repr(str(sentinel))+").write_text('unexpected')"
        parent="import subprocess,sys;from pathlib import Path;p=subprocess.Popen([sys.executable,'-c',"+repr(child)+"],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);Path("+repr(str(pidfile))+").write_text(str(p.pid))"
        code,_=self.process([sys.executable,'-c',parent],timeout=5)
        self.assertEqual(code,0)
        self.assertFalse(process_alive(int(pidfile.read_text())))
        time.sleep(1.1)
        self.assertFalse(sentinel.exists())

    def test_blocked_input_obeys_deadline(self):
        started=[];began=time.monotonic()
        with self.assertRaisesRegex(host_providers.ProviderError,'제한 시간'):
            self.process([sys.executable,'-c','import time; time.sleep(20)'],'x'*2000000,timeout=.2,on_started=started.append)
        self.assertLess(time.monotonic()-began,4)
        self.assertFalse(process_alive(started[0]))

    def test_blocked_input_obeys_stop(self):
        stop=threading.Event();timer=threading.Timer(.2,stop.set);timer.start();started=[];began=time.monotonic()
        try:
            with self.assertRaisesRegex(host_providers.ProviderError,'중지 요청'):
                self.process([sys.executable,'-c','import time; time.sleep(20)'],'x'*2000000,timeout=10,stop_event=stop,on_started=started.append)
        finally:timer.cancel()
        self.assertLess(time.monotonic()-began,4)
        self.assertFalse(process_alive(started[0]))

    @unittest.skipUnless(os.name == 'nt', 'Windows retained process handles')
    def test_exited_process_with_open_handle_is_not_alive(self):
        process=subprocess.Popen([sys.executable,'-c','pass'])
        try:
            process.wait(timeout=5)
            self.assertFalse(process_alive(process.pid))
        finally:
            process._handle.Close()

    def test_callback_failure_does_not_leave_a_suspended_or_live_worker(self):
        ids=[]
        def fail(pid):
            ids.append(pid)
            raise ValueError('synthetic callback failed')
        with self.assertRaisesRegex(ValueError,'synthetic callback'):
            self.process([sys.executable,'-c','import time;time.sleep(20)'],timeout=5,on_started=fail)
        self.assertFalse(process_alive(ids[0]))
