"""Publish exact producer-declared outputs without changing their content.

Only the original Windows writer can normalize an inherited OWNER RIGHTS
policy. This is neither a general document repair nor an observer ACL bypass.
"""
import os

from tools.storage import atomic_write, digest, lock

MAX_FILES = 200
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024


def _identity(relative):
    """Unambiguous Windows-portable identity, checked without filesystem I/O."""
    if (not isinstance(relative, str) or not relative
            or any(ord(c) < 32 or ord(c) == 127 or c in '\\:*?"<>|' for c in relative)):
        raise ValueError("명확한 프로젝트 상대 파일 경로가 필요합니다.")
    # A single directory suffix is conventional; repeated suffixes remain aliases.
    parts = (relative[:-1] if relative.endswith("/") else relative).split("/")
    reserved = {"con", "prn", "aux", "nul", *("com" + str(n) for n in range(1, 10)),
                *("lpt" + str(n) for n in range(1, 10))}
    if any(not p or p in {".", ".."} or p.endswith((".", " "))
           or p.split(".", 1)[0].casefold() in reserved for p in parts):
        raise ValueError("별칭 또는 모호한 Windows 파일 경로는 공개하지 않습니다.")
    return "/".join(p.casefold() for p in parts)


def _public_output(relative):
    from tools.sensitive_data import is_sensitive_path
    identity = _identity(relative)
    parts = identity.split("/")
    if (parts[0] in {"state", "inputs", "registers", "templates", "process", ".agents"}
            or identity in {"project.json", "handoff.md", "agents.md", "claude.md"}
            or parts[:2] == ["reports", "workspace-decisions"]
            or set(parts).intersection({".git", ".codex", ".claude", ".aws", ".ssh", ".gnupg", ".azure", ".netrc", ".npmrc", ".pypirc"})
            or any(p.startswith(".env") for p in parts) or is_sensitive_path(identity)):
        raise ValueError("제어 기록·원본 입력·민감 파일은 생성 산출물로 공개하지 않습니다.")
    return identity


def _bounded_read(path):
    with path.open("rb") as stream:
        raw = stream.read(MAX_FILE + 1)
    if not raw or len(raw) > MAX_FILE:
        raise ValueError("생성 산출물 공개 크기 제한을 확인하세요.")
    return raw


def _paths(root, step, refs, *, skip_directories=False):
    from tools.workspace import execution_path
    if (not isinstance(refs, list) or len(refs) > MAX_FILES
            or any(not isinstance(ref, str) for ref in refs)):
        raise ValueError("정확한 산출물 경로 목록이 필요합니다.")
    declarations = step.get("expected_outputs", [])
    identities = [_identity(ref) for ref in declarations]
    if len(set(identities)) != len(identities):
        raise ValueError("동일 파일을 가리키는 중복 출력 선언은 공개하지 않습니다.")
    outputs = set(declarations)
    inputs = [_identity(ref) for ref in step.get("inputs", [])]
    selected = []
    for relative in dict.fromkeys(refs):
        if relative not in outputs:
            continue  # Other-role evidence is referenced, never republished.
        identity = _public_output(relative)
        # Directory inputs also belong to the input owner, not this producer.
        if any(identity == ref or identity.startswith(ref + "/") for ref in inputs):
            raise ValueError("원본 입력을 생성 산출물로 공개하지 않습니다.")
        path = execution_path(root, relative)
        if skip_directories and path.is_dir():
            continue  # Existing directory evidence still uses runs.hashes.
        if relative.endswith("/") or not path.is_file():
            raise ValueError("생성된 일반 파일만 공개할 수 있습니다.")
        selected.append((relative, path))
    return selected


def publish_outputs(root, run, step, refs, *, expected_hashes=None):
    """Caller holds project lock; preflight all files before replacing any.

    Run/step objects must be the engine's current producer record. Ordinary
    result calls publish newly submitted outputs; explicit recovery additionally
    supplies the producer's exact recorded hashes. Automatic directory evidence
    keeps its existing receipt semantics; only exact files receive publication.
    """
    if os.name != "nt":
        return {"content_preserved": True, "files": []}
    from tools.windows_security import OWNER_RIGHTS, effective_user_sid, read_security
    selected = _paths(root, step, refs, skip_directories=expected_hashes is None)
    if not selected:
        return {"content_preserved": True, "files": []}
    anchor = read_security(root)["owner"]
    writer = effective_user_sid()
    prepared, total = [], 0
    for relative, path in selected:
        raw = _bounded_read(path)
        total += len(raw)
        if not raw or len(raw) > MAX_FILE or total > MAX_TOTAL:
            raise ValueError("생성 산출물 공개 크기 제한을 확인하세요.")
        sha = digest(raw)
        if expected_hashes is not None and expected_hashes.get(_identity(relative)) != sha:
            raise ValueError("생성 산출물이 작성자의 기록된 버전과 다릅니다.")
        policy = read_security(path)
        dynamic = any(a["sid"] == OWNER_RIGHTS and a["mask"] for a in policy["aces"])
        repair = policy["owner"] != anchor and dynamic
        if repair and (policy["owner"] != writer or policy["protected"]
                       or any(not a["flags"] & 0x10 for a in policy["aces"])):
            raise ValueError("원 작성자의 상속 정책 산출물만 공개할 수 있습니다.")
        prepared.append((relative, path, raw, sha, repair))
    results = []
    for relative, path, raw, sha, repair in prepared:
        if _bounded_read(path) != raw:
            raise ValueError("공개 준비 이후 산출물이 변경되었습니다.")
        if repair:
            atomic_write(path, raw, security_anchor=root, repair_owner=True)
        if _bounded_read(path) != raw:
            raise ValueError("공개 이후 산출물 바이트가 일치하지 않습니다.")
        results.append({"path": relative, "sha256": sha, "bytes": len(raw),
                        "content_preserved": True, "published": bool(repair)})
    return {"content_preserved": True, "files": results}


def publish_recorded_outputs(project, run_id, step_id, paths):
    """Explicit original-writer recovery; only declared, receipted output bytes."""
    from tools import runs, starter
    if os.name != "nt":
        raise OSError("기록된 산출물 공개 복원은 Windows에서만 사용할 수 있습니다.")
    if (not isinstance(paths, list) or not paths or len(paths) > MAX_FILES
            or any(not isinstance(p, str) for p in paths) or len(set(paths)) != len(paths)):
        raise ValueError("고유한 생성 산출물 경로를 명시하세요.")
    root, _ = starter.load_project(project)
    with lock(root):
        run = next((r for r in runs.read(root)["runs"] if r["id"] == run_id), None)
        step = next((s for s in run["steps"] if s["id"] == step_id), None) if run else None
        if step is None or any(p not in step.get("expected_outputs", []) for p in paths):
            raise ValueError("해당 작성 단계에 정확히 선언된 출력이 필요합니다.")
        expected = {}
        for result in step["results"]:
            record = {}
            for relative, sha in result.get("evidence_hashes", {}).items():
                identity = _identity(relative)
                if identity in record and record[identity] != sha:
                    raise ValueError("같은 제출 기록에 서로 다른 파일 별칭 해시가 있습니다.")
                record[identity] = sha
            expected.update(record)
        if any(_identity(p) not in expected for p in paths):
            raise ValueError("작성 단계의 실제 제출 근거가 필요합니다.")
        return publish_outputs(root, run, step, paths, expected_hashes=expected)
