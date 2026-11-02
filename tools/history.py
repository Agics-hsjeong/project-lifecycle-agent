"""Durable engine edits and conflict-checked undo; never rewrites audit history."""
import base64
import json
import re
from tools.storage import commit, confined, consistent_read, digest, lock


def read(root, revision):
    if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{32}", revision):
        raise ValueError("유효한 변경 revision이 필요합니다.")
    path = confined(root, f"state/history/{revision}.json")
    if not path.is_file():
        raise ValueError("보관된 변경 내용이 없습니다. 이전 버전은 외부 Git/백업을 확인하세요.")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or data.get("revision") != revision or not isinstance(data.get("entries"), list):
        raise ValueError("변경 보관 구조가 잘못되었습니다.")
    return path, data


def mutable_path(relative):
    exact = {"project.json", "HANDOFF.md", "proposal/package.json", "templates/selection.json",
             "templates/output-checks.json", "templates/manifest.json"}
    return relative in exact or bool(re.fullmatch(r"registers/(requirements|tasks|deliverables|risks)\.csv", relative)) or bool(
        re.fullmatch(r"templates/profiles/TPL-\d{3,}\.json", relative))


@consistent_read
def preview(folder, revision):
    from tools import lifecycle as engine, starter as core
    root, _ = core.load_project(folder)
    path, data = read(root, revision)
    if data["action"] not in {"apply", "undo"}:
        raise ValueError("승인·단계·원본 등록은 기계적으로 취소하지 않습니다. review/rewind와 실제 결정을 사용하세요.")
    current, conflicts, files = {}, [], []
    for entry in data["entries"]:
        relative = entry["path"]
        if not mutable_path(relative):
            raise ValueError("복원할 수 없는 관리 경로입니다.")
        target = confined(root, relative)
        checksum = digest(target.read_bytes()) if target.is_file() else None
        current[relative] = checksum
        if checksum != entry["after"]:
            conflicts.append(relative)
        files.append({"path": relative, "operation": "restore" if entry["before"] is not None else "remove"})
    events = engine.events(root)
    return {"target_revision": revision, "expected_revision": events[-1]["revision"] if events else None,
            "history_sha256": digest(path.read_bytes()), "current_sha256": current, "changes": files, "conflicts": conflicts}


def undo(folder, plan, actor="agent"):
    from tools import lifecycle as engine, starter as core
    root, _ = core.load_project(folder)
    if not isinstance(plan, dict) or "target_revision" not in plan:
        raise ValueError("undo-preview의 실제 변경 계획이 필요합니다.")
    with lock(root):
        fresh = preview(root, plan["target_revision"])
        if plan != fresh or fresh["conflicts"]:
            raise ValueError("복원 계획 이후 변경 또는 충돌이 있습니다. 사용자 편집을 덮어쓰지 않습니다.")
        _, record = read(root, plan["target_revision"])
        changes = {}
        for entry in record["entries"]:
            changes[entry["path"]] = base64.b64decode(entry["before"], validate=True) if entry["before"] is not None else None
        if "project.json" in changes and changes["project.json"] is not None:
            from tools.storage import json_bytes
            project = json.loads(changes["project.json"])
            project["updated_at"] = core.now()
            changes["project.json"] = json_bytes(project)
        revision = engine.add_event(root, changes, "undo", actor, {"target_revision": plan["target_revision"], "files": sorted(changes)})
        commit(root, changes)
        return {"revision": revision, "restored_files": sorted(changes), "notice": "관리 엔진 변경만 복원했습니다. 코드·문서의 일반 편집과 운영 배포 복구는 별도입니다."}
