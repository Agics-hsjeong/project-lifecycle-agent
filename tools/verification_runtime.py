"""Supervisor-approved, lock-bound dependencies for network-isolated verification.

Registration copies an explicitly selected installed environment, never installs
model-selected packages or executes package lifecycle scripts. The opaque runtime
reference names only a confined, immutable inventory outside the customer tree.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.storage import atomic_write, confined, json_bytes

MAX_BYTES = 2 * 1024 * 1024 * 1024
MAX_FILES = 60000


class RuntimeUnavailable(ValueError):
    pass


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _host():
    return {"os": sys.platform, "machine": platform.machine().lower(),
            "python": list(sys.version_info[:2])}


def _registry(project):
    root = Path(project).resolve()
    if not (root / "project.json").is_file():
        raise RuntimeUnavailable("런타임을 연결할 실제 이음 프로젝트가 필요합니다.")
    return confined(root.parent, ".ieum-runtime/" + root.name + "/runtimes")


def _inventory(root, *, skip_transient=False):
    root = Path(root).resolve()
    files, total = {}, 0
    if not root.is_dir():
        raise RuntimeUnavailable("승인된 의존성 폴더가 없습니다.")
    for current, directories, names in os.walk(root, followlinks=False):
        directories[:] = [n for n in directories if not skip_transient or n not in {"__pycache__", ".git", ".cache"}]
        for name in directories:
            entry = Path(current) / name
            attributes = getattr(entry.lstat(), "st_file_attributes", 0)
            if entry.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise RuntimeUnavailable("의존성 디렉터리 링크는 승인할 수 없습니다.")
        for name in names:
            if skip_transient and name.endswith((".pyc", ".pyo")):
                continue
            entry = Path(current) / name
            relative = entry.relative_to(root).as_posix()
            resolved = entry.resolve()
            if not resolved.is_relative_to(root) or not resolved.is_file():
                raise RuntimeUnavailable("의존성 파일이 승인 폴더 밖을 가리킵니다.")
            size = resolved.stat().st_size
            if len(files) >= MAX_FILES or size > MAX_BYTES - total:
                raise RuntimeUnavailable("승인 런타임의 파일·크기 한도를 초과했습니다.")
            total += size
            with resolved.open("rb") as handle:
                digest = hashlib.sha256()
                remaining = size
                while remaining:
                    data = handle.read(min(1024 * 1024, remaining))
                    if not data:
                        raise RuntimeUnavailable("의존성 파일이 읽는 동안 바뀌었습니다.")
                    digest.update(data)
                    remaining -= len(data)
                if handle.read(1):
                    raise RuntimeUnavailable("의존성 파일 크기가 읽는 동안 바뀌었습니다.")
            item = {"sha256": digest.hexdigest(), "bytes": size}
            if entry.is_symlink():
                item["link"] = os.path.relpath(resolved, entry.parent).replace(os.sep, "/")
            files[relative] = item
    return files


def _copy_inventory(source, destination, inventory):
    source, destination = Path(source).resolve(), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    for relative, item in inventory.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if "link" in item:
            os.symlink(item["link"], target)
        else:
            shutil.copy2(source / relative, target)
    if _inventory(destination) != inventory:
        raise RuntimeUnavailable("의존성 복사 후 무결성이 일치하지 않습니다.")


def register(project, *, kind, lockfile, packages, node=None, npm=None, browser=None):
    """Explicit trusted host action. A worker cannot register its own runtime."""
    project = Path(project).resolve()
    if kind not in {"python", "node"}:
        raise RuntimeUnavailable("지원하는 런타임은 python 또는 node입니다.")
    lockpath = confined(project, lockfile)
    if not lockpath.is_file() or lockpath.stat().st_size > 16 * 1024 * 1024:
        raise RuntimeUnavailable("프로젝트의 실제 의존성 잠금 파일이 필요합니다.")
    packages = Path(packages).resolve()
    inventory = _inventory(packages, skip_transient=True)
    if not inventory:
        raise RuntimeUnavailable("빈 의존성 환경은 등록할 수 없습니다.")
    base = _registry(project)
    base.mkdir(parents=True, exist_ok=True)
    staging = base / (".prepare-" + uuid.uuid4().hex)
    staging.mkdir()
    try:
        _copy_inventory(packages, staging / "packages", inventory)
        descriptor = {"schema_version": 1, "kind": kind, "host": _host(),
                      "lockfile": lockfile, "lock_sha256": _sha(lockpath.read_bytes())}
        if kind == "node":
            binary = Path(node or shutil.which("node") or "").resolve()
            if not binary.is_file() or binary.suffix.lower() not in ({".exe"} if os.name == "nt" else {"", ".exe"}):
                raise RuntimeUnavailable("승인할 Node 실행 파일이 필요합니다.")
            (staging / "bin").mkdir()
            shutil.copy2(binary, staging / "bin" / binary.name)
            descriptor["node"] = "bin/" + binary.name
            npm_source = Path(npm).resolve() if npm else binary.parent / "node_modules/npm"
            if not npm and not npm_source.is_dir():
                npm_launcher = shutil.which("npm")
                if npm_launcher and Path(npm_launcher).resolve().name in {"npm-cli.js", "npm.js"}:
                    npm_source = Path(npm_launcher).resolve().parent.parent
            if not (npm_source / "bin/npm-cli.js").is_file():
                raise RuntimeUnavailable("npm-cli.js가 포함된 승인 npm 폴더가 필요합니다.")
            _copy_inventory(npm_source, staging / "npm", _inventory(npm_source, skip_transient=True))
            if browser:
                browser_binary = Path(browser).resolve()
                if not browser_binary.is_file():
                    raise RuntimeUnavailable("승인할 브라우저 실행 파일이 없습니다.")
                _copy_inventory(browser_binary.parent, staging / "browser", _inventory(browser_binary.parent, skip_transient=True))
                descriptor["browser"] = "browser/" + browser_binary.name
        descriptor["files"] = _inventory(staging)
        reference = _sha(json_bytes(descriptor))
        target = base / reference
        if target.exists():
            _load(project, reference)
        else:
            atomic_write(staging / "runtime.json", json_bytes(descriptor))
            staging.rename(target)
        return {"ready": True, "runtime_ref": reference, "kind": kind,
                "lockfile": lockfile, "lock_sha256": descriptor["lock_sha256"],
                "files": len(descriptor["files"]), "host": descriptor["host"]}
    finally:
        if staging.exists():
            # This exact fresh staging directory is owned by this call.
            if staging.parent.resolve() != base.resolve() or not staging.name.startswith(".prepare-"):
                raise RuntimeUnavailable("런타임 정리 경계가 잘못되었습니다.")
            shutil.rmtree(staging)


def _load(project, reference):
    if not isinstance(reference, str) or not re.fullmatch(r"[0-9a-f]{64}", reference):
        raise RuntimeUnavailable("승인된 runtime_ref가 필요합니다.")
    base = _registry(project)
    root = confined(base, reference)
    path = root / "runtime.json"
    if not path.is_file() or path.stat().st_size > 32 * 1024 * 1024:
        raise RuntimeUnavailable("등록된 검증 환경이 없습니다. 에이전트가 잠금 파일 환경을 준비해야 합니다.")
    descriptor = json.loads(path.read_text(encoding="utf-8"))
    if descriptor.get("schema_version") != 1 or _sha(json_bytes(descriptor)) != reference:
        raise RuntimeUnavailable("검증 환경 등록 정보의 무결성이 바뀌었습니다.")
    if descriptor.get("host") != _host():
        raise RuntimeUnavailable("검증 환경의 OS·아키텍처·Python 버전이 현재 호스트와 다릅니다.")
    lockpath = confined(project, descriptor["lockfile"])
    if not lockpath.is_file() or _sha(lockpath.read_bytes()) != descriptor["lock_sha256"]:
        raise RuntimeUnavailable("잠금 파일이 변경됐습니다. 새 검증 환경을 준비하세요.")
    actual = _inventory(root)
    actual.pop("runtime.json", None)
    if actual != descriptor["files"]:
        raise RuntimeUnavailable("승인된 검증 의존성의 바이트가 변경됐습니다.")
    return root, descriptor


PYTHON_BOOTSTRAP = """import json,os,runpy,sys
if sys.platform=='win32':
 import socket
 def _ieum_network_failfast(event,args):
  if event in {'socket.bind','socket.connect'} and args and isinstance(args[0],socket.socket) and args[0].family in {socket.AF_INET,socket.AF_INET6}:
   raise PermissionError('IEUM_ENVIRONMENT_UNAVAILABLE: Windows network-isolated verification forbids TCP/UDP, including asyncio loopback self-pipes; use a compatible isolated runtime or explicitly separate local ASGI validation.')
 sys.addaudithook(_ieum_network_failfast)
