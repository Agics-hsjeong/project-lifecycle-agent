"""Lock-bound dependency environments; no package downloads or model calls."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from tools import starter, verification_runtime as runtime


def node_child_forwarder(check, *, diagnostics=False, inherit_stdio=False):
    # Normal npm lifecycle commands inherit the supervisor's log descriptors.
    # Avoid nested capture pipes inside the strict Linux sandbox. Keep captured
    # output as a separate non-isolated fixture regression below.
    options="{stdio:['ignore','inherit','inherit']}" if inherit_stdio else "{encoding:'utf8'}"
    before = ("require('fs').writeSync(2,'ieum:parent:start '+JSON.stringify({execPath:process.execPath,argv:process.argv,cwd:process.cwd()}).slice(0,2048)+'\\n');" if diagnostics else '')
    after = ("require('fs').writeSync(2,'ieum:parent:child '+JSON.stringify({status:child.status,signal:child.signal,error:child.error?String(child.error):null,stdio:"+json.dumps('inherit' if inherit_stdio else 'pipe')+",stdoutBytes:child.stdout?child.stdout.length:null,stderrBytes:child.stderr?child.stderr.length:null}).slice(0,2048)+'\\n');" if diagnostics else '')
    forward = "" if inherit_stdio else "process.stdout.write(child.stdout||'');process.stderr.write(child.stderr||'');"
    return (before+"const child=require('child_process').spawnSync(process.execPath,['-e',"+json.dumps(check)+"],"+options+");"+
            after+forward+"if(child.error){require('fs').writeSync(2,String(child.error)+'\\n');}process.exitCode=child.error||child.status===null?1:child.status;")


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        base=starter.ROOT/'.test-output';base.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='approved-runtime-',dir=base)
        self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        with contextlib.redirect_stdout(io.StringIO()):
            self.project=Path(starter.init_project(self.base/'projects','sample','Runtime fixture','product',profile='api'))
        (self.project/'requirements.lock').write_text('fixture-package==1.0 --hash=sha256:'+'0'*64+'\n')
        self.packages=self.base/'approved-packages';self.packages.mkdir()
        (self.packages/'fixture_package.py').write_text('VALUE = 73\n')
        self.copy=self.base/'copy';self.copy.mkdir();(self.copy/'requirements.lock').write_bytes((self.project/'requirements.lock').read_bytes())
        self.scratch=self.copy/'.ieum-verification/tmp'

    def registered(self):
        return runtime.register(self.project,kind='python',lockfile='requirements.lock',packages=self.packages)

    def test_dependency_import_and_fingerprint_without_host_site_packages(self):
        approved=self.registered()
        plan=runtime.prepare([sys.executable,'-c','import fixture_package;assert fixture_package.VALUE==73'],self.copy,
                             {'runtime_ref':approved['runtime_ref']},self.scratch,project=self.project)
        result=subprocess.run(plan['argv'],cwd=self.copy,capture_output=True,text=True,env={**os.environ,**plan['env']})
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(plan['fingerprint']['kind'],'python')
        runtime.assert_unchanged(plan)

    def test_registered_dependency_tampering_fails_before_launch(self):
        approved=self.registered();cache=runtime._registry(self.project)/approved['runtime_ref']
        (cache/'packages/fixture_package.py').write_text('VALUE=99\n')
        with self.assertRaisesRegex(runtime.RuntimeUnavailable,'바이트'):
            runtime.prepare([sys.executable,'-m','unittest'],self.copy,{'runtime_ref':approved['runtime_ref']},self.scratch,project=self.project)

    def test_untracked_bytecode_is_not_ignored_after_registration(self):
        approved=self.registered();cache=runtime._registry(self.project)/approved['runtime_ref']
        (cache/'packages/extra.pyc').write_bytes(b'Synthetic changed bytecode')
        with self.assertRaisesRegex(runtime.RuntimeUnavailable,'바이트'):
            runtime._load(self.project,approved['runtime_ref'])

    def test_lockfile_drift_requires_a_new_environment(self):
        approved=self.registered();(self.project/'requirements.lock').write_text('changed dependency')
        with self.assertRaisesRegex(runtime.RuntimeUnavailable,'잠금 파일'):
            runtime._load(self.project,approved['runtime_ref'])

    def test_reference_cannot_be_a_host_path_or_traversal(self):
        for reference in ['../private','C:/private','a'*63,'g'*64]:
            with self.subTest(reference=reference),self.assertRaises(runtime.RuntimeUnavailable):
                runtime._load(self.project,reference)

    def test_no_reference_keeps_stdlib_command_without_grants(self):
        argv=[sys.executable,'-m','unittest']
        plan=runtime.prepare(argv,self.copy,{},self.scratch)
        self.assertEqual(plan['argv'],argv)
        self.assertEqual(plan['read_paths'],[])
        self.assertIsNone(plan['fingerprint'])

    def test_package_links_cannot_escape_the_approved_root(self):
        outside=self.base/'secret.txt';outside.write_text('Synthetic private fixture')
        try:(self.packages/'outside.py').symlink_to(outside)
        except OSError:self.skipTest('Host cannot create symlinks')
        with self.assertRaisesRegex(runtime.RuntimeUnavailable,'밖'):
            self.registered()

    def test_changed_environment_is_detected_after_verification(self):
        approved=self.registered()
        plan=runtime.prepare([sys.executable,'-m','unittest'],self.copy,{'runtime_ref':approved['runtime_ref']},self.scratch,project=self.project)
        (Path(plan['read_paths'][0])/'packages/fixture_package.py').write_text('VALUE=0\n')
        with self.assertRaises(runtime.RuntimeUnavailable):runtime.assert_unchanged(plan)

    def test_candidate_lock_drift_cannot_use_old_dependencies(self):
        approved=self.registered();(self.copy/'requirements.lock').write_text('Unapproved candidate dependency')
        with self.assertRaisesRegex(runtime.RuntimeUnavailable,'사본의 잠금'):
            runtime.prepare([sys.executable,'-m','unittest'],self.copy,{'runtime_ref':approved['runtime_ref']},self.scratch,project=self.project)

    def test_external_runners_fail_readiness_without_an_approved_runtime(self):
        for argv in [['npm','test'],['node','check.mjs'],[sys.executable,'-m','pytest']]:
            with self.subTest(argv=argv),self.assertRaises(runtime.RuntimeUnavailable):
                runtime.preflight({'verification_commands':[argv]},self.project)

    @unittest.skipUnless(shutil.which('node'), 'Node child output forwarding requires Node')
    def test_node_fixture_preserves_output_and_nonzero_exit(self):
        # Large pipe output makes premature parent exit observable on POSIX;
        # a failing child must retain both diagnostics and its nonzero status.
        size=256*1024
        check=f"process.stdout.write('o'.repeat({size}));process.stderr.write('e'.repeat({size}));process.exitCode=7;"
        for inherit_stdio in (False, True):
            with self.subTest(inherit_stdio=inherit_stdio):
                result=subprocess.run([shutil.which('node'),'-e',node_child_forwarder(check,inherit_stdio=inherit_stdio)],
                                      cwd=self.base,capture_output=True,timeout=15)
                self.assertEqual(result.returncode,7)
                self.assertEqual(result.stdout,b'o'*size)
                self.assertEqual(result.stderr,b'e'*size)

    @unittest.skipUnless(shutil.which('node'), 'Node child error regression requires Node')
    def test_node_fixture_rejects_spawn_error_even_with_zero_status(self):
        # This explicit synthetic seam models the observed Linux result shape:
        # status=0 together with spawnSync.error must never claim success.
        setup="require('child_process').spawnSync=()=>({status:0,error:Error('synthetic spawn failure'),stdout:'',stderr:''});"
        for inherit_stdio in (False, True):
            with self.subTest(inherit_stdio=inherit_stdio):
                result=subprocess.run([shutil.which('node'),'-e',setup+node_child_forwarder('0',inherit_stdio=inherit_stdio)],
                                      cwd=self.base,capture_output=True,timeout=15)
                self.assertNotEqual(result.returncode,0)
                self.assertIn(b'synthetic spawn failure',result.stderr)

    @unittest.skipUnless(os.environ.get('IEUM_SANDBOX_CLI') and shutil.which('node'), 'Actual strict Node verifier requires IEUM_SANDBOX_CLI and Node')
    def test_node_child_resolves_approved_files_without_reading_volume_or_original(self):
        # A tiny trusted npm-shaped launcher isolates Node/child resolution;
        # the separate real-project harness compiles using the actual npm + TS.
        from tools import verification_sandbox as sandbox, host_providers
        fixture=self.packages/'fixture';fixture.mkdir()
        (fixture/'index.cjs').write_text('module.exports = 73;\n')
        npm=self.base/'trusted-npm';(npm/'bin').mkdir(parents=True)
        private=self.project/'private-fixture.txt';private.write_text('Synthetic original project sentinel')
        check=("const fs=require('fs');const assert=require('assert/strict');fs.writeSync(2,'ieum:child:start\\n');"
               "assert.equal(require('./node_modules/fixture/index.cjs'),73);"
               "assert.throws(()=>fs.readFileSync("+json.dumps(str(private))+"));fs.writeFileSync(process.env.IEUM_VERIFICATION_SCRATCH+'/node-child-completed','child:73; original:denied');console.log('child:73; original:denied')")
        script=node_child_forwarder(check, diagnostics=True, inherit_stdio=True)
        (npm/'bin/npm-cli.js').write_text(script,encoding='utf-8')
        approved=runtime.register(self.project,kind='node',lockfile='requirements.lock',packages=self.packages,node=Path(shutil.which('node')),npm=npm)
        plan=runtime.prepare(['npm','test'],self.copy,{'runtime_ref':approved['runtime_ref']},self.scratch,project=self.project)
        command,environment,boundary=sandbox.prepare(plan['argv'],self.copy,self.project,executable=os.environ['IEUM_SANDBOX_CLI'],
            readonly=True,read_paths=plan['read_paths'],environment=plan['env'])
        try:
            stdout=self.base/'node.stdout';stderr=self.base/'node.stderr'
            code,_=host_providers._process(command,self.copy,None,stdout,stderr,20,environment=environment)
            actual_stdout=stdout.read_text(encoding='utf-8',errors='replace')
            actual_stderr=stderr.read_text(encoding='utf-8',errors='replace')
            completed=self.scratch/'node-child-completed'
            diagnostic=json.dumps({'exit_code':code,'verifier_started':Path(boundary['started_marker']).is_file(),
                'child_completed':completed.is_file(),'planned_argv':plan['argv'],'launcher':command[:2],
                'node_options':environment.get('NODE_OPTIONS'),'stdout':actual_stdout[-2048:],
                'stderr':actual_stderr[-4096:]},ensure_ascii=False)
            self.assertEqual(code,0,diagnostic)
            self.assertTrue(Path(boundary['started_marker']).is_file(),diagnostic)
            self.assertTrue(completed.is_file(),diagnostic)
            self.assertEqual(completed.read_text(encoding='utf-8'),'child:73; original:denied',diagnostic)
            self.assertIn('child:73; original:denied',actual_stdout,diagnostic)
            runtime.assert_unchanged(plan)
        finally:
            sandbox.cleanup(boundary)

    @unittest.skipUnless(os.name=='nt' and os.environ.get('IEUM_SANDBOX_CLI'), 'Actual Windows network-isolated asyncio check')
    def test_windows_asyncio_fails_fast_as_environment_limit_without_network_grant(self):
        from tools import verification_sandbox as sandbox, host_providers
        approved=self.registered()
        plan=runtime.prepare([sys.executable,'-c','import asyncio; asyncio.run(asyncio.sleep(0))'],self.copy,
            {'runtime_ref':approved['runtime_ref']},self.scratch,project=self.project)
        command,environment,boundary=sandbox.prepare(plan['argv'],self.copy,self.project,executable=os.environ['IEUM_SANDBOX_CLI'],
            readonly=True,read_paths=plan['read_paths'],environment=plan['env'])
        try:
            stdout=self.base/'async.stdout';stderr=self.base/'async.stderr'
            code,_=host_providers._process(command,self.copy,None,stdout,stderr,12,environment=environment)
            self.assertNotEqual(code,0)
            self.assertIn('IEUM_ENVIRONMENT_UNAVAILABLE:',stderr.read_text(encoding='utf-8',errors='replace'))
            self.assertFalse(boundary['network'])
            runtime.assert_unchanged(plan)
        finally:
            sandbox.cleanup(boundary)
