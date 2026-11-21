"""Exercise generation tool policy with synthetic inputs, no model calls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tools import worker_boundary as boundary


class WorkerBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ieum-worker-boundary-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'project'
        self.job = self.base / 'job'
        self.root = self.job / 'candidate'
        self.project.mkdir()
        (self.root / 'src').mkdir(parents=True)
        (self.root / 'inputs').mkdir()
        (self.root / 'inputs' / 'brief.md').write_text('synthetic brief', encoding='utf-8')
        (self.project / 'outside.txt').write_text('SYNTHETIC_OUTSIDE_SENTINEL', encoding='utf-8')
        self.policy = {'version': 1, 'candidate_root': str(self.root), 'allowed_paths': ['src', 'result.md']}

    def event(self, tool, **data):
        return {'hook_event_name': 'PreToolUse', 'tool_name': tool, 'tool_input': data}

    def check(self, tool, **data):
        return boundary.check_tool(self.policy, self.event(tool, **data))

    def claude(self):
        with patch.object(boundary, '_claude_version', return_value=(2, 1, 327)):
            return boundary.prepare('claude', ['fake-claude', '-p', '--tools', 'Read,Glob,Grep,Write,Edit',
                '--allowedTools', 'Read,Glob,Grep,Write,Edit'], self.root, self.project,
                self.job, executable=['fake-claude'], allowed_paths=self.policy['allowed_paths'])

    def test_actual_tools_read_inputs_write_only_owned_outputs(self):
        self.assertTrue(self.check('Read', file_path=str(self.root / 'inputs/brief.md')))
        self.assertTrue(self.check('Grep', pattern='https://example.test/foo', path=str(self.root)))
        self.assertTrue(self.check('Glob', pattern='**/*.md', path=str(self.root)))
        for tool in ('Write', 'Edit'):
            self.assertTrue(self.check(tool, file_path=str(self.root / 'src/new.py')))
            self.assertTrue(self.check(tool, file_path=str(self.root / 'result.md')))
            for target in ('inputs/brief.md', 'src-copy/escape.py', '.claude/settings.json', '.env', '.ieum-verification/check.py'):
                with self.subTest(tool=tool, target=target), self.assertRaises(boundary.BoundaryUnavailable):
                    self.check(tool, file_path=str(self.root / target))

    def test_read_absolute_parent_uri_dos_unc_ads_are_denied(self):
        targets = [str(self.project / 'outside.txt'), '../outside.txt', 'src/../../outside.txt',
                   'file:///etc/passwd', 'https://example.test/key', '//server/share/key',
                   r'\\server\share\key', r'\\?\C:\outside.txt', r'C:outside.txt', str(self.root / 'src/.. /../outside.txt'),
                   str(self.root / 'src/NUL'), str(self.root / 'src/con.txt'),
                   str(self.root / 'src/key.txt') + ':private', '~/.ssh/id_rsa',
                   str(self.root.parent / 'candidate-sibling/key.txt')]
        for target in targets:
            with self.subTest(target=target), self.assertRaises(boundary.BoundaryUnavailable):
                self.check('Read', file_path=target)

    def test_search_patterns_cannot_change_search_root(self):
        for pattern in ('../*', '**/../../*', '/etc/*', 'C:/*', r'..\*', '~/*'):
            with self.subTest(pattern=pattern), self.assertRaises(boundary.BoundaryUnavailable):
                self.check('Glob', pattern=pattern, path=str(self.root))
        with self.assertRaises(boundary.BoundaryUnavailable):
            self.check('Grep', path=str(self.project), pattern='SYNTHETIC')
        with self.assertRaises(boundary.BoundaryUnavailable):
            self.check('Grep', path=str(self.root), glob='../*', pattern='SYNTHETIC')

    def test_unknown_tool_and_malformed_contract_fail_closed(self):
        for event in (None, {}, self.event('Bash', command='type outside.txt'), self.event('Read'),
                      {'tool_name': 'Read', 'tool_input': {'file_path': str(self.root)}}):
            with self.subTest(event=event), self.assertRaises((boundary.BoundaryUnavailable, TypeError)):
                boundary.check_tool(self.policy, event)
        with self.assertRaises(boundary.BoundaryUnavailable):
            boundary.check_tool({**self.policy, 'allowed_paths': ['.']}, self.event('Read', file_path=str(self.root)))

    def test_symlink_or_junction_cannot_bypass_read_or_recursive_search(self):
        link = self.root / 'inputs/outside'
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.project)], capture_output=True)
            if result.returncode:
                self.skipTest('Junction creation unavailable')
            self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        else:
            link.symlink_to(self.project, target_is_directory=True)
        for tool, data in [('Read', {'file_path': str(link / 'outside.txt')}),
                           ('Glob', {'path': str(self.root), 'pattern': '**/*'}),
                           ('Grep', {'path': str(self.root), 'pattern': 'SYNTHETIC'})]:
            with self.subTest(tool=tool), self.assertRaises(boundary.BoundaryUnavailable):
                self.check(tool, **data)

    def test_reparse_detection_without_python312_junction_api(self):
        link = self.root / 'inputs/legacy-api-link'
        if os.name == 'nt':
            result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(self.project)], capture_output=True)
            if result.returncode:
                self.skipTest('Junction creation unavailable')
            self.addCleanup(lambda: os.rmdir(link) if link.exists() else None)
        else:
            link.symlink_to(self.project, target_is_directory=True)
        def unavailable(path):
            raise AttributeError('Python 3.10 has no pathlib.is_junction')
        # A descriptor that raises makes hasattr(instance, ...) false, matching
        # Python 3.10; no process-wide class deletion or OS version assumption.
        with patch.object(Path, 'is_junction', new=property(unavailable), create=True):
            self.assertFalse(hasattr(link, 'is_junction'))
            for tool, data in [('Read', {'file_path': str(link / 'outside.txt')}),
                               ('Glob', {'path': str(self.root), 'pattern': '**/*'}),
                               ('Grep', {'path': str(self.root), 'pattern': 'SYNTHETIC'})]:
                with self.subTest(tool=tool), self.assertRaises(boundary.BoundaryUnavailable):
                    self.check(tool, **data)
        self.assertEqual((self.project / 'outside.txt').read_text(encoding='utf-8'), 'SYNTHETIC_OUTSIDE_SENTINEL')

    def test_internal_json_receipt_has_no_io_and_remains_usable(self):
        self.assertTrue(self.check('StructuredOutput', summary='synthetic', evidence_refs=['result.md']))
        with self.assertRaises(boundary.BoundaryUnavailable):
            self.check('StructuredOutput', summary='synthetic', evidence_refs=[], command='run code')

    def test_missing_or_root_write_allowlist_is_rejected(self):
        for paths in (None, [], ['.'], ['../'], ['C:/data'], ['.env']):
            with self.subTest(paths=paths), self.assertRaises(boundary.BoundaryUnavailable):
                boundary.prepare('codex', ['codex', 'exec', '-'], self.root, self.project, self.job, allowed_paths=paths)

    def test_codex_profile_denies_external_files_network_and_shell_secrets(self):
        with patch.dict(os.environ, {'IEUM_SYNTHETIC_SECRET': 'synthetic-credential',
                                     'OPENAI_API_KEY': 'synthetic-core-auth', 'NODE_OPTIONS': '--require=bad.js'}):
            prepared = boundary.prepare('codex', ['codex', 'exec', '--approve-for-me', '-'],
                self.root, self.project, self.job, allowed_paths=['src'])
        argv = prepared['argv']
        self.assertNotIn('--approve-for-me', argv)
        self.assertIn('--ignore-user-config', argv)
        self.assertIn('--strict-config', argv)
        settings = [argv[i + 1] for i, value in enumerate(argv) if value == '-c']
        profile = next(value for value in settings if value.startswith('permissions.ieum-worker='))
        self.assertIn('":root"="deny"', profile)
        self.assertIn('":minimal"="read"', profile)
        self.assertIn('network={enabled=false}', profile)
        self.assertIn('approval_policy="never"', settings)
        self.assertIn('shell_environment_policy.inherit="none"', settings)
        shell = next(value for value in settings if value.startswith('shell_environment_policy.set='))
        self.assertNotIn('synthetic-core-auth', shell)
        self.assertNotIn('synthetic-credential', str(prepared))
        self.assertNotIn('NODE_OPTIONS', prepared['env'])
        self.assertEqual(prepared['env']['OPENAI_API_KEY'], 'synthetic-core-auth')
        self.assertEqual(argv[-1], '-')

    def test_claude_trusted_exec_hook_cannot_be_edited_and_blocks_hook_failure(self):
        prepared = self.claude()
        contract = prepared['contract']
        self.assertFalse(Path(contract['settings_path']).is_relative_to(self.root))
        settings = json.loads(Path(contract['settings_path']).read_text(encoding='utf-8'))
        hook = settings['hooks']['PreToolUse'][0]['hooks'][0]
        self.assertEqual(hook['onFailure'], 'block')
        self.assertEqual(hook['command'], sys.executable)
        self.assertIn('args', hook)
        self.assertNotIn('shell', hook)
        self.assertIn('--restricted', prepared['argv'])
        self.assertEqual(prepared['env']['CLAUDE_CODE_DISABLE_FILE_MENTIONS'], '1')
        with self.assertRaises(boundary.BoundaryUnavailable):
            self.check('Write', file_path=contract['hook_entry'])
        command = [hook['command']] + hook['args']
        success = subprocess.run(command, input=json.dumps(self.event('Write', file_path=str(self.root / 'src/code.py'))),
                                 capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(success.returncode, 0, success.stderr)
        denied = subprocess.run(command, input=json.dumps(self.event('Read', file_path=str(self.project / 'outside.txt'))),
                                capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(denied.returncode, 2)
        self.assertNotIn('SYNTHETIC_OUTSIDE_SENTINEL', denied.stdout + denied.stderr)
        malformed = subprocess.run(command, input='{bad json', capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(malformed.returncode, 2)
        Path(contract['hook_policy']).write_text('{invalid', encoding='utf-8')
        invalid = subprocess.run(command, input='{}', capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(invalid.returncode, 2)

    def test_old_or_missing_claude_fails_closed_before_model(self):
        for version in ('2.1.80', '2.1.298', '2.1.299', '2.1.326', 'not-a-version'):
            with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, version, '')):
                with self.subTest(version=version), self.assertRaises(boundary.BoundaryUnavailable):
                    boundary._claude_version(['fake-claude'])
        with patch.object(subprocess, 'run', side_effect=FileNotFoundError):
            with self.assertRaises(boundary.BoundaryUnavailable):
                boundary._claude_version(['missing-claude'])

    def test_shell_control_characters_are_plain_exec_arguments(self):
        # The paths are actual Windows-valid metacharacters. No shell quoting or
        # string-built command can reinterpret them as command substitution.
        awkward = self.base / "job' $(SYNTHETIC) & literal"
        awkward.mkdir()
        root = awkward / 'candidate'
        root.mkdir()
        with patch.object(boundary, '_claude_version', return_value=(2, 1, 327)):
            prepared = boundary.prepare('claude', ['fake', '--tools', 'Read,Glob,Grep,Write,Edit',
                '--allowedTools', 'Read,Glob,Grep,Write,Edit'], root, self.project, awkward,
                executable=['fake'], allowed_paths=['out.md'])
        settings = json.loads(Path(prepared['contract']['settings_path']).read_text(encoding='utf-8'))
        hook = settings['hooks']['PreToolUse'][0]['hooks'][0]
        done = subprocess.run([hook['command']] + hook['args'],
            input=json.dumps(self.event('Write', file_path=str(root / 'out.md'))),
            capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(done.returncode, 0, done.stderr)


if __name__ == '__main__':
    unittest.main()
