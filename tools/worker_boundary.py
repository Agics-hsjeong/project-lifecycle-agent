"""Provider tool boundaries for a generation copy; authentication stays in CLI core.

Claude uses a trusted, non-writable PreToolUse guard, not an OS sandbox. Codex
uses an explicit deny-by-default permission profile. Neither has a broad fallback.
"""
import json
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys


class BoundaryUnavailable(ValueError):
    pass


TOOLS = {'Read', 'Glob', 'Grep', 'Write', 'Edit'}
_WIN_RESERVED = {'con', 'prn', 'aux', 'nul', 'conin$', 'conout$'} | {f'{stem}{n}' for stem in ('com', 'lpt') for n in range(1, 10)}
MIN_CLAUDE = (2, 1, 327)
CORE_ENV = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'OS',
            'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'HOME', 'USERPROFILE',
            'HOMEDRIVE', 'HOMEPATH', 'APPDATA', 'LOCALAPPDATA', 'PROGRAMDATA',
            'PROGRAMFILES', 'PROGRAMFILES(X86)', 'LANG', 'LC_ALL', 'TZ',
            'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'CLAUDE_CODE_GIT_BASH_PATH',
            'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'ANTHROPIC_API_KEY',
            'ANTHROPIC_BASE_URL', 'CLAUDE_CODE_OAUTH_TOKEN'}
SHELL_ENV = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC',
             'OS', 'PROCESSOR_ARCHITECTURE', 'NUMBER_OF_PROCESSORS', 'LANG', 'LC_ALL', 'TZ'}


def _relative(value):
    if not isinstance(value, str) or not value or '\0' in value:
        raise BoundaryUnavailable('허용 경로는 비어 있지 않은 상대 경로여야 합니다.')
    value = value.replace('\\', '/')
    path = PurePosixPath(value)
    if path.is_absolute() or ':' in value or any(p in {'', '.', '..'} for p in value.split('/')):
        raise BoundaryUnavailable('루트·상위·장치 경로는 작업 범위에 사용할 수 없습니다.')
    from tools.sensitive_data import is_sensitive_path
    if is_sensitive_path(value):
        raise BoundaryUnavailable('민감한 경로는 작업 범위에 사용할 수 없습니다.')
    return path.as_posix()


def _is_link(path):
    """Reject reparse points even before pathlib.is_junction (Python 3.12)."""
    if path.is_symlink():
        return True
    try:
        attributes = getattr(path.lstat(), 'st_file_attributes', 0)
    except FileNotFoundError:
        # File tools may create a new owned file or missing parent directory.
        return False
    return bool(attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0))


def _no_links(root, target, *, recursive=False):
    # Resolve is necessary but insufficient: a link pointing back inside can be
    # retargeted. Reject all links/junctions in model-visible paths instead.
    target = Path(target)
    current = target
    while current != root:
        if _is_link(current):
            raise BoundaryUnavailable('심볼릭 링크와 정션은 작업자 경로에 허용하지 않습니다.')
        if current == current.parent:
            raise BoundaryUnavailable('작업 경로가 사본 밖입니다.')
        current = current.parent
    if recursive and target.is_dir():
        for folder, directories, files in os.walk(target, followlinks=False):
            for name in directories + files:
                item = Path(folder) / name
                if _is_link(item):
                    raise BoundaryUnavailable('검색 대상에 링크가 있어 작업을 차단했습니다.')


def _tool_path(root, value):
    if not isinstance(value, str) or not value or any(ord(c) < 32 for c in value):
        raise BoundaryUnavailable('도구 경로가 잘못되었습니다.')
    normalized = value.replace('\\', '/')
    if normalized.startswith(('//', '~')) or '://' in normalized or re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', normalized) and not re.match(r'^[A-Za-z]:/', normalized):
        raise BoundaryUnavailable('URI·UNC·사용자 홈 경로는 허용하지 않습니다.')
    if any(p == '..' or p.endswith((' ', '.')) and p != '.' or p.split('.')[0].casefold() in _WIN_RESERVED for p in normalized.split('/')):
        raise BoundaryUnavailable('상위 경로 탐색은 허용하지 않습니다.')
    if os.name != 'nt' and ntpath.splitdrive(value)[0]:
        raise BoundaryUnavailable('다른 운영체제의 절대 경로는 허용하지 않습니다.')
    # Colon after drive letter is an NTFS alternate data stream/device spelling.
    tail = normalized[2:] if re.match(r'^[A-Za-z]:', normalized) else normalized
    if ':' in tail:
        raise BoundaryUnavailable('장치·스트림 경로는 허용하지 않습니다.')
    target = Path(normalized)
    target = target if target.is_absolute() else root / target
    if not target.is_relative_to(root) or not target.resolve().is_relative_to(root):
        raise BoundaryUnavailable('도구 접근은 작업 사본 안으로 제한됩니다.')
    _no_links(root, target)
    from tools.sensitive_data import is_sensitive_path
    if is_sensitive_path(target.relative_to(root).as_posix()):
        raise BoundaryUnavailable('민감한 경로에는 접근할 수 없습니다.')
    return target


