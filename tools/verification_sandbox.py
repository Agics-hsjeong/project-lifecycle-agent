"""Fail-closed OS-isolated verification. Codex sandbox is a local tool, not a model call."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import stat
import uuid
from zipfile import ZipFile, ZIP_DEFLATED

INTERNAL = '.ieum-verification'
SAFE_ENV = {'PATH', 'PATHEXT', 'SYSTEMROOT', 'SYSTEMDRIVE', 'WINDIR', 'COMSPEC', 'OS', 'PROCESSOR_ARCHITECTURE',
            'NUMBER_OF_PROCESSORS', 'LOCALAPPDATA', 'APPDATA', 'HOME', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH',
            'LANG', 'LC_ALL', 'TZ', 'PROGRAMFILES', 'PROGRAMFILES(X86)', 'PROGRAMDATA'}


class SandboxUnavailable(ValueError):
    pass


def portable_python(runtime, root):
    """Copy public interpreter files, never change host ACLs or copy site credentials."""
    target = runtime / 'python'
    target.mkdir(parents=True, exist_ok=False)
    source = Path(sys.base_prefix)
    version = str(sys.version_info.major) + str(sys.version_info.minor)
    for pattern in ('python*.exe', 'python*.dll', 'vcruntime*.dll'):
        for file in source.glob(pattern):
            if file.is_file():
                shutil.copyfile(file, target / file.name)
    if not (target / Path(sys.executable).name).is_file():
        shutil.copyfile(sys.executable, target / Path(sys.executable).name)
    for name in ('LICENSE.txt', 'LICENSE'):
        if (source / name).is_file():
            shutil.copyfile(source / name, target / name)
    dlls = target / 'DLLs'
    dlls.mkdir()
    for pattern in ('*.pyd', '*.dll'):
        for file in (source / 'DLLs').glob(pattern):
            shutil.copyfile(file, dlls / file.name)
    excluded = {'site-packages', '__pycache__', 'test', 'tests', 'idlelib', 'ensurepip', 'tkinter', 'turtledemo'}
    with ZipFile(target / ('python' + version + '.zip'), 'w', compression=ZIP_DEFLATED, compresslevel=1) as archive:
        library = source / 'Lib'
        for current, directories, names in os.walk(library):
            directories[:] = [name for name in directories if name not in excluded]
            for name in names:
                if name.endswith('.py'):
                    file = Path(current) / name
                    archive.write(file, file.relative_to(library).as_posix())
    (target / ('python' + version + '._pth')).write_text('python' + version + '.zip\nDLLs\n' + str(root) + '\n', encoding='utf-8')
    return target / Path(sys.executable).name


def _filesystem_rules(root, project, runtime, selected, *, platform=None):
    platform = sys.platform if platform is None else platform
    # Bubblewrap starts from an empty, default-denied filesystem. Redundant
    # nested deny mounts below its read-only ancestor masks can fail during
    # setup. Expose only the copy and public runtime there; keep explicit
    # home/project denies on the native Windows and macOS backends.
    filesystem = {':root': 'deny', ':minimal': 'read',
                  root.as_posix(): 'write', runtime.as_posix(): 'read'}
    if not platform.startswith('linux'):
        filesystem[Path.home().as_posix()] = 'deny'
        filesystem[project.as_posix()] = 'deny'
    if platform == 'win32':
        filesystem[Path(os.environ.get('SystemRoot', 'C:/Windows')).as_posix()] = 'read'
    else:
        for readable in (Path(sys.base_prefix).resolve(), Path(sys.executable).resolve().parent):
            if platform.startswith('linux') and project.is_relative_to(readable):
                raise SandboxUnavailable('원본 프로젝트가 검증 인터프리터의 읽기 범위 안에 있습니다. 분리된 검증 런타임을 준비하세요.')
            filesystem[readable.as_posix()] = 'read'
    filesystem[selected.as_posix()] = 'read'
    return filesystem


def prepare(argv, root, project, *, executable=None):
    """The returned command writes only its copy. There is deliberately no unsafe fallback."""
    from tools.host_providers import executable_argv
    root, project = Path(root).resolve(), Path(project).resolve()
    if root == project or root.is_relative_to(project):
        raise SandboxUnavailable('검증 사본은 실제 고객 프로젝트 밖에 있어야 합니다.')
    try:
        launcher = executable_argv('codex', executable)
    except ValueError as error:
        raise SandboxUnavailable('격리 검증에 Codex sandbox CLI가 필요합니다. CLI를 연결한 뒤 재시도하세요. 검증은 실행하지 않았습니다.') from error
    args = list(argv)
    internal = root / INTERNAL
    from tools.storage import confined
    confined(root, INTERNAL + '/tmp').mkdir(parents=True, exist_ok=True)
    # Keep the executable below Windows CreateProcess path limits even when
    # project/job paths contain several UUIDs. This fresh owned directory is
    # exposed read-only, never as a writable host temp root.
    runtime = Path(tempfile.mkdtemp(prefix='ieum-v-')).resolve()
    try:
        verifier_python = str(portable_python(runtime, root)) if os.name == 'nt' else sys.executable
        if Path(args[0]).resolve() == Path(sys.executable).resolve():
            args[0] = verifier_python
        selected = Path(shutil.which(args[0]) or args[0]).resolve()
        if selected.is_relative_to(project):
            raise SandboxUnavailable('검증 실행 파일은 원본 프로젝트 안에서 직접 실행하지 않습니다. 사본의 검증 런타임을 준비하세요.')
        marker = confined(root, INTERNAL + '/started-' + uuid.uuid4().hex)
        entrypoint = runtime / 'entrypoint.py'
        entrypoint.write_text(
            'import json,subprocess,sys\nfrom pathlib import Path\n'
            'Path(sys.argv[1]).write_text("started",encoding="utf-8")\n'
            'raise SystemExit(subprocess.call(json.loads(sys.argv[2])))\n', encoding='utf-8')
        sandbox_args = [verifier_python, str(entrypoint), str(marker), json.dumps(args, ensure_ascii=False)]
        filesystem = _filesystem_rules(root, project, runtime, selected)
        profile = 'permissions.ieum-verification={filesystem={' + ','.join(
            json.dumps(k, ensure_ascii=False) + '=' + json.dumps(v) for k, v in filesystem.items()) + '},network={enabled=false}}'
        settings = ['-c', 'windows.sandbox="mxc"', '-c', 'windows.require_mxc=true'] if os.name == 'nt' else []
        command = launcher + settings + ['sandbox', '--include-managed-config', '--permission-profile', 'ieum-verification', '-c', profile,
                                         '-C', str(root), '--'] + sandbox_args
        return command, safe_environment(root), {'backend': 'codex-sandbox-mxc' if os.name == 'nt' else 'codex-sandbox', 'network': False, 'write_root': str(root), 'started_marker': str(marker), 'runtime_root': str(runtime)}
    except BaseException:
        try:
            cleanup({'runtime_root': str(runtime)})
        except (OSError, ValueError):
            pass
        raise


def safe_environment(root):
    from tools.storage import confined
    temporary = confined(root, INTERNAL + '/tmp')
    temporary.mkdir(parents=True, exist_ok=True)
    environment = {k: v for k, v in os.environ.items() if k.upper() in SAFE_ENV}
    environment.update(TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
                       PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    return environment


def cleanup(info):
    """Delete only the fresh public runtime owned by this verification call."""
    value = info.get('runtime_root') if isinstance(info, dict) else None
    if not value:
        return
    runtime = Path(value)
    parent = Path(tempfile.gettempdir()).resolve()
    if runtime.parent.resolve() != parent or not runtime.name.startswith('ieum-v-'):
        raise ValueError('Unexpected verification runtime cleanup target')
    if not runtime.exists():
        return
    attributes = getattr(runtime.lstat(), 'st_file_attributes', 0)
    if runtime.is_symlink() or attributes & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0):
        raise ValueError('Linked verification runtime cannot be cleaned')
    if not runtime.resolve().is_relative_to(parent) or runtime.resolve() == parent:
        raise ValueError('Verification runtime escaped its owned temporary parent')
    shutil.rmtree(runtime)
