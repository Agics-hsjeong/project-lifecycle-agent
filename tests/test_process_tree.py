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
from unittest.mock import patch
from tools import host_providers, starter
from tools import process_tree
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

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux zombie owner regression')
    def test_exited_unreaped_child_is_not_alive(self):
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        try:
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                fields = Path(f'/proc/{pid}/stat').read_text().rpartition(')')[2].split()
                if fields[0] == 'Z':
                    break
                time.sleep(.01)
            else:
                self.fail('Child did not reach zombie state')
            self.assertFalse(process_alive(pid))
            self.assertTrue(process_alive(os.getpid()))
        finally:
            os.waitpid(pid, 0)


    def test_native_stdout_stderr_and_nonzero_exit_are_preserved(self):
        code,_=self.process([sys.executable,'-c',"import sys; print(sys.stdin.read()); print('native error',file=sys.stderr); sys.exit(7)"],'native input',timeout=5)
        self.assertEqual(code,7)
        self.assertIn('native input',(self.root/'out.log').read_text())
        self.assertIn('native error',(self.root/'err.log').read_text())

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux detached process lifetime')
    def test_detached_session_is_reaped_after_leader_normal_exit(self):
        sentinel=self.root/'detached-late.txt';pidfile=self.root/'detached.pid'
        child="import os,time;from pathlib import Path;os.setsid();Path("+repr(str(pidfile))+").write_text(str(os.getpid()));time.sleep(1);Path("+repr(str(sentinel))+").write_text('unexpected')"
        parent="import subprocess,sys,time;from pathlib import Path;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);ready=Path("+repr(str(pidfile))+")\nwhile not ready.exists() or not ready.stat().st_size: time.sleep(.005)"
        code,_=self.process([sys.executable,'-c',parent],timeout=5)
        self.assertEqual(code,0)
        self.assertFalse(process_alive(int(pidfile.read_text())))
        time.sleep(1.1)
        self.assertFalse(sentinel.exists())

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux detached process stop')
    def test_detached_session_stop_finishes_before_late_write(self):
        sentinel=self.root/'stop-late.txt';pidfile=self.root/'stop-child.pid'
        stop=threading.Event();started=[]
        child="import os,time;from pathlib import Path;os.setsid();Path("+repr(str(pidfile))+").write_text(str(os.getpid()));time.sleep(2);Path("+repr(str(sentinel))+").write_text('unexpected')"
        parent="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);time.sleep(20)"
        def cancel_when_ready():
            deadline=time.monotonic()+5
            while (not pidfile.exists() or not pidfile.stat().st_size) and time.monotonic()<deadline:
                time.sleep(.01)
            stop.set()
        watcher=threading.Thread(target=cancel_when_ready)
        watcher.start();began=time.monotonic()
        try:
            with self.assertRaisesRegex(host_providers.ProviderError,'중지 요청'):
                self.process([sys.executable,'-c',parent],timeout=10,stop_event=stop,on_started=started.append)
        finally:
            watcher.join(timeout=6)
        self.assertLess(time.monotonic()-began,3)
        self.assertTrue(pidfile.exists(),'Detached child never reached its session')
        self.assertFalse(process_alive(int(pidfile.read_text())))
        self.assertFalse(process_alive(started[0]))
        time.sleep(2.1)
        self.assertFalse(sentinel.exists())

    @unittest.skipUnless(sys.platform.startswith('linux'), 'Linux owner death signal')
    def test_owner_death_reaps_broker_and_detached_session(self):
        sentinel=self.root/'owner-late.txt';pidfile=self.root/'owner-child.pid';brokerfile=self.root/'broker.pid'
        child="import os,time;from pathlib import Path;os.setsid();Path("+repr(str(pidfile))+").write_text(str(os.getpid()));time.sleep(2);Path("+repr(str(sentinel))+").write_text('unexpected')"
        parent="import subprocess,sys,time;subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);time.sleep(20)"
        owner="import sys;from pathlib import Path;sys.path.insert(0,"+repr(str(starter.ROOT))+");from tools import host_providers;host_providers._process("+repr([sys.executable,'-c',parent])+",Path("+repr(str(self.root))+"),None,Path("+repr(str(self.root/'owner-out.log'))+"),Path("+repr(str(self.root/'owner-err.log'))+"),timeout=10,on_started=lambda pid:Path("+repr(str(brokerfile))+").write_text(str(pid)))"
        process=subprocess.Popen([sys.executable,'-c',owner])
        try:
            deadline=time.monotonic()+5
            while (not pidfile.exists() or not pidfile.stat().st_size) and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertTrue(pidfile.exists(),'Detached child did not start')
            process.kill();process.wait(timeout=3)
            ids=[int(pidfile.read_text()),int(brokerfile.read_text())]
            deadline=time.monotonic()+3
            while any(process_alive(pid) for pid in ids) and time.monotonic()<deadline:
                time.sleep(.01)
            self.assertFalse(any(process_alive(pid) for pid in ids))
            time.sleep(2.1)
            self.assertFalse(sentinel.exists())
        finally:
            if process.poll() is None:
                process.kill();process.wait(timeout=3)


    def test_linux_pid_reuse_before_handle_never_signals_other_process(self):
        record={'pid':123,'start':4,'state':'S'}
        with patch.object(process_tree,'_linux_record',return_value={**record,'start':5}), patch.object(process_tree.os,'pidfd_open',create=True) as opened, patch.object(process_tree.signal,'pidfd_send_signal',create=True) as sent:
            process_tree._linux_signal_owned(record,15)
        opened.assert_not_called()
        sent.assert_not_called()

    def test_linux_pid_reuse_after_handle_never_signals_other_process(self):
        record={'pid':123,'start':4,'state':'S'}
        with patch.object(process_tree,'_linux_record',side_effect=[record,{**record,'start':5}]), patch.object(process_tree.os,'pidfd_open',return_value=789,create=True), patch.object(process_tree.signal,'pidfd_send_signal',create=True) as sent, patch.object(process_tree.os,'close') as closed:
            process_tree._linux_signal_owned(record,15)
        sent.assert_not_called()
        closed.assert_called_once_with(789)