def check_tool(config, event):
    """Validate the actual provider tool request; no model-authored claims trusted."""
    if not isinstance(config, dict) or config.get('version') != 1:
        raise BoundaryUnavailable('작업자 경계 설정이 잘못되었습니다.')
    root = Path(config['candidate_root']).resolve(strict=True)
    allowed = config.get('allowed_paths')
    if not isinstance(allowed, list) or not allowed:
        raise BoundaryUnavailable('쓰기 범위가 없습니다.')
    allowed = [_relative(value) for value in allowed]
    if not isinstance(event, dict) or event.get('hook_event_name') != 'PreToolUse':
        raise BoundaryUnavailable('지원하는 도구 이벤트가 아닙니다.')
    tool, data = event.get('tool_name'), event.get('tool_input')
    if tool == 'StructuredOutput' and isinstance(data, dict):
        # The provider's JSON-schema response has no filesystem/network effect.
        # Host receipt validation and evidence ownership still run afterwards.
        if set(data) == {'summary', 'evidence_refs'} and isinstance(data['summary'], str) and isinstance(data['evidence_refs'], list) and all(isinstance(value, str) for value in data['evidence_refs']):
            return True
        raise BoundaryUnavailable('구조화 응답이 지정된 receipt 형식과 다릅니다.')
    if tool not in TOOLS or not isinstance(data, dict):
        raise BoundaryUnavailable('허용되지 않은 도구입니다.')
    if tool in {'Read', 'Write', 'Edit'}:
        target = _tool_path(root, data.get('file_path'))
    else:
        target = _tool_path(root, data.get('path') or str(root))
        _no_links(root, target, recursive=True)
        # Glob searches with an absolute/parent pattern can ignore the path root.
        for key in ('pattern', 'glob') if tool == 'Glob' else ('glob',):
            value = data.get(key)
            if value is not None:
                if not isinstance(value, str) or '\0' in value:
                    raise BoundaryUnavailable('검색 패턴이 잘못되었습니다.')
                normalized = value.replace('\\', '/')
                if normalized.startswith(('/', '~')) or ':' in normalized or any(p == '..' or p.endswith((' ', '.')) and p != '.' for p in normalized.split('/')):
                    raise BoundaryUnavailable('검색 패턴으로 사본 밖을 탐색할 수 없습니다.')
    if tool in {'Write', 'Edit'}:
        rel = target.relative_to(root).as_posix()
        if not any(rel == path or rel.startswith(path.rstrip('/') + '/') for path in allowed):
            raise BoundaryUnavailable('쓰기 권한이 없는 산출물 경로입니다.')
    return True


def hook_main(config_path):
    try:
        path = Path(config_path)
        if path.is_symlink() or path.stat().st_size > 65536:
            raise BoundaryUnavailable('작업자 경계 설정이 잘못되었습니다.')
        config = json.loads(path.read_text(encoding='utf-8'))
        raw = sys.stdin.buffer.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise BoundaryUnavailable('도구 이벤트가 너무 큽니다.')
        check_tool(config, json.loads(raw))
    except Exception:
        # No secret path, raw input, or tool payload is echoed into native logs.
        print('IEUM_WORKER_BOUNDARY_DENIED: tool access is outside the candidate/owned outputs.', file=sys.stderr)
        return 2
    return 0