package,candidate,args=json.loads(sys.argv[1])
sys.path[:0]=[candidate,package]
if not args: raise SystemExit('A registered Python check is required')
if args[0]=='-m':
 sys.argv=args[1:];runpy.run_module(args[1],run_name='__main__',alter_sys=True)
elif args[0]=='-c':
 sys.argv=['-c']+args[2:];exec(compile(args[1],'<registered-check>','exec'))
else:
 sys.argv=args;runpy.run_path(args[0],run_name='__main__')
"""


def preflight(execution, project):
    reference = execution.get("runtime_ref")
    if reference:
        _, descriptor = _load(project, reference)
        return {"ready": True, "kind": descriptor["kind"], "runtime_ref": reference}
    for argv in execution.get("verification_commands", []):
        name = Path(argv[0]).name.lower()
        needs_runtime = name in {"node", "node.exe", "npm", "npm.cmd", "npx", "npx.cmd", "pytest", "pytest.exe", "tsc"}
        if name in {"python", "python3", "python.exe", Path(sys.executable).name.lower()} and len(argv) > 2 and argv[1] == "-m":
            module = argv[2].split('.')[0]
            if module not in sys.stdlib_module_names and not (Path(project) / (module + '.py')).is_file() and not (Path(project) / module).is_dir():
                needs_runtime = True
        if needs_runtime:
            raise RuntimeUnavailable("외부 의존성 검증에는 잠금 파일로 승인한 runtime_ref가 필요합니다. 에이전트가 먼저 환경을 준비해야 합니다.")
    return {"ready": True, "kind": "stdlib"}


def prepare(argv, candidate_root, execution, scratch, *, project=None):
    args = list(argv)
    reference = execution.get("runtime_ref")
    if not reference:
        preflight({"verification_commands": [args]}, project or candidate_root)
        return {"argv": args, "env": {}, "read_paths": [], "fingerprint": None}
    if project is None:
        raise RuntimeUnavailable("승인된 검증 환경을 연결할 프로젝트가 필요합니다.")
    candidate_root = Path(candidate_root).resolve()
    scratch = Path(scratch).resolve()
    if not scratch.is_relative_to(candidate_root / ".ieum-verification"):
        raise RuntimeUnavailable("검증 임시 출력은 감독자 scratch 안에 있어야 합니다.")
    scratch.mkdir(parents=True, exist_ok=True)
    runtime, descriptor = _load(project, reference)
    candidate_lock = confined(candidate_root, descriptor["lockfile"])
    if not candidate_lock.is_file() or _sha(candidate_lock.read_bytes()) != descriptor["lock_sha256"]:
        raise RuntimeUnavailable("작업 사본의 잠금 파일이 승인된 환경과 다릅니다. 입력 manifest와 의존성 변경을 확인하세요.")
    environment = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
    result = {"env": environment, "read_paths": [str(runtime)],
              "fingerprint": {"runtime_ref": reference, "kind": descriptor["kind"],
                              "lock_sha256": descriptor["lock_sha256"], "host": descriptor["host"]},
              "_project": str(Path(project).resolve()), "_reference": reference}
    if descriptor["kind"] == "python":
        if Path(args[0]).name.lower() not in {"python", "python3", "python.exe", Path(sys.executable).name.lower()}:
            raise RuntimeUnavailable("Python 검증 환경은 등록된 Python 명령에만 연결합니다.")
        if len(args) < 2 or args[1].startswith('-') and args[1] not in {'-m', '-c'}:
            raise RuntimeUnavailable("Python 검증 명령은 -m, -c 또는 스크립트 경로가 필요합니다.")
        result["argv"] = [sys.executable, "-I", "-S", "-B", "-c", PYTHON_BOOTSTRAP,
                          json.dumps([str(runtime / "packages"), str(candidate_root), args[1:]], ensure_ascii=False)]
    else:
        dependencies = candidate_root / "node_modules"
        package_inventory = {name[len("packages/"):]: item for name,item in descriptor["files"].items() if name.startswith("packages/")}
        if dependencies.exists():
            if dependencies.is_symlink() or _inventory(dependencies) != package_inventory:
                raise RuntimeUnavailable("검증 사본의 의존성 폴더가 승인된 환경과 다릅니다.")
        else:
            _copy_inventory(runtime / "packages", dependencies, package_inventory)
        executable = Path(args[0]).name.lower()
        node = str(runtime / descriptor["node"])
        if executable in {"npm", "npm.cmd", "npm.exe"}:
            if len(args) < 2 or args[1] not in {"run", "test"}:
                raise RuntimeUnavailable("격리 검증에서 npm 설치·다운로드 명령은 실행하지 않습니다.")
            result["argv"] = [node, str(runtime / "npm/bin/npm-cli.js")] + args[1:]
        elif executable in {"node", "node.exe"}:
            result["argv"] = [node] + args[1:]
        else:
            raise RuntimeUnavailable("Node 검증 환경은 node 또는 npm run/test 명령에 연결합니다.")
        environment["PATH"] = os.pathsep.join([str(runtime / "bin"), str(candidate_root / "node_modules" / ".bin"),
            str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32")] if os.name == 'nt'
            else [str(runtime / "bin"), str(candidate_root / "node_modules" / ".bin"), '/usr/bin', '/bin'])
        # Windows strict filesystem rules deliberately deny volume ancestors.
        # Node's default realpath walks those ancestors even for confined files.
        # Preserve already-vetted dependency paths instead of granting D:/C: read;
        # NODE_OPTIONS also reaches npm-launched Node/compiler child processes.
        environment.update(NODE_OPTIONS="--preserve-symlinks --preserve-symlinks-main",
                           npm_config_cache=str(scratch / "npm-cache"), npm_config_ignore_scripts="true",
                           npm_config_update_notifier="false", npm_config_audit="false", npm_config_fund="false")
        if descriptor.get("browser"):
            environment["PLAYWRIGHT_EXECUTABLE_PATH"] = str(runtime / descriptor["browser"])
        result["_dependencies"] = str(dependencies)
        result["_dependency_inventory"] = package_inventory
    return result


def assert_unchanged(plan):
    if not plan.get("_reference"):
        return
    _load(Path(plan["_project"]), plan["_reference"])
    if plan.get("_dependencies") and _inventory(Path(plan["_dependencies"])) != plan["_dependency_inventory"]:
        raise RuntimeUnavailable("검증 도중 의존성 바이트가 변경됐습니다.")


def main():
    parser = argparse.ArgumentParser(description="에이전트가 신뢰된 설치 의존성을 검증 환경으로 등록")
    parser.add_argument("project", type=Path)
    parser.add_argument("--kind", choices=("python", "node"), required=True)
    parser.add_argument("--lockfile", required=True)
    parser.add_argument("--packages", type=Path, required=True)
    parser.add_argument("--node", type=Path)
    parser.add_argument("--npm", type=Path)
    parser.add_argument("--browser", type=Path)
    args = parser.parse_args()
    try:
        result = register(args.project, kind=args.kind, lockfile=args.lockfile, packages=args.packages,
                          node=args.node, npm=args.npm, browser=args.browser)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, OSError) as error:
        print(json.dumps({"ready": False, "error": str(error)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
