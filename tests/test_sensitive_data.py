"""Synthetic credentials only; no real customer files or model calls."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from tools import sensitive_data as privacy, starter, workspace, lifecycle


class SensitiveDataTests(unittest.TestCase):
    def test_credential_paths_are_blocked_without_blocking_public_certificates(self):
        for path in ['config/credentials.json', 'config/key.pem', '.ssh/id_ed25519', '.env', 'secrets/config.json', 'service-account-prod.json', 'keystore.pfx']:
            with self.subTest(path=path): self.assertTrue(privacy.is_sensitive_path(path))
        for path in ['src/policy.py', 'package.json', 'config/settings.json', 'certifi/cacert.pem', 'docs/security.md', '.env.example']:
            with self.subTest(path=path): self.assertFalse(privacy.is_sensitive_path(path))

    def test_private_content_is_blocked_ordinary_sources_and_certificates_are_not(self):
        for value in [b'-----BEGIN PRIVATE KEY-----\nSYNTHETIC_KEY_BODY\n-----END PRIVATE KEY-----',
                      json.dumps({'type':'service_account','private_key':'SYNTHETIC_PRIVATE_VALUE'}).encode(),
                      b'github_pat_12345678901234567890', b'{"client_secret":"SYNTHETIC_SECRET_VALUE"}',
                      b'password: SYNTHETIC_YAML_VALUE\n', b'{\"nested\":{\"client_secret\":\"SYNTHETIC_PARTIAL\",',
                      r'{\"api_key\":\"SYNTHETIC_ESCAPED\"'.encode(),
                      '{\"password\":\"SYNTHETIC_UTF16\"}'.encode('utf-16'),
                      b'https://user:SYNTHETIC_URL@localhost/test?code=E_AUTH', b'API_KEY = \"SYNTHETIC_LITERAL_VALUE\"']:
            with self.subTest(value=value[:30]):self.assertTrue(privacy.contains_sensitive_data(value))
        for value in [b'VALUE = 1\n', b'{"name":"portal","version":"1.0.0"}', b'-----BEGIN CERTIFICATE-----\nPUBLIC\n-----END CERTIFICATE-----', b'{"api_key":"${API_KEY}"}']:
            with self.subTest(value=value[:30]):self.assertFalse(privacy.contains_sensitive_data(value))

    def test_json_nested_arrays_escaped_and_partial_values_are_redacted(self):
        fixtures = [
            '{"password":"SYNTHETIC_JSON_VALUE","path":"src/policy.py","code":"E_AUTH"}',
            '{"nested":[{"api_key":"SYNTHETIC_JSON_VALUE"}],"code":"E_AUTH"}',
            '{"headers":{"x-api-key":"SYNTHETIC_JSON_VALUE"},"api_token":"SYNTHETIC_JSON_VALUE"}',
            json.dumps({'payload':json.dumps({'password':'SYNTHETIC_JSON_VALUE'})}),
            'native error {"password": "SYNTHETIC_JSON_VALUE", "code":"E_AUTH"',
            r'native error {\"password\":\"SYNTHETIC_JSON_VALUE\"}',
            '{"pass\\u0077ord":"SYNTHETIC_JSON_VALUE",',
            "password='SYNTHETIC_JSON_VALUE with spaces' code=E_AUTH",
            '비밀번호: "SYNTHETIC_JSON_VALUE"\nReading src/policy.py',
        ]
        for value in fixtures:
            with self.subTest(value=value):
                actual=privacy.redact_text(value)
                self.assertNotIn('SYNTHETIC_JSON_VALUE',actual)
                self.assertIn('[redacted]',actual)
        actual=json.loads(privacy.redact_text(fixtures[0]))
        self.assertEqual(actual['path'],'src/policy.py');self.assertEqual(actual['code'],'E_AUTH')

    def test_headers_urls_private_key_and_provider_tokens_are_redacted(self):
        fixtures = [
            'Authorization: Basic SYNTHETIC_JSON_VALUE\npath=src/policy.py code=E_AUTH',
            'Bearer SYNTHETIC_JSON_VALUE',
            'https://user:SYNTHETIC_JSON_VALUE@host/path?api_key=SYNTHETIC_JSON_VALUE&code=E_AUTH',
            'https://127.0.0.1/path#token=SYNTHETIC_JSON_VALUE',
            '-----BEGIN OPENSSH PRIVATE KEY-----\nSYNTHETIC_JSON_VALUE\n-----END OPENSSH PRIVATE KEY-----',
        ]
        for value in fixtures:
            with self.subTest(value=value):self.assertNotIn('SYNTHETIC_JSON_VALUE',privacy.redact_text(value))
        self.assertEqual(privacy.redact_text('https://example.com/doc?q=one%20two#section'), 'https://example.com/doc?q=one%20two#section')
        actual=privacy.redact_text('path=src/policy.py code=E_AUTH token=SYNTHETIC_JSON_VALUE')
        self.assertIn('src/policy.py',actual);self.assertIn('E_AUTH',actual)

    def test_ordinary_source_argv_and_json_whitespace_remain_byte_stable(self):
        samples = ['{"ordinary":1}', 'HTTPS://example.com/doc?q=one%20two#section',
                   r'print("{\"ordinary\": 1}")', r'path="C:\work\ordinary.txt"']
        for sample in samples:
            with self.subTest(sample=sample):self.assertEqual(privacy.redact_text(sample),sample)

    def test_snapshot_does_not_mistake_private_metadata_for_zero_usage(self):
        records=[{'command_id':'one','attempt_id':'a','tokens':100,'cost_usd':.1,'source':'fixture'}]
        commands=[{'id':'one','status':'completed'},{'id':'two','status':'failed'}]
        value=workspace._usage_summary(records,commands)
        self.assertIsNone(value['tokens']);self.assertIsNone(value['cost_usd'])
        self.assertEqual(value['known_tokens'],100);self.assertEqual(value['known_cost_usd'],.1)
        self.assertEqual(value['coverage'],'partial');self.assertEqual(value['total_attempts'],2)
        self.assertEqual(value['unreported_attempts'],1)
        value=workspace._usage_summary([],[])
        self.assertEqual(value['coverage'],'unknown');self.assertIsNone(value['known_tokens'])

    def test_native_activity_and_running_attempts_are_unreported_not_free(self):
        native = [{'kind':'started','agent_id':'native-role','task_id':'task'},
                  {'kind':'tool_finished','agent_id':'native-role','task_id':'task'},
                  {'kind':'completed','agent_id':'native-role','task_id':'task'}]
        summary = workspace._usage_summary([], [], native)
        self.assertEqual(summary['total_attempts'],1)
        self.assertEqual(summary['native_unreported_attempts'],1)
        self.assertEqual(summary['coverage'],'unknown')
        self.assertIsNone(summary['tokens'])
        records = [{'command_id':'c','attempt_id':'old','tokens':8,'cost_usd':.01,'source':'fixture'}]
        summary = workspace._usage_summary(records,[{'id':'c','status':'running','attempt_id':'new'}])
        self.assertEqual(summary['total_attempts'],2)
        self.assertEqual(summary['known_tokens'],8)
        self.assertIsNone(summary['tokens'])

    def test_excluded_and_failed_are_neither_done_nor_waiting(self):
        counts=workspace._summary_counts([{'status':'done'},{'status':'skipped'},{'status':'failed'}])
        self.assertEqual(counts['completed'],1);self.assertEqual(counts['excluded'],1)
        self.assertEqual(counts['failed'],1);self.assertEqual(counts['waiting'],0)
        self.assertEqual(counts['applicable'],2);self.assertEqual(counts['completion_percent'],50)


class WorkspacePrivacyIntegrationTests(unittest.TestCase):
    def setUp(self):
        parent=starter.ROOT/'.test-output';parent.mkdir(exist_ok=True)
        temp=tempfile.TemporaryDirectory(prefix='privacy-engine-',dir=parent);self.addCleanup(temp.cleanup)
        with contextlib.redirect_stdout(io.StringIO()):
            self.root=Path(starter.init_project(Path(temp.name),'project','Synthetic privacy','product'))
        lifecycle.apply_patch(self.root,{'expected_revision':workspace.revision(self.root),'tasks':[
            {'role':'backend','owner':'Synthetic worker','status':'todo','next_action':'Implement policy'}]})
        self.plan={'instruction':'Produce scoped file','allowed_paths':['src/policy.py'],'expected_outputs':['src/policy.py'],
                   'verification_commands':[['python','-c','pass']], 'input_paths':[]}
        workspace.configure(self.root,{'expected_revision':workspace.revision(self.root),'policy':{'auto_run':True},'tasks':[
            {'id':'TASK-001','stage':'implementation','execution':self.plan}]})

    def host(self,kind,**fields):
        return workspace.host_event(self.root,{'expected_revision':workspace.revision(self.root),'agent_id':'fixture-worker','provider':'codex','role':'backend','kind':kind,**fields})

    def test_activity_host_failure_and_public_export_redact_at_storage_boundary(self):
        secret='SYNTHETIC_JSON_VALUE'
        workspace.activity_event(self.root,{'agent_id':'fixture-worker','provider':'codex','role':'backend','kind':'error',
            'task_id':'TASK-001','summary':json.dumps({'password':secret,'code':'E_AUTH','path':'src/policy.py'})})
        command=self.host('claim',task_id='TASK-001')['command'];self.assertIsNotNone(command)
        self.host('started',command_id=command['id'])
        self.host('failed',command_id=command['id'],notes='native error {"password":"'+secret+'","code":"E_AUTH"}')
        for name in [workspace.ACTIVITY_STORE,workspace.STORE,'state/events.jsonl']:
            path=self.root/name
            if path.exists():self.assertNotIn(secret,path.read_text(encoding='utf-8'))
        result=workspace.snapshot(self.root)
        self.assertNotIn(secret,json.dumps(result));self.assertIn('E_AUTH',json.dumps(result))
        self.assertEqual(result['summary_counts']['failed'],1)
        self.assertEqual(result['tasks'][0]['status'],'failed')
        self.assertEqual(result['usage']['coverage'],'unknown');self.assertEqual(result['usage']['unreported_attempts'],1)

    def test_input_runtime_and_verification_policy_validate_without_widening_paths(self):
        value=workspace.validate_execution(self.root,{**self.plan,'runtime_ref':'a'*64})
        self.assertEqual(value['input_paths'],[]);self.assertEqual(value['runtime_ref'],'a'*64)
        for invalid in [{'runtime_ref':'../../runtime'}, {'input_paths':['../private.json']}, {'input_paths':['config/credentials.json']},
                        {'verification_mode':'artifact_only'}, {'verification_mode':'artifact_only','verification_reason':'Document','verification_commands':[['python','-c','pass']]}]:
            with self.subTest(invalid=invalid),self.assertRaises(ValueError):workspace.validate_execution(self.root,{**self.plan,**invalid})
        value=workspace.validate_execution(self.root,{**self.plan,'verification_mode':'artifact_only','verification_reason':'Document draft','verification_commands':[]})
        self.assertEqual(value['verification_mode'],'artifact_only')

    def test_verification_success_requires_real_bound_checks(self):
        for value in [{'mode':'execute','status':'passed','checks':0,'source':'supervisor'},
                      {'mode':'execute','status':'passed','checks':1,'source':'supervisor'},
                      {'mode':'artifact_only','status':'passed','checks':1,'manifest_sha':'a'*64,'source':'supervisor'}]:
            with self.subTest(value=value),self.assertRaises(ValueError):workspace.validate_verification_summary(value)
        value=workspace.validate_verification_summary({'mode':'execute','status':'passed','checks':1,'manifest_sha':'a'*64,'source':'supervisor'})
        self.assertEqual(value['status'],'passed')