def _claude_version(cmd):
    try:
        response = subprocess.run(list(cmd) + ['--version'], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  timeout=15, text=True, encoding='utf-8', errors='replace', shell=False)
        match = re.search(r'\b(\d+)\.(\d+)\.(\d+)\b', response.stdout)
        version = tuple(map(int, match.groups())) if response.returncode == 0 and match else ()
    except (OSError, subprocess.TimeoutExpired):
        version = ()
    if version < MIN_CLAUDE:
        raise BoundaryUnavailable('안전한 훅 실패 차단과 파일 자동삽입 방지에는 Claude Code 2.1.327 이상이 필요합니다. 실행하지 않았습니다.')
    return version


def _toml_table(values):
    return '{' + ','.join(json.dumps(k, ensure_ascii=False) + '=' + json.dumps(v, ensure_ascii=False) for k, v in values.items()) + '}'


def prepare(provider, cmd, candidate_root, project, job_dir, *, executable=None, allowed_paths=None):
    root, project, job = Path(candidate_root).resolve(), Path(project).resolve(), Path(job_dir).resolve()
    if provider not in {'codex', 'claude'}:
        raise BoundaryUnavailable('지원하지 않는 작업자입니다.')
    if root == project or root.is_relative_to(project) or project.is_relative_to(root):
        raise BoundaryUnavailable('작업 사본은 원본 프로젝트 밖으로 분리해야 합니다.')
    if job == root or job.is_relative_to(root):
        raise BoundaryUnavailable('신뢰 설정은 작업 사본 밖에 있어야 합니다.')
    if not isinstance(allowed_paths, list) or not allowed_paths:
        raise BoundaryUnavailable('명시적인 쓰기 범위가 필요합니다.')
    allowed_paths = [_relative(value) for value in allowed_paths]
    if not isinstance(cmd, (list, tuple)) or not cmd or any(not isinstance(x, str) or '\0' in x for x in cmd):
        raise BoundaryUnavailable('작업자 실행 argv가 잘못되었습니다.')
    _no_links(root, root, recursive=True)
    env = {k: v for k, v in os.environ.items() if k.upper() in CORE_ENV}
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    command = list(cmd)
    contract = {'version': 1, 'provider': provider, 'read_root': str(root), 'network_tools': False,
                'allowed_paths': allowed_paths, 'authentication': 'native-cli-core'}
    if provider == 'codex':
        # --approve-for-me selects legacy workspace-write and would disable the
        # explicit profile. Ignore personal settings/rules, never managed policy.
        command = [value for value in command if value != '--approve-for-me']
        if any(value in command for value in ('--sandbox', '-s', '--dangerously-bypass-approvals-and-sandbox')):
            raise BoundaryUnavailable('작업자 경계를 바꾸는 실행 옵션은 허용하지 않습니다.')
        # Prefer the public OS shell over an app/user-installed shell whose
        # home directory is deliberately denied to model tools. Resolve the CLI
        # launcher first; its core authentication remains outside tool sandbox.
        resolved_cli = shutil.which(command[0])
        if resolved_cli:
            command[0] = str(Path(resolved_cli).resolve())
        if os.name == 'nt':
            system = Path(os.environ.get('SystemRoot', 'C:/Windows')).resolve()
            public_path = [str(system / 'System32'), str(system / 'System32/WindowsPowerShell/v1.0'), str(system)]
            for value in env.get('PATH', '').split(os.pathsep):
                if not value:
                    continue
                path = Path(value).resolve()
                if not path.is_relative_to(Path.home().resolve()) and not path.is_relative_to(project) and str(path) not in public_path:
                    public_path.append(str(path))
            env['PATH'] = os.pathsep.join(public_path)
        fs = {':root': 'deny', ':minimal': 'read', root.as_posix(): 'write'}
        if os.name == 'nt':
            fs[Path(os.environ.get('SystemRoot', 'C:/Windows')).as_posix()] = 'read'
            fs[Path.home().as_posix()] = 'deny'
            fs[project.as_posix()] = 'deny'
        else:
            for value in ('/usr', '/bin', '/sbin', '/lib', '/lib64'):
                if Path(value).exists():
                    if project.is_relative_to(Path(value)):
                        raise BoundaryUnavailable('원본 프로젝트가 공개 런타임 경로 아래에 있습니다.')
                    fs[value] = 'read'
        settings = {'default_permissions': 'ieum-worker', 'permissions.ieum-worker': None,
                    'approval_policy': 'never', 'allow_login_shell': False, 'shell_environment_policy.inherit': 'none',
                    'shell_environment_policy.ignore_default_excludes': False,
                    'web_search': 'disabled', 'apps._default.enabled': False,
                    'plugins._default.enabled': False, 'agents.enabled': False,
                    'memories.use_memories': False, 'project_doc_max_bytes': 0}
        extra = ['--ignore-user-config', '--ignore-rules', '--strict-config']
        for key, value in settings.items():
            if value is not None:
                extra += ['-c', key + '=' + json.dumps(value)]
        extra += ['-c', 'permissions.ieum-worker={filesystem=' + _toml_table(fs) + ',network={enabled=false}}']
        shell = {k: v for k, v in env.items() if k.upper() in SHELL_ENV}
        shell.update(HOME=str(root), USERPROFILE=str(root), TMP=str(root / '.ieum-worker-tmp'),
                     TEMP=str(root / '.ieum-worker-tmp'), TMPDIR=str(root / '.ieum-worker-tmp'))
        (root / '.ieum-worker-tmp').mkdir(exist_ok=True)
        extra += ['-c', 'shell_environment_policy.set=' + _toml_table(shell)]
        if os.name == 'nt':
            extra += ['-c', 'windows.sandbox="mxc"']
        # Arguments after a standalone '-' belong to exec's prompt, so insert
        # settings before it rather than after arbitrary prompt text.
        index = command.index('exec') + 1 if 'exec' in command else len(command)
        command[index:index] = extra
        contract.update(backend='codex-permission-profile', default_permissions='ieum-worker', shell_secrets=False)
    else:
        if any(value in command for value in ('--bare', '--safe-mode', '--dangerously-skip-permissions', '--add-dir', '--plugin-dir', '--settings')):
            raise BoundaryUnavailable('작업자 경계를 바꾸는 Claude 옵션은 허용하지 않습니다.')
        for flag in ('--tools', '--allowedTools'):
            if flag not in command or set(command[command.index(flag) + 1].split(',')) != TOOLS:
                raise BoundaryUnavailable('Claude 작업자는 허용된 다섯 파일 도구만 사용할 수 있습니다.')
        from tools.host_providers import executable_argv
        launcher = executable_argv('claude', executable)
        version = _claude_version(launcher)
        trusted = job / 'worker-boundary'
        trusted.mkdir(parents=True, exist_ok=False)
        config = trusted / 'policy.json'
        config.write_text(json.dumps({'version': 1, 'candidate_root': str(root), 'allowed_paths': allowed_paths}), encoding='utf-8')
        script = trusted / 'guard.py'
        script.write_text(Path(__file__).read_text(encoding='utf-8'), encoding='utf-8')
        # The copied script imports only sensitive_data from this trusted copy.
        package = trusted / 'tools'
        package.mkdir()
        (package / '__init__.py').write_text('', encoding='utf-8')
        shutil.copyfile(Path(__file__).with_name('sensitive_data.py'), package / 'sensitive_data.py')
        entry = trusted / 'launch.py'
        entry.write_text('import runpy,sys\nfrom pathlib import Path\nsys.path.insert(0,str(Path(__file__).parent))\nrunpy.run_path(str(Path(__file__).with_name("guard.py")),run_name="__main__")\n', encoding='utf-8')
        settings = trusted / 'settings.json'
        settings.write_text(json.dumps({'disableAllHooks': False, 'hooks': {'PreToolUse': [{
            'matcher': '.*', 'hooks': [{'type': 'command', 'command': sys.executable,
                'args': ['-I', str(entry), '--hook', str(config)], 'timeout': 10, 'onFailure': 'block'}]}]}}), encoding='utf-8')
        command += ['--restricted', '--setting-sources', '', '--settings', str(settings), '--disable-slash-commands', '--include-hook-events']
        env['CLAUDE_CODE_DISABLE_FILE_MENTIONS'] = '1'
        contract.update(backend='claude-pretooluse-guard', settings_path=str(settings),
                        hook_policy=str(config), hook_entry=str(entry), cli_version='.'.join(map(str, version)))
    return {'argv': command, 'env': env, 'contract': contract}


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--hook':
        raise SystemExit(hook_main(sys.argv[2]))
    raise SystemExit(2)
