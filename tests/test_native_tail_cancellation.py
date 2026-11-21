"""Final native log delivery respects cancellation/deadlines after the CLI leader exits."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from tools import host_providers, starter
from tools.storage import process_alive


class NativeTailCancellationTests(unittest.TestCase):
    def setUp(self):
        parent = starter.ROOT / ".test-output"
        parent.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(prefix="native-tail-final-", dir=parent)
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.events = []
        self.delivery_after_tree_stop = []
        self.parent = None
        self.tree_stopped = False
        self.deferred_final_reads = 0
        records = ''.join(json.dumps({"type": "item.started", "item": {
            "id": "synthetic-"+str(i), "type": "command_execution", "command": "synthetic-no-secret"}})+'\n' for i in range(100))
        child = ("import os,sys,time;from pathlib import Path;sys.stdout.write("+repr(records)+");sys.stdout.flush();"
                 "Path('child-ready').write_text(str(os.getpid()));time.sleep(5);Path('late-child-write').write_text('late')")
        self.program = ("import subprocess,sys,time;from pathlib import Path;"
                        "subprocess.Popen([sys.executable,'-c',"+repr(child)+"]);"
                        "deadline=time.monotonic()+3\n"
                        "while not Path('child-ready').exists() and time.monotonic()<deadline:time.sleep(.005)\n"
                        "raise SystemExit(0 if Path('child-ready').exists() else 4)")

    def run_final_batch(self, *, timeout, stop=None, delay=0):
        real_popen = subprocess.Popen
        real_terminate = host_providers.ProcessTree.terminate
        real_poll = host_providers.NativeActivityTail.poll
        def capture(*args, **kwargs):
            self.parent = real_popen(*args, **kwargs)
            return self.parent
        def stop_tree(tree, process):
            value = real_terminate(tree, process)
            self.tree_stopped = True
            return value
        def final_batch_only(tail, final=False):
            # Model a public-event batch received after its leader has finished.
            # Keep this bounded even on the old implementation with no pre-drain stop.
            if not self.tree_stopped:
                if self.parent is None or self.parent.poll() is None:
                    return
                self.deferred_final_reads += 1
                if self.deferred_final_reads < 3:
                    return
            return real_poll(tail, final=final)
        def activity(event):
            self.events.append(event)
            self.delivery_after_tree_stop.append(self.tree_stopped)
            if stop is not None and len(self.events) == 1:
                stop.set()
            if delay:
                time.sleep(delay)
        with patch.object(host_providers.subprocess, "Popen", side_effect=capture), \
                patch.object(host_providers.ProcessTree, "terminate", new=stop_tree), \
                patch.object(host_providers.NativeActivityTail, "poll", new=final_batch_only):
            return host_providers._process([sys.executable,"-c",self.program],self.root,None,
                self.root/"stdout.jsonl",self.root/"stderr.log",timeout,
                stop_event=stop,native_provider="codex",on_activity=activity)

    def assert_producers_reaped(self):
        self.assertIsNotNone(self.parent)
        self.assertFalse(process_alive(self.parent.pid))
        pidfile = self.root/"child-ready"
        self.assertTrue(pidfile.is_file(), (self.root/"stderr.log").read_text(encoding="utf-8"))
        self.assertFalse(process_alive(int(pidfile.read_text(encoding="utf-8"))))
        self.assertFalse((self.root/"late-child-write").exists())
        self.assertTrue(self.events, "The test must exercise final public-event delivery")
        self.assertTrue(all(self.delivery_after_tree_stop), "Surviving producers must stop before final events are drained")

    def test_stop_during_final_delivery_never_returns_success(self):
        stop = threading.Event()
        with self.assertRaisesRegex(host_providers.ProviderError,"중지 요청"):
            self.run_final_batch(timeout=8,stop=stop,delay=.002)
        self.assertTrue(stop.is_set())
        self.assert_producers_reaped()

    def test_final_delivery_obeys_deadline_after_leader_exit(self):
        with self.assertRaisesRegex(host_providers.ProviderError,"제한 시간"):
            self.run_final_batch(timeout=2,delay=.025)
        self.assert_producers_reaped()

    def test_normal_final_delivery_is_complete_before_success(self):
        code,_ = self.run_final_batch(timeout=8,delay=.001)
        self.assertEqual(code,0)
        self.assertEqual(len(self.events),100)
        self.assertEqual(len({event["event_id"] for event in self.events}),100)
        self.assert_producers_reaped()


if __name__ == "__main__":
    unittest.main()
